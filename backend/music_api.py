"""Раздел «Музыка»: загрузка трека, генерация, мастеринг, подготовка релиза.

ЧТО ЭТО ЗА РАЗДЕЛ И ЧЕМ ОН НЕ ЯВЛЯЕТСЯ.

Студия клипов работает с треком как с материалом для видео. Здесь трек —
сам продукт: его приносят, меряют, мастерят и готовят к выпуску. Поэтому
своя сущность (`db.MusicTrack`), а не семь новых колонок в `Track`, и свой
экран, а не вкладка внутри карточки клипа.

ЧЕТЫРЕ ЧЕСТНЫЕ ГРАНИЦЫ. Все четыре видны в интерфейсе, а не только здесь:

  1. **Генерация музыки** идёт через ElevenLabs Music. Ключа на проде нет →
     раздел генерации показан, но выключен, и написано, чего не хватает.
     Никакой «демо-генерации» и никакого молчаливого фолбэка.

  2. **Мастеринг** — не нейросеть. Matchering по эталону (свой контейнер,
     себестоимость 0), запасной путь — нормализация громкости ffmpeg'ом,
     платный облачный RoEx — только явной кнопкой. Чем именно мастерили,
     написано на результате: если движок по эталону был недоступен и
     сработала нормализация, человек читает это, а не догадывается.

  3. **Дистрибуции на площадки у нас НЕТ.** Прямой публикации в Spotify,
     Apple Music, VK и Яндекс.Музыку без договора с дистрибьютором не
     существует ни у кого — это всегда чужой договор и чужая отгрузка.
     Поэтому кнопка «Отправить на площадки» делает ровно две вещи:
     собирает комплект (мастер + обложка + метаданные) и создаёт заявку в
     наш лейбл (`MusicLead`, та же таблица, что у формы /music.html).
     Сроки в ответе — «после подписания договора», а не «через час».

  4. **Соцсети — другое дело.** Instagram, YouTube и TikTok публикуются
     нашим хост-агентом (`backend/social.py`) по-настоящему. Аудио они не
     принимают, поэтому сначала из обложки и дорожки собирается mp4. Если
     служба публикации погашена — так и написано, кнопка выключена.

ДЕНЬГИ. Списание — до обращения к внешнему сервису, возврат — при отказе
(закон §6.9 дизайн-системы). Мастеринг своим движком стоит символические
очки, облачный — ровно себестоимость.

Модуль не импортирует main на верхнем уровне: он подключается ИЗ main.py,
и импорт наверху дал бы цикл. Всё, что нужно от студии (сессия, деньги,
регистрация файлов), достаётся через `_core()` внутри обработчиков — тот же
приём, что в `backend/bot_api.py`.
"""
from __future__ import annotations

import csv
import io
import json
import logging
import os
import re
import subprocess
import threading
import time
import uuid
import zipfile
from datetime import date

from fastapi import APIRouter, Depends, Form, HTTPException, Request, UploadFile
from sqlalchemy.orm import Session

import audio
import audio_analysis
import mastering
from db import MusicLead, MusicTrack, SessionLocal, User, now

log = logging.getLogger("rapclips.music")
router = APIRouter()

UPLOAD_DIR = os.environ.get("UPLOAD_DIR", "/data/uploads")
FFMPEG = os.environ.get("FFMPEG_BIN", "ffmpeg")
FFPROBE = os.environ.get("FFPROBE_BIN", "ffprobe")
os.makedirs(UPLOAD_DIR, exist_ok=True)

# ─────────────────────────── границы приёма файла ───────────────────────────
# Проверяются ДО списания очков и до любой обработки (дизайн-система §4.12).
MAX_UPLOAD_MB = float(os.environ.get("MUSIC_MAX_UPLOAD_MB", "200"))
MAX_DURATION_S = float(os.environ.get("MUSIC_MAX_DURATION_S", "1200"))   # 20 минут
MIN_DURATION_S = float(os.environ.get("MUSIC_MIN_DURATION_S", "3"))
MAX_COVER_MB = float(os.environ.get("MUSIC_MAX_COVER_MB", "25"))
# Расширения, которые ffmpeg открывает и которые несут музыку. Список белый:
# принять «что угодно» и упасть на декодировании — это ошибка после загрузки
# стомегабайтного файла, то есть худший момент из возможных.
AUDIO_EXT = (".mp3", ".wav", ".flac", ".m4a", ".aac", ".ogg", ".oga",
             ".opus", ".aif", ".aiff", ".wma", ".alac")
COVER_EXT = (".jpg", ".jpeg", ".png", ".webp")
# 3000×3000 — то, что просят все дистрибьюторы. Меньше 1400 не примет никто.
COVER_TARGET = 3000
COVER_MIN = 1400

# Библиотека жанровых эталонов для мастеринга по референсу. Это ФАЙЛЫ на
# диске, а не строки в коде: чужие мастера нельзя класть в репозиторий, и
# владелец наполняет папку сам. Пусто → в интерфейсе честная пустая полка,
# а не выдуманный список жанров, за которым ничего нет.
REF_DIR = os.environ.get("MUSIC_REF_DIR", "/data/references")

# ─────────────────────────────── цены в очках ───────────────────────────────
# Считаны по худшему для нас курсу очка (1.13 ¢, пакет p15000) — см. шапку
# backend/audio_patch.md, там же полный расчёт.
COST_MUSIC_PER_30S = int(os.environ.get("MUSIC_COST_PER_30S", "12"))
COST_MASTER_LOCAL = int(os.environ.get("MUSIC_COST_MASTER", "6"))
COST_MASTER_CLOUD = int(os.environ.get("MUSIC_COST_MASTER_CLOUD", "200"))
COST_VIDEO = int(os.environ.get("MUSIC_COST_VIDEO", "4"))
# Замер, разбор, пакет релиза и заявка считаются у нас на CPU — бесплатно.

# Длина генерации: границы Eleven Music, продублированы здесь, чтобы форма
# знала их до запроса, а не узнавала из ошибки.
GEN_MIN_S = 10
GEN_MAX_S = int(audio.MUSIC_MAX_MS / 1000)

# Профили громкости. Человеку показываем задачу («под стрим»), а не число:
# −14 LUFS ничего не говорит тому, кто не сводит сам. Числа при этом видны
# рядом — врать про «магию» мы не собираемся.
TARGETS = (
    {"id": "streaming", "lufs": -14.0, "tp": -1.0},
    {"id": "club", "lufs": -9.0, "tp": -1.0},
    {"id": "youtube", "lufs": -13.0, "tp": -1.0},
    {"id": "cd", "lufs": -9.0, "tp": -0.3},
)
TARGET_IDS = tuple(x["id"] for x in TARGETS)

PLATFORMS = ("instagram", "youtube", "tiktok")

# Дистрибьютор, через которого пойдёт отгрузка, когда будет подписан договор.
# Читается из окружения: пока договора нет, врать названием нельзя.
DISTRIBUTOR = os.environ.get("MUSIC_DISTRIBUTOR", "Zvonko Digital")
DISTRIBUTOR_READY = os.environ.get("MUSIC_DISTRIBUTOR_READY", "").strip().lower() in ("1", "yes", "true")


def _core():
    """main импортируется ЛЕНИВО и только внутри обработчиков — см. bot_api."""
    import main  # noqa: PLC0415
    return main


# ────────────────────────── сессия, владение, ошибки ──────────────────────────

def db_session():
    session = SessionLocal()
    try:
        yield session
    finally:
        session.close()


def current_user(request: Request, db: Session = Depends(db_session)) -> User:
    """Тот же вход, что и в студии: своей авторизации раздел не заводит."""
    core = _core()
    user = core._resolve_user(request, db)
    if not user:
        raise HTTPException(401, "не авторизован")
    core._guard_user(user)
    return user


def _own(db: Session, user: User, track_id: int) -> MusicTrack:
    """Чужой трек — 404, а не 403: чужим не раскрываем даже факт наличия."""
    t = db.get(MusicTrack, int(track_id or 0))
    if not t or t.deleted_at is not None or not (user.is_admin or t.owner_id == user.id):
        raise HTTPException(404, "трек не найден")
    return t


