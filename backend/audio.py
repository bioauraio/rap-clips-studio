"""Раздел «Аудио»: генерация звука через ElevenLabs.

Три вещи, которые сервису нужны и которых у него не было:
  * речь (TTS) — хук, скит, интро, войсовер поверх клипа;
  * музыка (Eleven Music) — бит или подложка прямо в студии, без стороннего
    сервиса и без «принесите свой трек»;
  * звуковые эффекты (Sound Effects) — вжух, удар, шум улицы под монтаж.

Всё это ОДИН ключ ElevenLabs и один и тот же протокол: JSON внутрь, байты
аудио наружу. Поэтому клиент один на три задачи.

Почему модуль отдельный и самодостаточный: как и `social.py`, он ничего не
знает про модели БД, очки и роуты — принимает текст, отдаёт имя файла в
UPLOAD_DIR. Роуты и списание живут в main.py (см. audio_patch.md).

Ключа нет → модуль честно говорит «озвучка не подключена» и НЕ падает на
импорте: раздел просто не показывается, как это уже сделано со Stripe.

Что перенесено из контент-матрицы BIOAURA (infra/agents/matrix_idea_worker.py)
и почему именно так:
  * порог «ответ меньше 1000 байт = это не аудио, а текст ошибки» —
    ElevenLabs умеет отдать 200 с крошечным телом;
  * 192 kbps, а не 128: на 128 речь звучит глухо (проверено там же). Но
    192 доступны от тарифа Creator, поэтому при отказе по формату мы
    автоматически откатываемся на 128, а не роняем генерацию;
  * настройки голоса 0.45/0.85/0.35 вместо дефолтных 0.5/0.75 — подобраны
    на живых записях, дефолт звучит вяло.
Прокси-обвязка матрицы (ELEVEN_PROXY, обход блокировки РФ) сюда НЕ
переносилась: qlolapp ходит в интернет напрямую.
"""
from __future__ import annotations

import asyncio
import logging
import os
import re
import shutil
import subprocess
import time
import uuid

import httpx

log = logging.getLogger("rapclips.audio")

# ─────────────────────────────── настройки ───────────────────────────────

ELEVEN_API = os.environ.get("ELEVENLABS_API", "https://api.elevenlabs.io").rstrip("/")
ELEVEN_KEY = os.environ.get("ELEVENLABS_API_KEY", "").strip()

# Модель речи. Дефолт — актуальная многоязычная: она понимает русский и
# английский из одного голоса, а хук на двух языках у нас обычное дело.
# eleven_v3 выразительнее, но лимит текста втрое меньше и он дороже —
# переключается через env, без правки кода.
ELEVEN_MODEL = os.environ.get("ELEVENLABS_MODEL", "eleven_multilingual_v2")
ELEVEN_MUSIC_MODEL = os.environ.get("ELEVENLABS_MUSIC_MODEL", "music_v1")
ELEVEN_SFX_MODEL = os.environ.get("ELEVENLABS_SFX_MODEL", "eleven_text_to_sound_v2")

# 192 kbps: на 128 речь глохнет (грабля из матрицы). Требует тариф Creator+,
# при отказе откатываемся на 128 — см. _post_audio.
ELEVEN_FORMAT = os.environ.get("ELEVENLABS_FORMAT", "mp3_44100_192")
ELEVEN_FORMAT_FALLBACK = "mp3_44100_128"

UPLOAD_DIR = os.environ.get("UPLOAD_DIR", "/data/uploads")
FFMPEG = os.environ.get("FFMPEG_BIN", "ffmpeg")
FFPROBE = os.environ.get("FFPROBE_BIN", "ffprobe")
os.makedirs(UPLOAD_DIR, exist_ok=True)

# Синтез минуты речи — секунды, музыки — до пары минут. Таймауты разные,
# чтобы длинный трек не резался на полуслове, а короткий хук не ждал зря.
TTS_TIMEOUT = httpx.Timeout(180.0, connect=15.0)
MUSIC_TIMEOUT = httpx.Timeout(600.0, connect=15.0)
VOICES_TIMEOUT = httpx.Timeout(30.0, connect=10.0)

