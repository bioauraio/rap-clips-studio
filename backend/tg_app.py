"""Мини-апп lolq.ai в Telegram: вход по initData и мосты к аккаунту сайта.

ГЛАВНОЕ РЕШЕНИЕ — ЧЕТВЁРТАЯ ДВЕРЬ В ТУ ЖЕ КОМНАТУ. Никакой отдельной модели
пользователя у мини-аппа нет: `/api/auth/telegram-webapp` проверяет подпись
Telegram и уходит в тот же `_external_login(field="tg_id")`, которым живут
Login Widget на сайте и бот. Поэтому вошедший в мини-апп, вошедший на сайте
через Telegram и написавший боту — гарантированно один аккаунт с одними
токенами, тарифом, проектами и партнёркой.

ПОДПИСЬ У МИНИ-АППА ДРУГАЯ, ЧЕМ У LOGIN WIDGET. Переиспользовать код
`auth_telegram` нельзя: там `secret = sha256(token)`, здесь
`secret = HMAC_SHA256(key="WebAppData", msg=token)`, и порядок аргументов HMAC
на обоих шагах разный. Плюс поле `signature` (Bot API 8.0) УЧАСТВУЕТ в строке
проверки — исключается только `hash`. Парсеры, написанные до 8.0, выкидывают
`signature` и не сходятся никогда.

КУКА И IFRAME. `qv_session` ставится с SameSite=Lax. На телефоне мини-апп живёт
в нативном webview, страница верхнеуровневая — кука едет. В Telegram Desktop и
Web это iframe, запросы кросс-сайтовые, и Lax-кука не поедет НИКУДА. Поэтому
роут отдаёт тот же токен ещё и в теле ответа: фронт держит его в памяти и шлёт
заголовком `Authorization: Bearer`. Заголовок браузер кросс-сайтово сам не
подставляет, так что CSRF-поверхность не растёт (см. `_resolve_user`).
"""
from __future__ import annotations

import hashlib
import hmac
import json
import logging
import os
import secrets
import threading
import time
from urllib.parse import parse_qsl

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

log = logging.getLogger("rapclips")

router = APIRouter()

# Свежесть initData. Сутки — как у Login Widget в main.py.
INIT_TTL_S = int(os.environ.get("TG_INITDATA_TTL_S", "86400"))
# Ссылка входа через Яндекс живёт 5 минут: человек уходит в браузер и
# возвращается, дольше держать нечего.
LINK_NONCE_TTL_S = int(os.environ.get("TG_LINK_NONCE_TTL_S", "300"))

# nonce → (state, user_id, expires). state: "pending" | "done".
# В памяти намеренно: таблица ради пятиминутного одноразового ключа — лишняя
# миграция, а рестарт API просто заставит нажать кнопку ещё раз.
_nonces: dict[str, tuple[str, int, float]] = {}
_lock = threading.Lock()


def _core():
    import main  # noqa: PLC0415
    return main


def _stars():
    import stars  # noqa: PLC0415
    return stars


def _deny(status: int, code: str, message: str = "") -> JSONResponse:
    return JSONResponse(status_code=status, content={"error": code, "detail": message or code})


def bot_token() -> str:
    return _stars().BOT_TOKEN


def miniapp_url(start_param: str = "") -> str:
    """Прямая ссылка мини-аппа: t.me/<bot>/<app>?startapp=<param>."""
    st = _stars()
    if not st.BOT_USERNAME:
        return ""
    url = f"https://t.me/{st.BOT_USERNAME}/{st.MINIAPP_NAME or 'app'}"
    return url + (f"?startapp={start_param}" if start_param else "")


# ───────────────────────── проверка подписи initData ─────────────────────────

def verify_init_data(init_data: str) -> dict | None:
    """Разобрать и проверить `WebApp.initData`. None — подпись не сошлась.

    Три места, где это чаще всего ломают, и как здесь сделано:
      • `user` — JSON-строка, кладётся в строку проверки URL-ДЕКОДИРОВАННОЙ и
        как есть, без переупаковки (иначе порядок ключей поедет);
      • исключаем ТОЛЬКО `hash`; `signature` участвует в HMAC;
      • ключ и сообщение на двух шагах меняются местами — это не опечатка.
    """
    token = bot_token()
    if not token or not init_data:
        return None
    try:
        pairs = parse_qsl(init_data, keep_blank_values=True, strict_parsing=True)
    except ValueError:
        return None
    data = dict(pairs)
    got = str(data.pop("hash", ""))
    if not got:
        return None
    check = "\n".join(f"{k}={data[k]}" for k in sorted(data))
    secret = hmac.new(b"WebAppData", token.encode(), hashlib.sha256).digest()
    calc = hmac.new(secret, check.encode(), hashlib.sha256).hexdigest()
    if not hmac.compare_digest(calc, got):
        return None
    try:
        auth_date = int(data.get("auth_date") or 0)
    except ValueError:
        return None
    if auth_date <= 0 or time.time() - auth_date > INIT_TTL_S:
        return None
    # Кэша «уже видели этот hash» здесь СОЗНАТЕЛЬНО нет. initData фиксирована
    # на весь запуск мини-аппа, и одноразовость ломала бы штатный повторный
    # вход после 401. Защиту даёт срок жизни: чужая строка бесполезна через
    # сутки, а до того она открывает ровно тот же аккаунт, что и её хозяину.
    out = dict(data)
    if data.get("user"):
        try:
            out["user"] = json.loads(data["user"])
        except (ValueError, TypeError):
            return None
    return out


