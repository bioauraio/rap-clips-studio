"""rap-clips-studio — личный инструмент: раскадровка клипа на 8 рэп-треков.

Не часть организма BIOAURA: свой пароль вместо SSO, своя SQLite, свой домен.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import logging
import math
import os
import re
import secrets
import shutil
import subprocess
import time
import uuid

from fastapi import Depends, FastAPI, Form, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from itsdangerous import BadSignature, URLSafeTimedSerializer
from sqlalchemy import func
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

import claude
import mediagen
import stripe_pay
from db import (
    AttributePhoto, Character, CharacterAttribute, CharacterPhoto, FileOwner,
    Payout, ProcessedPayment, Project, RefEvent, Scene, SceneRef, SessionLocal,
    Track, User, init_db, now,
)

log = logging.getLogger("rapclips")
logging.basicConfig(level=logging.INFO, format="%(asctime)s [rapclips] %(message)s")

APP_PASSWORD = os.environ.get("APP_PASSWORD", "")
SECRET_KEY = os.environ.get("SECRET_KEY", "")
UPLOAD_DIR = os.environ.get("UPLOAD_DIR", "/data/uploads")
COOKIE_NAME = "rc_session"  # легаси-кука владельца ({"ok": True}) — живёт дальше
COOKIE_MAX_AGE = 60 * 60 * 24 * 30  # 30 дней — прежний срок легаси-куки
QV_COOKIE = "qv_session"  # новая кука публичного сервиса: {"uid": user.id}
QV_MAX_AGE = 60 * 60 * 24 * 180  # 180 дней — гости не должны терять проекты

if not APP_PASSWORD or not SECRET_KEY:
    raise RuntimeError("заданы не все переменные окружения: APP_PASSWORD, SECRET_KEY")

os.makedirs(UPLOAD_DIR, exist_ok=True)
signer = URLSafeTimedSerializer(SECRET_KEY, salt="rapclips-session")

app = FastAPI(title=os.environ.get("BRAND_NAME", "lolq.ai"))
init_db()


def db_session():
    session = SessionLocal()
    try:
        yield session
    finally:
        session.close()


# ─────────────────── пользователи: пароли и сессии ───────────────────

def _hash_password(password: str) -> str:
    """pbkdf2 из стандартной библиотеки: соль хранится в самом хэше."""
    iterations = 200_000
    salt = secrets.token_hex(16)
    dk = hashlib.pbkdf2_hmac("sha256", password.encode(), bytes.fromhex(salt), iterations)
    return f"pbkdf2${iterations}${salt}${dk.hex()}"


def _verify_password(password: str, stored: str) -> bool:
    try:
        algo, iterations, salt, digest = stored.split("$")
        if algo != "pbkdf2":
            return False
        dk = hashlib.pbkdf2_hmac("sha256", password.encode(), bytes.fromhex(salt), int(iterations))
        return hmac.compare_digest(dk.hex(), digest)
    except (ValueError, TypeError):
        return False


def _resolve_user(request: Request, db: Session) -> User | None:
    """qv_session ({"uid": …}) или легаси rc_session ({"ok": True} → админ)."""
    token = request.cookies.get(QV_COOKIE)
    if token:
        try:
            data = signer.loads(token, max_age=QV_MAX_AGE)
            user = db.get(User, int(data.get("uid") or 0))
            if user:
                return user
        except (BadSignature, ValueError, TypeError, AttributeError):
            pass
    legacy = request.cookies.get(COOKIE_NAME)
    if legacy:
        try:
            data = signer.loads(legacy, max_age=COOKIE_MAX_AGE)
            if isinstance(data, dict) and data.get("ok"):
                return _admin_user(db)
        except BadSignature:
            pass
    return None


def current_user(request: Request, db: Session = Depends(db_session)) -> User:
    user = _resolve_user(request, db)
    if not user:
        raise HTTPException(401, "не авторизован")
    return user


def _admin_user(db: Session) -> User | None:
    return db.query(User).filter(User.is_admin.is_(True)).order_by(User.id).first()


def _bootstrap_users() -> None:
    """Одноразовый init при старте: админ-владелец, усыновление легаси-проектов
    без owner_id и регистрация легаси-файлов в FileOwner. Дешёвый проход по
    SQLite; повторные запуски ничего не трогают."""
    db = SessionLocal()
    try:
        admin = _admin_user(db)
        if not admin:
            admin = User(name="владелец", is_admin=True, gen_points=10**9)
            db.add(admin)
            db.commit()
            db.refresh(admin)
        for p in db.query(Project).filter(Project.owner_id.is_(None)).all():
            p.owner_id = admin.id
        db.commit()

        # Легаси-файлы: всё, что упомянуто в БД, приписываем владельцу проекта.
        known = {row.filename for row in db.query(FileOwner).all()}

        def reg(fname: str, owner_id: int | None) -> None:
            if fname and fname not in known:
                db.add(FileOwner(filename=fname, user_id=owner_id or admin.id))
                known.add(fname)

        for p in db.query(Project).all():
            reg(p.cover_filename, p.owner_id)
            for c in p.characters:
                for ph in c.photos:
                    reg(ph.filename, p.owner_id)
                for attr in c.attributes:
                    for aph in attr.photos:
                        reg(aph.filename, p.owner_id)
            for t in p.tracks:
                for f in (t.audio_filename, t.cover_filename,
                          t.storyboard_filename, t.clip_filename):
                    reg(f, p.owner_id)
                for s in t.scenes:
                    for f in (s.image_filename, s.image_last_filename,
                              s.video_filename, s.audio_filename):
                        reg(f, p.owner_id)
        db.commit()
    finally:
        db.close()


_bootstrap_users()


def _reg_file(db: Session, filename: str, owner_id: int | None) -> None:
    """Каждый создаваемый файл приписывается владельцу (INSERT OR REPLACE):
    без записи в FileOwner файл из /api/media увидит только админ."""
    if not filename:
        return
    db.merge(FileOwner(filename=filename, user_id=int(owner_id or 0)))


def _check_file_owner(db: Session, user: User, fname: str) -> None:
    """Файлы приватны: чужой файл — 404 (не раскрываем существование).
    Файл без записи (легаси) отдаётся только админу."""
    if user.is_admin:
        return
    fo = db.get(FileOwner, fname)
    if not fo or fo.user_id != user.id:
        raise HTTPException(404, "файл не найден")


# ─────────────────────── деньги: доллары, рубли, курс ───────────────────────
# Сервис международный, поэтому правда о цене — ДОЛЛАРЫ в центах. Рубли для
# ЮKassa считаются от них по курсу из env: ходить за курсом в интернет нельзя —
# биллинг не должен зависеть от чужого API, а цена не должна прыгать между
# показом витрины и нажатием «оплатить». Нужен свой рублёвый ценник — задай
# PRICE_PRO_RUB / PRICE_PRO_MAX_RUB / PRICE_STUDIO_RUB (в рублях), он победит курс.
USD_RUB = max(1, int(float(os.environ.get("USD_RUB", "100"))))
YEAR_DISCOUNT_PCT = max(0, min(90, int(os.environ.get("YEAR_DISCOUNT_PCT", "20"))))
PLAN_DAYS = int(os.environ.get("PLAN_DAYS", "30"))
PLAN_YEAR_DAYS = int(os.environ.get("PLAN_YEAR_DAYS", "365"))


def _year_cents(usd_cents: int) -> int:
    """Годовой ценник: 12 месяцев минус скидка, округлённые до целых долларов —
    $2870, а не $2870.40: дробный годовой ценник выглядит как ошибка вёрстки."""
    raw = usd_cents * 12 * (100 - YEAR_DISCOUNT_PCT) // 100
    return (raw + 50) // 100 * 100


def _rub_kopeks(key: str, usd_cents: int) -> int:
    """Рублёвый ценник: явный из env (PRICE_<KEY>_RUB) или пересчёт по курсу."""
    env = os.environ.get(f"PRICE_{key.upper()}_RUB", "").strip()
    if env:
        return int(round(float(env) * 100))
    return usd_cents * USD_RUB


# Тарифы сервиса. free — целиком на подписках владельца (ChatGPT рисует кадры,
# Grok оживляет), и стоит нам ноль. Платные открывают Nano Banana для кадров и
# Seedance/Kling для видео — за них мы платим по API kie.ai.
#
# ВАЖНО про экономику. Очко привязано к деньгам ровно одной константой
# POINT_USD (ниже): столько СЕБЕСТОИМОСТИ мы кладём в одно очко. Отсюда:
#   норма тарифа × POINT_USD = максимум, который тариф может стоить нам за месяц.
# Это худший случай — человек спускает все очки на самый дорогой движок. По
# нынешней сетке он равен 41-44 % выручки тарифа. Маржа положительная, но
# ЭТО НЕ 30 %: движки уровня Seedance 2.5 столько стоят на самом деле, и
# подгонять цифры «чтобы красиво» здесь нельзя.
#
# video — СЕМЕЙСТВА движков, которые видит фронт (grok|seedance|kling).
# engines — какая конкретная модель стоит за семейством на ЭТОМ тарифе:
# «Seedance» у PRO и у PRO MAX — разные деньги и разное качество.
# extra_engines — модели, доступные тарифу сверх дефолтных (явным выбором;
# фронт научится их показывать, см. backend/models_patch.md).
# image_engine — движок КАДРОВ по умолчанию. Нет ключа kie — молча работаем
# на шлюзе, и /api/providers честно отдаёт, что реально включилось.
PLANS = {
    "free": {
        "title": "FREE", "usd_cents": 0, "points": 120,
        "video": ["grok"], "engines": {"grok": "grok"}, "extra_engines": [],
        # FREE обязан остаться на шлюзовых кадрах: Nano Banana для всех
        # означала бы $3.60 живых денег за каждого зарегистрировавшегося.
        "image_engine": "chatgpt",
        "priority": False, "badge": "",
        "note": "One full 3-minute clip on us — Grok engine",
        "features": [
            "120 points — enough for one 3-minute clip",
            "Grok engine: animates the first frame of every scene",
            "Story, storyboard, characters and one-click assembly",
        ],
    },
    "pro": {
        "title": "PRO", "usd_cents": int(os.environ.get("PRICE_PRO_USD", "20")) * 100,
        # 660 очков = ровно один трёхминутный клип на Seedance 2 Mini
        # (30 сцен × 22 очка). Раньше было 700 очков при цене сцены 10 —
        # то есть два клипа по $1.23 за сцену, это −$50 на каждом подписчике.
        "points": 660,
        "video": ["grok", "seedance"],
        "engines": {"grok": "grok", "seedance": "seedance-2-mini"},
        "extra_engines": [],
        "image_engine": "chatgpt",
        "priority": False, "badge": "",
        "note": "Seedance 2 Mini — motion between your first and last frame",
        "features": [
            "660 points every month — one full 3-minute clip on Seedance",
            "Seedance 2 Mini: video interpolated between first and last frame",
            "Unused points roll over, up to two monthly norms",
        ],
    },
    "pro_max": {
        "title": "PRO MAX", "usd_cents": int(os.environ.get("PRICE_PRO_MAX_USD", "100")) * 100,
        # 2400 → 3400 очков (+42 %). Больше дать нельзя: 3400 × POINT_USD =
        # $42.5 предельной себестоимости на $100 выручки.
        "points": 3400,
        "video": ["grok", "seedance", "kling"],
        "engines": {"grok": "grok", "seedance": "seedance-2-5", "kling": "kling-3.0-pro"},
        # Дешёвые сильные модели тарифу тоже открыты — выбором, а не по умолчанию.
        "extra_engines": ["seedance-2-mini", "seedance-2-5-480", "kling-3.0", "minimax-h3"],
        "image_engine": "nano-banana-pro",
        "priority": False, "badge": "Most popular",
        "note": "Nano Banana Pro frames, Seedance 2.5 and Kling 3.0 Pro",
        "features": [
            "3400 points every month",
            "Nano Banana Pro draws the frames — native vertical 2K, up to 8 references",
            "Seedance 2.5 and Kling 3.0 Pro unlocked",
            "Unused points roll over, up to two monthly norms",
        ],
    },
    "studio": {
        "title": "STUDIO", "usd_cents": int(os.environ.get("PRICE_STUDIO_USD", "299")) * 100,
        # 10500 очков = два полных клипа на Seedance 2.5 (62 сцены) или шесть
        # на Kling 3.0 Pro. Целый клип на самой дорогой модели физически
        # помещается только сюда.
        "points": 10500,
        "video": ["grok", "seedance", "kling"],
        "engines": {"grok": "grok", "seedance": "seedance-2-5", "kling": "kling-3.0-pro"},
        "extra_engines": ["seedance-2-mini", "seedance-2-0", "seedance-2-5-480",
                          "kling-3.0", "minimax-h3"],
        "image_engine": "nano-banana-pro",
        # priority — пока ЯРЛЫК ВИТРИНЫ: очередь генераций у нас одна и
        # однопоточная (см. _run_all_videos). Реальный приоритет = отдельная
        # задача; не обещай в интерфейсе больше, чем этот флаг делает.
        "priority": True, "badge": "For labels",
        "note": "Album-scale volume on every engine",
        "features": [
            "10500 points every month — two full clips on Seedance 2.5",
            "Every engine, including Seedance 2.0 and 480p Seedance 2.5",
            "Nano Banana Pro frames, priority processing and direct support",
        ],
    },
}
# Легаси-поле старого кода и старых записей: имя модели Seedance у тарифа.
for _pid, _p in PLANS.items():
    _p["seedance_model"] = _p["engines"].get("seedance", "")
# Производные ценники считаем один раз на старте: год = месяц ×12 −20 %,
# рубли = доллары × курс. Оба ценника лежат рядом, чтобы витрина и платёжка
# не считали цену каждая по-своему.
for _pid, _p in PLANS.items():
    _p["usd_year_cents"] = _year_cents(_p["usd_cents"])
    _p["rub_kopeks"] = _rub_kopeks(_pid, _p["usd_cents"])
    _p["rub_year_kopeks"] = _rub_kopeks(f"{_pid}_year", _p["usd_year_cents"])
    # Легаси-поле для старого фронта, который рисует «₽»: цена в рублях.
    _p["price"] = _p["rub_kopeks"] // 100

# Пакеты очков (докупка сверх подписки).
#
# ПОЧИНЕНА ДЫРА. Было: очко в пакете 1.13-2.25¢ против 4.2¢ в PRO MAX — вчетверо
# дешевле, да ещё и без подписки. В таком виде подписку выгоднее было не
# покупать вообще. Стало: САМОЕ дешёвое пакетное очко (3.19¢) дороже САМОГО
# дорогого подписочного (3.03¢ у PRO), то есть пакет проигрывает любой
# подписке по цене и остаётся тем, чем должен быть — удобством «добрать
# сейчас», а не способом обойти тариф.
TOPUP_PACKS = {
    "p400": {"points": 400, "usd_cents": 1500, "badge": ""},
    "p1000": {"points": 1000, "usd_cents": 3600, "badge": ""},
    "p2500": {"points": 2500, "usd_cents": 8700, "badge": "Popular"},
    "p6000": {"points": 6000, "usd_cents": 19900, "badge": ""},
    "p15000": {"points": 15000, "usd_cents": 47900, "badge": "Best value"},
}
# Вторая половина той же дыры: пакеты продавались кому угодно, включая FREE.
# Теперь докупка — только при живой платной подписке.
TOPUP_REQUIRES_PLAN = os.environ.get("TOPUP_REQUIRES_PLAN", "1") not in ("0", "false", "no")
_BASE_PER_POINT = TOPUP_PACKS["p400"]["usd_cents"] / TOPUP_PACKS["p400"]["points"]
for _kid, _k in TOPUP_PACKS.items():
    _k["rub_kopeks"] = _rub_kopeks(_kid, _k["usd_cents"])
    # Выгода относительно самого мелкого пакета — витрине нужен ярлык «−15 %».
    _k["save_pct"] = int(round(100 - 100 * (_k["usd_cents"] / _k["points"]) / _BASE_PER_POINT))

# ───────────────────────── сколько стоит работа ─────────────────────────
# ЕДИНСТВЕННАЯ константа, связывающая очки с деньгами: сколько себестоимости
# лежит в одном очке. Всё остальное считается из неё и из долларовых цен
# движков в mediagen.VIDEO_ENGINES/IMAGE_ENGINES — цена в очках физически не
# может разойтись с тем, что мы платим kie.ai.
POINT_USD = float(os.environ.get("POINT_USD", "0.0125"))
# Шлюзы владельца стоят нам ноль, но даром отдавать их нельзя: без ценника
# перерисовка кадров становится бесконечным насосом по чужой подписке.
# 2 очка — символическая плата ровно за это.
GATEWAY_POINTS = int(os.environ.get("GATEWAY_POINTS", "2"))
SCENE_SEC = 6              # средняя длина сцены, из claude.py


def _points_of_usd(usd: float) -> int:
    """Доллары себестоимости → очки. Округление ВВЕРХ: недобор очка — это
    наши деньги, а не пользовательские."""
    if usd <= 0:
        return GATEWAY_POINTS
    return max(GATEWAY_POINTS, math.ceil(usd / POINT_USD))


# Цена ПАРЫ кадров сцены (первый + последний) по движку картинок.
FRAME_COST = {
    eid: _points_of_usd(2 * mediagen.image_engine_usd(eid))
    for eid in mediagen.IMAGE_ENGINES
}
# Цена ВИДЕО сцены (6 секунд) по движку видео.
VIDEO_COST = {
    eid: _points_of_usd(mediagen.video_engine_usd(eid, SCENE_SEC))
    for eid in mediagen.VIDEO_ENGINES
}
# Легаси-карта «движок → цена сцены целиком» для витрины и старых вызовов:
# кадры считаются по шлюзу (базовый случай), видео — по своему движку.
SCENE_COST = {
    eid: FRAME_COST["chatgpt"] + VIDEO_COST[eid] for eid in VIDEO_COST
}
FRAMES_COST = FRAME_COST["chatgpt"]  # аванс за кадры на шлюзе (легаси-имя)
# Текстовые шаги идут через нашу подписку Claude и стоят нам ноль — берём за них
# ноль и мы: иначе бесплатный тариф не доживал до первого клипа, а именно первый
# собранный клип и продаёт сервис.
COST_STORY = 0
COST_SCENES = 0
COST_STORYBOARD = 2        # лист раскадровки — картинка
COST_CHARACTER_MODEL = 2   # разворот персонажа — картинка
CLIP_SCENES = int(os.environ.get("CLIP_SCENES", "30"))  # клип 3 минуты ≈ 30 сцен по 6 сек


def _plan_of(user: "User") -> str:
    if user.is_admin:
        return "pro_max"
    return user.plan if user.plan in PLANS else "free"


def _plan_image_engine(user: "User | None") -> str:
    """Движок КАДРОВ этого человека: дефолт тарифа, опущенный до реально
    живого. Нет KIE_API_KEY — тихо работаем на шлюзе (сцена не должна падать
    из-за ненастроенного агрегатора), но врать об этом наверх нельзя."""
    plan = PLANS[_plan_of(user)] if user else PLANS["free"]
    return mediagen.resolve_image_engine(plan.get("image_engine") or "chatgpt")


def _plan_engine_ids(plan_id: str) -> list[str]:
    """Все движки видео тарифа: дефолтные по семействам + явные extra."""
    plan = PLANS.get(plan_id) or PLANS["free"]
    out = list(plan["engines"].values())
    for eid in plan.get("extra_engines", []):
        if eid not in out:
            out.append(eid)
    return [e for e in out if e in mediagen.VIDEO_ENGINES]


def _plan_video_engine(user: "User | None", provider: str, engine: str = "") -> str:
    """Семейство + (необязательный) явный движок → что реально запустим.

    Явный движок принимается, только если он открыт тарифом: иначе человек с
    FREE попросил бы Seedance 2.5 строкой в запросе."""
    plan_id = _plan_of(user) if user else "free"
    plan = PLANS[plan_id]
    engine = (engine or "").strip()
    if engine and engine in _plan_engine_ids(plan_id):
        return engine
    return plan["engines"].get(provider) or plan["engines"].get("grok", "grok")


def _plan_work_cost(plan_id: str) -> int:
    """Цена сцены на РАБОЧЕЙ лошадке тарифа — самом дешёвом платном движке.
    Именно по ней честно считать «сколько клипов выйдет»: самый дорогой
    движок тарифа существует для отдельных кадров, а не для целого клипа."""
    engines = _plan_engines(plan_id)
    paid = {eid: c for eid, c in engines.items()
            if mediagen.VIDEO_ENGINES.get(eid, {}).get("paid")}
    if paid:
        return min(paid.values())
    return min(engines.values(), default=SCENE_COST["grok"])


def _plan_engines(plan_id: str) -> dict:
    """Движки тарифа с ПОЛНОЙ ценой сцены на этом тарифе (кадры + видео):
    {"seedance-2-5": 167, ...}. Одно место правды и для витрины, и для оценки
    «сколько клипов выйдет на тарифе»."""
    plan = PLANS.get(plan_id) or PLANS["free"]
    frames = FRAME_COST.get(plan.get("image_engine") or "chatgpt", FRAMES_COST)
    return {eid: frames + VIDEO_COST[eid] for eid in _plan_engine_ids(plan_id)}


def _image_cost(user: "User", engine: str = "") -> int:
    """Цена ОДНОЙ служебной картинки (лист раскадровки, моделька персонажа).
    Раньше была плоской двойкой — на Nano Banana Pro такая картинка стоит нам
    $0.09, и плоская цена превращала витрину персонажей в дыру в кошельке."""
    eng = engine or _plan_image_engine(user)
    return max(2, _points_of_usd(mediagen.image_engine_usd(eng)))


def _frames_cost(user: "User", scene: "Scene | None" = None) -> int:
    """Цена пары кадров сцены. Если кадры уже нарисованы — по ТОМУ движку,
    которым их реально нарисовали: иначе смена тарифа между кадрами и видео
    ломала бы добор до цены сцены."""
    engine = (scene.image_engine if scene else "") or _plan_image_engine(user)
    return FRAME_COST.get(engine, FRAMES_COST)


def _scene_cost(user: "User", provider: str, scene: "Scene | None" = None,
                engine: str = "") -> int:
    """Полная цена сцены: кадры своим движком + видео своим движком."""
    vid = _plan_video_engine(user, provider, engine or (scene.video_engine if scene else ""))
    return _frames_cost(user, scene) + VIDEO_COST.get(vid, VIDEO_COST["grok"])


def _allowed_provider(user: "User", wanted: str) -> str:
    """Тариф решает, чем рисуем видео. Недоступный провайдер тихо понижаем
    до разрешённого, чтобы кнопка работала всегда, а не падала с ошибкой."""
    allowed = PLANS[_plan_of(user)]["video"]
    if wanted in allowed:
        return wanted
    for p in ("seedance", "kling", "grok"):
        if p in allowed:
            return p
    return "grok"


class ApiError(Exception):
    """Ошибка с машиночитаемым кодом: фронту нужно различать «платежи не
    подключены» и «нет такого тарифа», а не разбирать текст сообщения."""

    def __init__(self, status: int, code: str, message: str = "", **extra):
        super().__init__(message or code)
        self.status, self.code, self.message, self.extra = status, code, message, extra


@app.exception_handler(ApiError)
async def _api_error_handler(request: Request, exc: ApiError) -> JSONResponse:
    body = {"error": exc.code, "detail": exc.message or exc.code}
    body.update(exc.extra)
    return JSONResponse(status_code=exc.status, content=body)


class NotEnoughPoints(Exception):
    """Очки кончились. Не текст «напиши владельцу сервиса», а структура: по ней
    фронт открывает витрину с нужной суммой, а не показывает тупик.
    Ответ 402: {"error":"not_enough_points","need":…,"have":…,"plan":…}."""

    def __init__(self, need: int, have: int, plan: str, what: str = ""):
        super().__init__("not_enough_points")
        self.need, self.have, self.plan, self.what = int(need), int(have), plan, what


@app.exception_handler(NotEnoughPoints)
async def _not_enough_points_handler(request: Request, exc: NotEnoughPoints) -> JSONResponse:
    return JSONResponse(
        status_code=402,
        content={
            "error": "not_enough_points",
            "need": exc.need,
            "have": exc.have,
            "short": max(0, exc.need - exc.have),
            "plan": exc.plan,
            # exc.what — внутренняя метка для логов (по-русски), наружу не идёт:
            # интерфейс английский, и это не текст для человека.
            # detail — для клиентов, которые по привычке читают это поле.
            "detail": f"Not enough points: this step costs {exc.need}, "
                      f"you have {exc.have}.",
        },
    )


def _take_points(db: Session, user: User, points: int) -> bool:
    """Тихое списание: False — не хватило. Нужно фоновым шагам (супергенерация
    доводит счёт по факту), которым некуда бросать HTTP-ошибку."""
    if user.is_admin or points <= 0:
        return True
    if int(user.gen_points or 0) < points:
        return False
    user.gen_points = int(user.gen_points) - points
    db.commit()
    return True


def _charge(db: Session, user: User, points: int, what: str) -> None:
    """Списание очков генерации В МОМЕНТ постановки задачи (не в треде):
    генерации идут через подписки владельца, лимит защищает его кошелёк."""
    if user.is_admin or points <= 0:
        return
    if not _take_points(db, user, points):
        raise NotEnoughPoints(points, int(user.gen_points or 0), _plan_of(user), what)
    log.info("user %s: −%s очков за %s (осталось %s)", user.id, points, what, user.gen_points)


def _scene_charge(db: Session, user: User, scene: "Scene", cost: int, what: str) -> None:
    """Списать за сцену ДО её цены, а не заново.

    Кадры взяли аванс, видео добирает разницу до цены движка, перегенерация
    уже оплаченного не стоит ничего. Так «кадры входят в цену сцены»
    превращается в честную арифметику, а не в двойную оплату одной сцены."""
    paid = int(scene.charged_points or 0)
    if cost <= paid:
        return
    _charge(db, user, cost - paid, what)
    scene.charged_points = cost
    db.commit()


def _scenes_charge(db: Session, user: User, scenes: list, cost_of, what: str) -> int:
    """То же для пачки сцен: одно списание на весь пакет (и один отказ, если
    очков не хватило), потом отметки на сценах."""
    rows, total = [], 0
    for s in scenes:
        cost = cost_of(s)
        paid = int(s.charged_points or 0)
        if cost > paid:
            total += cost - paid
            rows.append((s, cost))
    if total:
        _charge(db, user, total, what)
    for s, cost in rows:
        s.charged_points = cost
    if rows:
        db.commit()
    return total


# ─────────── партнёрка: промокоды, рефералы, вознаграждения ───────────
# Проценты и минималку правим в infra/.env, а не в коде: их крутят под акции.
# REF_DISCOUNT_PCT — скидка приглашённому на ПЕРВУЮ оплату, REF_REWARD_PCT —
# доля амбассадора с КАЖДОГО платежа его реферала (включая автопродления).
REF_DISCOUNT_PCT = max(0, min(90, int(os.environ.get("REF_DISCOUNT_PCT", "10"))))
REF_REWARD_PCT = max(0, min(100, int(os.environ.get("REF_REWARD_PCT", "30"))))
REF_MIN_PAYOUT_KOPEKS = max(0, int(float(os.environ.get("REF_MIN_PAYOUT", "1000")) * 100))
# Без похожих символов (O/0 и I/1): код диктуют голосом и переписывают от руки.
REF_ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"


def _norm_code(code: str) -> str:
    """Код приезжает и из поля ввода, и из вставленной целиком ссылки —
    приводим к одному виду, чтобы «qlolapp.art/?ref=k7m2xt » тоже сработало."""
    raw = str(code or "").strip().upper()
    if "REF=" in raw:
        raw = raw.split("REF=", 1)[1]
    return "".join(c for c in raw if c in REF_ALPHABET)[:12]


def _ref_link(code: str) -> str:
    return f"{PUBLIC_BASE_URL}/?ref={code}" if code else ""


def _new_ref_code(db: Session) -> str:
    """Уникальный промокод. Коллизия увела бы чужих рефералов, поэтому каждый
    кандидат проверяется по базе, а не «вероятность мала — и ладно»."""
    for _ in range(50):
        code = "".join(secrets.choice(REF_ALPHABET) for _ in range(6))
        if not db.query(User).filter(User.ref_code == code).first():
            return code
    raise HTTPException(500, "не удалось выдать промокод, попробуй ещё раз")


def _find_ambassador(db: Session, code: str) -> "User | None":
    """Владелец кода. Пустой код не ищем: у всех обычных юзеров ref_code = ''
    и поиск по пустой строке вернул бы случайного человека."""
    code = _norm_code(code)
    if not code:
        return None
    return db.query(User).filter(User.ref_code == code,
                                 User.is_ambassador.is_(True)).first()


def _attach_ref(db: Session, user: "User | None", code: str) -> "User | None":
    """Закрепить пользователя за амбассадором — навсегда.

    Первое касание побеждает: уже закреплённого не переклеиваем, иначе
    реферала уводил бы тот, кто последним подсунул ссылку. Своим же кодом
    закрепиться нельзя — это была бы вечная скидка самому себе."""
    if not user or user.referred_by:
        return None
    amb = _find_ambassador(db, code)
    if not amb or amb.id == user.id:
        return None
    user.referred_by = amb.id
    db.add(RefEvent(ambassador_id=amb.id, referral_id=user.id, kind="signup"))
    db.commit()
    log.info("партнёрка: юзер %s закреплён за амбассадором %s (код %s)",
             user.id, amb.id, amb.ref_code)
    return amb


def _ref_stats(db: Session, user: User) -> dict:
    """Единственное место, где считаются деньги партнёрки.

    Начислено — счётчик на юзере, выплачено и «в заявке» — суммы по таблице
    заявок. Поэтому одни и те же деньги нельзя заказать дважды (сумма заявки
    в new сразу выпадает из доступного), а отклонение заявки возвращает их
    само собой, без обратных проводок."""
    rows = (db.query(Payout.status, func.coalesce(func.sum(Payout.amount_kopeks), 0))
            .filter(Payout.ambassador_id == user.id)
            .group_by(Payout.status).all())
    by_status = {str(s): int(v or 0) for s, v in rows}
    accrued = int(user.ref_balance_kopeks or 0)
    reserved = by_status.get("new", 0)
    paid = by_status.get("paid", 0)
    invited = db.query(func.count(User.id)).filter(User.referred_by == user.id).scalar()
    buyers = (db.query(func.count(func.distinct(RefEvent.referral_id)))
              .filter(RefEvent.ambassador_id == user.id,
                      RefEvent.kind == "payment").scalar())
    turnover = (db.query(func.coalesce(func.sum(RefEvent.amount_kopeks), 0))
                .filter(RefEvent.ambassador_id == user.id,
                        RefEvent.kind == "payment").scalar())
    return {
        "invited": int(invited or 0),
        "buyers": int(buyers or 0),
        "turnover_kopeks": int(turnover or 0),
        "accrued_kopeks": accrued,
        "paid_kopeks": paid,
        "reserved_kopeks": reserved,
        "available_kopeks": max(0, accrued - paid - reserved),
    }


def _ref_first_payment(db: Session, user: User) -> bool:
    """Первая ли это оплата приглашённого. Считаем по ленте событий, а не по
    флагу на юзере: иначе появилось бы второе место с правдой о платежах."""
    return not db.query(RefEvent).filter(RefEvent.referral_id == user.id,
                                         RefEvent.kind == "payment").first()


def _ref_reward(db: Session, buyer: User, amount_kopeks: int, payment_id: str) -> None:
    """Начислить амбассадору долю с платежа его реферала.

    Вебхук ЮKassa штатно приходит по нескольку раз на один платёж, поэтому
    начисляем строго один раз: сначала ищем событие по payment_id, а гонку
    двух одновременных вебхуков ловит UNIQUE на колонке.

    Событие пишем даже при нулевой доле (REF_REWARD_PCT=0): по нему считается
    «первая ли это оплата», и без записи скидка приглашённому давалась бы
    заново на каждом платеже."""
    if not buyer.referred_by or amount_kopeks <= 0:
        return
    pay_id = str(payment_id or "").strip()
    if not pay_id:
        return
    amb = db.get(User, buyer.referred_by)
    if not amb or amb.id == buyer.id:
        return
    # Ищем и по «голому» id: платежи, начатые до перехода на префикс провайдера,
    # записаны в ленту без него, и второй раз платить за них нельзя.
    legacy = pay_id.split(":", 1)[1] if ":" in pay_id else pay_id
    if db.query(RefEvent).filter(RefEvent.payment_id.in_([pay_id, legacy])).first():
        return
    reward = amount_kopeks * REF_REWARD_PCT // 100
    db.add(RefEvent(ambassador_id=amb.id, referral_id=buyer.id, kind="payment",
                    amount_kopeks=amount_kopeks, reward_kopeks=reward,
                    payment_id=pay_id))
    amb.ref_balance_kopeks = int(amb.ref_balance_kopeks or 0) + reward
    try:
        db.commit()
    except IntegrityError:
        # Дубль вебхука успел вставить событие первым — второй раз не платим.
        db.rollback()
        return
    log.info("партнёрка: +%s коп. амбассадору %s с платежа %s реферала %s",
             reward, amb.id, pay_id, buyer.id)


# ─────────── владение: каждая сущность прослеживается до владельца ───────────

def _owned(user: User, project: Project | None) -> bool:
    return bool(project) and (user.is_admin or project.owner_id == user.id)


def _own_project(db: Session, user: User, project_id: int) -> Project:
    project = db.get(Project, project_id)
    if not _owned(user, project):
        # 404, а не 403 — чужим не раскрываем даже факт существования.
        raise HTTPException(404, "проект не найден")
    return project


def _own_track(db: Session, user: User, track_id: int) -> Track:
    track = db.get(Track, track_id)
    if not track or not _owned(user, track.project):
        raise HTTPException(404, "трек не найден")
    return track


def _own_scene(db: Session, user: User, scene_id: int) -> Scene:
    scene = db.get(Scene, scene_id)
    if not scene or not _owned(user, scene.track.project):
        raise HTTPException(404, "кадр не найден")
    return scene


def _own_scene_ref(db: Session, user: User, ref_id: int) -> SceneRef:
    """Владение референсом кадра — по цепочке сцена → трек → проект."""
    ref = db.get(SceneRef, ref_id)
    if not ref or not _owned(user, ref.scene.track.project):
        raise HTTPException(404, "референс не найден")
    return ref


def _own_character(db: Session, user: User, char_id: int) -> Character:
    ch = db.get(Character, char_id)
    if not ch or not _owned(user, ch.project):
        raise HTTPException(404, "персонаж не найден")
    return ch


def _own_attribute(db: Session, user: User, attr_id: int) -> CharacterAttribute:
    attr = db.get(CharacterAttribute, attr_id)
    if not attr or not _owned(user, attr.character.project):
        raise HTTPException(404, "атрибут не найден")
    return attr


def _own_char_photo(db: Session, user: User, photo_id: int) -> CharacterPhoto:
    ph = db.get(CharacterPhoto, photo_id)
    if not ph or not _owned(user, ph.character.project):
        raise HTTPException(404, "фото не найдено")
    return ph


def _own_attr_photo(db: Session, user: User, photo_id: int) -> AttributePhoto:
    ph = db.get(AttributePhoto, photo_id)
    if not ph or not _owned(user, ph.attribute.character.project):
        raise HTTPException(404, "фото не найдено")
    return ph


def get_or_create_project(db: Session, user: User, project_id: int | None = None) -> Project:
    if project_id:
        return _own_project(db, user, project_id)
    project = (db.query(Project).filter(Project.owner_id == user.id)
               .order_by(Project.id).first())
    if not project:
        project = Project(name="Клип", kind="album", owner_id=user.id)
        db.add(project)
        db.commit()
        db.refresh(project)
    return project


# ─────────────────────────── авторизация ───────────────────────────

def _user_dict(user: User) -> dict:
    return {"id": user.id, "name": user.name, "login": user.login,
            "is_admin": user.is_admin, "gen_points": user.gen_points, "plan": _plan_of(user), "plan_title": PLANS[_plan_of(user)]["title"]}


def _session_response(user: User) -> JSONResponse:
    response = JSONResponse({"ok": True})
    response.set_cookie(
        QV_COOKIE, signer.dumps({"uid": user.id}), max_age=QV_MAX_AGE,
        httponly=True, samesite="lax", secure=True,
    )
    return response


@app.post("/api/start")
def start(ref: str = "", db: Session = Depends(db_session)):
    """Кнопка «Старт» лендинга: гостевой аккаунт сразу, без регистрации —
    логин с паролем гость сможет добавить потом через /api/register.

    ?ref=КОД с реферальной ссылки закрепляет гостя за амбассадором прямо
    здесь: до оплаты человек может месяцами ходить гостем."""
    guest = User(name="гость")
    db.add(guest)
    db.commit()
    db.refresh(guest)
    _attach_ref(db, guest, ref)
    return _session_response(guest)


@app.post("/api/login")
async def login(request: Request, db: Session = Depends(db_session)):
    body = await request.json()
    login_name = str(body.get("login") or "").strip()
    password = str(body.get("password") or "")
    if login_name:
        user = db.query(User).filter(User.login == login_name).first()
        if not user or not user.password_hash or not _verify_password(password, user.password_hash):
            raise HTTPException(401, "неверный логин или пароль")
        return _session_response(user)
    # Легаси-вход владельца: один общий пароль, как было до qlolvideo.
    if not password or password != APP_PASSWORD:
        raise HTTPException(401, "неверный пароль")
    admin = _admin_user(db)
    if not admin:
        raise HTTPException(500, "админ не инициализирован")
    return _session_response(admin)


@app.post("/api/register")
async def register(request: Request, user: User = Depends(current_user), db: Session = Depends(db_session)):
    """Гость сохраняет аккаунт: логин и пароль вешаются на ТОТ ЖЕ user id,
    так что проекты и файлы гостя остаются при нём."""
    body = await request.json()
    login_name = str(body.get("login") or "").strip()
    password = str(body.get("password") or "")
    name = str(body.get("name") or "").strip()
    if user.login:
        raise HTTPException(400, "у аккаунта уже есть логин")
    if not login_name:
        raise HTTPException(400, "введи логин")
    if len(password) < 6:
        raise HTTPException(400, "пароль от 6 символов")
    # Уникальность логина проверяем кодом: UNIQUE-констрейнт не добавить
    # мягкой ALTER-миграцией без пересоздания таблицы.
    if db.query(User).filter(User.login == login_name, User.id != user.id).first():
        raise HTTPException(400, "логин занят")
    user.login = login_name
    user.password_hash = _hash_password(password)
    if name:
        user.name = name
    db.commit()
    return {"ok": True, "user": _user_dict(user)}


# ─────────────────── вход через Telegram и Яндекс ID ───────────────────
# Креды задаются в infra/.env; пока пусты — кнопки на лендинге скрыты, вход
# по паролю продолжает работать как раньше.
TG_BOT_TOKEN = os.environ.get("TG_LOGIN_BOT_TOKEN", "")
TG_BOT_USERNAME = os.environ.get("TG_LOGIN_BOT_USERNAME", "")
YANDEX_CLIENT_ID = os.environ.get("YANDEX_CLIENT_ID", "")
YANDEX_CLIENT_SECRET = os.environ.get("YANDEX_CLIENT_SECRET", "")
PUBLIC_BASE_URL = os.environ.get("PUBLIC_BASE_URL", "https://qlolapp.art")


def _auth_response(user: "User") -> JSONResponse:
    """Ставит сессионную куку внешнего входа — как обычный логин."""
    token = signer.dumps({"uid": user.id})
    resp = JSONResponse({"ok": True, "name": user.name})
    resp.set_cookie(QV_COOKIE, token, max_age=COOKIE_MAX_AGE, httponly=True,
                    samesite="lax", secure=True)
    return resp


def _adopt_guest(db: Session, guest: "User | None", found: "User") -> "User":
    """Гость нажал «войти через TG/Яндекс»: его проекты переезжают в найденный
    постоянный аккаунт, чтобы работа, начатая до входа, не потерялась."""
    if guest and guest.id != found.id and not guest.login and not guest.tg_id and not guest.yandex_id:
        for pr in db.query(Project).filter(Project.owner_id == guest.id).all():
            pr.owner_id = found.id
        # Человек пришёл по реферальной ссылке гостем и только потом вошёл в
        # свой аккаунт — закрепление переезжает вместе с проектами, иначе
        # амбассадор терял приведённого на первом же входе. Уже закреплённого
        # не переклеиваем и своим же кодом закрепиться не даём.
        if not found.referred_by and guest.referred_by and guest.referred_by != found.id:
            found.referred_by = guest.referred_by
            db.add(RefEvent(ambassador_id=guest.referred_by, referral_id=found.id,
                            kind="signup"))
        db.commit()
    return found


@app.get("/api/auth/config")
def auth_config():
    """Что показывать на экране входа: кнопки появляются только при кредах."""
    return {
        "telegram": bool(TG_BOT_TOKEN and TG_BOT_USERNAME),
        "telegram_bot": TG_BOT_USERNAME,
        "yandex": bool(YANDEX_CLIENT_ID and YANDEX_CLIENT_SECRET),
        "google": bool(GOOGLE_CLIENT_ID and GOOGLE_CLIENT_SECRET),
    }


@app.post("/api/auth/telegram")
async def auth_telegram(request: Request, ref: str = "", db: Session = Depends(db_session)):
    """Telegram Login Widget: сверяем подпись данных токеном бота (HMAC-SHA256),
    по tg_id находим или заводим аккаунт."""
    if not TG_BOT_TOKEN:
        raise HTTPException(400, "вход через Telegram не настроен")
    data = await request.json()
    received_hash = str(data.pop("hash", ""))
    check = "\n".join(f"{k}={data[k]}" for k in sorted(data) if data[k] is not None)
    secret = hashlib.sha256(TG_BOT_TOKEN.encode()).digest()
    calc = hmac.new(secret, check.encode(), hashlib.sha256).hexdigest()
    if not hmac.compare_digest(calc, received_hash):
        raise HTTPException(403, "подпись Telegram не сошлась")
    if time.time() - int(data.get("auth_date") or 0) > 86400:
        raise HTTPException(403, "данные входа устарели, попробуй ещё раз")

    tg_id = str(data.get("id") or "")
    if not tg_id:
        raise HTTPException(400, "нет telegram id")
    user = db.query(User).filter(User.tg_id == tg_id).first()
    guest = None
    token = request.cookies.get(QV_COOKIE)
    if token:
        try:
            guest = db.get(User, int(signer.loads(token, max_age=COOKIE_MAX_AGE).get("uid") or 0))
        except Exception:  # noqa: BLE001
            guest = None
    name = " ".join(x for x in [data.get("first_name"), data.get("last_name")] if x) or "гость"
    if not user:
        # Гость без внешних привязок просто «становится» этим аккаунтом.
        if guest and not guest.login and not guest.tg_id and not guest.yandex_id:
            user = guest
        else:
            user = User(name=name)
            db.add(user)
        user.tg_id = tg_id
        user.name = name
        user.tg_username = str(data.get("username") or "")
        user.avatar_url = str(data.get("photo_url") or "")
        db.commit()
        db.refresh(user)
    else:
        user = _adopt_guest(db, guest, user)
    _attach_ref(db, user, ref)
    return _auth_response(user)


GOOGLE_CLIENT_ID = os.environ.get("GOOGLE_CLIENT_ID", "")
GOOGLE_CLIENT_SECRET = os.environ.get("GOOGLE_CLIENT_SECRET", "")


def _external_login(db: Session, request: Request, field: str, ext_id: str,
                    name: str, email: str = "", avatar: str = "",
                    ref: str = "") -> "User":
    """Общий вход по внешнему id: находим аккаунт, иначе «повышаем» гостя,
    иначе заводим нового. Проекты гостя не теряются."""
    user = db.query(User).filter(getattr(User, field) == ext_id).first()
    guest = None
    token = request.cookies.get(QV_COOKIE)
    if token:
        try:
            guest = db.get(User, int(signer.loads(token, max_age=COOKIE_MAX_AGE).get("uid") or 0))
        except Exception:  # noqa: BLE001
            guest = None
    fresh_guest = guest and not guest.login and not guest.tg_id and not guest.yandex_id \
        and not guest.google_id
    if not user:
        user = guest if fresh_guest else User()
        if not fresh_guest:
            db.add(user)
        setattr(user, field, ext_id)
        user.name = name or user.name or "гость"
        if email:
            user.email = email
        if avatar:
            user.avatar_url = avatar
        db.commit()
        db.refresh(user)
    else:
        _adopt_guest(db, guest, user)
    _attach_ref(db, user, ref)
    return user


@app.get("/api/auth/google/start")
def auth_google_start(ref: str = ""):
    """ref едет в state: после редиректа на Google наши query-параметры
    теряются, а state Google возвращает обратно как есть."""
    if not GOOGLE_CLIENT_ID:
        raise HTTPException(400, "вход через Google не настроен")
    from fastapi.responses import RedirectResponse
    redirect = f"{PUBLIC_BASE_URL}/api/auth/google/callback"
    url = ("https://accounts.google.com/o/oauth2/v2/auth?response_type=code"
           f"&client_id={GOOGLE_CLIENT_ID}&redirect_uri={redirect}"
           "&scope=openid%20email%20profile&access_type=online&prompt=select_account")
    code = _norm_code(ref)
    if code:
        url += f"&state={code}"
    return RedirectResponse(url)


@app.get("/api/auth/google/callback")
async def auth_google_callback(code: str = "", state: str = "", request: Request = None,
                               db: Session = Depends(db_session)):
    if not (GOOGLE_CLIENT_ID and GOOGLE_CLIENT_SECRET):
        raise HTTPException(400, "вход через Google не настроен")
    if not code:
        raise HTTPException(400, "Google не вернул код")
    import httpx as _httpx
    async with _httpx.AsyncClient(timeout=30) as client:
        tok = await client.post("https://oauth2.googleapis.com/token", data={
            "code": code, "client_id": GOOGLE_CLIENT_ID,
            "client_secret": GOOGLE_CLIENT_SECRET,
            "redirect_uri": f"{PUBLIC_BASE_URL}/api/auth/google/callback",
            "grant_type": "authorization_code",
        })
        if tok.status_code != 200:
            raise HTTPException(403, f"Google отказал: {tok.text[:150]}")
        access = (tok.json() or {}).get("access_token", "")
        info = await client.get("https://www.googleapis.com/oauth2/v2/userinfo",
                                headers={"Authorization": f"Bearer {access}"})
        if info.status_code != 200:
            raise HTTPException(403, "не удалось получить профиль Google")
        prof = info.json() or {}
    gid = str(prof.get("id") or "")
    if not gid:
        raise HTTPException(403, "Google не вернул id")
    user = _external_login(db, request, "google_id", gid,
                           prof.get("name") or prof.get("email") or "гость",
                           prof.get("email") or "", prof.get("picture") or "",
                           ref=state)
    from fastapi.responses import RedirectResponse
    resp = RedirectResponse("/")
    resp.set_cookie(QV_COOKIE, signer.dumps({"uid": user.id}), max_age=COOKIE_MAX_AGE,
                    httponly=True, samesite="lax", secure=True)
    return resp


@app.get("/api/auth/yandex/start")
def auth_yandex_start(ref: str = ""):
    """ref едет в state — обратно с Яндекса вернётся тот же код (см. Google)."""
    if not YANDEX_CLIENT_ID:
        raise HTTPException(400, "вход через Яндекс не настроен")
    from fastapi.responses import RedirectResponse
    redirect = f"{PUBLIC_BASE_URL}/api/auth/yandex/callback"
    url = ("https://oauth.yandex.ru/authorize?response_type=code"
           f"&client_id={YANDEX_CLIENT_ID}&redirect_uri={redirect}")
    ref_code = _norm_code(ref)
    if ref_code:
        url += f"&state={ref_code}"
    return RedirectResponse(url)


@app.get("/api/auth/yandex/callback")
async def auth_yandex_callback(code: str = "", state: str = "", request: Request = None,
                               db: Session = Depends(db_session)):
    if not (YANDEX_CLIENT_ID and YANDEX_CLIENT_SECRET):
        raise HTTPException(400, "вход через Яндекс не настроен")
    if not code:
        raise HTTPException(400, "Яндекс не вернул код")
    import httpx as _httpx
    async with _httpx.AsyncClient(timeout=30) as client:
        tok = await client.post("https://oauth.yandex.ru/token", data={
            "grant_type": "authorization_code", "code": code,
            "client_id": YANDEX_CLIENT_ID, "client_secret": YANDEX_CLIENT_SECRET,
        })
        if tok.status_code != 200:
            raise HTTPException(403, f"Яндекс отказал: {tok.text[:150]}")
        access = (tok.json() or {}).get("access_token", "")
        info = await client.get("https://login.yandex.ru/info?format=json",
                                headers={"Authorization": f"OAuth {access}"})
        if info.status_code != 200:
            raise HTTPException(403, "не удалось получить профиль Яндекса")
        prof = info.json() or {}

    yid = str(prof.get("id") or "")
    if not yid:
        raise HTTPException(403, "Яндекс не вернул id")
    user = db.query(User).filter(User.yandex_id == yid).first()
    guest = None
    token = request.cookies.get(QV_COOKIE) if request else None
    if token:
        try:
            guest = db.get(User, int(signer.loads(token, max_age=COOKIE_MAX_AGE).get("uid") or 0))
        except Exception:  # noqa: BLE001
            guest = None
    name = prof.get("real_name") or prof.get("display_name") or prof.get("login") or "гость"
    if not user:
        if guest and not guest.login and not guest.tg_id and not guest.yandex_id:
            user = guest
        else:
            user = User(name=name)
            db.add(user)
        user.yandex_id = yid
        user.name = name
        db.commit()
        db.refresh(user)
    else:
        user = _adopt_guest(db, guest, user)
    _attach_ref(db, user, state)
    from fastapi.responses import RedirectResponse
    resp = RedirectResponse("/")
    resp.set_cookie(QV_COOKIE, signer.dumps({"uid": user.id}), max_age=COOKIE_MAX_AGE,
                    httponly=True, samesite="lax", secure=True)
    return resp


@app.post("/api/logout")
async def logout():
    response = JSONResponse({"ok": True})
    response.delete_cookie(QV_COOKIE)
    response.delete_cookie(COOKIE_NAME)
    return response


@app.get("/api/me")
async def me(request: Request, db: Session = Depends(db_session)):
    user = _resolve_user(request, db)
    if not user:
        return {"authed": False}
    return {"authed": True, "user": _user_dict(user)}


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


def _midframes(s: Scene) -> list[dict]:
    """midframes_json → список; битый/пустой JSON = пустой список."""
    try:
        data = json.loads(s.midframes_json or "[]")
        return data if isinstance(data, list) else []
    except ValueError:
        return []


def _midframe_count(duration_sec: int) -> int:
    """Сколько промежуточных кадров положено сцене: примерно раз в 2 секунды
    между первым и последним, но не больше 4 (экономия очков и времени)."""
    return max(0, min(4, round((duration_sec or 0) / 2) - 1))


def scene_dict(s: Scene) -> dict:
    return {
        "id": s.id, "position": s.position, "start_sec": s.start_sec,
        "duration_sec": s.duration_sec, "lyric_line": s.lyric_line,
        "characters": s.characters,
        "attribute_ids": [int(x) for x in (s.attribute_ids or "").split(",") if x.strip().isdigit()],
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
        "midframes": [
            {"url": f"/api/media/{m['filename']}", "thumb_url": f"/api/thumb/{m['filename']}"}
            for m in _midframes(s) if m.get("filename")
        ],
        "midframes_expected": _midframe_count(s.duration_sec),
        # Референсы кадра: композиция/свет/вайб, которые владелец прикрепил сам.
        "refs": [
            {"id": r.id, "url": f"/api/media/{r.filename}",
             "thumb_url": f"/api/thumb/{r.filename}"}
            for r in s.refs
        ],
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
        "film_grain": t.film_grain, "no_story": t.no_story,
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
def list_projects(user: User = Depends(current_user), db: Session = Depends(db_session)):
    return [
        {"id": p.id, "name": p.name, "kind": p.kind, "tracks": len(p.tracks)}
        for p in db.query(Project).filter(Project.owner_id == user.id)
                    .order_by(Project.id).all()
    ]


@app.post("/api/projects")
async def create_project(request: Request, user: User = Depends(current_user), db: Session = Depends(db_session)):
    body = await request.json()
    kind = str(body.get("kind") or "album")
    if kind not in ("album", "single"):
        kind = "album"
    project = Project(name=str(body.get("name") or "Новый проект"), kind=kind,
                      owner_id=user.id)
    db.add(project)
    db.commit()
    db.refresh(project)
    return {"id": project.id, "name": project.name, "kind": project.kind}


@app.delete("/api/projects/{project_id}")
def delete_project(project_id: int, user: User = Depends(current_user), db: Session = Depends(db_session)):
    project = _own_project(db, user, project_id)
    # «Последний» считаем в рамках владельца проекта — у каждого свой набор.
    if db.query(Project).filter(Project.owner_id == project.owner_id).count() <= 1:
        raise HTTPException(400, "нельзя удалить последний проект")
    for t in project.tracks:
        if t.audio_filename:
            _remove_media(t.audio_filename)
        for sc in t.scenes:
            for f in (sc.image_filename, sc.image_last_filename, sc.video_filename, sc.audio_filename):
                _remove_media(f)
            for m in _midframes(sc):
                _remove_media(m.get("filename", ""))
            for r in sc.refs:
                _remove_media(r.filename)
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
def get_project(project_id: int | None = None, user: User = Depends(current_user), db: Session = Depends(db_session)):
    return project_dict(get_or_create_project(db, user, project_id), with_scenes=True)


@app.patch("/api/project")
async def update_project(request: Request, project_id: int | None = None, user: User = Depends(current_user), db: Session = Depends(db_session)):
    body = await request.json()
    project = get_or_create_project(db, user, project_id)
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
def generate_story(project_id: int | None = None, user: User = Depends(current_user), db: Session = Depends(db_session)):
    from threading import Thread
    project = get_or_create_project(db, user, project_id)
    if not project.tracks:
        raise HTTPException(400, "сначала загрузи хотя бы один трек")
    _charge(db, user, COST_STORY, "сюжет проекта")
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
    user: User = Depends(current_user), db: Session = Depends(db_session),
):
    project = get_or_create_project(db, user, project_id)
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
        _reg_file(db, fname, project.owner_id)
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
async def update_track(track_id: int, request: Request, user: User = Depends(current_user), db: Session = Depends(db_session)):
    track = _own_track(db, user, track_id)
    body = await request.json()
    for field in ("title", "lyrics", "comment", "style"):
        if field in body:
            setattr(track, field, str(body[field]))
    if "film_grain" in body:
        track.film_grain = bool(body["film_grain"])
    if "no_story" in body:
        track.no_story = bool(body["no_story"])
    db.commit()
    return track_dict(track)


@app.delete("/api/tracks/{track_id}")
def delete_track(track_id: int, user: User = Depends(current_user), db: Session = Depends(db_session)):
    track = _own_track(db, user, track_id)
    if track.audio_filename:
        path = os.path.join(UPLOAD_DIR, track.audio_filename)
        if os.path.exists(path):
            os.remove(path)
    for s in track.scenes:
        for f in (s.image_filename, s.image_last_filename, s.video_filename, s.audio_filename):
            _remove_media(f)
        for m in _midframes(s):
            _remove_media(m.get("filename", ""))
        for r in s.refs:
            _remove_media(r.filename)
    _remove_media(track.cover_filename)
    db.delete(track)
    db.commit()
    return {"ok": True}


@app.post("/api/tracks/reorder")
async def reorder_tracks(request: Request, user: User = Depends(current_user), db: Session = Depends(db_session)):
    body = await request.json()
    order = body.get("order") or []  # список id в новом порядке
    for i, track_id in enumerate(order, start=1):
        track = db.get(Track, int(track_id))
        # Чужие id в списке молча пропускаем — порядок правится только у своих.
        if track and _owned(user, track.project):
            track.position = i
    db.commit()
    return {"ok": True}


@app.get("/api/tracks/{track_id}/audio")
def get_audio(track_id: int, user: User = Depends(current_user), db: Session = Depends(db_session)):
    track = _own_track(db, user, track_id)
    if not track.audio_filename:
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
async def upload_project_cover(project_id: int, cover: UploadFile, user: User = Depends(current_user), db: Session = Depends(db_session)):
    project = _own_project(db, user, project_id)
    old = project.cover_filename
    project.cover_filename = await _save_cover_file(cover)
    _reg_file(db, project.cover_filename, project.owner_id)
    db.commit()
    db.refresh(project)
    # Старый файл убираем только ПОСЛЕ commit: если запись не прошла,
    # прежняя обложка остаётся живой.
    _remove_media(old)
    return {"ok": True, "cover_url": f"/api/media/{project.cover_filename}"}


@app.post("/api/tracks/{track_id}/cover")
async def upload_track_cover(track_id: int, cover: UploadFile, user: User = Depends(current_user), db: Session = Depends(db_session)):
    track = _own_track(db, user, track_id)
    old = track.cover_filename
    track.cover_filename = await _save_cover_file(cover)
    _reg_file(db, track.cover_filename, track.project.owner_id)
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
            story="" if track.no_story else project.story,
            character_bible=project.character_bible,
            track_note=track_note, title=track.title, lyrics=track.lyrics,
            comment=clean_comment, style=track.style,
            duration_sec=track.audio_duration_sec or 180,
            characters=characters_payload(project),
            audio_profile=track.audio_profile,
        ))
        for s in list(track.scenes):
            _remove_media(s.image_filename)
            _remove_media(s.video_filename)
            # Сцены пересобираются с нуля — файлы их рефов иначе осиротеют.
            for r in s.refs:
                _remove_media(r.filename)
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
def generate_scenes(track_id: int, user: User = Depends(current_user), db: Session = Depends(db_session)):
    from threading import Thread
    track = _own_track(db, user, track_id)
    if not track.project.story and not track.no_story:
        raise HTTPException(400, "сначала сгенерируй общий сюжет проекта (или включи «без сюжета»)")
    _charge(db, user, COST_SCENES, f"раскадровка трека {track.id}")
    track.scenes_status = "queued"
    db.commit()
    Thread(target=_run_scene_generation, args=(track_id,), daemon=True).start()
    return {"ok": True}


@app.patch("/api/scenes/{scene_id}")
async def update_scene(scene_id: int, request: Request, user: User = Depends(current_user), db: Session = Depends(db_session)):
    scene = _own_scene(db, user, scene_id)
    body = await request.json()
    # characters и image_prompt_last фронт слал всегда — бэк их молча ронял;
    # чипы персонажей и правка последнего кадра держатся на этих полях.
    for field in ("duration_sec", "lyric_line", "characters", "shot_size", "camera_move",
                  "image_prompt", "motion_prompt", "shot_note", "image_prompt_last"):
        if field in body:
            setattr(scene, field, str(body[field]) if field != "duration_sec" else body[field])
    if "attribute_ids" in body:
        ids = body["attribute_ids"] or []
        scene.attribute_ids = ",".join(str(int(i)) for i in ids if str(i).isdigit())
    db.commit()
    return scene_dict(scene)


@app.delete("/api/scenes/{scene_id}")
def delete_scene(scene_id: int, user: User = Depends(current_user), db: Session = Depends(db_session)):
    scene = _own_scene(db, user, scene_id)
    for f in (scene.image_filename, scene.image_last_filename,
              scene.video_filename, scene.audio_filename):
        _remove_media(f)
    for m in _midframes(scene):
        _remove_media(m.get("filename", ""))
    # Строки рефов уносит каскад ORM, а файлы на диске — только мы.
    for r in scene.refs:
        _remove_media(r.filename)
    track = scene.track
    db.delete(scene)
    db.flush()
    _renumber_scenes(track)
    db.commit()
    return {"ok": True}


# ───────────────────── референсы кадра (композиция/вайб) ─────────────────────

@app.post("/api/scenes/{scene_id}/refs")
async def add_scene_ref(scene_id: int, photo: UploadFile, user: User = Depends(current_user), db: Session = Depends(db_session)):
    """Картинка-референс кадра: чем показывать словами «как снято», проще
    приложить кадр-образец. В генерацию он уходит ПЕРВЫМ (см.
    _scene_reference_photo), но стилистику задаёт стиль трека, а не реф."""
    scene = _own_scene(db, user, scene_id)
    ext = os.path.splitext(photo.filename or "")[1].lower() or ".jpg"
    if ext not in (".jpg", ".jpeg", ".png", ".webp"):
        raise HTTPException(400, "поддерживаются jpg/png/webp")
    fname = f"sref_{uuid.uuid4().hex}{ext}"
    with open(os.path.join(UPLOAD_DIR, fname), "wb") as f:
        f.write(await photo.read())
    max_pos = max((r.position for r in scene.refs), default=0)
    db.add(SceneRef(scene_id=scene.id, position=max_pos + 1, filename=fname))
    _reg_file(db, fname, scene.track.project.owner_id)
    db.commit()
    # scene.refs загружен ДО вставки — без refresh ответ отстаёт на один реф.
    db.refresh(scene)
    return scene_dict(scene)


@app.delete("/api/scenes/refs/{ref_id}")
def delete_scene_ref(ref_id: int, user: User = Depends(current_user), db: Session = Depends(db_session)):
    ref = _own_scene_ref(db, user, ref_id)
    _remove_media(ref.filename)
    db.delete(ref)
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
            characters=characters_payload(track.project),
        ))
        prompt = built.get("prompt") or ""
        if not prompt:
            raise RuntimeError("Claude не вернул промпт листа раскадровки")
        # Лист: референсом идёт КОЛЛАЖ моделек всех героев трека (до 3) — так
        # лица узнаваемы. Одна моделька референсом копировалась целиком вместо
        # сетки кадров, поэтому именно коллаж, а не одно фото.
        board_ref = None
        board_collage = ""
        paths = []
        for c in sorted(track.project.characters, key=lambda x: (not x.is_main, x.position)):
            if not c.photos:
                continue
            cand = os.path.join(UPLOAD_DIR, c.photos[0].filename)
            if os.path.exists(cand):
                paths.append(cand)
            if len(paths) >= 3:
                break
        owner = db.get(User, track.project.owner_id) if track.project.owner_id else None
        engine = _plan_image_engine(owner)
        multi = mediagen.IMAGE_ENGINES.get(engine, {}).get("max_refs", 1) > 1
        if multi:
            pass  # Nano Banana возьмёт модельки отдельными картинками
        elif len(paths) == 1:
            board_ref = paths[0]
        elif paths:
            board_ref = _ref_collage(db, paths, track.project.owner_id)
            if board_ref:
                board_collage = os.path.basename(board_ref)
        data, mime = asyncio.run(mediagen.generate_image(
            prompt, board_ref, reference_paths=paths if multi else None, engine=engine))
        old = track.storyboard_filename
        # Лист смотрят целиком, апскейл до 4К ему не нужен.
        track.storyboard_filename = _save_image(data, mime, upscale=False)
        _reg_file(db, track.storyboard_filename, track.project.owner_id)
        track.storyboard_status = "done"
        db.commit()
        _remove_media(old)
        if board_collage:
            _remove_media(board_collage)
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


@app.post("/api/tracks/{track_id}/storyboard-cells")
def storyboard_cells(track_id: int, user: User = Depends(current_user), db: Session = Depends(db_session)):
    """Режет лист на ячейки и отдаёт их превью — БЕЗ записи в сцены.
    Владелец сам решает в модалке, какие ячейки взять и в какие сцены их
    положить (см. apply-cells)."""
    track = _own_track(db, user, track_id)
    if not track.storyboard_filename:
        raise HTTPException(400, "сначала сгенерируй лист раскадровки")
    src = os.path.join(UPLOAD_DIR, track.storyboard_filename)
    if not os.path.exists(src):
        raise HTTPException(404, "файл листа не найден")
    n = max(1, len(track.scenes))
    cols = 3 if n > 4 else 2
    rows = -(-n // cols)
    cells = []
    for i in range(n):
        cx, cy = i % cols, i // cols
        fname = f"cell_{uuid.uuid4().hex}.png"
        dst = os.path.join(UPLOAD_DIR, fname)
        r = subprocess.run(
            ["ffmpeg", "-y", "-i", src, "-vf",
             f"crop=iw/{cols}:ih/{rows}:{cx}*iw/{cols}:{cy}*ih/{rows}", dst],
            capture_output=True, timeout=120,
        )
        if r.returncode != 0 or not os.path.exists(dst):
            continue
        _reg_file(db, fname, track.project.owner_id)
        cells.append({"index": i + 1, "filename": fname,
                      "url": f"/api/media/{fname}", "thumb_url": f"/api/thumb/{fname}"})
    db.commit()
    return {"ok": True, "grid": f"{cols}x{rows}", "cells": cells}


@app.post("/api/tracks/{track_id}/apply-cells")
async def apply_cells(track_id: int, request: Request, user: User = Depends(current_user),
                      db: Session = Depends(db_session)):
    """Кладёт выбранные ячейки листа первыми кадрами выбранных сцен.

    body: {"pairs": [{"filename": "cell_xxx.png", "scene_id": 45}, ...]}
    Копируем файл под новым именем: одна ячейка может уйти в несколько сцен,
    а исходные cell_* подчищаются позже вместе с медиа проекта."""
    track = _own_track(db, user, track_id)
    body = await request.json()
    pairs = body.get("pairs") or []
    scene_ids = {s.id for s in track.scenes}
    applied = 0
    for pair in pairs:
        fname = os.path.basename(str(pair.get("filename") or ""))
        sid = int(pair.get("scene_id") or 0)
        if not fname or sid not in scene_ids:
            continue
        src = os.path.join(UPLOAD_DIR, fname)
        if not os.path.exists(src):
            continue
        scene = db.get(Scene, sid)
        if not scene:
            continue
        new_name = f"slice_{uuid.uuid4().hex}.png"
        shutil.copyfile(src, os.path.join(UPLOAD_DIR, new_name))
        old = scene.image_filename
        scene.image_filename = new_name
        scene.image_status = "done"
        scene.image_error = ""
        _reg_file(db, new_name, track.project.owner_id)
        db.commit()
        _remove_media(old)
        applied += 1
    return {"ok": True, "applied": applied}


@app.post("/api/tracks/{track_id}/slice-storyboard")
def slice_storyboard(track_id: int, user: User = Depends(current_user), db: Session = Depends(db_session)):
    """Нарезка листа-сетки на ячейки: каждая становится ПЕРВЫМ кадром своей
    сцены по порядку. Черновые кадры из листа — дальше можно перегенерировать
    точечно или анимировать как есть."""
    track = _own_track(db, user, track_id)
    if not track.storyboard_filename:
        raise HTTPException(400, "сначала сгенерируй лист раскадровки")
    scenes = sorted(track.scenes, key=lambda x: x.position)
    if not scenes:
        raise HTTPException(400, "у трека нет сцен")
    src = os.path.join(UPLOAD_DIR, track.storyboard_filename)
    if not os.path.exists(src):
        raise HTTPException(404, "файл листа не найден")
    n = len(scenes)
    cols = 3 if n > 4 else 2
    rows = -(-n // cols)
    done = 0
    for i, sc in enumerate(scenes):
        cx, cy = i % cols, i // cols
        fname = f"slice_{uuid.uuid4().hex}.png"
        dst = os.path.join(UPLOAD_DIR, fname)
        r = subprocess.run(
            ["ffmpeg", "-y", "-i", src, "-vf",
             f"crop=iw/{cols}:ih/{rows}:{cx}*iw/{cols}:{cy}*ih/{rows}",
             dst],
            capture_output=True, timeout=120,
        )
        if r.returncode != 0 or not os.path.exists(dst):
            continue
        old = sc.image_filename
        sc.image_filename = fname
        sc.image_status = "done"
        sc.image_error = ""
        _reg_file(db, fname, track.project.owner_id)
        db.commit()
        _remove_media(old)
        done += 1
    return {"ok": True, "sliced": done, "grid": f"{cols}x{rows}"}


@app.post("/api/tracks/{track_id}/generate-storyboard")
def generate_storyboard(track_id: int, user: User = Depends(current_user), db: Session = Depends(db_session)):
    from threading import Thread
    track = _own_track(db, user, track_id)
    if not track.scenes:
        raise HTTPException(400, "сначала сгенерируй раскадровку трека")
    _charge(db, user, _image_cost(user), f"лист раскадровки трека {track.id}")
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


def _scene_selected_attributes(scene: Scene, chars: list[Character]) -> list[CharacterAttribute]:
    """Атрибуты, ЯВНО отмеченные для кадра. Явный выбор исключает смешивание:
    когда у героя несколько вещей, генератор больше не тащит в кадр всё сразу."""
    ids = {int(x) for x in (scene.attribute_ids or "").split(",") if x.strip().isdigit()}
    if not ids:
        return []
    return [a for c in chars for a in c.attributes if a.id in ids]


def _scene_attribute_photo(scene: Scene, chars: list[Character]) -> str | None:
    """Референс-АТРИБУТ: если текст сцены упоминает фирменную вещь персонажа
    (шляпу, квадрик, тачку) — кадр строится вокруг предмета, и референсом
    должно идти фото самой вещи, а не лицо героя. Проверяем только персонажей
    ЭТОЙ сцены; совпадение — регистронезависимое вхождение имени атрибута."""
    # Явно выбранные вещи имеют приоритет над поиском имени в тексте.
    for a in _scene_selected_attributes(scene, chars):
        for ph in a.photos:
            path = os.path.join(UPLOAD_DIR, ph.filename)
            if os.path.exists(path):
                return path
            break
    if (scene.attribute_ids or "").strip():
        return None  # выбор сделан осознанно — из текста ничего не подхватываем
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


def _ref_collage(db: Session, paths: list[str], owner_id: int | None) -> str | None:
    """Несколько входных картинок одним листом (hstack): генератор видит их
    разом, а не только первую. Имя уникальное (uuid), а не по id сцены —
    параллельные генерации соседних кадров больше не перетирают файл друг
    друга прямо во время отправки в шлюз."""
    if len(paths) < 2:
        return paths[0] if paths else None
    fname = f"refjoin_{uuid.uuid4().hex}.png"
    out = os.path.join(UPLOAD_DIR, fname)
    inputs = []
    for pth in paths:
        inputs += ["-i", pth]
    scale = ";".join(f"[{i}:v]scale=-2:768[v{i}]" for i in range(len(paths)))
    stack = "".join(f"[v{i}]" for i in range(len(paths))) + f"hstack=inputs={len(paths)}[out]"
    try:
        r = subprocess.run(
            ["ffmpeg", "-y", *inputs, "-filter_complex", f"{scale};{stack}",
             "-map", "[out]", out],
            capture_output=True, timeout=120,
        )
    except (OSError, subprocess.SubprocessError) as e:
        # Склейка — удобство, а не условие генерации: упавший ffmpeg не должен
        # ронять весь кадр, вызывающий откатится на первую картинку.
        log.warning("коллаж-референс не собрался: %s", e)
        return None
    if r.returncode != 0 or not os.path.exists(out):
        return None
    # Как и любой файл в UPLOAD_DIR — с владельцем, иначе /api/media его прячет.
    _reg_file(db, fname, owner_id)
    db.commit()
    return out


def _scene_ref_paths(scene: Scene) -> list[str]:
    """Живые файлы референсов кадра по порядку (битые ссылки пропускаем)."""
    out = []
    for r in sorted(scene.refs, key=lambda x: (x.position, x.id)):
        path = os.path.join(UPLOAD_DIR, r.filename)
        if os.path.exists(path):
            out.append(path)
    return out


def _character_model_paths(chars: list[Character], limit: int) -> list[str]:
    """Первые фото-модельки персонажей (по одной на героя) — они отвечают
    только за узнаваемость лица, не за стилистику кадра."""
    paths: list[str] = []
    for c in chars:
        if len(paths) >= limit:
            break
        if not c.photos:
            continue
        path = os.path.join(UPLOAD_DIR, c.photos[0].filename)
        if os.path.exists(path):
            paths.append(path)
    return paths


def _scene_reference_photo(db: Session, scene: Scene, project: Project) -> str | None:
    """Референс генерации кадра.

    Приоритет — референс КАДРА: если владелец приложил свои картинки
    (композиция/свет/вайб), в шлюз уходит коллаж «реф + до двух моделек
    персонажей сцены», причём реф стоит ПЕРВЫМ. Так генератор перестаёт
    просто копировать фото-модельку, а лица при этом остаются узнаваемыми.

    Рефов нет — прежнее поведение: фото атрибута, если текст сцены крутится
    вокруг фирменной вещи, иначе модельки персонажей кадра (или главного
    героя, когда персонажи кадра не указаны)."""
    chars = _scene_characters(scene, project)
    scene_refs = _scene_ref_paths(scene)
    if scene_refs:
        models = _character_model_paths(
            chars or [c for c in project.characters if c.is_main], 2)
        # Реф первым: первая картинка коллажа для генератора — главная.
        return _ref_collage(db, [scene_refs[0], *models], project.owner_id) or scene_refs[0]

    attr_path = _scene_attribute_photo(scene, chars)
    if attr_path:
        return attr_path
    if not chars:
        chars = [c for c in project.characters if c.is_main]
    paths = _character_model_paths(chars, 3)
    if not paths:
        return None
    # Несколько героев в кадре — референсом идёт сборный лист: модельки бок о
    # бок, иначе генератор видит только первого и рисует остальных от балды.
    return _ref_collage(db, paths, project.owner_id) or paths[0]


def _scene_reference_paths(db: Session, scene: Scene, project: Project) -> list[str]:
    """Те же референсы, что и в _scene_reference_photo, но СПИСКОМ — без
    hstack-склейки.

    Коллаж был костылём под шлюзы: они принимают ровно одну картинку. Nano
    Banana берёт до 8-14 отдельных, и это принципиально другое качество
    идентичности — модель перестаёт воспроизводить саму сетку коллажа в кадре.
    Порядок тот же: реф кадра первым (он главный), дальше модельки героев."""
    chars = _scene_characters(scene, project)
    out: list[str] = []
    scene_refs = _scene_ref_paths(scene)
    if scene_refs:
        out += scene_refs[:3]
        out += _character_model_paths(
            chars or [c for c in project.characters if c.is_main], 3)
    else:
        attr_path = _scene_attribute_photo(scene, chars)
        if attr_path:
            out.append(attr_path)
        if not chars:
            chars = [c for c in project.characters if c.is_main]
        out += _character_model_paths(chars, 4)
    # Дедуп с сохранением порядка + потолок по самому скупому Nano Banana (8).
    seen: set[str] = set()
    uniq = [p for p in out if not (p in seen or seen.add(p))]
    return uniq[:8]


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
    style = (track.style or "").strip() or "cinematic music video still"
    parts = [
        # 1. Стиль — ПЕРВЫМ и безусловным законом кадра. Раньше он стоял после
        # промпта сцены, и генератор тянул свет с фото-модельки: кадры выходили
        # тёмными студийными портретами вместо клипа в заданной стилистике.
        f"VISUAL STYLE (mandatory, overrides everything): {style}. "
        f"Render the whole frame in this style — lighting, palette, texture, grain and mood "
        f"come from the STYLE, never from the reference images.",
        # 2. Что происходит в кадре.
        base,
        # 3. Роль референсов: узнаваемость и композиция, но не картинка целиком.
        "Reference images define composition, framing energy and character identity ONLY — "
        "do not copy their color grade, lighting or background.",
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
    # 4. Анти-требования: ровно те грабли, из-за которых кадры выходили
    # одинаковыми тёмными портретами на сером фоне.
    parts.append(
        "Do not copy the reference photo as-is. Do not output a studio portrait or a plain grey "
        "backdrop unless the style says so. Expose for a bright readable image: avoid crushed "
        "blacks and muddy dark frames unless the style explicitly asks for night noir."
    )
    # 5. Динамика: кадр клипа — момент действия, а не позирование в камеру.
    parts.append(
        "The shot must be caught in motion: the character is acting, moving and interacting with "
        "the environment, camera and body in a dynamic pose. No posed portrait staring into the "
        "lens unless the shot description above explicitly asks for exactly that."
    )
    parts.append(f"Consistent single continuous music video, unified visual style: {style}.")
    if neighbours:
        parts.append("Adjacent shots for continuity: " + " | ".join(neighbours))
    parts.append("Vertical 9:16 composition, no text, no captions, no watermarks, no logos.")
    return "\n".join(p for p in parts if p.strip())


def _run_scene_frames(scene_id: int, which: str = "both") -> None:
    """which: both | first | last — что именно пересобираем."""
    db = SessionLocal()
    collage = ""  # временный склеенный референс — убираем в finally, чтобы не копился
    try:
        scene = db.get(Scene, scene_id)
        if not scene:
            return
        scene.image_status = "running"
        scene.image_error = ""
        db.commit()
        track = scene.track
        import asyncio
        owner = db.get(User, track.project.owner_id) if track.project.owner_id else None
        engine = _plan_image_engine(owner)
        # Nano Banana берёт референсы ОТДЕЛЬНЫМИ картинками (до 8-14 штук) —
        # ради этого движок и подключался. Шлюзам по-прежнему нужен один файл,
        # поэтому коллаж собираем только для них.
        multi = mediagen.IMAGE_ENGINES.get(engine, {}).get("max_refs", 1) > 1
        reference = None
        ref_list: list[str] = []
        if multi:
            ref_list = _scene_reference_paths(db, scene, track.project)
        else:
            reference = _scene_reference_photo(db, scene, track.project)
            if reference and os.path.basename(reference).startswith("refjoin_"):
                collage = os.path.basename(reference)
        first_data = last_data = None
        first_mime = last_mime = ""
        native_4k = False
        if which in ("both", "first"):
            res = asyncio.run(mediagen.generate_image_ex(
                _frame_prompt(scene, track, "first"), reference,
                reference_paths=ref_list, engine=engine))
            first_data, first_mime = res["data"], res["mime"]
            native_4k = res["native_4k"]
            scene.image_engine = res["engine"]
        if which in ("both", "last"):
            res = asyncio.run(mediagen.generate_image_ex(
                _frame_prompt(scene, track, "last"), reference,
                reference_paths=ref_list, engine=engine))
            last_data, last_mime = res["data"], res["mime"]
            native_4k = native_4k or res["native_4k"]
            scene.image_engine = res["engine"]

        old_first, old_last = scene.image_filename, scene.image_last_filename
        old_video, old_audio = scene.video_filename, scene.audio_filename
        old_mids = [m.get("filename", "") for m in _midframes(scene)]
        if first_data is not None:
            scene.image_filename = _save_image(first_data, first_mime, upscale=not native_4k)
            _reg_file(db, scene.image_filename, track.project.owner_id)
        else:
            old_first = ""  # первый кадр не пересобирали — оставляем как есть
        if last_data is not None:
            scene.image_last_filename = _save_image(last_data, last_mime, upscale=not native_4k)
            _reg_file(db, scene.image_last_filename, track.project.owner_id)
        else:
            old_last = ""
        scene.image_status = "done"
        # Кадры переснялись — старое видео, утверждение и промежуточные
        # кадры (интерполяция СТАРОЙ пары) к ним не относятся.
        scene.approved = False
        scene.video_filename = ""
        scene.video_status = ""
        scene.video_error = ""
        scene.audio_filename = ""
        scene.midframes_json = ""
        db.commit()
        for f in (old_first, old_last, old_video, old_audio, *old_mids):
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
        _remove_media(collage)
        db.close()


@app.post("/api/scenes/{scene_id}/generate-frames")
def generate_scene_frames(scene_id: int, which: str = "both", user: User = Depends(current_user),
                          db: Session = Depends(db_session)):
    from threading import Thread
    scene = _own_scene(db, user, scene_id)
    if not scene.image_prompt.strip():
        raise HTTPException(400, "у сцены пуст промпт первого кадра")
    if which not in ("both", "first", "last"):
        which = "both"
    # Кадры не имеют своей цены: они берут аванс в счёт цены сцены. Видео потом
    # добирает разницу до цены движка, а перерисовка кадров уже оплаченной
    # сцены бесплатна — человек не должен бояться жать «перегенерировать».
    _scene_charge(db, user, scene, _frames_cost(user, scene),
                  f"кадры сцены {scene.id} ({which})")
    scene.image_status = "queued"
    db.commit()
    Thread(target=_run_scene_frames, args=(scene_id, which), daemon=True).start()
    return {"ok": True}


# ─────────────────── промежуточные кадры сцены ───────────────────

def _run_midframes(scene_id: int) -> None:
    """Промежуточные кадры между первым и последним: промпт каждого строится
    интерполяцией двух готовых промптов (Claude не нужен), референсом идёт
    первый кадр сцены. Прогресс дописывается в midframes_json по одному кадру
    с commit'ом — упавшая генерация не теряет уже готовые."""
    db = SessionLocal()
    try:
        scene = db.get(Scene, scene_id)
        if not scene:
            return
        track = scene.track
        total = _midframe_count(scene.duration_sec)
        # Старый набор заменяется целиком: файлы вычищаем, список обнуляем.
        old = [m.get("filename", "") for m in _midframes(scene)]
        scene.midframes_json = "[]"
        db.commit()
        for f in old:
            _remove_media(f)

        first = (scene.image_prompt or "").strip()
        last = (scene.image_prompt_last or scene.image_prompt or "").strip()
        ref = os.path.join(UPLOAD_DIR, scene.image_filename) if scene.image_filename else None
        if ref and not os.path.exists(ref):
            ref = None
        import asyncio
        done: list[dict] = []
        for n in range(1, total + 1):
            prompt = (f"Frame {n} of {total} between these two moments: "
                      f"{first} → {last}, style unchanged")
            data, mime = asyncio.run(mediagen.generate_image(prompt, reference_path=ref))
            fname = _save_image(data, mime)
            _reg_file(db, fname, track.project.owner_id)
            done.append({"filename": fname, "prompt": prompt})
            scene.midframes_json = json.dumps(done, ensure_ascii=False)
            db.commit()
        log.info("промежуточные кадры сцены %s готовы (%s шт.)", scene_id, total)
    except Exception as e:  # noqa: BLE001
        db.rollback()
        log.warning("промежуточные кадры сцены %s упали: %s", scene_id, e)
    finally:
        db.close()


