"""Публикация готовых клипов в соцсети через host-агент BIOAURA.

Своего Instagram API у нас нет и не будет: Graph API требует бизнес-аккаунт,
проверку приложения и живёт отдельной жизнью. Вместо него — уже работающий
host-агент `social_browser_publisher` (systemd на сервере msk, слушает
172.18.0.1:8771). Он держит сохранённый профиль Playwright и кликает в веб-морде
Instagram как человек: «Создать» → загрузка mp4 → кадрирование Original →
подпись → «Поделиться».

Что важно знать про агента (проверено разведкой, менять нельзя — он общий):
  * POST /publish СИНХРОННЫЙ. Ответ приходит только когда браузер отработал:
    это до 900 секунд. Идентификатора задачи нет, прогресс не опрашивается —
    свой статус мы ведём сами в поле Track.published_ig.
  * Очередь у агента ОДНА на все проекты (BIOAURA тоже публикует через него),
    воркер один. Наша задача может ждать чужую.
  * Ретрай публикации вслепую = ВТОРОЙ ПОСТ. Поэтому здесь нет ни одной
    повторной попытки /publish. Ретраим только /health — он безобидный.
  * Файл клипа агент качает сам обычным urllib, БЕЗ наших кук. Значит
    /api/media ему не отдаст ничего (401) — нужен публичный подписанный адрес,
    его строит public_clip_url() ниже.
  * Профиль браузера выбирается по account_key. Дефолтный (пустой ключ) —
    общий с BIOAURA, туда лить нельзя: у клипов свой аккаунт, свой ключ.

Молчаливых успехов здесь нет: если публикация не подтверждена агентом, наружу
уходит исключение PublishError с человеческим текстом и машинным кодом.
"""
from __future__ import annotations

import asyncio
import logging
import os
import threading

import httpx
from itsdangerous import BadSignature, SignatureExpired, URLSafeTimedSerializer

log = logging.getLogger("rapclips")

# ─────────────────────────────── настройки ───────────────────────────────

# Host-агент виден из контейнера по адресу docker-шлюза: rapclips-api сидит в
# сети bioaura-organism_default (172.18.0.х), а агент пускает только 127.* и
# 172.16–172.19.* — мы в белом списке по подсети.
PUBLISHER_URL = os.environ.get("SOCIAL_PUBLISHER_URL", "http://172.18.0.1:8771").rstrip("/")

# Ключ профиля браузера. Отдельный аккаунт клипов = отдельный каталог сессии:
# так наши посты не уедут в бренд-аккаунт BIOAURA и два проекта не дерутся за
# лок каталога Chromium.
IG_ACCOUNT_KEY = os.environ.get("SOCIAL_IG_ACCOUNT_KEY", "qlol")

# Публичный адрес сервиса — из него собирается ссылка на mp4 для агента.
PUBLIC_BASE_URL = os.environ.get("PUBLIC_BASE_URL", "https://qlolapp.art").rstrip("/")
# Необязательный обход: агент живёт на том же хосте, что и контейнер, поэтому
# может забрать файл напрямую с http://127.0.0.1:8930 — быстрее и не гоняет
# десятки мегабайт наружу через nginx. Пусто → берём публичный домен.
VIDEO_BASE_URL = (os.environ.get("SOCIAL_VIDEO_BASE_URL", "").strip() or PUBLIC_BASE_URL).rstrip("/")

# Публикация долгая: Playwright кликает вживую. Держим таймаут не меньше, чем
# у самого агента (900 с), иначе оборвём соединение на посте, который уже идёт.
PUBLISH_TIMEOUT_S = float(os.environ.get("SOCIAL_PUBLISH_TIMEOUT_S", "900"))
# Запас поверх лимита публикации. У агента свой такой же лимит: если рвать связь
# секунда в секунду, мы теряем его аккуратный 504 с объяснением и получаем
# безымянный обрыв. Пусть успеет ответить он — текст ошибки будет точнее.
READ_GRACE_S = float(os.environ.get("SOCIAL_READ_GRACE_S", "30"))
# Проверка живости должна быть мгновенной — она дёргается из UI.
HEALTH_TIMEOUT_S = float(os.environ.get("SOCIAL_HEALTH_TIMEOUT_S", "5"))
# Проверка сессии открывает браузер, это десятки секунд.
CHECK_TIMEOUT_S = float(os.environ.get("SOCIAL_CHECK_TIMEOUT_S", "200"))

