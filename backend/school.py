"""«Школа»: курсы, модули, уроки, доступ, авторы, кейсы, отзывы.

ПОЧЕМУ ОТДЕЛЬНЫЙ МОДУЛЬ, А НЕ ПРАВКА main.py. main.py — 14 тысяч строк и
живой файл: в нём одновременно работают другие руки. Школе от студии нужны
ровно четыре вещи — сессия (_resolve_user), касса (_charge), регистрация
файлов (_reg_file) и каталог тарифов (_plan_of). Своей авторизации и своей
кассы модуль не заводит, как chat/crm/music_api.

ЧЕМ ЭТО ОТЛИЧАЕТСЯ ОТ learn.py. Уроки-маркдауны остаются как были: они
источник органики, лежат файлами и версионируются коммитами. Курсы —
редактируемая владельцем витрина: модули, видео, авторы, кейсы, отзывы,
платный доступ. Требовать деплой ради переименования урока нельзя, поэтому
курсы живут в базе.

ДОСТУП — ОДНА ФУНКЦИЯ. `_can_read` отвечает на единственный вопрос «видит ли
этот человек ТЕЛО урока». Всё остальное (название, длительность, описание)
видно всегда: закрытый курс должен показывать, что именно человек не читает,
иначе это не витрина, а пустой экран.
"""
from __future__ import annotations

import logging
import os
import uuid
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, Form, HTTPException, Request, UploadFile
from sqlalchemy.orm import Session

from db import (
    Course, CourseAccess, CourseAuthor, CourseAuthorLink, CourseCase,
    CourseModule, CourseReview, Lesson, LessonProgress, SessionLocal, User, now,
)

log = logging.getLogger("rapclips")
router = APIRouter()

#: Порядок тарифов — тот же, что в learn.py; дублировать список нельзя,
#: но импортировать learn ради четырёх строк дороже, чем сослаться.
PLAN_ORDER = ("free", "pro", "pro_max", "studio")

IMAGE_EXT = (".jpg", ".jpeg", ".png", ".webp", ".gif")
VIDEO_EXT = (".mp4", ".webm", ".mov", ".m4v")


def _core():
    """main импортируется ЛЕНИВО: school подключается ИЗ main.py, и импорт
    на верхнем уровне дал бы цикл (тот же приём, что в crm и bot_api)."""
    import main  # noqa: PLC0415
    return main


def db_session():
    session = SessionLocal()
    try:
        yield session
    finally:
        session.close()


def maybe_user(request: Request, db: Session = Depends(db_session)) -> User | None:
    """Витрина курсов ПУБЛИЧНАЯ: список и оглавление читаются без входа —
    требовать аккаунт ради «посмотреть, чему учат» значит терять человека."""
    return _core()._resolve_user(request, db)


def auth_user(request: Request, db: Session = Depends(db_session)) -> User:
    user = _core()._resolve_user(request, db)
    if not user:
        raise HTTPException(401, "не авторизован")
    return user


def admin_user(request: Request, db: Session = Depends(db_session)) -> User:
    user = auth_user(request, db)
    if not user.is_admin:
        raise HTTPException(403, "только для админа")
    return user


# ─────────────────────────────── доступ ───────────────────────────────

def _plan_ok(user: User | None, min_plan: str) -> bool:
    if not min_plan or min_plan not in PLAN_ORDER:
        return True
    if not user:
        return False
    plan = _core()._plan_of(user)
    try:
        return PLAN_ORDER.index(plan) >= PLAN_ORDER.index(min_plan)
    except ValueError:
        return False


def _can_read(db: Session, user: User | None, course: Course) -> bool:
    """Видит ли человек ТЕЛО уроков курса."""
    if user and user.is_admin:
        return True
    if course.access == "admin_only":
        return False
    if course.access != "paid":
        return True
    if not user:
        return False
    if _plan_ok(user, course.min_plan) and course.min_plan:
        return True
    return bool(db.query(CourseAccess).filter(
        CourseAccess.user_id == user.id,
        CourseAccess.course_id == course.id).first())


def _lock_reason(user: User | None, course: Course) -> str:
    """Честная причина замка: «купи за N» или «нужен тариф». Пусто = открыто."""
    if course.access == "admin_only":
        return "admin_only"
    if course.min_plan:
        return "plan"
    if course.price_points:
        return "price"
    return "login" if not user else "price"


def _visible(db: Session, user: User | None, course: Course) -> bool:
    if user and user.is_admin:
        return True
    if course.status != "live":
        return False
    return course.access != "admin_only"


# ─────────────────────────────── сериализация ───────────────────────────────

def _media_url(fname: str) -> str:
    return f"/api/media/{fname}" if fname else ""


def _iso(dt: datetime | None) -> str:
    if not dt:
        return ""
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.isoformat()


def _lesson_ids(db: Session, course_id: int) -> list[int]:
    mods = db.query(CourseModule).filter(
        CourseModule.course_id == course_id).all()
    if not mods:
        return []
    rows = db.query(Lesson.id).filter(
        Lesson.module_id.in_([m.id for m in mods])).all()
    return [r[0] for r in rows]