# Сколько символов ElevenLabs берёт за один запрос. Больше лимита — 400,
# поэтому длинный текст режем сами и склеиваем ffmpeg'ом: молча обрезать
# текст нельзя, человек не поймёт, куда делся второй куплет.
MODEL_CHAR_LIMIT = {
    "eleven_v3": 2800,             # официальный потолок ~3000, берём с запасом
    "eleven_multilingual_v2": 9500,
    "eleven_multilingual_v1": 9500,
    "eleven_monolingual_v1": 9500,
    "eleven_turbo_v2_5": 38000,
    "eleven_turbo_v2": 28000,
    "eleven_flash_v2_5": 38000,
    "eleven_flash_v2": 28000,
}
DEFAULT_CHAR_LIMIT = 4500  # незнакомая модель — консервативно

# Длина музыки, мс. Границы API: 3 000 … 600 000.
MUSIC_MIN_MS = 3_000
MUSIC_MAX_MS = 600_000
# Эффекты: 0.5 … 30 секунд.
SFX_MIN_S = 0.5
SFX_MAX_S = 30.0

# Каталог голосов меняется редко, а дёргается он из интерфейса на каждый
# показ формы. Кэш на час + «последний удачный список» на случай, когда
# ElevenLabs отвечает ошибкой: пустой дропдаун выглядит как поломка.
VOICES_TTL_S = int(os.environ.get("ELEVENLABS_VOICES_TTL_S", "3600"))
_voices_cache: dict = {"at": 0.0, "items": []}


class AudioError(RuntimeError):
    """Ошибка аудио-модуля с машинным кодом — фронту нужно различать
    «ключ не подключён» и «текст слишком длинный», а не разбирать строку.

    code:
      disabled — нет ELEVENLABS_API_KEY, раздел выключен;
      auth     — ключ есть, но отвергнут (401/403) или кончилась квота;
      input    — кривой ввод (пустой текст, длительность вне границ);
      api      — ElevenLabs ответил ошибкой;
      network  — не достучались;
      ffmpeg   — не смогли склеить/померить локально.
    """

    def __init__(self, message: str, code: str = "api") -> None:
        super().__init__(message)
        self.code = code


def available() -> bool:
    """Модуль подключён, когда есть ключ. Всё остальное — детали."""
    return bool(ELEVEN_KEY)


def _require_key() -> None:
    if not ELEVEN_KEY:
        raise AudioError(
            "Audio generation is not connected: ELEVENLABS_API_KEY is not set. "
            "Create a key at elevenlabs.io → Profile → API keys and put it into infra/.env.",
            "disabled",
        )


def _headers() -> dict:
    return {"xi-api-key": ELEVEN_KEY, "Content-Type": "application/json"}


# ──────────────────────────── общие утилиты ────────────────────────────

def _run(cmd: list, timeout: int = 300) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, capture_output=True, timeout=timeout)


def duration_sec(path: str) -> float:
    """Длительность файла. 0.0 — не смогли определить (битый файл)."""
    try:
        out = subprocess.run(
            [FFPROBE, "-v", "error", "-show_entries", "format=duration",
             "-of", "default=noprint_wrappers=1:nokey=1", path],
            capture_output=True, text=True, timeout=30, check=True,
        )
        return max(0.0, round(float(out.stdout.strip()), 2))
    except Exception as e:  # noqa: BLE001
        log.warning("ffprobe не определил длительность %s: %s", path, e)
        return 0.0


def has_audio(path: str) -> bool:
    """Есть ли в файле звуковая дорожка (перенос из матрицы: перед STS и
    перед мастерингом надо знать, что мы вообще что-то обрабатываем)."""
    try:
        out = subprocess.run(
            [FFPROBE, "-v", "error", "-select_streams", "a:0",
             "-show_entries", "stream=codec_type",
             "-of", "default=noprint_wrappers=1:nokey=1", path],
            capture_output=True, text=True, timeout=30,
        )
        return "audio" in out.stdout
    except Exception:  # noqa: BLE001
        return False


