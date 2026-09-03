#!/usr/bin/env python3
"""lolq.ai в Telegram: весь конвейер генерации клипа в чате.

ЧТО ЭТО. Отдельный процесс, который разговаривает с человеком в Telegram и
делает за него ровно то же, что студия на сайте: принимает mp3, спрашивает
стиль и героя, запускает супергенерацию, показывает живой прогресс и отдаёт
готовый клип. Плюс ручной режим по сценам, «мои клипы», токены с тарифом и
партнёрский раздел.

ТРИ РЕШЕНИЯ, ИЗ КОТОРЫХ СЛЕДУЕТ ВСЁ ОСТАЛЬНОЕ
------------------------------------------------------------------
1. Бот НЕ заводит свою авторизацию. Он опознаёт человека по tg_id, получает
   от API готовую куку `qv_session` (см. backend/bot_api.py) и дальше ходит в
   ОБЫЧНЫЕ публичные роуты. Поэтому аккаунт, токены, тариф, проекты и партнёрка
   у бота и сайта общие буквально, а не «синхронизируются». Ни одной новой
   проверки прав здесь нет — значит, нет и второго места, где их можно забыть.

2. Поллинг, а не вебхук. Входящие соединения Telegram → РФ-сервер не проходят,
   setWebhook мёртв (по этой же причине поллингом работают все живые боты
   соседнего проекта). Заодно не нужен публичный TLS-эндпоинт, а переезд бота
   на другой хост не требует ничего, кроме перезапуска.

3. Готовый клип 1080×1920 в Telegram НЕ ВЛЕЗАЕТ — почти никогда. Потолок
   отдачи файла ботом 50 МБ, а три минуты CRF 18 весят 180–330 МБ. Поэтому
   бот отдаёт превью-транскод под 45 МБ видео-сообщением И кнопку на
   подписанную ссылку с оригиналом. Это не обходной манёвр, а часть продукта:
   300 МБ в чате — плохой подарок мобильному интернету.

ЗАВИСИМОСТИ. Только httpx, который в проекте уже есть. Bot API — это обычный
HTTP с JSON и multipart; aiogram здесь дал бы свой роутер, свою FSM и свой
жизненный цикл поверх наших, а взамен закрыл бы полторы сотни строк
транспорта. Не берём.

ЗАПУСК: `python bot.py` (или `python -m bot`). Без BOT_TOKEN процесс честно
пишет в лог, что бот выключен, и спит — чтобы контейнер с restart:unless-stopped
не крутил бесконечный рестарт-луп.
"""
from __future__ import annotations

import asyncio
import contextlib
import html
import json
import logging
import re
import math
import os
import random
import sqlite3
import time
from collections import deque
from urllib.parse import quote

import httpx

log = logging.getLogger("qlolbot")
logging.basicConfig(level=os.environ.get("BOT_LOG_LEVEL", "INFO"),
                    format="%(asctime)s [qlolbot] %(levelname)s %(message)s")
# httpx печатает полный URL запроса на INFO, а в URL Bot API лежит токен.
logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("httpcore").setLevel(logging.WARNING)


class _TokenMask(logging.Filter):
    """Страховка второго уровня: любой /bot<токен>/ в сообщении → /bot***/."""
    _RE = re.compile(r"/bot[0-9]+:[A-Za-z0-9_-]+/")

    def filter(self, record: logging.LogRecord) -> bool:
        try:
            if record.args:
                record.msg = str(record.msg) % record.args
                record.args = ()
            record.msg = self._RE.sub("/bot***/", str(record.msg))
        except Exception:  # noqa: BLE001 — фильтр не должен ронять лог
            pass
        return True


for _h in logging.getLogger().handlers:
    _h.addFilter(_TokenMask())

# ─────────────────────────────── настройки ───────────────────────────────

BOT_TOKEN = os.environ.get("BOT_TOKEN", "").strip()
BOT_USERNAME = os.environ.get("BOT_USERNAME", os.environ.get("TG_LOGIN_BOT_USERNAME", "")).lstrip("@")
MINIAPP_NAME = os.environ.get("TG_MINIAPP_NAME", "app").strip().lstrip("/")
TELEGRAM_API_BASE = os.environ.get("TELEGRAM_API_BASE", "https://api.telegram.org").rstrip("/")

# Выход наружу. Из РФ api.telegram.org напрямую может не открыться, поэтому
# маршруты пробуются по очереди и рабочий запоминается: сначала прокси (если
# задан), потом напрямую. Формат BOT_PROXY_URL — как у httpx:
# socks5://host:port, http://user:pass@host:port. Несколько — через запятую.
BOT_PROXY_URL = os.environ.get("BOT_PROXY_URL", "").strip()

# Наш API. Внутри docker-сети это http://rapclips:8000, снаружи — публичный
# домен. Служебные роуты /internal/* наружу не проксируются, поэтому при
# разнесённых хостах бот должен ходить именно во внутренний адрес.
API_BASE = os.environ.get("QLOL_API_BASE", "http://127.0.0.1:8000").rstrip("/")
INTERNAL_KEY = os.environ.get("BOT_INTERNAL_KEY", "").strip()
PUBLIC_BASE_URL = os.environ.get("PUBLIC_BASE_URL", "https://qlolapp.art").rstrip("/")
BRAND = os.environ.get("BRAND_NAME", "lolq.ai")

BOT_DB_PATH = os.environ.get("BOT_DB_PATH", "/data/bot.db")
TMP_DIR = os.environ.get("BOT_TMP_DIR", "/tmp/qlolbot")

ADMIN_TG_IDS = {x.strip() for x in os.environ.get("BOT_ADMIN_TG_ID", "").split(",") if x.strip()}
SUPPORT_CONTACT = os.environ.get("BOT_SUPPORT_CONTACT", "").strip()

# Лимиты Telegram, которые нельзя обойти — только заложить.
TG_DOWNLOAD_LIMIT = 20 * 1024 * 1024   # getFile: бот не скачает больше 20 МБ
TG_UPLOAD_LIMIT = 49 * 1024 * 1024     # sendVideo: 50 МБ, берём запас
TG_PHOTO_LIMIT = 10 * 1024 * 1024
CAPTION_LIMIT = 1024
TEXT_LIMIT = 4096
ALBUM_LIMIT = 10

# Темп отправки. Лимит — 1 сообщение в секунду в чат и ~30/сек суммарно;
# берём запас, потому что штраф за перебор (429) дороже, чем полсекунды.
PER_CHAT_GAP = float(os.environ.get("BOT_PER_CHAT_GAP", "1.05"))
GLOBAL_RATE = int(os.environ.get("BOT_GLOBAL_RATE", "25"))
# Как часто разрешено ПЕРЕРИСОВЫВАТЬ живое сообщение прогресса. Реже, чем
# лимит: правка сообщения тоже сообщение, а бару спешить некуда.
EDIT_GAP = float(os.environ.get("BOT_EDIT_GAP", "6"))

POLL_TIMEOUT = int(os.environ.get("BOT_LONGPOLL_TIMEOUT", "45"))
WATCH_POLL = float(os.environ.get("BOT_WATCH_POLL", "8"))

# Как долго доверять закешированной паре «tg_id → аккаунт». РАНЬШЕ было 30
# дней, и это оказалось багом: когда tg_id владельца перевесили на другой
# аккаунт прямо в базе, бот ещё месяц ходил под старым uid (кука валидна,
# 401 не приходит) и честно показывал «Клипов пока нет». Единственный
# источник правды — User.tg_id, поэтому сверяемся с ним через служебный
# роут не реже, чем раз в этот интервал: это один SELECT по индексу.
SESSION_RESYNC_S = int(os.environ.get("BOT_SESSION_RESYNC_S", "600"))

# Цена в звёздах. Telegram требует продавать цифровые товары ТОЛЬКО за Stars,
# внешние платёжные ссылки внутри бота запрещены. Курс покупки звёзд у людей
# ~50 XTR за доллар, но магазин забирает свою долю, поэтому номинал берём с
# наценкой — иначе продажа через Telegram идёт в убыток.
# Формула цены и потолки живут в backend/stars.py — ОДНИ на бота и на мини-апп.
# Своей копии здесь больше нет: два прайса в одном магазине разъезжаются в
# первый же день, когда кто-то поправит только один из них.
from stars import (  # noqa: E402
    STARS_SUB_CAP, STARS_SUB_PERIOD, price_override, stars_price,
)

MAX_AUDIO_SEC = int(os.environ.get("BOT_MAX_AUDIO_SEC", "480"))
MIN_AUDIO_SEC = int(os.environ.get("BOT_MIN_AUDIO_SEC", "20"))

os.makedirs(TMP_DIR, exist_ok=True)


# ═══════════════════════════ транспорт Telegram ═══════════════════════════

class TgError(Exception):
    """Ошибка от Bot API: код и описание как есть, без домыслов."""

    def __init__(self, code: int, description: str, retry_after: int = 0):
        super().__init__(f"{code}: {description}")
        self.code, self.description, self.retry_after = code, description, retry_after


class Pacer:
    """Очередь отправки: не быстрее 1 сообщения в секунду в чат и не быстрее
    GLOBAL_RATE в секунду суммарно.

    Блокировка держится на всё время отправки, поэтому два сообщения в один
    чат физически не могут уехать одновременно и порядок не путается."""

    def __init__(self) -> None:
        self._locks: dict[int, asyncio.Lock] = {}
        self._last: dict[int, float] = {}
        self._recent: deque[float] = deque()

    @contextlib.asynccontextmanager
    async def slot(self, chat_id: int | None):
        if chat_id is None:
            yield
            return
        lock = self._locks.setdefault(chat_id, asyncio.Lock())
        async with lock:
            gap = self._last.get(chat_id, 0.0) + PER_CHAT_GAP - time.monotonic()
            if gap > 0:
                await asyncio.sleep(gap)
            while True:
                now = time.monotonic()
                while self._recent and now - self._recent[0] > 1.0:
                    self._recent.popleft()
                if len(self._recent) < GLOBAL_RATE:
                    break
                await asyncio.sleep(1.0 - (now - self._recent[0]))
            self._recent.append(time.monotonic())
            try:
                yield
            finally:
                self._last[chat_id] = time.monotonic()


class Telegram:
    """Клиент Bot API поверх httpx с перебором маршрутов выхода.

    Маршрут выбирается один раз и запоминается; переключаемся только когда
    текущий действительно отвалился. Так в нормальной жизни нет лишних
    попыток, а при падении прокладки бот сам находит живой путь."""

    def __init__(self, token: str) -> None:
        self.token = token
        self.base = f"{TELEGRAM_API_BASE}/bot{token}"
        self.file_base = f"{TELEGRAM_API_BASE}/file/bot{token}"
        routes: list[tuple[str, str | None]] = []
        for raw in BOT_PROXY_URL.split(","):
            raw = raw.strip()
            if raw:
                routes.append((f"proxy {raw.split('@')[-1]}", raw))
        routes.append(("direct", None))
        self.routes = routes
        self.route = 0
        self._clients: dict[int, httpx.AsyncClient] = {}
        self.pacer = Pacer()
        self._last_net_complaint = 0.0
        self._check_socks()

    @staticmethod
    def _check_socks() -> None:
        """SOCKS-прокси httpx умеет только с пакетом socksio.

        Голый httpx падает на socks5:// с невнятным сообщением из недр
        библиотеки, а мост наружу у нас именно SOCKS — поэтому проверяем сразу
        и говорим точную команду, а не «нет связи с Telegram»."""
        if "socks" not in BOT_PROXY_URL.lower():
            return
        try:
            import socksio  # noqa: F401, PLC0415
        except ImportError:
            log.error("BOT_PROXY_URL указывает на SOCKS-прокси, но пакет socksio не "
                      "установлен — httpx не умеет SOCKS без него. Добавь в "
                      "backend/requirements.txt строку httpx[socks]==0.27.2 и "
                      "пересобери образ (см. backend/bot_patch.md).")

    def _client(self, idx: int) -> httpx.AsyncClient:
        if idx not in self._clients:
            name, proxy = self.routes[idx]
            self._clients[idx] = httpx.AsyncClient(
                proxy=proxy, timeout=httpx.Timeout(180.0, connect=25.0),
                follow_redirects=True)
        return self._clients[idx]

    async def close(self) -> None:
        for c in self._clients.values():
            with contextlib.suppress(Exception):
                await c.aclose()

    async def call(self, method: str, chat_id: int | None = None, *,
                   files: dict | None = None, timeout: float | None = None,
                   **params):
        """Вызов метода Bot API. Возвращает поле result.

        Ошибки Telegram (ok:false) поднимаются как TgError и разбираются
        вызывающим: 403 «bot was blocked» и 400 «message is not modified» —
        нормальная жизнь, а не аварии."""
        payload = {k: v for k, v in params.items() if v is not None}
        # chat_id — отдельный аргумент, потому что он же управляет темпом
        # отправки; в тело запроса его надо вернуть руками.
        if chat_id is not None:
            payload["chat_id"] = chat_id
        for key in ("reply_markup", "prices", "media"):
            if key in payload and not isinstance(payload[key], str):
                payload[key] = json.dumps(payload[key], ensure_ascii=False)

        async with self.pacer.slot(chat_id):
            return await self._send(method, payload, files, timeout)

    async def _send(self, method: str, payload: dict, files: dict | None,
                    timeout: float | None):
        attempts = 0
        tried_routes = 0
        while True:
            idx = self.route
            client = self._client(idx)
            try:
                if files:
                    r = await client.post(f"{self.base}/{method}", data=payload,
                                          files=files, timeout=timeout or 600.0)
                else:
                    r = await client.post(f"{self.base}/{method}", json=payload,
                                          timeout=timeout or 180.0)
            except (httpx.TransportError, httpx.TimeoutException) as e:
                tried_routes += 1
                if tried_routes >= len(self.routes):
                    self._complain(e)
                    raise TgError(0, f"нет связи с Telegram: {e}") from e
                self.route = (self.route + 1) % len(self.routes)
                log.warning("телеграм недоступен через «%s» (%s) — пробую «%s»",
                            self.routes[idx][0], str(e)[:120], self.routes[self.route][0])
                continue

            try:
                data = r.json()
            except ValueError:
                data = {"ok": False, "error_code": r.status_code,
                        "description": r.text[:200]}
            if data.get("ok"):
                if idx != 0 and tried_routes:
                    log.info("телеграм: рабочий маршрут — «%s»", self.routes[idx][0])
                return data.get("result")

            code = int(data.get("error_code") or r.status_code)
            desc = str(data.get("description") or "")
            retry = int((data.get("parameters") or {}).get("retry_after") or 0)
            if code == 429 and attempts < 5:
                # Ждём РОВНО столько, сколько сказал Telegram: своя эвристика
                # тут только удлиняет наказание.
                attempts += 1
                log.info("телеграм просит подождать %s с (%s)", retry or 1, method)
                await asyncio.sleep(retry or 1)
                continue
            if code >= 500 and attempts < 3:
                attempts += 1
                await asyncio.sleep(1.5 * attempts)
                continue
            raise TgError(code, desc, retry)

    def _complain(self, err: Exception) -> None:
        """Диагностика недоступности — не чаще раза в минуту, но подробная:
        молча ретраить в пустоту хуже, чем сказать, что именно не работает."""
        if time.monotonic() - self._last_net_complaint < 60:
            return
        self._last_net_complaint = time.monotonic()
        log.error(
            "Bot API недоступен ни одним маршрутом (%s). Проверь: (1) задан ли "
            "BOT_PROXY_URL — из РФ api.telegram.org напрямую обычно закрыт; "
            "(2) жив ли сам прокси; (3) не блокирует ли исходящие фаервол. "
            "Последняя ошибка: %s",
            ", ".join(n for n, _ in self.routes), str(err)[:200])

    async def download(self, file_id: str, dest: str) -> int:
        """Скачать файл пользователя. Лимит getFile — 20 МБ, и это не наш
        выбор: проверять размер надо ДО скачивания (см. handle_audio)."""
        info = await self.call("getFile", file_id=file_id)
        path = info.get("file_path")
        if not path:
            raise TgError(0, "телеграм не отдал путь к файлу")
        url = f"{self.file_base}/{path}"
        client = self._client(self.route)
        size = 0
        with open(dest, "wb") as fh:
            async with client.stream("GET", url, timeout=300.0) as resp:
                resp.raise_for_status()
                async for chunk in resp.aiter_bytes(262144):
                    fh.write(chunk)
                    size += len(chunk)
        return size


# ═══════════════════════ собственное хранилище бота ═══════════════════════

class Store:
    """SQLite бота — ОТДЕЛЬНАЯ база, не rapclips.db.

    Причина конкретная: основная база тоже SQLite, и второй пишущий процесс из
    другого контейнера даст «database is locked» ровно в тот момент, когда
    супергенерация коммитит прогресс. Единственный писатель основной базы —
    API; бот ходит к ней только по HTTP."""

    def __init__(self, path: str) -> None:
        os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
        self.db = sqlite3.connect(path, check_same_thread=False)
        self.db.row_factory = sqlite3.Row
        self.db.execute("PRAGMA journal_mode=WAL")
        self.db.executescript("""
        CREATE TABLE IF NOT EXISTS users(
            -- lang БЕЗ значения по умолчанию: пусто = «человек ещё не выбирал»,
            -- и тогда язык берётся из language_code клиента. С DEFAULT 'en'
            -- строка успевала создаться раньше /start (её заводит любое
            -- входящее сообщение), и русскоязычный получал английское
            -- приветствие — «не выбирал» было не отличить от «выбрал English».
            tg_id TEXT PRIMARY KEY, uid INTEGER, cookie TEXT, cookie_at REAL,
            lang TEXT, chat_id INTEGER, name TEXT,
            notify TEXT DEFAULT 'all', created REAL);
        CREATE TABLE IF NOT EXISTS state(tg_id TEXT PRIMARY KEY, data TEXT);
        CREATE TABLE IF NOT EXISTS watch(
            track_id INTEGER PRIMARY KEY, project_id INTEGER, tg_id TEXT,
            chat_id INTEGER, msg_id INTEGER, last_text TEXT, started REAL,
            pinned INTEGER DEFAULT 0);
        CREATE TABLE IF NOT EXISTS files(
            key TEXT PRIMARY KEY, file_id TEXT, ts REAL);
        CREATE TABLE IF NOT EXISTS kv(k TEXT PRIMARY KEY, v TEXT);
        """)
        self.db.commit()

    # — служебное —
    def get_kv(self, key: str, default=None):
        row = self.db.execute("SELECT v FROM kv WHERE k=?", (key,)).fetchone()
        return row["v"] if row else default

    def set_kv(self, key: str, value) -> None:
        self.db.execute("INSERT INTO kv(k,v) VALUES(?,?) "
                        "ON CONFLICT(k) DO UPDATE SET v=excluded.v", (key, str(value)))
        self.db.commit()

    # — пользователи —
    def user(self, tg_id: str) -> sqlite3.Row | None:
        return self.db.execute("SELECT * FROM users WHERE tg_id=?", (str(tg_id),)).fetchone()

    def upsert_user(self, tg_id: str, **fields) -> None:
        cur = self.user(tg_id)
        if not cur:
            self.db.execute("INSERT INTO users(tg_id, created) VALUES(?,?)",
                            (str(tg_id), time.time()))
        if fields:
            cols = ", ".join(f"{k}=?" for k in fields)
            self.db.execute(f"UPDATE users SET {cols} WHERE tg_id=?",
                            (*fields.values(), str(tg_id)))
        self.db.commit()

    # — FSM: чего бот ждёт от этого человека —
    def state(self, tg_id: str) -> dict:
        row = self.db.execute("SELECT data FROM state WHERE tg_id=?", (str(tg_id),)).fetchone()
        if not row:
            return {}
        try:
            return json.loads(row["data"])
        except ValueError:
            return {}

    def set_state(self, tg_id: str, data: dict | None) -> None:
        if not data:
            self.db.execute("DELETE FROM state WHERE tg_id=?", (str(tg_id),))
        else:
            self.db.execute("INSERT INTO state(tg_id,data) VALUES(?,?) "
                            "ON CONFLICT(tg_id) DO UPDATE SET data=excluded.data",
                            (str(tg_id), json.dumps(data, ensure_ascii=False)))
        self.db.commit()

    # — наблюдение за генерациями —
    def watch_add(self, track_id: int, project_id: int, tg_id: str,
                  chat_id: int, msg_id: int) -> None:
        self.db.execute(
            "INSERT INTO watch(track_id,project_id,tg_id,chat_id,msg_id,last_text,started)"
            " VALUES(?,?,?,?,?,'',?) ON CONFLICT(track_id) DO UPDATE SET "
            "project_id=excluded.project_id, chat_id=excluded.chat_id,"
            "msg_id=excluded.msg_id, last_text='', started=excluded.started",
            (int(track_id), int(project_id), str(tg_id), int(chat_id),
             int(msg_id), time.time()))
        self.db.commit()

    def watch_all(self) -> list[sqlite3.Row]:
        return self.db.execute("SELECT * FROM watch").fetchall()

    def watch_touch(self, track_id: int, text: str) -> None:
        self.db.execute("UPDATE watch SET last_text=? WHERE track_id=?", (text, int(track_id)))
        self.db.commit()

    def watch_drop(self, track_id: int) -> None:
        self.db.execute("DELETE FROM watch WHERE track_id=?", (int(track_id),))
        self.db.commit()

    def watch_for(self, tg_id: str) -> list[sqlite3.Row]:
        return self.db.execute("SELECT * FROM watch WHERE tg_id=?", (str(tg_id),)).fetchall()

    def watch_has(self, track_id: int) -> bool:
        return self.db.execute("SELECT 1 FROM watch WHERE track_id=?",
                               (int(track_id),)).fetchone() is not None

    # — кэш file_id: один раз залил, дальше шлём ссылкой —
    def file_id(self, key: str) -> str | None:
        row = self.db.execute("SELECT file_id FROM files WHERE key=?", (key,)).fetchone()
        return row["file_id"] if row else None

    def remember_file(self, key: str, file_id: str) -> None:
        self.db.execute("INSERT INTO files(key,file_id,ts) VALUES(?,?,?) "
                        "ON CONFLICT(key) DO UPDATE SET file_id=excluded.file_id",
                        (key, file_id, time.time()))
        self.db.commit()

    def forget_file(self, key: str) -> None:
        self.db.execute("DELETE FROM files WHERE key=?", (key,))
        self.db.commit()


# ═════════════════════════════ клиент нашего API ═════════════════════════════

