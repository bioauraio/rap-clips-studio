"""Аудио-студия: генерация (Suno через kie), анализ, мастеринг, стемы, лейбл.

Каждая функция — «в один клик»: вход это файл трека или промпт, выход —
готовый файл или числа. Ничего не требует стороннего софта, кроме ffmpeg
(мастеринг) и numpy (тональность); Suno и стемы идут через kie.ai Suno API.
"""
from __future__ import annotations

import json
import logging
import os
import subprocess
import uuid

import httpx

import audio_analysis

log = logging.getLogger("rapclips")

KIE_API = os.environ.get("KIE_API", "https://api.kie.ai")
KIE_KEY = os.environ.get("KIE_API_KEY", "")


def _headers() -> dict:
    return {"Authorization": f"Bearer {KIE_KEY}", "Content-Type": "application/json"}


# ─────────────────────────── генерация (Suno) ───────────────────────────

async def suno_generate(prompt: str, style: str = "", title: str = "",
                        instrumental: bool = False, model: str = "V4_5") -> str:
    """Запустить генерацию трека. Возвращает taskId — результат забирается
    отдельным опросом: Suno считает минуты, держать запрос столько нельзя."""
    if not KIE_KEY:
        raise RuntimeError("генерация музыки не настроена: нет ключа kie")
    body = {"prompt": prompt[:2500], "customMode": bool(style or title),
            "instrumental": instrumental, "model": model}
    if style:
        body["style"] = style[:200]
    if title:
        body["title"] = title[:80]
    async with httpx.AsyncClient(timeout=httpx.Timeout(60.0, connect=15.0)) as client:
        r = await client.post(f"{KIE_API}/api/v1/generate", json=body, headers=_headers())
    data = r.json() if r.status_code == 200 else {}
    if int(data.get("code") or r.status_code) not in (200, 0):
        raise RuntimeError(f"suno отказал: {str(data.get('msg') or r.text)[:200]}")
    tid = (data.get("data") or {}).get("taskId") or ""
    if not tid:
        raise RuntimeError(f"suno не вернул taskId: {str(data)[:150]}")
    return tid


async def suno_status(task_id: str) -> dict:
    """Состояние генерации: {status, tracks:[{title,audio_url,duration}]}."""
    async with httpx.AsyncClient(timeout=httpx.Timeout(45.0, connect=15.0)) as client:
        r = await client.get(f"{KIE_API}/api/v1/generate/record-info",
                             params={"taskId": task_id}, headers=_headers())
    d = (r.json() or {}).get("data") or {}
    status = str(d.get("status") or "").upper()
    tracks = []
    for t in ((d.get("response") or {}).get("sunoData") or []):
        tracks.append({"title": t.get("title") or "", "audio_url": t.get("audioUrl") or "",
                       "image_url": t.get("imageUrl") or "",
                       "duration": t.get("duration") or 0})
    return {"status": status or "PENDING", "tracks": tracks,
            "error": str(d.get("errorMessage") or "")[:200]}


# ─────────────────────────── стемы (вокал/минус) ───────────────────────────

async def vocal_split_start(audio_url: str) -> str:
    """Разложение на вокал и инструментал через kie Suno API."""
    if not KIE_KEY:
        raise RuntimeError("стемы не настроены: нет ключа kie")
    async with httpx.AsyncClient(timeout=httpx.Timeout(60.0, connect=15.0)) as client:
        r = await client.post(f"{KIE_API}/api/v1/vocal-removal/generate",
                              json={"taskId": "", "audioId": "", "audioUrl": audio_url},
                              headers=_headers())
    data = r.json() if r.status_code == 200 else {}
    if int(data.get("code") or r.status_code) not in (200, 0):
        raise RuntimeError(f"стемы: kie отказал — {str(data.get('msg') or r.text)[:200]}")
    tid = (data.get("data") or {}).get("taskId") or ""
    if not tid:
        raise RuntimeError("стемы: kie не вернул taskId")
    return tid