def mean_volume_db(path: str) -> float:
    """Средняя громкость дорожки. −91.0 — тишина/не смогли."""
    try:
        r = subprocess.run(
            [FFMPEG, "-v", "info", "-i", path, "-af", "volumedetect", "-f", "null", "-"],
            capture_output=True, text=True, timeout=120,
        )
        m = re.search(r"mean_volume:\s*(-?[\d.]+) dB", r.stderr)
        return float(m.group(1)) if m else -91.0
    except Exception:  # noqa: BLE001
        return -91.0


def to_wav(src_path: str, out_path: str, sample_rate: int = 44100, bits: int = 24) -> str:
    """Привести что угодно к WAV без потерь.

    Зачем: мастеринг обязан работать с несжатым исходником — гонять mp3
    через компрессор и снова в mp3 значит копить артефакты. Плюс matchering
    читает через libsndfile, который на mp3 в старых сборках спотыкается,
    а ffmpeg у нас в образе есть всегда."""
    codec = {16: "pcm_s16le", 24: "pcm_s24le", 32: "pcm_f32le"}.get(bits, "pcm_s24le")
    r = _run([FFMPEG, "-y", "-v", "error", "-i", src_path,
              "-vn", "-ac", "2", "-ar", str(sample_rate), "-c:a", codec, out_path], timeout=600)
    if r.returncode != 0 or not os.path.exists(out_path):
        raise AudioError(f"ffmpeg could not decode the audio: {r.stderr.decode()[-200:]}", "ffmpeg")
    return out_path


def concat_audio(parts: list, out_path: str, bitrate: str = "192k") -> str:
    """Склейка кусков в один файл. Перекодируем, а не copy: куски приходят
    отдельными запросами и у них может разойтись служебная шапка — concat
    с -c copy на таком материале даёт треск на стыках."""
    if not parts:
        raise AudioError("nothing to concatenate", "input")
    if len(parts) == 1:
        shutil.copyfile(parts[0], out_path)
        return out_path
    work = os.path.dirname(out_path) or UPLOAD_DIR
    listfile = os.path.join(work, f"concat_{uuid.uuid4().hex}.txt")
    try:
        with open(listfile, "w") as f:
            for p in parts:
                f.write("file '%s'\n" % p.replace("'", "'\\''"))
        r = _run([FFMPEG, "-y", "-v", "error", "-f", "concat", "-safe", "0", "-i", listfile,
                  "-c:a", "libmp3lame", "-b:a", bitrate, out_path], timeout=600)
        if r.returncode != 0 or not os.path.exists(out_path):
            raise AudioError(f"ffmpeg could not join the parts: {r.stderr.decode()[-200:]}", "ffmpeg")
        return out_path
    finally:
        try:
            os.remove(listfile)
        except OSError:
            pass


def _ext_for_format(fmt: str) -> str:
    if fmt.startswith("mp3"):
        return ".mp3"
    if fmt.startswith("opus"):
        return ".opus"
    if fmt.startswith("pcm"):
        return ".wav"
    if fmt.startswith("ulaw") or fmt.startswith("alaw"):
        return ".wav"
    return ".mp3"


# ─────────────────────────── каталог голосов ───────────────────────────

def _voice_languages(v: dict) -> list:
    """Языки голоса из всех мест, куда ElevenLabs их кладёт.

    Формат ответа за годы менялся: у премейд-голосов язык лежит в
    verified_languages, у клонов — в fine_tuning, у части — просто ярлыком.
    Собираем всё в один список, чтобы фильтр не зависел от вида голоса."""
    langs = set()
    for item in (v.get("verified_languages") or []):
        if isinstance(item, dict) and item.get("language"):
            langs.add(str(item["language"]).lower()[:2])
    ft = v.get("fine_tuning") or {}
    if isinstance(ft, dict) and ft.get("language"):
        langs.add(str(ft["language"]).lower()[:2])
    labels = v.get("labels") or {}
    if isinstance(labels, dict) and labels.get("language"):
        langs.add(str(labels["language"]).lower()[:2])
    return sorted(langs)