def _sweep() -> None:
    """Выбросить протухшие nonce. Зовётся под _lock."""
    now = time.time()
    for n in [n for n, v in _nonces.items() if v[2] < now]:
        _nonces.pop(n, None)


# ─────────────────────── слияние аккаунтов: общие правила ───────────────────────

def _is_fresh(core, db, user, ignore: str = "") -> bool:
    """«Свежий» аккаунт — тот, потерять который не жалко: без пароля, без
    других внешних входов, без оплат, без проектов и без платного тарифа.

    Это единственное место, где решается, можно ли склеивать. Молча слить два
    ЖИВЫХ аккаунта нельзя: у обоих токены, тариф и работа, и любое такое слияние
    что-то стирает."""
    if not user:
        return False
    for field in ("tg_id", "yandex_id", "google_id"):
        if field != ignore and str(getattr(user, field, "") or ""):
            return False
    if user.login or user.password_hash:
        return False
    if core._plan_of(user) != "free":
        return False
    if db.query(core.ProcessedPayment).filter(
            core.ProcessedPayment.user_id == user.id).first():
        return False
    if db.query(core.Project).filter(core.Project.owner_id == user.id).first():
        return False
    return True


def merge_accounts(core, db, tg_user, web_user, field: str = "tg_id") -> tuple[object, str]:
    """Свести аккаунт мини-аппа и аккаунт сайта. Возвращает (кто выжил, код).

    Четыре случая, все явные:
      • у сайта нет телеграма → просто привязываем, живём дальше сайтовым;
      • это один и тот же аккаунт → делать нечего;
      • один из двух свежий → его внешний ключ переезжает к живому;
      • оба живые → 409 и выбор человеку. Здесь мы не решаем за него."""
    if not tg_user or not web_user:
        return (web_user or tg_user), "ok"
    if tg_user.id == web_user.id:
        return web_user, "same"
    if _is_fresh(core, db, tg_user, ignore=field):
        # Телеграм переезжает на аккаунт сайта, свежий остаётся без привязок.
        setattr(web_user, field, getattr(tg_user, field))
        setattr(tg_user, field, "")
        if getattr(tg_user, "tg_username", "") and field == "tg_id":
            web_user.tg_username = tg_user.tg_username
        db.commit()
        return web_user, "adopted_web"
    if _is_fresh(core, db, web_user):
        # Обратный случай (вход через Яндекс завёл пустой аккаунт): его
        # внешние ключи переезжают на живой телеграм-аккаунт.
        # Только внешние ключи. Логин сюда не попадает намеренно: он ходит
        # в паре с password_hash, и переносить одно без другого — это тихо
        # сломанный вход по паролю. (_is_fresh и так отсекает аккаунты с
        # логином, но список не должен провоцировать это добавить.)
        for f in ("yandex_id", "google_id"):
            val = str(getattr(web_user, f, "") or "")
            if val and not str(getattr(tg_user, f, "") or ""):
                setattr(tg_user, f, val)
                setattr(web_user, f, "")
        if web_user.email and not tg_user.email:
            tg_user.email = web_user.email
        db.commit()
        return tg_user, "adopted_tg"
    return tg_user, "conflict"


# ─────────────────────────── вход в мини-аппе ───────────────────────────

def _session_payload(core, user, extra: dict | None = None) -> dict:
    out = {
        "ok": True,
        # Токен для Authorization: Bearer — единственный способ работать в
        # Telegram Desktop/Web, где мини-апп живёт в iframe и Lax-кука не едет.
        "token": core.signer.dumps({"uid": user.id}),
        "user": core._user_dict(user),
    }
    out.update(extra or {})
    return out


def _with_cookie(core, user, payload: dict, status: int = 200) -> JSONResponse:
    resp = JSONResponse(payload, status_code=status)
    resp.set_cookie(core.QV_COOKIE, core.signer.dumps({"uid": user.id}),
                    max_age=core.QV_MAX_AGE, httponly=True,
                    samesite="lax", secure=True)
    return resp


