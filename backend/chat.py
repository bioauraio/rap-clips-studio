"""Чат lolq.ai — ОДНО окно, в котором переключается модель.

Главная идея, из-за которой это не «ещё четыре вкладки»: намерение задаёт
ВЫБРАННАЯ МОДЕЛЬ, а не режим экрана. Выбран текстовый движок — Enter уходит в
подписочный шлюз; выбран движок картинок — тот же текст уезжает промптом
вместе с вложениями; выбран движок видео — оживляется картинка (вложенная,
явно указанная кнопкой «Оживить» или последняя в этой же ленте).

Три вещи, которые здесь принципиальны и легко потерять при правках:

1. ТАРИФ. Чат не открывает НИ ОДНОГО движка, закрытого тарифом: фильтры те же,
   что у студии (_plan_engine_ids / image_engine тарифа). Иначе FREE со 120
   токенами запускал бы Seedance 2.5 (154 токена за прогон) и Nano Banana Pro
   ($0.09 живых денег за картинку) — экономика сервиса ломается мгновенно.
   Закрытые движки ВИДНЫ (серые, с ценой и ярлыком тарифа) — это витрина.

2. ВОЗВРАТ ОЧКОВ. Списываем в момент постановки задачи, как везде в проекте,
   но здесь запросы одиночные: молча съеденные за упавшую генерацию 154 токена
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
from fastapi import APIRouter, Depends, Form, HTTPException, Request, UploadFile
from sqlalchemy.orm import Session

import mediagen
import refs as refs_mod
from db import (
    Chat, ChatFile, ChatMessage, Character, CharacterPhoto, FileOwner,
    Project, Scene, SceneRef, SessionLocal, User, now,
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
# Сколько вариантов за один запуск. Четыре — потолок не от движка, а от глаза:
# больше четырёх миниатюр в полосе перестают сравниваться, а цена умножается
# ровно на N и на дорогом движке уходит за тысячу токенов одним нажатием.
CHAT_MAX_VARIANTS = 4
# НАСТОЯЩИЕ лимиты поля ввода. Раз мы режем текст на сервере — обязаны
# написать, где именно: счётчик «0/5000» из чужого интерфейса, не совпадающий
# с обрезкой, хуже отсутствия счётчика.
CHAT_TEXT_LIMIT = 8000        # разговор с текстовой моделью
CHAT_PROMPT_LIMIT = 2000      # промпт картинки и промпт движения

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
    # Блокировка — тот же гейт, что и в студии. Без этой строки заблокированный
    # человек продолжал бы жечь платные движки через чат: чат разрешает
    # сессию сам и до сих пор про блокировку ничего не знал.
    _ctx.guard(user)
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
# студийный /api/providers, поэтому цена в токенах не может разойтись с
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
    FREE рисовал бы на $0.09 за картинку при 120 токенах на весь месяц."""
    out = [e for e in mediagen.GATEWAY_IMAGE_ENGINES if e in mediagen.image_engines_live()]
    plan_engine = (_ctx.plans[_plan_id(user)].get("image_engine") or "chatgpt")
    paid_plan = bool(mediagen.IMAGE_ENGINES.get(plan_engine, {}).get("paid"))
    if (paid_plan or user.is_admin) and mediagen.kie_available():
        out += [k for k, v in mediagen.IMAGE_ENGINES.items()
                if v["channel"] == "kie" and k not in out]
    return out


def _allowed_video_engines(user: User) -> list[str]:
    """Движки видео, открытые ЭТОМУ человеку.

    Админу открыто всё — ровно как в _allowed_image_engines выше. Без этой
    оговорки список врал в одну сторону: _check_allowed пропускает админа на
    любой движок и не берёт с него денег, а панель тем временем гасила
    seedance-2-0 замком «открывается с тарифа STUDIO». Владелец видел запрет
    на то, что у него прямо сейчас работает и стоит ноль."""
    if user.is_admin:
        return list(mediagen.VIDEO_ENGINES)
    return _ctx.plan_engine_ids(_plan_id(user))


def _image_points(user: User, engine: str, resolution: str = "") -> int:
    return int(_ctx.image_cost(user, engine, resolution or CHAT_IMAGE_RESOLUTION))


def _video_points(engine: str, duration: int) -> int:
    """Цена ролика БЕЗ доплаты за кадры: первый кадр в чате уже есть,
    добирать нечего."""
    usd = mediagen.video_engine_usd(engine, int(duration or 6))
    return int(_ctx.points_of_usd(usd))


def _image_resolutions(engine: str) -> list[str]:
    """Разрешения, которые движок реально умеет. Пусто — разрешение задаёт
    сам движок, и переключателя быть не должно: контрол, который ничего не
    меняет, врёт дороже отсутствующего."""
    spec = mediagen.IMAGE_ENGINES.get(engine) or {}
    return [r for r in (spec.get("resolutions") or ())]


def _image_aspects(engine: str) -> list[str]:
    """Форматы кадра движка. Шлюзы формат не принимают вовсе (payload у них
    — один prompt), у nano-banana-edit поля aspect_ratio нет в доках."""
    spec = mediagen.IMAGE_ENGINES.get(engine) or {}
    if spec.get("channel") == "gateway" or spec.get("aspect") is False:
        return []
    return list(mediagen.ASPECTS.keys())


def _video_aspects(engine: str) -> list[str]:
    spec = mediagen.VIDEO_ENGINES.get(engine) or {}
    if spec.get("channel") == "gateway":
        return []
    return list(mediagen.ASPECTS.keys())


def _norm_aspect(value, allowed: list[str]) -> str:
    want = str(value or "").strip()
    if want in allowed:
        return want
    return allowed[0] if allowed else "9:16"


def _norm_resolution(value, allowed: list[str]) -> str:
    want = str(value or "").strip().upper()
    if want in allowed:
        return want
    if CHAT_IMAGE_RESOLUTION in allowed:
        return CHAT_IMAGE_RESOLUTION
    return allowed[0] if allowed else CHAT_IMAGE_RESOLUTION


