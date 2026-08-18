"""rap-clips-studio — личный инструмент: раскадровка клипа на 8 рэп-треков.

Не часть организма BIOAURA: свой пароль вместо SSO, своя SQLite, свой домен.
"""
from __future__ import annotations

import logging
import os
import re
import shutil
import subprocess
import time
import uuid

from fastapi import Depends, FastAPI, Form, HTTPException, Request, Response, UploadFile
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from itsdangerous import BadSignature, URLSafeTimedSerializer
from sqlalchemy.orm import Session

import claude
import mediagen
from db import (
    AttributePhoto, Character, CharacterAttribute, CharacterPhoto, Project,
    Scene, SessionLocal, Track, init_db, now,
)

log = logging.getLogger("rapclips")
logging.basicConfig(level=logging.INFO, format="%(asctime)s [rapclips] %(message)s")

APP_PASSWORD = os.environ.get("APP_PASSWORD", "")
SECRET_KEY = os.environ.get("SECRET_KEY", "")
UPLOAD_DIR = os.environ.get("UPLOAD_DIR", "/data/uploads")
COOKIE_NAME = "rc_session"
COOKIE_MAX_AGE = 60 * 60 * 24 * 30  # 30 дней — личный инструмент, не банк

if not APP_PASSWORD or not SECRET_KEY:
    raise RuntimeError("заданы не все переменные окружения: APP_PASSWORD, SECRET_KEY")

os.makedirs(UPLOAD_DIR, exist_ok=True)
signer = URLSafeTimedSerializer(SECRET_KEY, salt="rapclips-session")

app = FastAPI(title="rap-clips-studio")
init_db()


def db_session():
    session = SessionLocal()
    try:
        yield session
    finally:
        session.close()


def require_auth(request: Request) -> None:
    token = request.cookies.get(COOKIE_NAME)
    if not token:
        raise HTTPException(401, "не авторизован")
    try:
        signer.loads(token, max_age=COOKIE_MAX_AGE)
    except BadSignature:
        raise HTTPException(401, "сессия истекла")


def get_or_create_project(db: Session, project_id: int | None = None) -> Project:
    if project_id:
        project = db.get(Project, project_id)
        if not project:
            raise HTTPException(404, "проект не найден")
        return project
    project = db.query(Project).order_by(Project.id).first()
    if not project:
        project = Project(name="Клип", kind="album")
        db.add(project)
        db.commit()
        db.refresh(project)
    return project


# ─────────────────────────── авторизация ───────────────────────────

@app.post("/api/login")
async def login(request: Request, response: Response):
    body = await request.json()
    if str(body.get("password") or "") != APP_PASSWORD:
        raise HTTPException(401, "неверный пароль")
    token = signer.dumps({"ok": True})
    response = JSONResponse({"ok": True})
    response.set_cookie(
        COOKIE_NAME, token, max_age=COOKIE_MAX_AGE, httponly=True,
        samesite="lax", secure=True,
    )
    return response


@app.post("/api/logout")
async def logout():
    response = JSONResponse({"ok": True})
    response.delete_cookie(COOKIE_NAME)
    return response


@app.get("/api/me")
async def me(request: Request):
    token = request.cookies.get(COOKIE_NAME)
    if not token:
        return {"authed": False}
    try:
        signer.loads(token, max_age=COOKIE_MAX_AGE)
        return {"authed": True}
    except BadSignature:
        return {"authed": False}


# ──────────────────────────── сериализация ───────────────────────────

def attribute_dict(a: CharacterAttribute) -> dict:
    return {
        "id": a.id, "name": a.name, "description": a.description,
        "photos": [
            {"id": ph.id, "url": f"/api/media/{ph.filename}"} for ph in a.photos
        ],
    }


def character_dict(c: Character) -> dict:
    return {
        "id": c.id, "position": c.position, "name": c.name,
        "description": c.description, "is_main": c.is_main,
        "photos": [
            {"id": ph.id, "url": f"/api/media/{ph.filename}"} for ph in c.photos
        ],
        "attributes": [attribute_dict(a) for a in c.attributes],
    }


def characters_payload(project: Project) -> list[dict]:
    """Роспись персонажей для промптов Claude."""
    return [
        {"name": c.name, "description": c.description,
         "is_main": c.is_main, "photos": len(c.photos),
         # Фирменные вещи персонажа — чтобы Claude знал их по именам
         # и вписывал в кадры (см. _characters_block в claude.py).
         "attributes": [
             {"name": a.name, "description": a.description}
             for a in c.attributes if a.name.strip()
         ]}
        for c in sorted(project.characters, key=lambda x: x.position)
        if c.name.strip()
    ]


def scene_dict(s: Scene) -> dict:
    return {
        "id": s.id, "position": s.position, "start_sec": s.start_sec,
        "duration_sec": s.duration_sec, "lyric_line": s.lyric_line,
        "characters": s.characters,
        "shot_size": s.shot_size, "camera_move": s.camera_move,
        "image_prompt": s.image_prompt, "motion_prompt": s.motion_prompt,
        "shot_note": s.shot_note,
        "image_prompt_last": s.image_prompt_last,
        "image_url": f"/api/media/{s.image_filename}" if s.image_filename else "",
        "image_last_url": f"/api/media/{s.image_last_filename}" if s.image_last_filename else "",
        # Превью: 4К-кадры по 15МБ браузер в сетке не тянет — в карточках миниатюры.
        "image_thumb_url": f"/api/thumb/{s.image_filename}" if s.image_filename else "",
        "image_last_thumb_url": f"/api/thumb/{s.image_last_filename}" if s.image_last_filename else "",
        "image_status": s.image_status, "image_error": s.image_error,
        "audio_url": f"/api/media/{s.audio_filename}" if s.audio_filename else "",
        "approved": s.approved,
        "video_url": f"/api/media/{s.video_filename}" if s.video_filename else "",
        "video_status": s.video_status, "video_error": s.video_error,
        "video_provider": s.video_provider,
    }


def track_dict(t: Track, with_scenes: bool = False) -> dict:
    d = {
        "id": t.id, "position": t.position, "title": t.title, "lyrics": t.lyrics,
        "comment": t.comment, "style": t.style, "audio_filename": t.audio_filename,
        "director_note": t.director_note, "audio_profile": t.audio_profile,
        "audio_duration_sec": t.audio_duration_sec,
        "scenes_status": t.scenes_status, "scenes_error": t.scenes_error,
        "scenes_count": len(t.scenes),
        "approved_count": sum(1 for s in t.scenes if s.approved),
        "storyboard_url": f"/api/media/{t.storyboard_filename}" if t.storyboard_filename else "",
        "storyboard_status": t.storyboard_status, "storyboard_error": t.storyboard_error,
        "clip_url": f"/api/media/{t.clip_filename}" if t.clip_filename else "",
        "clip_status": t.clip_status, "clip_error": t.clip_error,
        "cover_url": f"/api/media/{t.cover_filename}" if t.cover_filename else "",
        "supergen_status": t.supergen_status, "supergen_note": t.supergen_note,
    }
    if with_scenes:
        d["scenes"] = [scene_dict(s) for s in t.scenes]
    return d