def _done_set(db: Session, user: User | None, course_id: int) -> set[int]:
    if not user:
        return set()
    rows = db.query(LessonProgress.lesson_id).filter(
        LessonProgress.user_id == user.id,
        LessonProgress.course_id == course_id).all()
    return {r[0] for r in rows}


def _authors_of(db: Session, course_id: int) -> list[dict]:
    links = db.query(CourseAuthorLink).filter(
        CourseAuthorLink.course_id == course_id).order_by(
        CourseAuthorLink.sort_order, CourseAuthorLink.id).all()
    out = []
    for link in links:
        a = db.get(CourseAuthor, link.author_id)
        if not a:
            continue
        out.append({"id": a.id, "name": a.name, "role": a.role,
                    "bio": a.bio, "avatar_url": _media_url(a.avatar_filename)})
    return out


def _rating_of(db: Session, course_id: int) -> tuple[float, int]:
    rows = db.query(CourseReview.rating).filter(
        CourseReview.course_id == course_id,
        CourseReview.published.is_(True)).all()
    if not rows:
        return 0.0, 0
    vals = [int(r[0] or 0) for r in rows]
    return round(sum(vals) / len(vals), 1), len(vals)


def _course_card(db: Session, user: User | None, c: Course) -> dict:
    ids = _lesson_ids(db, c.id)
    done = _done_set(db, user, c.id) & set(ids)
    total = len(ids)
    unlocked = _can_read(db, user, c)
    rating, reviews = _rating_of(db, c.id)
    return {
        "id": c.id,
        "title": c.title,
        "subtitle": c.subtitle,
        "cover_url": _media_url(c.cover_filename),
        "status": c.status,
        "access": c.access,
        "price_points": int(c.price_points or 0),
        "min_plan": c.min_plan,
        "sort_order": int(c.sort_order or 0),
        "lessons": total,
        "done": len(done),
        "percent": int(round(len(done) * 100 / total)) if total else 0,
        "unlocked": unlocked,
        "lock": "" if unlocked else _lock_reason(user, c),
        "authors": _authors_of(db, c.id),
        "rating": rating,
        "reviews": reviews,
    }


def _lesson_row(l: Lesson, *, done: bool, unlocked: bool) -> dict:  # noqa: E741
    return {
        "id": l.id,
        "module_id": l.module_id,
        "title": l.title,
        "summary": l.summary,
        "minutes": int(l.minutes or 0),
        "status": l.status,
        "published_at": _iso(l.published_at),
        "sort_order": int(l.sort_order or 0),
        "cover_url": _media_url(l.cover_filename),
        "has_video": bool(l.video_url or l.video_filename),
        "done": done,
        "locked": not unlocked,
    }


def _video_url(l: Lesson) -> str:  # noqa: E741
    return _media_url(l.video_filename) if l.video_filename else (l.video_url or "")


# ─────────────────────────────── публичные роуты ───────────────────────────────

@router.get("/api/courses")
def list_courses(request: Request, db: Session = Depends(db_session)):
    user = maybe_user(request, db)
    rows = db.query(Course).order_by(Course.sort_order, Course.id).all()
    out = [_course_card(db, user, c) for c in rows if _visible(db, user, c)]
    return {"courses": out, "is_admin": bool(user and user.is_admin)}


@router.get("/api/courses/{course_id}")
def get_course(course_id: int, request: Request,
               db: Session = Depends(db_session)):
    user = maybe_user(request, db)
    c = db.get(Course, course_id)
    if not c or not _visible(db, user, c):
        raise HTTPException(404, "курс не найден")
    is_adm = bool(user and user.is_admin)
    unlocked = _can_read(db, user, c)
    done = _done_set(db, user, c.id)
    card = _course_card(db, user, c)
    mods = []
    for m in db.query(CourseModule).filter(
            CourseModule.course_id == c.id).order_by(
            CourseModule.sort_order, CourseModule.id).all():
        lessons = db.query(Lesson).filter(Lesson.module_id == m.id).order_by(
            Lesson.sort_order, Lesson.id).all()
        # Черновики уроков видит только админ: ученику показывать пустую
        # строку «скоро» бессмысленно, а как готовый урок — обман.
        lessons = [l for l in lessons if is_adm or l.status == "published"]  # noqa: E741
        mods.append({
            "id": m.id, "title": m.title, "sort_order": int(m.sort_order or 0),
            "lessons": [_lesson_row(l, done=l.id in done, unlocked=unlocked)
                        for l in lessons],
        })
    cases = [{
        "id": x.id, "title": x.title, "description": x.description,
        "media_url": _media_url(x.media_filename), "video_url": x.video_url,
    } for x in db.query(CourseCase).filter(
        CourseCase.course_id == c.id).order_by(
        CourseCase.sort_order, CourseCase.id).all()]
    rev_q = db.query(CourseReview).filter(CourseReview.course_id == c.id)
    if not is_adm:
        rev_q = rev_q.filter(CourseReview.published.is_(True))
    reviews = [{
        "id": r.id, "author_name": r.author_name or "…",
        "rating": int(r.rating or 5), "text": r.text,
        "published": bool(r.published), "created_at": _iso(r.created_at),
        "mine": bool(user and r.user_id == user.id),
    } for r in rev_q.order_by(CourseReview.id.desc()).all()]
    card.update({"modules": mods, "cases": cases, "reviews": reviews,
                 "is_admin": is_adm,
                 "can_review": bool(user and unlocked)})
    return card


