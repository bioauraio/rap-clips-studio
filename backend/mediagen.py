"""Медиа-слой: картинки кадров, видео сцен, нарезка аудио, сборка клипа.

Картинки — через host-шлюзы BIOAURA (ChatGPT/Grok по подписке), чистый HTTP.
Видео — два провайдера, оба по ПОДПИСКЕ (ключей не покупаем):
  * seedance — host-шлюз с живым UI Dreamina (infra/seedance_gateway.py).
    Умеет ПЕРВЫЙ и ПОСЛЕДНИЙ кадр: сцена интерполируется между двумя нашими
    картинками, отсюда связный монтаж вместо отдельного «оживления» одного
    кадра. Доступен, только когда в шлюзе живая сессия владельца.
  * grok — резервный, оживляет ТОЛЬКО первый кадр (последний игнорирует).
    Работает через ту же подписочную сессию, что и контент-конвейер, и пишет
    результат в общий каталог организма — забираем файл себе и стираем оттуда.

ffmpeg (есть в образе) режет аудиодорожку под каждую сцену и собирает
утверждённые сцены в цельный клип на трек.
"""
from __future__ import annotations

import asyncio
import base64
import os
import shutil
import subprocess
import time
import uuid

import httpx

IMAGE_GATEWAY_URL = os.environ.get("IMAGE_GATEWAY_URL", "http://172.18.0.1:8766") + "/generate"
GROK_GATEWAY_URL = os.environ.get("GROK_GATEWAY_URL", "http://172.18.0.1:8767")

# Хостовый путь до нашего /data (bind-mount) — Grok читает файл с диска хоста.
HOST_DATA_DIR = os.environ.get("HOST_DATA_DIR", "/opt/rapclips/data")
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
PUBLIC_BASE_URL = os.environ.get("PUBLIC_BASE_URL", "https://clips.resetaura.io")
OUTBOX_DIR = os.environ.get("OUTBOX_DIR", "/data/outbox")
os.makedirs(OUTBOX_DIR, exist_ok=True)
SEEDANCE_TIMEOUT_S = float(os.environ.get("SEEDANCE_TIMEOUT_S", "900"))

IMAGE_TIMEOUT = httpx.Timeout(200.0, connect=15.0)
VIDEO_TIMEOUT = httpx.Timeout(560.0, connect=15.0)

# Итоговое разрешение кадров/клипа. 9:16. «4К» по высоте — апскейл ffmpeg:
# сами генераторы столько не отдают, поэтому честно апскейлим lanczos'ом.
FRAME_W = int(os.environ.get("FRAME_W", "2160"))
FRAME_H = int(os.environ.get("FRAME_H", "3840"))
CLIP_W = int(os.environ.get("CLIP_W", "1080"))
CLIP_H = int(os.environ.get("CLIP_H", "1920"))


class MediaError(RuntimeError):
    pass


def seedance_available() -> bool:
    """Провайдер доступен, когда задан API-ключ seevio."""
    return bool(SEEVIO_API_KEY)


def video_providers() -> list[str]:
    out = []
    if seedance_available():
        out.append("seedance")
    out.append("grok")
    return out


# ──────────────────────────── картинки ────────────────────────────

async def generate_image(prompt: str, reference_path: str | None = None) -> tuple[bytes, str]:
    """Генерация картинки кадра. Возвращает (байты, mime).

    reference_path — фото-моделька персонажа (путь в контейнере). Референс
    умеет только Grok-шлюз (кладём файл на вход его Imagine-композера), поэтому
    с референсом порядок провайдеров разворачивается: Grok — первым, ChatGPT —
    запасным (уже без модельки, чисто по словесному описанию)."""
    errors = []

    async def _chatgpt() -> tuple[bytes, str] | None:
        payload: dict = {"prompt": prompt}
        # ChatGPT-шлюз умеет референс напрямую (reference_image_b64) — модель
        # держит лицо/предмет с фото, не только Grok.
        if reference_path and os.path.exists(reference_path):
            with open(reference_path, "rb") as f:
                payload["reference_image_b64"] = base64.b64encode(f.read()).decode()
            payload["reference_mime"] = "image/png" if reference_path.lower().endswith(".png") else "image/jpeg"
        try:
            async with httpx.AsyncClient(timeout=IMAGE_TIMEOUT) as client:
                r = await client.post(IMAGE_GATEWAY_URL, json=payload)
            if r.status_code == 200:
                data = r.json()
                return base64.b64decode(data["image_b64"]), data.get("mime", "image/png")
            errors.append(f"ChatGPT-шлюз {r.status_code}: {r.text[:150]}")
        except Exception as e:  # noqa: BLE001
            errors.append(f"ChatGPT-шлюз недоступен: {e}")
        return None

    async def _grok() -> tuple[bytes, str] | None:
        payload: dict = {"prompt": prompt}
        if reference_path and os.path.exists(reference_path):
            payload["image_path"] = _host_path(reference_path)
        try:
            async with httpx.AsyncClient(timeout=IMAGE_TIMEOUT) as client:
                r = await client.post(f"{GROK_GATEWAY_URL}/generate_image", json=payload)
            if r.status_code == 200:
                data = r.json()
                return base64.b64decode(data["image_b64"]), data.get("mime", "image/jpeg")
            errors.append(f"Grok-шлюз {r.status_code}: {r.text[:150]}")
        except Exception as e:  # noqa: BLE001
            errors.append(f"Grok-шлюз недоступен: {e}")
        return None

    # ChatGPT первым всегда: у него выше качество и он теперь тоже умеет референс.
    order = (_chatgpt, _grok)
    for fn in order:
        result = await fn()
        if result:
            return result
    raise MediaError(" / ".join(errors))


