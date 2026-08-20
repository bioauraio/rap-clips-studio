"""Мастеринг трека: свести громкость и тембр так, чтобы трек не звучал
тише и глуше всего остального в плейлисте.

ЧЕСТНО О ТОМ, ЧТО ЭТО ТАКОЕ. «Лучшей нейронки по мастерингу» не существует
как категории: победитель по соотношению «результат / деньги / контроль» —
Matchering 2.0 — это вообще не нейросеть, а классический DSP на numpy/scipy.
Продавать его как ИИ нельзя: враньё всплывёт на первом же вопросе. Поэтому в
интерфейсе мы говорим «мастеринг по эталонному треку», а не «AI mastering»,
и в отчёте пишем ровно те операции, которые произошли.

ТРИ ДВИЖКА, порядок выбора при engine="auto":

  1. matchering — ОСНОВНОЙ, если задан референс-трек и сервис поднят.
     Мастеринг по эталону: подтягивает АЧХ, RMS, пиковую амплитуду и ширину
     стерео нашего трека под выбранный эталон. В единственном приличном
     независимом слепом тесте (Benn Jordan, 472 человека, double-blind) занял
     3-е место — выше всех коммерческих ИИ-сервисов; выше были только живые
     инженеры. Стоит нам НОЛЬ и не выпускает аудио наружу с сервера.
     Лицензия GPL-3.0, поэтому библиотека НЕ импортируется внутрь нашего
     процесса: она живёт отдельным контейнером/процессом, а мы ходим в него
     по HTTP (см. backend/matchering_service.py и infra/matchering.Dockerfile).

  2. ffmpeg — ЗАПАСНОЙ и он же дефолт без референса. Двухпроходный loudnorm:
     первый проход меряет, второй приводит к целевой громкости с ограничением
     по true-peak. Это НОРМАЛИЗАЦИЯ + лимитер, а не мастеринг, и в отчёте так
     и написано. Работает всегда: ffmpeg есть в образе.

  3. roex — ПЛАТНАЯ опция (RoEx Tonn API, ~$1.76–2.20 за трек). Единственный
     облачный сервис, который подключается без переписки с отделом продаж.
     Независимых слепых тестов по нему нет — только маркетинг самого RoEx,
     поэтому он второй кнопкой, а не движком по умолчанию.

ПОЧЕМУ НЕ «ПРОСТО КУПИТЬ API». Полный трёхминутный клип на Grok стоит
пользователю 120 очков, а нам — ноль (наша подписка). Один платный мастеринг
у RoEx — это ~$2, то есть ~180 очков живыми деньгами: дороже целого клипа за
самый дешёвый по восприятию результат («вы просто сделали громче»). Отсюда
гибрид: бесплатные движки по умолчанию, платный — осознанным выбором.

Ориентиры громкости: стриминг нормализует к −14 LUFS, true peak держат ниже
−1 dBTP (запас на перекодирование в lossy). Рэп и электронику традиционно
мастерят громче, −9…−11 LUFS, сознательно отдавая динамику — поэтому цель
задаётся параметром, а не прибита в коде.
"""
from __future__ import annotations

import json
import logging
import os
import re
import shutil
import subprocess
import time
import uuid

import httpx
from itsdangerous import BadSignature, SignatureExpired, URLSafeTimedSerializer

import audio

log = logging.getLogger("rapclips.mastering")

# ─────────────────────────────── настройки ───────────────────────────────

UPLOAD_DIR = os.environ.get("UPLOAD_DIR", "/data/uploads")
FFMPEG = os.environ.get("FFMPEG_BIN", "ffmpeg")
os.makedirs(UPLOAD_DIR, exist_ok=True)

# Свой контейнер с matchering (см. infra/matchering.Dockerfile). Пусто —
# движок выключен, остаётся ffmpeg.
MATCHERING_URL = os.environ.get("MATCHERING_URL", "").rstrip("/")
# Запасной способ без контейнера: отдельный интерпретатор, в котором стоит
# matchering. Тоже отдельный ПРОЦЕСС — GPL-библиотека не оказывается внутри
# нашего кода ни при каком раскладе.
MATCHERING_PYTHON = os.environ.get("MATCHERING_PYTHON", "").strip()
MATCHERING_TIMEOUT_S = float(os.environ.get("MATCHERING_TIMEOUT_S", "900"))

