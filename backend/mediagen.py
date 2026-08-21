"""Медиа-слой: картинки кадров, видео сцен, нарезка аудио, сборка клипа.

КАДРЫ — движки в IMAGE_ENGINES:
  * chatgpt / grok — host-шлюзы BIOAURA по подписке владельца, стоят нам ноль,
    но принимают ОДИН референс (отсюда hstack-коллаж в main.py) и живут в
    одной браузерной сессии на весь Организм.
  * nano-banana / nano-banana-2 / nano-banana-pro — Google Gemini Image через
    агрегатор kie.ai. Берут до 8-14 ОТДЕЛЬНЫХ референсов (коллаж не нужен),
    отдают нативную вертикаль 1K/2K/4K и держат параллельные задачи.
    Платные: тариф решает, кому они достаются.

ВИДЕО — движки в VIDEO_ENGINES:
  * grok — host-шлюз по подписке, бесплатен, но оживляет ТОЛЬКО первый кадр.
  * seedance-2-mini / 2-0 / 2-5, kling-3.0(-pro), minimax-h3 — через kie.ai,
    все умеют ПЕРВЫЙ и ПОСЛЕДНИЙ кадр: сцена интерполируется между двумя
    нашими картинками, отсюда связный монтаж.
  * seevio.ai и официальный klingai.com остаются АВАРИЙНЫМИ каналами.

Себестоимость каждого движка в долларах лежит прямо в реестрах — из этих же
чисел main.py собирает цену сцены в токенах, поэтому прайс не может разойтись
с реальными расходами.

ffmpeg (есть в образе) режет аудиодорожку под каждую сцену и собирает
утверждённые сцены в цельный клип на трек.
"""
from __future__ import annotations

import asyncio
import base64
import json
import os
import shutil
import subprocess
import time
import uuid

import logging

import httpx
import threading

# ─────────────────────── id последней внешней задачи ───────────────────────
# Списание токенов происходит В МОМЕНТ постановки задачи (main.py), а taskId
# у kie/seevio/kling появляется здесь, минутой позже и на несколько кадров
# стека глубже. Протаскивать его обратно через сигнатуры пяти функций
# незачем: генерация каждой сцены живёт в СВОЁМ треде (Thread + asyncio.run),
# поэтому threading.local хранит ровно ту задачу, которую этот тред и завёл.
# main.py забирает её через last_task_id() и дописывает в строку журнала —
# так спорная генерация разбирается по цепочке «списание → задача → ошибка».
_task_local = threading.local()


def note_task(task_id) -> None:
    _task_local.task_id = str(task_id or "")[:80]


def last_task_id() -> str:
    return getattr(_task_local, "task_id", "")


def reset_task() -> None:
    _task_local.task_id = ""


IMAGE_GATEWAY_URL = os.environ.get("IMAGE_GATEWAY_URL", "http://172.18.0.1:8766") + "/generate"
GROK_GATEWAY_URL = os.environ.get("GROK_GATEWAY_URL", "http://172.18.0.1:8767")

# Хостовый путь до нашего /data (bind-mount) — им пользуется ТОЛЬКО инстанс,
# стоящий на одной машине со шлюзами (msk). Публичный инстанс на 5.42.120.67
# ходит к шлюзам по SSH-туннелю и общей файловой системы с ними не имеет:
# путь там не существует, отсюда «reference image not found» в журнале шлюза.
# Поэтому картинки шлём БАЙТАМИ, а путь оставляем вторым полем для
# совместимости со шлюзом, который ещё не обновлён.
HOST_DATA_DIR = os.environ.get("HOST_DATA_DIR", "/opt/rapclips/data")
# Транспортная копия кадра для шлюзов. Кадры у нас 4К после upscale_to_4k
# (PNG 2160x3840, 8-20 МБ), а Grok рендерит 480p/720p — больше 1536 px по
# длинной стороне ему бесполезно. Без ужатия 60 сцен клипа дали бы больше
# гигабайта base64 через туннель; с ужатием — 20-40 МБ на клип.
GATEWAY_IMAGE_MAX_SIDE = int(os.environ.get("GATEWAY_IMAGE_MAX_SIDE", "1536"))
GATEWAY_IMAGE_MAX_KB = int(os.environ.get("GATEWAY_IMAGE_MAX_KB", "900"))
ORGANISM_UPLOADS_CONTAINER = os.environ.get("ORGANISM_UPLOADS_DIR", "/organism-uploads")
UPLOAD_DIR = os.environ.get("UPLOAD_DIR", "/data/uploads")
FFMPEG = os.environ.get("FFMPEG_BIN", "ffmpeg")
FFPROBE = os.environ.get("FFPROBE_BIN", "ffprobe")

# Seedance — оплаченный аккаунт владельца на seevio.ai: настоящий REST API
# (Seedance 2.5, image-to-video по первому+последнему кадру). Кадры seevio
# скачивает сам по image_urls — на время задачи кладём копии в публичный
# outbox под uuid-именами и подчищаем после.
SEEVIO_API_KEY = os.environ.get("SEEVIO_API_KEY", "")
SEEVIO_API = os.environ.get("SEEVIO_API", "https://api.seevio.ai")
SEEVIO_MODEL = os.environ.get("SEEVIO_MODEL", "seedance-2-5")
# 480p вдвое дешевле 720p по кредитам — экономный режим переключается в .env
# без пересборки: SEEVIO_RESOLUTION=480p.
SEEVIO_RESOLUTION = os.environ.get("SEEVIO_RESOLUTION", "720p")
PUBLIC_BASE_URL = os.environ.get("PUBLIC_BASE_URL", "https://clips.resetaura.io")
OUTBOX_DIR = os.environ.get("OUTBOX_DIR", "/data/outbox")
os.makedirs(OUTBOX_DIR, exist_ok=True)
SEEDANCE_TIMEOUT_S = float(os.environ.get("SEEDANCE_TIMEOUT_S", "900"))

# kie.ai — агрегатор: один ключ и один баланс на Nano Banana (кадры), Seedance
# и Kling (видео). Дешевле прямых каналов и не требует второго кошелька.
#   KIE_API_KEY  — ключ из kie.ai/api-key;
#   kling        — официальный API klingai.com (api-singapore) остаётся
#                  АВАРИЙНЫМ каналом: он дороже kie на 17-21 %.
KIE_API_KEY = os.environ.get("KIE_API_KEY", "")
KIE_API = os.environ.get("KIE_API", "https://api.kie.ai")
KIE_TIMEOUT_S = float(os.environ.get("KIE_TIMEOUT_S", "900"))
# Как часто опрашиваем задачу. Вебхук (callBackUrl) не используем: у сервиса
# один домен и лишний публичный роут-приёмник тут не окупается.
KIE_POLL_S = float(os.environ.get("KIE_POLL_S", "10"))
# Кадры готовятся быстрее видео, а их на трёхминутный клип шестьдесят: опрос
# раз в 10 секунд накидывал бы минуты пустого ожидания на ровном месте.
KIE_POLL_IMAGE_S = float(os.environ.get("KIE_POLL_IMAGE_S", "3"))
KLING_ACCESS_KEY = os.environ.get("KLING_ACCESS_KEY", "")
KLING_SECRET_KEY = os.environ.get("KLING_SECRET_KEY", "")
KLING_API = os.environ.get("KLING_API", "https://api-singapore.klingai.com")
KLING_MODEL = os.environ.get("KLING_MODEL", "kling-v2-6")
KLING_TIMEOUT_S = float(os.environ.get("KLING_TIMEOUT_S", "900"))

