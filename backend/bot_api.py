"""Служебные роуты для телеграм-бота lolq.ai.

Зачем отдельный модуль, а не правки в main.py: main.py живёт под несколькими
руками, а всё, что нужно боту на стороне сервера, помещается в один файл и
подключается ДВУМЯ строками (см. backend/bot_patch.md). Здесь нет ни одной
новой модели прав и ни одной новой таблицы — только тонкая обвязка над тем,
что в main.py уже написано и работает.

Главное решение: **бот не заводит свою авторизацию**. Кука `qv_session` — это
подписанный `{"uid": id}`, и /internal/tg-session просто выписывает её по
tg_id через существующий `_external_login`. Дальше бот ходит в ОБЫЧНЫЕ
публичные роуты с заголовком `Cookie: qv_session=…`, и `_own_track`,
`_charge`, `_check_file_owner` работают как есть. Поэтому здесь не появляется
ни «сервисных токенов от имени пользователя», ни второго набора проверок
владения — а значит, и второго места, где эти проверки можно забыть.

Почему внутренний ключ, а не SECRET_KEY у бота: SECRET_KEY подписывает и
сессии, и ссылки на клипы. Он остаётся в одном контейнере. У бота только
BOT_INTERNAL_KEY — его утечка не даёт подделывать медиа-ссылки.

Роуты /internal/* НАРУЖУ НЕ ПРОКСИРУЮТСЯ (см. docs/telegram-bot.md): они
слушают docker-сеть и защищены ключом. Без ключа в env они честно отвечают
503, а не работают «на всякий случай» открытыми.
"""
from __future__ import annotations

import hmac
import json
import logging
import os
import re
import secrets
import subprocess
import threading
import time

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

log = logging.getLogger("rapclips")

router = APIRouter()

# Ключ бота. Пустой = служебный контур выключен (503), а не открыт всем.
BOT_INTERNAL_KEY = os.environ.get("BOT_INTERNAL_KEY", "").strip()

# Превью клипа под лимит Telegram. 50 МБ — потолок отдачи файла ботом, берём
# запас на контейнер mp4 и погрешность битрейта.
PREVIEW_TARGET_MB = float(os.environ.get("BOT_PREVIEW_TARGET_MB", "45"))
PREVIEW_W = int(os.environ.get("BOT_PREVIEW_W", "720"))
PREVIEW_MIN_KBPS = int(os.environ.get("BOT_PREVIEW_MIN_KBPS", "900"))
PREVIEW_MAX_KBPS = int(os.environ.get("BOT_PREVIEW_MAX_KBPS", "4000"))
PREVIEW_AUDIO_KBPS = int(os.environ.get("BOT_PREVIEW_AUDIO_KBPS", "128"))
# Клип длиннее этого не влезает в 45 МБ даже на нижней границе качества —
# вместо мыла отдаём честный тизер и ссылку на оригинал.
PREVIEW_TEASER_SEC = int(os.environ.get("BOT_PREVIEW_TEASER_SEC", "60"))
# Превью — производный файл: удаляем старые, оригинал всегда пересоберёт.
PREVIEW_TTL_DAYS = float(os.environ.get("BOT_PREVIEW_TTL_DAYS", "14"))

# Код привязки аккаунта сайта к телеграму живёт 15 минут и сгорает при
# использовании. Хранится В ПАМЯТИ процесса намеренно: таблица ради
# одноразового 8-символьного кода — лишняя миграция, а перезапуск API просто
# заставит человека нажать кнопку ещё раз.
LINK_TTL_S = int(os.environ.get("BOT_LINK_TTL_S", "900"))
_link_codes: dict[str, tuple[int, float]] = {}
_link_lock = threading.Lock()


def _core():
    """main импортируется ЛЕНИВО и только внутри обработчиков.

    bot_api подключается ИЗ main.py, поэтому на момент импорта этого модуля
    main ещё не доисполнен — импорт на верхнем уровне дал бы цикл. Внутри
    обработчика модуль уже в sys.modules и достаётся мгновенно."""
    import main  # noqa: PLC0415
    return main


def _deny(status: int, code: str, message: str = "") -> JSONResponse:
    return JSONResponse(status_code=status, content={"error": code, "detail": message or code})


