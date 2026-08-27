"""Небольшая CRM lolq.ai: клиенты, действия админа и рассылки.

Зачем отдельный модуль, а не правки в main.py: main.py уже 6.5 тысяч строк, а
всё, что нужно владельцу для «видеть клиентов, включать-отключать подписки,
начислять токены и делать по ним рассылку», помещается в один файл и
подключается двумя строками (crm.mount(app), как у bot_api/stars/tg_app).

ТРИ РЕШЕНИЯ, БЕЗ КОТОРЫХ ЭТО БЫЛА БЫ ДЕКОРАЦИЯ:

1. Каждое админское действие пишется ДВАЖДЫ: движение токенов — в journal токенов
   через core._move_points (ту же единственную дверь, что и генерации), а сам
   факт «кто и когда это сделал» — в admin_actions. Журнал токенов покрывает
   только токены; смена тарифа и блокировка в него не ложатся, а знать, кто
   включил человеку ULTRA руками, нужно ровно так же.

2. Кнопка «отключить подписку» НЕ ВРЁТ. Она выключает НАШУ сторону
   (plan_until, autopay). Продление у Stripe делает сам Stripe, у Telegram
   Stars — сам Telegram, и у таких людей карточка честно пишет, где отменять
   на самом деле. Кнопка, которая делает вид, что отменила списание в Stripe,
   — это возвраты и споры, а не удобство.

3. Сегмент рассылки — ИМЯ И ПАРАМЕТРЫ, а не сохранённый список id. Список
   считается в момент отправки: сохранённый за неделю протухает, и рассылка
   «новичкам без генераций» ушла бы тем, кто уже собрал первый клип.
"""
from __future__ import annotations

import json
import logging
import re
import uuid
import os
import threading
import time
from datetime import timedelta

from fastapi import APIRouter, Depends, HTTPException, Request, UploadFile
from fastapi.responses import HTMLResponse
from itsdangerous import BadSignature, URLSafeSerializer
from sqlalchemy import func, or_
from sqlalchemy.orm import Session

import mailer
import mockup_catalog
import prompts_library
from db import (
    AdminAction, AppSetting, Campaign, CampaignRecipient, Chat, ChatMessage, FileOwner,
    PointEvent, ProcessedPayment, Project, RefEvent, Scene, SessionLocal,
    StyleAsset, StyleOverride, Track, User, now,
)

log = logging.getLogger("rapclips")
router = APIRouter()


def _core():
    """main импортируется ЛЕНИВО: crm подключается ИЗ main.py, и импорт на
    верхнем уровне дал бы цикл (тот же приём, что в bot_api)."""
    import main  # noqa: PLC0415
    return main


def db_session():
    session = SessionLocal()
    try:
        yield session
    finally:
        session.close()


def admin_user(request: Request, db: Session = Depends(db_session)) -> User:
    core = _core()
    user = core._resolve_user(request, db)
    if not user:
        raise HTTPException(401, "не авторизован")
    if not user.is_admin:
        raise HTTPException(403, "только для админа")
    return user


def _log_action(db: Session, admin: User, user_id: int, action: str, payload: dict) -> None:
    try:
        db.add(AdminAction(admin_id=admin.id, user_id=int(user_id or 0),
                           action=action,
                           payload_json=json.dumps(payload, ensure_ascii=False)[:2000]))
        db.commit()
    except Exception as e:  # noqa: BLE001
        db.rollback()
        log.warning("crm: действие %s не записалось: %s", action, str(e)[:150])


# ═══════════════════════════ КЛИЕНТЫ ═══════════════════════════

def _pay_source(user: User) -> str:
    """Кто на самом деле продлевает подписку. От этого зависит, врёт ли
    кнопка «отключить»."""
    if getattr(user, "stars_sub_charge_id", ""):
        return "stars"
    if user.stripe_subscription_id:
        return "stripe"
    if user.pay_method_id:
        return "yookassa"
    return ""


def _user_row(core, db: Session, u: User) -> dict:
    return {
        "id": u.id,
        "name": u.name or "",
        "login": u.login or "",
        "email": u.email or "",
        "tg": u.tg_username or "",
        "tg_id": u.tg_id or "",
        "avatar_url": u.avatar_url or "",
        "plan": core._plan_of(u),
        "plan_title": core.PLANS[core._plan_of(u)]["title"],
        "plan_tier": core._tier_of_user(u),
        "plan_period": u.plan_period or "month",
        "plan_until": core._as_utc(u.plan_until).isoformat() if u.plan_until else "",
        "autopay": bool(u.autopay),
        "pay_source": _pay_source(u),
        "points": int(u.gen_points or 0),
        "is_admin": bool(u.is_admin),
        "is_blocked": bool(getattr(u, "is_blocked", False)),
        "blocked_reason": getattr(u, "blocked_reason", "") or "",
        "is_ambassador": bool(u.is_ambassador),
        "ref_code": u.ref_code or "",
        "created_at": core._as_utc(u.created_at).isoformat() if u.created_at else "",
        "last_seen_at": (core._as_utc(u.last_seen_at).isoformat()
                         if getattr(u, "last_seen_at", None) else ""),
        "unsub": {"email": bool(getattr(u, "unsub_email", False)),
                  "tg": bool(getattr(u, "unsub_tg", False)),
                  "all": bool(getattr(u, "unsub_all", False))},
    }


@router.get("/api/admin/users")
def admin_users(q: str = "", plan: str = "", state: str = "", has: str = "",
                sort: str = "new", cursor: int = 0, limit: int = 50,
                user: User = Depends(admin_user), db: Session = Depends(db_session)):
    """Список клиентов с поиском и фильтрами.

    ГРАБЛЯ ПОИСКА: база — SQLite, и LIKE по кириллице там регистрозависим
    ровно так же, как ILIKE в Postgres с C-collation. Поэтому сравниваем
    через LOWER() с нормализованной строкой, а не «как получится»."""
    core = _core()
    limit = max(1, min(200, int(limit or 50)))
    query = db.query(User)
    needle = str(q or "").strip().lower()
    if needle:
        like = f"%{needle}%"
        conds = [func.lower(User.name).like(like), func.lower(User.login).like(like),
                 func.lower(User.email).like(like),
                 func.lower(User.tg_username).like(like),
                 func.lower(User.ref_code).like(like)]
        if needle.isdigit():
            conds.append(User.id == int(needle))
            conds.append(User.tg_id == needle)
        query = query.filter(or_(*conds))
    if plan == "paid":
        query = query.filter(User.plan != "free")
    elif plan:
        query = query.filter(User.plan == plan)
    if state == "blocked":
        query = query.filter(User.is_blocked.is_(True))
    elif state == "active":
        query = query.filter(User.last_seen_at.isnot(None),
                             User.last_seen_at >= now() - timedelta(days=7))
    elif state == "sleeping":
        query = query.filter(or_(User.last_seen_at.is_(None),
                                 User.last_seen_at < now() - timedelta(days=30)))
    if has == "email":
        query = query.filter(User.email != "")
    elif has == "tg":
        query = query.filter(User.tg_id != "")
    elif has == "ambassador":
        query = query.filter(User.is_ambassador.is_(True))

    if sort == "points":
        query = query.order_by(User.gen_points.desc(), User.id.desc())
    elif sort == "seen":
        query = query.order_by(User.last_seen_at.desc().nullslast(), User.id.desc())
    else:
        if cursor:
            query = query.filter(User.id < int(cursor))
        query = query.order_by(User.id.desc())
    if sort in ("points", "seen") and cursor:
        query = query.offset(int(cursor))

    rows = query.limit(limit + 1).all()
    more = len(rows) > limit
    rows = rows[:limit]
    if sort in ("points", "seen"):
        nxt = (int(cursor or 0) + limit) if more else 0
    else:
        nxt = rows[-1].id if (more and rows) else 0
    return {"items": [_user_row(core, db, u) for u in rows], "next_cursor": nxt,
            "total": db.query(func.count(User.id)).scalar() or 0}


@router.get("/api/admin/users/{uid}")
def admin_user_card(uid: int, user: User = Depends(admin_user),
                    db: Session = Depends(db_session)):
    """Карточка клиента: профиль, тариф, токены, работы, деньги."""
    core = _core()
    u = db.get(User, uid)
    if not u:
        raise HTTPException(404, "клиент не найден")
    row = _user_row(core, db, u)

    projects = db.query(Project).filter(Project.owner_id == u.id).all()
    track_ids = [t.id for p in projects for t in p.tracks]
    scenes = (db.query(func.count(Scene.id)).filter(Scene.track_id.in_(track_ids)).scalar()
              if track_ids else 0)
    clips = sum(1 for p in projects for t in p.tracks if t.clip_filename)
    files_n, files_b = (db.query(func.count(FileOwner.filename),
                                 func.coalesce(func.sum(FileOwner.size_bytes), 0))
                        .filter(FileOwner.user_id == u.id,
                                FileOwner.deleted_at.is_(None)).first() or (0, 0))
    chats = db.query(func.count(Chat.id)).filter(Chat.owner_id == u.id).scalar() or 0

    payments = (db.query(ProcessedPayment)
                .filter(ProcessedPayment.user_id == u.id)
                .order_by(ProcessedPayment.id.desc()).limit(50).all())
    paid_cents = sum(int(p.amount_cents or 0) for p in payments)
    paid_kopeks = sum(int(p.amount_kopeks or 0) for p in payments)
    spent = int(db.query(func.coalesce(func.sum(-PointEvent.delta), 0))
                .filter(PointEvent.user_id == u.id, PointEvent.delta < 0).scalar() or 0)
    cost_cents = int(db.query(func.coalesce(func.sum(PointEvent.cost_cents), 0))
                     .filter(PointEvent.user_id == u.id).scalar() or 0)

    inviter = db.get(User, u.referred_by) if u.referred_by else None
    refs = (db.query(func.count(RefEvent.id))
            .filter(RefEvent.ambassador_id == u.id,
                    RefEvent.kind == "signup").scalar() or 0)

    row.update({
        "linked": {"telegram": bool(u.tg_id), "yandex": bool(u.yandex_id),
                   "google": bool(u.google_id), "password": bool(u.login)},
        "drip": {"left": int(u.points_drip_left or 0),
                 "size": int(u.points_drip_size or 0),
                 "at": core._as_utc(u.points_drip_at).isoformat() if u.points_drip_at else ""},
        "work": {"projects": len(projects), "tracks": len(track_ids),
                 "scenes": int(scenes or 0), "clips": clips, "chats": chats,
                 "files": int(files_n or 0), "bytes": int(files_b or 0)},
        "money": {"payments": len(payments), "paid_cents": paid_cents,
                  "paid_kopeks": paid_kopeks, "spent_points": spent,
                  # Себестоимость и маржа — только здесь, в админской карточке.
                  "cost_cents": cost_cents,
                  "margin_cents": paid_cents - cost_cents},
        "invited_by": ({"id": inviter.id, "name": inviter.name or f"#{inviter.id}"}
                       if inviter else None),
        "referrals": int(refs),
        "payments": [{"id": p.id, "at": core._as_utc(p.created_at).isoformat() if p.created_at else "",
                      "provider": p.provider, "kind": p.kind, "plan": p.plan,
                      "period": p.period, "points": int(p.points or 0),
                      "amount_cents": int(p.amount_cents or 0),
                      "amount_kopeks": int(p.amount_kopeks or 0),
                      "currency": p.currency} for p in payments],
        # Что кнопка «отключить подписку» реально сделает, интерфейс пишет сам
        # по КОДУ pay_source (см. _user_row и crm.cancelNote в i18n.js). Готовой
        # фразы отсюда НЕ отдаём: сервер отвечает кодом, язык — дело
        # интерфейса. Раньше здесь лежал русский текст, и англоязычный админ
        # читал в английской карточке клиента русское предложение.
        "plans": [{"id": pid, "title": spec["title"], "points": spec["points"]}
                  for pid, spec in core.PLANS.items()],
        "tiers": [{"id": t["id"], "points": t["points"]}
                  for t in core.PLAN_TIERS.get("studio", [])],
    })
    return row


