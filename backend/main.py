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
from datetime import datetime, timedelta, timezone

from fastapi import Depends, FastAPI, Form, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from itsdangerous import BadSignature, SignatureExpired, URLSafeTimedSerializer
from sqlalchemy import func
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

import claude
import formats
import learn
import mediagen
import prompts_catalog
import stripe_pay
from db import (
    AttributePhoto, Character, CharacterAttribute, CharacterPhoto, Doc, FileOwner,
    Payout, PointEvent, ProcessedPayment, Project, RefEvent, Scene, SceneRef,
    SessionLocal, Track, TrackPhoto, User, init_db, now,
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


def _bearer_token(request: Request) -> str:
    """Тот же подписанный {"uid": …}, но заголовком.

    Нужен ровно одному контуру — мини-аппу Telegram: в Desktop и Web он живёт
    в iframe, запросы к нам кросс-сайтовые, и кука с SameSite=Lax не поедет ни
    в fetch, ни куда-либо ещё — человек оставался бы вечно неавторизованным.
    CSRF от этого не появляется: заголовок браузер кросс-сайтово сам не
    подставляет, его выставляет только наш собственный код."""
    raw = request.headers.get("authorization") or ""
    return raw[7:].strip() if raw[:7].lower() == "bearer " else ""


def _resolve_user(request: Request, db: Session) -> User | None:
    """qv_session ({"uid": …}) или легаси rc_session ({"ok": True} → админ)."""
    for token in (request.cookies.get(QV_COOKIE), _bearer_token(request)):
        if not token:
            continue
        try:
            data = signer.loads(token, max_age=QV_MAX_AGE)
            user = db.get(User, int(data.get("uid") or 0))
            if user:
                return user
        except (BadSignature, ValueError, TypeError, AttributeError):
            continue
    legacy = request.cookies.get(COOKIE_NAME)
    if legacy:
        try:
            data = signer.loads(legacy, max_age=COOKIE_MAX_AGE)
            if isinstance(data, dict) and data.get("ok"):
                return _admin_user(db)
        except BadSignature:
            pass
    return None


# Как часто отмечаем «человек был». НЕ на каждый запрос: фронт поллит
# /api/me раз в три секунды, база — SQLite, и UPDATE на каждый опрос дал бы
# блокировки на ровном месте. Пять минут отвечают на вопрос «когда был»
# ровно так же, а стоят в сто раз дешевле.
LAST_SEEN_EVERY_S = int(os.environ.get("LAST_SEEN_EVERY_S", "300"))


def _touch_seen(db: Session, user: User) -> None:
    last = _as_utc(getattr(user, "last_seen_at", None))
    if last and (now() - last).total_seconds() < LAST_SEEN_EVERY_S:
        return
    try:
        user.last_seen_at = now()
        db.commit()
    except Exception:  # noqa: BLE001
        db.rollback()


def _guard_user(user: User) -> User:
    """Блокировку проверяем НА ВХОДЕ КАЖДОГО контура, а не в интерфейсе: иначе
    она косметическая — заблокированный продолжает жечь наши деньги через
    /api/scenes/*/generate-video или через чат."""
    if getattr(user, "is_blocked", False):
        raise ApiError(403, "blocked",
                       (user.blocked_reason or "").strip()
                       or "аккаунт заблокирован — напиши в поддержку")
    return user


def current_user(request: Request, db: Session = Depends(db_session)) -> User:
    user = _resolve_user(request, db)
    if not user:
        raise HTTPException(401, "не авторизован")
    _guard_user(user)
    _touch_seen(db, user)
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


def _reset_orphan_jobs() -> None:
    """Сбросить статусы задач, чьи потоки не пережили перезапуск.

    Генерации живут в daemon-тредах: при рестарте контейнера (а он бывает при
    каждом деплое) поток исчезает, а строка в базе остаётся в 'running' — и
    кнопка навсегда превращается в «рисую лист…». Человек ждёт того, чего уже
    никто не делает. На старте честно помечаем такие задачи прерванными,
    чтобы их можно было запустить заново."""
    db = SessionLocal()
    try:
        note = "прервано перезапуском сервиса — запусти заново"
        n = 0
        for model, pairs in (
            (Track, (("scenes_status", "scenes_error"),
                     ("storyboard_status", "storyboard_error"),
                     ("clip_status", "clip_error"),
                     ("supergen_status", "supergen_note"))),
            (Scene, (("image_status", "image_error"),
                     ("video_status", "video_error"))),
            (Project, (("story_status", "story_error"),)),
        ):
            for col, err in pairs:
                rows = db.query(model).filter(
                    getattr(model, col).in_(("queued", "running"))).all()
                for row in rows:
                    setattr(row, col, "error")
                    if hasattr(row, err):
                        setattr(row, err, note)
                    n += 1
        if n:
            db.commit()
            log.info("сброшено зависших задач после рестарта: %s", n)
    except Exception as e:  # noqa: BLE001 — старт сервиса важнее уборки
        log.warning("не смог сбросить зависшие задачи: %s", e)
    finally:
        db.close()


_reset_orphan_jobs()


def _reg_file(db: Session, filename: str, owner_id: int | None, *,
              kind: str = "", project_id: int = 0, track_id: int = 0,
              scene_id: int = 0) -> None:
    """Каждый создаваемый файл приписывается владельцу: без записи в FileOwner
    файл из /api/media увидит только админ.

    Здесь же пишутся метаданные АРХИВА — дата, вид, проект/объект/кадр и
    размер. Раньше в таблице было две колонки, и «папка со всеми файлами,
    разложенная по датам, видам и проектам» строиться из неё не могла.

    Обновляем ПОЛЯМИ, а не db.merge: merge подставляет пустые значения
    остальных колонок, и повторная регистрация файла (та же сцена, второй
    прогон) стирала бы его вид и привязку к проекту."""
    if not filename:
        return
    fname = os.path.basename(filename)
    row = db.get(FileOwner, fname)
    if row is None:
        row = FileOwner(filename=fname, user_id=int(owner_id or 0), created_at=now())
        db.add(row)
    else:
        row.user_id = int(owner_id or 0)
    if kind:
        row.kind = kind
    if project_id:
        row.project_id = int(project_id)
    if track_id:
        row.track_id = int(track_id)
    if scene_id:
        row.scene_id = int(scene_id)
    row.deleted_at = None
    if not row.size_bytes:
        try:
            row.size_bytes = os.path.getsize(os.path.join(UPLOAD_DIR, fname))
        except OSError:
            row.size_bytes = 0


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
# ВАЖНО про экономику. Токен привязано к деньгам ровно одной константой
# POINT_USD (ниже): столько СЕБЕСТОИМОСТИ мы кладём в одно токен. Отсюда:
#   норма тарифа × POINT_USD = максимум, который тариф может стоить нам за месяц.
# Это худший случай — человек спускает все токены на самый дорогой движок. По
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
        # 150, а не 120: клип 3 минуты = 30 сцен × 4 = ровно 120, то есть запас
        # был нулевой — лист раскадровки (2), моделька героя (2) или одна
        # пересборка сцены (4) упирали человека в 402 на последней сцене, и
        # обещание «один полный клип» не выполнялось. Шлюзовые движки стоят
        # нам ноль, поэтому щедрость здесь бесплатная.
        "title": "FREE", "usd_cents": 0, "points": 150,
        "video": ["grok"], "engines": {"grok": "grok"}, "extra_engines": [],
        # FREE обязан остаться на шлюзовых кадрах: Nano Banana для всех
        # означала бы $3.60 живых денег за каждого зарегистрировавшегося.
        "image_engine": "chatgpt",
        "priority": False, "badge": "",
        "note": "One full 3-minute clip on us — Grok engine",
        "features": [
            # 150, а не 120: витрина обязана называть ту же цифру, которую
            # человек увидит на счётчике. Ровно на этой строке обещание и
            # ломалось — «120 токенов, одного клипа хватит» при клипе ценой
            # ровно 120 и листе раскадровки сверху.
            "150 points — a full 3-minute clip, with room to redo a scene",
            "Grok engine: animates the first frame of every scene",
            "Story, storyboard, characters and one-click assembly",
        ],
    },
    "pro": {
        "title": "PRO", "usd_cents": int(os.environ.get("PRICE_PRO_USD", "20")) * 100,
        # 660 токенов = ровно один трёхминутный клип на Seedance 2 Mini
        # (30 сцен × 22 токена). Раньше было 700 токенов при цене сцены 10 —
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
        # 2400 → 3400 токенов (+42 %). Больше дать нельзя: 3400 × POINT_USD =
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
        # ULTRA — тот же plan_id "studio". Ключ НЕ переименован сознательно:
        # он лежит в базе у живых подписчиков, в metadata обеих платёжек и в
        # вебхуках. Переименование стоило бы миграции и сломанных продлений,
        # а меняется здесь вывеска, а не идентификатор.
        "title": "ULTRA", "usd_cents": int(os.environ.get("PRICE_STUDIO_USD", "299")) * 100,
        # 10500 токенов = два полных клипа на Seedance 2.5 (62 сцены) или шесть
        # на Kling 3.0 Pro. Целый клип на самой дорогой модели физически
        # помещается только сюда. Это ПЕРВАЯ ступень шкалы (см. PLAN_TIERS):
        # у действующих подписчиков STUDIO ничего не меняется.
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
        "note": "Album-scale volume on every engine — pick your monthly volume",
        "features": [
            "From 10500 to 104000 points a month — you choose the step",
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

# ───────────────────── ULTRA: ступени объёма верхнего тарифа ─────────────────
#
# Верхний тариф продаётся не одной цифрой, а шкалой: человек двигает ползунок
# и выбирает месячный объём. Пятого тарифа сознательно НЕ заводим — витрина
# рисует четыре колонки, а четыре варианта человек ещё сравнивает, пять уже нет.
#
# Якорь ступеней — ЦЕЛЫЙ КЛИП НА ФЛАГМАНЕ. На ULTRA кадры рисует Nano Banana
# Pro (пара 15 токенов), сцена на Seedance 2.5 720p = 15 + 152 = 167 токенов,
# клип 3 минуты = 30 сцен = 5010 токенов. Каждая ступень — целое число таких
# клипов: 2 / 5 / 10 / 20. Отсюда и цифры токенов, они не подогнаны «покруглее».
#
# ПОЧЕМУ МАРЖА НЕ МОЖЕТ УЕХАТЬ ВНИЗ. VIDEO_COST/FRAME_COST считаются как
# ceil(себестоимость / POINT_USD) — токен физически не может стоить нам дороже
# POINT_USD ($0.0125) ни на одном движке. Худший случай ступени =
# points × POINT_USD, и он заложен в цену:
#   u1  10500 → $131 из $299 (маржа 56 %)   u3  52000 → $650 из $1199 (46 %)
#   u2  26000 → $325 из $659 (51 %)         u4 104000 → $1300 из $2149 (40 %)
#
# top=True — ступень за флагом ULTRA_TOP_TIERS. Продажа u4 создаёт
# обязательство на $1300 живых генераций от ОДНОГО человека в месяц, а баланс
# kie.ai маленький: верхние ступени открываем, когда баланс это выдерживает.
PLAN_TIERS = {
    "studio": [
        {"id": "u1", "points": 10500,  "usd_cents": 29900,  "top": False},
        {"id": "u2", "points": 26000,  "usd_cents": 65900,  "top": False},
        {"id": "u3", "points": 52000,  "usd_cents": 119900, "top": True},
        {"id": "u4", "points": 104000, "usd_cents": 214900, "top": True},
    ],
}
ULTRA_TOP_TIERS = os.environ.get("ULTRA_TOP_TIERS", "1") not in ("0", "false", "no")

# ПОЛ ЦЕНЫ ОЧКА. Объёмная скидка и годовая −20 % складываются, и на верхней
# ступени год выходил $20630 = 1.65¢ за токен при потолке себестоимости 1.25¢ —
# маржа 24 %, а с реферальной −10 % уже 16 %. Пол в 1.8¢ (маржа 30.6 %) ниже
# себя не пускает НИКАКУЮ скидку. Бьёт он сегодня ровно по годовому u4, и
# витрина честно пишет там −13 %, а не −20 %: процент считается из цены.
# Единственное исключение — реферальная скидка на первый платёж: это разовая
# стоимость привлечения, её пол не трогает (она применяется позже, в кассе).
POINT_PRICE_FLOOR_USD = float(os.environ.get("POINT_PRICE_FLOOR_USD", "0.018"))


def _floor_cents(points: int, cents: int) -> int:
    """Цена не ниже пола за токен. Округляем вверх до целого доллара —
    дробный ценник на витрине выглядит как ошибка вёрстки."""
    floor = int(math.ceil(points * POINT_PRICE_FLOOR_USD * 100))
    return max(int(cents), (floor + 99) // 100 * 100)


for _pid, _tiers in PLAN_TIERS.items():
    _base = _tiers[0]
    _base_per_point = _base["usd_cents"] / _base["points"]
    for _t in _tiers:
        _t["usd_year_cents"] = _floor_cents(_t["points"] * 12,
                                            _year_cents(_t["usd_cents"]))
        _t["rub_kopeks"] = _rub_kopeks(f"{_pid}_{_t['id']}", _t["usd_cents"])
        _t["rub_year_kopeks"] = _rub_kopeks(f"{_pid}_{_t['id']}_year",
                                            _t["usd_year_cents"])
        # Зачёркнутая цена — ЧЕСТНАЯ: тот же объём по цене токена базовой
        # ступени. Не выдуманный «якорь», а число из нашего же прайса,
        # которое человек может проверить делением сам.
        _t["list_usd_cents"] = int(round(_t["points"] * _base_per_point / 100)) * 100
        _t["save_pct"] = max(0, int(round(
            100 - 100 * (_t["usd_cents"] / _t["points"]) / _base_per_point)))
        # Годовая скидка СЧИТАЕТСЯ, а не берётся из YEAR_DISCOUNT_PCT: после
        # пола она уже другая, и обещать −20 % там, где −13 %, нельзя.
        _t["year_discount_pct"] = max(0, int(round(
            100 - 100 * _t["usd_year_cents"] / (_t["usd_cents"] * 12))))

# Пакеты токенов (докупка сверх подписки).
#
# ПОЧИНЕНА ДЫРА. Было: токен в пакете 1.13-2.25¢ против 4.2¢ в PRO MAX — вчетверо
# дешевле, да ещё и без подписки. В таком виде подписку выгоднее было не
# покупать вообще. Стало: САМОЕ дешёвое пакетное токен (3.19¢) дороже САМОГО
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
# ЕДИНСТВЕННАЯ константа, связывающая токены с деньгами: сколько себестоимости
# лежит в одном токене. Всё остальное считается из неё и из долларовых цен
# движков в mediagen.VIDEO_ENGINES/IMAGE_ENGINES — цена в токенах физически не
# может разойтись с тем, что мы платим kie.ai.
POINT_USD = float(os.environ.get("POINT_USD", "0.0125"))
# Шлюзы владельца стоят нам ноль, но даром отдавать их нельзя: без ценника
# перерисовка кадров становится бесконечным насосом по чужой подписке.
# 2 токена — символическая плата ровно за это.
GATEWAY_POINTS = int(os.environ.get("GATEWAY_POINTS", "2"))
SCENE_SEC = 6              # средняя длина сцены, из claude.py


def _points_of_usd(usd: float) -> int:
    """Доллары себестоимости → токены. Округление ВВЕРХ: недобор токена — это
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


# ───────────────── ступени объёма (ULTRA): одно место правды ─────────────────
# Ступень живёт в users.plan_tier. Пустая строка = ПЕРВАЯ ступень, поэтому все
# действующие подписчики STUDIO валидны без единого UPDATE в базе.

def _tiers_of(plan_id: str, *, visible_only: bool = False) -> list[dict]:
    tiers = PLAN_TIERS.get(plan_id) or []
    if visible_only and not ULTRA_TOP_TIERS:
        return [t for t in tiers if not t["top"]]
    return list(tiers)


def _norm_tier(plan_id: str, tier) -> str:
    """Ступень → её id. Неизвестная, пустая и скрытая флагом — базовая.

    Скрытую флагом ступень нельзя пропускать даже если её прислали руками:
    иначе выключенный ULTRA_TOP_TIERS обходится одной строкой в теле запроса.
    Уже КУПЛЕННУЮ ступень флаг при этом не отбирает — для чтения баланса и
    продления используется _tier_spec, а не эта функция."""
    tiers = _tiers_of(plan_id)
    if not tiers:
        return ""
    want = str(tier or "").strip().lower()
    allowed = {t["id"] for t in _tiers_of(plan_id, visible_only=True)}
    return want if want in allowed else tiers[0]["id"]


def _tier_spec(plan_id: str, tier) -> dict | None:
    """Описание ступени по её id — БЕЗ фильтра видимости: купленное продлевается
    по своей цене, даже если ступень сняли с витрины."""
    want = str(tier or "").strip().lower()
    for t in _tiers_of(plan_id):
        if t["id"] == want:
            return t
    tiers = _tiers_of(plan_id)
    return tiers[0] if tiers else None


def _plan_points(plan_id: str, tier: str = "") -> int:
    """Месячная норма токенов тарифа с учётом ступени."""
    spec = _tier_spec(plan_id, tier)
    if spec:
        return int(spec["points"])
    return int(PLANS[plan_id]["points"])


def _tier_of_user(user: "User") -> str:
    """Ступень человека. У тарифа без шкалы — пустая строка."""
    plan_id = _plan_of(user)
    if not PLAN_TIERS.get(plan_id):
        return ""
    spec = _tier_spec(plan_id, getattr(user, "plan_tier", "") or "")
    return spec["id"] if spec else ""


def _plan_image_engine(user: "User | None", want: str = "") -> str:
    """Движок КАДРОВ этого человека: дефолт тарифа, опущенный до реально
    живого. Нет KIE_API_KEY — тихо работаем на шлюзе (сцена не должна падать
    из-за ненастроенного агрегатора), но врать об этом наверх нельзя.

    want — явный выбор пользователя в карточке кадра. Берём его, только если
    движок вообще существует и открыт тарифом; иначе тихо возвращаем дефолт,
    чтобы бесплатный тариф не рисовал на платной модели."""
    plan = PLANS[_plan_of(user)] if user else PLANS["free"]
    default = plan.get("image_engine") or "chatgpt"
    want = (want or "").strip()
    if want and want in mediagen.IMAGE_ENGINES:
        allowed = set(plan.get("image_engines") or [default])
        # Шлюзовые движки бесплатны для нас — они открыты всем тарифам.
        if want in mediagen.GATEWAY_IMAGE_ENGINES or want in allowed:
            return mediagen.resolve_image_engine(want)
    return mediagen.resolve_image_engine(default)


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


def _image_cost(user: "User", engine: str = "", resolution: str = "") -> int:
    """Цена ОДНОЙ служебной картинки (лист раскадровки, моделька персонажа).
    Раньше была плоской двойкой — на Nano Banana Pro такая картинка стоит нам
    $0.09, и плоская цена превращала витрину персонажей в дыру в кошельке.

    resolution обязателен там, где мы просим НЕ дефолтное разрешение: 4К у
    Nano Banana дороже 2К (у 2-й версии $0.09 против $0.06). Без параметра
    цена считалась по дефолту, и разворот в 4К мы отдавали себе в убыток."""
    eng = engine or _plan_image_engine(user)
    return max(2, _points_of_usd(mediagen.image_engine_usd(eng, resolution)))


def _model_sheet_engine(user: "User") -> str:
    """Движок РАЗВОРОТА персонажа — отдельно от движка кадров.

    Nano Banana 2, а не Pro: для листа из четырёх фигур важнее не «Pro», а
    четырнадцать отдельных референсов против восьми — лист собирается по
    нескольким фото человека сразу. Плюс 4К у неё дешевле ($0.09 против
    $0.12). Ключа kie нет или тариф бесплатный — тихо уходим на шлюз, как
    везде: кнопка должна работать всегда."""
    plan = PLANS[_plan_of(user)]
    paid = bool(mediagen.IMAGE_ENGINES.get(plan.get("image_engine") or "", {}).get("paid"))
    if (paid or user.is_admin) and mediagen.kie_available():
        return mediagen.resolve_image_engine("nano-banana-2")
    return _plan_image_engine(user)


def _frames_cost(user: "User", scene: "Scene | None" = None,
                 engine: str = "") -> int:
    """Цена пары кадров сцены. Если кадры уже нарисованы — по ТОМУ движку,
    которым их реально нарисовали: иначе смена тарифа между кадрами и видео
    ломала бы добор до цены сцены."""
    eng = engine or (scene.image_engine if scene else "") or _plan_image_engine(user)
    return FRAME_COST.get(eng, FRAMES_COST)


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


# ══════════════ РЕЖИМЫ: вид проекта решает, что и чем генерим ══════════════
# Отдельного «реестра режимов» в базе нет и не нужно: Project.kind И ЕСТЬ
# режим (album|single → rap clips, ugc, series). Всё остальное — структура —
# лежит в backend/formats.py и читается отсюда. Новый режим = объект там,
# а не ветка if по всему файлу.

def _mode_of(project: "Project | None") -> dict:
    return formats.mode_of_kind(project.kind if project else "album")


def _catalog_of(project: "Project | None") -> str:
    return _mode_of(project)["format_catalog"]


def _format_key(track: "Track") -> str:
    """Каркас объекта: явный выбор владельца или дефолт режима."""
    catalog = _catalog_of(track.project)
    key = (track.format_key or "").strip()
    if key and formats.format_spec(catalog, key):
        return key
    return formats.default_format(catalog)


def _track_aspect(track: "Track") -> str:
    """Геометрия кадра объекта: явный выбор владельца → аспект режима →
    вертикаль. Раньше «9:16» было константой в шести местах mediagen; у
    мокапа кадр квадратный, и в вертикаль он влезает только с полями."""
    want = (getattr(track, "aspect", "") or "").strip()
    if want in mediagen.ASPECTS:
        return want
    return _mode_of(track.project).get("aspect") or mediagen.DEFAULT_ASPECT


# ══════════════ ДВИЖКИ: один выбор на объект, а не на каждый кадр ══════════════
# Порядок разрешения ОДИН для одиночной генерации, пакетной и супергенерации:
#     явный выбор запроса → движок трека → дефолт тарифа,
# и финальным фильтром всегда тариф (_plan_*_engine), который молча опускает
# недоступное. Раньше выбор жил только в карточке кадра, никуда не сохранялся
# и полностью игнорировался кнопками «все кадры» / «все видео» — они звали
# роут вообще без параметров.

def _resolve_image_engine(user: "User | None", track: "Track | None",
                          want: str = "") -> str:
    return _plan_image_engine(user, want or (track.image_engine if track else ""))


def _resolve_video_engine(user: "User | None", track: "Track | None",
                          provider: str, want: str = "") -> str:
    return _plan_video_engine(user, provider, want or (track.video_engine if track else ""))


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
    """Токены кончились. Не текст «напиши владельцу сервиса», а структура: по ней
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


# ─────────────────────────── журнал токенов ───────────────────────────
# ЕДИНСТВЕННЫЙ источник правды о расходе. До него история существовала только
# в log.info контейнера: _charge менял users.gen_points и писал строчку в лог,
# поэтому кабинет не мог показать ни расход по дням, ни «на что ушло», ни
# возвраты — строить было не из чего.
#
# ОДНА ДВЕРЬ. Раньше контракт «каждое движение токенов попадает в журнал»
# держался ДИСЦИПЛИНОЙ, а не кодом: пять мест мутировали user.gen_points
# напрямую и рядом вручную звали _log_points. Следующая правка про это
# забыла бы, и журнал перестал бы объяснять остаток — молча, а обнаружилось
# бы это через квартал. Теперь право менять user.gen_points есть ТОЛЬКО у
# _move_points, а сторож tools/check_ledger.sh валит сборку, если в коде
# появилось прямое присваивание мимо неё.
#
# Третий уровень защиты — инвариант: /api/admin/ledger/audit сверяет
# SUM(point_events.delta) с фактическим балансом каждого человека. Дыру он
# показывает за сутки, а не через квартал.

# Метка расхода по человеческому описанию операции. Так журнал не требует
# протаскивать лишний параметр через полсотни вызовов _charge, а разбор
# живёт в ОДНОМ месте вместо каждой кнопки.
_KIND_MARKS = (
    ("кадр", "frames"), ("видео", "video"), ("раскадров", "sheet"),
    ("лист", "sheet"), ("модельк", "model"), ("разворот", "model"),
    ("сюжет", "story"), ("сценар", "story"), ("логлайн", "story"),
    ("серию", "story"), ("серии", "story"), ("персон", "story"),
    ("озвуч", "audio"), ("музык", "audio"), ("звук", "audio"),
    ("чат", "chat"), ("сборк", "assemble"), ("клип", "assemble"),
)


def _guess_kind(what: str) -> str:
    low = str(what or "").lower()
    for mark, kind in _KIND_MARKS:
        if mark in low:
            return kind
    return "other"


def _cost_cents(kind: str, engine: str, *, count: int = 1,
                seconds: int = SCENE_SEC, resolution: str = "") -> int:
    """СЕБЕСТОИМОСТЬ операции в центах — сколько мы за неё платим kie.ai.

    Считается из того же прайса движков, из которого выведена цена в токенах
    (mediagen.*_engine_usd), поэтому маржа не может разъехаться с реальностью.
    Шлюзовые движки честно дают ноль: они идут по подписке владельца.

    Наружу это число НЕ отдаётся ни при каких условиях — из него
    восстанавливается наша наценка."""
    try:
        if kind == "video":
            usd = mediagen.video_engine_usd(engine, seconds or SCENE_SEC)
        elif kind == "frames":
            usd = 2 * mediagen.image_engine_usd(engine, resolution)
        elif kind in ("sheet", "model", "midframe", "image"):
            usd = mediagen.image_engine_usd(engine, resolution)
        else:
            usd = 0.0
    except Exception:  # noqa: BLE001
        usd = 0.0
    return max(0, int(round(usd * max(1, int(count or 1)) * 100)))


def _log_points(db: Session, user: User, delta: int, what: str, *,
                kind: str = "", ref_type: str = "", ref_id: int = 0,
                engine: str = "", cost_cents: int = 0, project_id: int = 0,
                track_id: int = 0, task_id: str = "", commit: bool = True) -> int:
    """Строка журнала; возвращает её id (0 — не записалась).

    По этому id воркер потом дописывает task_id внешней задачи: списание
    происходит В МОМЕНТ постановки, а taskId у kie появляется позже.

    commit=False — строка едет ОДНОЙ транзакцией с вызывающим кодом. Так
    оплата (выдача тарифа + отметка о платеже + приход в журнал) остаётся
    атомарной: раньше журнал коммитил сам, и падение между коммитами
    оставляло платёж наполовину выданным."""
    try:
        ev = PointEvent(
            user_id=user.id, delta=int(delta), kind=kind or _guess_kind(what),
            what=str(what or "")[:200], ref_type=ref_type, ref_id=int(ref_id or 0),
            engine=str(engine or "")[:60], balance_after=int(user.gen_points or 0),
            cost_cents=int(cost_cents or 0), project_id=int(project_id or 0),
            track_id=int(track_id or 0), task_id=str(task_id or "")[:80],
        )
        db.add(ev)
        if not commit:
            return 0
        db.commit()
        return int(ev.id or 0)
    except Exception as e:  # noqa: BLE001
        log.warning("журнал токенов: строка не записалась (%s): %s", what, str(e)[:150])
        return 0


def _move_points(db: Session, user: User, delta: int, what: str, *,
                 commit: bool = True, **meta) -> int:
    """ЕДИНСТВЕННОЕ место, где меняется user.gen_points. Возвращает id строки
    журнала.

    Всё, что двигает токены — списание, возврат, оплата, месячный транш,
    админский грант, откат звёздного платежа — проходит здесь. Ни один вызов
    не может «забыть» журнал, потому что журнал и есть эта функция."""
    delta = int(delta or 0)
    if not delta:
        return 0
    user.gen_points = int(user.gen_points or 0) + delta  # ledger-ok: единственная дверь
    try:
        return _log_points(db, user, delta, what, commit=commit, **meta)
    except Exception as e:  # noqa: BLE001
        # Журнал не должен отменять уже принятое решение о деньгах: если
        # строка не пишется, токены всё равно двигаем и жалуемся в лог.
        db.rollback()
        log.warning("журнал токенов упал, двигаю токены без строки (%s): %s",
                    what, str(e)[:150])
        fresh = db.get(User, user.id)
        if fresh is not None:
            fresh.gen_points = int(fresh.gen_points or 0) + delta  # ledger-ok
            db.commit()
        return 0


def _attach_task(db: Session, ref_type: str, ref_id: int, task_id: str,
                 kind: str = "") -> None:
    """Дописать id внешней задачи к последней строке списания за этот объект.

    Списание идёт ДО постановки задачи, taskId приходит из mediagen позже —
    поэтому не параметр, а второй шаг. Это то, чем разбирается спор:
    «списали 154 токена → задача kie abc123 → упала → возврат строкой ниже»."""
    if not task_id or not ref_id:
        return
    try:
        q = (db.query(PointEvent)
             .filter(PointEvent.ref_type == ref_type, PointEvent.ref_id == int(ref_id),
                     PointEvent.delta < 0, PointEvent.task_id == ""))
        if kind:
            q = q.filter(PointEvent.kind == kind)
        row = q.order_by(PointEvent.id.desc()).first()
        if row:
            row.task_id = str(task_id)[:80]
            db.commit()
    except Exception as e:  # noqa: BLE001
        db.rollback()
        log.warning("журнал токенов: не привязал задачу %s: %s", task_id, str(e)[:120])


def _take_points(db: Session, user: User, points: int, what: str = "",
                 **meta) -> bool:
    """Тихое списание: False — не хватило. Нужно фоновым шагам (супергенерация
    доводит счёт по факту), которым некуда бросать HTTP-ошибку."""
    if user.is_admin or points <= 0:
        return True
    if int(user.gen_points or 0) < points:
        return False
    _move_points(db, user, -int(points), what or "генерация", **meta)
    return True


def _charge(db: Session, user: User, points: int, what: str, **meta) -> int:
    """Списание токенов генерации В МОМЕНТ постановки задачи (не в треде):
    генерации идут через подписки владельца, лимит защищает его кошелёк.

    Возвращает id строки журнала — по нему потом привязывается внешняя задача."""
    if user.is_admin or points <= 0:
        return 0
    if int(user.gen_points or 0) < points:
        raise NotEnoughPoints(points, int(user.gen_points or 0), _plan_of(user), what)
    ev = _move_points(db, user, -int(points), what, **meta)
    log.info("user %s: −%s токенов за %s (осталось %s)", user.id, points, what, user.gen_points)
    return ev


def _refund(db: Session, user: User, points: int, what: str = "", **meta) -> int:
    """Вернуть токены за НЕсостоявшуюся работу.

    До чата возврата не было нигде: у сцены упавшая генерация оставляла
    charged_points на месте. В студии это тонет в потоке кнопок, а в чате
    запросы одиночные — молча съеденные за упавший Seedance 154 токена человек
    видит сразу и справедливо считает это воровством."""
    if user.is_admin or points <= 0:
        return 0
    meta.setdefault("kind", "refund")
    # Возврат ничего нам не стоит — себестоимость возвращённой работы уже
    # записана строкой списания, и второй раз её считать нельзя.
    meta.pop("cost_cents", None)
    ev = _move_points(db, user, int(points),
                      what or "возврат за неудачную генерацию", **meta)
    log.info("user %s: +%s токенов возврата за %s (стало %s)",
             user.id, points, what or "неудачную генерацию", user.gen_points)
    return ev


def _scene_meta(scene: "Scene") -> dict:
    """Привязка строки журнала к проекту и объекту. Денормализация ради
    отчёта «сколько ушло на этот проект»: иначе на каждой строке join
    scenes→tracks→projects."""
    try:
        track = scene.track
        return {"ref_type": "scene", "ref_id": scene.id,
                "track_id": track.id if track else 0,
                "project_id": track.project_id if track else 0}
    except Exception:  # noqa: BLE001
        return {"ref_type": "scene", "ref_id": scene.id}


def _scene_charge(db: Session, user: User, scene: "Scene", cost: int, what: str,
                  *, kind: str = "", engine: str = "", cost_cents: int = 0) -> int:
    """Списать за КАЖДЫЙ платный вызов движка.

    Раньше перегенерация уже оплаченной сцены была бесплатной: считалось, что
    «кадры входят в цену сцены». На бесплатных шлюзах это верно, но на платных
    движках каждое нажатие «перегенерировать» — живые деньги нам (Nano Banana
    Pro стоит $0.09 за картинку), и при трёх прогонах тариф уходил в минус.
    Теперь платим за вызов: сколько раз запустил движок — столько и списано.
    Бесплатные шлюзовые движки по-прежнему стоят символические 2 токена."""
    if cost <= 0:
        return 0
    ev = _charge(db, user, cost, what, kind=kind, engine=engine,
                 cost_cents=cost_cents or _cost_cents(kind, engine,
                                                      seconds=scene.duration_sec),
                 **_scene_meta(scene))
    scene.charged_points = int(scene.charged_points or 0) + cost
    db.commit()
    return ev


def _scenes_charge(db: Session, user: User, scenes: list, cost_of, what: str,
                   *, kind: str = "", engine: str = "", track_id: int = 0,
                   project_id: int = 0) -> int:
    """То же для пачки сцен: одно списание на весь пакет (и один отказ, если
    токенов не хватило), потом отметки на сценах."""
    rows, total = [], 0
    for s in scenes:
        cost = cost_of(s)
        if cost > 0:
            total += cost
            rows.append((s, cost))
    if total:
        _charge(db, user, total, what, kind=kind, engine=engine,
                cost_cents=_cost_cents(kind, engine, count=len(rows)),
                ref_type="track" if track_id else "", ref_id=track_id,
                track_id=track_id, project_id=project_id)
    for s, cost in rows:
        s.charged_points = int(s.charged_points or 0) + cost
    if rows:
        db.commit()
    return total


# ─────────── партнёрка: промокоды, рефералы, вознаграждения ───────────
# Проценты и минималку правим в infra/.env, а не в коде: их крутят под акции.
# REF_DISCOUNT_PCT — скидка приглашённому на ПЕРВУЮ оплату, REF_REWARD_PCT —
# доля амбассадора с КАЖДОГО платежа его реферала (включая автопродления).
REF_DISCOUNT_PCT = max(0, min(90, int(os.environ.get("REF_DISCOUNT_PCT", "10"))))
# Ставка 10%: при нашей наценке 2.4x к себестоимости движков 30% съедали
# всю маржу на объёмных и годовых тарифах. Правится в infra/.env.
REF_REWARD_PCT = max(0, min(100, int(os.environ.get("REF_REWARD_PCT", "10"))))
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


def _ref_reward(db: Session, buyer: User, amount_kopeks: int, payment_id: str,
                pct: "int | None" = None) -> None:
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
    # pct задан — платёж пришёл контуром со своей экономикой (Telegram Stars
    # оставляет нам 65 % цены). По умолчанию — общая ставка партнёрки.
    rate = REF_REWARD_PCT if pct is None else max(0, min(100, int(pct)))
    reward = amount_kopeks * rate // 100
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
    plan_id = _plan_of(user)
    tier = _tier_of_user(user)
    return {"id": user.id, "name": user.name, "login": user.login,
            "is_admin": user.is_admin, "gen_points": user.gen_points,
            "plan": plan_id, "plan_title": PLANS[plan_id]["title"],
            # Ступень объёма и месячная норма ЭТОГО человека: интерфейсу нужно
            # различать u1 и u4, у них разный объём при одном имени тарифа.
            "plan_tier": tier,
            "plan_points": _plan_points(plan_id, tier),
            # Онбординг «первый клип»: чеклист живёт на сервере, а не в
            # localStorage — человек начинает на десктопе, продолжает с телефона.
            "onboarding": [s for s in (user.onboarding or "").split(",") if s],
            "onboarding_done": bool(user.onboarding_done),
            # Блокировка едет в /api/me, потому что увидеть её должен САМ
            # заблокированный. current_user отвечает 403 на каждый рабочий
            # роут, но /api/me намеренно идёт мимо гварда (иначе экран входа
            # падал бы вместе с приложением) — и без этих двух полей человек
            # видел не «вы заблокированы, вот причина», а приложение, в
            # котором молча не работает ни одна кнопка.
            "is_blocked": bool(getattr(user, "is_blocked", False)),
            "blocked_reason": (getattr(user, "blocked_reason", "") or "").strip()}


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
    """Ставит сессионную куку внешнего входа — как обычный логин.

    Срок — QV_MAX_AGE (180 дней), как у гостевой сессии. Раньше здесь стоял
    COOKIE_MAX_AGE (30 дней): вошедший через Telegram, Яндекс или Google терял
    сессию вшестеро быстрее гостя, который вообще не регистрировался."""
    token = signer.dumps({"uid": user.id})
    resp = JSONResponse({"ok": True, "name": user.name})
    resp.set_cookie(QV_COOKIE, token, max_age=QV_MAX_AGE, httponly=True,
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
            # QV_MAX_AGE, а не COOKIE_MAX_AGE: гостевая кука живёт 180 дней,
            # и на тридцати гость старше месяца просто «не находился» — его
            # проекты молча оставались на брошенном аккаунте.
            guest = db.get(User, int(signer.loads(token, max_age=QV_MAX_AGE).get("uid") or 0))
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
            # QV_MAX_AGE, а не COOKIE_MAX_AGE: гостевая кука живёт 180 дней,
            # и на тридцати гость старше месяца просто «не находился» — его
            # проекты молча оставались на брошенном аккаунте.
            guest = db.get(User, int(signer.loads(token, max_age=QV_MAX_AGE).get("uid") or 0))
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


def _oauth_state(ref: str, state: str) -> str:
    """Что положить в state OAuth-провайдера.

    Обычный вход с сайта кладёт туда промокод. Вход из мини-аппа Telegram
    кладёт «<nonce>~<промокод>»: по nonce мини-апп потом узнаёт, что человек
    вошёл во внешнем браузере, и подхватывает аккаунт (см. tg_app.py). Готовый
    state передаём как есть — он собран нашим же кодом."""
    raw = str(state or "").strip()
    if "~" in raw:
        nonce, _, ref_in = raw.partition("~")
        nonce = "".join(c for c in nonce if c.isalnum() or c in "-_")[:32]
        return f"{nonce}~{_norm_code(ref_in or ref)}"
    return _norm_code(ref or raw)


def _oauth_finish(user: "User", state: str):
    """Куда вернуть человека после внешнего входа и с какой кукой.

    Из мини-аппа вернуть надо не на сайт, а обратно в Telegram: браузер
    открывался поверх мини-аппа именно ради входа."""
    import tg_app  # noqa: PLC0415
    from fastapi.responses import RedirectResponse  # noqa: PLC0415
    nonce, _ = tg_app.split_state(state)
    target = "/"
    if nonce and tg_app.remember_login(nonce, user.id):
        target = tg_app.miniapp_url(f"ylink_{nonce}") or "/"
    resp = RedirectResponse(target)
    resp.set_cookie(QV_COOKIE, signer.dumps({"uid": user.id}), max_age=QV_MAX_AGE,
                    httponly=True, samesite="lax", secure=True)
    return resp


@app.get("/api/auth/google/start")
def auth_google_start(ref: str = "", state: str = ""):
    """ref едет в state: после редиректа на Google наши query-параметры
    теряются, а state Google возвращает обратно как есть."""
    if not GOOGLE_CLIENT_ID:
        raise HTTPException(400, "вход через Google не настроен")
    from fastapi.responses import RedirectResponse
    redirect = f"{PUBLIC_BASE_URL}/api/auth/google/callback"
    url = ("https://accounts.google.com/o/oauth2/v2/auth?response_type=code"
           f"&client_id={GOOGLE_CLIENT_ID}&redirect_uri={redirect}"
           "&scope=openid%20email%20profile&access_type=online&prompt=select_account")
    code = _oauth_state(ref, state)
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
    import tg_app  # noqa: PLC0415
    _, ref_code = tg_app.split_state(state)
    user = _external_login(db, request, "google_id", gid,
                           prof.get("name") or prof.get("email") or "гость",
                           prof.get("email") or "", prof.get("picture") or "",
                           ref=ref_code)
    return _oauth_finish(user, state)


@app.get("/api/auth/yandex/start")
def auth_yandex_start(ref: str = "", state: str = ""):
    """ref едет в state — обратно с Яндекса вернётся тот же код (см. Google)."""
    if not YANDEX_CLIENT_ID:
        raise HTTPException(400, "вход через Яндекс не настроен")
    from fastapi.responses import RedirectResponse
    redirect = f"{PUBLIC_BASE_URL}/api/auth/yandex/callback"
    url = ("https://oauth.yandex.ru/authorize?response_type=code"
           f"&client_id={YANDEX_CLIENT_ID}&redirect_uri={redirect}")
    ref_code = _oauth_state(ref, state)
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
    # Раньше здесь лежала своя копия логики входа — с забытой проверкой
    # google_id в условии «свежий гость», то есть гость, вошедший через Google,
    # мог быть перехвачен Яндексом. Теперь общий путь, один на всех провайдеров.
    import tg_app  # noqa: PLC0415
    _, ref_code = tg_app.split_state(state)
    name = prof.get("real_name") or prof.get("display_name") or prof.get("login") or "гость"
    user = _external_login(db, request, "yandex_id", yid, name,
                           prof.get("default_email") or "", ref=ref_code)
    return _oauth_finish(user, state)


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


def _char_photo_dict(ph: CharacterPhoto) -> dict:
    return {
        "id": ph.id, "url": f"/api/media/{ph.filename}",
        "thumb_url": f"/api/thumb/{ph.filename}",
        # kind разводит загруженные фото и сгенерированные развороты: в
        # карточке они лежат разными рядами, а в референсы разворота уходят
        # только фото (иначе моделька рисуется с модельки, см. db.py).
        "kind": ph.kind or "photo", "pose_kind": ph.pose_kind or "",
        "from_photos": int(ph.from_photos or 0),
    }


def character_dict(c: Character) -> dict:
    photos = sorted(c.photos, key=lambda x: (x.position, x.id))
    model = _character_model_file(c)
    return {
        "id": c.id, "position": c.position, "name": c.name,
        "description": c.description, "is_main": c.is_main,
        # photos — весь список целиком (легаси-контракт фронта и библиотеки),
        # но с kind у каждой позиции.
        "photos": [_char_photo_dict(ph) for ph in photos],
        # Какая картинка реально уедет референсом в кадры сцен. Без этого
        # «моделька» оставалась догадкой: человек видит ряд картинок и не
        # знает, какая из них работает.
        "model_photo_id": model.id if model else 0,
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
    между первым и последним, но не больше 4 (экономия токенов и времени)."""
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
        # Движки сцены наружу отдавались... никогда: карточка кадра рисовала
        # чипы по s.image_engine/s.video_engine, которых в ответе не было, и
        # подсветка всегда врала. Теперь поля есть, и «переопределить движок»
        # в карточке показывает то, чем сцену реально сняли.
        "image_engine": s.image_engine or "", "video_engine": s.video_engine or "",
        # Режимы «сериалы» и «UGC»: акт серии и кто говорит в кадре.
        "act": s.act or "", "speaker": s.speaker or "",
    }


def _track_style_keys(t: Track) -> list[str]:
    """Ключи стилей трека. Старые треки хранят только текст промпта — для них
    один раз разбираем текст обратно в ключи тем же алгоритмом, каким его
    собирали (prompts_catalog.fusion — дословный порт buildFusionStyle)."""
    keys = [k for k in (t.style_keys or "").split(",") if k.strip()]
    if keys:
        return keys
    return prompts_catalog.keys_from_prompt(t.style or "")


def track_dict(t: Track, with_scenes: bool = False) -> dict:
    keys = _track_style_keys(t)
    d = {
        "id": t.id, "position": t.position, "title": t.title, "lyrics": t.lyrics,
        "comment": t.comment, "audio_filename": t.audio_filename,
        # ПОЛНЫЙ ТЕКСТ СТИЛЯ НАРУЖУ НЕ УХОДИТ. Раньше здесь стояло
        # "style": t.style — тот же промпт из закрытого реестра, только уже
        # записанный в трек. Перенос реестра на сервер без этой строки был бы
        # бессмысленным: промпт продолжал бы утекать через /api/tracks.
        # Наружу — ключи, подпись и та приписка, которую человек написал сам.
        "style_keys": keys,
        "style_label": prompts_catalog.labels(keys),
        "style_extra": t.style_extra or "",
        "clip_preset_key": t.clip_preset_key or "",
        # has_style — «стиль задан»: этапу настройки хватает факта, а не текста.
        # Кастом старого трека (текст без ключей) тоже считается заданным.
        "has_style": bool(keys or (t.style or "").strip()),
        "style_custom": bool(not keys and (t.style or "").strip()),
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
        # Один выбор движков на весь объект. Пусто = «как решит тариф»;
        # карточка кадра показывает это наследование, а не пустой чип.
        "video_engine": t.video_engine or "", "image_engine": t.image_engine or "",
        # Режимы «сериалы» и «UGC».
        "season_no": t.season_no or 0, "episode_no": t.episode_no or 0,
        "format_key": t.format_key or "", "location_bible": t.location_bible or "",
        # Геометрия и разрешение кадра. Пусто = «как у режима»: у клипа,
        # ролика и серии это вертикаль, у мокапа квадрат.
        "aspect": t.aspect or "", "eff_aspect": _track_aspect(t),
        "image_resolution": t.image_resolution or "",
        # Фото товара (режим мокапов): референс, по которому упаковка обязана
        # совпасть до последней буквы на этикетке.
        "photos": [{"id": ph.id, "position": ph.position, "kind": ph.kind or "photo",
                    "url": f"/api/media/{ph.filename}"}
                   for ph in sorted(t.photos, key=lambda x: (x.position, x.id))],
    }
    if with_scenes:
        d["scenes"] = [scene_dict(s) for s in t.scenes]
    return d


def doc_dict(d: Doc) -> dict:
    """Сценарный артефакт наружу. body_json отдаётся РАЗОБРАННЫМ: фронт не
    должен уметь парсить наш внутренний формат, он рисует список карточек."""
    parsed = None
    if (d.body_json or "").strip():
        try:
            parsed = json.loads(d.body_json)
        except Exception:  # noqa: BLE001
            parsed = None
    return {
        "id": d.id, "project_id": d.project_id, "track_id": d.track_id or 0,
        "kind": d.kind, "position": d.position, "title": d.title,
        "body": d.body, "data": parsed,
        "status": d.status, "error": d.error,
        "updated_at": d.updated_at.isoformat() if d.updated_at else "",
    }


def project_dict(p: Project, with_scenes: bool = False, docs: list | None = None) -> dict:
    mode = _mode_of(p)
    return {
        "id": p.id, "name": p.name, "kind": p.kind, "character_bible": p.character_bible,
        # Режим проекта — производная от kind, но отдаём явно: фронт не должен
        # держать вторую копию таблицы «какой kind в каком режиме».
        "mode": mode["id"],
        "docs": [doc_dict(d) for d in (docs or [])],
        "characters": [character_dict(c) for c in sorted(p.characters, key=lambda x: x.position)],
        "story": p.story, "story_status": p.story_status, "story_error": p.story_error,
        "cover_url": f"/api/media/{p.cover_filename}" if p.cover_filename else "",
        "tracks": [track_dict(t, with_scenes) for t in p.tracks],
    }



# ────────────────── «Школа lolq»: обучение и онбординг FREE ──────────────────
# Тексты уроков — файлы в docs/learn (см. backend/learn.py). Здесь только
# доступ по тарифу, прогресс и чеклист первого клипа.

ONBOARDING_STEPS = ("track", "style", "scenes", "clip")


def _learn_done(user: "User | None") -> set:
    if not user:
        return set()
    return {s for s in (user.onboarding or "").split(",")
            if s and s.startswith("learn:")}


@app.get("/api/learn")
def api_learn(request: Request, lang: str = "", db: Session = Depends(db_session)):
    """Список уроков с замками по тарифу и отметками пройденного.

    ПУБЛИЧНЫЙ: уровни 0–2 открыты без регистрации — они же двигатель органики,
    и требовать аккаунт ради чтения значит терять человека на входе."""
    user = _resolve_user(request, db)
    lg = _lang_of(request, lang)
    plan_id = _plan_of(user) if user else "free"
    done = {s.split(":", 1)[1] for s in _learn_done(user)}
    lessons = learn.index(lg, plan_id=plan_id,
                          is_admin=bool(user and user.is_admin), done=done)
    return {
        "lang": lg,
        "plan": plan_id,
        "authorized": bool(user),
        "levels": learn.levels(lg),
        "lessons": lessons,
        "done": sorted(done),
    }


@app.get("/api/learn/{slug}")
def api_lesson(slug: str, request: Request, lang: str = "",
               db: Session = Depends(db_session)):
    """Один урок. Закрытому тарифу отдаём начало и честную причину замка,
    а не пустой экран: человек должен видеть, что именно он не читает."""
    user = _resolve_user(request, db)
    lg = _lang_of(request, lang)
    item = learn.lesson(slug, lg)
    if not item:
        raise ApiError(404, "unknown_lesson", f"Unknown lesson: {slug!r}")
    plan_id = _plan_of(user) if user else "free"
    ok = learn.allowed(item["access"], plan_id,
                       is_admin=bool(user and user.is_admin))
    done = {s.split(":", 1)[1] for s in _learn_done(user)}
    out = learn.card(item, plan_id=plan_id,
                     is_admin=bool(user and user.is_admin),
                     done=item["slug"] in done)
    # Текст в маркдауне: разметку рисует клиент. Так один и тот же урок
    # ложится и в шторку студии, и в статическую страницу /learn — без
    # второго шаблона и второго набора метатегов.
    out["markdown"] = item["body"] if ok else learn.teaser(item["body"])
    out["full"] = ok
    out["lang"] = item["lang"]
    return out


@app.post("/api/learn/{slug}/done")
async def api_lesson_done(slug: str, request: Request,
                          user: User = Depends(current_user),
                          db: Session = Depends(db_session)):
    """Отметка «прочитал». Нужна не ради галочки, а чтобы онбординг знал,
    что человеку уже показывали, и не звал на тот же урок второй раз."""
    body = await request.json() if await request.body() else {}
    on = bool(body.get("done", True))
    steps = [s for s in (user.onboarding or "").split(",") if s]
    mark = f"learn:{slug}"
    if on and mark not in steps:
        steps.append(mark)
    if not on and mark in steps:
        steps.remove(mark)
    user.onboarding = ",".join(steps)
    db.commit()
    return {"ok": True, "done": on, "slug": slug}


# ─────────────────── онбординг FREE: чеклист «первый клип» ───────────────────

def _onboarding_state(db: Session, user: User) -> dict:
    """Где человек в своём первом клипе — СЧИТАЕТСЯ ПО ДАННЫМ, а не по
    галочкам. Галочку можно поставить и уйти, а трек либо загружен, либо нет.
    Ручные отметки лежат рядом и только добавляют шаги, которых в данных нет
    (например «взял демо-трек»).

    Плитка живёт до первого собранного клипа и после него исчезает: чеклист,
    который висит вечно, перестают читать."""
    tracks = (db.query(Track).join(Project, Track.project_id == Project.id)
              .filter(Project.owner_id == user.id).all())
    has_audio = any(t.audio_filename for t in tracks)
    has_style = any((t.style_keys or "").strip() or (t.style or "").strip()
                    for t in tracks)
    has_scenes = any(t.scenes for t in tracks)
    has_clip = any(t.clip_filename for t in tracks)
    if has_clip and not user.onboarding_done:
        user.onboarding_done = now()
        db.commit()
    manual = [s for s in (user.onboarding or "").split(",")
              if s and not s.startswith("learn:")]
    plan_id = _plan_of(user)
    points = int(user.gen_points or 0)
    # Прогноз до конца пути — та же арифметика, что и в списании: кадры и
    # видео по движкам тарифа, текстовые шаги бесплатны (они идут по нашей
    # подписке и стоят нам ноль — об этом надо говорить вслух).
    scene_cost = _plan_work_cost(plan_id)
    return {
        "done": bool(has_clip or user.onboarding_done),
        "steps": [
            {"id": "track", "done": has_audio},
            {"id": "style", "done": has_style},
            {"id": "scenes", "done": has_scenes},
            {"id": "clip", "done": has_clip},
        ],
        "marks": manual,
        "points": points,
        "plan": plan_id,
        # Что почём на этом тарифе — чеклист обязан показывать цифру ДО
        # нажатия, а не после списания.
        "costs": {
            "story": COST_STORY, "scenes": COST_SCENES,
            "scene": scene_cost, "clip_scenes": CLIP_SCENES,
            "clip_total": scene_cost * CLIP_SCENES,
        },
        # Хватает ли остатка на целый клип. Сам клип на FREE стоит 120 (30 сцен
        # по 4) при норме 150, то есть запас — 30 токенов: лист раскадровки,
        # моделька и одна переделка. Врать тут нельзя.
        "enough": points >= scene_cost * CLIP_SCENES,
    }


@app.get("/api/onboarding")
def api_onboarding(user: User = Depends(current_user),
                   db: Session = Depends(db_session)):
    return _onboarding_state(db, user)


@app.post("/api/onboarding")
async def api_onboarding_mark(request: Request, user: User = Depends(current_user),
                              db: Session = Depends(db_session)):
    """Ручная отметка шага: «взял демо-трек», «скрыл чеклист» и подобное.
    Шаги, которые видно по данным, сюда не пишутся — они и так считаются."""
    body = await request.json() if await request.body() else {}
    mark = re.sub(r"[^a-z0-9_\-]", "", str(body.get("mark") or "").lower())[:32]
    if not mark:
        raise ApiError(400, "bad_mark", "mark is required")
    steps = [s for s in (user.onboarding or "").split(",") if s]
    if bool(body.get("on", True)):
        if mark not in steps:
            steps.append(mark)
    elif mark in steps:
        steps.remove(mark)
    user.onboarding = ",".join(steps)[:500]
    db.commit()
    return _onboarding_state(db, user)


# ─────────────────────────────── проект ───────────────────────────────

@app.get("/api/projects")
def list_projects(user: User = Depends(current_user), db: Session = Depends(db_session)):
    return [
        {"id": p.id, "name": p.name, "kind": p.kind,
         "mode": _mode_of(p)["id"], "tracks": len(p.tracks)}
        for p in db.query(Project).filter(Project.owner_id == user.id)
                    .order_by(Project.id).all()
    ]


@app.post("/api/projects")
async def create_project(request: Request, user: User = Depends(current_user), db: Session = Depends(db_session)):
    body = await request.json()
    # Вид проекта = режим. Неизвестный вид тихо становится альбомом: это
    # безопасная сторона ошибки — клип умеет всё, что умели старые проекты.
    kind = formats.norm_kind(body.get("kind"))
    project = Project(name=str(body.get("name") or "Новый проект"), kind=kind,
                      owner_id=user.id)
    db.add(project)
    db.commit()
    db.refresh(project)
    return {"id": project.id, "name": project.name, "kind": project.kind,
            "mode": _mode_of(project)["id"]}


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


def _project_docs(db: Session, project: Project) -> list:
    return (db.query(Doc).filter(Doc.project_id == project.id)
            .order_by(Doc.track_id.is_(None).desc(), Doc.position, Doc.id).all())


@app.get("/api/project")
def get_project(project_id: int | None = None, user: User = Depends(current_user), db: Session = Depends(db_session)):
    project = get_or_create_project(db, user, project_id)
    # Документы едут ВМЕСТЕ с проектом: у них тот же status/error, что у
    # сюжета, и поллер фронта уже ходит сюда. Отдельный адрес заставил бы его
    # опрашивать два места и рассинхронизировал бы «генерится» на экране.
    return project_dict(project, with_scenes=True, docs=_project_docs(db, project))


# ─────────────────── лёгкий поллинг: ТОЛЬКО статусы ───────────────────
# Пока хотя бы одна сцена генерится, фронт каждые три секунды тянул ВЕСЬ
# проект: scene_dict отдаёт промпты кадра, анимации и последнего кадра —
# порядка 3 КБ текста на сцену, то есть под мегабайт JSON на альбом каждые
# три секунды, при том что меняются в нём два поля статуса. И на каждый
# такой ответ фронт сносил и пересобирал весь DOM.
#
# Здесь — единицы килобайт: id и статусы. Полную перезагрузку фронт делает
# только когда статус реально перешёл в done/error, то есть появился новый
# файл.

@app.get("/api/project/status")
def project_status(project_id: int | None = None, user: User = Depends(current_user),
                   db: Session = Depends(db_session)):
    project = get_or_create_project(db, user, project_id)
    docs = _project_docs(db, project)
    return {
        "id": project.id,
        "story_status": project.story_status,
        "docs": [{"id": d.id, "kind": d.kind, "track_id": d.track_id or 0,
                  "status": d.status, "error": d.error} for d in docs],
        "tracks": [{
            "id": t.id,
            "scenes_status": t.scenes_status,
            "storyboard_status": t.storyboard_status,
            "storyboard_url": (f"/api/media/{t.storyboard_filename}"
                               if t.storyboard_filename else ""),
            "clip_status": t.clip_status,
            "clip_url": f"/api/media/{t.clip_filename}" if t.clip_filename else "",
            "supergen_status": t.supergen_status,
            "supergen_note": t.supergen_note or "",
            "scenes_count": len(t.scenes),
            "approved_count": sum(1 for s in t.scenes if s.approved),
            "scenes": [{
                "id": s.id,
                "image_status": s.image_status, "image_error": s.image_error,
                "video_status": s.video_status, "video_error": s.video_error,
                "has_image": bool(s.image_filename),
                "has_last": bool(s.image_last_filename),
                "has_video": bool(s.video_filename),
                "mid": len(_midframes(s)),
            } for s in t.scenes],
        } for t in project.tracks],
    }


@app.patch("/api/project")
async def update_project(request: Request, project_id: int | None = None, user: User = Depends(current_user), db: Session = Depends(db_session)):
    body = await request.json()
    project = get_or_create_project(db, user, project_id)
    if "name" in body:
        project.name = str(body["name"])
    if "character_bible" in body:
        project.character_bible = str(body["character_bible"])
    db.commit()
    return project_dict(project, docs=_project_docs(db, project))


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
    _charge(db, user, COST_STORY, "сюжет проекта",
            kind="story", ref_type="project", ref_id=project.id,
            project_id=project.id)
    project.story_status = "queued"
    db.commit()
    Thread(target=_run_story_generation, args=(project.id,), daemon=True).start()
    return {"ok": True}


# ═══════════════════ РЕЖИМЫ, СЦЕНАРНЫЕ ДОКУМЕНТЫ И СЕРИАЛЫ ═══════════════════
# Ни одного нового конвейера. Всё ниже пишет ТЕКСТ в таблицу docs; кадры,
# видео и сборка потом берут его тем же путём, что и сюжет клипа. Поэтому
# «сериал» — это шесть роутов вокруг Doc, а не второе приложение.


@app.get("/api/modes")
def api_modes(request: Request, lang: str = ""):
    """Реестр режимов для фронта и мини-аппа: шаги, объекты, каркасы.

    Подписей режимов здесь нет намеренно — они i18n-ключи словаря
    (modes.<id>.*). Иначе перевод расползся бы между сервером и i18n.js, и
    третий язык пришлось бы заводить в двух местах."""
    lg = _lang_of(request, lang)
    # shortcuts — ОТДЕЛЬНЫМ полем, а не строкой в modes: ярлык («3D Pixar»)
    # выглядит в тумблере как режим, но режимом не является — у него нет ни
    # своего объекта, ни шагов, ни каркаса. Форма ответа сама это проговаривает.
    return {"modes": formats.public_modes(lang=lg),
            "shortcuts": formats.public_shortcuts(),
            "kinds": formats.PROJECT_KINDS}


def _own_doc(db: Session, user: User, doc_id: int) -> Doc:
    doc = db.get(Doc, doc_id)
    if not doc:
        raise HTTPException(404, "документ не найден")
    project = db.get(Project, doc.project_id)
    if not _owned(user, project):
        raise HTTPException(404, "документ не найден")
    return doc


def _find_doc(db: Session, project_id: int, kind: str,
              track_id: int | None = None) -> "Doc | None":
    q = db.query(Doc).filter(Doc.project_id == project_id, Doc.kind == kind)
    q = q.filter(Doc.track_id == track_id) if track_id else q.filter(Doc.track_id.is_(None))
    return q.first()


def _put_doc(db: Session, project_id: int, kind: str, *, track_id: int | None = None,
             title: str = "", body: str | None = None, data=None,
             status: str | None = None, error: str | None = None,
             position: int = 0) -> Doc:
    """Один документ каждого вида на объект: upsert, а не add.

    Иначе повторная генерация логлайна клала бы вторую строку, и «какой из
    них настоящий» решала бы сортировка по id — то есть случай."""
    doc = _find_doc(db, project_id, kind, track_id)
    if not doc:
        doc = Doc(project_id=project_id, track_id=track_id, kind=kind, position=position)
        db.add(doc)
    if title:
        doc.title = title[:200]
    if body is not None:
        doc.body = body
    if data is not None:
        doc.body_json = json.dumps(data, ensure_ascii=False)
    if status is not None:
        doc.status = status
    if error is not None:
        doc.error = error
    db.commit()
    db.refresh(doc)
    return doc


def _doc_status(project_id: int, kinds: tuple, status: str, error: str = "",
                track_id: int | None = None) -> None:
    """Отметить пачку документов одним статусом из фонового треда."""
    db = SessionLocal()
    try:
        for k in kinds:
            _put_doc(db, project_id, k, track_id=track_id, status=status, error=error)
    finally:
        db.close()


@app.get("/api/projects/{project_id}/docs")
def list_docs(project_id: int, user: User = Depends(current_user),
              db: Session = Depends(db_session)):
    project = _own_project(db, user, project_id)
    return [doc_dict(d) for d in _project_docs(db, project)]


@app.post("/api/projects/{project_id}/docs")
async def save_doc(project_id: int, request: Request,
                   user: User = Depends(current_user), db: Session = Depends(db_session)):
    """Ручная правка документа: владелец правит сгенерированное или пишет сам.

    Сценарные документы обязаны быть редактируемыми: ИИ ошибается в именах и
    в мелочах мира, а перегенерировать весь сезон ради одной фразы — дорого."""
    project = _own_project(db, user, project_id)
    body = await request.json()
    kind = str(body.get("kind") or "")
    if kind not in formats.DOC_KINDS:
        raise HTTPException(400, f"неизвестный вид документа: {kind}")
    track_id = int(body.get("track_id") or 0) or None
    if track_id:
        _own_track(db, user, track_id)
    text = str(body.get("body") or "")
    # Поэпизодный план читается ТОЛЬКО из body_json: по нему заводятся серии и
    # по нему пишется сценарий. Пока правка текста туда не доезжала, владелец
    # правил план, жал «создать серии» и молча получал СТАРЫЙ — расхождение
    # между тем, что на экране, и тем, что поехало в работу.
    data = _parse_beatsheet(text, _find_doc(db, project.id, kind, track_id)) \
        if kind == "beatsheet" else None
    doc = _put_doc(db, project.id, kind, track_id=track_id,
                   title=str(body.get("title") or ""),
                   body=text, data=data, status="", error="")
    return doc_dict(doc)


# Разбор ровно той раскладки, которую пишет _run_beatsheet: «N. Название» и
# строки «Событие/Меняется/Обрыв». Не парсер произвольного текста: если
# владелец написал план от руки в свободной форме, вернём None и прежний
# body_json останется на месте — это ровно то поведение, что было до правки,
# то есть отката не требуется.
_BEATSHEET_FIELDS = {"событие": "event", "меняется": "changes",
                     "обрыв": "cliffhanger", "главный": "lead"}


def _parse_beatsheet(text: str, old: "Doc | None") -> dict | None:
    keep = {}
    if old is not None and old.body_json:
        try:
            for r in (json.loads(old.body_json) or {}).get("episodes") or []:
                keep[int(r.get("no") or 0)] = r
        except Exception:  # noqa: BLE001
            keep = {}
    rows: list[dict] = []
    for line in (text or "").splitlines():
        line = line.strip()
        if not line:
            continue
        head = re.match(r"^(\d{1,3})[.)]\s*(.+)$", line)
        if head:
            no = int(head.group(1))
            # Поля, которых нет в тексте (beat, lead), берём у прежней версии
            # той же серии: иначе ручная правка названия стирала бы привязку
            # серии к биту арки сезона.
            row = dict(keep.get(no) or {})
            row.update({"no": no, "title": head.group(2).strip()})
            rows.append(row)
            continue
        if not rows or ":" not in line:
            continue
        label, _, val = line.partition(":")
        field = _BEATSHEET_FIELDS.get(label.strip().lower())
        if field:
            rows[-1][field] = val.strip()
    return {"episodes": rows} if rows else None


@app.delete("/api/docs/{doc_id}")
def delete_doc(doc_id: int, user: User = Depends(current_user),
               db: Session = Depends(db_session)):
    doc = _own_doc(db, user, doc_id)
    db.delete(doc)
    db.commit()
    return {"ok": True}


# ─────────────── библия сезона / персона блогера ───────────────

_BIBLE_DOCS = ("logline", "synopsis", "arc")


def _run_series_bible(project_id: int, idea: str, episodes: int) -> None:
    db = SessionLocal()
    try:
        project = db.get(Project, project_id)
        if not project:
            return
        key = formats.default_format("series")
        # Каркас берём у первой серии, если она уже заведена: владелец мог
        # выбрать жанр там, и библия обязана писаться под ТОТ ЖЕ каркас.
        for t in project.tracks:
            if formats.format_spec("series", t.format_key or ""):
                key = t.format_key
                break
        import asyncio
        res = asyncio.run(claude.generate_series_bible(
            idea=idea,
            format_label=((formats.format_spec("series", key) or {})
                          .get("label", {}).get("ru", key)),
            season_beats=formats.beats_block("series", key, "season_beats"),
            format_note=formats.seed(key).get("note", ""),
            episodes=episodes,
            character_bible=project.character_bible,
            characters=characters_payload(project),
        ))
        arcs = res.get("arcs") or []
        arc_text = "\n\n".join(
            f"{a.get('name', '')}: {a.get('arc', '')}" for a in arcs if a.get("arc"))
        world = str(res.get("world") or "")
        if world:
            arc_text = (arc_text + "\n\nМИР И ЕГО ПРАВИЛА\n" + world).strip()
        _put_doc(db, project_id, "logline", body=str(res.get("logline") or ""),
                 status="", error="", position=1)
        _put_doc(db, project_id, "synopsis", body=str(res.get("synopsis") or ""),
                 status="", error="", position=2)
        _put_doc(db, project_id, "arc", body=arc_text,
                 data={"arcs": arcs, "world": world}, status="", error="", position=3)
        if res.get("character_bible"):
            project.character_bible = str(res["character_bible"])
            db.commit()
        log.info("библия сезона готова для проекта %s", project_id)
    except Exception as e:  # noqa: BLE001
        db.rollback()
        _doc_status(project_id, _BIBLE_DOCS, "error", str(e)[:500])
        log.warning("библия сезона проекта %s упала: %s", project_id, e)
    finally:
        db.close()


def _run_ugc_persona(project_id: int, idea: str) -> None:
    db = SessionLocal()
    try:
        project = db.get(Project, project_id)
        if not project:
            return
        import asyncio
        res = asyncio.run(claude.generate_ugc_persona(
            idea=idea, character_bible=project.character_bible,
            characters=characters_payload(project),
        ))
        _put_doc(db, project_id, "persona", title=str(res.get("name") or ""),
                 body=str(res.get("persona") or ""), status="", error="", position=1)
        # Локация — отдельный документ, а не кусок персоны: она подставляется
        # в промпты своей дословной формулой и правится отдельно (сменил
        # комнату — переписал одну строку, а не всю персону).
        _put_doc(db, project_id, "location", body=str(res.get("location_bible") or ""),
                 status="", error="", position=2)
        if res.get("character_bible"):
            project.character_bible = str(res["character_bible"])
            db.commit()
        log.info("персона блогера готова для проекта %s", project_id)
    except Exception as e:  # noqa: BLE001
        db.rollback()
        _doc_status(project_id, ("persona", "location"), "error", str(e)[:500])
        log.warning("персона блогера проекта %s упала: %s", project_id, e)
    finally:
        db.close()


def _run_mockup_brandbook(project_id: int, idea: str) -> None:
    db = SessionLocal()
    try:
        project = db.get(Project, project_id)
        if not project:
            return
        prev = _find_doc(db, project_id, "brandbook")
        import asyncio
        res = asyncio.run(claude.generate_brandbook(
            idea=idea, brand_note=prev.body if prev else "",
        ))
        _put_doc(db, project_id, "brandbook", title=str(res.get("name") or ""),
                 body=str(res.get("brandbook") or ""), status="", error="", position=1)
        log.info("фирменный мир готов для проекта %s", project_id)
    except Exception as e:  # noqa: BLE001
        db.rollback()
        _doc_status(project_id, ("brandbook",), "error", str(e)[:500])
        log.warning("фирменный мир проекта %s упал: %s", project_id, e)
    finally:
        db.close()


@app.post("/api/projects/{project_id}/generate-bible")
async def generate_bible(project_id: int, request: Request,
                         user: User = Depends(current_user),
                         db: Session = Depends(db_session)):
    """Сезонный слой одним нажатием: логлайн + синопсис + арки (сериал) или
    персона блогера (UGC). У клипа своя кнопка — /api/project/generate-story."""
    from threading import Thread
    project = _own_project(db, user, project_id)
    catalog = _catalog_of(project)
    body = await request.json() if await request.body() else {}
    idea = str(body.get("idea") or "")[:4000]
    if catalog == "series":
        episodes = max(2, min(24, int(body.get("episodes") or 8)))
        _charge(db, user, COST_STORY, f"библия сезона проекта {project.id}",
                kind="story", ref_type="project", ref_id=project.id,
                project_id=project.id)
        _doc_status(project.id, _BIBLE_DOCS, "queued")
        Thread(target=_run_series_bible, args=(project.id, idea, episodes),
               daemon=True).start()
        return {"ok": True, "episodes": episodes}
    if catalog == "ugc":
        _charge(db, user, COST_STORY, f"персона блогера проекта {project.id}",
                kind="story", ref_type="project", ref_id=project.id,
                project_id=project.id)
        _doc_status(project.id, ("persona", "location"), "queued")
        Thread(target=_run_ugc_persona, args=(project.id, idea), daemon=True).start()
        return {"ok": True}
    if catalog == "mockup":
        _charge(db, user, COST_STORY, f"фирменный мир проекта {project.id}",
                kind="story", ref_type="project", ref_id=project.id,
                project_id=project.id)
        _doc_status(project.id, ("brandbook",), "queued")
        Thread(target=_run_mockup_brandbook, args=(project.id, idea), daemon=True).start()
        return {"ok": True}
    raise HTTPException(400, "у клипа сюжет генерится кнопкой «сюжет проекта»")


# ─────────────── поэпизодный план и создание серий ───────────────

def _run_beatsheet(project_id: int, episodes: int) -> None:
    db = SessionLocal()
    try:
        project = db.get(Project, project_id)
        if not project:
            return
        key = formats.default_format("series")
        for t in project.tracks:
            if formats.format_spec("series", t.format_key or ""):
                key = t.format_key
                break
        logline = _find_doc(db, project_id, "logline")
        synopsis = _find_doc(db, project_id, "synopsis")
        arc = _find_doc(db, project_id, "arc")
        import asyncio
        res = asyncio.run(claude.generate_beatsheet(
            logline=logline.body if logline else "",
            synopsis=synopsis.body if synopsis else project.story,
            arcs=arc.body if arc else "",
            season_beats=formats.beats_block("series", key, "season_beats"),
            format_note=formats.seed(key).get("note", ""),
            episodes=episodes,
        ))
        rows = res.get("episodes") or []
        text = "\n\n".join(
            f"{r.get('no', i)}. {r.get('title', '')}\n"
            f"Событие: {r.get('event', '')}\n"
            f"Меняется: {r.get('changes', '')}\n"
            f"Обрыв: {r.get('cliffhanger', '')}"
            for i, r in enumerate(rows, 1))
        _put_doc(db, project_id, "beatsheet", body=text, data={"episodes": rows},
                 status="", error="", position=4)
        log.info("поэпизодный план готов для проекта %s (%s серий)", project_id, len(rows))
    except Exception as e:  # noqa: BLE001
        db.rollback()
        _doc_status(project_id, ("beatsheet",), "error", str(e)[:500])
        log.warning("поэпизодный план проекта %s упал: %s", project_id, e)
    finally:
        db.close()


@app.post("/api/projects/{project_id}/generate-beatsheet")
async def generate_beatsheet_route(project_id: int, request: Request,
                                   user: User = Depends(current_user),
                                   db: Session = Depends(db_session)):
    from threading import Thread
    project = _own_project(db, user, project_id)
    if _catalog_of(project) != "series":
        raise HTTPException(400, "поэпизодный план бывает только у сериала")
    body = await request.json() if await request.body() else {}
    episodes = max(2, min(24, int(body.get("episodes") or 8)))
    if not _find_doc(db, project.id, "logline"):
        raise HTTPException(400, "сначала собери библию сезона")
    _charge(db, user, COST_STORY, f"поэпизодный план проекта {project.id}",
            kind="story", ref_type="project", ref_id=project.id,
            project_id=project.id)
    _doc_status(project.id, ("beatsheet",), "queued")
    Thread(target=_run_beatsheet, args=(project.id, episodes), daemon=True).start()
    return {"ok": True, "episodes": episodes}


@app.post("/api/projects/{project_id}/create-episodes")
async def create_episodes(project_id: int, request: Request,
                          user: User = Depends(current_user),
                          db: Session = Depends(db_session)):
    """Карточки поэпизодного плана → настоящие серии (Track).

    Отдельным нажатием, а не автоматически после плана: план переписывают по
    два-три раза, и каждый прогон плодил бы сезон-дубль."""
    project = _own_project(db, user, project_id)
    if _catalog_of(project) != "series":
        raise HTTPException(400, "серии бывают только у сериала")
    body = await request.json() if await request.body() else {}
    season = max(0, int(body.get("season_no") or 1))
    sheet = _find_doc(db, project.id, "beatsheet")
    rows = []
    if sheet and sheet.body_json:
        try:
            rows = (json.loads(sheet.body_json) or {}).get("episodes") or []
        except Exception:  # noqa: BLE001
            rows = []
    if not rows:
        raise HTTPException(400, "сначала сгенерируй поэпизодный план")
    have = {(t.season_no, t.episode_no) for t in project.tracks}
    key = formats.default_format("series")
    for t in project.tracks:
        if formats.format_spec("series", t.format_key or ""):
            key = t.format_key
            break
    pos = max((t.position for t in project.tracks), default=0)
    made = 0
    for i, r in enumerate(rows, 1):
        no = int(r.get("no") or i)
        if (season, no) in have:
            continue          # эта серия уже заведена — второй раз не создаём
        pos += 1
        # JSON от модели легко приезжает с null вместо строки — без `or ""`
        # в комментарий серии попало бы литеральное «Событие: None».
        parts = [("Событие", r.get("event")), ("Меняется", r.get("changes")),
                 ("Главный", r.get("lead")), ("Обрыв", r.get("cliffhanger"))]
        comment = "\n".join(f"{name}: {str(val).strip()}"
                            for name, val in parts if str(val or "").strip())
        db.add(Track(
            project_id=project.id, position=pos,
            title=str(r.get("title") or f"Серия {no}"),
            comment=comment, format_key=key,
            season_no=season, episode_no=no,
        ))
        made += 1
    db.commit()
    return {"ok": True, "created": made}


# ─────────────── сценарий одной серии ───────────────

def _run_episode_script(track_id: int) -> None:
    db = SessionLocal()
    try:
        track = db.get(Track, track_id)
        if not track:
            return
        project = track.project
        key = _format_key(track)
        logline = _find_doc(db, project.id, "logline")
        synopsis = _find_doc(db, project.id, "synopsis")
        arc = _find_doc(db, project.id, "arc")
        sheet = _find_doc(db, project.id, "beatsheet")
        card = track.comment
        if sheet and sheet.body_json:
            try:
                rows = (json.loads(sheet.body_json) or {}).get("episodes") or []
                for r in rows:
                    if int(r.get("no") or 0) == int(track.episode_no or 0):
                        card = json.dumps(r, ensure_ascii=False)
                        break
            except Exception:  # noqa: BLE001
                pass
        import asyncio
        res = asyncio.run(claude.generate_episode_script(
            logline=logline.body if logline else "",
            synopsis=synopsis.body if synopsis else project.story,
            arcs=arc.body if arc else "",
            character_bible=project.character_bible,
            episode_card=card,
            episode_beats=formats.beats_block("series", key, "episode_beats"),
            previously=_episode_previously(db, track),
            rules=formats.rules("series"),
            comment=track.comment,
        ))
        acts = res.get("acts") or []
        text = "\n\n".join(f"[{a.get('act', '')}]\n{a.get('text', '')}" for a in acts)
        _put_doc(db, project.id, "script", track_id=track.id,
                 title=str(res.get("title") or track.title), body=text,
                 data={"acts": acts, "summary": res.get("summary", ""),
                       "recap_points": res.get("recap_points") or []},
                 status="", error="")
        if res.get("title") and not (track.title or "").strip():
            track.title = str(res["title"])[:200]
            db.commit()
        log.info("сценарий серии %s готов (%s актов)", track_id, len(acts))
    except Exception as e:  # noqa: BLE001
        db.rollback()
        tr = db.get(Track, track_id)
        if tr:
            _doc_status(tr.project_id, ("script",), "error", str(e)[:500], track_id)
        log.warning("сценарий серии %s упал: %s", track_id, e)
    finally:
        db.close()


@app.post("/api/tracks/{track_id}/generate-script")
def generate_script(track_id: int, user: User = Depends(current_user),
                    db: Session = Depends(db_session)):
    from threading import Thread
    track = _own_track(db, user, track_id)
    if _catalog_of(track.project) != "series":
        raise HTTPException(400, "сценарий по актам бывает только у серии")
    if not _find_doc(db, track.project_id, "logline"):
        raise HTTPException(400, "сначала собери библию сезона")
    _charge(db, user, COST_STORY, f"сценарий серии {track.id}",
            kind="story", ref_type="track", ref_id=track.id,
            track_id=track.id, project_id=track.project_id)
    _put_doc(db, track.project_id, "script", track_id=track.id,
             status="queued", error="")
    Thread(target=_run_episode_script, args=(track_id,), daemon=True).start()
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
    title: str = Form(""), lyrics: str = Form(""), comment: str = Form(""),
    style_keys: str = Form(""), style_extra: str = Form(""),
    # Поля режимов «сериалы» и «UGC». Клип их просто не присылает — Form с
    # дефолтом не ломает существующий сабмит формы «+ трек».
    format_key: str = Form(""), season_no: int = Form(0), episode_no: int = Form(0),
    audio: UploadFile | None = None,
    project_id: int | None = None,
    user: User = Depends(current_user), db: Session = Depends(db_session),
):
    project = get_or_create_project(db, user, project_id)
    if project.kind == "single" and project.tracks:
        raise HTTPException(400, "это сингл — трек может быть только один")
    max_pos = max((t.position for t in project.tracks), default=0)
    # Стиль приходит КЛЮЧАМИ (csv), текст промпта собирает сервер: реестр
    # живёт в prompts_catalog и в браузер не уезжает.
    keys = [k.strip() for k in (style_keys or "").split(",")
            if k.strip() in prompts_catalog.STYLE_KEYS][:3]
    extra = (style_extra or "").strip()[:2000]
    catalog = _catalog_of(project)
    fkey = str(format_key or "").strip()
    if not formats.format_spec(catalog, fkey):
        fkey = formats.default_format(catalog)
    track = Track(
        project_id=project.id, position=max_pos + 1,
        title=title, lyrics=lyrics, comment=comment,
        style_keys=",".join(keys), style_extra=extra,
        style=prompts_catalog.fusion(keys, extra),
        format_key=fkey,
        season_no=max(0, int(season_no or 0)),
        # Номер серии внутри сезона: не передан — считаем по уже имеющимся,
        # иначе весь сезон был бы «серией 0».
        episode_no=int(episode_no or 0) or (
            1 + sum(1 for t in project.tracks if t.season_no == max(0, int(season_no or 0)))
            if catalog == "series" else 0),
    )
    if audio is not None:
        ext = os.path.splitext(audio.filename or "")[1] or ".mp3"
        fname = f"{uuid.uuid4().hex}{ext}"
        path = os.path.join(UPLOAD_DIR, fname)
        data = await audio.read()
        with open(path, "wb") as f:
            f.write(data)
        track.audio_filename = fname
        _reg_file(db, fname, project.owner_id, kind="audio",
                  project_id=project.id, track_id=track.id)
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
    # style ЗДЕСЬ БОЛЬШЕ НЕ ПРИНИМАЕТСЯ. Раньше фронт присылал сюда собранный
    # промпт целиком — это и была вторая половина утечки реестра. Стиль
    # ставится ключами через POST /api/tracks/{id}/style, где текст собирает
    # сервер.
    for field in ("title", "lyrics", "comment"):
        if field in body:
            setattr(track, field, str(body[field]))
    if "film_grain" in body:
        track.film_grain = bool(body["film_grain"])
    if "no_story" in body:
        track.no_story = bool(body["no_story"])
    if "location_bible" in body:
        track.location_bible = str(body["location_bible"])[:4000]
    for num in ("season_no", "episode_no"):
        if num in body:
            setattr(track, num, max(0, int(body[num] or 0)))
    if "format_key" in body:
        want = str(body["format_key"] or "").strip()
        catalog = _catalog_of(track.project)
        track.format_key = want if formats.format_spec(catalog, want) else ""
    # ДВИЖКИ ОБЪЕКТА. Пустая строка — валидное значение и означает «как решит
    # тариф»: так владелец может снять свой выбор, а не остаться с ним навсегда.
    # Проверяем по реестру, а не по тарифу: тариф опустит недоступное сам в
    # момент генерации, а стереть уже сделанный выбор из-за смены тарифа нельзя.
    if "video_engine" in body:
        want = str(body["video_engine"] or "").strip()
        track.video_engine = want if want in mediagen.VIDEO_ENGINES else ""
    if "image_engine" in body:
        want = str(body["image_engine"] or "").strip()
        track.image_engine = want if want in mediagen.IMAGE_ENGINES else ""
    # ГЕОМЕТРИЯ КАДРА. Пустая строка валидна и означает «как у режима» —
    # снять свой выбор так же можно, как и сделать.
    if "aspect" in body:
        want = str(body["aspect"] or "").strip()
        track.aspect = want if want in mediagen.ASPECTS else ""
    if "image_resolution" in body:
        want = str(body["image_resolution"] or "").strip().upper()
        track.image_resolution = want if want in ("1K", "2K", "4K") else ""
    db.commit()
    return track_dict(track)


# ─────────────────────── раздел «Промты»: каталог ───────────────────────
# Реестр целиком лежит в backend/prompts_catalog.py и наружу отдаётся ТОЛЬКО
# через public_* — по белому списку полей. Текстов промптов в этих ответах
# нет и быть не может: фирменные пресеты сняты покадровым разбором виральных
# аккаунтов, и отдать их — значит отдать единственный ров сервиса.

def _is_pro(user: "User | None") -> bool:
    """Открыт ли человеку разбор приёма. PRO+ — то есть любой платный тариф."""
    return bool(user and (user.is_admin or _plan_of(user) != "free"))


def _style_uses(db: Session) -> dict[str, int]:
    """Сколько раз стиль реально доехал до СОБРАННОГО клипа.

    Считаем по клипам, а не по кликам: счётчик на клике накрутит любопытство,
    и цифра перестанет что-либо значить. У конкурента в этом месте вбитые
    руками просмотры чужих тиктоков — у нас будет настоящее число."""
    out: dict[str, int] = {}
    rows = (db.query(Track.style_keys)
            .filter(Track.style_keys != "", Track.clip_filename != "").all())
    for (keys,) in rows:
        for k in (keys or "").split(","):
            k = k.strip()
            if k:
                out[k] = out.get(k, 0) + 1
    return out


def _lang_of(request: Request, lang: str = "") -> str:
    """Язык карточки: явный параметр, иначе Accept-Language, иначе английский."""
    want = (lang or "").strip().lower()[:2]
    if want in ("en", "ru"):
        return want
    head = (request.headers.get("accept-language") or "").lower()
    return "ru" if head.startswith("ru") else "en"


@app.get("/api/styles")
def api_styles(request: Request, lang: str = "", group: str = "", tier: str = "",
               db: Session = Depends(db_session)):
    """Витрина раздела «Промты»: группы, подборки, стили и сюжетные каркасы.

    ПУБЛИЧНЫЙ роут — раздел индексируется и открывается без аккаунта, как и
    витрина цен. Тариф читается, только чтобы поставить замок на карточке."""
    user = _resolve_user(request, db)
    lg = _lang_of(request, lang)
    uses = _style_uses(db)
    plan_id = _plan_of(user) if user else "free"
    paid = plan_id != "free"
    styles = prompts_catalog.public_styles(lang=lg, group=group, tier=tier, uses=uses)
    for s in styles:
        # locked — «нельзя снимать на этом тарифе», а не «нельзя смотреть»:
        # карточка, превью и описание открыты всем и всегда.
        s["locked"] = bool(s.get("tier") == "pro" and not paid)
    return {
        "lang": lg,
        "groups": [
            {"key": g["key"], "label": g["label"][lg], "hint": g["hint"][lg]}
            for g in prompts_catalog.GROUPS
        ],
        "collections": [
            {"key": c["key"], "label": c["label"][lg], "desc": c["desc"][lg],
             "styles": list(c.get("styles") or []),
             "featured": bool(c.get("featured"))}
            for c in prompts_catalog.COLLECTIONS
        ],
        "tags": {
            axis: [{"key": k, "label": (v[lg] if isinstance(v, dict) else v)}
                   for k, v in vals.items()]
            for axis, vals in prompts_catalog.TAGS.items()
        },
        "styles": styles,
        "presets": prompts_catalog.public_presets(lang=lg),
        "plan": plan_id,
        "authorized": bool(user),
        # Сколько стилей можно смешать за раз — правило принадлежит серверу,
        # а не вёрстке чипов.
        "max_mix": 3,
    }


@app.get("/api/styles/{key}")
def api_style(key: str, request: Request, lang: str = "",
              db: Session = Depends(db_session)):
    """Страница одного стиля: карточка, превью, примеры кадров."""
    user = _resolve_user(request, db)
    lg = _lang_of(request, lang)
    card = prompts_catalog.public_style(key, lang=lg,
                                        uses=_style_uses(db).get(key, 0))
    if not card:
        raise ApiError(404, "unknown_style", f"Unknown style: {key!r}")
    card["locked"] = bool(card.get("tier") == "pro"
                          and (not user or _plan_of(user) == "free"))
    # Разбор приёма — отдельным полем и только если он реально доступен:
    # closed-стили не раскрываются никогда и ни на каком тарифе.
    card["structure"] = prompts_catalog.style_structure(key, is_pro=_is_pro(user))
    card["structure_locked"] = bool(card.get("has_structure") and not card["structure"])
    return card


@app.get("/api/presets")
def api_presets(request: Request, lang: str = "", kind: str = ""):
    """Сюжетные каркасы — «что снимаем». Второй слой раздела: стиль отвечает
    на «как выглядит», каркас — на «что происходит в кадре»."""
    lg = _lang_of(request, lang)
    return {"lang": lg, "presets": prompts_catalog.public_presets(lang=lg, kind=kind)}


@app.get("/api/presets/{key}")
def api_preset(key: str, request: Request, lang: str = ""):
    card = prompts_catalog.public_preset(key, lang=_lang_of(request, lang))
    if not card:
        raise ApiError(404, "unknown_preset", f"Unknown preset: {key!r}")
    return card


@app.post("/api/tracks/{track_id}/style")
async def set_track_style(track_id: int, request: Request,
                          user: User = Depends(current_user),
                          db: Session = Depends(db_session)):
    """Применить стиль (и, если попросили, сюжетный каркас) к треку.

    body: {style_keys: [...], extra?: "", preset?: "ключ каркаса"}

    Текст промпта собирает СЕРВЕР: клиент присылает ключи, обратно получает
    ключи. Так реестр остаётся на сервере целиком, а не разъезжается по
    браузерам вместе с бандлом.

    Каркас ложится НЕ в поле стиля, а в свои: story-каркас пишет сквозной
    сюжет проекта и режиссёрскую заметку трека, punch-каркас поднимает
    no_story — и claude.py уходит в готовую ветку «РЕЖИМ БЕЗ СЮЖЕТА»."""
    track = _own_track(db, user, track_id)
    body = await request.json() if await request.body() else {}

    raw = body.get("style_keys")
    if isinstance(raw, str):
        raw = raw.split(",")
    keys: list[str] = []
    seen: set[str] = set()
    paid = bool(user.is_admin or _plan_of(user) != "free")
    for item in (raw or []):
        k = str(item or "").strip()
        if not k or k in seen or k not in prompts_catalog.STYLE_KEYS:
            continue
        # Тариф решает, чем СНИМАТЬ. Закрытый тарифом стиль молча пропускаем,
        # а не падаем ошибкой: кнопка обязана работать, просто без него.
        if not paid and (prompts_catalog.public_style(k) or {}).get("tier") == "pro":
            continue
        seen.add(k)
        keys.append(k)
        if len(keys) >= 3:      # больше трёх стилей — каша, а не микс
            break

    extra = str(body.get("extra") or "").strip()[:2000]
    track.style_keys = ",".join(keys)
    track.style_extra = extra
    track.style = prompts_catalog.fusion(keys, extra)

    if "preset" in body:
        pkey = str(body.get("preset") or "").strip()
        preset = prompts_catalog.public_preset(pkey) if pkey else None
        track.clip_preset_key = preset["key"] if preset else ""
        if preset:
            seed = prompts_catalog.preset_seed(preset["key"])
            track.no_story = bool(preset.get("no_story"))
            if seed.get("note"):
                track.director_note = seed["note"]
            # Сквозной сюжет — свойство ПРОЕКТА, и перетирать чужой текст
            # нельзя: подставляем только в пустой.
            if seed.get("story") and not (track.project.story or "").strip():
                track.project.story = seed["story"]
                track.project.story_status = "done"

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
    # Каскад БД снесёт строки, но не файлы на диске — их убираем сами.
    for ph in track.photos:
        _remove_media(ph.filename)
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
    _reg_file(db, project.cover_filename, project.owner_id, kind="cover",
              project_id=project.id)
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
    _reg_file(db, track.cover_filename, track.project.owner_id, kind="cover",
              project_id=track.project_id, track_id=track.id)
    db.commit()
    db.refresh(track)
    _remove_media(old)
    return {"ok": True, "cover_url": f"/api/media/{track.cover_filename}"}


# ─────────────────────────────── сцены ───────────────────────────────
# Раскадровка у всех режимов ОДНА: сцены → кадры → видео → сборка. Различается
# только промпт, которым сцены пишутся, и каркас, который в него подставляется.
# Поэтому ниже — три ветки на входе и общая запись в базу на выходе, а не три
# конвейера.

def _track_duration(track: Track) -> int:
    """Целевой хронометраж объекта в секундах.

    У клипа он объективен — длина дорожки. У ролика и серии дорожки нет:
    длину задаёт число слотов режима на среднюю длину кадра. Поэтому
    audio_duration_sec тут не «пустое поле», а просто неприменимое."""
    if track.audio_duration_sec:
        return int(track.audio_duration_sec)
    mode = _mode_of(track.project)
    lo, hi = mode["scenes"]["slot"]
    return int(mode["scenes"]["typ"] * (lo + hi) / 2)


def _episode_previously(db: Session, track: Track) -> str:
    """«В предыдущих сериях»: сводки серий того же сезона до текущей.

    Без этого блока каждая серия пишется так, будто она первая: герой заново
    знакомится с тем, что уже знает, и сезон рассыпается на не связанные
    эпизоды."""
    prev = [t for t in track.project.tracks
            if t.season_no == track.season_no and t.position < track.position]
    lines = []
    for t in sorted(prev, key=lambda x: x.position):
        doc = (db.query(Doc)
               .filter(Doc.track_id == t.id, Doc.kind == "script").first())
        summary = ""
        if doc:
            try:
                summary = (json.loads(doc.body_json or "{}") or {}).get("summary", "")
            except Exception:  # noqa: BLE001
                summary = ""
        lines.append(f"Серия {t.episode_no or t.position} «{t.title}»: "
                     f"{summary or t.comment or '(без сводки)'}")
    return "\n".join(lines)


def _scenes_for_series(db: Session, track: Track) -> dict:
    """Сцены серии: сценарий по актам → кадры. Сценарий обязателен — без него
    разбивка выдумывает сюжет заново и расходится с поэпизодным планом."""
    import asyncio
    project = track.project
    catalog = "series"
    key = _format_key(track)
    script_doc = (db.query(Doc)
                  .filter(Doc.track_id == track.id, Doc.kind == "script").first())
    if not script_doc or not (script_doc.body or "").strip():
        raise RuntimeError("у серии нет сценария — сгенерируй его на шаге «Серия»")
    return asyncio.run(claude.generate_series_scenes(
        script=script_doc.body,
        character_bible=project.character_bible,
        episode_beats=formats.beats_block(catalog, key, "episode_beats"),
        style=track.style,
        duration_sec=_track_duration(track),
        rules=formats.rules(catalog),
        characters=characters_payload(project),
        comment=track.comment,
    ))


def _scenes_for_ugc(db: Session, track: Track) -> dict:
    """Слоты UGC-ролика. Бриф необязателен: формат сам по себе — каркас."""
    import asyncio
    project = track.project
    catalog = "ugc"
    key = _format_key(track)
    spec = formats.format_spec(catalog, key) or {}
    brief_doc = (db.query(Doc)
                 .filter(Doc.track_id == track.id, Doc.kind == "brief").first())
    persona_doc = (db.query(Doc)
                   .filter(Doc.project_id == project.id, Doc.track_id.is_(None),
                           Doc.kind == "persona").first())
    loc_doc = (db.query(Doc)
               .filter(Doc.project_id == project.id, Doc.track_id.is_(None),
                       Doc.kind == "location").first())
    slots = int((spec.get("slots") or {}).get("typ") or 8)
    return asyncio.run(claude.generate_ugc_scenes(
        persona=persona_doc.body if persona_doc else "",
        character_bible=project.character_bible,
        # Локация ролика: своя, иначе общая локация канала. Второй уровень
        # нужен ровно затем, чтобы «сегодня снимаем на кухне» не переписывало
        # студию всего канала.
        location_bible=track.location_bible or (loc_doc.body if loc_doc else ""),
        format_beats=formats.beats_block(catalog, key, "beats"),
        format_note=formats.seed(key).get("note", ""),
        brief=(brief_doc.body if brief_doc else "") or track.comment,
        style=track.style,
        slots=slots,
        duration_sec=_track_duration(track),
        rules=formats.rules(catalog),
        lang="ru",
    ))


def _scenes_for_mockup(db: Session, track: Track) -> dict:
    """Кадры съёмки товара. Бриф необязателен: набор сцен сам по себе план,
    а фирменный мир проекта задаёт всё остальное."""
    import asyncio
    project = track.project
    catalog = "mockup"
    key = _format_key(track)
    spec = formats.format_spec(catalog, key) or {}
    brief_doc = (db.query(Doc)
                 .filter(Doc.track_id == track.id, Doc.kind == "brief").first())
    brand_doc = (db.query(Doc)
                 .filter(Doc.project_id == project.id, Doc.track_id.is_(None),
                         Doc.kind == "brandbook").first())
    shots = len(spec.get("shot_list") or []) or int((spec.get("shots") or {}).get("typ") or 6)
    return asyncio.run(claude.generate_mockup_shots(
        brandbook=brand_doc.body if brand_doc else "",
        brief=(brief_doc.body if brief_doc else "") or track.comment,
        shots_block=formats.shots_block(key),
        set_note=formats.seed(key).get("note", ""),
        style=track.style,
        shots=shots,
        rules=formats.rules(catalog),
        comment=track.comment,
    ))


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
        catalog = _catalog_of(project)
        # Заметка из отдельного поля; старые треки могли хранить её в комментарии.
        track_note = track.director_note
        if not track_note:
            note_match = re.search(r"\[режиссёрская заметка\]\s*(.+)$", track.comment, re.DOTALL)
            track_note = note_match.group(1).strip() if note_match else ""
        clean_comment = re.sub(r"\n*\[режиссёрская заметка\].*$", "", track.comment, flags=re.DOTALL).strip()
        import asyncio
        if catalog == "series":
            result = _scenes_for_series(db, track)
        elif catalog == "ugc":
            result = _scenes_for_ugc(db, track)
        elif catalog == "mockup":
            result = _scenes_for_mockup(db, track)
        else:
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
            # Реплика серии и строка трека — ОДНО поле lyric_line: у клипа это
            # строка песни, у серии реплика, у ролика фраза блогера. Второе
            # поле под текст завело бы три способа сказать одно и то же.
            speaker = str(sc.get("speaker") or "")
            line = str(sc.get("line") or sc.get("lyric_line") or "")
            chars = [str(n) for n in (sc.get("characters") or []) if str(n).strip()]
            if speaker and speaker not in chars:
                chars.append(speaker)
            db.add(Scene(
                track_id=track.id, position=i, start_sec=cursor, duration_sec=dur,
                lyric_line=line,
                characters=", ".join(chars),
                act=str(sc.get("act") or ""),
                speaker=speaker,
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
    catalog = _catalog_of(track.project)
    # Предусловие у каждого режима своё: клипу нужен сюжет проекта, серии —
    # её сценарий, ролику не нужно ничего (каркас формата и есть план).
    if catalog == "clip":
        if not track.project.story and not track.no_story:
            raise HTTPException(400, "сначала сгенерируй общий сюжет проекта (или включи «без сюжета»)")
    elif catalog == "series":
        has_script = db.query(Doc).filter(
            Doc.track_id == track.id, Doc.kind == "script",
            Doc.body != "").count()
        if not has_script:
            raise HTTPException(400, "сначала сгенерируй сценарий серии")
    elif catalog == "mockup":
        # Мокап без фото упаковки — это не съёмка, а фантазия: генератор
        # нарисует «похожую» банку с выдуманной этикеткой, и это выяснится
        # только после списания токенов за все шесть кадров.
        if not _track_photo_paths(track, 1):
            raise HTTPException(400, "сначала загрузи фото упаковки — по нему совпадает товар")
    _charge(db, user, COST_SCENES, f"раскадровка трека {track.id}",
            kind="story", ref_type="track", ref_id=track.id,
            track_id=track.id, project_id=track.project_id)
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
    _reg_file(db, fname, scene.track.project.owner_id, kind="ref",
              project_id=scene.track.project_id, track_id=scene.track_id,
              scene_id=scene.id)
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


def _save_image(data: bytes, mime: str, *, upscale: bool = True,
                aspect: str = "") -> str:
    fname = f"scene_{uuid.uuid4().hex}{_mime_ext(mime)}"
    path = os.path.join(UPLOAD_DIR, fname)
    with open(path, "wb") as f:
        f.write(data)
    if upscale:
        # Аспект обязателен: квадратный кадр, прогнанный через вертикальный
        # апскейл, приезжает с чёрными полями сверху и снизу.
        mediagen.upscale_to_4k(path, aspect or mediagen.DEFAULT_ASPECT)
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
        if prompt:
            # Модель сама выбирала раскладку («аккуратной сеткой»), и лист
            # выходил то 4x2, то 3x3 — нарезка резала мимо. Диктуем жёстко.
            _c, _r = sheet_grid(len(track.scenes))
            prompt = (f"{prompt}\n\nGRID (mandatory): exactly {_c} columns by {_r} rows, "
                      f"{_c * _r} equal rectangular panels of identical size, "
                      f"filling the whole image edge to edge. No outer margin, no gaps "
                      f"between panels, no rounded corners, no page background visible. "
                      f"Panels are numbered left to right, top to bottom.")
        if not prompt:
            raise RuntimeError("Claude не вернул промпт листа раскадровки")
        # Лист: референсом идёт КОЛЛАЖ моделек всех героев трека (до 3) — так
        # лица узнаваемы. Одна моделька референсом копировалась целиком вместо
        # сетки кадров, поэтому именно коллаж, а не одно фото.
        board_ref = None
        board_collage = ""
        paths = []
        for c in sorted(track.project.characters, key=lambda x: (not x.is_main, x.position)):
            # Каноническая моделька героя — разворот, если он есть (см.
            # _character_model_file): лист раскадровки должен опираться на то
            # же, на что и кадры сцен, иначе герой «плывёт» между ними.
            photo = _character_model_file(c)
            if photo:
                paths.append(os.path.join(UPLOAD_DIR, photo.filename))
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
        # Запоминаем сетку, по которой лист заказан: нарезка обязана резать
        # ровно по ней, а не пересчитывать заново.
        _c, _r = sheet_grid(len(track.scenes))
        track.storyboard_grid = f"{_c}x{_r}"
        _reg_file(db, track.storyboard_filename, track.project.owner_id,
                  kind="storyboard", project_id=track.project_id, track_id=track.id)
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


def sheet_grid(n: int) -> tuple[int, int]:
    """Единая сетка листа раскадровки: одна и та же для ПРОМПТА генерации и
    для НАРЕЗКИ. Раньше промпт просил «аккуратную сетку» на усмотрение модели,
    а нарезка считала свою — панели не совпадали, и куски резались со сдвигом."""
    n = max(1, int(n))
    cols = 2 if n <= 4 else (3 if n <= 9 else 4)
    rows = -(-n // cols)
    return cols, rows


def track_grid(track) -> tuple[int, int]:
    """Сетка конкретного листа: сохранённая при генерации, иначе расчётная."""
    raw = (getattr(track, "storyboard_grid", "") or "").lower()
    if "x" in raw:
        try:
            c, r = raw.split("x", 1)
            if int(c) > 0 and int(r) > 0:
                return int(c), int(r)
        except (TypeError, ValueError):
            pass
    return sheet_grid(len(track.scenes))


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
    cols, rows = track_grid(track)
    n = min(max(1, len(track.scenes)), cols * rows)
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
        _reg_file(db, fname, track.project.owner_id, kind="frame",
                  project_id=track.project_id, track_id=track.id)
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
        _reg_file(db, new_name, track.project.owner_id, kind="frame",
                  project_id=track.project_id, track_id=track.id, scene_id=scene.id)
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
    # Та же сетка, что у листа: иначе «разложить по кадрам» режет мимо панелей.
    cols, rows = track_grid(track)
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
        _reg_file(db, fname, track.project.owner_id, kind="frame",
                  project_id=track.project_id, track_id=track.id, scene_id=sc.id)
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
    sheet_engine = _plan_image_engine(user)
    _charge(db, user, _image_cost(user), f"лист раскадровки трека {track.id}",
            kind="sheet", engine=sheet_engine, ref_type="track", ref_id=track.id,
            track_id=track.id, project_id=track.project_id,
            cost_cents=_cost_cents("sheet", sheet_engine))
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


def _character_model_file(c: Character) -> "CharacterPhoto | None":
    """Каноническая моделька героя: ПОСЛЕДНИЙ сгенерированный разворот, а если
    развороты не делали — первое загруженное фото.

    Раньше здесь безусловно брался photos[0], то есть самое старое СЕЛФИ:
    кадры сцен всё это время опирались не на то, что владелец считает
    моделькой, и кнопка «сгенерировать модельку» на кадры не влияла вообще."""
    models = [p for p in c.photos if (p.kind or "photo") == "model"]
    for p in sorted(models, key=lambda x: (x.position, x.id), reverse=True):
        if os.path.exists(os.path.join(UPLOAD_DIR, p.filename)):
            return p
    for p in sorted(c.photos, key=lambda x: (x.position, x.id)):
        if os.path.exists(os.path.join(UPLOAD_DIR, p.filename)):
            return p
    return None


def _character_model_paths(chars: list[Character], limit: int,
                           prefer_photo: bool = False) -> list[str]:
    """Референсы персонажей (по одному на героя) — они отвечают только за
    узнаваемость лица, не за стилистику кадра.

    prefer_photo=True — для КАДРОВ СЦЕН: там берём обычное фото, а не
    сгенерированный разворот. Разворот сам является сеткой из нескольких
    ракурсов, и генератор воспроизводил именно её: вместо сцены выходил
    второй character sheet. Для листа раскадровки и самой генерации моделек
    разворот по-прежнему уместен."""
    paths: list[str] = []
    for c in chars:
        if len(paths) >= limit:
            break
        photo = None
        if prefer_photo:
            plain = [x for x in c.photos if (x.kind or "photo") != "model"]
            for x in sorted(plain, key=lambda y: (y.position, y.id)):
                if os.path.exists(os.path.join(UPLOAD_DIR, x.filename)):
                    photo = x
                    break
        if photo is None:
            photo = _character_model_file(c)
        if photo:
            paths.append(os.path.join(UPLOAD_DIR, photo.filename))
    return paths


def _track_photo_paths(track: Track, limit: int = 4) -> list[str]:
    """Живые фото товара по порядку. Правило то же, что у персонажа: если
    разворот (kind="model") сделан — он идёт первым, иначе загруженные фото."""
    rows = sorted(track.photos, key=lambda x: (x.position, x.id))
    models = [p for p in rows if (p.kind or "photo") == "model"]
    plain = [p for p in rows if (p.kind or "photo") != "model"]
    out: list[str] = []
    for p in (models[-1:] + plain):
        path = os.path.join(UPLOAD_DIR, p.filename)
        if os.path.exists(path) and path not in out:
            out.append(path)
        if len(out) >= limit:
            break
    return out


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
    # МОКАПЫ: узнаваемое здесь — упаковка, а не лицо. Фото товара главнее
    # всего остального: по нему совпадают силуэт, раскладка этикетки и
    # каждое читаемое слово, а перерисованная моделька героя тут не нужна.
    if formats.mode_of_kind(project.kind)["id"] == "mockup":
        item = _track_photo_paths(scene.track, 3) if scene.track else []
        if scene_refs and item:
            return _ref_collage(db, [item[0], scene_refs[0]], project.owner_id) or item[0]
        if item:
            return item[0]
        return scene_refs[0] if scene_refs else None
    if scene_refs:
        models = _character_model_paths(
            chars or [c for c in project.characters if c.is_main], 2, prefer_photo=True)
        # Реф первым: первая картинка коллажа для генератора — главная.
        return _ref_collage(db, [scene_refs[0], *models], project.owner_id) or scene_refs[0]

    attr_path = _scene_attribute_photo(scene, chars)
    if attr_path:
        return attr_path
    if not chars:
        chars = [c for c in project.characters if c.is_main]
    paths = _character_model_paths(chars, 3, prefer_photo=True)
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
    if formats.mode_of_kind(project.kind)["id"] == "mockup":
        # Фото упаковки ПЕРВЫМИ и все: Nano Banana берёт до восьми отдельных
        # картинок, и чем больше ракурсов товара он видит, тем меньше
        # додумывает форму крышки и надписи.
        out += _track_photo_paths(scene.track, 5) if scene.track else []
        out += scene_refs[:3]
        seen: set[str] = set()
        uniq = [x for x in out if not (x in seen or seen.add(x))]
        return uniq[:8]
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
            desc = (c.description or "").strip()
            if desc:
                parts.append(
                    f"Character '{c.name}' (must stay identical across every shot"
                    f" of the whole album): {desc}"
                )
            else:
                # Описание пустое — раньше в промпт уходило «Character 'Артём': »
                # с пустым хвостом, и модель лепила случайного человека, потому
                # что словами о нём не сказано НИЧЕГО. Опираемся на референс явно.
                parts.append(
                    f"Character '{c.name}': take the face, hair, body type and "
                    f"clothing STRICTLY from the reference photo of this person. "
                    f"Same face in every shot — do not invent another person, "
                    f"do not beautify, do not change age, ethnicity or hairstyle."
                )
    else:
        bible = (project.character_bible or "").strip()
        if bible:
            parts.append(
                f"Main character reference (must stay identical across every shot): {bible}"
            )
        else:
            parts.append(
                "Main character: take the face, hair, body type and clothing STRICTLY "
                "from the reference photo. Same person in every shot — do not invent "
                "another face, do not beautify, do not change age or ethnicity."
            )
    # 4. Анти-требования: ровно те грабли, из-за которых кадры выходили
    # одинаковыми тёмными портретами на сером фоне.
    parts.append(
        "Do not copy the reference photo as-is. Do not output a studio portrait or a plain grey "
        "backdrop unless the style says so. Expose for a bright readable image: avoid crushed "
        "blacks and muddy dark frames unless the style explicitly asks for night noir."
    )
    # 4b. Самое частое разрушение кадра: реф-моделька персонажа сама по себе
    # является СЕТКОЙ из нескольких ракурсов на нейтральном фоне, и генератор
    # воспроизводит именно эту раскладку — вместо сцены выходит второй
    # character sheet. Запрещаем явно и повторяем формулировками, которые
    # модель понимает буквально.
    parts.append(
        "CRITICAL: some reference images are character turnaround sheets — several views of the "
        "same person side by side on a neutral backdrop. Take ONLY the person's identity from "
        "them: face, hair, body type, clothing. NEVER reproduce their layout. The output is ONE "
        "single photographic frame of a real scene: one camera angle, one moment in time, real "
        "environment. Absolutely no multi-panel grids, no split screens, no side-by-side views, "
        "no contact sheets, no turnarounds, no character sheets, no collage, no white or grey "
        "studio cyclorama, no repeated figures of the same character within the frame."
    )
    # 4c. Идентичность важнее красоты: генераторы склонны «улучшать» лицо и
    # подменять человека похожим типажом — для сквозного героя альбома это
    # разрушает всю затею.
    parts.append(
        "IDENTITY IS THE HIGHEST PRIORITY after the style: the person in this frame must be "
        "recognisably the SAME human as in the reference photo — same facial structure, same "
        "hair, same skin tone, same age. Keep their real face; never replace them with a "
        "generic model or a better-looking lookalike."
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


def _run_scene_frames(scene_id: int, which: str = "both", engine: str = "") -> None:
    """which: both | first | last — что именно пересобираем.
    engine — явный движок кадров (chatgpt / nano-banana…); пустая строка
    означает «взять дефолт тарифа»."""
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
        mediagen.reset_task()
        owner = db.get(User, track.project.owner_id) if track.project.owner_id else None
        # Раньше здесь стояло `engine = _plan_image_engine(owner)` — переданный
        # параметр молча затирался дефолтом тарифа, и выбор движка в интерфейсе
        # не значил вообще ничего. Теперь работает цепочка «запрос → трек → тариф».
        engine = _resolve_image_engine(owner, track, engine)
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
        aspect = _track_aspect(track)
        # Разрешение просит объект (у мокапа этикетку читают с экрана
        # телефона), иначе решает движок по своему дефолту.
        img_res = (track.image_resolution or "").strip()
        if which in ("both", "first"):
            res = asyncio.run(mediagen.generate_image_ex(
                _frame_prompt(scene, track, "first"), reference,
                reference_paths=ref_list, engine=engine,
                resolution=img_res, aspect=aspect))
            first_data, first_mime = res["data"], res["mime"]
            native_4k = res["native_4k"]
            scene.image_engine = res["engine"]
        if which in ("both", "last"):
            res = asyncio.run(mediagen.generate_image_ex(
                _frame_prompt(scene, track, "last"), reference,
                reference_paths=ref_list, engine=engine,
                resolution=img_res, aspect=aspect))
            last_data, last_mime = res["data"], res["mime"]
            native_4k = native_4k or res["native_4k"]
            scene.image_engine = res["engine"]

        _attach_task(db, "scene", scene.id, mediagen.last_task_id(), "frames")
        old_first, old_last = scene.image_filename, scene.image_last_filename
        old_video, old_audio = scene.video_filename, scene.audio_filename
        old_mids = [m.get("filename", "") for m in _midframes(scene)]
        if first_data is not None:
            scene.image_filename = _save_image(first_data, first_mime,
                                               upscale=not native_4k, aspect=aspect)
            _reg_file(db, scene.image_filename, track.project.owner_id, kind="frame",
                      project_id=track.project_id, track_id=track.id, scene_id=scene.id)
        else:
            old_first = ""  # первый кадр не пересобирали — оставляем как есть
        if last_data is not None:
            scene.image_last_filename = _save_image(last_data, last_mime,
                                                    upscale=not native_4k, aspect=aspect)
            _reg_file(db, scene.image_last_filename, track.project.owner_id,
                      kind="frame_last", project_id=track.project_id,
                      track_id=track.id, scene_id=scene.id)
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
def generate_scene_frames(scene_id: int, which: str = "both", engine: str = "",
                          user: User = Depends(current_user),
                          db: Session = Depends(db_session)):
    from threading import Thread
    scene = _own_scene(db, user, scene_id)
    if not scene.image_prompt.strip():
        raise HTTPException(400, "у сцены пуст промпт первого кадра")
    if which not in ("both", "first", "last"):
        which = "both"
    # Движок разрешаем ДО списания: цена кадров зависит именно от него, а сам
    # он берётся по цепочке «явный выбор → движок объекта → тариф». Раньше
    # выбор из карточки кадра доезжал сюда и молча затирался внутри
    # _run_scene_frames дефолтом тарифа.
    engine = _resolve_image_engine(user, scene.track, engine)
    _scene_charge(db, user, scene, _frames_cost(user, scene, engine),
                  f"кадры сцены {scene.id} ({which})", kind="frames", engine=engine)
    scene.image_status = "queued"
    db.commit()
    Thread(target=_run_scene_frames, args=(scene_id, which, engine), daemon=True).start()
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
            _reg_file(db, fname, track.project.owner_id, kind="midframe",
                      project_id=track.project_id, track_id=track.id, scene_id=scene.id)
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
                  f"промежуточные кадры сцены {scene.id}",
                  kind="frames", engine=scene.image_engine or _plan_image_engine(user))
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
        mediagen.reset_task()
        owner = db.get(User, track.project.owner_id) if track.project.owner_id else None
        # Семейство знает фронт, конкретную модель выбирает тариф — и она
        # уже записана на сцене при списании, чтобы движок не «переехал»
        # между оплатой и генерацией.
        engine = scene.video_engine or _plan_video_engine(owner, scene.video_provider)
        fname = asyncio.run(mediagen.animate_scene(
            prompt=scene.motion_prompt, first_path=first_path, last_path=last_path,
            duration_sec=scene.duration_sec, provider=scene.video_provider,
            seedance_model=PLANS[_plan_of(owner)].get("seedance_model", "") if owner else "",
            engine=engine, aspect=_track_aspect(track),
        ))
        # Задача внешнего движка — в строку списания: «списали 154 токена →
        # задача kie abc123». Без неё спорную генерацию разобрать нечем.
        _attach_task(db, "scene", scene.id, mediagen.last_task_id(), "video")
        old_video = scene.video_filename
        scene.video_filename = fname
        _reg_file(db, fname, track.project.owner_id, kind="video",
                  project_id=track.project_id, track_id=track.id, scene_id=scene.id)
        scene.video_status = "done"

        # Отрезок трека ровно под эту сцену — слушаем видео с его музыкой.
        old_audio = scene.audio_filename
        audio_src = _track_audio_path(track)
        if audio_src:
            try:
                scene.audio_filename = mediagen.slice_audio(
                    audio_src, scene.start_sec, scene.duration_sec)
                _reg_file(db, scene.audio_filename, track.project.owner_id,
                          kind="audio", project_id=track.project_id,
                          track_id=track.id, scene_id=scene.id)
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
    engine = _resolve_video_engine(user, scene.track, provider,
                                   str(body.get("engine") or ""))
    # Цена по движку: Grok идёт по нашей подписке и стоит в разы дешевле
    # платного Seedance 2.5 — раньше все платные списывали одинаковые 16.
    cost = _scene_cost(user, provider, scene, engine)
    if scene.video_filename:
        # Перерендер: у сцены уже есть видео, и это НОВЫЙ вызов платного API.
        # Берём цену самого видео — цену сцены без аванса за кадры (их не
        # перерисовываем). У Grok разница нулевая, и перерендер бесплатен:
        # он и правда ничего нам не стоит.
        _charge(db, user, max(0, cost - _frames_cost(user, scene)),
                f"перерендер видео сцены {scene.id} ({engine})",
                kind="video", engine=engine,
                cost_cents=_cost_cents("video", engine, seconds=scene.duration_sec),
                **_scene_meta(scene))
    else:
        _scene_charge(db, user, scene, cost, f"видео сцены {scene.id} ({engine})",
                      kind="video", engine=engine)
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
            videos, _track_audio_path(track), film_grain=track.film_grain,
            aspect=_track_aspect(track))
        _reg_file(db, track.clip_filename, track.project.owner_id, kind="clip",
                  project_id=track.project_id, track_id=track.id)
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
    больше или меньше. Лишние оплаченные сцены возвращаем токенами, недостающие
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
    # Добор — это сцены, которые МЫ РЕАЛЬНО СГЕНЕРИМ, то есть живые деньги
    # движку. Без cost_cents они падали в журнал с себестоимостью 0, и на
    # длинных треках (там, где добор и случается) маржа выглядела тем выше,
    # чем сильнее оценка промахнулась мимо реальности.
    # Движок выводим ТЕМ ЖЕ путём, что и на списании предоплаты (см.
    # supergen_start), иначе себестоимость посчиталась бы по чужому прайсу.
    eng = _resolve_video_engine(
        owner, track,
        _allowed_provider(owner, "seedance" if mediagen.seedance_available() else "grok")
    ) if owner else ""
    if need and owner and not _take_points(
            db, owner, need, f"супергенерация трека {track.id}: добор по факту",
            kind="video", engine=eng, ref_type="track", ref_id=track.id,
            track_id=track.id, project_id=track.project_id,
            cost_cents=_cost_cents("video", eng,
                                   count=max(1, need // max(1, per_scene)))):
        return (f"не хватило {need} токенов: трек длиннее оценки "
                f"({len(scenes)} сцен). Пополни баланс и запусти ещё раз")
    for s in unpaid:
        s.charged_points = per_scene
    if left and owner:
        # Оценка была щедрее реальности — неиспользованное возвращаем.
        # Через ту же дверь, что и всё остальное: прямое присваивание
        # gen_points здесь однажды уже увело журнал в сторону от баланса.
        _move_points(db, owner, left * per_scene,
                     f"возврат предоплаты супергенерации трека {track.id}",
                     kind="refund", ref_type="track", ref_id=track.id,
                     track_id=track.id, project_id=track.project_id)
        log.info("супергенерация трека %s: вернули %s токенов за %s лишних сцен",
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

        if _catalog_of(project) != "clip":
            # У серии сквозной документ — сценарий (он уже обязателен на входе
            # роута), у ролика — каркас формата. Сюжет проекта им не нужен и
            # генерить его тут значило бы платить за текст, который никто
            # не прочитает.
            pass
        elif track.no_story:
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
        # Движок кадров фиксируем один раз на весь конвейер и берём С ТРЕКА:
        # соседние сцены одного объекта не должны уехать на разные модели.
        sg_owner = db.get(User, track.project.owner_id) if track.project.owner_id else None
        img_engine = _resolve_image_engine(sg_owner, track)
        for i, sid in enumerate(scene_ids, 1):
            db.expire_all()
            s = db.get(Scene, sid)
            if not (s and s.image_filename and s.image_last_filename):
                note(f"кадры: сцена {i}/{total}…")
                _run_scene_frames(sid, engine=img_engine)
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
        engine = _resolve_video_engine(owner, track, provider)
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
    catalog = _catalog_of(track.project)
    # Дорожка обязательна только там, где она ЗАДАЁТ ритм. У ролика и серии
    # ритм задают слоты и акты, и требовать mp3 значило бы запрещать им
    # супергенерацию без всякой причины.
    if catalog == "clip" and not track.audio_filename:
        raise HTTPException(400, "у трека нет аудио — загрузи дорожку")
    if catalog == "series" and not _find_doc(db, track.project_id, "script", track.id):
        raise HTTPException(400, "у серии нет сценария — сгенерируй его на шаге «Серия»")
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
    vid_engine = _resolve_video_engine(user, track, prov)
    img_engine = _resolve_image_engine(user, track)
    per_scene = _scene_cost(user, prov, engine=vid_engine)
    scenes = list(track.scenes)
    if not (track.project.story or "").strip():
        _charge(db, user, COST_STORY, f"сюжет проекта {track.project.id}",
                kind="story", ref_type="project", ref_id=track.project_id,
                project_id=track.project_id)
    prepaid = 0
    if scenes:
        # Сцены уже есть: платим только за ту работу, которую конвейер реально
        # сделает. Готовое видео он не перерисовывает — брать за него нельзя.
        def _sg_cost(s: Scene) -> int:
            if not s.video_filename:
                return per_scene    # полный круг: кадры + видео
            if not (s.image_filename and s.image_last_filename):
                # видео есть, дорисуем недостающие кадры — по цене СВОЕГО движка
                return _frames_cost(user, s, img_engine)
            return 0                # делать нечего

        _scenes_charge(db, user, scenes, _sg_cost,
                       f"супергенерация трека {track.id} ({vid_engine})",
                       kind="video", engine=vid_engine, track_id=track.id,
                       project_id=track.project_id)
    else:
        # Сцен ещё нет — объём оцениваем по длительности трека (~6 сек на сцену).
        # Прежняя оценка упиралась в потолок 30 сцен: четырёхминутный трек
        # списывал как трёхминутный, а работу делал всю. Теперь оценка честная,
        # а расхождение с реальностью разводит _settle_supergen: недостачу
        # добирает, лишнее возвращает.
        prepaid = _est_scenes(_track_duration(track))
        _charge(db, user, COST_SCENES + per_scene * prepaid,
                f"супергенерация трека {track.id} ({vid_engine}, ~{prepaid} сцен)",
                kind="video", ref_type="track", ref_id=track.id, engine=vid_engine,
                track_id=track.id, project_id=track.project_id,
                cost_cents=_cost_cents("video", vid_engine, count=prepaid))
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


# Подписанная временная ссылка на наш файл. Нужна внешним движкам (kie.ai),
# которые принимают ТОЛЬКО url, а не байты: у них нет ручки загрузки файла
# (/api/file-base64-upload отвечает 404 — проверено на живом ключе).
# Токен подписан нашим секретом и живёт час: этого хватает движку скачать
# кадр, но ссылка не становится вечной раздачей приватного файла.
_pub_signer = URLSafeTimedSerializer(SECRET_KEY, salt="rapclips-pubfile")
PUBFILE_TTL_S = int(os.environ.get("PUBFILE_TTL_S", "3600"))


def pub_file_token(filename: str) -> str:
    return _pub_signer.dumps({"f": os.path.basename(filename)})


def pub_file_url(filename: str) -> str:
    base = (os.environ.get("PUBLIC_BASE_URL", "") or "").rstrip("/")
    return f"{base}/pub/{pub_file_token(filename)}" if base else ""


@app.get("/pub/{token}")
def get_public_file(token: str, request: Request):
    """Отдача файла по подписанному токену — без сессии и без владельца.
    Проверяем подпись и срок; подделать ссылку нельзя, перебрать — тоже."""
    try:
        data = _pub_signer.loads(token, max_age=PUBFILE_TTL_S)
    except SignatureExpired:
        raise HTTPException(410, "ссылка истекла")
    except BadSignature:
        raise HTTPException(404, "файл не найден")
    fname = os.path.basename(str((data or {}).get("f") or ""))
    path = os.path.join(UPLOAD_DIR, fname)
    if not fname or not os.path.exists(path):
        raise HTTPException(404, "файл не найден")
    return _media_response(path, request)


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
        try:
            r = subprocess.run(
                ["ffmpeg", "-y", "-i", src, "-vf", "scale=640:-2", "-q:v", "5", dst],
                capture_output=True, timeout=60,
            )
        except (OSError, subprocess.SubprocessError) as e:
            # Нет ffmpeg или он завис: раньше отсюда улетал 500, и на месте
            # оплаченной генерации человек видел иконку битой картинки.
            # Оригинал тяжелее миниатюры — но он есть.
            log.warning("миниатюра %s не собралась: %s", filename, str(e)[:150])
            return FileResponse(src)
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
            # Умеет ли движок геометрию кадра и какие разрешения берёт.
            # Без этих двух полей интерфейс не может сказать правду о
            # квадрате: движок с "aspect": False молча вернёт вертикаль,
            # чем бы человек ни щёлкнул в блоке параметров.
            "aspect": bool(spec.get("aspect")),
            "resolutions": list(spec.get("resolutions") or ()),
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
                    _char_photo_dict(ph) for ph in c.photos
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
        _reg_file(db, fname, project.owner_id, kind="photo", project_id=project.id)
        # kind переносим вместе с файлом: иначе клон терял свой разворот и
        # его кадры снова опирались бы на селфи.
        db.add(CharacterPhoto(character_id=clone.id, position=i, filename=fname,
                              kind=ph.kind or "photo", pose_kind=ph.pose_kind or "",
                              from_photos=int(ph.from_photos or 0)))
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
            _reg_file(db, fname, project.owner_id, kind="attr", project_id=project.id)
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
    # Без записи владельца /api/media прячет файл от всех, кроме админа —
    # человек загружал фото и видел на его месте дырку.
    _reg_file(db, fname, ch.project.owner_id, kind="photo", project_id=ch.project_id)
    max_pos = max((p.position for p in ch.photos), default=0)
    ph = CharacterPhoto(character_id=ch.id, position=max_pos + 1, filename=fname,
                        kind="photo")
    db.add(ph)
    db.commit()
    # ch.photos загружен ДО вставки — без refresh ответ отстаёт на одно фото.
    db.refresh(ch)
    return character_dict(ch)


# ─────────────── ФОТО ТОВАРА (режим мокапов) ───────────────
# Тот же механизм, что у фото персонажа, но на объекте второго уровня: в
# мокап-проекте Track — это артикул, и «узнаваемое лицо» здесь сама упаковка.
# Заводить на каждый артикул персонажа значило бы держать два параллельных
# списка одного и того же.


@app.post("/api/tracks/{track_id}/photos")
async def add_track_photo(track_id: int, photo: UploadFile,
                          user: User = Depends(current_user),
                          db: Session = Depends(db_session)):
    track = _own_track(db, user, track_id)
    ext = os.path.splitext(photo.filename or "")[1].lower() or ".jpg"
    if ext not in (".jpg", ".jpeg", ".png", ".webp"):
        raise HTTPException(400, "поддерживаются jpg/png/webp")
    fname = f"item_{uuid.uuid4().hex}{ext}"
    with open(os.path.join(UPLOAD_DIR, fname), "wb") as f:
        f.write(await photo.read())
    # Без записи владельца /api/media прячет файл от всех, кроме админа.
    _reg_file(db, fname, track.project.owner_id, kind="photo",
              project_id=track.project_id, track_id=track.id)
    max_pos = max((p.position for p in track.photos), default=0)
    db.add(TrackPhoto(track_id=track.id, position=max_pos + 1, filename=fname,
                      kind="photo"))
    db.commit()
    db.refresh(track)
    return track_dict(track)


@app.delete("/api/track-photos/{photo_id}")
def del_track_photo(photo_id: int, user: User = Depends(current_user),
                    db: Session = Depends(db_session)):
    ph = db.get(TrackPhoto, photo_id)
    if not ph:
        raise HTTPException(404, "фото не найдено")
    track = db.get(Track, ph.track_id)
    if not track or not _owned(user, track.project):
        raise HTTPException(404, "фото не найдено")
    _remove_media(ph.filename)
    db.delete(ph)
    db.commit()
    db.refresh(track)
    return track_dict(track)


# Как человек подписал ракурс своего фото. Классификатора ракурса у нас нет и
# городить его незачем: подпись над зоной загрузки даёт генератору ровно то,
# чего никакой автоотбор из свалки селфи не даст.
PHOTO_POSES = {
    "face": "close-up of the face, front view",
    "three_quarter": "three-quarter view of the head and shoulders",
    "full": "full body, head to toe",
    "back": "view from behind",
    "": "",
}


@app.patch("/api/characters/photos/{photo_id}")
async def update_character_photo(photo_id: int, request: Request,
                                 user: User = Depends(current_user),
                                 db: Session = Depends(db_session)):
    """Подпись ракурса у фото и «сделать основным» у разворота.

    primary двигает картинку в конец своей группы, потому что каноническим
    считается ПОСЛЕДНИЙ разворот (см. _character_model_file) — так «основным»
    становится именно тот, на который человек показал."""
    ph = _own_char_photo(db, user, photo_id)
    body = await request.json() if await request.body() else {}
    if "pose" in body:
        pose = str(body.get("pose") or "")
        ph.pose_kind = pose if pose in PHOTO_POSES else ""
    if body.get("primary"):
        ch = ph.character
        ph.position = max((p.position for p in ch.photos), default=0) + 1
    db.commit()
    db.refresh(ph.character)
    return character_dict(ph.character)


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


# Рамки самого ЛИСТА — отдельно от стиля рендера. Два пункта здесь появились
# по практике, и без них развороты ломаются: ПУСТЫЕ РУКИ (предмет в руке гонит
# дрейф от ракурса к ракурсу) и прямой запрет «улучшать» (модель по умолчанию
# омолаживает и стройнит — герой перестаёт быть собой).
MODEL_SHEET_VIEWS = {
    "full": (
        "Four views left to right: front, three-quarter, side profile, back. "
        "Relaxed A-pose, arms slightly away from the body, hands EMPTY and visible, "
        "full body head to toe, identical outfit, hair and accessories in all four views, "
        "same height and same proportions in all four views. "
        "Even neutral grey studio background, no props, no furniture, "
        "no text, no labels, no captions, no grid lines, no watermark."
    ),
    "closeup": (
        "Character head sheet: face close-up front view, face three-quarter view, "
        "face side profile, plus a study of both hands, arranged left to right. "
        "Same hairstyle, same facial hair, same accessories in every view. "
        "Even neutral grey studio background, sharp focus on the face, "
        "no text, no labels, no captions, no grid lines, no watermark."
    ),
}

# Фото главнее текста по ВНЕШНОСТИ, текст главнее фото по ОДЕЖДЕ и стилю.
# Без этой строки описание конкурирует с фотографией за лицо и обычно
# побеждает: «худой парень с острыми скулами» в тексте перерисовывает
# круглое лицо с фото, и моделька перестаёт быть этим человеком.
MODEL_SHEET_IDENTITY = (
    "The reference photos are ALL the SAME real person — this is the character. "
    "Reproduce the face, the hairline, the body proportions and the skin tone exactly "
    "as they are in the photos. Do NOT beautify, do NOT slim the body, do NOT change "
    "the age, the ethnicity or the face shape. "
    "Where the photos and the written description disagree: the PHOTOS win on looks "
    "(face, body, hair), the DESCRIPTION wins on clothing, accessories and mood."
)

# Сколько загруженных фото уезжает в генерацию. Больше не значит лучше: десяток
# однотипных селфи размывает идентичность вместо того, чтобы её уточнить —
# движку нужны РАЗНЫЕ ракурсы, а не количество.
MODEL_SHEET_MAX_PHOTOS = 6
# Сколько фото влезает в ОДИН коллаж для шлюза. Шлюз принимает ровно одну
# картинку, поэтому фото склеиваются в лист — но не больше четырёх: hstack
# делит ширину поровну, и на пятом лицо в исходнике уже нечитаемо.
MODEL_SHEET_COLLAGE_PHOTOS = 4


def _model_sheet_photos(ch: Character, limit: int) -> list:
    """Какие фото уедут референсами разворота.

    Только kind="photo": сгенерированные листы в референсы НЕ идут. Иначе
    следующий запуск рисует модельку с модельки, живое фото вытесняется, и
    через две-три итерации от человека не остаётся ничего.

    Больше лимита — берём первое (обычно лицо крупно) и последние: свежие
    загрузки чаще и есть те самые «другие ракурсы»."""
    live = [p for p in sorted(ch.photos, key=lambda x: (x.position, x.id))
            if (p.kind or "photo") == "photo"
            and os.path.exists(os.path.join(UPLOAD_DIR, p.filename))]
    limit = max(1, int(limit))
    if len(live) <= limit:
        return live
    if limit == 1:
        return live[:1]
    return [live[0]] + live[-(limit - 1):]


def _model_sheet_prompt(kind: str, views: str, desc: str, photos: list) -> str:
    """Четыре блока в жёстком порядке: стиль листа → идентичность → описание
    → рамки листа. Порядок не косметика: начало промпта весит больше."""
    base = MODEL_SHEET_STYLES.get(kind) or MODEL_SHEET_STYLES["3d"]
    rules = MODEL_SHEET_VIEWS.get(views) or MODEL_SHEET_VIEWS["full"]
    parts = [base]
    if photos:
        parts.append(MODEL_SHEET_IDENTITY)
        # Подписанные ракурсы: человек сам сказал, где анфас, а где рост.
        angles = [PHOTO_POSES.get(p.pose_kind or "", "") for p in photos]
        angles = [a for a in angles if a]
        if angles:
            parts.append("The reference photos show: " + "; ".join(angles) + ".")
    else:
        parts.append(
            "No reference photos are available: build the character from the "
            "written description alone and keep him consistent across all views.")
    if desc:
        parts.append(f"CHARACTER (clothing, accessories, character and mood): {desc}")
    parts.append(rules)
    return "\n\n".join(parts)


@app.post("/api/characters/{char_id}/generate-model")
async def generate_character_model(char_id: int, request: Request,
                                   user: User = Depends(current_user),
                                   db: Session = Depends(db_session)):
    """3D-РАЗВОРОТ персонажа: лист ракурсов, собранный ПО ЕГО ФОТОГРАФИЯМ.

    Это не файл для Blender и не меш — движка, отдающего геометрию, у нас нет
    (kie.ai 3D не отдаёт вообще), да и в кадры клипа меш всё равно не попадёт:
    Seedance и Kling принимают картинку. Это лист ракурсов в 3D-рендер-стиле,
    и из него дальше строятся кадры сцен.

    Что здесь починено против прежней версии:
      * лист рисовался ВЕРТИКАЛЬНЫМ (aspect по умолчанию 9:16), хотя промпт
        просил горизонтальный: модель слушается параметра, а не слова в
        тексте — отсюда «не похоже на разворот». Теперь 16:9 явно;
      * 2K на четыре фигуры давал ~290 px ширины на ракурс, в них лицо не
        выживает. Теперь 4K там, где движок его умеет;
      * фото уходят ОТДЕЛЬНЫМИ референсами (до шести), а не hstack-коллажем:
        коллаж модель периодически воспроизводила сеткой прямо в кадре;
      * результат помечается kind="model" и больше не подмешивается в
        референсы следующего запуска."""
    ch = _own_character(db, user, char_id)
    body = await request.json() if await request.body() else {}
    desc = (str(body.get("description") or "").strip() or ch.description).strip()
    kind = str(body.get("kind") or "3d")
    if kind not in MODEL_SHEET_STYLES:
        kind = "3d"
    views = str(body.get("views") or "full")
    if views not in MODEL_SHEET_VIEWS:
        views = "full"

    engine = _model_sheet_engine(user)
    spec = mediagen.IMAGE_ENGINES.get(engine, {})
    max_refs = int(spec.get("max_refs") or 1)
    # Движок с несколькими входами берёт фото по одному; шлюзу с его
    # единственным входом фото всё равно нужны — только склеенные листом,
    # поэтому и здесь их несколько, а не одно.
    photos = _model_sheet_photos(
        ch, min(MODEL_SHEET_MAX_PHOTOS, max_refs) if max_refs > 1
        else MODEL_SHEET_COLLAGE_PHOTOS)
    # Ни фото, ни описания — генерировать нечего. Одного из двух достаточно:
    # фото без описания работает, описание без фото работает тоже.
    if not desc and not photos:
        raise HTTPException(400, "нужно описание персонажа")

    # 4K просим только у движков с нативным 4K: шлюзы его не умеют, и платить
    # за разрешение, которого движок не даёт, незачем.
    resolution = "4K" if "4K" in (spec.get("resolutions") or ()) else ""
    cost = _image_cost(user, engine, resolution)
    _charge(db, user, cost, f"разворот персонажа {ch.id}",
            kind="model", engine=engine, ref_type="character", ref_id=ch.id,
            project_id=ch.project_id,
            cost_cents=_cost_cents("model", engine, resolution=resolution))

    paths = [os.path.join(UPLOAD_DIR, p.filename) for p in photos]
    owner_id = ch.project.owner_id
    reference = None
    collage = ""
    # Движок с несколькими входами получает фото по одному — ради этого Nano
    # Banana и подключалась. Шлюз принимает ровно одну картинку, ему
    # по-прежнему клеим коллаж.
    if max_refs > 1:
        pass
    elif len(paths) == 1:
        reference = paths[0]
    elif paths:
        reference = _ref_collage(db, paths[:MODEL_SHEET_COLLAGE_PHOTOS], owner_id)
        if reference:
            collage = os.path.basename(reference)
        else:
            # Склейка — удобство, а не условие генерации: упавший ffmpeg не
            # должен оставить лист вообще БЕЗ референса, то есть без лица.
            reference = paths[0]

    prompt = _model_sheet_prompt(kind, views, desc, photos)
    try:
        data, mime = await mediagen.generate_image(
            prompt, reference,
            reference_paths=paths if max_refs > 1 else None,
            engine=engine, resolution=resolution,
            # Разворот — ГОРИЗОНТАЛЬНЫЙ лист. Именно этот параметр, а не слово
            # "horizontal" в промпте, решает, что получится на выходе.
            aspect="16:9")
    except Exception as e:  # noqa: BLE001
        # Не сделали — не берём денег: возврат ровно того, что списали.
        _refund(db, user, cost, f"разворот персонажа {ch.id}")
        raise HTTPException(502, f"генератор не отдал модельку: {str(e)[:200]}")
    finally:
        if collage:
            _remove_media(collage)

    fname = _save_image(data, mime, upscale=False)
    _reg_file(db, fname, owner_id, kind="model", project_id=ch.project_id)
    max_pos = max((p.position for p in ch.photos), default=0)
    db.add(CharacterPhoto(
        character_id=ch.id, position=max_pos + 1, filename=fname,
        # kind="model" — вот эта пометка и разрывает петлю обратной связи:
        # следующий запуск возьмёт в референсы фото, а не этот лист.
        kind="model", pose_kind=(kind if views == "full" else "closeup"),
        from_photos=len(photos)))
    db.commit()
    db.refresh(ch)
    out = character_dict(ch)
    # Сколько фото реально сработало. Без этого «почему не похоже» остаётся
    # загадкой, а ответ обычно простой — фотографий не было ни одной.
    out["from_photos"] = len(photos)
    out["engine"] = engine
    out["engine_title"] = spec.get("title") or engine
    return out


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



def _run_all_frames(track_id: int, engine: str = "") -> None:
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
        log.info("пакет кадров трека %s: %s сцен движком %s",
                 track_id, len(scene_ids), engine or "(по тарифу)")
        for sid in scene_ids:
            _run_scene_frames(sid, engine=engine)
    except Exception as e:  # noqa: BLE001
        log.warning("пакет кадров трека %s упал: %s", track_id, e)


@app.post("/api/tracks/{track_id}/generate-all-frames")
def generate_all_frames(track_id: int, engine: str = "",
                        user: User = Depends(current_user),
                        db: Session = Depends(db_session)):
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
    # Движок выбираем ДО списания: цена кадров зависит именно от него, а сам
    # выбор берётся с ТРЕКА, а не молча падает в дефолт тарифа, как раньше.
    eng = _resolve_image_engine(user, track, engine)
    _scenes_charge(db, user, todo, lambda sc: _frames_cost(user, sc, eng),
                   f"кадры всех сцен трека {track.id} ({eng})",
                   kind="frames", engine=eng, track_id=track.id,
                   project_id=track.project_id)
    for s in todo:
        s.image_status = "queued"
    db.commit()
    Thread(target=_run_all_frames, args=(track_id, eng), daemon=True).start()
    return {"ok": True, "queued": len(todo), "engine": eng}


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
    eng = _resolve_video_engine(user, track, prov, engine)
    _scenes_charge(db, user, todo, lambda sc: _scene_cost(user, prov, sc, eng),
                   f"видео всех сцен трека {track.id} ({eng})",
                   kind="video", engine=eng, track_id=track.id,
                   project_id=track.project_id)
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


def _plan_price(plan_id: str, period: str, tier: str = "") -> tuple[int, int]:
    """Ценник тарифа за период: (центы, копейки). Одно место, где месяц
    превращается в год и где ступень объёма превращается в деньги, — иначе
    витрина, касса и автосписание посчитают цену каждая по-своему.

    tier читается БЕЗ фильтра видимости (_tier_spec): купленную ступень
    продлеваем по её цене, даже если её убрали с витрины флагом."""
    plan = PLANS[plan_id]
    spec = _tier_spec(plan_id, tier) if PLAN_TIERS.get(plan_id) else None
    src = spec or plan
    if period == "year":
        return int(src["usd_year_cents"]), int(src["rub_year_kopeks"])
    return int(src["usd_cents"]), int(src["rub_kopeks"])


def _pay_key(provider: str, payment_id: str) -> str:
    """Ключ платежа для таблицы обработанных: id платёжки с её именем.
    Два провайдера пишут в одну колонку, пересечься их id не должны."""
    pid = str(payment_id or "").strip()
    return f"{provider}:{pid}" if pid else ""


def _grant_cap(user: User, grant: int) -> int:
    """Сколько токенов РЕАЛЬНО ляжет на счёт при начислении: потолок — две
    МЕСЯЧНЫЕ нормы этого начисления.

    ПРИБАВЛЯЕМ к остатку, а не перезаписываем. Раньше стояло
    max(остаток, норма): экономный человек, у которого осталось 590 из 600,
    после оплаты получал 10 токенов за 990 ₽ — тариф наказывал за бережливость.
    Потолок — две нормы: копить бесконечно нельзя, иначе подписка превращается
    в склад, но месяц простоя больше не сгорает.
    Опускать баланс потолок не имеет права: сверху могли лежать докупленные
    пакеты, за них заплачено отдельно и они не сгорают."""
    cur = int(user.gen_points or 0)
    return max(cur, min(cur + int(grant), 2 * int(grant))) - cur


def _add_points(db: Session, user: User, grant: int, what: str, **meta) -> int:
    """Начислить токены с потолком и записать это в журнал. Возвращает,
    сколько реально начислено (потолок мог срезать часть).

    commit=False: начисление обязано ехать ОДНОЙ транзакцией с выдачей
    тарифа и отметкой о платеже — иначе падение между коммитами оставляет
    человека без тарифа при взятых деньгах."""
    got = _grant_cap(user, grant)
    if got:
        meta.setdefault("kind", "plan")
        _move_points(db, user, got, what, commit=False, **meta)
    return got


def _grant_plan_points(db: Session, user: User, plan_id: str, period: str,
                       tier: str = "", what: str = "", provider: str = "") -> int:
    """Начислить токены за оплаченный период.

    ГОД НАЧИСЛЯЕТСЯ ПОМЕСЯЧНО. Раньше period="year" клал норму ×12 разом при
    потолке накопления 2×12 норм: годовой ULTRA u4 — это $2149×12 выручки
    против 104000×12×POINT_USD = $15600 обязательства В ДЕНЬ ОПЛАТЫ, и при
    накоплении до потолка тариф уходил в минус. Теперь оплата даёт ПЕРВЫЙ
    транш, а остальные 11 капают раз в PLAN_DAYS (см. _points_drip_pass).
    Человек получает то же самое за год, мы — ровный расход вместо ямы.

    Оплата года ПЕРЕЗАПИСЫВАЕТ график капель, а не складывает его с прежним:
    иначе два годовых платежа подряд дали бы 22 транша вместо 11."""
    norm = _plan_points(plan_id, tier)
    if period == "year":
        user.points_drip_left = 11
        user.points_drip_size = norm
        user.points_drip_at = now() + timedelta(days=PLAN_DAYS)
    else:
        user.points_drip_left = 0
        user.points_drip_size = 0
        user.points_drip_at = None
    return _add_points(db, user, norm, what or f"тариф {plan_id}",
                       kind="plan", ref_type="payment", engine=provider)


def _points_drip_pass(db: Session) -> int:
    """Очередной месячный транш годовым подписчикам. Возвращает, скольким
    начислили.

    Отдельный проход, а не побочный эффект продления: годовую подписку никто
    не продлевает 11 месяцев, и цепляться начислению не за что."""
    rows = (db.query(User)
            .filter(User.points_drip_left > 0, User.points_drip_at.isnot(None),
                    User.points_drip_at <= now()).all())
    done = 0
    for u in rows:
        # Тариф кончился раньше срока (отмена, возврат) — капли прекращаем:
        # токены годовой подписки не должны пережить саму подписку.
        if (u.plan or "free") == "free":
            u.points_drip_left = 0
            u.points_drip_size = 0
            u.points_drip_at = None
            continue
        # Строки журнала едут ОДНОЙ транзакцией со всем проходом (commit=False
        # внутри _add_points): иначе полсотни капель = полсотни коммитов.
        _add_points(db, u, int(u.points_drip_size or 0),
                    "месячный транш годовой подписки",
                    kind="drip", ref_type="plan")
        u.points_drip_left = int(u.points_drip_left) - 1
        u.points_drip_at = (_as_utc(u.points_drip_at) or now()) + timedelta(days=PLAN_DAYS)
        if u.points_drip_left <= 0:
            u.points_drip_left = 0
            u.points_drip_at = None
        done += 1
    if rows:
        db.commit()
    return done


def _already_processed(db: Session, provider: str, payment_id: str,
                       alt_ids=()) -> bool:
    """Выдавали ли уже по этому платежу.

    alt_ids — ДРУГИЕ имена того же платежа. Одна оплата подписки Stripe
    приезжает двумя событиями, и у сессии чекаута поле invoice бывает пустым
    (Stripe кладёт id счёта не всегда) — тогда события ключевались по-разному,
    и один платёж выдавал ДВА месяца, две нормы токенов и две доли амбассадору.
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
                   tier: str = "", alt_ids=()) -> bool:
    """Выдать оплаченное: тариф с токенами или пакет токенов. False — уже выдавали.

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
            log.warning("платёж %s: неизвестный пакет токенов %r", key, pack_id)
            return False
        # Пакет считаем ПО СВОЕЙ таблице, а не по числу из metadata: metadata
        # ездит через чужой сервис, а прайс живёт здесь.
        points = int(pack["points"])
        # Пакет потолком НЕ режется (за него заплачено отдельно), но едет
        # через ту же дверь: строка журнала попадает в ту же транзакцию, что
        # и отметка о платеже.
        _move_points(db, user, points, f"пакет токенов {pack_id}", commit=False,
                     kind="topup", ref_type="payment", engine=provider)
    else:
        if plan_id not in PLANS or PLANS[plan_id]["usd_cents"] <= 0:
            log.warning("платёж %s: неизвестный тариф %r", key, plan_id)
            return False
        period = _norm_period(period)
        # Ступень: из metadata платежа, а при продлении без неё — из
        # запланированного понижения, иначе текущая. Понижение применяется
        # ровно здесь, в момент продления: раньше человек платил бы дважды
        # за один месяц.
        if PLAN_TIERS.get(plan_id):
            want = str(tier or "").strip().lower() \
                or (user.plan_tier_next or "") or (user.plan_tier or "")
            tier = _tier_spec(plan_id, want)["id"]
            user.plan_tier = tier
            user.plan_tier_next = ""
        else:
            tier = ""
            user.plan_tier = ""
            user.plan_tier_next = ""
        points = _grant_plan_points(db, user, plan_id, period, tier,
                                    what=f"тариф {plan_id}", provider=provider)
        user.plan = plan_id
        user.plan_period = period
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
        # ВСЁ, включая выдачу: месяц и токены уже начислены им.
        db.rollback()
        log.info("платёж %s уже обработан — пропускаем", key)
        return False
    # Приход в журнал токенов поехал ТОЙ ЖЕ транзакцией, что и выдача (см.
    # _move_points/_add_points с commit=False выше): кабинет обязан объяснять,
    # откуда взялся баланс, а не только куда он делся. Раньше строка писалась
    # здесь, после коммита — и при откате по IntegrityError журнал оставался
    # с приходом, которого не было.
    log.info("выдано по платежу %s: юзер %s, %s %s, +%s токенов", key, user.id, kind,
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
    """Сколько клипов по 3 минуты выходит из этих токенов на таком движке."""
    if points <= 0 or scene_cost <= 0:
        return 0
    return int(points) // (scene_cost * CLIP_SCENES)


def _volume_breakdown(plan_id: str, points: int) -> dict:
    """Расшифровка объёма в ЧЕЛОВЕЧЕСКИХ единицах — по каждому движку тарифа.

    Витрина обязана переводить токены в то, что человек понимает: клипы и кадры.
    Считаем здесь, а не на фронте, — иначе появится третья копия прайса,
    которая разъедется вслед за LD_PLANS_FALLBACK.

    ВАЖНО про «0 клипов». На дорогом движке объём часто не дотягивает до
    целого клипа (3400 токенов PRO MAX = 20 сцен на Seedance 2.5, две трети
    песни). Врать нельзя, но и писать «0 клипов» — значит убить карточку
    собственной рукой: отдаём и clips, и scenes, а витрина показывает сцены
    там, где клипов меньше одного."""
    plan = PLANS[plan_id]
    frames_pair = FRAME_COST.get(plan.get("image_engine") or "chatgpt", FRAMES_COST)
    one_image = max(2, _points_of_usd(
        mediagen.image_engine_usd(plan.get("image_engine") or "chatgpt")))
    rows = []
    for eid, scene_cost in sorted(_plan_engines(plan_id).items(),
                                  key=lambda kv: kv[1], reverse=True):
        spec = mediagen.VIDEO_ENGINES.get(eid) or {}
        rows.append({
            "engine": eid,
            "title": spec.get("title", eid),
            "scene_cost": scene_cost,
            "clips": _movies_estimate(points, scene_cost),
            "scenes": int(points) // scene_cost if scene_cost else 0,
        })
    return {
        "engines": rows,
        "frames_pair_cost": frames_pair,
        "image_cost": one_image,
        # Сколько ОТДЕЛЬНЫХ кадров можно нарисовать, если тратить только на них.
        "images": int(points) // one_image if one_image else 0,
        "scene_pairs": int(points) // frames_pair if frames_pair else 0,
        "clip_scenes": CLIP_SCENES,
    }


def _tier_card(plan_id: str, spec: dict) -> dict:
    """Ступень объёма для витрины: оба ценника, зачёркнутая цена, скидка и
    расшифровка «сколько это клипов» по каждому движку тарифа."""
    pts = int(spec["points"])
    return {
        "id": spec["id"],
        "points": pts,
        "usd": round(spec["usd_cents"] / 100, 2),
        "usd_cents": int(spec["usd_cents"]),
        "usd_year": round(spec["usd_year_cents"] / 100, 2),
        "usd_year_cents": int(spec["usd_year_cents"]),
        "usd_year_per_month": round(spec["usd_year_cents"] / 12 / 100, 2),
        "rub": int(spec["rub_kopeks"]) // 100,
        "rub_kopeks": int(spec["rub_kopeks"]),
        "rub_year": int(spec["rub_year_kopeks"]) // 100,
        "rub_year_kopeks": int(spec["rub_year_kopeks"]),
        # Зачёркнутая цена = тот же объём по цене токена базовой ступени.
        "list_usd": round(spec["list_usd_cents"] / 100, 2),
        "list_usd_cents": int(spec["list_usd_cents"]),
        "save_pct": int(spec["save_pct"]),
        # Годовая скидка ИМЕННО ЭТОЙ ступени: после пола цены токена она уже не
        # YEAR_DISCOUNT_PCT, и обещать −20 % там, где −13 %, нельзя.
        "year_discount_pct": int(spec["year_discount_pct"]),
        "usd_per_point": round(spec["usd_cents"] / 100 / pts, 5) if pts else 0.0,
        "volume": _volume_breakdown(plan_id, pts),
    }


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
        # Ступени объёма (сегодня — только ULTRA). Пустой список = тариф без
        # шкалы, витрина рисует обычную карточку. Первая ступень по цене и
        # объёму совпадает с самим тарифом, поэтому старый фронт, который про
        # ступени ничего не знает, продолжает показывать верный ценник.
        "tiers": [_tier_card(plan_id, s)
                  for s in _tiers_of(plan_id, visible_only=True)],
        "volume": _volume_breakdown(plan_id, int(p["points"])),
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
        # Правда об экономике токена: сколько человек платит за токен и сколько
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
        # Пакет — добор, а не тариф: он дороже подписочного токена и продаётся
        # только при живой платной подписке (см. TOPUP_REQUIRES_PLAN).
        "requires_plan": TOPUP_REQUIRES_PLAN,
        "movies_estimate": _movies_estimate(k["points"], SCENE_COST["seedance-2-mini"]),
        "movies_estimate_grok": _movies_estimate(k["points"], SCENE_COST["grok"]),
    }


def _providers_state() -> dict:
    return {"stripe": _stripe_enabled(), "yookassa": _yookassa_enabled()}


@app.get("/api/billing/plans")
def billing_plans(request: Request, db: Session = Depends(db_session)):
    """Всё, что нужно витрине: тарифы обоих периодов, пакеты токенов, флаги
    платёжек, цена работы в токенах и текущее состояние человека.

    Роут ПУБЛИЧНЫЙ: цены — первое, что смотрит человек с лендинга, и требовать
    ради них аккаунт значит терять его на входе. Гостю отдаём витрину без
    личного блока, авторизованному — с текущим тарифом и остатком."""
    providers = _providers_state()
    user = _resolve_user(request, db)
    return {
        "current": _plan_of(user) if user else "free",
        "current_period": (user.plan_period or "month") if user else "month",
        # Ступень объёма: какая куплена и какая запланирована на продление.
        "current_tier": _tier_of_user(user) if user else "",
        "next_tier": (user.plan_tier_next or "") if user else "",
        "plan_until": user.plan_until.isoformat() if (user and user.plan_until) else "",
        "autopay": bool(user and user.autopay and (user.pay_method_id
                                                   or user.stripe_subscription_id
                                                   or getattr(user, "stars_sub_charge_id", ""))),
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
        # Докупка токенов разрешена только при живой платной подписке.
        "topup_requires_plan": TOPUP_REQUIRES_PLAN,
        "topup_allowed": bool(user and _plan_of(user) != "free") if TOPUP_REQUIRES_PLAN else True,
        # Прайс работы в токенах — витрине, чтобы объяснять, куда они уходят.
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
            # Якорь экономики: сколько себестоимости лежит в одном токене.
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
    """Пакеты токенов отдельно от тарифов: докупка не трогает подписку.
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
    """Ссылка на оплату: подписка на тариф или разовая покупка пакета токенов.

    body: {kind:"plan"|"topup", plan?, tier?, pack?, period?:"month"|"year",
           provider?:"stripe"|"yookassa", currency?:"usd"|"rub", promo?}
    tier — ступень объёма для тарифов со шкалой (ULTRA). Неизвестная и
    скрытая флагом молча понижаются до базовой: обходить ULTRA_TOP_TIERS
    одной строкой в теле запроса нельзя.
    Провайдер по умолчанию выбирается валютой: доллары — Stripe, рубли — ЮKassa."""
    body = await request.json() if await request.body() else {}
    kind = str(body.get("kind") or "").strip().lower()
    plan_id = str(body.get("plan") or "").strip()
    pack_id = str(body.get("pack") or "").strip()
    if kind not in ("plan", "topup"):
        kind = "topup" if pack_id else "plan"
    period = _norm_period(body.get("period"))
    tier = _norm_tier(plan_id, body.get("tier")) if plan_id in PLANS else ""

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
        amount_cents, amount_kopeks = _plan_price(plan_id, period, tier)
        points = _plan_points(plan_id, tier)
        # В названии платежа — объём, а не только имя тарифа: в выписке по
        # карте и в письме кассы человек должен узнать, за какую ступень платил.
        vol = f" {points // 1000}k" if PLAN_TIERS.get(plan_id) else ""
        title = (f"{BRAND} {plan['title']}{vol} — "
                 f"{'12 months' if period == 'year' else '1 month'}")

    # Промокод партнёрки: если человек ещё ни за кем не закреплён — закрепляем
    # прямо здесь (то же первое касание, что и по ссылке ?ref=). Скидка — один
    # раз, на первую ПОДПИСКУ; продления и пакеты токенов идут по прайсу.
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
                ambassador_id=amb.id if amb else 0, tier=tier,
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
            "kind": kind, "plan": plan_id, "tier": tier, "pack": pack_id, "period": period,
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
        # Пакет токенов — разовая покупка, карту для него не сохраняем.
        "save_payment_method": kind == "plan",
        "confirmation": {"type": "redirect",
                         "return_url": f"{PUBLIC_BASE_URL}/?paid={plan_id or pack_id}"},
        "description": title,
        # promo и ambassador_id — след для разбора спорных начислений: по
        # платежу в кабинете ЮKassa видно, чей это был реферал.
        "metadata": {"user_id": str(user.id), "plan": plan_id, "kind": kind,
                     "pack": pack_id, "period": period, "tier": tier,
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
        "kind": kind, "plan": plan_id, "tier": tier, "pack": pack_id, "period": period,
        "payment_id": data.get("id", ""),
        "amount_kopeks": pay_kopeks, "discount_kopeks": discount_kopeks,
        "amount_cents": 0, "discount_cents": 0,
        "promo": meta_promo,
    }


# ─────────────────────────── вебхук ЮKassa ───────────────────────────

@app.post("/api/billing/webhook")
async def billing_webhook(request: Request, db: Session = Depends(db_session)):
    """Уведомление ЮKassa: по успешной оплате выдаём тариф или пакет токенов.

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
    tier = str(meta.get("tier") or "")
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
                             pay_method_id=saved_method, tier=tier)
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
                tier=str(meta.get("tier") or ""),
                amount_cents=amount_cents, currency="USD",
                stripe_customer=customer, stripe_subscription=sub_id,
                alt_ids=(inv_id, str(obj.get("id") or "")))
        else:
            # Разовая покупка токенов: ключ — id платёжного намерения.
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
            tier=str(meta.get("tier") or ""),
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
    # Ступень: запланированное понижение вступает в силу ровно на продлении,
    # поэтому и списываем уже по новой цене.
    tier = ""
    if PLAN_TIERS.get(user.plan or ""):
        tier = _tier_spec(user.plan, (user.plan_tier_next or "")
                          or (user.plan_tier or ""))["id"]
    _, amount_kopeks = _plan_price(user.plan, period, tier)
    if amount_kopeks <= 0:
        return "fail"
    # Idempotence-Key ДЕТЕРМИНИРОВАННЫЙ: если ЮKassa списала, а ответ до нас не
    # дошёл (таймаут сети), повтор через час вернёт ТОТ ЖЕ платёж, а не спишет
    # с карты второй раз. Раньше тут был свежий uuid на каждый заход.
    seed = (f"sub:{user.id}:{user.plan}:{tier}:{period}:"
            f"{_as_utc(user.plan_until) or now():%Y-%m-%d}")
    idem = uuid.uuid5(uuid.NAMESPACE_URL, seed).hex
    import httpx as _httpx
    payload = {
        "amount": {"value": f"{amount_kopeks // 100}.{amount_kopeks % 100:02d}",
                   "currency": "RUB"},
        "capture": True,
        "payment_method_id": user.pay_method_id,
        "description": f"{BRAND} {plan['title']} — renewal",
        "metadata": {"user_id": str(user.id), "plan": user.plan, "kind": "plan",
                     "period": period, "tier": tier},
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
                          plan_id=user.plan, period=period, tier=tier,
                          amount_kopeks=amount_kopeks, currency="RUB"):
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
    db = SessionLocal()
    try:
        # Сначала капли годовых подписок: они не привязаны к продлению —
        # годовую никто не продлевает 11 месяцев, и цепляться начислению не за что.
        try:
            _points_drip_pass(db)
        except Exception as e:  # noqa: BLE001 — капля не должна ронять проход продлений
            db.rollback()
            log.warning("помесячная выдача токенов упала: %s", str(e)[:200])
        due = db.query(User).filter(User.plan != "free", User.plan_until.isnot(None),
                                    User.plan_until <= now()).all()
        for u in due:
            if u.stripe_subscription_id:
                continue  # продлевает Stripe своим счётом
            if getattr(u, "stars_sub_charge_id", ""):
                # Подписку за звёзды продлевает сам Telegram и присылает новый
                # successful_payment. Карты у такого человека нет и быть не
                # может, поэтому без этой строки автосписание «проваливалось»,
                # и через SUB_GRACE_DAYS живой плательщик слетал на free.
                continue
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
            # Ступень и график капель принадлежат подписке — вместе с ней и уходят.
            u.plan_tier = ""
            u.plan_tier_next = ""
            u.points_drip_left = 0
            u.points_drip_size = 0
            u.points_drip_at = None
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


@app.post("/api/billing/tier")
async def billing_tier(request: Request, user: User = Depends(current_user),
                       db: Session = Depends(db_session)):
    """Сменить ступень объёма внутри купленного тарифа.

    ПОВЫШЕНИЕ — не здесь: за него надо доплатить, и делает его обычный чекаут
    (/api/billing/create с новым tier). Возвращаем действие "checkout", чтобы
    фронт увёл человека в кассу, а не делал вид, что объём вырос сам.

    ПОНИЖЕНИЕ — пишем в plan_tier_next и применяем на продлении. Понижать
    сразу нельзя: за текущий период уже заплачено по старой цене."""
    body = await request.json() if await request.body() else {}
    plan_id = _plan_of(user)
    if not PLAN_TIERS.get(plan_id):
        raise ApiError(400, "no_tiers", "This plan has no volume steps.")
    want = _norm_tier(plan_id, body.get("tier"))
    order = [t["id"] for t in _tiers_of(plan_id)]
    cur = _tier_of_user(user) or order[0]
    if want == cur:
        user.plan_tier_next = ""
        db.commit()
        return {"ok": True, "action": "kept", "tier": cur, "next_tier": ""}
    if order.index(want) > order.index(cur):
        return {"ok": True, "action": "checkout", "tier": cur, "next_tier": want}
    user.plan_tier_next = want
    db.commit()
    log.info("юзер %s запланировал понижение ступени %s → %s", user.id, cur, want)
    return {"ok": True, "action": "scheduled", "tier": cur, "next_tier": want,
            "until": user.plan_until.isoformat() if user.plan_until else ""}


@app.post("/api/billing/cancel")
async def billing_cancel(user: User = Depends(current_user), db: Session = Depends(db_session)):
    """Отключить автопродление — тариф доработает до конца оплаченного срока.

    У звёзд отмена делается на стороне Telegram (editUserStarSubscription) и
    пропорционального возврата не даёт: доступ доживает до конца оплаченного
    периода. Это надо говорить прямо, а не прятать за словом «отменено»."""
    user.autopay = False
    db.commit()
    stars_ok, stars_err = True, ""
    if getattr(user, "stars_sub_charge_id", ""):
        import stars as stars_mod  # noqa: PLC0415
        stars_ok, stars_err = await stars_mod.cancel_subscription(db, user)
    stripe_ok = True
    if user.stripe_subscription_id and _stripe_enabled():
        try:
            await stripe_pay.cancel_subscription(user.stripe_subscription_id)
        except Exception as e:  # noqa: BLE001
            stripe_ok = False
            log.warning("stripe: не отменилась подписка %s: %s",
                        user.stripe_subscription_id, str(e)[:200])
    return {"ok": True, "stripe_cancelled": stripe_ok,
            "stars_cancelled": stars_ok, "stars_error": stars_err,
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


def _stars_subscription(user: User) -> dict:
    """Состояние звёздной подписки для кабинета: откуда она, когда следующее
    списание и как её отменить. Пусто — значит человек платил не звёздами."""
    if not getattr(user, "stars_sub_charge_id", ""):
        return {}
    try:
        import stars as stars_mod  # noqa: PLC0415
        return stars_mod.subscription_state(user)
    except Exception as e:  # noqa: BLE001
        log.warning("кабинет: не собрал состояние звёздной подписки: %s", str(e)[:150])
        return {"provider": "stars", "active": True, "state": "active"}


@app.get("/api/account")
def account(user: User = Depends(current_user), db: Session = Depends(db_session)):
    """Личный кабинет: тариф, срок, токены, привязки входа, проекты."""
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
        # Автопродление живёт в ТРЁХ местах: карта ЮKassa у нас, подписка у
        # Stripe и подписка Telegram Stars, которую продлевает сам Telegram.
        # Без звёзд в этой строке кабинет писал бы «автопродление выключено»
        # человеку, с которого Telegram списывает каждые 30 дней.
        "autopay": bool(user.autopay and (user.pay_method_id
                                          or user.stripe_subscription_id
                                          or getattr(user, "stars_sub_charge_id", ""))),
        "pay_provider": ("stars" if getattr(user, "stars_sub_charge_id", "")
                         else ("stripe" if user.stripe_subscription_id
                               else ("yookassa" if user.pay_method_id else ""))),
        # Следующее списание = конец оплаченного периода: у всех трёх контуров
        # это одна и та же дата, и второй правды о ней заводить не надо.
        "next_charge": user.plan_until.isoformat() if (user.plan_until and user.autopay
                                                       and plan != "free") else "",
        "stars_subscription": _stars_subscription(user),
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


# ─────────────────────── расход токенов: данные для дашборда ───────────────────────
# Витрина кабинета отвечает на вопрос «сколько я ещё сделаю», а не «какой у
# меня тариф». Поэтому здесь считается ТЕМП, а не только остаток: прогноз
# «при нынешнем расходе хватит до <даты>» — единственная цифра, ради которой
# человек вообще открывает кабинет.

USAGE_KINDS = ("frames", "video", "chat", "audio", "story", "sheet",
               "model", "assemble", "other")


@app.get("/api/account/usage")
def account_usage(days: int = 30, user: User = Depends(current_user),
                  db: Session = Depends(db_session)):
    days = max(7, min(90, int(days or 30)))
    since = now() - timedelta(days=days)
    rows = (db.query(PointEvent)
            .filter(PointEvent.user_id == user.id, PointEvent.created_at >= since)
            .order_by(PointEvent.created_at.desc()).all())

    daily: dict[str, dict] = {}
    for i in range(days):
        d = (now() - timedelta(days=days - 1 - i)).date().isoformat()
        daily[d] = {"date": d, "spent": 0, "granted": 0,
                    **{k: 0 for k in USAGE_KINDS}}
    spent = granted = 0
    # Разбивка по ДВИЖКАМ — второй вопрос кабинета после «сколько всего».
    # Kling 3.0 Pro и Seedance 2 Mini отличаются в цене на порядок, и без
    # этой строки «куда потратилось» отвечено только наполовину.
    by_engine: dict[str, dict] = {}
    by_kind: dict[str, dict] = {}
    cost_cents = 0
    for e in rows:
        d = (_as_utc(e.created_at) or now()).date().isoformat()
        cell = daily.get(d)
        if cell is None:
            continue
        if e.delta < 0:
            cell["spent"] += -e.delta
            spent += -e.delta
            kind = e.kind if e.kind in USAGE_KINDS else "other"
            cell[kind] += -e.delta
            k = by_kind.setdefault(kind, {"kind": kind, "spent": 0, "ops": 0})
            k["spent"] += -e.delta
            k["ops"] += 1
            eng = (e.engine or "").strip() or "—"
            row = by_engine.setdefault(eng, {"engine": eng, "spent": 0, "ops": 0})
            row["spent"] += -e.delta
            row["ops"] += 1
            cost_cents += int(e.cost_cents or 0)
        else:
            cell["granted"] += e.delta
            granted += e.delta

    # Темп считаем по неделе, а не по месяцу: месяц назад человек мог быть на
    # другом тарифе, и «в среднем» врало бы в обе стороны сразу.
    week_since = now() - timedelta(days=7)
    burn_week = sum(-e.delta for e in rows
                    if e.delta < 0 and (_as_utc(e.created_at) or now()) >= week_since)
    burn_day = burn_week / 7.0
    points = int(user.gen_points or 0)
    forecast = ""
    if burn_day > 0 and points > 0:
        forecast = (now() + timedelta(days=min(365, points / burn_day))).date().isoformat()

    # До какой даты история ВОССТАНОВЛЕНА, а не записана. Говорим это вслух:
    # у восстановленных строк нет возвратов и нет удалённых сцен, и график до
    # этой даты — форма, а не точный счёт.
    approx = (db.query(func.max(PointEvent.created_at))
              .filter(PointEvent.user_id == user.id,
                      PointEvent.ref_type == "backfill").scalar())

    out = {
        "days": days,
        "daily": list(daily.values()),
        "kinds": list(USAGE_KINDS),
        "spent": spent, "granted": granted,
        "by_kind": sorted(by_kind.values(), key=lambda r: -r["spent"]),
        "by_engine": sorted(by_engine.values(), key=lambda r: -r["spent"]),
        "burn_day": round(burn_day, 1),
        "burn_week": burn_week,
        "points": points,
        "forecast_date": forecast,
        "approx_before": _as_utc(approx).isoformat() if approx else "",
        "limits": _plan_limits(db, user),
        "recent": [_event_dict(e) for e in rows[:12]],
    }
    if user.is_admin:
        # Себестоимость — ТОЛЬКО админу: из неё восстанавливается наша
        # наценка, а прайс построен именно на ней (POINT_USD).
        out["cost_cents"] = cost_cents
    return out


def _plan_limits(db: Session, user: User) -> dict:
    """Рамка тарифа: норма, потолок накопления, что уже израсходовано в
    текущем периоде и когда следующее начисление.

    Про ПОТОЛОК человек сегодня не знает вообще, и это прямой источник обиды:
    накопил, оплатил, часть сгорела. Норма ×2 — ровно то, что делает
    _grant_cap, и кабинет обязан это показывать заранее, а не постфактум."""
    plan_id = _plan_of(user)
    tier = _tier_of_user(user)
    norm = _plan_points(plan_id, tier)
    # Начало периода = ПОСЛЕДНЕЕ начисление, а не «месяц назад»: у годовой
    # подписки токены капают раз в PLAN_DAYS, и календарный месяц врал бы.
    last_grant = (db.query(func.max(PointEvent.created_at))
                  .filter(PointEvent.user_id == user.id,
                          PointEvent.kind.in_(("plan", "drip", "topup"))).scalar())
    start = _as_utc(last_grant) or _as_utc(user.created_at) or (now() - timedelta(days=30))
    used = int(db.query(func.coalesce(func.sum(-PointEvent.delta), 0))
               .filter(PointEvent.user_id == user.id, PointEvent.delta < 0,
                       PointEvent.created_at >= start).scalar() or 0)
    engines = []
    plan_costs = _plan_engines(plan_id)
    for eid in _plan_engine_ids(plan_id):
        spec = mediagen.VIDEO_ENGINES.get(eid) or {}
        engines.append({
            "id": eid, "title": spec.get("title") or eid,
            "scene_cost": plan_costs.get(eid, 0),
            # «Открыт тарифом» и «жив по ключам» — разные вещи, и кабинет
            # обязан различать их: ключа kie нет — движок в прайсе есть,
            # а нажать на него нельзя.
            "live": mediagen.video_engine_live(eid),
        })
    return {
        "plan": plan_id, "plan_title": PLANS[plan_id]["title"], "tier": tier,
        "period": user.plan_period or "month",
        "norm": norm, "cap": norm * 2, "used": used,
        "period_start": start.isoformat(),
        "plan_until": _as_utc(user.plan_until).isoformat() if user.plan_until else "",
        "tier_next": user.plan_tier_next or "",
        # Токены годовой подписки, которые ещё НЕ выданы: «на счету 660, ещё
        # 7260 придут по месяцам» — иначе годовой тариф выглядит обманом.
        "drip_left": int(user.points_drip_left or 0),
        "drip_size": int(user.points_drip_size or 0),
        "drip_at": _as_utc(user.points_drip_at).isoformat() if user.points_drip_at else "",
        "image_engine": _plan_image_engine(user),
        "engines": engines,
    }


def _event_dict(e: PointEvent, admin: bool = False) -> dict:
    row = {"id": e.id, "at": (_as_utc(e.created_at) or now()).isoformat(),
           "delta": e.delta, "kind": e.kind, "what": e.what,
           "engine": e.engine, "ref_type": e.ref_type, "ref_id": e.ref_id,
           "project_id": e.project_id, "track_id": e.track_id,
           "balance_after": e.balance_after}
    if admin:
        # task_id и себестоимость наружу не идут: первое — внутренняя кухня
        # движков, второе раскрывает наценку.
        row["task_id"] = e.task_id or ""
        row["cost_cents"] = int(e.cost_cents or 0)
    return row


# ─────────────────── лента операций: фильтры и курсор ───────────────────
# Дашборд показывает 12 последних строк — этого хватает «что я сделал только
# что» и не хватает «за что списали 154 токена в прошлый вторник». Здесь та же
# история целиком, с фильтрами и курсором. Курсор по id, а не OFFSET:
# OFFSET на растущей таблице пропускает строки при добавлении новых.

@app.get("/api/account/events")
def account_events(kind: str = "", engine: str = "", project_id: int = 0,
                   only: str = "", cursor: int = 0, limit: int = 50,
                   user_id: int = 0, user: User = Depends(current_user),
                   db: Session = Depends(db_session)):
    limit = max(1, min(200, int(limit or 50)))
    owner_id = user.id
    if user_id and user.is_admin:
        owner_id = int(user_id)      # админ смотрит чужую ленту из CRM
    q = db.query(PointEvent).filter(PointEvent.user_id == owner_id)
    if kind:
        q = q.filter(PointEvent.kind.in_([k for k in kind.split(",") if k]))
    if engine:
        q = q.filter(PointEvent.engine == engine)
    if project_id:
        q = q.filter(PointEvent.project_id == int(project_id))
    # «Только возвраты» отдельным фильтром: сегодня они тонут в общем приходе
    # вместе с оплатами, а это ровно та строка, ради которой открывают журнал.
    if only == "refund":
        q = q.filter(PointEvent.kind == "refund")
    elif only == "spent":
        q = q.filter(PointEvent.delta < 0)
    elif only == "granted":
        q = q.filter(PointEvent.delta > 0)
    if cursor:
        q = q.filter(PointEvent.id < int(cursor))
    rows = q.order_by(PointEvent.id.desc()).limit(limit + 1).all()
    more = len(rows) > limit
    rows = rows[:limit]
    return {
        "items": [_event_dict(e, user.is_admin) for e in rows],
        "next_cursor": rows[-1].id if (more and rows) else 0,
        "engines": sorted({r[0] for r in db.query(PointEvent.engine)
                           .filter(PointEvent.user_id == owner_id).distinct().all()
                           if r[0]}),
    }


# ═══════════════════════ ФАЙЛОВЫЙ АРХИВ ═══════════════════════
# «Папка со всеми файлами, рассортированная по датам, видам и проектам».
# Индекс — таблица file_owners: она и так регистрирует каждый созданный файл
# ради приватности (/api/media отдаёт чужое только админу), поэтому второго
# места правды не заводим. Метаданные пишет _reg_file, пробелы добирает
# _files_sweep.
#
# КЛЮЧЕВОЕ ОГРАНИЧЕНИЕ: клип весит до 1.5 ГБ, кадр — 15 МБ в 4К. Поэтому
# страница архива НИКОГДА не грузит оригиналы: сетка живёт на /api/thumb
# (ffmpeg, 640px, кэш), видео показывается ПОСТЕРОМ, а не <video>.

FILE_KINDS = ("frame", "frame_last", "midframe", "ref", "video", "clip",
              "storyboard", "cover", "model", "photo", "attr", "audio",
              "chat", "other")
# Что считаем видео — по нему решается, брать ли постер и показывать ▶.
VIDEO_KINDS = ("video", "clip")
IMAGE_KINDS = ("frame", "frame_last", "midframe", "ref", "storyboard",
               "cover", "model", "photo", "attr")


def _kind_by_name(fname: str) -> str:
    """Вид осиротевшего файла — по имени и расширению. Имена у нас
    префиксные (char_, attr_, refjoin_, chat_), этого хватает."""
    low = fname.lower()
    ext = os.path.splitext(low)[1]
    if low.startswith("chat_"):
        return "chat"
    if low.startswith("char_"):
        return "photo"
    if low.startswith("attr_"):
        return "attr"
    if ext in (".mp4", ".mov", ".webm"):
        return "video"
    if ext in (".mp3", ".wav", ".m4a", ".aac", ".ogg", ".flac"):
        return "audio"
    if ext in (".png", ".jpg", ".jpeg", ".webp"):
        return "other"
    return "other"


def _file_dict(f: FileOwner) -> dict:
    is_video = f.kind in VIDEO_KINDS or f.filename.lower().endswith((".mp4", ".mov", ".webm"))
    is_audio = f.kind == "audio" and not is_video
    return {
        "filename": f.filename,
        "kind": f.kind or "other",
        "at": (_as_utc(f.created_at) or now()).isoformat(),
        "size_bytes": int(f.size_bytes or 0),
        "project_id": int(f.project_id or 0),
        "track_id": int(f.track_id or 0),
        "scene_id": int(f.scene_id or 0),
        "url": f"/api/media/{f.filename}",
        # Аудио миниатюры не имеет — ffmpeg соберёт из него чёрный кадр,
        # и сетка заполнится квадратами пустоты.
        "thumb_url": "" if is_audio else f"/api/thumb/{f.filename}",
        "is_video": is_video,
        "is_audio": is_audio,
    }


def _file_in_use(db: Session, fname: str) -> str:
    """Ссылается ли на файл живая сущность. Пустая строка — не ссылается.

    Удалять файл, на который смотрит сцена, нельзя: сцена без image_filename
    ломает половину кнопок карточки, и человек получает пустое место вместо
    оплаченной генерации. Поэтому вместо удаления говорим, где он занят."""
    sc = (db.query(Scene)
          .filter((Scene.image_filename == fname) | (Scene.image_last_filename == fname)
                  | (Scene.video_filename == fname) | (Scene.audio_filename == fname))
          .first())
    if sc:
        return f"scene:{sc.id}"
    tr = (db.query(Track)
          .filter((Track.audio_filename == fname) | (Track.cover_filename == fname)
                  | (Track.storyboard_filename == fname) | (Track.clip_filename == fname))
          .first())
    if tr:
        return f"track:{tr.id}"
    pr = db.query(Project).filter(Project.cover_filename == fname).first()
    if pr:
        return f"project:{pr.id}"
    if db.query(CharacterPhoto).filter(CharacterPhoto.filename == fname).first():
        return "character"
    if db.query(AttributePhoto).filter(AttributePhoto.filename == fname).first():
        return "attribute"
    if db.query(SceneRef).filter(SceneRef.filename == fname).first():
        return "ref"
    return ""


@app.get("/api/files")
def list_files(kind: str = "", project_id: int = 0, track_id: int = 0,
               days: int = 0, sort: str = "date", cursor: str = "",
               limit: int = 60, user_id: int = 0,
               user: User = Depends(current_user), db: Session = Depends(db_session)):
    """Метаданные файлов человека. Оригиналы отсюда не едут никогда —
    только имена, размеры и ссылки."""
    limit = max(1, min(200, int(limit or 60)))
    owner_id = int(user_id) if (user_id and user.is_admin) else user.id
    q = (db.query(FileOwner)
         .filter(FileOwner.user_id == owner_id, FileOwner.deleted_at.is_(None)))
    if kind:
        q = q.filter(FileOwner.kind.in_([k for k in kind.split(",") if k]))
    if project_id:
        q = q.filter(FileOwner.project_id == int(project_id))
    if track_id:
        q = q.filter(FileOwner.track_id == int(track_id))
    if days:
        q = q.filter(FileOwner.created_at >= now() - timedelta(days=int(days)))

    # Итоги считаем ДО пагинации: человек должен видеть, что клипы съели
    # 40 ГБ, а кадры 300 МБ, — иначе непонятно, что вообще чистить.
    totals_q = q.with_entities(FileOwner.kind,
                               func.count(FileOwner.filename),
                               func.coalesce(func.sum(FileOwner.size_bytes), 0))
    by_kind = [{"kind": k or "other", "count": int(c), "bytes": int(b)}
               for k, c, b in totals_q.group_by(FileOwner.kind).all()]

    if sort == "size":
        if cursor:
            c_size, _, c_name = cursor.partition("|")
            try:
                c_size_i = int(c_size)
            except ValueError:
                c_size_i = 0
            q = q.filter((FileOwner.size_bytes < c_size_i)
                         | ((FileOwner.size_bytes == c_size_i)
                            & (FileOwner.filename < c_name)))
        q = q.order_by(FileOwner.size_bytes.desc(), FileOwner.filename.desc())
    else:
        # Курсор по паре (дата, имя), а не OFFSET: пока человек листает,
        # генерации продолжают писать новые файлы, и OFFSET начал бы
        # повторять и пропускать строки.
        if cursor:
            c_at, _, c_name = cursor.partition("|")
            dt = _as_utc(_parse_iso(c_at))
            if dt:
                q = q.filter((FileOwner.created_at < dt)
                             | ((FileOwner.created_at == dt)
                                & (FileOwner.filename < c_name)))
        q = q.order_by(FileOwner.created_at.desc(), FileOwner.filename.desc())

    rows = q.limit(limit + 1).all()
    more = len(rows) > limit
    rows = rows[:limit]
    nxt = ""
    if more and rows:
        last = rows[-1]
        nxt = (f"{int(last.size_bytes or 0)}|{last.filename}" if sort == "size"
               else f"{(_as_utc(last.created_at) or now()).isoformat()}|{last.filename}")
    projects = [{"id": p.id, "name": p.name, "kind": p.kind}
                for p in db.query(Project).filter(Project.owner_id == owner_id)
                .order_by(Project.id.desc()).limit(100).all()]
    return {
        "items": [_file_dict(f) for f in rows],
        "next_cursor": nxt,
        "totals": {"count": sum(r["count"] for r in by_kind),
                   "bytes": sum(r["bytes"] for r in by_kind),
                   "by_kind": sorted(by_kind, key=lambda r: -r["bytes"])},
        "projects": projects,
        "kinds": list(FILE_KINDS),
    }


@app.get("/api/files/link/{filename}")
def file_link(filename: str, user: User = Depends(current_user),
              db: Session = Depends(db_session)):
    """Подписанная ссылка на скачивание.

    Не /api/media: там нужна кука, и «скачать по ссылке с телефона» ломается.
    Токен живёт PUBFILE_TTL_S и подделке не поддаётся."""
    fname = os.path.basename(filename)
    if not os.path.exists(os.path.join(UPLOAD_DIR, fname)):
        raise HTTPException(404, "файл не найден")
    _check_file_owner(db, user, fname)
    return {"url": f"/pub/{pub_file_token(fname)}", "ttl_s": PUBFILE_TTL_S}


@app.delete("/api/files/{filename}")
def delete_file(filename: str, user: User = Depends(current_user),
                db: Session = Depends(db_session)):
    """Мягкое удаление: файл уходит из архива сразу, с диска — отложенным
    проходом. Занятый живой сценой файл не удаляем и честно говорим, где он."""
    fname = os.path.basename(filename)
    _check_file_owner(db, user, fname)
    row = db.get(FileOwner, fname)
    if not row:
        raise HTTPException(404, "файл не найден")
    used = _file_in_use(db, fname)
    if used:
        raise ApiError(409, "file_in_use",
                       "файл используется — сначала отвяжи его", used=used)
    row.deleted_at = now()
    db.commit()
    return {"ok": True, "filename": fname}


def _parse_iso(value: str):
    """Разбор ISO-строки из курсора. Кривой курсор — не ошибка 500,
    а просто первая страница."""
    try:
        from datetime import datetime as _dt  # noqa: PLC0415
        return _dt.fromisoformat(str(value or "").replace("Z", "+00:00"))
    except (ValueError, TypeError):
        return None


def _files_sweep() -> None:
    """Достроить индекс архива: вид, проект, объект, кадр, размер и дата.

    Работает как _backfill_point_events — по тому, что осталось в базе. Файл
    регистрировался в file_owners с самого начала, но только именем и
    владельцем, поэтому «разложить по датам, видам и проектам» было нечем.

    Проход самоисчерпывающийся: трогает только строки без вида."""
    from sqlalchemy import text as _sqltext
    from db import engine as _engine
    # (SQL, вид) — одним UPDATE на связь, а не построчным обходом: строк
    # десятки тысяч, и ORM-цикл занял бы старт сервиса.
    plan = [
        ("""UPDATE file_owners SET kind='frame', scene_id=(
                SELECT s.id FROM scenes s WHERE s.image_filename = file_owners.filename)
            WHERE kind='' AND filename IN (SELECT image_filename FROM scenes WHERE image_filename != '')"""),
        ("""UPDATE file_owners SET kind='frame_last', scene_id=(
                SELECT s.id FROM scenes s WHERE s.image_last_filename = file_owners.filename)
            WHERE kind='' AND filename IN (SELECT image_last_filename FROM scenes WHERE image_last_filename != '')"""),
        ("""UPDATE file_owners SET kind='video', scene_id=(
                SELECT s.id FROM scenes s WHERE s.video_filename = file_owners.filename)
            WHERE kind='' AND filename IN (SELECT video_filename FROM scenes WHERE video_filename != '')"""),
        ("""UPDATE file_owners SET kind='audio', scene_id=(
                SELECT s.id FROM scenes s WHERE s.audio_filename = file_owners.filename)
            WHERE kind='' AND filename IN (SELECT audio_filename FROM scenes WHERE audio_filename != '')"""),
        ("""UPDATE file_owners SET kind='ref', scene_id=(
                SELECT r.scene_id FROM scene_refs r WHERE r.filename = file_owners.filename)
            WHERE kind='' AND filename IN (SELECT filename FROM scene_refs)"""),
        ("""UPDATE file_owners SET kind='clip', track_id=(
                SELECT t.id FROM tracks t WHERE t.clip_filename = file_owners.filename)
            WHERE kind='' AND filename IN (SELECT clip_filename FROM tracks WHERE clip_filename != '')"""),
        ("""UPDATE file_owners SET kind='storyboard', track_id=(
                SELECT t.id FROM tracks t WHERE t.storyboard_filename = file_owners.filename)
            WHERE kind='' AND filename IN (SELECT storyboard_filename FROM tracks WHERE storyboard_filename != '')"""),
        ("""UPDATE file_owners SET kind='audio', track_id=(
                SELECT t.id FROM tracks t WHERE t.audio_filename = file_owners.filename)
            WHERE kind='' AND filename IN (SELECT audio_filename FROM tracks WHERE audio_filename != '')"""),
        ("""UPDATE file_owners SET kind='cover', track_id=(
                SELECT t.id FROM tracks t WHERE t.cover_filename = file_owners.filename)
            WHERE kind='' AND filename IN (SELECT cover_filename FROM tracks WHERE cover_filename != '')"""),
        ("""UPDATE file_owners SET kind='cover', project_id=(
                SELECT p.id FROM projects p WHERE p.cover_filename = file_owners.filename)
            WHERE kind='' AND filename IN (SELECT cover_filename FROM projects WHERE cover_filename != '')"""),
        ("""UPDATE file_owners SET kind=(
                SELECT CASE WHEN cp.kind='model' THEN 'model' ELSE 'photo' END
                FROM character_photos cp WHERE cp.filename = file_owners.filename)
            WHERE kind='' AND filename IN (SELECT filename FROM character_photos)"""),
        ("""UPDATE file_owners SET kind='attr'
            WHERE kind='' AND filename IN (SELECT filename FROM attribute_photos)"""),
        ("""UPDATE file_owners SET kind='chat'
            WHERE kind='' AND filename IN (SELECT media_filename FROM chat_messages WHERE media_filename != '')"""),
        ("""UPDATE file_owners SET kind='chat'
            WHERE kind='' AND filename IN (SELECT filename FROM chat_files)"""),
        # Проект и объект по сцене — вторым проходом, когда scene_id уже стоит.
        ("""UPDATE file_owners SET track_id = (
                SELECT s.track_id FROM scenes s WHERE s.id = file_owners.scene_id)
            WHERE scene_id != 0 AND track_id = 0"""),
        ("""UPDATE file_owners SET project_id = (
                SELECT t.project_id FROM tracks t WHERE t.id = file_owners.track_id)
            WHERE track_id != 0 AND project_id = 0"""),
    ]
    try:
        with _engine.begin() as conn:
            todo = conn.execute(_sqltext(
                "SELECT COUNT(*) FROM file_owners WHERE kind = ''")).scalar() or 0
            if not todo:
                return
            for sql in plan:
                conn.execute(_sqltext(sql))
            # Всё, что не нашлось ни в одной связи, — по имени файла.
            rest = conn.execute(_sqltext(
                "SELECT filename FROM file_owners WHERE kind = ''")).fetchall()
            for (fname,) in rest:
                conn.execute(_sqltext(
                    "UPDATE file_owners SET kind=:k WHERE filename=:f"),
                    {"k": _kind_by_name(fname), "f": fname})
        # Размер и дата — с диска: в базе их взять неоткуда, а без них архив
        # не умеет ни «по дате», ни «сколько занято». Лимит большой намеренно:
        # это ОДИН стартовый проход, и оставить половину файлов без даты
        # значит уронить курсорную пагинацию на них (сравнение с NULL всегда
        # ложно, и такие строки просто выпали бы из ленты).
        _files_stat_pass(limit=200000)
        log.info("архив файлов: разложено %s записей по видам", todo)
    except Exception as e:  # noqa: BLE001
        log.warning("архив файлов: разбор не прошёл: %s", str(e)[:200])


def _files_stat_pass(limit: int = 20000) -> int:
    """Проставить размер и дату файлам, у которых их нет (легаси-строки).
    Дата берётся из mtime — другой у нас про эти файлы просто нет."""
    from sqlalchemy import text as _sqltext
    from db import engine as _engine
    done = 0
    try:
        with _engine.begin() as conn:
            rows = conn.execute(_sqltext(
                "SELECT filename FROM file_owners "
                "WHERE size_bytes = 0 OR created_at IS NULL LIMIT :n"),
                {"n": limit}).fetchall()
            for (fname,) in rows:
                path = os.path.join(UPLOAD_DIR, fname)
                try:
                    st = os.stat(path)
                except OSError:
                    # Файла нет на диске — строка мусорная, помечаем удалённой,
                    # чтобы архив не показывал битые плитки.
                    conn.execute(_sqltext(
                        "UPDATE file_owners SET deleted_at = :t WHERE filename = :f"),
                        {"t": now(), "f": fname})
                    continue
                conn.execute(_sqltext(
                    "UPDATE file_owners SET size_bytes = :s, "
                    "created_at = COALESCE(created_at, :t) WHERE filename = :f"),
                    {"s": int(st.st_size), "t": datetime.fromtimestamp(
                        st.st_mtime, tz=timezone.utc), "f": fname})
                done += 1
    except Exception as e:  # noqa: BLE001
        log.warning("архив файлов: не собрал размеры: %s", str(e)[:200])
    return done


def _files_purge_pass() -> int:
    """Физически стереть то, что помечено удалённым больше суток назад.
    Сутки — окно на «ой, не то нажал»."""
    db = SessionLocal()
    gone = 0
    try:
        rows = (db.query(FileOwner)
                .filter(FileOwner.deleted_at.isnot(None),
                        FileOwner.deleted_at <= now() - timedelta(days=1))
                .limit(500).all())
        for row in rows:
            _remove_media(row.filename)
            thumb = os.path.join(THUMB_DIR, row.filename + ".jpg")
            if os.path.exists(thumb):
                try:
                    os.remove(thumb)
                except OSError:
                    pass
            db.delete(row)
            gone += 1
        if gone:
            db.commit()
            log.info("архив файлов: стёрто с диска %s удалённых файлов", gone)
    except Exception as e:  # noqa: BLE001
        db.rollback()
        log.warning("архив файлов: уборка не прошла: %s", str(e)[:200])
    finally:
        db.close()
    return gone


def _backfill_point_events() -> None:
    """Восстановить историю токенов за прошлое — один раз, из того, что осталось.

    Журнала раньше не было: расход жил в log.info контейнера. Собрать из
    scenes.charged_points, chat_messages.points и processed_payments можно
    ФОРМУ графика, но не точный счёт — возвратов и удалённых сцен там нет.
    Поэтому строки помечаются ref_type='backfill', а кабинет честно пишет,
    что история до этой даты приблизительная.

    Проход самоисчерпывающийся: работает только на пустой таблице."""
    from sqlalchemy import text as _sqltext
    from db import engine as _engine
    try:
        with _engine.begin() as conn:
            have = conn.execute(_sqltext("SELECT COUNT(*) FROM point_events")).scalar()
            if have:
                return
            done = conn.execute(_sqltext("""
                INSERT INTO point_events
                    (user_id, created_at, delta, kind, what, ref_type, ref_id, engine, balance_after)
                SELECT p.owner_id, s.updated_at, -s.charged_points,
                       CASE WHEN s.video_filename != '' THEN 'video' ELSE 'frames' END,
                       'сцена ' || s.position, 'backfill', s.id,
                       COALESCE(s.video_engine, ''), 0
                FROM scenes s
                JOIN tracks t ON t.id = s.track_id
                JOIN projects p ON p.id = t.project_id
                WHERE s.charged_points > 0 AND p.owner_id IS NOT NULL
            """)).rowcount
            done += conn.execute(_sqltext("""
                INSERT INTO point_events
                    (user_id, created_at, delta, kind, what, ref_type, ref_id, engine, balance_after)
                SELECT c.owner_id, m.created_at, -m.points, 'chat', 'запрос в чате',
                       'backfill', m.id, COALESCE(m.engine, ''), 0
                FROM chat_messages m
                JOIN chats c ON c.id = m.chat_id
                WHERE m.points > 0
            """)).rowcount
            done += conn.execute(_sqltext("""
                INSERT INTO point_events
                    (user_id, created_at, delta, kind, what, ref_type, ref_id, engine, balance_after)
                SELECT pp.user_id, pp.created_at, pp.points,
                       CASE WHEN pp.kind = 'topup' THEN 'topup' ELSE 'plan' END,
                       'оплата ' || COALESCE(pp.plan, ''), 'backfill', pp.id,
                       COALESCE(pp.provider, ''), 0
                FROM processed_payments pp
                WHERE pp.points > 0 AND pp.user_id IS NOT NULL
            """)).rowcount
        if done:
            log.info("журнал токенов: восстановлено %s строк истории", done)
    except Exception as e:  # noqa: BLE001
        # Не критично: журнал начнёт заполняться с этого момента.
        log.warning("восстановление журнала токенов не прошло: %s", str(e)[:200])


_backfill_point_events()
# Индекс архива: вид/проект/объект/размер для всего, что уже лежит на диске.
_files_sweep()


def _files_worker() -> None:
    """Суточный проход архива: дочитать размеры новых файлов и физически
    стереть то, что удалено больше суток назад. Тот же паттерн демон-треда,
    что у подписок и ретенции чата — своего планировщика в проекте нет."""
    while True:
        try:
            time.sleep(6 * 3600)
            _files_stat_pass(limit=5000)
            _files_purge_pass()
        except Exception as e:  # noqa: BLE001
            log.warning("архив файлов: суточный проход упал: %s", str(e)[:200])


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


# ─────────────────────────────── чат ───────────────────────────────
# Чат живёт отдельным модулем (backend/chat.py) и НЕ импортирует main: всё,
# что ему нужно от студии — разрешение сессии, деньги, файлы, тарифные
# фильтры, — приезжает сюда через configure(). Так у денег и у движков
# остаётся одно место правды, а круговой ссылки между модулями нет.
import chat as chat_module  # noqa: E402

chat_module.configure(
    resolve_user=_resolve_user,
    owned=_owned,
    plans=PLANS,
    plan_of=_plan_of,
    plan_engine_ids=_plan_engine_ids,
    plan_image_engine=_plan_image_engine,
    image_cost=_image_cost,
    points_of_usd=_points_of_usd,
    gateway_points=GATEWAY_POINTS,
    charge=_charge,
    refund=_refund,
    cost_cents=_cost_cents,
    guard=_guard_user,
    reg_file=_reg_file,
    save_image=_save_image,
    remove_media=_remove_media,
    upload_dir=UPLOAD_DIR,
)
# ВАЖНО: роутер включается ДО mount("/") — иначе StaticFiles перехватит
# /api/chat* и вернёт 404 вместо ответа.
app.include_router(chat_module.router)


# ─────────────────────────────── статика (SPA) ───────────────────────────────

# Фоновая проверка подписок: раз в час смотрим, кому пора продлить.
from threading import Thread as _Thread  # noqa: E402
_Thread(target=_subscription_worker, daemon=True).start()
# Архив файлов: размеры новых файлов и уборка удалённых.
_Thread(target=_files_worker, daemon=True).start()
# Ретенция медиа чатов: суточный проход, тот же паттерн демон-треда.
chat_module.start_worker()


# ─────────────────────────── телеграм-контур ───────────────────────────
# Три модуля, ни один из которых не заводит своей авторизации и своей кассы:
#   bot_api — служебные роуты бота (сессия по tg_id, выдача звёздных оплат);
#   stars   — прайс в звёздах, счета, подписки, возвраты и сверка платежей;
#   tg_app  — мини-апп: вход по initData и мосты к аккаунту сайта.
# ВАЖНО: строго ДО app.mount("/", StaticFiles…) — статика на «/» перехватывает
# всё, что зарегистрировано после неё, и POST /internal/… начинает отвечать
# 405 Method Not Allowed, молча и совершенно непонятно.
import bot_api  # noqa: E402
import crm  # noqa: E402
import stars  # noqa: E402
import tg_app  # noqa: E402

bot_api.mount(app)
# CRM и рассылки: клиенты, админские действия, сегменты, кампании, отписка.
crm.mount(app)
stars.mount(app)
tg_app.mount(app)


FRONTEND_DIR = os.environ.get("FRONTEND_DIR", "/app/static")
if os.path.isdir(FRONTEND_DIR):
    app.mount("/", StaticFiles(directory=FRONTEND_DIR, html=True), name="static")