# RoEx Tonn — платный облачный мастеринг. Нет ключа → опция не показывается.
ROEX_API = os.environ.get("ROEX_API", "https://tonn.roexaudio.com").rstrip("/")
ROEX_API_KEY = os.environ.get("ROEX_API_KEY", "").strip()
ROEX_TIMEOUT_S = float(os.environ.get("ROEX_TIMEOUT_S", "900"))
ROEX_POLL_S = float(os.environ.get("ROEX_POLL_S", "10"))
# Жанры RoEx. Наши — первые два, остальные оставлены, чтобы не врать выбором.
ROEX_STYLES = ("HIPHOP_GRIME", "ELECTRONIC", "POP", "ROCK_INDIE",
               "ACOUSTIC", "REGGAE_DUB", "METAL", "OTHER")
ROEX_LOUDNESS = ("LOW", "MEDIUM", "HIGH")

# Целевые значения по умолчанию — «как играют площадки».
TARGET_LUFS = float(os.environ.get("MASTER_TARGET_LUFS", "-14"))
TARGET_TP = float(os.environ.get("MASTER_TARGET_TP", "-1"))
TARGET_LRA = float(os.environ.get("MASTER_TARGET_LRA", "11"))
# Готовые цели под площадки: клиенту понятнее «Spotify», чем «−14 LUFS».
LOUDNESS_TARGETS = {
    "streaming": (-14.0, -1.0),   # Spotify/Apple/YouTube — нормализуют к −14
    "club": (-9.0, -1.0),         # рэп и электроника: громче, динамику отдаём
    "youtube": (-13.0, -1.0),
    "cd": (-9.0, -0.3),
}

# Формат мастера. WAV — то, что просят дистрибьюторы (Zvonko в том числе),
# mp3 — если важнее вес файла в браузере. Меняется без правки кода.
MASTER_OUTPUT = os.environ.get("MASTER_OUTPUT", "wav").lower()
MASTER_SR = int(os.environ.get("MASTER_SR", "44100"))

# Подписанная публичная ссылка на исходник — нужна ТОЛЬКО RoEx: он умеет
# принимать трек лишь по URL, файлом не загрузишь. Своя соль: пропуск на
# аудио не должен подходить ни к сессии, ни к ссылке на клип из social.py.
SECRET_KEY = os.environ.get("SECRET_KEY", "")
PUBLIC_BASE_URL = os.environ.get("PUBLIC_BASE_URL", "https://qlolapp.art").rstrip("/")
LINK_TTL_S = int(os.environ.get("MASTER_LINK_TTL_S", str(6 * 60 * 60)))
_link_signer = URLSafeTimedSerializer(SECRET_KEY or "rapclips-audio-unset",
                                      salt="rapclips-audio-source")


class MasteringError(RuntimeError):
    """Мастеринг не состоялся.

    code: disabled | input | engine | network | timeout | ffmpeg | credits
    """

    def __init__(self, message: str, code: str = "engine") -> None:
        super().__init__(message)
        self.code = code


# ─────────────────────── публичная ссылка на исходник ───────────────────────

def public_source_url(filename: str) -> str:
    """Временный публичный адрес файла для внешнего сервиса мастеринга.

    Имя файла зашито в подпись на SECRET_KEY, срок жизни ограничен: подобрать
    чужой файл нельзя, вечной публичной раздачи не появляется."""
    if not SECRET_KEY:
        raise MasteringError("SECRET_KEY is not set — cannot sign the upload link.", "disabled")
    token = _link_signer.dumps(filename)
    return f"{PUBLIC_BASE_URL}/api/audio/public/{token}"


def source_from_token(token: str) -> str:
    """Обратная операция для роута раздачи. Пусто — ссылка битая/просрочена."""
    try:
        name = _link_signer.loads(token, max_age=LINK_TTL_S)
    except (BadSignature, SignatureExpired, TypeError, ValueError):
        return ""
    # Защита от путешествия по каталогам: наружу отдаём только плоское имя.
    return os.path.basename(str(name or ""))


# ─────────────────────────── измерение громкости ───────────────────────────