@router.post("/api/admin/users/{uid}/points")
async def admin_points(uid: int, request: Request, user: User = Depends(admin_user),
                       db: Session = Depends(db_session)):
    """Начислить или списать токены руками.

    Идёт через core._move_points — ту же единственную дверь, что и генерации:
    иначе в кабинете человека появился бы остаток, которого не объясняет ни
    одна строка истории."""
    core = _core()
    u = db.get(User, uid)
    if not u:
        raise HTTPException(404, "клиент не найден")
    body = await request.json()
    delta = int(body.get("delta") or 0)
    reason = str(body.get("reason") or "").strip()[:160]
    if not delta:
        raise HTTPException(400, "нужно ненулевое число токенов")
    if delta < 0 and int(u.gen_points or 0) + delta < 0:
        delta = -int(u.gen_points or 0)      # в минус баланс не уводим
    core._move_points(db, u, delta,
                      f"админ: {reason or ('начисление' if delta > 0 else 'списание')}",
                      kind="admin", ref_type="admin", ref_id=user.id)
    _log_action(db, user, uid, "points", {"delta": delta, "reason": reason})
    log.info("crm: админ %s → юзеру %s %+d токенов (%s)", user.id, uid, delta, reason)
    return {"ok": True, "points": int(u.gen_points or 0)}


@router.post("/api/admin/users/{uid}/plan")
async def admin_plan(uid: int, request: Request, user: User = Depends(admin_user),
                     db: Session = Depends(db_session)):
    """Поставить тариф руками: план, ступень, срок, автопродление.

    Токены при этом начисляются ТОЛЬКО если попросили явно (grant_points):
    «поменять тариф» и «выдать месячную норму» — разные действия, и склеивать
    их значит раздавать токены при каждой правке срока."""
    core = _core()
    u = db.get(User, uid)
    if not u:
        raise HTTPException(404, "клиент не найден")
    body = await request.json()
    plan_id = str(body.get("plan") or "").strip()
    if plan_id and plan_id not in core.PLANS:
        raise HTTPException(400, f"неизвестный тариф {plan_id}")
    before = {"plan": u.plan, "tier": u.plan_tier, "until": str(u.plan_until or ""),
              "autopay": bool(u.autopay)}
    if plan_id:
        u.plan = plan_id
        if core.PLAN_TIERS.get(plan_id):
            u.plan_tier = core._tier_spec(plan_id, str(body.get("tier") or u.plan_tier))["id"]
        else:
            u.plan_tier = ""
        u.plan_tier_next = ""
    if "period" in body:
        u.plan_period = core._norm_period(body.get("period"))
    if body.get("days") is not None:
        days = int(body.get("days") or 0)
        u.plan_until = (now() + timedelta(days=days)) if days > 0 else None
    if "autopay" in body:
        u.autopay = bool(body.get("autopay"))
    if (u.plan or "free") == "free":
        # Тариф снят — годовые капли не должны его пережить.
        u.points_drip_left = 0
        u.points_drip_size = 0
        u.points_drip_at = None
    db.commit()
    granted = 0
    if body.get("grant_points"):
        granted = core._add_points(
            db, u, core._plan_points(core._plan_of(u), core._tier_of_user(u)),
            f"админ: норма тарифа {core._plan_of(u)}",
            kind="admin", ref_type="admin", ref_id=user.id)
        db.commit()
    _log_action(db, user, uid, "plan", {"before": before, "after": {
        "plan": u.plan, "tier": u.plan_tier, "until": str(u.plan_until or ""),
        "autopay": bool(u.autopay)}, "granted": granted})
    log.info("crm: админ %s → юзеру %s тариф %s (%s)", user.id, uid, u.plan, u.plan_tier)
    return {"ok": True, "granted": granted, **_user_row(core, db, u)}


@router.post("/api/admin/users/{uid}/block")
async def admin_block(uid: int, request: Request, user: User = Depends(admin_user),
                      db: Session = Depends(db_session)):
    core = _core()
    u = db.get(User, uid)
    if not u:
        raise HTTPException(404, "клиент не найден")
    if u.is_admin:
        raise HTTPException(400, "владельца блокировать нельзя")
    body = await request.json()
    u.is_blocked = bool(body.get("blocked"))
    u.blocked_reason = str(body.get("reason") or "").strip()[:200]
    db.commit()
    _log_action(db, user, uid, "block",
                {"blocked": u.is_blocked, "reason": u.blocked_reason})
    log.info("crm: админ %s %s юзера %s", user.id,
             "заблокировал" if u.is_blocked else "разблокировал", uid)
    return {"ok": True, **_user_row(core, db, u)}


@router.get("/api/admin/users/{uid}/actions")
def admin_user_actions(uid: int, user: User = Depends(admin_user),
                       db: Session = Depends(db_session)):
    core = _core()
    rows = (db.query(AdminAction).filter(AdminAction.user_id == uid)
            .order_by(AdminAction.id.desc()).limit(50).all())
    admins = {u.id: u.name for u in db.query(User).filter(User.is_admin.is_(True)).all()}
    return {"items": [{
        "id": a.id, "action": a.action,
        "at": core._as_utc(a.created_at).isoformat() if a.created_at else "",
        "admin": admins.get(a.admin_id) or f"#{a.admin_id}",
        "payload": a.payload_json,
    } for a in rows]}


# ═══════════════════════ СВОДКА ПО СЕРВИСУ ═══════════════════════

@router.get("/api/admin/models-toggle")
def admin_models_list(user: User = Depends(admin_user), db: Session = Depends(db_session)):
    """Все модели сервиса с тумблерами. Выключенная исчезает у клиентов."""
    core = _core()
    off = core._disabled_models(db)
    out = []
    for eid, spec in core.mediagen.VIDEO_ENGINES.items():
        out.append({"id": f"video:{eid}", "title": spec.get("title", eid),
                    "kind": "video", "enabled": f"video:{eid}" not in off})
    for eid, spec in core.mediagen.IMAGE_ENGINES.items():
        out.append({"id": f"image:{eid}", "title": spec.get("title", eid),
                    "kind": "image", "enabled": f"image:{eid}" not in off})
    return {"models": out}


@router.post("/api/admin/models-toggle")
async def admin_models_save(request: Request, user: User = Depends(admin_user),
                            db: Session = Depends(db_session)):
    body = await request.json()
    off = [str(x) for x in (body.get("disabled") or [])][:100]
    row = db.get(AppSetting, "disabled_models")
    if row:
        row.value = json.dumps(off)
    else:
        db.add(AppSetting(key="disabled_models", value=json.dumps(off)))
    db.commit()
    return {"ok": True, "disabled": off}


@router.post("/api/admin/earn/sale")
async def admin_earn_sale(request: Request, user: User = Depends(admin_user),
                          db: Session = Depends(db_session)):
    """Менеджер фиксирует продажу по партнёрской ссылке.

    Правило: оплачивается только ПЕРВАЯ продажа клиента — client_key уникален,
    и повторная запись честно отвечает 409, а не молча дублирует выплату.
    Партнёру начисляются коины на сумму вознаграждения по нынешнему курсу."""
    core = _core()
    body = await request.json()
    code = str(body.get("partner_code") or "").strip().upper()
    partner = db.query(User).filter(User.ref_code == code).first()
    if not partner:
        raise HTTPException(404, "партнёр с таким кодом не найден")
    preset = db.get(core.TrendPreset, int(body.get("preset_id") or 0))
    if not preset or preset.kind != "earn":
        raise HTTPException(404, "продукт не найден")
    client_key = str(body.get("client_key") or "").strip().lower()
    if not client_key:
        raise HTTPException(400, "нужен ключ клиента (телефон или почта)")
    import hashlib
    ck = hashlib.sha256(client_key.encode()).hexdigest()[:24]
    amount = int(float(body.get("amount_rub") or 0) * 100)
    if amount <= 0:
        raise HTTPException(400, "сумма продажи обязательна")
    pct = max(10, min(20, int(preset.reward_pct or 10)))
    reward = amount * pct // 100
    sale = core.EarnSale(preset_id=preset.id, partner_id=partner.id,
                         client_key=ck, amount_kopeks=amount,
                         reward_kopeks=reward)
    db.add(sale)
    try:
        db.commit()
    except Exception:  # noqa: BLE001 — уникальный client_key сработал
        db.rollback()
        raise HTTPException(409, "этот клиент уже оплачивался — засчитывается только первая продажа")
    # Вознаграждение — коинами по курсу: рубли → токены → коины.
    tokens = core._points_of_usd(amount / 100 / max(1.0, core.USD_RUB)) * pct // 100
    coins = max(1, int(tokens / core.BONUS_RATE))
    core._grant_bonus(db, partner, coins,
                      f"продажа «{preset.title[:40]}» ({pct}%)",
                      ref_type="earn_sale", ref_id=sale.id)
    return {"ok": True, "sale_id": sale.id, "reward_kopeks": reward,
            "coins_granted": coins}


@router.get("/api/admin/design")
def admin_design(user: User = Depends(admin_user), db: Session = Depends(db_session)):
    core = _core()
    return {"tokens": core._design_tokens(db), "defaults": core.DESIGN_DEFAULTS}


@router.post("/api/admin/design")
async def admin_design_save(request: Request, user: User = Depends(admin_user),
                            db: Session = Depends(db_session)):
    core = _core()
    body = await request.json()
    tokens = {k: str(body.get(k))[:40] for k in core.DESIGN_DEFAULTS if body.get(k)}
    row = db.get(AppSetting, "design_tokens")
    if row:
        row.value = json.dumps(tokens, ensure_ascii=False)
    else:
        db.add(AppSetting(key="design_tokens", value=json.dumps(tokens, ensure_ascii=False)))
    db.commit()
    return {"ok": True, "tokens": core._design_tokens(db)}


@router.get("/api/admin/trends")
def admin_trends(user: User = Depends(admin_user), db: Session = Depends(db_session)):
    core = _core()
    rows = db.query(core.TrendPreset).order_by(
        core.TrendPreset.position, core.TrendPreset.id).all()
    return {"presets": [{
        "id": t.id, "title": t.title, "enabled": t.enabled,
        "image_prompt": t.image_prompt, "motion_prompt": t.motion_prompt,
        "image_engine": t.image_engine, "video_engine": t.video_engine,
        "duration_sec": t.duration_sec, "aspect": t.aspect,
        "poster_url": f"/api/media/{t.poster_filename}" if t.poster_filename else "",
        "sample_url": f"/api/media/{t.sample_filename}" if t.sample_filename else "",
        # Партнёрские поля: без них вкладка «Заработок» не смогла бы отличить
        # продукт от тренда и показывала бы пустые награду и ссылку.
        "kind": t.kind or "trend", "landing_url": t.landing_url or "",
        "reward_note": t.reward_note or "", "reward_pct": int(t.reward_pct or 10),
        "position": t.position,
    } for t in rows]}