def _key_ok(request: Request) -> bool:
    """Сверка внутреннего ключа. compare_digest — чтобы ключ нельзя было
    подобрать по времени ответа."""
    got = request.headers.get("x-internal-key") or ""
    return bool(BOT_INTERNAL_KEY) and hmac.compare_digest(got, BOT_INTERNAL_KEY)


class _NoCookies:
    """Заглушка Request для `_external_login`.

    Тому нужен только `request.cookies` — он ищет гостевую сессию браузера,
    чтобы «усыновить» её проекты. У бота браузерного гостя нет и быть не
    может, поэтому отдаём пустые куки: аккаунт найдётся по tg_id или заведётся
    новым, а чужой гость случайно не приклеится."""

    cookies: dict = {}


# ───────────────────────── сессия бота = кука сайта ─────────────────────────

@router.post("/internal/tg-session")
async def tg_session(request: Request):
    """Выписать боту куку `qv_session` для человека с этим tg_id.

    Ровно тот же путь, которым ходит Telegram Login Widget на сайте: один
    `_external_login`, одна модель аккаунта. Поэтому вошедший на сайте через
    Telegram и написавший боту — это гарантированно один и тот же аккаунт с
    одними очками, тарифом, проектами и партнёркой."""
    if not _key_ok(request):
        return _deny(503 if not BOT_INTERNAL_KEY else 403, "internal_key",
                     "служебный контур бота выключен или ключ не сошёлся")
    core = _core()
    body = await request.json()
    tg_id = str(body.get("tg_id") or "").strip()
    if not tg_id:
        return _deny(400, "no_tg_id", "нет telegram id")

    name = " ".join(str(x) for x in (body.get("first_name"), body.get("last_name")) if x)
    db = core.SessionLocal()
    try:
        user = core._external_login(
            db, _NoCookies(), "tg_id", tg_id,
            name=name.strip() or str(body.get("username") or "") or "guest",
            avatar=str(body.get("photo_url") or ""),
            ref=str(body.get("ref") or ""),
        )
        # @username мог смениться — держим его свежим для карточек и поддержки.
        uname = str(body.get("username") or "")
        if uname and user.tg_username != uname:
            user.tg_username = uname
            db.commit()
        return {
            "cookie": core.signer.dumps({"uid": user.id}),
            "cookie_name": core.QV_COOKIE,
            "max_age": core.QV_MAX_AGE,
            "user": {
                "id": user.id, "name": user.name,
                "plan": core._plan_of(user), "points": int(user.gen_points or 0),
                "is_admin": bool(user.is_admin),
                "ref_code": user.ref_code or "",
                "is_ambassador": bool(user.is_ambassador),
            },
        }
    finally:
        db.close()


@router.post("/internal/tg-adopt")
async def tg_adopt(request: Request):
    """Привязать tg_id к УЖЕ существующему аккаунту сайта (по коду привязки).

    Нужно тем, кто завёл аккаунт логином/паролем, Google или Яндексом: у них
    tg_id пуст, и бот их не находит. Код выдаёт /api/bot/link-code под живой
    сессией сайта, человек открывает t.me/<bot>?start=link_КОД.

    Если у этого tg_id уже есть свой аккаунт — молча НЕ сливаем: два аккаунта
    с очками и проектами склеить без потерь нельзя, поэтому отвечаем конфликтом
    и оставляем выбор человеку."""
    if not _key_ok(request):
        return _deny(503 if not BOT_INTERNAL_KEY else 403, "internal_key", "ключ не сошёлся")
    core = _core()
    body = await request.json()
    code = str(body.get("code") or "").strip().upper()
    tg_id = str(body.get("tg_id") or "").strip()
    if not code or not tg_id:
        return _deny(400, "bad_request", "нужны code и tg_id")

    with _link_lock:
        _sweep_link_codes()
        row = _link_codes.pop(code, None)  # одноразовый: гасим сразу
    if not row:
        return _deny(404, "link_expired", "код привязки просрочен или уже использован")
    uid = row[0]

    db = core.SessionLocal()
    try:
        user = db.get(core.User, uid)
        if not user:
            return _deny(404, "no_user", "аккаунт не найден")
        other = db.query(core.User).filter(core.User.tg_id == tg_id,
                                           core.User.id != user.id).first()
        if other:
            return _deny(409, "tg_taken",
                         "этот телеграм уже привязан к другому аккаунту")
        if user.tg_id and user.tg_id != tg_id:
            return _deny(409, "already_linked",
                         "к аккаунту уже привязан другой телеграм")
        user.tg_id = tg_id
        if body.get("username"):
            user.tg_username = str(body["username"])
        db.commit()
        log.info("бот: аккаунт %s привязан к telegram %s", user.id, tg_id)
        return {"ok": True, "user_id": user.id, "name": user.name}
    finally:
        db.close()