@router.post("/api/auth/telegram-webapp")
async def auth_telegram_webapp(request: Request):
    """Автовход мини-аппа. Ни `/api/start`, ни форма пароля внутри Telegram не
    вызываются никогда — человек уже опознан платформой."""
    core = _core()
    if not bot_token():
        return _deny(503, "telegram_off", "вход через Telegram не настроен")
    body = await request.json()
    data = verify_init_data(str(body.get("init_data") or ""))
    if not data:
        return _deny(403, "bad_signature", "Telegram не подтвердил подпись")
    tg = data.get("user") or {}
    tg_id = str(tg.get("id") or "")
    if not tg_id:
        return _deny(400, "no_tg_id", "Telegram не прислал пользователя")

    # start_param приезжает и в initData (`start_param`), и отдельным полем от
    # фронта — берём любой непустой.
    start_param = str(body.get("start_param") or data.get("start_param") or "").strip()
    ref, bridge = "", ""
    if start_param.startswith("ref_"):
        ref = start_param[4:]
    elif start_param.startswith(("link_", "ylink_")):
        bridge = start_param

    name = " ".join(str(x) for x in (tg.get("first_name"), tg.get("last_name")) if x).strip()
    db = core.SessionLocal()
    try:
        user = core._external_login(
            db, request, "tg_id", tg_id,
            name=name or str(tg.get("username") or "") or "guest",
            avatar=str(tg.get("photo_url") or ""), ref=ref,
        )
        uname = str(tg.get("username") or "")
        if uname and user.tg_username != uname:
            user.tg_username = uname
            db.commit()

        merged = ""
        if bridge:
            web_user = _bridge_target(core, db, bridge)
            if web_user is None:
                # Просроченный код привязки с сайта — человеку есть что нажать
                # ещё раз, и об этом надо сказать. А вот пустой ylink_ чаще
                # всего значит, что поллинг мини-аппа успел раньше и вход УЖЕ
                # состоялся: ругаться на это было бы ложной тревогой.
                kind = "expired" if bridge.startswith("link_") else ""
                return _with_cookie(core, user, _session_payload(
                    core, user, {"bridge": kind}))
            field = "tg_id"
            survivor, code = merge_accounts(core, db, user, web_user, field)
            if code == "conflict":
                # Два живых аккаунта. Склеивать нельзя — это потеря денег и
                # работы, поэтому решение отдаём человеку явным экраном.
                return _with_cookie(core, user, _session_payload(core, user, {
                    "bridge": "conflict",
                    "other": {"name": web_user.name,
                              "plan": core._plan_of(web_user),
                              "points": int(web_user.gen_points or 0)},
                }), status=200)
            user, merged = survivor, code

        lang = str(tg.get("language_code") or "")
        return _with_cookie(core, user, _session_payload(core, user, {
            "merged": merged,
            "lang": "ru" if lang.startswith("ru") else "en",
            "ref": ref,
        }))
    finally:
        db.close()


def _bridge_target(core, db, start_param: str):
    """Аккаунт сайта, к которому мини-апп просят привязаться.

    `link_<КОД>` — код из `/api/bot/link-code` (человек нажал «Продолжить в
    Telegram» на сайте). `ylink_<nonce>` — возврат из внешнего браузера после
    входа через Яндекс ID. Оба одноразовые."""
    import bot_api  # noqa: PLC0415
    if start_param.startswith("link_"):
        code = start_param[5:].strip().upper()
        with bot_api._link_lock:
            bot_api._sweep_link_codes()
            row = bot_api._link_codes.pop(code, None)
        return db.get(core.User, row[0]) if row else None
    nonce = start_param[6:].strip()
    with _lock:
        _sweep()
        row = _nonces.pop(nonce, None)
    if not row or row[0] != "done" or not row[1]:
        return None
    return db.get(core.User, row[1])


# ──────────────────── Яндекс ID и Google из мини-аппа ────────────────────
#
# Уходить на oauth.yandex.ru ВНУТРИ webview нельзя: с 20.07.2026 Telegram
# блокирует методы Mini App с чужого origin, и `window.Telegram.WebApp` после
# редиректа мёртв. Поэтому OAuth открывается во ВНЕШНЕМ браузере через
# `openLink()`, а результат возвращается двумя независимыми путями:
#   1) браузер редиректит на t.me/<bot>/<app>?startapp=ylink_<nonce> — Telegram
#      переоткрывает мини-апп, и он видит nonce в start_param;
#   2) мини-апп параллельно опрашивает /api/tg/link-status — на десктопе, где
#      ссылку t.me некому обработать, срабатывает только этот путь.
# Обе ветки сходятся в один и тот же `merge_accounts`.

