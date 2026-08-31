"""Озвучка через ElevenLabs — голос ИИ-блогеров.

Контур за ключом: без ELEVENLABS_API_KEY ручки честно отвечают, что озвучка
не настроена, а не падают. Ключ появится — заработает без правок кода.

Схема: у сцены ролика есть реплика (lyric_line — в UGC/блогерах это текст
ведущего). Озвучка кладётся в audio_filename сцены — то же поле, куда клип
кладёт отрезок трека, поэтому сборка подхватывает голос без изменений.
"""
from __future__ import annotations

import logging
import os
import uuid

import httpx

log = logging.getLogger("rapclips")

ELEVEN_KEY = os.environ.get("ELEVENLABS_API_KEY", "")
ELEVEN_API = os.environ.get("ELEVENLABS_API", "https://api.elevenlabs.io")
# Модель мультиязычная: блогеры говорят по-русски, дефолтная eleven_monolingual
# русский калечит.
ELEVEN_MODEL = os.environ.get("ELEVENLABS_MODEL", "eleven_multilingual_v2")


def available() -> bool:
    return bool(ELEVEN_KEY)


async def list_voices() -> list[dict]:
    """Голоса аккаунта: id, имя, превью. Пусто, если ключа нет."""
    if not ELEVEN_KEY:
        return []
    async with httpx.AsyncClient(timeout=httpx.Timeout(30.0, connect=10.0)) as client:
        r = await client.get(f"{ELEVEN_API}/v1/voices",
                             headers={"xi-api-key": ELEVEN_KEY})
        if r.status_code != 200:
            log.warning("elevenlabs voices %s: %s", r.status_code, r.text[:150])
            return []
        data = r.json() or {}
    return [{"id": v.get("voice_id"), "name": v.get("name") or "",
             "preview_url": v.get("preview_url") or ""}
            for v in (data.get("voices") or [])]


# Словесная манера → настройки ElevenLabs. Точных «эмоций» у API нет:
# эмоцией управляют stability (ниже = экспрессивнее и менее предсказуемо)
# и style (выше = сильнее актёрская подача). Ключевые слова ловим и в
# русской заметке персонажа, и в английской.
_EMOTION_PRESETS = [
    (("спокой", "мягк", "медлен", "ласков", "calm", "soft", "slow", "gentle"),
     {"stability": 0.7, "style": 0.15}),
    (("агресс", "злоб", "ярост", "кричит", "angry", "aggressive", "rage", "shout"),
     {"stability": 0.25, "style": 0.85}),
    (("энерг", "быстр", "задор", "весел", "радост", "energetic", "fast", "happy", "excited"),
     {"stability": 0.35, "style": 0.6}),
    (("груст", "печал", "устал", "sad", "tired", "melancholy"),
     {"stability": 0.6, "style": 0.35}),
    (("шепч", "шёпот", "шепот", "вкрадчив", "whisper", "quiet"),
     {"stability": 0.65, "style": 0.5}),
]


def settings_for(voice_note: str = "", emotion: str = "") -> dict:
    """Настройки голоса из словесной манеры персонажа и эмоции сцены.

    Эмоция сцены главнее манеры по умолчанию: заметка описывает персонажа
    вообще, а эмоция — конкретную реплику."""
    out = {"stability": 0.45, "similarity_boost": 0.8, "style": 0.4,
           "use_speaker_boost": True}
    for source in (voice_note or "", emotion or ""):
        low = source.lower()
        if not low.strip():
            continue
        for keys, patch in _EMOTION_PRESETS:
            if any(k in low for k in keys):
                out.update(patch)
                break
    return out


async def tts(text: str, voice_id: str, upload_dir: str,
              settings: dict | None = None) -> str:
    """Текст → mp3 в хранилище. Возвращает имя файла."""
    if not ELEVEN_KEY:
        raise RuntimeError("озвучка не настроена: нет ключа ElevenLabs")
    text = (text or "").strip()
    if not text:
        raise RuntimeError("пустой текст — озвучивать нечего")
    async with httpx.AsyncClient(timeout=httpx.Timeout(180.0, connect=15.0)) as client:
        r = await client.post(
            f"{ELEVEN_API}/v1/text-to-speech/{voice_id}",
            headers={"xi-api-key": ELEVEN_KEY, "Content-Type": "application/json"},
            json={"text": text[:4000], "model_id": ELEVEN_MODEL,
                  "voice_settings": settings or settings_for()})
    if r.status_code != 200:
        raise RuntimeError(f"elevenlabs {r.status_code}: {r.text[:200]}")
    if len(r.content) < 1000:
        raise RuntimeError("elevenlabs вернул пустой звук")
    fname = f"voice_{uuid.uuid4().hex}.mp3"
    with open(os.path.join(upload_dir, fname), "wb") as f:
        f.write(r.content)
    return fname
