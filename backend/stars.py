"""Оплата Telegram Stars для lolq.ai — один модуль на бота и на мини-апп.

ПОЧЕМУ ОТДЕЛЬНЫЙ ФАЙЛ. Внутри Telegram цифровой товар продаётся ТОЛЬКО за
звёзды: внешняя платёжная ссылка в мини-аппе или в боте — нарушение правил
платформы (предупреждение → скрытие из мобильных клиентов → удаление). При
этом на сайте ЮKassa и Stripe остаются как были. Значит, у звёзд свой контур,
но НЕ своя касса: выдача идёт через тот же `_grant_payment` и ту же таблицу
`ProcessedPayment`, где `payment_id = "stars:<telegram_payment_charge_id>"` и
UNIQUE-констрейнт даёт идемпотентность даром.

ЦЕНА ЖИВЁТ ЗДЕСЬ И ТОЛЬКО ЗДЕСЬ. Раньше `stars_price`/`price_override` лежали
в `bot.py`, и мини-апп неизбежно завёл бы вторую копию формулы — а разъехаться
двум прайсам в одном магазине нельзя. Теперь бот импортирует их отсюда.

ДОЛЯ МАГАЗИНА. Человек платит ≈$0.02 за звезду, разработчику приходит $0.013 —
то есть 65 %, а не 70 %: 30 % забирает Apple/Google, ~5 % Telegram. Поэтому
`BOT_STARS_STORE_CUT` по умолчанию 35, а не 30 — на тридцати формула недобирала
7 % с каждой продажи (PRO приходил $18.59 вместо $20).

ЧЕГО ЗВЁЗДЫ НЕ УМЕЮТ (и поэтому этого нет в витрине мини-аппа):
  • период подписки ровно один — 2592000 секунд, годовой оплаты не бывает;
  • потолок подписки 10000 XTR за период — ULTRA туда не влезает физически;
  • пропорционального возврата при отмене нет — доступ доживает до конца срока.
"""
from __future__ import annotations

import logging
import math
import os
import re
import threading
import time

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

log = logging.getLogger("rapclips")
# httpx печатает URL Bot API на INFO — вместе с токеном. Глушим и маскируем.
logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("httpcore").setLevel(logging.WARNING)


class _TokenMask(logging.Filter):
    _RE = re.compile(r"/bot[0-9]+:[A-Za-z0-9_-]+/")

    def filter(self, record: logging.LogRecord) -> bool:
        try:
            if record.args:
                record.msg = str(record.msg) % record.args
                record.args = ()
            record.msg = self._RE.sub("/bot***/", str(record.msg))
        except Exception:  # noqa: BLE001
            pass
        return True


for _h in logging.getLogger().handlers:
    if not any(isinstance(f, _TokenMask) for f in _h.filters):
        _h.addFilter(_TokenMask())

router = APIRouter()

# ─────────────────────────────── настройки ───────────────────────────────

# Мини-апп подписан токеном ТОГО бота, чей мини-апп открыт, — значит BOT_TOKEN.
# TG_LOGIN_BOT_TOKEN остаётся запасным на случай, когда на сайте живёт только
# Login Widget и отдельного бота нет. Если это РАЗНЫЕ боты, подпись initData
# не сойдётся никогда (см. docs/telegram-miniapp.md).
BOT_TOKEN = (os.environ.get("BOT_TOKEN")
             or os.environ.get("TG_LOGIN_BOT_TOKEN") or "").strip()
BOT_USERNAME = (os.environ.get("BOT_USERNAME")
                or os.environ.get("TG_LOGIN_BOT_USERNAME") or "").lstrip("@").strip()
# Имя мини-аппа из BotFather (/newapp). Прямая ссылка — t.me/<bot>/<app>.
MINIAPP_NAME = os.environ.get("TG_MINIAPP_NAME", "app").strip().lstrip("/")