@app.post("/api/scenes/{scene_id}/generate-midframes")
def generate_midframes(scene_id: int, user: User = Depends(current_user), db: Session = Depends(db_session)):
    from threading import Thread
    scene = _own_scene(db, user, scene_id)
    total = _midframe_count(scene.duration_sec)
    if total <= 0:
        raise HTTPException(400, "сцена короткая — промежуточные кадры не нужны")
    if not scene.image_filename:
        raise HTTPException(400, "сначала сгенерируй кадры сцены — референсом идёт первый кадр")
    if not (scene.image_prompt or "").strip():
        raise HTTPException(400, "у сцены пуст промпт первого кадра")
    # Промежуточные кадры — тоже кадры этой сцены: входят в её цену.
    _scene_charge(db, user, scene, _frames_cost(user, scene),
                  f"промежуточные кадры сцены {scene.id}")
    Thread(target=_run_midframes, args=(scene_id,), daemon=True).start()
    return {"ok": True, "count": total}


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
        owner = db.get(User, track.project.owner_id) if track.project.owner_id else None
        # Семейство знает фронт, конкретную модель выбирает тариф — и она
        # уже записана на сцене при списании, чтобы движок не «переехал»
        # между оплатой и генерацией.
        engine = scene.video_engine or _plan_video_engine(owner, scene.video_provider)
        fname = asyncio.run(mediagen.animate_scene(
            prompt=scene.motion_prompt, first_path=first_path, last_path=last_path,
            duration_sec=scene.duration_sec, provider=scene.video_provider,
            seedance_model=PLANS[_plan_of(owner)].get("seedance_model", "") if owner else "",
            engine=engine,
        ))
        old_video = scene.video_filename
        scene.video_filename = fname
        _reg_file(db, fname, track.project.owner_id)
        scene.video_status = "done"

        # Отрезок трека ровно под эту сцену — слушаем видео с его музыкой.
        old_audio = scene.audio_filename
        audio_src = _track_audio_path(track)
        if audio_src:
            try:
                scene.audio_filename = mediagen.slice_audio(
                    audio_src, scene.start_sec, scene.duration_sec)
                _reg_file(db, scene.audio_filename, track.project.owner_id)
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
async def generate_scene_video(scene_id: int, request: Request, user: User = Depends(current_user), db: Session = Depends(db_session)):
    from threading import Thread
    scene = _own_scene(db, user, scene_id)
    if not scene.image_filename:
        raise HTTPException(400, "сначала сгенерируй кадры сцены")
    body = await request.json() if await request.body() else {}
    provider = str(body.get("provider") or scene.video_provider or "seedance")
    provider = _allowed_provider(user, provider)
    if provider not in mediagen.video_providers():
        raise HTTPException(400, f"провайдер {provider} недоступен: {mediagen.video_providers()}")
    # engine — явный id модели (см. /api/providers.video_engines). Чужой тарифу
    # движок молча опускается до дефолтного, а не даёт FREE'шнику Seedance 2.5.
    engine = _plan_video_engine(user, provider, str(body.get("engine") or ""))
    # Цена по движку: Grok идёт по нашей подписке и стоит в разы дешевле
    # платного Seedance 2.5 — раньше все платные списывали одинаковые 16.
    cost = _scene_cost(user, provider, scene, engine)
    if scene.video_filename:
        # Перерендер: у сцены уже есть видео, и это НОВЫЙ вызов платного API.
        # Берём цену самого видео — цену сцены без аванса за кадры (их не
        # перерисовываем). У Grok разница нулевая, и перерендер бесплатен:
        # он и правда ничего нам не стоит.
        _charge(db, user, max(0, cost - _frames_cost(user, scene)),
                f"перерендер видео сцены {scene.id} ({engine})")
    else:
        _scene_charge(db, user, scene, cost, f"видео сцены {scene.id} ({engine})")
    scene.video_provider = provider
    scene.video_engine = engine
    scene.video_status = "queued"
    db.commit()
    Thread(target=_run_scene_video, args=(scene_id,), daemon=True).start()
    return {"ok": True}