def _sweep_link_codes() -> None:
    dead = [c for c, (_, exp) in _link_codes.items() if exp < time.time()]
    for c in dead:
        _link_codes.pop(c, None)


@router.get("/api/bot/link-code")
def link_code(request: Request):
    """Ссылка «продолжить в Telegram» для того, кто уже сидит на сайте.

    Payload у deep-link ограничен 64 символами и алфавитом A-Za-z0-9_-, так
    что подписанный токен itsdangerous туда не влезает (он длиннее и содержит
    точки). Поэтому короткий одноразовый код."""
    core = _core()
    db = core.SessionLocal()
    try:
        user = core._resolve_user(request, db)
        if not user:
            return _deny(401, "unauthorized", "нужна сессия сайта")
        code = "".join(secrets.choice(core.REF_ALPHABET) for _ in range(8))
        with _link_lock:
            _sweep_link_codes()
            _link_codes[code] = (user.id, time.time() + LINK_TTL_S)
        bot = os.environ.get("TG_LOGIN_BOT_USERNAME", "").lstrip("@")
        return {
            "code": code,
            "url": f"https://t.me/{bot}?start=link_{code}" if bot else "",
            "expires_in": LINK_TTL_S,
        }
    finally:
        db.close()


# ────────────────────────── оплата звёздами ──────────────────────────

@router.post("/internal/stars-grant")
async def stars_grant(request: Request):
    """Выдать оплаченное звёздами Telegram — через ОБЩУЮ точку `_grant_payment`.

    Второй кассы здесь нет: Stars становятся третьим провайдером в том же
    реестре ProcessedPayment. `payment_id` = "stars:<telegram_payment_charge_id>",
    UNIQUE на колонке даёт идемпотентность даром — повтор того же charge_id
    ничего не выдаст второй раз. Партнёрка (`_ref_reward`) работает и здесь,
    поэтому амбассадор получает долю с оплат из бота так же, как с сайта."""
    if not _key_ok(request):
        return _deny(503 if not BOT_INTERNAL_KEY else 403, "internal_key", "ключ не сошёлся")
    core = _core()
    body = await request.json()
    tg_id = str(body.get("tg_id") or "").strip()
    charge_id = str(body.get("charge_id") or "").strip()
    kind = str(body.get("kind") or "topup")
    if not tg_id or not charge_id:
        return _deny(400, "bad_request", "нужны tg_id и charge_id")

    db = core.SessionLocal()
    try:
        user = db.query(core.User).filter(core.User.tg_id == tg_id).first()
        if not user:
            return _deny(404, "no_user", "нет аккаунта с таким telegram id")
        # Сумма в центах нужна партнёрке: доля амбассадора считается от денег,
        # а не от звёзд. Берём наш прайс, а не число из платежа Telegram.
        amount_cents = int(body.get("amount_cents") or 0)
        if kind == "topup":
            pack_id = str(body.get("pack") or "")
            if pack_id not in core.TOPUP_PACKS:
                return _deny(400, "unknown_pack", f"неизвестный пакет {pack_id}")
            amount_cents = amount_cents or int(core.TOPUP_PACKS[pack_id]["usd_cents"])
            granted = core._grant_payment(
                db, user, provider="stars", payment_id=charge_id, kind="topup",
                pack_id=pack_id, amount_cents=amount_cents, currency="USD")
        else:
            plan_id = str(body.get("plan") or "")
            if plan_id not in core.PLANS or core.PLANS[plan_id]["usd_cents"] <= 0:
                return _deny(400, "unknown_plan", f"неизвестный тариф {plan_id}")
            period = core._norm_period(body.get("period"))
            amount_cents = amount_cents or int(
                core.PLANS[plan_id]["usd_year_cents" if period == "year" else "usd_cents"])
            granted = core._grant_payment(
                db, user, provider="stars", payment_id=charge_id, kind="plan",
                plan_id=plan_id, period=period, amount_cents=amount_cents,
                currency="USD")
        if granted:
            import stars as stars_mod  # noqa: PLC0415
            core._ref_reward(db, user, core._reward_kopeks(0, amount_cents),
                             core._pay_key("stars", charge_id),
                             pct=stars_mod.REF_REWARD_PCT_STARS)
            # Первый платёж ПОДПИСКИ: без его charge_id отменить подписку со
            # своей стороны нечем — editUserStarSubscription принимает именно
            # первый charge, а не последний.
            if kind != "topup" and body.get("subscription"):
                stars_mod.remember_subscription(db, user, charge_id)
        db.refresh(user)
        return {
            "ok": True, "granted": bool(granted),
            "points": int(user.gen_points or 0),
            "plan": core._plan_of(user),
            "plan_title": core.PLANS[core._plan_of(user)]["title"],
        }
    finally:
        db.close()