TELEGRAM_API_BASE = os.environ.get("TELEGRAM_API_BASE", "https://api.telegram.org").rstrip("/")
# Тот же формат, что у бота: socks5://host:port или http://user:pass@host:port,
# несколько — через запятую. Пусто = ходим напрямую.
BOT_PROXY_URL = os.environ.get("BOT_PROXY_URL", "").strip()

STARS_PER_USD = float(os.environ.get("BOT_STARS_PER_USD", "50"))
STARS_STORE_CUT = float(os.environ.get("BOT_STARS_STORE_CUT", "35")) / 100
STARS_SUB_CAP = int(os.environ.get("BOT_STARS_SUB_CAP", "10000"))
STARS_SUB_PERIOD = 2592000  # 30 суток — единственный период, который есть у Telegram

# Разовый счёт Telegram тоже не бесконечный. Официального числа в документации
# нет, поэтому держим свой потолок: лучше не показать самый крупный пакет, чем
# показать кнопку, которая молча не создаёт счёт.
STARS_INVOICE_CAP = int(os.environ.get("BOT_STARS_INVOICE_CAP", "100000"))

# Доля амбассадора именно со звёздных оплат. По умолчанию — общая ставка.
# Отдельный ключ нужен на случай возврата к паритетным ценам: 65 % прихода
# минус 30 % амбассадору дают продажу в минус (см. docs/telegram-miniapp.md).
_REF_STARS_ENV = os.environ.get("REF_REWARD_PCT_STARS", "").strip()
REF_REWARD_PCT_STARS = (max(0, min(100, int(_REF_STARS_ENV)))
                        if _REF_STARS_ENV.isdigit() else None)

# Сверка платежей: раз в час спрашиваем у Telegram список звёздных транзакций
# и догоняем то, чего нет в ProcessedPayment. Нужна потому, что мини-апп
# вебхуков не получает: оплата приезжает апдейтом БОТУ, а если бот лежал
# больше суток — апдейт у Telegram протух, деньги взяты, тариф не выдан.
RECONCILE_EVERY_S = int(os.environ.get("STARS_RECONCILE_EVERY_S", "3600"))
RECONCILE_ENABLED = os.environ.get("STARS_RECONCILE", "1") not in ("0", "false", "no")


def enabled() -> bool:
    """Звёздный контур жив только с токеном бота. Без него роуты честно
    отвечают 503, а не притворяются работающими."""
    return bool(BOT_TOKEN)


def _core():
    """main импортируется ЛЕНИВО: этот модуль подключается ИЗ main.py, и на
    момент импорта main ещё не доисполнен (см. bot_api._core)."""
    import main  # noqa: PLC0415
    return main


def _deny(status: int, code: str, message: str = "") -> JSONResponse:
    return JSONResponse(status_code=status, content={"error": code, "detail": message or code})


def _key_ok(request: Request) -> bool:
    import bot_api  # noqa: PLC0415
    return bot_api._key_ok(request)


# ─────────────────────────── прайс в звёздах ───────────────────────────

def stars_price(usd_cents: int) -> int:
    """Номинал в звёздах. Считаем от «чистыми»: пересчёт usd×50 в лоб оставил
    бы нам 65 % цены, то есть продажу через Telegram себе в убыток."""
    usd = max(0, int(usd_cents or 0)) / 100
    raw = usd * STARS_PER_USD / max(0.05, 1 - STARS_STORE_CUT)
    return max(1, int(math.ceil(raw / 10) * 10))


def price_override(kind: str, ident: str, default: int) -> int:
    """Ручная цена под акцию: BOT_STARS_PRICE_PLAN_PRO=1200. Только целое
    число звёзд, иначе берём расчётное."""
    env = os.environ.get(f"BOT_STARS_PRICE_{kind}_{ident}".upper(), "").strip()
    if env.isdigit():
        return int(env)
    return default


