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
import mediagen
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
        "image_prompt_last": s.image_prompt_last,
        "image_url": f"/api/media/{s.image_filename}" if s.image_filename else "",
        "image_last_url": f"/api/media/{s.image_last_filename}" if s.image_last_filename else "",
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
        "audio_duration_sec": t.audio_duration_sec,
        "scenes_status": t.scenes_status, "scenes_error": t.scenes_error,
        "scenes_count": len(t.scenes),
        "approved_count": sum(1 for s in t.scenes if s.approved),
        "storyboard_url": f"/api/media/{t.storyboard_filename}" if t.storyboard_filename else "",
        "storyboard_status": t.storyboard_status, "storyboard_error": t.storyboard_error,
        "clip_url": f"/api/media/{t.clip_filename}" if t.clip_filename else "",
        "clip_status": t.clip_status, "clip_error": t.clip_error,
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
    for s in track.scenes:
        _remove_media(s.image_filename)
        _remove_media(s.video_filename)
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
    _remove_media(scene.image_filename)
    _remove_media(scene.video_filename)
    db.delete(scene)
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
        f"Main character reference (must stay identical across every shot): {project.character_bible}",
    ]
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
        first_data, first_mime = asyncio.run(
            mediagen.generate_image(_frame_prompt(scene, track, "first")))
        last_data, last_mime = asyncio.run(
            mediagen.generate_image(_frame_prompt(scene, track, "last")))

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


@app.get("/api/media/{filename}")
def get_media(filename: str, _=Depends(require_auth)):
    path = os.path.join(UPLOAD_DIR, os.path.basename(filename))
    if not os.path.exists(path):
        raise HTTPException(404, "файл не найден")
    return FileResponse(path)


@app.get("/api/providers")
def providers(_=Depends(require_auth)):
    return {"video": mediagen.video_providers(), "seedance": mediagen.seedance_available()}


@app.get("/api/health")
def health():
    return {"ok": True, "ts": int(time.time())}


# ─────────────────────────────── статика (SPA) ───────────────────────────────

FRONTEND_DIR = os.environ.get("FRONTEND_DIR", "/app/static")
if os.path.isdir(FRONTEND_DIR):
    app.mount("/", StaticFiles(directory=FRONTEND_DIR, html=True), name="static")