# ─────────────────────── общий словарь стилей ───────────────────────
# Стили ЖИВУТ во фронте: промпты в app.js, названия и описания в i18n.js.
# Бот читает их оттуда, а не держит свою копию, иначе через месяц «Ghibli» на
# сайте и в боте описывались бы по-разному. Файлы лежат в образе рядом
# (FRONTEND_DIR=/app/static), парсинг кэшируется по mtime.
_styles_cache: dict = {"mtime": 0.0, "data": []}
_styles_lock = threading.Lock()

_RE_PRESETS = re.compile(r'const STYLE_PRESETS\s*=\s*\[(.*?)\n\];', re.S)
_RE_PRESET = re.compile(r'key:\s*"([A-Za-z0-9_]+)"\s*,\s*value:\s*"((?:[^"\\]|\\.)*)"')
_RE_I18N_BLOCK = re.compile(r'\n\s{2,}styles:\s*\{')
_RE_I18N_ITEM = re.compile(
    r'(\w+):\s*\{\s*label:\s*"((?:[^"\\]|\\.)*)"\s*,\s*desc:\s*"((?:[^"\\]|\\.)*)"\s*,?\s*\}')


def _unquote(raw: str) -> str:
    try:
        return json.loads('"' + raw + '"')
    except ValueError:
        return raw


def _parse_labels(text: str) -> list[dict]:
    """Названия/описания стилей из i18n.js: первый блок `styles:` — английский,
    второй — русский (порядок языков в файле именно такой)."""
    out = []
    for m in _RE_I18N_BLOCK.finditer(text):
        chunk = text[m.start(): m.start() + 12000]
        got = _RE_I18N_ITEM.findall(chunk)
        if got:
            out.append({k: {"label": _unquote(lb), "desc": _unquote(ds)} for k, lb, ds in got})
    return out


def _load_styles() -> list[dict]:
    """[{key, value, en:{label,desc}, ru:{…}}]. Пустой список — не смогли
    разобрать фронт: врать «стилей нет» нельзя, поэтому наверх уходит пустота,
    а бот честно переключается на стиль своими словами (API принимает любой
    текст в поле style)."""
    core = _core()
    fdir = getattr(core, "FRONTEND_DIR", "/app/static")
    p_app, p_i18n = os.path.join(fdir, "app.js"), os.path.join(fdir, "i18n.js")
    try:
        mtime = max(os.path.getmtime(p_app), os.path.getmtime(p_i18n))
    except OSError:
        log.warning("бот: не найден фронт в %s — список стилей пуст", fdir)
        return []
    with _styles_lock:
        if _styles_cache["data"] and _styles_cache["mtime"] >= mtime:
            return _styles_cache["data"]
        try:
            app_js = open(p_app, encoding="utf-8").read()
            i18n_js = open(p_i18n, encoding="utf-8").read()
        except OSError as e:
            log.warning("бот: не прочитать фронт для стилей: %s", e)
            return []
        block = _RE_PRESETS.search(app_js)
        presets = _RE_PRESET.findall(block.group(1)) if block else []
        labels = _parse_labels(i18n_js)
        en = labels[0] if labels else {}
        ru = labels[1] if len(labels) > 1 else en
        data = [{
            "key": key,
            "value": _unquote(value),
            "en": en.get(key) or {"label": key, "desc": ""},
            "ru": ru.get(key) or en.get(key) or {"label": key, "desc": ""},
        } for key, value in presets]
        if not data:
            log.warning("бот: STYLE_PRESETS не разобрались из %s — бот попросит "
                        "описать стиль словами", p_app)
        _styles_cache.update({"mtime": mtime, "data": data})
        return data