def measure(path: str) -> dict:
    """Замер дорожки: integrated LUFS, true peak, диапазон громкости.

    Считаем фильтром loudnorm в режиме анализа — он отдаёт готовый JSON и
    те же цифры, по которым потом работает второй проход. Отдельный ebur128
    дал бы то же самое, но парсить пришлось бы текст лога."""
    r = subprocess.run(
        [FFMPEG, "-hide_banner", "-nostats", "-i", path,
         "-af", f"loudnorm=I={TARGET_LUFS}:TP={TARGET_TP}:LRA={TARGET_LRA}:print_format=json",
         "-f", "null", "-"],
        capture_output=True, text=True, timeout=900,
    )
    m = re.findall(r"\{[^{}]*\"input_i\"[^{}]*\}", r.stderr, re.S)
    if not m:
        raise MasteringError(
            f"could not measure loudness: {r.stderr[-200:]}", "ffmpeg")
    data = json.loads(m[-1])

    def num(key: str) -> float:
        try:
            v = float(data.get(key))
        except (TypeError, ValueError):
            return 0.0
        # loudnorm пишет -inf/-70 на тишине — наружу такие числа не пускаем.
        return v if v > -80.0 else -80.0

    return {
        "lufs": round(num("input_i"), 1),
        "true_peak": round(num("input_tp"), 1),
        "lra": round(num("input_lra"), 1),
        "threshold": round(num("input_thresh"), 1),
        "raw": data,
    }


# ────────────────────────────── движок ffmpeg ──────────────────────────────

def _ffmpeg_master(src: str, dst: str, target_i: float, target_tp: float,
                   measured: dict) -> str:
    """Двухпроходный loudnorm: замер уже есть, здесь второй проход.

    linear=true просит ffmpeg обойтись ОДНИМ множителем на весь трек — так
    динамика не мнётся. Если запаса не хватает, ffmpeg сам переходит в
    динамический режим; поэтому в отчёте мы пишем не «сохранили динамику»,
    а показываем измеренный диапазон до и после."""
    raw = measured.get("raw") or {}
    flt = (
        f"loudnorm=I={target_i}:TP={target_tp}:LRA={TARGET_LRA}"
        f":measured_I={raw.get('input_i', measured['lufs'])}"
        f":measured_TP={raw.get('input_tp', measured['true_peak'])}"
        f":measured_LRA={raw.get('input_lra', measured['lra'])}"
        f":measured_thresh={raw.get('input_thresh', measured['threshold'])}"
        f":offset={raw.get('target_offset', 0.0)}"
        f":linear=true:print_format=summary"
    )
    codec = ["-c:a", "pcm_s24le"] if dst.endswith(".wav") else ["-c:a", "libmp3lame", "-b:a", "320k"]
    r = subprocess.run(
        [FFMPEG, "-y", "-hide_banner", "-nostats", "-i", src,
         "-af", flt, "-ar", str(MASTER_SR)] + codec + [dst],
        capture_output=True, text=True, timeout=1800,
    )
    if r.returncode != 0 or not os.path.exists(dst):
        raise MasteringError(f"ffmpeg mastering failed: {r.stderr[-200:]}", "ffmpeg")
    return dst


# ──────────────────────────── движок matchering ────────────────────────────

def matchering_available() -> bool:
    return bool(MATCHERING_URL or MATCHERING_PYTHON)


def matchering_health() -> dict:
    """Живость сайдкара. Дёргается из интерфейса, поэтому таймаут короткий."""
    if MATCHERING_PYTHON and not MATCHERING_URL:
        return {"ok": os.path.exists(MATCHERING_PYTHON), "mode": "subprocess"}
    if not MATCHERING_URL:
        return {"ok": False, "mode": "off"}
    try:
        r = httpx.get(f"{MATCHERING_URL}/health", timeout=5.0)
        data = r.json() if r.status_code == 200 else {}
        return {"ok": bool(data.get("ok")), "mode": "http",
                "version": data.get("matchering") or ""}
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "mode": "http", "error": str(e)[:200]}