@router.get("/api/lessons/{lesson_id}")
def get_lesson(lesson_id: int, request: Request,
               db: Session = Depends(db_session)):
    user = maybe_user(request, db)
    l = db.get(Lesson, lesson_id)  # noqa: E741
    if not l:
        raise HTTPException(404, "урок не найден")
    m = db.get(CourseModule, l.module_id)
    c = db.get(Course, m.course_id) if m else None
    if not c or not _visible(db, user, c):
        raise HTTPException(404, "урок не найден")
    is_adm = bool(user and user.is_admin)
    if l.status != "published" and not is_adm:
        raise HTTPException(404, "урок не найден")
    unlocked = _can_read(db, user, c)
    done = _done_set(db, user, c.id)
    out = _lesson_row(l, done=l.id in done, unlocked=unlocked)
    out["body_md"] = l.body_md if unlocked else ""
    out["video_src"] = _video_url(l) if unlocked else ""
    out["course"] = {"id": c.id, "title": c.title,
                     "price_points": int(c.price_points or 0),
                     "min_plan": c.min_plan, "access": c.access,
                     "lock": "" if unlocked else _lock_reason(user, c)}
    out["module_title"] = m.title if m else ""
    # Соседние уроки — сквозной порядок по всему курсу, а не внутри модуля:
    # «следующий» после последнего урока модуля — первый урок следующего.
    flat = []
    for mm in db.query(CourseModule).filter(
            CourseModule.course_id == c.id).order_by(
            CourseModule.sort_order, CourseModule.id).all():
        for ll in db.query(Lesson).filter(Lesson.module_id == mm.id).order_by(
                Lesson.sort_order, Lesson.id).all():
            if is_adm or ll.status == "published":
                flat.append(ll)
    idx = next((i for i, x in enumerate(flat) if x.id == l.id), -1)
    out["number"] = ""
    if idx >= 0:
        prev = flat[idx - 1] if idx > 0 else None
        nxt = flat[idx + 1] if idx + 1 < len(flat) else None
        out["prev"] = {"id": prev.id, "title": prev.title} if prev else None
        out["next"] = {"id": nxt.id, "title": nxt.title} if nxt else None
    else:
        out["prev"] = out["next"] = None
    return out


@router.post("/api/lessons/{lesson_id}/done")
def lesson_done(lesson_id: int, request: Request,
                db: Session = Depends(db_session)):
    user = auth_user(request, db)
    l = db.get(Lesson, lesson_id)  # noqa: E741
    if not l:
        raise HTTPException(404, "урок не найден")
    m = db.get(CourseModule, l.module_id)
    c = db.get(Course, m.course_id) if m else None
    if not c:
        raise HTTPException(404, "урок не найден")
    row = db.query(LessonProgress).filter(
        LessonProgress.user_id == user.id,
        LessonProgress.lesson_id == l.id).first()
    if not row:
        db.add(LessonProgress(user_id=user.id, lesson_id=l.id,
                              course_id=c.id, done_at=now()))
        db.commit()
    ids = _lesson_ids(db, c.id)
    done = _done_set(db, user, c.id) & set(ids)
    return {"ok": True, "done": len(done), "total": len(ids),
            "percent": int(round(len(done) * 100 / len(ids))) if ids else 0}


@router.post("/api/lessons/{lesson_id}/undone")
def lesson_undone(lesson_id: int, request: Request,
                  db: Session = Depends(db_session)):
    user = auth_user(request, db)
    db.query(LessonProgress).filter(
        LessonProgress.user_id == user.id,
        LessonProgress.lesson_id == lesson_id).delete()
    db.commit()
    return {"ok": True}


@router.post("/api/courses/{course_id}/buy")
def buy_course(course_id: int, request: Request,
               db: Session = Depends(db_session)):
    """Покупка курса за токены. ИДЕМПОТЕНТНО: повторный клик по «Открыть»
    (двойное нажатие, ретрай сети) не должен списать вторую цену."""
    user = auth_user(request, db)
    c = db.get(Course, course_id)
    if not c or not _visible(db, user, c):
        raise HTTPException(404, "курс не найден")
    if _can_read(db, user, c):
        return {"ok": True, "already": True}
    if c.access != "paid" or not c.price_points:
        raise HTTPException(400, "курс не продаётся за токены")
    if c.min_plan:
        raise HTTPException(403, f"курс открывается тарифом {c.min_plan}")
    core = _core()
    core._charge(db, user, int(c.price_points), f"курс «{c.title}»")
    db.add(CourseAccess(user_id=user.id, course_id=c.id,
                        granted_at=now(), source="purchase"))
    db.commit()
    return {"ok": True, "already": False}