# Ссылка на клип живёт ограниченное время: это одноразовый пропуск для агента,
# а не вечная публичная раздача чужих файлов.
LINK_TTL_S = int(os.environ.get("SOCIAL_LINK_TTL_S", str(6 * 60 * 60)))
SECRET_KEY = os.environ.get("SECRET_KEY", "")

# Лимит подписи Instagram — 2200 символов. Агент режет сам, но лучше отдать
# уже подрезанный текст: тогда обрыв придётся на границу, которую выбрали мы.
CAPTION_LIMIT = 2200
# Instagram считает больше 30 хэштегов спамом и молча их гасит.
HASHTAG_LIMIT = 30

PLATFORMS = ("instagram", "youtube", "tiktok")

# Подписыватель ссылок на клип. Соль своя: пропуск на файл не должен подходить
# к сессионной куке и наоборот.
_link_signer = URLSafeTimedSerializer(SECRET_KEY or "rapclips-social-unset",
                                      salt="rapclips-social-clip")

# Клипы, которые прямо сейчас публикуются. Защита от двойного поста внутри
# нашего процесса: агент задачу не опознаёт как дубль и опубликует оба раза.
_inflight: set[str] = set()
_inflight_lock = threading.Lock()


class PublishError(RuntimeError):
    """Публикация НЕ состоялась (или не подтверждена — что для нас одно и то же).

    code — машинная метка для фронта и логов:
      offline — служба публикации не запущена / не отвечает;
      login   — сессия соцсети слетела, нужен повторный вход владельцем;
      proxy   — на сервере не поднят VPN, прямой выход запрещён;
      timeout — агент не ответил вовремя, состояние поста НЕИЗВЕСТНО;
      busy    — этот клип уже публикуется;
      config  — кривые входные данные или настройки;
      error   — всё остальное, текст от агента.
    """

    def __init__(self, message: str, code: str = "error") -> None:
        super().__init__(message)
        self.code = code


# ──────────────────────── публичная ссылка на клип ────────────────────────

def clip_link_token(filename: str) -> str:
    """Одноразовый подписанный пропуск на файл клипа.

    Внутри токена лежит само имя файла: отдельный параметр не нужен, подменить
    имя нельзя — подпись на SECRET_KEY."""
    if not SECRET_KEY:
        raise PublishError("не задан SECRET_KEY — нечем подписать ссылку на клип", "config")
    return _link_signer.dumps(os.path.basename(filename))


def clip_from_token(token: str, max_age_s: int | None = None) -> str:
    """Имя файла из пропуска. Пустая строка — просрочен или подделан.

    Хвост «.mp4» в адресе допускаем: некоторые загрузчики требуют «похожий на
    видео» URL, а на разбор токена расширение влиять не должно."""
    raw = (token or "").strip()
    if raw.endswith(".mp4"):
        raw = raw[:-4]
    try:
        name = _link_signer.loads(raw, max_age=max_age_s or LINK_TTL_S)
    except SignatureExpired:
        return ""
    except BadSignature:
        return ""
    return os.path.basename(name) if isinstance(name, str) else ""


def public_clip_url(filename: str) -> str:
    """Абсолютный адрес клипа для host-агента.

    Абсолютный — обязательное требование: относительный путь агент склеит со
    СВОИМ базовым доменом (content.resetaura.io) и скачает чужой файл."""
    return f"{VIDEO_BASE_URL}/api/social/clip/{clip_link_token(filename)}.mp4"


# ─────────────────────────────── подпись ───────────────────────────────

DEFAULT_HASHTAGS = (
    "#клип", "#нейросети", "#aivideo", "#музыка", "#рэп", "#qlolvideo",
)