def _matchering_master(src_wav: str, ref_wav: str, dst: str) -> str:
    """Мастеринг по эталону. HTTP-сайдкар приоритетнее: он уже прогрет.

    Библиотека под GPL-3.0. Для SaaS это безопасно (не AGPL, мы ничего не
    распространяем), но чтобы вопрос не всплывал вообще — только отдельный
    процесс, никаких импортов внутрь FastAPI."""
    if MATCHERING_URL:
        try:
            with open(src_wav, "rb") as ftarget, open(ref_wav, "rb") as fref:
                r = httpx.post(
                    f"{MATCHERING_URL}/master",
                    files={"target": ("target.wav", ftarget, "audio/wav"),
                           "reference": ("reference.wav", fref, "audio/wav")},
                    timeout=MATCHERING_TIMEOUT_S,
                )
        except httpx.HTTPError as e:
            raise MasteringError(f"mastering service is unreachable: {e}", "network")
        if r.status_code != 200:
            raise MasteringError(
                f"mastering service error {r.status_code}: {r.text[:200]}", "engine")
        tmp = dst if dst.endswith(".wav") else dst + ".tmp.wav"
        with open(tmp, "wb") as f:
            f.write(r.content)
        if os.path.getsize(tmp) < 1000:
            raise MasteringError("mastering service returned an empty file", "engine")
        return tmp

    # Локальный режим: тот же matchering_service.py, но как CLI чужим
    # интерпретатором. Нужен, когда сайдкар поднимать негде.
    tmp = dst if dst.endswith(".wav") else dst + ".tmp.wav"
    runner = os.path.join(os.path.dirname(os.path.abspath(__file__)), "matchering_service.py")
    r = subprocess.run(
        [MATCHERING_PYTHON, runner, "--target", src_wav, "--reference", ref_wav, "--out", tmp],
        capture_output=True, text=True, timeout=MATCHERING_TIMEOUT_S,
    )
    if r.returncode != 0 or not os.path.exists(tmp):
        raise MasteringError(f"matchering failed: {(r.stderr or r.stdout)[-300:]}", "engine")
    return tmp


# ─────────────────────────────── движок RoEx ───────────────────────────────

def roex_available() -> bool:
    return bool(ROEX_API_KEY)


def _roex_master(source_url: str, dst: str, style: str, loudness: str) -> str:
    """Платный облачный мастеринг: превью → финал.

    Превью бесплатное (30 секунд, кредиты не тратит) и служит подтверждением,
    что сервис вообще принял трек. Кредиты списывает уже финал, поэтому очки
    пользователю мы снимаем ПОСЛЕ того, как файл лежит на диске — иначе при
    падении чужого API человек платит за воздух."""
    if not ROEX_API_KEY:
        raise MasteringError("RoEx is not connected: ROEX_API_KEY is not set.", "disabled")
    style = style if style in ROEX_STYLES else "HIPHOP_GRIME"
    loudness = loudness if loudness in ROEX_LOUDNESS else "MEDIUM"
    headers = {"X-API-Key": ROEX_API_KEY, "Content-Type": "application/json"}
    body = {"masteringData": {
        "trackData": [{"trackURL": source_url}],
        "musicalStyle": style,
        "desiredLoudness": loudness,
        "sampleRate": str(MASTER_SR),
    }}
    try:
        with httpx.Client(timeout=60.0) as client:
            r = client.post(f"{ROEX_API}/masteringpreview", json=body, headers=headers)
            if r.status_code in (401, 403):
                raise MasteringError("RoEx rejected the API key.", "disabled")
            if r.status_code == 402:
                raise MasteringError("RoEx credits are exhausted.", "credits")
            if r.status_code >= 400:
                raise MasteringError(f"RoEx error {r.status_code}: {r.text[:200]}", "engine")
            task_id = (r.json() or {}).get("mastering_task_id") or ""
            if not task_id:
                raise MasteringError("RoEx did not return a task id.", "engine")

            # Опрос: вебхуков у нас нет (нужен публичный обработчик), поэтому
            # ждём поллингом. Задача идёт минутами, не секундами.
            deadline = time.time() + ROEX_TIMEOUT_S
            last = ""
            preview_ready = False
            while time.time() < deadline:
                time.sleep(ROEX_POLL_S)
                pr = client.post(f"{ROEX_API}/retrievepreviewmaster",
                                 json={"masteringData": {"masteringTaskId": task_id}},
                                 headers=headers)
                if pr.status_code == 200:
                    res = (pr.json() or {}).get("previewMasterTaskResults") or {}
                    if res.get("download_url_mastered_preview"):
                        preview_ready = True
                        break
                last = f"{pr.status_code}: {pr.text[:150]}"
            if not preview_ready:
                raise MasteringError(
                    f"RoEx did not finish in {int(ROEX_TIMEOUT_S)}s. Last answer — {last}",
                    "timeout")

            fr = client.post(f"{ROEX_API}/retrievefinalmaster",
                             json={"masteringData": {"masteringTaskId": task_id}},
                             headers=headers)
            if fr.status_code >= 400:
                raise MasteringError(
                    f"RoEx final master error {fr.status_code}: {fr.text[:200]}", "engine")
            final_url = (fr.json() or {}).get("finalMasterTaskResults") or ""
            if isinstance(final_url, dict):
                final_url = final_url.get("download_url_mastered") or ""
            if not final_url:
                raise MasteringError("RoEx did not return the master file link.", "engine")

            tmp = dst if dst.endswith(".wav") else dst + ".tmp.wav"
            with client.stream("GET", str(final_url), timeout=600.0) as resp:
                if resp.status_code >= 400:
                    raise MasteringError(
                        f"could not download the master: {resp.status_code}", "network")
                with open(tmp, "wb") as f:
                    for chunk in resp.iter_bytes():
                        f.write(chunk)
            if os.path.getsize(tmp) < 1000:
                raise MasteringError("RoEx returned an empty file", "engine")
            return tmp
    except httpx.HTTPError as e:
        raise MasteringError(f"RoEx is unreachable: {e}", "network")