def _api_error(status: int, code: str, message: str = "", **extra):
    return _core().ApiError(status, code, message, **extra)


def _audio_error(e):
    """Ошибка аудио-модуля → ApiError с машинным кодом.

    Фронту важно различать «ключа нет» и «слишком длинный кусок»: разбирать
    текст ошибки на клиенте — верный способ сломаться на первой правке."""
    status = {"disabled": 503, "network": 503, "timeout": 504,
              "input": 400, "auth": 502, "credits": 402}.get(getattr(e, "code", ""), 502)
    return _api_error(status, f"audio_{getattr(e, 'code', 'error')}", str(e))


def _mastering_error(e):
    status = {"disabled": 503, "input": 400, "network": 503,
              "timeout": 504, "credits": 402}.get(getattr(e, "code", ""), 502)
    return _api_error(status, f"master_{getattr(e, 'code', 'error')}", str(e))


# ──────────────────────────────── файлы ────────────────────────────────

def _ext_of(name: str) -> str:
    return os.path.splitext(str(name or ""))[1].lower()


async def _save_upload(file: UploadFile, dest_path: str, max_mb: float) -> int:
    """Приём файла кусками с потолком по размеру.

    Читать целиком в память нельзя: двухсотмегабайтный wav в оперативке
    контейнера — это перезапуск сервиса ради одной загрузки. Потолок
    проверяем ПО ХОДУ и обрываем на первом лишнем куске, а не после того,
    как всё легло на диск."""
    limit = int(max_mb * 1024 * 1024)
    size = 0
    try:
        with open(dest_path, "wb") as out:
            while True:
                chunk = await file.read(1024 * 1024)
                if not chunk:
                    break
                size += len(chunk)
                if size > limit:
                    raise _api_error(
                        413, "file_too_big",
                        f"The file is larger than {int(max_mb)} MB. "
                        f"Bounce a shorter version or use a compressed format.")
                out.write(chunk)
    except Exception:
        try:
            os.remove(dest_path)
        except OSError:
            pass
        raise
    return size


def _probe_duration(path: str) -> float:
    return audio.duration_sec(path)


def _probe_image(path: str) -> tuple:
    """Размер картинки через ffprobe: PIL в образе нет, а ffmpeg есть всегда."""
    try:
        r = subprocess.run(
            [FFPROBE, "-v", "error", "-select_streams", "v:0",
             "-show_entries", "stream=width,height",
             "-of", "csv=p=0:s=x", path],
            capture_output=True, text=True, timeout=30)
        w, _, h = (r.stdout or "").strip().partition("x")
        return int(w or 0), int(h or 0)
    except Exception:  # noqa: BLE001
        return 0, 0


# ──────────────────────────── сериализация ────────────────────────────

def _media(fname: str) -> str:
    return f"/api/media/{fname}" if fname else ""


def _track_dict(t: MusicTrack, *, full: bool = False) -> dict:
    """Карточка трека для интерфейса.

    Волну и разбор отдаём только в `full` (карточка открыта): список из
    полусотни треков с огибающими по 480 чисел — это мегабайт на прокрутку."""
    d = {
        "id": t.id,
        "created_at": (t.created_at or now()).isoformat(),
        "title": t.title,
        "artist": t.artist,
        "origin": t.origin,
        "source_name": t.source_name,
        "source_url": _media(t.source_filename),
        "source_filename": t.source_filename,
        "source_ext": t.source_ext,
        "source_bytes": t.source_bytes,
        "duration_sec": round(float(t.duration_sec or 0), 2),
        "lufs": t.lufs, "true_peak": t.true_peak, "lra": t.lra,
        "probe_status": t.probe_status, "probe_note": t.probe_note,
        "bpm": t.bpm,
        "master_url": _media(t.master_filename),
        "master_filename": t.master_filename,
        "master_status": t.master_status,
        "master_note": t.master_note,
        "master_engine": t.master_engine,
        "master_target": t.master_target,
        "master_ref_title": t.master_ref_title,
        "master_lufs": t.master_lufs,
        "master_true_peak": t.master_true_peak,
        "master_lra": t.master_lra,
        "cover_url": _media(t.cover_filename),
        "cover_w": t.cover_w, "cover_h": t.cover_h,
        "video_url": _media(t.video_filename),
        "video_status": t.video_status, "video_note": t.video_note,
        "social_status": t.social_status, "social_note": t.social_note,
        "social_url": t.social_url, "social_platform": t.social_platform,
        "package_url": _media(t.package_filename),
        "package_filename": t.package_filename,
        "release_status": t.release_status,
        "submitted_at": t.submitted_at.isoformat() if t.submitted_at else "",
        "meta": {
            "title": t.title, "artist": t.artist, "feat": t.feat,
            "genre": t.genre, "language": t.language, "version": t.version,
            "release_date": t.release_date, "isrc": t.isrc, "upc": t.upc,
            "explicit": bool(t.explicit), "ai_disclosure": t.ai_disclosure,
            "credits": t.credits, "notes": t.notes,
        },
    }
    if full:
        d["wave"] = _json_or(t.wave_json, [])
        analysis = _json_or(t.analysis_json, {})
        d["analysis"] = {
            "bpm": analysis.get("bpm"),
            "bpm_alt": analysis.get("bpm_alt"),
            "confidence": analysis.get("bpm_confidence"),
            "bar_sec": analysis.get("bar_sec"),
            "sections": analysis.get("sections") or [],
            "engine": analysis.get("engine") or "",
        } if analysis else None
        d["gen_prompt"] = t.gen_prompt
    return d


def _json_or(raw: str, fallback):
    try:
        return json.loads(raw) if raw else fallback
    except ValueError:
        return fallback


# ═══════════════════════════ приём и замер трека ═══════════════════════════

def _probe_async(track_id: int) -> None:
    """Замер и разбор в фоне: громкость, огибающая, темп, секции.

    Почему фоном, а не в ответе на загрузку: измерение loudnorm'ом — это
    полный проход по файлу, на длинном wav это десятки секунд, и всё это
    время человек смотрел бы на крутилку вместо своего трека. А почему
    вообще СРАЗУ, до всякой кнопки: замеры и волна — единственное, что
    доказывает нашу компетентность бесплатно и до оплаты."""
    db = SessionLocal()
    try:
        t = db.get(MusicTrack, track_id)
        if not t or not t.source_filename:
            return
        t.probe_status = "running"
        db.commit()
        path = os.path.join(UPLOAD_DIR, t.source_filename)

        # Волна первой: она нужна интерфейсу раньше всего остального.
        try:
            wave = audio_analysis.waveform(path)
            if wave:
                t.wave_json = json.dumps(wave)
                db.commit()
        except Exception as e:  # noqa: BLE001
            log.warning("музыка %s: волна не посчиталась: %s", track_id, str(e)[:200])

        note = []
        got_loudness = got_tempo = False
        try:
            m = mastering.measure(path)
            t.lufs, t.true_peak, t.lra = m["lufs"], m["true_peak"], m["lra"]
            got_loudness = True
        except Exception as e:  # noqa: BLE001
            note.append(f"loudness: {str(e)[:200]}")

        if audio_analysis.available():
            try:
                data = audio_analysis.analyze(path)
                t.analysis_json = json.dumps(data)
                t.bpm = int(round(data.get("bpm") or 0))
                got_tempo = True
            except Exception as e:  # noqa: BLE001
                note.append(f"tempo: {str(e)[:200]}")
        else:
            note.append("tempo: numpy is not installed in this image")

        # «Ошибка» — только когда не вышло ВООБЩЕ ничего. Посчитанная громкость
        # без темпа — это результат, и прятать его за красной плашкой нельзя.
        t.probe_note = "; ".join(note)
        t.probe_status = "done" if (got_loudness or got_tempo) else "error"
        t.updated_at = now()
        db.commit()
        log.info("музыка %s: замер готов (%s LUFS, %s BPM)", track_id, t.lufs, t.bpm)
    except Exception as e:  # noqa: BLE001
        db.rollback()
        t = db.get(MusicTrack, track_id)
        if t:
            t.probe_status = "error"
            t.probe_note = str(e)[:500]
            db.commit()
        log.warning("музыка %s: замер упал: %s", track_id, str(e)[:300])
    finally:
        db.close()