@router.get("/api/styles")
def styles(lang: str = ""):
    """Стили для бота (и для любого другого клиента). lang: en | ru | пусто —
    пусто отдаёт оба языка."""
    data = _load_styles()
    if lang in ("en", "ru"):
        return {"styles": [{"key": s["key"], "value": s["value"],
                            "label": s[lang]["label"], "desc": s[lang]["desc"]}
                           for s in data]}
    return {"styles": data}


# ───────────────────── клип: превью под лимит и ссылка ─────────────────────

_preview_jobs: dict[str, dict] = {}
_preview_lock = threading.Lock()


def _preview_name(clip: str) -> str:
    """Имя превью выводится из имени клипа детерминированно: перезапуск API не
    теряет уже собранное превью и не заставляет считать его заново."""
    return "preview_" + os.path.splitext(os.path.basename(clip))[0] + ".mp4"


def _sweep_previews(upload_dir: str) -> None:
    """Превью — производное: держать его вечно значит удвоить диск на каждый
    клип. Старые чистим, оригинал пересоберёт по первому запросу."""
    if PREVIEW_TTL_DAYS <= 0:
        return
    edge = time.time() - PREVIEW_TTL_DAYS * 86400
    try:
        for name in os.listdir(upload_dir):
            if not name.startswith("preview_"):
                continue
            path = os.path.join(upload_dir, name)
            if os.path.getmtime(path) < edge:
                os.remove(path)
    except OSError as e:
        log.warning("бот: чистка превью не прошла: %s", e)


def preview_plan(duration_sec: int) -> tuple[int, int, bool]:
    """(битрейт кбит/с, сколько секунд берём, тизер?) — арифметика превью.

    Битрейт считается ОТ ДЛИТЕЛЬНОСТИ, а не берётся константой: 45 МБ на три
    минуты и на шесть — это разные битрейты. Если даже нижняя граница качества
    не влезает в лимит, честнее отдать первую минуту и сказать об этом, чем
    растянуть мыло на весь клип.

    Отдельной функцией, потому что это единственное здесь, что можно проверить
    без ffmpeg, — и единственное, что молча испортит отправку, если ошибётся."""
    dur = max(1, int(duration_sec or 0))
    kbps = int(PREVIEW_TARGET_MB * 8192 / dur) - PREVIEW_AUDIO_KBPS
    if kbps < PREVIEW_MIN_KBPS:
        return PREVIEW_MIN_KBPS, min(dur, PREVIEW_TEASER_SEC), True
    return max(PREVIEW_MIN_KBPS, min(PREVIEW_MAX_KBPS, kbps)), dur, False


def _build_preview(track_id: int, clip: str, duration: int) -> None:
    """Транскод клипа под целевой размер (см. preview_plan)."""
    core = _core()
    dst_name = _preview_name(clip)
    src = os.path.join(core.UPLOAD_DIR, os.path.basename(clip))
    dst = os.path.join(core.UPLOAD_DIR, dst_name)
    teaser = False
    try:
        dur = max(1, int(duration or core._ffprobe_duration(src)))
        kbps, dur, teaser = preview_plan(dur)
        cmd = ["ffmpeg", "-y"]
        if teaser:
            cmd += ["-t", str(PREVIEW_TEASER_SEC)]
        cmd += [
            "-i", src,
            "-vf", f"scale={PREVIEW_W}:-2",
            "-c:v", "libx264", "-preset", "veryfast", "-pix_fmt", "yuv420p",
            "-b:v", f"{kbps}k", "-maxrate", f"{int(kbps * 1.25)}k",
            "-bufsize", f"{kbps * 2}k",
            "-c:a", "aac", "-b:a", f"{PREVIEW_AUDIO_KBPS}k",
            "-movflags", "+faststart", dst,
        ]
        r = subprocess.run(cmd, capture_output=True, timeout=1800)
        if r.returncode != 0 or not os.path.exists(dst):
            raise RuntimeError((r.stderr or b"")[-400:].decode("utf-8", "replace"))

        # Владелец превью = владелец клипа, иначе /api/media его не отдаст.
        db = core.SessionLocal()
        try:
            track = db.get(core.Track, track_id)
            owner = track.project.owner_id if track and track.project else None
            core._reg_file(db, dst_name, owner)
            db.commit()
        finally:
            db.close()

        with _preview_lock:
            _preview_jobs[clip] = {
                "status": "done", "filename": dst_name, "teaser": teaser,
                "size": os.path.getsize(dst), "seconds": dur,
            }
        log.info("бот: превью клипа трека %s готово (%s МБ%s)", track_id,
                 round(os.path.getsize(dst) / 1048576, 1), ", тизер" if teaser else "")
        _sweep_previews(core.UPLOAD_DIR)
    except Exception as e:  # noqa: BLE001
        with _preview_lock:
            _preview_jobs[clip] = {"status": "error", "error": str(e)[:300]}
        log.warning("бот: превью клипа трека %s не собралось: %s", track_id, e)