def _norm_hashtags(tags) -> list[str]:
    """Приводим к виду #слово, чистим пробелы и дубли с сохранением порядка."""
    out: list[str] = []
    seen: set[str] = set()
    for raw in tags or ():
        tag = str(raw).strip().replace(" ", "")
        if not tag:
            continue
        tag = "#" + tag.lstrip("#")
        if len(tag) < 2:
            continue
        key = tag.lower()
        if key in seen:
            continue
        seen.add(key)
        out.append(tag)
        if len(out) >= HASHTAG_LIMIT:
            break
    return out


def build_caption(track_title: str = "", style: str = "", hashtags=None) -> str:
    """Текст поста: название клипа, строка про сервис, ссылка и хэштеги.

    Собирается так, чтобы при переполнении лимита резалось НАЗВАНИЕ, а ссылка и
    хэштеги дожили до конца: ради них пост и публикуется."""
    title = " ".join((track_title or "").split()).strip()
    style_txt = " ".join((style or "").split()).strip()
    tags = _norm_hashtags(hashtags if hashtags is not None else DEFAULT_HASHTAGS)

    head = f"🎬 {title}" if title else "🎬 Новый клип"
    if style_txt:
        head += f"\nСтиль: {style_txt}"
    body = "Клип собран нейросетями: сюжет, раскадровка, анимация — по одному треку."
    link = f"Сделать свой: {PUBLIC_BASE_URL.replace('https://', '').replace('http://', '')}"
    tail = "\n".join(x for x in (body, link, " ".join(tags)) if x)

    caption = f"{head}\n\n{tail}" if head else tail
    if len(caption) <= CAPTION_LIMIT:
        return caption
    # Не влезли — ужимаем голову ровно настолько, чтобы хвост остался целым.
    room = CAPTION_LIMIT - len(tail) - 3  # 3 — на «\n\n» и многоточие
    head = head[:max(0, room)].rstrip() + ("…" if room > 0 else "")
    return f"{head}\n\n{tail}"[:CAPTION_LIMIT]


# ──────────────────────── связь с host-агентом ────────────────────────

def _human_timeout(seconds: float) -> str:
    """«900» → «15 мин», «45» → «45 с»: в тексте ошибки владельцу нужен срок,
    который читается, а не деление на 60 с потерей остатка."""
    s = int(seconds)
    return f"{s // 60} мин" if s >= 120 else f"{s} с"


def _fail_from_status(platform: str, status: int, detail: str) -> PublishError:
    """Ответ агента → честная ошибка для владельца.

    Тексты агента техничные и на смеси языков; переводим их в то, что человек
    может сделать руками, не теряя оригинал в логе."""
    low = (detail or "").lower()
    if status == 503 or "vpn" in low or "прокси" in low:
        return PublishError(
            "на сервере не поднят VPN/прокси — публикация запрещена с российского IP. "
            "Проверь мост выхода и перезапусти службу публикации.", "proxy")
    if "логин" in low or "не залогинен" in low or "login" in low:
        return PublishError(
            f"сессия {platform} слетела, нужен повторный вход владельцем "
            "(см. docs/instagram.md — вход занимает пару минут).", "login")
    if status == 504 or "не завершилась" in low:
        return PublishError(
            "служба публикации не уложилась в отведённое время. Пост мог всё-таки "
            "уйти — проверь аккаунт в Instagram, прежде чем публиковать заново.", "timeout")
    if status == 403:
        return PublishError(
            "служба публикации не принимает запросы с этого адреса "
            "(контейнер вне списка разрешённых подсетей агента).", "config")
    if status == 400:
        return PublishError(f"служба публикации отклонила запрос: {detail or 'некорректные данные'}",
                            "config")
    # Агент качает файл сам и без наших кук: 404/крошечный файл = он не смог
    # забрать клип по ссылке, а не проблема самой публикации.
    if "маленьк" in low or "404" in low or "403" in low:
        return PublishError(
            "служба публикации не смогла скачать клип по ссылке "
            "(истёк срок ссылки или сервис недоступен снаружи). Собери клип заново "
            "или повтори публикацию — ссылка выпишется свежая.", "config")
    return PublishError(detail or f"публикация не удалась (HTTP {status})", "error")