# Кадры Nano Banana: разрешение (1K|2K|4K) и порядок движков картинок.
# 2K — рабочий компромисс: нативная вертикаль без апскейла и вдвое дешевле 4K.
NANO_RESOLUTION = os.environ.get("NANO_RESOLUTION", "2K")
# Порядок фолбэка движков кадров, если явный не задан вызовом.
IMAGE_PROVIDER_ORDER = os.environ.get("IMAGE_PROVIDER_ORDER", "").strip()

IMAGE_TIMEOUT = httpx.Timeout(200.0, connect=15.0)
VIDEO_TIMEOUT = httpx.Timeout(560.0, connect=15.0)

# Итоговое разрешение кадров/клипа. 9:16. «4К» по высоте — апскейл ffmpeg:
# сами генераторы столько не отдают, поэтому честно апскейлим lanczos'ом.
FRAME_W = int(os.environ.get("FRAME_W", "2160"))
FRAME_H = int(os.environ.get("FRAME_H", "3840"))
CLIP_W = int(os.environ.get("CLIP_W", "1080"))
CLIP_H = int(os.environ.get("CLIP_H", "1920"))

# Аспекты, которые умеем: вертикаль (клип, ролик, серия), квадрат (карточка
# маркетплейса) и 4:5 (лента). До режима мокапов «9:16» было ВПИСАНО
# константой в шесть мест этого файла — спорить было не с чем, все режимы
# вертикальные. Как только появился квадрат, каждое из этих мест начало бы
# молча дописывать чёрные поля или возвращать вертикаль вместо квадрата.
ASPECTS = {"9:16": (9, 16), "1:1": (1, 1), "4:5": (4, 5)}
DEFAULT_ASPECT = "9:16"


def norm_aspect(aspect: str) -> str:
    a = str(aspect or "").strip()
    return a if a in ASPECTS else DEFAULT_ASPECT


def _fit(long_side: int, aspect: str) -> tuple[int, int]:
    """Размер кадра под аспект при заданной ДЛИННОЙ стороне. Чётные значения:
    libx264 с нечётной стороной не соберётся."""
    w, h = ASPECTS[norm_aspect(aspect)]
    if h >= w:
        out_h, out_w = long_side, int(round(long_side * w / h))
    else:
        out_w, out_h = long_side, int(round(long_side * h / w))
    return out_w - (out_w % 2), out_h - (out_h % 2)


def frame_size(aspect: str = DEFAULT_ASPECT) -> tuple[int, int]:
    """Размер апскейла кадра. Длинная сторона — прежняя FRAME_H (4K по высоте),
    поэтому у вертикали ничего не меняется вообще."""
    return _fit(FRAME_H, aspect)


def clip_size(aspect: str = DEFAULT_ASPECT) -> tuple[int, int]:
    return _fit(CLIP_H, aspect)


log = logging.getLogger("rapclips.media")


class MediaError(RuntimeError):
    pass


def kie_available() -> bool:
    """kie.ai доступен, когда задан ключ агрегатора."""
    return bool(KIE_API_KEY)


def seedance_available() -> bool:
    """Seedance доступен через kie.ai (основной канал) или seevio (запасной)."""
    return bool(KIE_API_KEY or SEEVIO_API_KEY)


def kling_available() -> bool:
    """Kling доступен, если задан ключ агрегатора или пара ключей официального API."""
    return bool(KIE_API_KEY or (KLING_ACCESS_KEY and KLING_SECRET_KEY))


def video_providers() -> list[str]:
    """Семейства движков видео — словарь, который знает фронт: grok/seedance/kling.
    Конкретную модель внутри семейства выбирает тариф (см. VIDEO_ENGINES)."""
    out = []
    if seedance_available():
        out.append("seedance")
    if kling_available():
        out.append("kling")
    out.append("grok")
    return out


# ─────────────────────── реестр движков (правда о цене) ───────────────────────
# Один реестр на весь сервис: id движка → как его вызвать и сколько он стоит
# НАМ в долларах. Тарификация в токенах собирается из этих же чисел (main.py),
# поэтому цена в токенах физически не может разойтись с себестоимостью.
#
# Цены — прайс kie.ai (август 2026), сцена 6 секунд, вертикаль 9:16, без звука.
# ВАЖНО: это ПРАЙС-ЛИСТ, а не факт. Реальный расход (creditsConsumed) виден в
# kie.ai/logs — по нему цифры калибруются после первых прогонов.

IMAGE_ENGINES: dict[str, dict] = {
    # Шлюзы владельца: подписка уже оплачена, генерация стоит нам ноль.
    "chatgpt": {
        "title": "ChatGPT (gateway)", "channel": "gateway",
        "usd": 0.0, "max_refs": 1, "native_4k": False, "paid": False,
    },
    "grok": {
        "title": "Grok (gateway)", "channel": "gateway",
        "usd": 0.0, "max_refs": 1, "native_4k": False, "paid": False,
    },
    # Nano Banana (Google Gemini Image) через kie.ai. Смысл не в Elo — по
    # слепым тестам GPT Image 2 со шлюза даже выше, — а в инженерии: до 14
    # ОТДЕЛЬНЫХ референсов вместо склеенного коллажа, нативная вертикаль
    # 1K/2K/4K без ffmpeg-апскейла и параллельные задачи вместо одной
    # браузерной сессии на весь Организм.
    "nano-banana": {
        "title": "Nano Banana (edit)", "channel": "kie",
        "model": "google/nano-banana-edit", "refs_field": "image_urls",
        "usd": 0.02, "max_refs": 10, "native_4k": False, "paid": True,
        # ПРЕДПОЛОЖЕНИЕ: у edit-модели поля aspect_ratio в доках нет — не шлём.
        "resolutions": (), "aspect": False,
    },
    "nano-banana-2": {
        "title": "Nano Banana 2", "channel": "kie",
        "model": "nano-banana-2", "refs_field": "image_input",
        # 1K $0.04 / 2K $0.06 / 4K $0.09 — цену берём под выбранное разрешение.
        "usd": 0.06, "usd_by_res": {"1K": 0.04, "2K": 0.06, "4K": 0.09},
        "max_refs": 14, "native_4k": True, "paid": True,
        "resolutions": ("1K", "2K", "4K"), "aspect": True,
    },
    "nano-banana-pro": {
        "title": "Nano Banana Pro", "channel": "kie",
        "model": "nano-banana-pro", "refs_field": "image_input",
        # 1K и 2K по $0.09, 4K $0.12.
        "usd": 0.09, "usd_by_res": {"1K": 0.09, "2K": 0.09, "4K": 0.12},
        "max_refs": 8, "native_4k": True, "paid": True,
        "resolutions": ("1K", "2K", "4K"), "aspect": True,
    },
}

GATEWAY_IMAGE_ENGINES = ("chatgpt", "grok")


def image_engine_usd(engine: str, resolution: str = "") -> float:
    """Себестоимость ОДНОЙ картинки на этом движке в долларах."""
    spec = IMAGE_ENGINES.get(engine) or {}
    by_res = spec.get("usd_by_res") or {}
    res = (resolution or NANO_RESOLUTION).upper()
    return float(by_res.get(res, spec.get("usd", 0.0)))


def image_engines_live() -> list[str]:
    """Что реально можно вызвать прямо сейчас (по ключам)."""
    out = list(GATEWAY_IMAGE_ENGINES)
    if kie_available():
        out += [k for k, v in IMAGE_ENGINES.items() if v["channel"] == "kie"]
    return out


def resolve_image_engine(wanted: str) -> str:
    """Движок кадров, который реально отработает. Нет ключа kie → молча
    возвращаем шлюз: сцена не должна падать из-за ненастроенного агрегатора,
    но наверх (в /api/providers) уходит именно ЭТОТ, честный ответ."""
    wanted = (wanted or "").strip()
    if wanted in IMAGE_ENGINES and wanted in image_engines_live():
        return wanted
    return "chatgpt"