@router.post("/internal/clip-preview")
async def clip_preview(request: Request):
    """Превью клипа под лимит Telegram: {status: running|done|error}.

    Асинхронный, с опросом: транскод трёхминутного клипа — это минуты, и
    держать на нём HTTP-соединение бота значит однажды словить обрыв на
    середине. Транскод делает API, а не бот: файл лежит в его томе, тащить
    300 МБ в соседний контейнер незачем."""
    if not _key_ok(request):
        return _deny(503 if not BOT_INTERNAL_KEY else 403, "internal_key", "ключ не сошёлся")
    core = _core()
    body = await request.json()
    track_id = int(body.get("track_id") or 0)
    db = core.SessionLocal()
    try:
        track = db.get(core.Track, track_id)
        if not track or not track.clip_filename:
            return _deny(404, "no_clip", "у трека нет собранного клипа")
        clip = track.clip_filename
        duration = int(track.audio_duration_sec or 0)
    finally:
        db.close()

    dst = os.path.join(core.UPLOAD_DIR, _preview_name(clip))
    if os.path.exists(dst):
        return {"status": "done", "filename": _preview_name(clip),
                "size": os.path.getsize(dst), "teaser": False}
    with _preview_lock:
        job = _preview_jobs.get(clip)
        if job and job.get("status") == "running":
            return {"status": "running"}
        if job and job.get("status") == "error" and not body.get("retry"):
            return {"status": "error", "error": job.get("error", "")}
        _preview_jobs[clip] = {"status": "running"}
    threading.Thread(target=_build_preview, args=(track_id, clip, duration),
                     daemon=True).start()
    return {"status": "running"}


@router.post("/internal/clip-link")
async def clip_link(request: Request):
    """Подписанная временная ссылка на ОРИГИНАЛ клипа.

    Оригинал 1080×1920 в Telegram не влезает почти никогда, поэтому рядом с
    превью всегда нужна ссылка на настоящий файл. Подпись и TTL — из
    social.clip_link_token, ничего нового не изобретаем."""
    if not _key_ok(request):
        return _deny(503 if not BOT_INTERNAL_KEY else 403, "internal_key", "ключ не сошёлся")
    core = _core()
    body = await request.json()
    db = core.SessionLocal()
    try:
        track = db.get(core.Track, int(body.get("track_id") or 0))
        if not track or not track.clip_filename:
            return _deny(404, "no_clip", "у трека нет собранного клипа")
        name = track.clip_filename
        size = 0
        path = os.path.join(core.UPLOAD_DIR, name)
        if os.path.exists(path):
            size = os.path.getsize(path)
    finally:
        db.close()
    try:
        import social  # noqa: PLC0415
        return {"url": social.public_clip_url(name), "ttl": social.LINK_TTL_S, "size": size}
    except Exception as e:  # noqa: BLE001
        return _deny(500, "link_failed", str(e)[:200])