@router.post("/api/tg/link-start")
async def link_start(request: Request):
    """Начать вход через внешнего провайдера из мини-аппа."""
    core = _core()
    body = await request.json()
    provider = str(body.get("provider") or "yandex").lower()
    if provider not in ("yandex", "google"):
        return _deny(400, "bad_provider", "только yandex или google")
    if provider == "yandex" and not core.YANDEX_CLIENT_ID:
        return _deny(503, "provider_off", "вход через Яндекс не настроен")
    if provider == "google" and not core.GOOGLE_CLIENT_ID:
        return _deny(503, "provider_off", "вход через Google не настроен")
    if not verify_init_data(str(body.get("init_data") or "")):
        return _deny(403, "bad_signature", "Telegram не подтвердил подпись")

    nonce = secrets.token_urlsafe(12)
    with _lock:
        _sweep()
        _nonces[nonce] = ("pending", 0, time.time() + LINK_NONCE_TTL_S)
    ref = core._norm_code(str(body.get("ref") or ""))
    # state Яндекс/Google возвращают как есть — «~» в кодах не встречается,
    # поэтому одного разделителя хватает и base64 не нужен.
    state = f"{nonce}~{ref}"
    return {
        "ok": True, "nonce": nonce, "expires_in": LINK_NONCE_TTL_S,
        "url": f"{core.PUBLIC_BASE_URL}/api/auth/{provider}/start?state={state}",
    }


def split_state(state: str) -> tuple[str, str]:
    """Разобрать state OAuth: «<nonce>~<ref>» или просто «<ref>» (как было)."""
    raw = str(state or "")
    if "~" in raw:
        nonce, _, ref = raw.partition("~")
        return nonce.strip(), ref.strip()
    return "", raw.strip()


def remember_login(nonce: str, user_id: int) -> bool:
    """Отметить, что по этому nonce человек вошёл. Зовётся из OAuth-колбэка."""
    if not nonce:
        return False
    with _lock:
        _sweep()
        row = _nonces.get(nonce)
        if not row:
            return False
        _nonces[nonce] = ("done", int(user_id), time.time() + LINK_NONCE_TTL_S)
    return True


@router.get("/api/tg/link-status")
def link_status(nonce: str = "", request: Request = None):
    """Опрос из мини-аппа: вошёл ли человек во внешнем браузере.

    Отдаёт куку и Bearer-токен сразу — на десктопе это единственный путь
    вернуться в аккаунт, ссылку t.me там перехватывать некому."""
    core = _core()
    with _lock:
        _sweep()
        row = _nonces.get(str(nonce or "").strip())
    if not row:
        return {"status": "expired"}
    if row[0] != "done":
        return {"status": "pending"}
    db = core.SessionLocal()
    try:
        web_user = db.get(core.User, row[1])
        if not web_user:
            return {"status": "expired"}
        tg_user = core._resolve_user(request, db) if request else None
        survivor, code = merge_accounts(core, db, tg_user, web_user, "tg_id")
        if code == "conflict":
            return {"status": "conflict",
                    "other": {"name": web_user.name, "plan": core._plan_of(web_user),
                              "points": int(web_user.gen_points or 0)}}
        with _lock:
            _nonces.pop(str(nonce or "").strip(), None)
        return _with_cookie(core, survivor, {
            "status": "done", "merged": code,
            **_session_payload(core, survivor),
        })
    finally:
        db.close()


# ─────────────────────────── конфигурация фронта ───────────────────────────

@router.get("/api/tg/config")
def tg_config():
    """Что мини-апп и сайт могут показывать: без токенов кнопок не рисуем."""
    core = _core()
    st = _stars()
    return {
        "webapp": bool(st.BOT_TOKEN),
        "bot": st.BOT_USERNAME,
        "miniapp": st.MINIAPP_NAME,
        "miniapp_url": miniapp_url(),
        "stars": st.enabled(),
        "yandex": bool(core.YANDEX_CLIENT_ID and core.YANDEX_CLIENT_SECRET),
        "google": bool(core.GOOGLE_CLIENT_ID and core.GOOGLE_CLIENT_SECRET),
        "public_base": core.PUBLIC_BASE_URL,
    }


def mount(app) -> None:
    app.include_router(router)
    st = _stars()
    if st.BOT_TOKEN and not st.BOT_USERNAME:
        log.warning("мини-апп: BOT_TOKEN есть, а BOT_USERNAME пуст — ссылки "
                    "t.me/<bot>/<app> собрать не из чего, вход через Яндекс "
                    "внутри Telegram вернётся только поллингом")
    log.info("мини-апп: %s (бот @%s, приложение «%s»)",
             "включён" if st.BOT_TOKEN else "ВЫКЛЮЧЕН — нет BOT_TOKEN",
             st.BOT_USERNAME or "?", st.MINIAPP_NAME)
