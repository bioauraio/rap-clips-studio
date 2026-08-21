"""Чат lolq.ai — ОДНО окно, в котором переключается модель.

Главная идея, из-за которой это не «ещё четыре вкладки»: намерение задаёт
ВЫБРАННАЯ МОДЕЛЬ, а не режим экрана. Выбран текстовый движок — Enter уходит в
подписочный шлюз; выбран движок картинок — тот же текст уезжает промптом
вместе с вложениями; выбран движок видео — оживляется картинка (вложенная,
явно указанная кнопкой «Оживить» или последняя в этой же ленте).

Три вещи, которые здесь принципиальны и легко потерять при правках:

1. ТАРИФ. Чат не открывает НИ ОДНОГО движка, закрытого тарифом: фильтры те же,
   что у студии (_plan_engine_ids / image_engine тарифа). Иначе FREE со 120
   очками запускал бы Seedance 2.5 (154 очка за прогон) и Nano Banana Pro
   ($0.09 живых денег за картинку) — экономика сервиса ломается мгновенно.
   Закрытые движки ВИДНЫ (серые, с ценой и ярлыком тарифа) — это витрина.

2. ВОЗВРАТ ОЧКОВ. Списываем в момент постановки задачи, как везде в проекте,
   но здесь запросы одиночные: молча съеденные за упавшую генерацию 154 очка
   человек увидит сразу. Упал тред — _refund возвращает ровно списанное.

3. РЕТЕНЦИЯ МЕДИА. Студия хранит файлы осмысленно (кадр нужен клипу), чат
   плодит картинки пачками. У каждого медиа-сообщения есть срок хранения по
   тарифу; ночной сборщик сносит файл, но НЕ сообщение — остаётся промпт, по
   которому картинку можно повторить, и кнопка «Сохранить в проект», которая
   снимает срок и переносит файл персонажу.

Модуль намеренно не импортирует main.py (тот импортирует его) — всё, что
нужно из студии, приезжает через configure() в _ctx.
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import shutil
import time
import uuid
from datetime import timedelta
from threading import Thread
from types import SimpleNamespace

import httpx
from fastapi import APIRouter, Depends, HTTPException, Request, UploadFile
from sqlalchemy.orm import Session

import mediagen
from db import (
    Chat, ChatFile, ChatMessage, Character, CharacterPhoto, FileOwner,
    SessionLocal, User, now,
)

log = logging.getLogger("rapclips.chat")

router = APIRouter()

# Шлюз подписок владельца: OpenAI-совместимая ручка уже есть на хосте
# (infra/agents/agent_gateway.py), историю диалога она принимает списком
# messages. Стриминга у неё нет и не будет — ответ приходит целиком.
GATEWAY_BASE = os.environ.get("AGENT_GATEWAY_URL", "http://172.18.0.1:8765").rstrip("/")
CHAT_GATEWAY_URL = GATEWAY_BASE + "/v1/chat/completions"
CHAT_TIMEOUT = httpx.Timeout(280.0, connect=15.0)

# Сколько последних сообщений уезжает контекстом. Шлюз схлопывает историю в
# один prompt, поэтому длинная лента там превращается в километровый запрос —
# двадцати реплик хватает на связный разговор и не разносит расход.
HISTORY_LIMIT = int(os.environ.get("CHAT_HISTORY_LIMIT", "20"))

# Картинки чата — 2K, БЕЗ апскейла до 4К. Вертикальный 4K PNG весит мегабайты,
# в ленте он не нужен, а хранить его месяцами — прямые деньги за диск.
CHAT_IMAGE_RESOLUTION = os.environ.get("CHAT_IMAGE_RESOLUTION", "2K")
CHAT_VIDEO_DURATIONS = (4, 6, 8, 10)
CHAT_MAX_FILES = 8
CHAT_MAX_FILE_MB = int(os.environ.get("CHAT_MAX_FILE_MB", "12"))

# Срок хранения медиа по тарифу (дни). Текст не удаляется никогда.
RETENTION_DAYS = {"free": 7, "pro": 30, "pro_max": 90, "studio": 90}
# Висячее вложение (залито кнопкой «+», сообщение так и не отправлено).
ORPHAN_FILE_HOURS = 24

# Всё, что нужно от студии. Заполняется configure() из main.py — так модуль не
# импортирует main (тот импортирует его) и не тащит за собой круговую ссылку.
_ctx = SimpleNamespace()


def configure(**kwargs) -> None:
    for key, value in kwargs.items():
        setattr(_ctx, key, value)


# ─────────────────────────── сессия и владение ───────────────────────────

def db_session():
    session = SessionLocal()
    try:
        yield session
    finally:
        session.close()


def current_user(request: Request, db: Session = Depends(db_session)) -> User:
    """Тот же вход, что у студии: разрешение сессии живёт в main.py и
    приезжает сюда через configure() — здесь только оболочка-зависимость."""
    user = _ctx.resolve_user(request, db)
    if not user:
        raise HTTPException(401, "не авторизован")
    return user


def _own_chat(db: Session, user: User, chat_id: int) -> Chat:
    chat = db.get(Chat, chat_id)
    if not chat or (chat.owner_id != user.id and not user.is_admin):
        raise HTTPException(404, "чат не найден")
    return chat


def _own_message(db: Session, user: User, message_id: int) -> ChatMessage:
    msg = db.get(ChatMessage, message_id)
    if not msg:
        raise HTTPException(404, "сообщение не найдено")
    _own_chat(db, user, msg.chat_id)
    return msg


def _plan_id(user: User) -> str:
    return _ctx.plan_of(user)


def _retention_days(user: User) -> int:
    if user.is_admin:
        return 0  # админ = владелец сервиса: его файлы не подметаем
    return RETENTION_DAYS.get(_plan_id(user), RETENTION_DAYS["free"])


# ─────────────────────────── реестр моделей ───────────────────────────
# Один список вместо трёх вкладок. Собирается из тех же реестров, что и
# студийный /api/providers, поэтому цена в очках не может разойтись с
# себестоимостью, а тариф — с тем, что реально открыто.

TEXT_MODEL_ID = "auto"


def _text_points() -> int:
    """Текст стоит нам ноль (подписка владельца), но даром отдавать его
    нельзя: без ценника чат становится бесконечным насосом по чужому
    аккаунту. Та же символическая двойка, что у шлюзовых кадров."""
    return int(_ctx.gateway_points)


def _allowed_image_engines(user: User) -> list[str]:
    """Движки картинок, открытые ЭТОМУ человеку.

    Шлюзы владельца открыты всем — они стоят нам ноль. Платные Nano Banana
    открываются только тарифом, у которого движок кадров тоже платный: иначе
    FREE рисовал бы на $0.09 за картинку при 120 очках на весь месяц."""
    out = [e for e in mediagen.GATEWAY_IMAGE_ENGINES if e in mediagen.image_engines_live()]
    plan_engine = (_ctx.plans[_plan_id(user)].get("image_engine") or "chatgpt")
    paid_plan = bool(mediagen.IMAGE_ENGINES.get(plan_engine, {}).get("paid"))
    if (paid_plan or user.is_admin) and mediagen.kie_available():
        out += [k for k, v in mediagen.IMAGE_ENGINES.items()
                if v["channel"] == "kie" and k not in out]
    return out


def _allowed_video_engines(user: User) -> list[str]:
    return _ctx.plan_engine_ids(_plan_id(user))


def _image_points(user: User, engine: str) -> int:
    return int(_ctx.image_cost(user, engine, CHAT_IMAGE_RESOLUTION))


def _video_points(engine: str, duration: int) -> int:
    """Цена ролика БЕЗ доплаты за кадры: первый кадр в чате уже есть,
    добирать нечего."""
    usd = mediagen.video_engine_usd(engine, int(duration or 6))
    return int(_ctx.points_of_usd(usd))


def _norm_duration(value) -> int:
    try:
        want = int(value or 6)
    except (TypeError, ValueError):
        want = 6
    return min(CHAT_VIDEO_DURATIONS, key=lambda d: abs(d - want))


def _model_id(kind: str, engine: str) -> str:
    """id позиции селектора = «вид:движок».

    Префикс обязателен, а не украшение: grok и chatgpt живут В ОБОИХ реестрах
    сразу — и в IMAGE_ENGINES, и в VIDEO_ENGINES. Без префикса «grok» из поля
    ввода означал бы то картинку, то ролик по порядку проверок, и человек,
    выбравший видео, молча получал бы картинку за другие деньги."""
    return f"{kind}:{engine}"


def _split_model(model_id: str) -> tuple[str, str]:
    """«вид:движок» → (вид, движок). Голое имя без префикса тоже принимаем —
    так старые сохранённые чаты не ломаются."""
    raw = str(model_id or "").strip()
    if ":" in raw:
        kind, _, engine = raw.partition(":")
        kind = kind.strip()
        engine = engine.strip()
        if kind in ("text", "image", "video") and engine:
            return kind, engine
        raw = engine or raw
    if raw == TEXT_MODEL_ID or not raw:
        return "text", TEXT_MODEL_ID
    if raw in mediagen.VIDEO_ENGINES and raw not in mediagen.IMAGE_ENGINES:
        return "video", raw
    if raw in mediagen.IMAGE_ENGINES:
        return "image", raw
    if raw in mediagen.VIDEO_ENGINES:
        return "video", raw
    return "", raw


def _engine_title(engine: str, kind: str = "") -> str:
    if engine == TEXT_MODEL_ID:
        return "Auto (subscription)"
    if kind == "video":
        spec = mediagen.VIDEO_ENGINES.get(engine) or {}
    elif kind == "image":
        spec = mediagen.IMAGE_ENGINES.get(engine) or {}
    else:
        spec = mediagen.IMAGE_ENGINES.get(engine) or mediagen.VIDEO_ENGINES.get(engine) or {}
    return spec.get("title") or engine


def _models_payload(user: User) -> list[dict]:
    """Плоский список позиций селектора: текст, картинки, видео подряд.

    Закрытые тарифом позиции НЕ выкидываются — они приезжают с allowed=false
    и ценой. Молчаливый запрет выглядит как поломка; видимый замок с ценником
    работает витриной.
    """
    items: list[dict] = [{
        "id": _model_id("text", TEXT_MODEL_ID), "kind": "text",
        "title": _engine_title(TEXT_MODEL_ID),
        "live": True, "points": _text_points(), "allowed": True,
        "needs_image": False, "first_last": False, "max_refs": 0,
        "note": "", "plan": "",
    }]

    img_allowed = _allowed_image_engines(user)
    img_live = mediagen.image_engines_live()
    for eid, spec in mediagen.IMAGE_ENGINES.items():
        items.append({
            "id": _model_id("image", eid), "kind": "image", "title": spec["title"],
            "live": eid in img_live,
            "points": _image_points(user, eid),
            "allowed": eid in img_allowed,
            "needs_image": False, "first_last": False,
            "max_refs": int(spec.get("max_refs") or 1),
            "note": "", "plan": "" if eid in img_allowed else "pro_max",
        })

    vid_allowed = _allowed_video_engines(user)
    for eid, spec in mediagen.VIDEO_ENGINES.items():
        items.append({
            "id": _model_id("video", eid), "kind": "video", "title": spec["title"],
            "live": mediagen.video_engine_live(eid),
            "points": _video_points(eid, 6),
            "allowed": eid in vid_allowed,
            # Видео в чате всегда стартует с картинки: чистого text-to-video у
            # нас нет ни на одном движке, и обещать его в селекторе нельзя.
            "needs_image": True,
            "first_last": bool(spec.get("first_last")),
            "max_refs": 1,
            "note": spec.get("note", ""),
            "plan": "" if eid in vid_allowed else _cheapest_plan_with(eid),
        })
    return items


def _cheapest_plan_with(engine: str) -> str:
    """Самый дешёвый тариф, где этот движок открыт — чтобы замок в селекторе
    называл конкретный тариф, а не абстрактное «нужен платный»."""
    for pid in ("pro", "pro_max", "studio"):
        if engine in _ctx.plan_engine_ids(pid):
            return pid
    return "studio"


def _check_allowed(user: User, model_id: str) -> tuple[str, str]:
    """«вид:движок» → (вид, движок), с проверкой тарифа.

    Чужой тарифу движок здесь именно ПАДАЕТ, а не понижается молча: в студии
    тихое понижение спасает кнопку, а в чате человек выбрал модель руками и
    обязан узнать, что она закрыта, — иначе он платит за одно, получая другое."""
    kind, engine = _split_model(model_id)
    if not kind or (kind == "image" and engine not in mediagen.IMAGE_ENGINES) \
            or (kind == "video" and engine not in mediagen.VIDEO_ENGINES):
        raise HTTPException(400, "неизвестная модель")
    if user.is_admin:
        return kind, engine
    if kind == "image" and engine not in _allowed_image_engines(user):
        raise HTTPException(403, "эта модель картинок закрыта твоим тарифом")
    if kind == "video" and engine not in _allowed_video_engines(user):
        raise HTTPException(403, "эта модель видео закрыта твоим тарифом")
    if kind == "video" and not mediagen.video_engine_live(engine):
        raise HTTPException(400, "движок видео сейчас недоступен")
    return kind, engine


@router.get("/api/chat/models")
def chat_models(user: User = Depends(current_user)):
    plan_id = _plan_id(user)
    return {
        "models": _models_payload(user),
        "default_text": _model_id("text", TEXT_MODEL_ID),
        "default_image": _model_id("image", _ctx.plan_image_engine(user)),
        "default_video": _model_id("video", (_allowed_video_engines(user) or ["grok"])[0]),
        "durations": list(CHAT_VIDEO_DURATIONS),
        "plan": plan_id,
        "plan_title": _ctx.plans[plan_id]["title"],
        "points": None if user.is_admin else int(user.gen_points or 0),
        "is_admin": bool(user.is_admin),
        "retention_days": _retention_days(user),
        "max_files": CHAT_MAX_FILES,
    }


# ─────────────────────────── сериализация ───────────────────────────

def _msg_dict(m: ChatMessage) -> dict:
    media = m.media_filename or ""
    expired = bool(not media and m.kind in ("image", "video") and m.status == "done")
    try:
        params = json.loads(m.params_json or "{}")
    except ValueError:
        params = {}
    return {
        "id": m.id, "role": m.role, "kind": m.kind, "text": m.text,
        "engine": _model_id(m.kind, m.engine) if m.engine else "",
        "engine_title": _engine_title(m.engine, m.kind) if m.engine else "",
        "points": int(m.points or 0),
        "status": m.status or "done", "error": m.error or "",
        "url": f"/api/media/{media}" if media else "",
        # Видео отдаём целиком (плееру нужен Range), картинке хватает миниатюры.
        "thumb_url": f"/api/thumb/{media}" if media and m.kind == "image" else "",
        "expired": expired,
        "expires_at": m.expires_at.isoformat() if m.expires_at else "",
        "created_at": m.created_at.isoformat() if m.created_at else "",
        "params": params,
        "files": [{"id": f.id, "url": f"/api/media/{f.filename}",
                   "thumb_url": f"/api/thumb/{f.filename}"} for f in m.files],
    }


def _chat_dict(c: Chat, spent: int = 0) -> dict:
    return {
        "id": c.id, "title": c.title or "", "model": c.model or "",
        "spent": int(spent or 0),
        "created_at": c.created_at.isoformat() if c.created_at else "",
        "updated_at": c.updated_at.isoformat() if c.updated_at else "",
    }


def _auto_title(text: str) -> str:
    line = " ".join(str(text or "").split())
    return line[:40] if line else ""


# ─────────────────────────── список чатов ───────────────────────────

@router.get("/api/chats")
def list_chats(user: User = Depends(current_user), db: Session = Depends(db_session)):
    rows = (db.query(Chat)
            .filter(Chat.owner_id == user.id, Chat.archived.is_(False))
            .order_by(Chat.updated_at.desc(), Chat.id.desc())
            .limit(200).all())
    out = []
    for c in rows:
        spent = sum(int(m.points or 0) for m in c.messages)
        out.append(_chat_dict(c, spent))
    return out


@router.post("/api/chats")
async def create_chat(request: Request, user: User = Depends(current_user),
                      db: Session = Depends(db_session)):
    body = await _body(request)
    chat = Chat(owner_id=user.id, title=str(body.get("title") or "")[:120],
                model=str(body.get("model") or ""))
    db.add(chat)
    db.commit()
    db.refresh(chat)
    return _chat_dict(chat)


@router.patch("/api/chats/{chat_id}")
async def update_chat(chat_id: int, request: Request,
                      user: User = Depends(current_user),
                      db: Session = Depends(db_session)):
    chat = _own_chat(db, user, chat_id)
    body = await _body(request)
    if "title" in body:
        chat.title = str(body.get("title") or "")[:120]
    if "model" in body:
        chat.model = str(body.get("model") or "")[:60]
    db.commit()
    return _chat_dict(chat)


@router.delete("/api/chats/{chat_id}")
def delete_chat(chat_id: int, user: User = Depends(current_user),
                db: Session = Depends(db_session)):
    chat = _own_chat(db, user, chat_id)
    # Файлы каскад БД не удалит — чистим их сами, как в студии.
    for m in chat.messages:
        _drop_media(db, m.media_filename)
        for f in m.files:
            _drop_media(db, f.filename)
    db.delete(chat)
    db.commit()
    return {"ok": True}


@router.get("/api/chats/{chat_id}/messages")
def chat_messages(chat_id: int, before: int = 0, limit: int = 50,
                  user: User = Depends(current_user),
                  db: Session = Depends(db_session)):
    """Лента страницами снизу вверх: before=<id> подгружает то, что раньше."""
    chat = _own_chat(db, user, chat_id)
    q = db.query(ChatMessage).filter(ChatMessage.chat_id == chat.id)
    if before:
        q = q.filter(ChatMessage.id < int(before))
    rows = q.order_by(ChatMessage.id.desc()).limit(max(1, min(200, int(limit or 50)))).all()
    rows.reverse()
    spent = sum(int(m.points or 0) for m in chat.messages)
    # Есть ли что-то РАНЬШЕ показанного куска. Считаем по границе страницы, а
    # не по общему числу сообщений: при подгрузке вверх (before=…) общее число
    # ничего не говорит о том, осталось ли что-то ещё выше.
    older = 0
    if rows:
        older = (db.query(ChatMessage)
                 .filter(ChatMessage.chat_id == chat.id, ChatMessage.id < rows[0].id)
                 .count())
    return {
        "chat": _chat_dict(chat, spent),
        "messages": [_msg_dict(m) for m in rows],
        "has_more": bool(older),
        "points": None if user.is_admin else int(user.gen_points or 0),
    }


# ─────────────────────────── вложения ───────────────────────────

@router.post("/api/chat/upload")
async def chat_upload(file: UploadFile, user: User = Depends(current_user),
                      db: Session = Depends(db_session)):
    """Файл кнопкой «+». Живёт без сообщения, пока его не отправят —
    невостребованные подметает сборщик."""
    ext = os.path.splitext(file.filename or "")[1].lower() or ".png"
    if ext not in (".jpg", ".jpeg", ".png", ".webp"):
        raise HTTPException(400, "поддерживаются jpg/png/webp")
    data = await file.read()
    if len(data) > CHAT_MAX_FILE_MB * 1024 * 1024:
        raise HTTPException(400, f"файл больше {CHAT_MAX_FILE_MB} МБ")
    fname = f"chat_{uuid.uuid4().hex}{ext}"
    with open(os.path.join(_ctx.upload_dir, fname), "wb") as f:
        f.write(data)
    _ctx.reg_file(db, fname, user.id)
    row = ChatFile(owner_id=user.id, filename=fname, position=0)
    db.add(row)
    db.commit()
    db.refresh(row)
    return {"id": row.id, "url": f"/api/media/{fname}",
            "thumb_url": f"/api/thumb/{fname}"}


def _take_files(db: Session, user: User, file_ids: list) -> list[ChatFile]:
    out = []
    for raw in (file_ids or [])[:CHAT_MAX_FILES]:
        try:
            row = db.get(ChatFile, int(raw))
        except (TypeError, ValueError):
            continue
        if row and row.owner_id == user.id and row.message_id is None:
            out.append(row)
    return out


def _file_paths(rows: list) -> list[str]:
    out = []
    for r in rows:
        path = os.path.join(_ctx.upload_dir, r.filename)
        if os.path.exists(path):
            out.append(path)
    return out


# ─────────────────────────── отправка сообщения ───────────────────────────

async def _body(request: Request) -> dict:
    raw = await request.body()
    if not raw:
        return {}
    try:
        data = json.loads(raw)
    except ValueError:
        return {}
    return data if isinstance(data, dict) else {}


def _last_image_message(db: Session, chat: Chat) -> ChatMessage | None:
    return (db.query(ChatMessage)
            .filter(ChatMessage.chat_id == chat.id,
                    ChatMessage.kind == "image",
                    ChatMessage.media_filename != "")
            .order_by(ChatMessage.id.desc()).first())


def _source_frame(db: Session, chat: Chat, files: list, from_id) -> str:
    """Картинка-источник для видео. Порядок: явно указанное сообщение
    (кнопка «Оживить») → вложение → последняя картинка ЭТОЙ ленты.

    Именно последний пункт и делает «всё в одном окне»: написал → получил
    картинку → выбрал видео-движок → получил ролик, не выходя из поля ввода."""
    if from_id:
        src = db.get(ChatMessage, int(from_id))
        if src and src.chat_id == chat.id and src.media_filename:
            path = os.path.join(_ctx.upload_dir, src.media_filename)
            if os.path.exists(path):
                return path
    paths = _file_paths(files)
    if paths:
        return paths[0]
    last = _last_image_message(db, chat)
    if last:
        path = os.path.join(_ctx.upload_dir, last.media_filename)
        if os.path.exists(path):
            return path
    raise HTTPException(400, "для видео нужна картинка: приложи файл или сгенерируй кадр в этом чате")


def _post_message(db: Session, user: User, chat: Chat, body: dict) -> dict:
    """Общий путь отправки: и обычная реплика, и «Оживить» приходят сюда."""
    text = str(body.get("text") or "").strip()
    model_id = str(body.get("engine") or chat.model or "").strip()
    kind, engine = _check_allowed(user, model_id)
    files = _take_files(db, user, body.get("file_ids") or [])
    duration = _norm_duration(body.get("duration"))

    if not text and kind != "video":
        raise HTTPException(400, "напиши, что нужно сделать")

    params: dict = {}
    source_path = ""
    if kind == "video":
        source_path = _source_frame(db, chat, files, body.get("from_message_id"))
        params["duration"] = duration
        params["source"] = os.path.basename(source_path)
        cost = _video_points(engine, duration)
    elif kind == "image":
        params["resolution"] = CHAT_IMAGE_RESOLUTION
        cost = _image_points(user, engine)
    else:
        cost = _text_points()

    # Списываем ДО создания строк: отказ по деньгам не должен оставлять в
    # ленте сообщение-призрак, на которое человек потом смотрит и не понимает.
    _ctx.charge(db, user, cost, f"чат {chat.id}: {engine}")

    pos = (db.query(ChatMessage).filter(ChatMessage.chat_id == chat.id).count()) + 1
    ask = ChatMessage(chat_id=chat.id, position=pos, role="user", kind="text",
                      text=text, status="done")
    db.add(ask)
    db.flush()
    for i, f in enumerate(files, start=1):
        f.message_id = ask.id
        f.position = i

    days = _retention_days(user)
    answer = ChatMessage(
        chat_id=chat.id, position=pos + 1, role="assistant", kind=kind,
        text="", engine=engine, points=cost, status="queued",
        params_json=json.dumps(params, ensure_ascii=False),
        expires_at=(now() + timedelta(days=days)) if (days and kind != "text") else None,
    )
    db.add(answer)
    if not chat.title:
        chat.title = _auto_title(text) or _engine_title(engine, kind)
    # В чате запоминается ИМЕННО позиция селектора (с префиксом вида), а не
    # голое имя движка: вернувшись, человек должен попасть в тот же режим.
    chat.model = _model_id(kind, engine)
    chat.updated_at = now()
    db.commit()
    db.refresh(ask)
    db.refresh(answer)

    if kind == "text":
        Thread(target=_run_text, args=(answer.id,), daemon=True).start()
    elif kind == "image":
        Thread(target=_run_image, args=(answer.id, _file_paths(files)), daemon=True).start()
    else:
        Thread(target=_run_video, args=(answer.id, source_path, duration), daemon=True).start()

    return {"chat": _chat_dict(chat), "ask": _msg_dict(ask), "answer": _msg_dict(answer),
            "points": None if user.is_admin else int(user.gen_points or 0)}


@router.post("/api/chats/{chat_id}/messages")
async def send_message(chat_id: int, request: Request,
                       user: User = Depends(current_user),
                       db: Session = Depends(db_session)):
    chat = _own_chat(db, user, chat_id)
    return _post_message(db, user, chat, await _body(request))


@router.post("/api/chats/messages/{message_id}/animate")
async def animate_message(message_id: int, request: Request,
                          user: User = Depends(current_user),
                          db: Session = Depends(db_session)):
    """Кнопка «Оживить» на картинке в ленте.

    Отдельного механизма у неё нет — это обычная отправка, где источником
    кадра явно назначено ЭТО сообщение. Ради этого приёма чат и живёт внутри
    сервиса: путь «промпт → картинка → ролик» проходится не выходя из ленты."""
    msg = _own_message(db, user, message_id)
    if not msg.media_filename or msg.kind != "image":
        raise HTTPException(400, "оживить можно только картинку")
    chat = db.get(Chat, msg.chat_id)
    body = await _body(request)
    body["from_message_id"] = msg.id
    if not str(body.get("engine") or "").strip():
        body["engine"] = _model_id("video", (_allowed_video_engines(user) or ["grok"])[0])
    if not str(body.get("text") or "").strip():
        # Промпт движения по умолчанию — из промпта самой картинки: пустой
        # motion-prompt Seedance и Kling понимают как «делай что хочешь».
        prev = (db.query(ChatMessage)
                .filter(ChatMessage.chat_id == chat.id, ChatMessage.id < msg.id,
                        ChatMessage.role == "user")
                .order_by(ChatMessage.id.desc()).first())
        body["text"] = (prev.text if prev else "") or "slow cinematic camera move, subtle motion"
    return _post_message(db, user, chat, body)


@router.post("/api/chats/{chat_id}/messages/{message_id}/retry")
def retry_message(chat_id: int, message_id: int,
                  user: User = Depends(current_user),
                  db: Session = Depends(db_session)):
    """Повтор упавшего (или протухшего по сроку) ответа тем же движком.

    Деньги за упавшую генерацию уже вернулись — значит повтор платный заново,
    и это честно: считает его тот же _charge, что и первую попытку."""
    chat = _own_chat(db, user, chat_id)
    msg = db.get(ChatMessage, message_id)
    if not msg or msg.chat_id != chat.id or msg.role != "assistant":
        raise HTTPException(404, "сообщение не найдено")
    if msg.status in ("queued", "running"):
        raise HTTPException(400, "эта генерация ещё идёт")
    ask = (db.query(ChatMessage)
           .filter(ChatMessage.chat_id == chat.id, ChatMessage.id < msg.id,
                   ChatMessage.role == "user")
           .order_by(ChatMessage.id.desc()).first())
    try:
        params = json.loads(msg.params_json or "{}")
    except ValueError:
        params = {}
    body = {
        "text": ask.text if ask else msg.text,
        "engine": _model_id(msg.kind, msg.engine) if msg.engine else chat.model,
        "duration": params.get("duration"),
    }
    if msg.kind == "video":
        prev_img = _last_image_message(db, chat)
        if prev_img:
            body["from_message_id"] = prev_img.id
    return _post_message(db, user, chat, body)


# ─────────────────────────── фоновые исполнители ───────────────────────────
# Тот же паттерн, что у сцен студии: status="queued" + demon-тред + asyncio.run
# внутри. Фронт тянет статусы обычным поллингом — SSE ради этого не заводим.

def _fail(message_id: int, err: str) -> None:
    """Пометить ответ ошибкой И ВЕРНУТЬ ОЧКИ.

    Возврата в проекте до чата не было нигде: у сцены упавшая генерация
    оставляла charged_points на месте. Там это тонет в потоке, здесь — нет."""
    db = SessionLocal()
    try:
        msg = db.get(ChatMessage, message_id)
        if not msg:
            return
        msg.status = "error"
        msg.error = str(err)[:500]
        points = int(msg.points or 0)
        chat = db.get(Chat, msg.chat_id)
        owner = db.get(User, chat.owner_id) if chat else None
        if owner and points > 0:
            _ctx.refund(db, owner, points, f"чат {msg.chat_id}: {msg.engine}")
            msg.points = 0
        db.commit()
    except Exception as e:  # noqa: BLE001
        log.warning("не удалось пометить ошибку сообщения %s: %s", message_id, str(e)[:200])
    finally:
        db.close()


def _mark_running(db: Session, msg: ChatMessage) -> None:
    msg.status = "running"
    msg.error = ""
    db.commit()


def _history_messages(db: Session, chat_id: int, upto_id: int) -> list[dict]:
    """История для шлюза в формате OpenAI. Берём только текстовые реплики:
    картинку и ролик модель всё равно не увидит, а строка «[картинка]» в
    контексте только сбивает."""
    rows = (db.query(ChatMessage)
            .filter(ChatMessage.chat_id == chat_id, ChatMessage.id < upto_id,
                    ChatMessage.kind == "text", ChatMessage.status != "error")
            .order_by(ChatMessage.id.desc()).limit(HISTORY_LIMIT).all())
    rows.reverse()
    out = []
    for m in rows:
        text = (m.text or "").strip()
        if text:
            out.append({"role": m.role, "content": text})
    return out


async def _ask_gateway(messages: list[dict]) -> tuple[str, str]:
    """Ответ подписочного шлюза целиком → (текст, кто ответил).

    Цепочка провайдеров живёт на хосте (claude → chatgpt → grok) и падает
    молча, поэтому в селекторе у нас ОДНА позиция «Auto», а не три имени
    моделей: показывать «Claude» там, где на сервере нет его бинарника, —
    значит врать ярлыком."""
    payload = {"model": "sonnet", "messages": messages}
    async with httpx.AsyncClient(timeout=CHAT_TIMEOUT) as client:
        r = await client.post(CHAT_GATEWAY_URL, json=payload)
    if r.status_code != 200:
        raise RuntimeError(f"шлюз ответил {r.status_code}: {r.text[:200]}")
    data = r.json() or {}
    choices = data.get("choices") or []
    text = ""
    if choices:
        text = str(((choices[0] or {}).get("message") or {}).get("content") or "")
    if not text.strip():
        raise RuntimeError("шлюз вернул пустой ответ")
    return text.strip(), str(data.get("model") or "")


def _run_text(message_id: int) -> None:
    db = SessionLocal()
    try:
        msg = db.get(ChatMessage, message_id)
        if not msg:
            return
        _mark_running(db, msg)
        # Свежая реплика человека УЖЕ лежит в базе к этому моменту, поэтому
        # она приходит последней строкой истории. Дописывать её отдельно
        # нельзя: вопрос уезжал бы в модель дважды.
        history = _history_messages(db, msg.chat_id, msg.id)
        if not history:
            raise RuntimeError("пустой запрос")
        text, provider = asyncio.run(_ask_gateway(history))
        msg = db.get(ChatMessage, message_id)
        msg.text = text
        msg.status = "done"
        params = {}
        try:
            params = json.loads(msg.params_json or "{}")
        except ValueError:
            params = {}
        params["provider"] = provider
        msg.params_json = json.dumps(params, ensure_ascii=False)
        db.commit()
    except Exception as e:  # noqa: BLE001
        db.rollback()
        db.close()
        _fail(message_id, e)
        return
    finally:
        try:
            db.close()
        except Exception:  # noqa: BLE001
            pass


def _run_image(message_id: int, ref_paths: list[str]) -> None:
    db = SessionLocal()
    try:
        msg = db.get(ChatMessage, message_id)
        if not msg:
            return
        _mark_running(db, msg)
        chat = db.get(Chat, msg.chat_id)
        owner_id = chat.owner_id if chat else 0
        ask = (db.query(ChatMessage)
               .filter(ChatMessage.chat_id == msg.chat_id, ChatMessage.id < msg.id,
                       ChatMessage.role == "user")
               .order_by(ChatMessage.id.desc()).first())
        prompt = (ask.text if ask else "").strip() or "cinematic vertical image"
        res = asyncio.run(mediagen.generate_image_ex(
            prompt, reference_paths=[p for p in ref_paths if os.path.exists(p)] or None,
            engine=msg.engine, resolution=CHAT_IMAGE_RESOLUTION, aspect="9:16"))
        # upscale=False намеренно: 4К-картинка в ленте не нужна, а её вес
        # платится диском на весь срок хранения.
        fname = _ctx.save_image(res["data"], res["mime"], upscale=False)
        msg = db.get(ChatMessage, message_id)
        msg.media_filename = fname
        msg.status = "done"
        _ctx.reg_file(db, fname, owner_id)
        db.commit()
    except Exception as e:  # noqa: BLE001
        db.rollback()
        db.close()
        _fail(message_id, e)
        return
    finally:
        try:
            db.close()
        except Exception:  # noqa: BLE001
            pass


def _run_video(message_id: int, first_path: str, duration: int) -> None:
    db = SessionLocal()
    try:
        msg = db.get(ChatMessage, message_id)
        if not msg:
            return
        _mark_running(db, msg)
        chat = db.get(Chat, msg.chat_id)
        owner_id = chat.owner_id if chat else 0
        ask = (db.query(ChatMessage)
               .filter(ChatMessage.chat_id == msg.chat_id, ChatMessage.id < msg.id,
                       ChatMessage.role == "user")
               .order_by(ChatMessage.id.desc()).first())
        prompt = (ask.text if ask else "").strip() or "subtle cinematic motion"
        spec = mediagen.VIDEO_ENGINES.get(msg.engine) or {}
        fname = asyncio.run(mediagen.animate_scene(
            prompt=prompt, first_path=first_path, last_path=None,
            duration_sec=int(duration or 6),
            provider=spec.get("family") or "grok", engine=msg.engine))
        msg = db.get(ChatMessage, message_id)
        msg.media_filename = fname
        msg.status = "done"
        _ctx.reg_file(db, fname, owner_id)
        db.commit()
    except Exception as e:  # noqa: BLE001
        db.rollback()
        db.close()
        _fail(message_id, e)
        return
    finally:
        try:
            db.close()
        except Exception:  # noqa: BLE001
            pass


# ─────────────────────── мост «чат → студия» ───────────────────────

@router.post("/api/chats/messages/{message_id}/save-to")
async def save_to_project(message_id: int, request: Request,
                          user: User = Depends(current_user),
                          db: Session = Depends(db_session)):
    """Картинку из чата — персонажу (фото или разворот), со снятием срока.

    Это и есть смысл чата ВНУТРИ этого сервиса, а не отдельным продуктом:
    удачный кадр не остаётся в переписке, а уезжает в проект."""
    msg = _own_message(db, user, message_id)
    if not msg.media_filename or msg.kind != "image":
        raise HTTPException(400, "сохранить можно только картинку")
    body = await _body(request)
    char_id = int(body.get("character_id") or 0)
    as_kind = "model" if str(body.get("as") or "photo") == "model" else "photo"
    ch = db.get(Character, char_id)
    if not ch or not _ctx.owned(user, ch.project):
        raise HTTPException(404, "персонаж не найден")

    src = os.path.join(_ctx.upload_dir, msg.media_filename)
    if not os.path.exists(src):
        raise HTTPException(404, "файл отсутствует на диске")
    ext = os.path.splitext(msg.media_filename)[1] or ".png"
    # Копия БАЙТАМИ под новым именем: удаление чата не должно уносить фото
    # персонажа (та же логика, что у клонирования героев в студии).
    fname = f"char_{uuid.uuid4().hex}{ext}"
    shutil.copyfile(src, os.path.join(_ctx.upload_dir, fname))
    _ctx.reg_file(db, fname, ch.project.owner_id)
    max_pos = max((p.position for p in ch.photos), default=0)
    db.add(CharacterPhoto(character_id=ch.id, position=max_pos + 1, filename=fname,
                          kind=as_kind, pose_kind="3d" if as_kind == "model" else "",
                          from_photos=0))
    # Сообщение сохранено в проект — срок хранения снимаем: файл теперь не
    # мусор переписки, а часть работы.
    msg.expires_at = None
    db.commit()
    return {"ok": True, "character_id": ch.id, "as": as_kind}


# ─────────────────────── ретенция: ночной сборщик ───────────────────────

def _drop_media(db: Session, filename: str) -> None:
    if not filename:
        return
    _ctx.remove_media(filename)
    row = db.get(FileOwner, filename)
    if row:
        db.delete(row)


def sweep_expired() -> int:
    """Снести просроченные медиа чатов и висячие вложения.

    Удаляется ФАЙЛ, не сообщение: на месте картинки остаётся промпт и кнопка
    «повторить». Срок человек видит в интерфейсе до первой генерации, а не
    в оферте после."""
    db = SessionLocal()
    dropped = 0
    try:
        rows = (db.query(ChatMessage)
                .filter(ChatMessage.media_filename != "",
                        ChatMessage.expires_at.isnot(None),
                        ChatMessage.expires_at < now())
                .limit(500).all())
        for m in rows:
            _drop_media(db, m.media_filename)
            m.media_filename = ""
            m.expires_at = None
            dropped += 1
        cutoff = now() - timedelta(hours=ORPHAN_FILE_HOURS)
        orphans = (db.query(ChatFile)
                   .filter(ChatFile.message_id.is_(None), ChatFile.created_at < cutoff)
                   .limit(500).all())
        for f in orphans:
            _drop_media(db, f.filename)
            db.delete(f)
            dropped += 1
        if dropped:
            db.commit()
            log.info("ретенция чатов: убрано %s файлов", dropped)
    except Exception as e:  # noqa: BLE001
        db.rollback()
        log.warning("сборщик чатов упал: %s", str(e)[:200])
    finally:
        db.close()
    return dropped


def _retention_worker() -> None:
    """Сутки шага, работа ПЕРЕД сном: иначе после каждого рестарта контейнера
    первый проход откладывался бы ещё на день (та же грабля, что была у
    проверки подписок)."""
    while True:
        try:
            sweep_expired()
        except Exception as e:  # noqa: BLE001
            log.warning("проход ретенции чатов упал: %s", str(e)[:200])
        time.sleep(24 * 3600)


def start_worker() -> None:
    Thread(target=_retention_worker, daemon=True).start()