# ──────────────────────────── kie.ai: общий протокол ────────────────────────────

def _kie_headers() -> dict:
    return {"Authorization": f"Bearer {KIE_API_KEY}", "Content-Type": "application/json"}


async def _kie_upload(client: httpx.AsyncClient, path: str) -> str:
    """Свой файл → ссылка, по которой его скачает kie.ai.

    Раньше здесь был base64-аплоад в /api/file-base64-upload, но у kie такой
    ручки НЕТ: на живом ключе она отдаёт 404 (проверены и /api/v1/... варианты).
    Из-за этого молча падали все платные движки — Seedance и Kling.

    Поэтому отдаём ПОДПИСАННУЮ ВРЕМЕННУЮ ссылку на свой файл (роут /pub/{token}
    в main.py): подпись нашим секретом, срок час. Движку хватает скачать кадр,
    а приватный файл не превращается в вечную публичную раздачу."""
    fname = os.path.basename(path)
    base = (os.environ.get("PUBLIC_BASE_URL", "") or "").rstrip("/")
    if not base.startswith("https://"):
        # kie ходит к нам снаружи: без публичного https он кадр не заберёт.
        raise MediaError(
            "kie: не задан публичный адрес сервиса (PUBLIC_BASE_URL) — "
            "движку неоткуда скачать кадр")
    try:
        from main import pub_file_url  # локальный импорт: mediagen грузится раньше main
        url = pub_file_url(fname)
    except Exception as e:  # noqa: BLE001 — не роняем генерацию из-за импорта
        raise MediaError(f"kie: не смог собрать ссылку на кадр ({type(e).__name__})")
    if not url:
        raise MediaError("kie: пустая ссылка на кадр")
    return url


async def _kie_result_urls(model: str, payload_input: dict, *, timeout_s: float,
                           poll_s: float = 0.0) -> list[str]:
    """createTask → poll recordInfo → список готовых ссылок.

    Результат у kie лежит СТРОКОЙ JSON в data.resultJson — разбираем именно
    её, а не выцепляем ссылку регуляркой из всего словаря: в data.param
    приезжает наш же запрос, и регулярка ловила ссылку на входной кадр."""
    body = {"model": model, "input": payload_input}
    async with httpx.AsyncClient(timeout=httpx.Timeout(120.0, connect=15.0)) as client:
        r = await client.post(f"{KIE_API}/api/v1/jobs/createTask", json=body,
                              headers=_kie_headers())
        if r.status_code == 402:
            raise MediaError(f"kie.ai insufficient_credits: {r.text[:200]}")
        if r.status_code == 429:
            # Задача НЕ встаёт в очередь при 429 — это отказ, а не задержка.
            raise MediaError(f"kie.ai rate limit 429: {r.text[:200]}")
        if r.status_code not in (200, 201, 202):
            raise MediaError(f"kie.ai submit {r.status_code}: {r.text[:250]}")
        data = r.json() or {}
        if int(data.get("code") or 200) not in (200, 0):
            msg = str(data.get("msg") or data)[:200]
            if "credit" in msg.lower():
                raise MediaError(f"kie.ai insufficient_credits: {msg}")
            raise MediaError(f"kie.ai отказал: {msg}")
        inner = data.get("data") or {}
        task_id = inner.get("taskId") or inner.get("task_id") or data.get("taskId")
        if not task_id:
            raise MediaError(f"kie.ai: нет taskId ({str(data)[:200]})")
        note_task(task_id)

        deadline = time.time() + timeout_s
        while time.time() < deadline:
            await asyncio.sleep(poll_s or KIE_POLL_S)
            sr = await client.get(f"{KIE_API}/api/v1/jobs/recordInfo",
                                  params={"taskId": task_id}, headers=_kie_headers())
            if sr.status_code != 200:
                continue
            sd = (sr.json() or {}).get("data") or {}
            state = str(sd.get("state") or "").lower()
            if state == "fail":
                reason = sd.get("failMsg") or sd.get("failCode") or "без причины"
                text = str(reason)
                if "credit" in text.lower() or "insufficient" in text.lower():
                    raise MediaError(f"kie.ai insufficient_credits: {text[:200]}")
                raise MediaError(f"kie.ai: задача упала — {text[:200]}")
            if state == "success":
                raw = sd.get("resultJson") or "{}"
                try:
                    parsed = json.loads(raw) if isinstance(raw, str) else (raw or {})
                except (ValueError, TypeError):
                    parsed = {}
                urls = parsed.get("resultUrls") or parsed.get("result_urls") or []
                if not urls:
                    raise MediaError(f"kie.ai: пустой результат ({str(sd)[:200]})")
                log.info("kie.ai %s: готово, списано кредитов %s",
                         model, sd.get("creditsConsumed"))
                return [str(u) for u in urls]
    raise MediaError(f"kie.ai: таймаут ожидания результата ({model})")


# ──────────────────────────── картинки ────────────────────────────

async def _nano_banana(prompt: str, ref_paths: list[str], engine: str,
                       resolution: str, aspect: str) -> tuple[bytes, str]:
    """Кадр через Nano Banana на kie.ai.

    Референсы уходят ОТДЕЛЬНЫМИ картинками (до max_refs), а не склейкой:
    именно ради этого движок и подключался — hstack-коллаж модель periodически
    воспроизводила как сетку прямо в кадре."""
    spec = IMAGE_ENGINES[engine]
    urls: list[str] = []
    async with httpx.AsyncClient(timeout=httpx.Timeout(180.0, connect=15.0)) as client:
        for pth in ref_paths[: int(spec["max_refs"])]:
            if pth and os.path.exists(pth):
                urls.append(await _kie_upload(client, pth))
    inp: dict = {"prompt": prompt, "output_format": "png"}
    if urls:
        inp[spec["refs_field"]] = urls
    if spec.get("resolutions"):
        res = (resolution or NANO_RESOLUTION).upper()
        if res not in spec["resolutions"]:
            res = spec["resolutions"][0]
        inp["resolution"] = res
    if spec.get("aspect"):
        inp["aspect_ratio"] = norm_aspect(aspect)
    out = await _kie_result_urls(spec["model"], inp, timeout_s=KIE_TIMEOUT_S,
                                 poll_s=KIE_POLL_IMAGE_S)
    async with httpx.AsyncClient(timeout=httpx.Timeout(180.0, connect=15.0),
                                 follow_redirects=True) as client:
        ir = await client.get(out[0])
    if ir.status_code != 200 or len(ir.content) < 1000:
        raise MediaError(f"kie.ai: кадр не скачался ({ir.status_code})")
    mime = ir.headers.get("content-type", "image/png").split(";")[0].strip()
    if not mime.startswith("image/"):
        mime = "image/png"
    return ir.content, mime