def _voice_row(v: dict) -> dict:
    labels = v.get("labels") or {}
    return {
        "voice_id": v.get("voice_id") or "",
        "name": v.get("name") or "",
        "category": v.get("category") or "",
        "gender": (labels.get("gender") or "").lower(),
        "age": (labels.get("age") or "").lower(),
        "accent": (labels.get("accent") or "").lower(),
        "use_case": (labels.get("use_case") or labels.get("description") or "").lower(),
        "languages": _voice_languages(v),
        "preview_url": v.get("preview_url") or "",
    }


async def list_voices(language: str = "", refresh: bool = False) -> list:
    """Каталог голосов подписки. language — двухбуквенный код ('en', 'ru'),
    пусто = все.

    Фильтр применяем ПОСЛЕ кэша, а не в запросе: каталог один, а языков в
    интерфейсе несколько — иначе кэш пришлось бы держать на каждый язык."""
    _require_key()
    fresh = _voices_cache["items"] and (time.time() - _voices_cache["at"]) < VOICES_TTL_S
    if refresh or not fresh:
        try:
            items = await _fetch_voices()
            _voices_cache["items"] = items
            _voices_cache["at"] = time.time()
        except AudioError:
            # Отдаём последний удачный список: пустой дропдаун человек читает
            # как «сервис сломан», хотя сломался только запрос каталога.
            if not _voices_cache["items"]:
                raise
            log.warning("каталог голосов не обновился, отдаю прошлый (%s шт.)",
                        len(_voices_cache["items"]))
    items = _voices_cache["items"]
    if language:
        code = language.lower()[:2]
        items = [v for v in items if not v["languages"] or code in v["languages"]]
    return items


async def _fetch_voices() -> list:
    """GET /v2/voices с постраничкой, откат на /v1/voices.

    v2 отдаёт пагинацию и фильтры, но на части ключей его нет — а v1 живёт
    везде и возвращает всё одним куском."""
    out: list = []
    try:
        async with httpx.AsyncClient(timeout=VOICES_TIMEOUT) as client:
            token = ""
            for _ in range(20):  # 20 страниц по 100 — потолок от бесконечного цикла
                params = {"page_size": 100}
                if token:
                    params["next_page_token"] = token
                r = await client.get(f"{ELEVEN_API}/v2/voices", params=params,
                                     headers={"xi-api-key": ELEVEN_KEY})
                if r.status_code == 404:
                    raise LookupError("v2 недоступен")
                _raise_for_api(r)
                data = r.json()
                out.extend(_voice_row(v) for v in (data.get("voices") or []))
                token = data.get("next_page_token") or ""
                if not data.get("has_more") or not token:
                    break
        return out
    except LookupError:
        pass
    except httpx.HTTPError as e:
        raise AudioError(f"ElevenLabs is unreachable: {e}", "network")
    try:
        async with httpx.AsyncClient(timeout=VOICES_TIMEOUT) as client:
            r = await client.get(f"{ELEVEN_API}/v1/voices", headers={"xi-api-key": ELEVEN_KEY})
            _raise_for_api(r)
            return [_voice_row(v) for v in (r.json().get("voices") or [])]
    except httpx.HTTPError as e:
        raise AudioError(f"ElevenLabs is unreachable: {e}", "network")