def plan_xtr(plan_id: str) -> int:
    core = _core()
    plan = core.PLANS.get(plan_id) or {}
    return price_override("PLAN", plan_id, stars_price(int(plan.get("usd_cents") or 0)))


def pack_xtr(pack_id: str) -> int:
    core = _core()
    pack = core.TOPUP_PACKS.get(pack_id) or {}
    return price_override("PACK", pack_id, stars_price(int(pack.get("usd_cents") or 0)))


def _discount_pct(db, user) -> int:
    """Скидка приглашённого на ПЕРВУЮ оплату. В боте её не было — счёт
    создавался фиксированной ценой; здесь счёт делает сервер под конкретного
    человека, значит скидку можно посчитать честно."""
    core = _core()
    if not user or not user.referred_by:
        return 0
    if not core._ref_first_payment(db, user):
        return 0
    return int(core.REF_DISCOUNT_PCT or 0)


def _apply_discount(xtr: int, pct: int) -> int:
    if pct <= 0:
        return xtr
    return max(1, int(math.ceil(xtr * (100 - pct) / 100 / 10) * 10))


# ─────────────────────────── транспорт Bot API ───────────────────────────

_routes_lock = threading.Lock()
_route_ok: str | None = None  # запомненный рабочий маршрут наружу


def _proxies() -> list[str | None]:
    out: list[str | None] = [p.strip() for p in BOT_PROXY_URL.split(",") if p.strip()]
    out.append(None)  # напрямую — всегда последним запасным
    return out


async def call(method: str, **params):
    """Вызов Bot API. Маршруты пробуются по очереди (прокси, потом напрямую),
    рабочий запоминается: из РФ api.telegram.org напрямую может не открыться,
    а перебирать маршруты на каждом запросе — лишние секунды в оплате."""
    global _route_ok
    if not BOT_TOKEN:
        raise RuntimeError("BOT_TOKEN не задан — звёздная оплата выключена")
    import httpx  # noqa: PLC0415
    url = f"{TELEGRAM_API_BASE}/bot{BOT_TOKEN}/{method}"
    body = {k: v for k, v in params.items() if v is not None}
    order = _proxies()
    if _route_ok is not None and _route_ok in order:
        order = [_route_ok] + [p for p in order if p != _route_ok]
    last = None
    for proxy in order:
        try:
            async with httpx.AsyncClient(timeout=40, proxy=proxy) as client:
                r = await client.post(url, json=body)
        except Exception as e:  # noqa: BLE001
            last = e
            continue
        data = r.json() if r.headers.get("content-type", "").startswith("application/json") else {}
        if not data.get("ok"):
            # Ответ ПОЛУЧЕН — маршрут рабочий, а отказ содержательный:
            # перебирать дальше нечего, ошибку возвращаем как есть.
            with _routes_lock:
                _route_ok = proxy
            raise RuntimeError(str(data.get("description") or r.text)[:300])
        with _routes_lock:
            _route_ok = proxy
        return data.get("result")
    raise RuntimeError(f"Telegram недоступен: {str(last)[:200]}")


# ─────────────────────────────── витрина ───────────────────────────────

def _plan_row(core, plan_id: str, discount: int) -> dict:
    plan = core.PLANS[plan_id]
    base = plan_xtr(plan_id)
    xtr = _apply_discount(base, discount)
    over = xtr > STARS_SUB_CAP
    return {
        "id": plan_id,
        "title": plan["title"],
        "points": int(plan["points"]),
        # Долларов и рублей здесь НЕТ намеренно: показывать внутри Telegram
        # сравнение с ценой сайта нельзя, это и есть запрещённый steering.
        "xtr": xtr,
        "xtr_base": base,
        "discount_pct": discount if xtr != base else 0,
        "period_days": STARS_SUB_PERIOD // 86400,
        "available": not over,
        "reason": "over_cap" if over else "",
        "note": plan.get("note", ""),
        "features": list(plan.get("features") or []),
        "badge": plan.get("badge", ""),
    }