async def generate_image_ex(
    prompt: str,
    reference_path: str | None = None,
    *,
    reference_paths: list[str] | None = None,
    engine: str = "",
    resolution: str = "",
    aspect: str = DEFAULT_ASPECT,
) -> dict:
    """Кадр + честный ответ, ЧЕМ он нарисован.

    Возвращает {"data","mime","engine","native_4k"}. native_4k=True значит,
    что движок отдал нативный 4K и ffmpeg-апскейл кадру уже не нужен.

    reference_path — старый одиночный референс (коллаж моделек);
    reference_paths — список отдельных картинок, его понимает только
    Nano Banana; шлюзам всё равно достанется первая (у них вход на одну).

    Порядок движков: запрошенный → ChatGPT-шлюз → Grok-шлюз. Падение kie
    не роняет сцену, а тихо откатывается на подписочные шлюзы."""
    errors: list[str] = []
    refs = [p for p in (reference_paths or []) if p and os.path.exists(p)]
    if not refs and reference_path and os.path.exists(reference_path):
        refs = [reference_path]
    single_ref = reference_path if (reference_path and os.path.exists(reference_path)) \
        else (refs[0] if refs else None)

    async def _chatgpt() -> dict | None:
        payload: dict = {"prompt": prompt}
        try:
            # ChatGPT-шлюз умеет референс напрямую (reference_image_b64) —
            # модель держит лицо/предмет с фото, не только Grok. Через общий
            # helper: он ужимает 4К-кадр, который шлюз всё равно отверг бы
            # своим лимитом 12 МБ. Готовим ВНУТРИ try — сбой подготовки должен
            # уйти в цепочку фолбэков, а не мимо неё.
            if single_ref:
                payload["reference_image_b64"], payload["reference_mime"] = \
                    await asyncio.to_thread(_gateway_image_field, single_ref)
            async with httpx.AsyncClient(timeout=IMAGE_TIMEOUT) as client:
                r = await client.post(IMAGE_GATEWAY_URL, json=payload)
            if r.status_code == 200:
                data = r.json()
                return {"data": base64.b64decode(data["image_b64"]),
                        "mime": data.get("mime", "image/png"),
                        "engine": "chatgpt", "native_4k": False}
            errors.append(f"ChatGPT-шлюз {r.status_code}: {r.text[:150]}")
        except Exception as e:  # noqa: BLE001
            errors.append(f"ChatGPT-шлюз недоступен: {e}")
        return None

    async def _grok() -> dict | None:
        payload: dict = {"prompt": prompt}
        try:
            if single_ref:
                # Байты — основной вход (работают с любого сервера), путь
                # остаётся рядом для шлюза, который ещё не обновлён: старый
                # прочитает путь и проигнорирует image_b64, новый предпочтёт
                # байты. Поэтому порядок выкладки шлюза и сервиса не важен.
                payload["image_b64"], payload["image_mime"] = \
                    await asyncio.to_thread(_gateway_image_field, single_ref)
                payload["image_path"] = _host_path(single_ref)
            async with httpx.AsyncClient(timeout=IMAGE_TIMEOUT) as client:
                r = await client.post(f"{GROK_GATEWAY_URL}/generate_image", json=payload)
            if r.status_code == 200:
                data = r.json()
                return {"data": base64.b64decode(data["image_b64"]),
                        "mime": data.get("mime", "image/jpeg"),
                        "engine": "grok", "native_4k": False}
            errors.append(f"Grok-шлюз {r.status_code}: {r.text[:150]}")
        except Exception as e:  # noqa: BLE001
            errors.append(f"Grok-шлюз недоступен: {e}")
        return None

    async def _nano(engine_id: str) -> dict | None:
        try:
            data, mime = await _nano_banana(prompt, refs, engine_id, resolution, aspect)
            res = (resolution or NANO_RESOLUTION).upper()
            return {"data": data, "mime": mime, "engine": engine_id,
                    "native_4k": res == "4K"}
        except Exception as e:  # noqa: BLE001
            errors.append(f"{IMAGE_ENGINES[engine_id]['title']}: {str(e)[:200]}")
        return None

    # Порядок: явно запрошенный движок, дальше — бесплатные шлюзы владельца.
    order: list[str] = []
    wanted = (engine or "").strip()
    if wanted and wanted in IMAGE_ENGINES:
        order.append(wanted)
    elif IMAGE_PROVIDER_ORDER:
        order += [x.strip() for x in IMAGE_PROVIDER_ORDER.split(",") if x.strip()]
    for fallback in GATEWAY_IMAGE_ENGINES:
        if fallback not in order:
            order.append(fallback)

    live = image_engines_live()
    for engine_id in order:
        if engine_id not in live:
            continue
        if engine_id == "chatgpt":
            result = await _chatgpt()
        elif engine_id == "grok":
            result = await _grok()
        else:
            result = await _nano(engine_id)
        if result:
            return result
    raise MediaError(" / ".join(errors) or "нет ни одного живого движка кадров")


async def generate_image(prompt: str, reference_path: str | None = None,
                         **kwargs) -> tuple[bytes, str]:
    """Совместимая обёртка старого контракта: (байты, mime)."""
    res = await generate_image_ex(prompt, reference_path, **kwargs)
    return res["data"], res["mime"]


def upscale_to_4k(path: str, aspect: str = DEFAULT_ASPECT) -> None:
    """Апскейл кадра до 4К по длинной стороне, на месте (генераторы столько
    не дают). Аспект обязателен параметром: квадрат, пропущенный через
    вертикальный pad, приезжает с чёрными полями сверху и снизу — и именно
    в таком виде уходит в карточку товара."""
    fw, fh = frame_size(aspect)
    tmp = path + ".up.png"
    cmd = [
        FFMPEG, "-y", "-i", path,
        "-vf", f"scale={fw}:{fh}:force_original_aspect_ratio=decrease:flags=lanczos,"
               f"pad={fw}:{fh}:(ow-iw)/2:(oh-ih)/2:color=black",
        tmp,
    ]
    r = subprocess.run(cmd, capture_output=True, timeout=180)
    if r.returncode == 0 and os.path.exists(tmp):
        os.replace(tmp, path)
    else:
        if os.path.exists(tmp):
            os.remove(tmp)


# ──────────────────────────── аудио сцены ────────────────────────────

def slice_audio(track_audio_path: str, start_sec: float, duration_sec: float) -> str:
    """Режет отрывок трека под сцену. Возвращает имя файла в UPLOAD_DIR."""
    if not os.path.exists(track_audio_path):
        raise MediaError("у трека нет аудиофайла — нечего резать")
    out_name = f"cut_{uuid.uuid4().hex}.m4a"
    out_path = os.path.join(UPLOAD_DIR, out_name)
    cmd = [
        FFMPEG, "-y", "-ss", str(start_sec), "-t", str(duration_sec),
        "-i", track_audio_path, "-vn", "-c:a", "aac", "-b:a", "192k", out_path,
    ]
    r = subprocess.run(cmd, capture_output=True, timeout=180)
    if r.returncode != 0 or not os.path.exists(out_path):
        raise MediaError(f"ffmpeg не нарезал аудио: {r.stderr.decode()[-200:]}")
    return out_name


# ──────────────────────────── видео сцены ────────────────────────────

def _data_url(path: str) -> str:
    mime = "image/png" if path.lower().endswith(".png") else "image/jpeg"
    with open(path, "rb") as f:
        return f"data:{mime};base64," + base64.b64encode(f.read()).decode("ascii")


def _outbox_publish(path: str) -> tuple[str, str]:
    """Копия кадра под uuid-именем в outbox -> (публичный URL, имя копии)."""
    name = f"ob_{uuid.uuid4().hex}{os.path.splitext(path)[1] or '.png'}"
    shutil.copyfile(path, os.path.join(OUTBOX_DIR, name))
    return f"{PUBLIC_BASE_URL}/api/outbox/{name}", name