def _raise_for_api(r: httpx.Response) -> None:
    """Ошибку ElevenLabs превращаем в наш AudioError с внятным текстом.

    Отдельно ловим 401/402: «ключ не тот» и «кончились кредиты» — это разные
    сообщения владельцу, а по HTTP-коду они почти одинаковые."""
    if r.status_code < 400:
        return
    detail = ""
    try:
        body = r.json()
        d = body.get("detail")
        if isinstance(d, dict):
            detail = str(d.get("message") or d.get("status") or "")
        elif d:
            detail = str(d)
        if not detail:
            detail = str(body)[:300]
    except Exception:  # noqa: BLE001
        detail = r.text[:300]
    if r.status_code in (401, 403):
        raise AudioError(f"ElevenLabs rejected the API key: {detail}", "auth")
    if r.status_code == 402 or "quota" in detail.lower() or "credit" in detail.lower():
        raise AudioError(f"ElevenLabs credits are exhausted: {detail}", "auth")
    raise AudioError(f"ElevenLabs error {r.status_code}: {detail}", "api")


# ────────────────────────────── речь (TTS) ──────────────────────────────

def voice_settings(stability: float = 0.45, similarity: float = 0.85,
                   style: float = 0.35, speed: float = 1.0,
                   speaker_boost: bool = True) -> dict:
    """Настройки голоса. Дефолты не «из документации», а с живых записей:
    заводские 0.5/0.75/0.0 звучат ровно и скучно, на рэп-подаче — особенно.

    Границы жёсткие: за ними ElevenLabs отвечает 400, и вина будет наша."""
    return {
        "stability": min(1.0, max(0.0, float(stability))),
        "similarity_boost": min(1.0, max(0.0, float(similarity))),
        "style": min(1.0, max(0.0, float(style))),
        "speed": min(1.2, max(0.7, float(speed))),
        "use_speaker_boost": bool(speaker_boost),
    }


def split_text(text: str, limit: int) -> list:
    """Нарезка длинного текста под лимит запроса.

    Режем по абзацам → предложениям → словам, и НИКОГДА не рвём слово.
    Молча обрезать хвост нельзя: человек заплатил за весь текст и ждёт
    его целиком — именно поэтому здесь склейка, а не усечение."""
    text = (text or "").strip()
    if not text:
        return []
    if len(text) <= limit:
        return [text]

    def by(parts: list, sep: str) -> list:
        chunks: list = []
        buf = ""
        for p in parts:
            piece = (p + sep) if sep else p
            if len(buf) + len(piece) <= limit:
                buf += piece
            else:
                if buf.strip():
                    chunks.append(buf.strip())
                buf = piece
        if buf.strip():
            chunks.append(buf.strip())
        return chunks

    chunks = by(text.split("\n\n"), "\n\n")
    out: list = []
    for ch in chunks:
        if len(ch) <= limit:
            out.append(ch)
            continue
        sentences = re.split(r"(?<=[.!?…])\s+", ch)
        for part in by(sentences, " "):
            if len(part) <= limit:
                out.append(part)
                continue
            for word_part in by(part.split(" "), " "):
                # Слово длиннее лимита — патология (склеенный текст без
                # пробелов), тут уже режем механически, иначе запрос не уйдёт.
                while len(word_part) > limit:
                    out.append(word_part[:limit])
                    word_part = word_part[limit:]
                if word_part:
                    out.append(word_part)
    return [c for c in out if c.strip()]


async def _post_audio(url: str, payload: dict, timeout: httpx.Timeout,
                      fmt: str) -> bytes:
    """POST c JSON → байты аудио.

    Две грабли, обе из практики:
      * при отказе по формату (192 kbps есть не на всех тарифах) повторяем
        запрос на 128 — терять генерацию из-за битрейта глупо;
      * тело меньше 1000 байт — это НЕ аудио, а текст ошибки с кодом 200.
        Без этой проверки в UPLOAD_DIR ложится «мп3» на 200 байт, который
        не играет, и виноватым выглядит плеер."""
    async def _try(fmt_now: str) -> httpx.Response:
        async with httpx.AsyncClient(timeout=timeout) as client:
            return await client.post(url, params={"output_format": fmt_now},
                                     json=payload, headers=_headers())

    try:
        r = await _try(fmt)
        if r.status_code >= 400 and fmt != ELEVEN_FORMAT_FALLBACK and fmt.startswith("mp3"):
            text = (r.text or "")[:300].lower()
            if "output_format" in text or "tier" in text or "subscription" in text:
                log.warning("формат %s недоступен на этом тарифе, откат на %s",
                            fmt, ELEVEN_FORMAT_FALLBACK)
                r = await _try(ELEVEN_FORMAT_FALLBACK)
        _raise_for_api(r)
    except httpx.HTTPError as e:
        raise AudioError(f"ElevenLabs is unreachable: {e}", "network")
    data = r.content
    if len(data) < 1000:
        raise AudioError(
            "ElevenLabs returned an empty audio response — the request was "
            "rejected without an error code. Check the voice id and the text.",
            "api")
    return data