@router.post("/api/courses/{course_id}/review")
def add_review(course_id: int, request: Request,
               rating: int = Form(5), text: str = Form(""),
               db: Session = Depends(db_session)):
    """Отзыв ученика. На витрину попадает после модерации: published ставит
    админ. Иначе первая же реклама курса собрала бы спам в галерее."""
    user = auth_user(request, db)
    c = db.get(Course, course_id)
    if not c:
        raise HTTPException(404, "курс не найден")
    if not _can_read(db, user, c):
        raise HTTPException(403, "отзыв оставляют те, у кого есть доступ")
    row = db.query(CourseReview).filter(
        CourseReview.course_id == c.id,
        CourseReview.user_id == user.id).first()
    if not row:
        row = CourseReview(course_id=c.id, user_id=user.id, created_at=now())
        db.add(row)
    row.author_name = (user.name or user.login or "ученик")[:80]
    row.rating = max(1, min(5, int(rating or 5)))
    row.text = (text or "").strip()[:2000]
    row.published = False
    db.commit()
    return {"ok": True, "moderation": True}


# ─────────────────────────────── файлы ───────────────────────────────

async def _save_media(db: Session, user: User, upload: UploadFile,
                      *, kind: str, exts: tuple[str, ...]) -> str:
    ext = os.path.splitext(upload.filename or "")[1].lower()
    if ext not in exts:
        raise HTTPException(400, "неподдерживаемый формат: " + ", ".join(exts))
    fname = f"{kind}_{uuid.uuid4().hex}{ext}"
    core = _core()
    with open(os.path.join(core.UPLOAD_DIR, fname), "wb") as f:
        f.write(await upload.read())
    core._reg_file(db, fname, user.id, kind=kind)
    db.commit()
    return fname


def _drop_media(fname: str, db: Session) -> None:
    if not fname:
        return
    try:
        _core()._remove_media(fname, db)
    except Exception:  # noqa: BLE001
        log.warning("школа: не удалось убрать файл %s", fname)


# ─────────────────────────────── админские роуты ───────────────────────────────

def _next_order(db: Session, model, **flt) -> int:
    q = db.query(model)
    for k, v in flt.items():
        q = q.filter(getattr(model, k) == v)
    rows = q.all()
    return (max([int(r.sort_order or 0) for r in rows]) + 1) if rows else 0


@router.get("/api/admin/courses")
def admin_courses(user: User = Depends(admin_user),
                  db: Session = Depends(db_session)):
    rows = db.query(Course).order_by(Course.sort_order, Course.id).all()
    return {"courses": [_course_card(db, user, c) for c in rows]}


@router.post("/api/admin/courses")
def create_course(title: str = Form(""), subtitle: str = Form(""),
                  user: User = Depends(admin_user),
                  db: Session = Depends(db_session)):
    c = Course(title=(title or "Новый курс").strip()[:200],
               subtitle=(subtitle or "").strip()[:400],
               status="draft", access="free", created_at=now(),
               sort_order=_next_order(db, Course))
    db.add(c)
    db.commit()
    return {"ok": True, "id": c.id}


@router.post("/api/admin/courses/{course_id}")
def update_course(course_id: int, title: str = Form(None),
                  subtitle: str = Form(None), status: str = Form(None),
                  access: str = Form(None), price_points: int = Form(None),
                  min_plan: str = Form(None), sort_order: int = Form(None),
                  user: User = Depends(admin_user),
                  db: Session = Depends(db_session)):
    c = db.get(Course, course_id)
    if not c:
        raise HTTPException(404, "курс не найден")
    if title is not None:
        c.title = title.strip()[:200]
    if subtitle is not None:
        c.subtitle = subtitle.strip()[:400]
    if status in ("draft", "live", "archived"):
        c.status = status
    if access in ("free", "paid", "admin_only"):
        c.access = access
    if price_points is not None:
        c.price_points = max(0, int(price_points))
    if min_plan is not None:
        c.min_plan = min_plan if min_plan in PLAN_ORDER else ""
    if sort_order is not None:
        c.sort_order = int(sort_order)
    db.commit()
    return {"ok": True}


@router.post("/api/admin/courses/{course_id}/cover")
async def course_cover(course_id: int, cover: UploadFile,
                       user: User = Depends(admin_user),
                       db: Session = Depends(db_session)):
    c = db.get(Course, course_id)
    if not c:
        raise HTTPException(404, "курс не найден")
    old = c.cover_filename
    c.cover_filename = await _save_media(db, user, cover,
                                         kind="cover", exts=IMAGE_EXT)
    db.commit()
    _drop_media(old, db)
    return {"ok": True, "cover_url": _media_url(c.cover_filename)}


