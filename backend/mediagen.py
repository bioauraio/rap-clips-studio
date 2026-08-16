"""Картинка кадра и анимация кадра — через host-шлюзы BIOAURA (та же
подписочная инфраструктура, что у контент-конвейера: ChatGPT/Grok по
подписке, ключей и денег с нашей стороны не тратится).

Картинки: чистый HTTP, prompt → base64, никакой файловой связи с организмом.
Видео (Grok /animate): единственный provider пока (Seedance — позже, см.
VIDEO_PROVIDERS). Шлюзу нужен ХОСТОВЫЙ путь к файлу картинки (он сам процесс
на хосте, не в докере) и он пишет результат в SVOЙ фиксированный каталог —
общие /uploads организма. Поэтому video-пайплайн копирует готовый mp4 в
собственное хранилище rap-clips-studio и стирает оригинал из организма:
ролик генерится чужими руками (общая Grok-сессия), но живёт только у нас.
"""
from __future__ import annotations

import base64
import os
import shutil
import time
import uuid

import httpx

IMAGE_GATEWAY_URL = os.environ.get("IMAGE_GATEWAY_URL", "http://172.18.0.1:8766") + "/generate"
GROK_GATEWAY_URL = os.environ.get("GROK_GATEWAY_URL", "http://172.18.0.1:8767")
# Хостовый путь до НАШЕГО /data (bind-mount, см. docker-compose.yml) — Grok
# читает картинку с диска хоста, поэтому нужен путь снаружи контейнера.
HOST_DATA_DIR = os.environ.get("HOST_DATA_DIR", "/opt/rapclips/data")
# Куда Grok физически пишет готовое видео (общий с организмом каталог) — и
# откуда мы его тут же забираем себе и удаляем оригинал.
ORGANISM_UPLOADS_CONTAINER = os.environ.get("ORGANISM_UPLOADS_DIR", "/organism-uploads")

IMAGE_TIMEOUT = httpx.Timeout(200.0, connect=15.0)
VIDEO_TIMEOUT = httpx.Timeout(560.0, connect=15.0)


class MediaError(RuntimeError):
    pass


async def generate_image(prompt: str) -> tuple[bytes, str]:
    """ChatGPT-подписка → фолбэк Grok, оба возвращают чистые байты картинки."""
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


VIDEO_PROVIDERS = ("grok",)  # "seedance" добавится сюда же, тем же контрактом


async def animate_scene(prompt: str, image_container_path: str, provider: str = "grok") -> str:
    """Возвращает ИМЯ ФАЙЛА готового mp4 в НАШЕМ /data/uploads."""
    if provider != "grok":
        raise MediaError(f"провайдер видео пока не подключён: {provider}")

    rel = os.path.relpath(image_container_path, "/data")
    host_image_path = os.path.join(HOST_DATA_DIR, rel)

    async with httpx.AsyncClient(timeout=VIDEO_TIMEOUT) as client:
        r = await client.post(f"{GROK_GATEWAY_URL}/animate", json={
            "prompt": prompt, "image_path": host_image_path,
            "request_id": f"rapclips-{uuid.uuid4().hex}",
        })
    if r.status_code != 200:
        raise MediaError(f"Grok animate {r.status_code}: {r.text[:250]}")
    data = r.json()
    video_url = data.get("video_url") or ""
    if not video_url:
        raise MediaError(f"Grok animate: пустой video_url ({data})")

    # Файл сейчас лежит в общем /uploads организма (см. модуль-докстринг) —
    # копируем к себе и подчищаем за собой.
    src_name = os.path.basename(video_url)
    src_path = os.path.join(ORGANISM_UPLOADS_CONTAINER, src_name)
    for _ in range(20):  # запись на диск после ответа шлюза — секундная гонка
        if os.path.exists(src_path):
            break
        time.sleep(0.5)
    if not os.path.exists(src_path):
        raise MediaError(f"Grok отчитался об успехе, но файла нет: {src_path}")

    dst_name = f"scene_{uuid.uuid4().hex}.mp4"
    dst_path = os.path.join("/data/uploads", dst_name)
    shutil.copyfile(src_path, dst_path)
    try:
        os.remove(src_path)
    except OSError:
        pass
    return dst_name