def _pack_row(core, pack_id: str) -> dict:
    pack = core.TOPUP_PACKS[pack_id]
    xtr = pack_xtr(pack_id)
    over = xtr > STARS_INVOICE_CAP
    return {
        "id": pack_id,
        "points": int(pack["points"]),
        "xtr": xtr,
        "available": not over,
        "reason": "over_cap" if over else "",
        "badge": pack.get("badge", ""),
    }


@router.get("/api/tg/pricing")
def tg_pricing(request: Request):
    """Витрина мини-аппа: те же тарифы и пакеты, что на сайте, но в звёздах.

    Источник правды — PLANS/TOPUP_PACKS из main.py: второго прайса у сервиса
    нет и быть не должно."""
    core = _core()
    db = core.SessionLocal()
    try:
        user = core._resolve_user(request, db)
        discount = _discount_pct(db, user) if user else 0
        plans = [_plan_row(core, pid, discount) for pid in core.PLANS
                 if int(core.PLANS[pid]["usd_cents"]) > 0]
        packs = [_pack_row(core, kid) for kid in core.TOPUP_PACKS]
        plan_now = core._plan_of(user) if user else "free"
        return {
            "enabled": enabled(),
            "currency": "XTR",
            "plans": plans,
            "packs": packs,
            "current": plan_now,
            "points": int(user.gen_points or 0) if user else 0,
            "authorized": bool(user),
            # Докупка токенов — только при живой платной подписке (правило сайта,
            # не Telegram): пакетное токен дороже подписочного.
            "topup_requires_plan": core.TOPUP_REQUIRES_PLAN,
            "topup_allowed": (bool(user and plan_now != "free")
                              if core.TOPUP_REQUIRES_PLAN else True),
            # Уже подписан звёздами: показываем «Управлять», а не «Купить» —
            # Telegram разрешает НЕСКОЛЬКО подписок одного человека на бота,
            # и второй счёт человек оплатил бы вслепую.
            "subscription": subscription_state(user) if user else {},
            "sub_cap": STARS_SUB_CAP,
        }
    finally:
        db.close()


def subscription_state(user) -> dict:
    """Что показать в кабинете про звёздную подписку."""
    charge = str(getattr(user, "stars_sub_charge_id", "") or "")
    if not charge:
        return {}
    state = str(getattr(user, "stars_sub_state", "") or "active")
    return {
        "provider": "stars",
        "active": state == "active",
        "state": state,
        "plan": user.plan or "free",
        "xtr": plan_xtr(user.plan or "free"),
        "until": user.plan_until.isoformat() if user.plan_until else "",
        "period_days": STARS_SUB_PERIOD // 86400,
        # Отменить можно и мимо нас — человек должен знать, где именно.
        "manage_hint": "telegram_settings",
    }


# ─────────────────────────────── счета ───────────────────────────────

def _payload(kind: str, ident: str, period: str, cents: int) -> str:
    """payload инвойса. Формат совместим с тем, что бот уже разбирает:
    `plan:<id>:<period>:<src>[:<cents>]` и `pack:<id>[:<src>[:<cents>]]`.
    src="ma" (mini app) нужен только для статистики, разбор от него не зависит.
    Центы едут в payload, потому что при скидке приглашённого фактическая
    сумма отличается от прайса, а доля амбассадора считается от неё."""
    if kind == "plan":
        return f"plan:{ident}:{period}:ma:{int(cents)}"
    return f"pack:{ident}:ma:{int(cents)}"