@router.post("/api/admin/courses/{course_id}/delete")
def delete_course(course_id: int, user: User = Depends(admin_user),
                  db: Session = Depends(db_session)):
    c = db.get(Course, course_id)
    if not c:
        raise HTTPException(404, "курс не найден")
    ids = _lesson_ids(db, c.id)
    if ids:
        db.query(LessonProgress).filter(
            LessonProgress.lesson_id.in_(ids)).delete(synchronize_session=False)
    for model in (CourseAccess, CourseCase, CourseReview, CourseAuthorLink):
        db.query(model).filter(model.course_id == c.id).delete(
            synchronize_session=False)
    db.delete(c)
    db.commit()
    return {"ok": True}


@router.post("/api/admin/courses/{course_id}/modules")
def create_module(course_id: int, title: str = Form(""),
                  user: User = Depends(admin_user),
                  db: Session = Depends(db_session)):
    if not db.get(Course, course_id):
        raise HTTPException(404, "курс не найден")
    m = CourseModule(course_id=course_id,
                     title=(title or "Новый модуль").strip()[:200],
                     created_at=now(),
                     sort_order=_next_order(db, CourseModule, course_id=course_id))
    db.add(m)
    db.commit()
    return {"ok": True, "id": m.id}


@router.post("/api/admin/modules/{module_id}")
def update_module(module_id: int, title: str = Form(None),
                  sort_order: int = Form(None),
                  user: User = Depends(admin_user),
                  db: Session = Depends(db_session)):
    m = db.get(CourseModule, module_id)
    if not m:
        raise HTTPException(404, "модуль не найден")
    if title is not None:
        m.title = title.strip()[:200]
    if sort_order is not None:
        m.sort_order = int(sort_order)
    db.commit()
    return {"ok": True}


@router.post("/api/admin/modules/{module_id}/delete")
def delete_module(module_id: int, user: User = Depends(admin_user),
                  db: Session = Depends(db_session)):
    m = db.get(CourseModule, module_id)
    if not m:
        raise HTTPException(404, "модуль не найден")
    ids = [r[0] for r in db.query(Lesson.id).filter(
        Lesson.module_id == m.id).all()]
    if ids:
        db.query(LessonProgress).filter(
            LessonProgress.lesson_id.in_(ids)).delete(synchronize_session=False)
    db.delete(m)
    db.commit()
    return {"ok": True}


@router.post("/api/admin/modules/{module_id}/lessons")
def create_lesson(module_id: int, title: str = Form(""),
                  user: User = Depends(admin_user),
                  db: Session = Depends(db_session)):
    if not db.get(CourseModule, module_id):
        raise HTTPException(404, "модуль не найден")
    l = Lesson(module_id=module_id,  # noqa: E741
               title=(title or "Новый урок").strip()[:200],
               status="draft", created_at=now(),
               sort_order=_next_order(db, Lesson, module_id=module_id))
    db.add(l)
    db.commit()
    return {"ok": True, "id": l.id}


@router.post("/api/admin/lessons/{lesson_id}")
def update_lesson(lesson_id: int, title: str = Form(None),
                  summary: str = Form(None), body_md: str = Form(None),
                  video_url: str = Form(None), minutes: int = Form(None),
                  status: str = Form(None), sort_order: int = Form(None),
                  module_id: int = Form(None),
                  user: User = Depends(admin_user),
                  db: Session = Depends(db_session)):
    l = db.get(Lesson, lesson_id)  # noqa: E741
    if not l:
        raise HTTPException(404, "урок не найден")
    if title is not None:
        l.title = title.strip()[:200]
    if summary is not None:
        l.summary = summary.strip()[:600]
    if body_md is not None:
        l.body_md = body_md
    if video_url is not None:
        l.video_url = video_url.strip()[:600]
    if minutes is not None:
        l.minutes = max(0, int(minutes))
    if module_id is not None and db.get(CourseModule, int(module_id)):
        l.module_id = int(module_id)
    if sort_order is not None:
        l.sort_order = int(sort_order)
    if status in ("draft", "published"):
        # Дата публикации ставится ОДИН раз: она стоит в оглавлении, и
        # перепубликация правки не должна делать старый урок «новым».
        if status == "published" and not l.published_at:
            l.published_at = now()
        l.status = status
    db.commit()
    return {"ok": True, "published_at": _iso(l.published_at)}


@router.post("/api/admin/lessons/{lesson_id}/video")
async def lesson_video(lesson_id: int, video: UploadFile,
                       user: User = Depends(admin_user),
                       db: Session = Depends(db_session)):
    l = db.get(Lesson, lesson_id)  # noqa: E741
    if not l:
        raise HTTPException(404, "урок не найден")
    old = l.video_filename
    l.video_filename = await _save_media(db, user, video,
                                         kind="lesson", exts=VIDEO_EXT)
    db.commit()
    _drop_media(old, db)
    return {"ok": True, "video_src": _media_url(l.video_filename)}