class ApiError(Exception):
    """Ответ API с машиночитаемым кодом. Текст наружу человеку НЕ отдаём как
    есть: сообщения бэкенда местами по-русски, а бот двуязычный."""

    def __init__(self, status: int, code: str, detail: str = "", data: dict | None = None):
        super().__init__(f"{status} {code}: {detail}")
        self.status, self.code, self.detail, self.data = status, code, detail, data or {}


class Qlol:
    """HTTP-клиент к нашему же API — от имени конкретного человека.

    Кука кэшируется в базе бота; при 401 (сессия протухла, аккаунт пересоздан)
    перевыписывается один раз и запрос повторяется."""

    def __init__(self, store: Store) -> None:
        self.store = store
        self.http = httpx.AsyncClient(base_url=API_BASE, timeout=httpx.Timeout(120.0, connect=15.0))
        self.caps: dict = {}

    async def close(self) -> None:
        with contextlib.suppress(Exception):
            await self.http.aclose()

    async def _internal(self, path: str, payload: dict) -> dict:
        r = await self.http.post(path, json=payload,
                                 headers={"X-Internal-Key": INTERNAL_KEY})
        if r.status_code >= 400:
            body = _safe_json(r)
            raise ApiError(r.status_code, str(body.get("error") or "internal"),
                           str(body.get("detail") or r.text[:200]), body)
        return r.json()

    async def session(self, tg_user: dict, ref: str = "", force: bool = False) -> str:
        """Кука человека. Кэш короткий (SESSION_RESYNC_S), потом ПЕРЕСВЕРКА
        по tg_id: аккаунт могли перепривязать на сайте или руками в базе, а
        кука со старым uid остаётся формально валидной — 401 не придёт
        никогда, и без пересверки бот вечно живёт в чужом пустом аккаунте."""
        tg_id = str(tg_user["id"])
        row = self.store.user(tg_id)
        if row and row["cookie"] and not force and time.time() - (row["cookie_at"] or 0) < SESSION_RESYNC_S:
            return row["cookie"]
        data = await self._internal("/internal/tg-session", {
            "tg_id": tg_id,
            "username": tg_user.get("username") or "",
            "first_name": tg_user.get("first_name") or "",
            "last_name": tg_user.get("last_name") or "",
            "ref": ref,
        })
        new_uid = int(data["user"]["id"])
        if row and row["uid"] and int(row["uid"]) != new_uid:
            log.info("tg %s: аккаунт сменился %s → %s — пересинхронизировал по tg_id",
                     tg_id, row["uid"], new_uid)
        self.store.upsert_user(tg_id, cookie=data["cookie"], cookie_at=time.time(),
                               uid=new_uid, name=data["user"]["name"])
        return data["cookie"]

    async def req(self, tg_user: dict, method: str, path: str, *, retry_auth: bool = True,
                  **kw):
        cookie = await self.session(tg_user)
        headers = dict(kw.pop("headers", {}) or {})
        headers["Cookie"] = f"qv_session={cookie}"
        r = await self.http.request(method, path, headers=headers, **kw)
        if r.status_code == 401 and retry_auth:
            await self.session(tg_user, force=True)
            return await self.req(tg_user, method, path, retry_auth=False, **kw)
        if r.status_code >= 400:
            body = _safe_json(r)
            raise ApiError(r.status_code, str(body.get("error") or ""),
                           str(body.get("detail") or r.text[:300]), body)
        if r.headers.get("content-type", "").startswith("application/json"):
            return r.json()
        return r.content

    async def stream_to(self, tg_user: dict, path: str, dest: str,
                        limit: int | None = None) -> int:
        """Скачать медиа нашего API в файл. limit — стоп-кран: не тянуть 300 МБ
        только чтобы потом упереться в лимит Telegram."""
        cookie = await self.session(tg_user)
        size = 0
        async with self.http.stream("GET", path, headers={"Cookie": f"qv_session={cookie}"},
                                    timeout=600.0) as r:
            if r.status_code >= 400:
                raise ApiError(r.status_code, "media", f"не отдался файл {path}")
            with open(dest, "wb") as fh:
                async for chunk in r.aiter_bytes(262144):
                    fh.write(chunk)
                    size += len(chunk)
                    if limit and size > limit:
                        raise ApiError(413, "too_big", "файл больше лимита")
        return size

    async def media_size(self, tg_user: dict, path: str) -> int:
        """Размер файла без скачивания: Range-запрос на первый байт. HEAD не
        подходит — роуты медиа зарегистрированы только на GET."""
        cookie = await self.session(tg_user)
        r = await self.http.get(path, headers={"Cookie": f"qv_session={cookie}",
                                               "Range": "bytes=0-0"})
        if r.status_code >= 400:
            return 0  # 0 = «не знаю»; звать на помощь превью безопаснее догадки
        rng = r.headers.get("content-range") or ""
        if "/" in rng:
            with contextlib.suppress(ValueError):
                return int(rng.split("/")[-1])
        with contextlib.suppress(ValueError):
            return int(r.headers.get("content-length") or 0)
        return 0

    async def capabilities(self) -> dict:
        if not self.caps:
            try:
                r = await self.http.get("/internal/bot-capabilities",
                                        headers={"X-Internal-Key": INTERNAL_KEY})
                self.caps = r.json() if r.status_code == 200 else {}
            except Exception as e:  # noqa: BLE001
                log.warning("не получил список возможностей API: %s", e)
                self.caps = {}
        return self.caps


def _safe_json(r: httpx.Response) -> dict:
    try:
        body = r.json()
        return body if isinstance(body, dict) else {"detail": str(body)[:300]}
    except ValueError:
        return {"detail": r.text[:300]}


# ═══════════════════════════════ язык и тексты ═══════════════════════════════
# EN — канон сервиса, RU — второй язык. Структура зеркальна frontend/i18n.js.

STR = {
    "en": {
        "hello": (
            "<b>{brand} — music videos made by AI.</b>\n\n"
            "Send me an MP3. I'll write the story, cut it into scenes, draw every "
            "frame, animate them and assemble the clip with your track on top.\n\n"
            "You have <b>{points} points</b> — that's one full 3-minute clip, on us.\n\n"
            "🎵 <i>Just send an audio file to begin.</i>"),
        # Запасное приветствие без обещания клипа: если норма бесплатного
        # тарифа или цена сцены изменятся, первое приветствие станет неправдой,
        # а проверить его будет некому. Считаем по movies_left из аккаунта.
        "hello_plain": (
            "<b>{brand} — music videos made by AI.</b>\n\n"
            "Send me an MP3. I'll write the story, cut it into scenes, draw every "
            "frame, animate them and assemble the clip with your track on top.\n\n"
            "You have <b>{points} points</b>.\n\n"
            "🎵 <i>Just send an audio file to begin.</i>"),
        "hello_back": ("Welcome back, {name}.\n<b>{points} points</b> · {plan}\n\n"
                       "Send a track — or pick up where you left off."),
        "menu_clips": "🎬 My clips", "menu_chars": "👥 Characters",
        "menu_points": "💎 Points", "menu_settings": "⚙️ Settings",
        "menu_trends": "🔥 Trends", "menu_mockup": "📦 Mockups",
        "menu_model3d": "🧊 3D model",
        "trends_title": "🔥 <b>Trends</b> — pick one, send your photo, get the video.",
        "trends_empty": "No trends right now — check back later.",
        "trend_pick": "🔥 <b>{title}</b>\nSend me the photo to star in this trend.",
        "gen_confirm": "Generate for ⚡{n}?",
        "yes_go": "✅ Yes", "no_cancel": "✖️ Cancel",
        "gen_started": ("🚀 Accepted. I'll send the result here when it's ready — "
                        "you can close the chat."),
        "trend_ready": "🔥 <b>{title}</b> is ready!",
        "gen_failed": ("⚠️ <b>{title}</b> didn't work out: {why}\n"
                       "The points went back to your balance."),
        "evt_clip_done": "🎉 <b>{title}</b> is ready — sending it over.",
        "evt_clip_fail": ("⚠️ <b>{title}</b> stopped: {why}\n"
                          "Everything already generated is paid for — "
                          "restarting won't charge for it again."),
        "mockup_title": ("📦 <b>Mockups</b> — a product shot from one photo.\n"
                         "Frame price: <b>⚡{n}</b>."),
        "mockup_pick": "📦 <b>{title}</b>\nSend me a photo of your product.",
        "mockup_ready": "📦 <b>{title}</b> — the shot is ready.",
        "model3d_ask": ("🧊 Send a photo of your hero — I'll draw a model sheet: "
                        "the same face from every angle."),
        "char_name_ask": "What's the hero's name? One or two words.",
        "open_app": "📱 Open the app",
        "no_photo": "The photo got lost — start over, please.",
        "how_it_works": "▶️ How it works", "lang_btn": "🌐 EN / RU",
        "help": (
            "<b>How it works</b>\n\n"
            "1. Send an <b>MP3</b> (up to 20 MB — that's the Telegram limit for bots).\n"
            "2. Pick a <b>look</b> — up to 3 presets blend into one, the first is the base.\n"
            "3. Tell me <b>who's in the frame</b> — the hero stays the same in every scene.\n"
            "4. Press <b>Make the clip</b> and close the chat. I'll ping you when it's done.\n\n"
            "Under the hood: story → storyboard → first and last frame of every scene → "
            "animation → assembly with your track.\n\n"
            "<b>Commands</b>\n"
            "/new — start a new clip\n/clips — my clips\n/points — points and plan\n"
            "/partners — referral programme\n/status — what's running now\n"
            "/settings — language and notifications\n/site — open the web studio\n"
            "/paysupport — payment questions"),
        "send_audio": "🎵 Send me an audio file to start a clip.",
        "audio_too_big": (
            "That file is <b>{mb} MB</b> — Telegram only lets me download up to 20 MB.\n\n"
            "Export an MP3 at 320 kbps (a 3-minute track is about 7 MB), or upload the "
            "original in the web studio — same account, same points:\n{url}"),
        "audio_too_long": "That track is {mm}:{ss} long. I take up to {max} minutes.",
        "audio_too_short": "That's only {sec} seconds. I need at least {min} to make a clip.",
        "audio_got": ("🎧 <b>{title}</b> · {dur}\n"
                      "About <b>{scenes} scenes</b> — one per ~6 seconds.\n"
                      "Engine: <b>{engine}</b> · cost <b>{cost} points</b> · "
                      "you have <b>{points}</b>.\n\nNow pick the look."),
        "pick_style": ("<b>Pick a look</b> — up to 3, the first one is the base.\n\n{picked}"),
        "picked_none": "<i>Nothing picked yet.</i>",
        "style_done": "✅ Done", "style_rand": "🎲 Surprise me",
        "style_own": "✍️ Describe my own",
        "style_own_ask": "Describe the look in your own words — one or two lines.",
        "style_need": "Pick at least one look first.",
        "who": ("<b>Who's in the frame?</b>\n"
                "The hero stays the same in every scene — that's what makes it a film "
                "and not 30 random pictures."),
        "who_text": "✍️ Describe in one line", "who_photo": "📸 Send photos",
        "who_lib": "📚 From my characters",
        "who_ask": ("Describe your hero in one line — name, look, vibe.\n"
                    "<i>Example: Mark, 25, shaved head, black bomber, gold chain, "
                    "always deadpan.</i>"),
        "who_photo_ask": ("Send 1–3 photos of the hero (as photos, not files). "
                          "When you're done, press «Ready»."),
        "who_photo_ok": "📸 Photo {n} saved.",
        "who_ready": "✅ Ready",
        "char_added": "👤 <b>{name}</b> is in the cast.",
        "model_btn": "🎭 Build a model sheet",
        "model_need_desc": ("Describe the hero in one line first — the model sheet is "
                            "drawn from the description, the photos only hold the face."),
        "model_wait": "🎭 Drawing the model sheet — four views of your hero…",
        "model_done": "🎭 Model sheet is ready — the face will hold across every scene.",
        "confirm": ("🎬 <b>{title}</b>\n"
                    "Look: <b>{style}</b>\n"
                    "Hero: <b>{hero}</b>\n"
                    "~<b>{scenes} scenes</b> · engine <b>{engine}</b>\n\n"
                    "<b>{cost} points</b> — you'll have <b>{left}</b> after.\n"
                    "Takes about <b>{eta}</b>. I'll ping you when it's done — "
                    "you can close the chat."),
        "go": "🚀 Make the clip", "manual": "🎛 Manual mode",
        "change_style": "✏️ Change look", "extras": "⚙️ Extras",
        "rename": "✏️ Rename", "rename_ask": "Send a new title for the track.",
        "extras_title": ("<b>Extras</b> — everything here is optional.\n\n"
                         "Lyrics make the storyboard follow the words. The director's "
                         "note is where you say what the clip is about."),
        "ex_lyrics": "📝 Lyrics", "ex_comment": "🎙 Director's note",
        "ex_grain": "🎞 Film grain: {v}", "ex_nostory": "🎲 No story: {v}",
        "ex_lyrics_ask": "Send the lyrics as one message.",
        "ex_comment_ask": "What is this clip about? One or two lines is enough.",
        "on": "on", "off": "off",
        "saved": "Saved.",
        "started": "🚀 Started. I'll keep this message updated.",
        "prog": "🎬 <b>{title}</b>\n{bar} {pct}%\n\n{stage}\n⏱ {elapsed} in{eta}",
        "st_story": "Writing the story",
        "st_scenes": "Cutting the track into scenes",
        "st_frames": "Frames · scene {i} of {n}",
        "st_video": "Animating · scene {i} of {n}",
        "st_assemble": "Assembling the clip",
        "st_queued": "Queued",
        "eta_left": " · ~{v} left",
        "show_frame": "🖼 Show last frame", "stop": "⏹ Stop",
        "stopped": "⏹ Asked the pipeline to stop after the current scene.",
        "no_stop": ("I can't stop a running generation — that switch isn't wired up on "
                    "the server yet. It will finish on its own."),
        "done_title": "🎉 <b>{title}</b> is ready — {n} scenes, {dur}.",
        "done_sending": "🎬 Clip is assembled — sending it over…",
        "original": "⬇️ Original 1080×1920", "redo": "🔁 Redo a scene",
        "share": "📤 Share", "new_track": "🎵 New track", "watch": "▶️ Watch",
        "preview_note": ("Telegram caps videos at 50 MB, so this is a {w}px preview. "
                         "The original is one tap away."),
        "teaser_note": ("The full clip is {mb} MB — too big for Telegram. Here's the "
                        "first minute; the original is one tap away."),
        "preview_building": "⏳ Squeezing the clip under Telegram's 50 MB limit…",
        "preview_failed": ("Couldn't build a preview for the chat. The original is "
                           "fine — grab it here:"),
        "failed": ("⚠️ Stopped at <b>{stage}</b> — {why}\n\n"
                   "Everything already generated is <b>paid for</b> — restarting won't "
                   "charge you for it again."),
        "retry": "🔁 Continue", "support": "💬 Support",
        "no_points": ("Not enough points: this needs <b>{need}</b>, you have <b>{have}</b>."),
        "buy_points": "⭐ Buy {n}+ points", "upgrade": "💎 Upgrade plan",
        "clips_title": "🎬 <b>My clips</b> — {n} total",
        "clips_empty": "No clips yet. Send me a track and let's fix that.",
        "clip_row": "{i}. <b>{title}</b> · {dur} · {style}",
        "points_title": ("<b>{plan}</b>{until}\n<b>{points} points</b> ≈ <b>{movies}</b> "
                         "more clips\n\nScene price on your plan: {engines}"),
        "buy": "⭐ Buy points", "plans": "💎 Plans", "paysupport_btn": "🧾 Payment help",
        "packs_title": "<b>Point packs</b> — they never expire.",
        "plans_title": "<b>Plans</b> — renewed monthly by Telegram.",
        "pay_title": "{n} points", "pay_desc": "{n} generation points for {brand}. Never expire.",
        "plan_pay_title": "{plan} — 1 month",
        "plan_pay_desc": "{points} points every month, {engines}.",
        "plan_too_big": ("{plan} is above Telegram's 10 000 ⭐ subscription cap, "
                         "so it can't be sold here. A large point pack does the same job."),
        "paid_ok": "⭐ Payment received. {what}\nYou now have <b>{points} points</b>.",
        "paid_topup": "Points added.", "paid_plan": "Plan is now <b>{plan}</b>.",
        "sub_canceled": ("Auto-renewal is off. The plan keeps working until the end "
                         "of the period you already paid for — Telegram does not "
                         "refund the unused part."),
        "sub_failed": ("Telegram could not renew the plan — not enough Stars on your "
                       "balance. Top up and the renewal will go through on the next "
                       "attempt; the plan works until the paid period ends."),
        "paysupport": (
            "<b>Payments</b>\n\n"
            "Inside Telegram everything is paid with Telegram Stars — that's the only "
            "way Telegram allows for digital goods.\n\n"
            "• Points never expire and are spent per scene.\n"
            "• Plans renew every 30 days; cancel any time in Telegram → Settings → "
            "My Stars → Subscriptions.\n"
            "• Something went wrong with a payment? Write here and we'll refund it.\n"
            "{contact}"),
        "partners_off": ("<b>Referral programme</b>\n\nGive friends <b>{disc}%</b> off "
                         "their first payment and keep <b>{rew}%</b> of everything they "
                         "ever pay."),
        "partners_join": "🤝 Join",
        "partners_on": ("<b>Your code:</b> <code>{code}</code>\n"
                        "Friends get <b>{disc}%</b> off, you get <b>{rew}%</b> of every "
                        "payment they make.\n\n🔗 {web}\n💬 {tg}\n\n"
                        "Invited: <b>{invited}</b> · Buyers: <b>{buyers}</b>\n"
                        "Available: <b>{avail}</b>"),
        "partners_share": "📤 Share the link", "payout": "💸 Request payout",
        "payout_ask": ("Send the payout details (card, transfer, whatever works) — "
                       "the owner sends the money by hand and reads this."),
        "payout_min": "Minimum payout is {v}. You have {have}.",
        "payout_ok": "💸 Request sent. The owner pays these out by hand.",
        "settings_title": "<b>Settings</b>",
        "set_lang": "🌐 Language: {v}", "set_notify": "🔔 Updates: {v}",
        "notify_all": "every step", "notify_done": "only the result",
        "set_site": "🌐 Open the web studio", "set_unlink": "🔓 Unlink Telegram",
        "site_link": ("Here's a one-tap link into the web studio — same account, "
                      "same points:\n{url}"),
        "unlink_no": ("Unlinking has to happen on the site, in your account settings — "
                      "so a lost phone can't lock you out of your own clips."),
        "chars_title": "👤 <b>Cast</b> — they show up in every clip of this album.",
        "chars_empty": "No characters yet. Add one and the hero stops changing face.",
        "char_add": "➕ Add a character", "char_photos": "📸 Add photos",
        "char_del": "🗑 Remove", "char_attr": "🎩 Add a signature item",
        "attr_ask": ("Name the item and describe it in one line.\n"
                     "<i>Example: quad bike — battered yellow ATV with a cracked "
                     "headlight.</i>"),
        "manual_title": ("🎛 <b>Manual mode</b> — <b>{title}</b>\n"
                         "Scenes: <b>{scenes}</b> · frames ready: <b>{frames}</b> · "
                         "videos: <b>{videos}</b> · approved: <b>{appr}</b>"),
        "m_story": "🧠 Story", "m_scenes": "✂️ Cut into scenes",
        "m_board": "🖼 Storyboard sheet", "m_frames": "🎨 All frames",
        "m_videos": "🎥 All videos", "m_assemble": "🎬 Assemble",
        "m_list": "📋 Scenes", "m_settings": "✏️ Track settings",
        "m_queued": "Queued — I'll show progress on the pinned message.",
        "scene_card": ("<b>Scene {i}/{n}</b> · {shot} · {t}\n{note}\n\n"
                       "Frames: {frames} · Video: {video} · {appr}"),
        "sc_first": "🔁 First", "sc_last": "🔁 Last", "sc_both": "🎨 Both frames",
        "sc_video": "🎥 Animate", "sc_appr": "✅ Approve", "sc_unappr": "⬜ Un-approve",
        "sc_prompt": "✏️ Prompt", "sc_mid": "➕ In-between frames",
        "sc_ref": "📎 Reference",
        "sc_ref_ask": "Send a picture to use as the reference for this scene.",
        "sc_prompt_ask": "Send the new prompt for the first frame of this scene.",
        "sc_ref_ok": "📎 Reference attached to scene {i}.",
        "yes": "yes", "no": "no", "ready": "ready", "pending": "—",
        "back": "◀️ Back", "close": "✖️ Close",
        "prev": "◀️", "next": "▶️",
        "nothing_running": "Nothing is generating right now.",
        "running_now": "🎬 Running now:",
        "cancelled": "Cancelled.",
        "linked_ok": "🔗 Linked. Your web account and this chat are one account now.",
        "link_bad": "That link has expired — open the site and press the button again.",
        "link_taken": ("This Telegram is already tied to another {brand} account. "
                       "Sign in on the site with that account, or write to support."),
        "err_style": "Pick a look for the track first.",
        "err_hero": "Add at least one character — the clip needs a hero.",
        "err_audio": "This track has no audio file.",
        "err_running": "That one is already generating.",
        "err_engine": ("The video engine isn't available right now. This is on our side — "
                       "try again in a bit, or write to support."),
        "err_generic": "Something went wrong: {why}",
        "err_net": ("Couldn't reach the studio API. The generation itself isn't affected — "
                    "try again in a moment."),
    },
    "ru": {
        "hello": (
            "<b>{brand} — клипы, которые снимает ИИ.</b>\n\n"
            "Пришли mp3. Я напишу сюжет, нарежу его на сцены, нарисую каждый кадр, "
            "оживлю их и соберу клип с твоей дорожкой.\n\n"
            "У тебя <b>{points} токенов</b> — это один полный клип на три минуты, "
            "бесплатно.\n\n🎵 <i>Просто отправь аудиофайл — и поехали.</i>"),
        "hello_plain": (
            "<b>{brand} — клипы, которые снимает ИИ.</b>\n\n"
            "Пришли mp3. Я напишу сюжет, нарежу его на сцены, нарисую каждый кадр, "
            "оживлю их и соберу клип с твоей дорожкой.\n\n"
            "У тебя <b>{points} токенов</b>.\n\n"
            "🎵 <i>Просто отправь аудиофайл — и поехали.</i>"),
        "hello_back": ("С возвращением, {name}.\n<b>{points} токенов</b> · {plan}\n\n"
                       "Пришли трек — или продолжи начатое."),
        "menu_clips": "🎬 Мои клипы", "menu_chars": "👥 Персонажи",
        "menu_points": "💎 Токены", "menu_settings": "⚙️ Настройки",
        "menu_trends": "🔥 Тренды", "menu_mockup": "📦 Мокап",
        "menu_model3d": "🧊 3D-моделька",
        "trends_title": "🔥 <b>Тренды</b> — выбери, пришли своё фото, получи ролик.",
        "trends_empty": "Трендов сейчас нет — загляни позже.",
        "trend_pick": "🔥 <b>{title}</b>\nПришли фото — вставлю тебя в этот тренд.",
        "gen_confirm": "Сгенерить за ⚡{n}?",
        "yes_go": "✅ Да", "no_cancel": "✖️ Отмена",
        "gen_started": ("🚀 Принял. Пришлю результат сюда, как будет готов — "
                        "чат можно закрыть."),
        "trend_ready": "🔥 <b>{title}</b> готов!",
        "gen_failed": ("⚠️ <b>{title}</b> не получился: {why}\n"
                       "Токены вернулись на баланс."),
        "evt_clip_done": "🎉 <b>{title}</b> готов — отправляю.",
        "evt_clip_fail": ("⚠️ <b>{title}</b> остановился: {why}\n"
                          "Всё уже сгенерированное оплачено — повторный запуск "
                          "второй раз за это не спишет."),
        "mockup_title": ("📦 <b>Мокапы</b> — продуктовый кадр из одного фото.\n"
                         "Цена кадра: <b>⚡{n}</b>."),
        "mockup_pick": "📦 <b>{title}</b>\nПришли фото товара.",
        "mockup_ready": "📦 <b>{title}</b> — кадр готов.",
        "model3d_ask": ("🧊 Пришли фото героя — нарисую модельку: одно и то же "
                        "лицо со всех ракурсов."),
        "char_name_ask": "Как зовут героя? Одно-два слова.",
        "open_app": "📱 Открыть приложение",
        "no_photo": "Фото потерялось — начни заново, пожалуйста.",
        "how_it_works": "▶️ Как это работает", "lang_btn": "🌐 EN / RU",
        "help": (
            "<b>Как это работает</b>\n\n"
            "1. Присылаешь <b>mp3</b> (до 20 МБ — это лимит Telegram для ботов).\n"
            "2. Выбираешь <b>стиль</b> — до трёх пресетов смешиваются в один, первый "
            "главный.\n"
            "3. Говоришь, <b>кто в кадре</b> — герой не меняет лицо от сцены к сцене.\n"
            "4. Жмёшь <b>Сделать клип</b> и закрываешь чат. Я напишу, когда будет готово.\n\n"
            "Внутри: сюжет → раскадровка → первый и последний кадр каждой сцены → "
            "оживление → сборка с твоей дорожкой.\n\n"
            "<b>Команды</b>\n"
            "/new — новый клип\n/clips — мои клипы\n/points — токены и тариф\n"
            "/partners — партнёрская программа\n/status — что сейчас считается\n"
            "/settings — язык и уведомления\n/site — открыть студию в браузере\n"
            "/paysupport — вопросы по оплате"),
        "send_audio": "🎵 Пришли аудиофайл — с него начинается клип.",
        "audio_too_big": (
            "Файл весит <b>{mb} МБ</b> — Telegram даёт боту скачать только 20 МБ.\n\n"
            "Сохрани mp3 в 320 kbps (три минуты — это примерно 7 МБ) или залей "
            "оригинал в студии: аккаунт и токены те же:\n{url}"),
        "audio_too_long": "Трек идёт {mm}:{ss}. Я беру дорожки до {max} минут.",
        "audio_too_short": "Тут всего {sec} секунд. Нужно хотя бы {min}.",
        "audio_got": ("🎧 <b>{title}</b> · {dur}\n"
                      "Примерно <b>{scenes} сцен</b> — по одной на ~6 секунд.\n"
                      "Движок: <b>{engine}</b> · цена <b>{cost} токенов</b> · "
                      "у тебя <b>{points}</b>.\n\nТеперь выбери стиль."),
        "pick_style": ("<b>Выбери стиль</b> — до трёх, первый становится основой.\n\n{picked}"),
        "picked_none": "<i>Пока ничего не выбрано.</i>",
        "style_done": "✅ Готово", "style_rand": "🎲 Удиви меня",
        "style_own": "✍️ Опишу свой",
        "style_own_ask": "Опиши стиль своими словами — одна-две строки.",
        "style_need": "Сначала выбери хотя бы один стиль.",
        "who": ("<b>Кто в кадре?</b>\n"
                "Герой остаётся одним и тем же во всех сценах — именно это делает "
                "клип фильмом, а не набором из тридцати случайных картинок."),
        "who_text": "✍️ Опишу словами", "who_photo": "📸 Пришлю фото",
        "who_lib": "📚 Из моих персонажей",
        "who_ask": ("Опиши героя одной строкой — имя, внешность, повадка.\n"
                    "<i>Например: Марк, 25, бритый, чёрный бомбер, золотая цепь, "
                    "всегда с каменным лицом.</i>"),
        "who_photo_ask": ("Пришли 1–3 фото героя (именно фото, не файлом). "
                          "Как закончишь — жми «Готово»."),
        "who_photo_ok": "📸 Фото {n} сохранено.",
        "who_ready": "✅ Готово",
        "char_added": "👤 <b>{name}</b> в составе.",
        "model_btn": "🎭 Сделать модельку",
        "model_need_desc": ("Сначала опиши героя одной строкой — моделька рисуется по "
                            "описанию, фото держат только лицо."),
        "model_wait": "🎭 Рисую модельку — героя в четырёх ракурсах…",
        "model_done": "🎭 Моделька готова — лицо будет держаться во всех кадрах.",
        "confirm": ("🎬 <b>{title}</b>\n"
                    "Стиль: <b>{style}</b>\n"
                    "Герой: <b>{hero}</b>\n"
                    "~<b>{scenes} сцен</b> · движок <b>{engine}</b>\n\n"
                    "<b>{cost} токенов</b> — останется <b>{left}</b>.\n"
                    "Займёт примерно <b>{eta}</b>. Напишу, когда будет готово — "
                    "чат можно закрыть."),
        "go": "🚀 Сделать клип", "manual": "🎛 Ручной режим",
        "change_style": "✏️ Сменить стиль", "extras": "⚙️ Тонкости",
        "rename": "✏️ Переименовать", "rename_ask": "Пришли новое название трека.",
        "extras_title": ("<b>Тонкости</b> — всё это необязательно.\n\n"
                         "Текст песни заставляет раскадровку идти за словами. "
                         "Заметка режиссёра — про что вообще клип."),
        "ex_lyrics": "📝 Текст песни", "ex_comment": "🎙 Заметка режиссёра",
        "ex_grain": "🎞 Плёночное зерно: {v}", "ex_nostory": "🎲 Без сюжета: {v}",
        "ex_lyrics_ask": "Пришли текст песни одним сообщением.",
        "ex_comment_ask": "Про что этот клип? Хватит одной-двух строк.",
        "on": "вкл", "off": "выкл",
        "saved": "Сохранил.",
        "started": "🚀 Запустил. Буду обновлять это сообщение.",
        "prog": "🎬 <b>{title}</b>\n{bar} {pct}%\n\n{stage}\n⏱ прошло {elapsed}{eta}",
        "st_story": "Пишу сюжет",
        "st_scenes": "Режу трек на сцены",
        "st_frames": "Кадры · сцена {i} из {n}",
        "st_video": "Оживляю · сцена {i} из {n}",
        "st_assemble": "Собираю клип",
        "st_queued": "В очереди",
        "eta_left": " · осталось ~{v}",
        "show_frame": "🖼 Показать последний кадр", "stop": "⏹ Стоп",
        "stopped": "⏹ Попросил конвейер остановиться после текущей сцены.",
        "no_stop": ("Остановить идущую генерацию я не могу — этот выключатель на "
                    "сервере пока не подключён. Она доработает сама."),
        "done_title": "🎉 <b>{title}</b> готов — {n} сцен, {dur}.",
        "done_sending": "🎬 Клип собран — отправляю…",
        "original": "⬇️ Оригинал 1080×1920", "redo": "🔁 Перегенерировать сцену",
        "share": "📤 Поделиться", "new_track": "🎵 Новый трек", "watch": "▶️ Смотреть",
        "preview_note": ("Telegram не пропускает видео больше 50 МБ, поэтому это "
                         "превью {w}px. Оригинал — в одно нажатие."),
        "teaser_note": ("Целиком клип весит {mb} МБ — для Telegram это слишком. Вот "
                        "первая минута; оригинал в одно нажатие."),
        "preview_building": "⏳ Ужимаю клип под лимит Telegram в 50 МБ…",
        "preview_failed": ("Превью для чата собрать не вышло. С оригиналом всё в "
                           "порядке — забирай:"),
        "failed": ("⚠️ Остановился на этапе «<b>{stage}</b>» — {why}\n\n"
                   "Всё, что уже сгенерировано, <b>оплачено</b> — повторный запуск "
                   "второй раз за это не спишет."),
        "retry": "🔁 Продолжить", "support": "💬 Поддержка",
        "no_points": "Не хватает токенов: нужно <b>{need}</b>, есть <b>{have}</b>.",
        "buy_points": "⭐ Купить {n}+ токенов", "upgrade": "💎 Сменить тариф",
        "clips_title": "🎬 <b>Мои клипы</b> — всего {n}",
        "clips_empty": "Клипов пока нет. Пришли трек — исправим.",
        "clip_row": "{i}. <b>{title}</b> · {dur} · {style}",
        "points_title": ("<b>{plan}</b>{until}\n<b>{points} токенов</b> ≈ ещё "
                         "<b>{movies}</b> клипов\n\nЦена сцены на твоём тарифе: {engines}"),
        "buy": "⭐ Купить токены", "plans": "💎 Тарифы", "paysupport_btn": "🧾 Вопрос по оплате",
        "packs_title": "<b>Пакеты токенов</b> — они не сгорают.",
        "plans_title": "<b>Тарифы</b> — Telegram продлевает их сам, раз в 30 дней.",
        "pay_title": "{n} токенов", "pay_desc": "{n} токенов генерации в {brand}. Не сгорают.",
        "plan_pay_title": "{plan} — 1 месяц",
        "plan_pay_desc": "{points} токенов каждый месяц, {engines}.",
        "plan_too_big": ("{plan} не помещается в лимит подписки Telegram — 10 000 ⭐, "
                         "поэтому здесь он не продаётся. Ту же задачу решает большой "
                         "пакет токенов."),
        "paid_ok": "⭐ Оплата прошла. {what}\nТеперь у тебя <b>{points} токенов</b>.",
        "paid_topup": "Токены начислены.", "paid_plan": "Тариф теперь <b>{plan}</b>.",
        "sub_canceled": ("Автопродление отключено. Тариф доработает до конца уже "
                         "оплаченного периода — неиспользованные дни Telegram "
                         "не возвращает."),
        "sub_failed": ("Telegram не смог продлить тариф: на балансе не хватило "
                       "звёзд. Пополни — следующая попытка пройдёт; до конца "
                       "оплаченного периода тариф работает."),
        "paysupport": (
            "<b>Оплата</b>\n\n"
            "Внутри Telegram всё оплачивается звёздами — для цифровых товаров "
            "Telegram другого способа не разрешает.\n\n"
            "• Токены не сгорают и тратятся посценово.\n"
            "• Тариф продлевается каждые 30 дней; отключить — в Telegram → Настройки → "
            "Мои звёзды → Подписки.\n"
            "• Если с платежом что-то не так — напиши сюда, вернём.\n{contact}"),
        "partners_off": ("<b>Партнёрская программа</b>\n\nДруг получает <b>{disc}%</b> "
                         "скидки на первую оплату, ты — <b>{rew}%</b> со всех его "
                         "платежей, всегда."),
        "partners_join": "🤝 Подключиться",
        "partners_on": ("<b>Твой код:</b> <code>{code}</code>\n"
                        "Другу <b>{disc}%</b> скидки, тебе <b>{rew}%</b> с каждого его "
                        "платежа.\n\n🔗 {web}\n💬 {tg}\n\n"
                        "Приглашено: <b>{invited}</b> · Платили: <b>{buyers}</b>\n"
                        "Доступно: <b>{avail}</b>"),
        "partners_share": "📤 Поделиться ссылкой", "payout": "💸 Заказать выплату",
        "payout_ask": ("Пришли реквизиты (карта, перевод — что удобно). Деньги "
                       "владелец отправляет руками и читает это поле."),
        "payout_min": "Минимальная выплата — {v}. У тебя {have}.",
        "payout_ok": "💸 Заявка отправлена. Выплаты владелец делает руками.",
        "settings_title": "<b>Настройки</b>",
        "set_lang": "🌐 Язык: {v}", "set_notify": "🔔 Уведомления: {v}",
        "notify_all": "каждый шаг", "notify_done": "только результат",
        "set_site": "🌐 Открыть студию в браузере", "set_unlink": "🔓 Отвязать Telegram",
        "site_link": ("Ссылка в студию — аккаунт и токены те же:\n{url}"),
        "unlink_no": ("Отвязка делается на сайте, в настройках аккаунта — чтобы "
                      "потерянный телефон не отрезал тебя от собственных клипов."),
        "chars_title": "👤 <b>Состав</b> — они появляются во всех клипах этого альбома.",
        "chars_empty": "Персонажей пока нет. Заведи героя — и лицо перестанет плыть.",
        "char_add": "➕ Добавить персонажа", "char_photos": "📸 Добавить фото",
        "char_del": "🗑 Убрать", "char_attr": "🎩 Добавить фирменную вещь",
        "attr_ask": ("Назови вещь и опиши одной строкой.\n"
                     "<i>Например: квадрик — убитый жёлтый ATV с треснувшей фарой.</i>"),
        "manual_title": ("🎛 <b>Ручной режим</b> — <b>{title}</b>\n"
                         "Сцен: <b>{scenes}</b> · кадры: <b>{frames}</b> · "
                         "видео: <b>{videos}</b> · утверждено: <b>{appr}</b>"),
        "m_story": "🧠 Сюжет", "m_scenes": "✂️ Нарезать сцены",
        "m_board": "🖼 Лист раскадровки", "m_frames": "🎨 Все кадры",
        "m_videos": "🎥 Все видео", "m_assemble": "🎬 Собрать клип",
        "m_list": "📋 Сцены", "m_settings": "✏️ Настройки трека",
        "m_queued": "Поставил в очередь — прогресс будет в закреплённом сообщении.",
        "scene_card": ("<b>Сцена {i}/{n}</b> · {shot} · {t}\n{note}\n\n"
                       "Кадры: {frames} · Видео: {video} · {appr}"),
        "sc_first": "🔁 Первый", "sc_last": "🔁 Последний", "sc_both": "🎨 Оба кадра",
        "sc_video": "🎥 Оживить", "sc_appr": "✅ Утвердить", "sc_unappr": "⬜ Снять",
        "sc_prompt": "✏️ Промпт", "sc_mid": "➕ Промежуточные кадры",
        "sc_ref": "📎 Референс",
        "sc_ref_ask": "Пришли картинку-референс для этой сцены.",
        "sc_prompt_ask": "Пришли новый промпт первого кадра этой сцены.",
        "sc_ref_ok": "📎 Референс прикреплён к сцене {i}.",
        "yes": "да", "no": "нет", "ready": "готовы", "pending": "—",
        "back": "◀️ Назад", "close": "✖️ Закрыть",
        "prev": "◀️", "next": "▶️",
        "nothing_running": "Сейчас ничего не считается.",
        "running_now": "🎬 Сейчас считается:",
        "cancelled": "Отменил.",
        "linked_ok": "🔗 Готово. Аккаунт на сайте и этот чат — теперь одно и то же.",
        "link_bad": "Ссылка просрочена — открой сайт и нажми кнопку ещё раз.",
        "link_taken": ("Этот телеграм уже привязан к другому аккаунту {brand}. Войди "
                       "на сайте под ним или напиши в поддержку."),
        "err_style": "Сначала выбери стиль трека.",
        "err_hero": "Добавь хотя бы одного персонажа — клипу нужен герой.",
        "err_audio": "У этого трека нет аудио.",
        "err_running": "По нему генерация уже идёт.",
        "err_engine": ("Видеодвижок сейчас недоступен. Это на нашей стороне — попробуй "
                       "через некоторое время или напиши в поддержку."),
        "err_generic": "Что-то пошло не так: {why}",
        "err_net": ("Не достучался до API студии. На саму генерацию это не влияет — "
                    "попробуй ещё раз через минуту."),
    },
}