@router.post("/api/tg/invoice")
async def tg_invoice(request: Request):
    """Ссылка на счёт в звёздах для текущего человека мини-аппа.

    Счёт создаёт СЕРВЕР: цена, скидка приглашённого и потолок подписки — наши
    решения, и подсовывать их из браузера нельзя."""
    if not enabled():
        return _deny(503, "stars_off", "оплата звёздами не настроена")
    core = _core()
    body = await request.json()
    kind = str(body.get("kind") or "plan")
    db = core.SessionLocal()
    try:
        user = core._resolve_user(request, db)
        if not user:
            return _deny(401, "unauthorized", "нужна сессия")
        discount = _discount_pct(db, user)
        if kind == "topup":
            pack_id = str(body.get("pack") or "")
            if pack_id not in core.TOPUP_PACKS:
                return _deny(400, "unknown_pack", "неизвестный пакет")
            if core.TOPUP_REQUIRES_PLAN and core._plan_of(user) == "free":
                return _deny(409, "needs_plan", "пакеты продаются при активной подписке")
            pack = core.TOPUP_PACKS[pack_id]
            xtr = pack_xtr(pack_id)
            if xtr > STARS_INVOICE_CAP:
                return _deny(409, "over_cap", "пакет не влезает в один счёт Telegram")
            cents = int(pack["usd_cents"])
            title = f"{int(pack['points'])} points"
            desc = f"{int(pack['points'])} generation points for {core.BRAND}"
            period = None
        else:
            plan_id = str(body.get("plan") or "")
            plan = core.PLANS.get(plan_id)
            if not plan or int(plan["usd_cents"]) <= 0:
                return _deny(400, "unknown_plan", "неизвестный тариф")
            base = plan_xtr(plan_id)
            xtr = _apply_discount(base, discount)
            if xtr > STARS_SUB_CAP:
                # Кнопки на это в витрине быть не должно — но если она всё же
                # нажалась, честный отказ лучше пустого экрана Telegram.
                return _deny(409, "over_cap", "подписка дороже потолка Telegram")
            cents = int(plan["usd_cents"])
            if xtr != base and base > 0:
                cents = max(1, int(round(cents * xtr / base)))
            title = f"{plan['title']} — 30 days"
            desc = (f"{int(plan['points'])} points every month. "
                    f"{plan.get('note') or ''}").strip()[:255]
            period = STARS_SUB_PERIOD
        try:
            link = await call(
                "createInvoiceLink",
                title=title[:32], description=desc[:255],
                payload=_payload(kind, pack_id if kind == "topup" else plan_id,
                                 "month", cents),
                provider_token="", currency="XTR",
                # Ровно один элемент: у XTR список цен из нескольких строк
                # Telegram не принимает.
                prices=[{"label": title[:32], "amount": xtr}],
                subscription_period=period,
            )
        except Exception as e:  # noqa: BLE001
            log.warning("звёзды: не создал счёт (%s %s): %s", kind, body, str(e)[:200])
            return _deny(502, "invoice_failed", str(e)[:200])
        return {"ok": True, "url": link, "xtr": xtr,
                "kind": kind, "discount_pct": discount if kind == "plan" else 0}
    finally:
        db.close()


# ──────────────────────── подписка: события и отмена ────────────────────────

def remember_subscription(db, user, charge_id: str) -> None:
    """Запомнить charge_id ПЕРВОГО платежа подписки.

    Без него `editUserStarSubscription` вызвать нечем: Telegram требует именно
    первый charge, а не последний. Раньше он нигде не сохранялся — отменить
    подписку со своей стороны было физически невозможно."""
    if not charge_id or not hasattr(user, "stars_sub_charge_id"):
        return
    user.stars_sub_charge_id = charge_id
    if hasattr(user, "stars_sub_state"):
        user.stars_sub_state = "active"
    db.commit()