@router.post("/api/admin/trends")
async def admin_trend_save(request: Request, user: User = Depends(admin_user),
                           db: Session = Depends(db_session)):
    """Создать или поправить шаблон. Файлы постера и примера — отдельной ручкой."""
    core = _core()
    body = await request.json()
    t = db.get(core.TrendPreset, int(body.get("id") or 0)) if body.get("id") else None
    if not t:
        t = core.TrendPreset()
        db.add(t)
    for field in ("title", "image_prompt", "motion_prompt",
                  "image_engine", "video_engine", "aspect",
                  "kind", "landing_url", "reward_note"):
        if field in body:
            setattr(t, field, str(body[field] or ""))
    if "reward_pct" in body:
        t.reward_pct = max(1, min(50, int(body["reward_pct"] or 10)))
    if "duration_sec" in body:
        t.duration_sec = max(2, min(12, int(body["duration_sec"] or 6)))
    if "enabled" in body:
        t.enabled = bool(body["enabled"])
    if "position" in body:
        t.position = int(body["position"] or 0)
    db.commit()
    db.refresh(t)
    return {"ok": True, "id": t.id}


@router.post("/api/admin/trends/{preset_id}/media")
async def admin_trend_media(preset_id: int, kind: str = "poster",
                            file: UploadFile = None,
                            user: User = Depends(admin_user),
                            db: Session = Depends(db_session)):
    """Постер (картинка) или пример (ролик) шаблона."""
    core = _core()
    t = db.get(core.TrendPreset, preset_id)
    if not t:
        raise HTTPException(404, "шаблон не найден")
    data = await file.read()
    if len(data) < 1024:
        raise HTTPException(400, "файл пуст")
    ext = os.path.splitext(file.filename or "")[1].lower() or (".jpg" if kind == "poster" else ".mp4")
    fname = f"trendm_{preset_id}_{kind}_{uuid.uuid4().hex[:8]}{ext}"
    path = os.path.join(core.UPLOAD_DIR, fname)
    with open(path, "wb") as f:
        f.write(data)
    if kind == "sample":
        t.sample_filename = fname
    else:
        t.poster_filename = fname
    db.commit()
    return {"ok": True, "url": f"/api/media/{fname}"}


@router.delete("/api/admin/trends/{preset_id}")
def admin_trend_delete(preset_id: int, user: User = Depends(admin_user),
                       db: Session = Depends(db_session)):
    core = _core()
    t = db.get(core.TrendPreset, preset_id)
    if t:
        db.delete(t)
        db.commit()
    return {"ok": True}


@router.get("/api/admin/pricing")
def admin_pricing(user: User = Depends(admin_user), db: Session = Depends(db_session)):
    """Нынешняя наценка и то, во что она обходится клиенту.

    Показываем не голый коэффициент, а его следствие: сколько токенов стоит
    типичная сцена сейчас. Двигать процент вслепую — верный способ уронить
    маржу или отпугнуть ценой.
    """
    core = _core()
    k = core._markup()
    engines = []
    for eid, title in [(e, e) for e in core.mediagen.VIDEO_ENGINES]:
        engines.append({"id": eid, "video_cost": core.VIDEO_COST.get(eid, 0)})
    return {
        "markup": round(k, 2),
        "min": core.MARKUP_MIN,
        "max": core.MARKUP_MAX,
        "point_usd_base": core.POINT_USD,
        "point_usd_now": round(core._point_usd(), 5),
        "frame_pair_cost": core.FRAME_COST.get("nano-banana", 0),
        "scene_cost": core.SCENE_COST.get("seedance-2-mini", 0),
        "engines": engines,
        "signup_bonus": core.REF_SIGNUP_BONUS,
        "cashback_pct": core.REF_CASHBACK_PCT,
    }


@router.post("/api/admin/pricing")
async def admin_set_pricing(request: Request, user: User = Depends(admin_user),
                            db: Session = Depends(db_session)):
    """Сдвинуть коэффициент наценки. Действует сразу, без переката сервиса."""
    core = _core()
    body = await request.json()
    try:
        k = float(body.get("markup"))
    except (TypeError, ValueError):
        raise HTTPException(400, "наценка должна быть числом")
    k = max(core.MARKUP_MIN, min(core.MARKUP_MAX, k))
    row = db.get(AppSetting, core.MARKUP_KEY)
    if row:
        row.value = str(k)
        row.updated_at = now()
    else:
        db.add(AppSetting(key=core.MARKUP_KEY, value=str(k)))
    db.commit()
    # Кэш держит значение несколько секунд — сбрасываем, чтобы ползунок
    # отвечал сразу, а не «через пять секунд».
    core._markup_cache.update(at=0.0, value=k)
    log.info("админ %s поставил наценку %s", user.id, k)
    return admin_pricing(user=user, db=db)


@router.get("/api/admin/stats")
def admin_stats(days: int = 30, user: User = Depends(admin_user),
                db: Session = Depends(db_session)):
    """Регистрации, выручка, себестоимость и маржа по дням.

    Выручка лежит в processed_payments, себестоимость — в point_events.
    cost_cents. До появления второй колонки маржа сервиса была неизвестна в
    принципе: мы знали, сколько нам заплатили, и не знали, сколько мы за это
    отдали kie.ai."""
    core = _core()
    days = max(7, min(180, int(days or 30)))
    since = now() - timedelta(days=days)
    daily = {}
    for i in range(days):
        d = (now() - timedelta(days=days - 1 - i)).date().isoformat()
        daily[d] = {"date": d, "signups": 0, "revenue_cents": 0,
                    "cost_cents": 0, "spent": 0}
    for u in db.query(User).filter(User.created_at >= since).all():
        d = (core._as_utc(u.created_at) or now()).date().isoformat()
        if d in daily:
            daily[d]["signups"] += 1
    for p in db.query(ProcessedPayment).filter(ProcessedPayment.created_at >= since).all():
        d = (core._as_utc(p.created_at) or now()).date().isoformat()
        if d in daily:
            cents = int(p.amount_cents or 0) or int(
                (p.amount_kopeks or 0) / max(1, core.USD_RUB))
            daily[d]["revenue_cents"] += cents
    for e in (db.query(PointEvent.created_at, PointEvent.cost_cents, PointEvent.delta)
              .filter(PointEvent.created_at >= since).all()):
        d = (core._as_utc(e[0]) or now()).date().isoformat()
        if d in daily:
            daily[d]["cost_cents"] += int(e[1] or 0)
            if (e[2] or 0) < 0:
                daily[d]["spent"] += -int(e[2])
    rows = list(daily.values())
    users_total = db.query(func.count(User.id)).scalar() or 0
    paying = db.query(func.count(User.id)).filter(User.plan != "free").scalar() or 0
    blocked = db.query(func.count(User.id)).filter(User.is_blocked.is_(True)).scalar() or 0
    with_email = db.query(func.count(User.id)).filter(User.email != "").scalar() or 0
    with_tg = db.query(func.count(User.id)).filter(User.tg_id != "").scalar() or 0
    active7 = (db.query(func.count(User.id))
               .filter(User.last_seen_at >= now() - timedelta(days=7)).scalar() or 0)
    files_n, files_b = (db.query(func.count(FileOwner.filename),
                                 func.coalesce(func.sum(FileOwner.size_bytes), 0))
                        .filter(FileOwner.deleted_at.is_(None)).first() or (0, 0))
    return {
        "days": days, "daily": rows,
        "revenue_cents": sum(r["revenue_cents"] for r in rows),
        "cost_cents": sum(r["cost_cents"] for r in rows),
        "signups": sum(r["signups"] for r in rows),
        "spent": sum(r["spent"] for r in rows),
        "users": {"total": users_total, "paying": paying, "blocked": blocked,
                  "with_email": with_email, "with_tg": with_tg, "active7": active7},
        "storage": {"files": int(files_n or 0), "bytes": int(files_b or 0)},
        "channels": mailer.state(),
    }


@router.get("/api/admin/ledger/audit")
def ledger_audit(limit: int = 50, user: User = Depends(admin_user),
                 db: Session = Depends(db_session)):
    """Инвариант журнала: сумма строк против фактического баланса.

    Расхождение = кто-то прошёл мимо _move_points. Проверка ловит дыру за
    сутки, а не через квартал, и заодно показывает, где журнал разъехался
    из-за ручной правки базы.

    ВАЖНО про интерпретацию: у людей, заведённых ДО журнала, стартовая норма
    (150 токенов плана free) строкой не оформлена, поэтому дельта на её
    величину — это норма, а не дыра. Поэтому отдаём и то, и другое."""
    core = _core()
    free_start = core.PLANS["free"]["points"]
    # Стартовая норма строкой журнала не оформлена (человек её получает в
    # момент создания аккаунта, до всякой истории), поэтому дельта на её
    # величину — это норма, а не дыра. 120 — прежнее стартовое значение,
    # с ним живут все, кто зарегистрировался до подъёма нормы FREE до 150.
    ok_offsets = {0, free_start, 120}
    sums = dict(db.query(PointEvent.user_id, func.coalesce(func.sum(PointEvent.delta), 0))
                .group_by(PointEvent.user_id).all())
    bad = []
    checked = 0
    for u in db.query(User).filter(User.is_admin.is_(False)).all():
        checked += 1
        journal = int(sums.get(u.id, 0))
        diff = int(u.gen_points or 0) - journal
        if diff in ok_offsets:
            continue
        bad.append({"id": u.id, "name": u.name or f"#{u.id}",
                    "points": int(u.gen_points or 0), "journal": journal,
                    "diff": diff})
    bad.sort(key=lambda r: -abs(r["diff"]))
    # checked — сколько человек РЕАЛЬНО проверено, а не сколько их в журнале
    # (было len(sums)). Разница не косметическая: у человека без единой
    # операции журнальной строки нет, он в sums не попадает — и отчёт выдавал
    # «проверено 2, расхождений 3», то есть больше дыр, чем проверок. Такому
    # отчёту не верят целиком, включая честный «расхождений 0».
    return {"free_start": free_start, "checked": checked,
            "mismatch": len(bad), "items": bad[:max(1, min(500, limit))]}


# ═══════════════════════════ СТИЛИ ═══════════════════════════
# Редактор каталога стилей. Каталог живёт в коде (backend/prompts_catalog.py),
# правки владельца — в базе (style_overrides / style_assets), а накладывает
# одно на другое main.reload_style_overlay().
#
# ТРИ ЗАМКА НА ЗАКРЫТОСТЬ ПРОМПТОВ, и ни один не требует внимательности:
#   1. prompt / story_base / structure отдаются ТОЛЬКО отсюда, из-под
#      admin_user. Публичные /api/styles* собирают ответ по белому списку
#      PUBLIC_STYLE_FIELDS — новое закрытое поле физически не может уехать
#      наружу, даже если про него забудут.
#   2. validate() гоняется ПЕРЕД записью, а не после: админ, вставивший
#      промпт в поле «описание», получает отказ, а не тихую утечку.
#   3. В журнал действий пишется ХЭШ И ДЛИНА промпта, а не текст: журнал не
#      должен становиться вторым хранилищем закрытых промптов.

STYLE_ASSET_KINDS = ("poster", "loop", "shot", "ref", "promptfile")