def t(lang: str, key: str, **kw) -> str:
    val = STR.get(lang, STR["en"]).get(key) or STR["en"].get(key) or key
    if kw:
        try:
            return val.format(**kw)
        except (KeyError, IndexError):
            return val
    return val


# ═════════════════════════════ мелкие помощники ═════════════════════════════

def esc(s) -> str:
    return html.escape(str(s or ""), quote=False)


def mmss(sec) -> str:
    sec = int(sec or 0)
    return f"{sec // 60}:{sec % 60:02d}"


def human_time(sec: float, lang: str) -> str:
    """«38 min» / «1 ч 20 мин» — без секунд: точность тут никого не спасает."""
    sec = max(0, int(sec))
    if sec < 90:
        return f"{sec} sec" if lang == "en" else f"{sec} сек"
    minutes = round(sec / 60)
    if minutes < 60:
        return f"{minutes} min" if lang == "en" else f"{minutes} мин"
    h, m = divmod(minutes, 60)
    if lang == "en":
        return f"{h} h {m} min" if m else f"{h} h"
    return f"{h} ч {m} мин" if m else f"{h} ч"


def bar(pct: int, width: int = 20) -> str:
    full = max(0, min(width, round(width * pct / 100)))
    return "▓" * full + "░" * (width - full)


def money(kopeks: int) -> str:
    return f"{kopeks / 100:,.2f} ₽".replace(",", " ")


def clip_text(s: str, limit: int) -> str:
    """Обрезка с оглядкой на HTML: разрез посреди `&amp;` или `<b>` превращает
    сообщение в невалидную разметку, и Telegram отвечает 400 вместо текста."""
    s = s or ""
    if len(s) <= limit:
        return s
    cut = s[: limit - 1]
    for ch in ("&", "<"):
        pos = cut.rfind(ch)
        if pos >= 0 and (";" if ch == "&" else ">") not in cut[pos:]:
            cut = cut[:pos]
    return cut.rstrip() + "…"


def kb(rows: list[list[dict]]) -> dict:
    return {"inline_keyboard": [r for r in rows if r]}


def btn(text: str, data: str) -> dict:
    """callback_data ограничен 64 БАЙТАМИ — поэтому схема короткая («sc:317:v»),
    а не JSON. Обрезаем на всякий случай: молча потерянная кнопка хуже."""
    return {"text": text, "callback_data": data[:64]}


def url_btn(text: str, url: str) -> dict:
    return {"text": text, "url": url}


# ═══════════════════════════════ сам бот ═══════════════════════════════