@router.post("/internal/stars-subscription")
async def stars_subscription(request: Request):
    """Апдейт `subscription` (BotSubscriptionUpdated) от бота.

    Отмену подписки Telegram присылает ТОЛЬКО этим апдейтом. Без него мы бы
    не узнали ни об одной отмене и продолжали считать человека платящим."""
    if not _key_ok(request):
        return _deny(503 if not enabled() else 403, "internal_key", "ключ не сошёлся")
    core = _core()
    body = await request.json()
    tg_id = str(body.get("tg_id") or "").strip()
    state = str(body.get("state") or "").strip().lower()
    if not tg_id or state not in ("active", "canceled", "cancelled", "failed", "expired"):
        return _deny(400, "bad_request", "нужны tg_id и понятный state")
    state = "canceled" if state == "cancelled" else state
    db = core.SessionLocal()
    try:
        user = db.query(core.User).filter(core.User.tg_id == tg_id).first()
        if not user:
            return _deny(404, "no_user", "нет аккаунта с таким telegram id")
        if hasattr(user, "stars_sub_state"):
            user.stars_sub_state = state
        if state in ("canceled", "failed", "expired"):
            # Доступ НЕ снимаем: оплаченный период человек дорабатывает.
            # Снимет его обычный проход по подпискам, когда выйдет plan_until.
            user.autopay = False
        else:
            user.autopay = True
        db.commit()
        log.info("звёзды: подписка юзера %s → %s", user.id, state)
        return {"ok": True, "state": state, "plan": core._plan_of(user),
                "until": user.plan_until.isoformat() if user.plan_until else ""}
    finally:
        db.close()


async def cancel_subscription(db, user) -> tuple[bool, str]:
    """Отменить звёздную подписку со своей стороны. Возврата за неиспользованные
    дни у Telegram нет — доступ доживает до конца оплаченного периода."""
    charge = str(getattr(user, "stars_sub_charge_id", "") or "")
    if not charge:
        return False, "no_subscription"
    if not (enabled() and user.tg_id):
        return False, "stars_off"
    try:
        await call("editUserStarSubscription", user_id=int(user.tg_id),
                   telegram_payment_charge_id=charge, is_canceled=True)
    except Exception as e:  # noqa: BLE001
        log.warning("звёзды: не отменил подписку юзера %s: %s", user.id, str(e)[:200])
        return False, str(e)[:200]
    if hasattr(user, "stars_sub_state"):
        user.stars_sub_state = "canceled"
    user.autopay = False
    db.commit()
    return True, ""


# ─────────────────────────────── возвраты ───────────────────────────────