async def publisher_health(retries: int = 2) -> dict:
    """Быстрая проверка живости службы публикации. Исключений не бросает.

    /health у агента без фильтра по IP и без побочных эффектов, поэтому его —
    единственное, что можно спокойно ретраить."""
    last = ""
    for attempt in range(max(1, retries)):
        try:
            async with httpx.AsyncClient(timeout=httpx.Timeout(HEALTH_TIMEOUT_S, connect=2.0)) as client:
                r = await client.get(f"{PUBLISHER_URL}/health")
            data = r.json() if r.headers.get("content-type", "").startswith("application/json") else {}
            if not isinstance(data, dict):
                data = {}
            ready = bool(data.get("publish_ready"))
            return {
                "ok": bool(data.get("ok")),
                "ready": ready,
                "proxy": bool(data.get("proxy_reachable")),
                "last_error": str(data.get("last_error") or "")[:300],
                "account_key": IG_ACCOUNT_KEY,
                "detail": "" if ready else (
                    "служба отвечает, но публиковать не готова: "
                    + (str(data.get("proxy_error") or "") or "нет прокси/VPN")),
            }
        except Exception as e:  # noqa: BLE001 — наружу отдаём статус, а не падение
            last = str(e)
            if attempt + 1 < max(1, retries):
                await asyncio.sleep(0.7)
    log.info("служба публикации не отвечает: %s", last)
    return {"ok": False, "ready": False, "proxy": False, "last_error": last[:300],
            "account_key": IG_ACCOUNT_KEY,
            "detail": "служба публикации не запущена — подними её на сервере "
                      "(systemctl start social-browser-publisher)."}


async def check_session(platform: str = "instagram", account_key: str = "") -> dict:
    """Жива ли сессия соцсети — без публикации. Открывает браузер, это долго.

    Нужна, чтобы после «сессия слетела» владелец мог проверить вход, ничего не
    публикуя, и чтобы повтор публикации не был выстрелом вслепую."""
    platform = (platform or "instagram").strip().lower()
    if platform not in PLATFORMS:
        raise PublishError(f"неизвестная площадка: {platform}", "config")
    key = account_key or (IG_ACCOUNT_KEY if platform == "instagram" else "")
    params = {"platform": platform}
    if key:
        params["account_key"] = key
    try:
        async with httpx.AsyncClient(timeout=httpx.Timeout(CHECK_TIMEOUT_S, connect=5.0)) as client:
            r = await client.get(f"{PUBLISHER_URL}/check", params=params)
    except (httpx.ConnectError, httpx.ConnectTimeout):
        raise PublishError("служба публикации не запущена — проверить вход нечем.", "offline")
    except httpx.TimeoutException:
        raise PublishError("проверка входа не ответила вовремя — служба занята другой задачей.",
                           "timeout")
    except Exception as e:  # noqa: BLE001
        raise PublishError(f"не смог проверить вход: {e}", "error")
    data = r.json() if r.content else {}
    if not isinstance(data, dict):
        data = {}
    if r.status_code != 200 or not data.get("ok"):
        raise _fail_from_status(platform, r.status_code, str(data.get("error") or ""))
    return {"logged_in": bool(data.get("logged_in")), "platform": platform,
            "account_key": key, "detail": str(data.get("detail") or "")[:300]}