def project_dict(p: Project, with_scenes: bool = False) -> dict:
    return {
        "id": p.id, "name": p.name, "kind": p.kind, "character_bible": p.character_bible,
        "characters": [character_dict(c) for c in sorted(p.characters, key=lambda x: x.position)],
        "story": p.story, "story_status": p.story_status, "story_error": p.story_error,
        "cover_url": f"/api/media/{p.cover_filename}" if p.cover_filename else "",
        "tracks": [track_dict(t, with_scenes) for t in p.tracks],
    }


# ─────────────────────────────── проект ───────────────────────────────

@app.get("/api/projects")
def list_projects(_=Depends(require_auth), db: Session = Depends(db_session)):
    return [
        {"id": p.id, "name": p.name, "kind": p.kind, "tracks": len(p.tracks)}
        for p in db.query(Project).order_by(Project.id).all()
    ]


@app.post("/api/projects")
async def create_project(request: Request, _=Depends(require_auth), db: Session = Depends(db_session)):
    body = await request.json()
    kind = str(body.get("kind") or "album")
    if kind not in ("album", "single"):
        kind = "album"
    project = Project(name=str(body.get("name") or "Новый проект"), kind=kind)
    db.add(project)
    db.commit()
    db.refresh(project)
    return {"id": project.id, "name": project.name, "kind": project.kind}


@app.delete("/api/projects/{project_id}")
def delete_project(project_id: int, _=Depends(require_auth), db: Session = Depends(db_session)):
    project = db.get(Project, project_id)
    if not project:
        raise HTTPException(404, "проект не найден")
    if db.query(Project).count() <= 1:
        raise HTTPException(400, "нельзя удалить последний проект")
    for t in project.tracks:
        if t.audio_filename:
            _remove_media(t.audio_filename)
        for sc in t.scenes:
            for f in (sc.image_filename, sc.image_last_filename, sc.video_filename, sc.audio_filename):
                _remove_media(f)
        _remove_media(t.storyboard_filename)
        _remove_media(t.clip_filename)
        _remove_media(t.cover_filename)
    for c in project.characters:
        for ph in c.photos:
            _remove_media(ph.filename)
        for attr in c.attributes:
            for ph in attr.photos:
                _remove_media(ph.filename)
    _remove_media(project.cover_filename)
    db.delete(project)
    db.commit()
    return {"ok": True}


@app.get("/api/project")
def get_project(project_id: int | None = None, _=Depends(require_auth), db: Session = Depends(db_session)):
    return project_dict(get_or_create_project(db, project_id), with_scenes=True)


@app.patch("/api/project")
async def update_project(request: Request, project_id: int | None = None, _=Depends(require_auth), db: Session = Depends(db_session)):
    body = await request.json()
    project = get_or_create_project(db, project_id)
    if "name" in body:
        project.name = str(body["name"])
    if "character_bible" in body:
        project.character_bible = str(body["character_bible"])
    db.commit()
    return project_dict(project)


def _run_story_generation(project_id: int) -> None:
    db = SessionLocal()
    try:
        project = db.get(Project, project_id)
        if not project:
            return
        project.story_status = "running"
        project.story_error = ""
        db.commit()
        tracks = [
            {"position": t.position, "title": t.title, "lyrics": t.lyrics,
             "comment": t.comment, "style": t.style,
             "audio_profile": t.audio_profile}
            for t in project.tracks
        ]
        import asyncio
        result = asyncio.run(claude.generate_story(project.character_bible, tracks, characters_payload(project)))
        project.character_bible = result.get("character_bible", project.character_bible)
        project.story = result.get("story", "")
        notes = {n.get("position"): n.get("note", "") for n in result.get("track_notes", [])}
        for t in project.tracks:
            note = notes.get(t.position)
            if note:
                # Заметка живёт отдельным полем — комментарий владельца не трогаем.
                t.director_note = note
        project.story_status = "done"
        db.commit()
        log.info("сюжет сгенерирован для проекта %s", project_id)
    except Exception as e:  # noqa: BLE001
        db.rollback()
        project = db.get(Project, project_id)
        if project:
            project.story_status = "error"
            project.story_error = str(e)[:500]
            db.commit()
        log.warning("генерация сюжета упала: %s", e)
    finally:
        db.close()


@app.post("/api/project/generate-story")
def generate_story(project_id: int | None = None, _=Depends(require_auth), db: Session = Depends(db_session)):
    from threading import Thread
    project = get_or_create_project(db, project_id)
    if not project.tracks:
        raise HTTPException(400, "сначала загрузи хотя бы один трек")
    project.story_status = "queued"
    db.commit()
    Thread(target=_run_story_generation, args=(project.id,), daemon=True).start()
    return {"ok": True}


# ─────────────────────────────── треки ───────────────────────────────