async def _animate_seedance(
    prompt: str, first_path: str, last_path: str | None, duration_sec: int,
    model: str = "",
) -> str:
    """seevio.ai (Seedance 2.5): image-to-video по первому и последнему кадру.

    Задача асинхронная: submit -> poll /v1/tasks/{id}. Звук не генерим —
    поверх ляжет дорожка трека. Ролик длиннее сцены подрезаем ffmpeg'ом."""
    if not SEEVIO_API_KEY:
        raise MediaError("нет SEEVIO_API_KEY — добавь ключ seevio в infra/.env")
    headers = {"Authorization": f"Bearer {SEEVIO_API_KEY}"}
    outbox: list[str] = []
    try:
        first_url, name1 = _outbox_publish(first_path)
        outbox.append(name1)
        image_urls = [first_url]
        if last_path and os.path.exists(last_path):
            last_url, name2 = _outbox_publish(last_path)
            outbox.append(name2)
            image_urls.append(last_url)
        payload = {
            "model": model or SEEVIO_MODEL,
            "input": {
                "prompt": prompt,
                "generation_type": "image-to-video",
                "image_urls": image_urls,
                "duration": max(4, min(30, int(round(duration_sec)))),
                "resolution": SEEVIO_RESOLUTION,
                "generate_audio": False,
                "watermark": False,
            },
        }
        async with httpx.AsyncClient(timeout=httpx.Timeout(60.0, connect=15.0)) as client:
            r = await client.post(f"{SEEVIO_API}/v1/videos/generations", json=payload, headers=headers)
        if r.status_code not in (200, 201, 202):
            raise MediaError(f"seevio submit {r.status_code}: {r.text[:250]}")
        data = r.json() or {}
        inner = data.get("data") or {}
        task_id = (data.get("taskId") or data.get("task_id") or data.get("id")
                   or inner.get("taskId") or inner.get("task_id") or inner.get("id"))
        if not task_id:
            raise MediaError(f"seevio: нет task_id в ответе ({str(data)[:200]})")
        note_task(task_id)

        deadline = time.time() + SEEDANCE_TIMEOUT_S
        video_url = ""
        async with httpx.AsyncClient(timeout=httpx.Timeout(60.0, connect=15.0)) as client:
            while time.time() < deadline:
                await asyncio.sleep(10)
                tr = await client.get(f"{SEEVIO_API}/v1/tasks/{task_id}", headers=headers)
                if tr.status_code != 200:
                    continue
                td = tr.json() or {}
                status = td.get("status") or (td.get("data") or {}).get("status") or ""
                if status == "failed":
                    reason = ((td.get("data") or {}).get("failed_reason")
                              or td.get("failed_reason") or "без причины")
                    raise MediaError(f"seevio: задача упала — {str(reason)[:200]}")
                results = ((td.get("data") or {}).get("results") or td.get("results") or [])
                if status == "completed" and results:
                    video_url = str(results[0])
                    break
        if not video_url:
            raise MediaError("seevio: таймаут ожидания видео")

        dst_name = f"scene_{uuid.uuid4().hex}.mp4"
        dst = os.path.join(UPLOAD_DIR, dst_name)
        async with httpx.AsyncClient(timeout=httpx.Timeout(300.0, connect=15.0), follow_redirects=True) as client:
            vr = await client.get(video_url)
            if vr.status_code != 200 or len(vr.content) < 50_000:
                raise MediaError(f"seevio: не скачалось видео ({vr.status_code}, {len(vr.content)} байт)")
            with open(dst, "wb") as f:
                f.write(vr.content)
        # seevio отдаёт минимум 4с — сцену короче подрезаем к её длительности.
        if duration_sec and duration_sec >= 2:
            trimmed = dst + ".trim.mp4"
            r2 = subprocess.run(
                [FFMPEG, "-y", "-i", dst, "-t", str(duration_sec),
                 "-c:v", "libx264", "-preset", "veryfast", "-crf", "18",
                 "-an", "-pix_fmt", "yuv420p", trimmed],
                capture_output=True, timeout=300,
            )
            if r2.returncode == 0 and os.path.exists(trimmed):
                os.replace(trimmed, dst)
        return dst_name
    finally:
        for name in outbox:
            try:
                os.remove(os.path.join(OUTBOX_DIR, name))
            except OSError:
                pass


def _host_path(container_path: str) -> str:
    """Путь внутри контейнера -> путь на хосте: шлюзы живут вне докера.

    Осмыслен ТОЛЬКО когда шлюз стоит на этой же машине. Публичному инстансу
    он не помогает — там картинку везут байтами (_gateway_image_field)."""
    return os.path.join(HOST_DATA_DIR, os.path.relpath(container_path, "/data"))


def _gateway_image_field(path: str) -> tuple[str, str]:
    """Картинка для шлюза в виде (base64, mime).

    Байты работают с ЛЮБОГО сервера, в отличие от пути. Крупные кадры ужимаем
    до GATEWAY_IMAGE_MAX_SIDE: шлюзам больше не нужно (Grok рендерит 480p/720p,
    а ChatGPT-шлюз вообще режет референс на 12 МБ и на сыром 4К падал), зато
    тащить 15 МБ на каждую сцену по туннелю дорого.

    Ужимаем через ffmpeg, а не Pillow: ffmpeg тут и так основной обработчик
    картинок (upscale_to_4k, _ref_collage), а Pillow в requirements.txt нет."""
    try:
        size_kb = os.path.getsize(path) // 1024
    except OSError:
        size_kb = 0
    src = path
    tmp = ""
    if size_kb > GATEWAY_IMAGE_MAX_KB:
        tmp = os.path.join(UPLOAD_DIR, f"gw_{uuid.uuid4().hex}.jpg")
        # Ужать не удалось — это НЕ повод ронять сцену: шлём оригинал, пусть
        # дорого. Ловим и падение ffmpeg, и его отсутствие, и таймаут: иначе
        # исключение прошло бы мимо цепочки фолбэков движков.
        try:
            # Кадры у нас ВЕРТИКАЛЬНЫЕ (9:16), поэтому ограничивать надо не
            # ширину, а длинную сторону: min() по обеим сторонам задаёт рамку
            # (и не даёт увеличить мелкую картинку), а decrease вписывает в неё
            # с сохранением пропорций. Тот же приём, что в upscale_to_4k.
            r = subprocess.run(
                [FFMPEG, "-y", "-i", path, "-vf",
                 f"scale='min({GATEWAY_IMAGE_MAX_SIDE},iw)':"
                 f"'min({GATEWAY_IMAGE_MAX_SIDE},ih)':"
                 "force_original_aspect_ratio=decrease:flags=lanczos",
                 "-q:v", "3", tmp],
                capture_output=True, timeout=120,
            )
            err = r.stderr.decode("utf-8", "replace")[-200:]
            ok = r.returncode == 0 and os.path.exists(tmp)
        except (OSError, subprocess.SubprocessError) as e:
            err, ok = str(e)[:200], False
        if ok:
            src = tmp
        else:
            log.warning("транспортная копия не собралась, шлём оригинал: %s", err)
    try:
        with open(src, "rb") as f:
            data = f.read()
    finally:
        # Убираем и недоделанный кусок: упавший ffmpeg мог оставить обрезок.
        if tmp:
            try:
                os.remove(tmp)
            except OSError:
                pass
    mime = "image/png" if data[:4] == b"\x89PNG" else "image/jpeg"
    return base64.b64encode(data).decode(), mime