@router.post("/api/admin/lessons/{lesson_id}/cover")
async def lesson_cover(lesson_id: int, cover: UploadFile,
                       user: User = Depends(admin_user),
                       db: Session = Depends(db_session)):
    l = db.get(Lesson, lesson_id)  # noqa: E741
    if not l:
        raise HTTPException(404, "урок не найден")
    old = l.cover_filename
    l.cover_filename = await _save_media(db, user, cover,
                                         kind="cover", exts=IMAGE_EXT)
    db.commit()
    _drop_media(old, db)
    return {"ok": True, "cover_url": _media_url(l.cover_filename)}


@router.post("/api/admin/lessons/{lesson_id}/delete")
def delete_lesson(lesson_id: int, user: User = Depends(admin_user),
                  db: Session = Depends(db_session)):
    l = db.get(Lesson, lesson_id)  # noqa: E741
    if not l:
        raise HTTPException(404, "урок не найден")
    db.query(LessonProgress).filter(
        LessonProgress.lesson_id == l.id).delete(synchronize_session=False)
    db.delete(l)
    db.commit()
    return {"ok": True}


@router.post("/api/admin/reorder")
def reorder(kind: str = Form(...), ids: str = Form(""),
            user: User = Depends(admin_user),
            db: Session = Depends(db_session)):
    """Порядок задаётся СПИСКОМ id в новом порядке, а не пересчётом соседей:
    перетаскивание в браузере знает итоговый порядок целиком."""
    model = {"course": Course, "module": CourseModule,
             "lesson": Lesson, "case": CourseCase}.get(kind)
    if not model:
        raise HTTPException(400, "неизвестный тип")
    for pos, raw in enumerate([x for x in ids.split(",") if x.strip()]):
        row = db.get(model, int(raw))
        if row:
            row.sort_order = pos
    db.commit()
    return {"ok": True}


@router.get("/api/admin/courses/{course_id}/students")
def course_students(course_id: int, user: User = Depends(admin_user),
                    db: Session = Depends(db_session)):
    c = db.get(Course, course_id)
    if not c:
        raise HTTPException(404, "курс не найден")
    ids = set(_lesson_ids(db, c.id))
    rows = db.query(LessonProgress).filter(
        LessonProgress.course_id == c.id).all()
    by_user: dict[int, list[LessonProgress]] = {}
    for r in rows:
        if r.lesson_id in ids:
            by_user.setdefault(int(r.user_id), []).append(r)
    for acc in db.query(CourseAccess).filter(
            CourseAccess.course_id == c.id).all():
        by_user.setdefault(int(acc.user_id), [])
    out = []
    access_by_user = {int(a.user_id): a for a in db.query(CourseAccess).filter(
        CourseAccess.course_id == c.id).all()}
    for uid, progress in by_user.items():
        u = db.get(User, uid)
        if not u:
            continue
        last = max([p.done_at for p in progress if p.done_at], default=None)
        acc = access_by_user.get(uid)
        out.append({
            "user_id": uid,
            "name": u.name or u.login or u.email or f"#{uid}",
            "login": u.login, "email": u.email,
            "done": len(progress), "total": len(ids),
            "percent": int(round(len(progress) * 100 / len(ids))) if ids else 0,
            "last_at": _iso(last),
            "access": acc.source if acc else ("free" if c.access != "paid" else ""),
        })
    out.sort(key=lambda x: (-x["done"], x["name"]))
    return {"students": out, "total_lessons": len(ids)}


def _find_user(db: Session, ident: str) -> User | None:
    ident = (ident or "").strip()
    if not ident:
        return None
    if ident.isdigit():
        u = db.get(User, int(ident))
        if u:
            return u
    return (db.query(User).filter(User.login == ident).first()
            or db.query(User).filter(User.email == ident).first())


@router.post("/api/admin/courses/{course_id}/grant")
def grant_access(course_id: int, who: str = Form(""),
                 user: User = Depends(admin_user),
                 db: Session = Depends(db_session)):
    c = db.get(Course, course_id)
    if not c:
        raise HTTPException(404, "курс не найден")
    target = _find_user(db, who)
    if not target:
        raise HTTPException(404, "ученик не найден")
    row = db.query(CourseAccess).filter(
        CourseAccess.course_id == c.id,
        CourseAccess.user_id == target.id).first()
    if not row:
        db.add(CourseAccess(course_id=c.id, user_id=target.id,
                            granted_at=now(), source="admin"))
        db.commit()
    return {"ok": True, "user_id": target.id,
            "name": target.name or target.login or target.email}


@router.post("/api/admin/courses/{course_id}/revoke")
def revoke_access(course_id: int, who: str = Form(""),
                  user: User = Depends(admin_user),
                  db: Session = Depends(db_session)):
    target = _find_user(db, who)
    if not target:
        raise HTTPException(404, "ученик не найден")
    db.query(CourseAccess).filter(
        CourseAccess.course_id == course_id,
        CourseAccess.user_id == target.id).delete(synchronize_session=False)
    db.commit()
    return {"ok": True}


# ─────────────────────────── авторы ───────────────────────────