# ─────────────────────────────── оркестровка ───────────────────────────────

def engines(reference: bool = False) -> list:
    """Чем можно мастерить прямо сейчас — для интерфейса.

    price_hint — не цена в очках (она в main.py), а маркер «нам это стоит
    денег или нет»: витрина обязана честно показывать платную кнопку."""
    mh = matchering_health()
    return [
        {"id": "matchering", "title": "Match a reference track",
         "ready": bool(mh.get("ok")), "needs_reference": True, "paid": False,
         "note": "Matches EQ, level and stereo width to a track you choose as the sound you want.",
         "detail": mh},
        {"id": "ffmpeg", "title": "Loudness only",
         "ready": True, "needs_reference": False, "paid": False,
         "note": "Brings the track to streaming loudness and limits true peaks. No tone changes."},
        {"id": "roex", "title": "Cloud mastering (paid)",
         "ready": roex_available(), "needs_reference": False, "paid": True,
         "note": "RoEx Tonn: genre-tuned mastering on their servers. Costs real money per track."},
    ]


def _report(engine: str, before: dict, after: dict, extra: str = "") -> str:
    """Отчёт человеку. Только то, что реально произошло — никакой магии.

    Три числа: громкость, пик, диапазон. По ним видно и результат, и цену:
    если диапазон ужался вдвое, значит трек стал громче за счёт динамики,
    и человек имеет право это знать."""
    what = {
        "matchering": "Reference match: EQ curve, level, peak amplitude and stereo width "
                      "pulled towards your reference track, then a brick-wall limiter. "
                      "Classic DSP, no neural network, your mix is not re-recorded.",
        "ffmpeg": "Loudness normalisation and true-peak limiting only. "
                  "Nothing in the tone or the balance of your mix was changed.",
        "roex": "Cloud mastering by RoEx Tonn: corrective EQ, multiband dynamics "
                "and limiting tuned to the genre you picked.",
    }.get(engine, "Processed.")
    lines = [
        what,
        "Loudness {} → {} LUFS · true peak {} → {} dBTP · dynamic range {} → {} LU.".format(
            before.get("lufs"), after.get("lufs"),
            before.get("true_peak"), after.get("true_peak"),
            before.get("lra"), after.get("lra"),
        ),
    ]
    if extra:
        lines.append(extra)
    lines.append("A master cannot fix a mix — if something is off in the balance, "
                 "it stays off, only louder.")
    return "\n".join(lines)


