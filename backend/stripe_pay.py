"""Stripe для lolq.ai: подписки (планы) и разовые платежи (пакеты очков).

Почему без SDK: библиотека stripe тянет свои зависимости и свой HTTP-клиент,
а нам нужны ровно четыре вызова — Checkout Session, купон, отмена подписки и
проверка подписи вебхука. httpx в проекте уже есть, поэтому ходим в REST API
напрямую: меньше зависимостей, нечему ломаться при пересборке образа.

Почему цены создаются на лету (price_data), а не берутся из панели: иначе
владельцу пришлось бы руками заводить 6 объектов Price (3 плана × 2 периода) и
5 пакетов, а потом синхронизировать их с кодом. Прайс живёт в main.py и
уезжает в Stripe вместе с каждой сессией.

Ключи (infra/.env):
  STRIPE_SECRET_KEY      — sk_live_… / sk_test_…  (Dashboard → Developers → API keys)
  STRIPE_WEBHOOK_SECRET  — whsec_…                (Dashboard → Developers → Webhooks)
Нет ключей — модуль честно выключен: enabled() = False, роуты не падают.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import logging
import os
import time
import uuid

import httpx

log = logging.getLogger("rapclips")

STRIPE_SECRET_KEY = os.environ.get("STRIPE_SECRET_KEY", "")
STRIPE_WEBHOOK_SECRET = os.environ.get("STRIPE_WEBHOOK_SECRET", "")
STRIPE_API = os.environ.get("STRIPE_API", "https://api.stripe.com")
# Фиксируем версию API: Stripe меняет форму объектов, а мы разбираем их руками.
STRIPE_API_VERSION = os.environ.get("STRIPE_API_VERSION", "2024-06-20")
# Допуск на расхождение часов при проверке подписи вебхука (как в SDK).
SIG_TOLERANCE_SEC = int(os.environ.get("STRIPE_SIG_TOLERANCE", "300"))


def enabled() -> bool:
    """Приём долларов подключён. Секрет вебхука обязателен отдельно: без него
    мы не можем доверять уведомлениям, а значит и продавать нечего."""
    return bool(STRIPE_SECRET_KEY)


def webhook_ready() -> bool:
    return bool(STRIPE_SECRET_KEY and STRIPE_WEBHOOK_SECRET)


# ─────────────────────────── низкий уровень ───────────────────────────

def _flatten(prefix: str, value, out: list[tuple[str, str]]) -> None:
    """Stripe принимает form-urlencoded с bracket-нотацией:
    line_items[0][price_data][unit_amount]=2000. Разворачиваем вложенные
    словари и списки в такие ключи."""
    if value is None or value == "":
        return
    if isinstance(value, bool):
        out.append((prefix, "true" if value else "false"))
    elif isinstance(value, dict):
        for k, v in value.items():
            _flatten(f"{prefix}[{k}]" if prefix else str(k), v, out)
    elif isinstance(value, (list, tuple)):
        for i, v in enumerate(value):
            _flatten(f"{prefix}[{i}]", v, out)
    else:
        out.append((prefix, str(value)))


def _form(params: dict) -> list[tuple[str, str]]:
    out: list[tuple[str, str]] = []
    _flatten("", params, out)
    return out


async def _post(path: str, params: dict, idempotency_key: str = "") -> dict:
    """POST в Stripe. Idempotency-Key обязателен для всего, что создаёт
    деньги: повтор запроса после таймаута сети вернёт ТОТ ЖЕ объект, а не
    заведёт второй платёж."""
    headers = {
        "Authorization": f"Bearer {STRIPE_SECRET_KEY}",
        "Stripe-Version": STRIPE_API_VERSION,
        "Content-Type": "application/x-www-form-urlencoded",
    }
    if idempotency_key:
        headers["Idempotency-Key"] = idempotency_key[:255]
    async with httpx.AsyncClient(timeout=40) as client:
        r = await client.post(f"{STRIPE_API}{path}", data=_form(params), headers=headers)
    try:
        data = r.json() or {}
    except ValueError:
        data = {}
    if r.status_code not in (200, 201):
        msg = ((data.get("error") or {}).get("message") or r.text or "")[:300]
        raise StripeError(f"Stripe: {msg}", status=r.status_code)
    return data


class StripeError(RuntimeError):
    def __init__(self, message: str, status: int = 502):
        super().__init__(message)
        self.status = status


# ─────────────────────────── создание оплаты ───────────────────────────

async def create_coupon(percent_off: int, name: str = "") -> str:
    """Разовый купон под реферальную скидку.

    В режиме subscription цену подписки менять нельзя (она рекуррентная), а
    скидка по промокоду партнёрки даётся ТОЛЬКО на первую оплату — поэтому
    duration=once: Stripe применит его к первому счёту и отпустит."""
    pct = max(1, min(90, int(percent_off)))
    data = await _post("/v1/coupons", {
        "percent_off": pct,
        "duration": "once",
        "name": (name or f"referral -{pct}%")[:40],
        "max_redemptions": 1,
    }, idempotency_key=uuid.uuid4().hex)
    return str(data.get("id") or "")


async def create_checkout_session(
    *,
    kind: str,                  # plan | topup
    user_id: int,
    title: str,                 # что человек видит в чекауте и в выписке
    amount_cents: int,
    success_url: str,
    cancel_url: str,
    period: str = "month",      # для kind=plan: month | year
    plan_id: str = "",
    pack_id: str = "",
    points: int = 0,
    email: str = "",
    customer_id: str = "",
    coupon_id: str = "",
    promo: str = "",
    ambassador_id: int = 0,
    idempotency_key: str = "",
) -> dict:
    """Checkout Session: подписка для плана, разовый платёж для пакета очков.

    metadata дублируется в subscription_data/payment_intent_data — иначе при
    автопродлении к нам приезжает invoice, у которого своей metadata нет, и
    непонятно, кому и какой план выдавать."""
    meta = {
        "user_id": str(int(user_id)),
        "kind": kind,
        "plan": plan_id,
        "pack": pack_id,
        "period": period,
        "points": str(int(points or 0)),
        "promo": promo or "",
        "ambassador_id": str(int(ambassador_id or 0)),
    }
    item: dict = {
        "quantity": 1,
        "price_data": {
            "currency": "usd",
            "unit_amount": int(amount_cents),
            "product_data": {"name": title[:250]},
        },
    }
    params: dict = {
        "success_url": success_url,
        "cancel_url": cancel_url,
        "client_reference_id": str(int(user_id)),
        "line_items": [item],
        "metadata": meta,
        # Локаль по браузеру: интерфейс сервиса английский, но платить человеку
        # удобнее на своём языке.
        "locale": "auto",
    }
    # customer и customer_email взаимоисключающие: если человек уже платил,
    # цепляем его к существующему клиенту, чтобы карты и счета не разъезжались.
    if customer_id:
        params["customer"] = customer_id
    elif email:
        params["customer_email"] = email

    if kind == "plan":
        item["price_data"]["recurring"] = {
            "interval": "year" if period == "year" else "month",
        }
        params["mode"] = "subscription"
        params["subscription_data"] = {"metadata": meta}
        if coupon_id:
            params["discounts"] = [{"coupon": coupon_id}]
    else:
        params["mode"] = "payment"
        # Разовая покупка очков: способ оплаты НЕ сохраняем — это не подписка.
        params["payment_intent_data"] = {"metadata": meta}
        if not customer_id:
            params["customer_creation"] = "if_required"
        if coupon_id:
            params["discounts"] = [{"coupon": coupon_id}]

    data = await _post("/v1/checkout/sessions", params,
                       idempotency_key=idempotency_key or uuid.uuid4().hex)
    return {
        "id": str(data.get("id") or ""),
        "url": str(data.get("url") or ""),
        "amount_cents": int(data.get("amount_total") or amount_cents),
        "customer": str(data.get("customer") or ""),
    }


async def cancel_subscription(subscription_id: str, at_period_end: bool = True) -> dict:
    """Отмена подписки. По умолчанию в конце оплаченного периода — деньги уже
    взяты, отбирать доступ задним числом нечестно (так же работает ЮKassa-ветка)."""
    sid = str(subscription_id or "").strip()
    if not sid:
        return {}
    if at_period_end:
        return await _post(f"/v1/subscriptions/{sid}", {"cancel_at_period_end": True})
    headers = {
        "Authorization": f"Bearer {STRIPE_SECRET_KEY}",
        "Stripe-Version": STRIPE_API_VERSION,
    }
    async with httpx.AsyncClient(timeout=40) as client:
        r = await client.delete(f"{STRIPE_API}/v1/subscriptions/{sid}", headers=headers)
    try:
        return r.json() or {}
    except ValueError:
        return {}


# ─────────────────────────── проверка вебхука ───────────────────────────

def verify_webhook(payload: bytes, sig_header: str) -> dict | None:
    """Подпись Stripe: заголовок вида `t=1699999999,v1=<hex>,v1=<hex>`.

    Считаем HMAC-SHA256 от "<t>.<сырое тело>" секретом вебхука и сравниваем
    в постоянном времени (hmac.compare_digest) — обычное == утекает по времени
    и позволяет подобрать подпись побайтово. Тело нужно СЫРОЕ: любой re-dump
    JSON меняет пробелы и ломает подпись.

    None = верить нельзя (нет секрета, кривой заголовок, не сошлась подпись
    или событие слишком старое — защита от переигрывания записанного вебхука)."""
    if not STRIPE_WEBHOOK_SECRET or not sig_header or not payload:
        return None
    ts = ""
    sigs: list[str] = []
    for part in str(sig_header).split(","):
        key, _, val = part.strip().partition("=")
        if key == "t":
            ts = val.strip()
        elif key == "v1":
            sigs.append(val.strip())
    if not ts or not sigs:
        return None
    try:
        sent_at = int(ts)
    except ValueError:
        return None
    if SIG_TOLERANCE_SEC > 0 and abs(int(time.time()) - sent_at) > SIG_TOLERANCE_SEC:
        log.warning("stripe: подпись вебхука просрочена (t=%s)", ts)
        return None
    expected = hmac.new(
        STRIPE_WEBHOOK_SECRET.encode("utf-8"),
        b"%s.%s" % (ts.encode("ascii"), payload),
        hashlib.sha256,
    ).hexdigest()
    if not any(hmac.compare_digest(expected, s) for s in sigs):
        log.warning("stripe: подпись вебхука не сошлась")
        return None
    try:
        return json.loads(payload.decode("utf-8")) or {}
    except (ValueError, UnicodeDecodeError):
        return None


def event_metadata(obj: dict) -> dict:
    """Наша metadata из объекта события.

    У checkout.session она лежит прямо в metadata, у invoice — в
    subscription_details (новые версии API прячут его в parent) или в строках
    счёта. Смотрим во все места: иначе автопродление приезжает «ничьим»."""
    obj = obj or {}
    candidates = [
        obj.get("metadata"),
        (obj.get("subscription_details") or {}).get("metadata"),
        ((obj.get("parent") or {}).get("subscription_details") or {}).get("metadata"),
        (((obj.get("lines") or {}).get("data") or [{}])[0] or {}).get("metadata"),
    ]
    for meta in candidates:
        if isinstance(meta, dict) and meta.get("user_id"):
            return meta
    return {}


def subscription_id_of(obj: dict) -> str:
    """id подписки из checkout.session или invoice — в разных версиях API он
    лежит то строкой, то объектом, то в parent."""
    obj = obj or {}
    for cand in (
        obj.get("subscription"),
        (obj.get("parent") or {}).get("subscription_details", {}).get("subscription"),
        (((obj.get("lines") or {}).get("data") or [{}])[0] or {}).get("subscription"),
    ):
        if isinstance(cand, str) and cand.startswith("sub_"):
            return cand
        if isinstance(cand, dict) and str(cand.get("id") or "").startswith("sub_"):
            return str(cand["id"])
    return ""