async def _animate_grok(prompt: str, first_path: str) -> str:
    # Подготовка кадра — тоже часть канала: её сбой обязан выглядеть как
    # MediaError, иначе он пролетит мимо разбора ошибок в animate_scene.
    try:
        image_b64, image_mime = await asyncio.to_thread(_gateway_image_field, first_path)
    except OSError as e:
        raise MediaError(f"Grok animate: не смог прочитать кадр {first_path}: {e}") from e
    async with httpx.AsyncClient(timeout=VIDEO_TIMEOUT) as client:
        r = await client.post(f"{GROK_GATEWAY_URL}/animate", json={
            "prompt": prompt,
            # Кадр туда — байтами (работает с любого сервера).
            "image_b64": image_b64, "image_mime": image_mime,
            # Путь рядом: старый шлюз возьмёт его и не заметит байтов.
            "image_path": _host_path(first_path),
            # Ролик обратно — тоже байтами: общего тома с шлюзом у публичного
            # инстанса нет. Старый шлюз поле игнорирует, и мы читаем том, как
            # читали раньше.
            "return_video": True,
            "request_id": f"rapclips-{uuid.uuid4().hex}",
        })
    if r.status_code != 200:
        raise MediaError(f"Grok animate {r.status_code}: {r.text[:250]}")
    data = r.json() or {}
    video_url = data.get("video_url") or ""
    if not video_url:
        raise MediaError("Grok animate: пустой video_url")

    dst_name = f"scene_{uuid.uuid4().hex}.mp4"
    dst_path = os.path.join(UPLOAD_DIR, dst_name)
    src_path = os.path.join(ORGANISM_UPLOADS_CONTAINER, os.path.basename(video_url))

    video_b64 = data.get("video_b64") or ""
    if video_b64:
        try:
            payload = base64.b64decode(video_b64, validate=True)
        except (ValueError, TypeError) as e:
            raise MediaError(f"Grok animate: битый video_b64 ({e})") from e
        if len(payload) < 1000:
            raise MediaError(f"Grok animate: обрезанный ролик, {len(payload)} байт")
        with open(dst_path, "wb") as f:
            f.write(payload)
        # На msk шлюз пишет тот же ролик в общий том; байты мы уже забрали,
        # поэтому файл там надо подчистить — иначе он копится вечно.
        try:
            os.remove(src_path)
        except OSError:
            pass
        return dst_name

    # Старый шлюз (или return_video отключён): забираем файл с общего тома.
    for _ in range(20):
        if os.path.exists(src_path):
            break
        await asyncio.sleep(0.5)
    if not os.path.exists(src_path):
        raise MediaError(
            f"Grok отчитался об успехе, но файла нет: {src_path}. "
            "Похоже, шлюз ещё не умеет return_video, а общего тома с ним у "
            "этого инстанса нет — обнови grok_gateway.py на хосте шлюзов")

    shutil.copyfile(src_path, dst_path)
    try:
        os.remove(src_path)
    except OSError:
        pass
    return dst_name


def _kling_jwt() -> str:
    """Официальный Kling авторизуется коротким JWT (HS256) из пары ключей."""
    import base64 as _b64
    import hashlib as _hl
    import hmac as _hmac
    import json as _json
    now = int(time.time())
    header = {"alg": "HS256", "typ": "JWT"}
    payload = {"iss": KLING_ACCESS_KEY, "exp": now + 1800, "nbf": now - 5}

    def seg(obj: dict) -> bytes:
        raw = _json.dumps(obj, separators=(",", ":")).encode()
        return _b64.urlsafe_b64encode(raw).rstrip(b"=")

    signing = seg(header) + b"." + seg(payload)
    sig = _hmac.new(KLING_SECRET_KEY.encode(), signing, _hl.sha256).digest()
    return (signing + b"." + _b64.urlsafe_b64encode(sig).rstrip(b"=")).decode()


# ─────────────────────── реестр движков видео ───────────────────────
# Схемы полей у моделей kie.ai РАЗНЫЕ, общего inp не существует: Seedance и
# MiniMax ждут first_frame_url/last_frame_url, Kling — массив image_urls,
# у части моделей вообще нет aspect_ratio. Поэтому у каждого движка свой
# сборщик тела запроса, а не один словарь на всех.
#
# first_last=False — движок НЕ умеет хвостовой кадр: наш монтаж на нём
# деградирует до «оживления первого кадра», и это надо честно показывать.

def _body_seedance(prompt: str, first: str, last: str | None, dur: int,
                   spec: dict, aspect: str = DEFAULT_ASPECT) -> dict:
    inp = {
        "prompt": prompt,
        "first_frame_url": first,
        "duration": max(4, min(30, int(round(dur)))),
        "resolution": spec.get("resolution", "720p"),
        "aspect_ratio": norm_aspect(aspect),
        # По умолчанию Seedance генерит звук — это и лишние деньги, и мусорная
        # дорожка под наш трек.
        "generate_audio": False,
    }
    if last:
        inp["last_frame_url"] = last
    return inp


def _body_kling(prompt: str, first: str, last: str | None, dur: int,
                spec: dict, aspect: str = DEFAULT_ASPECT) -> dict:
    # У Kling 3.0 duration — СТРОКА, кадры лежат массивом [первый, последний],
    # а multi_shots обязателен: с ним модель режет ролик на планы сама.
    urls = [first] + ([last] if last else [])
    return {
        "prompt": prompt,
        "image_urls": urls,
        "duration": str(max(3, min(15, int(round(dur))))),
        "mode": spec.get("mode", "std"),
        "aspect_ratio": norm_aspect(aspect),
        "multi_shots": False,
        # sound у Kling ОБЯЗАТЕЛЕН и строго булев: без поля API отвечает 422
        # «sound cannot be empty», со строкой «off» — 500 «it must be a boolean».
        # Звук нам не нужен: сверху ложится трек пользователя.
        "sound": False,
    }


def _body_minimax(prompt: str, first: str, last: str | None, dur: int,
                  spec: dict, aspect: str = DEFAULT_ASPECT) -> dict:
    inp = {
        "prompt": prompt,
        "first_frame_url": first,
        "duration": max(4, min(15, int(round(dur)))),
        "resolution": spec.get("resolution", "768p"),
    }
    if last:
        inp["last_frame_url"] = last
    return inp


VIDEO_ENGINES: dict[str, dict] = {
    # Шлюз владельца: стоит нам ноль, но оживляет ТОЛЬКО первый кадр.
    "grok": {
        "title": "Grok (gateway)", "family": "grok", "channel": "gateway",
        "usd_6s": 0.0, "first_last": False, "paid": False,
        "note": "Наша подписка. Оживляет только первый кадр сцены.",
    },
    # Seedance 2 Mini — топовое семейство арены по цене входного билета,
    # и оно умеет первый+последний кадр. Рабочая лошадь платных тарифов.
    "seedance-2-mini": {
        "title": "Seedance 2 Mini · 720p", "family": "seedance", "channel": "kie",
        "model": "bytedance/seedance-2-mini", "body": _body_seedance,
        "resolution": "720p", "usd_6s": 0.246, "first_last": True, "paid": True,
        "note": "Интерполяция между первым и последним кадром, 720p.",
    },
    "seedance-2-0": {
        "title": "Seedance 2.0 · 720p", "family": "seedance", "channel": "kie",
        "model": "bytedance/seedance-2", "body": _body_seedance,
        "resolution": "720p", "usd_6s": 1.23, "first_last": True, "paid": True,
        "note": "Первое место слепой арены image-to-video. Дорогая.",
    },
    "seedance-2-5": {
        "title": "Seedance 2.5 · 720p", "family": "seedance", "channel": "kie",
        "model": "bytedance/seedance-2-5", "body": _body_seedance,
        "resolution": "720p", "usd_6s": 1.89, "first_last": True, "paid": True,
        "note": "Самая дорогая позиция прайса. Витринный движок, не поточный.",
    },
    "seedance-2-5-480": {
        "title": "Seedance 2.5 · 480p", "family": "seedance", "channel": "kie",
        "model": "bytedance/seedance-2-5", "body": _body_seedance,
        "resolution": "480p", "usd_6s": 0.84, "first_last": True, "paid": True,
        "note": "Та же модель вдвое дешевле — ценой разрешения.",
    },
    "kling-3.0": {
        "title": "Kling 3.0 · 720p", "family": "kling", "channel": "kie",
        "model": "kling-3.0/video", "body": _body_kling, "mode": "std",
        "usd_6s": 0.42, "first_last": True, "paid": True,
        "note": "Kling 3.0 в стандартном режиме, первый+последний кадр.",
    },
    "kling-3.0-pro": {
        "title": "Kling 3.0 Pro · 1080p", "family": "kling", "channel": "kie",
        "model": "kling-3.0/video", "body": _body_kling, "mode": "pro",
        "usd_6s": 0.54, "first_last": True, "paid": True,
        "note": "Kling 3.0 Pro, 1080p, первый+последний кадр.",
    },
    "minimax-h3": {
        "title": "MiniMax H3 · 768p", "family": "seedance", "channel": "kie",
        "model": "minimax-h3/image-to-video", "body": _body_minimax,
        "resolution": "768p", "usd_6s": 0.48, "first_last": True, "paid": True,
        "note": "Второе место арены. Пока не выведен на витрину.",
    },
}