def master_sync(src_filename: str, *, engine: str = "auto",
                reference_filename: str = "", target: str = "streaming",
                style: str = "HIPHOP_GRIME", roex_loudness: str = "MEDIUM") -> dict:
    """Полный проход мастеринга. Синхронный: вызывается из фонового потока.

    src_filename / reference_filename — имена файлов в UPLOAD_DIR (не пути:
    наружу мы никогда не принимаем путь).

    Возвращает {"filename", "engine", "note", "before", "after", "paid"}.
    """
    src_path = os.path.join(UPLOAD_DIR, os.path.basename(src_filename or ""))
    if not src_filename or not os.path.exists(src_path):
        raise MasteringError("The source track is missing.", "input")
    if not audio.has_audio(src_path):
        raise MasteringError("This file has no audio stream.", "input")

    ref_path = ""
    if reference_filename:
        ref_path = os.path.join(UPLOAD_DIR, os.path.basename(reference_filename))
        if not os.path.exists(ref_path):
            raise MasteringError("The reference track is missing.", "input")

    target_i, target_tp = LOUDNESS_TARGETS.get(target, (TARGET_LUFS, TARGET_TP))

    # Выбор движка. auto: эталон есть и сервис живой → matchering, иначе
    # ffmpeg. roex НИКОГДА не выбирается сам — только явной кнопкой: это
    # единственный вариант, который тратит деньги владельца.
    chosen = engine or "auto"
    fallback_note = ""
    if chosen == "auto":
        if ref_path and matchering_available() and matchering_health().get("ok"):
            chosen = "matchering"
        else:
            chosen = "ffmpeg"
    if chosen == "matchering" and not ref_path:
        chosen = "ffmpeg"
        fallback_note = ("No reference track was given, so only loudness was matched. "
                         "Upload a track you want to sound like for a full match.")
    if chosen == "matchering" and not matchering_health().get("ok"):
        chosen = "ffmpeg"
        fallback_note = ("The reference-matching engine is offline right now, "
                         "so only loudness was matched.")
    if chosen == "roex" and not roex_available():
        raise MasteringError("Cloud mastering is not connected: ROEX_API_KEY is not set.",
                             "disabled")

    work = os.path.join(UPLOAD_DIR, f"master_{uuid.uuid4().hex}")
    os.makedirs(work, exist_ok=True)
    try:
        before = measure(src_path)
        ext = ".wav" if MASTER_OUTPUT != "mp3" else ".mp3"
        name = f"master_{uuid.uuid4().hex}{ext}"
        out_path = os.path.join(UPLOAD_DIR, name)

        if chosen == "matchering":
            # Оба файла — в один формат: matchering сравнивает спектры, и
            # разная частота дискретизации на входе только мешает.
            src_wav = audio.to_wav(src_path, os.path.join(work, "target.wav"), MASTER_SR, 24)
            ref_wav = audio.to_wav(ref_path, os.path.join(work, "reference.wav"), MASTER_SR, 24)
            produced = _matchering_master(src_wav, ref_wav, os.path.join(work, "out.wav"))
        elif chosen == "roex":
            produced_url = public_source_url(os.path.basename(src_filename))
            produced = _roex_master(produced_url, os.path.join(work, "out.wav"),
                                    style, roex_loudness)
        else:
            # Промежуточный файл всегда WAV: если итог просят в mp3, лучше
            # закодировать один раз в самом конце, чем дважды подряд.
            produced = _ffmpeg_master(src_path, os.path.join(work, "out.wav"),
                                      target_i, target_tp, before)

        # Приведение к итоговому формату одним проходом — заодно чинит
        # случай, когда движок отдал не тот sample rate.
        codec = (["-c:a", "pcm_s24le"] if ext == ".wav"
                 else ["-c:a", "libmp3lame", "-b:a", "320k"])
        r = subprocess.run(
            [FFMPEG, "-y", "-v", "error", "-i", produced, "-ar", str(MASTER_SR)]
            + codec + [out_path],
            capture_output=True, text=True, timeout=1800,
        )
        if r.returncode != 0 or not os.path.exists(out_path):
            raise MasteringError(f"could not write the master file: {r.stderr[-200:]}", "ffmpeg")

        after = measure(out_path)
        return {
            "filename": name,
            "engine": chosen,
            "paid": chosen == "roex",
            "note": _report(chosen, before, after, fallback_note),
            "before": {k: before[k] for k in ("lufs", "true_peak", "lra")},
            "after": {k: after[k] for k in ("lufs", "true_peak", "lra")},
            "target": target,
        }
    finally:
        shutil.rmtree(work, ignore_errors=True)


def status() -> dict:
    """Сводка раздела для интерфейса."""
    return {
        "engines": engines(),
        "targets": sorted(LOUDNESS_TARGETS.keys()),
        "styles": list(ROEX_STYLES),
        "output": "wav" if MASTER_OUTPUT != "mp3" else "mp3",
        "sample_rate": MASTER_SR,
    }