async def publish_clip(
    clip_filename: str,
    caption: str,
    *,
    platform: str = "instagram",
    account_key: str = "",
    title: str = "",
    video_url: str = "",
) -> dict:
    """Отправить готовый клип в соцсеть и дождаться подтверждения.

    Возвращает {"ok": True, "external_url": …} ТОЛЬКО когда агент подтвердил
    публикацию. Во всех остальных случаях — PublishError. Промежуточного
    «наверное получилось» здесь нет: врать о выложенном посте хуже, чем упасть.

    Повторных попыток нет намеренно: /publish не идемпотентен, второй вызов
    даёт второй пост в ленте.
    """
    platform = (platform or "instagram").strip().lower()
    if platform not in PLATFORMS:
        raise PublishError(f"неизвестная площадка: {platform}", "config")

    fname = os.path.basename(clip_filename or "")
    if not fname:
        raise PublishError("клип ещё не собран — публиковать нечего", "config")

    caption = (caption or "").strip()
    if not caption:
        raise PublishError("пустая подпись — агент такой пост не примет", "config")
    caption = caption[:CAPTION_LIMIT]

    key = account_key or (IG_ACCOUNT_KEY if platform == "instagram" else "")
    url = video_url or public_clip_url(fname)
    if not url.startswith(("http://", "https://")):
        raise PublishError("ссылка на клип должна быть абсолютной, иначе агент скачает чужой файл",
                           "config")

    payload = {
        "platform": platform,
        "video_url": url,
        "title": (title or "qlolvideo")[:100],
        "caption": caption,
    }
    if key:
        payload["account_key"] = key

    # Метку «публикуется» ставим последним действием перед запросом: всё, что
    # могло упасть на проверках, уже упало и не оставит клип залипшим в замке.
    guard = f"{platform}:{fname}"
    with _inflight_lock:
        if guard in _inflight:
            raise PublishError("этот клип уже публикуется — дождись результата, "
                               "иначе в ленте окажется два одинаковых поста.", "busy")
        _inflight.add(guard)

    log.info("публикую клип %s в %s (профиль %s)", fname, platform, key or "по умолчанию")
    try:
        # connect короткий (служба либо есть, либо нет), read — во всю длину
        # работы браузера: ответ приходит только по завершении публикации.
        timeout = httpx.Timeout(PUBLISH_TIMEOUT_S + READ_GRACE_S, connect=20.0)
        async with httpx.AsyncClient(timeout=timeout) as client:
            r = await client.post(f"{PUBLISHER_URL}/publish", json=payload)
    except (httpx.ConnectError, httpx.ConnectTimeout):
        raise PublishError(
            "служба публикации не запущена — подними её на сервере и повтори "
            "(systemctl start social-browser-publisher).", "offline")
    except httpx.ReadTimeout:
        # Соединение живо, ответа нет — классическое «воркер завис молча».
        # Пост при этом мог уже уйти, поэтому автоповтора здесь нет и не будет.
        raise PublishError(
            f"служба публикации не ответила за {_human_timeout(PUBLISH_TIMEOUT_S)}. "
            "Пост мог уйти — сначала открой Instagram и проверь ленту, "
            "и только потом публикуй заново.", "timeout")
    except httpx.TimeoutException:
        raise PublishError("связь со службой публикации оборвалась по таймауту. "
                           "Проверь ленту в Instagram, прежде чем повторять.", "timeout")
    except Exception as e:  # noqa: BLE001
        raise PublishError(f"не смог достучаться до службы публикации: {e}", "offline")
    finally:
        with _inflight_lock:
            _inflight.discard(guard)

    try:
        data = r.json() if r.content else {}
    except ValueError:
        data = {}
    if not isinstance(data, dict):
        data = {}

    if r.status_code != 200 or not data.get("ok"):
        detail = str(data.get("error") or (r.text or "")[:300])
        err = _fail_from_status(platform, r.status_code, detail)
        log.warning("публикация клипа %s не удалась [%s]: %s", fname, err.code, detail)
        raise err

    external = str(data.get("external_url") or "")
    log.info("клип %s опубликован: %s", fname, external or "(ссылка не определилась)")
    return {
        "ok": True,
        "platform": platform,
        # Агент иногда не может достать permalink и отдаёт заглушку
        # вида «instagram:browser:<время>» — публикация при этом состоялась.
        "external_url": external if external.startswith("http") else "",
        "raw_external": external,
        "account_key": str(data.get("account_key") or key),
        "video_url": url,
    }


def publish_clip_sync(clip_filename: str, caption: str, **kwargs) -> dict:
    """Синхронная обёртка для фоновых потоков (Thread(target=…) в main.py).

    Своего event loop у такого потока нет, поэтому поднимаем разовый —
    как и остальной медиа-слой проекта."""
    return asyncio.run(publish_clip(clip_filename, caption, **kwargs))


def health_sync(retries: int = 2) -> dict:
    """То же для /health из синхронного кода."""
    return asyncio.run(publisher_health(retries=retries))
