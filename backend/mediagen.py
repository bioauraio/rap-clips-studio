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

# Seedance — по ПОДПИСКЕ через host-шлюз с живым веб-UI Dreamina
# (infra/seedance_gateway.py), как Grok: ключей не покупаем, ходим сессией
# владельца. Шлюз читает картинки с диска хоста и туда же кладёт mp4.
SEEDANCE_GATEWAY_URL = os.environ.get("SEEDANCE_GATEWAY_URL", "http://172.18.0.1:8768")
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
    """Провайдер доступен, только если шлюз поднят И в нём живая сессия."""
    try:
        r = httpx.get(f"{SEEDANCE_GATEWAY_URL}/health", timeout=4.0)
        return r.status_code == 200 and bool((r.json() or {}).get("cookies"))
    except Exception:  # noqa: BLE001
        return False


def video_providers() -> list[str]:
    out = []
    if seedance_available():
        out.append("seedance")
    out.append("grok")
    return out


# ──────────────────────────── картинки ────────────────────────────

async def generate_image(prompt: str) -> tuple[bytes, str]:
    """ChatGPT-подписка → фолбэк Grok. Возвращает (байты, mime)."""
    errors = []
    try:
        async with httpx.AsyncClient(timeout=IMAGE_TIMEOUT) as client:
            r = await client.post(IMAGE_GATEWAY_URL, json={"prompt": prompt})
        if r.status_code == 200:
            data = r.json()
            return base64.b64decode(data["image_b64"]), data.get("mime", "image/png")
        errors.append(f"ChatGPT-шлюз {r.status_code}: {r.text[:150]}")
    except Exception as e:  # noqa: BLE001
        errors.append(f"ChatGPT-шлюз недоступен: {e}")

    try:
        async with httpx.AsyncClient(timeout=IMAGE_TIMEOUT) as client:
            r = await client.post(f"{GROK_GATEWAY_URL}/generate_image", json={"prompt": prompt})
        if r.status_code == 200:
            data = r.json()
            return base64.b64decode(data["image_b64"]), data.get("mime", "image/jpeg")
        errors.append(f"Grok-шлюз {r.status_code}: {r.text[:150]}")
    except Exception as e:  # noqa: BLE001
        errors.append(f"Grok-шлюз недоступен: {e}")

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


async def _animate_seedance(
    prompt: str, first_path: str, last_path: str | None, duration_sec: int,
) -> str:
    """Шлюз драйвит UI Dreamina: первый кадр + последний кадр -> ролик."""
    payload = {
        "prompt": prompt,
        "first_image_path": _host_path(first_path),
        "duration_sec": duration_sec,
    }
    if last_path and os.path.exists(last_path):
        payload["last_image_path"] = _host_path(last_path)

    async with httpx.AsyncClient(timeout=httpx.Timeout(SEEDANCE_TIMEOUT_S, connect=15.0)) as client:
        r = await client.post(f"{SEEDANCE_GATEWAY_URL}/animate", json=payload)
    if r.status_code != 200:
        raise MediaError(f"Seedance {r.status_code}: {r.text[:250]}")
    data = r.json() or {}
    fname = data.get("filename") or ""
    if not fname:
        raise MediaError(f"Seedance: пустой ответ шлюза ({str(data)[:200]})")
    # Шлюз пишет прямо в наш каталог загрузок (SEEDANCE_OUT_DIR), копировать
    # ничего не нужно — файл уже на месте.
    if not os.path.exists(os.path.join(UPLOAD_DIR, fname)):
        raise MediaError(f"Seedance отчитался об успехе, но файла нет: {fname}")
    return fname


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
                "Seedance недоступен: шлюз не поднят или нет сессии "
                "(запусти infra/seedance_login_local.py и войди в Dreamina)")
        return await _animate_seedance(prompt, first_path, last_path, duration_sec)
    if provider == "grok":
        return await _animate_grok(prompt, first_path)
    raise MediaError(f"неизвестный провайдер видео: {provider}")


# ──────────────────────────── сборка клипа ────────────────────────────

def assemble_clip(scene_videos: list[str], track_audio_path: str | None) -> str:
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
                       f"pad={CLIP_W}:{CLIP_H}:(ow-iw)/2:(oh-ih)/2:color=black,fps=30",
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