def video_engine_usd(engine: str, duration_sec: int = 6) -> float:
    """Себестоимость сцены такой длительности. Прайс у нас за 6 секунд —
    другие длины считаем пропорционально (у kie цена посекундная)."""
    spec = VIDEO_ENGINES.get(engine) or {}
    base = float(spec.get("usd_6s", 0.0))
    if not duration_sec or duration_sec == 6:
        return base
    return round(base * (float(duration_sec) / 6.0), 4)


def video_engine_live(engine: str) -> bool:
    """Движок реально вызываем прямо сейчас?"""
    spec = VIDEO_ENGINES.get(engine)
    if not spec:
        return False
    if spec["channel"] == "gateway":
        return True
    if spec["family"] == "kling":
        return bool(KIE_API_KEY or (KLING_ACCESS_KEY and KLING_SECRET_KEY))
    return bool(KIE_API_KEY or SEEVIO_API_KEY)


def video_engines_live() -> list[str]:
    return [k for k in VIDEO_ENGINES if video_engine_live(k)]


async def _animate_via_kie(engine: str, prompt: str, first_path: str,
                           last_path: str | None, duration_sec: int,
                           aspect: str = DEFAULT_ASPECT) -> str:
    """Единый путь через агрегатор kie.ai — там живут и Seedance, и Kling.

    Один ключ, один баланс и одинаковый протокол задач. Кадры уходят
    base64-аплоадом на их же хранилище: наш outbox светил файлы наружу и
    зависел от PUBLIC_BASE_URL, который у сервиса исторически чужой."""
    spec = VIDEO_ENGINES.get(engine)
    if not spec or spec["channel"] != "kie":
        raise MediaError(f"движок {engine!r} не ходит через kie.ai")
    if not (first_path and os.path.exists(first_path)):
        raise MediaError(f"нет файла первого кадра: {first_path}")
    async with httpx.AsyncClient(timeout=httpx.Timeout(180.0, connect=15.0)) as client:
        first_url = await _kie_upload(client, first_path)
        last_url = ""
        # Хвостовой кадр отправляем ТОЛЬКО тем, кто его понимает: остальным он
        # либо игнорируется, либо ломает валидацию запроса.
        if spec.get("first_last") and last_path and os.path.exists(last_path):
            last_url = await _kie_upload(client, last_path)
    inp = spec["body"](prompt, first_url, last_url or None, duration_sec, spec,
                       norm_aspect(aspect))
    urls = await _kie_result_urls(spec["model"], inp, timeout_s=KIE_TIMEOUT_S)
    return await _fetch_video(urls[0], duration_sec)


async def _fetch_video(video_url: str, duration_sec: int) -> str:
    """Скачивает готовый ролик и подрезает под длительность сцены."""
    dst_name = f"scene_{uuid.uuid4().hex}.mp4"
    dst = os.path.join(UPLOAD_DIR, dst_name)
    async with httpx.AsyncClient(timeout=httpx.Timeout(300.0, connect=15.0),
                                 follow_redirects=True) as client:
        vr = await client.get(video_url)
        if vr.status_code != 200 or len(vr.content) < 50_000:
            raise MediaError(f"видео не скачалось ({vr.status_code})")
        with open(dst, "wb") as f:
            f.write(vr.content)
    if duration_sec and duration_sec >= 2:
        trimmed = dst + ".trim.mp4"
        r2 = subprocess.run(
            [FFMPEG, "-y", "-i", dst, "-t", str(duration_sec),
             "-c:v", "libx264", "-preset", "veryfast", "-crf", "18",
             "-an", "-pix_fmt", "yuv420p", trimmed],
            capture_output=True, timeout=300,
        )
        if r2.returncode == 0 and os.path.exists(trimmed):
            os.replace(trimmed, dst)
    return dst_name


async def _animate_kling_official(prompt: str, first_path: str, last_path: str | None,
                                  duration_sec: int,
                                  aspect: str = DEFAULT_ASPECT) -> str:
    """АВАРИЙНЫЙ канал Kling: официальный API klingai.com по паре ключей.

    Основной путь — kie.ai: там та же модель дешевле на 17-21 %, и это один
    баланс с Nano Banana и Seedance вместо второго кошелька. Эта ветка живёт
    на случай, когда агрегатор лёг или у него кончились кредиты."""
    if not (KLING_ACCESS_KEY and KLING_SECRET_KEY):
        raise MediaError("официальный Kling недоступен: нет KLING_ACCESS_KEY/KLING_SECRET_KEY")
    outbox: list[str] = []
    try:
        first_url, n1 = _outbox_publish(first_path)
        outbox.append(n1)
        tail_url = ""
        if last_path and os.path.exists(last_path):
            tail_url, n2 = _outbox_publish(last_path)
            outbox.append(n2)
        # Официальный API принимает длительность строкой и только 5 или 10 секунд.
        dur = 10 if duration_sec > 7 else 5

        headers = {"Authorization": f"Bearer {_kling_jwt()}"}
        payload: dict = {
            "model_name": KLING_MODEL, "prompt": prompt, "image": first_url,
            "duration": str(dur), "aspect_ratio": norm_aspect(aspect), "mode": "std",
        }
        if tail_url:
            payload["image_tail"] = tail_url
        submit_url = f"{KLING_API}/v1/videos/image2video"
        status_url = f"{KLING_API}/v1/videos/image2video/"

        async with httpx.AsyncClient(timeout=httpx.Timeout(60.0, connect=15.0)) as client:
            r = await client.post(submit_url, json=payload, headers=headers)
        if r.status_code not in (200, 201, 202):
            raise MediaError(f"Kling submit {r.status_code}: {r.text[:250]}")
        data = r.json() or {}
        inner = data.get("data") or {}
        task_id = (inner.get("task_id") or inner.get("taskId") or inner.get("id")
                   or data.get("task_id") or data.get("taskId"))
        if not task_id:
            raise MediaError(f"Kling: нет task_id ({str(data)[:200]})")
        note_task(task_id)

        deadline = time.time() + KLING_TIMEOUT_S
        video_url = ""
        async with httpx.AsyncClient(timeout=httpx.Timeout(60.0, connect=15.0)) as client:
            while time.time() < deadline:
                await asyncio.sleep(10)
                sr = await client.get(f"{status_url}{task_id}", headers=headers)
                if sr.status_code != 200:
                    continue
                sd = (sr.json() or {}).get("data") or {}
                state = str(sd.get("task_status") or sd.get("state") or "").lower()
                if state in ("fail", "failed", "error"):
                    reason = sd.get("task_status_msg") or sd.get("failMsg") or "без причины"
                    raise MediaError(f"Kling: задача упала — {str(reason)[:200]}")
                if state in ("succeed", "success", "completed"):
                    videos = ((sd.get("task_result") or {}).get("videos") or [])
                    if videos:
                        video_url = str(videos[0].get("url") or "")
                    if not video_url:
                        raise MediaError(f"Kling: пустой результат ({str(sd)[:200]})")
                    break
        if not video_url:
            raise MediaError("Kling: таймаут ожидания видео")
        return await _fetch_video(video_url, duration_sec)
    finally:
        for name in outbox:
            try:
                os.remove(os.path.join(OUTBOX_DIR, name))
            except OSError:
                pass