def _compress_ref_image(data: bytes, ext: str) -> tuple[bytes, str]:
    """Референс → jpeg: длинная сторона ≤1536, качество вниз, пока не влезет
    в ~800КБ. Не вышло сжать — возвращаем оригинал (пусть дорого, но живо)."""
    import subprocess, tempfile, os as _os
    src = tempfile.NamedTemporaryFile(suffix=ext, delete=False)
    dst_path = src.name + ".out.jpg"
    try:
        src.write(data)
        src.close()
        for q in (4, 7, 12):
            r = subprocess.run(
                ["ffmpeg", "-y", "-i", src.name, "-vf",
                 "scale='min(1536,iw)':'min(1536,ih)':"
                 "force_original_aspect_ratio=decrease:flags=lanczos",
                 "-q:v", str(q), dst_path],
                capture_output=True, timeout=60)
            if r.returncode == 0 and _os.path.exists(dst_path):
                out = open(dst_path, "rb").read()
                if len(out) <= 800 * 1024 or q == 12:
                    return out, ".jpg"
        return data, ext
    except Exception:  # noqa: BLE001
        return data, ext
    finally:
        for pth in (src.name, dst_path):
            try:
                _os.remove(pth)
            except OSError:
                pass
STYLE_ASSET_MAX = int(os.environ.get("STYLE_ASSET_MAX_MB", "40")) * 1024 * 1024
_STYLE_EXT = {"image/jpeg": ".jpg", "image/png": ".png", "image/webp": ".webp",
              "video/mp4": ".mp4", "text/plain": ".txt", "text/markdown": ".md"}


def _prompt_mark(text: str) -> dict:
    """Отпечаток промпта для журнала: длина и хэш. Текста здесь нет."""
    import hashlib  # noqa: PLC0415
    body = (text or "").encode("utf-8")
    return {"len": len(text or ""), "sha1": hashlib.sha1(body).hexdigest()[:12]}


def _style_row(db: Session, key: str) -> "StyleOverride | None":
    return db.get(StyleOverride, key)


def _asset_dict(core, a: StyleAsset) -> dict:
    return {"id": a.id, "kind": a.kind, "position": a.position,
            "url": f"/style-assets/{a.filename}", "filename": a.filename,
            "in_generation": bool(a.in_generation),
            "title": a.title or "", "note": a.note or ""}


# ─── Вид стиля: "style" (наш) или "reference" (авторский пресет по чужому
# ролику/инсте). Организация каталога админки, на генерацию не влияет.
# Живёт в AppSetting одним JSON — колонка в style_override потребовала бы
# миграции, а грабли DDL при деплое известны (docs: deploy-lock).
_STYLE_META_KEY = "style_meta"
_INSTA_RE = re.compile(r"instagram\.com|/reel|инстаграм|инста|reels", re.I)


def _style_meta(db: Session) -> dict:
    row = db.get(AppSetting, _STYLE_META_KEY)
    try:
        v = json.loads(row.value) if row and row.value else {}
        return v if isinstance(v, dict) else {}
    except Exception:  # noqa: BLE001
        return {}


def _style_meta_save(db: Session, meta: dict) -> None:
    row = db.get(AppSetting, _STYLE_META_KEY)
    if not row:
        row = AppSetting(key=_STYLE_META_KEY, value="{}")
        db.add(row)
    row.value = json.dumps(meta, ensure_ascii=False)
    db.commit()


#: Авторские пресеты: их снимали конкретные люди с узнаваемой манерой, и
#: каталог «Референсы» — это каталог АВТОРОВ, а не второй сорт стилей.
#: Перенос делается сидом, а не руками владельца: шесть кликов в админке —
#: это шесть шансов забыть один.
REFERENCE_AUTHORS = ("spike", "munir", "fanuel", "punkrf", "dreamclad", "katsumi")
_REF_SEED_MARK = "_authors_v1"

TRANSLATE_SYSTEM = (
    "Переведи промпт для image-генерации на английский. Сохрани все "
    "художественные детали, порядок и структуру. Верни ТОЛЬКО перевод, "
    "без пояснений, без кавычек и без заголовков."
)


async def translate_to_en(text: str) -> str:
    """Русский промпт → английский. Через бесплатный текстовый шлюз.

    Владелец думает и правит по-русски, в модель уходит английский: русский
    промпт картинку рисует заметно хуже, а держать в голове второй язык на
    каждую правку — способ перестать править вовсе."""
    import textgen  # noqa: PLC0415
    src = (text or "").strip()
    if not src:
        return ""
    out = await textgen.ask(src, TRANSLATE_SYSTEM)
    return (out or "").strip()


@router.post("/api/admin/translate")
async def admin_translate(request: Request, user: User = Depends(admin_user)):
    body = await request.json() if await request.body() else {}
    try:
        return {"en": await translate_to_en(str(body.get("text") or "")[:20000])}
    except Exception as e:  # noqa: BLE001
        raise HTTPException(502, f"перевод не вышел: {str(e)[:200]}") from e


def _ensure_reference_seed(db: Session, pc) -> None:
    """Один раз перевести авторские пресеты в вид «референс»."""
    meta = _style_meta(db)
    if meta.get(_REF_SEED_MARK):
        return
    for key in REFERENCE_AUTHORS:
        if key not in pc.STYLE_KEYS:
            continue
        cur = dict(meta.get(key) or {})
        cur["kind"] = "reference"
        meta[key] = cur
    meta[_REF_SEED_MARK] = True
    _style_meta_save(db, meta)


def _style_meta_of(db: Session, key: str) -> dict:
    v = _style_meta(db).get(key)
    return dict(v) if isinstance(v, dict) else {}


def _scene_captions(db: Session, key: str) -> list[str]:
    """Подписи кадров-референсов автора: «что происходит в кадре»."""
    rows = (db.query(StyleAsset)
            .filter(StyleAsset.style_key == key, StyleAsset.kind == "ref")
            .order_by(StyleAsset.position, StyleAsset.id).all())
    return [(a.note or "").strip() for a in rows if (a.note or "").strip()]


def _compose_story_base(db: Session, key: str, manual: str) -> str:
    """База для сценариев = заметки владельца + автоблок из подписей кадров.

    Автоблок собирается КАЖДЫЙ раз заново, а хранится только ручная часть:
    иначе размеченный кадр попадал бы в базу дважды, а удалённый оставался
    бы в ней навсегда."""
    caps = _scene_captions(db, key)
    body = (manual or "").strip()
    if not caps:
        return body
    auto = "Сцены из роликов автора:\n" + "\n".join("— " + c for c in caps)
    return (body + "\n\n" + auto).strip() if body else auto


def _restore_story_base(db: Session, key: str) -> None:
    """Пересобрать story_base стиля после правки подписи кадра."""
    core = _core()
    manual = _style_meta_of(db, key).get("story_manual", "")
    if not manual and not _scene_captions(db, key):
        return
    row = _style_row(db, key)
    if not row:
        row = StyleOverride(key=key, builtin=core.prompts_catalog.is_builtin(key))
        db.add(row)
    row.story_base = _compose_story_base(db, key, manual)
    db.commit()
    core.reload_style_overlay()


def _style_meta_defaults(db: Session, pc) -> dict:
    """Первый запуск: reference — стили, в чьих данных есть инста-ссылки.
    Остальное остаётся style; ручной переключатель в карточке."""
    meta = {}
    for key in pc.STYLE_KEYS:
        card = pc.public_style(key) or {}
        blob = json.dumps(card, ensure_ascii=False)
        blob += " " + (pc.style_prompt(key) or "")
        notes = " ".join((a.note or "") + " " + (a.title or "")
                         for a in db.query(StyleAsset)
                         .filter(StyleAsset.style_key == key).all())
        m = _INSTA_RE.search(blob + " " + notes)
        if m:
            meta[key] = {"kind": "reference"}
            url = re.search(r"https?://\S*instagram\.com\S*", blob + " " + notes)
            if url:
                meta[key]["source_url"] = url.group(0).rstrip('",)')
    _style_meta_save(db, meta)
    return meta


@router.post("/api/admin/styles/{key}/meta")
async def admin_style_meta(key: str, request: Request,
                           user: User = Depends(admin_user),
                           db: Session = Depends(db_session)):
    """Вид (style|reference) и ссылка на исходник. Каталог не трогает."""
    core = _core()
    if key not in core.prompts_catalog.STYLE_KEYS:
        raise HTTPException(404, "нет такого стиля")
    body = await request.json() if await request.body() else {}
    meta = _style_meta(db)
    cur = dict(meta.get(key) or {})
    if "kind" in body:
        want = str(body["kind"] or "style")
        if want not in ("style", "reference"):
            raise HTTPException(400, "kind: style|reference")
        cur["kind"] = want
    if "source_url" in body:
        cur["source_url"] = str(body["source_url"] or "")[:500]
    if "links" in body:
        # СПИСОК, а не одно поле: у автора инста, тикток и ютуб, и «одна
        # ссылка на исходник» заставляла бы выбирать, какую из трёх потерять.
        cur["links"] = [str(u).strip()[:500]
                        for u in (body["links"] or []) if str(u).strip()][:12]
    meta[key] = cur
    _style_meta_save(db, meta)
    _log_action(db, user, 0, "style_meta", {"key": key, **cur})
    return {"ok": True, "key": key, **cur}


@router.get("/api/admin/styles")
def admin_styles(user: User = Depends(admin_user), db: Session = Depends(db_session)):
    """Список стилей с пометкой «изменён». Промптов здесь нет — они едут
    только в карточке одного стиля, по явному запросу."""
    core = _core()
    pc = core.prompts_catalog
    uses = core._style_uses(db)
    overridden = set(pc.overlay_keys())
    meta = _style_meta(db)
    if not meta and not db.get(AppSetting, _STYLE_META_KEY):
        meta = _style_meta_defaults(db, pc)
    _ensure_reference_seed(db, pc)
    meta = _style_meta(db)
    assets: dict[str, int] = {}
    for (skey, cnt) in (db.query(StyleAsset.style_key, func.count(StyleAsset.id))
                        .group_by(StyleAsset.style_key).all()):
        assets[skey] = int(cnt)
    rows = []
    for key in pc.STYLE_KEYS:
        card = pc.public_style(key) or {}
        rows.append({
            "key": key,
            "label": card.get("label", {}),
            "group": card.get("group", ""),
            "tier": card.get("tier", ""),
            "prompt_class": card.get("prompt_class", ""),
            "builtin": pc.is_builtin(key),
            "overridden": key in overridden,
            "assets": assets.get(key, 0),
            "has_story_base": bool(pc.style_story_base(key)),
            "uses": uses.get(key, 0),
            "skind": (meta.get(key) or {}).get("kind", "style"),
            "source_url": (meta.get(key) or {}).get("source_url", ""),
        })
    return {
        "styles": rows,
        "groups": [{"key": g["key"], "label": g["label"]} for g in pc.GROUPS],
        # Словарь тегов контролируемый: свободные теги через полгода дают
        # три разных фильтра на одно и то же понятие.
        "tags": {axis: [{"key": k, "label": v} for k, v in vals.items()]
                 for axis, vals in pc.TAGS.items()},
        "problems": pc.validate(),
    }