@app.post("/api/scenes/{scene_id}/approve")
async def approve_scene(scene_id: int, request: Request, user: User = Depends(current_user), db: Session = Depends(db_session)):
    """Утверждение ВИДЕО сцены: утверждённые идут в общий клип трека."""
    scene = _own_scene(db, user, scene_id)
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
        track.clip_filename = mediagen.assemble_clip(
            videos, _track_audio_path(track), film_grain=track.film_grain)
        _reg_file(db, track.clip_filename, track.project.owner_id)
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
def assemble_track_clip(track_id: int, user: User = Depends(current_user), db: Session = Depends(db_session)):
    from threading import Thread
    track = _own_track(db, user, track_id)
    approved = [s for s in track.scenes if s.approved and s.video_filename]
    if not approved:
        raise HTTPException(400, "нет утверждённых сцен с видео")
    track.clip_status = "queued"
    db.commit()
    Thread(target=_run_assemble, args=(track_id,), daemon=True).start()
    return {"ok": True, "scenes": len(approved)}


# ──────────────────────────── супергенерация ────────────────────────────

def _est_scenes(duration_sec: int) -> int:
    """Сколько сцен выйдет из трека: одна сцена на ~6 секунд (claude.py режет
    по 2–10). Верхняя граница 200 — защита от битой длительности, а не тариф."""
    dur = int(duration_sec or 180)
    return max(1, min(200, int(round(dur / SCENE_SEC))))