@router.get("/api/admin/authors")
def list_authors(user: User = Depends(admin_user),
                 db: Session = Depends(db_session)):
    rows = db.query(CourseAuthor).order_by(CourseAuthor.id).all()
    return {"authors": [{"id": a.id, "name": a.name, "role": a.role,
                         "bio": a.bio,
                         "avatar_url": _media_url(a.avatar_filename)}
                        for a in rows]}


@router.post("/api/admin/authors")
def create_author(name: str = Form(""), role: str = Form(""),
                  bio: str = Form(""), course_id: int = Form(0),
                  user: User = Depends(admin_user),
                  db: Session = Depends(db_session)):
    a = CourseAuthor(name=(name or "Автор").strip()[:120],
                     role=(role or "").strip()[:120],
                     bio=(bio or "").strip()[:2000], created_at=now())
    db.add(a)
    db.commit()
    if course_id and db.get(Course, int(course_id)):
        db.add(CourseAuthorLink(course_id=int(course_id), author_id=a.id,
                                sort_order=_next_order(db, CourseAuthorLink,
                                                       course_id=int(course_id))))
        db.commit()
    return {"ok": True, "id": a.id}


@router.post("/api/admin/authors/{author_id}")
def update_author(author_id: int, name: str = Form(None), role: str = Form(None),
                  bio: str = Form(None), user: User = Depends(admin_user),
                  db: Session = Depends(db_session)):
    a = db.get(CourseAuthor, author_id)
    if not a:
        raise HTTPException(404, "автор не найден")
    if name is not None:
        a.name = name.strip()[:120]
    if role is not None:
        a.role = role.strip()[:120]
    if bio is not None:
        a.bio = bio.strip()[:2000]
    db.commit()
    return {"ok": True}


@router.post("/api/admin/authors/{author_id}/avatar")
async def author_avatar(author_id: int, avatar: UploadFile,
                        user: User = Depends(admin_user),
                        db: Session = Depends(db_session)):
    a = db.get(CourseAuthor, author_id)
    if not a:
        raise HTTPException(404, "автор не найден")
    old = a.avatar_filename
    a.avatar_filename = await _save_media(db, user, avatar,
                                          kind="cover", exts=IMAGE_EXT)
    db.commit()
    _drop_media(old, db)
    return {"ok": True, "avatar_url": _media_url(a.avatar_filename)}


@router.post("/api/admin/authors/{author_id}/delete")
def delete_author(author_id: int, user: User = Depends(admin_user),
                  db: Session = Depends(db_session)):
    a = db.get(CourseAuthor, author_id)
    if not a:
        raise HTTPException(404, "автор не найден")
    db.query(CourseAuthorLink).filter(
        CourseAuthorLink.author_id == a.id).delete(synchronize_session=False)
    db.delete(a)
    db.commit()
    return {"ok": True}


@router.post("/api/admin/courses/{course_id}/authors")
def link_author(course_id: int, author_id: int = Form(...),
                unlink: int = Form(0), user: User = Depends(admin_user),
                db: Session = Depends(db_session)):
    if unlink:
        db.query(CourseAuthorLink).filter(
            CourseAuthorLink.course_id == course_id,
            CourseAuthorLink.author_id == int(author_id)).delete(
            synchronize_session=False)
    elif not db.query(CourseAuthorLink).filter(
            CourseAuthorLink.course_id == course_id,
            CourseAuthorLink.author_id == int(author_id)).first():
        db.add(CourseAuthorLink(course_id=course_id, author_id=int(author_id),
                                sort_order=_next_order(db, CourseAuthorLink,
                                                       course_id=course_id)))
    db.commit()
    return {"ok": True}


# ─────────────────────────── кейсы и отзывы ───────────────────────────

@router.post("/api/admin/courses/{course_id}/cases")
def create_case(course_id: int, title: str = Form(""),
                description: str = Form(""), video_url: str = Form(""),
                user: User = Depends(admin_user),
                db: Session = Depends(db_session)):
    if not db.get(Course, course_id):
        raise HTTPException(404, "курс не найден")
    x = CourseCase(course_id=course_id, title=(title or "Кейс").strip()[:200],
                   description=(description or "").strip()[:2000],
                   video_url=(video_url or "").strip()[:600], created_at=now(),
                   sort_order=_next_order(db, CourseCase, course_id=course_id))
    db.add(x)
    db.commit()
    return {"ok": True, "id": x.id}


@router.post("/api/admin/cases/{case_id}")
def update_case(case_id: int, title: str = Form(None),
                description: str = Form(None), video_url: str = Form(None),
                user: User = Depends(admin_user),
                db: Session = Depends(db_session)):
    x = db.get(CourseCase, case_id)
    if not x:
        raise HTTPException(404, "кейс не найден")
    if title is not None:
        x.title = title.strip()[:200]
    if description is not None:
        x.description = description.strip()[:2000]
    if video_url is not None:
        x.video_url = video_url.strip()[:600]
    db.commit()
    return {"ok": True}