@router.get("/api/admin/styles/{key}")
def admin_style_card(key: str, user: User = Depends(admin_user),
                     db: Session = Depends(db_session)):
    """Полная карточка ОДНОГО стиля, включая закрытый промпт. Только админу
    и только по явному запросу."""
    core = _core()
    pc = core.prompts_catalog
    card = pc.public_style(key)
    if not card:
        raise HTTPException(404, "нет такого стиля")
    row = _style_row(db, key)
    assets = (db.query(StyleAsset).filter(StyleAsset.style_key == key)
              .order_by(StyleAsset.kind, StyleAsset.position, StyleAsset.id).all())
    return {
        "key": key,
        "builtin": pc.is_builtin(key),
        "overridden": bool(row),
        "enabled": bool(row.enabled) if row else True,
        "card": card,
        # Закрытое. Наружу этих трёх полей нет ни в одном публичном роуте.
        "prompt": pc.style_prompt(key),
        "story_base": pc.style_story_base(key),
        "structure": pc.style_structure_raw(key) or {},
        # Заводские значения — для diff'а и кнопки «вернуть как было».
        "builtin_prompt": pc.builtin_prompt(key),
        "builtin_card": pc.builtin_style(key) or {},
        "assets": [_asset_dict(core, a) for a in assets],
        "asset_kinds": list(STYLE_ASSET_KINDS),
        "skind": (_style_meta(db).get(key) or {}).get("kind", "style"),
        "source_url": (_style_meta(db).get(key) or {}).get("source_url", ""),
        # Русский исходник промпта и ссылки автора живут в style_meta:
        # колонка в style_overrides потребовала бы миграции, а грабли DDL
        # при деплое известны (docs: deploy-lock).
        "prompt_ru": _style_meta_of(db, key).get("prompt_ru", ""),
        "links": list(_style_meta_of(db, key).get("links") or []),
        # База для сценариев показана двумя кусками: ручной (её и правят) и
        # автоблок из подписей кадров (он только читается).
        "story_manual": _style_meta_of(db, key).get("story_manual", ""),
        "story_auto": _scene_captions(db, key),
    }


_STYLE_TEXT_FIELDS = ("group", "tier", "prompt_class", "mix_role",
                      "prompt", "story_base")
_STYLE_DICT_FIELDS = (("label", "label_json"), ("desc", "desc_json"),
                      ("gain", "gain_json"), ("music", "music_json"),
                      ("tempo", "tempo_json"), ("structure", "structure_json"))
_STYLE_LIST_FIELDS = (("tags", "tags_json"), ("mix_with", "mix_with_json"),
                      ("avoid_mix", "avoid_mix_json"), ("engines", "engines_json"))


@router.put("/api/admin/styles/{key}")
async def admin_style_save(key: str, request: Request,
                           user: User = Depends(admin_user),
                           db: Session = Depends(db_session)):
    """Сохранить наложение. ПРОВЕРКА ИДЁТ ДО ЗАПИСИ.

    Порядок именно такой: пишем в сессию flush'ем (без commit), собираем
    кандидата, накладываем, гоняем validate(). Ошибка — откат и 400.
    Обратный порядок означал бы, что битый или протекающий каталог успевает
    побывать правдой для витрины."""
    core = _core()
    pc = core.prompts_catalog
    body = await request.json() if await request.body() else {}
    # РУССКИЙ ИСХОДНИК И АВТОПЕРЕВОД. В модель уходит английский prompt —
    # это не обсуждается, русский промпт рисует заметно хуже. Но правит
    # владелец по-русски, и когда он поменял русский текст, а английский не
    # трогал, переводим сами: иначе поле «промпт» и то, что реально уехало
    # в генерацию, разъезжаются молча.
    meta_patch: dict = {}
    if "prompt_ru" in body:
        meta_patch["prompt_ru"] = str(body["prompt_ru"] or "")[:20000]
    if body.get("translate") and meta_patch.get("prompt_ru"):
        try:
            body["prompt"] = await translate_to_en(meta_patch["prompt_ru"])
        except Exception as e:  # noqa: BLE001
            raise HTTPException(502, f"перевод не вышел: {str(e)[:200]}") from e
    # База для сценариев: правится ручная часть, хранится склейка с
    # автоблоком подписей кадров.
    if "story_manual" in body:
        manual = str(body["story_manual"] or "")[:20000]
        meta_patch["story_manual"] = manual
        body["story_base"] = _compose_story_base(db, key, manual)
    if meta_patch:
        meta = _style_meta(db)
        cur = dict(meta.get(key) or {})
        cur.update(meta_patch)
        meta[key] = cur
        _style_meta_save(db, meta)
    row = _style_row(db, key)
    if not row:
        row = StyleOverride(key=key, builtin=pc.is_builtin(key))
        db.add(row)
    if "enabled" in body:
        row.enabled = bool(body["enabled"])
    for field in _STYLE_TEXT_FIELDS:
        if field in body:
            setattr(row, field, str(body[field] or "")[:20000])
    for field, col in _STYLE_DICT_FIELDS:
        if field in body:
            val = body[field]
            setattr(row, col, json.dumps(val, ensure_ascii=False)
                    if isinstance(val, dict) and val else "")
    for field, col in _STYLE_LIST_FIELDS:
        if field in body:
            val = body[field]
            setattr(row, col, json.dumps(val, ensure_ascii=False)
                    if isinstance(val, list) and val else "")
    row.updated_by = user.id
    db.flush()

    candidate = core.style_overlay_data(db)
    pc.set_overlay(candidate)
    problems = pc.validate()
    if problems:
        db.rollback()
        core.reload_style_overlay()
        raise HTTPException(400, "каталог не сходится: " + "; ".join(problems[:4]))
    db.commit()
    core.reload_style_overlay()
    _log_action(db, user, 0, "style", {
        "key": key,
        # ХЭШ И ДЛИНА, НЕ ТЕКСТ: журнал не должен становиться вторым
        # хранилищем закрытых промптов.
        "prompt": _prompt_mark(row.prompt),
        "story_base": _prompt_mark(row.story_base),
        "enabled": bool(row.enabled),
    })
    return admin_style_card(key, user=user, db=db)


@router.delete("/api/admin/styles/{key}")
def admin_style_reset(key: str, user: User = Depends(admin_user),
                      db: Session = Depends(db_session)):
    """Снять наложение — стиль возвращается к заводскому. Именно снять, а не
    записать поверх копию заводского: копия через полгода разойдётся с
    кодом, и «вернуть как было» перестанет работать."""
    core = _core()
    row = _style_row(db, key)
    if not row:
        raise HTTPException(404, "у этого стиля нет правок")
    if not row.builtin:
        raise HTTPException(400, "это свой стиль, а не правка встроенного — "
                                 "его можно только выключить")
    db.delete(row)
    db.commit()
    core.reload_style_overlay()
    _log_action(db, user, 0, "style_reset", {"key": key})
    return {"ok": True, "key": key}


@router.post("/api/admin/styles/{key}/assets")
async def admin_style_asset_add(key: str, request: Request,
                                user: User = Depends(admin_user),
                                db: Session = Depends(db_session)):
    """Залить файл стиля: постер, пример кадра, референс в генерацию или
    текстовый файл с промптом.

    Файл ложится в том /data (STYLE_ASSETS_DIR), а не в образ: его кладёт
    владелец, и переживать пересборку он обязан."""
    core = _core()
    if key not in core.prompts_catalog.STYLE_KEYS:
        raise HTTPException(404, "нет такого стиля")
    form = await request.form()
    upload = form.get("file")
    if upload is None or not hasattr(upload, "read"):
        raise HTTPException(400, "нет файла")
    kind = str(form.get("kind") or "shot")
    if kind not in STYLE_ASSET_KINDS:
        raise HTTPException(400, f"неизвестный вид файла: {kind}")
    data = await upload.read()
    if not data:
        raise HTTPException(400, "пустой файл")
    if len(data) > STYLE_ASSET_MAX:
        raise HTTPException(413, f"файл больше {STYLE_ASSET_MAX // 1024 // 1024} МБ")
    ext = (_STYLE_EXT.get(getattr(upload, "content_type", "") or "")
           or os.path.splitext(getattr(upload, "filename", "") or "")[1].lower()
           or ".bin")
    if ext not in (".jpg", ".jpeg", ".png", ".webp", ".mp4", ".txt", ".md"):
        raise HTTPException(400, f"такой формат не берём: {ext}")
    # Референсы в генерацию жмём НА СЕРВЕРЕ: длинная сторона до 1536,
    # jpeg (kie не ест webp), потолок ~800КБ. Pillow в образе нет — ffmpeg,
    # он тут и так главный обработчик картинок (см. mediagen).
    if kind == "ref" and ext in (".jpg", ".jpeg", ".png", ".webp"):
        data, ext = _compress_ref_image(data, ext)
    fname = f"style_{key}_{int(time.time())}_{os.urandom(4).hex()}{ext}"
    path = os.path.join(core.STYLE_ASSETS_DIR, fname)
    with open(path, "wb") as f:
        f.write(data)
    pos = (db.query(func.coalesce(func.max(StyleAsset.position), 0))
           .filter(StyleAsset.style_key == key, StyleAsset.kind == kind).scalar() or 0)
    # Постер и петля у стиля ровно одни: заливка новых заменяет старые,
    # иначе витрина молча показывает первый попавшийся.
    if kind in ("poster", "loop"):
        for old in db.query(StyleAsset).filter(StyleAsset.style_key == key,
                                               StyleAsset.kind == kind).all():
            core._remove_style_asset(old.filename)
            db.delete(old)
    asset = StyleAsset(style_key=key, kind=kind, filename=fname, position=pos + 1,
                       in_generation=bool(str(form.get("in_generation") or "") in ("1", "true", "on")),
                       title=str(form.get("title") or "")[:200])
    db.add(asset)
    db.commit()
    core.reload_style_overlay()
    _log_action(db, user, 0, "style_asset", {"key": key, "kind": kind,
                                             "bytes": len(data)})
    return _asset_dict(core, asset)


@router.patch("/api/admin/styles/assets/{asset_id}")
async def admin_style_asset_patch(asset_id: int, request: Request,
                                  user: User = Depends(admin_user),
                                  db: Session = Depends(db_session)):
    """Тумблер «в генерацию» и подпись. Всё остальное у файла неизменно."""
    core = _core()
    asset = db.get(StyleAsset, asset_id)
    if not asset:
        raise HTTPException(404, "файл не найден")
    body = await request.json() if await request.body() else {}
    if "in_generation" in body:
        asset.in_generation = bool(body["in_generation"])
    if "title" in body:
        asset.title = str(body["title"] or "")[:200]
    if "position" in body:
        asset.position = max(0, int(body["position"] or 0))
    note_changed = "note" in body
    if note_changed:
        # «Что происходит в кадре»: кто, что делает, где, каким приёмом.
        # Из этих подписей сама собирается база для сценариев автора.
        asset.note = str(body["note"] or "")[:2000]
    db.commit()
    core.reload_style_overlay()
    if note_changed:
        _restore_story_base(db, asset.style_key)
    return _asset_dict(core, asset)


@router.post("/api/admin/styles/{key}/assets/order")
async def admin_style_asset_order(key: str, request: Request,
                                  user: User = Depends(admin_user),
                                  db: Session = Depends(db_session)):
    core = _core()
    body = await request.json() if await request.body() else {}
    ids = [int(x) for x in (body.get("ids") or []) if str(x).isdigit()]
    rows = {a.id: a for a in db.query(StyleAsset).filter(StyleAsset.style_key == key).all()}
    for i, aid in enumerate(ids, start=1):
        if aid in rows:
            rows[aid].position = i
    db.commit()
    core.reload_style_overlay()
    return {"ok": True, "order": ids}