def _start_probe(track_id: int) -> None:
    threading.Thread(target=_probe_async, args=(track_id,), daemon=True).start()


@router.post("/api/music/tracks")
async def upload_track(
    file: UploadFile | None = None,
    title: str = Form(""),
    artist: str = Form(""),
    user: User = Depends(current_user),
    db: Session = Depends(db_session),
):
    """Окно загрузки трека: файл → карточка с замерами.

    Проверки идут по порядку «дёшево → дорого»: расширение (мгновенно),
    размер (по ходу приёма), наличие дорожки и длительность (ffprobe на уже
    лежащем файле). Отклонённый файл удаляется сразу — мусор в UPLOAD_DIR
    от неудачных загрузок копится быстрее всего остального."""
    if file is None or not (file.filename or "").strip():
        raise HTTPException(400, "нечего загружать: файл не пришёл")
    ext = _ext_of(file.filename)
    if ext not in AUDIO_EXT:
        raise _api_error(
            415, "bad_format",
            f"{ext or 'this file'} is not an audio format we can read. "
            f"Accepted: {', '.join(x.lstrip('.') for x in AUDIO_EXT)}.")

    fname = f"mus_{uuid.uuid4().hex}{ext}"
    path = os.path.join(UPLOAD_DIR, fname)
    size = await _save_upload(file, path, MAX_UPLOAD_MB)

    if not audio.has_audio(path):
        os.remove(path)
        raise _api_error(415, "no_audio_stream",
                         "There is no audio stream in this file.")
    dur = _probe_duration(path)
    if dur < MIN_DURATION_S:
        os.remove(path)
        raise _api_error(400, "too_short",
                         f"The track is only {dur:g} s long — that is shorter than "
                         f"{MIN_DURATION_S:g} s and there is nothing to master.")
    if dur > MAX_DURATION_S:
        os.remove(path)
        raise _api_error(
            400, "too_long",
            f"The track is {int(dur // 60)}:{int(dur % 60):02d} long, the limit is "
            f"{int(MAX_DURATION_S // 60)} minutes.")

    core = _core()
    t = MusicTrack(
        owner_id=user.id,
        source_filename=fname,
        source_name=os.path.basename(file.filename or "")[:200],
        source_ext=ext.lstrip("."),
        source_bytes=size,
        duration_sec=dur,
        origin="upload",
        probe_status="queued",
        title=(title or os.path.splitext(os.path.basename(file.filename or ""))[0])[:200],
        artist=(artist or "")[:200],
    )
    db.add(t)
    db.commit()
    db.refresh(t)
    core._reg_file(db, fname, user.id, kind="audio")
    db.commit()
    _start_probe(t.id)
    log.info("музыка: принят трек %s (%s, %.1f МБ, %.1f с)",
             t.id, ext, size / 1048576, dur)
    return _track_dict(t, full=True)


@router.get("/api/music/tracks")
def list_tracks(limit: int = 30, before: int = 0,
                user: User = Depends(current_user),
                db: Session = Depends(db_session)):
    """Лента треков. Курсор по id вниз — бесконечная прокрутка, не страницы."""
    # Даже админ видит здесь СВОИ треки: раздел — рабочее место, а не витрина
    # чужих релизов. Чужой трек достаётся точечно, по id, через _own.
    q = (db.query(MusicTrack)
         .filter(MusicTrack.deleted_at.is_(None), MusicTrack.owner_id == user.id))
    if before:
        q = q.filter(MusicTrack.id < int(before))
    n = max(1, min(100, int(limit or 30)))
    rows = q.order_by(MusicTrack.id.desc()).limit(n + 1).all()
    more = len(rows) > n
    rows = rows[:n]
    total = (db.query(MusicTrack)
             .filter(MusicTrack.deleted_at.is_(None),
                     MusicTrack.owner_id == user.id).count())
    return {
        "items": [_track_dict(t) for t in rows],
        "next_cursor": rows[-1].id if (more and rows) else 0,
        "total": total,
    }


@router.get("/api/music/tracks/{track_id}")
def get_track(track_id: int, user: User = Depends(current_user),
              db: Session = Depends(db_session)):
    return _track_dict(_own(db, user, track_id), full=True)


# Поля, которые человек правит руками. Белый список, а не «что прислали»:
# PATCH с произвольными ключами — это дверь к owner_id и master_status.
META_FIELDS = ("title", "artist", "feat", "genre", "language", "version",
               "release_date", "isrc", "upc", "ai_disclosure", "credits", "notes")
AI_DISCLOSURE = ("", "none", "music", "vocals", "all")


@router.patch("/api/music/tracks/{track_id}")
async def update_track(track_id: int, request: Request,
                       user: User = Depends(current_user),
                       db: Session = Depends(db_session)):
    """Метаданные релиза. Ровно эти поля уезжают в пакет и в заявку."""
    t = _own(db, user, track_id)
    body = await request.json()
    if not isinstance(body, dict):
        raise HTTPException(400, "ожидается объект")
    for key in META_FIELDS:
        if key not in body:
            continue
        val = str(body.get(key) or "").strip()[:2000]
        if key == "release_date" and val and not re.fullmatch(r"\d{4}-\d{2}-\d{2}", val):
            raise _api_error(400, "bad_date", "Release date must look like 2026-09-01.")
        if key == "isrc" and val and not re.fullmatch(r"[A-Za-z]{2}[A-Za-z0-9]{3}\d{7}", val.replace("-", "")):
            raise _api_error(400, "bad_isrc",
                             "An ISRC looks like RUA1B2500001 — 12 characters. "
                             "Leave it empty and the distributor will issue one.")
        if key == "ai_disclosure" and val not in AI_DISCLOSURE:
            raise _api_error(400, "bad_disclosure", "Unknown AI disclosure value.")
        setattr(t, key, val)
    if "explicit" in body:
        t.explicit = bool(body.get("explicit"))
    t.updated_at = now()
    db.commit()
    return _track_dict(t, full=True)


@router.delete("/api/music/tracks/{track_id}")
def delete_track(track_id: int, user: User = Depends(current_user),
                 db: Session = Depends(db_session)):
    """Мягкое удаление: строка остаётся ради истории очков, файлы уносим.

    Оригинал удаляем ПОСЛЕДНИМ и только вместе со всем остальным — «удалил
    релиз, а мастер остался жить в архиве» выглядит как утечка."""
    t = _own(db, user, track_id)
    core = _core()
    for f in (t.master_filename, t.video_filename, t.package_filename,
              t.cover_filename, t.master_ref_filename, t.source_filename):
        if f:
            core._remove_media(f)
    t.deleted_at = now()
    db.commit()
    return {"ok": True, "id": t.id}


@router.get("/api/music/tracks/{track_id}/analysis")
def track_analysis(track_id: int, refresh: bool = False,
                   user: User = Depends(current_user),
                   db: Session = Depends(db_session)):
    """Темп, сетка и секции. Считается у нас на CPU — бесплатно."""
    t = _own(db, user, track_id)
    if not audio_analysis.available():
        raise _api_error(503, "analysis_off",
                         "Tempo analysis is off in this build: numpy is missing.")
    data = _json_or(t.analysis_json, None)
    if data is None or refresh:
        try:
            data = audio_analysis.analyze(os.path.join(UPLOAD_DIR, t.source_filename))
        except audio_analysis.AnalysisError as e:
            raise _audio_error(e)
        t.analysis_json = json.dumps(data)
        t.bpm = int(round(data.get("bpm") or 0))
        db.commit()
    return {
        "bpm": data.get("bpm"),
        # 87 и 174 автомат не различает, и делать вид, что различает, — врать.
        "bpm_alt": data.get("bpm_alt"),
        "confidence": data.get("bpm_confidence"),
        "bar_sec": data.get("bar_sec"),
        "duration_sec": data.get("duration_sec"),
        "sections": data.get("sections"),
        "engine": data.get("engine"),
    }


