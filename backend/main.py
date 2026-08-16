"""rap-clips-studio — личный инструмент: раскадровка клипа на 8 рэп-треков.

Не часть организма BIOAURA: свой пароль вместо SSO, своя SQLite, свой домен.
"""
from __future__ import annotations

import logging
import os
import re
import subprocess
import time
import uuid

from fastapi import Depends, FastAPI, Form, HTTPException, Request, Response, UploadFile
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from itsdangerous import BadSignature, URLSafeTimedSerializer
from sqlalchemy.orm import Session

import claude
from db import Project, Scene, SessionLocal, Track, init_db, now

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


def get_or_create_project(db: Session) -> Project:
    project = db.query(Project).first()
    if not project:
        project = Project(name="Клип")
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

def scene_dict(s: Scene) -> dict:
    return {
        "id": s.id, "position": s.position, "start_sec": s.start_sec,
        "duration_sec": s.duration_sec, "lyric_line": s.lyric_line,
        "shot_size": s.shot_size, "camera_move": s.camera_move,
        "image_prompt": s.image_prompt, "motion_prompt": s.motion_prompt,
        "shot_note": s.shot_note,
    }


def track_dict(t: Track, with_scenes: bool = False) -> dict:
    d = {
        "id": t.id, "position": t.position, "title": t.title, "lyrics": t.lyrics,
        "comment": t.comment, "style": t.style, "audio_filename": t.audio_filename,
        "audio_duration_sec": t.audio_duration_sec,
        "scenes_status": t.scenes_status, "scenes_error": t.scenes_error,
        "scenes_count": len(t.scenes),
    }
    if with_scenes:
        d["scenes"] = [scene_dict(s) for s in t.scenes]
    return d


def project_dict(p: Project, with_scenes: bool = False) -> dict:
    return {
        "id": p.id, "name": p.name, "character_bible": p.character_bible,
        "story": p.story, "story_status": p.story_status, "story_error": p.story_error,
        "tracks": [track_dict(t, with_scenes) for t in p.tracks],
    }


# ─────────────────────────────── проект ───────────────────────────────

@app.get("/api/project")
def get_project(_=Depends(require_auth), db: Session = Depends(db_session)):
    return project_dict(get_or_create_project(db), with_scenes=True)


@app.patch("/api/project")
async def update_project(request: Request, _=Depends(require_auth), db: Session = Depends(db_session)):
    body = await request.json()
    project = get_or_create_project(db)
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
             "comment": t.comment, "style": t.style}
            for t in project.tracks
        ]
        import asyncio
        result = asyncio.run(claude.generate_story(project.character_bible, tracks))
        project.character_bible = result.get("character_bible", project.character_bible)
        project.story = result.get("story", "")
        notes = {n.get("position"): n.get("note", "") for n in result.get("track_notes", [])}
        for t in project.tracks:
            note = notes.get(t.position)
            if note:
                t.comment = (t.comment + f"\n\n[режиссёрская заметка] {note}").strip()
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
def generate_story(_=Depends(require_auth), db: Session = Depends(db_session)):
    from threading import Thread
    project = get_or_create_project(db)
    if not project.tracks:
        raise HTTPException(400, "сначала загрузи хотя бы один трек")
    project.story_status = "queued"
    db.commit()
    Thread(target=_run_story_generation, args=(project.id,), daemon=True).start()
    return {"ok": True}


# ─────────────────────────────── треки ───────────────────────────────

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
    _=Depends(require_auth), db: Session = Depends(db_session),
):
    project = get_or_create_project(db)
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
        note_match = re.search(r"\[режиссёрская заметка\]\s*(.+)$", track.comment, re.DOTALL)
        track_note = note_match.group(1).strip() if note_match else ""
        import asyncio
        result = asyncio.run(claude.generate_scenes(
            story=project.story, character_bible=project.character_bible,
            track_note=track_note, title=track.title, lyrics=track.lyrics,
            comment=track.comment, style=track.style,
            duration_sec=track.audio_duration_sec or 180,
        ))
        for s in list(track.scenes):
            db.delete(s)
        db.flush()
        cursor = 0
        for i, sc in enumerate(result.get("scenes", []), start=1):
            dur = max(2, min(12, int(sc.get("duration_sec") or 6)))
            db.add(Scene(
                track_id=track.id, position=i, start_sec=cursor, duration_sec=dur,
                lyric_line=str(sc.get("lyric_line") or ""),
                shot_size=str(sc.get("shot_size") or ""),
                camera_move=str(sc.get("camera_move") or ""),
                image_prompt=str(sc.get("image_prompt") or ""),
                motion_prompt=str(sc.get("motion_prompt") or ""),
                shot_note=str(sc.get("shot_note") or ""),
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
    db.delete(scene)
    db.commit()
    return {"ok": True}


@app.get("/api/health")
def health():
    return {"ok": True, "ts": int(time.time())}


# ─────────────────────────────── статика (SPA) ───────────────────────────────

FRONTEND_DIR = os.environ.get("FRONTEND_DIR", "/app/static")
if os.path.isdir(FRONTEND_DIR):
    app.mount("/", StaticFiles(directory=FRONTEND_DIR, html=True), name="static")