async def synthesize(text: str, voice_id: str, *, model: str = "",
                     settings: dict | None = None, language: str = "",
                     out_format: str = "") -> dict:
    """Текст → mp3 в UPLOAD_DIR.

    Возвращает {"filename", "duration_sec", "chars", "chunks", "model", "voice_id"}:
    chars нужен списанию очков (тарифицируем по символам, как сам ElevenLabs),
    chunks — честный признак, что текст резался.
    """
    _require_key()
    text = (text or "").strip()
    if not text:
        raise AudioError("Nothing to voice: the text is empty.", "input")
    if not voice_id:
        raise AudioError("Pick a voice first.", "input")

    model = model or ELEVEN_MODEL
    fmt = out_format or ELEVEN_FORMAT
    limit = MODEL_CHAR_LIMIT.get(model, DEFAULT_CHAR_LIMIT)
    chunks = split_text(text, limit)
    vs = settings or voice_settings()
    url = f"{ELEVEN_API}/v1/text-to-speech/{voice_id}"

    work = os.path.join(UPLOAD_DIR, f"tts_{uuid.uuid4().hex}")
    os.makedirs(work, exist_ok=True)
    try:
        parts: list = []
        for i, chunk in enumerate(chunks):
            payload = {
                "text": chunk,
                "model_id": model,
                "voice_settings": vs,
            }
            if language:
                payload["language_code"] = language.lower()[:2]
            # Соседние куски отдаём моделью как контекст: без этого на стыке
            # слышно смену интонации — фраза начинается «с нуля», как будто
            # диктор вдохнул и начал новый дубль.
            if i > 0:
                payload["previous_text"] = chunks[i - 1][-500:]
            if i < len(chunks) - 1:
                payload["next_text"] = chunks[i + 1][:500]
            data = await _post_audio(url, payload, TTS_TIMEOUT, fmt)
            part = os.path.join(work, f"p{i:03d}{_ext_for_format(fmt)}")
            with open(part, "wb") as f:
                f.write(data)
            parts.append(part)
            log.info("tts: кусок %s/%s, %s символов, %s КБ",
                     i + 1, len(chunks), len(chunk), len(data) // 1024)

        name = f"tts_{uuid.uuid4().hex}{_ext_for_format(fmt)}"
        out_path = os.path.join(UPLOAD_DIR, name)
        await asyncio.to_thread(concat_audio, parts, out_path)
        return {
            "filename": name,
            "duration_sec": await asyncio.to_thread(duration_sec, out_path),
            "chars": len(text),
            "chunks": len(chunks),
            "model": model,
            "voice_id": voice_id,
        }
    finally:
        shutil.rmtree(work, ignore_errors=True)


# ────────────────────────────── музыка ──────────────────────────────

async def compose_music(prompt: str, length_sec: float = 30.0, *,
                        instrumental: bool = False, model: str = "",
                        out_format: str = "") -> dict:
    """Промпт → музыкальный фрагмент (Eleven Music).

    ВАЖНО про громкость: Eleven Music отдаёт трек примерно на −24 LUFS,
    то есть на десяток децибел тише, чем играют площадки. Выкладывать такое
    как есть нельзя — поэтому сгенерированное здесь принято прогонять через
    мастеринг (backend/mastering.py) прямо в этом же разделе.
    """
    _require_key()
    prompt = (prompt or "").strip()
    if not prompt:
        raise AudioError("Describe the music you want.", "input")
    ms = int(round(float(length_sec) * 1000))
    if ms < MUSIC_MIN_MS or ms > MUSIC_MAX_MS:
        raise AudioError(
            f"Music length must be between {MUSIC_MIN_MS // 1000} and "
            f"{MUSIC_MAX_MS // 1000} seconds.", "input")

    fmt = out_format or ELEVEN_FORMAT
    payload = {
        "prompt": prompt,
        "music_length_ms": ms,
        "model_id": model or ELEVEN_MUSIC_MODEL,
    }
    if instrumental:
        payload["force_instrumental"] = True
    data = await _post_audio(f"{ELEVEN_API}/v1/music", payload, MUSIC_TIMEOUT, fmt)
    name = f"music_{uuid.uuid4().hex}{_ext_for_format(fmt)}"
    path = os.path.join(UPLOAD_DIR, name)
    with open(path, "wb") as f:
        f.write(data)
    return {
        "filename": name,
        "duration_sec": await asyncio.to_thread(duration_sec, path),
        "seconds_billed": round(ms / 1000.0, 1),
        "model": payload["model_id"],
    }


async def sound_effect(prompt: str, seconds: float = 0.0, *,
                       influence: float = 0.3, loop: bool = False,
                       out_format: str = "") -> dict:
    """Промпт → звуковой эффект. seconds=0 — пусть модель решит сама
    (для коротких ударов и вжухов это обычно лучше принудительной длины)."""
    _require_key()
    prompt = (prompt or "").strip()
    if not prompt:
        raise AudioError("Describe the sound you want.", "input")
    payload = {
        "text": prompt,
        "model_id": ELEVEN_SFX_MODEL,
        "prompt_influence": min(1.0, max(0.0, float(influence))),
    }
    if seconds:
        if seconds < SFX_MIN_S or seconds > SFX_MAX_S:
            raise AudioError(
                f"Sound effect length must be between {SFX_MIN_S} and {SFX_MAX_S} seconds.",
                "input")
        payload["duration_seconds"] = round(float(seconds), 2)
    if loop:
        payload["loop"] = True

    fmt = out_format or ELEVEN_FORMAT
    data = await _post_audio(f"{ELEVEN_API}/v1/sound-generation", payload, TTS_TIMEOUT, fmt)
    name = f"sfx_{uuid.uuid4().hex}{_ext_for_format(fmt)}"
    path = os.path.join(UPLOAD_DIR, name)
    with open(path, "wb") as f:
        f.write(data)
    return {
        "filename": name,
        "duration_sec": await asyncio.to_thread(duration_sec, path),
        "model": ELEVEN_SFX_MODEL,
    }


# ─────────────────── синхронные обёртки для фоновых задач ───────────────────
# Фоновые шаги студии живут в обычных потоках (Thread в main.py), где нет
# работающего event loop. asyncio.run внутри потока — самый дешёвый мост.

def _sync(coro_factory):
    return asyncio.run(coro_factory())


def synthesize_sync(text: str, voice_id: str, **kw) -> dict:
    return _sync(lambda: synthesize(text, voice_id, **kw))


def compose_music_sync(prompt: str, length_sec: float = 30.0, **kw) -> dict:
    return _sync(lambda: compose_music(prompt, length_sec, **kw))


def sound_effect_sync(prompt: str, seconds: float = 0.0, **kw) -> dict:
    return _sync(lambda: sound_effect(prompt, seconds, **kw))


def status() -> dict:
    """Короткая сводка для интерфейса: что подключено, чем поём.
    Ключ наружу НЕ отдаём — только флаг (паттерн из контент-матрицы)."""
    return {
        "enabled": available(),
        "model": ELEVEN_MODEL,
        "music_model": ELEVEN_MUSIC_MODEL,
        "format": ELEVEN_FORMAT,
        "char_limit": MODEL_CHAR_LIMIT.get(ELEVEN_MODEL, DEFAULT_CHAR_LIMIT),
        "music_max_sec": MUSIC_MAX_MS // 1000,
    }