def upscale_to_4k(path: str) -> None:
    """Апскейл кадра до вертикального 4К на месте (генераторы столько не дают)."""
    tmp = path + ".up.png"
    cmd = [
        FFMPEG, "-y", "-i", path,
        "-vf", f"scale={FRAME_W}:{FRAME_H}:force_original_aspect_ratio=decrease:flags=lanczos,"
               f"pad={FRAME_W}:{FRAME_H}:(ow-iw)/2:(oh-ih)/2:color=black",
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
            "model": SEEVIO_MODEL,
            "input": {
                "prompt": prompt,
                "generation_type": "image-to-video",
                "image_urls": image_urls,
                "duration": max(4, min(30, int(round(duration_sec)))),
                "resolution": "720p",
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
    """Путь внутри контейнера -> путь на хосте: шлюзы живут вне докера."""
    return os.path.join(HOST_DATA_DIR, os.path.relpath(container_path, "/data"))


async def _animate_grok(prompt: str, first_path: str) -> str:
    host_image_path = _host_path(first_path)
    async with httpx.AsyncClient(timeout=VIDEO_TIMEOUT) as client:
        r = await client.post(f"{GROK_GATEWAY_URL}/animate", json={
            "prompt": prompt, "image_path": host_image_path,
            "request_id": f"rapclips-{uuid.uuid4().hex}",
        })
    if r.status_code != 200:
        raise MediaError(f"Grok animate {r.status_code}: {r.text[:250]}")
    video_url = (r.json() or {}).get("video_url") or ""
    if not video_url:
        raise MediaError("Grok animate: пустой video_url")

    src_path = os.path.join(ORGANISM_UPLOADS_CONTAINER, os.path.basename(video_url))
    for _ in range(20):
        if os.path.exists(src_path):
            break
        time.sleep(0.5)
    if not os.path.exists(src_path):
        raise MediaError(f"Grok отчитался об успехе, но файла нет: {src_path}")

    dst_name = f"scene_{uuid.uuid4().hex}.mp4"
    shutil.copyfile(src_path, os.path.join(UPLOAD_DIR, dst_name))
    try:
        os.remove(src_path)
    except OSError:
        pass
    return dst_name


async def animate_scene(
    *, prompt: str, first_path: str, last_path: str | None,
    duration_sec: int, provider: str,
) -> str:
    """Возвращает имя mp4 в UPLOAD_DIR."""
    if provider == "seedance":
        if not seedance_available():
            raise MediaError(
                "Seedance недоступен: нет SEEVIO_API_KEY — создай ключ в "
                "дашборде seevio.ai и добавь его в infra/.env")
        return await _animate_seedance(prompt, first_path, last_path, duration_sec)
    if provider == "grok":
        return await _animate_grok(prompt, first_path)
    raise MediaError(f"неизвестный провайдер видео: {provider}")


# ──────────────────────────── сборка клипа ────────────────────────────

def assemble_clip(scene_videos: list[str], track_audio_path: str | None,
                  film_grain: bool = False) -> str:
    """Склеивает видео утверждённых сцен подряд и кладёт поверх дорожку трека.

    Каждая сцена приводится к единому размеру/фпс — иначе concat рассыпается
    на разных источниках (Seedance и Grok отдают разные размеры)."""
    if not scene_videos:
        raise MediaError("нет утверждённых сцен для сборки")
    work = os.path.join(UPLOAD_DIR, f"build_{uuid.uuid4().hex}")
    os.makedirs(work, exist_ok=True)
    try:
        normalized = []
        for i, name in enumerate(scene_videos):
            src = os.path.join(UPLOAD_DIR, name)
            dst = os.path.join(work, f"n{i:03d}.mp4")
            cmd = [
                FFMPEG, "-y", "-i", src,
                "-vf", f"scale={CLIP_W}:{CLIP_H}:force_original_aspect_ratio=decrease:flags=lanczos,"
                       f"pad={CLIP_W}:{CLIP_H}:(ow-iw)/2:(oh-ih)/2:color=black,fps=30"
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