@router.post("/api/admin/cases/{case_id}/media")
async def case_media(case_id: int, media: UploadFile,
                     user: User = Depends(admin_user),
                     db: Session = Depends(db_session)):
    x = db.get(CourseCase, case_id)
    if not x:
        raise HTTPException(404, "кейс не найден")
    old = x.media_filename
    x.media_filename = await _save_media(db, user, media, kind="cover",
                                         exts=IMAGE_EXT + VIDEO_EXT)
    db.commit()
    _drop_media(old, db)
    return {"ok": True, "media_url": _media_url(x.media_filename)}


@router.post("/api/admin/cases/{case_id}/delete")
def delete_case(case_id: int, user: User = Depends(admin_user),
                db: Session = Depends(db_session)):
    x = db.get(CourseCase, case_id)
    if not x:
        raise HTTPException(404, "кейс не найден")
    db.delete(x)
    db.commit()
    return {"ok": True}


@router.post("/api/admin/reviews/{review_id}")
def moderate_review(review_id: int, published: int = Form(None),
                    text: str = Form(None), author_name: str = Form(None),
                    rating: int = Form(None),
                    user: User = Depends(admin_user),
                    db: Session = Depends(db_session)):
    r = db.get(CourseReview, review_id)
    if not r:
        raise HTTPException(404, "отзыв не найден")
    if published is not None:
        r.published = bool(int(published))
    if text is not None:
        r.text = text.strip()[:2000]
    if author_name is not None:
        r.author_name = author_name.strip()[:80]
    if rating is not None:
        r.rating = max(1, min(5, int(rating)))
    db.commit()
    return {"ok": True}


@router.post("/api/admin/reviews/{review_id}/delete")
def delete_review(review_id: int, user: User = Depends(admin_user),
                  db: Session = Depends(db_session)):
    r = db.get(CourseReview, review_id)
    if not r:
        raise HTTPException(404, "отзыв не найден")
    db.delete(r)
    db.commit()
    return {"ok": True}


# ─────────────────────────── сид первых курсов ───────────────────────────

#: Программа, которая до сих пор жила заглушками в модалке «Школа». Сид
#: ставится ОДИН раз и опознаётся по seed_key: повторный старт контейнера не
#: создаёт вторые «Первый клип», а правки владельца не затираются.
SEED = [
    ("level0", "Первый клип", "От трека до готового вертикального ролика", [
        ("Старт", [("Проект и трек", 6), ("Разбор текста и ритма", 5),
                   ("Персонаж и его лицо", 7)]),
        ("Сборка", [("Раскадровка по строчкам", 8), ("Кадры и видео", 9),
                    ("Сборка и выгрузка", 6)]),
    ]),
    ("level1", "Ремесло", "Стиль, свет и персонажи, которые не плывут", [
        ("Стиль", [("Один стиль и смесь трёх", 7), ("Свет и палитра", 6)]),
        ("Персонажи", [("Референсы лица", 8), ("Сцена с двумя героями", 7)]),
    ]),
    ("level2", "Движки и финал", "Какой движок за что отвечает и чем добить", [
        ("Движки", [("Чем отличаются движки", 6), ("Цена кадра и цена ошибки", 5)]),
        ("Финал", [("Апскейл и зерно", 6), ("Вертикаль 9:16 и публикация", 7)]),
    ]),
    ("level3", "Продакшн-режим", "Поток роликов вместо разовых экспериментов", [
        ("Поток", [("Шаблоны и пресеты", 7), ("Пакетная генерация", 8)]),
        ("Дистрибуция", [("Обложки и тексты", 6), ("Календарь выкладки", 6)]),
    ]),
]


def seed_courses() -> None:
    db = SessionLocal()
    try:
        for order, (key, title, subtitle, modules) in enumerate(SEED):
            if db.query(Course).filter(Course.seed_key == key).first():
                continue
            c = Course(seed_key=key, title=title, subtitle=subtitle,
                       status="live", access="free", sort_order=order,
                       created_at=now())
            db.add(c)
            db.commit()
            for m_i, (m_title, lessons) in enumerate(modules):
                m = CourseModule(course_id=c.id, title=m_title,
                                 sort_order=m_i, created_at=now())
                db.add(m)
                db.commit()
                for l_i, (l_title, minutes) in enumerate(lessons):
                    db.add(Lesson(module_id=m.id, title=l_title,
                                  minutes=minutes, sort_order=l_i,
                                  status="published", published_at=now(),
                                  created_at=now(),
                                  body_md=f"# {l_title}\n\nТекст урока скоро "
                                          f"появится — раздел уже открыт."))
                db.commit()
            log.info("школа: засеян курс %s", title)
    except Exception:  # noqa: BLE001
        # Сид — удобство, а не условие старта: упавший сид не должен
        # уронить весь сервис на импорте.
        log.exception("школа: сид курсов не прошёл")
    finally:
        db.close()


def mount(app) -> None:
    app.include_router(router)
    seed_courses()