class Bot:
    def __init__(self) -> None:
        self.store = Store(BOT_DB_PATH)
        self.tg = Telegram(BOT_TOKEN)
        self.api = Qlol(self.store)
        self.me: dict = {}
        self.styles: list[dict] = []
        self._watcher: asyncio.Task | None = None
        self._last_edit: dict[int, float] = {}
        self._billing_cache: tuple[float, dict] = (0.0, {})
        # Апдейты обрабатываются параллельно: отдача клипа ждёт транскод минутами,
        # и последовательный цикл на это время замораживал бы бота для ВСЕХ.
        # Порядок сообщений внутри чата всё равно держит Pacer (лок на чат).
        self._tasks: set[asyncio.Task] = set()
        self._gate = asyncio.Semaphore(int(os.environ.get("BOT_CONCURRENCY", "32")))

    def spawn(self, coro, what: str = "") -> None:
        """Фоновая задача со ссылкой на неё: без ссылки сборщик мусора может
        убить задачу на середине, и апдейт молча пропадёт."""
        async def guarded():
            async with self._gate:
                try:
                    await coro
                except Exception as e:  # noqa: BLE001
                    log.exception("фоновая задача %s упала: %s", what, e)

        task = asyncio.create_task(guarded())
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)

    # ── короткие обёртки ──

    def lang_of(self, tg_id: str, fallback: str = "en") -> str:
        row = self.store.user(tg_id)
        return (row["lang"] if row and row["lang"] else fallback) or "en"

    async def send(self, chat_id: int, text: str, lang: str = "en", **kw):
        # lang здесь не используется — он в сигнатуре, чтобы все вызовы читались
        # одинаково («кому, что, на каком языке») и язык не терялся при правках.
        return await self.tg.call("sendMessage", chat_id=chat_id,
                                  text=clip_text(text, TEXT_LIMIT),
                                  parse_mode="HTML",
                                  disable_web_page_preview=True, **kw)

    async def edit(self, chat_id: int, msg_id: int, text: str, **kw):
        try:
            return await self.tg.call("editMessageText", chat_id=chat_id,
                                      message_id=msg_id,
                                      text=clip_text(text, TEXT_LIMIT),
                                      parse_mode="HTML",
                                      disable_web_page_preview=True, **kw)
        except TgError as e:
            # «message is not modified» — не ошибка, а наша же защита сработала.
            if "not modified" in e.description:
                return None
            raise

    async def answer_cb(self, cb_id: str, text: str = "", alert: bool = False) -> None:
        with contextlib.suppress(TgError):
            await self.tg.call("answerCallbackQuery", callback_query_id=cb_id,
                               text=clip_text(text, 190) or None, show_alert=alert)

    def main_kb(self, lang: str) -> dict:
        return {
            "keyboard": [
                [{"text": t(lang, "menu_clips")}, {"text": t(lang, "menu_chars")},
                 {"text": t(lang, "menu_trends")}],
                [{"text": t(lang, "menu_mockup")}, {"text": t(lang, "menu_model3d")},
                 {"text": t(lang, "menu_points")}, {"text": t(lang, "menu_settings")}],
            ],
            "resize_keyboard": True,
        }

    # ── ошибки API → человеческий текст ──

    def api_error_text(self, e: ApiError, lang: str) -> tuple[str, list[list[dict]]]:
        """Честный разбор отказа. Никаких «упс, попробуйте позже» там, где
        известна настоящая причина: кончились токены — показываем сколько нужно
        и кнопку докупки; нет стиля/героя — говорим, чего именно не хватает."""
        if e.code == "not_enough_points" or e.status == 402:
            need = int(e.data.get("need") or 0)
            have = int(e.data.get("have") or 0)
            short = int(e.data.get("short") or max(0, need - have))
            return (t(lang, "no_points", need=need, have=have),
                    [[btn(t(lang, "buy_points", n=short), "pay:packs")],
                     [btn(t(lang, "upgrade"), "pay:plans")]])
        low = (e.detail or "").lower()
        # Предусловия супергенерации брошены обычным HTTPException с русским
        # текстом (коды у них появятся вместе с необязательной частью патча) —
        # опознаём по подстроке, а показываем на языке человека.
        if "стил" in low or "style" in low:
            return t(lang, "err_style"), []
        if "персонаж" in low or "character" in low:
            return t(lang, "err_hero"), []
        if "аудио" in low or "дорожк" in low:
            return t(lang, "err_audio"), []
        if "уже идёт" in low or "already" in low:
            return t(lang, "err_running"), []
        if "провайдер" in low or "provider" in low or "движ" in low:
            return t(lang, "err_engine"), []
        # Остальное отдаём КАК ЕСТЬ. Часть сообщений бэкенда по-русски, и
        # англоязычный человек увидит их русскими — но точная сумма из «доступно
        # только 500 ₽» полезнее, чем аккуратный пустой шаблон.
        return t(lang, "err_generic", why=esc(clip_text(e.detail or e.code, 300))), []

    async def report_api_error(self, chat_id: int, lang: str, e: Exception) -> None:
        if isinstance(e, ApiError):
            text, rows = self.api_error_text(e, lang)
            await self.send(chat_id, text, lang, reply_markup=kb(rows) if rows else None)
        elif isinstance(e, (httpx.TransportError, httpx.TimeoutException)):
            await self.send(chat_id, t(lang, "err_net"), lang)
        else:
            log.exception("необработанная ошибка")
            await self.send(chat_id, t(lang, "err_generic", why=esc(str(e)[:200])), lang)

    # ── справочники ──

    async def load_styles(self) -> list[dict]:
        """Каталог стилей из витрины /api/styles?lang=…: key/label/desc на
        двух языках, свёрнутые в [{key, en:{label,desc}, ru:{…}}]. Текстов
        промптов бот не получает и не собирает — стиль ставится ключами через
        POST /api/tracks/{id}/style, текст склеивает сервер."""
        if not self.styles:
            merged: dict[str, dict] = {}
            for lg in ("en", "ru"):
                try:
                    data = await self.api.http.get("/api/styles", params={"lang": lg})
                    rows = (data.json() or {}).get("styles") or []
                except Exception as e:  # noqa: BLE001
                    log.warning("не забрал стили (%s) из API: %s", lg, e)
                    rows = []
                for r in rows:
                    key = str(r.get("key") or "").strip()
                    if not key:
                        continue
                    item = merged.setdefault(key, {"key": key})
                    item[lg] = {"label": str(r.get("label") or key),
                                "desc": str(r.get("desc") or "")}
            for item in merged.values():
                item.setdefault("en", item.get("ru") or {"label": item["key"], "desc": ""})
                item.setdefault("ru", item["en"])
            self.styles = list(merged.values())
        return self.styles

    def style_label(self, key: str, lang: str) -> str:
        for s in self.styles:
            if s["key"] == key:
                return (s.get(lang) or s.get("en") or {}).get("label") or key
        return key

    async def billing(self) -> dict:
        """Витрина тарифов и пакетов — из ПУБЛИЧНОГО /api/billing/plans.
        Свою копию прайса бот не держит принципиально: цена живёт в одном месте."""
        ts, data = self._billing_cache
        if data and time.time() - ts < 300:
            return data
        try:
            r = await self.api.http.get("/api/billing/plans")
            data = r.json()
            self._billing_cache = (time.time(), data)
        except Exception as e:  # noqa: BLE001
            log.warning("не забрал витрину тарифов: %s", e)
            data = data or {}
        return data

    async def engine_for(self, user: dict) -> tuple[str, int]:
        """Каким движком пойдёт супергенерация и сколько стоит сцена.

        Повторяет выбор сервера (`_allowed_provider` + `_scene_cost`), чтобы
        цифра на экране подтверждения совпала с реальным списанием."""
        try:
            prov = await self.api.req(user, "GET", "/api/providers")
        except Exception:  # noqa: BLE001
            prov = {"video": ["grok"], "plan": "free"}
        bill = await self.billing()
        plan_id = prov.get("plan") or bill.get("current") or "free"
        card = next((p for p in bill.get("plans", []) if p["id"] == plan_id), None)
        engines = (card or {}).get("engines") or {"grok": 4}
        available = prov.get("video") or ["grok"]
        # Приоритет тот же, что у сервера: seedance → kling → grok.
        for want in ("seedance", "kling", "grok"):
            if want not in available:
                continue
            for key, cost in engines.items():
                if key.startswith(want):
                    return key, int(cost)
        return "grok", int(engines.get("grok", 4))

    # ═══════════════════════ вход в диалог ═══════════════════════

    async def cmd_start(self, msg: dict, payload: str = "") -> None:
        tg_user = msg["from"]
        tg_id = str(tg_user["id"])
        chat_id = msg["chat"]["id"]

        # Язык при первом заходе — по языку клиента, дальше только вручную.
        row = self.store.user(tg_id)
        lang = (row["lang"] if row and row["lang"] else None) or \
               ("ru" if str(tg_user.get("language_code") or "").startswith("ru") else "en")

        ref = ""
        if payload.startswith("ref_"):
            ref = payload[4:]
        self.store.upsert_user(tg_id, lang=lang, chat_id=chat_id,
                               name=tg_user.get("first_name") or "")

        # Реферальный код должен закрепиться ПРИ СОЗДАНИИ аккаунта: _attach_ref
        # ставит амбассадора один раз и навсегда, поэтому передаём его в самую
        # первую выписку сессии, а не после.
        try:
            await self.api.session(tg_user, ref=ref)
        except Exception as e:  # noqa: BLE001
            log.warning("не выписал сессию для %s: %s", tg_id, e)
            await self.send(chat_id, t(lang, "err_net"), lang)
            return

        if payload.startswith("link_"):
            await self.do_link(tg_user, chat_id, lang, payload[5:])
            return

        try:
            acc = await self.api.req(tg_user, "GET", "/api/account")
        except Exception as e:  # noqa: BLE001
            await self.report_api_error(chat_id, lang, e)
            return

        first_time = not row or not row["uid"]
        if first_time:
            # «Хватит на один клип» обещаем, только если это правда: сервер сам
            # считает movies_left по текущей цене сцены на текущем тарифе.
            key = "hello" if int(acc.get("movies_left") or 0) >= 1 else "hello_plain"
            text = t(lang, key, brand=BRAND, points=acc.get("points", 0))
            rows = [[btn(t(lang, "how_it_works"), "help"), btn(t(lang, "lang_btn"), "set:lang:toggle")]]
        else:
            text = t(lang, "hello_back", name=esc(acc.get("name") or ""),
                     points=acc.get("points", 0), plan=acc.get("plan_title", ""))
            rows = []
        await self.send(chat_id, text, lang, reply_markup=self.main_kb(lang))
        if rows:
            await self.send(chat_id, t(lang, "send_audio"), lang, reply_markup=kb(rows))

        if payload.startswith("track_"):
            with contextlib.suppress(Exception):
                await self.show_manual(tg_user, chat_id, lang, int(payload[6:]))
        elif payload.startswith("clip_"):
            with contextlib.suppress(Exception):
                await self.send_clip(tg_user, chat_id, lang, int(payload[5:]))

    async def do_link(self, tg_user: dict, chat_id: int, lang: str, code: str) -> None:
        """Привязка аккаунта, заведённого на сайте паролем/Google/Яндексом."""
        try:
            await self.api._internal("/internal/tg-adopt", {
                "code": code.strip().upper(), "tg_id": str(tg_user["id"]),
                "username": tg_user.get("username") or "",
            })
        except ApiError as e:
            if e.code in ("tg_taken", "already_linked"):
                await self.send(chat_id, t(lang, "link_taken", brand=BRAND), lang)
            else:
                await self.send(chat_id, t(lang, "link_bad"), lang)
            return
        # Аккаунт сменился — старая кука указывает на прежнего гостя.
        await self.api.session(tg_user, force=True)
        await self.send(chat_id, t(lang, "linked_ok"), lang, reply_markup=self.main_kb(lang))

    # ═══════════════════════ приём аудио ═══════════════════════

    async def handle_audio(self, msg: dict, tg_user: dict, lang: str) -> None:
        chat_id = msg["chat"]["id"]
        src = msg.get("audio") or msg.get("voice") or msg.get("document") or {}
        file_id = src.get("file_id")
        size = int(src.get("file_size") or 0)
        duration = int(src.get("duration") or 0)

        # Размер проверяем ДО скачивания: getFile всё равно откажет на 20 МБ,
        # и лучше сразу дать работающий выход, чем упереться в стену.
        if size > TG_DOWNLOAD_LIMIT:
            await self.send(chat_id, t(lang, "audio_too_big",
                                       mb=round(size / 1048576, 1),
                                       url=f"{PUBLIC_BASE_URL}/"), lang)
            return
        if duration and duration > MAX_AUDIO_SEC:
            await self.send(chat_id, t(lang, "audio_too_long", mm=duration // 60,
                                       ss=f"{duration % 60:02d}",
                                       max=MAX_AUDIO_SEC // 60), lang)
            return
        if duration and duration < MIN_AUDIO_SEC:
            await self.send(chat_id, t(lang, "audio_too_short", sec=duration,
                                       min=MIN_AUDIO_SEC), lang)
            return

        title = (src.get("title") or "").strip()
        fname = (src.get("file_name") or "").strip()
        if not title and fname:
            title = os.path.splitext(fname)[0]
        if not title:
            title = "Untitled" if lang == "en" else "Без названия"
        performer = (src.get("performer") or "").strip()
        if performer and performer.lower() not in title.lower():
            title = f"{performer} — {title}"

        await self.tg.call("sendChatAction", chat_id=chat_id, action="upload_document")
        local = os.path.join(TMP_DIR, f"in_{tg_user['id']}_{int(time.time())}.mp3")
        try:
            await self.tg.download(file_id, local)
        except TgError as e:
            log.warning("не скачал аудио: %s", e)
            await self.send(chat_id, t(lang, "audio_too_big",
                                       mb=round(size / 1048576, 1) or "?",
                                       url=f"{PUBLIC_BASE_URL}/"), lang)
            return

        try:
            with open(local, "rb") as fh:
                track = await self.api.req(
                    tg_user, "POST", "/api/tracks",
                    data={"title": title[:120], "lyrics": "", "comment": "", "style": ""},
                    files={"audio": (fname or "track.mp3", fh.read(), "audio/mpeg")},
                )
        except Exception as e:  # noqa: BLE001
            await self.report_api_error(chat_id, lang, e)
            return
        finally:
            with contextlib.suppress(OSError):
                os.remove(local)

        dur = int(track.get("audio_duration_sec") or duration or 0)
        engine, cost_per_scene = await self.engine_for(tg_user)
        scenes = max(1, round(dur / 6)) if dur else 30
        acc = await self.api.req(tg_user, "GET", "/api/account")

        await self.send(chat_id, t(lang, "audio_got", title=esc(track.get("title")),
                                   dur=mmss(dur), scenes=scenes,
                                   engine=esc(engine),
                                   cost=scenes * cost_per_scene,
                                   points=acc.get("points", 0)), lang)
        self.store.set_state(str(tg_user["id"]), {"track": track["id"], "styles": []})
        await self.ask_style(tg_user, chat_id, lang)

    # ═══════════════════════ выбор стиля ═══════════════════════

    async def ask_style(self, tg_user: dict, chat_id: int, lang: str,
                        edit_msg: int | None = None) -> None:
        await self.load_styles()
        st = self.store.state(str(tg_user["id"]))
        picked: list[str] = st.get("styles") or []

        if not self.styles:
            # Фронт не разобрался — не выдумываем свой список, а идём путём,
            # который в API и так есть: стиль обычным текстом.
            self.store.set_state(str(tg_user["id"]), {**st, "await": "style_text"})
            await self.send(chat_id, t(lang, "style_own_ask"), lang,
                            reply_markup={"force_reply": True})
            return

        if picked:
            head = " · ".join(
                ("★ " if i == 0 else "✅ ") + esc(self.style_label(k, lang))
                for i, k in enumerate(picked))
            desc = next((s for s in self.styles if s["key"] == picked[0]), None)
            if desc:
                head += "\n<i>" + esc((desc.get(lang) or desc["en"])["desc"]) + "</i>"
        else:
            head = t(lang, "picked_none")

        rows, row = [], []
        for s in self.styles:
            mark = ""
            if s["key"] in picked:
                mark = "★ " if picked[0] == s["key"] else "✅ "
            row.append(btn(mark + self.style_label(s["key"], lang)[:28], f"st:{s['key']}"))
            if len(row) == 2:
                rows.append(row)
                row = []
        if row:
            rows.append(row)
        rows.append([btn(t(lang, "style_rand"), "st:!rand"),
                     btn(t(lang, "style_own"), "st:!own")])
        rows.append([btn(t(lang, "style_done"), "st:!done")])

        text = t(lang, "pick_style", picked=head)
        if edit_msg:
            await self.edit(chat_id, edit_msg, text, reply_markup=kb(rows))
        else:
            await self.send(chat_id, text, lang, reply_markup=kb(rows))

    async def on_style_cb(self, cb: dict, tg_user: dict, lang: str, key: str) -> None:
        chat_id = cb["message"]["chat"]["id"]
        msg_id = cb["message"]["message_id"]
        tg_id = str(tg_user["id"])
        st = self.store.state(tg_id)
        picked: list[str] = st.get("styles") or []

        if key == "!rand":
            await self.load_styles()
            picked = [random.choice(self.styles)["key"]] if self.styles else []
        elif key == "!own":
            self.store.set_state(tg_id, {**st, "await": "style_text"})
            await self.answer_cb(cb["id"])
            await self.send(chat_id, t(lang, "style_own_ask"), lang,
                            reply_markup={"force_reply": True})
            return
        elif key == "!done":
            if not picked:
                await self.answer_cb(cb["id"], t(lang, "style_need"), alert=True)
                return
            await self.answer_cb(cb["id"])
            await self.apply_style(tg_user, chat_id, lang, st.get("track"), picked, "",
                                   " + ".join(self.style_label(k, lang) for k in picked))
            return
        else:
            if key in picked:
                picked.remove(key)
            elif len(picked) < 3:
                picked.append(key)
            else:
                await self.answer_cb(cb["id"], "3 max")
                return

        self.store.set_state(tg_id, {**st, "styles": picked})
        await self.answer_cb(cb["id"])
        await self.ask_style(tg_user, chat_id, lang, edit_msg=msg_id)

    async def apply_style(self, tg_user: dict, chat_id: int, lang: str,
                          track_id: int | None, keys: list[str], extra: str,
                          human: str) -> None:
        """Ключи (≤3) и/или свой текст → POST /api/tracks/{id}/style.
        Смешение делает сервер (prompts_catalog.fusion), у бота копии нет."""
        if not track_id:
            await self.send(chat_id, t(lang, "send_audio"), lang)
            return
        try:
            await self.api.req(tg_user, "POST", f"/api/tracks/{track_id}/style",
                               json={"style_keys": list(keys or [])[:3],
                                     "extra": (extra or "")[:2000]})
        except Exception as e:  # noqa: BLE001
            await self.report_api_error(chat_id, lang, e)
            return
        st = self.store.state(str(tg_user["id"]))
        self.store.set_state(str(tg_user["id"]),
                             {**st, "track": track_id, "style_human": human, "await": None})
        await self.ask_hero(tg_user, chat_id, lang)

    # ═══════════════════════ герой ═══════════════════════

    async def ask_hero(self, tg_user: dict, chat_id: int, lang: str) -> None:
        """Персонаж обязателен: без него супергенерация вернёт 400. Подаём это
        не как ограничение API, а как часть ремесла — герой держит фильм."""
        try:
            project = await self.api.req(tg_user, "GET", "/api/project")
        except Exception as e:  # noqa: BLE001
            await self.report_api_error(chat_id, lang, e)
            return
        chars = [c for c in project.get("characters", []) if (c.get("name") or "").strip()]
        rows = []
        for c in chars[:6]:
            rows.append([btn("👤 " + esc(c["name"])[:40], f"ch:use:{c['id']}")])
        rows.append([btn(t(lang, "who_text"), "ch:text"),
                     btn(t(lang, "who_photo"), "ch:photo")])
        await self.send(chat_id, t(lang, "who"), lang, reply_markup=kb(rows))

    async def create_character(self, tg_user: dict, chat_id: int, lang: str,
                               description: str) -> dict | None:
        """Персонаж из одной строки: первое слово (или до запятой) — имя."""
        text = description.strip()
        name = text.split(",")[0].strip()
        if len(name.split()) > 3:
            name = " ".join(name.split()[:2])
        name = name.strip(" .,:—-") or ("Hero" if lang == "en" else "Герой")
        try:
            ch = await self.api.req(tg_user, "POST", "/api/characters",
                                    json={"name": name[:60], "description": text[:1500],
                                          "is_main": True})
        except Exception as e:  # noqa: BLE001
            await self.report_api_error(chat_id, lang, e)
            return None
        await self.send(chat_id, t(lang, "char_added", name=esc(ch["name"])), lang)
        return ch

    # ═══════════════════════ подтверждение и запуск ═══════════════════════

    async def show_confirm(self, tg_user: dict, chat_id: int, lang: str) -> None:
        st = self.store.state(str(tg_user["id"]))
        track_id = st.get("track")
        if not track_id:
            await self.send(chat_id, t(lang, "send_audio"), lang)
            return
        try:
            project = await self.api.req(tg_user, "GET", "/api/project")
            acc = await self.api.req(tg_user, "GET", "/api/account")
        except Exception as e:  # noqa: BLE001
            await self.report_api_error(chat_id, lang, e)
            return
        track = next((x for x in project.get("tracks", []) if x["id"] == track_id), None)
        if not track:
            await self.send(chat_id, t(lang, "send_audio"), lang)
            return

        engine, per_scene = await self.engine_for(tg_user)
        dur = int(track.get("audio_duration_sec") or 0)
        scenes = len(track.get("scenes") or []) or max(1, round(dur / 6))
        cost = scenes * per_scene
        points = int(acc.get("points") or 0)
        heroes = ", ".join(c["name"] for c in project.get("characters", [])
                           if (c.get("name") or "").strip()) or "—"
        # Оценка времени: две-пять минут на сцену (кадры + оживление), берём
        # середину. Обещать точную минуту тут нельзя и не нужно.
        eta = human_time(scenes * 190, lang)

        text = t(lang, "confirm", title=esc(track.get("title")),
                 style=esc(st.get("style_human") or "—"), hero=esc(heroes),
                 scenes=scenes, engine=esc(engine), cost=cost,
                 left=max(0, points - cost), eta=eta)
        rows = [
            [btn(t(lang, "go"), f"go:{track_id}")],
            [btn(t(lang, "manual"), f"man:{track_id}"),
             btn(t(lang, "change_style"), "st:!again")],
            [btn(t(lang, "rename"), f"ttl:{track_id}"),
             btn(t(lang, "extras"), f"x:{track_id}")],
        ]
        if cost > points:
            short = cost - points
            rows.insert(0, [btn(t(lang, "buy_points", n=short), "pay:packs")])
        await self.send(chat_id, text, lang, reply_markup=kb(rows))

    async def start_supergen(self, tg_user: dict, chat_id: int, lang: str,
                             track_id: int) -> None:
        try:
            await self.api.req(tg_user, "POST", f"/api/tracks/{track_id}/supergen")
        except Exception as e:  # noqa: BLE001
            await self.report_api_error(chat_id, lang, e)
            return
        project = await self.api.req(tg_user, "GET", "/api/project")
        track = next((x for x in project.get("tracks", []) if x["id"] == track_id), {})
        text = self.render_progress(track, lang, started=time.time())
        msg = await self.send(chat_id, text, lang,
                              reply_markup=kb(self.progress_kb(track_id, lang)))
        self.store.watch_add(track_id, project["id"], str(tg_user["id"]), chat_id,
                             msg["message_id"])
        self.store.watch_touch(track_id, text)
        # Закрепляем: длинная генерация не должна тонуть в переписке.
        with contextlib.suppress(TgError):
            await self.tg.call("pinChatMessage", chat_id=chat_id,
                               message_id=msg["message_id"], disable_notification=True)
        self.store.set_state(str(tg_user["id"]), None)

    def progress_kb(self, track_id: int, lang: str) -> list[list[dict]]:
        row = [btn(t(lang, "show_frame"), f"pk:{track_id}")]
        # Кнопка «Стоп» появляется, только если отмена реально подключена на
        # сервере. Кнопка, которая ничего не делает, — обман, а не забота.
        if self.api.caps.get("supergen_cancel"):
            row.append(btn(t(lang, "stop"), f"pstop:{track_id}"))
        return [row]

    # ═══════════════════════ прогресс ═══════════════════════

    @staticmethod
    def progress_of(track: dict) -> tuple[str, int, int, int]:
        """(этап, процент, сделано, всего) — считается ПО СЦЕНАМ, а не по
        тексту статуса.

        `supergen_note` — свободная русская строка, её нельзя ни показывать
        англоязычному человеку, ни разбирать регуляркой: первая же правка
        формулировки сломает разбор. Зато /api/project отдаёт все сцены с их
        файлами, и по ним прогресс считается точно и без языка.

        Веса этапов неравные (кадры и видео — по 42 %), иначе бар стоит на
        нуле первые десять минут, а потом прыгает."""
        scenes = track.get("scenes") or []
        total = len(scenes)
        if track.get("clip_url") or track.get("clip_status") == "done":
            return "done", 100, total, total
        if track.get("clip_status") in ("queued", "running"):
            return "assemble", 95, total, total
        if not total:
            if track.get("scenes_status") == "running":
                return "scenes", 5, 0, 0
            return "story", 2, 0, 0
        frames = sum(1 for s in scenes if s.get("image_url") and s.get("image_last_url"))
        videos = sum(1 for s in scenes if s.get("video_url"))
        pct = int(8 + 42 * frames / total + 42 * videos / total)
        if frames < total:
            return "frames", min(pct, 92), frames, total
        if videos < total:
            return "video", min(pct, 92), videos, total
        return "assemble", 92, total, total

    # Этап → строка интерфейса. Одна карта на прогресс и на разбор ошибки:
    # разъехавшись, они показывали бы разные названия одного и того же шага.
    STAGE_NAMES = {"story": "st_story", "scenes": "st_scenes", "frames": "st_frames",
                   "video": "st_video", "assemble": "st_assemble", "done": "st_assemble"}

    def render_progress(self, track: dict, lang: str, started: float) -> str:
        stage, pct, done, total = self.progress_of(track)
        if track.get("supergen_status") == "queued":
            stage_text = t(lang, "st_queued")
        else:
            stage_text = t(lang, self.STAGE_NAMES.get(stage, "st_queued"),
                           i=done + 1 if done < total else total, n=total)
        elapsed = max(0, time.time() - (started or time.time()))
        eta = ""
        if 5 <= pct < 100 and elapsed > 60:
            remain = elapsed * (100 - pct) / pct
            eta = t(lang, "eta_left", v=human_time(remain, lang))
        return t(lang, "prog", title=esc(track.get("title")), bar=bar(pct), pct=pct,
                 stage=stage_text, elapsed=human_time(elapsed, lang), eta=eta)

    async def watch_loop(self) -> None:
        """Опрос активных генераций и обновление закреплённых сообщений.

        Один запрос на проект за круг (у альбома может считаться несколько
        треков), правка сообщения — только когда текст реально изменился и не
        чаще EDIT_GAP: иначе Telegram отвечает «message is not modified» и
        штрафует за темп."""
        while True:
            try:
                rows = self.store.watch_all()
                if rows:
                    cache: dict[tuple[str, int], dict] = {}
                    for w in rows:
                        try:
                            await self.tick_watch(w, cache)
                        except Exception as e:  # noqa: BLE001
                            log.warning("наблюдение за треком %s: %s", w["track_id"], e)
                # Тем же тактом — события «генерация готова» из API: они
                # покрывают запуски с сайта и из мини-аппа, не только из бота.
                await self.poll_events()
            except Exception as e:  # noqa: BLE001
                log.exception("сбой цикла наблюдения: %s", e)
            await asyncio.sleep(WATCH_POLL)

    async def tick_watch(self, w: sqlite3.Row, cache: dict) -> None:
        tg_id, chat_id = str(w["tg_id"]), int(w["chat_id"])
        row = self.store.user(tg_id)
        if not row:
            self.store.watch_drop(w["track_id"])
            return
        lang = row["lang"] or "en"
        tg_user = {"id": int(tg_id)}
        key = (tg_id, int(w["project_id"]))
        if key not in cache:
            cache[key] = await self.api.req(
                tg_user, "GET", f"/api/project?project_id={int(w['project_id'])}")
        project = cache[key]
        track = next((x for x in project.get("tracks", []) if x["id"] == w["track_id"]), None)
        if not track:
            self.store.watch_drop(w["track_id"])
            return

        status = track.get("supergen_status") or ""
        stage, pct, _, _ = self.progress_of(track)

        if status == "error":
            await self.finish_error(tg_user, chat_id, lang, track, w)
            return
        if status == "done" or (track.get("clip_url") and status != "running"):
            await self.finish_ok(tg_user, chat_id, lang, track, w)
            return

        if (row["notify"] or "all") != "all":
            return  # человек попросил не дёргать его на каждом шаге
        text = self.render_progress(track, lang, w["started"])
        if text == (w["last_text"] or ""):
            return
        if time.monotonic() - self._last_edit.get(chat_id, 0) < EDIT_GAP:
            return
        self._last_edit[chat_id] = time.monotonic()
        try:
            await self.edit(chat_id, w["msg_id"], text,
                            reply_markup=kb(self.progress_kb(w["track_id"], lang)))
            self.store.watch_touch(w["track_id"], text)
        except TgError as e:
            if e.code == 403:
                # Заблокировал бота: снимаем с наблюдения, генерацию не трогаем —
                # клип соберётся и будет ждать его на сайте.
                log.info("чат %s заблокировал бота — снимаю трек %s с наблюдения",
                         chat_id, w["track_id"])
                self.store.watch_drop(w["track_id"])
            elif e.code == 400 and "message to edit not found" in e.description:
                self.store.watch_drop(w["track_id"])
            else:
                raise

    async def finish_ok(self, tg_user: dict, chat_id: int, lang: str,
                        track: dict, w: sqlite3.Row) -> None:
        self.store.watch_drop(track["id"])
        with contextlib.suppress(TgError):
            await self.tg.call("unpinChatMessage", chat_id=chat_id, message_id=w["msg_id"])
        with contextlib.suppress(TgError):
            await self.edit(chat_id, w["msg_id"],
                            t(lang, "prog", title=esc(track.get("title")), bar=bar(100),
                              pct=100, stage="✅", elapsed=human_time(
                                  time.time() - (w["started"] or time.time()), lang),
                              eta=""), reply_markup=None)
        # ГОТОВО — обязательно НОВЫМ сообщением: правка старого не даёт пуша, а
        # человек ушёл из чата два часа назад и ничего бы не увидел.
        await self.send(chat_id, t(lang, "done_title", title=esc(track.get("title")),
                                   n=len(track.get("scenes") or []),
                                   dur=mmss(track.get("audio_duration_sec"))), lang)
        # Отметка «этот клип уже отдан»: событие из /internal/bot-events о том
        # же треке не должно прислать его второй раз.
        if track.get("clip_url"):
            self.store.set_kv(f"clip_sent:{track['id']}",
                              track["clip_url"].rsplit("/", 1)[-1])
        # Отдача клипа = транскод + загрузка, это минуты. Отдельной задачей,
        # иначе цикл наблюдения замирает и чужие генерации теряют прогресс.
        self.spawn(self.send_clip(tg_user, chat_id, lang, track["id"], track=track),
                   "send_clip")

    async def finish_error(self, tg_user: dict, chat_id: int, lang: str,
                           track: dict, w: sqlite3.Row) -> None:
        self.store.watch_drop(track["id"])
        with contextlib.suppress(TgError):
            await self.tg.call("unpinChatMessage", chat_id=chat_id, message_id=w["msg_id"])
        stage, pct, done, total = self.progress_of(track)
        why = track.get("supergen_note") or track.get("clip_error") or ""
        rows = [[btn(t(lang, "retry"), f"go:{track['id']}"),
                 btn(t(lang, "manual"), f"man:{track['id']}")]]
        if SUPPORT_CONTACT:
            rows.append([url_btn(t(lang, "support"), SUPPORT_CONTACT)])
        await self.send(chat_id, t(lang, "failed",
                                   stage=t(lang, self.STAGE_NAMES.get(stage, "st_queued"),
                                           i=done, n=total),
                                   why=esc(clip_text(why, 220))), lang,
                        reply_markup=kb(rows))

    # ═══════════════════════ отдача клипа ═══════════════════════

    async def send_clip(self, tg_user: dict, chat_id: int, lang: str,
                        track_id: int, track: dict | None = None) -> None:
        """Клип в чат: оригинал если влезает, иначе превью + ссылка.

        Проверяем размер ДО скачивания — тянуть 300 МБ в контейнер бота, чтобы
        потом упереться в лимит Telegram, бессмысленно."""
        if track is None or not track.get("clip_url"):
            # Снимок мог устареть на секунды между «готово» и записью файла —
            # перечитываем, вместо того чтобы объявлять клип потерянным.
            project = await self.api.req(tg_user, "GET", "/api/project")
            track = next((x for x in project.get("tracks", []) if x["id"] == track_id), None)
        if not track or not track.get("clip_url"):
            await self.send(chat_id, t(lang, "err_generic", why="no clip"), lang)
            return
        clip_name = track["clip_url"].rsplit("/", 1)[-1]
        title = esc(track.get("title"))
        rows = [[btn(t(lang, "redo"), f"man:{track_id}"), btn(t(lang, "new_track"), "new")]]

        # Уже отправляли — Telegram отдаёт file_id, второй раз файл не гоняем.
        cached = self.store.file_id(f"clip:{clip_name}")
        link = await self.clip_link(track_id)
        if link:
            rows.insert(0, [url_btn(t(lang, "original"), link)])
        if cached:
            try:
                await self.tg.call("sendVideo", chat_id=chat_id, video=cached,
                                   caption=title, parse_mode="HTML",
                                   supports_streaming=True, reply_markup=kb(rows))
                return
            except TgError as e:
                log.info("file_id клипа протух (%s) — перезаливаю", e.description[:80])
                self.store.forget_file(f"clip:{clip_name}")

        size = await self.api.media_size(tg_user, f"/api/media/{clip_name}")
        if size and size <= TG_UPLOAD_LIMIT:
            await self.upload_video(tg_user, chat_id, lang, f"/api/media/{clip_name}",
                                    f"clip:{clip_name}", title, rows, track)
            return

        # Не влезает — просим API собрать превью и ждём его.
        note = await self.send(chat_id, t(lang, "preview_building"), lang)
        prev = await self.wait_preview(track_id)
        with contextlib.suppress(TgError):
            await self.tg.call("deleteMessage", chat_id=chat_id, message_id=note["message_id"])
        if not prev:
            text = t(lang, "preview_failed")
            await self.send(chat_id, f"{text}\n{link or ''}", lang, reply_markup=kb(rows))
            return
        cap_note = (t(lang, "teaser_note", mb=round(size / 1048576)) if prev.get("teaser")
                    else t(lang, "preview_note", w=720))
        await self.upload_video(tg_user, chat_id, lang,
                                f"/api/media/{prev['filename']}",
                                f"prev:{prev['filename']}",
                                f"{title}\n<i>{cap_note}</i>", rows, track)

    async def clip_link(self, track_id: int) -> str:
        try:
            data = await self.api._internal("/internal/clip-link", {"track_id": track_id})
            return data.get("url") or ""
        except Exception as e:  # noqa: BLE001
            log.warning("не выписал ссылку на клип %s: %s", track_id, e)
            return ""

    async def wait_preview(self, track_id: int, limit_s: int = 1500) -> dict | None:
        deadline = time.time() + limit_s
        while time.time() < deadline:
            try:
                data = await self.api._internal("/internal/clip-preview", {"track_id": track_id})
            except Exception as e:  # noqa: BLE001
                log.warning("превью клипа %s: %s", track_id, e)
                return None
            if data.get("status") == "done":
                return data
            if data.get("status") == "error":
                log.warning("превью клипа %s не собралось: %s", track_id, data.get("error"))
                return None
            await asyncio.sleep(6)
        return None

    async def upload_video(self, tg_user: dict, chat_id: int, lang: str, path: str,
                           cache_key: str, caption: str, rows: list, track: dict) -> None:
        local = os.path.join(TMP_DIR, f"out_{abs(hash(cache_key)) % 10**8}.mp4")
        try:
            await self.tg.call("sendChatAction", chat_id=chat_id, action="upload_video")
            await self.api.stream_to(tg_user, path, local, limit=TG_UPLOAD_LIMIT + 2**20)
            with open(local, "rb") as fh:
                res = await self.tg.call(
                    "sendVideo", chat_id=chat_id,
                    files={"video": ("clip.mp4", fh.read(), "video/mp4")},
                    caption=clip_text(caption, CAPTION_LIMIT), parse_mode="HTML",
                    supports_streaming="true",
                    width=720, height=1280,
                    duration=int(track.get("audio_duration_sec") or 0) or None,
                    reply_markup=kb(rows), timeout=900.0)
            file_id = ((res or {}).get("video") or {}).get("file_id")
            if file_id:
                self.store.remember_file(cache_key, file_id)
        except Exception as e:  # noqa: BLE001
            log.warning("отправка клипа не прошла: %s", e)
            await self.send(chat_id, t(lang, "preview_failed"), lang, reply_markup=kb(rows))
        finally:
            with contextlib.suppress(OSError):
                os.remove(local)

    async def send_photo(self, tg_user: dict, chat_id: int, url_path: str,
                         caption: str = "", rows: list | None = None) -> None:
        """Кадр в чат. Берём МИНИАТЮРУ (/api/thumb): оригиналы 4К не пролезают
        в лимит фото 10 МБ, да и смотреть их в ленте незачем."""
        name = url_path.rsplit("/", 1)[-1]
        cached = self.store.file_id(f"ph:{name}")
        if cached:
            with contextlib.suppress(TgError):
                await self.tg.call("sendPhoto", chat_id=chat_id, photo=cached,
                                   caption=clip_text(caption, CAPTION_LIMIT) or None,
                                   parse_mode="HTML",
                                   reply_markup=kb(rows) if rows else None)
                return
        local = os.path.join(TMP_DIR, f"ph_{abs(hash(name)) % 10**8}.jpg")
        try:
            await self.api.stream_to(tg_user, f"/api/thumb/{name}", local, limit=TG_PHOTO_LIMIT)
            with open(local, "rb") as fh:
                res = await self.tg.call(
                    "sendPhoto", chat_id=chat_id,
                    files={"photo": ("frame.jpg", fh.read(), "image/jpeg")},
                    caption=clip_text(caption, CAPTION_LIMIT) or None, parse_mode="HTML",
                    reply_markup=kb(rows) if rows else None, timeout=300.0)
            photos = (res or {}).get("photo") or []
            if photos:
                self.store.remember_file(f"ph:{name}", photos[-1]["file_id"])
        except Exception as e:  # noqa: BLE001
            log.warning("не отправил кадр %s: %s", name, e)
        finally:
            with contextlib.suppress(OSError):
                os.remove(local)

    # ═══════════════════════ мои клипы ═══════════════════════

    async def show_clips(self, tg_user: dict, chat_id: int, lang: str, page: int = 0,
                         edit_msg: int | None = None) -> None:
        try:
            projects = await self.api.req(tg_user, "GET", "/api/projects")
            tracks = []
            for p in projects:
                pr = await self.api.req(tg_user, "GET", f"/api/project?project_id={p['id']}")
                tracks.extend(pr.get("tracks", []))
        except Exception as e:  # noqa: BLE001
            await self.report_api_error(chat_id, lang, e)
            return
        ready = [x for x in tracks if x.get("clip_url")]
        ready.sort(key=lambda x: x["id"], reverse=True)
        if not ready:
            await self.send(chat_id, t(lang, "clips_empty"), lang)
            return

        per = 5
        pages = max(1, (len(ready) + per - 1) // per)
        page = max(0, min(page, pages - 1))
        chunk = ready[page * per:(page + 1) * per]
        lines = [t(lang, "clips_title", n=len(ready)), ""]
        rows = []
        for i, tr in enumerate(chunk, start=page * per + 1):
            lines.append(t(lang, "clip_row", i=i, title=esc(tr.get("title")),
                           dur=mmss(tr.get("audio_duration_sec")),
                           scenes=len(tr.get("scenes") or []),
                           style="🎞" if tr.get("film_grain") else "🎬"))
            rows.append([btn(f"{i}. " + (tr.get("title") or "—")[:28], f"clip:{tr['id']}")])
        nav = []
        if page > 0:
            nav.append(btn(t(lang, "prev"), f"cl:{page - 1}"))
        if page < pages - 1:
            nav.append(btn(t(lang, "next"), f"cl:{page + 1}"))
        if nav:
            rows.append(nav)
        text = "\n".join(lines)
        if edit_msg:
            await self.edit(chat_id, edit_msg, text, reply_markup=kb(rows))
        else:
            await self.send(chat_id, text, lang, reply_markup=kb(rows))

    # ═══════════════════════ токены, тариф, оплата ═══════════════════════

    async def show_points(self, tg_user: dict, chat_id: int, lang: str) -> None:
        try:
            acc = await self.api.req(tg_user, "GET", "/api/account")
        except Exception as e:  # noqa: BLE001
            await self.report_api_error(chat_id, lang, e)
            return
        bill = await self.billing()
        card = next((p for p in bill.get("plans", []) if p["id"] == acc.get("plan")), {})
        engines = " · ".join(f"{k} {v}" for k, v in (card.get("engines") or {}).items())
        until = ""
        if acc.get("plan_until"):
            until = " · " + str(acc["plan_until"])[:10]
        rows = [[btn(t(lang, "buy"), "pay:packs"), btn(t(lang, "plans"), "pay:plans")],
                [btn(t(lang, "paysupport_btn"), "paysupport")]]
        await self.send(chat_id, t(lang, "points_title",
                                   plan=esc(acc.get("plan_title") or ""), until=until,
                                   points=acc.get("points", 0),
                                   movies=acc.get("movies_left", 0),
                                   engines=esc(engines)), lang, reply_markup=kb(rows))

    async def show_packs(self, tg_user: dict, chat_id: int, lang: str) -> None:
        bill = await self.billing()
        rows = []
        for pack in bill.get("packs", []):
            xtr = price_override("PACK", pack["id"], stars_price(pack["usd_cents"]))
            label = f"{pack['points']} — {xtr} ⭐"
            if pack.get("badge"):
                label += f" · {pack['badge']}"
            rows.append([btn(label, f"buy:pack:{pack['id']}")])
        # Ссылки на сайт в витрине НЕТ намеренно. Сама по себе она безобидна,
        # но стоящая в списке цен читается как альтернативный способ оплаты —
        # то есть ровно тот steering, за который Telegram снимает приложение.
        # Открыть студию можно командой /site.
        await self.send(chat_id, t(lang, "packs_title"), lang, reply_markup=kb(rows))

    async def show_plans(self, tg_user: dict, chat_id: int, lang: str) -> None:
        bill = await self.billing()
        rows = []
        for plan in bill.get("plans", []):
            if plan["id"] == "free" or plan["usd_cents"] <= 0:
                continue
            xtr = price_override("PLAN", plan["id"], stars_price(plan["usd_cents"]))
            if xtr > STARS_SUB_CAP:
                # Дороже потолка подписки Telegram — счёт физически не создать.
                # Внутри Telegram такой тариф просто отсутствует: объяснять его
                # через ссылку на сайт нельзя, это steering.
                continue
            rows.append([btn(f"{plan['title']} — {xtr} ⭐ / 30 " +
                             ("days" if lang == "en" else "дн."),
                             f"buy:plan:{plan['id']}")])
        await self.send(chat_id, t(lang, "plans_title"), lang, reply_markup=kb(rows))

    async def invoice_pack(self, tg_user: dict, chat_id: int, lang: str, pack_id: str) -> None:
        bill = await self.billing()
        pack = next((p for p in bill.get("packs", []) if p["id"] == pack_id), None)
        if not pack:
            return
        xtr = price_override("PACK", pack_id, stars_price(pack["usd_cents"]))
        await self.tg.call(
            "sendInvoice", chat_id=chat_id,
            title=t(lang, "pay_title", n=pack["points"]),
            description=t(lang, "pay_desc", n=pack["points"], brand=BRAND),
            payload=f"pack:{pack_id}", provider_token="", currency="XTR",
            prices=[{"label": f"{pack['points']} points", "amount": xtr}])

    async def invoice_plan(self, tg_user: dict, chat_id: int, lang: str, plan_id: str) -> None:
        """Подписка звёздами: период строго 30 дней, продлевает сам Telegram.

        createInvoiceLink, а не sendInvoice: подписочный период поддерживает
        только он. Ссылку отдаём кнопкой."""
        bill = await self.billing()
        plan = next((p for p in bill.get("plans", []) if p["id"] == plan_id), None)
        if not plan:
            return
        xtr = price_override("PLAN", plan_id, stars_price(plan["usd_cents"]))
        if xtr > STARS_SUB_CAP:
            await self.send(chat_id, t(lang, "plan_too_big", plan=plan["title"]), lang)
            return
        engines = ", ".join((plan.get("engines") or {}).keys())
        try:
            link = await self.tg.call(
                "createInvoiceLink",
                title=t(lang, "plan_pay_title", plan=plan["title"]),
                description=t(lang, "plan_pay_desc", points=plan["points"], engines=engines),
                payload=f"plan:{plan_id}:month", provider_token="", currency="XTR",
                prices=[{"label": plan["title"], "amount": xtr}],
                subscription_period=STARS_SUB_PERIOD)
        except TgError as e:
            log.warning("не создал ссылку на подписку %s: %s", plan_id, e)
            await self.send(chat_id, t(lang, "err_generic", why=esc(e.description[:200])), lang)
            return
        await self.send(chat_id, f"<b>{esc(plan['title'])}</b> — {xtr} ⭐ / 30 "
                                 f"{'days' if lang == 'en' else 'дн.'}", lang,
                        reply_markup=kb([[url_btn(f"⭐ {xtr}", link)]]))

    async def on_pre_checkout(self, q: dict) -> None:
        """Ответить надо за 10 секунд, иначе платёж срывается. Здесь только
        проверка, что товар существует, — деньги выдаются после оплаты."""
        payload = str(q.get("invoice_payload") or "")
        ok, reason = True, ""
        bill = await self.billing()
        if payload.startswith("pack:"):
            ok = any(p["id"] == payload.split(":")[1] for p in bill.get("packs", []))
        elif payload.startswith("plan:"):
            ok = any(p["id"] == payload.split(":")[1] for p in bill.get("plans", []))
        else:
            ok = False
        if not ok:
            reason = "This item is no longer available."
        with contextlib.suppress(TgError):
            await self.tg.call("answerPreCheckoutQuery", pre_checkout_query_id=q["id"],
                               ok=ok, error_message=reason or None)

    async def on_paid(self, msg: dict, tg_user: dict, lang: str) -> None:
        chat_id = msg["chat"]["id"]
        sp = msg["successful_payment"]
        payload = str(sp.get("invoice_payload") or "")
        charge = str(sp.get("telegram_payment_charge_id") or "")
        body = {"tg_id": str(tg_user["id"]), "charge_id": charge}
        # Фактическая сумма в центах едет в payload: при скидке приглашённого
        # она отличается от прайса, а доля амбассадора считается от неё.
        cents = [int(p) for p in payload.split(":")[2:] if p.isdigit()]
        if cents:
            body["amount_cents"] = cents[-1]
        if payload.startswith("pack:"):
            body.update({"kind": "topup", "pack": payload.split(":")[1]})
        elif payload.startswith("plan:"):
            parts = payload.split(":")
            body.update({"kind": "plan", "plan": parts[1],
                         "period": parts[2] if len(parts) > 2 else "month"})
            # Первый платёж ПОДПИСКИ: только его charge_id принимает
            # editUserStarSubscription, последний не подходит. Раньше он
            # никуда не сохранялся, и отменить подписку было физически нечем.
            if sp.get("is_first_recurring") or sp.get("subscription_expiration_date"):
                body["subscription"] = True
        else:
            log.warning("оплата с непонятным payload %r (charge %s)", payload, charge)
            return
        try:
            res = await self.api._internal("/internal/stars-grant", body)
        except Exception as e:  # noqa: BLE001
            # Деньги взяты, выдача не прошла — это ровно тот случай, когда
            # молчать нельзя: пишем и человеку, и в лог с charge_id для возврата.
            log.error("ОПЛАТА %s НЕ ВЫДАНА (%s): %s", charge, payload, e)
            await self.send(chat_id, t(lang, "err_generic",
                                       why="payment received, granting failed — "
                                           f"/paysupport (id {esc(charge)})"), lang)
            return
        what = (t(lang, "paid_plan", plan=res.get("plan_title") or "")
                if body.get("kind") == "plan" else t(lang, "paid_topup"))
        await self.send(chat_id, t(lang, "paid_ok", what=what,
                                   points=res.get("points", 0)), lang)

    async def cmd_refund(self, msg: dict, lang: str, args: str) -> None:
        """Возврат звёзд И ОТКАТ ВЫДАННОГО.

        Раньше здесь был голый refundStarPayment: деньги уходили обратно, а
        токены и месяц тарифа оставались у человека. Теперь звонок идёт в
        /internal/stars-refund — он сам зовёт Telegram, списывает выданные
        токены, откатывает plan_until и отменяет начисление амбассадору."""
        chat_id = msg["chat"]["id"]
        parts = args.split()
        if len(parts) != 2:
            await self.send(chat_id, "usage: /refund &lt;user_tg_id&gt; &lt;charge_id&gt;", lang)
            return
        try:
            res = await self.api._internal("/internal/stars-refund",
                                           {"tg_id": parts[0], "charge_id": parts[1]})
            await self.send(chat_id, f"✅ refunded · points {res.get('points', 0)} · "
                                     f"plan {esc(str(res.get('plan') or ''))}", lang)
            return
        except Exception as e:  # noqa: BLE001
            log.warning("возврат %s не прошёл через API: %s", parts[1], str(e)[:200])
        # Служебный контур молчит — возвращаем хотя бы деньги, а расхождение
        # догонит суточная сверка. Молча не делать ничего здесь нельзя.
        try:
            await self.tg.call("refundStarPayment", user_id=int(parts[0]),
                               telegram_payment_charge_id=parts[1])
            await self.send(chat_id, "✅ refunded (звёзды вернул, выданное "
                                     "откатить не смог — проверь API)", lang)
        except TgError as e:
            await self.send(chat_id, f"⚠️ {esc(e.description)}", lang)

    async def on_subscription(self, upd: dict) -> None:
        """Апдейт `subscription`: человек отменил подписку, у него не хватило
        звёзд или он её возобновил.

        Отмену Telegram присылает ТОЛЬКО так. Без этого обработчика мы бы не
        узнали ни об одной отмене и продолжали считать человека платящим."""
        sub = upd.get("subscription") or {}
        who = sub.get("user") or sub.get("from") or {}
        tg_id = str(who.get("id") or "")
        state = str(sub.get("state") or sub.get("status") or "").lower()
        if not tg_id or not state:
            log.warning("апдейт subscription без tg_id/state: %s", str(upd)[:200])
            return
        try:
            await self.api._internal("/internal/stars-subscription",
                                     {"tg_id": tg_id, "state": state})
        except Exception as e:  # noqa: BLE001
            log.error("подписка %s → %s не записалась: %s", tg_id, state, str(e)[:200])
            return
        row = self.store.user(tg_id)
        chat_id = row["chat_id"] if row else None
        if not chat_id:
            return  # человек боту не писал — писать ему некуда
        lang = self.lang_of(tg_id, "en")
        if state == "failed":
            # Не хватило звёзд на продление: об этом человеку надо сказать,
            # иначе тариф просто исчезнет и это будет выглядеть поломкой.
            await self.send(chat_id, t(lang, "sub_failed"), lang)
        elif state in ("canceled", "cancelled"):
            await self.send(chat_id, t(lang, "sub_canceled"), lang)

    # ═══════════════════════ партнёрка ═══════════════════════

    async def show_partners(self, tg_user: dict, chat_id: int, lang: str) -> None:
        try:
            amb = await self.api.req(tg_user, "GET", "/api/ambassador")
        except Exception as e:  # noqa: BLE001
            await self.report_api_error(chat_id, lang, e)
            return
        if not amb.get("is_ambassador"):
            await self.send(chat_id, t(lang, "partners_off",
                                       disc=amb.get("discount_pct", 0),
                                       rew=amb.get("reward_pct", 0)), lang,
                            reply_markup=kb([[btn(t(lang, "partners_join"), "amb:join")]]))
            return
        code = amb.get("code") or ""
        web = amb.get("link") or f"{PUBLIC_BASE_URL}/?ref={code}"
        tg_link = f"https://t.me/{BOT_USERNAME}?start=ref_{code}" if BOT_USERNAME else ""
        stats = amb.get("stats") or {}
        # Ссылка едет параметром внутрь другого адреса — без кодирования «?ref=»
        # обрезался бы вместе с кодом, и партнёр делился бы пустой ссылкой.
        pitch = "Make a music video with AI" if lang == "en" else "Клип за один вечер"
        share = ("https://t.me/share/url?url=" + quote(tg_link or web, safe="") +
                 "&text=" + quote(pitch, safe=""))
        rows = [[url_btn(t(lang, "partners_share"), share)]]
        if stats.get("available_kopeks", 0) > 0:
            rows.append([btn(t(lang, "payout"), "amb:payout")])
        await self.send(chat_id, t(lang, "partners_on", code=esc(code),
                                   disc=amb.get("discount_pct", 0),
                                   rew=amb.get("reward_pct", 0),
                                   web=esc(web), tg=esc(tg_link or "—"),
                                   invited=stats.get("invited", 0),
                                   buyers=stats.get("buyers", 0),
                                   avail=money(stats.get("available_kopeks", 0))),
                        lang, reply_markup=kb(rows))

    # ═══════════════════════ персонажи ═══════════════════════

    async def show_characters(self, tg_user: dict, chat_id: int, lang: str) -> None:
        try:
            project = await self.api.req(tg_user, "GET", "/api/project")
        except Exception as e:  # noqa: BLE001
            await self.report_api_error(chat_id, lang, e)
            return
        chars = project.get("characters", [])
        if not chars:
            await self.send(chat_id, t(lang, "chars_empty"), lang,
                            reply_markup=kb([[btn(t(lang, "char_add"), "ch:new")]]))
            return
        await self.send(chat_id, t(lang, "chars_title"), lang)
        for c in chars[:10]:
            desc = clip_text(c.get("description") or "", 500)
            attrs = ", ".join(a["name"] for a in c.get("attributes", []) if a.get("name"))
            text = f"👤 <b>{esc(c['name'])}</b>\n{esc(desc)}"
            if attrs:
                text += f"\n🎩 {esc(attrs)}"
            rows = [
                [btn(t(lang, "char_photos"), f"ch:ph:{c['id']}"),
                 btn(t(lang, "model_btn"), f"ch:model:{c['id']}")],
                [btn(t(lang, "char_attr"), f"ch:attr:{c['id']}"),
                 btn(t(lang, "char_del"), f"ch:del:{c['id']}")],
            ]
            photos = c.get("photos") or []
            if photos:
                await self.send_photo(tg_user, chat_id, photos[0]["url"], text, rows)
            else:
                await self.send(chat_id, text, lang, reply_markup=kb(rows))
        await self.send(chat_id, "—", lang,
                        reply_markup=kb([[btn(t(lang, "char_add"), "ch:new")]]))

    # ═══════════════ тренды, мокапы, 3D-моделька ═══════════════

    async def load_trends(self) -> list[dict]:
        """Витрина трендов — публичный /api/trends, каждый раз свежий: цена
        в кнопке обязана совпасть со списанием, кэшировать её нельзя."""
        try:
            r = await self.api.http.get("/api/trends")
            return (r.json() or {}).get("presets") or []
        except Exception as e:  # noqa: BLE001
            log.warning("не забрал тренды: %s", e)
            return []

    async def send_witness_album(self, chat_id: int, items: list[tuple[str, str]]) -> None:
        """Альбом витринных картинок (постеры трендов, превью шаблонов) через
        служебный роут: у витрины нет владельца, публичные роуты её прячут."""
        files, media, tmp = {}, [], []
        try:
            for n, (fname, cap) in enumerate(items[:ALBUM_LIMIT]):
                if not fname:
                    continue
                local = os.path.join(TMP_DIR, f"wt_{abs(hash(fname)) % 10**8}.jpg")
                r = await self.api.http.get(f"/internal/witness-media/{fname}",
                                            params={"thumb": 1},
                                            headers={"X-Internal-Key": INTERNAL_KEY})
                if r.status_code >= 400:
                    continue
                with open(local, "wb") as fh:
                    fh.write(r.content)
                tmp.append(local)
                key = f"p{n}"
                files[key] = (f"{key}.jpg", open(local, "rb").read(), "image/jpeg")
                media.append({"type": "photo", "media": f"attach://{key}", "caption": cap})
            if media:
                await self.tg.call("sendMediaGroup", chat_id=chat_id, media=media,
                                   files=files, timeout=600.0)
        except Exception as e:  # noqa: BLE001
            log.warning("витринный альбом не ушёл: %s", e)
        finally:
            for p in tmp:
                with contextlib.suppress(OSError):
                    os.remove(p)

    async def show_trends(self, tg_user: dict, chat_id: int, lang: str) -> None:
        presets = await self.load_trends()
        if not presets:
            await self.send(chat_id, t(lang, "trends_empty"), lang)
            return
        top = presets[:6]
        album = [(p["poster_url"].rsplit("/", 1)[-1] if p.get("poster_url") else "",
                  f"{i}. {p['title']}") for i, p in enumerate(top, 1)]
        await self.send_witness_album(chat_id, [x for x in album if x[0]])
        rows = [[btn(f"{i}. {p['title']} — ⚡{p.get('cost_points', 0)}"[:60],
                     f"tf:{p['id']}")] for i, p in enumerate(top, 1)]
        await self.send(chat_id, t(lang, "trends_title"), lang, reply_markup=kb(rows))

    async def pick_trend(self, tg_user: dict, chat_id: int, lang: str, preset_id: int) -> None:
        presets = await self.load_trends()
        p = next((x for x in presets if int(x["id"]) == int(preset_id)), None)
        if not p:
            await self.send(chat_id, t(lang, "trends_empty"), lang)
            return
        self.store.set_state(str(tg_user["id"]), {
            "await": "trend_photo", "trend": int(p["id"]),
            "cost": int(p.get("cost_points") or 0), "gtitle": p["title"]})
        await self.send(chat_id, t(lang, "trend_pick", title=esc(p["title"])), lang)

    async def run_trend(self, tg_user: dict, chat_id: int, lang: str) -> None:
        st = self.store.state(str(tg_user["id"]))
        path, preset = st.get("photo_path"), st.get("trend")
        self.store.set_state(str(tg_user["id"]), None)
        if not preset or not path or not os.path.exists(path):
            await self.send(chat_id, t(lang, "no_photo"), lang)
            return
        try:
            with open(path, "rb") as fh:
                await self.api.req(tg_user, "POST", f"/api/trends/{int(preset)}/make",
                                   files={"photo": ("photo.jpg", fh.read(), "image/jpeg")})
        except Exception as e:  # noqa: BLE001
            await self.report_api_error(chat_id, lang, e)
            return
        finally:
            with contextlib.suppress(OSError):
                os.remove(path)
        await self.send(chat_id, t(lang, "gen_started"), lang)

    async def show_mockups(self, tg_user: dict, chat_id: int, lang: str) -> None:
        try:
            data = await self.api.req(tg_user, "GET", "/api/mockup/templates")
        except Exception as e:  # noqa: BLE001
            await self.report_api_error(chat_id, lang, e)
            return
        tpls = data.get("templates") or []
        cost = int(data.get("cost_points") or 1)
        # Витрина из шести: сперва showcase с превью, потом остальные с превью.
        ordered = ([x for x in tpls if x.get("showcase") and x.get("preview_url")]
                   + [x for x in tpls if not x.get("showcase") and x.get("preview_url")]
                   + [x for x in tpls if not x.get("preview_url")])
        top = ordered[:6]
        lkey = "en" if lang == "en" else "ru"
        album = [(x["preview_url"].rsplit("/", 1)[-1] if x.get("preview_url") else "",
                  f"{i}. {x.get(lkey) or x['id']}") for i, x in enumerate(top, 1)]
        await self.send_witness_album(chat_id, [a for a in album if a[0]])
        rows = [[btn(f"{i}. {x.get('emoji', '')} {x.get(lkey) or x['id']} — ⚡{cost}"[:60],
                     f"mk:{x['id']}")] for i, x in enumerate(top, 1)]
        await self.send(chat_id, t(lang, "mockup_title", n=cost), lang,
                        reply_markup=kb(rows))

    async def pick_mockup(self, tg_user: dict, chat_id: int, lang: str, tpl_id: str) -> None:
        try:
            data = await self.api.req(tg_user, "GET", "/api/mockup/templates")
        except Exception as e:  # noqa: BLE001
            await self.report_api_error(chat_id, lang, e)
            return
        tpl = next((x for x in data.get("templates") or [] if x["id"] == tpl_id), None)
        if not tpl:
            return
        lkey = "en" if lang == "en" else "ru"
        self.store.set_state(str(tg_user["id"]), {
            "await": "mockup_photo", "tpl": tpl_id,
            "cost": int(data.get("cost_points") or 1),
            "gtitle": tpl.get(lkey) or tpl_id})
        await self.send(chat_id, t(lang, "mockup_pick",
                                   title=esc(tpl.get(lkey) or tpl_id)), lang)

    async def run_mockup(self, tg_user: dict, chat_id: int, lang: str) -> None:
        st = self.store.state(str(tg_user["id"]))
        path, tpl = st.get("photo_path"), st.get("tpl")
        title = st.get("gtitle") or "Товар"
        self.store.set_state(str(tg_user["id"]), None)
        if not tpl or not path or not os.path.exists(path):
            await self.send(chat_id, t(lang, "no_photo"), lang)
            return
        try:
            # Товары бота живут в одном проекте вида «мокап» — как на сайте.
            projects = await self.api.req(tg_user, "GET", "/api/projects")
            pid = next((p["id"] for p in projects
                        if p.get("mode") == "mockup" or p.get("kind") == "mockup"), 0)
            if not pid:
                pr = await self.api.req(tg_user, "POST", "/api/projects",
                                        json={"kind": "mockup", "name": "Мокапы"})
                pid = pr["id"]
            track = await self.api.req(tg_user, "POST", f"/api/tracks?project_id={pid}",
                                       data={"title": str(title)[:120]})
            with open(path, "rb") as fh:
                await self.api.req(tg_user, "POST", f"/api/tracks/{track['id']}/photos",
                                   files={"photo": ("item.jpg", fh.read(), "image/jpeg")})
            await self.api.req(tg_user, "POST",
                               f"/api/tracks/{track['id']}/mockup-from-template",
                               json={"template_id": tpl})
        except Exception as e:  # noqa: BLE001
            await self.report_api_error(chat_id, lang, e)
            return
        finally:
            with contextlib.suppress(OSError):
                os.remove(path)
        await self.send(chat_id, t(lang, "gen_started"), lang)

    async def start_model3d(self, tg_user: dict, chat_id: int, lang: str) -> None:
        try:
            info = await self.api.req(tg_user, "GET", "/api/model-sheet")
        except Exception as e:  # noqa: BLE001
            await self.report_api_error(chat_id, lang, e)
            return
        self.store.set_state(str(tg_user["id"]), {
            "await": "model3d_photo", "cost": int(info.get("cost") or 0)})
        await self.send(chat_id, t(lang, "model3d_ask"), lang)

    async def run_model3d(self, tg_user: dict, chat_id: int, lang: str) -> None:
        st = self.store.state(str(tg_user["id"]))
        path = st.get("photo_path")
        self.store.set_state(str(tg_user["id"]), None)
        if not path or not os.path.exists(path):
            await self.send(chat_id, t(lang, "no_photo"), lang)
            return
        try:
            name = (tg_user.get("first_name") or "Hero").strip()[:60] or "Hero"
            ch = await self.api.req(tg_user, "POST", "/api/characters",
                                    json={"name": name, "description": "",
                                          "is_main": False})
            with open(path, "rb") as fh:
                await self.api.req(tg_user, "POST", f"/api/characters/{ch['id']}/photos",
                                   files={"photo": ("hero.jpg", fh.read(), "image/jpeg")})
        except Exception as e:  # noqa: BLE001
            await self.report_api_error(chat_id, lang, e)
            return
        finally:
            with contextlib.suppress(OSError):
                os.remove(path)
        await self.make_model(tg_user, chat_id, lang, ch["id"])

    # ═══════════ уведомления о генерациях, запущенных где угодно ═══════════

    async def poll_events(self) -> None:
        """Забрать у API события «генерация готова» и разослать результаты.

        Ловит и то, что человек запустил НА САЙТЕ: клип, тренд, мокап-кадр.
        Доставка at-most-once — сервер помечает строки при выдаче."""
        # capabilities(), а не кэш напрямую: на старте бот мог подняться
        # раньше API (docker перезапускает оба разом), и пустой кэш без
        # повторного запроса выключал бы уведомления навсегда.
        caps = await self.api.capabilities()
        if not caps.get("bot_events"):
            return
        try:
            data = await self.api._internal("/internal/bot-events", {"limit": 20})
        except Exception as e:  # noqa: BLE001
            log.warning("события бота не забрались: %s", str(e)[:120])
            return
        for ev in data.get("events") or []:
            self.spawn(self.deliver_event(ev), "event")

    async def deliver_event(self, ev: dict) -> None:
        tg_id = str(ev.get("tg_id") or "")
        row = self.store.user(tg_id)
        chat_id = row["chat_id"] if row else None
        if not chat_id:
            return  # человек боту не писал — писать ему некуда
        lang = (row["lang"] or "en")
        tg_user = {"id": int(tg_id)}
        kind, ok = str(ev.get("kind") or ""), ev.get("status") == "done"
        title = esc(ev.get("title") or "")
        why = esc(clip_text(str(ev.get("error") or ""), 200))
        if kind == "clip":
            track_id = int(ev.get("track_id") or 0)
            if self.store.watch_has(track_id):
                return  # запущено из бота — доставит цикл наблюдения
            if ok:
                # Один клип — одно сообщение, даже если событий два (сборка
                # руками после супергенерации и т.п.).
                key = f"clip_sent:{track_id}"
                if self.store.get_kv(key) == (ev.get("filename") or ""):
                    return
                self.store.set_kv(key, ev.get("filename") or "")
                await self.send(chat_id, t(lang, "evt_clip_done", title=title), lang)
                await self.send_clip(tg_user, chat_id, lang, track_id)
            else:
                rows = [[btn(t(lang, "retry"), f"go:{track_id}")]]
                await self.send(chat_id, t(lang, "evt_clip_fail", title=title, why=why),
                                lang, reply_markup=kb(rows))
        elif kind == "trend":
            if ok and ev.get("filename"):
                rows = [[btn(t(lang, "menu_trends"), "menu:trends")]]
                await self.upload_video(tg_user, chat_id, lang,
                                        f"/api/media/{ev['filename']}",
                                        f"tv:{ev['filename']}",
                                        t(lang, "trend_ready", title=title),
                                        rows, {"audio_duration_sec": 0})
            elif not ok:
                await self.send(chat_id, t(lang, "gen_failed",
                                           title=title or "Trend", why=why), lang)
        elif kind == "mockup":
            if ok and ev.get("filename"):
                rows = [[btn(t(lang, "menu_mockup"), "menu:mockup")],
                        [url_btn("🌐 " + ("web studio" if lang == "en" else "веб-студия"),
                                 f"{PUBLIC_BASE_URL}/")]]
                await self.send_photo(tg_user, chat_id, f"/api/media/{ev['filename']}",
                                      t(lang, "mockup_ready", title=title), rows)
            elif not ok:
                await self.send(chat_id, t(lang, "gen_failed",
                                           title=title or "Mockup", why=why), lang)

    # ═══════════════════════ ручной режим ═══════════════════════

    async def show_manual(self, tg_user: dict, chat_id: int, lang: str, track_id: int,
                          edit_msg: int | None = None) -> None:
        """Ручной режим: всё, что честно ложится на линейный чат.

        Нарезка листа раскадровки по ячейкам, сравнение кадров бок о бок и
        массовая правка тридцати промптов сюда НЕ выносятся — в чате это
        получилось бы имитацией инструмента, а не инструментом. Для них внизу
        ссылка в веб-студию: аккаунт и токены те же."""
        try:
            project = await self.api.req(tg_user, "GET", "/api/project")
        except Exception as e:  # noqa: BLE001
            await self.report_api_error(chat_id, lang, e)
            return
        track = next((x for x in project.get("tracks", []) if x["id"] == track_id), None)
        if not track:
            await self.send(chat_id, t(lang, "send_audio"), lang)
            return
        scenes = track.get("scenes") or []
        frames = sum(1 for s in scenes if s.get("image_url") and s.get("image_last_url"))
        videos = sum(1 for s in scenes if s.get("video_url"))
        appr = sum(1 for s in scenes if s.get("approved"))
        text = t(lang, "manual_title", title=esc(track.get("title")), scenes=len(scenes),
                 frames=frames, videos=videos, appr=appr)
        rows = [
            [btn(t(lang, "m_scenes"), f"tr:{track_id}:scenes"),
             btn(t(lang, "m_story"), f"tr:{track_id}:story")],
            [btn(t(lang, "m_board"), f"tr:{track_id}:board"),
             btn(t(lang, "m_list"), f"sb:{track_id}:0")],
            [btn(t(lang, "m_frames"), f"tr:{track_id}:frames"),
             btn(t(lang, "m_videos"), f"tr:{track_id}:videos")],
            [btn(t(lang, "m_assemble"), f"tr:{track_id}:assemble")],
            [btn(t(lang, "m_settings"), f"x:{track_id}"),
             btn(t(lang, "go"), f"go:{track_id}")],
            [url_btn("🌐 " + ("web studio" if lang == "en" else "веб-студия"),
                     f"{PUBLIC_BASE_URL}/")],
        ]
        if edit_msg:
            await self.edit(chat_id, edit_msg, text, reply_markup=kb(rows))
        else:
            await self.send(chat_id, text, lang, reply_markup=kb(rows))

    async def track_action(self, tg_user: dict, chat_id: int, lang: str,
                           track_id: int, action: str) -> None:
        """Пакетные шаги конвейера — те же роуты, что жмёт кнопка на сайте."""
        paths = {
            "story": ("POST", "/api/project/generate-story"),
            "scenes": ("POST", f"/api/tracks/{track_id}/generate-scenes"),
            "board": ("POST", f"/api/tracks/{track_id}/generate-storyboard"),
            "frames": ("POST", f"/api/tracks/{track_id}/generate-all-frames"),
            "videos": ("POST", f"/api/tracks/{track_id}/generate-all-videos"),
            "assemble": ("POST", f"/api/tracks/{track_id}/assemble"),
        }
        if action not in paths:
            return
        method, path = paths[action]
        try:
            await self.api.req(tg_user, method, path)
        except Exception as e:  # noqa: BLE001
            await self.report_api_error(chat_id, lang, e)
            return
        await self.send(chat_id, t(lang, "m_queued"), lang)
        # Ставим на наблюдение, чтобы прогресс шёл тем же живым сообщением.
        await self.attach_watch(tg_user, chat_id, lang, track_id)

    async def attach_watch(self, tg_user: dict, chat_id: int, lang: str,
                           track_id: int) -> None:
        project = await self.api.req(tg_user, "GET", "/api/project")
        track = next((x for x in project.get("tracks", []) if x["id"] == track_id), None)
        if not track:
            return
        text = self.render_progress(track, lang, started=time.time())
        msg = await self.send(chat_id, text, lang,
                              reply_markup=kb(self.progress_kb(track_id, lang)))
        self.store.watch_add(track_id, project["id"], str(tg_user["id"]), chat_id,
                             msg["message_id"])
        self.store.watch_touch(track_id, text)

    async def show_storyboard(self, tg_user: dict, chat_id: int, lang: str,
                              track_id: int, page: int = 0) -> None:
        """Раскадровка альбомами по 10 — предел media group у Telegram."""
        try:
            project = await self.api.req(tg_user, "GET", "/api/project")
        except Exception as e:  # noqa: BLE001
            await self.report_api_error(chat_id, lang, e)
            return
        track = next((x for x in project.get("tracks", []) if x["id"] == track_id), None)
        scenes = (track or {}).get("scenes") or []
        if not scenes:
            await self.send(chat_id, t(lang, "m_queued"), lang)
            return
        pages = max(1, (len(scenes) + ALBUM_LIMIT - 1) // ALBUM_LIMIT)
        page = max(0, min(page, pages - 1))
        chunk = scenes[page * ALBUM_LIMIT:(page + 1) * ALBUM_LIMIT]

        lines, rows, row = [], [], []
        for s in chunk:
            i = s["position"]
            mark = "✅" if s.get("approved") else ("🎥" if s.get("video_url")
                                                  else ("🖼" if s.get("image_url") else "⬜"))
            lines.append(f"{mark} <b>#{i}</b> · {mmss(s.get('start_sec'))} · "
                         f"{esc(clip_text(s.get('shot_note') or s.get('lyric_line') or '', 90))}")
            row.append(btn(f"#{i}", f"sc:{s['id']}:card"))
            if len(row) == 5:
                rows.append(row)
                row = []
        if row:
            rows.append(row)
        nav = []
        if page > 0:
            nav.append(btn(t(lang, "prev"), f"sb:{track_id}:{page - 1}"))
        nav.append(btn(f"{page + 1}/{pages}", "nop"))
        if page < pages - 1:
            nav.append(btn(t(lang, "next"), f"sb:{track_id}:{page + 1}"))
        rows.append(nav)
        rows.append([btn(t(lang, "back"), f"man:{track_id}")])

        # Картинки — альбомом, если они есть; иначе просто список.
        media = [s for s in chunk if s.get("image_thumb_url")]
        if media:
            with contextlib.suppress(TgError):
                await self.send_album(tg_user, chat_id, media)
        await self.send(chat_id, "\n".join(lines), lang, reply_markup=kb(rows))

    async def send_album(self, tg_user: dict, chat_id: int, scenes: list) -> None:
        files, media = {}, []
        tmp = []
        try:
            for n, s in enumerate(scenes[:ALBUM_LIMIT]):
                name = s["image_thumb_url"].rsplit("/", 1)[-1]
                local = os.path.join(TMP_DIR, f"al_{abs(hash(name)) % 10**8}.jpg")
                await self.api.stream_to(tg_user, f"/api/thumb/{name}", local,
                                         limit=TG_PHOTO_LIMIT)
                tmp.append(local)
                key = f"p{n}"
                files[key] = (f"{key}.jpg", open(local, "rb").read(), "image/jpeg")
                media.append({"type": "photo", "media": f"attach://{key}",
                              "caption": f"#{s['position']}"})
            if media:
                await self.tg.call("sendMediaGroup", chat_id=chat_id, media=media,
                                   files=files, timeout=600.0)
        except Exception as e:  # noqa: BLE001
            log.warning("альбом раскадровки не ушёл: %s", e)
        finally:
            for p in tmp:
                with contextlib.suppress(OSError):
                    os.remove(p)

    async def show_scene(self, tg_user: dict, chat_id: int, lang: str, scene_id: int) -> None:
        try:
            project = await self.api.req(tg_user, "GET", "/api/project")
        except Exception as e:  # noqa: BLE001
            await self.report_api_error(chat_id, lang, e)
            return
        scene = track = None
        for tr in project.get("tracks", []):
            for s in tr.get("scenes") or []:
                if s["id"] == scene_id:
                    scene, track = s, tr
                    break
        if not scene:
            return
        total = len(track.get("scenes") or [])
        frames = "🖼🖼" if (scene.get("image_url") and scene.get("image_last_url")) \
            else ("🖼" if scene.get("image_url") else t(lang, "pending"))
        video = "✅" if scene.get("video_url") else \
            (scene.get("video_status") or t(lang, "pending"))
        text = t(lang, "scene_card", i=scene["position"], n=total,
                 shot=esc(scene.get("shot_size") or "—"),
                 t=mmss(scene.get("start_sec")),
                 note=esc(clip_text(scene.get("shot_note") or scene.get("lyric_line") or "", 400)),
                 frames=frames, video=esc(str(video)),
                 appr="✅" if scene.get("approved") else "⬜")
        rows = [
            [btn(t(lang, "sc_first"), f"sc:{scene_id}:first"),
             btn(t(lang, "sc_last"), f"sc:{scene_id}:last")],
            [btn(t(lang, "sc_both"), f"sc:{scene_id}:both"),
             btn(t(lang, "sc_video"), f"sc:{scene_id}:video")],
            [btn(t(lang, "sc_unappr") if scene.get("approved") else t(lang, "sc_appr"),
                 f"sc:{scene_id}:appr"),
             btn(t(lang, "sc_mid"), f"sc:{scene_id}:mid")],
            [btn(t(lang, "sc_prompt"), f"sc:{scene_id}:prompt"),
             btn(t(lang, "sc_ref"), f"sc:{scene_id}:ref")],
            [btn(t(lang, "back"), f"sb:{track['id']}:{(scene['position'] - 1) // ALBUM_LIMIT}")],
        ]
        if scene.get("image_thumb_url"):
            await self.send_photo(tg_user, chat_id, scene["image_thumb_url"], text, rows)
        else:
            await self.send(chat_id, text, lang, reply_markup=kb(rows))

    async def scene_action(self, tg_user: dict, chat_id: int, lang: str,
                           scene_id: int, action: str) -> None:
        tg_id = str(tg_user["id"])
        try:
            if action in ("first", "last", "both"):
                await self.api.req(tg_user, "POST",
                                   f"/api/scenes/{scene_id}/generate-frames?which={action}")
                await self.send(chat_id, t(lang, "m_queued"), lang)
            elif action == "video":
                await self.api.req(tg_user, "POST", f"/api/scenes/{scene_id}/generate-video",
                                   json={})
                await self.send(chat_id, t(lang, "m_queued"), lang)
            elif action == "mid":
                await self.api.req(tg_user, "POST", f"/api/scenes/{scene_id}/generate-midframes")
                await self.send(chat_id, t(lang, "m_queued"), lang)
            elif action == "appr":
                project = await self.api.req(tg_user, "GET", "/api/project")
                cur = False
                for tr in project.get("tracks", []):
                    for s in tr.get("scenes") or []:
                        if s["id"] == scene_id:
                            cur = bool(s.get("approved"))
                await self.api.req(tg_user, "POST", f"/api/scenes/{scene_id}/approve",
                                   json={"approved": not cur})
                await self.show_scene(tg_user, chat_id, lang, scene_id)
            elif action == "prompt":
                self.store.set_state(tg_id, {"await": "scene_prompt", "scene": scene_id})
                await self.send(chat_id, t(lang, "sc_prompt_ask"), lang,
                                reply_markup={"force_reply": True})
            elif action == "ref":
                self.store.set_state(tg_id, {"await": "scene_ref", "scene": scene_id})
                await self.send(chat_id, t(lang, "sc_ref_ask"), lang,
                                reply_markup={"force_reply": True})
            elif action == "card":
                await self.show_scene(tg_user, chat_id, lang, scene_id)
        except Exception as e:  # noqa: BLE001
            await self.report_api_error(chat_id, lang, e)

    # ═══════════════════════ настройки трека ═══════════════════════

    async def show_extras(self, tg_user: dict, chat_id: int, lang: str, track_id: int,
                          edit_msg: int | None = None) -> None:
        try:
            project = await self.api.req(tg_user, "GET", "/api/project")
        except Exception as e:  # noqa: BLE001
            await self.report_api_error(chat_id, lang, e)
            return
        track = next((x for x in project.get("tracks", []) if x["id"] == track_id), None)
        if not track:
            return
        on, off = t(lang, "on"), t(lang, "off")
        rows = [
            [btn(t(lang, "ex_lyrics") + (" ✅" if track.get("lyrics") else ""),
                 f"xl:{track_id}"),
             btn(t(lang, "ex_comment") + (" ✅" if track.get("comment") else ""),
                 f"xc:{track_id}")],
            [btn(t(lang, "ex_grain", v=on if track.get("film_grain") else off),
                 f"xg:{track_id}")],
            [btn(t(lang, "ex_nostory", v=on if track.get("no_story") else off),
                 f"xn:{track_id}")],
            [btn(t(lang, "rename"), f"ttl:{track_id}"),
             btn(t(lang, "change_style"), "st:!again")],
            [btn(t(lang, "back"), f"man:{track_id}"), btn(t(lang, "go"), f"go:{track_id}")],
        ]
        text = t(lang, "extras_title")
        if edit_msg:
            await self.edit(chat_id, edit_msg, text, reply_markup=kb(rows))
        else:
            await self.send(chat_id, text, lang, reply_markup=kb(rows))

    # ═══════════════════════ настройки бота ═══════════════════════

    async def show_settings(self, tg_user: dict, chat_id: int, lang: str,
                            edit_msg: int | None = None) -> None:
        row = self.store.user(str(tg_user["id"]))
        notify = (row["notify"] if row else "all") or "all"
        nmap = {"all": "notify_all", "done": "notify_done"}
        rows = [
            [btn(t(lang, "set_lang", v="English" if lang == "en" else "Русский"),
                 "set:lang:toggle")],
            [btn(t(lang, "set_notify", v=t(lang, nmap.get(notify, "notify_all"))),
                 "set:notify")],
            [btn(t(lang, "set_site"), "set:site")],
            [btn(t(lang, "set_unlink"), "set:unlink")],
        ]
        if BOT_USERNAME:
            rows.insert(2, [url_btn(t(lang, "open_app"),
                                    f"https://t.me/{BOT_USERNAME}/{MINIAPP_NAME}")])
        if edit_msg:
            await self.edit(chat_id, edit_msg, t(lang, "settings_title"), reply_markup=kb(rows))
        else:
            await self.send(chat_id, t(lang, "settings_title"), lang, reply_markup=kb(rows))

    async def cmd_status(self, tg_user: dict, chat_id: int, lang: str) -> None:
        rows = self.store.watch_for(str(tg_user["id"]))
        if not rows:
            await self.send(chat_id, t(lang, "nothing_running"), lang)
            return
        try:
            project = await self.api.req(tg_user, "GET", "/api/project")
        except Exception as e:  # noqa: BLE001
            await self.report_api_error(chat_id, lang, e)
            return
        lines = [t(lang, "running_now")]
        for w in rows:
            track = next((x for x in project.get("tracks", []) if x["id"] == w["track_id"]), None)
            if not track:
                continue
            _, pct, _, _ = self.progress_of(track)
            lines.append(f"• <b>{esc(track.get('title'))}</b> — {pct}%")
        await self.send(chat_id, "\n".join(lines), lang)

    # ═══════════════════════ диспетчер сообщений ═══════════════════════

    async def on_message(self, msg: dict) -> None:
        tg_user = msg.get("from") or {}
        if not tg_user or tg_user.get("is_bot"):
            return
        chat = msg.get("chat") or {}
        if chat.get("type") != "private":
            return  # бот личный: в группах ему делать нечего
        chat_id = chat["id"]
        tg_id = str(tg_user["id"])
        lang = self.lang_of(tg_id, "ru" if str(tg_user.get("language_code") or "").startswith("ru") else "en")
        self.store.upsert_user(tg_id, chat_id=chat_id)

        if msg.get("successful_payment"):
            await self.on_paid(msg, tg_user, lang)
            return

        text = (msg.get("text") or "").strip()
        if text.startswith("/"):
            cmd, _, args = text.partition(" ")
            await self.on_command(msg, tg_user, lang, cmd.split("@")[0].lower(), args.strip())
            return

        if msg.get("audio") or msg.get("voice") or self.is_audio_doc(msg):
            await self.handle_audio(msg, tg_user, lang)
            return
        if msg.get("photo"):
            await self.handle_photo(msg, tg_user, lang)
            return

        # Кнопки нижней клавиатуры приходят обычным текстом. Сверяем ОБА
        # языка: у человека в чате может висеть клавиатура, отправленная до
        # смены языка, и её кнопки не должны превращаться в «пришли аудио».
        for key, fn in (("menu_clips", self.show_clips), ("menu_chars", self.show_characters),
                        ("menu_trends", self.show_trends), ("menu_mockup", self.show_mockups),
                        ("menu_model3d", self.start_model3d),
                        ("menu_points", self.show_points), ("menu_settings", self.show_settings)):
            if text and any(text == t(lg, key) for lg in ("en", "ru")):
                await fn(tg_user, chat_id, lang)
                return

        if text:
            await self.handle_text(msg, tg_user, lang, text)

    @staticmethod
    def is_audio_doc(msg: dict) -> bool:
        doc = msg.get("document") or {}
        mime = str(doc.get("mime_type") or "")
        name = str(doc.get("file_name") or "").lower()
        return mime.startswith("audio/") or name.endswith((".mp3", ".wav", ".m4a", ".flac", ".ogg"))

    async def on_command(self, msg: dict, tg_user: dict, lang: str, cmd: str, args: str) -> None:
        chat_id = msg["chat"]["id"]
        if cmd == "/start":
            await self.cmd_start(msg, args.strip())
        elif cmd in ("/help", "/how"):
            await self.send(chat_id, t(lang, "help"), lang)
        elif cmd == "/new":
            self.store.set_state(str(tg_user["id"]), None)
            await self.send(chat_id, t(lang, "send_audio"), lang)
        elif cmd == "/clips":
            await self.show_clips(tg_user, chat_id, lang)
        elif cmd in ("/points", "/balance"):
            await self.show_points(tg_user, chat_id, lang)
        elif cmd in ("/partners", "/ref"):
            await self.show_partners(tg_user, chat_id, lang)
        elif cmd in ("/characters", "/cast"):
            await self.show_characters(tg_user, chat_id, lang)
        elif cmd == "/settings":
            await self.show_settings(tg_user, chat_id, lang)
        elif cmd == "/status":
            await self.cmd_status(tg_user, chat_id, lang)
        elif cmd == "/lang":
            new = "ru" if lang == "en" else "en"
            self.store.upsert_user(str(tg_user["id"]), lang=new)
            await self.send(chat_id, t(new, "settings_title"), new,
                            reply_markup=self.main_kb(new))
        elif cmd == "/site":
            await self.send(chat_id, t(lang, "site_link", url=f"{PUBLIC_BASE_URL}/"), lang)
        elif cmd == "/paysupport":
            contact = f"\n{SUPPORT_CONTACT}" if SUPPORT_CONTACT else ""
            await self.send(chat_id, t(lang, "paysupport", contact=contact), lang)
        elif cmd == "/cancel":
            self.store.set_state(str(tg_user["id"]), None)
            await self.send(chat_id, t(lang, "cancelled"), lang)
        elif cmd == "/refund" and str(tg_user["id"]) in ADMIN_TG_IDS:
            await self.cmd_refund(msg, lang, args)
        elif cmd == "/task":
            # ПМ-функция для группы «lolq team»: /task название | high | 2026-09-01
            # Пишем прямо в общую базу задач (/team) — бот живёт в том же
            # образе и томе, что API, отдельный мост тут был бы театром.
            await self.cmd_task(msg, tg_user, lang, args)
        elif cmd == "/tasks":
            await self.cmd_tasks(msg, lang)
        else:
            await self.send(chat_id, t(lang, "help"), lang)

    async def cmd_task(self, msg: dict, tg_user: dict, lang: str, args: str) -> None:
        """Поставить задачу в /team из чата: /task название | приоритет | дата."""
        chat_id = msg["chat"]["id"]
        raw = (args or "").strip()
        if not raw:
            await self.send(chat_id,
                            "Так: /task название | high | 2026-09-01\n"
                            "Приоритет и дата не обязательны.", lang)
            return
        parts = [x.strip() for x in raw.split("|")]
        title = parts[0][:500]
        prio = parts[1].lower() if len(parts) > 1 and parts[1].strip() else "none"
        if prio not in ("none", "low", "medium", "high"):
            prio = "none"
        due = parts[2][:10] if len(parts) > 2 else ""
        who = (tg_user.get("first_name") or tg_user.get("username") or "tg")
        try:
            from db import SessionLocal, TeamTask  # noqa: PLC0415 — общий том
            dbs = SessionLocal()
            try:
                dbs.add(TeamTask(title=title,
                                 description=f"поставлено из Telegram ({who})",
                                 priority=prio, due_at=due,
                                 author_type="agent", author_id=None))
                dbs.commit()
            finally:
                dbs.close()
        except Exception as e:  # noqa: BLE001 — честная ошибка в чат
            await self.send(chat_id, f"не записалась: {str(e)[:120]}", lang)
            return
        extra = (f" · {prio}" if prio != "none" else "") + (f" · до {due}" if due else "")
        await self.send(chat_id,
                        f"✅ Задача поставлена: «{title[:80]}»{extra}\n"
                        f"Доска: {PUBLIC_BASE_URL}/team", lang)

    async def cmd_tasks(self, msg: dict, lang: str) -> None:
        """Свод открытых задач в чат — быстрый статус без перехода на доску."""
        chat_id = msg["chat"]["id"]
        try:
            from db import SessionLocal, TeamTask  # noqa: PLC0415
            dbs = SessionLocal()
            try:
                rows = (dbs.query(TeamTask).filter(TeamTask.status != "done")
                        .order_by(TeamTask.priority.desc(), TeamTask.id)
                        .limit(15).all())
            finally:
                dbs.close()
        except Exception as e:  # noqa: BLE001
            await self.send(chat_id, f"доска не читается: {str(e)[:120]}", lang)
            return
        if not rows:
            await self.send(chat_id, "Открытых задач нет 🎉", lang)
            return
        lines = []
        for t2 in rows:
            mark = {"open": "▫️", "in_progress": "🔸"}.get(t2.status, "▫️")
            tail = (f" · {t2.priority}" if t2.priority != "none" else "") +                    (f" · до {t2.due_at}" if t2.due_at else "")
            lines.append(f"{mark} {t2.title[:70]}{tail}")
        await self.send(chat_id, "Открытые задачи:\n" + "\n".join(lines)
                        + f"\n\nДоска: {PUBLIC_BASE_URL}/team", lang)

    async def handle_text(self, msg: dict, tg_user: dict, lang: str, text: str) -> None:
        """Свободный текст читается только когда бот его ЖДЁТ (ForceReply)."""
        chat_id = msg["chat"]["id"]
        tg_id = str(tg_user["id"])
        st = self.store.state(tg_id)
        waiting = st.get("await")
        if not waiting:
            await self.send(chat_id, t(lang, "send_audio"), lang)
            return
        try:
            if waiting == "style_text":
                self.store.set_state(tg_id, {**st, "await": None})
                await self.apply_style(tg_user, chat_id, lang, st.get("track"),
                                       st.get("styles") or [], text[:2000],
                                       clip_text(text, 60))
            elif waiting == "char_text":
                self.store.set_state(tg_id, {**st, "await": None})
                if await self.create_character(tg_user, chat_id, lang, text):
                    await self.show_confirm(tg_user, chat_id, lang)
            elif waiting == "title":
                await self.api.req(tg_user, "PATCH", f"/api/tracks/{st['track']}",
                                   json={"title": text[:120]})
                self.store.set_state(tg_id, {**st, "await": None})
                await self.send(chat_id, t(lang, "saved"), lang)
                await self.show_confirm(tg_user, chat_id, lang)
            elif waiting in ("lyrics", "comment"):
                await self.api.req(tg_user, "PATCH", f"/api/tracks/{st['track']}",
                                   json={waiting: text[:20000]})
                self.store.set_state(tg_id, {**st, "await": None})
                await self.send(chat_id, t(lang, "saved"), lang)
                await self.show_extras(tg_user, chat_id, lang, st["track"])
            elif waiting == "scene_prompt":
                await self.api.req(tg_user, "PATCH", f"/api/scenes/{st['scene']}",
                                   json={"image_prompt": text[:4000]})
                self.store.set_state(tg_id, None)
                await self.send(chat_id, t(lang, "saved"), lang)
                await self.show_scene(tg_user, chat_id, lang, st["scene"])
            elif waiting == "char_name":
                # Имя героя из потока «Персонажи → Добавить»: фото уже лежат,
                # осталось назвать и предложить модельку с честной ценой.
                char_id = st.get("char")
                await self.api.req(tg_user, "PATCH", f"/api/characters/{char_id}",
                                   json={"name": text[:60].strip() or "Hero"})
                self.store.set_state(tg_id, None)
                rows = []
                with contextlib.suppress(Exception):
                    info = await self.api.req(tg_user, "GET", "/api/model-sheet")
                    rows = [[btn(t(lang, "model_btn") + f" — ⚡{int(info.get('cost') or 0)}",
                                 f"ch:model:{char_id}")]]
                await self.send(chat_id, t(lang, "char_added", name=esc(text[:60])), lang,
                                reply_markup=kb(rows) if rows else None)
            elif waiting == "char_desc":
                await self.api.req(tg_user, "PATCH", f"/api/characters/{st['char']}",
                                   json={"description": text[:1500]})
                self.store.set_state(tg_id, {**st, "await": None})
                await self.make_model(tg_user, chat_id, lang, st["char"])
            elif waiting == "attr":
                name, _, rest = text.partition("—") if "—" in text else text.partition("-")
                await self.api.req(tg_user, "POST", f"/api/characters/{st['char']}/attributes",
                                   json={"name": (name or text)[:60].strip(),
                                         "description": (rest or text)[:800].strip()})
                self.store.set_state(tg_id, None)
                await self.send(chat_id, t(lang, "saved"), lang)
            elif waiting == "payout":
                await self.api.req(tg_user, "POST", "/api/ambassador/details",
                                   json={"details": text[:500]})
                await self.api.req(tg_user, "POST", "/api/ambassador/payout", json={})
                self.store.set_state(tg_id, None)
                await self.send(chat_id, t(lang, "payout_ok"), lang)
        except Exception as e:  # noqa: BLE001
            await self.report_api_error(chat_id, lang, e)

    async def handle_photo(self, msg: dict, tg_user: dict, lang: str) -> None:
        chat_id = msg["chat"]["id"]
        tg_id = str(tg_user["id"])
        st = self.store.state(tg_id)
        waiting = st.get("await")
        photo = (msg.get("photo") or [])[-1]
        if not photo:
            return
        local = os.path.join(TMP_DIR, f"up_{tg_id}_{int(time.time() * 1000)}.jpg")
        try:
            await self.tg.download(photo["file_id"], local)
            if waiting in ("trend_photo", "mockup_photo", "model3d_photo"):
                # Файл живёт до подтверждения цены: платное действие не
                # запускается без явного «Да» с цифрой в кнопке.
                keep = os.path.join(TMP_DIR, f"keep_{tg_id}_{int(time.time() * 1000)}.jpg")
                os.replace(local, keep)
                nxt = {"trend_photo": ("trend_go", "tgo"),
                       "mockup_photo": ("mockup_go", "mgo"),
                       "model3d_photo": ("model3d_go", "m3go")}[waiting]
                st.update({"await": nxt[0], "photo_path": keep})
                self.store.set_state(tg_id, st)
                rows = [[btn(t(lang, "yes_go"), nxt[1]),
                         btn(t(lang, "no_cancel"), "gcancel")]]
                await self.send(chat_id, t(lang, "gen_confirm", n=st.get("cost", 0)),
                                lang, reply_markup=kb(rows))
                return
            data = open(local, "rb").read()
            if waiting == "scene_ref":
                await self.api.req(tg_user, "POST", f"/api/scenes/{st['scene']}/refs",
                                   files={"photo": ("ref.jpg", data, "image/jpeg")})
                self.store.set_state(tg_id, None)
                await self.send(chat_id, t(lang, "sc_ref_ok", i=""), lang)
                return
            if waiting == "char_photo":
                char_id = st.get("char")
                if not char_id:
                    # Первое фото создаёт персонажа: имя человек уточнит позже.
                    ch = await self.api.req(tg_user, "POST", "/api/characters",
                                            json={"name": tg_user.get("first_name") or "Hero",
                                                  "description": "", "is_main": True})
                    char_id = ch["id"]
                    st["char"] = char_id
                res = await self.api.req(tg_user, "POST", f"/api/characters/{char_id}/photos",
                                         files={"photo": ("hero.jpg", data, "image/jpeg")})
                n = len(res.get("photos") or [])
                st["photos"] = n
                self.store.set_state(tg_id, st)
                await self.send(chat_id, t(lang, "who_photo_ok", n=n), lang,
                                reply_markup=kb([[btn(t(lang, "who_ready"), "ch:photodone")]]))
                return
            await self.send(chat_id, t(lang, "send_audio"), lang)
        except Exception as e:  # noqa: BLE001
            await self.report_api_error(chat_id, lang, e)
        finally:
            with contextlib.suppress(OSError):
                os.remove(local)

    # ═══════════════════════ диспетчер кнопок ═══════════════════════

    async def on_callback(self, cb: dict) -> None:
        tg_user = cb["from"]
        msg = cb.get("message") or {}
        chat_id = (msg.get("chat") or {}).get("id")
        if not chat_id:
            await self.answer_cb(cb["id"])
            return
        tg_id = str(tg_user["id"])
        lang = self.lang_of(tg_id)
        data = str(cb.get("data") or "")
        msg_id = msg.get("message_id")

        try:
            if data == "nop":
                await self.answer_cb(cb["id"])
            elif data == "help":
                await self.answer_cb(cb["id"])
                await self.send(chat_id, t(lang, "help"), lang)
            elif data == "new":
                await self.answer_cb(cb["id"])
                self.store.set_state(tg_id, None)
                await self.send(chat_id, t(lang, "send_audio"), lang)
            elif data.startswith("st:"):
                key = data[3:]
                if key == "!again":
                    await self.answer_cb(cb["id"])
                    await self.ask_style(tg_user, chat_id, lang)
                else:
                    await self.on_style_cb(cb, tg_user, lang, key)
            elif data.startswith("ch:"):
                await self.on_char_cb(cb, tg_user, lang, data[3:])
            elif data.startswith("go:"):
                await self.answer_cb(cb["id"], t(lang, "started"))
                await self.start_supergen(tg_user, chat_id, lang, int(data[3:]))
            elif data.startswith("man:"):
                await self.answer_cb(cb["id"])
                await self.show_manual(tg_user, chat_id, lang, int(data[4:]), edit_msg=msg_id)
            elif data.startswith("tr:"):
                _, tid, action = data.split(":", 2)
                await self.answer_cb(cb["id"])
                await self.track_action(tg_user, chat_id, lang, int(tid), action)
            elif data.startswith("sb:"):
                _, tid, page = data.split(":")
                await self.answer_cb(cb["id"])
                await self.show_storyboard(tg_user, chat_id, lang, int(tid), int(page))
            elif data.startswith("sc:"):
                _, sid, action = data.split(":", 2)
                await self.answer_cb(cb["id"])
                await self.scene_action(tg_user, chat_id, lang, int(sid), action)
            elif data.startswith("x:"):
                await self.answer_cb(cb["id"])
                await self.show_extras(tg_user, chat_id, lang, int(data[2:]), edit_msg=msg_id)
            elif data[:2] in ("xg", "xn") and data[2:3] == ":":
                tid = int(data[3:])
                await self.toggle_track(tg_user, chat_id, lang, tid,
                                        "film_grain" if data[:2] == "xg" else "no_story",
                                        cb, msg_id)
            elif data.startswith("xl:") or data.startswith("xc:"):
                field = "lyrics" if data.startswith("xl:") else "comment"
                self.store.set_state(tg_id, {"await": field, "track": int(data[3:])})
                await self.answer_cb(cb["id"])
                await self.send(chat_id, t(lang, f"ex_{field}_ask"), lang,
                                reply_markup={"force_reply": True})
            elif data.startswith("ttl:"):
                st = self.store.state(tg_id)
                self.store.set_state(tg_id, {**st, "await": "title", "track": int(data[4:])})
                await self.answer_cb(cb["id"])
                await self.send(chat_id, t(lang, "rename_ask"), lang,
                                reply_markup={"force_reply": True})
            elif data.startswith("cl:"):
                await self.answer_cb(cb["id"])
                await self.show_clips(tg_user, chat_id, lang, int(data[3:]), edit_msg=msg_id)
            elif data.startswith("clip:"):
                await self.answer_cb(cb["id"])
                await self.send_clip(tg_user, chat_id, lang, int(data[5:]))
            elif data.startswith("pk:"):
                await self.answer_cb(cb["id"])
                await self.peek_frame(tg_user, chat_id, lang, int(data[3:]))
            elif data.startswith("pstop:"):
                await self.stop_supergen(cb, tg_user, chat_id, lang, int(data[6:]))
            elif data == "pay:packs":
                await self.answer_cb(cb["id"])
                await self.show_packs(tg_user, chat_id, lang)
            elif data == "pay:plans":
                await self.answer_cb(cb["id"])
                await self.show_plans(tg_user, chat_id, lang)
            elif data.startswith("buy:pack:"):
                await self.answer_cb(cb["id"])
                await self.invoice_pack(tg_user, chat_id, lang, data.split(":")[2])
            elif data.startswith("buy:plan:"):
                await self.answer_cb(cb["id"])
                await self.invoice_plan(tg_user, chat_id, lang, data.split(":")[2])
            elif data.startswith("big:"):
                # Легаси-кнопка старых сообщений: новые витрины её не рисуют.
                await self.answer_cb(cb["id"])
                bill = await self.billing()
                plan = next((p for p in bill.get("plans", []) if p["id"] == data[4:]), {})
                await self.send(chat_id, t(lang, "plan_too_big",
                                           plan=plan.get("title", "")), lang)
            elif data == "paysupport":
                await self.answer_cb(cb["id"])
                contact = f"\n{SUPPORT_CONTACT}" if SUPPORT_CONTACT else ""
                await self.send(chat_id, t(lang, "paysupport", contact=contact), lang)
            elif data == "amb:join":
                await self.answer_cb(cb["id"])
                await self.api.req(tg_user, "POST", "/api/ambassador/join")
                await self.show_partners(tg_user, chat_id, lang)
            elif data == "amb:payout":
                self.store.set_state(tg_id, {"await": "payout"})
                await self.answer_cb(cb["id"])
                await self.send(chat_id, t(lang, "payout_ask"), lang,
                                reply_markup={"force_reply": True})
            elif data.startswith("tf:"):
                await self.answer_cb(cb["id"])
                await self.pick_trend(tg_user, chat_id, lang, int(data[3:]))
            elif data.startswith("mk:"):
                await self.answer_cb(cb["id"])
                await self.pick_mockup(tg_user, chat_id, lang, data[3:])
            elif data == "tgo":
                await self.answer_cb(cb["id"])
                await self.run_trend(tg_user, chat_id, lang)
            elif data == "mgo":
                await self.answer_cb(cb["id"])
                await self.run_mockup(tg_user, chat_id, lang)
            elif data == "m3go":
                await self.answer_cb(cb["id"])
                await self.run_model3d(tg_user, chat_id, lang)
            elif data == "gcancel":
                st = self.store.state(tg_id)
                if st.get("photo_path"):
                    with contextlib.suppress(OSError):
                        os.remove(st["photo_path"])
                self.store.set_state(tg_id, None)
                await self.answer_cb(cb["id"])
                await self.send(chat_id, t(lang, "cancelled"), lang)
            elif data == "menu:trends":
                await self.answer_cb(cb["id"])
                await self.show_trends(tg_user, chat_id, lang)
            elif data == "menu:mockup":
                await self.answer_cb(cb["id"])
                await self.show_mockups(tg_user, chat_id, lang)
            elif data.startswith("set:"):
                await self.on_settings_cb(cb, tg_user, lang, data[4:], msg_id)
            else:
                await self.answer_cb(cb["id"])
        except Exception as e:  # noqa: BLE001
            await self.answer_cb(cb["id"])
            await self.report_api_error(chat_id, lang, e)

    async def toggle_track(self, tg_user: dict, chat_id: int, lang: str, track_id: int,
                           field: str, cb: dict, msg_id: int) -> None:
        project = await self.api.req(tg_user, "GET", "/api/project")
        track = next((x for x in project.get("tracks", []) if x["id"] == track_id), {})
        await self.api.req(tg_user, "PATCH", f"/api/tracks/{track_id}",
                           json={field: not bool(track.get(field))})
        await self.answer_cb(cb["id"])
        await self.show_extras(tg_user, chat_id, lang, track_id, edit_msg=msg_id)

    async def on_char_cb(self, cb: dict, tg_user: dict, lang: str, action: str) -> None:
        chat_id = cb["message"]["chat"]["id"]
        tg_id = str(tg_user["id"])
        st = self.store.state(tg_id)
        await self.answer_cb(cb["id"])
        if action == "text":
            self.store.set_state(tg_id, {**st, "await": "char_text"})
            await self.send(chat_id, t(lang, "who_ask"), lang,
                            reply_markup={"force_reply": True})
        elif action == "new":
            # «Персонажи → Добавить»: фото → имя → (по желанию) моделька.
            self.store.set_state(tg_id, {"await": "char_photo", "flow": "cast"})
            await self.send(chat_id, t(lang, "who_photo_ask"), lang)
        elif action == "photo":
            self.store.set_state(tg_id, {**st, "await": "char_photo"})
            await self.send(chat_id, t(lang, "who_photo_ask"), lang)
        elif action == "photodone":
            char_id = st.get("char")
            if st.get("flow") == "cast":
                # Поток из «Персонажей»: дальше имя, а не подтверждение клипа.
                self.store.set_state(tg_id, {"await": "char_name", "char": char_id})
                await self.send(chat_id, t(lang, "char_name_ask"), lang,
                                reply_markup={"force_reply": True})
                return
            self.store.set_state(tg_id, {**st, "await": None})
            rows = []
            if char_id:
                rows = [[btn(t(lang, "model_btn"), f"ch:model:{char_id}")]]
            await self.send(chat_id, t(lang, "saved"), lang,
                            reply_markup=kb(rows) if rows else None)
            await self.show_confirm(tg_user, chat_id, lang)
        elif action.startswith("use:"):
            await self.show_confirm(tg_user, chat_id, lang)
        elif action.startswith("model:"):
            char_id = int(action.split(":")[1])
            # Модельке хватает ОДНОГО из двух: описания или фото (ровно как
            # проверяет сервер). Просим описание, только когда нет ни того,
            # ни другого, — а не показываем отказ.
            project = await self.api.req(tg_user, "GET", "/api/project")
            ch = next((c for c in project.get("characters", []) if c["id"] == char_id), {})
            if not (ch.get("description") or "").strip() and not (ch.get("photos") or []):
                self.store.set_state(tg_id, {**st, "await": "char_desc", "char": char_id})
                await self.send(chat_id, t(lang, "model_need_desc"), lang,
                                reply_markup={"force_reply": True})
                return
            await self.make_model(tg_user, chat_id, lang, char_id)
        elif action.startswith("attr:"):
            self.store.set_state(tg_id, {"await": "attr", "char": int(action.split(":")[1])})
            await self.send(chat_id, t(lang, "attr_ask"), lang,
                            reply_markup={"force_reply": True})
        elif action.startswith("del:"):
            with contextlib.suppress(Exception):
                await self.api.req(tg_user, "DELETE", f"/api/characters/{action.split(':')[1]}")
            await self.show_characters(tg_user, chat_id, lang)
        elif action.startswith("ph:"):
            self.store.set_state(tg_id, {"await": "char_photo", "char": int(action.split(":")[1])})
            await self.send(chat_id, t(lang, "who_photo_ask"), lang)

    async def make_model(self, tg_user: dict, chat_id: int, lang: str, char_id: int) -> None:
        """Разворот героя в четырёх ракурсах. Шаг долгий — предупреждаем ДО, а
        не показываем молчание на минуту."""
        await self.send(chat_id, t(lang, "model_wait"), lang)
        try:
            # Синхронный роут ждёт движок картинок — стандартных 120 секунд
            # клиента может не хватить, а падать по таймауту на оплаченной
            # генерации нельзя.
            res = await self.api.req(tg_user, "POST",
                                     f"/api/characters/{char_id}/generate-model",
                                     json={}, timeout=600.0)
        except Exception as e:  # noqa: BLE001
            await self.report_api_error(chat_id, lang, e)
            return
        photos = (res or {}).get("photos") or []
        if photos:
            await self.send_photo(tg_user, chat_id, photos[-1]["url"], t(lang, "model_done"))
        else:
            await self.send(chat_id, t(lang, "model_done"), lang)

    async def on_settings_cb(self, cb: dict, tg_user: dict, lang: str, action: str,
                             msg_id: int) -> None:
        chat_id = cb["message"]["chat"]["id"]
        tg_id = str(tg_user["id"])
        await self.answer_cb(cb["id"])
        if action == "lang:toggle":
            new = "ru" if lang == "en" else "en"
            self.store.upsert_user(tg_id, lang=new)
            await self.show_settings(tg_user, chat_id, new, edit_msg=msg_id)
            await self.send(chat_id, t(new, "settings_title"), new,
                            reply_markup=self.main_kb(new))
        elif action == "notify":
            row = self.store.user(tg_id)
            # Два режима, а не три: «только ошибки» вёл бы себя так же, как
            # «только результат» — готовый клип человек хочет получить всегда.
            order = ["all", "done"]
            cur = (row["notify"] if row else "all") or "all"
            self.store.upsert_user(tg_id, notify=order[(order.index(cur) + 1) % len(order)])
            await self.show_settings(tg_user, chat_id, lang, edit_msg=msg_id)
        elif action == "site":
            await self.send(chat_id, t(lang, "site_link", url=f"{PUBLIC_BASE_URL}/"), lang)
        elif action == "unlink":
            await self.send(chat_id, t(lang, "unlink_no"), lang)

    async def peek_frame(self, tg_user: dict, chat_id: int, lang: str, track_id: int) -> None:
        """Показать последний готовый кадр — «что там сейчас рисуется»."""
        try:
            project = await self.api.req(tg_user, "GET", "/api/project")
        except Exception as e:  # noqa: BLE001
            await self.report_api_error(chat_id, lang, e)
            return
        track = next((x for x in project.get("tracks", []) if x["id"] == track_id), None)
        done = [s for s in (track or {}).get("scenes") or [] if s.get("image_thumb_url")]
        if not done:
            await self.send(chat_id, t(lang, "st_queued"), lang)
            return
        last = done[-1]
        await self.send_photo(tg_user, chat_id, last["image_thumb_url"],
                              f"#{last['position']} · " +
                              esc(clip_text(last.get("shot_note") or "", 300)))

    async def stop_supergen(self, cb: dict, tg_user: dict, chat_id: int, lang: str,
                            track_id: int) -> None:
        if not self.api.caps.get("supergen_cancel"):
            await self.answer_cb(cb["id"], t(lang, "no_stop"), alert=True)
            return
        try:
            await self.api._internal("/internal/supergen-cancel", {"track_id": track_id})
            await self.answer_cb(cb["id"], t(lang, "stopped"), alert=True)
        except Exception:  # noqa: BLE001
            await self.answer_cb(cb["id"], t(lang, "no_stop"), alert=True)

    # ═══════════════════════ главный цикл ═══════════════════════

    async def run(self) -> None:
        self.me = await self.tg.call("getMe")
        global BOT_USERNAME
        if not BOT_USERNAME:
            BOT_USERNAME = self.me.get("username") or ""
        log.info("бот @%s на связи (маршрут «%s»)", self.me.get("username"),
                 self.tg.routes[self.tg.route][0])

        caps = await self.api.capabilities()
        if not caps.get("ok"):
            log.error("служебный контур API не отвечает. Бот НЕ сможет ни выписать "
                      "сессию, ни выдать оплату. Проверь: подключён ли bot_api "
                      "(backend/bot_patch.md), совпадает ли BOT_INTERNAL_KEY, "
                      "доступен ли QLOL_API_BASE=%s", API_BASE)
        else:
            log.info("API: стилей %s, отмена супергенерации %s",
                     caps.get("styles"), "есть" if caps.get("supergen_cancel") else "нет")
        await self.load_styles()
        await self.set_commands()

        self._watcher = asyncio.create_task(self.watch_loop())

        offset = int(self.store.get_kv("offset", 0) or 0)
        while True:
            try:
                updates = await self.tg.call(
                    "getUpdates", offset=offset or None, timeout=POLL_TIMEOUT,
                    allowed_updates=["message", "callback_query", "pre_checkout_query",
                                     # без "subscription" отмена звёздной подписки
                                     # не придёт вообще — молча, без ошибки
                                     "subscription"])
            except TgError as e:
                if e.code == 409:
                    # Второй экземпляр бота с тем же токеном — апдейты будут
                    # теряться у обоих. Одна реплика, жёстко.
                    log.error("409 Conflict: с этим токеном уже работает другой "
                              "процесс getUpdates. Оставь ОДИН экземпляр бота.")
                    await asyncio.sleep(15)
                    continue
                log.warning("getUpdates: %s", e)
                await asyncio.sleep(5)
                continue
            except Exception as e:  # noqa: BLE001
                log.warning("getUpdates упал: %s", e)
                await asyncio.sleep(5)
                continue

            for upd in updates or []:
                offset = max(offset, int(upd["update_id"]) + 1)
                if "pre_checkout_query" in upd:
                    # На это Telegram даёт 10 секунд — отвечаем не откладывая.
                    await self.on_pre_checkout(upd["pre_checkout_query"])
                elif "subscription" in upd:
                    self.spawn(self.on_subscription(upd), "subscription")
                elif "message" in upd:
                    self.spawn(self.on_message(upd["message"]), "message")
                elif "callback_query" in upd:
                    self.spawn(self.on_callback(upd["callback_query"]), "callback")
            if updates:
                self.store.set_kv("offset", offset)

    async def set_commands(self) -> None:
        cmds = {
            "en": [("start", "start over"), ("new", "new clip"), ("clips", "my clips"),
                   ("points", "points and plan"), ("partners", "referral programme"),
                   ("status", "what's running"), ("settings", "language, notifications"),
                   ("site", "open the web studio"), ("paysupport", "payment questions"),
                   ("help", "how it works")],
            "ru": [("start", "начать заново"), ("new", "новый клип"), ("clips", "мои клипы"),
                   ("points", "токены и тариф"), ("partners", "партнёрская программа"),
                   ("status", "что считается"), ("settings", "язык, уведомления"),
                   ("site", "открыть студию"), ("paysupport", "вопросы по оплате"),
                   ("help", "как это работает")],
        }
        for lang, items in cmds.items():
            with contextlib.suppress(TgError):
                await self.tg.call(
                    "setMyCommands",
                    commands=[{"command": c, "description": d} for c, d in items],
                    language_code=lang if lang != "en" else None)

    async def shutdown(self) -> None:
        await self.tg.close()
        await self.api.close()


async def main() -> None:
    if not BOT_TOKEN:
        # Не падаем: контейнер с restart:unless-stopped крутил бы рестарт-луп,
        # а в логах было бы одно и то же исключение вместо внятной причины.
        log.warning("BOT_TOKEN не задан — телеграм-бот ВЫКЛЮЧЕН. Получи токен у "
                    "@BotFather, положи его в infra/.env как BOT_TOKEN и перезапусти "
                    "сервис. Остальной сервис работает как обычно.")
        while True:
            await asyncio.sleep(3600)
    if not INTERNAL_KEY:
        log.error("BOT_INTERNAL_KEY не задан — бот не сможет опознавать людей. "
                  "Сгенерируй ключ (openssl rand -hex 32), положи его в infra/.env "
                  "И там, и там (API и бот читают одну переменную).")
    bot = Bot()
    try:
        await bot.run()
    finally:
        await bot.shutdown()


if __name__ == "__main__":
    with contextlib.suppress(KeyboardInterrupt):
        asyncio.run(main())
