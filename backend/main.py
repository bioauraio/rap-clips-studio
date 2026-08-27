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
import random
import secrets
import threading
import shutil
import subprocess
import time
import uuid
from datetime import datetime, timedelta, timezone

from fastapi import Depends, FastAPI, Response, Form, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from itsdangerous import BadSignature, SignatureExpired, URLSafeTimedSerializer
from sqlalchemy import func, or_
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, object_session

import claude
import formats
import gate
import learn
import mediagen
import mockup_catalog
import voice
import music
import audio_analysis
import prompts_catalog
import prompts_library
import stripe_pay
import textgen
from db import (
    AppSetting, ChangeLog, EarnClick, EarnSale, TeamProject, TeamTask, StudioOrder, TrendJob, TrendPreset, AttributePhoto, Character, CharacterAttribute, CharacterPhoto, Doc, FileOwner,
    FrameCache, Payout, PointEvent, ProcessedPayment, Project, RefEvent, Scene,
    SceneRef, SceneVersion, SessionLocal, StyleAsset, StyleOverride, Track,
    AuthCode, TrackPhoto, User, init_db, now,
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


def _err_text(e: BaseException, limit: int = 400) -> str:
    """Человеческий текст ошибки, который НИКОГДА не пуст.

    Прямой str(e) на это не годится: у httpx.ConnectTimeout и половины
    сетевых исключений он равен пустой строке. А именно эти ошибки у нас
    самые частые — шлюз на 172.18.0.1:8765 и движки картинок отваливаются
    по таймауту. Пустой текст уезжал в scenes_error / restyle_note и далее
    в карточку, и человек видел «ошибка» без единого слова о причине —
    ровно тот молчаливый сбой, против которого написан весь остальной код.

    Поэтому: сначала сообщение, а если его нет — имя класса исключения.
    «ConnectTimeout» — плохая надпись, но она отвечает на вопрос «что
    случилось», а пустая строка не отвечает ни на что."""
    msg = str(e).strip()
    if not msg:
        msg = type(e).__name__
    return msg[:limit]


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


def _rescue_paid_jobs(db: Session) -> int:
    """Дозабрать результаты задач, переживших перезапуск сервиса.

    Ищем сцены, застрявшие в queued/running, у которых в журнале списания
    записан id внешней задачи. Спрашиваем движок об итоге: готово — скачиваем
    и ставим на место, упало — возвращаем токены, ещё считается — оставляем
    как есть, следующий заход подберёт.
    """
    import asyncio

    saved = 0
    try:
        stuck = (db.query(Scene)
                 .filter(or_(Scene.video_status.in_(("queued", "running")),
                             Scene.image_status.in_(("queued", "running"))))
                 .all())
    except Exception as e:  # noqa: BLE001 — старая база без колонок
        log.warning("не вышло собрать зависшие генерации: %s", str(e)[:150])
        return 0
    for scene in stuck:
        for kind, status_col, file_col, ext in (
            ("video", "video_status", "video_filename", ".mp4"),
            ("frames", "image_status", "image_filename", ".png"),
        ):
            if getattr(scene, status_col, "") not in ("queued", "running"):
                continue
            ev = (db.query(PointEvent)
                  .filter(PointEvent.ref_type == "scene", PointEvent.ref_id == scene.id,
                          PointEvent.kind == kind, PointEvent.task_id != "")
                  .order_by(PointEvent.id.desc()).first())
            if not ev or not ev.task_id:
                continue
            try:
                res = asyncio.run(mediagen.kie_task_result(ev.task_id))
            except Exception as e:  # noqa: BLE001 — движок недоступен, попробуем позже
                log.warning("задача %s не опрошена: %s", ev.task_id, str(e)[:120])
                continue
            if res["state"] == "waiting":
                continue           # ещё считается — не трогаем, подберём позже
            if res["state"] == "fail":
                owner = (db.get(User, scene.track.project.owner_id)
                         if scene.track and scene.track.project else None)
                if owner and ev.delta < 0:
                    _refund(db, owner, abs(int(ev.delta)),
                            f"движок не справился с задачей {ev.task_id}")
                setattr(scene, status_col, "error")
                db.commit()
                continue
            if not res["urls"]:
                continue
            try:
                fname = asyncio.run(mediagen.fetch_to_upload(res["urls"][0], ext))
            except Exception as e:  # noqa: BLE001 — ссылка протухла
                log.warning("результат задачи %s не скачался: %s", ev.task_id, str(e)[:120])
                continue
            setattr(scene, file_col, fname)
            setattr(scene, status_col, "done")
            track = scene.track
            if track:
                _reg_file(db, fname, track.project.owner_id, kind=kind,
                          project_id=track.project_id, track_id=track.id, scene_id=scene.id)
            if kind == "video":
                scene.video_seconds = mediagen.video_duration(fname)
                if not scene.approved_manual:
                    scene.approved = True
            db.commit()
            saved += 1
            log.info("спасена оплаченная генерация: сцена %s, задача %s",
                     scene.id, ev.task_id)
    return saved


def _fallback_note(res: dict, wanted: str) -> str:
    """Почему кадр рисовал шлюз, а не заказанный движок — человеку, коротко."""
    if res.get("engine") == wanted or not res.get("fallback_reason"):
        return ""
    reason = res["fallback_reason"]
    low = reason.lower()
    if "credit" in low or "insufficient" in low:
        return "закончились кредиты движков — рисовал шлюз"
    return "движок отказал — рисовал шлюз: " + reason[:120]


def _log_change(db: Session, user: User, project_id: int, ref_type: str,
                ref_id: int, field: str, old, new, actor: str = "user") -> None:
    """Смена настройки → строка журнала. Одинаковые значения не пишем."""
    old_s, new_s = str(old or ""), str(new or "")
    if old_s == new_s:
        return
    try:
        db.add(ChangeLog(project_id=project_id, user_id=user.id, actor=actor,
                         ref_type=ref_type, ref_id=ref_id, field=field,
                         old_value=old_s[:500], new_value=new_s[:500]))
    except Exception as e:  # noqa: BLE001 — журнал не роняет работу
        log.warning("журнал изменений не записался: %s", str(e)[:120])


def _reset_orphan_jobs() -> None:
    """Сбросить статусы задач, чьи потоки не пережили перезапуск.

    Генерации живут в daemon-тредах: при рестарте контейнера (а он бывает при
    каждом деплое) поток исчезает, а строка в базе остаётся в 'running' — и
    кнопка навсегда превращается в «рисую лист…». Человек ждёт того, чего уже
    никто не делает. На старте честно помечаем такие задачи прерванными,
    чтобы их можно было запустить заново."""
    db = SessionLocal()
    try:
        # СНАЧАЛА СПАСАЕМ ОПЛАЧЕННОЕ. Тред умер вместе с процессом, но задача
        # на стороне движка считалась дальше и уже списала наши деньги. Прежде
        # чем помечать что-либо ошибкой, спрашиваем движок: если результат
        # готов — забираем его. Иначе каждый деплой сжигал токены за работу,
        # которая была сделана.
        rescued = _rescue_paid_jobs(db)
        note = "прервано перезапуском сервиса — запусти заново"
        n = 0
        for model, pairs in (
            (Track, (("scenes_status", "scenes_error"),
                     ("storyboard_status", "storyboard_error"),
                     ("clip_status", "clip_error"),
                     ("restyle_status", "restyle_note"),
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
        if rescued:
            log.info("после перезапуска дозабрано готовых генераций: %s", rescued)
        if n:
            db.commit()
            log.info("сброшено зависших задач после рестарта: %s", n)
    except Exception as e:  # noqa: BLE001 — старт сервиса важнее уборки
        log.warning("не смог сбросить зависшие задачи: %s", e)
    finally:
        db.close()


_reset_orphan_jobs()



def _phys_key(path: str) -> str:
    """«устройство:инод» — физический ключ файла.

    Копия проекта делает ЖЁСТКУЮ ССЫЛКУ: у одного куска диска появляется
    несколько имён и несколько строк в file_owners. Без этого ключа сумма
    size_bytes считала бы такой файл столько раз, сколько у него имён, —
    и квота с архивом врали бы тем сильнее, чем активнее человек копирует."""
    try:
        st = os.stat(path)
    except OSError:
        return ""
    return f"{st.st_dev}:{st.st_ino}"


def _reg_file(db: Session, filename: str, owner_id: int | None, *,
              kind: str = "", project_id: int = 0, track_id: int = 0,
              scene_id: int = 0, src_filename: str = "") -> None:
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
    if src_filename:
        row.src_filename = os.path.basename(src_filename)[:200]
    row.deleted_at = None
    path = os.path.join(UPLOAD_DIR, fname)
    if not row.size_bytes:
        try:
            row.size_bytes = os.path.getsize(path)
        except OSError:
            row.size_bytes = 0
    if not row.phys_key:
        row.phys_key = _phys_key(path)


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
        "extra_engines": ["seedance-2-mini", "seedance-2-5-480", "kling-3.0",
                          "minimax-h3", "veo-3.1-fast", "veo-3.1-lite"],
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
                          "kling-3.0", "minimax-h3",
                          "veo-3.1-fast", "veo-3.1", "veo-3.1-lite"],
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
# Курс бонусной валюты: один коин покрывает столько токенов стоимости.
BONUS_RATE = max(0.1, min(1.0, float(os.environ.get("BONUS_RATE", "0.68"))))
# Меньше килобайта — это не дорожка, а обрыв связи или пустой файл.
MIN_AUDIO_BYTES = 1024


# КОЭФФИЦИЕНТ НАЦЕНКИ. Наш токен — своя валюта, он НЕ равен токену движка:
# POINT_USD говорит, сколько себестоимости мы кладём в один токен, и чем
# меньше это число, тем больше токенов стоит одна и та же генерация. Наценка
# k делает ровно это: рабочая цена токена = POINT_USD / k. При k = 1 мы
# продаём по себестоимости, при k = 2 берём вдвое.
#
# Живёт в базе, а не в окружении: владелец правит её ползунком в админке, и
# ждать переката контейнера ради процента наценки незачем. Значение кэшируем
# на несколько секунд — иначе каждый расчёт цены лез бы в базу.
MARKUP_KEY = "markup"
MARKUP_DEFAULT = float(os.environ.get("MARKUP", "1.0"))
MARKUP_MIN, MARKUP_MAX = 0.5, 5.0
_markup_cache = {"at": 0.0, "value": MARKUP_DEFAULT}


def _markup() -> float:
    """Нынешний коэффициент наценки."""
    if time.time() - _markup_cache["at"] < 5:
        return _markup_cache["value"]
    value = MARKUP_DEFAULT
    db = SessionLocal()
    try:
        row = db.get(AppSetting, MARKUP_KEY)
        if row:
            value = float(row.value or MARKUP_DEFAULT)
    except Exception as e:  # noqa: BLE001 — база недоступна, цена не должна падать
        log.warning("наценка не прочиталась, беру прежнюю: %s", str(e)[:120])
        value = _markup_cache["value"]
    finally:
        db.close()
    value = max(MARKUP_MIN, min(MARKUP_MAX, value))
    _markup_cache.update(at=time.time(), value=value)
    return value


def _point_usd() -> float:
    """Сколько себестоимости в одном нашем токене с учётом наценки."""
    return POINT_USD / max(0.01, _markup())


def _points_of_usd(usd: float) -> int:
    """Доллары себестоимости → токены. Округление ВВЕРХ: недобор токена — это
    наши деньги, а не пользовательские."""
    if usd <= 0:
        return GATEWAY_POINTS
    return max(GATEWAY_POINTS, math.ceil(usd / _point_usd()))


# Цена ПАРЫ кадров сцены (первый + последний) по движку картинок.
class _PriceTable(dict):
    """Ценник, который пересчитывается при смене наценки.

    Раньше это были обычные словари, посчитанные один раз на импорте: сдвинув
    ползунок, владелец менял бы цену только после переката контейнера, а до
    него сервис продолжал бы продавать по старой. Наследуемся от dict, чтобы
    весь прежний код (`FRAME_COST[eid]`, `.get`, итерация) работал как был.
    """

    def __init__(self, price):
        super().__init__()
        self._price = price
        self._markup = None
        self._sync()

    def _sync(self):
        k = _markup()
        if k == self._markup:
            return
        self._markup = k
        super().clear()
        super().update(self._price())

    def __getitem__(self, key):
        self._sync()
        return super().__getitem__(key)

    def get(self, key, default=None):
        self._sync()
        return super().get(key, default)

    def items(self):
        self._sync()
        return super().items()

    def values(self):
        self._sync()
        return super().values()

    def __iter__(self):
        self._sync()
        return super().__iter__()


FRAME_COST = _PriceTable(lambda: {
    eid: _points_of_usd(2 * mediagen.image_engine_usd(eid))
    for eid in mediagen.IMAGE_ENGINES
})
# Цена ВИДЕО сцены (6 секунд) по движку видео.
VIDEO_COST = _PriceTable(lambda: {
    eid: _points_of_usd(mediagen.video_engine_usd(eid, SCENE_SEC))
    for eid in mediagen.VIDEO_ENGINES
})
# Легаси-карта «движок → цена сцены целиком» для витрины и старых вызовов:
# кадры считаются по шлюзу (базовый случай), видео — по своему движку.
SCENE_COST = _PriceTable(lambda: {
    eid: FRAME_COST["chatgpt"] + VIDEO_COST[eid] for eid in VIDEO_COST
})


class _FramesCost(int):
    """«Аванс за кадры на шлюзе» — легаси-имя, которое читают как число.
    Значение зависит от наценки, поэтому подставляем свежее при каждом
    приведении к int/строке, а не замораживаем на импорте."""

    def __int__(self):
        return FRAME_COST["chatgpt"]

    def __index__(self):
        return FRAME_COST["chatgpt"]

    def __repr__(self):
        return str(FRAME_COST["chatgpt"])


FRAMES_COST = FRAME_COST["chatgpt"]  # аванс за кадры на шлюзе (легаси-имя)
# Текстовые шаги идут через нашу подписку Claude и стоят нам ноль — берём за них
# ноль и мы: иначе бесплатный тариф не доживал до первого клипа, а именно первый
# собранный клип и продаёт сервис.
COST_STORY = 0
COST_SCENES = 0
# Цена текстового шага по выбранной модели. Считается из той же
# себестоимости в долларах и той же функцией, что кадры и видео, — второй
# кассы в сервисе нет. ИСКЛЮЧЕНИЕ ровно одно: шлюз стоит НОЛЬ, а не
# GATEWAY_POINTS. _points_of_usd(0) вернул бы 2, и это сломало бы прямо
# записанное выше обещание «бесплатный тариф доживает до первого клипа».
TEXT_COST = {
    eid: (0 if textgen.is_gateway(eid) else _points_of_usd(textgen.text_engine_usd(eid)))
    for eid in textgen.TEXT_ENGINES
}
# ═════════════════════ ИСТОРИЯ ВАРИАНТОВ: СКОЛЬКО ХРАНИМ ═════════════════════
#
# Владелец просил две вещи разом: «варианты сохранялись в истории» и «памяти
# серверов хватало». Это одно требование, а не два: у машины, где живёт
# сервис, 48 ГБ на всё И ЧУЖИЕ ПРОЕКТЫ на том же разделе. История без
# ретенции — это способ уронить соседей, а не функция.
#
# Поэтому глубина истории — ТАРИФНАЯ ВЕЛИЧИНА, как и всё остальное здесь.
# Второй шкалы «сколько дней» не заводим: срок идёт той же строкой.
SCENE_VERSIONS_KEEP = max(1, int(os.environ.get("SCENE_VERSIONS_KEEP", "2")))
#: план → (сколько вариантов на сцену, сколько дней их держим)
VERSIONS_BY_PLAN = {
    "free": (SCENE_VERSIONS_KEEP, 7),
    "pro": (3, 30),
    "pro_max": (5, 60),
    "studio": (8, 180),
}
#: план → квота диска в гигабайтах. Считается по ФИЗИЧЕСКИМ файлам (жёсткие
#: ссылки схлопываются по phys_key), иначе копия проекта съедала бы квоту,
#: не занимая ни байта.
PLAN_STORAGE_GB = {"free": 3, "pro": 15, "pro_max": 50, "studio": 150}
# Собранный клип живёт после последнего просмотра столько дней. Он —
# ЕДИНСТВЕННЫЙ крупный артефакт, который восстанавливается за НОЛЬ токенов
# (ffmpeg склеит его заново из тех же видео за минуту-другую), поэтому под
# нехватку места он уезжает первым.
CLIP_KEEP_DAYS = int(os.environ.get("CLIP_KEEP_DAYS", "14"))
# Порог, ниже которого генерации отвечают 507 вместо того, чтобы дописать
# 700-мегабайтный клип в два свободных гигабайта.
DISK_MIN_FREE_PCT = float(os.environ.get("DISK_MIN_FREE_PCT", "10"))
DISK_WARN_FREE_PCT = float(os.environ.get("DISK_WARN_FREE_PCT", "20"))


def _versions_keep(user: "User | None") -> int:
    return VERSIONS_BY_PLAN.get(_plan_of(user) if user else "free",
                                (SCENE_VERSIONS_KEEP, 7))[0]


def _versions_days(user: "User | None") -> int:
    return VERSIONS_BY_PLAN.get(_plan_of(user) if user else "free",
                                (SCENE_VERSIONS_KEEP, 7))[1]


def _storage_quota_bytes(user: "User | None") -> int:
    """Квота диска человека в байтах. У ULTRA она растёт со ступенью объёма:
    альбом на десять треков и должен занимать в десять раз больше."""
    if not user:
        return PLAN_STORAGE_GB["free"] * 1024 ** 3
    plan = _plan_of(user)
    gb = PLAN_STORAGE_GB.get(plan, PLAN_STORAGE_GB["free"])
    if plan == "studio":
        base = _plan_points("studio") or 1
        gb = int(gb * max(1.0, (_plan_points(plan, _tier_of_user(user)) or base) / base))
    return int(gb) * 1024 ** 3


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


# ───────────────── параллельные генерации: лимит по тарифу ─────────────────
# Сам ограничитель живёт в backend/gate.py; здесь только перевод «человек →
# число». Держать сетку в двух местах нельзя: витрина обязана показывать РОВНО
# то число, которое сервер применяет. У конкурента строка «до 6 видео»
# захардкожена во фронте отдельно от серверного лимита — и два соседних тарифа
# показывают одинаковую цифру там, где продаётся разница.

def _parallel_limit(user: "User | None") -> int:
    if not user:
        return gate.PLAN_PARALLEL["free"]
    return gate.limit_for(_plan_of(user), _tier_of_user(user),
                          is_admin=bool(user.is_admin))


def _queue_state(user: "User | None") -> dict:
    """Что у человека в работе + во что превращается его лимит.

    ОДНО МЕСТО на два роута (/api/gen/queue и /api/billing/plans): собирать
    этот словарь в двух местах значило бы, что витрина и студия однажды
    начнут называть разные числа."""
    limit = _parallel_limit(user)
    st = gate.status(user.id if user else 0, limit)
    st["clip_minutes"] = _clip_minutes(limit)
    return st


def _spawn_gen(user: "User", fn, *args, kind: str = "gen") -> None:
    """Запустить генерацию под лимитом тарифа.

    Полная замена `Thread(target=fn, args=args, daemon=True).start()`. Роут
    ведёт себя как раньше (отвечает сразу), а поток внутри себя ждёт слота.
    Токены к этому моменту уже списаны, и это правильно: ОЧЕРЕДЬ НЕ ТЕРЯЕТ
    ЗАДАЧУ, поэтому инвариант «списано → сделано либо возвращено» цел.

    Вложенности здесь нет и быть не должно: пакетные задачи (_run_all_frames,
    _run_all_videos, _run_supergen) зовут посценовые ФУНКЦИИ напрямую, а не
    через _spawn_gen. Иначе пачка занимала бы слот и ждала собственных детей,
    которым слот уже не достался бы, — на FREE это вечный дедлок."""
    gate.spawn(user.id, _parallel_limit(user), fn, args, kind=kind)


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
                 engine: str = "", which: str = "both") -> int:
    """Цена кадров сцены. По умолчанию — пара; which="first"/"last" — ОДИН
    кадр за половину цены пары (вверх): дефолт генерации теперь один кадр,
    и брать за него как за два было бы обманом. Если кадры уже нарисованы —
    по ТОМУ движку, которым их реально нарисовали: иначе смена тарифа между
    кадрами и видео ломала бы добор до цены сцены."""
    eng = engine or (scene.image_engine if scene else "") or _plan_image_engine(user)
    pair = FRAME_COST.get(eng, FRAMES_COST)
    return pair if which == "both" else -(-int(pair) // 2)


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


def _resolve_text_engine(user: "User | None", project: "Project | None" = None,
                         track: "Track | None" = None, want: str = "") -> str:
    """Текстовая модель сценарного шага. Та же цепочка, что у картинок:
    явный выбор запроса → объект → проект → шлюз.

    Выбор живёт на ПРОЕКТЕ: сюжет, библия, сценарий серии и раскадровка —
    один конвейер, и разные модели на соседних шагах дают разъезд тона."""
    return textgen.resolve_text_engine(
        wanted=want,
        track=(track.text_engine if track else ""),
        project=(project.text_engine if project else ""),
        plan=_plan_of(user) if user else "free",
    )


def _text_engine_for(db: Session, project: "Project | None",
                     track: "Track | None" = None, want: str = "") -> str:
    """Движок текстового шага изнутри воркера: владелец берётся у проекта.

    Воркеры принимают id, а не объекты, — протаскивать движок через
    сигнатуры восьми функций незачем: разрешение детерминировано и даёт
    там же тот же ответ, что и в роуте, который списал токены."""
    owner = db.get(User, project.owner_id) if (project and project.owner_id) else None
    return _resolve_text_engine(owner, project, track, want)


def _text_refund(db: Session, project: "Project | None", engine: str,
                 what: str, **meta) -> None:
    """Вернуть токены за НЕсостоявшийся платный текстовый шаг.

    Фолбэка на шлюз здесь нет намеренно: человек выбрал Claude, Claude не
    ответил — он должен увидеть ошибку и свои токены обратно, а не молча
    получить текст от другой модели."""
    points = TEXT_COST.get(engine, 0)
    if points <= 0 or not project or not project.owner_id:
        return
    owner = db.get(User, project.owner_id)
    if owner:
        _refund(db, owner, points, what, project_id=project.id, **meta)


def _text_charge(db: Session, user: "User", engine: str, what: str, **meta) -> int:
    """Списание за текстовый шаг. Шлюз стоит ноль и не пишет строку вовсе —
    журнал не должен зарастать нулями."""
    points = TEXT_COST.get(engine, 0)
    if points <= 0:
        return 0
    meta.setdefault("kind", "story")
    return _charge(db, user, points, what, engine=engine,
                   cost_cents=_cost_cents("text", engine), **meta)


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
        elif kind in ("text", "story"):
            # Без этой ветки платный Claude уезжал бы в журнал с нулевой
            # себестоимостью, а /api/admin/ledger/audit сверяет именно её.
            usd = textgen.text_engine_usd(engine)
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
    # КОИНЫ — СВОЯ ВАЛЮТА БОНУСОВ (решение владельца 25.08). bonus_points
    # хранит коины, и один коин покрывает BONUS_RATE (0.68) токена стоимости.
    # Реклама честно говорит «500 бонусов», а себестоимость этих бонусов для
    # нас — 340 токенов: партнёрка перестаёт грозить убытком при вирусном
    # росте. Коины сгорают первыми; начисление коинов (kind=bonus) НЕ трогает
    # рублёвый кошелёк gen_points. Уже выданные ранние бонусы остались в
    # gen_points по курсу 1:1 — обещанного назад не забираем.
    def _apply(target):
        """Одна логика движения для основной ветки и фолбэка журнала: раньше
        фолбэк делал голый += delta и списывал мимо коинов — второй раз."""
        if delta < 0:
            need = -delta
            coins = int(target.bonus_points or 0)
            cover = min(need, int(coins * BONUS_RATE))
            if cover > 0:
                # Округление ВВЕРХ: недожечь коин — подарить стоимость дважды.
                burn = min(coins, max(1, -(-cover * 100 // int(BONUS_RATE * 100))))
                target.bonus_points = coins - burn
            target.gen_points = int(target.gen_points or 0) - (need - cover)  # ledger-ok
        elif meta.get("kind") == "bonus":
            target.bonus_points = int(target.bonus_points or 0) + delta
        else:
            target.gen_points = int(target.gen_points or 0) + delta  # ledger-ok

    _apply(user)
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
            _apply(fresh)   # rollback откатил основную ветку — повторяем той же логикой
            db.commit()
        return 0


# Регистрация задачи В МОМЕНТ ЗАВЕДЕНИЯ, а не по возврату результата.
# Тред генерации кладёт сюда id, как только внешний движок его выдал; если
# сервис в этот момент перезапустится, id останется в базе и ролик можно будет
# забрать. Контекст (какая сцена) держим в том же threading.local, что и
# mediagen: у каждой генерации свой тред.
_task_ctx = threading.local()


def _set_task_ctx(ref_type: str, ref_id: int, kind: str) -> None:
    _task_ctx.ref = (ref_type, int(ref_id or 0), kind)


def _task_hook(task_id: str) -> None:
    ref = getattr(_task_ctx, "ref", None)
    if not ref or not task_id:
        return
    ref_type, ref_id, kind = ref
    db = SessionLocal()
    try:
        _attach_task(db, ref_type, ref_id, task_id, kind)
    except Exception as e:  # noqa: BLE001 — журнал не роняет генерацию
        log.warning("не смог записать задачу %s: %s", task_id, e)
    finally:
        db.close()


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


# Хук вешаем ЗДЕСЬ, а не на старте файла: _task_hook определён строкой выше,
# а раньше вызов стоял до определения и ронял сервис на импорте.
mediagen.set_task_hook(_task_hook)


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
    available = int(user.gen_points or 0) + int(int(user.bonus_points or 0) * BONUS_RATE)
    if available < points:
        raise NotEnoughPoints(points, available, _plan_of(user), what)
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
    # Код ищем у ЛЮБОГО пользователя, а не только у амбассадора: звать друзей
    # за токены может каждый. Денежная доля по-прежнему достаётся только
    # амбассадорам — это разные механики на одной ссылке.
    return db.query(User).filter(User.ref_code == code).first()


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
    _bonus_for_signup(db, user)
    log.info("партнёрка: юзер %s закреплён за %s (код %s)",
             user.id, amb.id, amb.ref_code)
    return amb


# БОНУСЫ ЗА ДРУЗЕЙ. Деньгами партнёрка платит амбассадорам (Payout), а это
# другая, массовая механика: любой пользователь зовёт друга и получает токены.
# Токенами платить выгоднее и нам, и ему — они возвращаются в сервис, а не
# уходят на карту.
REF_SIGNUP_BONUS = int(os.environ.get("REF_SIGNUP_BONUS", "500"))
# Доля от КАЖДОЙ оплаты друга, начисляемая пригласившему токенами.
REF_CASHBACK_PCT = max(0, min(100, int(os.environ.get("REF_CASHBACK_PCT", "10"))))


def _grant_bonus(db: Session, user: "User | None", points: int, what: str,
                 **meta) -> int:
    """Начислить бонусные токены. Единственная дверь для кэшбэка."""
    if not user or points <= 0:
        return 0
    meta.setdefault("kind", "bonus")
    ev = _move_points(db, user, int(points), what, **meta)
    log.info("бонус: user %s +%s токенов за %s (бонусных стало %s)",
             user.id, points, what, user.bonus_points)
    return ev


def _bonus_for_signup(db: Session, invited: "User") -> None:
    """Друг зарегистрировался по ссылке — пригласившему бонус."""
    if not invited or not invited.referred_by or REF_SIGNUP_BONUS <= 0:
        return
    host = db.get(User, invited.referred_by)
    if not host:
        return
    _grant_bonus(db, host, REF_SIGNUP_BONUS,
                 f"бонус за приглашённого друга #{invited.id}",
                 ref_type="ref_signup", ref_id=invited.id)


def _bonus_for_payment(db: Session, payer: "User", amount_kopeks: int,
                       points_bought: int = 0) -> None:
    """Друг оплатил — пригласившему кэшбэк токенами.

    Считаем от КУПЛЕННЫХ токенов, а не от рублей: цена рубля в токенах
    зависит от тарифа и наценки, и процент от суммы давал бы разный бонус за
    одинаковую покупку. Если тариф без явного числа токенов, отступаем к
    сумме через нынешнюю цену токена.
    """
    if not payer or not payer.referred_by or REF_CASHBACK_PCT <= 0:
        return
    host = db.get(User, payer.referred_by)
    if not host:
        return
    base = int(points_bought or 0)
    if base <= 0 and amount_kopeks > 0:
        base = _points_of_usd(amount_kopeks / 100 / max(1.0, USD_RUB))
    bonus = int(base * REF_CASHBACK_PCT / 100)
    if bonus <= 0:
        return
    _grant_bonus(db, host, bonus,
                 f"кэшбэк {REF_CASHBACK_PCT}% с оплаты друга #{payer.id}",
                 ref_type="ref_payment", ref_id=payer.id)


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
                pct: "int | None" = None, points_bought: int = 0) -> None:
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
    # Кэшбэк токенами получает КТО УГОДНО, кто привёл друга. Ниже по функции
    # идёт денежная доля — она только для амбассадоров.
    if not db.query(RefEvent).filter(RefEvent.payment_id == str(payment_id or "")).first():
        _bonus_for_payment(db, buyer, amount_kopeks, points_bought)
    if not amb.is_ambassador:
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


# ─────────────────────────── тренды ───────────────────────────
# Короткий контур «фото → трендовый ролик»: вся режиссура зашита в шаблон,
# человек только загружает фотографию. Ни проекта, ни трека — самый быстрый
# путь к результату, каким пользуются витрины конкурентов.

def _trend_dict(t: TrendPreset, user: "User | None" = None) -> dict:
    v_eng = t.video_engine or "seedance-2-mini"
    i_eng = t.image_engine or "nano-banana"
    cost = (_points_of_usd(mediagen.image_engine_usd(i_eng))
            + _points_of_usd(mediagen.video_engine_usd(v_eng, t.duration_sec or 6)))
    return {
        "id": t.id, "title": t.title, "duration_sec": t.duration_sec,
        "poster_url": f"/api/media/{t.poster_filename}" if t.poster_filename else "",
        "sample_url": f"/api/media/{t.sample_filename}" if t.sample_filename else "",
        "cost_points": cost,
    }


@app.get("/api/projects/{project_id}/changes")
def project_changes(project_id: int, user: User = Depends(current_user),
                    db: Session = Depends(db_session)):
    """История изменений настроек проекта — человеком и агентом."""
    pr = db.get(Project, project_id)
    if not pr or pr.owner_id != user.id:
        raise HTTPException(404, "проект не найден")
    rows = (db.query(ChangeLog).filter(ChangeLog.project_id == project_id)
            .order_by(ChangeLog.id.desc()).limit(100).all())
    return {"changes": [{
        "at": r.created_at.isoformat() if r.created_at else "",
        "actor": r.actor, "ref_type": r.ref_type, "ref_id": r.ref_id,
        "field": r.field, "old": r.old_value, "new": r.new_value,
    } for r in rows]}


# ─────────── живые токены дизайн-системы ───────────
# Канон: тёплая бумага с разлиновкой + ЕДИНСТВЕННЫЙ акцент — родной
# жёлто-оранжевый огненный градиент. Ключевые токены владелец крутит в
# админке, вкладка «Дизайн»; сюда они приезжают CSS-переопределением.
# Выключенные владельцем модели: не показываются клиентам и не принимаются
# к генерации. Экономия и управление ассортиментом без переката.
def _disabled_models(db: Session) -> set:
    row = db.get(AppSetting, "disabled_models")
    if not row:
        return set()
    try:
        return set(json.loads(row.value or "[]"))
    except ValueError:
        return set()


DESIGN_DEFAULTS = {
    "accent_from": "#e0503a", "accent_mid": "#ea6a34", "accent_to": "#f0913c",
    "bg": "#faf7f2", "radius_scale": "1.0", "glass_blur": "20",
}


def _design_tokens(db: Session) -> dict:
    row = db.get(AppSetting, "design_tokens")
    if not row:
        return dict(DESIGN_DEFAULTS)
    try:
        saved = json.loads(row.value or "{}")
    except ValueError:
        saved = {}
    out = dict(DESIGN_DEFAULTS)
    out.update({k: str(v)[:40] for k, v in saved.items() if k in DESIGN_DEFAULTS})
    return out


@app.get("/api/theme.css", include_in_schema=False)
def theme_css(db: Session = Depends(db_session)):
    t = _design_tokens(db)
    css = (":root{"
           f"--fire-ink: linear-gradient(95deg, {t['accent_from']} 0%, "
           f"{t['accent_mid']} 45%, {t['accent_to']} 100%);"
           f"--accent: {t['accent_from']};"
           f"--bg: {t['bg']};"
           "}"
           ".topbar,.track-card,.scene-card,.pa-panel,.mode-sheet,.trend-card,"
           ".modal-box{backdrop-filter: blur(" + t["glass_blur"] + "px) saturate(1.5);}")
    return Response(css, media_type="text/css",
                    headers={"Cache-Control": "no-cache"})


# ─────────────────────────── задачник команды ───────────────────────────
# Аналог задачника Организма, перенесён в наш стиль: колонки статусов + Гант.
# Доступ — is_admin или is_team: менеджер видит задачи, но не кассу.

def _team_user(user: User = Depends(current_user)) -> User:
    if not (user.is_admin or getattr(user, "is_team", False)):
        raise HTTPException(403, "доступ только для команды")
    return user


def _task_dict(t: TeamTask, names: dict) -> dict:
    return {"id": t.id, "title": t.title, "description": t.description,
            "project_id": t.project_id, "assignee_id": t.assignee_id,
            "assignee": names.get(t.assignee_id, ""),
            "author_type": t.author_type, "priority": t.priority,
            "status": t.status, "start_at": t.start_at, "due_at": t.due_at,
            "sort_order": t.sort_order}


@app.get("/api/team/board")
def team_board(user: User = Depends(_team_user), db: Session = Depends(db_session)):
    tasks = db.query(TeamTask).order_by(TeamTask.sort_order, TeamTask.id).all()
    projects = (db.query(TeamProject)
                .filter(TeamProject.status != "archived")
                .order_by(TeamProject.start_date).all())
    members = db.query(User).filter(
        (User.is_admin.is_(True)) | (User.is_team.is_(True))).all()
    names = {m.id: (m.name or m.login or f"id{m.id}") for m in members}
    return {
        "tasks": [_task_dict(t, names) for t in tasks],
        "projects": [{"id": p.id, "name": p.name, "color": p.color,
                      "start_date": p.start_date, "end_date": p.end_date,
                      "status": p.status} for p in projects],
        "members": [{"id": m.id, "name": names[m.id]} for m in members],
        "me": user.id,
    }


@app.post("/api/team/tasks")
async def team_task_create(request: Request, user: User = Depends(_team_user),
                           db: Session = Depends(db_session)):
    body = await request.json()
    title = str(body.get("title") or "").strip()
    if not title:
        raise HTTPException(400, "у задачи нет названия")
    t = TeamTask(title=title[:500],
                 description=str(body.get("description") or "")[:4000],
                 project_id=body.get("project_id") or None,
                 assignee_id=body.get("assignee_id") or None,
                 priority=str(body.get("priority") or "none"),
                 start_at=str(body.get("start_at") or "")[:10],
                 due_at=str(body.get("due_at") or "")[:10],
                 author_type="user", author_id=user.id)
    db.add(t)
    db.commit()
    db.refresh(t)
    return _task_dict(t, {user.id: user.name or user.login or ""})


@app.patch("/api/team/tasks/{task_id}")
async def team_task_update(task_id: int, request: Request,
                           user: User = Depends(_team_user),
                           db: Session = Depends(db_session)):
    t = db.get(TeamTask, task_id)
    if not t:
        raise HTTPException(404, "задача не найдена")
    body = await request.json()
    for f in ("title", "description", "priority", "start_at", "due_at"):
        if f in body:
            setattr(t, f, str(body[f] or "")[:4000])
    for f in ("project_id", "assignee_id", "sort_order"):
        if f in body:
            setattr(t, f, body[f] or (0 if f == "sort_order" else None))
    if "status" in body and body["status"] in ("open", "in_progress", "done"):
        t.status = body["status"]
        t.completed_at = now() if t.status == "done" else None
    db.commit()
    members = db.query(User).filter(
        (User.is_admin.is_(True)) | (User.is_team.is_(True))).all()
    return _task_dict(t, {m.id: (m.name or m.login or "") for m in members})


@app.delete("/api/team/tasks/{task_id}")
def team_task_delete(task_id: int, user: User = Depends(_team_user),
                     db: Session = Depends(db_session)):
    t = db.get(TeamTask, task_id)
    if t:
        db.delete(t)
        db.commit()
    return {"ok": True}


@app.post("/api/team/projects")
async def team_project_create(request: Request, user: User = Depends(_team_user),
                              db: Session = Depends(db_session)):
    body = await request.json()
    name = str(body.get("name") or "").strip()
    if not name:
        raise HTTPException(400, "у проекта нет названия")
    p = TeamProject(name=name[:160],
                    color=str(body.get("color") or "#e0503a")[:16],
                    start_date=str(body.get("start_date") or "")[:10],
                    end_date=str(body.get("end_date") or "")[:10])
    db.add(p)
    db.commit()
    return {"ok": True, "id": p.id}


@app.get("/api/earn")
def list_earn(request: Request, db: Session = Depends(db_session)):
    """Партнёрские продукты: шаблон любого вида со встроенным продуктом.

    Человек генерирует ролик, постит его со СВОЕЙ ссылкой /go/{id}?u=код —
    и получает долю с заказов, пришедших по его трафику."""
    user = _resolve_user(request, db)
    rows = (db.query(TrendPreset)
            .filter(TrendPreset.enabled.is_(True), TrendPreset.kind == "earn")
            .order_by(TrendPreset.position, TrendPreset.id).all())
    out = []
    for t in rows:
        d = _trend_dict(t, user)
        d["reward_note"] = t.reward_note
        # Индивидуальная ссылка появляется только у вошедшего: код выдаём
        # лениво — он же код партнёрки, одна ссылка на всё.
        if user:
            if not user.ref_code:
                user.ref_code = _new_ref_code(db)
                db.commit()
            d["my_link"] = f"{PUBLIC_BASE_URL}/go/{t.id}?u={user.ref_code}"
        out.append(d)
    return {"products": out, "authorized": bool(user)}


@app.get("/api/earn/stats")
def earn_stats(user: User = Depends(current_user), db: Session = Depends(db_session)):
    """Дашборд партнёра: клики, продажи, начислено. Продажи считает менеджер
    по правилу «только первая продажа клиента» — повторный клиент в таблицу
    физически не вставляется."""
    clicks = db.query(func.count(EarnClick.id)).filter(
        EarnClick.user_id == user.id).scalar() or 0
    sales = db.query(func.count(EarnSale.id)).filter(
        EarnSale.partner_id == user.id).scalar() or 0
    earned = db.query(func.coalesce(func.sum(EarnSale.reward_kopeks), 0)).filter(
        EarnSale.partner_id == user.id).scalar() or 0
    paid = db.query(func.coalesce(func.sum(EarnSale.reward_kopeks), 0)).filter(
        EarnSale.partner_id == user.id, EarnSale.status == "paid").scalar() or 0
    return {"clicks": int(clicks), "sales": int(sales),
            "earned_kopeks": int(earned), "paid_kopeks": int(paid)}


@app.post("/api/trends/jobs/{job_id}/share")
def trend_job_share(job_id: int, user: User = Depends(current_user),
                    db: Session = Depends(db_session)):
    """Автор публикует свой ролик: страница /v/{id} становится доступной."""
    job = db.get(TrendJob, job_id)
    if not job or job.user_id != user.id:
        raise HTTPException(404, "ролик не найден")
    if not job.video_filename:
        raise HTTPException(400, "ролик ещё не готов")
    job.is_public = True
    db.commit()
    return {"ok": True, "url": f"{PUBLIC_BASE_URL}/v/{job.id}"}


@app.get("/v/{job_id}", include_in_schema=False)
def trend_public_page(job_id: int, db: Session = Depends(db_session)):
    """Публичная страница ролика — петля Remix: смотришь чужой результат,
    жмёшь «Сделать так же», загружаешь своё фото. Каждый расшаренный ролик
    приводит следующего человека."""
    job = db.get(TrendJob, job_id)
    if not job or not job.is_public or not job.video_filename:
        raise HTTPException(404, "ролик не найден")
    t = db.get(TrendPreset, job.preset_id)
    title = (t.title if t else "lolq.ai")
    html = f"""<!doctype html><html lang="ru"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{title} — lolq.ai</title>
<style>
 body{{margin:0;background:#17120e;color:#f0e7dc;font:15px/1.5 system-ui;
      display:flex;flex-direction:column;align-items:center;gap:18px;
      min-height:100vh;justify-content:center;padding:24px}}
 video{{max-width:min(92vw,420px);border-radius:22px;box-shadow:0 18px 60px rgba(0,0,0,.5)}}
 a.btn{{display:inline-block;padding:14px 30px;border-radius:999px;font-weight:800;
      text-decoration:none;color:#fff;font-size:16px;
      background:linear-gradient(95deg,#e0503a,#ea6a34,#f0913c)}}
 .brand{{color:#a8988a;font-size:12px;letter-spacing:.14em;text-transform:uppercase}}
 h1{{font-size:18px;margin:0;text-align:center}}
</style></head><body>
<span class="brand">lolq.ai · тренды</span>
<h1>{title}</h1>
<video src="/v-media/{job.id}" controls autoplay loop muted playsinline></video>
<a class="btn" href="/trends">Сделать так же со своим фото →</a>
</body></html>"""
    return Response(html, media_type="text/html")


@app.get("/v-media/{job_id}", include_in_schema=False)
def trend_public_media(job_id: int, db: Session = Depends(db_session)):
    """Видео публичного ролика — отдаётся только когда автор поделился."""
    job = db.get(TrendJob, job_id)
    if not job or not job.is_public or not job.video_filename:
        raise HTTPException(404, "ролик не найден")
    return FileResponse(os.path.join(UPLOAD_DIR, job.video_filename),
                        media_type="video/mp4")


@app.get("/go/{preset_id}", include_in_schema=False)
def earn_go(preset_id: int, u: str = "", request: Request = None,
            db: Session = Depends(db_session)):
    """Партнёрский переход: лог клика → лендинг продукта.

    Клик — первичный документ атрибуции: по нему менеджер сводит «чей
    покупатель» при выплате. ip двигаем в хэш: для атрибуции хватает
    уникальности, хранить голые адреса незачем."""
    t = db.get(TrendPreset, preset_id)
    if not t or t.kind != "earn" or not t.landing_url:
        raise HTTPException(404, "продукт не найден")
    owner = _find_ambassador(db, u)
    if owner:
        import hashlib
        ip = (request.client.host if request and request.client else "") or ""
        db.add(EarnClick(preset_id=t.id, user_id=owner.id,
                         ip_hash=hashlib.sha256(ip.encode()).hexdigest()[:16]))
        db.commit()
    url = t.landing_url
    if owner and "?" not in url:
        url += f"?ref={owner.ref_code}"
    elif owner:
        url += f"&ref={owner.ref_code}"
    return RedirectResponse(url, status_code=302)


@app.get("/api/trends")
def list_trends(request: Request, db: Session = Depends(db_session)):
    """Витрина шаблонов. Публичная: карточки с примерами — двигатель захода."""
    user = _resolve_user(request, db)
    rows = (db.query(TrendPreset).filter(TrendPreset.enabled.is_(True), TrendPreset.kind != "earn")
            .order_by(TrendPreset.position, TrendPreset.id).all())
    return {"presets": [_trend_dict(t, user) for t in rows],
            "authorized": bool(user)}


@app.post("/api/trends/{preset_id}/make")
async def make_trend(preset_id: int, photo: UploadFile, request: Request = None,
                     user: User = Depends(current_user),
                     db: Session = Depends(db_session)):
    """Фото → ролик по шаблону. Единственное действие пользователя."""
    _guard_disk()
    t = db.get(TrendPreset, preset_id)
    if not t or not t.enabled:
        raise HTTPException(404, "шаблон не найден")
    data = await photo.read()
    if len(data) < 1024:
        raise HTTPException(400, "фото не долетело — попробуй ещё раз")
    # Свой стиль разрешён только в «Заработке»: тренд — фиксированный шаблон,
    # а партнёрский ролик может быть каким угодно, кроме одного — продукт в
    # кадре не меняется.
    user_style = ""
    if t.kind == "earn":
        form = await request.form() if hasattr(request, "form") else {}
        user_style = str(form.get("style") or "")[:1000]
    ext = os.path.splitext(photo.filename or "")[1].lower() or ".jpg"
    fname = f"trend_{uuid.uuid4().hex}{ext}"
    with open(os.path.join(UPLOAD_DIR, fname), "wb") as f:
        f.write(data)
    _reg_file(db, fname, user.id, kind="photo")
    cost = _trend_dict(t)["cost_points"]
    _charge(db, user, cost, f"тренд «{t.title}»", kind="trend", engine=t.video_engine or "")
    job = TrendJob(preset_id=t.id, user_id=user.id, photo_filename=fname,
                   user_style=user_style, charged_points=cost)
    db.add(job)
    db.commit()
    db.refresh(job)
    _spawn_gen(user, _run_trend_job, job.id, kind="video")
    return {"ok": True, "job_id": job.id, "charged": cost}


@app.get("/api/trends/jobs/{job_id}")
def trend_job_status(job_id: int, user: User = Depends(current_user),
                     db: Session = Depends(db_session)):
    job = db.get(TrendJob, job_id)
    if not job or job.user_id != user.id:
        raise HTTPException(404, "задача не найдена")
    return {
        "id": job.id, "status": job.status, "error": job.error,
        "frame_url": f"/api/media/{job.frame_filename}" if job.frame_filename else "",
        "video_url": f"/api/media/{job.video_filename}" if job.video_filename else "",
    }


def _run_trend_job(job_id: int) -> None:
    """Кадр по фото человека → анимация по шаблону. Два шага, оба зашиты."""
    import asyncio
    db = SessionLocal()
    try:
        job = db.get(TrendJob, job_id)
        if not job:
            return
        t = db.get(TrendPreset, job.preset_id)
        owner = db.get(User, job.user_id)
        job.status = "frame"
        db.commit()
        photo = os.path.join(UPLOAD_DIR, job.photo_filename)
        prompt = (t.image_prompt or "")
        refs = [photo]
        if getattr(job, "user_style", ""):
            # Стиль партнёра ложится ПОВЕРХ, но продукт неприкосновенен:
            # это и есть оффер — товар в кадре, остальное как хочешь.
            prompt += ("\n\nSTYLE REQUESTED BY THE CREATOR (apply fully): "
                       + job.user_style
                       + "\nThe PRODUCT itself must stay exactly as in its "
                         "reference: same packaging, same label text, unchanged.")
        if t.kind == "earn" and t.poster_filename:
            ppath = os.path.join(UPLOAD_DIR, t.poster_filename)
            if os.path.exists(ppath):
                refs.append(ppath)
        prompt += ("\n\nThe first reference photo is the REAL person to feature: "
                   "reproduce their exact face, hair and build. Do not beautify "
                   "or restyle them.")
        data, mime = asyncio.run(mediagen.generate_image(
            prompt, reference_path=refs[0], reference_paths=refs,
            engine=t.image_engine or "", aspect=t.aspect or "9:16"))
        job.frame_filename = _save_image(data, mime)
        _reg_file(db, job.frame_filename, job.user_id, kind="frames")
        job.status = "video"
        db.commit()
        mediagen.reset_task()
        job.video_filename = asyncio.run(mediagen.animate_scene(
            prompt=t.motion_prompt or "subtle natural motion",
            first_path=os.path.join(UPLOAD_DIR, job.frame_filename),
            last_path=None, duration_sec=t.duration_sec or 6,
            provider="seedance", engine=t.video_engine or "seedance-2-mini",
            aspect=t.aspect or "9:16"))
        _reg_file(db, job.video_filename, job.user_id, kind="video")
        job.status = "done"
        db.commit()
        log.info("тренд %s: ролик готов для user %s", job.preset_id, job.user_id)
    except Exception as e:  # noqa: BLE001 — вернуть токены и сказать правду
        db.rollback()
        job = db.get(TrendJob, job_id)
        if job:
            job.status = "error"
            job.error = _err_text(e, 400)
            owner = db.get(User, job.user_id)
            if owner and job.charged_points:
                _refund(db, owner, job.charged_points, f"тренд не получился ({job.id})")
            db.commit()
        log.warning("тренд-задача %s упала: %s", job_id, e)
    finally:
        db.close()


# ─────────────────────────── авторизация ───────────────────────────

def _user_dict(user: User) -> dict:
    plan_id = _plan_of(user)
    tier = _tier_of_user(user)
    return {"id": user.id, "name": user.name, "login": user.login,
            # Ава из Telegram (photo_url в initData/виджете) — для кнопки
            # «Профиль» в шапке; у почтовых аккаунтов пустая строка.
            "avatar_url": user.avatar_url or "",
            # Привязка Telegram: кабинету нужно знать, показывать ли кнопку
            # «привязать» и что показывать привязанным.
            "tg_linked": bool(user.tg_id), "tg_username": user.tg_username or "",
            # Привязка телефона: кабинет показывает «✓ +7 962 •••• 55» или
            # кнопку «привязать»; сам номер целиком наружу не уходит.
            "phone_linked": bool(user.phone), "phone_masked": _mask_phone(user.phone),
            "is_admin": user.is_admin, "gen_points": user.gen_points,
            # Из чего сложен остаток: бонусные заработаны приглашениями и
            # тратятся первыми, платные — то, что человек купил сам.
            "bonus_points": int(user.bonus_points or 0),
            "bonus_rate": BONUS_RATE,
            "paid_points": int(user.gen_points or 0),
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
        # Человек, зарегистрированный по email, логично вводит email —
        # даже если поле называется «логин».
        if not user and "@" in login_name:
            user = db.query(User).filter(User.email == login_name.lower()).first()
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
        "email": bool(UNISENDER_API_KEY and AUTH_MAIL_FROM),
        "phone": bool(SMS_GATEWAY_URL and SMS_GATEWAY_TOKEN),
    }


def _relink_allowed(db: Session, holder: User) -> bool:
    """Можно ли забрать телеграм у аккаунта holder.

    Только у «пустышки»: без логина, почты и телефона, без единой сцены и без
    купленных токенов. Такие аккаунты плодит сам мини-апп при первом открытии,
    и держать на них привязку — значит навсегда развести человека с его работой.
    """
    if holder.login or holder.email or holder.phone:
        return False
    # 150 — стартовый баланс нового аккаунта (db.User.gen_points): больше него
    # значит человек покупал токены, и такой аккаунт «пустышкой» не считается.
    if int(holder.gen_points or 0) > 150:
        return False
    scenes = (db.query(Scene).join(Track, Scene.track_id == Track.id)
              .join(Project, Track.project_id == Project.id)
              .filter(Project.owner_id == holder.id).count())
    return scenes == 0


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
        # Залогиненный аккаунт БЕЗ телеграма — это ПРИВЯЗКА, а не новый юзер:
        # человек жмёт «привязать Telegram» из кабинета и должен остаться на
        # своём аккаунте с проектами, есть у него логин/почта или нет.
        if guest and not guest.tg_id:
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
    elif guest and guest.id != user.id and _relink_allowed(db, user):
        # ПЕРЕПРИВЯЗКА. Телеграм часто «застревает» на пустом аккаунте, который
        # завёлся сам при первом открытии мини-аппа: человек потом работает под
        # своим настоящим и получает «этот телеграм уже привязан к другому».
        # Если тот аккаунт пустой (без логина/почты/телефона и без работы), он
        # уступает привязку текущему — с записью в журнал изменений.
        _log_change(db, "user", user.id, "tg_relink",
                    old_value=f"user {user.id}", new_value=f"user {guest.id}")
        user.tg_id = None
        user.tg_username = None
        db.flush()
        guest.tg_id = tg_id
        guest.tg_username = str(data.get("username") or "")
        if not guest.avatar_url:
            guest.avatar_url = str(data.get("photo_url") or "")
        db.commit()
        user = guest
    elif guest and guest.id != user.id and (guest.login or guest.email or guest.phone):
        # Настоящий аккаунт с наработками телеграм не отбирает молча.
        raise HTTPException(409, "этот Telegram уже привязан к другому аккаунту "
                                 "с проектами — войди под ним или отвяжи там")
    else:
        user = _adopt_guest(db, guest, user)
    # Ава обновляется и у существующего аккаунта: photo_url приходит с каждым
    # входом, а записывался раньше только при первой привязке — человек менял
    # фото в Telegram, а в шапке годами висело старое (или пустота).
    photo = str(data.get("photo_url") or "")
    if photo and photo != (user.avatar_url or ""):
        user.avatar_url = photo
        db.commit()
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


# ─────────────── вход по email (код в письме) и телефону (SMS) ───────────────
# Письма — Unisender Go (транзакционный API организма), SMS — шлюз SMS.RU.
# Ключи в infra/.env; пока пусты — /api/auth/config отдаёт false и вкладок
# на экране входа просто нет.
UNISENDER_API_KEY = os.environ.get("UNISENDER_API_KEY", "").strip()
UNISENDER_API_URL = os.environ.get(
    "UNISENDER_API_URL", "https://go2.unisender.ru/ru/transactional/api/v1").rstrip("/")
# lolq.ai у Unisender не подтверждён — шлём с подтверждённого bioura.io,
# пока владелец не заведёт домен (см. чек-лист в docs).
AUTH_MAIL_FROM = os.environ.get("AUTH_MAIL_FROM", "noreply@bioura.io").strip()
AUTH_MAIL_FROM_NAME = os.environ.get("AUTH_MAIL_FROM_NAME", "lolq.ai").strip()
SMS_GATEWAY_URL = os.environ.get("SMS_GATEWAY_URL", "").strip()
SMS_GATEWAY_TOKEN = os.environ.get("SMS_GATEWAY_TOKEN", "").strip()
SMS_SENDER = os.environ.get("SMS_SENDER", "").strip()

CODE_TTL_S = 3600          # код живёт час
CODE_MAX_ATTEMPTS = 5      # после пяти промахов код сгорает
SMS_PER_DAY = 5            # SMS на номер в сутки
SMS_MIN_INTERVAL_S = 60    # не чаще раза в минуту


def _norm_email(raw: str) -> str:
    e = str(raw or "").strip().lower()
    if not e or "@" not in e or " " in e or len(e) > 254:
        return ""
    return e


def _norm_phone(raw: str) -> str:
    """Только цифры, российская нормализация 8→7, итог 11–15 цифр."""
    digits = "".join(c for c in str(raw or "") if c.isdigit())
    if len(digits) == 11 and digits.startswith("8"):
        digits = "7" + digits[1:]
    if len(digits) == 10 and digits.startswith("9"):
        digits = "7" + digits
    return digits if 11 <= len(digits) <= 15 else ""


def _mask_phone(phone: str) -> str:
    """«+79623446955» → «+7 962 •••• 55»: середина скрыта, узнать свой можно."""
    p = str(phone or "")
    if len(p) < 7:
        return f"+{p}" if p else ""
    return f"+{p[0]} {p[1:4]} •••• {p[-2:]}"


def _issue_code(db: Session, kind: str, address: str, user_id: int = 0) -> str:
    """Новый код гасит прежние живые того же вида: действителен последний."""
    db.query(AuthCode).filter(AuthCode.kind == kind, AuthCode.address == address,
                              AuthCode.used == False).update({"used": True})  # noqa: E712
    code = f"{secrets.randbelow(900000) + 100000}"
    db.add(AuthCode(kind=kind, address=address, code=code, user_id=user_id))
    db.commit()
    return code


def _check_code(db: Session, kind: str, address: str, code: str) -> "AuthCode":
    """Проверить код; кидает HTTPException словами, почему не подошёл."""
    row = (db.query(AuthCode)
           .filter(AuthCode.kind == kind, AuthCode.address == address,
                   AuthCode.used == False)  # noqa: E712
           .order_by(AuthCode.id.desc()).first())
    if not row:
        raise HTTPException(400, "код не запрашивался или уже использован")
    created = row.created_at.replace(tzinfo=timezone.utc) if row.created_at.tzinfo is None \
        else row.created_at
    if (now() - created).total_seconds() > CODE_TTL_S:
        row.used = True
        db.commit()
        raise HTTPException(400, "код устарел — запроси новый")
    if not code or not hmac.compare_digest(row.code, str(code).strip()):
        row.attempts += 1
        if row.attempts >= CODE_MAX_ATTEMPTS:
            row.used = True
        db.commit()
        raise HTTPException(400, "неверный код")
    row.used = True
    db.commit()
    return row


async def _send_auth_email(to: str, subject: str, text: str) -> None:
    """Unisender Go /email/send.json. Ошибка транспорта — честный 502."""
    if not (UNISENDER_API_KEY and AUTH_MAIL_FROM):
        raise HTTPException(400, "почтовый вход не настроен")
    import httpx as _httpx
    payload = {"message": {
        "recipients": [{"email": to}],
        "subject": subject,
        "body": {"plaintext": text},
        "from_email": AUTH_MAIL_FROM,
        "from_name": AUTH_MAIL_FROM_NAME,
    }}
    async with _httpx.AsyncClient(timeout=30) as client:
        r = await client.post(f"{UNISENDER_API_URL}/email/send.json",
                              headers={"X-API-KEY": UNISENDER_API_KEY}, json=payload)
    try:
        data = r.json()
    except ValueError:
        data = {}
    if r.status_code != 200 or data.get("status") != "success":
        log.warning("auth email fail: %s %s", r.status_code, str(data)[:300])
        raise HTTPException(502, "письмо не ушло — попробуй позже")


async def _send_auth_sms(phone: str, text: str) -> None:
    """SMS.RU /sms/send тем же протоколом, что bioura.io."""
    if not (SMS_GATEWAY_URL and SMS_GATEWAY_TOKEN):
        raise HTTPException(400, "вход по телефону не настроен")
    import httpx as _httpx
    data = {"api_id": SMS_GATEWAY_TOKEN, "to": phone, "msg": text, "json": 1}
    if SMS_SENDER:
        data["from"] = SMS_SENDER
    async with _httpx.AsyncClient(timeout=15) as client:
        r = await client.post(SMS_GATEWAY_URL, data=data)
    try:
        body = r.json()
    except ValueError:
        body = {}
    st = (body.get("sms") or {}).get(phone, {}) or {}
    if body.get("status") != "OK" or st.get("status") != "OK":
        log.warning("auth sms fail: %s %s", r.status_code, str(body)[:300])
        raise HTTPException(502, "SMS не ушла — попробуй позже")


def _guest_of(request: Request, db: Session) -> "User | None":
    token = request.cookies.get(QV_COOKIE)
    if not token:
        return None
    try:
        return db.get(User, int(signer.loads(token, max_age=QV_MAX_AGE).get("uid") or 0))
    except Exception:  # noqa: BLE001
        return None


@app.post("/api/auth/register-email")
async def auth_register_email(request: Request, ref: str = "",
                              db: Session = Depends(db_session)):
    """Регистрация по email: аккаунт с паролем сразу, кодом подтверждается
    адрес. Гость с проектами «повышается» до аккаунта, ничего не теряя."""
    body = await request.json()
    email = _norm_email(body.get("email"))
    password = str(body.get("password") or "")
    if not email:
        raise HTTPException(400, "введи корректный email")
    if len(password) < 6:
        raise HTTPException(400, "пароль от 6 символов")
    existing = db.query(User).filter(User.email == email).first()
    if existing and existing.password_hash:
        raise HTTPException(400, "этот email уже зарегистрирован — войди или сбрось пароль")
    guest = _guest_of(request, db)
    user = existing
    if not user:
        fresh_guest = guest and not guest.login and not guest.tg_id \
            and not guest.yandex_id and not guest.google_id and not guest.email
        user = guest if fresh_guest else User(name=email.split("@")[0])
        if not fresh_guest:
            db.add(user)
    user.email = email
    user.email_verified = False
    user.password_hash = _hash_password(password)
    if not user.login and not db.query(User).filter(User.login == email).first():
        user.login = email
    if not user.name:
        user.name = email.split("@")[0]
    db.commit()
    db.refresh(user)
    _attach_ref(db, user, ref)
    code = _issue_code(db, "email_verify", email, user.id)
    await _send_auth_email(
        email, "Код подтверждения lolq.ai",
        f"Твой код подтверждения: {code}\n\nКод действует 1 час. "
        "Если это не ты — просто удали письмо.")
    return {"ok": True, "sent": True}


@app.post("/api/auth/verify-email")
async def auth_verify_email(request: Request, db: Session = Depends(db_session)):
    body = await request.json()
    email = _norm_email(body.get("email"))
    if not email:
        raise HTTPException(400, "введи корректный email")
    row = _check_code(db, "email_verify", email, body.get("code"))
    user = db.get(User, row.user_id) or db.query(User).filter(User.email == email).first()
    if not user:
        raise HTTPException(400, "аккаунт не найден — зарегистрируйся заново")
    user.email_verified = True
    db.commit()
    return _auth_response(user)


@app.post("/api/auth/forgot")
async def auth_forgot(request: Request, db: Session = Depends(db_session)):
    body = await request.json()
    email = _norm_email(body.get("email"))
    if not email:
        raise HTTPException(400, "введи корректный email")
    user = db.query(User).filter(User.email == email).first()
    # Не раскрываем, есть ли такой аккаунт: ответ одинаковый в обе стороны.
    if user:
        code = _issue_code(db, "email_reset", email, user.id)
        await _send_auth_email(
            email, "Сброс пароля lolq.ai",
            f"Код для сброса пароля: {code}\n\nКод действует 1 час. "
            "Если ты не просил сброс — не отвечай на письмо, пароль не изменится.")
    return {"ok": True, "sent": True}


@app.post("/api/auth/reset")
async def auth_reset(request: Request, db: Session = Depends(db_session)):
    body = await request.json()
    email = _norm_email(body.get("email"))
    password = str(body.get("password") or "")
    if not email:
        raise HTTPException(400, "введи корректный email")
    if len(password) < 6:
        raise HTTPException(400, "пароль от 6 символов")
    row = _check_code(db, "email_reset", email, body.get("code"))
    user = db.get(User, row.user_id)
    if not user:
        raise HTTPException(400, "аккаунт не найден")
    user.password_hash = _hash_password(password)
    user.email_verified = True  # код из письма и есть подтверждение адреса
    db.commit()
    return _auth_response(user)


@app.post("/api/auth/phone/start")
async def auth_phone_start(request: Request, db: Session = Depends(db_session)):
    body = await request.json()
    phone = _norm_phone(body.get("phone"))
    if not phone:
        raise HTTPException(400, "введи телефон в формате +7...")
    day_ago = now() - timedelta(days=1)
    recent = (db.query(AuthCode)
              .filter(AuthCode.kind == "phone", AuthCode.address == phone,
                      AuthCode.created_at > day_ago)
              .order_by(AuthCode.id.desc()).all())
    if len(recent) >= SMS_PER_DAY:
        raise HTTPException(429, "лимит SMS на сегодня исчерпан — попробуй завтра")
    if recent:
        last = recent[0].created_at
        last = last.replace(tzinfo=timezone.utc) if last.tzinfo is None else last
        if (now() - last).total_seconds() < SMS_MIN_INTERVAL_S:
            raise HTTPException(429, "код уже отправлен — подожди минуту")
    code = _issue_code(db, "phone", phone)
    await _send_auth_sms(phone, f"Код входа lolq.ai: {code}")
    return {"ok": True, "sent": True}


@app.post("/api/auth/phone/verify")
async def auth_phone_verify(request: Request, ref: str = "",
                            db: Session = Depends(db_session)):
    body = await request.json()
    phone = _norm_phone(body.get("phone"))
    if not phone:
        raise HTTPException(400, "введи телефон в формате +7...")
    _check_code(db, "phone", phone, body.get("code"))
    user = db.query(User).filter(User.phone == phone).first()
    # РЕЖИМ ПРИВЯЗКИ: запрос из кабинета (body.link) с живой сессией цепляет
    # номер к ТЕКУЩЕМУ аккаунту, а не логинит в другой. Иначе «привязать
    # телефон» молча пересаживало бы человека на чужой пустой аккаунт.
    if body.get("link"):
        me_user = _resolve_user(request, db)
        if not me_user:
            raise HTTPException(401, "сессия не найдена — войди и попробуй снова")
        if user and user.id != me_user.id:
            raise HTTPException(409, "этот номер уже привязан к другому аккаунту")
        me_user.phone = phone
        db.commit()
        return {"ok": True, "linked": True, "phone_masked": _mask_phone(phone)}
    guest = _guest_of(request, db)
    if not user:
        fresh_guest = guest and not guest.login and not guest.tg_id \
            and not guest.yandex_id and not guest.google_id and not guest.email \
            and not guest.phone
        user = guest if fresh_guest else User(name=f"+{phone}")
        if not fresh_guest:
            db.add(user)
        user.phone = phone
        if not user.name:
            user.name = f"+{phone}"
        db.commit()
        db.refresh(user)
    else:
        _adopt_guest(db, guest, user)
    _attach_ref(db, user, ref)
    return _auth_response(user)


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
        # Привязка к ПРЕДМЕТУ (треку мокап-проекта): если она есть, фото
        # предмета показываются в атрибуте и уходят в кадр — заводить вещи
        # дважды не нужно.
        "item_track_id": int(getattr(a, "item_track_id", 0) or 0),
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
        # Голос ElevenLabs, закреплённый за персонажем, и его манера речи.
        "voice_id": c.voice_id or "",
        "voice_note": getattr(c, "voice_note", "") or "",
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


def _scene_dialogue(s: Scene) -> list[dict]:
    """dialogue_json → список реплик; битый JSON = пусто."""
    try:
        data = json.loads(getattr(s, "dialogue_json", "") or "[]")
        return data if isinstance(data, list) else []
    except ValueError:
        return []


def _midframes(s: Scene) -> list[dict]:
    """midframes_json → список; битый/пустой JSON = пустой список."""
    try:
        data = json.loads(s.midframes_json or "[]")
        return data if isinstance(data, list) else []
    except ValueError:
        return []


def _midframe_count(duration_sec: int) -> int:
    """Сколько промежуточных кадров положено сцене: примерно раз в 2 секунды
    между первым и последним, но не больше 5 (экономия токенов и времени).
    Длинной сцене (12-30с) опор нужно больше: видео едет отрезками ~6с
    между соседними кадрами цепочки."""
    return max(0, min(5, round((duration_sec or 0) / 2) - 1))


def _frames_state(s: Scene) -> str:
    chain = _scene_frame_chain(s)
    if not chain:
        return "red"
    if s.video_filename and (s.video_src_sig or "") == "|".join(chain):
        return "green"
    return "yellow"


def scene_dict(s: Scene) -> dict:
    return {
        # track_id нужен мосту «Доснять в мастерской»: карточка кадра рисуется
        # без объекта трека, а заголовок возврата («← К сцене 12 трека …»)
        # обязан назвать трек, иначе это кнопка «назад в никуда».
        "id": s.id, "track_id": s.track_id, "position": s.position, "start_sec": s.start_sec,
        "duration_sec": s.duration_sec, "lyric_line": s.lyric_line,
        "characters": s.characters,
        "prompt_stale": bool(getattr(s, "prompt_stale", False)),
        # Реальная длина ролика: слот сцены и длина видео расходятся, а
        # обрезать надо по тому, что есть на самом деле.
        "video_seconds": float(s.video_seconds or 0),
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
        # Светофор кадров: green — видео снято ровно из текущих кадров,
        # yellow — кадры менялись после съёмки (или видео ещё нет), red — кадров нет.
        "frames_state": _frames_state(s),
        "video_status": s.video_status, "video_error": s.video_error,
        "video_provider": s.video_provider,
        # Движки сцены наружу отдавались... никогда: карточка кадра рисовала
        # чипы по s.image_engine/s.video_engine, которых в ответе не было, и
        # подсветка всегда врала. Теперь поля есть, и «переопределить движок»
        # в карточке показывает то, чем сцену реально сняли.
        "image_engine": s.image_engine or "", "video_engine": s.video_engine or "",
        # Каким стилем СНЯТ этот кадр и сколько снимков прежних стилей лежит
        # рядом. Смешанный трек (перерисовали только припев) — законное
        # состояние, но человек обязан его видеть, а не гадать.
        "style_keys": [k for k in (s.style_keys or "").split(",") if k],
        "versions": len(s.versions or []),
        # ПРОИСХОЖДЕНИЕ КАДРА. continued_from — «этот план продолжает тот»:
        # карточка рисует стрелку, а генерация видео знает, что склейка
        # встык. copied_from — просто копия.
        "continued_from_id": int(s.continued_from_id or 0),
        "copied_from_id": int(s.copied_from_id or 0),
        # Видео снято под ДРУГУЮ длительность (рядом вставили кадр, слоты
        # пересчитались). Не ошибка и не повод стирать оплаченную работу —
        # но человек обязан это видеть до сборки.
        "video_stale": bool(s.video_stale and s.video_filename),
        # Режимы «сериалы» и «UGC»: акт серии и кто говорит в кадре.
        "act": s.act or "", "speaker": s.speaker or "",
        "dialogue": _scene_dialogue(s),
    }


#: Метка «в промптах сцен стиля нет». Пустая строка означает НЕИЗВЕСТНО, а
#: неизвестное у нас — легаси-раскадровки, у которых стиль вписан в каждый
#: image_prompt (так требовал прежний claude.SCENES_SYSTEM). Различать эти
#: два состояния обязательно: от этого зависит, нужен ли платный шаг
#: переписывания промптов перед рестайлом.
PROMPTS_NO_STYLE = "-"


def _prompts_style_base(t: Track) -> str | None:
    """Под какой стиль писаны ТЕКСТЫ сцен трека.

    None — стиля в них нет вовсе (новая раскадровка), переписывать нечего.
    Строка ключей — под этот стиль они и писаны; если он разошёлся с
    нынешним, промпты придётся переписать, иначе в кадр уедут два стиля
    сразу и человек увидит прежние картинки."""
    raw = (t.prompts_style_keys or "").strip()
    if raw == PROMPTS_NO_STYLE:
        return None
    return raw or ",".join(_track_style_keys(t))


def _track_style_keys(t: Track) -> list[str]:
    """Ключи стилей трека. Старые треки хранят только текст промпта — для них
    один раз разбираем текст обратно в ключи тем же алгоритмом, каким его
    собирали (prompts_catalog.fusion — дословный порт buildFusionStyle)."""
    keys = [k for k in (t.style_keys or "").split(",") if k.strip()]
    if keys:
        return keys
    return prompts_catalog.keys_from_prompt(t.style or "")



# ─────────────── 3D-облёт товара (режим мокапов) ───────────────
# Восемь ракурсов по кругу: 0°, 45°, … 315°. Листаются drag'ом на карточке
# трека и выглядят как вращение 3D-модельки — без единого полигона.
TURNAROUND_YAWS = (0, 45, 90, 135, 180, 225, 270, 315)


def _turnaround_files(t: Track) -> list[str]:
    try:
        v = json.loads(t.turnaround_files or "[]")
        return [str(x) for x in v] if isinstance(v, list) else []
    except Exception:  # noqa: BLE001
        return []


def _turnaround_cost(t: Track) -> int:
    """Цена облёта = 8 кадров тем же прайсом, что у кадров сцен (FRAME_COST —
    цена ПАРЫ, поэтому восемь кадров = четыре пары)."""
    try:
        sess = object_session(t)
        owner = (sess.get(User, t.project.owner_id)
                 if (sess and t.project.owner_id) else None)
        eng = _resolve_image_engine(owner, t)
        return _frames_cost(owner, None, eng) * (len(TURNAROUND_YAWS) // 2)
    except Exception:  # noqa: BLE001
        return FRAMES_COST * (len(TURNAROUND_YAWS) // 2)


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
        "coverage": _scenes_coverage(t),
        "approved_count": sum(1 for s in t.scenes if s.approved),
        "storyboard_url": f"/api/media/{t.storyboard_filename}" if t.storyboard_filename else "",
        "storyboard_status": t.storyboard_status, "storyboard_error": t.storyboard_error,
        # ЛИСТ УСТАРЕЛ: сцен стало больше или меньше, чем было в момент его
        # заказа, значит сетка листа не совпадает с сеткой нарезки и
        # «разложить по кадрам» порежет мимо панелей. Отдельного флага нет
        # намеренно — флаг можно забыть выставить, расхождение чисел нельзя.
        # 0 в storyboard_scenes = лист старый, состояние неизвестно, молчим.
        "storyboard_stale": bool(t.storyboard_filename and t.storyboard_scenes
                                 and t.storyboard_scenes != len(t.scenes)),
        "storyboard_scenes": int(t.storyboard_scenes or 0),
        "clip_url": f"/api/media/{t.clip_filename}" if t.clip_filename else "",
        "clip_status": t.clip_status, "clip_error": t.clip_error,
        "cover_url": f"/api/media/{t.cover_filename}" if t.cover_filename else "",
        "supergen_status": t.supergen_status, "supergen_note": t.supergen_note,
        # РЕСТАЙЛ. prompts_dirty — тексты сцен писаны под прежний стиль, и
        # перед перерисовкой их придётся переписать (это единственный шаг
        # рестайла, который стоит текстовых токенов).
        "restyle_status": t.restyle_status or "", "restyle_note": t.restyle_note or "",
        "prompts_dirty": bool(t.scenes and _prompts_style_base(t) is not None
                              and _prompts_style_base(t) != (t.style_keys or "")),
        # Собранный клип снят в прежнем стиле — кнопка сборки обязана сказать.
        "clip_stale": bool(t.clip_stale and t.clip_filename),
        "text_engine": t.text_engine or "",
        # Сколько сцен сняты НЕ нынешним стилем трека: столько кадров
        # покажут метку «в прежнем стиле».
        "scenes_stale": sum(
            1 for s in t.scenes
            if s.image_filename and (s.style_keys or "") != (t.style_keys or "")),
        # Сколько сцен несут видео, снятое под другой слот (рядом вставили
        # кадр). Сборка подрежет их по слоту — сказать об этом надо ДО неё.
        "video_stale_count": sum(1 for s in t.scenes if s.video_stale and s.video_filename),
        # Раскадровка длиннее дорожки: сборка вешает звук с -shortest, и
        # лишние секунды молча обрежутся по музыке. Молчать об этом нельзя.
        "timing_over_sec": max(0, sum(int(s.duration_sec or 0) for s in t.scenes)
                               - int(t.audio_duration_sec or 0)) if t.audio_duration_sec else 0,
        "copied_from_id": int(t.copied_from_id or 0),
        "film_grain": t.film_grain, "no_story": t.no_story,
        "random_cast": bool(getattr(t, "random_cast", False)),
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
        # 3D-облёт товара: 8 ракурсов по кругу + цена кнопки.
        "turnaround_status": t.turnaround_status or "",
        "turnaround_note": t.turnaround_note or "",
        "turnaround_urls": [f"/api/media/{f}" for f in _turnaround_files(t)],
        "turnaround_cost": _turnaround_cost(t),
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
        # Копия проекта — не «новый проект с похожим именем»: карточка
        # обязана уметь сказать, из чего она сделана.
        "copied_from_id": int(p.copied_from_id or 0),
        # Текстовая модель конвейера. Пусто = «как решит тариф»; блок
        # сценария показывает это наследование, а не пустой чип.
        "text_engine": p.text_engine or "",
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
    is_adm = bool(user and user.is_admin)
    lessons = learn.index(lg, plan_id=plan_id, is_admin=is_adm, done=done)
    courses = learn.program(lg, plan_id=plan_id, is_admin=is_adm, done=done)
    # Замок академии живёт НЕ на уроке, а на его артефакте: текст открыт всем
    # (уровни 0–4 — двигатель органики), а набор приёмов упирается в тариф.
    # learn.py про каталог промтов не знает и знать не должен, поэтому пару
    # «курс → какие наборы закрыты» сшиваем здесь, в одном месте.
    for row in courses:
        packs = [prompts_library.public_pack(k, lang=lg, plan_id=plan_id,
                                             is_admin=is_adm)
                 for k in row["packs"]]
        packs = [x for x in packs if x]
        row["pack_cards"] = [{"key": x["key"], "label": x["label"],
                              "tier": x["tier"], "locked": x["locked"],
                              "shots": len(x.get("shots") or [])}
                             for x in packs]
        row["locked_packs"] = len([x for x in packs if x["locked"]])
    return {
        "lang": lg,
        "plan": plan_id,
        "authorized": bool(user),
        "levels": learn.levels(lg),
        "lessons": lessons,
        "courses": courses,
        "progress": learn.progress(lg, done),
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
    # АРТЕФАКТ УРОКА едет вместе с уроком, а не отдельным запросом: кнопка
    # «применить в проект» рисуется в тот же момент, что и текст, и второй
    # круг за карточкой набора означал бы урок, у которого кнопка появляется
    # позже самого урока.
    out["pack_card"] = prompts_library.public_pack(
        item["pack"], lang=lg, plan_id=plan_id,
        is_admin=bool(user and user.is_admin)) if item["pack"] else None
    out["preset_card"] = prompts_catalog.public_preset(item["preset"], lang=lg) \
        if item["preset"] else None
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
            "restyle_status": t.restyle_status or "",
            "restyle_note": t.restyle_note or "",
            "turnaround_status": t.turnaround_status or "",
            "turnaround_note": t.turnaround_note or "",
            "scenes_count": len(t.scenes),
            "coverage": _scenes_coverage(t),
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
    # Сюжет правится руками не реже, чем генерируется: без этой ветки правки
    # в поле «Сюжет» уходили в никуда — кнопка «Сохранить» их не отправляла.
    if "story" in body:
        project.story = str(body["story"] or "")
        if project.story.strip() and project.story_status != "running":
            project.story_status = "done"
    # ТЕКСТОВАЯ МОДЕЛЬ сценарного конвейера. Пустая строка валидна и значит
    # «как решит тариф» — снять свой выбор можно так же, как сделать.
    # Проверяем по реестру, а не по тарифу: тариф опустит закрытое сам в
    # момент генерации, а стирать сделанный выбор из-за смены тарифа нельзя.
    if "text_engine" in body:
        want = str(body["text_engine"] or "").strip()
        project.text_engine = want if want in textgen.TEXT_ENGINES else ""
    db.commit()
    return project_dict(project, docs=_project_docs(db, project))


def _run_story_generation(project_id: int) -> None:
    db = SessionLocal()
    engine = "gateway"
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
             # Как стиль трека влияет на сюжет — из админки стилей. До неё
             # стиль умел влиять только на картинку.
             "style_base": prompts_catalog.story_base(_track_style_keys(t)),
             "audio_profile": t.audio_profile}
            for t in project.tracks
        ]
        engine = _text_engine_for(db, project)
        import asyncio
        result = asyncio.run(claude.generate_story(
            project.character_bible, tracks, characters_payload(project),
            engine=engine))
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
            project.story_error = _err_text(e, 500)
            db.commit()
            _text_refund(db, project, engine, "возврат: сюжет не написался",
                         ref_type="project", ref_id=project.id)
        log.warning("генерация сюжета упала: %s", e)
    finally:
        db.close()


@app.post("/api/project/generate-story")
def generate_story(project_id: int | None = None, user: User = Depends(current_user), db: Session = Depends(db_session)):
    from threading import Thread
    project = get_or_create_project(db, user, project_id)
    if not project.tracks:
        raise HTTPException(400, "сначала загрузи хотя бы один трек")
    engine = _resolve_text_engine(user, project)
    _charge(db, user, COST_STORY, "сюжет проекта",
            kind="story", ref_type="project", ref_id=project.id,
            project_id=project.id)
    _text_charge(db, user, engine, f"сюжет проекта ({engine})",
                 ref_type="project", ref_id=project.id, project_id=project.id)
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
    engine = "gateway"
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
        engine = _text_engine_for(db, project)
        import asyncio
        res = asyncio.run(claude.generate_series_bible(
            idea=idea, engine=engine,
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
        _doc_status(project_id, _BIBLE_DOCS, "error", _err_text(e, 500))
        _text_refund(db, db.get(Project, project_id), engine,
                     "возврат: библия сезона не написалась")
        log.warning("библия сезона проекта %s упала: %s", project_id, e)
    finally:
        db.close()


def _run_ugc_persona(project_id: int, idea: str) -> None:
    db = SessionLocal()
    engine = "gateway"
    try:
        project = db.get(Project, project_id)
        if not project:
            return
        engine = _text_engine_for(db, project)
        import asyncio
        res = asyncio.run(claude.generate_ugc_persona(
            idea=idea, character_bible=project.character_bible, engine=engine,
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
        _doc_status(project_id, ("persona", "location"), "error", _err_text(e, 500))
        _text_refund(db, db.get(Project, project_id), engine,
                     "возврат: блогер не собрался")
        log.warning("персона блогера проекта %s упала: %s", project_id, e)
    finally:
        db.close()


def _run_mockup_brandbook(project_id: int, idea: str) -> None:
    db = SessionLocal()
    engine = "gateway"
    try:
        project = db.get(Project, project_id)
        if not project:
            return
        prev = _find_doc(db, project_id, "brandbook")
        engine = _text_engine_for(db, project)
        import asyncio
        res = asyncio.run(claude.generate_brandbook(
            idea=idea, brand_note=prev.body if prev else "", engine=engine,
        ))
        _put_doc(db, project_id, "brandbook", title=str(res.get("name") or ""),
                 body=str(res.get("brandbook") or ""), status="", error="", position=1)
        log.info("фирменный мир готов для проекта %s", project_id)
    except Exception as e:  # noqa: BLE001
        db.rollback()
        _doc_status(project_id, ("brandbook",), "error", _err_text(e, 500))
        _text_refund(db, db.get(Project, project_id), engine,
                     "возврат: фирменный мир не написался")
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
    text_engine = _resolve_text_engine(user, project)
    if catalog == "series":
        episodes = max(2, min(24, int(body.get("episodes") or 8)))
        _charge(db, user, COST_STORY, f"библия сезона проекта {project.id}",
                kind="story", ref_type="project", ref_id=project.id,
                project_id=project.id)
        _text_charge(db, user, text_engine,
                     f"библия сезона проекта {project.id} ({text_engine})",
                     ref_type="project", ref_id=project.id, project_id=project.id)
        _doc_status(project.id, _BIBLE_DOCS, "queued")
        Thread(target=_run_series_bible, args=(project.id, idea, episodes),
               daemon=True).start()
        return {"ok": True, "episodes": episodes}
    if catalog == "ugc":
        _charge(db, user, COST_STORY, f"персона блогера проекта {project.id}",
                kind="story", ref_type="project", ref_id=project.id,
                project_id=project.id)
        _text_charge(db, user, text_engine, f"блогер проекта {project.id} ({text_engine})",
                     ref_type="project", ref_id=project.id, project_id=project.id)
        _doc_status(project.id, ("persona", "location"), "queued")
        Thread(target=_run_ugc_persona, args=(project.id, idea), daemon=True).start()
        return {"ok": True}
    if catalog == "mockup":
        _charge(db, user, COST_STORY, f"фирменный мир проекта {project.id}",
                kind="story", ref_type="project", ref_id=project.id,
                project_id=project.id)
        _text_charge(db, user, text_engine,
                     f"фирменный мир проекта {project.id} ({text_engine})",
                     ref_type="project", ref_id=project.id, project_id=project.id)
        _doc_status(project.id, ("brandbook",), "queued")
        Thread(target=_run_mockup_brandbook, args=(project.id, idea), daemon=True).start()
        return {"ok": True}
    raise HTTPException(400, "у клипа сюжет генерится кнопкой «сюжет проекта»")


# ─────────────── поэпизодный план и создание серий ───────────────

def _run_beatsheet(project_id: int, episodes: int) -> None:
    db = SessionLocal()
    engine = "gateway"
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
        engine = _text_engine_for(db, project)
        import asyncio
        res = asyncio.run(claude.generate_beatsheet(
            engine=engine,
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
        _doc_status(project_id, ("beatsheet",), "error", _err_text(e, 500))
        _text_refund(db, db.get(Project, project_id), engine,
                     "возврат: поэпизодный план не написался")
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
    text_engine = _resolve_text_engine(user, project)
    _text_charge(db, user, text_engine,
                 f"поэпизодный план проекта {project.id} ({text_engine})",
                 ref_type="project", ref_id=project.id, project_id=project.id)
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
    # ЕДИНЫЙ СТИЛЬ СЕЗОНА: новая серия жёстко наследует стиль первой серии
    # со стилем — сериал не имеет права менять картинку между сериями.
    donor = next((t for t in sorted(project.tracks, key=lambda x: x.position)
                  if (t.style or "").strip() or (t.style_keys or "").strip()), None)
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
            style=donor.style if donor else "",
            style_keys=donor.style_keys if donor else "",
            style_extra=donor.style_extra if donor else "",
            image_engine=donor.image_engine if donor else "",
            video_engine=donor.video_engine if donor else "",
        ))
        made += 1
    db.commit()
    return {"ok": True, "created": made}


# ─────────────── сценарий одной серии ───────────────

def _run_episode_script(track_id: int) -> None:
    db = SessionLocal()
    engine = "gateway"
    project = None
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
        engine = _text_engine_for(db, project, track)
        import asyncio
        res = asyncio.run(claude.generate_episode_script(
            engine=engine,
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
            _doc_status(tr.project_id, ("script",), "error", _err_text(e, 500), track_id)
            _text_refund(db, tr.project, engine, "возврат: сценарий серии не написался",
                         ref_type="track", ref_id=tr.id)
        log.warning("сценарий серии %s упал: %s", track_id, _err_text(e))
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
    _text_engine = _resolve_text_engine(user, track.project, track)
    _text_charge(db, user, _text_engine, f"сценарий серии {track.id} ({_text_engine})",
                 ref_type="track", ref_id=track.id, track_id=track.id,
                 project_id=track.project_id)
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
        # ОБОРВАННУЮ ЗАГРУЗКУ НЕ ЗАПИСЫВАЕМ. Раньше сюда падало что угодно,
        # включая ноль байт: файл на диске в четыре байта, запись в базе
        # осталась, длительность подставилась дефолтная — и плеер молча
        # показывал «Ошибка», а причину увидеть было негде.
        if len(data) < MIN_AUDIO_BYTES:
            raise HTTPException(400, "файл дорожки не долетел или пуст — загрузи ещё раз")
        with open(path, "wb") as f:
            f.write(data)
        dur = _ffprobe_duration(path)
        if dur <= 0:
            os.remove(path)
            raise HTTPException(400, "это не читается как аудио — проверь формат файла")
        track.audio_filename = fname
        _reg_file(db, fname, project.owner_id, kind="audio",
                  project_id=project.id, track_id=track.id)
        track.audio_duration_sec = dur
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
    # ЗАМЕТКА РЕЖИССЁРА пишется здесь, и это единственное место. До базы промтов
    # её ставили только генерация сюжета и сид каркаса — то есть применить
    # сценарий из каталога было нечем, а заводить ради этого пишущий роут в
    # самом каталоге значило бы дать одному полю второго хозяина.
    if "director_note" in body:
        track.director_note = str(body["director_note"] or "")[:4000]
    if "film_grain" in body:
        track.film_grain = bool(body["film_grain"])
    if "random_cast" in body:
        track.random_cast = bool(body["random_cast"])
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
        _log_change(db, user, track.project_id, "track", track.id,
                    "video_engine", track.video_engine, body.get("video_engine"))
        want = str(body["video_engine"] or "").strip()
        track.video_engine = want if want in mediagen.VIDEO_ENGINES else ""
    if "image_engine" in body:
        _log_change(db, user, track.project_id, "track", track.id,
                    "image_engine", track.image_engine, body.get("image_engine"))
        want = str(body["image_engine"] or "").strip()
        track.image_engine = want if want in mediagen.IMAGE_ENGINES else ""
    if "text_engine" in body:
        want = str(body["text_engine"] or "").strip()
        track.text_engine = want if want in textgen.TEXT_ENGINES else ""
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

# ═══════════════ НАЛОЖЕНИЕ АДМИНКИ ПОВЕРХ КАТАЛОГА СТИЛЕЙ ═══════════════
# Каталог живёт в backend/prompts_catalog.py (код, версионируется, летит
# деплоем), правки владельца — в базе (том /data, переживают пересборку).
# Здесь единственное место, где одно накладывается на другое.

def _json(value: str, default):
    try:
        out = json.loads(value or "")
    except (ValueError, TypeError):
        return default
    return out if isinstance(out, type(default)) else default


def _style_asset_url(fname: str) -> str:
    return f"/style-assets/{fname}" if fname else ""


def style_overlay_data(db: Session) -> dict:
    """Собрать наложение из базы. Отдельно от применения — чтобы админка
    могла ПРОВЕРИТЬ кандидата (flush без commit) до того, как он станет
    правдой для витрины."""
    data: dict[str, dict] = {}
    for row in db.query(StyleOverride).all():
        item: dict = {"enabled": bool(row.enabled)}
        for field, col in (("label", row.label_json), ("desc", row.desc_json),
                           ("gain", row.gain_json), ("music", row.music_json),
                           ("tempo", row.tempo_json)):
            val = _json(col, {})
            if val:
                item[field] = val
        for field, col in (("tags", row.tags_json), ("mix_with", row.mix_with_json),
                           ("avoid_mix", row.avoid_mix_json),
                           ("engines", row.engines_json)):
            val = _json(col, [])
            if val:
                item[field] = val
        for field, col in (("group", row.group), ("tier", row.tier),
                           ("prompt_class", row.prompt_class),
                           ("mix_role", row.mix_role),
                           ("prompt", row.prompt), ("story_base", row.story_base)):
            if (col or "").strip():
                item[field] = col
        structure = _json(row.structure_json, {})
        if structure:
            item["structure"] = structure
        data[row.key] = item
    # Файлы: витрина отдельно, генерация отдельно. Смешивать нельзя —
    # постер карточки в промпт кадра не попадает и попадать не должен.
    for row in (db.query(StyleAsset)
                .order_by(StyleAsset.style_key, StyleAsset.position, StyleAsset.id)
                .all()):
        item = data.setdefault(row.style_key, {"enabled": True})
        if row.kind == "poster":
            item.setdefault("media", {})["poster"] = _style_asset_url(row.filename)
        elif row.kind == "loop":
            item.setdefault("media", {})["loop"] = _style_asset_url(row.filename)
        elif row.kind == "shot":
            media = item.setdefault("media", {})
            media.setdefault("shots", []).append(_style_asset_url(row.filename))
        elif row.kind == "ref" and row.in_generation:
            item.setdefault("gen_refs", []).append(row.filename)
    return data


def reload_style_overlay(db: Session | None = None) -> int:
    """Перечитать правки стилей из базы в каталог.

    Зовётся на старте и ПОСЛЕ КАЖДОГО сохранения в админке. Не на каждый
    кадр: кадров тысячи, а API-контейнер один, и инвалидация тривиальна."""
    own = db is None
    db = db or SessionLocal()
    try:
        data = style_overlay_data(db)
        prompts_catalog.set_overlay(data)
        problems = prompts_catalog.validate()
        if problems:
            # Не падаем: сервис обязан подняться. Но молчать нельзя —
            # битый каталог это битая витрина и битые промпты.
            log.warning("каталог стилей с наложением: %s проблем — %s",
                        len(problems), "; ".join(problems[:5]))
        return len(data)
    except Exception as e:  # noqa: BLE001
        log.warning("наложение стилей не загрузилось: %s", str(e)[:200])
        return 0
    finally:
        if own:
            db.close()


#: Ключи app_settings, в которых лежат наложения каталогов. Именованные
#: константы, а не строки по коду: опечатка в ключе выглядит как «админка не
#: сохраняет», и искать её пришлось бы в двух файлах.
PROMPTS_OVERLAY_KEY = "prompts_overlay"
MOCKUP_OVERLAY_KEY = "mockup_overlay"


def _overlay_setting(db: Session, key: str) -> dict:
    row = db.get(AppSetting, key)
    if not row or not (row.value or "").strip():
        return {}
    try:
        data = json.loads(row.value)
    except Exception:  # noqa: BLE001
        log.warning("наложение %s не разбирается как JSON", key)
        return {}
    return data if isinstance(data, dict) else {}


def _overlay_setting_save(db: Session, key: str, data: dict) -> None:
    blob = json.dumps(data, ensure_ascii=False)
    row = db.get(AppSetting, key)
    if row:
        row.value = blob
    else:
        db.add(AppSetting(key=key, value=blob))
    db.commit()


def reload_prompts_overlay(db: Session | None = None) -> int:
    """Перечитать правки слоёв промтов (сценарии/сцены/движение/свет).

    Та же дисциплина, что у стилей: наложение живёт в базе, файл остаётся
    источником, инвалидация — на старте и после каждого сохранения."""
    own = db is None
    db = db or SessionLocal()
    try:
        data = _overlay_setting(db, PROMPTS_OVERLAY_KEY)
        prompts_library.set_library_overlay(data)
        return sum(len(v) for v in data.values() if isinstance(v, dict))
    except Exception as e:  # noqa: BLE001
        log.warning("наложение промтов не загрузилось: %s", str(e)[:200])
        return 0
    finally:
        if own:
            db.close()


def reload_mockup_overlay(db: Session | None = None) -> int:
    """Перечитать правки шаблонов мокапов."""
    own = db is None
    db = db or SessionLocal()
    try:
        data = _overlay_setting(db, MOCKUP_OVERLAY_KEY)
        mockup_catalog.set_overlay(data)
        return len(data)
    except Exception as e:  # noqa: BLE001
        log.warning("наложение мокапов не загрузилось: %s", str(e)[:200])
        return 0
    finally:
        if own:
            db.close()


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


# ───────────────────── приёмы: третий слой каталога ─────────────────────
# Стиль — «как выглядит клип», каркас — «что снимаем», приём — «как снята ОДНА
# сцена». Данные в backend/prompts_library.py.
#
# ЗДЕСЬ НЕТ ПИШУЩЕГО РОУТА, И ЭТО НЕ НЕДОДЕЛКА. Кнопка «применить» собирается
# из двух вызовов: POST .../apply СЧИТАЕТ готовое тело (и ничего не меняет), а
# записывает его обычный PATCH /api/scenes/{id}. Пишущий эндпоинт добавил бы
# третье место, где надо помнить список полей сцены, — и первым же расхождением
# сломал бы применение половины карточки. Побочная выгода важнее: человек видит
# подставленный текст на карточках ДО того, как потратит токены на кадры, и
# может отказаться, ничего не откатывая.

@app.get("/api/shots")
def api_shots(request: Request, lang: str = "", category: str = "",
              tier: str = "", style: str = "", db: Session = Depends(db_session)):
    """Витрина приёмов. Тексты промптов вкладываются только тем, кому их
    открывает тариф; остальным карточка приезжает с locked=True и БЕЗ полей
    first/last/motion."""
    user = _resolve_user(request, db)
    lg = _lang_of(request, lang)
    plan = _plan_of(user) if user else "free"
    return {
        "lang": lg,
        "categories": [
            {"key": c["key"], "label": c["label"].get(lg, c["label"]["en"]),
             "hint": c["hint"].get(lg, c["hint"]["en"])}
            for c in prompts_library.CATEGORIES
        ],
        "shots": prompts_library.public_shots(
            lang=lg, category=category, tier=tier, style=style,
            plan_id=plan, is_admin=bool(user and user.is_admin)),
    }


@app.get("/api/shots/{key}")
def api_shot(key: str, request: Request, lang: str = "",
             db: Session = Depends(db_session)):
    user = _resolve_user(request, db)
    card = prompts_library.public_shot(
        key, lang=_lang_of(request, lang),
        plan_id=_plan_of(user) if user else "free",
        is_admin=bool(user and user.is_admin))
    if not card:
        raise ApiError(404, "unknown_shot", f"Unknown shot: {key!r}")
    return card


@app.get("/api/packs")
def api_packs(request: Request, lang: str = "", db: Session = Depends(db_session)):
    """Наборы приёмов — «артефакт урока», то, что применяется одной кнопкой."""
    user = _resolve_user(request, db)
    return {"lang": _lang_of(request, lang),
            "packs": prompts_library.public_packs(
                lang=_lang_of(request, lang),
                plan_id=_plan_of(user) if user else "free",
                is_admin=bool(user and user.is_admin))}


@app.get("/api/packs/{key}")
def api_pack(key: str, request: Request, lang: str = "",
             db: Session = Depends(db_session)):
    user = _resolve_user(request, db)
    pack = prompts_library.public_pack(
        key, lang=_lang_of(request, lang),
        plan_id=_plan_of(user) if user else "free",
        is_admin=bool(user and user.is_admin))
    if not pack:
        raise ApiError(404, "unknown_pack", f"Unknown pack: {key!r}")
    return pack


@app.post("/api/shots/{key}/apply")
async def api_shot_apply(key: str, request: Request,
                         user: User = Depends(current_user),
                         db: Session = Depends(db_session)):
    """Один приём → готовое тело для PATCH /api/scenes/{id}.

    body: {slots: {"character": "...", ...}}

    Симметрично набору ниже и по той же причине: подстановка слотов живёт в
    ОДНОМ месте (prompts_library.scene_patch), а не повторяется в браузере.
    Повтори её на фронте — и первая же правка формулировки приёма разъедется
    с тем, что применяет урок.

    Замок проверяем здесь же: без него текст платной карточки утёк бы через
    подстановку в обход витрины."""
    shot = prompts_library._BY_KEY.get(key)
    if not shot:
        raise ApiError(404, "unknown_shot", f"Unknown shot: {key!r}")
    if not prompts_library.unlocked(shot["tier"], _plan_of(user),
                                    is_admin=user.is_admin):
        raise ApiError(403, "plan_required",
                       f"Shot {key!r} requires the {shot['tier']} plan")
    body = await request.json() if await request.body() else {}
    slots = body.get("slots") if isinstance(body.get("slots"), dict) else {}
    patch = prompts_library.scene_patch(key, slots)
    return {"shot": key, "scene": patch}


@app.post("/api/packs/{key}/apply")
async def api_pack_apply(key: str, request: Request,
                         user: User = Depends(current_user),
                         db: Session = Depends(db_session)):
    """Подставить слоты и вернуть ГОТОВЫЕ тела для PATCH /api/scenes/{id}.

    body: {slots: {"character": "...", ...}}

    Ничего не меняет — считает и отдаёт. Запись делает фронт, по одному PATCH
    на сцену, и это осознанно: человек видит применённый текст на карточках
    ДО того, как потратит очки на кадры, и может отказаться, ничего не откатывая.

    Замок проверяем здесь же: без этого текст платной карточки утёк бы через
    подстановку, минуя витрину."""
    pack = prompts_library._PACK_BY_KEY.get(key)
    if not pack:
        raise ApiError(404, "unknown_pack", f"Unknown pack: {key!r}")
    if not prompts_library.unlocked(pack["tier"], _plan_of(user),
                                    is_admin=user.is_admin):
        raise ApiError(403, "plan_required",
                       f"Pack {key!r} requires the {pack['tier']} plan")
    body = await request.json() if await request.body() else {}
    slots = body.get("slots") if isinstance(body.get("slots"), dict) else {}
    return {"pack": key, "preset": pack["preset"], "styles": list(pack["styles"]),
            "scenes": prompts_library.pack_patches(key, slots)}


# ═════════ БАЗА ПРОМТОВ: сценарии · сцены · движение · свет и цвет ═════════
# Четвёртый, пятый, шестой и седьмой слои каталога. Разница между ними — не
# размер карточки, а ЧТО ИМЕННО она заполняет:
#
#   сценарий — весь клип: сюжет проекта и режиссёрская заметка трека;
#   сцена    — один кадр: шесть полей сцены разом;
#   движение — motion_prompt и camera_move, ЗАМЕНЯЯ их;
#   свет     — хвост, ДОПИСЫВАЕМЫЙ в оба кадра.
#
# Стиль трека сюда не входит и входить не может: фирменные пятнадцать закрыты
# навсегда (см. prompts_catalog), наружу от них уходят подпись и описание.
#
# Пишущего роута здесь снова НЕТ, и по той же причине, что у приёмов выше:
# считает POST .../apply и POST /api/mix, а записывает обычный PATCH сцены,
# проекта или трека. Побочная выгода — та, которую владелец попросил прямо:
# человек видит «было → станет» ДО того, как что-то изменится.


def _groups(rows: list[dict], lg: str) -> list[dict]:
    """Группы слоя с локализованными подписью и подсказкой."""
    return [{"key": g["key"],
             "label": g["label"].get(lg, g["label"]["en"]),
             "hint": g["hint"].get(lg, g["hint"]["en"])} for g in rows]


def _lib_who(request: Request, db: Session, lang: str) -> tuple[str, str, bool]:
    """Язык, тариф и админский флаг — то, от чего зависит ЛЮБОЙ ответ каталога.
    Три строки, повторённые в девяти роутах, разъехались бы на первой правке."""
    user = _resolve_user(request, db)
    return (_lang_of(request, lang),
            _plan_of(user) if user else "free",
            bool(user and user.is_admin))


@app.get("/api/library")
def api_library(request: Request, lang: str = "", db: Session = Depends(db_session)):
    """ВСЯ база промтов одним ответом: четыре новых слоя плюс приёмы и наборы.

    Одним, а не шестью запросами, намеренно. Каталог открывают целиком и
    переключают вкладки мышкой: шесть round-trip'ов означали бы шесть
    состояний загрузки и пустую вкладку под курсором. Тексты уже отфильтрованы
    тарифом — закрытая карточка приезжает без first/last/motion вовсе."""
    lg, plan, adm = _lib_who(request, db, lang)
    kw = {"lang": lg, "plan_id": plan, "is_admin": adm}
    return {
        "lang": lg,
        "mix_rules": {
            "max_boards": prompts_library.MIX_RULES["max_boards"],
            "max_motions": prompts_library.MIX_RULES["max_motions"],
            "max_lights": prompts_library.MIX_RULES["max_lights"],
            "max_style_lights": prompts_library.MIX_RULES["max_style_lights"],
            "order": list(prompts_library.MIX_RULES["order"]),
            "note": prompts_library.MIX_RULES["note"].get(lg,
                    prompts_library.MIX_RULES["note"]["en"]),
        },
        "fields": list(prompts_library.MIX_FIELDS),
        "groups": {
            "boards": _groups(prompts_library.BOARD_GROUPS, lg),
            "motions": _groups(prompts_library.MOTION_GROUPS, lg),
            "cameras": _groups(prompts_library.CAMERA_GROUPS, lg),
            "lights": _groups(prompts_library.LIGHT_GROUPS, lg),
            "shots": _groups(prompts_library.CATEGORIES, lg),
        },
        "scripts": prompts_library.public_scripts(**kw),
        "boards": prompts_library.public_boards(**kw),
        "motions": _decorate_layer(prompts_library.public_motions(**kw), "motions"),
        "cameras": _decorate_cameras(prompts_library.public_cameras(**kw)),
        "lights": prompts_library.public_lights(**kw),
        # Приёмы и наборы — прежние слои. Они остаются в том же каталоге
        # отдельными группами: набор применяется на весь трек, приём на одну
        # сцену, и увести их из витрины значило бы спрятать артефакты уроков.
        "shots": prompts_library.public_shots(**kw),
        "packs": prompts_library.public_packs(**kw),
    }


@app.get("/api/scripts")
def api_scripts(request: Request, lang: str = "", cut: str = "", style: str = "",
                db: Session = Depends(db_session)):
    """Сценарные промты — каркас ВСЕГО клипа. Фильтры: темп монтажа и стиль."""
    lg, plan, adm = _lib_who(request, db, lang)
    return {"lang": lg,
            "scripts": prompts_library.public_scripts(
                lang=lg, cut=cut, style=style, plan_id=plan, is_admin=adm)}


@app.get("/api/scripts/{key}")
def api_script(key: str, request: Request, lang: str = "",
               db: Session = Depends(db_session)):
    lg, plan, adm = _lib_who(request, db, lang)
    card = prompts_library.public_script(key, lang=lg, plan_id=plan, is_admin=adm)
    if not card:
        raise ApiError(404, "unknown_script", f"Unknown script: {key!r}")
    return card


@app.get("/api/boards")
def api_boards(request: Request, lang: str = "", group: str = "", tier: str = "",
               style: str = "", db: Session = Depends(db_session)):
    """Раскадровочные промты — готовые сцены. Заполняют шесть полей кадра."""
    lg, plan, adm = _lib_who(request, db, lang)
    return {"lang": lg,
            "groups": _groups(prompts_library.BOARD_GROUPS, lg),
            "boards": prompts_library.public_boards(
                lang=lg, group=group, tier=tier, style=style,
                plan_id=plan, is_admin=adm)}


@app.get("/api/boards/{key}")
def api_board(key: str, request: Request, lang: str = "",
              db: Session = Depends(db_session)):
    lg, plan, adm = _lib_who(request, db, lang)
    card = prompts_library.public_board(key, lang=lg, plan_id=plan, is_admin=adm)
    if not card:
        raise ApiError(404, "unknown_board", f"Unknown board: {key!r}")
    return card


@app.get("/api/motions")
def api_motions(request: Request, lang: str = "", group: str = "",
                db: Session = Depends(db_session)):
    """Промты движения. Заменяют motion_prompt и camera_move целиком."""
    lg, plan, adm = _lib_who(request, db, lang)
    return {"lang": lg,
            "groups": _groups(prompts_library.MOTION_GROUPS, lg),
            "motions": _decorate_layer(prompts_library.public_motions(
                lang=lg, group=group, plan_id=plan, is_admin=adm), "motions")}


@app.get("/api/motions/{key}")
def api_motion(key: str, request: Request, lang: str = "",
               db: Session = Depends(db_session)):
    lg, plan, adm = _lib_who(request, db, lang)
    card = prompts_library.public_motion(key, lang=lg, plan_id=plan, is_admin=adm)
    if not card:
        raise ApiError(404, "unknown_motion", f"Unknown motion: {key!r}")
    return card


# ─────────────── превью карточек слоёв (камера и будущие пачки) ───────────────
# Общий магазин превью для ЛЮБОГО слоя каталога: ключ "layer:key" → имя файла.
# Отдельный от мокапов: у тех превью живут на id шаблона, здесь — на слой,
# чтобы будущие пачки («ракурсы», «свет», «стили») подключались без правок.

LAYER_PREVIEWS_FILE = os.path.join(UPLOAD_DIR, "layer_previews.json")


def _layer_previews() -> dict:
    try:
        with open(LAYER_PREVIEWS_FILE, encoding="utf-8") as f:
            v = json.load(f)
        return v if isinstance(v, dict) else {}
    except Exception:  # noqa: BLE001
        return {}


def _layer_previews_save(m: dict) -> None:
    tmp = LAYER_PREVIEWS_FILE + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(m, f, ensure_ascii=False)
    os.replace(tmp, LAYER_PREVIEWS_FILE)


def _preview_entry(v) -> tuple[str, list[str]]:
    """Запись превью: строка (одна картинка) или {"main":…, "all":[…]}."""
    if isinstance(v, dict):
        main = str(v.get("main") or "")
        gallery = [str(x) for x in (v.get("all") or []) if x]
        if main and main not in gallery:
            gallery.insert(0, main)
        return main, gallery
    return (str(v or ""), [str(v)] if v else [])


def _decorate_layer(cards: list[dict], layer: str) -> list[dict]:
    """Приклеить превью к карточкам ЛЮБОГО слоя каталога."""
    previews = _layer_previews()
    for c in cards:
        main, gallery = _preview_entry(previews.get(f"{layer}:{c['key']}"))
        c["preview_url"] = f"/api/media/{main}" if main else ""
        # Несколько кадров Тони на карточку: первый — главный, остальные
        # крутятся hover-сменой.
        c["preview_urls"] = [f"/api/media/{f}" for f in gallery]
    return cards


def _decorate_cameras(cards: list[dict]) -> list[dict]:
    return _decorate_layer(cards, "cameras")


@app.get("/api/cameras")
def api_cameras(request: Request, lang: str = "", group: str = "",
                db: Session = Depends(db_session)):
    """Камера-пресеты: законченные проезды. Пишутся в motion_prompt целиком."""
    lg, plan, adm = _lib_who(request, db, lang)
    return {"lang": lg,
            "groups": _groups(prompts_library.CAMERA_GROUPS, lg),
            "cameras": _decorate_cameras(prompts_library.public_cameras(
                lang=lg, group=group, plan_id=plan, is_admin=adm))}


@app.get("/api/cameras/{key}")
def api_camera(key: str, request: Request, lang: str = "",
               db: Session = Depends(db_session)):
    lg, plan, adm = _lib_who(request, db, lang)
    card = prompts_library.public_camera(key, lang=lg, plan_id=plan, is_admin=adm)
    if not card:
        raise ApiError(404, "unknown_camera", f"Unknown camera: {key!r}")
    return _decorate_cameras([card])[0]


# Нейтральные сцены превью камера-пресетов: дорога/каньон/город, без брендов.
CAMERA_PREVIEW_SCENES = {
    "slider_arc": "an empty coastal road at golden hour, a lone figure by the roadside, "
                  "layered foreground rocks and distant cliffs, strong depth",
    "vehicle_tracking": "a plain matte-grey car driving fast on a desert canyon road, "
                        "dust and motion blur along the asphalt",
    "drone_push_in": "a low aerial rush over a rocky canyon toward a lone figure standing "
                     "on a cliff edge, dramatic evening sky",
    "truck_left": "a city street at dusk, a figure walking past layered storefronts and "
                  "lampposts, deep parallax between planes",
    "helicopter_orbit": "a distant telephoto aerial view of a lone figure on a highrise "
                        "rooftop at dawn, compressed city skyline behind",
    "crane_down": "a steep top-down view of a lone figure in an empty stone plaza with "
                  "long morning shadows",
}


def generate_camera_previews(ids: list[str] | None = None,
                             engine: str = "chatgpt") -> dict:
    """Превью камера-пресетов бесплатным шлюзом — та же механика, что у
    превью мокапов: нейтральные сцены, без брендов и без токенов клиента."""
    db = SessionLocal()
    done, failed = [], []
    try:
        previews = _layer_previews()
        import asyncio
        for cam in prompts_library.CAMERAS:
            key = cam["key"]
            if previews.get(f"cameras:{key}"):
                continue
            if ids and "*" not in ids and key not in ids:
                continue
            scene = CAMERA_PREVIEW_SCENES.get(
                key, "an empty scenic road through a canyon at sunset")
            prompt = (f"Cinematic film still, vertical 3:4 composition: {scene}. "
                      f"Natural light, realistic photography, no logos, no brands, "
                      f"no text, no watermark.")
            try:
                mediagen.reset_task()
                res = asyncio.run(mediagen.generate_image_ex(
                    prompt, None, engine=engine, aspect="3:4"))
                fname = _save_image(res["data"], res["mime"], upscale=False)
                _reg_file(db, fname, None, kind="layer_preview")
                db.commit()
                previews = _layer_previews()
                previews[f"cameras:{key}"] = fname
                _layer_previews_save(previews)
                done.append(key)
            except Exception as e:  # noqa: BLE001
                failed.append(f"{key}: {_err_text(e, 120)}")
                log.warning("превью камеры %s не вышло: %s", key, e)
        return {"done": done, "failed": failed}
    finally:
        db.close()


@app.post("/api/admin/cameras/previews")
def admin_camera_previews(all: int = 0,  # noqa: A002
                          user: User = Depends(current_user)):
    if not user.is_admin:
        raise HTTPException(403, "только для админа")
    return generate_camera_previews(["*"] if all else None)


# Раскладка референсов Тони по пресетам: кластеры времени в именах
# hf_20260827_HHMMSS_*.png (гипотеза координатора; владелец поправит кликом
# в админке — главная картинка меняется загрузкой или выбором из галереи).
CAMERA_REFS_DIR = os.path.join(UPLOAD_DIR, "camera_refs")
CAMERA_REF_CLUSTERS = (
    ("crane_down",       135300, 135399),
    ("vehicle_tracking", 140200, 140599),
    ("drone_push_in",    141220, 141699),
    ("truck_left",       142200, 142699),
    ("slider_arc",       143100, 143399),
    ("helicopter_orbit", 143500, 143599),
)


# Смысловое соответствие «движение → кластер кадров Тони»: наезд ← пролёт
# дрона, кран/тилт ← спуск по секвойям, вбок ← проезд, облёт/зум ← вертолёт.
MOTION_REF_MAP = {
    "m_push_settle": "drone_push_in",
    "m_steadi_follow": "drone_push_in",
    "m_pull_open": "slider_arc",
    "m_rack_focus": "slider_arc",
    "m_truck_side": "truck_left",
    "m_pan_link": "truck_left",
    "m_pedestal_down": "crane_down",
    "m_crane_rise": "crane_down",
    "m_tilt_up": "crane_down",
    "m_arc_quarter": "helicopter_orbit",
    "m_handheld_drift": "helicopter_orbit",
    "m_dolly_zoom": "helicopter_orbit",
}


def _layer_preview_prompt(layer: str, card: dict) -> str:
    """Промпт превью карточки слоя — по СОБСТВЕННОМУ тексту записи."""
    if layer == "cameras" and card.get("key") in CAMERA_PREVIEW_SCENES:
        scene = CAMERA_PREVIEW_SCENES[card["key"]]
        return (f"Cinematic film still, vertical 3:4 composition: {scene}. "
                f"Natural light, realistic photography, no logos, no brands, "
                f"no text, no watermark.")
    bits = []
    cam = str(card.get("camera") or "").strip()
    if cam:
        bits.append(f"camera: {cam}")
    body = " ".join(str(card.get("text") or card.get("solo")
                        or card.get("add") or "").split())[:400]
    body = body.replace("{character}", "a lone person").replace(
        "{location}", "a quiet city street")
    if body:
        bits.append(body)
    return ("Cinematic film still, vertical 3:4, a neutral demonstration of "
            "this camera or motion technique: " + "; ".join(bits) +
            ". One ordinary person in a neutral urban or nature scene, natural "
            "light, no logos, no brands, no text, no watermark.")


def _generate_layer_preview(db: Session, layer: str, card: dict,
                            engine: str = "chatgpt") -> str:
    """Один кадр превью бесплатным шлюзом; возвращает имя файла."""
    import asyncio
    mediagen.reset_task()
    res = asyncio.run(mediagen.generate_image_ex(
        _layer_preview_prompt(layer, card), None, engine=engine, aspect="3:4"))
    fname = _save_image(res["data"], res["mime"], upscale=False)
    _reg_file(db, fname, None, kind="layer_preview")
    db.commit()
    return fname


def generate_layer_previews(db: Session, layer: str) -> dict:
    """Догенерить превью ВСЕМ карточкам слоя без превью (бесплатный шлюз).
    Зовётся из админ-роута и из CLI."""
    done, failed = [], []
    previews = _layer_previews()
    for card in prompts_library.layer_rows(layer):
        key = card["key"]
        if _preview_entry(previews.get(f"{layer}:{key}"))[1]:
            continue
        try:
            fname = _generate_layer_preview(db, layer, card)
            previews = _layer_previews()
            previews[f"{layer}:{key}"] = {"main": fname, "all": [fname]}
            _layer_previews_save(previews)
            done.append(key)
        except Exception as e:  # noqa: BLE001
            failed.append(f"{key}: {_err_text(e, 120)}")
            log.warning("превью %s/%s не вышло: %s", layer, key, e)
    return {"done": done, "failed": failed}


@app.post("/api/admin/prompts/{layer}/previews")
def admin_layer_previews_batch(layer: str, user: User = Depends(current_user),
                               db: Session = Depends(db_session)):
    if not user.is_admin:
        raise HTTPException(403, "только для админа")
    if layer not in prompts_library.LAYERS:
        raise HTTPException(404, "нет такого слоя")
    return generate_layer_previews(db, layer)


@app.post("/api/admin/prompts/{layer}/{key}/preview-generate")
def admin_layer_preview_generate(layer: str, key: str,
                                 user: User = Depends(current_user),
                                 db: Session = Depends(db_session)):
    """Сгенерировать превью ОДНОЙ карточки по её собственному промпту (⚡0)."""
    if not user.is_admin:
        raise HTTPException(403, "только для админа")
    if layer not in prompts_library.LAYERS:
        raise HTTPException(404, "нет такого слоя")
    card = next((r for r in prompts_library.layer_rows(layer)
                 if r["key"] == key), None)
    if not card:
        raise HTTPException(404, "нет такой карточки")
    try:
        fname = _generate_layer_preview(db, layer, card)
    except Exception as e:  # noqa: BLE001
        raise HTTPException(502, f"превью не вышло: {_err_text(e, 200)}")
    previews = _layer_previews()
    _m, gallery = _preview_entry(previews.get(f"{layer}:{key}"))
    gallery = [fname] + gallery
    previews[f"{layer}:{key}"] = {"main": fname, "all": gallery}
    _layer_previews_save(previews)
    return {"ok": True, "preview_url": f"/api/media/{fname}"}


def seed_camera_refs(db: Session) -> dict:
    """Разложить кадры Тони из camera_refs/ по пресетам как превью-галереи.

    Файлы КОПИРУЮТСЯ в корень хранилища (подпапки /api/media не отдаёт) и
    регистрируются; исходники в camera_refs/ остаются нетронутыми. Повторный
    запуск идемпотентен: уже назначенный пресет не трогаем. Зовётся из
    админ-роута и из CLI (docker exec … python3 -c)."""
    if not os.path.isdir(CAMERA_REFS_DIR):
        raise RuntimeError(f"нет папки {CAMERA_REFS_DIR}")
    previews = _layer_previews()
    out, unmatched = {}, []
    files = sorted(os.listdir(CAMERA_REFS_DIR))
    for name in files:
        if not name.lower().endswith(".png"):
            continue
        m = re.match(r"hf_\d{8}_(\d{6})", name)
        if not m:
            unmatched.append(name)
            continue
        stamp = int(m.group(1))
        preset = next((k for k, lo, hi in CAMERA_REF_CLUSTERS
                       if lo <= stamp <= hi), "")
        if not preset:
            unmatched.append(name)
            continue
        out.setdefault(preset, []).append(name)
    seeded = {}
    for preset, names in out.items():
        entry_main, gallery = _preview_entry(previews.get(f"cameras:{preset}"))
        if gallery:
            seeded[preset] = f"уже назначено ({len(gallery)})"
            continue
        copied = []
        for name in names:
            fname = f"lprev_{uuid.uuid4().hex}.png"
            shutil.copyfile(os.path.join(CAMERA_REFS_DIR, name),
                            os.path.join(UPLOAD_DIR, fname))
            _reg_file(db, fname, None, kind="layer_preview")
            copied.append(fname)
        db.commit()
        previews[f"cameras:{preset}"] = {"main": copied[0], "all": copied}
        seeded[preset] = len(copied)
    # Движения слоя «Движение» — те же кадры Тони по смысловому соответствию
    # (наезд ← дрон-пролёт, кран ← спуск по секвойям и т.д.). Файлы
    # ПЕРЕИСПОЛЬЗУЮТСЯ: один файл может быть превью нескольких карточек.
    for mkey, donor in MOTION_REF_MAP.items():
        m_main, m_gal = _preview_entry(previews.get(f"motions:{mkey}"))
        if m_gal:
            continue
        _dm, d_gal = _preview_entry(previews.get(f"cameras:{donor}"))
        if not d_gal:
            continue
        # Раздаём разные кадры донора разным движениям, по кругу.
        offset = sum(1 for k2, d2 in MOTION_REF_MAP.items()
                     if d2 == donor and k2 < mkey)
        pick = d_gal[offset % len(d_gal)]
        previews[f"motions:{mkey}"] = {"main": pick, "all": [pick]}
        seeded[f"motions:{mkey}"] = 1
    _layer_previews_save(previews)
    return {"ok": True, "seeded": seeded, "unmatched": unmatched}


@app.post("/api/admin/cameras/seed-refs")
def admin_camera_seed_refs(user: User = Depends(current_user),
                           db: Session = Depends(db_session)):
    if not user.is_admin:
        raise HTTPException(403, "только для админа")
    try:
        return seed_camera_refs(db)
    except RuntimeError as e:
        raise HTTPException(404, str(e))


@app.post("/api/admin/prompts/{layer}/{key}/preview-main")
async def admin_layer_preview_main(layer: str, key: str, request: Request,
                                   user: User = Depends(current_user)):
    """Назначить ГЛАВНОЙ одну из картинок галереи пресета (клик в админке)."""
    if not user.is_admin:
        raise HTTPException(403, "только для админа")
    body = await request.json()
    fname = os.path.basename(str(body.get("filename") or ""))
    previews = _layer_previews()
    main, gallery = _preview_entry(previews.get(f"{layer}:{key}"))
    if fname not in gallery:
        raise HTTPException(404, "такой картинки нет в галерее пресета")
    previews[f"{layer}:{key}"] = {
        "main": fname, "all": [fname] + [g for g in gallery if g != fname]}
    _layer_previews_save(previews)
    return {"ok": True, "preview_url": f"/api/media/{fname}"}


@app.post("/api/admin/prompts/{layer}/{key}/preview")
async def admin_layer_preview_upload(layer: str, key: str,
                                     file: UploadFile | None = None,
                                     user: User = Depends(current_user),
                                     db: Session = Depends(db_session)):
    """Своя картинка-превью на карточку ЛЮБОГО слоя каталога.

    Тони перезальёт референсы руками — из чата файлы не достать. Старый файл
    подчищается, ключ хранится как "layer:key" (см. _layer_previews)."""
    if not user.is_admin:
        raise HTTPException(403, "только для админа")
    if layer not in prompts_library.LAYERS:
        raise HTTPException(404, "нет такого слоя")
    key = re.sub(r"[^a-z0-9_]", "", (key or "").strip().lower())[:60]
    if not key:
        raise HTTPException(400, "плохой ключ")
    if file is None or not (file.filename or "").strip():
        raise HTTPException(400, "нет файла")
    ext = os.path.splitext(file.filename)[1].lower()
    if ext not in (".jpg", ".jpeg", ".png", ".webp"):
        raise HTTPException(400, f"не похоже на картинку: {ext or '?'}")
    data = await file.read()
    if len(data) > 15 * 1024 * 1024:
        raise HTTPException(413, "картинка больше 15 МБ")
    if len(data) < 500:
        raise HTTPException(400, "файл пустой")
    fname = f"lprev_{uuid.uuid4().hex}{ext}"
    with open(os.path.join(UPLOAD_DIR, fname), "wb") as fh:
        fh.write(data)
    _reg_file(db, fname, None, kind="layer_preview")
    db.commit()
    previews = _layer_previews()
    main, gallery = _preview_entry(previews.get(f"{layer}:{key}"))
    # Загруженная картинка становится ГЛАВНОЙ; прежние остаются в галерее —
    # референсы Тони не затираются заменой главной.
    gallery = [fname] + [g for g in gallery if g != fname]
    previews[f"{layer}:{key}"] = {"main": fname, "all": gallery}
    _layer_previews_save(previews)
    return {"ok": True, "preview_url": f"/api/media/{fname}",
            "preview_urls": [f"/api/media/{g}" for g in gallery]}


@app.get("/api/lights")
def api_lights(request: Request, lang: str = "", group: str = "",
               db: Session = Depends(db_session)):
    """Свет и цвет. Дописываются в конец обоих кадров, ничего не переписывая."""
    lg, plan, adm = _lib_who(request, db, lang)
    return {"lang": lg,
            "groups": _groups(prompts_library.LIGHT_GROUPS, lg),
            "lights": prompts_library.public_lights(
                lang=lg, group=group, plan_id=plan, is_admin=adm)}


@app.get("/api/lights/{key}")
def api_light(key: str, request: Request, lang: str = "",
              db: Session = Depends(db_session)):
    lg, plan, adm = _lib_who(request, db, lang)
    card = prompts_library.public_light(key, lang=lg, plan_id=plan, is_admin=adm)
    if not card:
        raise ApiError(404, "unknown_light", f"Unknown light: {key!r}")
    return card


# ──────────────────────────── миксование ────────────────────────────

def _mix_selection(body: dict) -> tuple[str, str, list[str]]:
    """Разобрать выбор из тела запроса и обрезать его по потолкам MIX_RULES.

    Обрезаем ЗДЕСЬ, а не в словаре: потолок — правило продукта («одно движение
    на сцену»), и нарушить его можно только запросом мимо витрины."""
    board = str(body.get("board") or "").strip()
    motion = str(body.get("motion") or "").strip()
    raw = body.get("lights")
    lights, seen = [], set()
    for k in (raw if isinstance(raw, list) else []):
        k = str(k or "").strip()
        if k and k not in seen:
            seen.add(k)
            lights.append(k)
    return board, motion, lights[:prompts_library.MIX_RULES["max_lights"]]


def _mix_gate(board: str, motion: str, lights: list[str], user: "User") -> None:
    """404 на несуществующую карточку, 403 на закрытую тарифом.

    Замок стоит ЗДЕСЬ, а не только на витрине: без этой проверки текст платной
    карточки уезжал бы наружу подстановкой, минуя список, где он закрыт."""
    plan, adm = _plan_of(user), bool(user.is_admin)
    checks = ([(board, prompts_library._BOARD_BY_KEY, "board")] if board else [])
    if motion:
        checks.append((motion, prompts_library._MOTION_BY_KEY, "motion"))
    checks += [(k, prompts_library._LIGHT_BY_KEY, "light") for k in lights]
    for key, table, kind in checks:
        card = table.get(key)
        if not card:
            raise ApiError(404, f"unknown_{kind}", f"Unknown {kind}: {key!r}")
        if not prompts_library.unlocked(card["tier"], plan, is_admin=adm):
            raise ApiError(403, "plan_required",
                           f"Card {key!r} requires the {card['tier']} plan")


@app.post("/api/mix/check")
async def api_mix_check(request: Request):
    """Только конфликты, без единой строки промпта.

    Отдельный роут и БЕЗ замка по тарифу — намеренно. Витрина зовёт его на
    каждое изменение сборки, а объяснение «камера стоит и камера едет не бывают
    в одном кадре» — это не товар, а причина, по которой человеку стоит
    передумать. Прятать её за тарифом значит продавать молчание."""
    body = await request.json() if await request.body() else {}
    board, motion, lights = _mix_selection(body)
    return {"conflicts": prompts_library.check_mix(board, motion, lights)}


@app.post("/api/mix")
async def api_mix(request: Request, user: User = Depends(current_user),
                  db: Session = Depends(db_session)):
    """Сборка «заготовка + движение + свет» → готовое тело PATCH /api/scenes/{id}.

    body: {board?, motion?, lights?: [], slots?: {}, engine?: "", scene_id?: int}

    НИЧЕГО НЕ ПИШЕТ. Если пришёл scene_id, сцена читается — и ответ несёт
    `before` рядом с `scene`: ровно то «что именно изменится», которое просил
    владелец. Без scene_id движение и свет всё равно собираются, просто
    накладывать их не на что, и свет приезжает отдельным полем `add`.

    Движок берём с кадра, потом с трека: у Grok нет последнего кадра и текст
    движения для него другой (`solo`), а MiniMax читает команду камеры только
    в квадратных скобках. Спросить об этом человека нельзя — он не обязан
    помнить, чем снимает этот конкретный кадр."""
    body = await request.json() if await request.body() else {}
    board, motion, lights = _mix_selection(body)
    if not (board or motion or lights):
        raise ApiError(400, "empty_mix", "Nothing selected")
    _mix_gate(board, motion, lights, user)

    slots = body.get("slots") if isinstance(body.get("slots"), dict) else {}
    engine = str(body.get("engine") or "").strip()
    base, before = None, None
    scene = None
    if body.get("scene_id"):
        scene = _own_scene(db, user, int(body["scene_id"]))
        base = {f: getattr(scene, f, "") for f in prompts_library.MIX_FIELDS}
        before = dict(base)
        if not engine:
            engine = scene.video_engine or scene.track.video_engine or ""
    if engine and engine not in prompts_library.ENGINE_KEYS:
        engine = ""

    out = prompts_library.mix_scene(
        board, motion=motion, lights=lights, slots=slots,
        lang=_lang_of(request), engine=engine, base=base)
    out["before"] = before
    out["scene_id"] = scene.id if scene else 0
    # Поля, которые сборка РЕАЛЬНО меняет: витрина подсвечивает только их, а не
    # все шесть. Показать «изменится shot_note», когда он тот же самый, — это
    # предупреждение, которому перестают верить.
    out["changed"] = sorted(k for k, v in out["scene"].items()
                            if not before or str(before.get(k) or "") != str(v or ""))
    return out


@app.post("/api/scripts/{key}/apply")
async def api_script_apply(key: str, request: Request,
                           user: User = Depends(current_user),
                           db: Session = Depends(db_session)):
    """Сценарий → сюжет проекта, заметка режиссёра и черновик раскадровки.

    body: {slots?: {}, engine?: ""}

    Считает и отдаёт, не пишет. Записывают три существующих роута, каждый свой
    кусок: PATCH /api/project (сюжет), PATCH /api/tracks/{id} (заметка),
    PATCH /api/scenes/{id} (кадры). Четвёртого места, знающего эти поля, здесь
    не появляется — именно поэтому переименование поля ломает один файл, а не
    четыре."""
    card = prompts_library._SCRIPT_BY_KEY.get(key)
    if not card:
        raise ApiError(404, "unknown_script", f"Unknown script: {key!r}")
    if not prompts_library.unlocked(card["tier"], _plan_of(user),
                                    is_admin=user.is_admin):
        raise ApiError(403, "plan_required",
                       f"Script {key!r} requires the {card['tier']} plan")
    body = await request.json() if await request.body() else {}
    slots = body.get("slots") if isinstance(body.get("slots"), dict) else {}
    engine = str(body.get("engine") or "").strip()
    if engine not in prompts_library.ENGINE_KEYS:
        engine = ""
    lg = _lang_of(request)
    seed = prompts_library.script_seed(key)
    boards = prompts_library.script_boards(key)
    return {
        "script": key,
        "story": seed["story"],
        "note": seed["note"],
        "preset": card["preset"],
        "styles": list(card["styles_fit"]),
        "boards": boards,
        # Порядок сцен — порядок актов. Он и есть единственное, чем сценарий
        # отличается от россыпи заготовок, поэтому патчи приезжают списком.
        "scenes": [prompts_library.board_patch(b, slots, lang=lg, engine=engine)
                   for b in boards],
    }


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
    _log_change(db, user, track.project_id, "track", track.id,
                "style_keys", track.style_keys,
                raw if isinstance(raw, str) else ",".join(raw or []))
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
    # ЛЕГАСИ-РАСКАДРОВКА: до смены стиля запоминаем, под какой стиль писаны
    # её тексты. Без этой строки правда теряется в момент смены чипов, и
    # рестайл потом решит, что переписывать нечего, — а в промптах остался
    # старый стиль, и человек снова увидит прежние картинки.
    if track.scenes and not (track.prompts_style_keys or "").strip():
        track.prompts_style_keys = ",".join(_track_style_keys(track))
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
    # Битую дорожку honestly называем битой: браузер на такой файл показывает
    # одно слово «Ошибка», и человеку неоткуда узнать, что файл не долетел.
    if os.path.getsize(path) < MIN_AUDIO_BYTES:
        raise HTTPException(422, "файл дорожки повреждён или не догрузился — загрузи его заново")
    # accept-ranges нужен перемотке: без него ползунок в плеере тащит файл
    # с нуля на каждый прыжок.
    return FileResponse(path, headers={"Accept-Ranges": "bytes"})


@app.post("/api/tracks/{track_id}/audio/trim")
async def trim_track_audio(track_id: int, request: Request,
                           user: User = Depends(current_user),
                           db: Session = Depends(db_session)):
    """Оставить в дорожке только выбранный кусок.

    Клип часто делают под припев, а не под всю песню. Резать в стороннем
    редакторе и загружать заново — лишний круг; здесь тот же результат в один
    приём. Прежний файл НЕ удаляем: раскадровка уже привязана к прежним
    секундам, и вернуться должно быть куда.
    """
    track = _own_track(db, user, track_id)
    if not track.audio_filename:
        raise HTTPException(404, "у трека нет дорожки")
    src = os.path.join(UPLOAD_DIR, track.audio_filename)
    if not os.path.exists(src):
        raise HTTPException(404, "файл дорожки отсутствует на диске")
    body = await request.json()
    try:
        start = max(0.0, float(body.get("start") or 0))
        end = float(body.get("end") or track.audio_duration_sec or 0)
    except (TypeError, ValueError):
        raise HTTPException(400, "начало и конец должны быть числами")
    if end - start < 1:
        raise HTTPException(400, "кусок короче секунды — резать нечего")
    ext = os.path.splitext(track.audio_filename)[1] or ".mp3"
    fname = f"{uuid.uuid4().hex}{ext}"
    dst = os.path.join(UPLOAD_DIR, fname)
    try:
        r = subprocess.run(
            ["ffmpeg", "-y", "-ss", str(start), "-t", str(round(end - start, 2)),
             "-i", src, "-vn", "-c:a", "libmp3lame", "-b:a", "192k", dst],
            capture_output=True, timeout=600)
    except (OSError, subprocess.SubprocessError) as e:
        raise HTTPException(500, f"не вышло нарезать дорожку: {str(e)[:120]}")
    if r.returncode != 0 or not os.path.exists(dst):
        raise HTTPException(500, f"ffmpeg не нарезал дорожку: {r.stderr.decode()[-200:]}")
    track.audio_filename = fname
    track.audio_duration_sec = _ffprobe_duration(dst)
    _reg_file(db, fname, track.project.owner_id, kind="audio",
              project_id=track.project_id, track_id=track.id)
    try:
        track.audio_profile = _audio_profile(dst, track.audio_duration_sec)
    except Exception as e:  # noqa: BLE001 — профиль не обязателен
        log.warning("профиль звука после нарезки не посчитался: %s", str(e)[:120])
    db.commit()
    # Кадры звучат отрезками прежней дорожки — перерезаем под новую.
    threading.Thread(target=_resync_scene_audio, args=(track.id,), daemon=True).start()
    db.refresh(track)
    return track_dict(track)


@app.post("/api/tracks/{track_id}/audio")
async def replace_track_audio(track_id: int, audio: UploadFile,
                              user: User = Depends(current_user),
                              db: Session = Depends(db_session)):
    """Заменить дорожку у существующего трека.

    Раньше дорожку можно было задать только при создании: чтобы поменять
    испорченный или не тот файл, приходилось заводить трек заново — вместе с
    раскадровкой и всеми оплаченными кадрами. Здесь меняется только звук,
    кадры и видео остаются на месте.
    """
    _guard_disk()
    track = _own_track(db, user, track_id)
    ext = os.path.splitext(audio.filename or "")[1] or ".mp3"
    data = await audio.read()
    if len(data) < MIN_AUDIO_BYTES:
        raise HTTPException(400, "файл дорожки не долетел или пуст — загрузи ещё раз")
    fname = f"{uuid.uuid4().hex}{ext}"
    path = os.path.join(UPLOAD_DIR, fname)
    with open(path, "wb") as f:
        f.write(data)
    dur = _ffprobe_duration(path)
    if dur <= 0:
        os.remove(path)
        raise HTTPException(400, "это не читается как аудио — проверь формат файла")
    track.audio_filename = fname
    track.audio_duration_sec = dur
    _reg_file(db, fname, track.project.owner_id, kind="audio",
              project_id=track.project_id, track_id=track.id)
    try:
        track.audio_profile = _audio_profile(path, dur)
    except Exception as e:  # noqa: BLE001 — профиль не обязателен
        log.warning("профиль звука не посчитался: %s", str(e)[:120])
    # Клип собран под прежний звук, а кадры звучат его отрезками.
    track.clip_status = ""
    track.clip_error = ""
    db.commit()
    threading.Thread(target=_resync_scene_audio, args=(track.id,), daemon=True).start()
    db.refresh(track)
    return track_dict(track)


# Площадки, где аудио отдаётся только через обход их правил. Пускать сюда
# ссылку на ролик значит встроить в сервис выкачивание чужих записей: жалобы
# правообладателей прилетят не пользователю, а на домен и на платёжный
# аккаунт. Поэтому отказ явный и с объяснением, а не молчаливая ошибка.
STREAM_HOSTS = (
    "youtube.com", "youtu.be", "music.youtube.com",
    "soundcloud.com", "spotify.com", "vk.com", "vkvideo.ru",
    "tiktok.com", "instagram.com", "rutube.ru", "yandex.ru",
)
AUDIO_URL_MAX_BYTES = 60 * 1024 * 1024   # 60 МБ — больше песни не бывает


def _fetch_stream_audio(url: str) -> str:
    """Аудио из ролика через yt-dlp → mp3 в хранилище.

    Берём лучший аудиопоток и жмём в mp3. Егресс к площадке идёт через ту же
    прокладку, что и остальной внешний трафик сервера (WARP/релей): без неё
    YouTube из РФ недоступен. Ошибки yt-dlp отдаём человеку как есть.
    """
    try:
        import yt_dlp  # noqa: PLC0415
    except ImportError:
        raise RuntimeError("загрузка из роликов не собрана: нет yt-dlp в образе")
    out_base = os.path.join(UPLOAD_DIR, f"yt_{uuid.uuid4().hex}")
    opts = {
        "format": "bestaudio/best",
        "outtmpl": out_base + ".%(ext)s",
        "noplaylist": True,
        "quiet": True,
        "no_warnings": True,
        "postprocessors": [{"key": "FFmpegExtractAudio",
                            "preferredcodec": "mp3", "preferredquality": "192"}],
    }
    proxy = os.environ.get("EGRESS_PROXY", "")
    if proxy:
        opts["proxy"] = proxy
    try:
        with yt_dlp.YoutubeDL(opts) as ydl:
            ydl.download([url])
    except Exception as e:  # noqa: BLE001 — текст ошибки полезен человеку
        raise RuntimeError(f"не вышло забрать звук из ролика: {str(e)[:200]}")
    mp3 = out_base + ".mp3"
    if not os.path.exists(mp3) or os.path.getsize(mp3) < MIN_AUDIO_BYTES:
        raise RuntimeError("ролик скачался, но звук не извлёкся")
    return os.path.basename(mp3)


@app.post("/api/tracks/{track_id}/audio/from-url")
async def track_audio_from_url(track_id: int, request: Request,
                               user: User = Depends(current_user),
                               db: Session = Depends(db_session)):
    """Забрать дорожку по ПРЯМОЙ ссылке на аудиофайл.

    Работает с тем, что лежит файлом: облако с прямой ссылкой, битмейкерская
    площадка, твой сайт, выгрузка от дистрибьютора. Ссылки на ролики и
    стриминг отбиваются — оттуда звук достаётся только в обход правил
    площадки, и делать это частью продукта нельзя.
    """
    _guard_disk()
    track = _own_track(db, user, track_id)
    body = await request.json()
    url = str(body.get("url") or "").strip()
    if not url.lower().startswith(("http://", "https://")):
        raise HTTPException(400, "нужна ссылка, начинающаяся с http:// или https://")
    host = url.split("//", 1)[1].split("/", 1)[0].lower().lstrip("www.")
    if any(host == h or host.endswith("." + h) for h in STREAM_HOSTS):
        # По решению владельца площадки-стриминги скачиваются через yt-dlp:
        # берём ЛУЧШЕЕ аудио и перекодируем в mp3. Ответственность за права на
        # ролик — на том, кто вставил ссылку (это его сервис и его контент).
        try:
            fname = _fetch_stream_audio(url)
        except RuntimeError as e:
            raise HTTPException(400, str(e))
        dur = _ffprobe_duration(os.path.join(UPLOAD_DIR, fname))
        if dur <= 0:
            _remove_media(fname)
            raise HTTPException(400, "звук не извлёкся из ролика")
        track.audio_filename = fname
        track.audio_duration_sec = dur
        _reg_file(db, fname, track.project.owner_id, kind="audio",
                  project_id=track.project_id, track_id=track.id)
        track.clip_status = ""; track.clip_error = ""
        db.commit()
        threading.Thread(target=_resync_scene_audio, args=(track.id,), daemon=True).start()
        db.refresh(track)
        return track_dict(track)
    import httpx as _httpx  # noqa: PLC0415 — так же, как в остальных местах файла

    try:
        async with _httpx.AsyncClient(timeout=_httpx.Timeout(180.0, connect=15.0),
                                      follow_redirects=True) as client:
            r = await client.get(url)
    except Exception as e:  # noqa: BLE001 — недоступный адрес не должен ронять сервис
        raise HTTPException(400, f"не вышло скачать: {str(e)[:150]}")
    if r.status_code != 200:
        raise HTTPException(400, f"ссылка ответила {r.status_code} — файл недоступен")
    data = r.content or b""
    if len(data) < MIN_AUDIO_BYTES:
        raise HTTPException(400, "по ссылке пусто — это не файл дорожки")
    if len(data) > AUDIO_URL_MAX_BYTES:
        raise HTTPException(400, "файл больше 60 МБ — загрузи его вручную")
    # Расширение берём из адреса, а не из типа ответа: облака отдают
    # application/octet-stream на что угодно, и по нему ничего не понять.
    ext = os.path.splitext(url.split("?", 1)[0])[1].lower()
    if ext not in (".mp3", ".wav", ".m4a", ".aac", ".ogg", ".flac"):
        ext = ".mp3"
    fname = f"{uuid.uuid4().hex}{ext}"
    path = os.path.join(UPLOAD_DIR, fname)
    with open(path, "wb") as f:
        f.write(data)
    dur = _ffprobe_duration(path)
    if dur <= 0:
        os.remove(path)
        raise HTTPException(
            400, "по ссылке лежит не аудио — вероятно, это страница, а не файл")
    track.audio_filename = fname
    track.audio_duration_sec = dur
    _reg_file(db, fname, track.project.owner_id, kind="audio",
              project_id=track.project_id, track_id=track.id)
    try:
        track.audio_profile = _audio_profile(path, dur)
    except Exception as e:  # noqa: BLE001 — профиль не обязателен
        log.warning("профиль звука не посчитался: %s", str(e)[:120])
    track.clip_status = ""
    track.clip_error = ""
    db.commit()
    threading.Thread(target=_resync_scene_audio, args=(track.id,), daemon=True).start()
    db.refresh(track)
    return track_dict(track)


@app.get("/api/tracks/{track_id}/waveform")
def get_waveform(track_id: int, points: int = 900,
                 user: User = Depends(current_user), db: Session = Depends(db_session)):
    """Огибающая громкости дорожки — форма волны для плеера.

    Считаем на сервере: тянуть в браузер десятимегабайтный mp3 ради картинки
    волны значит ждать её на каждом открытии проекта. Результат кэшируем
    рядом с файлом — форма волны у дорожки не меняется.
    """
    track = _own_track(db, user, track_id)
    if not track.audio_filename:
        raise HTTPException(404, "аудио не найдено")
    src = os.path.join(UPLOAD_DIR, track.audio_filename)
    if not os.path.exists(src) or os.path.getsize(src) < MIN_AUDIO_BYTES:
        raise HTTPException(422, "дорожка повреждена или не догрузилась")
    points = max(100, min(2000, int(points or 900)))
    cache = os.path.join(THUMB_DIR, f"wave_{track.audio_filename}.{points}.json")
    if os.path.exists(cache) and os.path.getmtime(cache) >= os.path.getmtime(src):
        try:
            with open(cache, encoding="utf-8") as fh:
                return json.load(fh)
        except ValueError:
            pass  # кэш побился — считаем заново
    try:
        r = subprocess.run(
            ["ffmpeg", "-v", "error", "-i", src, "-ac", "1", "-ar", "8000",
             "-f", "s16le", "-"],
            capture_output=True, timeout=300)
    except (OSError, subprocess.SubprocessError) as e:
        raise HTTPException(500, f"не вышло прочитать дорожку: {str(e)[:120]}")
    raw = r.stdout or b""
    if not raw:
        raise HTTPException(422, "в дорожке нет звука")
    import array
    samples = array.array("h")
    samples.frombytes(raw[:len(raw) - (len(raw) % 2)])
    step = max(1, len(samples) // points)
    peaks = []
    for i in range(0, len(samples), step):
        chunk = samples[i:i + step]
        peaks.append(round(max(abs(min(chunk)), abs(max(chunk))) / 32768, 3) if chunk else 0)
    peaks = peaks[:points]
    data = {"peaks": peaks, "duration": float(track.audio_duration_sec or 0)}
    try:
        with open(cache, "w", encoding="utf-8") as fh:
            json.dump(data, fh)
    except OSError as e:  # noqa: BLE001 — без кэша просто медленнее
        log.warning("кэш формы волны не записался: %s", str(e)[:120])
    return data


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


def _scenes_coverage(track: Track) -> dict:
    """Сколько секунд трека реально покрыто раскадровкой.

    Частая и незаметная беда: дорожка на две минуты, а сцен сгенерировалось
    на тридцать секунд — модель отдала меньше, чем просили, или человек
    удалил лишние. Клип при этом собирается коротким, и понять почему
    неоткуда. Считаем честно и отдаём наверх."""
    total = int(_track_duration(track) or 0)
    covered = sum(int(s.duration_sec or 0) for s in track.scenes)
    left = max(0, total - covered)
    # Средняя длина сцены этого объекта — по ней прикидываем, сколько ещё нужно.
    mode = _mode_of(track.project)
    lo, hi = mode["scenes"]["slot"]
    slot = max(1, int(round((lo + hi) / 2)))
    return {
        "total_sec": total,
        "covered_sec": covered,
        "left_sec": left,
        "slot_sec": slot,
        "suggest": int(-(-left // slot)) if left > 0 else 0,
        "full": left <= max(2, slot // 2),
    }


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


def _scenes_for_series(db: Session, track: Track, engine: str = "") -> dict:
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
    # Стиль серии: свой, иначе — стиль первой серии проекта (единый сезон).
    style = (track.style or "").strip() or next(
        (t.style for t in sorted(project.tracks, key=lambda x: x.position)
         if (t.style or "").strip()), "")
    return asyncio.run(claude.generate_series_scenes(
        engine=engine,
        script=script_doc.body,
        character_bible=project.character_bible,
        episode_beats=formats.beats_block(catalog, key, "episode_beats"),
        style=style,
        duration_sec=_track_duration(track),
        rules=formats.rules(catalog),
        characters=characters_payload(project),
        comment=track.comment,
    ))


def _scenes_for_ugc(db: Session, track: Track, engine: str = "") -> dict:
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
        engine=engine,
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


def _scenes_for_mockup(db: Session, track: Track, engine: str = "") -> dict:
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
        engine=engine,
        brandbook=brand_doc.body if brand_doc else "",
        brief=(brief_doc.body if brief_doc else "") or track.comment,
        shots_block=formats.shots_block(key),
        set_note=formats.seed(key).get("note", ""),
        style=track.style,
        shots=shots,
        rules=formats.rules(catalog),
        comment=track.comment,
    ))


def _cast_plan_text(characters: list[dict], n_scenes: int) -> str:
    """Случайная равномерная расстановка персонажей по кадрам — ДЛЯ ПРОМПТА.

    Модель, получив только словесные квоты, всё равно тащит главного во все
    кадры. Числа против слов: сервер генерит случайное назначение (главный
    ≤50% кадров, каждый — минимум своя квота, один состав подряд ≤2 кадров)
    и передаёт его как рекомендацию."""
    names = [str(c.get("name") or "").strip() for c in characters]
    names = [x for x in names if x]
    if len(names) < 3 or n_scenes < 4:
        return ""
    rnd = random.Random(secrets.randbits(32))
    main = next((str(c["name"]).strip() for c in characters if c.get("is_main")),
                names[0])
    others = [x for x in names if x != main]
    quota = max(1, -(-n_scenes // len(names)) - 1)
    main_cap = max(quota, n_scenes // 2)
    counts = {x: 0 for x in names}
    plans: list[list[str]] = []
    deck: list[str] = []
    prev, prev_run = None, 0
    for i in range(n_scenes):
        size = _pick_cast_size(rnd, len(names))
        cast: list[str] = []
        if size > 0:
            if counts[main] < main_cap and rnd.random() < 0.5:
                cast.append(main)
            guard = 0
            while len(cast) < size and guard < 20:
                guard += 1
                if not deck:
                    deck = others[:]
                    rnd.shuffle(deck)
                pick = deck.pop()
                if pick not in cast:
                    cast.append(pick)
        key = tuple(sorted(cast))
        if key == prev and prev_run >= 2:
            least = min(names, key=lambda x: counts[x])
            cast = [least] if least not in cast else [main, least]
            key = tuple(sorted(cast))
        prev_run = prev_run + 1 if key == prev else 1
        prev = key
        for x in cast:
            counts[x] += 1
        plans.append(cast)
    # Добор квоты: недопредставленные встают вместо самых частых в одиночных
    # кадрах середины (первый и последний кадры — режиссёрские, не трогаем).
    for name in names:
        tries = 0
        while counts[name] < quota and tries < n_scenes:
            tries += 1
            i = rnd.randrange(1, n_scenes - 1)
            cast = plans[i]
            if name in cast or len(cast) != 1:
                continue
            old = cast[0]
            if counts[old] <= quota:
                continue
            counts[old] -= 1
            counts[name] += 1
            plans[i] = [name]
    lines = [f"кадр {i + 1}: " + (" + ".join(c) if c else "(без людей)")
             for i, c in enumerate(plans)]
    return "\n".join(lines)


def _rebalance_cast(scenes: list[dict], characters: list[dict]) -> None:
    """Страховка ПОСЛЕ генерации: модель, несмотря на квоты в промпте, тащит
    одного героя через раскадровку.

    Два прохода, оба без обращения к модели:
      1. персонаж, занявший БОЛЬШЕ 55% кадров, ЗАМЕНЯЕТСЯ недопредставленными
         в лишних одиночных кадрах (первый, последний и кадры-взаимодействия
         с 2+ героями не трогаются): имя меняется в characters и в текстах,
         внешность нового дописывается из карточки, в shot_note — пометка;
      2. ни разу не использованные персонажи вписываются в кадры с
         доминирующим составом (старое поведение)."""
    names = [str(c.get("name") or "").strip() for c in characters]
    names = [n for n in names if n]
    if len(names) < 2 or len(scenes) < 4:
        return
    by_name = {str(c.get("name") or "").strip(): c for c in characters}

    def cast_of(sc: dict) -> list[str]:
        return [str(n).strip() for n in (sc.get("characters") or []) if str(n).strip()]

    def desc_of(name: str) -> str:
        return " ".join(str((by_name.get(name) or {}).get("description") or "").split())[:220]

    def swap_in_scene(sc: dict, old: str, new: str) -> None:
        sc["characters"] = [new if x == old else x for x in cast_of(sc)]
        pat = re.compile(rf"(?<![\w]){re.escape(old)}(?![\w])")
        for field in ("image_prompt", "image_prompt_last", "motion_prompt"):
            text = str(sc.get(field) or "")
            if text:
                sc[field] = pat.sub(new, text)
        # Внешность в тексте могла остаться от прежнего героя — дописываем
        # новую из карточки: рендер слушает конкретное описание.
        d = desc_of(new)
        base = str(sc.get("image_prompt") or "").rstrip()
        if base and d and d[:60] not in base:
            sc["image_prompt"] = f"{base} In this shot {new} looks like this: {d}."
        note = str(sc.get("shot_note") or "").rstrip()
        sc["shot_note"] = (note + " · состав перераспределён").strip(" ·")

    # ── проход 1: перекос на одного персонажа (>55% кадров) ──
    counts = {n: 0 for n in names}
    for sc in scenes:
        for n in set(cast_of(sc)):
            if n in counts:
                counts[n] += 1
    cap = max(2, int(0.55 * len(scenes)))
    replaced = 0
    for heavy in sorted(counts, key=lambda x: -counts[x]):
        if counts[heavy] <= cap:
            break
        # Кандидаты на замену: одиночные кадры с этим героем, кроме краёв.
        spots = [i for i in range(1, len(scenes) - 1)
                 if cast_of(scenes[i]) == [heavy]]
        under = sorted((n for n in names if n != heavy), key=lambda x: counts[x])
        for i in spots:
            if counts[heavy] <= cap or not under:
                break
            new = under[0]
            swap_in_scene(scenes[i], heavy, new)
            counts[heavy] -= 1
            counts[new] += 1
            replaced += 1
            under.sort(key=lambda x: counts[x])
    # ── проход 2: ни разу не использованные — вписать в доминирующий состав ──
    unused = [n for n in names if counts[n] == 0]
    injected = 0
    if unused:
        tallies: dict = {}
        for sc in scenes:
            key = tuple(sorted(cast_of(sc)))
            tallies[key] = tallies.get(key, 0) + 1
        dominant = max(tallies, key=lambda k: tallies[k]) if tallies else ()
        slots = [i for i in range(1, len(scenes) - 1)
                 if tuple(sorted(cast_of(scenes[i]))) == dominant]
        taken: set = set()
        for k, name in enumerate(unused):
            d = desc_of(name)
            for j in range(2):
                idx = (len(slots) * (2 * k + j + 1)) // (2 * len(unused) + 1) \
                    if slots else -1
                if idx < 0:
                    break
                i = slots[min(idx, len(slots) - 1)]
                if i in taken:
                    i = next((x for x in slots if x not in taken), None)
                    if i is None:
                        break
                taken.add(i)
                sc = scenes[i]
                cast = cast_of(sc)
                if name not in cast:
                    cast.append(name)
                sc["characters"] = cast
                extra = (f" Also present in the frame: {name}"
                         + (f" — {d}" if d else "") + ".")
                for field in ("image_prompt", "image_prompt_last"):
                    text = str(sc.get(field) or "").rstrip()
                    if text and name not in text:
                        sc[field] = text + extra
                injected += 1
    if replaced or injected:
        log.info("страховка состава: замен %s, вписано забытых %s (кадров %s)",
                 replaced, injected, len(scenes))


def _run_scene_generation(track_id: int) -> None:
    db = SessionLocal()
    engine = "gateway"
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
        engine = _text_engine_for(db, project, track)
        import asyncio
        if catalog == "series":
            result = _scenes_for_series(db, track, engine)
        elif catalog == "ugc":
            result = _scenes_for_ugc(db, track, engine)
        elif catalog == "mockup":
            result = _scenes_for_mockup(db, track, engine)
        else:
            chars_payload = characters_payload(project)
            dur = track.audio_duration_sec or 180
            # Случайная равномерная расстановка — сервером, числами: модель
            # получает готовое назначение «кадр → имена» и держится его.
            plan = _cast_plan_text(chars_payload,
                                   max(6, min(60, round(dur / 6))))
            result = asyncio.run(claude.generate_scenes(
                story="" if track.no_story else project.story,
                character_bible=project.character_bible,
                track_note=track_note, title=track.title, lyrics=track.lyrics,
                comment=clean_comment, style=track.style,
                duration_sec=dur,
                characters=chars_payload,
                audio_profile=track.audio_profile,
                # Как стиль влияет на драматургию (админка стилей).
                story_base=prompts_catalog.story_base(_track_style_keys(track)),
                engine=engine,
                random_cast=bool(getattr(track, "random_cast", False)),
                cast_plan=plan,
            ))
            # Страховка состава: если модель провела один состав через >70%
            # кадров — вписываем забытых персонажей сервером, без второго
            # прохода по модели.
            _rebalance_cast(result.get("scenes") or [], characters_payload(project))
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
            # Диалог кадра (сериалы): несколько реплик; speaker/line дублируют
            # первую, авторы реплик обязаны попасть в characters.
            dialogue = [
                {"who": str(d.get("who") or "").strip(),
                 "line": str(d.get("line") or "").strip()}
                for d in (sc.get("dialogue") or [])
                if isinstance(d, dict) and str(d.get("line") or "").strip()
            ]
            if dialogue and not speaker:
                speaker = dialogue[0]["who"]
            if dialogue and not line:
                line = dialogue[0]["line"]
            chars = [str(n) for n in (sc.get("characters") or []) if str(n).strip()]
            if speaker and speaker not in chars:
                chars.append(speaker)
            for d in dialogue:
                if d["who"] and d["who"] not in chars:
                    chars.append(d["who"])
            # Имена от модели сводим к реальным персонажам проекта: выдуманная
            # роль («Гонщик» вместо «лол4к») не находится и молча откатывает
            # кадр на главного героя — весь клип выходит с одним человеком.
            names = _normalize_scene_characters(", ".join(chars), track.project)
            db.add(Scene(
                track_id=track.id, position=i, start_sec=cursor, duration_sec=dur,
                lyric_line=line,
                characters=names,
                act=str(sc.get("act") or ""),
                dialogue_json=json.dumps(dialogue, ensure_ascii=False) if dialogue else "",
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
        # «-» — не пустота, а УТВЕРЖДЕНИЕ: в этих текстах стиля нет вовсе
        # (claude.SCENES_SYSTEM это прямо запрещает), и рестайл для них —
        # просто перерисовка кадров, без единого обращения к модели.
        # Пустая строка означала бы «неизвестно», а неизвестное у нас — это
        # легаси-раскадровки, писанные со стилем внутри промпта.
        track.prompts_style_keys = PROMPTS_NO_STYLE
        db.commit()
        log.info("раскадровка готова для трека %s (%s кадров)", track_id, i if result.get("scenes") else 0)
    except Exception as e:  # noqa: BLE001
        db.rollback()
        track = db.get(Track, track_id)
        if track:
            track.scenes_status = "error"
            track.scenes_error = _err_text(e, 500)
            db.commit()
            _text_refund(db, track.project, engine,
                         "возврат: раскадровка не написалась",
                         ref_type="track", ref_id=track.id)
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
    text_engine = _resolve_text_engine(user, track.project, track)
    _text_charge(db, user, text_engine,
                 f"раскадровка трека {track.id} ({text_engine})",
                 ref_type="track", ref_id=track.id, track_id=track.id,
                 project_id=track.project_id)
    track.scenes_status = "queued"
    db.commit()
    Thread(target=_run_scene_generation, args=(track_id,), daemon=True).start()
    return {"ok": True}


def _swap_prompt_names(scene: Scene, old_raw: str, new_raw: str) -> None:
    """Заменить имена прежних героев в текстах кадра на новых.

    Замена позиционная: первый ушедший меняется на первого пришедшего и так
    далее. Это не идеально для сложных перестановок, зато честно закрывает
    типичный случай «был один герой, стал другой» и не трогает промпт, если
    список персонажей не изменился."""
    old_names = [n.strip() for n in (old_raw or "").split(",") if n.strip()]
    new_names = [n.strip() for n in (new_raw or "").split(",") if n.strip()]
    gone = [n for n in old_names if n not in new_names]
    came = [n for n in new_names if n not in old_names]
    if not gone or not came:
        return
    pairs = list(zip(gone, came))
    for field in ("image_prompt", "image_prompt_last", "motion_prompt", "shot_note"):
        text = getattr(scene, field, "") or ""
        if not text:
            continue
        for a, b in pairs:
            text = re.sub(rf"(?<![\w]){re.escape(a)}(?![\w])", b, text)
        setattr(scene, field, text)


@app.patch("/api/scenes/{scene_id}")
async def update_scene(scene_id: int, request: Request, user: User = Depends(current_user), db: Session = Depends(db_session)):
    scene = _own_scene(db, user, scene_id)
    body = await request.json()
    was_chars = scene.characters or ""
    # characters и image_prompt_last фронт слал всегда — бэк их молча ронял;
    # чипы персонажей и правка последнего кадра держатся на этих полях.
    for field in ("duration_sec", "lyric_line", "characters", "shot_size", "camera_move",
                  "image_prompt", "motion_prompt", "shot_note", "image_prompt_last"):
        if field in body:
            # Длительность — в границах [SCENE_MIN_SEC, SCENE_MAX_SEC]: раньше
            # поле писалось как пришло, и «300» ломало тайминги всего трека.
            setattr(scene, field, str(body[field]) if field != "duration_sec"
                    else _clamp_dur(body[field]))
    if "image_prompt" in body:
        scene.prompt_stale = False
    if "characters" in body:
        scene.characters = _normalize_scene_characters(scene.characters, scene.track.project)
        # ИМЯ ГЕРОЯ ВШИТО В ТЕКСТ ПРОМПТА. Сменить чип персонажа было
        # недостаточно: в image_prompt остаётся «Extreme close-up of lol4…»,
        # генератор читает имя оттуда и рисует прежнего человека. Меняем чип —
        # меняем и имена в тексте, иначе выбор ничего не значит.
        if "image_prompt" not in body and (was_chars or "") != (scene.characters or ""):
            _swap_prompt_names(scene, was_chars, scene.characters)
            # Имя подменили, внешность в тексте осталась прежней — говорим об
            # этом прямо, вместо того чтобы человек это выяснял по картинке.
            scene.prompt_stale = True
    if "attribute_ids" in body:
        ids = body["attribute_ids"] or []
        scene.attribute_ids = ",".join(str(int(i)) for i in ids if str(i).isdigit())
    # ПЕРЕОПРЕДЕЛЕНИЕ ДВИЖКА КАДРА. Раньше чип жил только в памяти карточки и
    # доезжал до сервера лишь вместе с запуском генерации — человек искал
    # кнопку «сохранить», которой не было. Пустая строка = наследование.
    if "video_engine" in body:
        want = str(body["video_engine"] or "").strip()
        if want and want not in mediagen.VIDEO_ENGINES:
            raise HTTPException(400, f"неизвестный движок видео: {want!r}")
        _log_change(db, user, scene.track.project_id, "scene", scene.id,
                    "video_engine", scene.video_engine, want)
        scene.video_engine = want
    if "image_engine" in body:
        want = str(body["image_engine"] or "").strip()
        if want and want not in mediagen.IMAGE_ENGINES:
            raise HTTPException(400, f"неизвестный движок кадров: {want!r}")
        _log_change(db, user, scene.track.project_id, "scene", scene.id,
                    "image_engine", scene.image_engine, want)
        scene.image_engine = want
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


# ───────────────────── референсы кадра ─────────────────────
# Виды референса и тексты правил переехали в backend/refs.py: те же самые
# инструкции нужны мастерской (chat.py), а chat.py не может импортировать
# main.py — тот импортирует его. Две копии текстов разъехались бы, и
# генератор получал бы взаимоисключающие указания про грейд.
from refs import REF_DEFAULT, REF_KINDS, REF_RULES, ref_legend  # noqa: E402,F401



@app.post("/api/scenes/{scene_id}/refs")
async def add_scene_ref(scene_id: int, photo: UploadFile, kind: str = Form("vibe"),
                        user: User = Depends(current_user), db: Session = Depends(db_session)):
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
    kind = kind if kind in REF_KINDS else "vibe"
    db.add(SceneRef(scene_id=scene.id, position=max_pos + 1, filename=fname, kind=kind))
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

def _remove_media(filename: str, db: Session | None = None) -> None:
    """Стереть файл с диска (и его миниатюру).

    db — по возможности передавать. Раньше эта функция трогала ТОЛЬКО диск,
    а строка в file_owners оставалась жить: на живом проде 171 запись
    (3.87 ГБ по индексу) указывала в никуда, и архив завышал занятое на
    59 %. Любая квота и любой мониторинг, построенные на таких данных, —
    это не мониторинг. Пропущенные места добирает _files_verify_pass, но
    пропускать их без нужды не надо."""
    if not filename:
        return
    fname = os.path.basename(filename)
    path = os.path.join(UPLOAD_DIR, fname)
    if os.path.exists(path):
        try:
            os.remove(path)
        except OSError:
            pass
    thumb = os.path.join(THUMB_DIR, fname + ".jpg")
    if os.path.exists(thumb):
        try:
            os.remove(thumb)
        except OSError:
            pass
    if db is not None:
        try:
            row = db.get(FileOwner, fname)
            if row is not None and row.deleted_at is None:
                row.deleted_at = now()
        except Exception:  # noqa: BLE001 — учёт не должен ронять генерацию
            pass


# ─────────────────── копия файла: имя новое, байты те же ───────────────────
# ГЛАВНОЕ ЧИСЛО ВСЕЙ ЗАТЕИ С КОПИРОВАНИЕМ: копия проекта стоит НОЛЬ БАЙТ.
#
# Это не оптимизация, а следствие устройства сервиса: каждый файл здесь
# пишется РОВНО ОДИН РАЗ под свежим uuid-именем, ни одна функция не открывает
# существующий файл на запись. Значит второе имя тому же иноду безопасно, а
# копия трёхминутного клипа перестаёт стоить полтора гигабайта.
#
# Единственная мутация файла на месте была в upscale_to_4k (os.replace) — она
# ссылок не ломает (replace меняет ИМЯ, а не инод), и с переходом на нативное
# разрешение её на этом пути больше нет вовсе.

def _clone_media(db: Session, src_name: str, *, owner_id: int | None,
                 kind: str = "", project_id: int = 0, track_id: int = 0,
                 scene_id: int = 0, prefix: str = "") -> str:
    """Новое имя тому же файлу. Возвращает имя копии («» — копировать нечего).

    os.link, а не shutil.copyfile: жёсткая ссылка занимает ноль байт и
    создаётся мгновенно. Фолбэк на копирование остаётся на случай, когда
    ссылку сделать нельзя (другая файловая система, tmpfs, EPERM): функция
    обязана работать всегда, просто иногда дороже."""
    src = os.path.basename(src_name or "")
    if not src:
        return ""
    src_path = os.path.join(UPLOAD_DIR, src)
    if not os.path.exists(src_path):
        return ""   # битую ссылку на пропавший файл не тиражируем
    stem, ext = os.path.splitext(src)
    pre = prefix or (stem.split("_", 1)[0] if "_" in stem else "copy")
    dst = f"{pre}_{uuid.uuid4().hex}{ext}"
    dst_path = os.path.join(UPLOAD_DIR, dst)
    try:
        os.link(src_path, dst_path)
    except OSError:
        try:
            shutil.copyfile(src_path, dst_path)
        except OSError as e:
            log.warning("копия файла %s не сделалась: %s", src, str(e)[:150])
            return ""
    _reg_file(db, dst, owner_id, kind=kind, project_id=project_id,
              track_id=track_id, scene_id=scene_id, src_filename=src)
    return dst


def _mime_ext(mime: str) -> str:
    return ".jpg" if "jpeg" in mime else ".png"


def _save_image(data: bytes, mime: str, *, upscale: bool = True,
                aspect: str = "", hi_quality: bool = False) -> str:
    """Кадр на диск — в НАТИВНОМ разрешении движка и в WebP.

    Апскейл до 4К отсюда убран намеренно. Он не добавлял информации
    (движки отдают 1–2K), но превращал кадр в PNG на 12.5 МБ: 183 кадра на
    проде весили 2.29 ГБ, те же кадры в WebP q92 — около 0.2 ГБ. Большой
    файл собирается на лету при экспорте (/api/export/frame), то есть
    ровно тогда, когда он кому-то понадобился.

    Параметр upscale сохранён в сигнатуре: его передают четыре места, и
    менять их ради одного слова незачем — теперь он значит «кадр, а не
    служебная картинка», то есть влияет только на качество сжатия."""
    del aspect  # апскейла больше нет — аспекту здесь нечего делать
    return mediagen.encode_frame(data, mime, UPLOAD_DIR,
                                 hi_quality=hi_quality or not upscale)


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
        # Листу нужен КАДР, а не его заголовок. Раньше уходили только shot_size
        # и shot_note («Сигарета и приземление»), а всё действие лежало в
        # image_prompt и терялось: на листе героиня просто стояла в кадре за
        # кадром, потому что модель не знала, что она делает.
        scenes = [
            {
                "position": s.position,
                "shot_size": s.shot_size,
                "shot_note": s.shot_note,
                "camera_move": s.camera_move,
                "image_prompt": s.image_prompt,
                "characters": s.characters,
            }
            for s in sorted(track.scenes, key=lambda x: x.position)
        ]
        import asyncio
        built = asyncio.run(claude.generate_storyboard_sheet_prompt(
            style=track.style, character_bible=track.project.character_bible, scenes=scenes,
            characters=characters_payload(track.project),
            engine=_text_engine_for(db, track.project, track),
        ))
        prompt = built.get("prompt") or ""
        if prompt:
            # Модель сама выбирала раскладку («аккуратной сеткой»), и лист
            # выходил то 4x2, то 3x3 — нарезка резала мимо. Диктуем жёстко.
            _c, _r = sheet_grid(len(track.scenes))
            # ЖЁСТКАЯ СЕТКА. Нарезка режет лист чистой математикой
            # (ширина/колонки, высота/строки), поэтому промпт диктует
            # геометрию буквально: равные прямоугольные ячейки по фиксированным
            # координатам и тонкие белые разделители ровно на границах — их
            # съедает внутренний отступ нарезки.
            prompt = (f"{prompt}\n\nGRID (mandatory, exact geometry): a strict uniform grid of "
                      f"exactly {_c} columns by {_r} rows = {_c * _r} panels. Every panel is a "
                      f"perfect rectangle of IDENTICAL size: width = image width / {_c}, "
                      f"height = image height / {_r}. Panel boundaries sit at exact fractions "
                      f"of the image ({'/'.join(str(i) + '/' + str(_c) for i in range(1, _c))} of the width; "
                      f"same logic for rows). Separate panels ONLY with thin straight white "
                      f"divider lines (2-3 px) exactly on those boundaries. Zero outer margin, "
                      f"zero padding, no rounded corners, no page background, no frames of "
                      f"varying size, no panel may cross a divider. Panels are numbered left "
                      f"to right, top to bottom.")
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
        # СКОЛЬКО СЦЕН БЫЛО В МОМЕНТ ЗАКАЗА. Как только их станет больше или
        # меньше (вставили кадр, продлили, удалили), лист перестанет
        # соответствовать сетке нарезки — и «разложить по кадрам» порежет
        # мимо панелей. Флаг «устарел» тут не годится: его пришлось бы
        # выставлять в пяти местах и в одном из них однажды забыть.
        track.storyboard_scenes = len(track.scenes)
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
            track.storyboard_error = _err_text(e, 500)
            db.commit()
        log.warning("лист раскадровки трека %s упал: %s", track_id, _err_text(e))
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


def _guard_sheet_fresh(track: Track) -> None:
    """Лист нарисован под ДРУГОЕ число сцен — резать его нельзя.

    Сетка листа зафиксирована в момент генерации (storyboard_grid), а
    нарезка идёт по позициям сцен. Разошлись — куски поедут со сдвигом и
    захватят соседние панели: человек получит мешанину вместо кадров и
    потеряет уже отрисованное. Отказ здесь честнее молчаливой порчи."""
    if track.storyboard_scenes and track.storyboard_filename \
            and track.storyboard_scenes != len(track.scenes):
        raise ApiError(409, "sheet_stale",
                       f"лист раскадровки нарисован на {track.storyboard_scenes} "
                       f"кадров, а сейчас их {len(track.scenes)} — пересобери лист",
                       sheet_scenes=int(track.storyboard_scenes),
                       scenes=len(track.scenes))


# ДОЛЯ ВНУТРЕННЕГО ОТСТУПА ЯЧЕЙКИ. Нарезка идёт чистой математикой
# (ширина/колонки), а не «детектом» панелей; крохотный отступ внутрь ячейки
# срезает белые разделители сетки и миллиметровые неточности рисовальщика.
CELL_INSET = 0.012


def _cell_crop(cols: int, rows: int, cx: int, cy: int) -> str:
    """ffmpeg-фильтр ячейки (cx, cy): математический крой с отступом внутрь."""
    return (f"crop=iw/{cols}*{1 - 2 * CELL_INSET:.4f}"
            f":ih/{rows}*{1 - 2 * CELL_INSET:.4f}"
            f":({cx}+{CELL_INSET:.4f})*iw/{cols}"
            f":({cy}+{CELL_INSET:.4f})*ih/{rows}")


@app.post("/api/tracks/{track_id}/storyboard-cells")
def storyboard_cells(track_id: int, user: User = Depends(current_user), db: Session = Depends(db_session)):
    """Режет лист на ячейки и отдаёт их превью — БЕЗ записи в сцены.
    Владелец сам решает в модалке, какие ячейки взять и в какие сцены их
    положить (см. apply-cells)."""
    track = _own_track(db, user, track_id)
    if not track.storyboard_filename:
        raise HTTPException(400, "сначала сгенерируй лист раскадровки")
    _guard_sheet_fresh(track)
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
            ["ffmpeg", "-y", "-i", src, "-vf", _cell_crop(cols, rows, cx, cy), dst],
            capture_output=True, timeout=120,
        )
        if r.returncode != 0 or not os.path.exists(dst):
            continue
        _reg_file(db, fname, track.project.owner_id, kind="frame",
                  project_id=track.project_id, track_id=track.id)
        cells.append({"index": i + 1, "filename": fname, "scene_hint": i,
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
        # ДОБАВЛЯЕМ, А НЕ ЗАМЕНЯЕМ. Раньше ячейка листа затирала уже
        # отрисованный кадр и он удалялся с диска: человек терял работу,
        # за которую заплатил токенами. Если первый кадр занят — ячейка
        # уходит в последний; если заняты оба — кладём её референсом сцены,
        # чтобы она осталась материалом, а не пропала.
        if not scene.image_filename:
            scene.image_filename = new_name
            scene.image_status = "done"
            scene.image_error = ""
        elif not scene.image_last_filename:
            scene.image_last_filename = new_name
        else:
            pos = 1 + max([r.position for r in scene.refs] or [0])
            db.add(SceneRef(scene_id=scene.id, filename=new_name,
                            position=pos, kind="vibe"))
        _reg_file(db, new_name, track.project.owner_id, kind="frame",
                  project_id=track.project_id, track_id=track.id, scene_id=scene.id)
        db.commit()
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
    _guard_sheet_fresh(track)
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
            ["ffmpeg", "-y", "-i", src, "-vf", _cell_crop(cols, rows, cx, cy), dst],
            capture_output=True, timeout=120,
        )
        if r.returncode != 0 or not os.path.exists(dst):
            continue
        # Тоже ДОБАВЛЯЕМ: «разложить лист по кадрам» на треке с готовыми
        # кадрами больше не стирает их. Занят первый — идём в последний,
        # заняты оба — ячейка остаётся референсом сцены.
        if not sc.image_filename:
            sc.image_filename = fname
            sc.image_status = "done"
            sc.image_error = ""
        elif not sc.image_last_filename:
            sc.image_last_filename = fname
        else:
            pos = 1 + max([r.position for r in sc.refs] or [0])
            db.add(SceneRef(scene_id=sc.id, filename=fname,
                            position=pos, kind="vibe"))
        _reg_file(db, fname, track.project.owner_id, kind="frame",
                  project_id=track.project_id, track_id=track.id, scene_id=sc.id)
        db.commit()
        done += 1
    return {"ok": True, "sliced": done, "grid": f"{cols}x{rows}"}


@app.post("/api/tracks/{track_id}/generate-storyboard")
def generate_storyboard(track_id: int, user: User = Depends(current_user), db: Session = Depends(db_session)):
    _guard_disk()
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
    _spawn_gen(user, _run_storyboard, track_id, kind="storyboard")
    return {"ok": True}


# СЛУЧАЙНЫЙ СОСТАВ КАДРОВ. Доли подобраны не «поровну», а по тому, как
# смонтирован живой клип: несущая масса — одиночные планы, они держат
# узнавание героя; пары дают диалог и конфликт; тройка и пустой кадр —
# редкие акценты, от которых ряд перестаёт быть однообразным.
CAST_SHAPE = (
    (1, 55),   # один герой в кадре
    (2, 28),   # двое
    (3, 10),   # трое
    (0, 7),    # без людей: пейзаж, деталь, предмет
)


def _pick_cast_size(rnd: random.Random, pool: int) -> int:
    """Сколько героев в этом кадре."""
    roll = rnd.randint(1, 100)
    acc = 0
    for size, weight in CAST_SHAPE:
        acc += weight
        if roll <= acc:
            return min(size, pool)
    return min(1, pool)


def _shuffle_track_cast(track: Track, project: Project, seed: "int | None" = None) -> int:
    """Разложить персонажей проекта по кадрам трека.

    Главный герой (первый в списке проекта) появляется заметно чаще прочих:
    равномерная раздача превращает клип в парад незнакомцев, где ни одно лицо
    не успевает запомниться. Остальные тасуются колодой — так каждый получает
    свои кадры, а не выпадает случайно по десять раз подряд.
    """
    names = [c.name.strip() for c in sorted(project.characters, key=lambda x: x.position)
             if c.name.strip()]
    if not names:
        return 0
    rnd = random.Random(seed if seed is not None else secrets.randbits(32))
    hero = names[0]
    others = names[1:]
    deck: list[str] = []
    changed = 0
    scenes = sorted(track.scenes, key=lambda x: (x.position, x.id))
    for sc in scenes:
        size = _pick_cast_size(rnd, len(names))
        if size == 0:
            cast = []
        else:
            # Герой в кадре с вероятностью 60% — при одном персонаже в проекте
            # это просто он сам.
            # ≤50%: главный не должен вылезать за половину кадров (квота
            # владельца), при одном персонаже в проекте это просто он сам.
            cast = [hero] if (not others or rnd.randint(1, 100) <= 50) else []
            while len(cast) < size and others:
                if not deck:
                    deck = others[:]
                    rnd.shuffle(deck)
                pick = deck.pop()
                if pick not in cast:
                    cast.append(pick)
            if not cast:
                cast = [hero]
        line = ",".join(cast)
        if line != (sc.characters or ""):
            was = sc.characters or ""
            sc.characters = line
            # Имена в тексте промпта едут за составом, а описание внешности
            # остаётся прежним — помечаем кадр, чтобы это не всплыло картинкой.
            _swap_prompt_names(sc, was, line)
            sc.prompt_stale = True
            changed += 1
    return changed


@app.post("/api/tracks/{track_id}/scenes/shuffle-cast")
def shuffle_track_cast(track_id: int, user: User = Depends(current_user),
                       db: Session = Depends(db_session)):
    """Перетасовать персонажей по кадрам — по кнопке, без генерации."""
    track = _own_track(db, user, track_id)
    changed = _shuffle_track_cast(track, track.project)
    db.commit()
    return {"ok": True, "changed": changed}


def _normalize_scene_characters(raw: str, project: Project) -> str:
    """Свести имена из ответа модели к РЕАЛЬНЫМ персонажам проекта.

    Модель регулярно выдумывает роли: в поле уезжало «Гонщик», хотя героя зовут
    «лол4к». Такое имя не находится, кадр молча откатывается на главного героя —
    и весь клип выходит с одним и тем же человеком, сколько бы персонажей ни
    выбрали. Инструкция «имена используй дословно» в промпте есть, но она
    мягкая, а это проверка.

    Порядок: точное совпадение → без регистра и пробелов → вхождение подстрокой
    (модель любит писать «Анька в шлеме»). Что не опознано — выбрасываем, и
    если не осталось ничего, поле пустеет: пустое поле честно означает «берём
    главного», а выдуманное имя означало то же самое, но молча."""
    names = [n.strip() for n in str(raw or "").split(",") if n.strip()]
    if not names:
        return ""
    real = [c for c in project.characters if (c.name or "").strip()]
    if not real:
        return ""
    exact = {c.name.strip().lower(): c.name for c in real}
    out, seen = [], set()
    for n in names:
        low = n.strip().lower()
        hit = exact.get(low)
        if not hit:
            # «Анька в шлеме» → Анька; «гонщик» → ничего.
            for c in real:
                cn = c.name.strip().lower()
                if cn and (cn in low or low in cn):
                    hit = c.name
                    break
        if hit and hit not in seen:
            seen.add(hit)
            out.append(hit)
    return ",".join(out)


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


def _attr_item_photo(a: CharacterAttribute) -> str | None:
    """Фото ПРЕДМЕТА, привязанного к атрибуту (см. CharacterAttribute
    .item_track_id). Сессию берём у самого атрибута: функция зовётся из
    цепочки референсов, куда db не протаскивается."""
    tid = int(getattr(a, "item_track_id", 0) or 0)
    if not tid:
        return None
    sess = object_session(a)
    tr = sess.get(Track, tid) if sess else None
    if not tr:
        return None
    paths = _track_photo_paths(tr, 1)
    return paths[0] if paths else None


def _scene_attribute_photo(scene: Scene, chars: list[Character]) -> str | None:
    """Референс-АТРИБУТ: если текст сцены упоминает фирменную вещь персонажа
    (шляпу, квадрик, тачку) — кадр строится вокруг предмета, и референсом
    должно идти фото самой вещи, а не лицо героя. Проверяем только персонажей
    ЭТОЙ сцены; совпадение — регистронезависимое вхождение имени атрибута."""
    # Явно выбранные вещи имеют приоритет над поиском имени в тексте.
    for a in _scene_selected_attributes(scene, chars):
        # Атрибут привязан к ПРЕДМЕТУ — берём фото предмета: там ракурсов
        # больше, и вещь в кадре совпадает с той, что снята в мокапах.
        item = _attr_item_photo(a)
        if item:
            return item
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
            item = _attr_item_photo(a)
            if item:
                return item
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
    if not chars:
        return paths
    # Сколько фото на героя. Раньше было жёстко ОДНО, и модель усваивала лицо
    # с одного ракурса — отсюда «персонаж не похож». Движки берут до 8-14
    # референсов, поэтому при одном-двух героях в кадре отдаём им несколько
    # снимков: чем больше ракурсов одного лица, тем меньше модель фантазирует.
    per_char = max(1, limit // max(1, len(chars)))
    for c in chars:
        if len(paths) >= limit:
            break
        picked: list[str] = []
        plain = [x for x in c.photos if (x.kind or "photo") != "model"]
        models = [x for x in c.photos if (x.kind or "photo") == "model"]
        # Для КАДРОВ СЦЕН разворот не берём ВООБЩЕ, если есть живое фото:
        # разворот сам является сеткой из нескольких ракурсов на нейтральном
        # фоне, и генератор воспроизводит именно её — вместо сцены выходит
        # второй character sheet. Раньше он приезжал «в конце, если место
        # осталось», и при одном герое место всегда оставалось.
        if prefer_photo:
            order = plain if plain else models
        else:
            order = models + plain
        for x in sorted(order, key=lambda y: (y.position, y.id)):
            if len(picked) >= per_char or len(paths) + len(picked) >= limit:
                break
            full = os.path.join(UPLOAD_DIR, x.filename)
            if os.path.exists(full) and full not in picked:
                picked.append(full)
        paths.extend(picked)
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
    # Референсы стиля (StyleAsset, in_generation=1) доезжают и до шлюзов:
    # последним слотом коллажа, персонажи всегда важнее по местам.
    style_tail = _style_ref_paths(scene.track)[:1]
    if scene_refs:
        models = _character_model_paths(
            chars or [c for c in project.characters if c.is_main], 4, prefer_photo=True)
        # Реф первым: первая картинка коллажа для генератора — главная.
        return (_ref_collage(db, [scene_refs[0], *models, *style_tail],
                             project.owner_id) or scene_refs[0])

    attr_path = _scene_attribute_photo(scene, chars)
    if attr_path:
        return attr_path
    if not chars:
        chars = [c for c in project.characters if c.is_main]
    # Лимит по движку: Nano Banana 2 берёт 14 картинок, Pro — 8, шлюз — 1.
    paths = _character_model_paths(chars, 6, prefer_photo=True)
    if not paths:
        return (_ref_collage(db, style_tail, project.owner_id) or style_tail[0]) \
            if style_tail else None
    # Несколько героев в кадре — референсом идёт сборный лист: модельки бок о
    # бок, иначе генератор видит только первого и рисует остальных от балды.
    return _ref_collage(db, [*paths, *style_tail], project.owner_id) or paths[0]


def _reference_legend(scene: Scene, project: Project) -> str:
    """Кто есть кто на референсах — словами, по именам.

    Раньше картинки уходили безымянным списком, и при двух-трёх героях в
    кадре модель не знала, какое лицо чьё: путала персонажей местами или
    лепила усреднённого. Теперь промпт прямо перечисляет, в каком порядке
    приложены снимки и к какому имени они относятся, а имена совпадают с
    теми, что стоят в описании сцены."""
    chars = _scene_characters(scene, project)
    if not chars:
        chars = [c for c in project.characters if c.is_main]
    if not chars:
        return ""
    n_scene = len(_scene_ref_paths(scene)[:3])
    limit = 3 if n_scene else 4
    per = max(1, limit // max(1, len(chars)))
    lines, idx = [], n_scene
    for c in chars:
        shots = min(per, len([x for x in c.photos
                              if os.path.exists(os.path.join(UPLOAD_DIR, x.filename))]))
        if shots <= 0:
            continue
        first, last = idx + 1, idx + shots
        span = f"image {first}" if shots == 1 else f"images {first}-{last}"
        lines.append(f"{span} = '{c.name}'")
        idx += shots
    if not lines:
        return ""
    head = ("REFERENCE MAP (who is who): " + "; ".join(lines) + ". ")
    if n_scene:
        head = (f"The first {n_scene} reference image(s) are scene mood/composition, "
                f"not people. ") + head
    return head + (
        "Match each named person in the shot to THEIR OWN reference images above. "
        "Do not swap faces between characters, do not merge them into one look, "
        "and do not give a character the face or clothes of another."
    )


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
    # prefer_photo=True в ОБЕИХ ветках: для кадра сцены нужен живой снимок
    # человека, а не разворот. Разворот — сетка ракурсов на нейтральном фоне,
    # и генератор рисует её вместо сцены. Раньше флаг стоял только в одной
    # ветке, и основной путь (у кадра нет своих рефов) тащил разворот первым.
    if scene_refs:
        out += scene_refs[:3]
        out += _character_model_paths(
            chars or [c for c in project.characters if c.is_main], 3,
            prefer_photo=True)
    else:
        attr_path = _scene_attribute_photo(scene, chars)
        if attr_path:
            out.append(attr_path)
        if not chars:
            chars = [c for c in project.characters if c.is_main]
        out += _character_model_paths(chars, 4, prefer_photo=True)
    # РЕФЕРЕНСЫ СТИЛЯ — последними и не больше двух. Порядок здесь не
    # вкусовщина: персонаж важнее стиля. Стилевые картинки приезжают из
    # админки стилей (StyleAsset, in_generation=1) и подмешиваются ТОЛЬКО
    # если после персонажей осталось место в потолке. Иначе рестайл вылечит
    # одну болезнь и вернёт вторую — «персонажи не похожи», которую только
    # что чинили.
    style_refs = _style_ref_paths(scene.track)
    # До ШЕСТИ случайных референсов стиля (по свободным слотам): чем больше
    # ракурсов эстетики видит Nano Banana, тем меньше он её выдумывает.
    # Случайные, а не первые — чтобы кадры трека не липли к одной картинке.
    if len(style_refs) > 6:
        import random as _rnd
        style_refs = _rnd.sample(style_refs, 6)
    # Дедуп с сохранением порядка + потолок по самому скупому Nano Banana (8).
    seen: set[str] = set()
    uniq = [p for p in out if not (p in seen or seen.add(p))]
    room = max(0, 8 - len(uniq))
    for p in style_refs[:6]:
        if room <= 0:
            break
        if p not in seen:
            uniq.append(p)
            seen.add(p)
            room -= 1
    return uniq[:8]


def _style_ref_paths(track: "Track | None") -> list[str]:
    """Картинки-референсы стиля трека, помеченные «в генерацию».

    Файлы лежат в STYLE_ASSETS_DIR (том /data), а не в образе: их кладёт
    владелец через админку стилей, и переживать деплой они обязаны."""
    if not track:
        return []
    out: list[str] = []
    for key in _track_style_keys(track):
        for fname in (prompts_catalog.style_gen_refs(key) or []):
            path = os.path.join(STYLE_ASSETS_DIR, os.path.basename(fname))
            if os.path.exists(path):
                out.append(path)
    return out


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
        f"VISUAL STYLE (applies to lighting, palette, texture and mood — NOT to who "
        f"the people are): {style}. "
        f"Render the whole frame in this style — lighting, palette, texture, grain and mood "
        f"come from the STYLE, never from the reference images. "
        f"BUT the identity of the people is NOT part of the style: faces, hair and outfits "
        f"come from the reference photos and stay exactly as they are, whatever the style is.",
        # 2. Что происходит в кадре.
        base,
        # 3. Роль референсов: узнаваемость и композиция, но не картинка целиком.
        "Reference images define composition, framing energy and character identity ONLY — "
        "do not copy their color grade, lighting or background.",
    ]
    # СЕРИАЛ: консистентность между сериями — тот же визуальный мир, гардероб
    # и лица, что и в предыдущих сериях сезона.
    if _catalog_of(project) == "series":
        parts.append(
            "Series continuity (mandatory): same visual style, wardrobe and "
            "character appearance as established in previous episodes of this "
            "series — identical faces, hair, outfits and overall look; no "
            "redesigns between episodes.")
    # Персонажи кадра: их канонические описания обязаны попасть в промпт
    # (внешность НЕ переизобретается, меняется только стилистика подачи).
    # Легенда «какая картинка чей герой» — ДО описаний персонажей, чтобы
    # модель сначала узнала расклад, а потом читала, кто как выглядит.
    legend = _reference_legend(scene, project)
    if legend:
        parts.append(legend)
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
                    f"do not beautify, do not change age, ethnicity or hairstyle. "
                    f"Reproduce their exact outfit from the reference: same garments, "
                    f"same colours, same silhouette, same accessories."
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
        "generic model or a better-looking lookalike. Reproduce their outfit as well: the "
        "same clothes, colours and accessories as in the reference, not a generic substitute."
    )
    # 4d. Закрытое лицо — часть образа, а не помеха. Генератор охотно «помогает»:
    # снимает шлем, маску или очки, чтобы показать лицо, и герой перестаёт быть собой.
    parts.append(
        "If the reference shows the character with a covered or hidden face — helmet, mask, "
        "balaclava, hood, animal head, dark glasses — KEEP IT ON exactly as in the reference. "
        "Never remove it, never lift the visor, never reveal the face underneath, never "
        "substitute a bare human head. The covering IS the character."
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


def _media_bytes(*names: str) -> int:
    """Сколько весит набор файлов. Дубликаты имён считаем один раз."""
    total = 0
    for f in {n for n in names if n}:
        try:
            total += os.path.getsize(os.path.join(UPLOAD_DIR, f))
        except OSError:
            pass
    return total


# ═══════════════════ КЭШ КАДРОВ: не платить дважды за одно ═══════════════════
#
# По журналу прода у одной сцены встречались шесть прогонов кадров подряд.
# Часть из них — буквально «то же самое ещё раз»: человек нажал кнопку, не
# дождался, нажал снова; вернулся к сцене и перерисовал, ничего не поменяв.
# Каждый такой прогон стоил ему токенов, нам — денег на kie.ai, а диску —
# ещё одного файла.
#
# Ключ — отпечаток ВСЕГО, от чего зависит картинка: текст промпта, движок,
# разрешение, аспект и список референсов. Совпало всё — картинка будет та
# же самая, и честнее отдать её жёсткой ссылкой за ноль.
FRAME_CACHE_DAYS = int(os.environ.get("FRAME_CACHE_DAYS", "30"))


def _frames_sig(scene: Scene, track: Track) -> str:
    """Отпечаток «под что сняты кадры сцены»: промпты + стиль."""
    raw = "\x1f".join([
        (scene.image_prompt or "").strip(),
        (scene.image_prompt_last or "").strip(),
        ",".join(_track_style_keys(track)),
    ])
    return hashlib.sha1(raw.encode("utf-8", "replace")).hexdigest()[:20]


def _frame_cache_key(db: Session, scene: Scene, track: Track, which: str,
                     engine: str, resolution: str, aspect: str) -> str:
    """Отпечаток одного кадра. Референсы входят ИМЕНАМИ ФАЙЛОВ: имена у нас
    уникальные (uuid), поэтому смена реферса гарантированно меняет ключ."""
    try:
        refs = [os.path.basename(p)
                for p in _scene_reference_paths(db, scene, track.project)]
    except Exception:  # noqa: BLE001 — кэш не должен ронять генерацию
        refs = ["?"]
    raw = "\x1f".join([
        _frame_prompt(scene, track, which),
        engine or "", (resolution or "").upper(), aspect or "", ",".join(refs),
    ])
    return hashlib.sha1(raw.encode("utf-8", "replace")).hexdigest()


def _frame_cache_get(db: Session, user: "User | None", key: str) -> str:
    """Имя уже нарисованного файла или «». СТРОГО в пределах одного
    пользователя: общий кэш означал бы «совпал промпт — увидел чужой кадр»."""
    if not user or not key:
        return ""
    row = (db.query(FrameCache)
           .filter(FrameCache.user_id == user.id, FrameCache.key_hash == key,
                   FrameCache.created_at >= now() - timedelta(days=FRAME_CACHE_DAYS))
           .order_by(FrameCache.id.desc()).first())
    if not row:
        return ""
    if not os.path.exists(os.path.join(UPLOAD_DIR, row.filename)):
        db.delete(row)          # файл убрали — запись мусорная
        db.commit()
        return ""
    return row.filename


def _frame_cache_put(db: Session, user: "User | None", key: str, engine: str,
                     filename: str) -> None:
    if not user or not key or not filename:
        return
    try:
        db.add(FrameCache(user_id=user.id, key_hash=key, engine=engine or "",
                          filename=filename, created_at=now()))
        db.flush()
    except Exception as e:  # noqa: BLE001
        log.warning("кэш кадров: не записался (%s)", str(e)[:120])


def _keep_scene_version(db: Session, scene: Scene, track: Track, note: str = "",
                        *, kind: str = "frames", cost_points: int = 0) -> None:
    """Снять текущие кадры (и видео) сцены в историю вариантов.

    Зовётся ПЕРЕД тем, как в сцену лягут новые файлы, и зовётся ВСЕГДА —
    это и есть «варианты сохраняются в истории проекта и в истории
    креатора». Раньше снимок делал только рестайл, а обычная перерисовка
    кадра шла через _remove_media(old_video): человек нажимал «перерисовать
    кадр» и терял видео сцены за 152 токена, о которых его не спросили.

    Здесь нет ни одного _remove_media: смысл функции ровно в том, чтобы
    файлы остались на диске и на них появилась вторая ссылка. Место
    ограничивает ретенция (_trim_scene_versions), а не потеря данных."""
    if not (scene.image_filename or scene.image_last_filename or scene.video_filename):
        return
    keys = [k for k in (scene.style_keys or "").split(",") if k.strip()] \
        or _track_style_keys(track)
    owner_id = track.project.owner_id if track.project else 0
    db.add(SceneVersion(
        scene_id=scene.id,
        user_id=int(owner_id or 0),
        project_id=int(track.project_id or 0),
        track_id=int(track.id or 0),
        kind=kind or "frames",
        cost_points=int(cost_points or 0),
        bytes=_media_bytes(scene.image_filename, scene.image_last_filename,
                           scene.video_filename, scene.audio_filename),
        style_keys=",".join(keys),
        style_label=prompts_catalog.labels(keys, "ru"),
        image_filename=scene.image_filename,
        image_last_filename=scene.image_last_filename,
        image_prompt=scene.image_prompt,
        image_prompt_last=scene.image_prompt_last,
        video_filename=scene.video_filename,
        audio_filename=scene.audio_filename,
        image_engine=scene.image_engine or "",
        video_engine=scene.video_engine or "",
        note=str(note or "")[:200],
    ))
    db.flush()
    owner = db.get(User, owner_id) if owner_id else None
    _trim_scene_versions(db, scene.id, owner)


def _trim_scene_versions(db: Session, scene_id: int, user: "User | None" = None) -> None:
    """Ретенция истории вариантов одной сцены: тарифная глубина и тарифный срок.

    Два правила поверх «оставить последние N»:
      * ЗАКРЕПЛЁННЫЕ (pinned) не считаются в лимите и не протухают. «Не
        удаляйте это» — законное желание; место в квоте они при этом
        занимают, потому что «храните вечно и бесплатно» — уже нет.
      * Протухшие по сроку уезжают, даже если их меньше лимита: на FREE
        история живёт неделю, у ULTRA — полгода."""
    keep = _versions_keep(user)
    days = _versions_days(user)
    rows = (db.query(SceneVersion)
            .filter(SceneVersion.scene_id == int(scene_id))
            .order_by(SceneVersion.id.desc()).all())
    rest = [r for r in rows if not r.pinned]   # закреплённые не трогаем вовсе
    edge = now() - timedelta(days=days)
    doomed = list(rest[keep:])
    for r in rest[:keep]:
        at = _as_utc(r.created_at)
        if at and at < edge:
            doomed.append(r)
    if not doomed:
        return
    # ЖИВЫЕ ССЫЛКИ. Один и тот же файл может лежать и в сцене, и в снимке:
    # при перерисовке только первого кадра последний остаётся прежним, и
    # версия ссылается ровно на тот файл, который сцена продолжает
    # показывать. Стереть его «как старый» значило бы выбить картинку из-под
    # живой сцены.
    scene = db.get(Scene, int(scene_id))
    alive = {scene.image_filename, scene.image_last_filename,
             scene.video_filename, scene.audio_filename} if scene else set()
    doomed_ids = {r.id for r in doomed}
    for keeper in rows:
        if keeper.id in doomed_ids:
            continue
        alive |= {keeper.image_filename, keeper.image_last_filename,
                  keeper.video_filename, keeper.audio_filename}
    for old in doomed:
        for f in (old.image_filename, old.image_last_filename,
                  old.video_filename, old.audio_filename):
            if f and f not in alive:
                _remove_media(f, db)
        db.delete(old)


def _run_scene_frames(scene_id: int, which: str = "both", engine: str = "",
                      keep_version: bool = False) -> None:
    """which: both | first | last — что именно пересобираем.
    engine — явный движок кадров (chatgpt / nano-banana…); пустая строка
    означает «взять дефолт тарифа».
    keep_version=True — старые кадры и видео уезжают в SceneVersion, а не
    в мусор. Так работает рестайл: сцена на Seedance 2.5 стоит 152 токена,
    и молча стирать её ради смены стиля нельзя."""
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
        _set_task_ctx("scene", scene.id, "video")
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
            scene.image_error = _fallback_note(res, engine)
        if which in ("both", "last"):
            # ПОСЛЕДНИЙ КАДР ВИДИТ ПЕРВЫЙ. Раньше это были два независимых
            # запроса с общим текстом — движок рисовал «два рандомных кадра»,
            # а не начало и конец одного плана. Теперь первый кадр идёт
            # референсом к последнему с прямым наказом держать место и свет.
            last_ref, last_refs = reference, list(ref_list)
            tmp_first = ""
            if first_data is not None:
                ext = "png" if "png" in (first_mime or "") else "jpg"
                tmp_first = os.path.join(UPLOAD_DIR, f"tmp_first_{scene.id}.{ext}")
                with open(tmp_first, "wb") as fh:
                    fh.write(first_data)
            elif scene.image_filename:
                p = os.path.join(UPLOAD_DIR, scene.image_filename)
                tmp_first = p if os.path.exists(p) else ""
            prompt_last = _frame_prompt(scene, track, "last")
            if tmp_first:
                if multi:
                    last_refs.append(tmp_first)
                else:
                    last_ref = tmp_first
                prompt_last += ("\n\nВАЖНО: это ПОСЛЕДНИЙ кадр той же сцены, "
                                "чей ПЕРВЫЙ кадр приложен референсом. Та же "
                                "локация, свет, персонажи, одежда и стиль — "
                                "покажи развитие действия между кадрами, а не "
                                "новую сцену.")
            res = asyncio.run(mediagen.generate_image_ex(
                prompt_last, last_ref,
                reference_paths=last_refs, engine=engine,
                resolution=img_res, aspect=aspect))
            if tmp_first.startswith(os.path.join(UPLOAD_DIR, "tmp_first_")):
                try: os.remove(tmp_first)
                except OSError: pass
            last_data, last_mime = res["data"], res["mime"]
            native_4k = native_4k or res["native_4k"]
            scene.image_engine = res["engine"]

        _attach_task(db, "scene", scene.id, mediagen.last_task_id(), "frames")
        # СНИМОК ДЕЛАЕТСЯ ВСЕГДА. Раньше — только при keep_version (рестайл),
        # а в обычной перерисовке ниже стоял _remove_media(old_video): человек
        # жал «перерисовать кадр» и терял видео сцены за 152 токена. Ветки
        # «с версией / без версии» больше нет — есть история и ретенция.
        _keep_scene_version(db, scene, track,
                            kind="restyle" if keep_version else "frames")
        old_mids = [m.get("filename", "") for m in _midframes(scene)]
        if first_data is not None:
            scene.image_filename = _save_image(first_data, first_mime,
                                               upscale=not native_4k, aspect=aspect)
            _reg_file(db, scene.image_filename, track.project.owner_id, kind="frame",
                      project_id=track.project_id, track_id=track.id, scene_id=scene.id)
            _frame_cache_put(db, owner, _frame_cache_key(db, scene, track, "first",
                                                         engine, img_res, aspect),
                             scene.image_engine or engine, scene.image_filename)
        if last_data is not None:
            scene.image_last_filename = _save_image(last_data, last_mime,
                                                    upscale=not native_4k, aspect=aspect)
            _reg_file(db, scene.image_last_filename, track.project.owner_id,
                      kind="frame_last", project_id=track.project_id,
                      track_id=track.id, scene_id=scene.id)
            _frame_cache_put(db, owner, _frame_cache_key(db, scene, track, "last",
                                                         engine, img_res, aspect),
                             scene.image_engine or engine, scene.image_last_filename)
        scene.image_status = "done"
        # Кадры переснялись — старое видео, утверждение и промежуточные
        # кадры (интерполяция СТАРОЙ пары) к ним не относятся. Видео при этом
        # НЕ СТИРАЕТСЯ с диска: оно уехало в версию строчкой выше, и его
        # можно вернуть кнопкой «Вернуть только видео».
        scene.approved = False
        scene.video_filename = ""
        scene.video_status = ""
        scene.video_error = ""
        scene.video_stale = False
        scene.audio_filename = ""
        scene.midframes_json = ""
        # Чем снята эта пара кадров. По этому полю карточка отличает сцену,
        # оставшуюся в прежнем стиле, от перерисованной.
        scene.style_keys = ",".join(_track_style_keys(track))
        # Отпечаток «под что сняты нынешние кадры»: по нему пакетная
        # перерисовка отличает изменившиеся сцены от нетронутых.
        scene.frames_sig = _frames_sig(scene, track)
        # Промежуточные кадры удаляем всегда: это интерполяция СТАРОЙ пары,
        # и в снимке она бессмысленна.
        for f in old_mids:
            _remove_media(f, db)
        db.commit()
        log.info("кадры сцены %s готовы", scene_id)
    except Exception as e:  # noqa: BLE001
        db.rollback()
        scene = db.get(Scene, scene_id)
        if scene:
            scene.image_status = "error"
            scene.image_error = _err_text(e, 500)
            db.commit()
        log.warning("генерация кадров сцены %s упала: %s", scene_id, e)
    finally:
        _remove_media(collage)
        db.close()


@app.post("/api/scenes/{scene_id}/generate-frames")
def generate_scene_frames(scene_id: int, which: str = "first", engine: str = "",
                          user: User = Depends(current_user),
                          db: Session = Depends(db_session)):
    """Дефолт — ТОЛЬКО первый кадр (which=first): пары «первый+последний»
    часто расходятся, а видео умеет ехать от одного кадра. «Последний» —
    отдельной кнопкой, и тогда он рисуется С РЕФЕРЕНСОМ первого."""
    _guard_disk()
    scene = _own_scene(db, user, scene_id)
    if not scene.image_prompt.strip():
        raise HTTPException(400, "у сцены пуст промпт первого кадра")
    if which not in ("both", "first", "last"):
        which = "first"
    # Движок разрешаем ДО списания: цена кадров зависит именно от него, а сам
    # он берётся по цепочке «явный выбор → движок объекта → тариф». Раньше
    # выбор из карточки кадра доезжал сюда и молча затирался внутри
    # _run_scene_frames дефолтом тарифа.
    engine = _resolve_image_engine(user, scene.track, engine)
    # КЭШ. Проверяем ДО списания — иначе экономия была бы только для нас, а
    # человек всё равно платил бы за уже нарисованное. Ключ считается из
    # всего, от чего зависит картинка: промпт, движок, разрешение, аспект,
    # референсы. Совпало всё — картинка будет та же самая.
    if _apply_frame_cache(db, user, scene, which, engine):
        return {"ok": True, "cached": True, "charged": 0}
    _scene_charge(db, user, scene, _frames_cost(user, scene, engine, which),
                  f"кадры сцены {scene.id} ({which})", kind="frames", engine=engine)
    scene.image_status = "queued"
    db.commit()
    _spawn_gen(user, _run_scene_frames, scene_id, which, engine, kind="frames")
    return {"ok": True}


def _apply_frame_cache(db: Session, user: User, scene: Scene, which: str,
                       engine: str) -> bool:
    """Если ВСЕ запрошенные кадры уже рисовались этим человеком с тем же
    отпечатком — поставить их ссылками и не списывать ничего.

    ГРАНИЦА, БЕЗ КОТОРОЙ ЭТО БЫЛА БЫ ЛОВУШКА: кэш работает ТОЛЬКО когда
    кадра ещё нет. Человек, который жмёт «перерисовать» на готовом кадре,
    хочет ДРУГОЙ вариант — модели стохастичны, в этом весь смысл кнопки. Вернуть
    ему ту же самую картинку (пусть и бесплатно) значило бы сделать вид, что
    кнопка сломана. Поэтому кэш ловит ровно то, ради чего заводился: повторное
    нажатие, ретрай после ошибки и пакет по сценам, где кадров нет.

    Половинчатого попадания не бывает намеренно: «первый из кэша, последний
    рисуем» усложнило бы списание вдвое ради экономии одной картинки."""
    have = {"first": bool(scene.image_filename), "last": bool(scene.image_last_filename)}
    want0 = ["first", "last"] if which == "both" else [which]
    if any(have.get(w) for w in want0):
        return False
    track = scene.track
    aspect = _track_aspect(track)
    res = (track.image_resolution or "").strip()
    want = ["first", "last"] if which == "both" else [which]
    hits = {}
    for w in want:
        key = _frame_cache_key(db, scene, track, w, engine, res, aspect)
        name = _frame_cache_get(db, user, key)
        if not name:
            return False
        hits[w] = name
    owner_id = track.project.owner_id
    # СНАЧАЛА собираем ВСЕ ссылки, потом трогаем сцену. Иначе сорвавшаяся на
    # втором кадре ссылка оставила бы сцену наполовину подменённой, да ещё и
    # с лишней записью в истории.
    clones = {}
    for w, name in hits.items():
        clone = _clone_media(db, name, owner_id=owner_id,
                             kind="frame" if w == "first" else "frame_last",
                             project_id=track.project_id, track_id=track.id,
                             scene_id=scene.id)
        if not clone:
            for made in clones.values():
                _remove_media(made, db)
            return False
        clones[w] = clone
    _keep_scene_version(db, scene, track, kind="frames")
    if "first" in clones:
        scene.image_filename = clones["first"]
    if "last" in clones:
        scene.image_last_filename = clones["last"]
    scene.image_status = "done"
    scene.image_error = ""
    scene.image_engine = engine
    scene.approved = False
    scene.video_filename = ""
    scene.video_status = ""
    scene.video_stale = False
    scene.midframes_json = ""
    scene.style_keys = ",".join(_track_style_keys(track))
    scene.frames_sig = _frames_sig(scene, track)
    db.commit()
    log.info("кадры сцены %s взяты из кэша — 0 токенов", scene.id)
    return True


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
                      f"{first} → {last}, style unchanged. Same single "
                      f"continuous shot with the same camera move in progress: "
                      f"identical location, light, wardrobe and identity, only "
                      f"the action has advanced proportionally. No morphing, "
                      f"no extra limbs.")
            # ТЕМ ЖЕ ДВИЖКОМ, ЧТО И ПАРА КАДРОВ. Иначе середина сцены
            # выпадает из стиля её краёв — и, что важнее, платим мы за один
            # движок, а рисуем другим: цена в /generate-midframes считается
            # именно по scene.image_engine.
            data, mime = asyncio.run(mediagen.generate_image(
                prompt, reference_path=ref, engine=scene.image_engine or "",
                resolution=(track.image_resolution or ""),
                aspect=_track_aspect(track)))
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


@app.delete("/api/scenes/{scene_id}/frames/{slot}")
def delete_scene_frame(slot: str, scene_id: int, user: User = Depends(current_user),
                       db: Session = Depends(db_session)):
    """Убрать один кадр сцены: "first", "last" или номер промежуточного.

    Кадры — опорные точки движения, и лишний портит сцену не меньше, чем
    недостающий. Удаляем и файл, а не только ссылку, иначе архив растёт от
    картинок, которых уже нигде не видно.
    """
    scene = _own_scene(db, user, scene_id)
    if slot == "first":
        if not scene.image_filename:
            raise HTTPException(404, "первого кадра нет")
        _remove_media(scene.image_filename)
        scene.image_filename = ""
    elif slot == "last":
        if not scene.image_last_filename:
            raise HTTPException(404, "последнего кадра нет")
        _remove_media(scene.image_last_filename)
        scene.image_last_filename = ""
    else:
        try:
            idx = int(slot)
        except ValueError:
            raise HTTPException(400, "непонятный кадр")
        mids = _midframes(scene)
        if not 0 <= idx < len(mids):
            raise HTTPException(404, "такого промежуточного кадра нет")
        _remove_media(mids[idx].get("filename", ""))
        mids.pop(idx)
        scene.midframes_json = json.dumps(mids, ensure_ascii=False)
    db.commit()
    return scene_dict(scene)


@app.post("/api/scenes/{scene_id}/frames/add")
def add_scene_frame(scene_id: int, user: User = Depends(current_user),
                    db: Session = Depends(db_session)):
    """Плюсик в сетке кадров: добавить кадр в ЛЮБОМ состоянии сцены.

    Промежуточный кадр рисуется по первому — он идёт референсом. Но у пустой
    сцены первого кадра нет, и кнопка отвечала отказом «сначала сгенерируй
    кадры»: человек нажимал «добавить кадр» и получал инструкцию вместо
    кадра. Теперь пустая сцена просто рисует свой первый кадр — это и есть
    «добавить кадр», когда добавлять пока не к чему.
    """
    _guard_disk()
    scene = _own_scene(db, user, scene_id)
    if not (scene.image_prompt or "").strip():
        raise HTTPException(400, "у сцены пуст промпт первого кадра — напиши его кнопкой «Промпт»")
    if not scene.image_filename:
        return generate_scene_frames(scene_id, which="first", engine="",
                                     user=user, db=db)
    eng = scene.image_engine or _plan_image_engine(user)
    _scene_charge(db, user, scene,
                  _image_cost(user, eng, scene.track.image_resolution or ""),
                  f"ещё один кадр сцены {scene.id}", kind="frames", engine=eng)
    db.commit()
    threading.Thread(target=_run_extra_midframe, args=(scene.id,), daemon=True).start()
    return {"ok": True}


def _run_extra_midframe(scene_id: int) -> None:
    """Один дополнительный кадр в хвост промежуточных."""
    import asyncio
    db = SessionLocal()
    try:
        scene = db.get(Scene, scene_id)
        if not scene:
            return
        track = scene.track
        ref = os.path.join(UPLOAD_DIR, scene.image_filename)
        mids = _midframes(scene)
        prompt = ((scene.image_prompt or "").strip()
                  + ". Same single continuous shot as the reference frame, the "
                    "same camera move in progress: identical location, light, "
                    "wardrobe and identity, only the action has advanced. "
                    "No morphing, no extra limbs.")
        data, mime = asyncio.run(mediagen.generate_image(
            prompt, reference_path=ref, engine=scene.image_engine or "",
            resolution=(track.image_resolution or ""), aspect=_track_aspect(track)))
        fname = _save_image(data, mime)
        _reg_file(db, fname, track.project.owner_id, kind="midframe",
                  project_id=track.project_id, track_id=track.id, scene_id=scene.id)
        mids.append({"filename": fname, "prompt": prompt})
        scene.midframes_json = json.dumps(mids, ensure_ascii=False)
        db.commit()
    except Exception as e:  # noqa: BLE001 — фон, ошибка не должна ронять поток
        db.rollback()
        log.warning("дополнительный кадр сцены %s упал: %s", scene_id, e)
    finally:
        db.close()


@app.post("/api/scenes/{scene_id}/generate-midframes")
def generate_midframes(scene_id: int, user: User = Depends(current_user), db: Session = Depends(db_session)):
    _guard_disk()
    scene = _own_scene(db, user, scene_id)
    total = _midframe_count(scene.duration_sec)
    if total <= 0:
        raise HTTPException(400, "сцена короткая — промежуточные кадры не нужны")
    if not scene.image_filename:
        raise HTTPException(400, "сначала сгенерируй кадры сцены — референсом идёт первый кадр")
    if not (scene.image_prompt or "").strip():
        raise HTTPException(400, "у сцены пуст промпт первого кадра")
    # ЦЕНА ПО ФАКТУ. Раньше здесь стояла цена ПАРЫ кадров (15 токенов на
    # Nano Banana Pro), а рисовалось до ЧЕТЫРЁХ картинок — то есть $0.36
    # себестоимости против 15 токенов выручки. Считаем по числу картинок тем
    # же движком, каким их и рисуем.
    eng = scene.image_engine or _plan_image_engine(user)
    _scene_charge(db, user, scene,
                  total * _image_cost(user, eng, scene.track.image_resolution or ""),
                  f"промежуточные кадры сцены {scene.id} ({total} шт.)",
                  kind="frames", engine=eng)
    _spawn_gen(user, _run_midframes, scene_id, kind="frames")
    return {"ok": True, "count": total}


# ───────────────── видео сцены + отрезок трека под неё ─────────────────

def _scene_frame_chain(scene: Scene) -> list[str]:
    """Все кадры сцены по порядку: первый, промежуточные, последний.

    Промежуточные раньше были только украшением карточки — видео рисовалось
    по двум крайним кадрам, а всё, что человек нарисовал между ними, движок
    не видел и придумывал переход сам. Теперь цепочка — это раскадровка
    сцены: сколько кадров, столько и опорных точек у движения.
    """
    out = []
    if scene.image_filename:
        out.append(scene.image_filename)
    for m in _midframes(scene):
        f = m.get("filename") or ""
        if f and os.path.exists(os.path.join(UPLOAD_DIR, f)):
            out.append(f)
    if scene.image_last_filename:
        out.append(scene.image_last_filename)
    return [f for f in out if os.path.exists(os.path.join(UPLOAD_DIR, f))]


def _backfill_video_sig() -> None:
    """Однократный бэкфилл светофора: видео, снятые до появления
    video_src_sig, считаем снятыми из текущих кадров — иначе весь старый
    контент разом «пожелтел» бы. Зовётся В КОНЦЕ модуля: на старте процесса
    _reset_orphan_jobs выполняется раньше определения _scene_frame_chain,
    и бэкфилл внутри него ронял весь сброс зависших задач."""
    db = SessionLocal()
    try:
        for sc in (db.query(Scene).filter(Scene.video_filename != "",
                                          Scene.video_src_sig == "").all()):
            sc.video_src_sig = "|".join(_scene_frame_chain(sc)) or "-"
        db.commit()
    except Exception as e:  # noqa: BLE001 — уборка не важнее старта
        log.warning("бэкфилл video_src_sig не прошёл: %s", e)
    finally:
        db.close()


def _concat_videos(parts: list[str], dest: str) -> bool:
    """Склейка отрезков сцены без перекодирования."""
    if len(parts) == 1:
        shutil.copyfile(parts[0], dest)
        return True
    lst = dest + ".txt"
    with open(lst, "w", encoding="utf-8") as fh:
        for part in parts:
            fh.write("file '" + part.replace("'", "'\\''") + "'\n")
    try:
        r = subprocess.run(
            ["ffmpeg", "-y", "-f", "concat", "-safe", "0", "-i", lst,
             "-c", "copy", dest],
            capture_output=True, timeout=900)
        if r.returncode != 0 or not os.path.exists(dest):
            # Отрезки от разных движков расходятся по кодеку — пересобираем.
            r = subprocess.run(
                ["ffmpeg", "-y", "-f", "concat", "-safe", "0", "-i", lst,
                 "-c:v", "libx264", "-preset", "veryfast", "-crf", "20",
                 "-pix_fmt", "yuv420p", dest],
                capture_output=True, timeout=1800)
        return r.returncode == 0 and os.path.exists(dest)
    except (OSError, subprocess.SubprocessError) as e:
        log.warning("склейка отрезков сцены не удалась: %s", str(e)[:200])
        return False
    finally:
        if os.path.exists(lst):
            os.remove(lst)


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
        # Первый кадр мог быть удалён: тогда опорным становится последний, и
        # движок оживляет его как единственный (без пары «первый→последний»).
        if scene.image_filename:
            first_path = os.path.join(UPLOAD_DIR, scene.image_filename)
            last_path = (
                os.path.join(UPLOAD_DIR, scene.image_last_filename)
                if scene.image_last_filename else None
            )
        else:
            first_path = os.path.join(UPLOAD_DIR, scene.image_last_filename)
            last_path = None
        import asyncio
        mediagen.reset_task()
        owner = db.get(User, track.project.owner_id) if track.project.owner_id else None
        # Семейство знает фронт, конкретную модель выбирает тариф — и она
        # уже записана на сцене при списании, чтобы движок не «переехал»
        # между оплатой и генерацией.
        engine = scene.video_engine or _plan_video_engine(owner, scene.video_provider)
        # Пустой motion_prompt валит генерацию на стороне движка
        # («input.prompt is required»), а сцена при этом выглядит готовой.
        # Собираем осмысленный запасной из того, что о кадре известно.
        motion = (scene.motion_prompt or "").strip()
        if not motion:
            bits = [b for b in (
                (scene.shot_note or "").strip(),
                (scene.camera_move or "").strip(),
            ) if b]
            base = (scene.image_prompt or "").strip()
            if base:
                bits.append(" ".join(base.split())[:220])
            motion = "; ".join(bits) or "subtle natural motion, slow camera drift, alive frame"
        # БЕСШОВНОСТЬ: движку говорим прямо, что это живая операторская
        # съёмка, а не нейро-морфинг. Дублей не плодим — клаузу добавляем,
        # только если её ещё нет в промпте.
        if "continuous real cinematography" not in motion:
            motion = (f"{motion}; footage must look like continuous real "
                      f"cinematography, invisible cuts; no morphing, no warping, "
                      f"no extra limbs, consistent identity and wardrobe")
        # Темп трека — в промпт движения: движение героя и камеры в темпе
        # дорожки читается как «попал в бит» даже до нарезки по битам.
        bpm = _track_bpm(track)
        if bpm and "BPM" not in motion.upper():
            motion = f"{motion}; movement paced to the music at {bpm:g} BPM"
        # Кадров может быть больше двух: тогда сцена рисуется отрезками
        # кадр→кадр и склеивается. Один кадр — тоже норма: движок оживит его
        # без конечной точки.
        chain = _scene_frame_chain(scene)
        if len(chain) > 2:
            legs = len(chain) - 1
            leg_sec = max(4, round((scene.duration_sec or 0) / legs)) if legs else 6
            parts = []
            for i in range(legs):
                a = os.path.join(UPLOAD_DIR, chain[i])
                b = os.path.join(UPLOAD_DIR, chain[i + 1])
                part = asyncio.run(mediagen.animate_scene(
                    prompt=motion, first_path=a, last_path=b,
                    duration_sec=leg_sec, provider=scene.video_provider,
                    seedance_model=PLANS[_plan_of(owner)].get("seedance_model", "") if owner else "",
                    engine=engine, aspect=_track_aspect(track),
                ))
                parts.append(os.path.join(UPLOAD_DIR, part))
            fname = f"scene_{scene.id}_{uuid.uuid4().hex[:8]}.mp4"
            if not _concat_videos(parts, os.path.join(UPLOAD_DIR, fname)):
                # Склейка не вышла — отдаём первый отрезок, а не пустоту.
                fname = os.path.basename(parts[0])
            else:
                for extra in parts:
                    _remove_media(os.path.basename(extra))
        else:
            fname = asyncio.run(mediagen.animate_scene(
                prompt=motion, first_path=first_path, last_path=last_path,
                duration_sec=scene.duration_sec, provider=scene.video_provider,
                seedance_model=PLANS[_plan_of(owner)].get("seedance_model", "") if owner else "",
                engine=engine, aspect=_track_aspect(track),
            ))
        # Задача внешнего движка — в строку списания: «списали 154 токена →
        # задача kie abc123». Без неё спорную генерацию разобрать нечем.
        _attach_task(db, "scene", scene.id, mediagen.last_task_id(), "video")
        # ПРЕДЫДУЩИЙ ДУБЛЬ УЕЗЖАЕТ В ИСТОРИЮ. Раньше здесь стояло
        # _remove_media(old_video): человек нажимал «перерендерить», получал
        # вариант хуже прежнего и обнаруживал, что прежнего больше нет. При
        # цене сцены до 152 токенов это не «перегенерация», а потеря денег.
        had_video = bool(scene.video_filename)
        if had_video:
            _keep_scene_version(db, scene, track, kind="video",
                                cost_points=int(scene.charged_points or 0))
        old_audio_only = "" if had_video else scene.audio_filename
        scene.video_filename = fname
        _reg_file(db, fname, track.project.owner_id, kind="video",
                  project_id=track.project_id, track_id=track.id, scene_id=scene.id)
        scene.video_status = "done"
        scene.video_seconds = mediagen.video_duration(fname)
        scene.video_src_sig = "|".join(_scene_frame_chain(scene))
        # Видео появилось — кадр идёт в клип. Раньше галочку надо было ставить
        # руками на каждый кадр, и оплаченные сцены не попадали в сборку
        # просто потому, что про них забыли. Осознанный отказ (человек сам снял
        # галочку) авто-приём не трогает.
        if not scene.approved_manual:
            scene.approved = True

        scene.video_stale = False    # видео снято под нынешний слот
        # Отрезок трека ровно под эту сцену — слушаем видео с его музыкой.
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
                old_audio_only = ""  # старый отрезок не трогаем, если новый не вышел
        db.commit()
        # Старый видеофайл НЕ стираем: на него смотрит версия. Убираем только
        # прежнюю нарезку дорожки, когда снимка не делали — она стоит ноль и
        # режется заново.
        _remove_media(old_audio_only, db)
        db.commit()
        log.info("видео сцены %s готово (%s)", scene_id, scene.video_provider)
    except Exception as e:  # noqa: BLE001
        db.rollback()
        scene = db.get(Scene, scene_id)
        if scene:
            scene.video_status = "error"
            scene.video_error = _err_text(e, 500)
            db.commit()
        log.warning("видео сцены %s упало: %s", scene_id, e)
    finally:
        db.close()


@app.post("/api/scenes/{scene_id}/generate-video")
async def generate_scene_video(scene_id: int, request: Request, user: User = Depends(current_user), db: Session = Depends(db_session)):
    _guard_disk()
    scene = _own_scene(db, user, scene_id)
    # Хватает ЛЮБОГО кадра: удалил первый — видео строится от последнего.
    # Требование именно первого заставляло перерисовывать то, что и так есть.
    if not (scene.image_filename or scene.image_last_filename):
        raise HTTPException(400, "сначала сгенерируй хотя бы один кадр сцены")
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
    _spawn_gen(user, _run_scene_video, scene_id, kind="video")
    return {"ok": True}


def _run_scene_full(scene_id: int) -> None:
    """Полный круг ОДНОЙ сцены: кадры → видео, последовательно в одном треде
    (та же логика, что у супергенерации, только для свежего кадра из
    cinema-бара). Видео не стартует, если кадры упали."""
    _run_scene_frames(scene_id)
    db = SessionLocal()
    try:
        scene = db.get(Scene, scene_id)
        ok = bool(scene and scene.image_filename and scene.image_status != "error")
        if ok:
            scene.video_status = "queued"
            db.commit()
    finally:
        db.close()
    if ok:
        _run_scene_video(scene_id)


@app.post("/api/scenes/{scene_id}/full-circle")
def scene_full_circle(scene_id: int, user: User = Depends(current_user),
                      db: Session = Depends(db_session)):
    """Кадры + видео сцены одной кнопкой (режим Video в cinema-баре)."""
    _guard_disk()
    scene = _own_scene(db, user, scene_id)
    if not scene.image_prompt.strip():
        raise HTTPException(400, "у сцены пуст промпт")
    if scene.image_status in ("queued", "running") or scene.video_status in ("queued", "running"):
        raise HTTPException(409, "сцена уже генерируется")
    provider = _allowed_provider(user, scene.video_provider or "seedance")
    vid_engine = _resolve_video_engine(user, scene.track, provider)
    cost = _scene_cost(user, provider, scene, vid_engine)
    _scene_charge(db, user, scene, cost,
                  f"полный круг сцены {scene.id} ({vid_engine})",
                  kind="video", engine=vid_engine)
    scene.video_provider = provider
    scene.video_engine = vid_engine
    scene.image_status = "queued"
    db.commit()
    _spawn_gen(user, _run_scene_full, scene_id, kind="video")
    return {"ok": True, "charged": cost}


@app.post("/api/scenes/{scene_id}/trim")
async def trim_scene_video(scene_id: int, request: Request,
                           user: User = Depends(current_user),
                           db: Session = Depends(db_session)):
    """Отрезать начало и/или конец у ГОТОВОГО видео сцены.

    Движок часто отдаёт лишнее: полсекунды разгона в начале или мёртвый хвост
    в конце. Перегенерация ради этого — новые токены и другой дубль, а нужно
    просто отрезать. Прежний ролик уходит в историю версий, слот сцены
    подгоняется под новую длину, и отрезок дорожки перерезается под неё же.
    """
    scene = _own_scene(db, user, scene_id)
    if not scene.video_filename:
        raise HTTPException(400, "у сцены нет видео — нечего обрезать")
    body = await request.json()
    full = (float(scene.video_seconds or 0)
            or mediagen.video_duration(scene.video_filename)
            or float(scene.duration_sec or 0))
    try:
        start = max(0.0, float(body.get("start") or 0))
        end = float(body.get("end") if body.get("end") is not None else full)
    except (TypeError, ValueError):
        raise HTTPException(400, "начало и конец должны быть числами")
    end = min(end, full) if full else end
    if end - start < 0.5:
        raise HTTPException(400, "после обрезки осталось бы меньше половины секунды")
    track = scene.track
    # Прежний дубль сохраняем ДО подмены: обрезка необратима, а человек
    # может захотеть вернуть полный ролик.
    _keep_scene_version(db, scene, track, kind="video",
                        cost_points=int(scene.charged_points or 0))
    old_video, old_audio = scene.video_filename, scene.audio_filename
    try:
        scene.video_filename = mediagen.trim_video(old_video, start, end)
    except mediagen.MediaError as e:
        raise HTTPException(400, str(e))
    _reg_file(db, scene.video_filename, track.project.owner_id, kind="video",
              project_id=track.project_id, track_id=track.id, scene_id=scene.id)
    # Слот сцены = длина того, что реально осталось: иначе сборка снова
    # подрежет ролик по старому слоту и обрезка потеряет смысл.
    scene.video_seconds = round(end - start, 2)
    scene.duration_sec = _clamp_dur(round(end - start))
    scene.video_stale = False
    if not scene.approved_manual:
        scene.approved = True
    _renumber_scenes(track)
    db.commit()
    # Прежний ролик остался в версии — с диска не убираем. Музыку под кадрами
    # перерезаем: слоты поехали, и дальше по треку тоже.
    threading.Thread(target=_resync_scene_audio, args=(track.id,), daemon=True).start()
    db.refresh(scene)
    return scene_dict(scene)


# ─────────────────────────── озвучка (ИИ-блогеры) ───────────────────────────

# ─────────────────────────── аудио-студия (дополнения) ───────────────────
# Генерация, мастеринг и анализ живут в music_api.py — здесь только то, чего
# там нет: стемы, тональность и отгрузка на лейбл.

# ─────────────────────── нарезка под бит ───────────────────────
# Сетка долей считается классическим MIR-набором (audio_analysis.py) за
# секунды CPU и без ключей. Кэш в памяти: разбор одного трека не меняется,
# пока не сменился файл дорожки.

_BEATS_CACHE: dict = {}
_BEATS_LOCK = threading.Lock()


def _track_beats(track: Track) -> dict:
    """Темп и сетка долей трека; пусто, если считать нечем или нечего."""
    import audio_analysis
    if not track.audio_filename or not audio_analysis.available():
        return {}
    key = (track.id, track.audio_filename)
    with _BEATS_LOCK:
        hit = _BEATS_CACHE.get(key)
    if hit is not None:
        return hit
    path = os.path.join(UPLOAD_DIR, track.audio_filename)
    if not os.path.exists(path):
        return {}
    try:
        a = audio_analysis.analyze(path)
        data = {"bpm": a.get("bpm") or 0,
                "bpm_confidence": a.get("bpm_confidence") or 0,
                "beats": a.get("beats") or [],
                "downbeats": a.get("downbeats") or [],
                "duration_sec": a.get("duration_sec") or 0}
    except Exception as e:  # noqa: BLE001
        log.warning("разбор долей трека %s не вышел: %s", track.id, e)
        data = {}
    with _BEATS_LOCK:
        _BEATS_CACHE[key] = data
        while len(_BEATS_CACHE) > 64:
            _BEATS_CACHE.pop(next(iter(_BEATS_CACHE)))
    return data


def _track_bpm(track: Track) -> float:
    """BPM для промптов движения — только из уже посчитанного кэша или из
    текстового профиля: гонять полный разбор ради одной строчки нельзя."""
    with _BEATS_LOCK:
        hit = _BEATS_CACHE.get((track.id, track.audio_filename))
    if hit:
        return float(hit.get("bpm") or 0)
    m = re.search(r"темп\s+([\d.]+)\s*BPM", track.audio_profile or "")
    return float(m.group(1)) if m else 0.0


@app.get("/api/tracks/{track_id}/beats")
def track_beats(track_id: int, user: User = Depends(current_user),
                db: Session = Depends(db_session)):
    """Сетка долей для волновой дорожки: метки пиков/долей и BPM."""
    track = _own_track(db, user, track_id)
    if not track.audio_filename:
        raise HTTPException(400, "сначала загрузи дорожку")
    data = _track_beats(track)
    if not data:
        raise HTTPException(422, "сетка долей не посчиталась (нет numpy или файл не читается)")
    return data


@app.post("/api/tracks/{track_id}/beat-align")
def beat_align(track_id: int, user: User = Depends(current_user),
               db: Session = Depends(db_session)):
    """Подвинуть границы сцен к ближайшим сильным долям трека.

    Правила: граница едет к ближайшему началу такта (downbeat, запас ±2 c),
    сцена не короче 2 секунд, сумма длительностей остаётся равной дорожке.
    Секундное разрешение честно ограничено схемой (duration_sec — целые
    секунды): граница попадает в ближайшую к такту целую секунду."""
    track = _own_track(db, user, track_id)
    scenes = sorted(track.scenes, key=lambda s: s.position)
    if len(scenes) < 2:
        raise HTTPException(400, "нарезать нечего — в раскадровке меньше двух кадров")
    data = _track_beats(track)
    if not data:
        raise HTTPException(422, "сетка долей не посчиталась — нарезка по битам недоступна")
    if float(data.get("bpm_confidence") or 0) < 0.2:
        raise HTTPException(422, "у трека нет выраженной доли (речь/эмбиент?) — "
                                 "честнее оставить нарезку по времени")
    duration = int(track.audio_duration_sec or round(data.get("duration_sec") or 0))
    grid = [float(t) for t in (data.get("downbeats") or [])]
    if len(grid) < 4:
        grid = [float(t) for t in (data.get("beats") or [])]
    if not grid:
        raise HTTPException(422, "долей не нашлось")

    # Границы: старые кумулятивные точки тянем к ближайшему такту.
    bounds = [0]
    cur = 0
    moved = 0
    for i, s in enumerate(scenes[:-1]):
        target = cur + max(2, int(s.duration_sec or 2))
        lo = bounds[-1] + 2
        hi = duration - 2 * (len(scenes) - 1 - i)
        cand = [g for g in grid if lo <= g <= hi and abs(g - target) <= 2.0]
        snapped = min(cand, key=lambda g: abs(g - target)) if cand else target
        b = int(round(max(lo, min(hi, snapped))))
        if b <= bounds[-1] + 1:
            b = bounds[-1] + 2
        bounds.append(b)
        cur = b
    bounds.append(max(duration, bounds[-1] + 2))

    for i, s in enumerate(scenes):
        new_dur = max(2, bounds[i + 1] - bounds[i])
        if new_dur != int(s.duration_sec or 0):
            moved += 1
            # Уже снятое видео не совпадает с новым слотом — говорим вслух,
            # но не удаляем: это оплаченные токены (см. video_stale в db.py).
            if s.video_filename:
                s.video_stale = True
        s.duration_sec = new_dur
        s.start_sec = bounds[i]
    db.commit()
    return {"ok": True, "moved": moved, "bpm": data.get("bpm"),
            "scenes": [{"id": s.id, "start_sec": s.start_sec,
                        "duration_sec": s.duration_sec} for s in scenes]}


@app.post("/api/tracks/{track_id}/music/key")
def music_key(track_id: int, user: User = Depends(current_user),
              db: Session = Depends(db_session)):
    """Тональность трека клипа в один клик (бесплатно, свой CPU)."""
    track = _own_track(db, user, track_id)
    if not track.audio_filename:
        raise HTTPException(400, "сначала загрузи дорожку")
    try:
        return music.detect_key(os.path.join(UPLOAD_DIR, track.audio_filename))
    except Exception as e:  # noqa: BLE001
        raise HTTPException(422, f"тональность не определилась: {str(e)[:150]}")


@app.post("/api/tracks/{track_id}/music/stems")
async def music_stems(track_id: int, user: User = Depends(current_user),
                      db: Session = Depends(db_session)):
    """Разложение на вокал и минус (kie Suno vocal removal)."""
    track = _own_track(db, user, track_id)
    if not track.audio_filename:
        raise HTTPException(400, "сначала загрузи дорожку")
    cost = _points_of_usd(0.05)
    _charge(db, user, cost, f"стемы трека {track.id}", kind="music")
    db.commit()
    url = pub_file_url(track.audio_filename)
    try:
        tid = await music.vocal_split_start(url)
    except RuntimeError as e:
        _refund(db, user, cost, "стемы не запустились")
        db.commit()
        raise HTTPException(502, str(e))
    return {"ok": True, "task_id": tid, "charged": cost}


@app.get("/api/music/stems/{task_id}")
async def music_stems_status(task_id: str, user: User = Depends(current_user)):
    try:
        return await music.vocal_split_status(task_id)
    except Exception as e:  # noqa: BLE001
        raise HTTPException(502, f"статус не читается: {str(e)[:150]}")


@app.post("/api/tracks/{track_id}/music/release")
async def music_release(track_id: int, request: Request,
                        user: User = Depends(current_user),
                        db: Session = Depends(db_session)):
    """«Отгрузить на лейбл» — заявка менеджеру в CRM.

    Прямого API дистрибуции пока нет (нужен аккаунт дистрибьютора и его
    ключи), поэтому один клик создаёт заявку со ссылкой на трек: менеджер
    отгружает руками, клиент видит статус. Честная механика вместо кнопки-
    пустышки."""
    track = _own_track(db, user, track_id)
    if not track.audio_filename:
        raise HTTPException(400, "сначала загрузи или сгенерируй трек")
    body = await request.json() if await request.body() else {}
    order = StudioOrder(
        user_id=user.id, contact=str(body.get("contact") or user.email or user.login or ""),
        name=f"Релиз: {track.title or 'без названия'}",
        brief=(f"Отгрузка трека на площадки.\n"
               f"Трек: {track.title}\nФайл: /api/media/{track.audio_filename}\n"
               f"Комментарий: {str(body.get('note') or '')[:500]}"),
        status="new", source="site")
    db.add(order)
    db.commit()
    return {"ok": True, "order_id": order.id}


@app.get("/api/voices")
def voices_list(user: User = Depends(current_user)):
    """Голоса ElevenLabs. enabled=false — контур ждёт ключ, интерфейс говорит
    об этом словами, а не пустым списком."""
    import asyncio
    if not voice.available():
        return {"enabled": False, "voices": []}
    return {"enabled": True, "voices": asyncio.run(voice.list_voices())}


def _char_voice(project: Project, name: str) -> tuple[str, str]:
    """(voice_id, voice_note) персонажа по имени; пусто — голос не закреплён."""
    for c in project.characters:
        if c.name.strip().lower() == (name or "").strip().lower():
            return (c.voice_id or "").strip(), getattr(c, "voice_note", "") or ""
    return "", ""


def _scene_voice_fallback(scene: Scene) -> tuple[str, str]:
    """Голос кадра, когда автор реплики не указан: speaker → персонажи кадра."""
    project = scene.track.project
    wanted = ([scene.speaker] if scene.speaker else [])         + [x.strip() for x in (scene.characters or "").split(",") if x.strip()]
    for name in wanted:
        vid, note = _char_voice(project, name)
        if vid:
            return vid, note
    return "", ""


async def _voice_scene_audio(scene: Scene, *, text: str = "",
                             voice_id: str = "", emotion: str = "") -> str:
    """Озвучка кадра → имя mp3. Диалог из нескольких реплик склеивается
    по порядку с паузами 0.35с; каждая реплика — голосом СВОЕГО персонажа.
    Явные text/voice_id из запроса главнее диалога (ручной режим)."""
    project = scene.track.project
    segments: list[tuple[str, str, str]] = []  # (text, voice_id, voice_note)
    if text or voice_id:
        line = (text or scene.lyric_line or scene.shot_note or "").strip()
        vid, note = (voice_id or ""), ""
        if not vid:
            vid, note = _scene_voice_fallback(scene)
        if not line:
            raise RuntimeError("у кадра нет реплики — напиши текст")
        if not vid:
            raise RuntimeError("выбери голос — или закрепи голос за персонажем в его досье")
        segments.append((line, vid, note))
    else:
        dialogue = _scene_dialogue(scene)
        if dialogue:
            for d in dialogue:
                line = str(d.get("line") or "").strip()
                if not line:
                    continue
                vid, note = _char_voice(project, str(d.get("who") or ""))
                if not vid:
                    vid, note = _scene_voice_fallback(scene)
                if not vid:
                    raise RuntimeError(
                        f"у персонажа «{d.get('who') or '?'}» не закреплён голос — "
                        f"выбери его в досье персонажа")
                segments.append((line, vid, note))
        else:
            line = (scene.lyric_line or "").strip()
            vid, note = _scene_voice_fallback(scene)
            if not line:
                raise RuntimeError("у кадра нет реплики")
            if not vid:
                raise RuntimeError("закрепи голос за персонажем в его досье")
            segments.append((line, vid, note))
    if not segments:
        raise RuntimeError("озвучивать нечего")
    files = []
    try:
        for line, vid, note in segments:
            files.append(await voice.tts(
                line, vid, UPLOAD_DIR,
                voice.settings_for(note, emotion)))
        if len(files) == 1:
            return files.pop()
        # Склейка реплик с паузами: apad на всех, кроме последней.
        out = f"voice_{uuid.uuid4().hex}.mp3"
        args = ["ffmpeg", "-y"]
        for f in files:
            args += ["-i", os.path.join(UPLOAD_DIR, f)]
        flt = "".join(
            f"[{i}:a]apad=pad_dur=0.35[a{i}];" if i < len(files) - 1
            else f"[{i}:a]anull[a{i}];"
            for i in range(len(files)))
        flt += "".join(f"[a{i}]" for i in range(len(files)))
        flt += f"concat=n={len(files)}:v=0:a=1[out]"
        args += ["-filter_complex", flt, "-map", "[out]",
                 os.path.join(UPLOAD_DIR, out)]
        r = subprocess.run(args, capture_output=True, timeout=300)
        if r.returncode != 0:
            raise RuntimeError(f"склейка реплик не вышла: {r.stderr.decode()[-150:]}")
        result, files_to_drop = out, files
        for f in files_to_drop:
            _remove_media(f)
        files = []
        return result
    finally:
        # Сюда с непустым files попадает только незаконченная работа
        # (успех либо pop'ает единственный файл, либо чистит список сам).
        for f in files:
            _remove_media(f)


@app.post("/api/scenes/{scene_id}/voiceover")
async def scene_voiceover(scene_id: int, request: Request,
                          user: User = Depends(current_user),
                          db: Session = Depends(db_session)):
    """Озвучить реплику кадра голосом блогера.

    Звук ложится в audio_filename сцены — то же поле, куда клип кладёт
    отрезок трека, поэтому сборка подхватывает голос без единой правки.
    Текст по умолчанию — реплика кадра (dialogue из body или lyric_line),
    голос по умолчанию — ГОЛОС ПЕРСОНАЖА СЦЕНЫ: сначала speaker, потом
    первый персонаж из characters с закреплённым voice_id. Эмоция:
    body.emotion главнее voice_note персонажа (см. voice.settings_for).
    """
    if not voice.available():
        raise HTTPException(503, "озвучка не настроена — нужен ключ ElevenLabs в infra/.env")
    scene = _own_scene(db, user, scene_id)
    body = await request.json()
    try:
        fname = await _voice_scene_audio(
            scene,
            text=str(body.get("text") or "").strip(),
            voice_id=str(body.get("voice_id") or "").strip(),
            emotion=str(body.get("emotion") or ""))
    except RuntimeError as e:
        code = 400 if ("голос" in str(e) or "реплик" in str(e)) else 502
        raise HTTPException(code, str(e))
    old = scene.audio_filename
    scene.audio_filename = fname
    _reg_file(db, fname, scene.track.project.owner_id, kind="audio",
              project_id=scene.track.project_id, track_id=scene.track_id,
              scene_id=scene.id)
    db.commit()
    if old and old != fname:
        _remove_media(old, db)
        db.commit()
    return {"ok": True, "audio_url": f"/api/media/{fname}"}


def _run_track_voiceover(track_id: int) -> None:
    """Озвучить серию пакетом: кадр за кадром, ошибки не роняют пакет."""
    import asyncio
    db = SessionLocal()
    try:
        track = db.get(Track, track_id)
        if not track:
            return
        done, skipped = 0, 0
        for scene in sorted(track.scenes, key=lambda x: x.position):
            has_lines = bool(_scene_dialogue(scene)) or bool(
                (scene.lyric_line or "").strip() and scene.speaker)
            if not has_lines:
                continue
            try:
                fname = asyncio.run(_voice_scene_audio(scene))
            except Exception as e:  # noqa: BLE001
                skipped += 1
                log.info("озвучка кадра %s пропущена: %s", scene.id, e)
                continue
            old = scene.audio_filename
            scene.audio_filename = fname
            _reg_file(db, fname, track.project.owner_id, kind="audio",
                      project_id=track.project_id, track_id=track.id,
                      scene_id=scene.id)
            db.commit()
            if old and old != fname:
                _remove_media(old, db)
                db.commit()
            done += 1
        log.info("озвучка серии %s: %s кадров, пропущено %s", track_id, done, skipped)
    finally:
        db.close()


@app.post("/api/tracks/{track_id}/voiceover")
def track_voiceover(track_id: int, user: User = Depends(current_user),
                    db: Session = Depends(db_session)):
    """«Озвучить серию»: все кадры с репликами — голосами их персонажей."""
    if not voice.available():
        raise HTTPException(503, "озвучка не настроена — нужен ключ ElevenLabs в infra/.env")
    track = _own_track(db, user, track_id)
    todo = [s for s in track.scenes
            if _scene_dialogue(s) or ((s.lyric_line or "").strip() and s.speaker)]
    if not todo:
        raise HTTPException(400, "в кадрах нет реплик — сначала сгенерируй раскадровку серии")
    _spawn_gen(user, _run_track_voiceover, track_id, kind="voice")
    return {"ok": True, "queued": len(todo)}


@app.post("/api/scenes/{scene_id}/approve")
async def approve_scene(scene_id: int, request: Request, user: User = Depends(current_user), db: Session = Depends(db_session)):
    """Утверждение ВИДЕО сцены: утверждённые идут в общий клип трека."""
    scene = _own_scene(db, user, scene_id)
    body = await request.json()
    approved = bool(body.get("approved", True))
    if approved and not scene.video_filename:
        raise HTTPException(400, "сначала сгенерируй видео сцены")
    scene.approved = approved
    # Ручной отказ — сигнал авто-приёму: эту сцену в клип не тащить.
    scene.approved_manual = not approved
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
        approved = [s for s in sorted(track.scenes, key=lambda x: (x.position, x.id))
                    if s.approved and s.video_filename]
        videos = [s.video_filename for s in approved]
        # СЛОТЫ РАСКАДРОВКИ. Движок отдаёт ролик своей длины (seevio, скажем,
        # не умеет короче четырёх секунд), и без этого списка собранный клип
        # шёл не по сетке раскадровки, а по тому, сколько кому захотелось
        # отдать: тайминги кадров переставали что-либо значить, а хвост молча
        # обрезался музыкой через -shortest.
        durations = [int(s.duration_sec or 0) for s in approved]
        old = track.clip_filename
        track.clip_filename = mediagen.assemble_clip(
            videos, _track_audio_path(track), film_grain=track.film_grain,
            aspect=_track_aspect(track), durations=durations)
        _reg_file(db, track.clip_filename, track.project.owner_id, kind="clip",
                  project_id=track.project_id, track_id=track.id)
        track.clip_status = "done"
        # Свежая склейка — по нынешним кадрам: метка «снят в прежнем стиле»
        # снимается ровно здесь и больше нигде.
        track.clip_stale = False
        # Отсчёт ретенции идёт от последнего обращения, а свежесобранный клип
        # человек как раз и собирался посмотреть.
        track.clip_seen_at = now()
        db.commit()
        _remove_media(old, db)
        db.commit()
        log.info("клип трека %s собран из %s сцен", track_id, len(videos))
    except Exception as e:  # noqa: BLE001
        db.rollback()
        track = db.get(Track, track_id)
        if track:
            track.clip_status = "error"
            track.clip_error = _err_text(e, 500)
            try:
                db.commit()
            except Exception:  # noqa: BLE001 — сессия отравлена, берём свежую
                db.rollback()
                fresh = SessionLocal()
                try:
                    row = fresh.get(Track, track_id)
                    if row:
                        row.clip_status = "error"
                        row.clip_error = _err_text(e, 500)
                        fresh.commit()
                finally:
                    fresh.close()
        log.warning("сборка клипа трека %s упала: %s", track_id, e)
    finally:
        db.close()


@app.post("/api/tracks/{track_id}/assemble")
def assemble_track_clip(track_id: int, user: User = Depends(current_user), db: Session = Depends(db_session)):
    from threading import Thread
    _guard_disk()
    track = _own_track(db, user, track_id)
    approved = [s for s in track.scenes if s.approved and s.video_filename]
    if not approved:
        raise HTTPException(400, "нет утверждённых сцен с видео")
    # ДОРОЖКУ ПРОВЕРЯЕМ ЗАРАНЕЕ. Битый файл валит сборку на самом последнем
    # шаге — наложении звука, когда все сцены уже перекодированы: минуты
    # работы впустую, а в интерфейсе просто «собираю клип…».
    if track.audio_filename:
        apath = os.path.join(UPLOAD_DIR, track.audio_filename)
        if not os.path.exists(apath) or os.path.getsize(apath) < MIN_AUDIO_BYTES:
            track.clip_status = "error"
            track.clip_error = "дорожка повреждена или не догрузилась — загрузи её заново"
            db.commit()
            raise HTTPException(422, track.clip_error)
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
            # хотя бы один кадр есть — сцена рабочая (видео умеет идти от одного)
            if not (s and (s.image_filename or s.image_last_filename)):
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
        note(f"супергенерация упала: {_err_text(e, 200)}", "error")
        log.warning("супергенерация трека %s упала: %s", track_id, e)
    finally:
        db.close()


@app.post("/api/tracks/{track_id}/supergen")
def supergen(track_id: int, user: User = Depends(current_user), db: Session = Depends(db_session)):
    _guard_disk()
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
            if not (s.image_filename or s.image_last_filename):
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
    _spawn_gen(user, _run_supergen, track_id, per_scene, prepaid, kind="supergen")
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
    # Отметка «клип смотрели». Ретенция снимает с диска только те склейки, к
    # которым давно не возвращались, — а «давно» надо чем-то мерить. Запрос
    # делается ТОЛЬКО для клипов (имя начинается с clip_), то есть на
    # единицы обращений, а не на каждую миниатюру.
    if fname.startswith("clip_"):
        try:
            tr = db.query(Track).filter(Track.clip_filename == fname).first()
            if tr is not None:
                tr.clip_seen_at = now()
                db.commit()
        except Exception:  # noqa: BLE001 — отметка не стоит отказа в файле
            db.rollback()
    return _media_response(path, request)


# ───────────────────── экспорт кадра в большом размере ─────────────────────
# Кадры хранятся в НАТИВНОМ разрешении движка (см. _save_image): 4К-апскейл
# не добавлял информации, но стоил 12 МБ на кадр — 2.29 ГБ на проде за 183
# картинки. Большой файл нужен изредка и поштучно, поэтому собирается на лету
# и не хранится вовсе.
EXPORT_DIR = os.environ.get("EXPORT_DIR", "/data/exports")
os.makedirs(EXPORT_DIR, exist_ok=True)


@app.get("/api/export/frame/{filename}")
def export_frame(filename: str, res: str = "4k", aspect: str = "",
                 user: User = Depends(current_user),
                 db: Session = Depends(db_session)):
    """Кадр в большом размере одним файлом. Ничего не хранит: собранная копия
    живёт в каталоге экспорта и убирается уборщиком через час."""
    fname = os.path.basename(filename)
    src = os.path.join(UPLOAD_DIR, fname)
    if not os.path.exists(src):
        raise HTTPException(404, "файл не найден")
    _check_file_owner(db, user, fname)
    stem = os.path.splitext(fname)[0]
    out_name = f"exp_{stem}_{str(res).lower()}.png"
    out = os.path.join(EXPORT_DIR, out_name)
    if not os.path.exists(out):
        if not mediagen.export_frame(src, out, aspect=aspect or mediagen.DEFAULT_ASPECT,
                                     res=res):
            # Не собралось — отдаём оригинал: он меньше, но он есть.
            return FileResponse(src)
    return FileResponse(out, filename=out_name)


# ───────────────────── файлы стилей (админка стилей) ─────────────────────
# Отдельный каталог в томе /data, а не в образе: постеры, примеры кадров и
# референсы кладёт владелец через админку, и переживать пересборку они
# обязаны. Раздаются ПУБЛИЧНО — витрина стилей открыта без аккаунта, и
# постер стиля такой же публичный объект, как его описание.
STYLE_ASSETS_DIR = os.environ.get("STYLE_ASSETS_DIR", "/data/styles")
os.makedirs(STYLE_ASSETS_DIR, exist_ok=True)


def _remove_style_asset(filename: str) -> None:
    """Стереть файл стиля с диска. Отдельно от _remove_media: медиа людей
    живут в UPLOAD_DIR и учитываются в архиве, файлы стилей — витрина
    сервиса и своего архива не имеют."""
    fname = os.path.basename(filename or "")
    if not fname:
        return
    path = os.path.join(STYLE_ASSETS_DIR, fname)
    try:
        if os.path.exists(path):
            os.remove(path)
    except OSError as e:
        log.warning("файл стиля %s не удалился: %s", fname, e)


@app.get("/style-assets/{filename}")
def style_asset(filename: str, request: Request):
    """Публичная раздача файла стиля. Ровно так же публична, как /img/styles/*
    в образе: это витрина, а не приватное медиа человека."""
    fname = os.path.basename(filename)
    path = os.path.join(STYLE_ASSETS_DIR, fname)
    if not os.path.exists(path):
        raise HTTPException(404, "файл не найден")
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


@app.get("/api/text-models")
def text_models(project_id: int | None = None,
                user: User = Depends(current_user),
                db: Session = Depends(db_session)):
    """Текстовые модели сценарного блока — по образцу /api/providers.

    Честность здесь дороже ассортимента: позиции без ключа обычному
    человеку не показываются вовсе (продавать то, чего нет, нельзя), а
    закрытая тарифом показывается с замком и подписью «в PRO» — как
    locked-стили в пикере. Цена в токенах приезжает отсюда же, из одной
    кассы с кадрами и видео."""
    plan_id = _plan_of(user)
    project = None
    if project_id:
        project = db.get(Project, int(project_id))
        if project and not _owned(user, project):
            project = None
    current = _resolve_text_engine(user, project)
    engines = []
    for row in textgen.public_engines(plan_id, admin=bool(user.is_admin),
                                      current=current):
        row["points"] = TEXT_COST.get(row["id"], 0)
        # Долларовую себестоимость наружу отдаём только админу: из неё
        # восстанавливается наша наценка.
        if not user.is_admin:
            row.pop("usd", None)
        engines.append(row)
    return {
        "engines": engines,
        "current": current,
        "chosen": (project.text_engine or "") if project else "",
        "plan": plan_id,
        # Порядок тарифов нужен интерфейсу, чтобы подписать замок («в PRO»),
        # а не выдумывать свою лестницу.
        "plan_order": list(textgen.PLAN_ORDER),
    }


@app.get("/api/providers")
def providers(user: User = Depends(current_user), db: Session = Depends(db_session)):
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
    # ТУМБЛЕРЫ ВЛАДЕЛЬЦА. Выключенная в админке модель не должна светиться у
    # клиентов — раньше фильтр стоял только в Генераторе, а витрина студии
    # шла этим путём и показывала всё.
    off = _disabled_models(db)
    engines = [e for e in engines if f"video:{e['id']}" not in off]
    images = [i for i in images if f"image:{i['id']}" not in off]
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


def _clone_character_into(db: Session, source: Character, project: Project) -> Character:
    """Копия персонажа в проект — вместе с фото, разворотами и атрибутами.

    Копия самостоятельная: удаление оригинала (или его фото) клона не ломает.
    Раньше фото копировались БАЙТАМИ ровно ради этой самостоятельности, но
    жёсткая ссылка даёт её тоже и стоит ноль: файл живёт, пока на него
    смотрит хоть одно имя, а _remove_media снимает ровно одно имя."""
    max_pos = max((c.position for c in project.characters), default=0)
    # Главный герой в проекте один (см. update_character): статус переносится
    # только если место главного в целевом проекте ещё свободно.
    has_main = any(c.is_main for c in project.characters)
    clone = Character(
        position=max_pos + 1,
        name=source.name, description=source.description,
        voice_id=source.voice_id or "",
        is_main=bool(source.is_main and not has_main),
    )
    # ЧЕРЕЗ КОЛЛЕКЦИЮ, А НЕ db.add(project_id=…) — иначе копия проекта
    # ДВОИТ героев. У только что созданного проекта collection «characters»
    # уже materialized пустым списком, и db.add в обход неё его не обновляет:
    # следующий читатель (_bring_characters в конце _copy_track_into, да и
    # max_pos/has_main выше) видит проект без героев и клонирует Рому второй
    # раз. Append держит коллекцию правдивой в пределах сессии, а project_id
    # проставит сам relationship на flush.
    project.characters.append(clone)
    db.flush()
    for i, ph in enumerate(source.photos, start=1):
        fname = _clone_media(db, ph.filename, owner_id=project.owner_id,
                             kind="photo", project_id=project.id, prefix="char")
        if not fname:
            continue    # битую ссылку на пропавший файл не тиражируем
        # kind переносим вместе с файлом: иначе клон терял свой разворот и
        # его кадры снова опирались бы на селфи.
        db.add(CharacterPhoto(character_id=clone.id, position=i, filename=fname,
                              kind=ph.kind or "photo", pose_kind=ph.pose_kind or "",
                              from_photos=int(ph.from_photos or 0)))
    # Атрибуты — часть образа персонажа: клон получает их вместе с фото.
    for attr in source.attributes:
        attr_clone = CharacterAttribute(
            character_id=clone.id, position=attr.position,
            name=attr.name, description=attr.description,
        )
        db.add(attr_clone)
        db.flush()
        for i, ph in enumerate(attr.photos, start=1):
            fname = _clone_media(db, ph.filename, owner_id=project.owner_id,
                                 kind="attr", project_id=project.id, prefix="attr")
            if not fname:
                continue
            db.add(AttributePhoto(attribute_id=attr_clone.id, position=i, filename=fname))
    return clone


@app.post("/api/characters/clone")
async def clone_character(request: Request, project_id: int | None = None, user: User = Depends(current_user), db: Session = Depends(db_session)):
    """Клонирование персонажа из библиотеки в проект."""
    body = await request.json()
    source = db.get(Character, int(body.get("source_id") or 0))
    if not source or not _owned(user, source.project):
        raise HTTPException(404, "исходный персонаж не найден")
    project = get_or_create_project(db, user, project_id)
    clone = _clone_character_into(db, source, project)
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
    if "voice_id" in body:
        ch.voice_id = str(body["voice_id"] or "").strip()[:80]
    if "voice_note" in body:
        ch.voice_note = str(body["voice_note"] or "").strip()[:500]
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


def _run_item_from_photo(track_id: int, src_path: str, engine: str, cost: int) -> None:
    """Чистая моделька предмета по живому фото: один кадр, нейтральный фон.
    Результат ложится ФОТО предмета (kind="model") — дальше он работает
    референсом во всех генерациях, как разворот у персонажа."""
    db = SessionLocal()
    try:
        track = db.get(Track, track_id)
        if not track:
            return
        prompt = (
            "the exact product from the reference photo, isolated on a clean "
            "neutral light-grey background, studio product photography, soft "
            "even lighting, subtle contact shadow, no props, no text overlays, "
            "the whole product in frame, every label letter and proportion "
            "identical to the reference, square frame")
        import asyncio
        mediagen.reset_task()
        res = asyncio.run(mediagen.generate_image_ex(
            prompt, src_path, engine=engine,
            resolution=(track.image_resolution or "").strip(), aspect="1:1"))
        fname = _save_image(res["data"], res["mime"])
        _reg_file(db, fname, track.project.owner_id, kind="model",
                  project_id=track.project_id, track_id=track.id)
        max_pos = max((p.position for p in track.photos), default=0)
        db.add(TrackPhoto(track_id=track.id, position=max_pos + 1,
                          filename=fname, kind="model"))
        track.turnaround_status = ""
        track.turnaround_note = ""
        db.commit()
        log.info("моделька предмета %s готова", track_id)
    except Exception as e:  # noqa: BLE001
        db.rollback()
        track = db.get(Track, track_id)
        if track:
            track.turnaround_status = "error"
            track.turnaround_note = _err_text(e, 200)
            owner = (db.get(User, track.project.owner_id)
                     if track.project.owner_id else None)
            db.commit()
            if owner and cost > 0:
                _refund(db, owner, cost, f"моделька предмета {track_id}: не вышло",
                        ref_type="track", ref_id=track_id,
                        track_id=track_id, project_id=track.project_id)
        log.warning("моделька предмета %s упала: %s", track_id, e)
    finally:
        db.close()


@app.post("/api/items/from-photo")
async def item_from_photo(photo: UploadFile, project_id: int,
                          title: str = "", user: User = Depends(current_user),
                          db: Session = Depends(db_session)):
    """«Сделать предмет по фото»: живой снимок → чистый предметный рендер,
    сохранённый как ПРЕДМЕТ. Цена — один кадр."""
    _guard_disk()
    project = db.get(Project, int(project_id))
    if not project or not _owned(user, project):
        raise HTTPException(404, "проект не найден")
    ext = os.path.splitext(photo.filename or "")[1].lower() or ".jpg"
    if ext not in (".jpg", ".jpeg", ".png", ".webp"):
        raise HTTPException(400, "поддерживаются jpg/png/webp")
    fname = f"item_{uuid.uuid4().hex}{ext}"
    src_path = os.path.join(UPLOAD_DIR, fname)
    with open(src_path, "wb") as f:
        f.write(await photo.read())

    track = Track(
        project_id=project.id,
        position=(db.query(func.coalesce(func.max(Track.position), 0))
                  .filter(Track.project_id == project.id).scalar() or 0) + 1,
        title=(title or "").strip() or "Предмет",
        lyrics="", comment="", style="", style_keys="",
    )
    db.add(track)
    db.flush()
    _reg_file(db, fname, project.owner_id, kind="photo",
              project_id=project.id, track_id=track.id)
    db.add(TrackPhoto(track_id=track.id, position=1, filename=fname, kind="photo"))

    engine = _resolve_image_engine(user, track)
    cost = _mockup_frame_cost(user, engine)
    _charge(db, user, cost, f"моделька предмета «{track.title}»",
            kind="frames", engine=engine,
            cost_cents=_cost_cents("image", engine),
            ref_type="track", ref_id=track.id,
            track_id=track.id, project_id=project.id)
    track.turnaround_status = "running"
    track.turnaround_note = "моделька…"
    db.commit()
    _spawn_gen(user, _run_item_from_photo, track.id, src_path, engine, cost,
               kind="frames")
    return {"ok": True, "charged": cost, "track_id": track.id}


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


# ─────────────── 3D-ОБЛЁТ ТОВАРА: 8 ракурсов по кругу ───────────────
# Тот же конвейер, что у кадров сцен: референс-фото товара + промпт ракурса →
# mediagen.generate_image_ex цепочкой «запрос → трек → тариф». Списание — как
# 8 кадров (4 пары по прайсу FRAME_COST), возврат — за ненарисованное.


def _turnaround_ref_path(track: Track) -> str:
    """Референс товара: последний kind="model", если он есть, иначе первое
    фото — то же правило, что у кадров сцен мокапа."""
    photos = sorted(track.photos, key=lambda p: (p.position, p.id))
    if not photos:
        return ""
    model = [p for p in photos if (p.kind or "") == "model"]
    ph = model[-1] if model else photos[0]
    path = os.path.join(UPLOAD_DIR, ph.filename)
    return path if os.path.exists(path) else ""


def _run_turnaround(track_id: int, engine: str, cost: int) -> None:
    db = SessionLocal()
    files: list[str] = []
    total = len(TURNAROUND_YAWS)
    try:
        track = db.get(Track, track_id)
        if not track:
            return
        track.turnaround_status = "running"
        track.turnaround_note = f"0/{total}"
        db.commit()
        ref_path = _turnaround_ref_path(track)
        if not ref_path:
            raise RuntimeError("нет фото товара")
        old = _turnaround_files(track)
        img_res = (track.image_resolution or "").strip()
        import asyncio
        mediagen.reset_task()
        for i, yaw in enumerate(TURNAROUND_YAWS, start=1):
            prompt = (
                "same exact product as in the reference photo, studio product "
                "photography, clean seamless light background, the camera is "
                "orbiting the product on a turntable, view from "
                f"{yaw} degrees yaw around the product, identical soft studio "
                "lighting in every shot, the product perfectly centered, same "
                "distance and framing, every label letter identical to the "
                "reference, square frame")
            res = asyncio.run(mediagen.generate_image_ex(
                prompt, ref_path, engine=engine,
                resolution=img_res, aspect="1:1"))
            fname = _save_image(res["data"], res["mime"])
            _reg_file(db, fname, track.project.owner_id, kind="turnaround",
                      project_id=track.project_id, track_id=track.id)
            files.append(fname)
            track.turnaround_note = f"{i}/{total}"
            db.commit()
        track.turnaround_files = json.dumps(files)
        track.turnaround_status = "done"
        track.turnaround_note = ""
        db.commit()
        # Старый облёт уезжает только ПОСЛЕ успешного нового: упавшая
        # перегенерация не должна оставить трек вовсе без вьюера.
        for f in old:
            _remove_media(f, db)
        db.commit()
        log.info("3D-облёт трека %s готов (%s ракурсов)", track_id, total)
    except Exception as e:  # noqa: BLE001
        db.rollback()
        track = db.get(Track, track_id)
        if track:
            track.turnaround_status = "error"
            track.turnaround_note = _err_text(e, 300)
            db.commit()
            # Возврат за ненарисованные ракурсы — с точностью до кадра.
            owner = (db.get(User, track.project.owner_id)
                     if track.project.owner_id else None)
            left = cost * (total - len(files)) // total
            if owner and left > 0:
                _refund(db, owner, left, f"3D-облёт товара {track_id}: не вышло",
                        ref_type="track", ref_id=track_id,
                        track_id=track_id, project_id=track.project_id)
        log.warning("3D-облёт трека %s упал: %s", track_id, e)
    finally:
        db.close()


@app.post("/api/tracks/{track_id}/turnaround")
def generate_turnaround(track_id: int, engine: str = "",
                        user: User = Depends(current_user),
                        db: Session = Depends(db_session)):
    _guard_disk()
    track = _own_track(db, user, track_id)
    if not track.photos:
        raise HTTPException(400, "сначала добавь фото товара")
    if track.turnaround_status in ("queued", "running"):
        raise HTTPException(409, "облёт уже генерируется")
    engine = _resolve_image_engine(user, track, engine)
    pairs = len(TURNAROUND_YAWS) // 2
    cost = _frames_cost(user, None, engine) * pairs
    _charge(db, user, cost, f"3D-облёт товара «{track.title or track.id}»",
            kind="frames", engine=engine,
            cost_cents=_cost_cents("frames", engine, count=pairs,
                                   resolution=(track.image_resolution or "").strip()),
            ref_type="track", ref_id=track.id,
            track_id=track.id, project_id=track.project_id)
    track.turnaround_status = "queued"
    track.turnaround_note = ""
    db.commit()
    _spawn_gen(user, _run_turnaround, track.id, engine, cost, kind="frames")
    return {"ok": True, "charged": cost}


# ─────────────── Маркетинг-студия: шаблоны мокапов ───────────────
# Каталог готовых предметных сцен (mockup_catalog.py): человек выбирает
# шаблон или пишет свой промпт, референсами едут фото товара и/или моделька
# персонажа, результат ложится ОБЫЧНОЙ сценой трека — дальше его можно
# оживить и собрать в клип штатным конвейером.

MOCKUP_PREVIEWS_FILE = os.path.join(UPLOAD_DIR, "mockup_previews.json")
MARKETING_CAMERAS = {
    "closeup": "Extreme close-up framing, the product fills most of the frame, macro detail.",
    "medium": "Medium shot framing, the product prominent with some scene context around it.",
    "wide": "Wide shot framing, the full scene visible, the product a clear focal point.",
    "": "",
}
MARKETING_ASPECTS = ("9:16", "3:4", "1:1")


def _mockup_previews() -> dict:
    try:
        with open(MOCKUP_PREVIEWS_FILE, encoding="utf-8") as f:
            v = json.load(f)
        return v if isinstance(v, dict) else {}
    except Exception:  # noqa: BLE001
        return {}


def _mockup_previews_save(m: dict) -> None:
    tmp = MOCKUP_PREVIEWS_FILE + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(m, f, ensure_ascii=False)
    os.replace(tmp, MOCKUP_PREVIEWS_FILE)


def _mockup_frame_cost(user: User, engine: str) -> int:
    """Цена ОДНОГО кадра. FRAME_COST — прайс пары (см. _frames_cost),
    поэтому одиночный кадр = половина, но не меньше 1."""
    return max(1, _frames_cost(user, None, engine) // 2)


@app.get("/api/mockup/templates")
def mockup_templates(user: User = Depends(current_user)):
    del user  # каталог одинаков для всех, но виден только своим
    previews = _mockup_previews()
    out = []
    for tpl in mockup_catalog.TEMPLATES:
        fname = previews.get(tpl["id"]) or ""
        out.append({
            "id": tpl["id"], "ru": tpl["ru"], "en": tpl["en"],
            "category": tpl["category"], "tara": tpl["tara"],
            "emoji": tpl["emoji"], "motion": bool(tpl.get("motion")),
            "prompt": mockup_catalog.scene_prompt(tpl),
            "preview_url": f"/api/media/{fname}" if fname else "",
        })
    return {"templates": out, "categories": list(mockup_catalog.CATEGORIES)}


def _run_marketing_frame(track_id: int, scene_id: int, prompt: str,
                         ref_paths: list[str], engine: str, aspect: str,
                         cost: int) -> None:
    db = SessionLocal()
    try:
        track = db.get(Track, track_id)
        scene = db.get(Scene, scene_id)
        if not track or not scene:
            return
        scene.image_status = "running"
        db.commit()
        img_res = (track.image_resolution or "").strip()
        # Шлюзы берут одну картинку — несколько референсов склеиваем в
        # коллаж (как _scene_reference_photo); Nano Banana получит список.
        single = ref_paths[0] if ref_paths else None
        if len(ref_paths) > 1:
            single = _ref_collage(db, ref_paths[:4],
                                  track.project.owner_id) or ref_paths[0]
        import asyncio
        mediagen.reset_task()
        res = asyncio.run(mediagen.generate_image_ex(
            prompt, single, reference_paths=ref_paths,
            engine=engine, resolution=img_res, aspect=aspect))
        fname = _save_image(res["data"], res["mime"])
        _reg_file(db, fname, track.project.owner_id, kind="frame",
                  project_id=track.project_id, track_id=track.id,
                  scene_id=scene.id)
        scene.image_filename = fname
        scene.image_engine = res.get("engine") or engine
        scene.image_status = ""
        scene.image_error = ""
        scene.style_keys = track.style_keys or ""
        db.commit()
        log.info("маркетинг-кадр сцены %s трека %s готов", scene_id, track_id)
    except Exception as e:  # noqa: BLE001
        db.rollback()
        scene = db.get(Scene, scene_id)
        if scene:
            scene.image_status = "error"
            scene.image_error = _err_text(e, 300)
            db.commit()
        track = db.get(Track, track_id)
        if track:
            owner = (db.get(User, track.project.owner_id)
                     if track.project.owner_id else None)
            if owner and cost > 0:
                _refund(db, owner, cost, f"маркетинг-кадр трека {track_id}: не вышло",
                        ref_type="scene", ref_id=scene_id,
                        track_id=track_id, project_id=track.project_id)
        log.warning("маркетинг-кадр сцены %s упал: %s", scene_id, e)
    finally:
        db.close()


def _marketing_generate(db: Session, user: User, track: Track, *,
                        template_id: str = "", prompt: str = "",
                        camera: str = "", aspect: str = "",
                        character_id: int = 0, use_product: bool = True,
                        product_track_id: int = 0) -> dict:
    """Общая механика POST marketing-gen / mockup-from-template."""
    _guard_disk()
    tpl = mockup_catalog.get(template_id) if template_id else None
    if template_id and not tpl:
        raise HTTPException(404, "нет такого шаблона")
    base = mockup_catalog.scene_prompt(tpl) if tpl else (prompt or "").strip()
    if not base:
        raise HTTPException(400, "нужен шаблон или свой промпт")
    if prompt and tpl:
        base = base + " " + prompt.strip()
    cam = MARKETING_CAMERAS.get((camera or "").strip(), "")
    if cam:
        base = base + " " + cam
    aspect = (aspect or "").strip() or "1:1"
    if aspect not in MARKETING_ASPECTS:
        aspect = "1:1"

    refs: list[str] = []
    if use_product:
        # Товар можно взять из ЛЮБОГО своего проекта: база предметов общая.
        src = track
        if product_track_id and int(product_track_id) != track.id:
            src = db.get(Track, int(product_track_id))
            if not src or src.project.owner_id != user.id:
                raise HTTPException(404, "нет такого товара")
        refs += _track_photo_paths(src, 4)
        if not refs:
            raise HTTPException(400, "сначала добавь фото товара")
        base = mockup_catalog.PRODUCT_CLAUSE + base \
            if not tpl else base  # у шаблона охрана этикетки уже вшита
    char = None
    if character_id:
        # База героев ОБЩАЯ: персонаж может жить в любом проекте владельца.
        char = db.get(Character, int(character_id))
        char_proj = db.get(Project, char.project_id) if char else None
        if not char or not char_proj or char_proj.owner_id != user.id:
            raise HTTPException(404, "нет такого персонажа")
        cpaths = _character_model_paths([char], 3, prefer_photo=True)
        if not cpaths:
            raise HTTPException(400, "у персонажа нет фото")
        refs += cpaths
        base += (f" The person in the scene is '{char.name}' — match the face "
                 "and look of that person exactly as in the reference photos.")
    if not refs:
        raise HTTPException(400, "нужен хотя бы один референс: товар или персонаж")
    seen: set[str] = set()
    refs = [p for p in refs if not (p in seen or seen.add(p))][:8]

    engine = _resolve_image_engine(user, track)
    cost = _mockup_frame_cost(user, engine)
    what = (f"кадр по шаблону «{tpl['ru']}»" if tpl else "маркетинг-кадр") \
        + f" — «{track.title or track.id}»"
    _charge(db, user, cost, what, kind="frames", engine=engine,
            cost_cents=_cost_cents("image", engine,
                                   resolution=(track.image_resolution or "").strip()),
            ref_type="track", ref_id=track.id,
            track_id=track.id, project_id=track.project_id)

    scene = Scene(
        track_id=track.id,
        position=(max((s.position for s in track.scenes), default=0) + 1),
        duration_sec=6,
        image_prompt=base,
        shot_note=(f"готовый кадр по шаблону «{tpl['ru']}»" if tpl
                   else "маркетинг-кадр по своему промпту"),
        image_status="queued",
        image_engine=engine,
        charged_points=cost,
        characters=(char.name if char else ""),
    )
    db.add(scene)
    db.commit()
    _spawn_gen(user, _run_marketing_frame, track.id, scene.id, base,
               refs, engine, aspect, cost, kind="frames")
    return {"ok": True, "charged": cost, "scene_id": scene.id}


@app.post("/api/tracks/{track_id}/marketing-gen")
async def marketing_gen(track_id: int, request: Request,
                        user: User = Depends(current_user),
                        db: Session = Depends(db_session)):
    body = await request.json()
    track = _own_track(db, user, track_id)
    return _marketing_generate(
        db, user, track,
        template_id=str(body.get("template_id") or ""),
        prompt=str(body.get("prompt") or ""),
        camera=str(body.get("camera") or ""),
        aspect=str(body.get("aspect") or ""),
        character_id=int(body.get("character_id") or 0),
        use_product=bool(body.get("use_product", True)),
        product_track_id=int(body.get("product_track_id") or 0),
    )


@app.post("/api/tracks/{track_id}/mockup-from-template")
async def mockup_from_template(track_id: int, request: Request,
                               user: User = Depends(current_user),
                               db: Session = Depends(db_session)):
    body = await request.json()
    track = _own_track(db, user, track_id)
    return _marketing_generate(
        db, user, track,
        template_id=str(body.get("template_id") or ""),
        use_product=True,
    )


def generate_mockup_previews(ids: list[str] | None = None,
                             engine: str = "chatgpt") -> dict:
    """Превью каталога нейтральной бутылкой через бесплатный шлюз.

    Зовётся из админ-эндпоинта и из CLI (docker exec … python3 -c). Без ids
    генерит витринные (showcase) шаблоны без превью; ids=["*"] — все."""
    db = SessionLocal()
    done, failed = [], []
    try:
        previews = _mockup_previews()
        todo = []
        for tpl in mockup_catalog.TEMPLATES:
            if previews.get(tpl["id"]):
                continue
            if ids and "*" not in ids and tpl["id"] not in ids:
                continue
            if not ids and not tpl.get("showcase"):
                continue
            todo.append(tpl)
        import asyncio
        for tpl in todo:
            try:
                mediagen.reset_task()
                res = asyncio.run(mediagen.generate_image_ex(
                    mockup_catalog.preview_prompt(tpl), None,
                    engine=engine, aspect="3:4"))
                fname = _save_image(res["data"], res["mime"], upscale=False)
                _reg_file(db, fname, None, kind="mockup_preview")
                db.commit()
                previews = _mockup_previews()
                previews[tpl["id"]] = fname
                _mockup_previews_save(previews)
                done.append(tpl["id"])
            except Exception as e:  # noqa: BLE001
                failed.append(f"{tpl['id']}: {_err_text(e, 120)}")
                log.warning("превью шаблона %s не вышло: %s", tpl["id"], e)
        return {"done": done, "failed": failed}
    finally:
        db.close()


def _own_item_id(db: Session, user: User, raw) -> int:
    """Проверка «предмет мой»: id трека владельца или 0. Чужой id молча в
    ноль не превращаем — это была бы тихая потеря привязки."""
    tid = int(raw or 0)
    if not tid:
        return 0
    tr = db.get(Track, tid)
    if not tr or tr.project.owner_id != user.id:
        raise HTTPException(404, "нет такого предмета")
    return tid


def _item_dict(tr: Track) -> dict:
    photos = sorted(tr.photos, key=lambda x: (x.position, x.id))
    return {
        "id": tr.id, "track_id": tr.id, "project_id": tr.project_id,
        "title": tr.title or f"#{tr.id}",
        "name": tr.title or f"#{tr.id}",
        "description": tr.comment or "",
        "photos": [{"id": p.id, "url": f"/api/media/{p.filename}",
                    "kind": p.kind or "photo"} for p in photos],
        "url": f"/api/media/{photos[0].filename}" if photos else "",
        "turnaround_urls": [f"/api/media/{f}" for f in _turnaround_files(tr)],
    }


@app.get("/api/items/all")
def items_all(user: User = Depends(current_user), db: Session = Depends(db_session)):
    """ОБЩАЯ БАЗА ПРЕДМЕТОВ: все треки мокап-проектов владельца. Предмет
    заводится один раз и виден отовсюду — из маркетинг-бара, из атрибутов
    персонажа, из другого проекта."""
    rows = (db.query(Track).join(Project, Project.id == Track.project_id)
            .filter(Project.owner_id == user.id).order_by(Track.id.desc()).all())
    out = [_item_dict(tr) for tr in rows
           if formats.mode_of_kind(tr.project.kind)["id"] == "mockup"]
    return {"items": out}


@app.get("/api/characters/all")
def characters_all(user: User = Depends(current_user),
                   db: Session = Depends(db_session)):
    """Общая база героев: персонажи ВСЕХ проектов владельца — для слотов
    маркетинг-бара и @-автокомплита, а не только текущего проекта."""
    rows = (db.query(Character).join(Project, Project.id == Character.project_id)
            .filter(Project.owner_id == user.id).order_by(Character.id).all())
    out = []
    for c in rows:
        photo = ""
        for ph in sorted(c.photos, key=lambda x: (x.position, x.id)):
            if os.path.exists(os.path.join(UPLOAD_DIR, ph.filename)):
                photo = f"/api/media/{ph.filename}"
                break
        out.append({"id": c.id, "name": c.name, "project_id": c.project_id,
                    "photo_url": photo})
    return {"characters": out}


@app.get("/api/mockup/products")
def mockup_products(user: User = Depends(current_user),
                    db: Session = Depends(db_session)):
    """Общая база товаров: по одному фото с каждого трека владельца, где
    загружены фото продукта, — слот «продукт» выбирает из любого проекта."""
    rows = (db.query(Track).join(Project, Project.id == Track.project_id)
            .filter(Project.owner_id == user.id).order_by(Track.id).all())
    out = []
    for tr in rows:
        paths = _track_photo_paths(tr, 1)
        if not paths:
            continue
        out.append({"track_id": tr.id, "title": tr.title or f"#{tr.id}",
                    "url": f"/api/media/{os.path.basename(paths[0])}"})
    return {"products": out}


@app.post("/api/admin/mockup/previews")
def admin_mockup_previews(all: int = 0,  # noqa: A002
                          user: User = Depends(current_user)):
    if not user.is_admin:
        raise HTTPException(403, "только для админа")
    return generate_mockup_previews(["*"] if all else None)


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


# РАЗВОРОТ ПРЕДМЕТА. У персонажа лист строится вокруг фигуры и лица, у вещи —
# вокруг формы, материала и этикетки: поза, рост и руки здесь не значат
# ничего, зато значат блики, толщина стенки и то, что написано на упаковке.
# Поэтому отдельный набор промптов, а не «тот же с другими словами».
PROP_SHEET_STYLES = {
    "3d": (
        "Professional 3D product turnaround sheet, high-end CG render (Unreal "
        "Engine / Blender cycles look): the SAME single object shown in four "
        "views side by side — front, three-quarter, side, back — floating at the "
        "same scale and the same distance in every view. Clean neutral light-grey "
        "studio background, soft even three-point lighting, subtle contact shadow. "
        "Accurate materials: surface finish, reflections, transparency, print."
    ),
    "real": (
        "Photorealistic product reference sheet: the SAME single object "
        "photographed in four views side by side — front, three-quarter, side, "
        "back — identical scale and identical distance in every view. Neutral "
        "light-grey seamless studio backdrop, soft even softbox lighting, sharp "
        "focus, true material texture."
    ),
}

PROP_SHEET_VIEWS = (
    "Four views left to right: front, three-quarter, side, back. "
    "ONE object only, no hands, no people, no packaging box unless the object IS "
    "the packaging, nothing else in frame. Identical colour, identical proportions "
    "and identical markings in all four views. "
    "Even neutral grey studio background, no text captions, no labels added by "
    "you, no grid lines, no watermark."
)

# Этикетка — то место, где генератор врёт чаще всего: он «дорисовывает»
# правдоподобный текст вместо настоящего. Для товара это брак.
PROP_SHEET_IDENTITY = (
    "The reference photos are ALL the SAME real object — this is the product. "
    "Reproduce its exact shape, exact colour, exact finish and exact printed "
    "artwork. Copy any text and logo on it EXACTLY as photographed, letter for "
    "letter; never invent, translate or restyle the lettering. If a detail is "
    "not visible in the photos, keep it plain rather than inventing it."
)


def _prop_sheet_photos(attr: CharacterAttribute, limit: int) -> list:
    """Живые снимки предмета для референса — сгенерированные листы не берём."""
    live = [ph for ph in sorted(attr.photos, key=lambda x: (x.position, x.id))
            if (getattr(ph, "kind", "photo") or "photo") == "photo"
            and os.path.exists(os.path.join(UPLOAD_DIR, ph.filename))]
    limit = max(1, int(limit))
    if len(live) <= limit:
        return live
    return [live[0]] + live[-(limit - 1):]


@app.post("/api/attributes/{attr_id}/generate-model")
async def generate_attribute_model(attr_id: int, request: Request,
                                   user: User = Depends(current_user),
                                   db: Session = Depends(db_session)):
    """3D-разворот ПРЕДМЕТА по его фото и описанию.

    То же самое, что разворот персонажа, но для вещи: набор ракурсов, из
    которого дальше строятся кадры. Нужен ровно затем же — чтобы продукт
    оставался собой от кадра к кадру, а не превращался в похожую банку.
    """
    attr = _own_attribute(db, user, attr_id)
    body = await request.json() if await request.body() else {}
    desc = (str(body.get("description") or "").strip() or attr.description).strip()
    kind = str(body.get("kind") or "3d")
    if kind not in PROP_SHEET_STYLES:
        kind = "3d"
    engine = _model_sheet_engine(user)
    spec = mediagen.IMAGE_ENGINES.get(engine, {})
    max_refs = int(spec.get("max_refs") or 1)
    photos = _prop_sheet_photos(
        attr, min(MODEL_SHEET_MAX_PHOTOS, max_refs) if max_refs > 1
        else MODEL_SHEET_COLLAGE_PHOTOS)
    if not desc and not photos:
        raise HTTPException(400, "нужны фото предмета или его описание")
    parts = [PROP_SHEET_STYLES[kind]]
    if photos:
        parts.append(PROP_SHEET_IDENTITY)
    else:
        parts.append("No reference photos: build the object from the written "
                     "description alone and keep it identical across all views.")
    if desc:
        parts.append(f"OBJECT (shape, material, colour, printed artwork): {desc}")
    parts.append(PROP_SHEET_VIEWS)
    prompt = "\n\n".join(parts)

    resolution = "4K" if "4K" in (spec.get("resolutions") or ()) else ""
    cost = _image_cost(user, engine, resolution)
    _charge(db, user, cost, f"разворот предмета {attr.id}",
            kind="frames", engine=engine, ref_type="attribute", ref_id=attr.id)
    db.commit()
    refs = [os.path.join(UPLOAD_DIR, ph.filename) for ph in photos]
    try:
        data, mime = await mediagen.generate_image(
            prompt, reference_paths=refs, engine=engine,
            resolution=resolution, aspect="16:9")
    except Exception as e:  # noqa: BLE001 — движок упал, деньги возвращаем
        _refund(db, user, cost, f"разворот предмета {attr.id}")
        db.commit()
        raise HTTPException(502, _err_text(e, 502))
    fname = _save_image(data, mime)
    _reg_file(db, fname, attr.character.project.owner_id, kind="model",
              project_id=attr.character.project_id)
    pos = max((ph.position for ph in attr.photos), default=0) + 1
    db.add(AttributePhoto(attribute_id=attr.id, position=pos,
                          filename=fname, kind="model"))
    db.commit()
    return {"ok": True, "url": f"/api/media/{fname}", "charged": cost}


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
        item_track_id=_own_item_id(db, user, body.get("item_track_id")),
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
    if "item_track_id" in body:
        attr.item_track_id = _own_item_id(db, user, body["item_track_id"])
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


@app.post("/api/tracks/{track_id}/scenes/reorder")
async def reorder_scenes(track_id: int, request: Request,
                         user: User = Depends(current_user),
                         db: Session = Depends(db_session)):
    """Новый порядок кадров раскадровки — он же порядок в финальном клипе.

    Слот едет ВМЕСТЕ с кадром: длительность — свойство кадра, а не места в
    ряду. Поэтому после перестановки заново раскладывается таймлайн, и куски
    дорожки под сценами пересчитываются: иначе кадр звучал бы музыкой той
    секунды, где он лежал раньше.
    """
    track = _own_track(db, user, track_id)
    body = await request.json()
    order = [int(x) for x in (body.get("order") or [])]
    mine = {s.id: s for s in track.scenes}
    seen = []
    for sid in order:
        if sid in mine and sid not in seen:
            seen.append(sid)
    if not seen:
        raise HTTPException(400, "пустой порядок кадров")
    # Кадры, которых в присланном списке нет, остаются в хвосте в прежнем
    # порядке: клиент мог отстать от свежесозданной сцены, и терять её нельзя.
    rest = [s.id for s in sorted(track.scenes, key=lambda x: (x.position, x.id))
            if s.id not in seen]
    for i, sid in enumerate(seen + rest, start=1):
        mine[sid].position = i
    _renumber_scenes(track)
    db.commit()
    # Музыка под кадрами разъехалась — нарезаем её заново под новые места.
    threading.Thread(target=_resync_scene_audio, args=(track.id,), daemon=True).start()
    return {"ok": True, "order": [s.id for s in
                                  sorted(track.scenes, key=lambda x: x.position)]}


@app.post("/api/tracks/{track_id}/scenes/retime")
async def retime_scene(track_id: int, request: Request,
                       user: User = Depends(current_user),
                       db: Session = Depends(db_session)):
    """Сдвинуть ГРАНИЦУ между двумя кадрами (метку на дорожке).

    Двигается не «длительность кадра», а стык: предыдущий кадр растёт ровно
    на столько, на сколько укорачивается следующий. Иначе перетаскивание
    метки уводило бы весь хвост клипа, и одна поправка на полсекунды
    рассинхронизировала бы тридцать сцен.

    Минимум секунда на кадр: кадр в ноль секунд — это не кадр, а дырка в
    таймлайне, которую нечем показать.
    """
    track = _own_track(db, user, track_id)
    body = await request.json()
    scene_id = int(body.get("scene_id") or 0)
    want = int(round(float(body.get("start_sec") or 0)))
    ordered = sorted(track.scenes, key=lambda x: (x.position, x.id))
    idx = next((i for i, s in enumerate(ordered) if s.id == scene_id), -1)
    if idx <= 0:
        raise HTTPException(400, "первую границу двигать некуда")
    prev, cur = ordered[idx - 1], ordered[idx]
    lo = int(prev.start_sec or 0) + 1
    hi = int(cur.start_sec or 0) + int(cur.duration_sec or 0) - 1
    if hi < lo:
        raise HTTPException(400, "кадры слишком короткие для сдвига")
    want = max(lo, min(hi, want))
    end = int(cur.start_sec or 0) + int(cur.duration_sec or 0)
    prev.duration_sec = want - int(prev.start_sec or 0)
    cur.start_sec = want
    cur.duration_sec = end - want
    _renumber_scenes(track)
    db.commit()
    # Куски дорожки под сценами уехали — режем заново, как после reorder.
    threading.Thread(target=_resync_scene_audio, args=(track.id,), daemon=True).start()
    return {"ok": True, "scenes": [{"id": s.id, "start_sec": s.start_sec,
                                    "duration_sec": s.duration_sec}
                                   for s in sorted(track.scenes,
                                                   key=lambda x: x.position)]}


def _resync_scene_audio(track_id: int) -> None:
    """Перенарезать отрезки дорожки под нынешние места кадров."""
    db = SessionLocal()
    try:
        track = db.get(Track, track_id)
        if not track:
            return
        src = _track_audio_path(track)
        if not src:
            return
        for s in sorted(track.scenes, key=lambda x: x.position):
            if not s.video_filename and not s.image_filename:
                continue
            old = s.audio_filename
            try:
                s.audio_filename = mediagen.slice_audio(
                    src, int(s.start_sec or 0), int(s.duration_sec or 0))
            except Exception as e:  # noqa: BLE001 — один кадр не рушит трек
                log.warning("нарезка звука кадра %s: %s", s.id, str(e)[:120])
                continue
            db.commit()
            if old and old != s.audio_filename:
                _remove_media(old, db)
                db.commit()
    except Exception as e:  # noqa: BLE001
        db.rollback()
        log.warning("пересборка звука трека %s: %s", track_id, str(e)[:150])
    finally:
        db.close()


@app.post("/api/tracks/{track_id}/scenes/extend")
async def extend_scenes(track_id: int, request: Request,
                        user: User = Depends(current_user),
                        db: Session = Depends(db_session)):
    """Дописать раскадровку до конца дорожки.

    Дорожка на две минуты, а сцен на тридцать секунд — модель отдала меньше,
    чем просили, или лишние удалили руками. Клип собирался коротким, и понять
    причину было неоткуда. Здесь досочиняем НЕДОСТАЮЩИЙ хвост: существующие
    сцены не трогаем, генерируем только то, что не покрыто, и ставим в конец.

    body: {"count": N} — сколько сцен добавить; не передан = сколько нужно
    до конца дорожки."""
    track = _own_track(db, user, track_id)
    body = await request.json() if await request.body() else {}
    cov = _scenes_coverage(track)
    want = int(body.get("count") or 0) or cov["suggest"]
    if want <= 0:
        raise HTTPException(400, "раскадровка уже покрывает всю дорожку")
    want = max(1, min(40, want))
    if track.scenes_status in ("queued", "running"):
        raise HTTPException(409, "раскадровка уже собирается")
    track.scenes_status = "queued"
    track.scenes_error = ""
    db.commit()
    from threading import Thread
    Thread(target=_run_scenes_extend, args=(track_id, want), daemon=True).start()
    return {"ok": True, "queued": want, "coverage": cov}


def _run_scenes_extend(track_id: int, count: int) -> None:
    """Досочинение хвоста раскадровки. Уже готовые сцены не трогаем."""
    db = SessionLocal()
    try:
        track = db.get(Track, track_id)
        if not track:
            return
        track.scenes_status = "running"
        db.commit()
        engine = _text_engine_for(db, track.project, track)
        existing = sorted(track.scenes, key=lambda s: s.position)
        tail = [{
            "position": s.position, "shot_note": s.shot_note,
            "lyric_line": s.lyric_line, "characters": s.characters,
        } for s in existing[-6:]]
        cov = _scenes_coverage(track)
        import asyncio
        result = asyncio.run(claude.extend_scenes(
            style=track.style, story=track.project.story or "",
            characters=characters_payload(track.project),
            existing_tail=tail, count=count,
            seconds_left=cov["left_sec"], slot=cov["slot_sec"],
            lyrics=track.lyrics or "", comment=track.comment or "",
            engine=engine,
            random_cast=bool(getattr(track, "random_cast", False)),
        ))
        pos = max((s.position for s in track.scenes), default=0)
        start = sum(int(s.duration_sec or 0) for s in track.scenes)
        for sc in (result.get("scenes") or [])[:count]:
            pos += 1
            dur = max(2, min(12, int(sc.get("duration_sec") or cov["slot_sec"])))
            db.add(Scene(
                track_id=track.id, position=pos, duration_sec=dur,
                lyric_line=str(sc.get("lyric_line") or ""),
                characters=_normalize_scene_characters(
                    str(sc.get("characters") or ""), track.project),
                shot_size=str(sc.get("shot_size") or ""),
                camera_move=str(sc.get("camera_move") or ""),
                shot_note=str(sc.get("shot_note") or ""),
                image_prompt=str(sc.get("image_prompt") or ""),
                image_prompt_last=str(sc.get("image_prompt_last") or ""),
                motion_prompt=str(sc.get("motion_prompt") or ""),
                video_provider="seedance" if mediagen.seedance_available() else "grok",
            ))
            start += dur
        # Лист раскадровки собран под прежний набор — помечаем устаревшим.
        if track.storyboard_filename:
            track.storyboard_grid = ""
        track.scenes_status = "done"
        db.commit()
    except Exception as e:  # noqa: BLE001 — причину показываем в карточке
        db.rollback()
        tr = db.get(Track, track_id)
        if tr:
            tr.scenes_status = "error"
            tr.scenes_error = str(e)[:400]
            db.commit()
        log.warning("дописать раскадровку не вышло: %s", e)
    finally:
        db.close()


@app.post("/api/scenes/{scene_id}/generate-prompt")
async def generate_scene_prompt(scene_id: int, user: User = Depends(current_user),
                                db: Session = Depends(db_session)):
    """Дописать промпты одного кадра по контексту раскадровки.

    Кадр, добавленный руками, приходит с пустыми полями: генерация видео на нём
    падала («input.prompt is required»), а кадры выходили случайными. Здесь
    модель видит стиль, сюжет, героев и соседние кадры и пишет ровно этот кадр,
    не трогая остальные."""
    scene = _own_scene(db, user, scene_id)
    track = scene.track
    project = track.project
    near = [
        {"position": x.position, "shot_note": x.shot_note, "image_prompt": x.image_prompt}
        for x in sorted(track.scenes, key=lambda y: y.position)
        if abs(x.position - scene.position) <= 2 and x.id != scene.id
    ]
    import asyncio
    try:
        res = asyncio.run(claude.generate_scene_prompt(
            style=track.style, story=project.story or "",
            characters=characters_payload(project), neighbours=near,
            scene={
                "position": scene.position, "duration_sec": scene.duration_sec,
                "shot_size": scene.shot_size, "camera_move": scene.camera_move,
                "characters": scene.characters, "shot_note": scene.shot_note,
            },
            lyrics_line=scene.lyric_line or "", comment=track.comment or "",
            engine=_text_engine_for(db, project, track),
        ))
    except Exception as e:  # noqa: BLE001 — причину показываем в карточке
        raise HTTPException(502, f"не вышло написать промпт: {str(e)[:200]}")
    scene.prompt_stale = False
    scene.image_prompt = str(res.get("image_prompt") or "").strip() or scene.image_prompt
    scene.image_prompt_last = str(res.get("image_prompt_last") or "").strip() or scene.image_prompt_last
    scene.motion_prompt = str(res.get("motion_prompt") or "").strip() or scene.motion_prompt
    if not (scene.shot_note or "").strip():
        scene.shot_note = str(res.get("shot_note") or "").strip()
    db.commit()
    db.refresh(scene)
    return scene_dict(scene)


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



# ═════════════════════════════════════════════════════════════════════════════
# ТАЙМИНГИ: одно место правды
#
# Как только в раскадровку можно вставить кадр (копией или продлением), длина
# клипа перестаёт совпадать с длиной дорожки — и это НЕ мелочь. Сборка вешает
# звук с -shortest, поэтому лишние секунды раскадровки молча обрезаются по
# музыке: последние кадры просто исчезают из клипа, а человек видит «сервис
# потерял мою работу».
#
# Поэтому вставка кадра ВСЕГДА спрашивает, что делать с таймингом, и считает
# ответ ОДНОЙ функцией — _retime_plan. Смету и применение считает она же:
# второй арифметики в сервисе быть не должно.
# ═════════════════════════════════════════════════════════════════════════════

#: Границы длительности кадра — общие с движками видео (2–12 секунд).
# Максимум подняли до 30: длинная сцена строится ЦЕПОЧКОЙ промежуточных
# кадров (_scene_frame_chain) — видео едет отрезками кадр→кадр по ~6 секунд,
# так что 30-секундный план физически снимается. Раскадровщик по-прежнему
# просит 2-10 секунд: длинные планы — осознанное ручное решение.
SCENE_MIN_SEC, SCENE_MAX_SEC = 2, 30
#: Как поступить с таймлайном при вставке кадра.
RETIME_POLICIES = ("squeeze", "spread", "tail")


def _clamp_dur(v) -> int:
    return max(SCENE_MIN_SEC, min(SCENE_MAX_SEC, int(v or SCENE_MIN_SEC)))


def _retime_plan(track: Track, policy: str, at_position: int, new_dur: int) -> dict:
    """Что произойдёт с таймингами, если вставить кадр длиной new_dur сразу
    ПОСЛЕ позиции at_position. Ничего не меняет.

      squeeze — слот исходного кадра делится пополам (6 с → 3 + 3). Сумма не
                меняется, дорожка не разъезжается, соседи не тронуты.
      spread  — новый кадр берёт полную длину, а лишние секунды
                пропорционально снимаются с ПОСЛЕДУЮЩИХ кадров (никто не
                короче двух секунд).
      tail    — клип просто становится длиннее.

    Политика, которая не может выполниться (кадр короче четырёх секунд для
    squeeze, все соседи уже по две секунды для spread), честно вырождается
    в tail и говорит об этом в смете — а не делает вид, что справилась."""
    scenes = sorted(track.scenes, key=lambda x: (x.position, x.id))
    new_dur = _clamp_dur(new_dur)
    changes: dict[int, int] = {}
    used = policy if policy in RETIME_POLICIES else "tail"
    fell_back = ""
    src = next((s for s in scenes if s.position == at_position), None)

    if used == "squeeze":
        if src is None or int(src.duration_sec or 0) < SCENE_MIN_SEC * 2:
            used, fell_back = "tail", "squeeze"
        else:
            total = int(src.duration_sec)
            half = max(SCENE_MIN_SEC, total // 2)
            changes[src.id] = total - half if total - half >= SCENE_MIN_SEC else half
            new_dur = total - changes[src.id]

    if used == "spread":
        after = [s for s in scenes if s.position > at_position]
        room = sum(max(0, int(s.duration_sec or 0) - SCENE_MIN_SEC) for s in after)
        if room <= 0:
            used, fell_back = "tail", "spread"
        else:
            need = min(new_dur, room)
            left = need
            for i, s in enumerate(after):
                have = max(0, int(s.duration_sec or 0) - SCENE_MIN_SEC)
                if have <= 0:
                    continue
                take = have if i == len(after) - 1 else int(round(need * have / room))
                take = min(have, max(0, take), left)
                if take:
                    changes[s.id] = int(s.duration_sec) - take
                    left -= take
            if left > 0:
                fell_back = "spread_partial"

    old_total = sum(int(s.duration_sec or 0) for s in scenes)
    new_total = old_total + new_dur
    for sid, dur in changes.items():
        was = next(int(s.duration_sec or 0) for s in scenes if s.id == sid)
        new_total -= (was - dur)
    audio = int(track.audio_duration_sec or 0)
    return {
        "policy": used,
        "requested": policy,
        "fell_back": fell_back,
        "new_duration": new_dur,
        "changes": changes,
        "track_delta_sec": new_total - old_total,
        "track_total_sec": new_total,
        "audio_sec": audio,
        "over_sec": max(0, new_total - audio) if audio else 0,
        # Видео этих кадров снято под ДРУГОЙ слот. Оно не пропадает (это до
        # 152 токенов чужих денег), но карточка обязана сказать об этом.
        "video_stale_ids": [sid for sid in changes
                            if next((s.video_filename for s in scenes if s.id == sid), "")],
    }


def _retime_apply(db: Session, track: Track, plan: dict) -> None:
    """Применить смету. Дальше — единая перенумерация: позиции подряд,
    start_sec пересчитан от нуля."""
    by_id = {s.id: s for s in track.scenes}
    for sid, dur in (plan.get("changes") or {}).items():
        s = by_id.get(sid)
        if not s or int(s.duration_sec or 0) == int(dur):
            continue
        s.duration_sec = _clamp_dur(dur)
        # Нарезка дорожки под сцену больше не соответствует слоту. Она стоит
        # ноль токенов и режется заново при следующей генерации видео —
        # поэтому её честнее убрать, чем оставить врать.
        if s.audio_filename:
            _remove_media(s.audio_filename, db)
            s.audio_filename = ""
        if s.video_filename:
            s.video_stale = True
    _renumber_scenes(track)


def _default_policy(track: Track, scene: Scene) -> str:
    """Что предложить по умолчанию. У клипа есть дорожка, и её длина —
    закон: там сумма слотов меняться не должна. У ролика и серии дорожки нет
    (см. _track_duration), и удлинить их — законное действие."""
    if not int(track.audio_duration_sec or 0):
        return "tail"
    return "squeeze" if int(scene.duration_sec or 0) >= SCENE_MIN_SEC * 2 else "spread"


def _retime_quote(track: Track, scene: Scene, new_dur: int) -> dict:
    """Смета по всем политикам разом — для окна выбора."""
    return {
        "scene_id": scene.id,
        "position": scene.position,
        "duration_sec": int(scene.duration_sec or 0),
        "default": _default_policy(track, scene),
        "policies": [
            _retime_plan(track, p, scene.position, new_dur) for p in RETIME_POLICIES
        ],
    }


@app.post("/api/tracks/{track_id}/fit-timings")
def fit_timings(track_id: int, user: User = Depends(current_user),
                db: Session = Depends(db_session)):
    """Подогнать раскадровку под длину дорожки.

    Нужно после «удлинить клип»: сумма слотов стала больше дорожки, а сборка
    вешает звук с -shortest — то есть лишние секунды МОЛЧА обрезаются по
    музыке вместе с последними кадрами. Это ровно тот случай, когда человек
    решает, что сервис потерял его работу.

    Сжимаем пропорционально, никого не делая короче двух секунд. Остаток от
    округления добираем с самых длинных кадров — так искажение размазывается
    там, где его меньше всего видно."""
    track = _own_track(db, user, track_id)
    target = int(track.audio_duration_sec or 0)
    if not target:
        raise HTTPException(400, "у объекта нет дорожки — подгонять не подо что")
    scenes = sorted(track.scenes, key=lambda x: (x.position, x.id))
    if not scenes:
        raise HTTPException(400, "у объекта нет кадров")
    total = sum(int(s.duration_sec or 0) for s in scenes)
    if total == target:
        return {"ok": True, "changed": 0, "total_sec": total}
    floor_total = SCENE_MIN_SEC * len(scenes)
    if target < floor_total:
        raise ApiError(409, "too_many_scenes",
                       f"кадров {len(scenes)}, короче {SCENE_MIN_SEC} секунд слот "
                       f"не бывает — на {target} с столько не помещается",
                       min_total=floor_total)
    plan = {}
    for sc in scenes:
        plan[sc.id] = _clamp_dur(round(int(sc.duration_sec or 0) * target / total))
    drift = target - sum(plan.values())
    order = sorted(scenes, key=lambda x: -int(x.duration_sec or 0))
    guard = 0
    while drift and guard < 1000:
        guard += 1
        moved = False
        for sc in order:
            if not drift:
                break
            step = 1 if drift > 0 else -1
            nxt = plan[sc.id] + step
            if SCENE_MIN_SEC <= nxt <= SCENE_MAX_SEC:
                plan[sc.id] = nxt
                drift -= step
                moved = True
        if not moved:
            break
    changed = {sid: d for sid, d in plan.items()
               if d != int(next(s.duration_sec for s in scenes if s.id == sid) or 0)}
    _retime_apply(db, track, {"changes": changed})
    db.commit()
    db.refresh(track)
    return {"ok": True, "changed": len(changed),
            "total_sec": sum(int(s.duration_sec or 0) for s in track.scenes),
            "track": track_dict(track, with_scenes=True)}


# ═════════════════════════════════════════════════════════════════════════════
# КОПИИ: проект, объект, кадр
#
# Копия стоит НОЛЬ БАЙТ и НОЛЬ ТОКЕНОВ. Ноль байт — потому что медиа
# переезжает жёсткими ссылками (_clone_media): каждый файл здесь пишется
# ровно один раз под uuid-именем, никто не открывает существующий файл на
# запись, и второе имя тому же иноду абсолютно безопасно. Ноль токенов —
# потому что копирование не зовёт ни один движок.
#
# ЧЕГО В КОПИИ НЕТ И ПОЧЕМУ:
#   * живых статусов задач — иначе копия рождается с вечным «рисую лист…»
#     (ровно та болезнь, которую лечит _reset_orphan_jobs при старте);
#   * утверждения сцен — утверждают то, что посмотрели;
#   * истории вариантов — она остаётся у оригинала;
#   * журнала токенов — он принадлежит событиям, а не объектам.
#
# А ВОТ charged_points ПЕРЕНОСИТСЯ, и это важно: это отметка «за эту работу
# уже заплачено». Без неё супергенерация (_settle_supergen: unpaid = charged
# < per_scene) выставила бы счёт второй раз за готовые сцены. Бесплатных
# генераций это не даёт — _scene_charge берёт деньги за ВЫЗОВ движка.
# ═════════════════════════════════════════════════════════════════════════════

#: Режимы копирования медиа: link — жёсткие ссылки (0 байт), none — только
#: структура (шаблон). Третьего режима нет намеренно: копирование байтами
#: даёт ровно то же поведение, что link, только дороже.
COPY_MEDIA_MODES = ("link", "none")


def _copy_scene_into(db: Session, src: Scene, track: Track, *, position: int,
                     media: str = "link", owner_id: int | None = None) -> Scene:
    """Копия кадра в объект. Возвращает новую сцену (уже во flush)."""
    with_media = media == "link"
    dst = Scene(
        track_id=track.id, position=position,
        duration_sec=_clamp_dur(src.duration_sec),
        lyric_line=src.lyric_line, characters=src.characters,
        attribute_ids=src.attribute_ids, shot_size=src.shot_size,
        camera_move=src.camera_move, act=src.act, speaker=src.speaker,
        image_prompt=src.image_prompt, image_prompt_last=src.image_prompt_last,
        motion_prompt=src.motion_prompt, shot_note=src.shot_note,
        style_keys=src.style_keys if with_media else "",
        frames_sig=src.frames_sig if with_media else "",
        image_engine=src.image_engine or "", video_engine=src.video_engine or "",
        video_provider=src.video_provider or "seedance",
        # «За эту работу уже заплачено» — см. комментарий к блоку.
        charged_points=int(src.charged_points or 0) if with_media else 0,
        copied_from_id=src.id,
        # Утверждение НЕ переносится: дубль, уехавший в сборку молча, — это
        # клип, которого человек не видел.
        approved=False,
    )
    db.add(dst)
    db.flush()
    if with_media:
        dst.image_filename = _clone_media(
            db, src.image_filename, owner_id=owner_id, kind="frame",
            project_id=track.project_id, track_id=track.id, scene_id=dst.id)
        dst.image_last_filename = _clone_media(
            db, src.image_last_filename, owner_id=owner_id, kind="frame_last",
            project_id=track.project_id, track_id=track.id, scene_id=dst.id)
        dst.video_filename = _clone_media(
            db, src.video_filename, owner_id=owner_id, kind="video",
            project_id=track.project_id, track_id=track.id, scene_id=dst.id)
        dst.image_status = "done" if dst.image_filename else ""
        dst.video_status = "done" if dst.video_filename else ""
        mids = []
        for m in _midframes(src):
            fname = _clone_media(db, m.get("filename", ""), owner_id=owner_id,
                                 kind="midframe", project_id=track.project_id,
                                 track_id=track.id, scene_id=dst.id)
            if fname:
                mids.append({"filename": fname, "prompt": m.get("prompt", "")})
        dst.midframes_json = json.dumps(mids, ensure_ascii=False) if mids else ""
    # Референсы кадра — часть постановки задачи, а не результата: их копируем
    # даже в «шаблон», иначе кадр придётся объяснять движку заново.
    for r in sorted(src.refs, key=lambda x: (x.position, x.id)):
        fname = _clone_media(db, r.filename, owner_id=owner_id, kind="ref",
                             project_id=track.project_id, track_id=track.id,
                             scene_id=dst.id, prefix="sref")
        if fname:
            db.add(SceneRef(scene_id=dst.id, position=r.position,
                            filename=fname, kind=r.kind or "vibe"))
    return dst


def _copy_track_into(db: Session, src: Track, project: Project, *,
                     media: str = "link", position: int = 0,
                     rename: bool = True) -> tuple:
    """Копия объекта в проект. Возвращает (новый трек, имена доклонированных
    персонажей).

    ЛОВУШКА, которую здесь и закрываем: Scene.characters — это ТЕКСТ с
    именами, а описания и модельки берутся из characters ЦЕЛЕВОГО проекта.
    Скопированный в чужой проект объект выглядит целым, но генерация в нём
    молча теряет героя: в промпт не уедет ни описание, ни фото-моделька.
    Поэтому недостающих героев доклонируем по именам и говорим об этом."""
    with_media = media == "link"
    owner_id = project.owner_id
    pos = position or (max((t.position for t in project.tracks), default=0) + 1)
    dst = Track(
        project_id=project.id, position=pos,
        title=((src.title or "").strip() + " (копия)") if rename else src.title,
        lyrics=src.lyrics, comment=src.comment,
        style=src.style, style_keys=src.style_keys, style_extra=src.style_extra,
        clip_preset_key=src.clip_preset_key,
        season_no=src.season_no, episode_no=src.episode_no,
        format_key=src.format_key, location_bible=src.location_bible,
        video_engine=src.video_engine, image_engine=src.image_engine,
        aspect=src.aspect, image_resolution=src.image_resolution,
        director_note=src.director_note,
        audio_profile=src.audio_profile,
        audio_duration_sec=src.audio_duration_sec,
        film_grain=src.film_grain, no_story=src.no_story,
        prompts_style_keys=src.prompts_style_keys,
        text_engine=src.text_engine or "",
        copied_from_id=src.id,
    )
    db.add(dst)
    db.flush()
    if with_media:
        dst.audio_filename = _clone_media(db, src.audio_filename, owner_id=owner_id,
                                          kind="audio", project_id=project.id,
                                          track_id=dst.id)
        dst.cover_filename = _clone_media(db, src.cover_filename, owner_id=owner_id,
                                          kind="cover", project_id=project.id,
                                          track_id=dst.id)
        dst.storyboard_filename = _clone_media(db, src.storyboard_filename,
                                               owner_id=owner_id, kind="storyboard",
                                               project_id=project.id, track_id=dst.id)
        dst.storyboard_grid = src.storyboard_grid
        dst.storyboard_scenes = src.storyboard_scenes
        dst.storyboard_status = "done" if dst.storyboard_filename else ""
    else:
        # Дорожка нужна даже «шаблону»: без неё у клипа нет ни хронометража,
        # ни сетки таймингов, и раскадровка теряет смысл.
        dst.audio_filename = _clone_media(db, src.audio_filename, owner_id=owner_id,
                                          kind="audio", project_id=project.id,
                                          track_id=dst.id)
    # Фото товара (режим мокапов) — это ВХОДНЫЕ данные, а не результат.
    for ph in sorted(src.photos, key=lambda x: (x.position, x.id)):
        fname = _clone_media(db, ph.filename, owner_id=owner_id, kind="photo",
                             project_id=project.id, track_id=dst.id, prefix="char")
        if fname:
            db.add(TrackPhoto(track_id=dst.id, position=ph.position, filename=fname,
                              kind=ph.kind or "photo",
                              from_photos=int(ph.from_photos or 0)))
    for sc in sorted(src.scenes, key=lambda x: (x.position, x.id)):
        _copy_scene_into(db, sc, dst, position=sc.position, media=media,
                         owner_id=owner_id)
    # Документы объекта (сценарий серии, бриф ролика) — часть его смысла.
    for doc in (db.query(Doc).filter(Doc.track_id == src.id).all()):
        db.add(Doc(project_id=project.id, track_id=dst.id, kind=doc.kind,
                   position=doc.position, title=doc.title, body=doc.body,
                   body_json=doc.body_json))
    brought = _bring_characters(db, src, project)
    db.flush()
    return dst, brought


def _bring_characters(db: Session, src: Track, project: Project) -> list:
    """Доклонировать в целевой проект героев, которых в нём нет, но которых
    объект называет по именам. Возвращает список имён."""
    if src.project_id == project.id:
        return []
    have = {(c.name or "").strip().lower() for c in project.characters}
    wanted: list[str] = []
    for sc in src.scenes:
        for name in (sc.characters or "").split(","):
            n = name.strip()
            if n and n.lower() not in have and n.lower() not in [w.lower() for w in wanted]:
                wanted.append(n)
    brought = []
    for n in wanted:
        source = next((c for c in src.project.characters
                       if (c.name or "").strip().lower() == n.lower()), None)
        if not source:
            continue
        _clone_character_into(db, source, project)
        brought.append(source.name)
    return brought


def _copy_media_bytes(track_ids: list, project_id: int, db: Session) -> dict:
    """Сколько медиа поедет в копию и сколько это стоит НА ДИСКЕ.

    Второе число — ноль, и это надо показывать вслух: «копия 4.1 ГБ,
    на диске займёт 0 байт» объясняет человеку, почему копировать можно
    смело, лучше любой подсказки."""
    q = db.query(func.coalesce(func.sum(FileOwner.size_bytes), 0),
                 func.count(FileOwner.filename)).filter(
        FileOwner.deleted_at.is_(None))
    if track_ids:
        q = q.filter(FileOwner.track_id.in_(track_ids))
    else:
        q = q.filter(FileOwner.project_id == int(project_id))
    total, count = q.first() or (0, 0)
    return {"media_files": int(count or 0), "media_bytes": int(total or 0),
            "disk_bytes": 0}


@app.get("/api/projects/{project_id}/copy/plan")
def copy_project_plan(project_id: int, user: User = Depends(current_user),
                      db: Session = Depends(db_session)):
    """Что войдёт в копию проекта и сколько она займёт на диске."""
    project = _own_project(db, user, project_id)
    scenes = sum(len(t.scenes) for t in project.tracks)
    out = _copy_media_bytes([], project.id, db)
    out.update({
        "name": project.name, "kind": project.kind,
        "tracks": len(project.tracks), "scenes": scenes,
        "characters": len(project.characters),
        "modes": list(COPY_MEDIA_MODES),
        "points": 0,
    })
    return out


@app.post("/api/projects/{project_id}/copy")
async def copy_project(project_id: int, request: Request,
                       user: User = Depends(current_user),
                       db: Session = Depends(db_session)):
    """Копия проекта целиком. Ноль токенов, ноль байт на диске."""
    src = _own_project(db, user, project_id)
    body = await request.json() if await request.body() else {}
    media = str(body.get("media") or "link")
    if media not in COPY_MEDIA_MODES:
        media = "link"
    name = str(body.get("name") or "").strip() or f"{src.name} (копия)"
    dst = Project(
        owner_id=user.id, name=name[:200], kind=src.kind,
        character_bible=src.character_bible, story=src.story,
        text_engine=src.text_engine or "", copied_from_id=src.id,
    )
    db.add(dst)
    db.flush()
    dst.cover_filename = _clone_media(db, src.cover_filename, owner_id=user.id,
                                      kind="cover", project_id=dst.id)
    for c in sorted(src.characters, key=lambda x: x.position):
        _clone_character_into(db, c, dst)
    db.flush()
    for t in sorted(src.tracks, key=lambda x: (x.position, x.id)):
        _copy_track_into(db, t, dst, media=media, position=t.position, rename=False)
    # Документы ПРОЕКТА (логлайн, синопсис, арка, поэпизодный план).
    for doc in db.query(Doc).filter(Doc.project_id == src.id,
                                    Doc.track_id.is_(None)).all():
        db.add(Doc(project_id=dst.id, kind=doc.kind, position=doc.position,
                   title=doc.title, body=doc.body, body_json=doc.body_json))
    db.commit()
    db.refresh(dst)
    log.info("проект %s скопирован в %s (media=%s)", src.id, dst.id, media)
    return {"ok": True, "project_id": dst.id, "media": media,
            "project": project_dict(dst, with_scenes=True,
                                    docs=_project_docs(db, dst))}


@app.get("/api/tracks/{track_id}/copy/plan")
def copy_track_plan(track_id: int, target_project_id: int = 0,
                    user: User = Depends(current_user),
                    db: Session = Depends(db_session)):
    """Что войдёт в копию объекта и каких героев придётся принести с собой."""
    track = _own_track(db, user, track_id)
    target = (_own_project(db, user, target_project_id) if target_project_id
              else track.project)
    out = _copy_media_bytes([track.id], track.project_id, db)
    out.update({
        "title": track.title, "scenes": len(track.scenes),
        "target_project_id": target.id, "target_project_name": target.name,
        "same_project": target.id == track.project_id,
        "bring_characters": _missing_character_names(track, target),
        "projects": [{"id": p.id, "name": p.name, "kind": p.kind}
                     for p in db.query(Project).filter(Project.owner_id == user.id)
                     .order_by(Project.id.desc()).limit(100).all()],
        "modes": list(COPY_MEDIA_MODES),
        "points": 0,
    })
    return out


def _missing_character_names(track: Track, project: Project) -> list:
    """Имена героев, которых объект называет, а в целевом проекте их нет."""
    if track.project_id == project.id:
        return []
    have = {(c.name or "").strip().lower() for c in project.characters}
    out: list[str] = []
    for sc in track.scenes:
        for name in (sc.characters or "").split(","):
            n = name.strip()
            if n and n.lower() not in have and n not in out:
                out.append(n)
    return out


@app.post("/api/tracks/{track_id}/copy")
async def copy_track(track_id: int, request: Request,
                     user: User = Depends(current_user),
                     db: Session = Depends(db_session)):
    """Копия объекта — в свой проект или в чужой (свой же, другой)."""
    src = _own_track(db, user, track_id)
    body = await request.json() if await request.body() else {}
    media = str(body.get("media") or "link")
    if media not in COPY_MEDIA_MODES:
        media = "link"
    target_id = int(body.get("target_project_id") or 0)
    target = _own_project(db, user, target_id) if target_id else src.project
    dst, brought = _copy_track_into(db, src, target, media=media)
    db.commit()
    db.refresh(dst)
    log.info("объект %s скопирован в проект %s (media=%s)", src.id, target.id, media)
    return {"ok": True, "track_id": dst.id, "project_id": target.id,
            "brought_characters": brought, "media": media,
            "track": track_dict(dst, with_scenes=True)}


@app.get("/api/scenes/{scene_id}/copy/plan")
def copy_scene_plan(scene_id: int, user: User = Depends(current_user),
                    db: Session = Depends(db_session)):
    """Копия кадра удлиняет раскадровку ровно так же, как продление, и
    пользуется той же сметой таймингов."""
    scene = _own_scene(db, user, scene_id)
    quote = _retime_quote(scene.track, scene, scene.duration_sec)
    quote["has_frames"] = bool(scene.image_filename or scene.image_last_filename)
    quote["has_video"] = bool(scene.video_filename)
    quote["points"] = 0
    return quote


@app.post("/api/scenes/{scene_id}/copy")
async def copy_scene(scene_id: int, request: Request,
                     user: User = Depends(current_user),
                     db: Session = Depends(db_session)):
    """Копия кадра сразу ПОСЛЕ исходного. Ноль токенов, ноль байт."""
    scene = _own_scene(db, user, scene_id)
    track = scene.track
    body = await request.json() if await request.body() else {}
    media = str(body.get("media") or "link")
    if media not in COPY_MEDIA_MODES:
        media = "link"
    policy = str(body.get("policy") or _default_policy(track, scene))
    plan = _retime_plan(track, policy, scene.position, scene.duration_sec)
    for s in track.scenes:
        if s.position > scene.position:
            s.position += 1
    dst = _copy_scene_into(db, scene, track, position=scene.position + 1,
                           media=media, owner_id=track.project.owner_id)
    dst.duration_sec = _clamp_dur(plan["new_duration"])
    db.flush()
    db.expire(track, ["scenes"])
    _retime_apply(db, track, plan)
    db.commit()
    db.refresh(dst)
    return {"ok": True, "scene": scene_dict(dst), "retime": _plan_public(plan),
            "track": track_dict(track, with_scenes=True)}


def _plan_public(plan: dict) -> dict:
    """Смета наружу: словарь changes с ключами-int json не переживёт."""
    out = dict(plan)
    out["changes"] = [{"scene_id": k, "duration_sec": v}
                      for k, v in (plan.get("changes") or {}).items()]
    return out


# ═════════════════════════════════════════════════════════════════════════════
# ПРОДЛИТЬ КАДР
#
# Владелец просил дословно: «продлить кадр чтобы генерировался следующий
# множился именно кадр и раскадровка пересобиралась».
#
# Это НЕ копия. Из кадра N рождается N+1, который НАЧИНАЕТСЯ ТАМ, ГДЕ
# ПРЕДЫДУЩИЙ ЗАКОНЧИЛСЯ: первым кадром новой сцены становится последний кадр
# исходной — тот же самый файл, второе имя, ноль байт. Дальше модель
# дописывает, чем этот план закончится (claude.continue_scene), а тайминги и
# нумерация пересобираются той же _retime_plan, что и у копии.
# ═════════════════════════════════════════════════════════════════════════════

@app.get("/api/scenes/{scene_id}/extend/plan")
def extend_scene_plan(scene_id: int, user: User = Depends(current_user),
                      db: Session = Depends(db_session)):
    """Смета продления: тайминги, цена текстового шага, что устареет."""
    scene = _own_scene(db, user, scene_id)
    track = scene.track
    engine = _resolve_text_engine(user, track.project, track)
    quote = _retime_quote(track, scene, scene.duration_sec)
    quote.update({
        "has_frames": bool(scene.image_filename or scene.image_last_filename),
        "text_engine": engine,
        # Текстовый шаг на шлюзе стоит НОЛЬ — это надо говорить прямо, иначе
        # человек не нажмёт кнопку, за которую с него ничего не возьмут.
        "points": TEXT_COST.get(engine, 0),
        "balance": int(user.gen_points or 0),
        "storyboard_stale_after": bool(track.storyboard_filename),
    })
    return quote


@app.post("/api/scenes/{scene_id}/extend")
async def extend_scene(scene_id: int, request: Request,
                       user: User = Depends(current_user),
                       db: Session = Depends(db_session)):
    """Продлить кадр: следующий начинается там, где этот закончился."""
    scene = _own_scene(db, user, scene_id)
    track = scene.track
    body = await request.json() if await request.body() else {}
    seed = scene.image_last_filename or scene.image_filename
    if not seed:
        raise HTTPException(400, "сначала сгенерируй кадры этого кадра — "
                                 "продолжать пока не от чего")
    policy = str(body.get("policy") or _default_policy(track, scene))
    plan = _retime_plan(track, policy, scene.position, scene.duration_sec)
    engine = _resolve_text_engine(user, track.project, track,
                                  str(body.get("text_engine") or ""))
    ev = _text_charge(db, user, engine, f"продолжение кадра {scene.position}",
                      ref_type="scene", ref_id=scene.id,
                      track_id=track.id, project_id=track.project_id)

    owner_id = track.project.owner_id
    for s in track.scenes:
        if s.position > scene.position:
            s.position += 1
    dst = Scene(
        track_id=track.id, position=scene.position + 1,
        duration_sec=_clamp_dur(plan["new_duration"]),
        characters=scene.characters, attribute_ids=scene.attribute_ids,
        shot_size=scene.shot_size, camera_move=scene.camera_move,
        act=scene.act, speaker=scene.speaker,
        # Первый кадр нового плана И ЕСТЬ последний кадр старого: его промпт
        # переносим ДОСЛОВНО. Перерисовка первого кадра тогда даст ту же
        # картинку, а не что-то новое — это и значит «начинается там же».
        image_prompt=(scene.image_prompt_last or scene.image_prompt),
        image_prompt_last="", motion_prompt=scene.motion_prompt,
        shot_note=scene.shot_note,
        # Стиль наследуется: первый кадр физически снят прежним стилем, и
        # написать иное значило бы солгать метке «снят в прежнем стиле».
        style_keys=scene.style_keys,
        image_engine=scene.image_engine or "", video_engine=scene.video_engine or "",
        video_provider=scene.video_provider or "seedance",
        continued_from_id=scene.id,
        charged_points=0,          # это НОВАЯ работа, за неё ещё не платили
        approved=False,
    )
    db.add(dst)
    db.flush()
    dst.image_filename = _clone_media(db, seed, owner_id=owner_id, kind="frame",
                                      project_id=track.project_id,
                                      track_id=track.id, scene_id=dst.id)
    dst.image_status = "done" if dst.image_filename else ""
    for r in sorted(scene.refs, key=lambda x: (x.position, x.id)):
        fname = _clone_media(db, r.filename, owner_id=owner_id, kind="ref",
                             project_id=track.project_id, track_id=track.id,
                             scene_id=dst.id, prefix="sref")
        if fname:
            db.add(SceneRef(scene_id=dst.id, position=r.position,
                            filename=fname, kind=r.kind or "vibe"))

    # Чем закончится новый план — пишет модель. Не вышло (шлюз лёг, ключа
    # нет) — кнопка ВСЁ РАВНО работает: продолжение по смыслу уже собрано,
    # модель добавляет только конец плана.
    written = False
    try:
        nxt = _next_lyric_line(track, scene)
        # AWAIT, А НЕ asyncio.run. Роут асинхронный, то есть уже крутится в
        # петле событий, и asyncio.run из неё ВСЕГДА падает RuntimeError —
        # модель не звалась бы никогда, а человек молча получал бы фолбэк.
        # asyncio.run в этом файле законен только внутри тредов-воркеров.
        res = await claude.continue_scene(
            image_prompt_first=dst.image_prompt,
            prev_motion=scene.motion_prompt, prev_note=scene.shot_note,
            characters=characters_payload(track.project),
            character_bible=track.project.character_bible or "",
            location_bible=track.location_bible or "",
            next_line=nxt, shot_size=scene.shot_size,
            camera_move=scene.camera_move, engine=engine,
        )
        dst.image_prompt_last = str(res.get("image_prompt_last") or "").strip()
        dst.motion_prompt = str(res.get("motion_prompt") or "").strip() or dst.motion_prompt
        dst.shot_note = str(res.get("shot_note") or "").strip() or dst.shot_note
        dst.camera_move = str(res.get("camera_move") or "").strip() or dst.camera_move
        dst.lyric_line = nxt
        written = bool(dst.image_prompt_last)
    except Exception as e:  # noqa: BLE001 — модель необязательна для действия
        log.warning("продолжение кадра %s: модель не ответила (%s)", scene.id, str(e)[:200])
        _text_refund(db, track.project, engine, "возврат: продолжение кадра",
                     ref_type="scene", ref_id=scene.id)
        ev = 0
    if not written:
        dst.image_prompt_last = dst.image_prompt
        dst.motion_prompt = ("continues the previous shot: "
                             + (scene.motion_prompt or "")).strip()
        dst.shot_note = f"продолжение кадра {scene.position}"
    db.flush()
    db.expire(track, ["scenes"])
    _retime_apply(db, track, plan)
    db.commit()
    db.refresh(dst)
    return {"ok": True, "scene": scene_dict(dst), "retime": _plan_public(plan),
            "written_by_model": written, "charged": bool(ev),
            "track": track_dict(track, with_scenes=True)}


def _next_lyric_line(track: Track, scene: Scene) -> str:
    """Строка текста, которая звучит СЛЕДУЮЩЕЙ. Нужна модели, чтобы
    продолжение попадало в трек, а не жило само по себе."""
    lines = [ln.strip() for ln in (track.lyrics or "").splitlines() if ln.strip()]
    if not lines or not (scene.lyric_line or "").strip():
        return ""
    try:
        i = lines.index(scene.lyric_line.strip())
    except ValueError:
        return ""
    return lines[i + 1] if i + 1 < len(lines) else ""


#: Что перерисовывать пакетной кнопкой.
#:   todo  — только те, у кого кадров ещё нет;
#:   dirty — плюс те, у кого С МОМЕНТА ОТРИСОВКИ изменился промпт или стиль;
#:   all   — все подряд, включая нетронутые.
FRAMES_SCOPES = ("todo", "dirty", "all")


def _frames_todo(track: Track, force: bool = False, scope: str = "") -> list:
    """Сцены, которым нужны кадры.

    ПОЧЕМУ ПО УМОЛЧАНИЮ НЕ «ВСЕ». Кнопка «перерисовать кадры» брала деньги
    за весь трек всегда — включая тридцать сцен, к которым никто не
    притрагивался. На Nano Banana Pro это $0.09 за картинку живых денег и
    столько же лишних файлов на диске. Теперь «грязная» сцена определяется
    честно: отпечаток промптов и стиля (frames_sig) разошёлся с тем, под
    что сняты нынешние кадры. «Все подряд» осталось — но отдельным выбором
    и со сметой, а не молча."""
    scope = scope if scope in FRAMES_SCOPES else ("dirty" if force else "todo")
    out = []
    for s in track.scenes:
        if not (s.image_prompt or "").strip():
            continue
        if s.image_prompt.startswith("(готовый кадр"):
            continue
        missing = not (s.image_filename or s.image_last_filename)
        if scope == "all" or missing:
            out.append(s)
            continue
        if scope == "dirty":
            # Пустой frames_sig = кадры сняты до появления отпечатка. Тогда
            # опираемся на прежний признак — стиль кадра против стиля трека.
            if not (s.frames_sig or ""):
                if (s.style_keys or "") != (track.style_keys or ""):
                    out.append(s)
            elif s.frames_sig != _frames_sig(s, track):
                out.append(s)
    return out


def _run_all_frames(track_id: int, engine: str = "", force: bool = False,
                    keep_version: bool = False, scene_ids: list | None = None,
                    which: str = "both") -> None:
    """Пакетная генерация: кадры сцен трека подряд, одна за другой.

    Последовательно, а не парал­лельно: шлюзы картинок обслуживают один
    браузер, и залп из 25 сцен просто выстроится в ту же очередь, но с
    таймаутами.

    scene_ids приходит СПИСКОМ из роута, а не пересчитывается здесь. Иначе
    касса и работа считали бы набор сцен по-разному: роут списал за
    «изменившиеся», а тред перерисовал бы «все подряд» — и разницу человек
    получил бы бесплатно за наш счёт."""
    ids = [int(x) for x in (scene_ids or [])]
    if not ids:
        db = SessionLocal()
        try:
            track = db.get(Track, track_id)
            ids = [s.id for s in _frames_todo(
                track, scope="all" if force else "todo")] if track else []
        finally:
            db.close()
    log.info("пакет кадров трека %s: %s сцен движком %s%s",
             track_id, len(ids), engine or "(по тарифу)",
             " (перерисовка)" if force else "")
    for sid in ids:
        try:
            _run_scene_frames(sid, which=which, engine=engine,
                              keep_version=keep_version)
        except Exception as e:  # noqa: BLE001 — одна упавшая сцена не роняет пакет
            log.warning("кадры сцены %s в пакете упали: %s", sid, _err_text(e))


@app.post("/api/tracks/{track_id}/generate-all-frames")
def generate_all_frames(track_id: int, engine: str = "", force: int = 0,
                        scope: str = "", which: str = "first",
                        user: User = Depends(current_user),
                        db: Session = Depends(db_session)):
    """scope: todo (по умолчанию) | dirty | all. force=1 — легаси-синоним
    dirty: та же кнопка «перерисовать кадры», но теперь она платит только за
    сцены, где действительно что-то изменилось."""
    _guard_disk()
    track = _own_track(db, user, track_id)
    if not track.scenes:
        raise HTTPException(400, "сначала сгенерируй раскадровку")
    scope = scope if scope in FRAMES_SCOPES else ("dirty" if force else "todo")
    todo = _frames_todo(track, scope=scope)
    if not todo:
        raise HTTPException(400, "перерисовывать нечего: у всех сцен кадры "
                                 "сняты под нынешние промпты и стиль")
    # Списываем за весь пакет вперёд — до того, как сцены встанут в очередь.
    # Кадры берут аванс в счёт цены сцены; уже оплаченные сцены не платят снова.
    # Движок выбираем ДО списания: цена кадров зависит именно от него, а сам
    # выбор берётся с ТРЕКА, а не молча падает в дефолт тарифа, как раньше.
    eng = _resolve_image_engine(user, track, engine)
    # Пакет по умолчанию рисует ТОЛЬКО первые кадры (см. generate_scene_frames)
    # — и стоит вдвое дешевле пары.
    if which not in ("both", "first"):
        which = "first"
    _scenes_charge(db, user, todo, lambda sc: _frames_cost(user, sc, eng, which),
                   f"кадры сцен трека {track.id} ({eng}, {scope}, {which})",
                   kind="frames", engine=eng, track_id=track.id,
                   project_id=track.project_id)
    for s in todo:
        s.image_status = "queued"
    db.commit()
    redraw = scope in ("dirty", "all")
    _spawn_gen(user, _run_all_frames, track_id, eng, redraw, redraw,
               [s.id for s in todo], which, kind="frames")
    return {"ok": True, "queued": len(todo), "engine": eng, "scope": scope,
            "force": redraw}


@app.get("/api/tracks/{track_id}/frames/quote")
def frames_quote(track_id: int, engine: str = "",
                 user: User = Depends(current_user),
                 db: Session = Depends(db_session)):
    """Сколько стоит каждый вариант пакетной отрисовки. Ничего не списывает.

    Считается ТЕМИ ЖЕ функциями, что и списание: второй кассы в сервисе нет."""
    track = _own_track(db, user, track_id)
    eng = _resolve_image_engine(user, track, engine)
    # Пакет по умолчанию рисует только первые кадры — и цена считается за них.
    per = _frames_cost(user, None, eng, "first")
    out = {"engine": eng, "per_scene": per, "per_pair": _frames_cost(user, None, eng),
           "balance": int(user.gen_points or 0),
           "scopes": []}
    for sc in FRAMES_SCOPES:
        n = len(_frames_todo(track, scope=sc))
        out["scopes"].append({"scope": sc, "scenes": n, "total": per * n})
    return out


# ═════════════════════════════════════════════════════════════════════════════
# РЕСТАЙЛ: тот же клип в другом стиле, БЕЗ потери раскадровки
#
# Владелец просил дословно: «сделай чтобы я допустим мог раскадровку оставить
# поменять стиль и картинки уже в новом стиле были». До этого такой кнопки не
# было вообще: единственный способ «переделать» — generate-scenes, а он
# начинается со `for s in track.scenes: db.delete(s)`, то есть уничтожает
# сцены, тайминги, описания и утверждения.
#
# Здесь меняется РОВНО ОДНО — визуал. Сцены, порядок, тайминги, крупности,
# персонажи и текст остаются на месте.
#
# Три честности, без которых это была бы ловушка:
#   1. Цена считается ДО запуска и теми же функциями, что списание
#      (_frames_cost / VIDEO_COST): второй кассы в сервисе нет.
#   2. Готовое видео не исчезает молча — оно уезжает в SceneVersion, и
#      человеку говорят об этом ДО нажатия, вместе с ценой пересъёмки.
#   3. Пересборка видео по умолчанию ВЫКЛЮЧЕНА. Это самая дорогая часть
#      работы, и включать её за человека нельзя.
# ═════════════════════════════════════════════════════════════════════════════

def _restyle_scope(track: Track, scene_ids: list[int] | None) -> list:
    """Какие сцены перерисовываем. Пустой список id = все сцены трека."""
    ids = {int(x) for x in (scene_ids or []) if str(x).strip().lstrip("-").isdigit()}
    out = []
    for sc in sorted(track.scenes, key=lambda x: (x.position, x.id)):
        if ids and sc.id not in ids:
            continue
        if not (sc.image_prompt or "").strip():
            continue
        if sc.image_prompt.startswith("(готовый кадр"):
            continue
        out.append(sc)
    return out


def _restyle_plan(db: Session, user: User, track: Track, *, keys: list[str],
                  extra: str = "", scene_ids: list[int] | None = None,
                  with_video: bool = False, text_engine: str = "") -> dict:
    """Смета рестайла. НИЧЕГО не списывает и ничего не меняет.

    Считается теми же функциями, что и списание, — иначе витрина и касса
    однажды посчитают цену по-разному, а разбираться с этим будет человек,
    у которого не сошлось."""
    scenes = _restyle_scope(track, scene_ids)
    img_engine = _resolve_image_engine(user, track)
    prov = _allowed_provider(user, "seedance" if mediagen.seedance_available() else "grok")
    vid_engine = _resolve_video_engine(user, track, prov)
    frames_per = _frames_cost(user, None, img_engine)
    video_per = VIDEO_COST.get(vid_engine, 0)
    with_video_scenes = [sc for sc in scenes if sc.video_filename]

    # Нужно ли переписывать ТЕКСТЫ промптов. Нужно только старым
    # раскадровкам: у них стиль вписан в каждый image_prompt, и после смены
    # чипов в промпт уезжают два стиля сразу.
    prompts_base = _prompts_style_base(track)
    need_prompts = prompts_base is not None and prompts_base != ",".join(keys)
    t_engine = _resolve_text_engine(user, track.project, track, text_engine)

    frames_total = frames_per * len(scenes)
    video_total = video_per * len(scenes)
    total = frames_total + (video_total if with_video else 0) \
        + (TEXT_COST.get(t_engine, 0) if need_prompts else 0)

    warn = []
    if with_video_scenes and not with_video:
        warn.append("video_stale")
    if track.clip_filename:
        warn.append("clip_stale")
    if need_prompts:
        warn.append("prompts_rewrite")

    balance = int(user.gen_points or 0)
    return {
        "style_keys": keys,
        "style_label": prompts_catalog.labels(keys, "ru"),
        "scenes_total": len(track.scenes),
        "scenes_selected": len(scenes),
        "scene_ids": [sc.id for sc in scenes],
        "prompts": {"needed": need_prompts, "engine": t_engine,
                    "points": TEXT_COST.get(t_engine, 0) if need_prompts else 0},
        "frames": {"engine": img_engine, "per_scene": frames_per,
                   "total": frames_total},
        "video": {"engine": vid_engine, "provider": prov, "per_scene": video_per,
                  "scenes_with_video": len(with_video_scenes),
                  "included": bool(with_video),
                  "total_if_included": video_total},
        "total": total,
        "balance": balance,
        "enough": bool(user.is_admin or balance >= total),
        "warn": warn,
    }


def _parse_style_keys(user: User, raw) -> tuple[list[str], str]:
    """Ключи стилей из тела запроса — ровно теми правилами, что и в
    set_track_style: тариф решает, чем снимать, и закрытый стиль молча
    выпадает, а не роняет запрос."""
    if isinstance(raw, str):
        raw = raw.split(",")
    keys: list[str] = []
    seen: set[str] = set()
    paid = bool(user.is_admin or _plan_of(user) != "free")
    for item in (raw or []):
        k = str(item or "").strip()
        if not k or k in seen or k not in prompts_catalog.STYLE_KEYS:
            continue
        if not paid and (prompts_catalog.public_style(k) or {}).get("tier") == "pro":
            continue
        seen.add(k)
        keys.append(k)
        if len(keys) >= 3:
            break
    return keys, ""


@app.post("/api/tracks/{track_id}/restyle/quote")
async def restyle_quote(track_id: int, request: Request,
                        user: User = Depends(current_user),
                        db: Session = Depends(db_session)):
    """Сколько будет стоить и что при этом потеряется. Ничего не списывает."""
    track = _own_track(db, user, track_id)
    body = await request.json() if await request.body() else {}
    keys, _ = _parse_style_keys(user, body.get("style_keys"))
    if not keys:
        keys = _track_style_keys(track)
    return _restyle_plan(
        db, user, track, keys=keys,
        extra=str(body.get("extra") or ""),
        scene_ids=body.get("scene_ids") or [],
        with_video=bool(body.get("with_video")),
        text_engine=str(body.get("text_engine") or ""),
    )


def _run_restyle(track_id: int, scene_ids: list[int], img_engine: str,
                 with_video: bool, vid_engine: str, provider: str,
                 need_prompts: bool, text_engine: str,
                 frames_points: int = 0) -> None:
    """Очередь рестайла: переписать промпты (если надо) → кадры → видео.

    Последовательно, как _run_all_frames, и по той же причине: шлюзы
    картинок обслуживают одну браузерную сессию на весь Организм."""
    db = SessionLocal()
    total = len(scene_ids)
    try:
        track = db.get(Track, track_id)
        if not track:
            return
        track.restyle_status = "running"
        track.restyle_note = f"0 из {total}"
        db.commit()
        if need_prompts:
            try:
                _rewrite_scene_prompts(db, track, scene_ids, text_engine)
                db.commit()
            except Exception as e:  # noqa: BLE001
                # Платный текстовый шаг не состоялся — возвращаем токены и
                # останавливаемся. Перерисовывать кадры со старыми промптами
                # нельзя: в кадр уедут два стиля сразу, и человек заплатит за
                # ровно ту картинку, от которой уходил.
                db.rollback()
                _text_refund(db, track.project, text_engine,
                             "возврат: промпты не переписались",
                             ref_type="track", ref_id=track.id)
                # И за кадры тоже: до движка картинок дело не дошло, ни один
                # кадр не нарисован. Списание, за которым не последовало
                # работы, — это не «цена попытки», это просто пропавшие
                # деньги человека.
                owner = (db.get(User, track.project.owner_id)
                         if track.project.owner_id else None)
                if owner and frames_points > 0:
                    _refund(db, owner, frames_points,
                            f"возврат: перерисовка трека {track.id} не началась",
                            ref_type="track", ref_id=track.id,
                            track_id=track.id, project_id=track.project_id)
                todo = set(scene_ids)
                for sc in track.scenes:
                    if sc.id not in todo or sc.image_status != "queued":
                        continue
                    sc.image_status = ""
                    if owner:
                        # Счётчик оплаченного по сцене откатываем вместе с
                        # деньгами: иначе следующая генерация решит, что
                        # сцена уже оплачена, и мы отдадим её даром.
                        sc.charged_points = max(
                            0, int(sc.charged_points or 0)
                            - _frames_cost(owner, sc, img_engine))
                db.commit()
                raise
        db.close()
        for i, sid in enumerate(scene_ids, start=1):
            _run_scene_frames(sid, engine=img_engine, keep_version=True)
            db = SessionLocal()
            try:
                tr = db.get(Track, track_id)
                if tr:
                    tr.restyle_note = f"{i} из {total}"
                    db.commit()
            finally:
                db.close()
        if with_video:
            for sid in scene_ids:
                db = SessionLocal()
                try:
                    sc = db.get(Scene, sid)
                    if sc and sc.image_filename:
                        sc.video_provider = provider
                        sc.video_engine = vid_engine
                        sc.video_status = "queued"
                        sc.video_error = ""
                        db.commit()
                finally:
                    db.close()
            for sid in scene_ids:
                _run_scene_video(sid)
        db = SessionLocal()
        tr = db.get(Track, track_id)
        if tr:
            tr.restyle_status = "done"
            tr.restyle_note = f"готово: {total}"
            # Тексты сцен теперь без стиля — следующий рестайл этого трека
            # обойдётся уже без обращения к модели.
            tr.prompts_style_keys = PROMPTS_NO_STYLE
            # Собранный клип снят по прежним кадрам. Файл не трогаем:
            # человек имеет право оставить старую склейку.
            if tr.clip_filename:
                tr.clip_stale = True
            db.commit()
        log.info("рестайл трека %s завершён (%s сцен)", track_id, total)
    except Exception as e:  # noqa: BLE001
        try:
            db.rollback()
        except Exception:  # noqa: BLE001
            pass
        db2 = SessionLocal()
        try:
            tr = db2.get(Track, track_id)
            if tr:
                tr.restyle_status = "error"
                tr.restyle_note = _err_text(e)
                # Сцены, до которых очередь не дошла, не должны остаться в
                # «queued» навсегда: иначе кнопка вечно показывает «рисую».
                for sc in tr.scenes:
                    if sc.id in set(scene_ids) and sc.image_status == "queued":
                        sc.image_status = ""
                db2.commit()
        finally:
            db2.close()
        log.warning("рестайл трека %s упал: %s", track_id, _err_text(e))
    finally:
        try:
            db.close()
        except Exception:  # noqa: BLE001
            pass


def _rewrite_scene_prompts(db: Session, track: Track, scene_ids: list[int],
                           engine: str) -> None:
    """Вычистить прежний стиль из текстов промптов старой раскадровки.

    Сцены, тайминги, крупности и персонажи не трогаются — модели это
    запрещено системным промптом (claude.RESTYLE_SYSTEM), а здесь мы ещё и
    принимаем ТОЛЬКО два поля из ответа. Остальное игнорируется, даже если
    модель их вернула."""
    scenes = [sc for sc in track.scenes if sc.id in set(scene_ids)]
    if not scenes:
        return
    payload = [{
        "position": sc.position, "shot_size": sc.shot_size,
        "camera_move": sc.camera_move, "shot_note": sc.shot_note,
        "characters": [c.strip() for c in (sc.characters or "").split(",") if c.strip()],
        "image_prompt": sc.image_prompt,
        "image_prompt_last": sc.image_prompt_last,
    } for sc in scenes]
    import asyncio
    res = asyncio.run(claude.restyle_prompts(
        scenes=payload,
        story_base=prompts_catalog.story_base(_track_style_keys(track)),
        character_bible=track.project.character_bible,
        characters=characters_payload(track.project),
        engine=engine,
    ))
    by_pos = {int(r.get("position") or 0): r for r in (res.get("scenes") or [])}
    changed = 0
    for sc in scenes:
        row = by_pos.get(sc.position)
        if not row:
            continue
        first = str(row.get("image_prompt") or "").strip()
        last = str(row.get("image_prompt_last") or "").strip()
        if first:
            sc.image_prompt = first
            changed += 1
        if last:
            sc.image_prompt_last = last
    log.info("рестайл трека %s: переписано промптов %s из %s",
             track.id, changed, len(scenes))


@app.post("/api/tracks/{track_id}/restyle")
async def restyle_track(track_id: int, request: Request,
                        user: User = Depends(current_user),
                        db: Session = Depends(db_session)):
    """Перерисовать кадры в новом стиле, сохранив раскадровку.

    body: {style_keys: [...], extra?: "", scene_ids?: [...],
           with_video?: false, text_engine?: ""}
    """
    track = _own_track(db, user, track_id)
    if not track.scenes:
        raise HTTPException(400, "сначала сгенерируй раскадровку")
    if track.scenes_status in ("queued", "running") \
            or track.supergen_status in ("queued", "running") \
            or track.restyle_status in ("queued", "running"):
        raise HTTPException(409, "по этому объекту уже идёт работа — дождись её")
    body = await request.json() if await request.body() else {}
    keys, _ = _parse_style_keys(user, body.get("style_keys"))
    if not keys:
        keys = _track_style_keys(track)
    if not keys:
        raise HTTPException(400, "не выбран ни один стиль")
    extra = str(body.get("extra") or "").strip()[:2000]
    with_video = bool(body.get("with_video"))
    plan = _restyle_plan(db, user, track, keys=keys, extra=extra,
                         scene_ids=body.get("scene_ids") or [],
                         with_video=with_video,
                         text_engine=str(body.get("text_engine") or ""))
    if not plan["scene_ids"]:
        raise HTTPException(400, "нечего перерисовывать: у сцен нет промптов")

    # СТИЛЬ МЕНЯЕТСЯ У ТРЕКА ЦЕЛИКОМ, даже при выборочной перерисовке:
    # стиль — свойство трека, а не кадра. У неперерисованных сцен остаётся
    # прежний scene.style_keys, и карточка честно помечает их «снято в
    # прежнем стиле» — смешанный трек это законное состояние, но видимое.
    if plan["prompts"]["needed"] and not (track.prompts_style_keys or "").strip():
        # Легаси-раскадровка: фиксируем, под какой стиль писаны её тексты,
        # прежде чем сменить стиль трека.
        track.prompts_style_keys = ",".join(_track_style_keys(track))
    track.style_keys = ",".join(keys)
    track.style_extra = extra
    track.style = prompts_catalog.fusion(keys, extra)

    scene_ids = list(plan["scene_ids"])
    scenes = [sc for sc in track.scenes if sc.id in set(scene_ids)]
    img_engine = plan["frames"]["engine"]
    _scenes_charge(db, user, scenes, lambda sc: _frames_cost(user, sc, img_engine),
                   f"перерисовка кадров трека {track.id} ({img_engine})",
                   kind="frames", engine=img_engine, track_id=track.id,
                   project_id=track.project_id)
    if plan["prompts"]["needed"]:
        _text_charge(db, user, plan["prompts"]["engine"],
                     f"перепись промптов трека {track.id} "
                     f"({plan['prompts']['engine']})",
                     ref_type="track", ref_id=track.id, track_id=track.id,
                     project_id=track.project_id)
    if with_video:
        # Только ВИДЕО, без кадров: за кадры уже списано строкой выше.
        # _scene_cost() здесь дал бы «кадры + видео» и содрал бы за кадры
        # второй раз — а смета показала человеку другое число.
        video_per = plan["video"]["per_scene"]
        _scenes_charge(db, user, scenes, lambda sc: video_per,
                       f"пересъёмка видео трека {track.id} ({plan['video']['engine']})",
                       kind="video", engine=plan["video"]["engine"],
                       track_id=track.id, project_id=track.project_id)
    for sc in scenes:
        sc.image_status = "queued"
    track.restyle_status = "queued"
    track.restyle_note = f"0 из {len(scene_ids)}"
    db.commit()
    _spawn_gen(user, _run_restyle, track.id, scene_ids, img_engine, with_video,
               plan["video"]["engine"], plan["video"]["provider"],
               bool(plan["prompts"]["needed"]), plan["prompts"]["engine"],
               plan["frames"]["total"], kind="restyle")
    return {"ok": True, "queued": len(scene_ids), "plan": plan}


# ───────────────────── версии кадров сцены ─────────────────────

def _version_dict(v: SceneVersion) -> dict:
    return {
        "id": v.id, "scene_id": v.scene_id,
        "at": (_as_utc(v.created_at) or now()).isoformat(),
        "kind": v.kind or "frames",
        "style_keys": [k for k in (v.style_keys or "").split(",") if k],
        "style_label": v.style_label or "",
        "image_url": f"/api/media/{v.image_filename}" if v.image_filename else "",
        "image_last_url": f"/api/media/{v.image_last_filename}" if v.image_last_filename else "",
        "image_thumb_url": f"/api/thumb/{v.image_filename}" if v.image_filename else "",
        "image_last_thumb_url": (f"/api/thumb/{v.image_last_filename}"
                                 if v.image_last_filename else ""),
        "video_url": f"/api/media/{v.video_filename}" if v.video_filename else "",
        # ПОСТЕР, А НЕ <video>. Тяжёлые файлы в сетке браузер просто
        # перестаёт грузить, упершись в лимит одновременных медиа, — это
        # уже проходили на архиве файлов.
        "video_poster_url": (f"/api/thumb/{v.video_filename}"
                             if v.video_filename else ""),
        "image_engine": v.image_engine or "", "video_engine": v.video_engine or "",
        "cost_points": int(v.cost_points or 0),
        "bytes": int(v.bytes or 0),
        "pinned": bool(v.pinned),
        "project_id": int(v.project_id or 0), "track_id": int(v.track_id or 0),
        "note": v.note or "",
    }


@app.get("/api/scenes/{scene_id}/versions")
def scene_versions(scene_id: int, user: User = Depends(current_user),
                   db: Session = Depends(db_session)):
    scene = _own_scene(db, user, scene_id)
    rows = (db.query(SceneVersion)
            .filter(SceneVersion.scene_id == scene.id)
            .order_by(SceneVersion.id.desc()).all())
    return {"versions": [_version_dict(v) for v in rows],
            "keep": _versions_keep(user), "days": _versions_days(user),
            "plan": _plan_of(user)}


@app.post("/api/scenes/{scene_id}/versions/{version_id}/pin")
async def pin_scene_version(scene_id: int, version_id: int, request: Request,
                            user: User = Depends(current_user),
                            db: Session = Depends(db_session)):
    """Закрепить вариант: ретенция его не вытеснит и срок его не тронет.

    Место в квоте он при этом занимает — «не удаляйте это» законное
    желание, «храните вечно и бесплатно» уже нет."""
    scene = _own_scene(db, user, scene_id)
    ver = db.get(SceneVersion, version_id)
    if not ver or ver.scene_id != scene.id:
        raise HTTPException(404, "версия не найдена")
    body = await request.json() if await request.body() else {}
    ver.pinned = bool(body.get("pinned", not ver.pinned))
    db.commit()
    return {"ok": True, "pinned": bool(ver.pinned)}


# ─────────────── история вариантов КРЕАТОРА (лента кабинета) ───────────────

@app.get("/api/account/versions")
def account_versions(project_id: int = 0, track_id: int = 0, kind: str = "",
                     pinned: int = 0, cursor: str = "", limit: int = 60,
                     user: User = Depends(current_user),
                     db: Session = Depends(db_session)):
    """Все варианты, сделанные ЭТИМ человеком, — лентой по времени.

    Курсорная пагинация по паре (дата, id), как в архиве файлов: пока
    человек листает, генерации продолжают писать новые варианты, и OFFSET
    начал бы повторять и пропускать строки.

    Денормализованные user_id/project_id/track_id в scene_versions нужны
    ровно здесь: без них каждая плитка требовала бы join'а до проекта."""
    limit = max(1, min(200, int(limit or 60)))
    q = db.query(SceneVersion).filter(SceneVersion.user_id == user.id)
    if project_id:
        q = q.filter(SceneVersion.project_id == int(project_id))
    if track_id:
        q = q.filter(SceneVersion.track_id == int(track_id))
    if kind:
        q = q.filter(SceneVersion.kind.in_([k for k in kind.split(",") if k]))
    if pinned:
        q = q.filter(SceneVersion.pinned.is_(True))
    totals = q.with_entities(func.count(SceneVersion.id),
                             func.coalesce(func.sum(SceneVersion.bytes), 0)).first()
    if cursor:
        c_at, _, c_id = cursor.partition("|")
        dt = _as_utc(_parse_iso(c_at))
        if dt:
            q = q.filter((SceneVersion.created_at < dt)
                         | ((SceneVersion.created_at == dt)
                            & (SceneVersion.id < int(c_id or 0))))
    rows = (q.order_by(SceneVersion.created_at.desc(), SceneVersion.id.desc())
            .limit(limit + 1).all())
    more = len(rows) > limit
    rows = rows[:limit]
    nxt = ""
    if more and rows:
        last = rows[-1]
        nxt = f"{(_as_utc(last.created_at) or now()).isoformat()}|{last.id}"
    # Позиция кадра — чтобы плитка называлась «кадр 7», а не «сцена 412».
    sids = {r.scene_id for r in rows}
    pos = {sid: p for sid, p in db.query(Scene.id, Scene.position)
           .filter(Scene.id.in_(sids)).all()} if sids else {}
    items = []
    for r in rows:
        d = _version_dict(r)
        d["scene_position"] = int(pos.get(r.scene_id) or 0)
        items.append(d)
    return {
        "items": items, "next_cursor": nxt,
        "totals": {"count": int(totals[0] or 0), "bytes": int(totals[1] or 0)},
        "keep": _versions_keep(user), "days": _versions_days(user),
        "kinds": ["frames", "video", "restyle", "midframes", "extend", "manual"],
        "projects": [{"id": p.id, "name": p.name, "kind": p.kind}
                     for p in db.query(Project).filter(Project.owner_id == user.id)
                     .order_by(Project.id.desc()).limit(100).all()],
    }


@app.post("/api/scenes/{scene_id}/versions/{version_id}/restore")
def restore_scene_version(scene_id: int, version_id: int, only: str = "all",
                          user: User = Depends(current_user),
                          db: Session = Depends(db_session)):
    """Вернуть вариант в сцену. Это ОБМЕН, а не перезапись: нынешнее
    состояние само становится вариантом, поэтому откат откатывается.

    only=video — вернуть ТОЛЬКО видео. Без этого возврат дубля видео тащил
    бы за собой и кадры, к которым он был снят, — а человек, скорее всего,
    хочет обратно именно тот дубль, оставив нынешние кадры на месте."""
    scene = _own_scene(db, user, scene_id)
    ver = db.get(SceneVersion, version_id)
    if not ver or ver.scene_id != scene.id:
        raise HTTPException(404, "версия не найдена")
    if only == "video":
        if not ver.video_filename:
            raise HTTPException(400, "в этом варианте нет видео")
        cur_video, cur_audio = scene.video_filename, scene.audio_filename
        cur_engine = scene.video_engine or ""
        scene.video_filename = ver.video_filename
        scene.audio_filename = ver.audio_filename or scene.audio_filename
        scene.video_engine = ver.video_engine or scene.video_engine
        scene.video_status = "done"
        scene.video_error = ""
        scene.video_stale = False
        # Версию выбирают осознанно — значит в клип идёт именно она.
        if not scene.approved_manual:
            scene.approved = True
        ver.video_filename, ver.audio_filename = cur_video, cur_audio
        ver.video_engine = cur_engine
        db.commit()
        db.refresh(scene)
        return scene_dict(scene)
    cur = {
        "image_filename": scene.image_filename,
        "image_last_filename": scene.image_last_filename,
        "image_prompt": scene.image_prompt,
        "image_prompt_last": scene.image_prompt_last,
        "video_filename": scene.video_filename,
        "audio_filename": scene.audio_filename,
        "image_engine": scene.image_engine or "",
        "video_engine": scene.video_engine or "",
        "style_keys": scene.style_keys or ",".join(_track_style_keys(scene.track)),
    }
    scene.image_filename = ver.image_filename
    scene.image_last_filename = ver.image_last_filename
    if (ver.image_prompt or "").strip():
        scene.image_prompt = ver.image_prompt
    if (ver.image_prompt_last or "").strip():
        scene.image_prompt_last = ver.image_prompt_last
    scene.video_filename = ver.video_filename
    scene.audio_filename = ver.audio_filename
    scene.image_engine = ver.image_engine
    scene.video_engine = ver.video_engine
    scene.style_keys = ver.style_keys
    scene.image_status = "done" if ver.image_filename else ""
    scene.image_error = ""
    scene.video_status = "done" if ver.video_filename else ""
    scene.video_error = ""
    # Вернули прежний дубль целиком — он и идёт в клип: возврат версии это
    # выбор, а не черновик. Ручной отказ по-прежнему сильнее.
    scene.approved = bool(ver.video_filename) and not scene.approved_manual
    # Промежуточные кадры относились к ТОЙ паре, которой больше нет.
    for m in _midframes(scene):
        _remove_media(m.get("filename", ""), db)
    scene.midframes_json = ""
    scene.video_stale = False
    ver.image_filename = cur["image_filename"]
    ver.image_last_filename = cur["image_last_filename"]
    ver.image_prompt = cur["image_prompt"]
    ver.image_prompt_last = cur["image_prompt_last"]
    ver.video_filename = cur["video_filename"]
    ver.audio_filename = cur["audio_filename"]
    ver.image_engine = cur["image_engine"]
    ver.video_engine = cur["video_engine"]
    ver.style_keys = cur["style_keys"]
    ver.style_label = prompts_catalog.labels(
        [k for k in cur["style_keys"].split(",") if k], "ru")
    db.commit()
    db.refresh(scene)
    return scene_dict(scene)


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
    _guard_disk()
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
    _spawn_gen(user, _run_all_videos, track_id, kind="video")
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


# ─────────── Робокасса: иностранные карты, цены в долларах ───────────
# Контур за ключами из ЛК Робокассы (Технические настройки магазина):
# ROBOKASSA_LOGIN, ROBOKASSA_PASS1 (создание платежа), ROBOKASSA_PASS2
# (проверка result-вебхука). Без ключей провайдер просто не предлагается.
ROBOKASSA_LOGIN = os.environ.get("ROBOKASSA_LOGIN", "")
ROBOKASSA_PASS1 = os.environ.get("ROBOKASSA_PASS1", "")
ROBOKASSA_PASS2 = os.environ.get("ROBOKASSA_PASS2", "")
# Иностранный ценник: решение владельца 26.08 — долларовые цены выше базы
# на 150% (множитель 2.5). Применяется к показу и к списанию валютного
# контура; рублёвую ЮKassa не трогает.
FOREIGN_PRICE_MULT = max(1.0, float(os.environ.get("FOREIGN_PRICE_MULT", "2.5")))


def _robokassa_enabled() -> bool:
    return bool(ROBOKASSA_LOGIN and ROBOKASSA_PASS1 and ROBOKASSA_PASS2)


def _rk_md5(*parts) -> str:
    import hashlib
    return hashlib.md5(":".join(str(x) for x in parts).encode()).hexdigest()


def _robokassa_link(user: User, *, kind: str, plan_id: str, period: str,
                    tier: str, pack_id: str, usd_cents: int) -> dict:
    """Ссылка на оплату Робокассой в долларах.

    Пользовательские поля едут Shp_-параметрами и входят в подпись по
    алфавиту — так вебхук получает контекст, не доверяя ему без проверки."""
    import secrets as _sec
    inv_id = _sec.randbelow(2_000_000_000) + 1
    out_sum = f"{usd_cents // 100}.{usd_cents % 100:02d}"
    shp = {"Shp_kind": kind, "Shp_pack": pack_id or "-", "Shp_period": period,
           "Shp_plan": plan_id or "-", "Shp_tier": tier or "-",
           "Shp_uid": str(user.id)}
    shp_sig = ":".join(f"{k}={shp[k]}" for k in sorted(shp))
    sig = _rk_md5(ROBOKASSA_LOGIN, out_sum, inv_id, "USD",
                  ROBOKASSA_PASS1, shp_sig)
    from urllib.parse import urlencode
    q = {"MerchantLogin": ROBOKASSA_LOGIN, "OutSum": out_sum, "InvId": inv_id,
         "OutSumCurrency": "USD", "Description": f"lolq.ai {plan_id or pack_id}",
         "SignatureValue": sig, **shp}
    return {"ok": True, "provider": "robokassa", "currency": "usd",
            "url": "https://auth.robokassa.ru/Merchant/Index.aspx?" + urlencode(q),
            "payment_id": str(inv_id), "kind": kind, "plan": plan_id,
            "tier": tier, "pack": pack_id, "period": period}


@app.post("/api/billing/robokassa-result")
async def robokassa_result(request: Request, db: Session = Depends(db_session)):
    """Result-вебхук Робокассы. Подпись — md5 с Паролем №2, ответ «OKInvId»."""
    form = dict((await request.form()).items()) if         (request.headers.get("content-type") or "").startswith("application/x-www")         else dict(request.query_params)
    out_sum = str(form.get("OutSum") or "")
    inv_id = str(form.get("InvId") or "")
    got = str(form.get("SignatureValue") or "").lower()
    shp = {k: str(v) for k, v in form.items() if k.startswith("Shp_")}
    shp_sig = ":".join(f"{k}={shp[k]}" for k in sorted(shp))
    want = _rk_md5(out_sum, inv_id, ROBOKASSA_PASS2, shp_sig).lower()
    if not _robokassa_enabled() or got != want:
        raise HTTPException(400, "bad sign")
    user = db.get(User, int(shp.get("Shp_uid") or 0))
    if not user:
        raise HTTPException(404, "user not found")
    cents = int(round(float(out_sum) * 100))
    _grant_payment(
        db, user, provider="robokassa", payment_id=inv_id,
        kind=("topup" if shp.get("Shp_kind") == "topup" else "plan"),
        plan_id=("" if shp.get("Shp_plan") in ("-", "") else shp["Shp_plan"]),
        period=shp.get("Shp_period") or "month",
        pack_id=("" if shp.get("Shp_pack") in ("-", "") else shp["Shp_pack"]),
        amount_cents=cents, currency="USD",
        tier=("" if shp.get("Shp_tier") in ("-", "") else shp["Shp_tier"]))
    return Response(f"OK{inv_id}", media_type="text/plain")


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


#: Сколько машинного времени занимает одна сцена — из замеров: кадры около
#: минуты, видео две-пять. Число нужно ровно для одной строки витрины
#: («клип за ~11 минут»), и оно обязано быть одним на весь сервис.
SCENE_WALL_SEC = int(os.environ.get("SCENE_WALL_SEC", "90"))


def _clip_minutes(parallel: int) -> int:
    """Во сколько минут превращается тариф: клип из CLIP_SCENES сцен при
    заданной параллельности.

    ОБЕЩАЕМ СКОРОСТЬ ГЕНЕРАЦИИ, А НЕ СБОРКИ. Финальная склейка идёт по одной
    на весь сервис (mediagen.FFMPEG_SLOTS = 1: у машины 3.8 ГБ без свопа и
    рядом чужие проекты), и витрина обязана говорить это прямо, иначе «клип за
    3 минуты» станет ложью на последнем шаге."""
    n = max(1, int(parallel or 1))
    return max(1, int(round(CLIP_SCENES * SCENE_WALL_SEC / n / 60)))


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
        # Параллельность СТУПЕНИ. Берётся из gate.py — того же места, откуда её
        # берёт сам ограничитель, а не переписывается в вёрстку.
        "parallel": gate.limit_for(plan_id, spec["id"]),
        "clip_minutes": _clip_minutes(gate.limit_for(plan_id, spec["id"])),
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
        # ПАРАЛЛЕЛЬНОСТЬ — продаётся как ВРЕМЯ, а не как число потоков.
        # «до 8 видео одновременно» — механизм; единица нашего продукта клип,
        # поэтому рядом едет и то, во что это превращается: клип 3 минуты
        # (CLIP_SCENES сцен) при ~90 секундах машинного времени на сцену.
        "parallel": gate.limit_for(plan_id),
        "clip_minutes": _clip_minutes(gate.limit_for(plan_id)),
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
    return {"stripe": _stripe_enabled(), "yookassa": _yookassa_enabled(),
            "robokassa": _robokassa_enabled()}


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
        # ДВЕ ДАТЫ, А НЕ ОДНА. «Оплачено до» и «токены до» — разные вещи у
        # годового подписчика: год оплачен целиком, а норма капает раз в месяц
        # (_points_drip_pass). Пока витрина знала одну дату, человек с годовой
        # подпиской не понимал, когда придут следующие токены, и читал остаток
        # как «мне выдали меньше, чем обещали».
        "points_next_at": (user.points_drip_at.isoformat()
                           if (user and user.points_drip_at) else ""),
        "points_next": int(user.points_drip_size or 0) if user else 0,
        "points_drip_left": int(user.points_drip_left or 0) if user else 0,
        # Параллельность ЭТОГО человека и что у него в работе прямо сейчас.
        "parallel": _parallel_limit(user),
        "queue": _queue_state(user),
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
        "robokassa_enabled": _robokassa_enabled(),
        # Витрина в долларах умножает на иностранный множитель здесь же:
        # цена на кнопке обязана совпадать с ценой списания.
        "foreign_mult": FOREIGN_PRICE_MULT,
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
    if provider not in ("stripe", "yookassa", "robokassa"):
        provider = "yookassa" if currency in ("rub", "rur", "₽") else "stripe"
    # Выключенный провайдер — не тупик: доллары идут stripe → robokassa,
    # рубли — yookassa. Иностранные карты живут на Робокассе, пока Stripe
    # без ключей.
    if provider == "stripe" and not _stripe_enabled():
        provider = "robokassa" if _robokassa_enabled() else "yookassa"
    elif provider == "yookassa" and not _yookassa_enabled():
        provider = "stripe" if _stripe_enabled() else "robokassa"
    elif provider == "robokassa" and not _robokassa_enabled():
        provider = "stripe" if _stripe_enabled() else "yookassa"
    if (provider == "stripe" and not _stripe_enabled()) or \
       (provider == "yookassa" and not _yookassa_enabled()) or \
       (provider == "robokassa" and not _robokassa_enabled()):
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

    # ИНОСТРАННЫЙ ЦЕННИК. Долларовые платежи (Stripe/Робокасса) идут по цене
    # ×FOREIGN_PRICE_MULT — решение владельца: зарубежный клиент платит выше
    # базы, рублёвая ЮKassa остаётся как была.
    if provider in ("stripe", "robokassa"):
        amount_cents = int(amount_cents * FOREIGN_PRICE_MULT)
        discount_cents = int(discount_cents * FOREIGN_PRICE_MULT)

    meta_promo = amb.ref_code if amb else ""
    if provider == "robokassa":
        pay_cents = amount_cents - discount_cents
        return _robokassa_link(user, kind=kind, plan_id=plan_id, period=period,
                               tier=tier, pack_id=pack_id, usd_cents=pay_cents)
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
        # МАГАЗИН БЕЗ РЕКУРРЕНТКИ. Пока ЮKassa не включила магазину
        # автоплатежи, просьба сохранить карту валит ВЕСЬ платёж 403-м —
        # человек не может купить тариф вовсе. Честный компромисс: проводим
        # оплату без сохранения. Подписка встаёт, автопродления не будет —
        # воркер напомнит о ручном продлении, а не спишет сам.
        if r.status_code == 403 and payload.get("save_payment_method") \
                and "recurring" in r.text.lower():
            log.warning("ЮKassa: магазину не включены автоплатежи — "
                        "провожу оплату без сохранения карты")
            payload["save_payment_method"] = False
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
    # ДВА УРОВНЯ ПАРТНЁРКИ. Простая доступна всем и платит ТОКЕНАМИ: человек
    # берёт ссылку и зовёт друзей. Режим амбассадора с денежными выплатами
    # включает админ вручную — деньги наружу не должны раздаваться по кнопке
    # «подключить», это отдельные отношения и отдельная отчётность.
    if not user.ref_code:
        user.ref_code = _new_ref_code(db)
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
        # Простая партнёрка: подключена, если код уже выдан. Именно по этому
        # признаку фронт рисует ссылку и счётчик токенов, а не по статусу
        # амбассадора — иначе обычный человек не увидел бы вообще ничего.
        "joined": bool(user.ref_code),
        "bonus_points": int(user.bonus_points or 0),
        "signup_bonus": REF_SIGNUP_BONUS,
        "cashback_pct": REF_CASHBACK_PCT,
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
        "points": user.gen_points,
        # Кошелёк один, но человек должен видеть, что из него заработано
        # приглашениями: иначе кэшбэк выглядит как «просто цифра стала больше».
        "bonus_points": int(user.bonus_points or 0),
        "bonus_rate": BONUS_RATE,
        "paid_points": int(user.gen_points or 0),
        "projects": projects,
        # Сколько клипов по 3 минуты ещё выйдет из остатка. Считаем по рабочей
        # лошадке тарифа (самый дешёвый ПЛАТНЫЙ движок): по самому дорогому на
        # PRO MAX выходит «0 клипов», хотя на Seedance 2 Mini их три.
        "movies_left": _movies_estimate(int(user.gen_points or 0),
                                        _plan_work_cost(plan)),
        "movies_left_top": _movies_estimate(int(user.gen_points or 0),
                                            max(_plan_engines(plan).values())),
        "linked": {"telegram": bool(user.tg_id), "yandex": bool(user.yandex_id),
                   "google": bool(user.google_id), "password": bool(user.login)},
        "tg_linked": bool(user.tg_id), "tg_username": user.tg_username or "",
        "phone_linked": bool(user.phone), "phone_masked": _mask_phone(user.phone),
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

@app.get("/api/gen/queue")
def api_gen_queue(user: User = Depends(current_user)):
    """Сколько задач у человека в работе, сколько ждёт и сколько даёт тариф.

    ЭТО ПОЗИЦИЯ В ОЧЕРЕДИ, А НЕ ТАЙМЕР. Выдуманный обратный отсчёт («осталось
    2 минуты») врёт при первой же длинной сцене; число «третья в очереди»
    проверяемо и не устаревает. Так же требует и docs/DESIGN_SYSTEM.md."""
    st = _queue_state(user)
    st["plan"] = _plan_of(user)
    st["tier"] = _tier_of_user(user)
    return st


USAGE_KINDS = ("frames", "video", "chat", "audio", "story", "sheet",
               "model", "assemble", "other")


@app.get("/api/account/api-credit")
async def account_api_credit(user: User = Depends(current_user)):
    """Остаток на нашем счету у поставщика движков — только владельцу.

    Обычному человеку это число ничего не говорит и говорить не должно: у
    него свои токены. Владельцу оно жизненно важно — расход админа во
    внутренний журнал не попадает, и уход счёта в минус иначе замечается
    только по отказам генерации.
    """
    if not user.is_admin:
        raise HTTPException(404, "не найдено")
    return await mediagen.kie_credit()


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
    ver = (db.query(SceneVersion)
           .filter((SceneVersion.image_filename == fname)
                   | (SceneVersion.image_last_filename == fname)
                   | (SceneVersion.video_filename == fname)
                   | (SceneVersion.audio_filename == fname))
           .first())
    if ver:
        # Снимок предыдущего стиля — это кнопка «вернуть как было». Молча
        # стереть его файл значит сделать откат кнопкой в пустоту.
        return f"version:{ver.scene_id}"
    return ""


def _storage_used_bytes(db: Session, user_id: int) -> int:
    """Сколько человек реально занимает на диске.

    Группировка по phys_key обязательна: копия проекта — это жёсткие
    ссылки, у одного куска диска несколько имён и несколько строк. Сумма
    size_bytes «в лоб» посчитала бы копию второй раз, и человек упёрся бы в
    квоту, не заняв ни байта. Легаси-строки без phys_key считаем по имени —
    для них это ровно прежнее поведение."""
    from sqlalchemy import text as _sqltext
    try:
        row = db.execute(_sqltext(
            "SELECT COALESCE(SUM(sz), 0) FROM ("
            "  SELECT MAX(size_bytes) AS sz FROM file_owners"
            "  WHERE user_id = :u AND deleted_at IS NULL"
            "  GROUP BY CASE WHEN phys_key = '' OR phys_key IS NULL"
            "                THEN filename ELSE phys_key END)"),
            {"u": int(user_id)}).first()
        return int((row or [0])[0] or 0)
    except Exception as e:  # noqa: BLE001
        log.warning("квота диска: не посчиталась (%s)", str(e)[:150])
        return 0


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
    owner = db.get(User, owner_id) if owner_id != user.id else user
    used = _storage_used_bytes(db, owner_id)
    quota = _storage_quota_bytes(owner)
    return {
        "items": [_file_dict(f) for f in rows],
        "next_cursor": nxt,
        "totals": {"count": sum(r["count"] for r in by_kind),
                   "bytes": sum(r["bytes"] for r in by_kind),
                   "by_kind": sorted(by_kind, key=lambda r: -r["bytes"])},
        # МЕСТО. Показываем честно и по физическим файлам: «занято 1.2 из 15
        # ГБ» объясняет, почему копировать проекты можно смело (копия не
        # занимает ничего), и заранее говорит, когда пора прибираться.
        "quota": {"used_bytes": used, "limit_bytes": quota,
                  "pct": round(100.0 * used / quota, 1) if quota else 0.0,
                  "plan": _plan_of(owner) if owner else "free",
                  "clip_keep_days": CLIP_KEEP_DAYS},
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


def _files_verify_pass(limit: int = 4000) -> int:
    """Сверить ВЕСЬ индекс архива с диском и пометить пропавшее удалённым.

    Не то же, что _files_stat_pass: тот трогает только строки без размера и
    сам себя исчерпывает. А врали строки С размером — те, чей файл удалили
    через _remove_media, который в file_owners не заглядывал вовсе. На живом
    проде таких набралось 171 штука на 3.87 ГБ по индексу: архив показывал
    занятое на 59 % больше реального. Квота, посчитанная по таким данным, —
    это не квота, а генератор ложных отказов.

    Проход курсорный по имени: полный обход раскладывается на несколько
    заходов и не держит базу."""
    from sqlalchemy import text as _sqltext
    from db import engine as _engine
    global _VERIFY_CURSOR
    gone = 0
    try:
        with _engine.begin() as conn:
            rows = conn.execute(_sqltext(
                "SELECT filename FROM file_owners WHERE deleted_at IS NULL "
                "AND filename > :c ORDER BY filename LIMIT :n"),
                {"c": _VERIFY_CURSOR, "n": int(limit)}).fetchall()
            if not rows:
                _VERIFY_CURSOR = ""      # круг закончен — начинаем сначала
                return 0
            for (fname,) in rows:
                _VERIFY_CURSOR = fname
                if os.path.exists(os.path.join(UPLOAD_DIR, fname)):
                    continue
                conn.execute(_sqltext(
                    "UPDATE file_owners SET deleted_at = :t WHERE filename = :f"),
                    {"t": now(), "f": fname})
                gone += 1
        if gone:
            log.info("архив файлов: %s записей указывали в никуда — помечены", gone)
    except Exception as e:  # noqa: BLE001
        log.warning("архив файлов: сверка с диском не прошла: %s", str(e)[:200])
    return gone


_VERIFY_CURSOR = ""


# ═════════════════════ МЕСТО НА ДИСКЕ: смотреть и убирать ═════════════════════
#
# У машины 48 ГБ на ВСЁ, и на том же разделе живут чужие проекты. Значит
# «кончилось место» здесь — это не наша неудобная ошибка, а упавшая чужая
# база. Поэтому свободное место — такой же первоклассный ресурс, как токены:
# его видно в /api/health, его стережёт демон, и при нехватке генерации
# отвечают отказом ДО того, как начнут писать.

def _disk_stat() -> dict:
    """Свободное место по РАЗДЕЛУ, а не по нашей папке: соседи стоят на том
    же /dev/sda1, и упрёмся мы вместе."""
    try:
        st = os.statvfs(UPLOAD_DIR)
    except OSError:
        return {"total": 0, "free": 0, "used": 0, "free_pct": 100.0}
    total = st.f_blocks * st.f_frsize
    free = st.f_bavail * st.f_frsize
    return {
        "total": int(total), "free": int(free), "used": int(total - free),
        "free_pct": round(100.0 * free / total, 2) if total else 100.0,
    }


def _mem_available() -> int:
    """Свободная память хоста в байтах (0 — не смогли узнать). Свопа на
    машине нет, поэтому это единственный запас, который у нас есть."""
    try:
        with open("/proc/meminfo") as f:
            for line in f:
                if line.startswith("MemAvailable:"):
                    return int(line.split()[1]) * 1024
    except OSError:
        pass
    return 0


def _sweep_dir(path: str, older_than_s: int, prefixes: tuple = ()) -> int:
    """Стереть временные файлы старше N секунд. Возвращает освобождённые байты."""
    freed = 0
    edge = time.time() - older_than_s
    try:
        names = os.listdir(path)
    except OSError:
        return 0
    for name in names:
        if prefixes and not name.startswith(prefixes):
            continue
        full = os.path.join(path, name)
        try:
            st = os.stat(full)
            if st.st_mtime > edge:
                continue
            size = st.st_size
            if os.path.isdir(full):
                shutil.rmtree(full, ignore_errors=True)
            else:
                os.remove(full)
            freed += size
        except OSError:
            continue
    return freed


def _reclaim(need_bytes: int = 0, aggressive: bool = False) -> dict:
    """Убрать лишнее. Порядок — по ЦЕНЕ ВОССТАНОВЛЕНИЯ, а не по размеру:
    первым уходит то, что вернётся само и бесплатно.

    БЕЗОПАСНЫЙ УРОВЕНЬ (всегда):
      1. миниатюры        — 0 токенов, пересоберутся лениво;
      2. outbox/сборки/экспорт — мусор незавершённых задач;
      3. фантомные строки — 0 байт, но чинит учёт (см. _files_verify_pass);
      4. варианты сверх ТАРИФНОГО лимита — это ровно та ретенция, о которой
         человеку сказано в интерфейсе.

    АГРЕССИВНЫЙ УРОВЕНЬ (только когда места правда нет):
      5. собранные клипы старше CLIP_KEEP_DAYS. Восстановление стоит 0
         токенов (ffmpeg склеит заново из тех же видео), но это ЧУЖОЙ
         готовый артефакт, и трогать его на всякий случай нельзя: молча
         исчезнувший клип — это ровно то, что человек читает как «сервис
         потерял мою работу».

    НИКОГДА автоматически: живые кадры и видео сцены, дорожки треков, фото
    персонажей, закреплённые варианты. Это чужая оплаченная работа."""
    freed = 0
    steps: list[str] = []
    freed_now = _sweep_dir(THUMB_DIR, 3 * 24 * 3600)
    if freed_now:
        steps.append(f"миниатюры {freed_now // 1024 // 1024} МБ")
    freed += freed_now
    freed_now = _sweep_dir(mediagen.OUTBOX_DIR, 3600)
    freed_now += _sweep_dir(EXPORT_DIR, 3600)
    freed_now += _sweep_dir(UPLOAD_DIR, 3600, prefixes=("build_", "refjoin_", "gw_", "raw_"))
    if freed_now:
        steps.append(f"временные {freed_now // 1024 // 1024} МБ")
    freed += freed_now
    # Сверка индекса с диском и физическое удаление помеченного: байты сюда
    # не приплюсовываем — они уже посчитаны там, где файл удаляли.
    _files_verify_pass(limit=20000)
    _files_purge_pass()
    if need_bytes and freed >= need_bytes:
        return {"freed": freed, "steps": steps}
    freed_now = _reclaim_versions()
    if freed_now:
        steps.append(f"варианты сверх лимита {freed_now // 1024 // 1024} МБ")
    freed += freed_now
    if aggressive and (not need_bytes or freed < need_bytes):
        freed_now = _reclaim_clips()
        if freed_now:
            steps.append(f"собранные клипы {freed_now // 1024 // 1024} МБ")
        freed += freed_now
    return {"freed": freed, "steps": steps}


def _reclaim_clips() -> int:
    """Собранные клипы, к которым давно не возвращались. Ноль токенов на
    восстановление — единственный крупный артефакт с таким свойством."""
    db = SessionLocal()
    freed, gone = 0, 0
    try:
        edge = now() - timedelta(days=CLIP_KEEP_DAYS)
        rows = (db.query(Track)
                .filter(Track.clip_filename != "",
                        Track.clip_status != "running")
                .all())
        for tr in rows:
            seen = _as_utc(tr.clip_seen_at) or _as_utc(tr.updated_at)
            if seen and seen > edge:
                continue
            freed += _media_bytes(tr.clip_filename)
            _remove_media(tr.clip_filename, db)
            tr.clip_filename = ""
            tr.clip_status = ""
            gone += 1
        if freed:
            db.commit()
            log.warning("МЕСТО: сняты собранные клипы старше %s дней (%s МБ, %s шт.) — "
                        "пересборка бесплатна",
                        CLIP_KEEP_DAYS, freed // 1024 // 1024, gone)
    except Exception as e:  # noqa: BLE001
        db.rollback()
        log.warning("уборка клипов не прошла: %s", str(e)[:200])
    finally:
        db.close()
    return freed


def _reclaim_versions() -> int:
    """Прогнать тарифную ретенцию по всем сценам, у которых история переросла
    лимит. Обычно это делает сам снимок, но у человека, который перестал
    генерить, срок истекает без единой генерации."""
    db = SessionLocal()
    freed = 0
    try:
        ids = [r[0] for r in db.query(SceneVersion.scene_id)
               .group_by(SceneVersion.scene_id).limit(2000).all()]
        for sid in ids:
            scene = db.get(Scene, sid)
            owner = None
            if scene and scene.track and scene.track.project:
                oid = scene.track.project.owner_id
                owner = db.get(User, oid) if oid else None
            before = sum(int(v.bytes or 0) for v in
                         db.query(SceneVersion).filter(SceneVersion.scene_id == sid).all())
            _trim_scene_versions(db, sid, owner)
            # FLUSH ОБЯЗАТЕЛЕН, иначе функция ВСЕГДА возвращает ноль. Сессия
            # живёт с autoflush=False (см. db.SessionLocal), а _trim_scene_versions
            # только помечает строки db.delete() — без flush следующий SELECT
            # видит их живыми, before == after, и «освобождено 0».
            # Врущий ноль опасен не сам по себе: на нём _reclaim не срабатывает
            # ранний выход `freed >= need_bytes` и идёт сносить СОБРАННЫЕ КЛИПЫ,
            # хотя место уже нашлось на вариантах.
            db.flush()
            after = sum(int(v.bytes or 0) for v in
                        db.query(SceneVersion).filter(SceneVersion.scene_id == sid).all())
            freed += max(0, before - after)
        db.commit()
    except Exception as e:  # noqa: BLE001
        db.rollback()
        log.warning("уборка вариантов не прошла: %s", str(e)[:200])
    finally:
        db.close()
    return freed


#: Взведён, когда места мало: генерации отвечают 507 вместо того, чтобы
#: дописать 700-мегабайтный клип в два свободных гигабайта и уронить соседей.
_DISK_LOW = {"on": False, "free_pct": 100.0}


def _guard_disk() -> None:
    """Дверь перед каждой генерацией. Отказ ЧЕСТНЕЕ молчаливой порчи: клип,
    оборванный на полпути кончившимся местом, выглядит как «сервис сломался»
    и стоит человеку токенов."""
    if not _DISK_LOW["on"]:
        return
    raise ApiError(507, "disk_low",
                   "на сервере кончается место — идёт уборка, попробуй через "
                   "несколько минут",
                   free_pct=_DISK_LOW["free_pct"])


def _disk_guard_pass() -> dict:
    st = _disk_stat()
    _DISK_LOW["free_pct"] = st["free_pct"]
    # Временные копии убираем ВСЕГДА, а не только под нехваткой места. С
    # переходом кадров на WebP через outbox проходит каждый кадр, который
    # едет во внешний движок (kie/seevio webp могут не принять — наружу
    # всегда уезжает JPEG-копия). Копия живёт минуты, но копится постоянно,
    # и ждать 20 % свободного диска, чтобы её снять, незачем.
    _sweep_dir(mediagen.OUTBOX_DIR, 3600)
    _sweep_dir(EXPORT_DIR, 3600)
    if st["free_pct"] < DISK_MIN_FREE_PCT:
        _DISK_LOW["on"] = True
        log.error("МЕСТО НА ДИСКЕ: свободно %s%% — генерации закрыты, убираю",
                  st["free_pct"])
        _reclaim(need_bytes=int(st["total"] * 0.15), aggressive=True)
        st = _disk_stat()
        _DISK_LOW["free_pct"] = st["free_pct"]
        _DISK_LOW["on"] = st["free_pct"] < DISK_MIN_FREE_PCT
    elif st["free_pct"] < DISK_WARN_FREE_PCT:
        _DISK_LOW["on"] = False
        log.warning("место на диске: свободно %s%% — прибираюсь заранее",
                    st["free_pct"])
        _reclaim()
    else:
        _DISK_LOW["on"] = False
    return st


def _disk_guard() -> None:
    """Демон-сторож. Тот же паттерн, что у подписок и архива: своего
    планировщика в проекте нет."""
    while True:
        try:
            time.sleep(DISK_GUARD_S)
            _disk_guard_pass()
        except Exception as e:  # noqa: BLE001
            log.warning("сторож диска: проход упал: %s", str(e)[:200])


DISK_GUARD_S = int(os.environ.get("DISK_GUARD_S", "600"))


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
    """Суточный проход архива: дочитать размеры новых файлов, сверить индекс
    с диском и физически стереть то, что удалено больше суток назад. Тот же
    паттерн демон-треда, что у подписок и ретенции чата — своего
    планировщика в проекте нет."""
    while True:
        try:
            time.sleep(6 * 3600)
            _files_stat_pass(limit=5000)
            _files_verify_pass(limit=20000)
            _files_purge_pass()
            _frame_cache_sweep()
        except Exception as e:  # noqa: BLE001
            log.warning("архив файлов: суточный проход упал: %s", str(e)[:200])


# ═══════════ разовая конверсия старых кадров: PNG 12 МБ → WebP 1 МБ ═══════════
#
# Новые кадры сохраняются в WebP сразу (см. _save_image), но на диске уже
# лежат старые: на проде это 183 файла на 2.29 ГБ при среднем 12.5 МБ. Ровно
# те же картинки в WebP q92 весят около 0.2 ГБ.
#
# Переименование файла — рискованная операция: ссылку на кадр держат восемь
# разных колонок. Поэтому переименование идёт ОДНОЙ транзакцией по ВСЕМ
# местам сразу, и только после её успеха стирается старый файл. Пропущенная
# ссылка означала бы пустое место вместо оплаченного кадра, а этого делать
# нельзя ни при каких обстоятельствах.
#: (таблица, колонка) — все места, где может лежать имя файла-картинки
_FNAME_COLUMNS = (
    ("scenes", "image_filename"), ("scenes", "image_last_filename"),
    ("scene_versions", "image_filename"), ("scene_versions", "image_last_filename"),
    ("tracks", "storyboard_filename"), ("tracks", "cover_filename"),
    ("projects", "cover_filename"),
    ("character_photos", "filename"), ("attribute_photos", "filename"),
    ("track_photos", "filename"), ("scene_refs", "filename"),
    ("frame_cache", "filename"),
)
FRAMES_CONVERT = os.environ.get("FRAMES_CONVERT", "1") not in ("0", "", "no")
#: Файлы, которые ffmpeg отказался пережать. В памяти, а не в базе: после
#: рестарта попробовать ещё раз дешевле, чем держать колонку под неудачи.
_CONVERT_SKIP: set = set()


def _rename_media_everywhere(conn, old: str, new: str) -> None:
    """Переписать имя файла во всех колонках, где оно может лежать."""
    from sqlalchemy import text as _sqltext
    for table, col in _FNAME_COLUMNS:
        try:
            conn.execute(_sqltext(f"UPDATE {table} SET {col} = :new WHERE {col} = :old"),
                         {"new": new, "old": old})
        except Exception:  # noqa: BLE001 — таблицы style_assets может не быть
            continue
    # Промежуточные кадры лежат JSON-строкой: правим подстроку.
    conn.execute(_sqltext(
        "UPDATE scenes SET midframes_json = REPLACE(midframes_json, :old, :new) "
        "WHERE midframes_json LIKE :like"),
        {"old": old, "new": new, "like": f"%{old}%"})
    conn.execute(_sqltext(
        "UPDATE file_owners SET filename = :new, size_bytes = 0, phys_key = '' "
        "WHERE filename = :old"), {"new": new, "old": old})


def _frames_convert_pass(limit: int = 40) -> int:
    """Батч конверсии. Возвращает, сколько файлов переведено."""
    from sqlalchemy import text as _sqltext
    from db import engine as _engine
    if not FRAMES_CONVERT or mediagen.FRAME_FORMAT != "webp":
        return 0
    done = 0
    try:
        with _engine.begin() as conn:
            rows = conn.execute(_sqltext(
                "SELECT filename FROM file_owners "
                "WHERE deleted_at IS NULL AND filename LIKE 'scene\\_%' ESCAPE '\\' "
                "AND (filename LIKE '%.png' OR filename LIKE '%.jpg') LIMIT :n"),
                {"n": int(limit) + len(_CONVERT_SKIP)}).fetchall()
        for (old,) in rows:
            if old in _CONVERT_SKIP or done >= limit:
                continue
            src = os.path.join(UPLOAD_DIR, old)
            if not os.path.exists(src):
                continue
            new = os.path.splitext(old)[0] + ".webp"
            dst = os.path.join(UPLOAD_DIR, new)
            try:
                with mediagen.ffmpeg_slot():
                    r = subprocess.run(
                        ["ffmpeg", "-y", "-i", src, "-c:v", "libwebp", "-quality",
                         str(mediagen.FRAME_WEBP_Q), "-compression_level", "5", dst],
                        capture_output=True, timeout=300)
            except (OSError, subprocess.SubprocessError) as e:
                log.warning("конверсия кадров остановлена: %s", str(e)[:150])
                return done
            if r.returncode != 0 or not os.path.exists(dst) or os.path.getsize(dst) < 1000:
                if os.path.exists(dst):
                    try:
                        os.remove(dst)
                    except OSError:
                        pass
                _CONVERT_SKIP.add(old)   # не долбиться в тот же файл вечно
                continue
            with _engine.begin() as conn:
                _rename_media_everywhere(conn, old, new)
            _remove_media(old)      # старое имя больше никому не нужно
            done += 1
    except Exception as e:  # noqa: BLE001
        log.warning("конверсия кадров не прошла: %s", str(e)[:200])
    return done


def _frames_convert_worker() -> None:
    """Тянет конверсию фоном, малыми батчами и с паузами: это уборка, а не
    работа человека, и ffmpeg она занимать надолго не должна."""
    time.sleep(90)      # дать сервису подняться и обслужить первые запросы
    while True:
        try:
            done = _frames_convert_pass()
            if done:
                log.info("кадры: переведено в webp %s файлов", done)
                time.sleep(30)
            else:
                time.sleep(6 * 3600)
        except Exception as e:  # noqa: BLE001
            log.warning("конверсия кадров: проход упал: %s", str(e)[:200])
            time.sleep(3600)


def _frame_cache_sweep() -> None:
    """Просроченные записи кэша кадров. Файлы при этом НЕ трогаем: на них
    смотрят живые сцены и варианты, а кэш — только указатель."""
    db = SessionLocal()
    try:
        gone = (db.query(FrameCache)
                .filter(FrameCache.created_at < now() - timedelta(days=FRAME_CACHE_DAYS))
                .delete(synchronize_session=False))
        if gone:
            db.commit()
            log.info("кэш кадров: снято %s просроченных записей", gone)
    except Exception as e:  # noqa: BLE001
        db.rollback()
        log.warning("кэш кадров: уборка не прошла: %s", str(e)[:150])
    finally:
        db.close()


@app.get("/api/health")
def health():
    """Здоровье сервиса — вместе с МЕСТОМ И ПАМЯТЬЮ.

    Раньше здесь было {ok, ts}: по такому health нельзя было узнать, что
    диск заканчивается, пока он не заканчивался. А на этой машине место
    общее с чужими проектами, и первым от нехватки падает не lolq."""
    disk = _disk_stat()
    db = SessionLocal()
    try:
        uploads = int(db.query(func.coalesce(func.sum(FileOwner.size_bytes), 0))
                      .filter(FileOwner.deleted_at.is_(None)).scalar() or 0)
        versions = int(db.query(func.coalesce(func.sum(SceneVersion.bytes), 0))
                       .scalar() or 0)
        phantom = int(db.query(func.count(FileOwner.filename))
                      .filter(FileOwner.deleted_at.isnot(None)).scalar() or 0)
    except Exception:  # noqa: BLE001
        uploads = versions = phantom = 0
    finally:
        db.close()
    return {
        "ok": True, "ts": int(time.time()),
        "disk": disk,
        "disk_low": bool(_DISK_LOW["on"]),
        "uploads_bytes": uploads,
        "versions_bytes": versions,
        "phantom_rows": phantom,
        "mem_available": _mem_available(),
        "frame_format": mediagen.FRAME_FORMAT,
        "clip_keep_days": CLIP_KEEP_DAYS,
    }


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
    # Место на диске. Мастерская показывает его в подвале левой панели рядом
    # со сроком хранения: упереться в квоту посреди генерации без единого
    # предупреждения — тот же 402, только про диск.
    storage_used=_storage_used_bytes,
    storage_quota=_storage_quota_bytes,
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
# СТОРОЖ ДИСКА. Раньше о нехватке места сервис узнавал ровно в тот момент,
# когда ffmpeg падал на середине сборки. На машине с 48 ГБ и чужими
# проектами на том же разделе это означало «мы уронили соседа».
_Thread(target=_disk_guard, daemon=True).start()
# Разовая конверсия старых кадров в WebP: тянется фоном, батчами, с
# приоритетом ниже генераций (см. _frames_convert_worker).
_Thread(target=_frames_convert_worker, daemon=True).start()
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


# ─────────────────────────────── раздел «Музыка» ───────────────────────────────
# Загрузка трека, генерация, мастеринг и подготовка релиза. Своей авторизации и
# своей кассы модуль не заводит: сессия, очки и регистрация файлов — отсюда.
# Тем же правилом, что и телеграм-контур, подключается ДО mount("/").
import music_api  # noqa: E402

music_api.mount(app)


# ─────────────────────────── раздел «Школа»: курсы ───────────────────────────
# Витрина курсов, доступ (тариф или токены), авторы, кейсы, отзывы. Как и
# музыка, подключается ДО mount("/") и не заводит своей авторизации и кассы.
import school  # noqa: E402

school.mount(app)


# ─────────────────────────── ВХОД В АДМИНКУ ───────────────────────────
# Отдельная страница /admin, а не вкладки внутри модалки кабинета. Владелец
# просил дословно: «дай ссылку на админку где у меня срм система и другие
# настройки приложения». CRM поверх студии в модальном окне — это ровно то,
# что он просит развести.
#
# ФАЙЛЫ АДМИНКИ ЛЕЖАТ ВНЕ FRONTEND_DIR. Это не вкусовщина: статика
# смонтирована на «/», и любой файл внутри неё читается кем угодно без
# всякой проверки. Положи мы admin.js рядом с app.js — проверка is_admin
# ниже стала бы декорацией, потому что файл всё равно отдался бы напрямую.
#
# Не админу отвечаем 404, а не 403: существование админки посторонним не
# подтверждаем. Не залогинен — тоже 404, а не редирект на вход: владелец
# входит обычным входом на сайте и потом открывает /admin.
#
# РЕГИСТРАЦИЯ СТРОГО ДО app.mount("/") — иначе статика перехватит и /admin,
# и его js, и вернёт 404 сама, молча и совершенно непонятно.
ADMIN_DIR = os.environ.get("ADMIN_DIR", "/app/admin")
_ADMIN_TYPES = {".html": "text/html; charset=utf-8",
                ".js": "application/javascript; charset=utf-8",
                ".css": "text/css; charset=utf-8",
                ".svg": "image/svg+xml", ".json": "application/json"}


def _admin_asset(rest: str) -> str:
    """Путь к файлу админки. Всё, что не найдено, отдаёт index.html —
    маршрутизация внутри страницы своя (?tab=…), сервер о ней не знает."""
    name = os.path.basename((rest or "").strip("/")) or "index.html"
    path = os.path.join(ADMIN_DIR, name)
    if os.path.isfile(path):
        return path
    return os.path.join(ADMIN_DIR, "index.html")


@app.get("/admin", include_in_schema=False)
@app.get("/admin/{rest:path}", include_in_schema=False)
def admin_page(request: Request, rest: str = "",
               db: Session = Depends(db_session)):
    user = _resolve_user(request, db)
    if not user or not user.is_admin:
        raise HTTPException(404, "not found")
    path = _admin_asset(rest)
    if not os.path.isfile(path):
        raise HTTPException(404, "админка не собрана")
    ext = os.path.splitext(path)[1].lower()
    return FileResponse(path, media_type=_ADMIN_TYPES.get(ext),
                        headers={"Cache-Control": "no-store"})


# Правки стилей из базы — до первого запроса, иначе первая же витрина
# отдаст заводской каталог и человек решит, что админка не сохраняет.
reload_style_overlay()
reload_prompts_overlay()
reload_mockup_overlay()

# ЧЕЛОВЕЧЕСКИЕ АДРЕСА РАЗДЕЛОВ. Приложение — SPA, и до сих пор разделы жили
# в якорях (#/make, ?home#ld-learn): такие ссылки стыдно слать и невозможно
# рекламировать. Каждый путь отдаёт тот же index.html, а какой раздел
# открыть — решает фронт по location.pathname.
SPA_ROUTES = ("home", "studio", "make", "generator", "trends", "academy",
              "prompts", "pricing", "music", "login", "marketing", "earn",
              "school")


@app.get("/team", include_in_schema=False)
def team_page():
    return FileResponse(os.path.join(FRONTEND_DIR, "team.html"),
                        headers={"Cache-Control": "no-cache"})


def _spa_index():
    return FileResponse(os.path.join(FRONTEND_DIR, "index.html"),
                        headers={"Cache-Control": "no-cache, must-revalidate"})


# Роуты регистрируются ЯВНО по одному: catch-all /{section} перехватывал бы
# и /app.js, и всю статику корня — а mount("/") стоит после роутов.
for _route in SPA_ROUTES:
    app.add_api_route(f"/{_route}", _spa_index, methods=["GET"],
                      include_in_schema=False)

# Страница курса: /school/course/{id}. Отдельным роутом, а не catch-all'ом —
# по той же причине, по какой разделы регистрируются по одному.
def _spa_course(course_id: int):  # noqa: ARG001 — путь читает фронт
    return _spa_index()


app.add_api_route("/school/course/{course_id}", _spa_course, methods=["GET"],
                  include_in_schema=False)


FRONTEND_DIR = os.environ.get("FRONTEND_DIR", "/app/static")


class _NoCacheIndex(StaticFiles):
    """index.html отдаётся с запретом кэширования.

    Safari охотно держит index неделями: человек открывал сайт после деплоя
    и получал СТАРУЮ оболочку — пустую страницу с фоном, потому что она
    запрашивала статику, которой уже нет. Версионируется у нас только
    js/css (?v=N), а сам index — нет, поэтому кэшировать его нельзя вовсе.
    """

    def file_response(self, *args, **kwargs):
        resp = super().file_response(*args, **kwargs)
        path = getattr(resp, "path", "") or ""
        if path.endswith("index.html"):
            resp.headers["Cache-Control"] = "no-cache, must-revalidate"
        return resp


if os.path.isdir(FRONTEND_DIR):
    app.mount("/", _NoCacheIndex(directory=FRONTEND_DIR, html=True), name="static")

_backfill_video_sig()