def _no_credits(err: Exception) -> bool:
    """Отказ «нет денег» — единственная ошибка, при которой откат на Grok
    честен: у платного канала кончился баланс, а сцена всё равно должна выйти."""
    text = str(err).lower()
    return ("insufficient_credits" in text or "insufficient" in text
            or " 402" in text or "credit" in text)


def resolve_video_engine(provider: str, engine: str = "") -> str:
    """Семейство (grok/seedance/kling) + желаемая модель → конкретный движок.

    Фронт пока знает только семейства, конкретную модель внутри семейства
    задаёт тариф. Незнакомое имя не роняем — берём самый дешёвый живой
    движок этого семейства."""
    engine = (engine or "").strip()
    if engine in VIDEO_ENGINES:
        return engine
    family = (provider or "grok").strip()
    if family == "grok":
        return "grok"
    same = [k for k, v in VIDEO_ENGINES.items() if v["family"] == family]
    if not same:
        return "grok"
    return sorted(same, key=lambda k: VIDEO_ENGINES[k]["usd_6s"])[0]


async def animate_scene(
    *, prompt: str, first_path: str, last_path: str | None,
    duration_sec: int, provider: str, seedance_model: str = "", engine: str = "",
    aspect: str = DEFAULT_ASPECT,
) -> str:
    """Возвращает имя mp4 в UPLOAD_DIR.

    Приоритет каналов: kie.ai (дешевле, один ключ) → seevio (запасной, только
    Seedance) → официальный Kling (запасной, только Kling) → Grok-шлюз
    (последний рубеж, оживляет лишь первый кадр).

    seedance_model — легаси-имя модели из тарифа; engine — новый явный id
    движка из VIDEO_ENGINES, он побеждает."""
    engine_id = resolve_video_engine(provider, engine or seedance_model)
    spec = VIDEO_ENGINES.get(engine_id) or VIDEO_ENGINES["grok"]

    if engine_id == "grok" or spec["channel"] == "gateway":
        return await _animate_grok(prompt, first_path)

    if not video_engine_live(engine_id):
        raise MediaError(
            f"{spec['title']} недоступен: нет KIE_API_KEY "
            f"(создай ключ на kie.ai/api-key и добавь в infra/.env)")

    # 1. Основной канал — kie.ai.
    if KIE_API_KEY:
        try:
            return await _animate_via_kie(engine_id, prompt, first_path, last_path,
                                          duration_sec, aspect)
        except MediaError as e:
            log.warning("kie.ai не смог (%s): %s", engine_id, str(e)[:200])
            kie_error = e
    else:
        kie_error = MediaError("нет KIE_API_KEY")

    # 2. Запасные каналы своего семейства.
    if spec["family"] == "seedance" and SEEVIO_API_KEY:
        try:
            return await _animate_seedance(prompt, first_path, last_path, duration_sec,
                                           model=seedance_model or SEEVIO_MODEL)
        except MediaError as e:
            if not _no_credits(e):
                raise
            log.warning("seevio без кредитов, откатываюсь на Grok: %s", str(e)[:200])
            return await _animate_grok(prompt, first_path)
    if spec["family"] == "kling" and KLING_ACCESS_KEY and KLING_SECRET_KEY:
        try:
            return await _animate_kling_official(prompt, first_path, last_path,
                                                 duration_sec, aspect)
        except MediaError as e:
            if not _no_credits(e):
                raise
            log.warning("официальный Kling без кредитов, откатываюсь на Grok: %s", str(e)[:200])
            return await _animate_grok(prompt, first_path)

    # 3. Кончились деньги у агрегатора — сцену всё равно отдаём (через Grok);
    # любая другая ошибка уходит наверх, чтобы её было видно, а не замазано.
    if _no_credits(kie_error):
        log.warning("kie.ai без кредитов, откатываюсь на Grok: %s", str(kie_error)[:200])
        return await _animate_grok(prompt, first_path)
    raise kie_error


# ──────────────────────────── сборка клипа ────────────────────────────

def assemble_clip(scene_videos: list[str], track_audio_path: str | None,
                  film_grain: bool = False, aspect: str = DEFAULT_ASPECT) -> str:
    """Склеивает видео утверждённых сцен подряд и кладёт поверх дорожку трека.

    Каждая сцена приводится к единому размеру/фпс — иначе concat рассыпается
    на разных источниках (Seedance и Grok отдают разные размеры)."""
    if not scene_videos:
        raise MediaError("нет утверждённых сцен для сборки")
    cw, ch = clip_size(aspect)
    work = os.path.join(UPLOAD_DIR, f"build_{uuid.uuid4().hex}")
    os.makedirs(work, exist_ok=True)
    try:
        normalized = []
        for i, name in enumerate(scene_videos):
            src = os.path.join(UPLOAD_DIR, name)
            dst = os.path.join(work, f"n{i:03d}.mp4")
            cmd = [
                FFMPEG, "-y", "-i", src,
                "-vf", f"scale={cw}:{ch}:force_original_aspect_ratio=decrease:flags=lanczos,"
                       f"pad={cw}:{ch}:(ow-iw)/2:(oh-ih)/2:color=black,fps=30"
                       # Плёнка: живое зерно + лёгкий прижим контраста, как 16мм скан.
                       + (",noise=alls=11:allf=t+u,eq=contrast=1.04:saturation=0.93" if film_grain else ""),
                "-an", "-c:v", "libx264", "-preset", "veryfast", "-crf", "18",
                "-pix_fmt", "yuv420p", dst,
            ]
            r = subprocess.run(cmd, capture_output=True, timeout=600)
            if r.returncode != 0 or not os.path.exists(dst):
                raise MediaError(f"ffmpeg не нормализовал сцену {name}: {r.stderr.decode()[-200:]}")
            normalized.append(dst)

        listfile = os.path.join(work, "list.txt")
        with open(listfile, "w") as f:
            for p in normalized:
                f.write(f"file '{p}'\n")

        silent = os.path.join(work, "silent.mp4")
        r = subprocess.run(
            [FFMPEG, "-y", "-f", "concat", "-safe", "0", "-i", listfile, "-c", "copy", silent],
            capture_output=True, timeout=600,
        )
        if r.returncode != 0 or not os.path.exists(silent):
            raise MediaError(f"ffmpeg не склеил сцены: {r.stderr.decode()[-200:]}")

        out_name = f"clip_{uuid.uuid4().hex}.mp4"
        out_path = os.path.join(UPLOAD_DIR, out_name)
        if track_audio_path and os.path.exists(track_audio_path):
            cmd = [
                FFMPEG, "-y", "-i", silent, "-i", track_audio_path,
                "-c:v", "copy", "-c:a", "aac", "-b:a", "192k",
                "-map", "0:v:0", "-map", "1:a:0", "-shortest", out_path,
            ]
        else:
            cmd = [FFMPEG, "-y", "-i", silent, "-c", "copy", out_path]
        r = subprocess.run(cmd, capture_output=True, timeout=600)
        if r.returncode != 0 or not os.path.exists(out_path):
            raise MediaError(f"ffmpeg не наложил звук: {r.stderr.decode()[-200:]}")
        return out_name
    finally:
        shutil.rmtree(work, ignore_errors=True)