@router.delete("/api/admin/styles/assets/{asset_id}")
def admin_style_asset_del(asset_id: int, user: User = Depends(admin_user),
                          db: Session = Depends(db_session)):
    core = _core()
    asset = db.get(StyleAsset, asset_id)
    if not asset:
        raise HTTPException(404, "файл не найден")
    core._remove_style_asset(asset.filename)
    key = asset.style_key
    db.delete(asset)
    db.commit()
    core.reload_style_overlay()
    _log_action(db, user, 0, "style_asset_del", {"key": key, "id": asset_id})
    return {"ok": True}


@router.get("/api/admin/styles/assets/{asset_id}/text")
def admin_style_asset_text(asset_id: int, user: User = Depends(admin_user),
                           db: Session = Depends(db_session)):
    """Прочитать текстовый файл с промптом, чтобы перенести его в поле.

    В генерацию уходит ПОЛЕ, а не файл. Второе место правды означало бы
    вечный вопрос «а что реально ушло в модель» — поэтому файл здесь только
    источник, из которого человек копирует осознанным нажатием."""
    core = _core()
    asset = db.get(StyleAsset, asset_id)
    if not asset or asset.kind != "promptfile":
        raise HTTPException(404, "это не файл с промптом")
    path = os.path.join(core.STYLE_ASSETS_DIR, asset.filename)
    if not os.path.exists(path):
        raise HTTPException(404, "файл потерян")
    with open(path, encoding="utf-8", errors="replace") as f:
        return {"text": f.read(200000), "filename": asset.filename}


# ═══════════════════ СЛОИ ПРОМТОВ И ШАБЛОНЫ МОКАПОВ ═══════════════════
#
# Одна страница админки «Промты» собирает пять вкладок: слои (этот блок),
# стили и референсы (блок выше), тренды (/api/admin/trends) и шаблоны
# мокапов (низ этого блока). Отдельных страниц у них больше нет — два входа
# в один каталог означали бы два разных представления о том, что сохранено.
#
# ХРАНЕНИЕ. Правки лежат наложением в app_settings, файл остаётся источником
# и живёт в git. Поэтому DELETE у заводской карточки не удаляет её, а СНИМАЕТ
# правку: удалить строку кода веб-формой нельзя и не нужно.


def _prompts_overlay(db: Session) -> dict:
    core = _core()
    data = core._overlay_setting(db, core.PROMPTS_OVERLAY_KEY)
    return {l: dict(data.get(l) or {}) for l in prompts_library.LAYERS}


def _layer_or_404(layer: str) -> str:
    if layer not in prompts_library.LAYERS:
        raise HTTPException(404, "нет такого слоя промтов")
    return layer


@router.get("/api/admin/prompts")
def admin_prompt_layers(user: User = Depends(admin_user)):
    """Оглавление вкладки: слои, их названия и сколько в каждом карточек."""
    return {"layers": [{
        "key": l, "title": prompts_library.LAYER_TITLES[l],
        "count": len(prompts_library.layer_rows(l)),
        "groups": [{"key": g["key"], "label": g["label"]["ru"]}
                   for g in prompts_library.layer_groups(l)],
    } for l in prompts_library.LAYERS]}


@router.get("/api/admin/prompts/{layer}")
def admin_prompt_list(layer: str, user: User = Depends(admin_user)):
    items = prompts_library.layer_list(layer)
    # Превью карточек (камера и будущие пачки): общий магазин "layer:key".
    core = _core()
    previews = core._layer_previews()
    for it in items:
        main, _g = core._preview_entry(previews.get(f"{layer}:{it['key']}"))
        it["preview_url"] = f"/api/media/{main}" if main else ""
    return {"layer": _layer_or_404(layer),
            "title": prompts_library.LAYER_TITLES[layer],
            "items": items}


@router.get("/api/admin/prompts/{layer}/{key}")
def admin_prompt_card(layer: str, key: str, user: User = Depends(admin_user)):
    card = prompts_library.layer_card(_layer_or_404(layer), key)
    if not card:
        raise HTTPException(404, "нет такой карточки")
    ov = (prompts_library.library_overlay().get(layer) or {}).get(key) or {}
    card["ru"] = {f: ov.get(f + "_ru", "")
                  for f in prompts_library.PROMPT_FIELDS[layer]}
    core = _core()
    main, gallery = core._preview_entry(core._layer_previews().get(f"{layer}:{key}"))
    card["preview_url"] = f"/api/media/{main}" if main else ""
    card["preview_gallery"] = [{"filename": g, "url": f"/api/media/{g}"} for g in gallery]
    return card


def _clean_patch(layer: str, body: dict) -> dict:
    """Отфильтровать присланное по белому списку EDITABLE.

    Белый список, а не «всё, что прислали»: поле `acts` или `fits_with`,
    прилетевшее из браузера, увело бы каталог в состояние, которое чинится
    только руками в базе.

    Плюс русские исходники промптов (`<поле>_ru`): они хранятся в наложении
    рядом, но в каталог НЕ попадают — в модель уходит английский текст."""
    fields = prompts_library.EDITABLE[layer]
    out: dict = {}
    for f in prompts_library.PROMPT_FIELDS[layer]:
        if f + "_ru" in body:
            out[f + "_ru"] = str(body[f + "_ru"] or "")[:8000]
    for f in fields:
        if f not in body:
            continue
        v = body[f]
        if f in prompts_library.BILINGUAL:
            if not isinstance(v, dict):
                continue
            out[f] = {"en": str(v.get("en") or "")[:4000],
                      "ru": str(v.get("ru") or "")[:4000]}
        elif isinstance(v, bool):
            out[f] = v
        else:
            out[f] = str(v or "")[:8000]
    return out


@router.put("/api/admin/prompts/{layer}/{key}")
async def admin_prompt_save(layer: str, key: str, request: Request,
                            user: User = Depends(admin_user),
                            db: Session = Depends(db_session)):
    core = _core()
    _layer_or_404(layer)
    key = re.sub(r"[^a-z0-9_]", "", (key or "").strip().lower())[:60]
    if not key:
        raise HTTPException(400, "ключ только из латиницы, цифр и подчёркиваний")
    body = await request.json()
    patch = _clean_patch(layer, body)
    # Автоперевод: поля из `translate` пересобираются из русского исходника.
    for f in (body.get("translate") or []):
        if f in prompts_library.PROMPT_FIELDS[layer] and patch.get(f + "_ru"):
            try:
                patch[f] = await translate_to_en(patch[f + "_ru"])
            except Exception as e:  # noqa: BLE001
                raise HTTPException(502, f"перевод не вышел: {str(e)[:200]}") from e
    data = _prompts_overlay(db)
    row = dict(data[layer].get(key) or {})
    row.update(patch)
    row.pop("enabled", None)          # сохранение всегда возвращает в строй
    data[layer][key] = row
    core._overlay_setting_save(db, core.PROMPTS_OVERLAY_KEY, data)
    core.reload_prompts_overlay(db)
    _log_action(db, user, user.id, "prompt_save", {"layer": layer, "key": key})
    return {"ok": True, "card": prompts_library.layer_card(layer, key)}


@router.delete("/api/admin/prompts/{layer}/{key}")
def admin_prompt_delete(layer: str, key: str, hide: int = 0,
                        user: User = Depends(admin_user),
                        db: Session = Depends(db_session)):
    """Заводская карточка: снять правки (hide=1 — убрать её с витрины).
    Своя карточка: удалить совсем."""
    core = _core()
    _layer_or_404(layer)
    data = _prompts_overlay(db)
    builtin = key in prompts_library._BUILTIN_BY_KEY_LAYER[layer]
    if builtin and hide:
        data[layer][key] = {"enabled": False}
    else:
        data[layer].pop(key, None)
    core._overlay_setting_save(db, core.PROMPTS_OVERLAY_KEY, data)
    core.reload_prompts_overlay(db)
    _log_action(db, user, user.id, "prompt_delete",
                {"layer": layer, "key": key, "hide": bool(hide)})
    return {"ok": True}


# ─────────────────────────── шаблоны мокапов ───────────────────────────

@router.get("/api/admin/mockups")
def admin_mockups(user: User = Depends(admin_user)):
    core = _core()
    previews = core._mockup_previews()
    items = mockup_catalog.admin_list()
    ov = mockup_catalog.overlay()
    for it in items:
        fname = previews.get(it["id"]) or ""
        it["preview_url"] = f"/api/media/{fname}" if fname else ""
        it["prompt_ru"] = (ov.get(it["id"]) or {}).get("prompt_ru", "")
    return {"items": items, "categories": list(mockup_catalog.CATEGORIES)}


@router.put("/api/admin/mockups/{tid}")
async def admin_mockup_save(tid: str, request: Request,
                            user: User = Depends(admin_user),
                            db: Session = Depends(db_session)):
    core = _core()
    tid = re.sub(r"[^a-z0-9_]", "", (tid or "").strip().lower())[:60]
    if not tid:
        raise HTTPException(400, "ключ только из латиницы, цифр и подчёркиваний")
    body = await request.json()
    patch: dict = {}
    if "prompt_ru" in body:
        patch["prompt_ru"] = str(body["prompt_ru"] or "")[:8000]
    if body.get("translate") and patch.get("prompt_ru"):
        try:
            body["prompt"] = await translate_to_en(patch["prompt_ru"])
        except Exception as e:  # noqa: BLE001
            raise HTTPException(502, f"перевод не вышел: {str(e)[:200]}") from e
    for f in mockup_catalog.EDITABLE:
        if f not in body:
            continue
        v = body[f]
        if f in ("motion", "showcase"):
            patch[f] = bool(v)
        elif f == "category":
            cat = str(v or "").strip()
            if cat and cat not in mockup_catalog.CATEGORIES:
                raise HTTPException(400, f"неизвестная категория: {cat}")
            patch[f] = cat
        else:
            patch[f] = str(v or "")[:8000]
    data = core._overlay_setting(db, core.MOCKUP_OVERLAY_KEY)
    row = dict(data.get(tid) or {})
    row.update(patch)
    row.pop("enabled", None)
    data[tid] = row
    core._overlay_setting_save(db, core.MOCKUP_OVERLAY_KEY, data)
    core.reload_mockup_overlay(db)
    _log_action(db, user, user.id, "mockup_save", {"id": tid})
    return {"ok": True}


@router.delete("/api/admin/mockups/{tid}")
def admin_mockup_delete(tid: str, hide: int = 0,
                        user: User = Depends(admin_user),
                        db: Session = Depends(db_session)):
    core = _core()
    data = core._overlay_setting(db, core.MOCKUP_OVERLAY_KEY)
    if mockup_catalog.is_builtin(tid) and hide:
        data[tid] = {"enabled": False}
    else:
        data.pop(tid, None)
    core._overlay_setting_save(db, core.MOCKUP_OVERLAY_KEY, data)
    core.reload_mockup_overlay(db)
    _log_action(db, user, user.id, "mockup_delete", {"id": tid, "hide": bool(hide)})
    return {"ok": True}


@router.post("/api/admin/mockups/{tid}/preview")
def admin_mockup_preview(tid: str, user: User = Depends(admin_user)):
    """Перегенерировать превью одного шаблона нейтральной бутылкой.

    Старое превью снимается ДО генерации: иначе generate_mockup_previews()
    увидит уже заполненный ключ и честно ничего не сделает."""
    core = _core()
    if not mockup_catalog.get(tid):
        raise HTTPException(404, "нет такого шаблона")
    previews = core._mockup_previews()
    previews.pop(tid, None)
    core._mockup_previews_save(previews)
    res = core.generate_mockup_previews([tid])
    if res.get("failed"):
        raise HTTPException(502, "; ".join(res["failed"])[:300])
    return {"ok": True, "preview_url": f"/api/media/{core._mockup_previews().get(tid, '')}"}