def _norm_variants(value) -> int:
    try:
        n = int(value or 1)
    except (TypeError, ValueError):
        n = 1
    return max(1, min(CHAT_MAX_VARIANTS, n))


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

    ЦЕНУ СЧИТАЕТ ТОЛЬКО СЕРВЕР. Раньше клиент домножал базовую цену на
    длительность сам (`points * dur / 6`) и расходился со списанием на любой
    длине, кроме шести секунд: Grok 10 с показывал 4 при списании 2, Seedance
    2.5 — 254 при 252. Здесь уезжает ГОТОВАЯ таблица цен, и второй кассы в
    сервисе больше нет — ровно то же правило, что записано в textgen.py.
    """
    items: list[dict] = [{
        "id": _model_id("text", TEXT_MODEL_ID), "kind": "text",
        "title": _engine_title(TEXT_MODEL_ID),
        "live": True, "points": _text_points(), "allowed": True,
        "needs_image": False, "first_last": False, "max_refs": 0,
        "note": "", "plan": "",
        "aspects": [], "resolutions": [], "versions": [],
        "points_by_duration": {}, "points_by_resolution": {},
        "limit": CHAT_TEXT_LIMIT,
    }]

    img_allowed = _allowed_image_engines(user)
    img_live = mediagen.image_engines_live()
    for eid, spec in mediagen.IMAGE_ENGINES.items():
        resolutions = _image_resolutions(eid)
        items.append({
            "id": _model_id("image", eid), "kind": "image", "title": spec["title"],
            "live": eid in img_live,
            "points": _image_points(user, eid),
            "allowed": eid in img_allowed,
            "needs_image": False, "first_last": False,
            "max_refs": int(spec.get("max_refs") or 1),
            "note": "", "plan": "" if eid in img_allowed else "pro_max",
            "aspects": _image_aspects(eid),
            "resolutions": resolutions,
            # Цена зависит от разрешения (у Nano Banana 2 — 1K $0.04 против
            # 4K $0.09), поэтому чип разрешения обязан носить свою цену.
            "points_by_resolution": {r: _image_points(user, eid, r) for r in resolutions},
            "points_by_duration": {},
            # Версий у картиночных движков нет: nano-banana-2 и pro — разные
            # модели, а не режимы одной. Пустой список = группы «Версия» нет.
            "versions": [],
            "limit": CHAT_PROMPT_LIMIT,
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
            "aspects": _video_aspects(eid),
            # Разрешение видео — часть ВЫБОРА ДВИЖКА и часть цены (720p против
            # 480p у Seedance 2.5 — это 152 токена против 68). Переключателя
            # качества здесь нет намеренно: он молча менял бы движок и цену.
            "resolutions": [],
            "quality": spec.get("resolution", "") or ("1080p" if spec.get("mode") == "pro" else ""),
            "points_by_duration": {str(d): _video_points(eid, d) for d in CHAT_VIDEO_DURATIONS},
            "points_by_resolution": {},
            # С двумя кадрами Seedance берёт пропорции с самих кадров, а выбор
            # формата игнорирует. Интерфейс обязан сказать это ДО запуска, а не
            # показывать чипы, которые в этом случае ничего не решают.
            "aspect_from_frames": mediagen.aspect_locked_by_frames(eid),
            "versions": [_model_id("video", v) for v in mediagen.engine_versions(eid)],
            "limit": CHAT_PROMPT_LIMIT,
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


def _storage(db: Session, user: User) -> dict:
    """Занято / доступно. Считается по ФИЗИЧЕСКИМ файлам — копия проекта живёт
    жёсткими ссылками и второй раз не весит ничего."""
    try:
        used = int(_ctx.storage_used(db, user.id))
        limit = int(_ctx.storage_quota(user))
    except Exception:  # noqa: BLE001
        return {}
    return {"used_bytes": used, "limit_bytes": limit,
            "pct": round(100.0 * used / limit, 1) if limit else 0.0}


@router.get("/api/chat/models")
def chat_models(user: User = Depends(current_user), db: Session = Depends(db_session)):
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
        "max_file_mb": CHAT_MAX_FILE_MB,
        "max_variants": CHAT_MAX_VARIANTS,
        "text_limit": CHAT_TEXT_LIMIT,
        "prompt_limit": CHAT_PROMPT_LIMIT,
        # Виды референса — те же четыре, что у кадров студии, и те же тексты
        # правил (backend/refs.py). Один реестр на оба места.
        "ref_kinds": list(refs_mod.REF_KINDS),
        "ref_default": refs_mod.REF_DEFAULT,
        "aspects": list(mediagen.ASPECTS.keys()),
        "default_resolution": CHAT_IMAGE_RESOLUTION,
        # Занятое место — тем же ответом, а не отдельным запросом: строка про
        # диск стоит рядом со строкой про срок хранения, и обе они про то,
        # сколько живёт результат.
        "storage": _storage(db, user),
    }


# ─────────────────────────── сериализация ───────────────────────────

def _msg_dict(m: ChatMessage) -> dict:
    media = m.media_filename or ""
    expired = bool(not media and m.kind in ("image", "video") and m.status == "done")
    try:
        params = json.loads(m.params_json or "{}")
    except ValueError:
        params = {}
    # ЧЕМ НАРИСОВАНО НА САМОМ ДЕЛЕ. Пусто — сработал запрошенный движок;
    # заполнено — цепочка фолбэков ушла на шлюз, и человек обязан это увидеть
    # на самом кадре, а не догадываться, почему картинка «другая».
    actual = (m.engine_actual or "").strip()
    return {
        "id": m.id, "role": m.role, "kind": m.kind, "text": m.text,
        "engine": _model_id(m.kind, m.engine) if m.engine else "",
        "engine_title": _engine_title(m.engine, m.kind) if m.engine else "",
        "engine_actual": actual,
        "engine_actual_title": _engine_title(actual, m.kind) if actual else "",
        "group_id": m.group_id or "",
        "pinned": bool(m.pinned),
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
                   "thumb_url": f"/api/thumb/{f.filename}",
                   "kind": f.kind or refs_mod.REF_DEFAULT} for f in m.files],
    }


def _chat_dict(c: Chat, spent: int = 0, thumb: str = "") -> dict:
    return {
        "id": c.id, "title": c.title or "", "model": c.model or "",
        "spent": int(spent or 0),
        "pinned": bool(c.pinned),
        # Миниатюра последнего медиа. Глаз узнаёт ленту по картинке быстрее,
        # чем читает авто-заголовок из первых сорока символов промпта.
        "thumb_url": f"/api/thumb/{thumb}" if thumb else "",
        "created_at": c.created_at.isoformat() if c.created_at else "",
        "updated_at": c.updated_at.isoformat() if c.updated_at else "",
    }


def _auto_title(text: str) -> str:
    line = " ".join(str(text or "").split())
    return line[:40] if line else ""


# ─────────────────────────── список чатов ───────────────────────────

@router.get("/api/chats")
def list_chats(q: str = "", user: User = Depends(current_user),
               db: Session = Depends(db_session)):
    """Ленты человека. q ищет и по названию, и ПО ТЕКСТУ СООБЩЕНИЙ.

    Клиентский фильтр искал только по названию, а название — это авто-заголовок
    из первых сорока символов первого промпта. То есть поиск по «трасса» не
    находил ленту, где это слово стоит во втором промпте, — и человек делал
    вывод, что поиска нет.

    ФИЛЬТРУЕМ В PYTHON, А НЕ В SQL. База у нас SQLite, и её lower() работает
    только с латиницей: «Трасса» осталась бы «Трассой», а иголка приехала бы
    строчной — русский поиск не находил бы ничего и молчал бы об этом.
    Сообщения ленты всё равно перебираются здесь же (расход и миниатюра), так
    что второго прохода это не стоит.
    """
    rows = (db.query(Chat)
            .filter(Chat.owner_id == user.id, Chat.archived.is_(False))
            .order_by(Chat.pinned.desc(), Chat.updated_at.desc(), Chat.id.desc())
            .limit(200).all())
    needle = str(q or "").strip().lower()
    out = []
    for c in rows:
        spent = 0
        thumb = ""
        hit = not needle or needle in (c.title or "").lower()
        for m in c.messages:
            spent += int(m.points or 0)
            if m.media_filename and m.kind == "image":
                thumb = m.media_filename
            if not hit and needle in (m.text or "").lower():
                hit = True
        if hit:
            out.append(_chat_dict(c, spent, thumb))
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
    if "pinned" in body:
        chat.pinned = bool(body.get("pinned"))
    db.commit()
    return _chat_dict(chat)


@router.patch("/api/chats/messages/{message_id}")
async def update_message(message_id: int, request: Request,
                         user: User = Depends(current_user),
                         db: Session = Depends(db_session)):
    """Пока здесь ровно одно поле — «избранное».

    И оно не про сортировку: закреплённое сообщение перестаёт подметаться
    ретенцией (sweep_expired). Звезда, которая только красит строку, а файл
    под ней всё равно исчезает по сроку, — это врущий контрол."""
    msg = _own_message(db, user, message_id)
    body = await _body(request)
    if "pinned" in body:
        msg.pinned = bool(body.get("pinned"))
        # Закрепили — срок снимаем сразу, а не «начиная со следующего прохода»;
        # открепили — возвращаем обычный срок тарифа от сегодняшнего дня.
        if msg.pinned:
            msg.expires_at = None
        elif msg.kind in ("image", "video") and msg.media_filename:
            days = _retention_days(user)
            msg.expires_at = (now() + timedelta(days=days)) if days else None
    db.commit()
    return _msg_dict(msg)


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
async def chat_upload(file: UploadFile, kind: str = Form(refs_mod.REF_DEFAULT),
                      user: User = Depends(current_user),
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
    _ctx.reg_file(db, fname, user.id, kind="chat")
    row = ChatFile(owner_id=user.id, filename=fname, position=0,
                   kind=refs_mod.ref_norm_kind(kind))
    db.add(row)
    db.commit()
    db.refresh(row)
    return {"id": row.id, "url": f"/api/media/{fname}",
            "thumb_url": f"/api/thumb/{fname}", "kind": row.kind}


def _take_files(db: Session, user: User, file_ids: list,
                kinds: dict | None = None) -> list[ChatFile]:
    """Вложения запроса + ВИД каждого референса.

    Вид приезжает вместе с отправкой, а не при заливке: человек ставит его
    чипом уже после того, как увидел картинку в строке. Список kinds —
    {"12": "style"}, ключ строкой, потому что это JSON."""
    kinds = kinds or {}
    out = []
    for raw in (file_ids or [])[:CHAT_MAX_FILES]:
        try:
            fid = int(raw)
        except (TypeError, ValueError):
            continue
        row = db.get(ChatFile, fid)
        if row and row.owner_id == user.id and row.message_id is None:
            want = kinds.get(str(fid)) or kinds.get(fid)
            if want:
                row.kind = refs_mod.ref_norm_kind(want)
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


def _last_frame(db: Session, chat: Chat, files: list, last_id, engine: str) -> str:
    """ВТОРОЙ кадр для интерполяции. Пусто — обычное оживление одного кадра.

    Движку без first_last второй кадр не показываем даже если он пришёл: Grok
    оживляет только первый, и молча съесть указанный человеком последний кадр
    значило бы взять деньги за работу, которой не было."""
    spec = mediagen.VIDEO_ENGINES.get(engine) or {}
    if not spec.get("first_last"):
        return ""
    if last_id:
        src = db.get(ChatMessage, int(last_id))
        if src and src.chat_id == chat.id and src.media_filename:
            path = os.path.join(_ctx.upload_dir, src.media_filename)
            if os.path.exists(path):
                return path
    paths = _file_paths(files)
    return paths[1] if len(paths) > 1 else ""


def _post_message(db: Session, user: User, chat: Chat, body: dict) -> dict:
    """Общий путь отправки: и обычная реплика, и «Оживить» приходят сюда."""
    model_id = str(body.get("engine") or chat.model or "").strip()
    kind, engine = _check_allowed(user, model_id)
    limit = CHAT_TEXT_LIMIT if kind == "text" else CHAT_PROMPT_LIMIT
    text = str(body.get("text") or "").strip()[:limit]
    files = _take_files(db, user, body.get("file_ids") or [], body.get("file_kinds") or {})
    duration = _norm_duration(body.get("duration"))
    variants = _norm_variants(body.get("variants"))

    if not text and kind != "video":
        raise HTTPException(400, "напиши, что нужно сделать")
    if kind == "text":
        # Текст вариантами не гоняем: четыре ответа шлюза — это четыре
        # разговора, а лента у нас одна. Контрол на фронте для текста скрыт,
        # но запрос может прийти и мимо него.
        variants = 1

    params: dict = {}
    source_path = ""
    last_path = ""
    if kind == "video":
        source_path = _source_frame(db, chat, files, body.get("from_message_id"))
        last_path = _last_frame(db, chat, files, body.get("last_message_id"), engine)
        aspect = _norm_aspect(body.get("aspect"), _video_aspects(engine))
        params["duration"] = duration
        params["source"] = os.path.basename(source_path)
        if last_path:
            params["last"] = os.path.basename(last_path)
        # Формат записываем, только если он реально уехал в движок: иначе
        # строка меты у ролика хвасталась бы «9:16», которых движок не видел.
        if _video_aspects(engine) and not (last_path and mediagen.aspect_locked_by_frames(engine)):
            params["aspect"] = aspect
        cost = _video_points(engine, duration)
    elif kind == "image":
        resolution = _norm_resolution(body.get("resolution"), _image_resolutions(engine))
        aspect = _norm_aspect(body.get("aspect"), _image_aspects(engine))
        params["resolution"] = resolution
        if _image_aspects(engine):
            params["aspect"] = aspect
        if files:
            params["refs"] = [f.kind or refs_mod.REF_DEFAULT for f in files]
        save_to = body.get("save_to")
        if isinstance(save_to, dict) and save_to.get("target"):
            params["save_to"] = save_to
        cost = _image_points(user, engine, resolution)
    else:
        cost = _text_points()

    # Списываем ДО создания строк: отказ по деньгам не должен оставлять в
    # ленте сообщение-призрак, на которое человек потом смотрит и не понимает.
    # Вид и движок уезжают в журнал токенов ЯВНО: без них кабинет не может
    # разложить расход по движкам, а спорную генерацию не с чем сверить.
    #
    # Каждый вариант оплачивается ОТДЕЛЬНОЙ проводкой, а не одной на пачку:
    # упавший третий вариант обязан вернуть ровно свою долю, а не всю сумму
    # и не ноль.
    for _ in range(variants):
        _ctx.charge(db, user, cost, f"чат {chat.id}: {engine}",
                    kind="chat", engine=engine, ref_type="chat", ref_id=chat.id,
                    cost_cents=_ctx.cost_cents(
                        "video" if kind == "video" else ("image" if kind == "image" else "text"),
                        engine, seconds=duration if kind == "video" else 0,
                        resolution=params.get("resolution", "") if kind == "image" else ""))

    pos = (db.query(ChatMessage).filter(ChatMessage.chat_id == chat.id).count()) + 1
    ask = ChatMessage(chat_id=chat.id, position=pos, role="user", kind="text",
                      text=text, status="done")
    db.add(ask)
    db.flush()
    for i, f in enumerate(files, start=1):
        f.message_id = ask.id
        f.position = i

    days = _retention_days(user)
    group = uuid.uuid4().hex if variants > 1 else ""
    answers: list[ChatMessage] = []
    for i in range(variants):
        answer = ChatMessage(
            chat_id=chat.id, position=pos + 1 + i, role="assistant", kind=kind,
            text="", engine=engine, points=cost, status="queued", group_id=group,
            params_json=json.dumps(params, ensure_ascii=False),
            expires_at=(now() + timedelta(days=days)) if (days and kind != "text") else None,
        )
        db.add(answer)
        answers.append(answer)
    if not chat.title:
        chat.title = _auto_title(text) or _engine_title(engine, kind)
    # В чате запоминается ИМЕННО позиция селектора (с префиксом вида), а не
    # голое имя движка: вернувшись, человек должен попасть в тот же режим.
    chat.model = _model_id(kind, engine)
    chat.updated_at = now()
    db.commit()
    db.refresh(ask)
    for answer in answers:
        db.refresh(answer)

    ref_kinds = [f.kind or refs_mod.REF_DEFAULT for f in files]
    for answer in answers:
        if kind == "text":
            Thread(target=_run_text, args=(answer.id,), daemon=True).start()
        elif kind == "image":
            Thread(target=_run_image,
                   args=(answer.id, _file_paths(files), ref_kinds), daemon=True).start()
        else:
            Thread(target=_run_video,
                   args=(answer.id, source_path, last_path, duration), daemon=True).start()

    return {"chat": _chat_dict(chat), "ask": _msg_dict(ask),
            "answer": _msg_dict(answers[0]),
            "answers": [_msg_dict(a) for a in answers],
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
    # Повтор идёт ТЕМИ ЖЕ параметрами, а не дефолтными: человек нажал
    # «повторить», а не «сделай что-нибудь ещё раз». Формат и разрешение
    # лежат в params с первой попытки.
    body = {
        "text": ask.text if ask else msg.text,
        "engine": _model_id(msg.kind, msg.engine) if msg.engine else chat.model,
        "duration": params.get("duration"),
        "aspect": params.get("aspect"),
        "resolution": params.get("resolution"),
        "variants": 1,
    }
    if msg.kind == "video":
        prev_img = _last_image_message(db, chat)
        if prev_img:
            body["from_message_id"] = prev_img.id
    return _post_message(db, user, chat, body)


@router.post("/api/chats/messages/{message_id}/cancel")
def cancel_message(message_id: int, user: User = Depends(current_user),
                   db: Session = Depends(db_session)):
    """Отмена задачи. Два РАЗНЫХ случая, и врать про них нельзя.

    queued — движок ещё не тронут, задача снимается целиком и очки
    возвращаются полностью.
    running — движок уже считает и уже выставит нам счёт. Отменить можно
    только ожидание: очки не возвращаются, и кнопка об этом прямо написана
    ДО нажатия, а не после."""
    msg = _own_message(db, user, message_id)
    if msg.role != "assistant" or msg.status not in ("queued", "running"):
        raise HTTPException(400, "отменять нечего: задача уже завершилась")
    was = msg.status
    msg.status = "canceled"
    refunded = 0
    if was == "queued":
        points = int(msg.points or 0)
        chat = db.get(Chat, msg.chat_id)
        owner = db.get(User, chat.owner_id) if chat else None
        if owner and points > 0:
            _ctx.refund(db, owner, points, f"чат {msg.chat_id}: отмена {msg.engine}",
                        kind="refund", engine=msg.engine or "",
                        ref_type="chat_message", ref_id=msg.id)
            msg.points = 0
            refunded = points
    db.commit()
    return {"ok": True, "refunded": refunded, "was": was,
            "message": _msg_dict(msg),
            "points": None if user.is_admin else int(user.gen_points or 0)}


# ─────────────────────────── улучшение промпта ───────────────────────────

# Инструкция шлюзу. Отдельной моделью не платим: шлюз стоит нам ноль, как и
# текстовые шаги студии (TEXT_COST), поэтому кнопка бесплатная.
ENHANCE_SYSTEM = (
    "You rewrite short user prompts into precise prompts for an image or video "
    "generator. Keep the user's intent and subject exactly. Add only what a camera "
    "would see: framing, lens, light, colour, texture, mood. No preamble, no "
    "explanation, no quotes, no markdown — return the rewritten prompt only, "
    "in English, under 120 words."
)


@router.post("/api/chat/enhance")
async def enhance_prompt(request: Request, user: User = Depends(current_user)):
    """«✨ Улучшить промпт» — 0 токенов, потому что шлюз стоит нам ноль.

    Результат НЕ подменяет промпт молча: он возвращается наверх, кладётся в
    поле, и под полем остаётся «вернуть как было». Человек платит за кадр до
    252 токенов — читать текст, за который он платит, он обязан до отправки."""
    body = await _body(request)
    text = str(body.get("text") or "").strip()
    if not text:
        raise HTTPException(400, "сначала напиши промпт")
    messages = [{"role": "system", "content": ENHANCE_SYSTEM},
                {"role": "user", "content": text[:CHAT_PROMPT_LIMIT]}]
    try:
        out, _provider = await _ask_gateway(messages)
    except Exception as e:  # noqa: BLE001
        raise HTTPException(502, f"не получилось улучшить промпт: {str(e)[:200]}")
    return {"text": out.strip()[:CHAT_PROMPT_LIMIT], "points": 0}


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
        if msg.status == "canceled":
            # Отменённую задачу ошибкой не перекрашиваем: человек уже знает,
            # что её нет, а очки за неё уже разобраны в cancel_message.
            return
        msg.status = "error"
        msg.error = str(err)[:500]
        points = int(msg.points or 0)
        chat = db.get(Chat, msg.chat_id)
        owner = db.get(User, chat.owner_id) if chat else None
        if owner and points > 0:
            _ctx.refund(db, owner, points, f"чат {msg.chat_id}: {msg.engine}",
                        kind="refund", engine=msg.engine or "",
                        ref_type="chat_message", ref_id=msg.id)
            msg.points = 0
        db.commit()
    except Exception as e:  # noqa: BLE001
        log.warning("не удалось пометить ошибку сообщения %s: %s", message_id, str(e)[:200])
    finally:
        db.close()


def _mark_running(db: Session, msg: ChatMessage) -> bool:
    """False — задачу отменили, пока она стояла в очереди: работать не над чем."""
    if msg.status == "canceled":
        return False
    msg.status = "running"
    msg.error = ""
    db.commit()
    return True


def _canceled(db: Session, message_id: int) -> bool:
    """Отменили, пока движок считал? Тогда результат не сохраняем — человек
    нажал «отменить», а не «поставь это в ленту чуть позже»."""
    msg = db.get(ChatMessage, message_id)
    return bool(msg and msg.status == "canceled")


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
        if not _mark_running(db, msg):
            return
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


def _refund_diff(db: Session, msg: ChatMessage, actual_engine: str) -> None:
    """Сработал НЕ ТОТ движок — вернуть разницу в цене.

    generate_image_ex откатывается по цепочке «запрошенный → ChatGPT-шлюз →
    Grok-шлюз» и честно возвращает поле engine, но до этого места его никто не
    читал: человек платил 8 токенов за Nano Banana Pro и мог получить картинку
    со шлюза, которая стоит нам ноль. Это не только «не тот движок» — это
    чужие деньги. Разницу возвращаем той же кассой, что и упавшую генерацию."""
    chat = db.get(Chat, msg.chat_id)
    owner = db.get(User, chat.owner_id) if chat else None
    if not owner:
        return
    paid = int(msg.points or 0)
    real = _image_points(owner, actual_engine, _msg_resolution(msg))
    if real >= paid:
        return
    back = paid - real
    _ctx.refund(db, owner, back,
                f"чат {msg.chat_id}: {msg.engine} → {actual_engine}",
                kind="refund", engine=actual_engine,
                ref_type="chat_message", ref_id=msg.id)
    msg.points = real


def _msg_resolution(msg: ChatMessage) -> str:
    try:
        params = json.loads(msg.params_json or "{}")
    except ValueError:
        params = {}
    return str(params.get("resolution") or CHAT_IMAGE_RESOLUTION)


def _run_image(message_id: int, ref_paths: list[str], ref_kinds: list[str] | None = None) -> None:
    db = SessionLocal()
    try:
        msg = db.get(ChatMessage, message_id)
        if not msg:
            return
        if not _mark_running(db, msg):
            return
        chat = db.get(Chat, msg.chat_id)
        owner_id = chat.owner_id if chat else 0
        try:
            params = json.loads(msg.params_json or "{}")
        except ValueError:
            params = {}
        ask = (db.query(ChatMessage)
               .filter(ChatMessage.chat_id == msg.chat_id, ChatMessage.id < msg.id,
                       ChatMessage.role == "user")
               .order_by(ChatMessage.id.desc()).first())
        prompt = (ask.text if ask else "").strip() or "cinematic vertical image"
        live_refs = [p for p in ref_paths if os.path.exists(p)]
        # ЛЕГЕНДА РЕФЕРЕНСОВ. Без неё модель получала пачку картинок и пачку
        # правил, никак не связанных между собой, — и клеила стиль к локации.
        # Тексты правил общие со студией (backend/refs.py), нумерация обязана
        # совпадать с порядком картинок в запросе.
        if live_refs and ref_kinds:
            prompt = prompt + "\n\n" + refs_mod.ref_legend(ref_kinds[:len(live_refs)])
        res = asyncio.run(mediagen.generate_image_ex(
            prompt, reference_paths=live_refs or None,
            engine=msg.engine,
            resolution=params.get("resolution") or CHAT_IMAGE_RESOLUTION,
            aspect=params.get("aspect") or "9:16"))
        if _canceled(db, message_id):
            return
        # upscale=False намеренно: 4К-картинка в ленте не нужна, а её вес
        # платится диском на весь срок хранения.
        fname = _ctx.save_image(res["data"], res["mime"], upscale=False)
        msg = db.get(ChatMessage, message_id)
        msg.media_filename = fname
        msg.status = "done"
        actual = str(res.get("engine") or "")
        if actual and actual != msg.engine:
            msg.engine_actual = actual
            _refund_diff(db, msg, actual)
        _ctx.reg_file(db, fname, owner_id, kind="chat")
        _auto_save(db, msg)
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


def _run_video(message_id: int, first_path: str, last_path: str, duration: int) -> None:
    db = SessionLocal()
    try:
        msg = db.get(ChatMessage, message_id)
        if not msg:
            return
        if not _mark_running(db, msg):
            return
        chat = db.get(Chat, msg.chat_id)
        owner_id = chat.owner_id if chat else 0
        try:
            params = json.loads(msg.params_json or "{}")
        except ValueError:
            params = {}
        ask = (db.query(ChatMessage)
               .filter(ChatMessage.chat_id == msg.chat_id, ChatMessage.id < msg.id,
                       ChatMessage.role == "user")
               .order_by(ChatMessage.id.desc()).first())
        prompt = (ask.text if ask else "").strip() or "subtle cinematic motion"
        spec = mediagen.VIDEO_ENGINES.get(msg.engine) or {}
        fname = asyncio.run(mediagen.animate_scene(
            prompt=prompt, first_path=first_path,
            last_path=last_path or None,
            duration_sec=int(duration or 6),
            provider=spec.get("family") or "grok", engine=msg.engine,
            aspect=params.get("aspect") or "9:16"))
        if _canceled(db, message_id):
            return
        msg = db.get(ChatMessage, message_id)
        msg.media_filename = fname
        msg.status = "done"
        _ctx.reg_file(db, fname, owner_id, kind="chat")
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

def _copy_media(src_name: str, prefix: str) -> str:
    """Копия БАЙТАМИ под новым именем. Именно копия, а не ссылка на тот же
    файл: удаление ленты не должно уносить фото персонажа или кадр сцены."""
    src = os.path.join(_ctx.upload_dir, src_name)
    if not os.path.exists(src):
        raise HTTPException(404, "файл отсутствует на диске")
    ext = os.path.splitext(src_name)[1] or ".png"
    fname = f"{prefix}_{uuid.uuid4().hex}{ext}"
    shutil.copyfile(src, os.path.join(_ctx.upload_dir, fname))
    return fname


def _own_scene_for_chat(db: Session, user: User, scene_id: int) -> Scene:
    scene = db.get(Scene, int(scene_id or 0))
    if not scene or not _ctx.owned(user, scene.track.project):
        raise HTTPException(404, "кадр не найден")
    return scene


AGENT_SYSTEM = (
    "Ты агент студии клипов: не советчик, а исполнитель. Тебе дают СОСТОЯНИЕ "
    "(один проект или все проекты человека) и его реплику. Ты отвечаешь планом "
    "конкретных действий по ЭТИМ данным.\n"
    "Отвечай ТОЛЬКО валидным JSON: {\"reply\": \"...\", \"actions\": [...]}.\n"
    "Каждое действие: {\"kind\", \"title\", \"track_id\", \"scene_id\", "
    "\"project_id\", \"prompt\", \"fields\"}.\n"
    "БЕСПЛАТНЫЕ kinds — их применит сервер сразу, без кнопки:\n"
    "  set_style   — поменять стиль трека: fields={\"style_keys\": \"ключ1,ключ2\", "
    "\"style_extra\": \"своими словами\"}\n"
    "  edit_scene  — поправить кадр: fields из image_prompt, image_prompt_last, "
    "motion_prompt, shot_note, camera_move, characters\n"
    "  new_track   — завести трек в проекте: fields={\"title\", \"comment\"}\n"
    "ПЛАТНЫЕ kinds — вернутся человеку кнопкой, запускает он:\n"
    "  gen_scenes, extend_scenes, gen_frames (scene_id), gen_video (scene_id), "
    "assemble (track_id), image (prompt), video (prompt)\n"
    "  open_project — переключить человека на проект project_id (бесплатно, кнопкой)\n"
    "none — просто ответ.\n"
    "title — надпись на кнопке по-русски, до 40 знаков. Предлагай только "
    "выполнимое сейчас: не зови рисовать кадры без раскадровки и не собирай "
    "клип без видео. За один ответ не больше двух бесплатных правок и трёх "
    "кнопок. reply — две-три живые фразы, без списков и markdown."
)

def _global_state(db: Session, user: User) -> dict:
    """Все проекты человека одним взглядом — контекст «Суперкомпьютера».

    Не полные раскадровки (это тысячи строк), а сводка недоделанного: агент
    отвечает на «что у меня где висит» и предлагает, куда пойти.
    """
    rows = (db.query(Project).filter(Project.owner_id == user.id)
            .order_by(Project.id.desc()).limit(20).all())
    out = []
    for pr in rows:
        tracks = list(pr.tracks)
        scenes = [sc for t in tracks for sc in t.scenes]
        out.append({
            "project_id": pr.id, "name": pr.name or "без названия",
            "kind": pr.kind or "album", "tracks": len(tracks),
            "scenes": len(scenes),
            "frames_missing": sum(1 for sc in scenes if not sc.image_filename),
            "videos_missing": sum(1 for sc in scenes
                                  if sc.image_filename and not sc.video_filename),
            "clips_ready": sum(1 for t in tracks if t.clip_filename),
        })
    return {"projects": out}


def _project_state(db: Session, user: User, project_id: int) -> dict:
    """Состояние проекта человеческим языком — контекст для агента.

    Агент без этого отвечает общими советами уровня «сначала придумайте идею».
    Смысл ассистента внутри студии именно в том, что он знает, что у ЭТОГО
    трека кадров на две минуты из трёх и что видео нет ровно у четырёх сцен.
    """
    p = (db.query(Project)
         .filter(Project.id == int(project_id or 0), Project.owner_id == user.id)
         .first())
    if not p:
        return {}
    tracks = []
    for t in sorted(p.tracks, key=lambda x: (x.position, x.id)):
        scenes = list(t.scenes)
        with_first = sum(1 for s in scenes if s.image_filename)
        with_video = sum(1 for s in scenes if s.video_filename)
        covered = sum(int(s.duration_sec or 0) for s in scenes)
        tracks.append({
            "id": t.id, "title": t.title or "без названия",
            "audio_sec": int(t.audio_duration_sec or 0),
            "scenes": len(scenes),
            "covered_sec": covered,
            "frames_done": with_first,
            "videos_done": with_video,
            "approved": sum(1 for s in scenes if s.approved and s.video_filename),
            "clip": t.clip_status or "нет",
            "style": (t.style_keys or "").replace(",", ", "),
            # Первые незакрытые сцены: агенту нужно на что сослаться id-шником.
            "next_without_frames": [s.id for s in sorted(scenes, key=lambda x: x.position)
                                    if not s.image_filename][:5],
            "next_without_video": [s.id for s in sorted(scenes, key=lambda x: x.position)
                                   if s.image_filename and not s.video_filename][:5],
        })
    return {
        "project": p.name or "без названия",
        "mode": p.kind or "album",
        "story": "есть" if (p.story or "").strip() else "нет",
        "characters": [c.name for c in sorted(p.characters, key=lambda x: x.position)
                       if (c.name or "").strip()],
        "tracks": tracks,
    }


def _core():
    """main импортируется ЛЕНИВО: chat подключается ИЗ main.py, и импорт на
    верхнем уровне дал бы цикл — тот же приём, что в crm.py и bot_api."""
    import main  # noqa: PLC0415
    return main


AGENT_FREE_KINDS = ("set_style", "edit_scene", "new_track")
AGENT_PAID_KINDS = ("gen_scenes", "extend_scenes", "gen_frames", "gen_video",
                    "assemble", "image", "video", "open_project", "none")


def _aid(value, fallback: int = 0) -> int:
    """id из ответа модели. Она нет-нет да пришлёт вместо числа НАЗВАНИЕ
    («Клип») — падать из-за этого нельзя, берём запасное значение."""
    try:
        return int(value)
    except (TypeError, ValueError):
        return fallback


def _agent_apply(db: Session, user: User, act: dict, default_project: int = 0) -> str:
    """Применить БЕСПЛАТНУЮ правку агента. Возвращает строку-отчёт.

    Сервер выполняет только то, что не стоит токенов и обратимо: стиль,
    тексты кадра, новый трек. Всё платное уходит человеку кнопкой — решение
    «с подтверждением» принято владельцем осознанно.
    """
    core = _core()
    kind = act.get("kind")
    fields = act.get("fields") or {}
    if kind == "set_style":
        t = db.get(core.Track, _aid(act.get("track_id")))
        if not t or not _owned_project(user, t.project):
            return "трек не найден"
        keys = [k.strip() for k in str(fields.get("style_keys") or "").split(",")
                if k.strip() in core.prompts_catalog.STYLE_KEYS][:3]
        extra = str(fields.get("style_extra") or "").strip()[:2000]
        t.style_keys = ",".join(keys)
        t.style_extra = extra
        t.style = core.prompts_catalog.fusion(keys, extra)
        db.commit()
        return f"стиль трека «{t.title}» обновлён"
    if kind == "edit_scene":
        sc = db.get(core.Scene, _aid(act.get("scene_id")))
        if not sc or not _owned_project(user, sc.track.project):
            return "кадр не найден"
        allowed = ("image_prompt", "image_prompt_last", "motion_prompt",
                   "shot_note", "camera_move", "characters")
        touched = []
        for f in allowed:
            if f in fields and str(fields[f]).strip():
                setattr(sc, f, str(fields[f]))
                touched.append(f)
        if "characters" in touched:
            sc.characters = core._normalize_scene_characters(sc.characters, sc.track.project)
        if "image_prompt" in touched:
            sc.prompt_stale = False
        db.commit()
        return f"кадр {sc.position} обновлён ({', '.join(touched)})" if touched else "нечего менять"
    if kind == "new_track":
        pr = db.get(Project, _aid(act.get("project_id"), default_project))
        if not pr or pr.owner_id != user.id:
            return "проект не найден"
        pos = max((t.position for t in pr.tracks), default=0) + 1
        t = core.Track(project_id=pr.id, position=pos,
                       title=str(fields.get("title") or "Новый трек")[:100],
                       comment=str(fields.get("comment") or "")[:2000])
        db.add(t)
        db.commit()
        return f"трек «{t.title}» создан"
    return "неизвестное действие"


def _owned_project(user: User, project) -> bool:
    return bool(project and project.owner_id == user.id)


@router.post("/api/chat/agent")
async def chat_agent(request: Request, user: User = Depends(current_user),
                     db: Session = Depends(db_session)):
    """Ассистент песочницы: понимает проект и предлагает следующий шаг.

    Стоит ноль токенов — идёт через подписочный шлюз, как «улучшить промпт».
    Ничего сам не запускает: возвращает предложения кнопками, нажатие делает
    человек. Автономно тратить чужие токены ассистент не должен.
    """
    body = await _body(request)
    text = str(body.get("text") or "").strip()
    scope = str(body.get("scope") or "project")
    if scope == "global":
        state = _global_state(db, user)
    else:
        state = _project_state(db, user, body.get("project_id") or 0)
        if not state:
            raise HTTPException(400, "проект не найден — выбери его в шапке")
    messages = [
        {"role": "system", "content": AGENT_SYSTEM},
        {"role": "user", "content":
            "СОСТОЯНИЕ ПРОЕКТА:\n"
            + json.dumps(state, ensure_ascii=False, indent=1)
            + "\n\nРЕПЛИКА ЧЕЛОВЕКА: "
            + (text or "(молчит — предложи, что делать дальше)")},
    ]
    try:
        out, provider = await _ask_gateway(messages)
    except Exception as e:  # noqa: BLE001
        raise HTTPException(502, f"ассистент не ответил: {str(e)[:200]}")
    raw = (out or "").strip()
    # Модель нет-нет да обернёт JSON в ```json — вырезаем забор, а не падаем.
    if raw.startswith("```"):
        raw = raw.split("```")[1] if "```" in raw[3:] else raw.strip("`")
        raw = raw[4:] if raw.lower().startswith("json") else raw
    try:
        data = json.loads(raw)
    except ValueError:
        # Не JSON — значит модель просто поговорила. Это не ошибка: отдаём
        # текст как есть, без кнопок.
        return {"reply": (out or "").strip()[:2000], "actions": [], "provider": provider}
    acts, applied = [], []
    for a in (data.get("actions") or [])[:6]:
        kind = str(a.get("kind") or "none")
        if kind in AGENT_FREE_KINDS:
            # Бесплатное и обратимое сервер делает сразу — за этим агент и
            # нужен. Отчёт уходит в ответ, чтобы человек видел, ЧТО менялось.
            try:
                applied.append(_agent_apply(db, user, a, _aid(body.get("project_id"))))
            except Exception as e:  # noqa: BLE001 — одна правка не рушит ответ
                applied.append(f"не вышло: {str(e)[:120]}")
            continue
        if kind not in AGENT_PAID_KINDS:
            continue
        acts.append({
            "kind": kind,
            "title": str(a.get("title") or "")[:40],
            "track_id": _aid(a.get("track_id")),
            "scene_id": _aid(a.get("scene_id")),
            "project_id": _aid(a.get("project_id"), _aid(body.get("project_id"))),
            "prompt": str(a.get("prompt") or "")[:2000],
        })
    return {"reply": str(data.get("reply") or "")[:2000], "actions": acts,
            "applied": applied, "provider": provider}


@router.get("/api/chat/targets")
def save_targets(user: User = Depends(current_user), db: Session = Depends(db_session)):
    """Куда можно положить результат — «Куда положить» в панели параметров.

    Только то, что реально существует у ЭТОГО человека: пустой список честно
    означает «проектов пока нет», а не выпадашку из четырёх мёртвых строк."""
    rows = (db.query(Project)
            .filter(Project.owner_id == user.id)
            .order_by(Project.id.desc()).limit(50).all())
    out = []
    for p in rows:
        out.append({
            "id": p.id, "name": p.name or "", "kind": p.kind or "album",
            "characters": [{"id": c.id, "name": c.name or ""} for c in p.characters],
            "tracks": [{
                "id": t.id, "title": t.title or "",
                "scenes": [{"id": sc.id, "position": sc.position,
                            "note": (sc.shot_note or sc.lyric_line or "")[:60],
                            "has_first": bool(sc.image_filename),
                            "has_last": bool(sc.image_last_filename)}
                           for sc in t.scenes],
            } for t in p.tracks],
        })
    return {"projects": out}


@router.post("/api/chats/messages/{message_id}/save-to")
async def save_to_project(message_id: int, request: Request,
                          user: User = Depends(current_user),
                          db: Session = Depends(db_session)):
    """Ручное «В проект» на карточке результата."""
    msg = _own_message(db, user, message_id)
    where = _save_media_to(db, user, msg, await _body(request))
    db.commit()
    return {"ok": True, **where}


def _save_media_to(db: Session, user: User, msg: ChatMessage, body: dict) -> dict:
    """Картинку из ленты — В РАБОТУ, со снятием срока хранения.

    Это и есть смысл мастерской ВНУТРИ этого сервиса, а не отдельным
    продуктом: удачный кадр не остаётся в переписке, а уезжает в проект.
    Четыре адреса, и все четыре — уже существующие сущности студии:
      character   — фото персонажа или его разворот;
      scene_ref   — референс кадра (с видом: вайб / стиль / локация / копия);
      scene_frame — сам кадр сцены (ДОБАВЛЯЕТСЯ, а не затирает — см. ниже);
      cover       — обложка проекта.
    Пятой сущности мастерская не заводит: она порождает медиа, а всё, что
    должно жить дольше ленты, уезжает в режим.

    Вызывается из ДВУХ мест: кнопкой на карточке результата и автоматически,
    когда в панели параметров выбрано «куда положить». Второе идёт сервером, а
    не клиентом, ровно потому, что обещание «положу в сцену 12» обязано
    пережить закрытую вкладку."""
    if not msg.media_filename or msg.kind != "image":
        raise HTTPException(400, "сохранить можно только картинку")
    target = str(body.get("target") or "character").strip()

    if target == "character":
        char_id = int(body.get("character_id") or 0)
        as_kind = "model" if str(body.get("as") or "photo") == "model" else "photo"
        ch = db.get(Character, char_id)
        if not ch or not _ctx.owned(user, ch.project):
            raise HTTPException(404, "персонаж не найден")
        fname = _copy_media(msg.media_filename, "char")
        _ctx.reg_file(db, fname, ch.project.owner_id, kind="photo",
                      project_id=ch.project_id)
        max_pos = max((p.position for p in ch.photos), default=0)
        db.add(CharacterPhoto(character_id=ch.id, position=max_pos + 1, filename=fname,
                              kind=as_kind, pose_kind="3d" if as_kind == "model" else "",
                              from_photos=0))
        where = {"character_id": ch.id, "as": as_kind}

    elif target == "scene_ref":
        scene = _own_scene_for_chat(db, user, body.get("scene_id"))
        fname = _copy_media(msg.media_filename, "sref")
        pos = 1 + max([r.position for r in scene.refs] or [0])
        db.add(SceneRef(scene_id=scene.id, position=pos, filename=fname,
                        kind=refs_mod.ref_norm_kind(body.get("kind"))))
        _ctx.reg_file(db, fname, scene.track.project.owner_id, kind="ref",
                      project_id=scene.track.project_id, track_id=scene.track_id,
                      scene_id=scene.id)
        where = {"scene_id": scene.id}

    elif target == "scene_frame":
        scene = _own_scene_for_chat(db, user, body.get("scene_id"))
        fname = _copy_media(msg.media_filename, "slice")
        # ДОБАВЛЯЕМ, А НЕ ЗАМЕНЯЕМ — тот же закон, что у ячеек листа
        # раскадровки (main.py, apply_sheet_cells): затирая занятый кадр, мы
        # уносили с диска работу, за которую человек уже заплатил токенами.
        slot = str(body.get("slot") or "").strip()
        if slot == "last" or (not slot and scene.image_filename and not scene.image_last_filename):
            if scene.image_last_filename:
                raise HTTPException(400, "последний кадр сцены уже занят")
            scene.image_last_filename = fname
            put = "last"
        elif not scene.image_filename:
            scene.image_filename = fname
            scene.image_status = "done"
            scene.image_error = ""
            put = "first"
        else:
            pos = 1 + max([r.position for r in scene.refs] or [0])
            db.add(SceneRef(scene_id=scene.id, position=pos, filename=fname, kind="vibe"))
            put = "ref"
        _ctx.reg_file(db, fname, scene.track.project.owner_id, kind="frame",
                      project_id=scene.track.project_id, track_id=scene.track_id,
                      scene_id=scene.id)
        where = {"scene_id": scene.id, "slot": put}

    elif target == "cover":
        pr = db.get(Project, int(body.get("project_id") or 0))
        if not pr or not _ctx.owned(user, pr):
            raise HTTPException(404, "проект не найден")
        fname = _copy_media(msg.media_filename, "cover")
        pr.cover_filename = fname
        _ctx.reg_file(db, fname, pr.owner_id, kind="cover", project_id=pr.id)
        where = {"project_id": pr.id}

    else:
        raise HTTPException(400, "неизвестный адрес сохранения")

    # Сообщение сохранено в работу — срок хранения снимаем: файл теперь не
    # мусор переписки, а часть проекта.
    msg.expires_at = None
    return {"target": target, **where}


def _auto_save(db: Session, msg: ChatMessage) -> None:
    """«Куда положить» из панели параметров. Ошибку сюда пускать нельзя:
    кадр уже сгенерирован и оплачен, и не доехавший адрес — повод написать об
    этом в сообщении, а не потерять картинку вместе с деньгами."""
    try:
        params = json.loads(msg.params_json or "{}")
    except ValueError:
        return
    spec = params.get("save_to")
    if not isinstance(spec, dict) or not spec.get("target"):
        return
    chat = db.get(Chat, msg.chat_id)
    owner = db.get(User, chat.owner_id) if chat else None
    if not owner:
        return
    try:
        params["saved_to"] = _save_media_to(db, owner, msg, spec)
    except Exception as e:  # noqa: BLE001
        params["save_error"] = str(getattr(e, "detail", e))[:200]
    params.pop("save_to", None)
    msg.params_json = json.dumps(params, ensure_ascii=False)


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
        # ЗАКРЕПЛЁННОЕ НЕ ПОДМЕТАЕМ. Звезда в интерфейсе прямо обещает «в
        # избранном файл не удаляется по сроку» — без этих двух условий она
        # была бы враньём: сортировка меняется, а файл всё равно исчезает.
        rows = (db.query(ChatMessage)
                .join(Chat, Chat.id == ChatMessage.chat_id)
                .filter(ChatMessage.media_filename != "",
                        ChatMessage.expires_at.isnot(None),
                        ChatMessage.expires_at < now(),
                        ChatMessage.pinned.is_(False),
                        Chat.pinned.is_(False))
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