async def vocal_split_status(task_id: str) -> dict:
    async with httpx.AsyncClient(timeout=httpx.Timeout(45.0, connect=15.0)) as client:
        r = await client.get(f"{KIE_API}/api/v1/vocal-removal/record-info",
                             params={"taskId": task_id}, headers=_headers())
    d = (r.json() or {}).get("data") or {}
    resp = d.get("response") or {}
    return {"status": str(d.get("status") or "PENDING").upper(),
            "vocal_url": resp.get("vocal_removal_info", {}).get("vocal_url", "")
                         or resp.get("vocalUrl", ""),
            "instrumental_url": resp.get("vocal_removal_info", {}).get("instrumental_url", "")
                                or resp.get("instrumentalUrl", "")}


# ─────────────────────────── мастеринг ───────────────────────────

def master(src_path: str, out_dir: str, lufs: float = -14.0) -> dict:
    """Мастеринг в один клик: двухпроходный loudnorm до стриминговой
    громкости (-14 LUFS, потолок -1 dBTP) плюс лёгкий стерео-контроль.

    Это не замена живому инженеру — это честный «сделай громко и ровно как
    на площадках», который закрывает 90% нужд до релиза."""
    if not os.path.exists(src_path):
        raise RuntimeError("файл трека не найден")
    probe = subprocess.run(
        ["ffmpeg", "-i", src_path, "-af",
         f"loudnorm=I={lufs}:TP=-1.0:LRA=11:print_format=json", "-f", "null", "-"],
        capture_output=True, timeout=600)
    tail = probe.stderr.decode()[-1200:]
    try:
        stats = json.loads(tail[tail.rindex("{"):])
    except ValueError:
        raise RuntimeError("не смог измерить громкость трека")
    fname = f"master_{uuid.uuid4().hex}.wav"
    out = os.path.join(out_dir, fname)
    flt = (f"loudnorm=I={lufs}:TP=-1.0:LRA=11:"
           f"measured_I={stats['input_i']}:measured_TP={stats['input_tp']}:"
           f"measured_LRA={stats['input_lra']}:measured_thresh={stats['input_thresh']}:"
           f"offset={stats['target_offset']}:linear=true,"
           "alimiter=limit=0.891")
    r = subprocess.run(["ffmpeg", "-y", "-i", src_path, "-af", flt,
                        "-ar", "44100", "-c:a", "pcm_s16le", out],
                       capture_output=True, timeout=900)
    if r.returncode != 0 or not os.path.exists(out):
        raise RuntimeError(f"мастеринг не собрался: {r.stderr.decode()[-160:]}")
    return {"filename": fname,
            "before_lufs": round(float(stats["input_i"]), 1),
            "after_lufs": lufs}


# ─────────────────────────── тональность ───────────────────────────

_KEYS = ["C", "C#", "D", "D#", "E", "F", "F#", "G", "G#", "A", "A#", "B"]
# Профили Крумхансл—Шмуклер: как часто каждая ступень звучит в тональности.
_MAJ = [6.35, 2.23, 3.48, 2.33, 4.38, 4.09, 2.52, 5.19, 2.39, 3.66, 2.29, 2.88]
_MIN = [6.33, 2.68, 3.52, 5.38, 2.60, 3.53, 2.54, 4.75, 3.98, 2.69, 3.34, 3.17]


def detect_key(path: str) -> dict:
    """Тональность по хроме: FFT-бины сворачиваются в 12 классов высоты и
    сравниваются с профилями мажора/минора во всех транспозициях."""
    import numpy as np
    y = audio_analysis._decode(path)
    spec = audio_analysis._spectrogram(y)
    freqs = np.fft.rfftfreq(audio_analysis.N_FFT, 1.0 / audio_analysis.SR)
    chroma = np.zeros(12)
    mask = (freqs > 55) & (freqs < 2000)
    notes = (12 * np.log2(freqs[mask] / 440.0) + 69).round().astype(int) % 12
    mag = np.asarray(spec)[:, mask].mean(axis=0)
    for n in range(12):
        chroma[n] = mag[notes == n].sum()
    if chroma.max() <= 0:
        raise RuntimeError("в треке не нашлось тонального материала")
    chroma = chroma / chroma.max()
    best = ("C", "major", -2.0)
    for shift in range(12):
        rolled = np.roll(chroma, -shift)
        for name, prof in (("major", _MAJ), ("minor", _MIN)):
            c = float(np.corrcoef(rolled, prof)[0, 1])
            if c > best[2]:
                best = (_KEYS[shift], name, c)
    return {"key": best[0], "scale": best[1], "confidence": round(best[2], 2)}