# ═══════════════════════ МОДЕЛИ И НАСТРОЙКИ СЕРВИСА ═══════════════════════

@router.get("/api/admin/models")
def admin_models(user: User = Depends(admin_user), db: Session = Depends(db_session)):
    """Витрина правды: что живо, сколько стоит нам и сколько человеку.
    Только чтение — движки правятся кодом и ключами, а не веб-формой."""
    core = _core()
    off = core._disabled_models(db)
    # Статистика по каждому движку: сколько раз списывали и на сколько токенов.
    # Группируем в один проход журнала — по строкам списаний с полем engine.
    from sqlalchemy import func as _f
    stats = {}
    try:
        rows = (db.query(core.PointEvent.engine,
                         _f.count(core.PointEvent.id),
                         _f.coalesce(_f.sum(-core.PointEvent.delta), 0))
                .filter(core.PointEvent.delta < 0, core.PointEvent.engine != "")
                .group_by(core.PointEvent.engine).all())
        stats = {e: {"uses": int(n), "earned": int(pts)} for e, n, pts in rows}
    except Exception as e:  # noqa: BLE001 — статистика не должна ронять список
        log.warning("статистика движков не собралась: %s", str(e)[:120])

    def _st(eid):
        d = stats.get(eid, {})
        return {"uses": d.get("uses", 0), "earned": d.get("earned", 0)}

    text = []
    for row in core.textgen.public_engines("studio", admin=True):
        row["points"] = core.TEXT_COST.get(row["id"], 0)
        row["enabled"] = f"text:{row['id']}" not in off
        row["toggle_id"] = f"text:{row['id']}"
        row.update(_st(row["id"]))
        text.append(row)
    images = [{
        "id": eid, "title": spec["title"], "channel": spec["channel"],
        "live": eid in core.mediagen.image_engines_live(),
        "points": core.FRAME_COST.get(eid, 0),
        "usd": round(core.mediagen.image_engine_usd(eid), 4),
        "enabled": f"image:{eid}" not in off, "toggle_id": f"image:{eid}",
        **_st(eid),
    } for eid, spec in core.mediagen.IMAGE_ENGINES.items()]
    videos = [{
        "id": eid, "title": spec["title"], "family": spec["family"],
        "live": core.mediagen.video_engine_live(eid),
        "points": core.VIDEO_COST.get(eid, 0),
        "usd": round(core.mediagen.video_engine_usd(eid, core.SCENE_SEC), 4),
        "enabled": f"video:{eid}" not in off, "toggle_id": f"video:{eid}",
        **_st(eid),
    } for eid, spec in core.mediagen.VIDEO_ENGINES.items()]
    return {"text": text, "images": images, "videos": videos,
            "point_usd": core.POINT_USD}


@router.get("/api/admin/settings")
def admin_settings(user: User = Depends(admin_user)):
    """ТОЛЬКО ИНДИКАЦИЯ. Значений ключей здесь нет и не будет: редактор
    ключей в вебе — это способ увести их одним XSS. Ключи живут в
    infra/.env, здесь видно лишь «задан / не задан» и жив ли канал."""
    core = _core()
    st = core.textgen.state()
    return {
        "keys": [
            {"key": "ANTHROPIC_API_KEY", "set": st["anthropic_key"],
             "note": "Claude Sonnet и Opus в блоке сценария"},
            {"key": "anthropic SDK", "set": st["anthropic_sdk"],
             "note": "пакет anthropic в образе (requirements.txt)"},
            {"key": "OPENROUTER_API_KEY", "set": st["openrouter_key"],
             "note": "запасной канал Claude"},
            {"key": "KIE_API_KEY", "set": core.mediagen.kie_available(),
             "note": "Nano Banana, Seedance, Kling"},
            {"key": "SEEVIO_API_KEY", "set": bool(core.mediagen.SEEVIO_API_KEY),
             "note": "аварийный канал Seedance"},
            {"key": "YOOKASSA_SECRET_KEY", "set": core._yookassa_enabled(),
             "note": "оплата в рублях"},
            {"key": "STRIPE", "set": core._stripe_enabled(),
             "note": "оплата в долларах"},
        ],
        "egress_proxy": st["egress_proxy"],
        "gateway_url": st["gateway_url"],
        "text_default": st["default"],
        "style_assets_dir": core.STYLE_ASSETS_DIR,
        "scene_versions_keep": core.SCENE_VERSIONS_KEEP,
    }


# ═══════════════════════════ СЕГМЕНТЫ ═══════════════════════════
# Только по данным, которые ТОЧНО есть в базе. Сегмент, который нельзя
# посчитать, — это обещание, которое нельзя выполнить.

SEGMENTS = {
    "all": "все зарегистрированные",
    "new_no_gen": "новички за 7 дней без единой генерации",
    "free_active": "на FREE, потратили больше 100 токенов",
    "low_points": "токены на исходе (меньше 15 % нормы тарифа)",
    "expiring": "подписка кончается в ближайшие 5 дней без автопродления",
    "sleeping": "не заходили 30 дней, но что-то генерили",
    "made_clip": "дошли до собранного клипа",
    "ambassadors": "амбассадоры партнёрки",
    "paying": "на платном тарифе",
}


def _segment_ids(core, db: Session, segment: str) -> list[int]:
    """Кто попадает в сегмент ПРЯМО СЕЙЧАС. Считается в момент отправки, а не
    при создании рассылки: список недельной давности протухает."""
    q = db.query(User).filter(User.is_admin.is_(False), User.is_blocked.is_(False))
    if segment == "paying":
        return [u.id for u in q.filter(User.plan != "free").all()]
    if segment == "ambassadors":
        return [u.id for u in q.filter(User.is_ambassador.is_(True)).all()]
    if segment == "expiring":
        return [u.id for u in q.filter(
            User.plan != "free", User.autopay.is_(False),
            User.plan_until.isnot(None),
            User.plan_until <= now() + timedelta(days=5),
            User.plan_until >= now()).all()]
    if segment == "new_no_gen":
        fresh = q.filter(User.created_at >= now() - timedelta(days=7)).all()
        spent = {r[0] for r in db.query(PointEvent.user_id)
                 .filter(PointEvent.delta < 0).distinct().all()}
        return [u.id for u in fresh if u.id not in spent]
    if segment == "sleeping":
        spent = {r[0] for r in db.query(PointEvent.user_id)
                 .filter(PointEvent.delta < 0).distinct().all()}
        rows = q.filter(or_(User.last_seen_at.is_(None),
                            User.last_seen_at < now() - timedelta(days=30))).all()
        return [u.id for u in rows if u.id in spent]
    if segment == "free_active":
        spent = dict(db.query(PointEvent.user_id,
                              func.coalesce(func.sum(-PointEvent.delta), 0))
                     .filter(PointEvent.delta < 0)
                     .group_by(PointEvent.user_id).all())
        return [u.id for u in q.filter(User.plan == "free").all()
                if int(spent.get(u.id, 0)) > 100]
    if segment == "low_points":
        out = []
        for u in q.all():
            norm = core._plan_points(core._plan_of(u), core._tier_of_user(u))
            if norm and int(u.gen_points or 0) < norm * 0.15:
                out.append(u.id)
        return out
    if segment == "made_clip":
        owners = {p.owner_id for p in db.query(Project)
                  .join(Track, Track.project_id == Project.id)
                  .filter(Track.clip_filename != "").all() if p.owner_id}
        return [u.id for u in q.all() if u.id in owners]
    return [u.id for u in q.all()]


@router.get("/api/admin/segments")
def admin_segments(user: User = Depends(admin_user), db: Session = Depends(db_session)):
    """Сегменты с ЖИВЫМИ числами и с тем, скольким из них мы физически можем
    написать по каждому каналу. Без второго числа «сегмент 900 человек»
    ничего не значит: почты может не быть ни у кого."""
    core = _core()
    channels = mailer.state()
    out = []
    for key, title in SEGMENTS.items():
        ids = _segment_ids(core, db, key)
        if ids:
            users = db.query(User).filter(User.id.in_(ids)).all()
        else:
            users = []
        out.append({
            "id": key, "title": title, "count": len(ids),
            "reach": {
                "inapp": len(ids),
                "tg": sum(1 for u in users if u.tg_id and not u.unsub_tg and not u.unsub_all),
                "email": sum(1 for u in users
                             if u.email and not u.unsub_email and not u.unsub_all),
            },
        })
    return {"segments": out, "channels": channels}


# ═══════════════════════════ РАССЫЛКИ ═══════════════════════════

def _campaign_dict(core, c: Campaign) -> dict:
    return {"id": c.id, "title": c.title, "channel": c.channel,
            "segment": c.segment, "subject": c.subject, "body": c.body,
            "transactional": bool(c.transactional),
            "status": c.status, "error": c.error or "",
            "total": c.total, "sent": c.sent, "failed": c.failed, "read": c.read,
            "at": core._as_utc(c.created_at).isoformat() if c.created_at else "",
            "finished_at": (core._as_utc(c.finished_at).isoformat()
                            if c.finished_at else "")}


@router.get("/api/admin/campaigns")
def list_campaigns(user: User = Depends(admin_user), db: Session = Depends(db_session)):
    core = _core()
    rows = db.query(Campaign).order_by(Campaign.id.desc()).limit(100).all()
    return {"items": [_campaign_dict(core, c) for c in rows],
            "channels": mailer.state(),
            "segments": [{"id": k, "title": v} for k, v in SEGMENTS.items()]}


@router.post("/api/admin/campaigns")
async def create_campaign(request: Request, user: User = Depends(admin_user),
                          db: Session = Depends(db_session)):
    core = _core()
    body = await request.json()
    channel = str(body.get("channel") or "inapp")
    if channel not in mailer.CHANNELS:
        raise HTTPException(400, f"канал: {', '.join(mailer.CHANNELS)}")
    segment = str(body.get("segment") or "all")
    if segment not in SEGMENTS:
        raise HTTPException(400, "неизвестный сегмент")
    text = str(body.get("body") or "").strip()
    if not text:
        raise HTTPException(400, "пустое письмо отправлять нечего")
    c = Campaign(
        title=str(body.get("title") or "").strip()[:120] or "без названия",
        channel=channel, segment=segment,
        subject=str(body.get("subject") or "").strip()[:200],
        body=text[:8000], transactional=bool(body.get("transactional")),
        created_by=user.id, status="draft")
    db.add(c)
    db.commit()
    db.refresh(c)
    _log_action(db, user, 0, "campaign", {"id": c.id, "channel": channel,
                                          "segment": segment})
    return _campaign_dict(core, c)