def _settle_supergen(db: Session, track: Track, per_scene: int, prepaid: int) -> str:
    """Развести предоплату супергенерации по реально нарезанным сценам.

    Предоплата бралась по ОЦЕНКЕ длительности, а сцен Claude мог нарезать
    больше или меньше. Лишние оплаченные сцены возвращаем очками, недостающие
    добираем с баланса. Пустая строка — всё сошлось; текст — почему не сошлось
    (тогда конвейер останавливается, а не работает бесплатно)."""
    if per_scene <= 0:
        return ""
    owner = db.get(User, track.project.owner_id) if track.project.owner_id else None
    scenes = sorted(track.scenes, key=lambda s: s.position)
    unpaid = [s for s in scenes if int(s.charged_points or 0) < per_scene]
    left = max(0, int(prepaid))
    need = 0
    for s in unpaid:
        if left > 0:
            left -= 1  # эту сцену закрывает предоплата
        else:
            need += per_scene - int(s.charged_points or 0)
    if need and owner and not _take_points(db, owner, need):
        return (f"не хватило {need} очков: трек длиннее оценки "
                f"({len(scenes)} сцен). Пополни баланс и запусти ещё раз")
    for s in unpaid:
        s.charged_points = per_scene
    if left and owner:
        # Оценка была щедрее реальности — неиспользованное возвращаем.
        owner.gen_points = int(owner.gen_points or 0) + left * per_scene
        log.info("супергенерация трека %s: вернули %s очков за %s лишних сцен",
                 track.id, left * per_scene, left)
    db.commit()
    return ""