def _audio_profile(path: str, duration_sec: int) -> str:
    """«Прослушивание» трека: ffmpeg меряет громкость по сегментам — сюжет и
    раскадровка получают реальную динамику дорожки (тихо/врыв/спад), а не
    выдумывают её. Без нейросетей: RMS-профиль честнее галлюцинаций."""
    if not duration_sec or duration_sec < 4:
        return ""
    n = min(10, max(4, duration_sec // 15))
    seg = duration_sec / n
    levels: list[float] = []
    for i in range(n):
        r = subprocess.run(
            ["ffmpeg", "-ss", str(round(i * seg, 1)), "-t", str(round(seg, 1)),
             "-i", path, "-af", "volumedetect", "-f", "null", "-"],
            capture_output=True, text=True, timeout=60,
        )
        m = re.search(r"mean_volume:\s*(-?[\d.]+) dB", r.stderr)
        levels.append(float(m.group(1)) if m else -91.0)
    if not any(v > -90 for v in levels):
        return ""
    lo, hi = min(levels), max(levels)
    span = max(hi - lo, 1.0)
    words = []
    for i, v in enumerate(levels):
        t0 = int(i * seg)
        rel = (v - lo) / span
        label = "тихо" if rel < 0.25 else "спокойно" if rel < 0.5 else "плотно" if rel < 0.75 else "врыв"
        if i and abs(levels[i] - levels[i - 1]) >= 4:
            label += " (подъём)" if levels[i] > levels[i - 1] else " (спад)"
        words.append(f"{t0 // 60}:{t0 % 60:02d} {label}")
    return (f"длительность {duration_sec // 60}:{duration_sec % 60:02d}; "
            f"динамика громкости по сегментам: " + ", ".join(words))


def _ffprobe_duration(path: str) -> int:
    try:
        out = subprocess.run(
            ["ffprobe", "-v", "error", "-show_entries", "format=duration",
             "-of", "default=noprint_wrappers=1:nokey=1", path],
            capture_output=True, text=True, timeout=20, check=True,
        )
        return max(1, round(float(out.stdout.strip())))
    except Exception as e:  # noqa: BLE001
        log.warning("ffprobe не смог определить длительность %s: %s", path, e)
        return 180  # разумный дефолт для рэп-трека, если ffprobe не смог


@app.post("/api/tracks")
async def create_track(
    title: str = Form(""), lyrics: str = Form(""), comment: str = Form(""), style: str = Form(""),
    audio: UploadFile | None = None,
    project_id: int | None = None,
    _=Depends(require_auth), db: Session = Depends(db_session),
):
    project = get_or_create_project(db, project_id)
    if project.kind == "single" and project.tracks:
        raise HTTPException(400, "это сингл — трек может быть только один")
    max_pos = max((t.position for t in project.tracks), default=0)
    track = Track(
        project_id=project.id, position=max_pos + 1,
        title=title, lyrics=lyrics, comment=comment, style=style,
    )
    if audio is not None:
        ext = os.path.splitext(audio.filename or "")[1] or ".mp3"
        fname = f"{uuid.uuid4().hex}{ext}"
        path = os.path.join(UPLOAD_DIR, fname)
        data = await audio.read()
        with open(path, "wb") as f:
            f.write(data)
        track.audio_filename = fname
        track.audio_duration_sec = _ffprobe_duration(path)
        try:
            track.audio_profile = _audio_profile(path, track.audio_duration_sec)
        except Exception as e:  # noqa: BLE001
            log.warning("профиль звука не посчитался: %s", e)
    db.add(track)
    db.commit()
    db.refresh(track)
    return track_dict(track)


@app.patch("/api/tracks/{track_id}")
async def update_track(track_id: int, request: Request, _=Depends(require_auth), db: Session = Depends(db_session)):
    track = db.get(Track, track_id)
    if not track:
        raise HTTPException(404, "трек не найден")
    body = await request.json()
    for field in ("title", "lyrics", "comment", "style"):
        if field in body:
            setattr(track, field, str(body[field]))
    db.commit()
    return track_dict(track)


@app.delete("/api/tracks/{track_id}")
def delete_track(track_id: int, _=Depends(require_auth), db: Session = Depends(db_session)):
    track = db.get(Track, track_id)
    if not track:
        raise HTTPException(404, "трек не найден")
    if track.audio_filename:
        path = os.path.join(UPLOAD_DIR, track.audio_filename)
        if os.path.exists(path):
            os.remove(path)
    for s in track.scenes:
        _remove_media(s.image_filename)
        _remove_media(s.video_filename)
    _remove_media(track.cover_filename)
    db.delete(track)
    db.commit()
    return {"ok": True}


@app.post("/api/tracks/reorder")
async def reorder_tracks(request: Request, _=Depends(require_auth), db: Session = Depends(db_session)):
    body = await request.json()
    order = body.get("order") or []  # список id в новом порядке
    for i, track_id in enumerate(order, start=1):
        track = db.get(Track, int(track_id))
        if track:
            track.position = i
    db.commit()
    return {"ok": True}


@app.get("/api/tracks/{track_id}/audio")
def get_audio(track_id: int, _=Depends(require_auth), db: Session = Depends(db_session)):
    track = db.get(Track, track_id)
    if not track or not track.audio_filename:
        raise HTTPException(404, "аудио не найдено")
    path = os.path.join(UPLOAD_DIR, track.audio_filename)
    if not os.path.exists(path):
        raise HTTPException(404, "файл отсутствует на диске")
    return FileResponse(path)


# ─────────────────────────── обложки проекта и трека ───────────────────────────

async def _save_cover_file(cover: UploadFile) -> str:
    """Обложка хранится как обычный медиа-файл под uuid-именем: при замене
    URL меняется вместе с файлом, поэтому браузерный кэш не показывает старую."""
    ext = os.path.splitext(cover.filename or "")[1].lower() or ".jpg"
    if ext not in (".jpg", ".jpeg", ".png", ".webp"):
        raise HTTPException(400, "поддерживаются jpg/png/webp")
    fname = f"cover_{uuid.uuid4().hex}{ext}"
    with open(os.path.join(UPLOAD_DIR, fname), "wb") as f:
        f.write(await cover.read())
    return fname


@app.post("/api/projects/{project_id}/cover")
async def upload_project_cover(project_id: int, cover: UploadFile, _=Depends(require_auth), db: Session = Depends(db_session)):
    project = db.get(Project, project_id)
    if not project:
        raise HTTPException(404, "проект не найден")
    old = project.cover_filename
    project.cover_filename = await _save_cover_file(cover)
    db.commit()
    db.refresh(project)
    # Старый файл убираем только ПОСЛЕ commit: если запись не прошла,
    # прежняя обложка остаётся живой.
    _remove_media(old)
    return {"ok": True, "cover_url": f"/api/media/{project.cover_filename}"}


@app.post("/api/tracks/{track_id}/cover")
async def upload_track_cover(track_id: int, cover: UploadFile, _=Depends(require_auth), db: Session = Depends(db_session)):
    track = db.get(Track, track_id)
    if not track:
        raise HTTPException(404, "трек не найден")
    old = track.cover_filename
    track.cover_filename = await _save_cover_file(cover)
    db.commit()
    db.refresh(track)
    _remove_media(old)
    return {"ok": True, "cover_url": f"/api/media/{track.cover_filename}"}


# ─────────────────────────────── сцены ───────────────────────────────

def _run_scene_generation(track_id: int) -> None:
    db = SessionLocal()
    try:
        track = db.get(Track, track_id)
        if not track:
            return
        track.scenes_status = "running"
        track.scenes_error = ""
        db.commit()
        project = track.project
        # Заметка из отдельного поля; старые треки могли хранить её в комментарии.
        track_note = track.director_note
        if not track_note:
            note_match = re.search(r"\[режиссёрская заметка\]\s*(.+)$", track.comment, re.DOTALL)
            track_note = note_match.group(1).strip() if note_match else ""
        clean_comment = re.sub(r"\n*\[режиссёрская заметка\].*$", "", track.comment, flags=re.DOTALL).strip()
        import asyncio
        result = asyncio.run(claude.generate_scenes(
            story=project.story, character_bible=project.character_bible,
            track_note=track_note, title=track.title, lyrics=track.lyrics,
            comment=clean_comment, style=track.style,
            duration_sec=track.audio_duration_sec or 180,
            characters=characters_payload(project),
            audio_profile=track.audio_profile,
        ))
        for s in list(track.scenes):
            _remove_media(s.image_filename)
            _remove_media(s.video_filename)
            db.delete(s)
        db.flush()
        cursor = 0
        for i, sc in enumerate(result.get("scenes", []), start=1):
            dur = max(2, min(12, int(sc.get("duration_sec") or 6)))
            db.add(Scene(
                track_id=track.id, position=i, start_sec=cursor, duration_sec=dur,
                lyric_line=str(sc.get("lyric_line") or ""),
                characters=", ".join(str(n) for n in (sc.get("characters") or []) if str(n).strip()),
                shot_size=str(sc.get("shot_size") or ""),
                camera_move=str(sc.get("camera_move") or ""),
                image_prompt=str(sc.get("image_prompt") or ""),
                image_prompt_last=str(sc.get("image_prompt_last") or ""),
                motion_prompt=str(sc.get("motion_prompt") or ""),
                shot_note=str(sc.get("shot_note") or ""),
                video_provider="seedance" if mediagen.seedance_available() else "grok",
            ))
            cursor += dur
        track.scenes_status = "done"
        db.commit()
        log.info("раскадровка готова для трека %s (%s кадров)", track_id, i if result.get("scenes") else 0)
    except Exception as e:  # noqa: BLE001
        db.rollback()
        track = db.get(Track, track_id)
        if track:
            track.scenes_status = "error"
            track.scenes_error = str(e)[:500]
            db.commit()
        log.warning("генерация раскадровки трека %s упала: %s", track_id, e)
    finally:
        db.close()


@app.post("/api/tracks/{track_id}/generate-scenes")
def generate_scenes(track_id: int, _=Depends(require_auth), db: Session = Depends(db_session)):
    from threading import Thread
    track = db.get(Track, track_id)
    if not track:
        raise HTTPException(404, "трек не найден")
    if not track.project.story:
        raise HTTPException(400, "сначала сгенерируй общий сюжет проекта")
    track.scenes_status = "queued"
    db.commit()
    Thread(target=_run_scene_generation, args=(track_id,), daemon=True).start()
    return {"ok": True}


@app.patch("/api/scenes/{scene_id}")
async def update_scene(scene_id: int, request: Request, _=Depends(require_auth), db: Session = Depends(db_session)):
    scene = db.get(Scene, scene_id)
    if not scene:
        raise HTTPException(404, "кадр не найден")
    body = await request.json()
    for field in ("duration_sec", "lyric_line", "shot_size", "camera_move", "image_prompt", "motion_prompt", "shot_note"):
        if field in body:
            setattr(scene, field, body[field])
    db.commit()
    return scene_dict(scene)


@app.delete("/api/scenes/{scene_id}")
def delete_scene(scene_id: int, _=Depends(require_auth), db: Session = Depends(db_session)):
    scene = db.get(Scene, scene_id)
    if not scene:
        raise HTTPException(404, "кадр не найден")
    for f in (scene.image_filename, scene.image_last_filename,
              scene.video_filename, scene.audio_filename):
        _remove_media(f)
    track = scene.track
    db.delete(scene)
    db.flush()
    _renumber_scenes(track)
    db.commit()
    return {"ok": True}

# ───────── лист раскадровки → кадры сцены → видео → сборка клипа ─────────

def _remove_media(filename: str) -> None:
    if not filename:
        return
    path = os.path.join(UPLOAD_DIR, filename)
    if os.path.exists(path):
        try:
            os.remove(path)
        except OSError:
            pass


def _mime_ext(mime: str) -> str:
    return ".jpg" if "jpeg" in mime else ".png"


def _save_image(data: bytes, mime: str, *, upscale: bool = True) -> str:
    fname = f"scene_{uuid.uuid4().hex}{_mime_ext(mime)}"
    path = os.path.join(UPLOAD_DIR, fname)
    with open(path, "wb") as f:
        f.write(data)
    if upscale:
        mediagen.upscale_to_4k(path)
    return fname


def _track_audio_path(track: Track) -> str | None:
    if not track.audio_filename:
        return None
    return os.path.join(UPLOAD_DIR, track.audio_filename)


# ─────────────────────────── лист раскадровки ───────────────────────────

def _run_storyboard(track_id: int) -> None:
    """Весь трек ОДНОЙ картинкой-сеткой: проверка целостности до покадровой
    отрисовки. Лист потом идёт контекстом в промпты отдельных кадров."""
    db = SessionLocal()
    try:
        track = db.get(Track, track_id)
        if not track:
            return
        track.storyboard_status = "running"
        track.storyboard_error = ""
        db.commit()
        scenes = [
            {"position": s.position, "shot_size": s.shot_size, "shot_note": s.shot_note}
            for s in track.scenes
        ]
        import asyncio
        built = asyncio.run(claude.generate_storyboard_sheet_prompt(
            style=track.style, character_bible=track.project.character_bible, scenes=scenes,
        ))
        prompt = built.get("prompt") or ""
        if not prompt:
            raise RuntimeError("Claude не вернул промпт листа раскадровки")
        data, mime = asyncio.run(mediagen.generate_image(prompt))
        old = track.storyboard_filename
        # Лист смотрят целиком, апскейл до 4К ему не нужен.
        track.storyboard_filename = _save_image(data, mime, upscale=False)
        track.storyboard_status = "done"
        db.commit()
        _remove_media(old)
        log.info("лист раскадровки трека %s готов", track_id)
    except Exception as e:  # noqa: BLE001
        db.rollback()
        track = db.get(Track, track_id)
        if track:
            track.storyboard_status = "error"
            track.storyboard_error = str(e)[:500]
            db.commit()
        log.warning("лист раскадровки трека %s упал: %s", track_id, e)
    finally:
        db.close()


@app.post("/api/tracks/{track_id}/generate-storyboard")
def generate_storyboard(track_id: int, _=Depends(require_auth), db: Session = Depends(db_session)):
    from threading import Thread
    track = db.get(Track, track_id)
    if not track:
        raise HTTPException(404, "трек не найден")
    if not track.scenes:
        raise HTTPException(400, "сначала сгенерируй раскадровку трека")
    track.storyboard_status = "queued"
    db.commit()
    Thread(target=_run_storyboard, args=(track_id,), daemon=True).start()
    return {"ok": True}


def _scene_characters(scene: Scene, project: Project) -> list[Character]:
    """Персонажи, указанные у кадра (по именам, без регистра)."""
    names = [n.strip().lower() for n in (scene.characters or "").split(",") if n.strip()]
    if not names:
        return []
    by_name = {c.name.strip().lower(): c for c in project.characters if c.name.strip()}
    return [by_name[n] for n in names if n in by_name]


def _scene_attribute_photo(scene: Scene, chars: list[Character]) -> str | None:
    """Референс-АТРИБУТ: если текст сцены упоминает фирменную вещь персонажа
    (шляпу, квадрик, тачку) — кадр строится вокруг предмета, и референсом
    должно идти фото самой вещи, а не лицо героя. Проверяем только персонажей
    ЭТОЙ сцены; совпадение — регистронезависимое вхождение имени атрибута."""
    haystack = f"{scene.image_prompt or ''}\n{scene.shot_note or ''}".lower()
    if not haystack.strip():
        return None
    for c in chars:
        for a in c.attributes:
            name = a.name.strip().lower()
            if not name or name not in haystack:
                continue
            # Первое фото атрибута — каноническая моделька предмета.
            for ph in a.photos:
                path = os.path.join(UPLOAD_DIR, ph.filename)
                if os.path.exists(path):
                    return path
                break  # контракт: именно ПЕРВОЕ фото; пропал файл — атрибут без референса
    return None


def _scene_reference_photo(scene: Scene, project: Project) -> str | None:
    """Фото-моделька для генерации кадра: первый персонаж кадра, у которого
    загружены фото (или главный герой, если персонажи кадра не указаны).
    Если в тексте сцены упомянут атрибут персонажа сцены — референсом идёт
    первое фото атрибута (кадр про вещь, а не про лицо)."""
    chars = _scene_characters(scene, project)
    attr_path = _scene_attribute_photo(scene, chars)
    if attr_path:
        return attr_path
    if not chars:
        chars = [c for c in project.characters if c.is_main]
    for c in chars:
        if c.photos:
            path = os.path.join(UPLOAD_DIR, c.photos[0].filename)
            if os.path.exists(path):
                return path
    return None


# ───────────────────── первый и последний кадр сцены ─────────────────────

# Общий хвост промпта: и первый, и последний кадр рисуются с оглядкой на
# соседей и на весь клип — иначе сцены выглядят как набор открыток.
def _frame_prompt(scene: Scene, track: Track, which: str) -> str:
    project = track.project
    neighbours = []
    for s in track.scenes:
        if abs(s.position - scene.position) <= 1 and s.id != scene.id:
            neighbours.append(f"{s.position}. {s.shot_note}")
    base = scene.image_prompt if which == "first" else (scene.image_prompt_last or scene.image_prompt)
    parts = [
        base,
        f"Consistent single continuous music video, unified visual style: {track.style}.",
    ]
    # Персонажи кадра: их канонические описания обязаны попасть в промпт
    # (внешность НЕ переизобретается, меняется только стилистика подачи).
    scene_chars = _scene_characters(scene, project)
    if scene_chars:
        for c in scene_chars:
            parts.append(
                f"Character '{c.name}' (must stay identical across every shot"
                f" of the whole album): {c.description}"
            )
    else:
        parts.append(
            f"Main character reference (must stay identical across every shot): {project.character_bible}"
        )
    if neighbours:
        parts.append("Adjacent shots for continuity: " + " | ".join(neighbours))
    parts.append("Vertical 9:16 composition, no text, no captions, no watermarks, no logos.")
    return "\n".join(p for p in parts if p.strip())


def _run_scene_frames(scene_id: int) -> None:
    db = SessionLocal()
    try:
        scene = db.get(Scene, scene_id)
        if not scene:
            return
        scene.image_status = "running"
        scene.image_error = ""
        db.commit()
        track = scene.track
        import asyncio
        reference = _scene_reference_photo(scene, track.project)
        first_data, first_mime = asyncio.run(
            mediagen.generate_image(_frame_prompt(scene, track, "first"), reference_path=reference))
        last_data, last_mime = asyncio.run(
            mediagen.generate_image(_frame_prompt(scene, track, "last"), reference_path=reference))

        old_first, old_last = scene.image_filename, scene.image_last_filename
        old_video, old_audio = scene.video_filename, scene.audio_filename
        scene.image_filename = _save_image(first_data, first_mime)
        scene.image_last_filename = _save_image(last_data, last_mime)
        scene.image_status = "done"
        # Кадры переснялись — старое видео и утверждение к ним не относятся.
        scene.approved = False
        scene.video_filename = ""
        scene.video_status = ""
        scene.video_error = ""
        scene.audio_filename = ""
        db.commit()
        for f in (old_first, old_last, old_video, old_audio):
            _remove_media(f)
        log.info("кадры сцены %s готовы", scene_id)
    except Exception as e:  # noqa: BLE001
        db.rollback()
        scene = db.get(Scene, scene_id)
        if scene:
            scene.image_status = "error"
            scene.image_error = str(e)[:500]
            db.commit()
        log.warning("генерация кадров сцены %s упала: %s", scene_id, e)
    finally:
        db.close()


@app.post("/api/scenes/{scene_id}/generate-frames")
def generate_scene_frames(scene_id: int, _=Depends(require_auth), db: Session = Depends(db_session)):
    from threading import Thread
    scene = db.get(Scene, scene_id)
    if not scene:
        raise HTTPException(404, "кадр не найден")
    if not scene.image_prompt.strip():
        raise HTTPException(400, "у сцены пуст промпт первого кадра")
    scene.image_status = "queued"
    db.commit()
    Thread(target=_run_scene_frames, args=(scene_id,), daemon=True).start()
    return {"ok": True}


# ───────────────── видео сцены + отрезок трека под неё ─────────────────

def _run_scene_video(scene_id: int) -> None:
    db = SessionLocal()
    try:
        scene = db.get(Scene, scene_id)
        if not scene:
            return
        scene.video_status = "running"
        scene.video_error = ""
        db.commit()
        track = scene.track
        first_path = os.path.join(UPLOAD_DIR, scene.image_filename)
        last_path = (
            os.path.join(UPLOAD_DIR, scene.image_last_filename)
            if scene.image_last_filename else None
        )
        import asyncio
        fname = asyncio.run(mediagen.animate_scene(
            prompt=scene.motion_prompt, first_path=first_path, last_path=last_path,
            duration_sec=scene.duration_sec, provider=scene.video_provider,
        ))
        old_video = scene.video_filename
        scene.video_filename = fname
        scene.video_status = "done"

        # Отрезок трека ровно под эту сцену — слушаем видео с его музыкой.
        old_audio = scene.audio_filename
        audio_src = _track_audio_path(track)
        if audio_src:
            try:
                scene.audio_filename = mediagen.slice_audio(
                    audio_src, scene.start_sec, scene.duration_sec)
            except Exception as e:  # noqa: BLE001
                log.warning("нарезка аудио сцены %s не удалась: %s", scene_id, e)
                old_audio = ""  # старый отрезок не трогаем, если новый не вышел
        db.commit()
        _remove_media(old_video)
        _remove_media(old_audio)
        log.info("видео сцены %s готово (%s)", scene_id, scene.video_provider)
    except Exception as e:  # noqa: BLE001
        db.rollback()
        scene = db.get(Scene, scene_id)
        if scene:
            scene.video_status = "error"
            scene.video_error = str(e)[:500]
            db.commit()
        log.warning("видео сцены %s упало: %s", scene_id, e)
    finally:
        db.close()


@app.post("/api/scenes/{scene_id}/generate-video")
async def generate_scene_video(scene_id: int, request: Request, _=Depends(require_auth), db: Session = Depends(db_session)):
    from threading import Thread
    scene = db.get(Scene, scene_id)
    if not scene:
        raise HTTPException(404, "кадр не найден")
    if not scene.image_filename:
        raise HTTPException(400, "сначала сгенерируй кадры сцены")
    body = await request.json() if await request.body() else {}
    provider = str(body.get("provider") or scene.video_provider or "seedance")
    if provider not in mediagen.video_providers():
        raise HTTPException(400, f"провайдер {provider} недоступен: {mediagen.video_providers()}")
    scene.video_provider = provider
    scene.video_status = "queued"
    db.commit()
    Thread(target=_run_scene_video, args=(scene_id,), daemon=True).start()
    return {"ok": True}


@app.post("/api/scenes/{scene_id}/approve")
async def approve_scene(scene_id: int, request: Request, _=Depends(require_auth), db: Session = Depends(db_session)):
    """Утверждение ВИДЕО сцены: утверждённые идут в общий клип трека."""
    scene = db.get(Scene, scene_id)
    if not scene:
        raise HTTPException(404, "кадр не найден")
    body = await request.json()
    approved = bool(body.get("approved", True))
    if approved and not scene.video_filename:
        raise HTTPException(400, "сначала сгенерируй видео сцены")
    scene.approved = approved
    db.commit()
    return scene_dict(scene)


# ───────────────────────── сборка клипа трека ─────────────────────────

def _run_assemble(track_id: int) -> None:
    db = SessionLocal()
    try:
        track = db.get(Track, track_id)
        if not track:
            return
        track.clip_status = "running"
        track.clip_error = ""
        db.commit()
        videos = [s.video_filename for s in track.scenes if s.approved and s.video_filename]
        old = track.clip_filename
        track.clip_filename = mediagen.assemble_clip(videos, _track_audio_path(track))
        track.clip_status = "done"
        db.commit()
        _remove_media(old)
        log.info("клип трека %s собран из %s сцен", track_id, len(videos))
    except Exception as e:  # noqa: BLE001
        db.rollback()
        track = db.get(Track, track_id)
        if track:
            track.clip_status = "error"
            track.clip_error = str(e)[:500]
            db.commit()
        log.warning("сборка клипа трека %s упала: %s", track_id, e)
    finally:
        db.close()


@app.post("/api/tracks/{track_id}/assemble")
def assemble_track_clip(track_id: int, _=Depends(require_auth), db: Session = Depends(db_session)):
    from threading import Thread
    track = db.get(Track, track_id)
    if not track:
        raise HTTPException(404, "трек не найден")
    approved = [s for s in track.scenes if s.approved and s.video_filename]
    if not approved:
        raise HTTPException(400, "нет утверждённых сцен с видео")
    track.clip_status = "queued"
    db.commit()
    Thread(target=_run_assemble, args=(track_id,), daemon=True).start()
    return {"ok": True, "scenes": len(approved)}


# ──────────────────────────── супергенерация ────────────────────────────

def _run_supergen(track_id: int) -> None:
    """Весь конвейер одним нажатием: сюжет (если пуст) → раскадровка (если нет)
    → кадры всех сцен → видео всех сцен → авто-утверждение → сборка клипа.

    Шаги выполняются ПОСЛЕДОВАТЕЛЬНО в одном треде: генераторы за шлюзами всё
    равно однопоточные, а так прогресс честный и падение любого шага видно."""
    db = SessionLocal()

    def note(txt: str, status: str = "running") -> None:
        t = db.get(Track, track_id)
        if t:
            t.supergen_status = status
            t.supergen_note = txt
            db.commit()

    try:
        track = db.get(Track, track_id)
        if not track:
            return
        project = track.project

        if not (project.story or "").strip():
            note("пишу сквозной сюжет…")
            _run_story_generation(project.id)
            db.expire_all()
            if db.get(Project, project.id).story_status == "error":
                note("сюжет не сгенерился — смотри ошибку в блоке сюжета", "error")
                return

        db.expire_all()
        track = db.get(Track, track_id)
        if not track.scenes:
            note("режу раскадровку…")
            _run_scene_generation(track_id)
            db.expire_all()
            track = db.get(Track, track_id)
            if track.scenes_status == "error" or not track.scenes:
                note("раскадровка не сгенерилась — смотри ошибку у трека", "error")
                return

        scene_ids = [s.id for s in sorted(track.scenes, key=lambda x: x.position)]
        total = len(scene_ids)
        for i, sid in enumerate(scene_ids, 1):
            db.expire_all()
            s = db.get(Scene, sid)
            if not (s and s.image_filename and s.image_last_filename):
                note(f"кадры: сцена {i}/{total}…")
                _run_scene_frames(sid)
                db.expire_all()
                s = db.get(Scene, sid)
                if not s or s.image_status == "error":
                    note(f"кадры сцены {i} упали: {(s.image_error if s else '')[:150]}", "error")
                    return

        provider = "seedance" if mediagen.seedance_available() else "grok"
        for i, sid in enumerate(scene_ids, 1):
            db.expire_all()
            s = db.get(Scene, sid)
            if not s:
                continue
            if not s.video_filename:
                note(f"видео: сцена {i}/{total} через {provider}…")
                s.video_provider = provider
                db.commit()
                _run_scene_video(sid)
                db.expire_all()
                s = db.get(Scene, sid)
                if not s or s.video_status == "error":
                    note(f"видео сцены {i} упало: {(s.video_error if s else '')[:150]}", "error")
                    return
            if not s.approved:
                s.approved = True
                db.commit()

        note("собираю клип из всех сцен…")
        _run_assemble(track_id)
        db.expire_all()
        track = db.get(Track, track_id)
        if track.clip_status == "error":
            note(f"сборка упала: {track.clip_error[:150]}", "error")
            return
        note(f"готово: клип собран из {total} сцен", "done")
    except Exception as e:  # noqa: BLE001
        db.rollback()
        note(f"супергенерация упала: {str(e)[:200]}", "error")
        log.warning("супергенерация трека %s упала: %s", track_id, e)
    finally:
        db.close()


@app.post("/api/tracks/{track_id}/supergen")
def supergen(track_id: int, _=Depends(require_auth), db: Session = Depends(db_session)):
    from threading import Thread
    track = db.get(Track, track_id)
    if not track:
        raise HTTPException(404, "трек не найден")
    if not track.audio_filename:
        raise HTTPException(400, "у трека нет аудио — загрузи дорожку")
    # Без стиля и персонажей Claude выдумывает свои: стиль обязателен, герои тоже.
    if not (track.style or "").strip():
        raise HTTPException(400, "не выбран стиль клипа — выбери пресет на карточке трека")
    if not any(c.name.strip() for c in track.project.characters):
        raise HTTPException(400, "в проекте нет персонажей — добавь нового или клонируй из базы")
    if track.supergen_status in ("queued", "running"):
        raise HTTPException(400, "супергенерация уже идёт")
    track.supergen_status = "queued"
    track.supergen_note = "старт…"
    db.commit()
    Thread(target=_run_supergen, args=(track_id,), daemon=True).start()
    return {"ok": True}


@app.get("/api/media/{filename}")
def get_media(filename: str, _=Depends(require_auth)):
    path = os.path.join(UPLOAD_DIR, os.path.basename(filename))
    if not os.path.exists(path):
        raise HTTPException(404, "файл не найден")
    return FileResponse(path)


THUMB_DIR = os.environ.get("THUMB_DIR", "/data/thumbs")
os.makedirs(THUMB_DIR, exist_ok=True)


@app.get("/api/thumb/{filename}")
def get_thumb(filename: str, _=Depends(require_auth)):
    """Миниатюра кадра ~640px: полноразмерные 4К PNG в сетке карточек браузер
    не прогружает. Рендерится лениво, кэшируется рядом с данными."""
    src = os.path.join(UPLOAD_DIR, os.path.basename(filename))
    if not os.path.exists(src):
        raise HTTPException(404, "файл не найден")
    dst = os.path.join(THUMB_DIR, os.path.basename(filename) + ".jpg")
    if not os.path.exists(dst) or os.path.getmtime(dst) < os.path.getmtime(src):
        r = subprocess.run(
            ["ffmpeg", "-y", "-i", src, "-vf", "scale=640:-2", "-q:v", "5", dst],
            capture_output=True, timeout=60,
        )
        if r.returncode != 0 or not os.path.exists(dst):
            return FileResponse(src)  # не вышло — отдаём оригинал, хуже не станет
    return FileResponse(dst)


@app.get("/api/outbox/{filename}")
def get_outbox(filename: str):
    """Отдача кадра внешнему видеогенератору (seevio тянет image_urls сам,
    куки приложения у него нет). В outbox лежат ТОЛЬКО временные копии кадров
    текущих задач под uuid-именами; mediagen кладёт их туда на время генерации
    и удаляет по завершении задачи."""
    path = os.path.join(mediagen.OUTBOX_DIR, os.path.basename(filename))
    if not os.path.exists(path):
        raise HTTPException(404, "файл не найден")
    return FileResponse(path)


@app.get("/api/providers")
def providers(_=Depends(require_auth)):
    return {"video": mediagen.video_providers(), "seedance": mediagen.seedance_available()}



# ─────────────────────────── персонажи альбома ───────────────────────────

@app.get("/api/characters/library")
def characters_library(_=Depends(require_auth), db: Session = Depends(db_session)):
    """Сквозная библиотека: персонажи ВСЕХ проектов разом — чтобы переносить
    героя из альбома в альбом клонированием, а не заводить его заново."""
    out = []
    for p in db.query(Project).order_by(Project.id).all():
        for c in sorted(p.characters, key=lambda x: x.position):
            out.append({
                "id": c.id, "name": c.name, "description": c.description,
                "is_main": c.is_main,
                "project_id": p.id, "project_name": p.name,
                "photos": [
                    {"id": ph.id, "url": f"/api/media/{ph.filename}"} for ph in c.photos
                ],
            })
    return out


@app.post("/api/characters/clone")
async def clone_character(request: Request, project_id: int | None = None, _=Depends(require_auth), db: Session = Depends(db_session)):
    """Клонирование персонажа из библиотеки в проект. Копия полностью
    самостоятельная: удаление оригинала (или его фото) не ломает клона."""
    body = await request.json()
    source = db.get(Character, int(body.get("source_id") or 0))
    if not source:
        raise HTTPException(404, "исходный персонаж не найден")
    project = get_or_create_project(db, project_id)
    max_pos = max((c.position for c in project.characters), default=0)
    # Главный герой в проекте один (см. update_character): статус переносится
    # только если место главного в целевом проекте ещё свободно.
    has_main = any(c.is_main for c in project.characters)
    clone = Character(
        project_id=project.id, position=max_pos + 1,
        name=source.name, description=source.description,
        is_main=bool(source.is_main and not has_main),
    )
    db.add(clone)
    db.flush()
    # Фото копируем БАЙТАМИ под новыми именами, а не ссылкой на тот же файл:
    # иначе удаление фото у оригинала снесло бы файл и у клона.
    for i, ph in enumerate(source.photos, start=1):
        src_path = os.path.join(UPLOAD_DIR, ph.filename)
        if not os.path.exists(src_path):
            continue  # битую ссылку на пропавший файл не тиражируем
        ext = os.path.splitext(ph.filename)[1] or ".jpg"
        fname = f"char_{uuid.uuid4().hex}{ext}"
        shutil.copyfile(src_path, os.path.join(UPLOAD_DIR, fname))
        db.add(CharacterPhoto(character_id=clone.id, position=i, filename=fname))
    # Атрибуты — часть образа персонажа: клон получает их вместе с фото
    # (тоже байтами под новыми именами — по той же причине, что и лица).
    for attr in source.attributes:
        attr_clone = CharacterAttribute(
            character_id=clone.id, position=attr.position,
            name=attr.name, description=attr.description,
        )
        db.add(attr_clone)
        db.flush()
        for i, ph in enumerate(attr.photos, start=1):
            src_path = os.path.join(UPLOAD_DIR, ph.filename)
            if not os.path.exists(src_path):
                continue
            ext = os.path.splitext(ph.filename)[1] or ".jpg"
            fname = f"attr_{uuid.uuid4().hex}{ext}"
            shutil.copyfile(src_path, os.path.join(UPLOAD_DIR, fname))
            db.add(AttributePhoto(attribute_id=attr_clone.id, position=i, filename=fname))
    db.commit()
    # clone.photos закэширован ДО вставки фото — без refresh ответ уйдёт пустым.
    db.refresh(clone)
    return character_dict(clone)


@app.post("/api/characters")
async def create_character(request: Request, project_id: int | None = None, _=Depends(require_auth), db: Session = Depends(db_session)):
    body = await request.json()
    project = get_or_create_project(db, project_id)
    max_pos = max((c.position for c in project.characters), default=0)
    ch = Character(
        project_id=project.id, position=max_pos + 1,
        name=str(body.get("name") or "Без имени"),
        description=str(body.get("description") or ""),
        is_main=bool(body.get("is_main")),
    )
    db.add(ch)
    db.commit()
    db.refresh(ch)
    return character_dict(ch)


@app.patch("/api/characters/{char_id}")
async def update_character(char_id: int, request: Request, _=Depends(require_auth), db: Session = Depends(db_session)):
    ch = db.get(Character, char_id)
    if not ch:
        raise HTTPException(404, "персонаж не найден")
    body = await request.json()
    if "name" in body:
        ch.name = str(body["name"])
    if "description" in body:
        ch.description = str(body["description"])
    if "is_main" in body:
        ch.is_main = bool(body["is_main"])
        if ch.is_main:  # главный герой один
            for other in ch.project.characters:
                if other.id != ch.id:
                    other.is_main = False
    db.commit()
    return character_dict(ch)


@app.delete("/api/characters/{char_id}")
def delete_character(char_id: int, _=Depends(require_auth), db: Session = Depends(db_session)):
    ch = db.get(Character, char_id)
    if not ch:
        raise HTTPException(404, "персонаж не найден")
    for ph in ch.photos:
        _remove_media(ph.filename)
    # Файлы фото атрибутов каскад БД не удалит — чистим их сами.
    for attr in ch.attributes:
        for ph in attr.photos:
            _remove_media(ph.filename)
    db.delete(ch)
    db.commit()
    return {"ok": True}


@app.post("/api/characters/{char_id}/photos")
async def add_character_photo(char_id: int, photo: UploadFile, _=Depends(require_auth), db: Session = Depends(db_session)):
    ch = db.get(Character, char_id)
    if not ch:
        raise HTTPException(404, "персонаж не найден")
    ext = os.path.splitext(photo.filename or "")[1].lower() or ".jpg"
    if ext not in (".jpg", ".jpeg", ".png", ".webp"):
        raise HTTPException(400, "поддерживаются jpg/png/webp")
    fname = f"char_{uuid.uuid4().hex}{ext}"
    with open(os.path.join(UPLOAD_DIR, fname), "wb") as f:
        f.write(await photo.read())
    max_pos = max((p.position for p in ch.photos), default=0)
    ph = CharacterPhoto(character_id=ch.id, position=max_pos + 1, filename=fname)
    db.add(ph)
    db.commit()
    # ch.photos загружен ДО вставки — без refresh ответ отстаёт на одно фото.
    db.refresh(ch)
    return character_dict(ch)


@app.delete("/api/characters/photos/{photo_id}")
def delete_character_photo(photo_id: int, _=Depends(require_auth), db: Session = Depends(db_session)):
    ph = db.get(CharacterPhoto, photo_id)
    if not ph:
        raise HTTPException(404, "фото не найдено")
    _remove_media(ph.filename)
    db.delete(ph)
    db.commit()
    return {"ok": True}


# ─────────────────────────── атрибуты персонажей ───────────────────────────

@app.post("/api/characters/{char_id}/attributes")
async def create_attribute(char_id: int, request: Request, _=Depends(require_auth), db: Session = Depends(db_session)):
    ch = db.get(Character, char_id)
    if not ch:
        raise HTTPException(404, "персонаж не найден")
    body = await request.json()
    max_pos = max((a.position for a in ch.attributes), default=0)
    attr = CharacterAttribute(
        character_id=ch.id, position=max_pos + 1,
        name=str(body.get("name") or "Без имени"),
        description=str(body.get("description") or ""),
    )
    db.add(attr)
    db.commit()
    db.refresh(attr)
    return attribute_dict(attr)


@app.patch("/api/attributes/{attr_id}")
async def update_attribute(attr_id: int, request: Request, _=Depends(require_auth), db: Session = Depends(db_session)):
    attr = db.get(CharacterAttribute, attr_id)
    if not attr:
        raise HTTPException(404, "атрибут не найден")
    body = await request.json()
    if "name" in body:
        attr.name = str(body["name"])
    if "description" in body:
        attr.description = str(body["description"])
    db.commit()
    return attribute_dict(attr)


@app.delete("/api/attributes/{attr_id}")
def delete_attribute(attr_id: int, _=Depends(require_auth), db: Session = Depends(db_session)):
    attr = db.get(CharacterAttribute, attr_id)
    if not attr:
        raise HTTPException(404, "атрибут не найден")
    for ph in attr.photos:
        _remove_media(ph.filename)
    db.delete(attr)
    db.commit()
    return {"ok": True}


@app.post("/api/attributes/{attr_id}/photos")
async def add_attribute_photo(attr_id: int, photo: UploadFile, _=Depends(require_auth), db: Session = Depends(db_session)):
    attr = db.get(CharacterAttribute, attr_id)
    if not attr:
        raise HTTPException(404, "атрибут не найден")
    ext = os.path.splitext(photo.filename or "")[1].lower() or ".jpg"
    if ext not in (".jpg", ".jpeg", ".png", ".webp"):
        raise HTTPException(400, "поддерживаются jpg/png/webp")
    fname = f"attr_{uuid.uuid4().hex}{ext}"
    with open(os.path.join(UPLOAD_DIR, fname), "wb") as f:
        f.write(await photo.read())
    max_pos = max((p.position for p in attr.photos), default=0)
    ph = AttributePhoto(attribute_id=attr.id, position=max_pos + 1, filename=fname)
    db.add(ph)
    db.commit()
    # attr.photos загружен ДО вставки — без refresh ответ отстаёт на одно фото.
    db.refresh(attr)
    return attribute_dict(attr)


@app.delete("/api/attributes/photos/{photo_id}")
def delete_attribute_photo(photo_id: int, _=Depends(require_auth), db: Session = Depends(db_session)):
    ph = db.get(AttributePhoto, photo_id)
    if not ph:
        raise HTTPException(404, "фото не найдено")
    _remove_media(ph.filename)
    db.delete(ph)
    db.commit()
    return {"ok": True}


# ─────────────────────────── ручное добавление кадра ───────────────────────────

def _renumber_scenes(track: Track) -> None:
    """После вставки/удаления кадра: позиции подряд и честный таймлайн."""
    cursor = 0
    for i, s in enumerate(sorted(track.scenes, key=lambda x: (x.position, x.id)), start=1):
        s.position = i
        s.start_sec = cursor
        cursor += s.duration_sec


@app.post("/api/tracks/{track_id}/scenes")
async def add_scene(track_id: int, request: Request, _=Depends(require_auth), db: Session = Depends(db_session)):
    """Ручной кадр. after_position: после какого кадра вставить (0 = в начало,
    не передан = в конец)."""
    track = db.get(Track, track_id)
    if not track:
        raise HTTPException(404, "трек не найден")
    body = await request.json()
    after = body.get("after_position")
    max_pos = max((s.position for s in track.scenes), default=0)
    after = max_pos if after is None else max(0, min(int(after), max_pos))
    for s in track.scenes:
        if s.position > after:
            s.position += 1
    scene = Scene(
        track_id=track.id, position=after + 1,
        duration_sec=max(2, min(12, int(body.get("duration_sec") or 5))),
        lyric_line=str(body.get("lyric_line") or ""),
        characters=str(body.get("characters") or ""),
        shot_size=str(body.get("shot_size") or ""),
        camera_move=str(body.get("camera_move") or ""),
        shot_note=str(body.get("shot_note") or ""),
        image_prompt=str(body.get("image_prompt") or ""),
        image_prompt_last=str(body.get("image_prompt_last") or ""),
        motion_prompt=str(body.get("motion_prompt") or ""),
        video_provider="seedance" if mediagen.seedance_available() else "grok",
    )
    db.add(scene)
    db.flush()
    # ORM-коллекция track.scenes собрана ДО вставки — без expire пересчёт
    # не увидит новый кадр и раздаст задвоенные позиции.
    db.expire(track, ["scenes"])
    _renumber_scenes(track)
    db.commit()
    db.refresh(scene)
    return scene_dict(scene)



def _run_all_frames(track_id: int) -> None:
    """Пакетная генерация: кадры ВСЕХ сцен трека подряд, одна за другой.

    Последовательно, а не парал­лельно: шлюзы картинок обслуживают один
    браузер, и залп из 25 сцен просто выстроится в ту же очередь, но с
    таймаутами. Сцены с уже готовыми кадрами пропускаются."""
    db = SessionLocal()
    try:
        track = db.get(Track, track_id)
        if not track:
            return
        scene_ids = [s.id for s in track.scenes
                     if not (s.image_filename and s.image_last_filename)
                     and (s.image_prompt or "").strip()
                     and not s.image_prompt.startswith("(готовый кадр")]
        db.close()
        log.info("пакет кадров трека %s: %s сцен", track_id, len(scene_ids))
        for sid in scene_ids:
            _run_scene_frames(sid)
    except Exception as e:  # noqa: BLE001
        log.warning("пакет кадров трека %s упал: %s", track_id, e)


@app.post("/api/tracks/{track_id}/generate-all-frames")
def generate_all_frames(track_id: int, _=Depends(require_auth), db: Session = Depends(db_session)):
    from threading import Thread
    track = db.get(Track, track_id)
    if not track:
        raise HTTPException(404, "трек не найден")
    if not track.scenes:
        raise HTTPException(400, "сначала сгенерируй раскадровку")
    todo = 0
    for s in track.scenes:
        if not (s.image_filename and s.image_last_filename) and (s.image_prompt or "").strip()                 and not s.image_prompt.startswith("(готовый кадр"):
            s.image_status = "queued"
            todo += 1
    db.commit()
    if not todo:
        raise HTTPException(400, "у всех сцен кадры уже готовы")
    Thread(target=_run_all_frames, args=(track_id,), daemon=True).start()
    return {"ok": True, "queued": todo}


@app.get("/api/health")
def health():
    return {"ok": True, "ts": int(time.time())}


# ─────────────────────────────── статика (SPA) ───────────────────────────────

FRONTEND_DIR = os.environ.get("FRONTEND_DIR", "/app/static")
if os.path.isdir(FRONTEND_DIR):
    app.mount("/", StaticFiles(directory=FRONTEND_DIR, html=True), name="static")