@router.post("/internal/stars-refund")
async def stars_refund(request: Request):
    """Вернуть звёзды И ОТОБРАТЬ ВЫДАННОЕ.

    Дыра, которую это закрывает: `/refund` в боте звал `refundStarPayment`,
    звёзды уходили обратно, а токены и тариф оставались. Плюс Telegram при
    чарджбэке в Apple/Google списывает звёзды с нашего баланса задним числом —
    то есть возврат может случиться и без нашего участия."""
    if not _key_ok(request):
        return _deny(503 if not enabled() else 403, "internal_key", "ключ не сошёлся")
    core = _core()
    body = await request.json()
    charge_id = str(body.get("charge_id") or "").strip()
    tg_id = str(body.get("tg_id") or "").strip()
    do_call = bool(body.get("call_telegram", True))
    if not charge_id:
        return _deny(400, "bad_request", "нужен charge_id")

    db = core.SessionLocal()
    try:
        key = core._pay_key("stars", charge_id)
        row = (db.query(core.ProcessedPayment)
               .filter(core.ProcessedPayment.payment_id == key).first())
        if not row:
            return _deny(404, "not_found", "по этому платежу мы ничего не выдавали")
        user = db.get(core.User, row.user_id) if row.user_id else None
        if not user and tg_id:
            user = db.query(core.User).filter(core.User.tg_id == tg_id).first()
        if not user:
            return _deny(404, "no_user", "аккаунт платежа не найден")

        if do_call and enabled() and user.tg_id:
            try:
                await call("refundStarPayment", user_id=int(user.tg_id),
                           telegram_payment_charge_id=charge_id)
            except Exception as e:  # noqa: BLE001
                # Уже возвращённый платёж Telegram отклоняет — это не повод
                # оставлять человеку выданное, поэтому продолжаем откат.
                log.warning("звёзды: refundStarPayment %s: %s", charge_id, str(e)[:200])

        points = int(row.points or 0)
        # Вид платежа запоминаем ДО удаления строки: после db.delete + commit
        # объект отсоединён, и обращение к row.kind уже упадёт.
        row_kind = row.kind
        if points:
            # Через ЕДИНСТВЕННУЮ дверь к балансу (core._move_points): прямое
            # присваивание gen_points здесь означало бы строку остатка, которую
            # не объясняет ни одна запись журнала. Не уводим в минус: за
            # звёздный платёж могли уже потратить больше, чем выдали.
            take = min(points, int(user.gen_points or 0))
            core._move_points(db, user, -take,
                              f"возврат звёздного платежа {charge_id[:24]}",
                              commit=False,
                              kind="topup" if row_kind == "topup" else "plan",
                              ref_type="payment", engine="stars")
        if row.kind == "plan":
            from datetime import timedelta  # noqa: PLC0415
            until = core._as_utc(user.plan_until)
            if until:
                user.plan_until = until - timedelta(days=core._period_days(row.period or "month"))
                if core._as_utc(user.plan_until) <= core.now():
                    user.plan = "free"
                    user.plan_period = "month"
                    user.autopay = False
            if hasattr(user, "stars_sub_charge_id") and user.stars_sub_charge_id == charge_id:
                user.stars_sub_charge_id = ""
                if hasattr(user, "stars_sub_state"):
                    user.stars_sub_state = "canceled"

        # Партнёрка: начисление по этому платежу отменяем, иначе амбассадору
        # заплатят за возвращённые деньги.
        ev = (db.query(core.RefEvent)
              .filter(core.RefEvent.payment_id.in_([key, charge_id])).first())
        if ev:
            amb = db.get(core.User, ev.ambassador_id)
            if amb:
                amb.ref_balance_kopeks = max(0, int(amb.ref_balance_kopeks or 0)
                                             - int(ev.reward_kopeks or 0))
            db.delete(ev)
        db.delete(row)
        db.commit()
        log.info("звёзды: возврат %s — юзер %s, −%s токенов, тариф %s",
                 charge_id, user.id, points, core._plan_of(user))
        return {"ok": True, "user_id": user.id, "points": int(user.gen_points or 0),
                "plan": core._plan_of(user)}
    finally:
        db.close()


# ─────────────────────── сверка платежей с Telegram ───────────────────────

def _parse_payload(core, payload: str) -> dict | None:
    """Разбор payload инвойса в аргументы выдачи. Формат общий с ботом."""
    parts = [p for p in str(payload or "").split(":")]
    if len(parts) < 2 or not parts[1]:
        return None
    if parts[0] == "pack":
        pack_id = parts[1]
        if pack_id not in core.TOPUP_PACKS:
            return None
        cents = next((int(p) for p in parts[2:] if p.isdigit()), 0)
        return {"kind": "topup", "pack_id": pack_id,
                "amount_cents": cents or int(core.TOPUP_PACKS[pack_id]["usd_cents"])}
    if parts[0] == "plan":
        plan_id = parts[1]
        plan = core.PLANS.get(plan_id)
        if not plan or int(plan["usd_cents"]) <= 0:
            return None
        period = core._norm_period(parts[2] if len(parts) > 2 else "month")
        cents = next((int(p) for p in parts[3:] if p.isdigit()), 0)
        default = int(plan["usd_year_cents" if period == "year" else "usd_cents"])
        return {"kind": "plan", "plan_id": plan_id, "period": period,
                "amount_cents": cents or default}
    return None