def _run_supergen(track_id: int, per_scene: int = 0, prepaid: int = 0) -> None:
    """Весь конвейер одним нажатием: сюжет (если пуст) → раскадровка (если нет)
    → кадры всех сцен → видео всех сцен → авто-утверждение → сборка клипа.

    per_scene/prepaid — цена сцены и сколько сцен оплачено вперёд: реальное
    число сцен известно только после раскадровки, поэтому счёт сводится уже
    внутри (см. _settle_supergen).

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

        if track.no_story:
            pass  # рандомные панчи: сюжет не нужен
        elif not (project.story or "").strip():
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

        # Сцены есть — сводим счёт: предоплата шла по оценке длительности.
        problem = _settle_supergen(db, track, per_scene, prepaid)
        if problem:
            note(problem, "error")
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
        owner = db.get(User, track.project.owner_id) if track.project.owner_id else None
        if owner:
            provider = _allowed_provider(owner, provider)
        # Модель внутри семейства фиксируем один раз на весь конвейер: иначе
        # соседние сцены одного клипа уехали бы на разные движки.
        engine = _plan_video_engine(owner, provider)
        for i, sid in enumerate(scene_ids, 1):
            db.expire_all()
            s = db.get(Scene, sid)
            if not s:
                continue
            if not s.video_filename:
                note(f"видео: сцена {i}/{total} через {engine}…")
                s.video_provider = provider
                s.video_engine = engine
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
def supergen(track_id: int, user: User = Depends(current_user), db: Session = Depends(db_session)):
    from threading import Thread
    track = _own_track(db, user, track_id)
    if not track.audio_filename:
        raise HTTPException(400, "у трека нет аудио — загрузи дорожку")
    # Без стиля и персонажей Claude выдумывает свои: стиль обязателен, герои тоже.
    if not (track.style or "").strip():
        raise HTTPException(400, "не выбран стиль клипа — выбери пресет на карточке трека")
    if not any(c.name.strip() for c in track.project.characters):
        raise HTTPException(400, "в проекте нет персонажей — добавь нового или клонируй из базы")
    if track.supergen_status in ("queued", "running"):
        raise HTTPException(400, "супергенерация уже идёт")
    # Стоимость конвейера — вперёд. Цена сцены зависит от движка, которым
    # супергенерация будет рисовать видео: тот же выбор, что и в _run_supergen.
    prov = _allowed_provider(user, "seedance" if mediagen.seedance_available() else "grok")
    per_scene = _scene_cost(user, prov)
    scenes = list(track.scenes)
    if not (track.project.story or "").strip():
        _charge(db, user, COST_STORY, f"сюжет проекта {track.project.id}")
    prepaid = 0
    if scenes:
        # Сцены уже есть: платим только за ту работу, которую конвейер реально
        # сделает. Готовое видео он не перерисовывает — брать за него нельзя.
        def _sg_cost(s: Scene) -> int:
            if not s.video_filename:
                return per_scene    # полный круг: кадры + видео
            if not (s.image_filename and s.image_last_filename):
                # видео есть, дорисуем недостающие кадры — по цене СВОЕГО движка
                return _frames_cost(user, s)
            return 0                # делать нечего

        _scenes_charge(db, user, scenes, _sg_cost,
                       f"супергенерация трека {track.id} ({prov})")
    else:
        # Сцен ещё нет — объём оцениваем по длительности трека (~6 сек на сцену).
        # Прежняя оценка упиралась в потолок 30 сцен: четырёхминутный трек
        # списывал как трёхминутный, а работу делал всю. Теперь оценка честная,
        # а расхождение с реальностью разводит _settle_supergen: недостачу
        # добирает, лишнее возвращает.
        prepaid = _est_scenes(track.audio_duration_sec)
        _charge(db, user, COST_SCENES + per_scene * prepaid,
                f"супергенерация трека {track.id} ({prov}, ~{prepaid} сцен)")
    track.supergen_status = "queued"
    track.supergen_note = "старт…"
    db.commit()
    Thread(target=_run_supergen, args=(track_id, per_scene, prepaid), daemon=True).start()
    return {"ok": True}


def _media_response(path: str, request: Request) -> Response:
    """Отдача файла с поддержкой Range: <video> в браузере всегда просит
    диапазон и ждёт 206 Partial Content. FileResponse отвечал 200 и целым
    файлом — плеер такое не проигрывает и не перематывает."""
    file_size = os.path.getsize(path)
    mime = "video/mp4" if path.lower().endswith(".mp4") else None
    range_header = request.headers.get("range") or request.headers.get("Range")
    if not range_header or not range_header.startswith("bytes="):
        resp = FileResponse(path, media_type=mime) if mime else FileResponse(path)
        resp.headers["Accept-Ranges"] = "bytes"
        return resp
    try:
        raw = range_header.split("=", 1)[1].split(",")[0].strip()
        start_s, _, end_s = raw.partition("-")
        start = int(start_s) if start_s else 0
        end = int(end_s) if end_s else file_size - 1
    except ValueError:
        start, end = 0, file_size - 1
    start = max(0, min(start, file_size - 1))
    end = max(start, min(end, file_size - 1))
    length = end - start + 1

    def _chunks():
        with open(path, "rb") as f:
            f.seek(start)
            left = length
            while left > 0:
                data = f.read(min(64 * 1024, left))
                if not data:
                    break
                left -= len(data)
                yield data

    from fastapi.responses import StreamingResponse
    return StreamingResponse(
        _chunks(), status_code=206,
        media_type=mime or "application/octet-stream",
        headers={
            "Content-Range": f"bytes {start}-{end}/{file_size}",
            "Accept-Ranges": "bytes",
            "Content-Length": str(length),
        },
    )


@app.get("/api/media/{filename}")
def get_media(filename: str, request: Request, user: User = Depends(current_user), db: Session = Depends(db_session)):
    fname = os.path.basename(filename)
    path = os.path.join(UPLOAD_DIR, fname)
    if not os.path.exists(path):
        raise HTTPException(404, "файл не найден")
    _check_file_owner(db, user, fname)
    return _media_response(path, request)


THUMB_DIR = os.environ.get("THUMB_DIR", "/data/thumbs")
os.makedirs(THUMB_DIR, exist_ok=True)


@app.get("/api/thumb/{filename}")
def get_thumb(filename: str, user: User = Depends(current_user), db: Session = Depends(db_session)):
    """Миниатюра кадра ~640px: полноразмерные 4К PNG в сетке карточек браузер
    не прогружает. Рендерится лениво, кэшируется рядом с данными."""
    src = os.path.join(UPLOAD_DIR, os.path.basename(filename))
    if not os.path.exists(src):
        raise HTTPException(404, "файл не найден")
    _check_file_owner(db, user, os.path.basename(filename))
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
def providers(user: User = Depends(current_user)):
    """Честная картина движков: что открыто тарифом, что реально живо по
    ключам и сколько стоит сцена на каждом.

    Принцип: если тариф обещает Nano Banana Pro, а KIE_API_KEY не задан, мы
    молча рисуем на шлюзе — но отдаём наверх ИМЕННО ЭТО, а не обещание.
    Фронт должен показывать реальность, иначе человек платит за строчку."""
    plan_id = _plan_of(user)
    plan = PLANS[plan_id]
    live_fams = mediagen.video_providers()  # что реально настроено ключами
    avail = [p for p in plan["video"] if p in live_fams]

    frames = _frames_cost(user)
    engines = []
    for eid in _plan_engine_ids(plan_id):
        spec = mediagen.VIDEO_ENGINES[eid]
        engines.append({
            "id": eid, "title": spec["title"], "family": spec["family"],
            "default": plan["engines"].get(spec["family"]) == eid,
            "live": mediagen.video_engine_live(eid),
            "first_last": bool(spec["first_last"]),
            "paid": bool(spec["paid"]),
            "scene_cost": frames + VIDEO_COST[eid],
            "video_cost": VIDEO_COST[eid],
            "usd_per_scene": round(mediagen.video_engine_usd(eid, SCENE_SEC), 4),
            "note": spec.get("note", ""),
        })

    want_image = plan.get("image_engine") or "chatgpt"
    real_image = _plan_image_engine(user)
    images = []
    for eid, spec in mediagen.IMAGE_ENGINES.items():
        images.append({
            "id": eid, "title": spec["title"],
            "live": eid in mediagen.image_engines_live(),
            "max_refs": int(spec["max_refs"]),
            "native_4k": bool(spec["native_4k"]),
            "frames_cost": FRAME_COST[eid],
            "usd_per_image": round(mediagen.image_engine_usd(eid), 4),
            "current": eid == real_image,
        })
    return {
        # Легаси-контракт фронта: семейства движков и два булевых флага.
        "video": avail or ["grok"], "plan": plan_id,
        "seedance": "seedance" in avail, "kling": "kling" in avail,
        # Новое: конкретные модели с ценой и реальной доступностью.
        "video_engines": engines,
        "image_engines": images,
        "image_engine": real_image,
        "image_engine_planned": want_image,
        # Тариф обещает платный движок кадров, а ключа нет — фронту нужен
        # повод сказать об этом вслух, а не показывать несбывшееся обещание.
        "image_engine_downgraded": real_image != want_image,
        "frames_cost": frames,
        "keys": {
            "kie": mediagen.kie_available(),
            "seevio": bool(mediagen.SEEVIO_API_KEY),
            "kling_official": bool(mediagen.KLING_ACCESS_KEY and mediagen.KLING_SECRET_KEY),
        },
        "priority": bool(plan["priority"]),
    }



# ─────────────────────────── персонажи альбома ───────────────────────────

@app.get("/api/characters/library")
def characters_library(user: User = Depends(current_user), db: Session = Depends(db_session)):
    """Сквозная библиотека: персонажи всех проектов ТЕКУЩЕГО пользователя —
    чтобы переносить героя из альбома в альбом клонированием."""
    out = []
    for p in db.query(Project).filter(Project.owner_id == user.id).order_by(Project.id).all():
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
async def clone_character(request: Request, project_id: int | None = None, user: User = Depends(current_user), db: Session = Depends(db_session)):
    """Клонирование персонажа из библиотеки в проект. Копия полностью
    самостоятельная: удаление оригинала (или его фото) не ломает клона."""
    body = await request.json()
    source = db.get(Character, int(body.get("source_id") or 0))
    if not source or not _owned(user, source.project):
        raise HTTPException(404, "исходный персонаж не найден")
    project = get_or_create_project(db, user, project_id)
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
        _reg_file(db, fname, project.owner_id)
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
            _reg_file(db, fname, project.owner_id)
            db.add(AttributePhoto(attribute_id=attr_clone.id, position=i, filename=fname))
    db.commit()
    # clone.photos закэширован ДО вставки фото — без refresh ответ уйдёт пустым.
    db.refresh(clone)
    return character_dict(clone)


@app.post("/api/characters")
async def create_character(request: Request, project_id: int | None = None, user: User = Depends(current_user), db: Session = Depends(db_session)):
    body = await request.json()
    project = get_or_create_project(db, user, project_id)
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
async def update_character(char_id: int, request: Request, user: User = Depends(current_user), db: Session = Depends(db_session)):
    ch = _own_character(db, user, char_id)
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
def delete_character(char_id: int, user: User = Depends(current_user), db: Session = Depends(db_session)):
    ch = _own_character(db, user, char_id)
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
async def add_character_photo(char_id: int, photo: UploadFile, user: User = Depends(current_user), db: Session = Depends(db_session)):
    ch = _own_character(db, user, char_id)
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


MODEL_SHEET_STYLES = {
    "3d": (
        "Professional 3D character model turnaround sheet, high-end CG render (Unreal Engine / Blender cycles look): the SAME character shown in four views side by side — front, three-quarter, side profile, back — standing in a relaxed A-pose, full body head to toe, consistent proportions across all four views. Clean neutral light-grey studio background, soft even three-point lighting, subtle contact shadow, no dramatic shadows. Detailed materials: fabric weave, skin subsurface scattering, hair cards."
    ),
    "real": (
        "Photorealistic character reference sheet: the SAME person photographed in four views side by side — front, three-quarter, side profile, back — standing relaxed, full body head to toe, identical clothing and proportions in every view. Neutral light-grey seamless studio backdrop, soft even softbox lighting, sharp focus, natural skin texture."
    ),
    "anime": (
        "Anime character model sheet (settei): the SAME character drawn in four views side by side — front, three-quarter, side profile, back — standing in a relaxed pose, full body head to toe, consistent design and proportions. Clean cel-shaded line art with flat colors, neutral light background, production reference sheet style."
    ),
}


@app.post("/api/characters/{char_id}/generate-model")
async def generate_character_model(char_id: int, request: Request,
                                   user: User = Depends(current_user),
                                   db: Session = Depends(db_session)):
    """Генерация модельки персонажа: разворот в четырёх ракурсах одним листом.

    Описание берём из тела запроса (или из карточки персонажа), референсом идут
    уже загруженные фото — коллажем, чтобы генератор держал лицо и одежду.
    Результат становится очередной фото-моделькой персонажа."""
    ch = _own_character(db, user, char_id)
    body = await request.json() if await request.body() else {}
    desc = (str(body.get("description") or "").strip() or ch.description).strip()
    if not desc:
        raise HTTPException(400, "нужно описание персонажа")
    kind = str(body.get("kind") or "3d")
    base = MODEL_SHEET_STYLES.get(kind, MODEL_SHEET_STYLES["3d"])
    _charge(db, user, _image_cost(user), f"моделька персонажа {ch.id}")

    engine = _plan_image_engine(user)
    max_refs = int(mediagen.IMAGE_ENGINES.get(engine, {}).get("max_refs", 1))
    paths = []
    for ph in ch.photos[: max(3, max_refs)]:
        cand = os.path.join(UPLOAD_DIR, ph.filename)
        if os.path.exists(cand):
            paths.append(cand)
    owner_id = ch.project.owner_id
    reference = None
    collage = ""
    # Идентичность героя держится на референсах: движок с несколькими входами
    # получает их по одному, шлюзу по-прежнему клеим коллаж.
    if max_refs > 1:
        pass
    elif len(paths) == 1:
        reference = paths[0]
    elif paths:
        reference = _ref_collage(db, paths, owner_id)
        if reference:
            collage = os.path.basename(reference)

    prompt = (
        f"{base}\n\nCHARACTER (follow this description exactly): {desc}\n\n"
        "The four views must be the SAME character — same face, hair, outfit and "
        "accessories in every view. Keep the identity from the reference photos. "
        "Horizontal sheet, plain background, no text, no labels, no watermark."
    )
    try:
        data, mime = await mediagen.generate_image(
            prompt, reference,
            reference_paths=paths if max_refs > 1 else None, engine=engine)
    except Exception as e:  # noqa: BLE001
        raise HTTPException(502, f"генератор не отдал модельку: {str(e)[:200]}")
    finally:
        if collage:
            _remove_media(collage)

    fname = _save_image(data, mime, upscale=False)
    _reg_file(db, fname, owner_id)
    max_pos = max((p.position for p in ch.photos), default=0)
    db.add(CharacterPhoto(character_id=ch.id, position=max_pos + 1, filename=fname))
    db.commit()
    db.refresh(ch)
    return character_dict(ch)


@app.delete("/api/characters/photos/{photo_id}")
def delete_character_photo(photo_id: int, user: User = Depends(current_user), db: Session = Depends(db_session)):
    ph = _own_char_photo(db, user, photo_id)
    _remove_media(ph.filename)
    db.delete(ph)
    db.commit()
    return {"ok": True}


# ─────────────────────────── атрибуты персонажей ───────────────────────────

@app.post("/api/characters/{char_id}/attributes")
async def create_attribute(char_id: int, request: Request, user: User = Depends(current_user), db: Session = Depends(db_session)):
    ch = _own_character(db, user, char_id)
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
async def update_attribute(attr_id: int, request: Request, user: User = Depends(current_user), db: Session = Depends(db_session)):
    attr = _own_attribute(db, user, attr_id)
    body = await request.json()
    if "name" in body:
        attr.name = str(body["name"])
    if "description" in body:
        attr.description = str(body["description"])
    db.commit()
    return attribute_dict(attr)


@app.delete("/api/attributes/{attr_id}")
def delete_attribute(attr_id: int, user: User = Depends(current_user), db: Session = Depends(db_session)):
    attr = _own_attribute(db, user, attr_id)
    for ph in attr.photos:
        _remove_media(ph.filename)
    db.delete(attr)
    db.commit()
    return {"ok": True}


@app.post("/api/attributes/{attr_id}/photos")
async def add_attribute_photo(attr_id: int, photo: UploadFile, user: User = Depends(current_user), db: Session = Depends(db_session)):
    attr = _own_attribute(db, user, attr_id)
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
def delete_attribute_photo(photo_id: int, user: User = Depends(current_user), db: Session = Depends(db_session)):
    ph = _own_attr_photo(db, user, photo_id)
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
async def add_scene(track_id: int, request: Request, user: User = Depends(current_user), db: Session = Depends(db_session)):
    """Ручной кадр. after_position: после какого кадра вставить (0 = в начало,
    не передан = в конец)."""
    track = _own_track(db, user, track_id)
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
def generate_all_frames(track_id: int, user: User = Depends(current_user), db: Session = Depends(db_session)):
    from threading import Thread
    track = _own_track(db, user, track_id)
    if not track.scenes:
        raise HTTPException(400, "сначала сгенерируй раскадровку")
    todo = [s for s in track.scenes
            if not (s.image_filename and s.image_last_filename)
            and (s.image_prompt or "").strip()
            and not s.image_prompt.startswith("(готовый кадр")]
    if not todo:
        raise HTTPException(400, "у всех сцен кадры уже готовы")
    # Списываем за весь пакет вперёд — до того, как сцены встанут в очередь.
    # Кадры берут аванс в счёт цены сцены; уже оплаченные сцены не платят снова.
    _scenes_charge(db, user, todo, lambda sc: _frames_cost(user, sc),
                   f"кадры всех сцен трека {track.id}")
    for s in todo:
        s.image_status = "queued"
    db.commit()
    Thread(target=_run_all_frames, args=(track_id,), daemon=True).start()
    return {"ok": True, "queued": len(todo)}


def _run_all_videos(track_id: int) -> None:
    """Очередь видео по всем сценам трека: генераторы всё равно однопоточные,
    поэтому идём последовательно и не роняем всю пачку из-за одной ошибки."""
    db = SessionLocal()
    try:
        track = db.get(Track, track_id)
        ids = [s.id for s in sorted(track.scenes, key=lambda x: x.position)] if track else []
    finally:
        db.close()
    for sid in ids:
        db = SessionLocal()
        try:
            sc = db.get(Scene, sid)
            skip = not sc or sc.video_status not in ("queued",)
        finally:
            db.close()
        if skip:
            continue
        _run_scene_video(sid)


@app.post("/api/tracks/{track_id}/generate-all-videos")
def generate_all_videos(track_id: int, provider: str = "", engine: str = "",
                        user: User = Depends(current_user),
                        db: Session = Depends(db_session)):
    from threading import Thread
    track = _own_track(db, user, track_id)
    todo = [s for s in track.scenes if s.image_filename and not s.video_filename]
    if not todo:
        raise HTTPException(400, "нет сцен с кадрами без видео")
    # Движок выбираем ДО списания: цена сцены зависит именно от него.
    prov = _allowed_provider(user, provider or ("seedance" if mediagen.seedance_available() else "grok"))
    eng = _plan_video_engine(user, prov, engine)
    _scenes_charge(db, user, todo, lambda sc: _scene_cost(user, prov, sc, eng),
                   f"видео всех сцен трека {track.id} ({eng})")
    for s in todo:
        s.video_provider = prov
        s.video_engine = eng
        s.video_status = "queued"
        s.video_error = ""
    db.commit()
    Thread(target=_run_all_videos, args=(track_id,), daemon=True).start()
    return {"ok": True, "queued": len(todo), "provider": prov, "engine": eng}


# ────────────────────────── оплата: Stripe + ЮKassa ──────────────────────────
# Два провайдера рядом, не вместо: Stripe продаёт в долларах международной
# аудитории, ЮKassa — в рублях российской. Ключи в infra/.env; какого нет, тот
# просто выключен, и витрина честно говорит об этом флагом в /api/billing/plans.
YOOKASSA_SHOP_ID = os.environ.get("YOOKASSA_SHOP_ID", "")
YOOKASSA_SECRET = os.environ.get("YOOKASSA_SECRET_KEY", "")
BRAND = os.environ.get("BRAND_NAME", "lolq.ai")  # уходит в выписку по карте
# Сколько дней после неудачного списания тариф ещё живёт. Раньше первый же
# сбой сети сбрасывал человека на free и стирал его карту — отток на ровном месте.
SUB_GRACE_DAYS = int(os.environ.get("SUB_GRACE_DAYS", "3"))


def _yookassa_enabled() -> bool:
    return bool(YOOKASSA_SHOP_ID and YOOKASSA_SECRET)


def _stripe_enabled() -> bool:
    return stripe_pay.enabled()


def _as_utc(dt):
    """SQLite отдаёт datetime без таймзоны, а now() — с UTC, и сравнение их
    падает TypeError'ом. Ловилось это только у тех, у кого plan_until уже был:
    вебхук на продлении отваливался 500-й, тариф не продлевался."""
    if dt is None:
        return None
    from datetime import timezone as _tz
    return dt if dt.tzinfo else dt.replace(tzinfo=_tz.utc)


def _period_days(period: str) -> int:
    return PLAN_YEAR_DAYS if period == "year" else PLAN_DAYS


def _norm_period(value) -> str:
    return "year" if str(value or "").strip().lower() in ("year", "annual", "yearly") else "month"


def _plan_price(plan_id: str, period: str) -> tuple[int, int]:
    """Ценник тарифа за период: (центы, копейки). Одно место, где месяц
    превращается в год, — иначе витрина и платёжка разъедутся."""
    plan = PLANS[plan_id]
    if period == "year":
        return int(plan["usd_year_cents"]), int(plan["rub_year_kopeks"])
    return int(plan["usd_cents"]), int(plan["rub_kopeks"])


def _pay_key(provider: str, payment_id: str) -> str:
    """Ключ платежа для таблицы обработанных: id платёжки с её именем.
    Два провайдера пишут в одну колонку, пересечься их id не должны."""
    pid = str(payment_id or "").strip()
    return f"{provider}:{pid}" if pid else ""


def _grant_plan_points(user: User, plan_id: str, period: str) -> int:
    """Начислить очки за оплаченный период.

    ПРИБАВЛЯЕМ к остатку, а не перезаписываем. Раньше стояло
    max(остаток, норма): экономный человек, у которого осталось 590 из 600,
    после оплаты получал 10 очков за 990 ₽ — тариф наказывал за бережливость.
    Потолок — две нормы периода: копить бесконечно нельзя, иначе подписка
    превращается в склад, но месяц простоя больше не сгорает.
    Опускать баланс потолок не имеет права: сверху могли лежать докупленные
    пакеты, за них заплачено отдельно и они не сгорают."""
    norm = int(PLANS[plan_id]["points"])
    grant = norm * (12 if period == "year" else 1)
    cap = 2 * grant
    cur = int(user.gen_points or 0)
    user.gen_points = max(cur, min(cur + grant, cap))
    return int(user.gen_points) - cur


def _already_processed(db: Session, provider: str, payment_id: str,
                       alt_ids=()) -> bool:
    """Выдавали ли уже по этому платежу.

    alt_ids — ДРУГИЕ имена того же платежа. Одна оплата подписки Stripe
    приезжает двумя событиями, и у сессии чекаута поле invoice бывает пустым
    (Stripe кладёт id счёта не всегда) — тогда события ключевались по-разному,
    и один платёж выдавал ДВА месяца, две нормы очков и две доли амбассадору.
    Поэтому ищем по всем известным именам, а пишем каноническое."""
    key = _pay_key(provider, payment_id)
    if not key:
        return True
    names = {key, str(payment_id or "").strip()}  # + «голое» имя: платежи до префиксов
    for alt in alt_ids:
        alt = str(alt or "").strip()
        if alt:
            names.update({_pay_key(provider, alt), alt})
    return bool(db.query(ProcessedPayment)
                .filter(ProcessedPayment.payment_id.in_(sorted(names))).first())


def _stripe_first_key(sub_id: str) -> str:
    """Имя первого платежа подписки Stripe: «sub_first:<id подписки>».

    Одна оплата приезжает двумя событиями — checkout.session.completed и
    invoice.paid, — и общего id у них может не быть (поле invoice у сессии
    пустое). Общее у них ровно одно: подписка. По ней и ключуем первый период;
    продления идут по своим id счетов и не пересекаются."""
    sid = str(sub_id or "").strip()
    return f"sub_first:{sid}" if sid else ""


def _stripe_is_first_invoice(db: Session, obj: dict, sub_id: str) -> bool:
    """Это первый счёт подписки (тот же платёж, что и чекаут), а не продление.

    Обычно достаточно billing_reason=subscription_create. Если Stripe его не
    прислал, смотрим, не выдавали ли мы только что первый период этой подписки:
    два события одного платежа приходят с разницей в секунды, а продление — через
    месяц, поэтому свежая отметка означает «это тот же платёж»."""
    if not sub_id:
        return False
    reason = str(obj.get("billing_reason") or "")
    if reason:
        return reason == "subscription_create"
    from datetime import timedelta
    row = (db.query(ProcessedPayment)
           .filter(ProcessedPayment.payment_id == _pay_key("stripe", _stripe_first_key(sub_id)))
           .first())
    return bool(row and _as_utc(row.created_at)
                and _as_utc(row.created_at) > now() - timedelta(hours=24))


def _grant_payment(db: Session, user: User, *, provider: str, payment_id: str,
                   kind: str = "plan", plan_id: str = "", period: str = "month",
                   pack_id: str = "", amount_cents: int = 0, amount_kopeks: int = 0,
                   currency: str = "USD", pay_method_id: str = "",
                   stripe_customer: str = "", stripe_subscription: str = "",
                   alt_ids=()) -> bool:
    """Выдать оплаченное: тариф с очками или пакет очков. False — уже выдавали.

    ИДЕМПОТЕНТНОСТЬ. Обе платёжки повторяют уведомление, пока не получат 200,
    и один платёж приезжает к нам по нескольку раз (а подписочный — ещё и
    двумя разными событиями). Отметка об обработке и сама выдача пишутся ОДНОЙ
    транзакцией: раньше отметка коммитилась отдельно, и падение между ними
    оставляло человека без тарифа при взятых деньгах — повтор вебхука уже
    отбивался как дубль. Гонку двух одновременных уведомлений ловит UNIQUE на
    payment_id: проигравший откатывается целиком и ничего не выдаёт."""
    key = _pay_key(provider, payment_id)
    if not key or _already_processed(db, provider, payment_id, alt_ids):
        return False

    points = 0
    if kind == "topup":
        pack = TOPUP_PACKS.get(pack_id)
        if not pack:
            log.warning("платёж %s: неизвестный пакет очков %r", key, pack_id)
            return False
        # Пакет считаем ПО СВОЕЙ таблице, а не по числу из metadata: metadata
        # ездит через чужой сервис, а прайс живёт здесь.
        points = int(pack["points"])
        user.gen_points = int(user.gen_points or 0) + points
    else:
        if plan_id not in PLANS or PLANS[plan_id]["usd_cents"] <= 0:
            log.warning("платёж %s: неизвестный тариф %r", key, plan_id)
            return False
        period = _norm_period(period)
        points = _grant_plan_points(user, plan_id, period)
        user.plan = plan_id
        user.plan_period = period
        from datetime import timedelta
        until = _as_utc(user.plan_until)
        base = until if (until and until > now()) else now()
        user.plan_until = base + timedelta(days=_period_days(period))
        user.autopay = True  # оплатил подписку — автопродление снова включено

    if pay_method_id:
        user.pay_method_id = pay_method_id
    if stripe_customer:
        user.stripe_customer_id = stripe_customer
    if stripe_subscription:
        user.stripe_subscription_id = stripe_subscription

    db.add(ProcessedPayment(
        payment_id=key, user_id=user.id, plan=plan_id or pack_id,
        provider=provider, kind=kind, period=period, points=points,
        amount_kopeks=int(amount_kopeks or 0), amount_cents=int(amount_cents or 0),
        currency=(currency or "USD").upper(),
    ))
    try:
        db.commit()
    except IntegrityError:
        # Второе уведомление по тому же платежу успело раньше — откатываем
        # ВСЁ, включая выдачу: месяц и очки уже начислены им.
        db.rollback()
        log.info("платёж %s уже обработан — пропускаем", key)
        return False
    log.info("выдано по платежу %s: юзер %s, %s %s, +%s очков", key, user.id, kind,
             plan_id or pack_id, points)
    return True


def _reward_kopeks(amount_kopeks: int, amount_cents: int) -> int:
    """Сумма платежа в копейках для партнёрки: доллары пересчитываем по курсу,
    потому что выплаты амбассадорам идут в рублях и баланс у них один."""
    if amount_kopeks > 0:
        return int(amount_kopeks)
    return int(amount_cents or 0) * USD_RUB


# ─────────────────────────── витрина тарифов ───────────────────────────

def _movies_estimate(points: int, scene_cost: int) -> int:
    """Сколько клипов по 3 минуты выходит из этих очков на таком движке."""
    if points <= 0 or scene_cost <= 0:
        return 0
    return int(points) // (scene_cost * CLIP_SCENES)


def _plan_card(plan_id: str) -> dict:
    """Карточка тарифа для витрины: оба ценника, оба периода, оценка в клипах.

    «Сколько клипов» считаем по РАБОЧЕЙ лошадке тарифа — самому дешёвому
    платному движку. По самому дорогому (Seedance 2.5) выходит меньше одного
    клипа на PRO MAX, и это правда, но правда про потолок, а не про тариф:
    её отдаём отдельным полем movies_estimate_top."""
    p = PLANS[plan_id]
    engines = _plan_engines(plan_id)
    work = _plan_work_cost(plan_id)
    top = max(engines.values()) if engines else SCENE_COST["grok"]
    usd_c, rub_k = int(p["usd_cents"]), int(p["rub_kopeks"])
    usd_y, rub_y = int(p["usd_year_cents"]), int(p["rub_year_kopeks"])
    return {
        "id": plan_id,
        "title": p["title"],
        "usd": round(usd_c / 100, 2),
        "usd_cents": usd_c,
        "usd_year": round(usd_y / 100, 2),
        "usd_year_cents": usd_y,
        # «$16 в месяц при годовой оплате» — главный аргумент годового тарифа.
        "usd_year_per_month": round(usd_y / 12 / 100, 2),
        "rub": rub_k // 100,
        "rub_kopeks": rub_k,
        "rub_year": rub_y // 100,
        "rub_year_kopeks": rub_y,
        "price": p["price"],  # легаси-поле старого фронта: рубли
        "points": int(p["points"]),
        "points_year": int(p["points"]) * 12,
        "video": list(p["video"]),
        "engines": engines,               # движок → полная цена сцены на тарифе
        "engine_titles": {eid: mediagen.VIDEO_ENGINES[eid]["title"]
                          for eid in engines if eid in mediagen.VIDEO_ENGINES},
        "default_engines": dict(p["engines"]),   # семейство → модель по умолчанию
        "image_engine": p.get("image_engine") or "chatgpt",
        "image_engine_title": mediagen.IMAGE_ENGINES.get(
            p.get("image_engine") or "chatgpt", {}).get("title", ""),
        "frames_cost": FRAME_COST.get(p.get("image_engine") or "chatgpt", FRAMES_COST),
        "scene_cost": work,               # рабочая лошадка тарифа
        "scene_cost_top": top,            # самый дорогой движок тарифа
        "movies_estimate": _movies_estimate(p["points"], work),
        "movies_estimate_top": _movies_estimate(p["points"], top),
        "movies_estimate_grok": _movies_estimate(p["points"], SCENE_COST["grok"]),
        # Правда об экономике очка: сколько человек платит за очко и сколько
        # себестоимости в него заложено. Считаем один раз тут, а не на фронте.
        "usd_per_point": round(int(p["usd_cents"]) / 100 / int(p["points"]), 5)
        if int(p["points"]) else 0.0,
        "features": list(p["features"]),
        "badge": p["badge"],
        "note": p["note"],
        "priority": bool(p["priority"]),
        "year_discount_pct": YEAR_DISCOUNT_PCT if usd_c > 0 else 0,
    }


def _pack_card(pack_id: str) -> dict:
    k = TOPUP_PACKS[pack_id]
    return {
        "id": pack_id,
        "points": int(k["points"]),
        "usd": round(k["usd_cents"] / 100, 2),
        "usd_cents": int(k["usd_cents"]),
        "rub": int(k["rub_kopeks"]) // 100,
        "rub_kopeks": int(k["rub_kopeks"]),
        "usd_per_1000_points": round(k["usd_cents"] / k["points"] * 10, 2),
        "usd_per_point": round(k["usd_cents"] / 100 / k["points"], 5),
        "save_pct": int(k["save_pct"]),
        "badge": k["badge"],
        # Пакет — добор, а не тариф: он дороже подписочного очка и продаётся
        # только при живой платной подписке (см. TOPUP_REQUIRES_PLAN).
        "requires_plan": TOPUP_REQUIRES_PLAN,
        "movies_estimate": _movies_estimate(k["points"], SCENE_COST["seedance-2-mini"]),
        "movies_estimate_grok": _movies_estimate(k["points"], SCENE_COST["grok"]),
    }


def _providers_state() -> dict:
    return {"stripe": _stripe_enabled(), "yookassa": _yookassa_enabled()}


@app.get("/api/billing/plans")
def billing_plans(request: Request, db: Session = Depends(db_session)):
    """Всё, что нужно витрине: тарифы обоих периодов, пакеты очков, флаги
    платёжек, цена работы в очках и текущее состояние человека.

    Роут ПУБЛИЧНЫЙ: цены — первое, что смотрит человек с лендинга, и требовать
    ради них аккаунт значит терять его на входе. Гостю отдаём витрину без
    личного блока, авторизованному — с текущим тарифом и остатком."""
    providers = _providers_state()
    user = _resolve_user(request, db)
    return {
        "current": _plan_of(user) if user else "free",
        "current_period": (user.plan_period or "month") if user else "month",
        "plan_until": user.plan_until.isoformat() if (user and user.plan_until) else "",
        "autopay": bool(user and user.autopay and (user.pay_method_id or user.stripe_subscription_id)),
        "points": int(user.gen_points or 0) if user else 0,
        "authorized": bool(user),
        # enabled — легаси-флаг старого фронта: хоть одна платёжка жива.
        "enabled": any(providers.values()),
        "providers": providers,
        "stripe_enabled": providers["stripe"],
        "yookassa_enabled": providers["yookassa"],
        "currency_default": "usd" if providers["stripe"] else "rub",
        "usd_rub": USD_RUB,
        "year_discount_pct": YEAR_DISCOUNT_PCT,
        "plans": [_plan_card(pid) for pid in PLANS],
        "packs": [_pack_card(kid) for kid in TOPUP_PACKS],
        # Докупка очков разрешена только при живой платной подписке.
        "topup_requires_plan": TOPUP_REQUIRES_PLAN,
        "topup_allowed": bool(user and _plan_of(user) != "free") if TOPUP_REQUIRES_PLAN else True,
        # Прайс работы в очках — витрине, чтобы объяснять, куда они уходят.
        "costs": {
            "scene": dict(SCENE_COST),          # кадры на шлюзе + видео движком
            "video": dict(VIDEO_COST),          # только видео сцены
            "frames": dict(FRAME_COST),         # пара кадров по движку картинок
            "frames_advance": FRAMES_COST,
            "story": COST_STORY,
            "storyboard_scenes": COST_SCENES,
            "storyboard_sheet": COST_STORYBOARD,
            "character_model": COST_CHARACTER_MODEL,
            "clip_scenes": CLIP_SCENES,
            "scene_sec": SCENE_SEC,
            # Якорь экономики: сколько себестоимости лежит в одном очке.
            "point_usd": POINT_USD,
        },
        # Справочник движков для витрины «что даёт верхний тариф».
        "engines": {
            "video": {
                eid: {
                    "title": spec["title"], "family": spec["family"],
                    "first_last": bool(spec["first_last"]), "paid": bool(spec["paid"]),
                    "video_cost": VIDEO_COST[eid],
                    "usd_per_scene": round(mediagen.video_engine_usd(eid, SCENE_SEC), 4),
                    "live": mediagen.video_engine_live(eid),
                    "note": spec.get("note", ""),
                }
                for eid, spec in mediagen.VIDEO_ENGINES.items()
            },
            "image": {
                eid: {
                    "title": spec["title"], "max_refs": int(spec["max_refs"]),
                    "native_4k": bool(spec["native_4k"]),
                    "frames_cost": FRAME_COST[eid],
                    "usd_per_image": round(mediagen.image_engine_usd(eid), 4),
                    "live": eid in mediagen.image_engines_live(),
                }
                for eid, spec in mediagen.IMAGE_ENGINES.items()
            },
        },
    }


@app.get("/api/billing/packs")
def billing_packs(request: Request, db: Session = Depends(db_session)):
    """Пакеты очков отдельно от тарифов: докупка не трогает подписку.
    Публичный по той же причине, что и витрина тарифов."""
    user = _resolve_user(request, db)
    return {
        "packs": [_pack_card(kid) for kid in TOPUP_PACKS],
        "topup_requires_plan": TOPUP_REQUIRES_PLAN,
        "topup_allowed": bool(user and _plan_of(user) != "free") if TOPUP_REQUIRES_PLAN else True,
        "providers": _providers_state(),
        "currency_default": "usd" if _stripe_enabled() else "rub",
        "points": int(user.gen_points or 0) if user else 0,
        "authorized": bool(user),
    }


# ─────────────────────────── создание платежа ───────────────────────────

@app.post("/api/billing/create")
async def billing_create(request: Request, user: User = Depends(current_user),
                         db: Session = Depends(db_session)):
    """Ссылка на оплату: подписка на тариф или разовая покупка пакета очков.

    body: {kind:"plan"|"topup", plan?, pack?, period?:"month"|"year",
           provider?:"stripe"|"yookassa", currency?:"usd"|"rub", promo?}
    Провайдер по умолчанию выбирается валютой: доллары — Stripe, рубли — ЮKassa."""
    body = await request.json() if await request.body() else {}
    kind = str(body.get("kind") or "").strip().lower()
    plan_id = str(body.get("plan") or "").strip()
    pack_id = str(body.get("pack") or "").strip()
    if kind not in ("plan", "topup"):
        kind = "topup" if pack_id else "plan"
    period = _norm_period(body.get("period"))

    currency = str(body.get("currency") or "").strip().lower()
    provider = str(body.get("provider") or "").strip().lower()
    if provider not in ("stripe", "yookassa"):
        provider = "yookassa" if currency in ("rub", "rur", "₽") else "stripe"
    # Выключенный провайдер — не тупик: если жив второй, уходим на него.
    if provider == "stripe" and not _stripe_enabled():
        provider = "yookassa"
    elif provider == "yookassa" and not _yookassa_enabled():
        provider = "stripe"
    if (provider == "stripe" and not _stripe_enabled()) or \
       (provider == "yookassa" and not _yookassa_enabled()):
        raise ApiError(503, "payments_disabled", "Payments are not connected yet.")

    if kind == "topup":
        pack = TOPUP_PACKS.get(pack_id)
        if not pack:
            raise ApiError(400, "unknown_pack", f"Unknown points pack: {pack_id!r}")
        # Вторая половина дыры в экономике: пакеты продавались кому угодно, и
        # подписку можно было не покупать вообще. Докупка — ДОБОР к живому
        # тарифу, а не его замена.
        if TOPUP_REQUIRES_PLAN and _plan_of(user) == "free":
            raise ApiError(403, "subscription_required",
                           "Points packs top up an active plan. "
                           "Subscribe first, then add points any time.")
        amount_cents, amount_kopeks = int(pack["usd_cents"]), int(pack["rub_kopeks"])
        title = f"{BRAND} — {pack['points']} points"
        points = int(pack["points"])
    else:
        plan = PLANS.get(plan_id)
        if not plan or plan["usd_cents"] <= 0:
            raise ApiError(400, "unknown_plan", f"Unknown plan: {plan_id!r}")
        amount_cents, amount_kopeks = _plan_price(plan_id, period)
        title = f"{BRAND} {plan['title']} — {'12 months' if period == 'year' else '1 month'}"
        points = int(plan["points"])

    # Промокод партнёрки: если человек ещё ни за кем не закреплён — закрепляем
    # прямо здесь (то же первое касание, что и по ссылке ?ref=). Скидка — один
    # раз, на первую ПОДПИСКУ; продления и пакеты очков идут по прайсу.
    promo = _norm_code(body.get("promo") or "")
    if promo:
        _attach_ref(db, user, promo)
    amb = db.get(User, user.referred_by) if user.referred_by else None
    discount_pct = 0
    if kind == "plan" and amb and REF_DISCOUNT_PCT > 0 and _ref_first_payment(db, user):
        discount_pct = REF_DISCOUNT_PCT
    discount_cents = amount_cents * discount_pct // 100
    discount_kopeks = amount_kopeks * discount_pct // 100

    meta_promo = amb.ref_code if amb else ""
    if provider == "stripe":
        coupon = ""
        if discount_pct:
            # У подписки цена рекуррентная, скидать её на первый счёт умеет
            # только купон duration=once — поэтому заводим его на лету.
            try:
                coupon = await stripe_pay.create_coupon(discount_pct, f"{BRAND} referral")
            except Exception as e:  # noqa: BLE001 — и отказ Stripe, и обрыв сети
                # Скидка не должна мешать оплате: без купона человек просто
                # платит полную цену, а не упирается в ошибку.
                log.warning("stripe: купон не создался (%s) — платим без скидки",
                            str(e)[:200])
                coupon, discount_pct, discount_cents = "", 0, 0
        try:
            session = await stripe_pay.create_checkout_session(
                kind=kind, user_id=user.id, title=title, amount_cents=amount_cents,
                success_url=f"{PUBLIC_BASE_URL}/?paid={plan_id or pack_id}"
                            f"&session_id={{CHECKOUT_SESSION_ID}}",
                cancel_url=f"{PUBLIC_BASE_URL}/?checkout=cancelled",
                period=period, plan_id=plan_id, pack_id=pack_id, points=points,
                email=user.email or "", customer_id=user.stripe_customer_id or "",
                coupon_id=coupon, promo=meta_promo,
                ambassador_id=amb.id if amb else 0,
            )
        except stripe_pay.StripeError as e:
            raise ApiError(502, "stripe_failed", str(e))
        except Exception as e:  # noqa: BLE001 — сеть до Stripe тоже отваливается
            log.warning("stripe: сессия не создалась: %s", str(e)[:200])
            raise ApiError(502, "stripe_failed", "Stripe is unreachable, try again.")
        if not session.get("url"):
            raise ApiError(502, "stripe_failed", "Stripe returned no checkout URL.")
        if session.get("customer") and not user.stripe_customer_id:
            user.stripe_customer_id = session["customer"]
            db.commit()
        return {
            "ok": True, "url": session["url"], "provider": "stripe", "currency": "usd",
            "kind": kind, "plan": plan_id, "pack": pack_id, "period": period,
            "payment_id": session["id"],
            "amount_cents": amount_cents - discount_cents, "discount_cents": discount_cents,
            "amount_kopeks": 0, "discount_kopeks": 0,
            "promo": meta_promo,
        }

    # ── ЮKassa: рубли, сохранённая карта и наше автосписание ──
    pay_kopeks = amount_kopeks - discount_kopeks
    import httpx as _httpx
    payload = {
        "amount": {"value": f"{pay_kopeks // 100}.{pay_kopeks % 100:02d}", "currency": "RUB"},
        "capture": True,
        # Сохранённый способ оплаты = подписка: дальше списываем сами.
        # Пакет очков — разовая покупка, карту для него не сохраняем.
        "save_payment_method": kind == "plan",
        "confirmation": {"type": "redirect",
                         "return_url": f"{PUBLIC_BASE_URL}/?paid={plan_id or pack_id}"},
        "description": title,
        # promo и ambassador_id — след для разбора спорных начислений: по
        # платежу в кабинете ЮKassa видно, чей это был реферал.
        "metadata": {"user_id": str(user.id), "plan": plan_id, "kind": kind,
                     "pack": pack_id, "period": period,
                     "promo": meta_promo, "ambassador_id": str(amb.id) if amb else ""},
    }
    async with _httpx.AsyncClient(timeout=30) as client:
        r = await client.post("https://api.yookassa.ru/v3/payments", json=payload,
                              auth=(YOOKASSA_SHOP_ID, YOOKASSA_SECRET),
                              headers={"Idempotence-Key": uuid.uuid4().hex})
    if r.status_code not in (200, 201):
        raise ApiError(502, "yookassa_failed", f"YooKassa refused: {r.text[:200]}")
    data = r.json() or {}
    url = ((data.get("confirmation") or {}).get("confirmation_url") or "")
    if not url:
        raise ApiError(502, "yookassa_failed", "YooKassa returned no payment URL.")
    return {
        "ok": True, "url": url, "provider": "yookassa", "currency": "rub",
        "kind": kind, "plan": plan_id, "pack": pack_id, "period": period,
        "payment_id": data.get("id", ""),
        "amount_kopeks": pay_kopeks, "discount_kopeks": discount_kopeks,
        "amount_cents": 0, "discount_cents": 0,
        "promo": meta_promo,
    }


# ─────────────────────────── вебхук ЮKassa ───────────────────────────

@app.post("/api/billing/webhook")
async def billing_webhook(request: Request, db: Session = Depends(db_session)):
    """Уведомление ЮKassa: по успешной оплате выдаём тариф или пакет очков.

    Событие проверяем обратным запросом в ЮKassa — на вебхук можно прислать
    что угодно, доверять телу нельзя."""
    body = await request.json()
    obj = (body or {}).get("object") or {}
    pay_id = str(obj.get("id") or "")
    if (body or {}).get("event") != "payment.succeeded" or not pay_id:
        return {"ok": True}
    if not _yookassa_enabled():
        return {"ok": True}
    import httpx as _httpx
    async with _httpx.AsyncClient(timeout=30) as client:
        r = await client.get(f"https://api.yookassa.ru/v3/payments/{pay_id}",
                             auth=(YOOKASSA_SHOP_ID, YOOKASSA_SECRET))
    if r.status_code != 200:
        log.warning("вебхук ЮKassa: платёж %s не подтвердился (%s)", pay_id, r.status_code)
        return {"ok": True}
    real = r.json() or {}
    if real.get("status") != "succeeded" or not real.get("paid"):
        return {"ok": True}
    meta = real.get("metadata") or {}
    uid = int(meta.get("user_id") or 0)
    user = db.get(User, uid)
    kind = str(meta.get("kind") or "plan")
    plan_id = str(meta.get("plan") or "")
    pack_id = str(meta.get("pack") or "")
    period = _norm_period(meta.get("period"))
    if not user:
        # Платёж без нашей metadata — деньги пришли, а кому выдавать, неясно.
        # Раньше это был тихий 200 в никуда; теперь хотя бы видно в логах.
        log.warning("вебхук ЮKassa: платёж %s без пользователя (metadata=%r)", pay_id, meta)
        return {"ok": True}
    # Долю амбассадору считаем от ФАКТИЧЕСКИ оплаченной суммы — со скидкой по
    # промокоду, а не от прайса тарифа.
    try:
        paid_value = float(((real.get("amount") or {}).get("value")) or 0)
    except (TypeError, ValueError):
        paid_value = 0.0
    paid_kopeks = int(round(paid_value * 100))
    pm = (real.get("payment_method") or {})
    saved_method = str(pm.get("id") or "") if pm.get("saved") else ""
    granted = _grant_payment(db, user, provider="yookassa", payment_id=pay_id, kind=kind,
                             plan_id=plan_id, period=period, pack_id=pack_id,
                             amount_kopeks=paid_kopeks, currency="RUB",
                             pay_method_id=saved_method)
    if not granted:
        return {"ok": True}
    _ref_reward(db, user, paid_kopeks, _pay_key("yookassa", pay_id))
    return {"ok": True}


# ─────────────────────────── вебхук Stripe ───────────────────────────

@app.post("/api/billing/stripe-webhook")
async def stripe_webhook(request: Request, db: Session = Depends(db_session)):
    """Уведомление Stripe. Подпись проверяется по STRIPE_WEBHOOK_SECRET —
    обратного запроса, как у ЮKassa, тут не нужно: подпись и есть доказательство.

    Событий на один платёж приходит несколько (checkout.session.completed и
    invoice.paid по одному счёту), поэтому ключом идемпотентности берём id
    СЧЁТА: продление и первая оплата подписки одинаково сводятся к нему."""
    raw = await request.body()
    event = stripe_pay.verify_webhook(raw, request.headers.get("stripe-signature", ""))
    if event is None:
        # 400, а не 200: Stripe повторит, а мы увидим проблему в дашборде.
        raise HTTPException(400, "bad stripe signature")
    etype = str(event.get("type") or "")
    obj = ((event.get("data") or {}).get("object")) or {}
    meta = stripe_pay.event_metadata(obj)
    uid = int(meta.get("user_id") or obj.get("client_reference_id") or 0)
    user = db.get(User, uid) if uid else None

    if etype == "checkout.session.completed":
        if str(obj.get("payment_status") or "") not in ("paid", "no_payment_required"):
            return {"ok": True}
        if not user:
            log.warning("stripe: сессия %s без пользователя (metadata=%r)",
                        obj.get("id"), meta)
            return {"ok": True}
        mode = str(obj.get("mode") or "")
        amount_cents = int(obj.get("amount_total") or 0)
        sub_id = stripe_pay.subscription_id_of(obj)
        customer = str(obj.get("customer") or "")
        if mode == "subscription":
            invoice = obj.get("invoice")
            inv_id = invoice if isinstance(invoice, str) else str(
                (invoice or {}).get("id") or "")
            # Ключ первого периода подписки — САМА ПОДПИСКА, а не счёт: поле
            # invoice у сессии бывает пустым, и тогда этот же платёж приезжал
            # событием invoice.paid под своим id и выдавался второй раз.
            pay_id = _stripe_first_key(sub_id) or inv_id or str(obj.get("id") or "")
            granted = _grant_payment(
                db, user, provider="stripe", payment_id=pay_id, kind="plan",
                plan_id=str(meta.get("plan") or ""), period=_norm_period(meta.get("period")),
                amount_cents=amount_cents, currency="USD",
                stripe_customer=customer, stripe_subscription=sub_id,
                alt_ids=(inv_id, str(obj.get("id") or "")))
        else:
            # Разовая покупка очков: ключ — id платёжного намерения.
            pi = obj.get("payment_intent")
            pay_id = pi if isinstance(pi, str) else str(
                (pi or {}).get("id") or obj.get("id") or "")
            granted = _grant_payment(
                db, user, provider="stripe", payment_id=pay_id, kind="topup",
                pack_id=str(meta.get("pack") or ""), amount_cents=amount_cents,
                currency="USD", stripe_customer=customer)
        if granted:
            _ref_reward(db, user, _reward_kopeks(0, amount_cents),
                        _pay_key("stripe", pay_id))
        return {"ok": True}

    if etype in ("invoice.paid", "invoice.payment_succeeded"):
        # Продление подписки: Stripe списывает сам, нам остаётся выдать месяц.
        if not user:
            log.warning("stripe: счёт %s без пользователя (metadata=%r)", obj.get("id"), meta)
            return {"ok": True}
        if str(obj.get("status") or "paid") != "paid":
            return {"ok": True}
        amount_cents = int(obj.get("amount_paid") or obj.get("amount_due") or 0)
        inv_id = str(obj.get("id") or "")
        sub_id = stripe_pay.subscription_id_of(obj)
        # Первый счёт подписки — тот же платёж, что и checkout.session.completed:
        # ключуем его подпиской, чтобы два события сошлись в одну выдачу.
        # Продление (subscription_cycle) — уже свой платёж, ключ у него свой.
        first_key = _stripe_first_key(sub_id) if _stripe_is_first_invoice(db, obj, sub_id) else ""
        pay_id = first_key or inv_id
        granted = _grant_payment(
            db, user, provider="stripe", payment_id=pay_id, kind="plan",
            plan_id=str(meta.get("plan") or ""), period=_norm_period(meta.get("period")),
            amount_cents=amount_cents, currency="USD",
            stripe_customer=str(obj.get("customer") or ""),
            stripe_subscription=sub_id, alt_ids=(inv_id,) if first_key else ())
        if granted:
            # Доля амбассадора идёт с КАЖДОГО платежа реферала, включая продления.
            _ref_reward(db, user, _reward_kopeks(0, amount_cents),
                        _pay_key("stripe", pay_id))
        return {"ok": True}

    if etype == "customer.subscription.deleted":
        # Подписка закрыта на стороне Stripe (отмена или неоплата). Доступ не
        # отбираем: он доработает до plan_until, дальше человека снимет воркер.
        sub_id = str(obj.get("id") or "")
        target = user or (db.query(User)
                          .filter(User.stripe_subscription_id == sub_id).first()
                          if sub_id else None)
        if target:
            target.stripe_subscription_id = ""
            target.autopay = False
            db.commit()
            log.info("stripe: подписка %s закрыта, автопродление у юзера %s снято",
                     sub_id, target.id)
        return {"ok": True}

    return {"ok": True}


# ────────────────── автопродление ЮKassa (у Stripe своё) ──────────────────

async def _charge_subscription(db: Session, user: "User") -> str:
    """Автосписание за следующий период по сохранённой карте ЮKassa.

    Возвращает "ok" | "pending" | "fail". Отдельный pending важен: при 3DS и
    асинхронном подтверждении платёж приходит позже вебхуком, и считать это
    провалом (как раньше) значило сбросить человеку тариф на ровном месте."""
    plan = PLANS.get(user.plan or "free")
    period = _norm_period(user.plan_period)
    if not plan or plan["usd_cents"] <= 0 or not user.pay_method_id or not user.autopay:
        return "fail"
    if user.stripe_subscription_id:
        return "pending"  # этого продлевает сам Stripe, руками не лезем
    _, amount_kopeks = _plan_price(user.plan, period)
    if amount_kopeks <= 0:
        return "fail"
    # Idempotence-Key ДЕТЕРМИНИРОВАННЫЙ: если ЮKassa списала, а ответ до нас не
    # дошёл (таймаут сети), повтор через час вернёт ТОТ ЖЕ платёж, а не спишет
    # с карты второй раз. Раньше тут был свежий uuid на каждый заход.
    seed = f"sub:{user.id}:{user.plan}:{period}:{_as_utc(user.plan_until) or now():%Y-%m-%d}"
    idem = uuid.uuid5(uuid.NAMESPACE_URL, seed).hex
    import httpx as _httpx
    payload = {
        "amount": {"value": f"{amount_kopeks // 100}.{amount_kopeks % 100:02d}",
                   "currency": "RUB"},
        "capture": True,
        "payment_method_id": user.pay_method_id,
        "description": f"{BRAND} {plan['title']} — renewal",
        "metadata": {"user_id": str(user.id), "plan": user.plan, "kind": "plan",
                     "period": period},
    }
    async with _httpx.AsyncClient(timeout=40) as client:
        r = await client.post("https://api.yookassa.ru/v3/payments", json=payload,
                              auth=(YOOKASSA_SHOP_ID, YOOKASSA_SECRET),
                              headers={"Idempotence-Key": idem})
    data = (r.json() or {}) if r.status_code in (200, 201) else {}
    status = str(data.get("status") or "")
    if status == "succeeded":
        pay_id = str(data.get("id") or "")
        # Этот же платёж приедет ещё и вебхуком: выдача идемпотентна по id,
        # поэтому неважно, кто из них успеет первым.
        if _grant_payment(db, user, provider="yookassa", payment_id=pay_id, kind="plan",
                          plan_id=user.plan, period=period, amount_kopeks=amount_kopeks,
                          currency="RUB"):
            _ref_reward(db, user, amount_kopeks, _pay_key("yookassa", pay_id))
        return "ok"
    if status in ("pending", "waiting_for_capture"):
        log.info("подписка юзера %s: платёж в статусе %s — ждём вебхук", user.id, status)
        return "pending"
    log.warning("не продлилась подписка юзера %s: %s", user.id, r.text[:200])
    return "fail"


def _subscription_pass() -> None:
    """Один проход по подпискам, которым пора продлиться.

    Проход делает ДВЕ вещи: списывает по карте ЮKassa и снимает с тарифа тех,
    у кого срок вышел. Раньше он весь целиком выключался без ключей ЮKassa —
    и на международном контуре (только Stripe) закончившиеся подписки не
    снимались НИКОГДА: отменил в Stripe, а PRO остаётся навсегда."""
    import asyncio as _asyncio
    from datetime import timedelta
    db = SessionLocal()
    try:
        due = db.query(User).filter(User.plan != "free", User.plan_until.isnot(None),
                                    User.plan_until <= now()).all()
        for u in due:
            if u.stripe_subscription_id:
                continue  # продлевает Stripe своим счётом
            res = "fail"
            if _yookassa_enabled() and u.pay_method_id:
                try:
                    res = _asyncio.run(_charge_subscription(db, u))
                except Exception as e:  # noqa: BLE001
                    res = "fail"
                    log.warning("ошибка автосписания у %s: %s", u.id, str(e)[:150])
            if res in ("ok", "pending"):
                continue
            # Не списалось — даём отсрочку: карта могла не ответить один раз,
            # а сбрасывать тариф и стирать способ оплаты (как было раньше)
            # значит терять человека из-за чужого таймаута.
            until = _as_utc(u.plan_until)
            if until and until + timedelta(days=SUB_GRACE_DAYS) > now():
                continue
            u.plan = "free"
            u.plan_period = "month"
            db.commit()
            log.info("подписка юзера %s закончилась — вернули на free (карту сохранили)", u.id)
    finally:
        db.close()


def _subscription_worker() -> None:
    """Раз в час проверяет, кому пора продлить подписку, и списывает.

    Сначала работаем, потом спим: раньше цикл начинался со сна, и после каждого
    рестарта контейнера продления ждали лишний час."""
    while True:
        try:
            _subscription_pass()
        except Exception as e:  # noqa: BLE001
            log.warning("проход по подпискам упал: %s", str(e)[:200])
        time.sleep(3600)


@app.post("/api/billing/cancel")
async def billing_cancel(user: User = Depends(current_user), db: Session = Depends(db_session)):
    """Отключить автопродление — тариф доработает до конца оплаченного срока."""
    user.autopay = False
    db.commit()
    stripe_ok = True
    if user.stripe_subscription_id and _stripe_enabled():
        try:
            await stripe_pay.cancel_subscription(user.stripe_subscription_id)
        except Exception as e:  # noqa: BLE001
            stripe_ok = False
            log.warning("stripe: не отменилась подписка %s: %s",
                        user.stripe_subscription_id, str(e)[:200])
    return {"ok": True, "stripe_cancelled": stripe_ok,
            "plan": _plan_of(user), "period": user.plan_period or "month",
            "until": user.plan_until.isoformat() if user.plan_until else ""}


# ─────────────────────── кабинет амбассадора ───────────────────────

def _payout_dict(p: Payout) -> dict:
    return {
        "id": p.id, "amount_kopeks": p.amount_kopeks,
        "amount": round(p.amount_kopeks / 100, 2),
        "details": p.details, "status": p.status, "comment": p.comment,
        "created_at": p.created_at.isoformat() if p.created_at else "",
        "updated_at": p.updated_at.isoformat() if p.updated_at else "",
    }


def _ref_event_dict(e: RefEvent, who: str) -> dict:
    return {
        "id": e.id, "kind": e.kind, "who": who,
        "amount_kopeks": e.amount_kopeks, "reward_kopeks": e.reward_kopeks,
        "created_at": e.created_at.isoformat() if e.created_at else "",
    }


@app.post("/api/ambassador/join")
def ambassador_join(user: User = Depends(current_user), db: Session = Depends(db_session)):
    """Подключить партнёрку. Повторный вызов безопасен и возвращает ТОТ ЖЕ код:
    выданный код перевыпускать нельзя — ссылки на него уже разошлись по чатам."""
    if not user.ref_code:
        user.ref_code = _new_ref_code(db)
    user.is_ambassador = True
    db.commit()
    return {"ok": True, "code": user.ref_code, "link": _ref_link(user.ref_code),
            "discount_pct": REF_DISCOUNT_PCT, "reward_pct": REF_REWARD_PCT}


@app.get("/api/ambassador")
def ambassador_cabinet(user: User = Depends(current_user), db: Session = Depends(db_session)):
    """Кабинет: код со ссылкой, деньги, лента событий и заявки на выплату.

    Не 403 для неподключённых — фронту нужно на что-то рисовать кнопку
    «стать амбассадором», поэтому отвечаем нулями и is_ambassador=false."""
    stats = _ref_stats(db, user) if user.is_ambassador else {
        "invited": 0, "buyers": 0, "turnover_kopeks": 0, "accrued_kopeks": 0,
        "paid_kopeks": 0, "reserved_kopeks": 0, "available_kopeks": 0,
    }
    events = (db.query(RefEvent).filter(RefEvent.ambassador_id == user.id)
              .order_by(RefEvent.id.desc()).limit(20).all())
    # Имена рефералов достаём одним запросом: 20 отдельных get'ов на каждый
    # показ кабинета — лишняя работа на ровном месте.
    names = {}
    ids = {e.referral_id for e in events}
    if ids:
        names = {u.id: (u.name or "") for u in
                 db.query(User).filter(User.id.in_(ids)).all()}
    payouts = (db.query(Payout).filter(Payout.ambassador_id == user.id)
               .order_by(Payout.id.desc()).limit(50).all())
    return {
        "is_ambassador": bool(user.is_ambassador),
        "code": user.ref_code or "",
        "link": _ref_link(user.ref_code),
        "discount_pct": REF_DISCOUNT_PCT,
        "reward_pct": REF_REWARD_PCT,
        "min_payout_kopeks": REF_MIN_PAYOUT_KOPEKS,
        "payout_details": user.payout_details or "",
        "stats": stats,
        "events": [_ref_event_dict(e, names.get(e.referral_id) or f"гость #{e.referral_id}")
                   for e in events],
        "payouts": [_payout_dict(p) for p in payouts],
    }


@app.post("/api/ambassador/details")
async def ambassador_details(request: Request, user: User = Depends(current_user),
                             db: Session = Depends(db_session)):
    """Реквизиты выплаты (карта, СБП, что угодно текстом) — их читает глазами
    владелец, когда переводит деньги руками."""
    body = await request.json()
    details = str(body.get("details") or "").strip()[:500]
    if not details:
        raise HTTPException(400, "укажи реквизиты для выплаты")
    user.payout_details = details
    db.commit()
    return {"ok": True, "details": details}


@app.post("/api/ambassador/payout")
async def ambassador_payout(request: Request, user: User = Depends(current_user),
                            db: Session = Depends(db_session)):
    """Заявка на выплату. Сумма сразу уходит в резерв (заявка в статусе new),
    поэтому заказать одни и те же деньги второй заявкой не выйдет."""
    if not user.is_ambassador:
        raise HTTPException(403, "сначала подключи партнёрку")
    body = await request.json()
    details = str(body.get("details") or "").strip()[:500] or (user.payout_details or "")
    if not details:
        raise HTTPException(400, "укажи реквизиты для выплаты")
    try:
        amount = int(body.get("amount_kopeks") or 0)
    except (TypeError, ValueError):
        raise HTTPException(400, "сумма не похожа на число")
    available = _ref_stats(db, user)["available_kopeks"]
    if available <= 0:
        # Отдельным сообщением, иначе человек видит «минимальная выплата» и
        # думает, что дело в сумме, а не в том, что деньги уже заказаны.
        raise HTTPException(400, "доступных к выплате начислений пока нет")
    if amount <= 0:
        amount = available  # без суммы забирают всё доступное
    if amount > available:
        raise HTTPException(400, f"доступно только {available // 100} ₽")
    if amount < REF_MIN_PAYOUT_KOPEKS:
        raise HTTPException(400, f"минимальная выплата — {REF_MIN_PAYOUT_KOPEKS // 100} ₽")
    user.payout_details = details
    payout = Payout(ambassador_id=user.id, amount_kopeks=amount, details=details,
                    status="new")
    db.add(payout)
    db.commit()
    db.refresh(payout)
    # Двойной клик или вторая вкладка могли завести две заявки на одни деньги:
    # пересчитываем уже ПОСЛЕ коммита и свою заявку отзываем, если ушли в минус.
    fresh = _ref_stats(db, user)
    if fresh["accrued_kopeks"] - fresh["paid_kopeks"] - fresh["reserved_kopeks"] < 0:
        db.delete(payout)
        db.commit()
        raise HTTPException(409, "заявка на эти деньги уже создана — обнови страницу")
    log.info("партнёрка: заявка на выплату %s коп. от амбассадора %s", amount, user.id)
    return {"ok": True, "payout": _payout_dict(payout), "stats": fresh}


@app.get("/api/admin/payouts")
def admin_payouts(status: str = "", user: User = Depends(current_user),
                  db: Session = Depends(db_session)):
    """Очередь выплат для владельца: кому и сколько перевести руками."""
    if not user.is_admin:
        raise HTTPException(403, "только для админа")
    q = db.query(Payout).order_by(Payout.id.desc())
    if status:
        q = q.filter(Payout.status == status)
    rows = q.limit(200).all()
    amb_ids = {p.ambassador_id for p in rows}
    ambs = {u.id: u for u in db.query(User).filter(User.id.in_(amb_ids)).all()} if amb_ids else {}
    out = []
    for p in rows:
        amb = ambs.get(p.ambassador_id)
        item = _payout_dict(p)
        item["ambassador"] = {
            "id": p.ambassador_id,
            "name": (amb.name if amb else "") or f"#{p.ambassador_id}",
            "code": amb.ref_code if amb else "",
            "email": amb.email if amb else "",
            "tg": amb.tg_username if amb else "",
        }
        out.append(item)
    return {"payouts": out}


@app.post("/api/admin/payouts/{payout_id}")
async def admin_payout_update(payout_id: int, request: Request,
                              user: User = Depends(current_user),
                              db: Session = Depends(db_session)):
    """Пометить заявку выплаченной или отклонить.

    Двигать можно только заявку из new — иначе повторный клик по «выплачено»
    удвоил бы счётчик выплат. Отклонение денег не возвращает специально:
    сумма перестаёт быть зарезервированной и сама попадает в доступные."""
    if not user.is_admin:
        raise HTTPException(403, "только для админа")
    body = await request.json()
    status = str(body.get("status") or "").strip()
    if status not in ("paid", "rejected"):
        raise HTTPException(400, "статус: paid или rejected")
    payout = db.get(Payout, payout_id)
    if not payout:
        raise HTTPException(404, "заявка не найдена")
    if payout.status != "new":
        raise HTTPException(400, f"заявка уже обработана: {payout.status}")
    payout.status = status
    payout.comment = str(body.get("comment") or "").strip()[:500]
    payout.updated_at = now()
    db.flush()  # autoflush выключен: сумму считаем уже с новым статусом
    amb = db.get(User, payout.ambassador_id)
    if amb:
        # Счётчик «выплачено» на юзере — зеркало таблицы заявок, а не отдельная
        # правда: пересчитываем его от оплаченных заявок.
        amb.ref_paid_kopeks = int(
            db.query(func.coalesce(func.sum(Payout.amount_kopeks), 0))
            .filter(Payout.ambassador_id == amb.id, Payout.status == "paid").scalar() or 0)
    db.commit()
    log.info("партнёрка: заявка %s → %s (амбассадор %s)", payout.id, status,
             payout.ambassador_id)
    return {"ok": True, "payout": _payout_dict(payout)}


@app.get("/api/account")
def account(user: User = Depends(current_user), db: Session = Depends(db_session)):
    """Личный кабинет: тариф, срок, очки, привязки входа, проекты."""
    plan = _plan_of(user)
    projects = db.query(Project).filter(Project.owner_id == user.id).count()
    # Партнёрка коротко — на карточку кабинета; подробности в /api/ambassador.
    # Для неподключённых считать нечего, поэтому лишних запросов не делаем.
    ref_stats = _ref_stats(db, user) if user.is_ambassador else None
    return {
        "name": user.name, "email": user.email, "login": user.login,
        "avatar_url": user.avatar_url,
        "plan": plan, "plan_title": PLANS[plan]["title"],
        "plan_note": PLANS[plan]["note"],
        "plan_period": user.plan_period or "month",
        "plan_until": user.plan_until.isoformat() if user.plan_until else "",
        # Автопродление живёт в двух местах: карта ЮKassa у нас, подписка — у Stripe.
        "autopay": bool(user.autopay and (user.pay_method_id or user.stripe_subscription_id)),
        "pay_provider": ("stripe" if user.stripe_subscription_id
                         else ("yookassa" if user.pay_method_id else "")),
        "points": user.gen_points, "projects": projects,
        # Сколько клипов по 3 минуты ещё выйдет из остатка. Считаем по рабочей
        # лошадке тарифа (самый дешёвый ПЛАТНЫЙ движок): по самому дорогому на
        # PRO MAX выходит «0 клипов», хотя на Seedance 2 Mini их три.
        "movies_left": _movies_estimate(int(user.gen_points or 0),
                                        _plan_work_cost(plan)),
        "movies_left_top": _movies_estimate(int(user.gen_points or 0),
                                            max(_plan_engines(plan).values())),
        "linked": {"telegram": bool(user.tg_id), "yandex": bool(user.yandex_id),
                   "google": bool(user.google_id), "password": bool(user.login)},
        "ambassador": {
            "is_ambassador": bool(user.is_ambassador),
            "ref_code": user.ref_code or "",
            "link": _ref_link(user.ref_code),
            # balance — доступное к выплате (начислено минус выплаты и заявки).
            "balance_kopeks": ref_stats["available_kopeks"] if ref_stats else 0,
            "balance": round(ref_stats["available_kopeks"] / 100, 2) if ref_stats else 0,
            "referrals": ref_stats["invited"] if ref_stats else 0,
        },
    }


@app.get("/api/health")
def health():
    return {"ok": True, "ts": int(time.time())}


def _backfill_scene_ledger() -> None:
    """Проставить счётчик оплаты сценам, сделанным ДО посценовой тарификации.

    У старых сцен charged_points = 0, и без этого они выглядят неоплаченными:
    супергенерация и пакетные кнопки взяли бы за уже готовые кадры и видео
    деньги второй раз. Ставим прежний прайс (видео 10, кадры 2) — ровно то,
    что за них уже списали. Работает поверх мягкой миграции и сама себя
    исчерпывает: у новых сцен счётчик заполняется в момент списания."""
    from sqlalchemy import text as _sqltext
    from db import engine as _engine
    try:
        with _engine.begin() as conn:
            done = conn.execute(_sqltext(
                "UPDATE scenes SET charged_points = 10 "
                "WHERE charged_points = 0 AND video_filename != ''")).rowcount
            done += conn.execute(_sqltext(
                "UPDATE scenes SET charged_points = 2 "
                "WHERE charged_points = 0 AND image_filename != ''")).rowcount
        if done:
            log.info("бэкфилл оплаты сцен: отмечено %s старых сцен", done)
    except Exception as e:  # noqa: BLE001
        # Не критично для работы сервиса — но знать об этом надо.
        log.warning("бэкфилл оплаты сцен не прошёл: %s", str(e)[:200])


_backfill_scene_ledger()


# ─────────────────────────────── статика (SPA) ───────────────────────────────

# Фоновая проверка подписок: раз в час смотрим, кому пора продлить.
from threading import Thread as _Thread  # noqa: E402
_Thread(target=_subscription_worker, daemon=True).start()


FRONTEND_DIR = os.environ.get("FRONTEND_DIR", "/app/static")
if os.path.isdir(FRONTEND_DIR):
    app.mount("/", StaticFiles(directory=FRONTEND_DIR, html=True), name="static")