@router.get("/api/social/clip/{token}")
def social_clip(token: str, request: Request):
    """Публичная отдача клипа по подписанному пропуску.

    Роут описан в social.py, но до сих пор нигде не смонтирован — без него
    ссылка на оригинал ведёт в никуда. Отдаём через `_media_response`, чтобы
    работала перемотка (Range), как и в приватной раздаче."""
    core = _core()
    try:
        import social  # noqa: PLC0415
        name = social.clip_from_token(token)
    except Exception:  # noqa: BLE001
        name = ""
    if not name:
        return _deny(404, "bad_token", "ссылка просрочена или неверна")
    path = os.path.join(core.UPLOAD_DIR, os.path.basename(name))
    if not os.path.exists(path):
        return _deny(404, "no_file", "файл не найден")
    return core._media_response(path, request)


# ───────────────────────── отмена супергенерации ─────────────────────────

@router.post("/internal/supergen-cancel")
async def supergen_cancel(request: Request):
    """Попросить конвейер остановиться после текущей сцены.

    Работает ТОЛЬКО если применён необязательный раздел bot_patch.md: колонка
    `Track.supergen_cancel` плюс две проверки флага внутри `_run_supergen`.
    Без них честно отвечаем `unsupported` — и бот не рисует кнопку «Стоп»,
    вместо того чтобы показывать кнопку, которая ничего не делает."""
    if not _key_ok(request):
        return _deny(503 if not BOT_INTERNAL_KEY else 403, "internal_key", "ключ не сошёлся")
    core = _core()
    if not hasattr(core.Track, "supergen_cancel"):
        return _deny(501, "unsupported", "отмена не подключена (см. backend/bot_patch.md)")
    db = core.SessionLocal()
    try:
        track = db.get(core.Track, int((await request.json()).get("track_id") or 0))
        if not track:
            return _deny(404, "no_track", "трек не найден")
        track.supergen_cancel = True
        db.commit()
        return {"ok": True}
    finally:
        db.close()


# ───────────────────────────── самодиагностика ─────────────────────────────

@router.get("/internal/bot-capabilities")
def capabilities(request: Request):
    """Что из необязательных частей патча реально применено.

    Бот спрашивает это на старте и подстраивает интерфейс: нет отмены — нет
    кнопки «Стоп», нет структурного прогресса — считает его сам по сценам.
    Так одна и та же сборка бота работает и до, и после применения патча."""
    if not _key_ok(request):
        return _deny(503 if not BOT_INTERNAL_KEY else 403, "internal_key", "ключ не сошёлся")
    core = _core()
    return {
        "ok": True,
        "supergen_cancel": hasattr(core.Track, "supergen_cancel"),
        "supergen_stage": hasattr(core.Track, "supergen_stage"),
        "styles": len(_load_styles()),
        "public_base": core.PUBLIC_BASE_URL,
        "brand": os.environ.get("BRAND_NAME", "lolq.ai"),
    }


def mount(app) -> None:
    """Подключение из main.py — две строки в патче, остальное здесь.

    Порядок вызова не важен: если StaticFiles уже смонтирован на «/», он
    перехватывал бы все наши роуты (и POST /internal/tg-session отвечал бы
    405 Method Not Allowed — молчаливо и совершенно непонятно), поэтому
    корневой mount переставляется в конец списка. Так патч работает и в
    начале, и в конце main.py."""
    app.include_router(router)
    try:
        from starlette.routing import Mount  # noqa: PLC0415
        routes = app.router.routes
        # path у Mount("/") нормализован в "" — проверяем оба варианта.
        catchall = [r for r in routes if isinstance(r, Mount) and r.path in ("", "/")]
        for r in catchall:
            routes.remove(r)
            routes.append(r)
        if catchall:
            log.info("бот: статика на «/» переставлена в конец, чтобы не "
                     "перехватывать роуты бота")
    except Exception as e:  # noqa: BLE001
        log.warning("бот: не удалось проверить порядок роутов (%s). Подключай "
                    "bot_api.mount(app) ДО app.mount(\"/\", StaticFiles…)", e)
    if BOT_INTERNAL_KEY and len(BOT_INTERNAL_KEY) < 24:
        log.warning("бот: BOT_INTERNAL_KEY короче 24 символов — это ключ к "
                    "выписке сессий, сделай его длинным и случайным")
    log.info("бот: служебные роуты подключены (ключ %s)",
             "задан" if BOT_INTERNAL_KEY else "НЕ ЗАДАН — контур выключен")