@router.get("/api/admin/campaigns/{cid}")
def campaign_card(cid: int, user: User = Depends(admin_user),
                  db: Session = Depends(db_session)):
    core = _core()
    c = db.get(Campaign, cid)
    if not c:
        raise HTTPException(404, "рассылка не найдена")
    rows = (db.query(CampaignRecipient)
            .filter(CampaignRecipient.campaign_id == cid)
            .order_by(CampaignRecipient.id.desc()).limit(200).all())
    ids = {r.user_id for r in rows}
    names = {u.id: (u.name or f"#{u.id}")
             for u in db.query(User).filter(User.id.in_(ids)).all()} if ids else {}
    return {**_campaign_dict(core, c), "recipients": [{
        "user_id": r.user_id, "name": names.get(r.user_id) or f"#{r.user_id}",
        "address": r.address, "status": r.status, "error": r.error or "",
        "sent_at": core._as_utc(r.sent_at).isoformat() if r.sent_at else "",
        "read_at": core._as_utc(r.read_at).isoformat() if r.read_at else "",
    } for r in rows]}


@router.post("/api/admin/campaigns/{cid}/send")
def send_campaign(cid: int, test: int = 0, user: User = Depends(admin_user),
                  db: Session = Depends(db_session)):
    """Отправка. test=1 — только себе, чтобы посмотреть, как выглядит.

    Канал без транспорта отвечает 400 с ТЕКСТОМ, чего не хватает, а не
    делает вид, что отправил."""
    core = _core()
    c = db.get(Campaign, cid)
    if not c:
        raise HTTPException(404, "рассылка не найдена")
    if c.status == "sending":
        raise HTTPException(409, "эта рассылка уже отправляется")
    st = mailer.state().get(c.channel) or {}
    if not st.get("enabled"):
        # missing и missing_alt — два РАВНОПРАВНЫХ пути (Resend или SMTP).
        # Фразу строит интерфейс по этим спискам; здесь текст на случай, когда
        # ответ читают глазами в логе или из curl.
        need = ", ".join(st.get("missing") or ["настроек"])
        alt = " + ".join(st.get("missing_alt") or [])
        raise core.ApiError(400, "channel_off",
                            f"канал не подключён: не хватает {need}"
                            + (f", или {alt}" if alt else ""),
                            channel=c.channel,
                            missing=st.get("missing") or [],
                            missing_alt=st.get("missing_alt") or [])
    ids = [user.id] if test else _segment_ids(core, db, c.segment)
    if not ids:
        raise HTTPException(400, "в сегменте сейчас никого нет")
    c.status = "sending"
    c.total = len(ids)
    c.sent = c.failed = c.read = 0
    c.error = ""
    db.commit()
    threading.Thread(target=_run_campaign, args=(cid, ids, bool(test)),
                     daemon=True).start()
    return {"ok": True, "total": len(ids), "test": bool(test)}


# Telegram держит около 30 сообщений в секунду глобально; берём 20 с запасом.
TG_RATE = float(os.environ.get("BROADCAST_TG_RATE", "20"))
EMAIL_RATE = float(os.environ.get("BROADCAST_EMAIL_RATE", "8"))


def _render(text: str, u: User, core) -> str:
    """Подстановки в текст. Ровно три — имя, остаток и тариф: всё, что можно
    подставить, не соврав."""
    return (text.replace("{name}", (u.name or "").strip() or "друг")
                .replace("{points}", str(int(u.gen_points or 0)))
                .replace("{plan}", core.PLANS[core._plan_of(u)]["title"]))


def _run_campaign(cid: int, ids: list[int], test: bool) -> None:
    core = _core()
    db = SessionLocal()
    try:
        c = db.get(Campaign, cid)
        if not c:
            return
        pause = 1.0 / (TG_RATE if c.channel == "tg" else
                       EMAIL_RATE if c.channel == "email" else 200.0)
        sent = failed = 0
        for uid in ids:
            u = db.get(User, uid)
            if not u:
                continue
            # Отписки глушат МАРКЕТИНГ, но не транзакционные сообщения
            # («оплата прошла», «подписка кончается») — это разные вещи и
            # юридически, и по здравому смыслу.
            if not c.transactional and (u.unsub_all
                                        or (c.channel == "email" and u.unsub_email)
                                        or (c.channel == "tg" and u.unsub_tg)):
                _mark(db, cid, uid, "", "skipped", "отписан")
                continue
            address = ("" if c.channel == "inapp"
                       else (u.tg_id if c.channel == "tg" else (u.email or "")))
            if c.channel != "inapp" and not address:
                _mark(db, cid, uid, "", "skipped", "нет адреса")
                continue
            # Дубль ловим ДО отправки: воркер может перезапуститься, и без
            # этой проверки половина базы получит письмо дважды.
            dup = (db.query(CampaignRecipient)
                   .filter(CampaignRecipient.campaign_id == cid,
                           CampaignRecipient.user_id == uid,
                           CampaignRecipient.status == "sent").first())
            if dup:
                continue
            text = _render(c.body, u, core)
            try:
                if c.channel == "tg":
                    mailer.send_tg(address, text)
                elif c.channel == "email":
                    mailer.send_email(address, c.subject or c.title, text,
                                      unsub_url=_unsub_url(core, u))
                # inapp: доставка = сама строка получателя, читать её будет
                # /api/notices. Отдельного ящика для этого не надо.
                _mark(db, cid, uid, address, "sent", "")
                sent += 1
            except mailer.TgSendError as e:
                if e.permanent:
                    # 403 — человек не начинал диалог с ботом или заблокировал
                    # его. Ретраить бессмысленно: помечаем отписанным.
                    u.unsub_tg = True
                    db.commit()
                _mark(db, cid, uid, address, "failed", str(e)[:180])
                failed += 1
                if e.retry_after:
                    time.sleep(min(60, e.retry_after))
            except Exception as e:  # noqa: BLE001
                _mark(db, cid, uid, address, "failed", str(e)[:180])
                failed += 1
            time.sleep(pause)
        c = db.get(Campaign, cid)
        if c:
            c.sent, c.failed = sent, failed
            c.status = "done"
            c.finished_at = now()
            db.commit()
        log.info("рассылка %s (%s): отправлено %s, ошибок %s%s",
                 cid, (c.channel if c else "?"), sent, failed,
                 " [тест]" if test else "")
    except Exception as e:  # noqa: BLE001
        db.rollback()
        c = db.get(Campaign, cid)
        if c:
            c.status = "error"
            c.error = str(e)[:400]
            db.commit()
        log.warning("рассылка %s упала: %s", cid, str(e)[:200])
    finally:
        db.close()


def _mark(db: Session, cid: int, uid: int, address: str, status: str, err: str) -> None:
    try:
        row = (db.query(CampaignRecipient)
               .filter(CampaignRecipient.campaign_id == cid,
                       CampaignRecipient.user_id == uid).first())
        if not row:
            row = CampaignRecipient(campaign_id=cid, user_id=uid)
            db.add(row)
        row.address = address or ""
        row.status = status
        row.error = err or ""
        row.sent_at = now() if status == "sent" else row.sent_at
        db.commit()
    except Exception as e:  # noqa: BLE001
        db.rollback()
        log.warning("рассылка %s: получатель %s не записался: %s", cid, uid, str(e)[:120])


# ═════════════════ входящие в приложении и отписка ═════════════════

@router.get("/api/notices")
def notices(request: Request, db: Session = Depends(db_session)):
    """Непрочитанные сообщения для плашки в приложении. Канал inapp работает
    без единого внешнего ключа — с него и надо начинать рассылки."""
    core = _core()
    user = core._resolve_user(request, db)
    if not user:
        return {"items": []}
    rows = (db.query(CampaignRecipient, Campaign)
            .join(Campaign, Campaign.id == CampaignRecipient.campaign_id)
            .filter(CampaignRecipient.user_id == user.id,
                    CampaignRecipient.status == "sent",
                    CampaignRecipient.read_at.is_(None),
                    Campaign.channel == "inapp")
            .order_by(CampaignRecipient.id.desc()).limit(5).all())
    return {"items": [{
        "id": r.id, "title": c.subject or c.title,
        "body": _render(c.body, user, core),
        "at": core._as_utc(r.sent_at).isoformat() if r.sent_at else "",
    } for r, c in rows]}


@router.post("/api/notices/{rid}/read")
def notice_read(rid: int, request: Request, db: Session = Depends(db_session)):
    core = _core()
    user = core._resolve_user(request, db)
    if not user:
        raise HTTPException(401, "не авторизован")
    row = db.get(CampaignRecipient, rid)
    if not row or row.user_id != user.id:
        raise HTTPException(404, "сообщение не найдено")
    if not row.read_at:
        row.read_at = now()
        c = db.get(Campaign, row.campaign_id)
        if c:
            c.read = int(c.read or 0) + 1
        db.commit()
    return {"ok": True}


def _unsub_signer(core):
    return URLSafeSerializer(core.SECRET_KEY, salt="lolq-unsub")


def _unsub_url(core, u: User) -> str:
    base = (os.environ.get("PUBLIC_BASE_URL", "") or "").rstrip("/")
    if not base:
        return ""
    return f"{base}/unsub/{_unsub_signer(core).dumps({'uid': u.id})}"


@router.get("/unsub/{token}", response_class=HTMLResponse)
def unsubscribe(token: str, db: Session = Depends(db_session)):
    """Отписка БЕЗ входа в аккаунт: требовать логин ради отписки — верный
    способ получить жалобу на спам вместо отписки."""
    core = _core()
    try:
        data = _unsub_signer(core).loads(token)
    except BadSignature:
        return HTMLResponse("<p>ссылка недействительна</p>", status_code=404)
    u = db.get(User, int((data or {}).get("uid") or 0))
    if not u:
        return HTMLResponse("<p>аккаунт не найден</p>", status_code=404)
    u.unsub_all = True
    u.unsub_email = True
    u.unsub_tg = True
    db.commit()
    log.info("отписка: юзер %s больше не получает рассылок", u.id)
    return HTMLResponse(
        '<div style="font-family:-apple-system,Segoe UI,Roboto,sans-serif;'
        'max-width:520px;margin:80px auto;padding:24px;color:#2d211a">'
        "<h2>Готово — вы отписаны</h2>"
        "<p>Письма о рассылках больше не придут. Сообщения об оплате и "
        "окончании подписки остаются: без них подписка обрывается молча.</p>"
        "</div>")


def _unstick_campaigns() -> None:
    """Снять замок с рассылок, оборванных перезапуском.

    Отправка живёт в демон-треде, а статус «sending» — в базе. Контейнер
    пересобирается при каждом деплое, тред умирает вместе с ним, и строка
    остаётся в «sending» навсегда. Дальше send_campaign отвечает 409 «эта
    рассылка уже отправляется» — и рассылку нельзя ни продолжить, ни
    перезапустить, вообще никак, кроме правки базы руками.

    Повторная отправка при этом безопасна: те, кому уже ушло, отсеиваются
    проверкой дубля по campaign_recipients (status='sent')."""
    db = SessionLocal()
    try:
        stuck = db.query(Campaign).filter(Campaign.status == "sending").all()
        for c in stuck:
            c.status = "error"
            c.error = ("отправка оборвалась (перезапуск сервиса) — нажми "
                       "«Отправить» ещё раз, уже отправленным не задвоится")
        if stuck:
            db.commit()
            log.warning("рассылки: снял замок с %s оборванных (%s)",
                        len(stuck), ", ".join(str(c.id) for c in stuck))
    except Exception as e:  # noqa: BLE001
        db.rollback()
        log.warning("рассылки: не смог снять замок: %s", str(e)[:150])
    finally:
        db.close()


def mount(app) -> None:
    """Подключение из main.py. Строго ДО app.mount(\"/\", StaticFiles…):
    статика на «/» перехватывает всё, что зарегистрировано после неё."""
    app.include_router(router)
    _unstick_campaigns()