# ═══════════════════════════ генерация музыки ═══════════════════════════

@router.post("/api/music/generate")
async def generate_music(
    prompt: str = Form(""), seconds: float = Form(60.0),
    instrumental: bool = Form(False), title: str = Form(""),
    user: User = Depends(current_user), db: Session = Depends(db_session),
):
    """Промпт → трек в разделе. Списываем ДО ElevenLabs, возвращаем при отказе.

    Сгенерированное приходит примерно на −24 LUFS, то есть на десяток
    децибел тише площадок. Поэтому карточка сразу предлагает мастеринг, а не
    делает вид, что файл готов к выпуску."""
    prompt = (prompt or "").strip()
    if not prompt:
        raise HTTPException(400, "опиши, какая нужна музыка")
    if not audio.available():
        raise _api_error(
            503, "audio_disabled",
            "Music generation is not connected: the service has no ElevenLabs key yet. "
            "Upload your own track instead — mastering and release prep work without it.")
    secs = max(float(GEN_MIN_S), min(float(GEN_MAX_S), float(seconds or 60)))
    core = _core()
    cost = max(1, -(-int(round(secs)) // 30)) * COST_MUSIC_PER_30S
    core._charge(db, user, cost, f"музыка (генерация {int(secs)} с)",
                 kind="audio", ref_type="music", engine="elevenlabs")
    try:
        res = await audio.compose_music(prompt, secs, instrumental=bool(instrumental))
    except audio.AudioError as e:
        core._refund(db, user, cost, "музыка не сгенерировалась",
                     kind="audio", ref_type="music")
        raise _audio_error(e)

    fname = res["filename"]
    path = os.path.join(UPLOAD_DIR, fname)
    t = MusicTrack(
        owner_id=user.id,
        source_filename=fname,
        source_name=fname,
        source_ext=os.path.splitext(fname)[1].lstrip("."),
        source_bytes=os.path.getsize(path) if os.path.exists(path) else 0,
        duration_sec=res.get("duration_sec") or secs,
        origin="generated",
        gen_prompt=prompt[:2000],
        gen_model=res.get("model") or "",
        probe_status="queued",
        title=(title or prompt)[:200],
        artist="",
        # Сгенерированное помечаем сразу: раскрытие ИИ требуют Spotify и
        # Deezer, и проставлять его задним числом человек забудет.
        ai_disclosure="all" if not instrumental else "music",
    )
    db.add(t)
    db.commit()
    db.refresh(t)
    core._reg_file(db, fname, user.id, kind="audio")
    db.commit()
    _start_probe(t.id)
    return {"charged": cost, **_track_dict(t, full=True)}


# ═════════════════════════════ мастеринг ═════════════════════════════

def _ref_library() -> list:
    """Полка эталонов: файлы из REF_DIR + необязательный manifest.json.

    Формат манифеста (пишет владелец, не мы):
        {"items":[{"file":"trap-2024.wav","title":"…","genre":"trap",
                   "note":"плотный низ, −8 LUFS"}]}
    Нет папки или она пуста — возвращаем пустой список. Придумывать
    «жанровые эталоны», за которыми нет файлов, нельзя: кнопка, которая
    ничего не делает, хуже отсутствующей кнопки."""
    if not os.path.isdir(REF_DIR):
        return []
    manifest = {}
    mpath = os.path.join(REF_DIR, "manifest.json")
    if os.path.exists(mpath):
        try:
            with open(mpath, encoding="utf-8") as f:
                data = json.load(f)
            for row in (data.get("items") or []):
                manifest[str(row.get("file") or "")] = row
        except Exception as e:  # noqa: BLE001
            log.warning("музыка: манифест эталонов не читается: %s", str(e)[:200])
    out = []
    for name in sorted(os.listdir(REF_DIR)):
        if _ext_of(name) not in AUDIO_EXT:
            continue
        row = manifest.get(name, {})
        out.append({
            "id": name,
            "title": str(row.get("title") or os.path.splitext(name)[0]),
            "genre": str(row.get("genre") or ""),
            "note": str(row.get("note") or ""),
        })
    return out


@router.get("/api/music/references")
def references(user: User = Depends(current_user)):
    """Что можно взять эталоном. Пустая полка — честное состояние, а не сбой."""
    items = _ref_library()
    return {
        "items": items,
        "dir_exists": os.path.isdir(REF_DIR),
        "note": "" if items else
                "The in-house reference shelf is empty: no reference files have been "
                "uploaded to the server yet. Upload a track you want to sound like — "
                "that works exactly the same way.",
    }


def _run_master(track_id: int, engine: str, target: str, style: str,
                ref_name: str, ref_title: str, cost: int, owner_id: int) -> None:
    """Мастеринг фоном: минуты своим движком, до четверти часа облачным.

    Очки списываем ЗДЕСЬ, по факту готового файла: у облачного движка
    кредиты тратит только финальный проход, и если чужой API упал — человек
    не платит за воздух. Баланс проверен заранее в роуте."""
    db = SessionLocal()
    core = _core()
    try:
        t = db.get(MusicTrack, track_id)
        if not t:
            return
        t.master_status = "running"
        t.master_note = ""
        db.commit()
        res = mastering.master_sync(
            t.source_filename, engine=engine, reference_filename=ref_name,
            target=target, style=style)
        t = db.get(MusicTrack, track_id)
        if not t:
            return
        old = t.master_filename
        t.master_filename = res["filename"]
        t.master_engine = res["engine"]
        t.master_note = res["note"]
        t.master_target = res.get("target") or target
        t.master_ref_filename = ref_name
        t.master_ref_title = ref_title
        t.master_lufs = res["after"]["lufs"]
        t.master_true_peak = res["after"]["true_peak"]
        t.master_lra = res["after"]["lra"]
        # Замеры исходника могли не досчитаться (замер шёл параллельно) —
        # мастеринг их всё равно посчитал, берём оттуда.
        if not t.lufs:
            t.lufs = res["before"]["lufs"]
            t.true_peak = res["before"]["true_peak"]
            t.lra = res["before"]["lra"]
        t.master_status = "done"
        t.updated_at = now()
        core._reg_file(db, res["filename"], owner_id, kind="audio")
        db.commit()
        owner = db.get(User, owner_id) if owner_id else None
        if owner and not core._take_points(db, owner, cost, f"мастеринг трека {track_id}",
                                           kind="audio", ref_type="music",
                                           ref_id=track_id, engine=res["engine"]):
            log.warning("музыка %s: мастер готов, но очков на списание не хватило", track_id)
        if old:
            core._remove_media(old)
        log.info("музыка %s: мастер готов (%s, %s → %s LUFS)", track_id,
                 res["engine"], res["before"]["lufs"], res["after"]["lufs"])
    except Exception as e:  # noqa: BLE001
        db.rollback()
        t = db.get(MusicTrack, track_id)
        if t:
            t.master_status = "error"
            t.master_note = str(e)[:500]
            db.commit()
        log.warning("музыка %s: мастеринг упал: %s", track_id, str(e)[:300])
    finally:
        db.close()


@router.post("/api/music/tracks/{track_id}/master")
async def master_track(
    track_id: int,
    engine: str = Form("auto"), target: str = Form("streaming"),
    style: str = Form("HIPHOP_GRIME"),
    ref_kind: str = Form(""), ref_id: str = Form(""),
    reference: UploadFile | None = None,
    user: User = Depends(current_user), db: Session = Depends(db_session),
):
    """Запуск мастеринга. Эталон — из полки, свой файл или прошлый эталон.

    Движок по эталону без эталона работать не может и честно понижается до
    выравнивания громкости — об этом будет сказано в отчёте на карточке, а
    не молча."""
    t = _own(db, user, track_id)
    core = _core()
    if not t.source_filename:
        raise HTTPException(400, "у трека нет дорожки")
    if t.master_status in ("queued", "running"):
        raise _api_error(409, "master_busy", "This track is already being mastered.")

    engine = engine if engine in ("auto", "matchering", "ffmpeg", "roex") else "auto"
    target = target if target in TARGET_IDS else "streaming"
    if engine == "roex":
        if not mastering.roex_available():
            raise _api_error(503, "mastering_cloud_off",
                             "Cloud mastering is not connected: there is no RoEx key.")
        if core._plan_of(user) == "free":
            raise _api_error(402, "plan_required",
                             "Cloud mastering costs us real money per track, "
                             "so it is on paid plans.", plan="pro")
    cost = COST_MASTER_CLOUD if engine == "roex" else COST_MASTER_LOCAL
    if not user.is_admin and int(user.gen_points or 0) < cost:
        raise core.NotEnoughPoints(cost, int(user.gen_points or 0),
                                   core._plan_of(user), "мастеринг")

    ref_name, ref_title = t.master_ref_filename or "", t.master_ref_title or ""
    kind = (ref_kind or "").strip().lower()
    if kind == "none":
        ref_name, ref_title = "", ""
    elif kind == "library" and ref_id:
        safe = os.path.basename(ref_id)
        src = os.path.join(REF_DIR, safe)
        if not os.path.exists(src):
            raise _api_error(404, "ref_missing", "That reference is not on the shelf.")
        # Копию кладём в UPLOAD_DIR: mastering.master_sync принимает ИМЕНА
        # файлов внутри UPLOAD_DIR и путей наружу не берёт — и правильно.
        ref_name = f"ref_{uuid.uuid4().hex}{_ext_of(safe)}"
        with open(src, "rb") as fsrc, open(os.path.join(UPLOAD_DIR, ref_name), "wb") as fdst:
            while True:
                chunk = fsrc.read(1024 * 1024)
                if not chunk:
                    break
                fdst.write(chunk)
        core._reg_file(db, ref_name, user.id, kind="audio")
        row = next((r for r in _ref_library() if r["id"] == safe), None)
        ref_title = (row or {}).get("title") or safe
    elif kind == "track" and ref_id:
        other = _own(db, user, int(ref_id or 0))
        ref_name = other.master_filename or other.source_filename
        ref_title = other.title or other.source_name
    elif reference is not None and (reference.filename or "").strip():
        ext = _ext_of(reference.filename)
        if ext not in AUDIO_EXT:
            raise _api_error(415, "bad_format", "The reference must be an audio file.")
        ref_name = f"ref_{uuid.uuid4().hex}{ext}"
        await _save_upload(reference, os.path.join(UPLOAD_DIR, ref_name), MAX_UPLOAD_MB)
        core._reg_file(db, ref_name, user.id, kind="audio")
        ref_title = os.path.basename(reference.filename)[:200]

    if engine == "matchering" and not ref_name:
        raise _api_error(400, "ref_required",
                         "Reference matching needs a track to match. Pick one from the "
                         "shelf or upload the track you want to sound like.")

    t.master_ref_filename = ref_name
    t.master_ref_title = ref_title
    t.master_status = "queued"
    t.master_note = ""
    t.master_target = target
    db.commit()
    threading.Thread(
        target=_run_master,
        args=(t.id, engine, target, style, ref_name, ref_title, cost, t.owner_id),
        daemon=True).start()
    return {"ok": True, "engine": engine, "target": target, "cost": cost,
            "reference": ref_title}


# ═══════════════════════════ обложка релиза ═══════════════════════════

@router.post("/api/music/tracks/{track_id}/cover")
async def upload_cover(track_id: int, file: UploadFile | None = None,
                       user: User = Depends(current_user),
                       db: Session = Depends(db_session)):
    """Обложка. Размер НЕ отвергаем, а измеряем и говорим правду.

    3000×3000 — то, что просят все дистрибьюторы; меньше 1400×1400 не примет
    никто. Резать чужую картинку самим нельзя: обложка — часть релиза, и
    решать, что на ней обрежется, должен автор."""
    t = _own(db, user, track_id)
    if file is None or not (file.filename or "").strip():
        raise HTTPException(400, "файл не пришёл")
    ext = _ext_of(file.filename)
    if ext not in COVER_EXT:
        raise _api_error(415, "bad_format", "Cover must be JPG, PNG or WEBP.")
    fname = f"cov_{uuid.uuid4().hex}{ext}"
    path = os.path.join(UPLOAD_DIR, fname)
    await _save_upload(file, path, MAX_COVER_MB)
    w, h = _probe_image(path)
    if not w or not h:
        os.remove(path)
        raise _api_error(415, "bad_image", "This file is not a readable image.")
    core = _core()
    old = t.cover_filename
    t.cover_filename, t.cover_w, t.cover_h = fname, w, h
    t.updated_at = now()
    core._reg_file(db, fname, user.id, kind="cover")
    db.commit()
    if old:
        core._remove_media(old)
    return {"ok": True, "cover_url": _media(fname), "w": w, "h": h,
            "square": w == h, "target": COVER_TARGET, "min": COVER_MIN}


# ═══════════════════════ проверка требований площадок ═══════════════════════

def _checks(t: MusicTrack) -> list:
    """Что мешает отдать релиз дистрибьютору. Три уровня: fail / warn / ok.

    Требования взяты из общих правил площадок и дистрибьюторов и написаны
    словами, а не ссылкой на «стандарты»: обложка 3000×3000, мастер в WAV,
    true peak не выше −1 dBTP, дата с запасом на питч, раскрытие ИИ. Где
    правило зависит от конкретного дистрибьютора — так и сказано."""
    out = []

    def add(key: str, level: str, ok_hint: str = "", value: str = ""):
        out.append({"key": key, "level": level, "hint": ok_hint, "value": value})

    add("title", "ok" if t.title.strip() else "fail", value=t.title)
    add("artist", "ok" if t.artist.strip() else "fail", value=t.artist)

    # Отсутствие мастера — предупреждение, а не запрет: человек мог принести
    # уже сведённый и отмастеренный трек со стороны, и запрещать ему выпуск
    # ради нашей же кнопки было бы нахальством.
    if not t.master_filename:
        add("master", "warn", value="none")
    else:
        add("master", "ok", value=os.path.splitext(t.master_filename)[1].lstrip(".").upper())
        # Единицы пишем рядом с числом: «−1» без «dBTP» — это не значение,
        # а загадка, и читать её человеку негде.
        tp = float(t.master_true_peak or 0)
        add("truepeak", "ok" if tp <= -0.9 else "warn", value=f"{tp:g} dBTP")
        lufs = float(t.master_lufs or 0)
        add("loudness", "ok" if -16.0 <= lufs <= -6.0 else "warn", value=f"{lufs:g} LUFS")

    if not t.cover_filename:
        add("cover", "fail")
    elif t.cover_w != t.cover_h:
        add("cover", "fail", value=f"{t.cover_w}×{t.cover_h}")
    elif t.cover_w < COVER_MIN:
        add("cover", "fail", value=f"{t.cover_w}×{t.cover_h}")
    elif t.cover_w < COVER_TARGET:
        add("cover", "warn", value=f"{t.cover_w}×{t.cover_h}")
    else:
        add("cover", "ok", value=f"{t.cover_w}×{t.cover_h}")

    if not t.release_date:
        add("date", "warn")
    else:
        try:
            d = date.fromisoformat(t.release_date)
            days = (d - date.today()).days
            # Показываем саму дату, а не «55»: число дней без подписи читается
            # как непонятно чей счётчик, а запас объясняет текст проверки.
            add("date", "ok" if days >= 14 else "warn", value=t.release_date)
        except ValueError:
            add("date", "warn", value=t.release_date)

    add("isrc", "ok" if t.isrc else "warn", value=t.isrc)
    add("genre", "ok" if t.genre else "warn", value=t.genre)
    add("disclosure", "ok" if t.ai_disclosure else
        ("fail" if t.origin == "generated" else "warn"), value=t.ai_disclosure)
    add("duration", "ok" if float(t.duration_sec or 0) >= 30 else "warn",
        value=f"{int(t.duration_sec // 60)}:{int(t.duration_sec % 60):02d}")
    return out


@router.get("/api/music/tracks/{track_id}/check")
def check_release(track_id: int, user: User = Depends(current_user),
                  db: Session = Depends(db_session)):
    t = _own(db, user, track_id)
    checks = _checks(t)
    return {
        "checks": checks,
        "fails": sum(1 for c in checks if c["level"] == "fail"),
        "warns": sum(1 for c in checks if c["level"] == "warn"),
        "cover_target": COVER_TARGET, "cover_min": COVER_MIN,
    }


# ═══════════════════════════ пакет релиза ═══════════════════════════

def _metadata_rows(t: MusicTrack) -> list:
    dur = int(t.duration_sec or 0)
    return [
        ("artist", t.artist), ("title", t.title), ("version", t.version),
        ("featuring", t.feat), ("genre", t.genre), ("language", t.language),
        ("release_date", t.release_date), ("isrc", t.isrc), ("upc", t.upc),
        ("explicit", "yes" if t.explicit else "no"),
        ("ai_disclosure", t.ai_disclosure or "none"),
        ("duration", f"{dur // 60}:{dur % 60:02d}"),
        ("bpm", str(t.bpm or "")),
        ("master_loudness_lufs", str(t.master_lufs or "")),
        ("master_true_peak_dbtp", str(t.master_true_peak or "")),
        ("master_engine", t.master_engine),
        ("credits", t.credits), ("notes", t.notes),
    ]


PACKAGE_README = """\
Release package built by lolq.ai / qlolmusic.

What is inside
  audio/     the master. WAV 24-bit 44.1 kHz unless you asked for mp3.
  cover/     the artwork exactly as you uploaded it — nothing was cropped.
  metadata.csv, metadata.json — the fields you filled in.

What this package is NOT
  It is not a delivery to Spotify, Apple Music, VK or Yandex Music. Nobody can
  deliver to those stores without a distributor contract — that is how the
  stores work, not a limitation of this tool. Hand this folder to your
  distributor, or send it to us and we will hand it over once our label
  contract is signed.

Before you send it anywhere
  * The cover must be square and at least 1400x1400 px; 3000x3000 is what every
    distributor asks for.
  * ISRC: leave it empty if you do not have one — the distributor issues it.
  * If any part of the track was generated, say so in ai_disclosure. Spotify and
    Deezer already require that disclosure and check it.
"""


@router.post("/api/music/tracks/{track_id}/package")
def build_package(track_id: int, user: User = Depends(current_user),
                  db: Session = Depends(db_session)):
    """Комплект файлов одним архивом: мастер + обложка + метаданные.

    Это то, что реально принимает любой дистрибьютор, и то, что человек
    может унести с собой куда угодно — включая другой сервис. Замок на
    собственный файл человека мы не вешаем."""
    t = _own(db, user, track_id)
    core = _core()
    if not t.master_filename and not t.source_filename:
        raise HTTPException(400, "нечего паковать")
    audio_name = t.master_filename or t.source_filename
    audio_path = os.path.join(UPLOAD_DIR, audio_name)
    if not os.path.exists(audio_path):
        raise _api_error(404, "no_audio", "The audio file is gone from the server.")

    # Имя файла внутри архива — «Артист - Название». Пустую половину не
    # приписываем: «- beat.wav» выглядит как ошибка экспорта, а не как релиз.
    stem = " - ".join(x for x in (t.artist.strip(), t.title.strip()) if x)
    safe = re.sub(r"[^\w\-. ]+", "", stem)[:80].strip(" -") or "release"
    fname = f"pack_{uuid.uuid4().hex}.zip"
    path = os.path.join(UPLOAD_DIR, fname)
    rows = _metadata_rows(t)

    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as z:
        z.write(audio_path, f"audio/{safe}{os.path.splitext(audio_name)[1]}")
        if not t.master_filename:
            # Пакет без мастера собрать можно, но человек обязан знать, что
            # внутри лежит исходник, а не мастер.
            z.writestr("audio/NOT_MASTERED.txt",
                       "This is the original upload, not a master: mastering was "
                       "never run for this track.\n")
        if t.cover_filename and os.path.exists(os.path.join(UPLOAD_DIR, t.cover_filename)):
            z.write(os.path.join(UPLOAD_DIR, t.cover_filename),
                    f"cover/{safe}{os.path.splitext(t.cover_filename)[1]}")
        buf = io.StringIO()
        w = csv.writer(buf)
        w.writerow(["field", "value"])
        for key, val in rows:
            w.writerow([key, val])
        z.writestr("metadata.csv", buf.getvalue())
        z.writestr("metadata.json", json.dumps(dict(rows), ensure_ascii=False, indent=2))
        z.writestr("README.txt", PACKAGE_README)

    old = t.package_filename
    t.package_filename = fname
    t.package_at = now()
    core._reg_file(db, fname, user.id, kind="other")
    db.commit()
    if old:
        core._remove_media(old)
    return {"ok": True, "url": _media(fname), "filename": fname,
            "size": os.path.getsize(path), "has_master": bool(t.master_filename)}


# ═══════════════════════ заявка в лейбл (дистрибуция) ═══════════════════════

def _distribution_state() -> dict:
    """Правда о дистрибуции одним объектом — её показывают рядом с кнопкой.

    `ready` включается переменной окружения и только после того, как договор
    подписан и доступы получены. Пока его нет, интерфейс говорит «заявка», а
    не «публикация», и никаких сроков не обещает."""
    return {
        "auto": False,                 # автоматической отгрузки нет и не бывает
        "ready": DISTRIBUTOR_READY,    # принимает ли лейбл релизы прямо сейчас
        "distributor": DISTRIBUTOR,
        "stores": ["Spotify", "Apple Music", "YouTube Music", "Deezer",
                   "VK Музыка", "Яндекс Музыка"],
        # Сроки — типовые для отрасли, а не наше обещание. Так и подписаны.
        "review_days": "1–5",
        "pitch_days": 14,
    }


@router.post("/api/music/tracks/{track_id}/submit")
async def submit_release(track_id: int, request: Request,
                         user: User = Depends(current_user),
                         db: Session = Depends(db_session)):
    """«Отправить на площадки» = собрать комплект + завести заявку в лейбл.

    Публикацией это НЕ является и называться так не будет. Прямой отгрузки
    в стриминги без договора с дистрибьютором не существует — см. шапку
    модуля и docs/qlolmusic.md."""
    t = _own(db, user, track_id)
    body = {}
    try:
        body = await request.json()
    except Exception:  # noqa: BLE001 — форма может прислать пустое тело
        body = {}
    if not isinstance(body, dict):
        body = {}
    contact = str(body.get("contact") or "").strip()[:200]
    comment = str(body.get("comment") or "").strip()[:2000]
    if not contact:
        contact = (user.email or user.tg_username or "").strip()
    if not contact:
        raise _api_error(400, "contact_required",
                         "Leave an email or a @telegram — otherwise there is no way "
                         "to answer you.")

    fails = [c for c in _checks(t) if c["level"] == "fail"]
    if fails:
        raise _api_error(400, "release_incomplete",
                         "The release is not ready yet.",
                         missing=[c["key"] for c in fails])

    lead = MusicLead(
        user_id=user.id, track_id=t.id,
        name=(t.artist or user.name or "")[:200],
        contact=contact, demo=t.title[:200], need="distribution",
        comment=comment, lang=str(body.get("lang") or "")[:8],
        source="studio", status="new",
        ip=_client_ip(request), user_agent=(request.headers.get("user-agent") or "")[:300],
    )
    db.add(lead)
    db.commit()
    db.refresh(lead)
    t.release_status = "submitted"
    t.submitted_at = now()
    t.lead_id = lead.id
    db.commit()
    log.info("музыка: заявка %s на релиз %s от пользователя %s", lead.id, t.id, user.id)
    return {"ok": True, "lead_id": lead.id, "distribution": _distribution_state()}


def _client_ip(request: Request) -> str:
    fwd = request.headers.get("x-forwarded-for") or ""
    if fwd:
        return fwd.split(",")[0].strip()[:60]
    return (request.client.host if request.client else "")[:60]


# ─────────────────── заявка со страницы лейбла (/music.html) ───────────────────
# Форма на витрине ждёт этот роут (docs/qlolmusic.md §2). Пока его не было,
# она честно отвечала «заявка НЕ ушла» — теперь уходит.

_LEAD_HITS: dict = {}
_LEAD_LOCK = threading.Lock()
LEAD_WINDOW_S = 3600
LEAD_MAX_PER_IP = int(os.environ.get("MUSIC_LEADS_PER_HOUR", "5"))
LEAD_NEEDS = ("distribution", "mastering", "clip", "all")


def _lead_rate_ok(ip: str) -> bool:
    """Пять заявок в час с адреса. Без этого форма без капчи — это спам-канал."""
    if not ip:
        return True
    cutoff = time.time() - LEAD_WINDOW_S
    with _LEAD_LOCK:
        hits = [x for x in _LEAD_HITS.get(ip, []) if x > cutoff]
        if len(hits) >= LEAD_MAX_PER_IP:
            _LEAD_HITS[ip] = hits
            return False
        hits.append(time.time())
        _LEAD_HITS[ip] = hits
        # Чистим словарь, чтобы он не рос вечно: адресов много, память одна.
        if len(_LEAD_HITS) > 5000:
            for k in [k for k, v in _LEAD_HITS.items() if not [x for x in v if x > cutoff]]:
                _LEAD_HITS.pop(k, None)
    return True


@router.post("/api/music/lead")
async def music_lead(request: Request):
    """Заявка со страницы лейбла. Без входа: это витрина, а не кабинет."""
    try:
        body = await request.json()
    except Exception:  # noqa: BLE001
        body = {}
    if not isinstance(body, dict):
        body = {}
    # Ловушка для ботов: поле, которого человек не видит. Заполнено — молча
    # отвечаем «ок», чтобы бот не подбирал форму дальше.
    if str(body.get("website") or body.get("company") or "").strip():
        return {"ok": True}
    name = str(body.get("name") or "").strip()[:200]
    contact = str(body.get("contact") or "").strip()[:200]
    if not name or not contact:
        raise _api_error(400, "lead_incomplete", "Name and contact are required.")
    ip = _client_ip(request)
    if not _lead_rate_ok(ip):
        raise _api_error(429, "too_many", "Too many applications from this address. "
                                          "Try again in a few minutes.")
    need = str(body.get("need") or "").strip().lower()
    db = SessionLocal()
    try:
        lead = MusicLead(
            name=name, contact=contact,
            demo=str(body.get("demo") or "").strip()[:500],
            need=need if need in LEAD_NEEDS else "all",
            comment=str(body.get("comment") or "").strip()[:2000],
            lang=str(body.get("lang") or "")[:8],
            source=str(body.get("source") or "music-page")[:40],
            ip=ip, user_agent=(request.headers.get("user-agent") or "")[:300],
        )
        db.add(lead)
        db.commit()
        log.info("музыка: заявка с витрины %s (%s)", lead.id, lead.need)
    finally:
        db.close()
    return {"ok": True}


@router.get("/api/music/leads")
def list_leads(limit: int = 50, user: User = Depends(current_user),
               db: Session = Depends(db_session)):
    """Заявки — владельцу сервиса. Без этого роута заявка уходит в никуда,
    а «мы получили» превращается в ложь через неделю после запуска."""
    if not user.is_admin:
        raise HTTPException(404, "не найдено")
    rows = (db.query(MusicLead).order_by(MusicLead.id.desc())
            .limit(max(1, min(200, int(limit or 50)))).all())
    return {"items": [{
        "id": r.id, "created_at": (r.created_at or now()).isoformat(),
        "name": r.name, "contact": r.contact, "demo": r.demo, "need": r.need,
        "comment": r.comment, "source": r.source, "track_id": r.track_id,
        "user_id": r.user_id, "status": r.status,
    } for r in rows]}


# ═════════════════════ видео для соцсетей и публикация ═════════════════════

VIDEO_SHAPES = {
    "vertical": (1080, 1920),   # Reels, Shorts, TikTok
    "square": (1080, 1080),
}


def _run_video(track_id: int, shape: str, seconds: int, wave: bool,
               cost: int, owner_id: int) -> None:
    """Обложка + дорожка → mp4. Ни одна соцсеть не принимает голое аудио.

    Волна поверх обложки — необязательная надстройка: фильтр showwaves есть
    не в каждой сборке ffmpeg, поэтому при его отказе собираем неподвижный
    кадр и пишем об этом на карточке, а не роняем задачу."""
    db = SessionLocal()
    core = _core()
    out_name = f"musvid_{uuid.uuid4().hex}.mp4"
    out_path = os.path.join(UPLOAD_DIR, out_name)
    try:
        t = db.get(MusicTrack, track_id)
        if not t:
            return
        t.video_status = "running"
        t.video_note = ""
        db.commit()
        w, h = VIDEO_SHAPES.get(shape, VIDEO_SHAPES["vertical"])
        src = os.path.join(UPLOAD_DIR, t.master_filename or t.source_filename)
        cover = os.path.join(UPLOAD_DIR, t.cover_filename) if t.cover_filename else ""
        dur = max(5, min(int(seconds or 60), int(t.duration_sec or 60)))

        base = [FFMPEG, "-y", "-v", "error"]
        if cover and os.path.exists(cover):
            base += ["-loop", "1", "-i", cover]
        else:
            # Обложки нет — ровный тёплый фон марки вместо чёрного прямоугольника.
            base += ["-f", "lavfi", "-i", f"color=c=0xfaf7f2:s={w}x{h}"]
        base += ["-i", src, "-t", str(dur)]

        fit = (f"scale={w}:{h}:force_original_aspect_ratio=increase,"
               f"crop={w}:{h},setsar=1")
        attempts = []
        if wave:
            attempts.append(base + [
                "-filter_complex",
                f"[0:v]{fit}[bg];"
                f"[1:a]showwaves=s={w}x{int(h * 0.14)}:mode=cline:colors=0xea6a34,"
                f"format=yuva420p,colorchannelmixer=aa=0.85[wv];"
                f"[bg][wv]overlay=0:{int(h * 0.78)}[v]",
                "-map", "[v]", "-map", "1:a"])
        attempts.append(base + ["-vf", fit, "-map", "0:v", "-map", "1:a"])

        tail = ["-c:v", "libx264", "-preset", "veryfast", "-crf", "20",
                "-pix_fmt", "yuv420p", "-r", "30",
                "-c:a", "aac", "-b:a", "192k", "-shortest", out_path]
        fallback_note = ""
        last_err = ""
        for i, cmd in enumerate(attempts):
            r = subprocess.run(cmd + tail, capture_output=True, text=True, timeout=1800)
            if r.returncode == 0 and os.path.exists(out_path):
                if i and wave:
                    fallback_note = ("The waveform overlay is not available in this "
                                     "ffmpeg build, so the cover is still.")
                break
            last_err = (r.stderr or "")[-300:]
        else:
            raise RuntimeError(last_err or "ffmpeg failed")

        t = db.get(MusicTrack, track_id)
        if not t:
            return
        old = t.video_filename
        t.video_filename = out_name
        t.video_status = "done"
        t.video_note = fallback_note
        t.updated_at = now()
        core._reg_file(db, out_name, owner_id, kind="clip")
        db.commit()
        owner = db.get(User, owner_id) if owner_id else None
        if owner and not core._take_points(db, owner, cost, f"видео для соцсетей {track_id}",
                                           kind="video", ref_type="music", ref_id=track_id):
            log.warning("музыка %s: видео готово, но очков на списание не хватило", track_id)
        if old:
            core._remove_media(old)
        log.info("музыка %s: видео %sx%s собрано (%s с)", track_id, w, h, dur)
    except Exception as e:  # noqa: BLE001
        db.rollback()
        t = db.get(MusicTrack, track_id)
        if t:
            t.video_status = "error"
            t.video_note = str(e)[:500]
            db.commit()
        try:
            os.remove(out_path)
        except OSError:
            pass
        log.warning("музыка %s: видео не собралось: %s", track_id, str(e)[:300])
    finally:
        db.close()


@router.post("/api/music/tracks/{track_id}/video")
def make_video(track_id: int, shape: str = Form("vertical"),
               seconds: int = Form(60), wave: bool = Form(True),
               user: User = Depends(current_user), db: Session = Depends(db_session)):
    """Собрать вертикальный ролик из обложки и дорожки — под соцсети."""
    t = _own(db, user, track_id)
    core = _core()
    if not (t.master_filename or t.source_filename):
        raise HTTPException(400, "нечего показывать")
    if t.video_status in ("queued", "running"):
        raise _api_error(409, "video_busy", "This video is already being built.")
    shape = shape if shape in VIDEO_SHAPES else "vertical"
    if not user.is_admin and int(user.gen_points or 0) < COST_VIDEO:
        raise core.NotEnoughPoints(COST_VIDEO, int(user.gen_points or 0),
                                   core._plan_of(user), "видео для соцсетей")
    t.video_status = "queued"
    t.video_note = ""
    db.commit()
    threading.Thread(target=_run_video,
                     args=(t.id, shape, int(seconds or 60), bool(wave),
                           COST_VIDEO, t.owner_id), daemon=True).start()
    return {"ok": True, "cost": COST_VIDEO, "shape": shape}


@router.get("/api/music/social")
async def social_status(user: User = Depends(current_user)):
    """Живность службы публикации. Отдельным роутом: проверка идёт секундами,
    и вешать её на общий /status значит тормозить открытие раздела."""
    try:
        import social  # noqa: PLC0415
        health = await social.publisher_health()
    except Exception as e:  # noqa: BLE001
        health = {"ok": False, "ready": False, "detail": str(e)[:300]}
    return {"platforms": list(PLATFORMS), **health}


def _run_publish(track_id: int, platform: str, caption: str, title: str) -> None:
    db = SessionLocal()
    try:
        import social  # noqa: PLC0415
        t = db.get(MusicTrack, track_id)
        if not t:
            return
        t.social_status = "running"
        t.social_note = ""
        db.commit()
        res = social.publish_clip_sync(t.video_filename, caption,
                                       platform=platform, title=title)
        t = db.get(MusicTrack, track_id)
        if not t:
            return
        t.social_status = "done"
        t.social_platform = platform
        t.social_url = res.get("external_url") or ""
        t.social_note = "" if res.get("external_url") else (
            "Published, but the agent could not read back the post link.")
        db.commit()
        log.info("музыка %s: опубликовано в %s (%s)", track_id, platform,
                 t.social_url or "без ссылки")
    except Exception as e:  # noqa: BLE001
        db.rollback()
        t = db.get(MusicTrack, track_id)
        if t:
            t.social_status = "error"
            t.social_note = str(e)[:500]
            db.commit()
        log.warning("музыка %s: публикация не удалась: %s", track_id, str(e)[:300])
    finally:
        db.close()


@router.post("/api/music/tracks/{track_id}/publish")
def publish(track_id: int, platform: str = Form("instagram"),
            caption: str = Form(""),
            user: User = Depends(current_user), db: Session = Depends(db_session)):
    """Публикация ролика в нашу соцсеть через хост-агент. Это работает
    по-настоящему — в отличие от стримингов, куда без дистрибьютора нельзя."""
    t = _own(db, user, track_id)
    if not t.video_filename:
        raise _api_error(400, "no_video",
                         "Build the video first — no social network accepts bare audio.")
    if t.social_status == "running":
        raise _api_error(409, "publish_busy", "This track is already being published.")
    platform = (platform or "instagram").lower()
    if platform not in PLATFORMS:
        raise _api_error(400, "bad_platform", "Unknown platform.")
    caption = (caption or "").strip() or f"{t.artist} — {t.title}".strip(" —")
    if not caption:
        raise _api_error(400, "caption_required", "Write a caption for the post.")
    t.social_status = "queued"
    t.social_note = ""
    db.commit()
    threading.Thread(target=_run_publish,
                     args=(t.id, platform, caption, (t.title or "qlolmusic")[:100]),
                     daemon=True).start()
    return {"ok": True, "platform": platform}


# ═════════════════════════ сводка раздела ═════════════════════════

@router.get("/api/music/status")
def music_status(user: User = Depends(current_user)):
    """Что в разделе живо и почём. Ключи наружу не отдаются — только флаги.

    Этим объектом интерфейс рисует ЧЕСТНЫЕ выключенные состояния: раздел
    генерации без ключа показан и выключен, движок по эталону без сайдкара
    помечен как офлайн, платный движок без ключа не показывается вовсе."""
    gen = audio.status()
    return {
        "generation": {
            "enabled": bool(gen.get("enabled")),
            "model": gen.get("music_model"),
            "max_sec": min(int(gen.get("music_max_sec") or GEN_MAX_S), GEN_MAX_S),
            "min_sec": GEN_MIN_S,
            "why_off": "" if gen.get("enabled") else "no_key",
        },
        "mastering": {
            "engines": mastering.engines(),
            "targets": [dict(x) for x in TARGETS],
            "output": "wav" if mastering.MASTER_OUTPUT != "mp3" else "mp3",
            "sample_rate": mastering.MASTER_SR,
            "references": len(_ref_library()),
        },
        "analysis": {"enabled": audio_analysis.available()},
        "upload": {
            "max_mb": MAX_UPLOAD_MB,
            "max_sec": MAX_DURATION_S,
            "formats": [x.lstrip(".") for x in AUDIO_EXT],
            "cover_formats": [x.lstrip(".") for x in COVER_EXT],
            "cover_target": COVER_TARGET, "cover_min": COVER_MIN,
        },
        "distribution": _distribution_state(),
        "social": {"platforms": list(PLATFORMS)},
        "cost": {
            "music_per_30_sec": COST_MUSIC_PER_30S,
            "master": COST_MASTER_LOCAL,
            "master_cloud": COST_MASTER_CLOUD,
            "video": COST_VIDEO,
            "analysis": 0, "package": 0, "submit": 0,
        },
        "points": int(user.gen_points or 0),
        "is_admin": bool(user.is_admin),
    }


# ─────────── публичная раздача исходника для облачного мастеринга ───────────

@router.get("/api/audio/public/{token}")
def audio_public(token: str, request: Request):
    """Исходник для внешнего сервиса мастеринга — БЕЗ куки приложения.

    RoEx Tonn принимает трек ТОЛЬКО по ссылке: файлом не загрузишь. Из
    /api/media он получил бы 401 (файлы приватные), поэтому здесь отдельный
    подписанный адрес с ограниченным сроком жизни. Своим движкам
    (matchering, ffmpeg) этот роут не нужен вообще — ещё один довод держать
    их движками по умолчанию."""
    fname = mastering.source_from_token(token)
    if not fname:
        raise HTTPException(404, "ссылка недействительна или устарела")
    path = os.path.join(UPLOAD_DIR, os.path.basename(fname))
    if not os.path.exists(path):
        raise HTTPException(404, "файл не найден")
    return _core()._media_response(path, request)


def mount(app) -> None:
    """Подключение из main.py одной строкой.

    Статику на «/» переставляем в конец: смонтированная раньше, она
    перехватывает /api/music/* и отвечает 405 вместо нашего роута — молча и
    совершенно непонятно (та же грабля, что у bot_api)."""
    app.include_router(router)
    try:
        from starlette.routing import Mount  # noqa: PLC0415
        routes = app.router.routes
        catchall = [r for r in routes if isinstance(r, Mount) and r.path in ("", "/")]
        for r in catchall:
            routes.remove(r)
            routes.append(r)
    except Exception as e:  # noqa: BLE001
        log.warning("музыка: не удалось проверить порядок роутов (%s). Подключай "
                    "music_api.mount(app) ДО app.mount(\"/\", StaticFiles…)", e)