async def reconcile_once(limit: int = 100) -> dict:
    """Догнать оплаты, которых у нас нет.

    Мини-апп вебхуков не получает: успешная оплата приезжает АПДЕЙТОМ БОТУ, и
    если бот лежал дольше суток — Telegram апдейт выбросил. Деньги взяты,
    тариф не выдан, и узнать об этом можно только спросив у Telegram список
    звёздных транзакций. Идемпотентность даёт та же ProcessedPayment, поэтому
    сверка безопасна на любой частоте."""
    if not enabled():
        return {"ok": False, "error": "stars_off"}
    core = _core()
    res = await call("getStarTransactions", offset=0, limit=max(1, min(100, limit)))
    txs = (res or {}).get("transactions") or []
    fixed, seen = 0, 0
    db = core.SessionLocal()
    try:
        for tx in txs:
            src = tx.get("source") or {}
            # Нас интересуют только ВХОДЯЩИЕ от людей: у исходящих (выплаты,
            # возвраты) source пуст, а receiver заполнен.
            if src.get("type") != "user":
                continue
            charge_id = str(tx.get("id") or "")
            tg_id = str((src.get("user") or {}).get("id") or "")
            args = _parse_payload(core, src.get("invoice_payload") or "")
            if not charge_id or not tg_id or not args:
                continue
            seen += 1
            key = core._pay_key("stars", charge_id)
            if db.query(core.ProcessedPayment).filter(
                    core.ProcessedPayment.payment_id == key).first():
                continue
            user = db.query(core.User).filter(core.User.tg_id == tg_id).first()
            if not user:
                log.warning("звёзды: платёж %s от tg %s — аккаунта нет, пропуск",
                            charge_id, tg_id)
                continue
            granted = core._grant_payment(
                db, user, provider="stars", payment_id=charge_id,
                currency="USD", **args)
            if granted:
                fixed += 1
                core._ref_reward(db, user,
                                 core._reward_kopeks(0, int(args["amount_cents"])),
                                 key, pct=REF_REWARD_PCT_STARS)
                if args["kind"] == "plan" and src.get("subscription_period"):
                    remember_subscription(db, user, charge_id)
                log.warning("звёзды: сверка ДОГНАЛА платёж %s (юзер %s) — "
                            "апдейт бота потерялся", charge_id, user.id)
        return {"ok": True, "checked": seen, "granted": fixed}
    finally:
        db.close()


def _reconcile_worker() -> None:
    import asyncio as _asyncio  # noqa: PLC0415
    # Первый проход — не сразу: на старте контейнера сеть наружу может ещё
    # подниматься, а спешить сверке некуда.
    time.sleep(90)
    while True:
        try:
            out = _asyncio.run(reconcile_once())
            if out.get("granted"):
                log.info("звёзды: сверка выдала %s потерянных платежей", out["granted"])
        except Exception as e:  # noqa: BLE001
            log.warning("звёзды: сверка не прошла: %s", str(e)[:200])
        time.sleep(max(300, RECONCILE_EVERY_S))


@router.post("/internal/stars-reconcile")
async def stars_reconcile(request: Request):
    """Ручной запуск сверки — для поддержки: «оплатил, ничего не пришло»."""
    if not _key_ok(request):
        return _deny(503 if not enabled() else 403, "internal_key", "ключ не сошёлся")
    try:
        return await reconcile_once()
    except Exception as e:  # noqa: BLE001
        return _deny(502, "reconcile_failed", str(e)[:200])


def mount(app) -> None:
    """Подключение из main.py. Статику на «/» переставляет bot_api.mount —
    здесь только роутер и фоновая сверка."""
    app.include_router(router)
    if enabled() and RECONCILE_ENABLED:
        threading.Thread(target=_reconcile_worker, daemon=True).start()
    log.info("звёзды: контур %s (курс %s XTR/$, доля магазина %s%%, потолок подписки %s)",
             "включён" if enabled() else "ВЫКЛЮЧЕН — нет BOT_TOKEN",
             STARS_PER_USD, int(STARS_STORE_CUT * 100), STARS_SUB_CAP)
