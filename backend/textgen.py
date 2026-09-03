"""Текстовые модели lolq.ai: один реестр на весь сценарный конвейер.

ЗАЧЕМ ОТДЕЛЬНЫЙ МОДУЛЬ. До него все четырнадцать текстовых генераций
(сюжет, библия сезона, битшит, сценарий серии, раскадровка, персона
блогера, брендбук, предметная съёмка) уходили в ОДНУ функцию
claude._ask() → host-шлюз 172.18.0.1:8765. Шлюз честно пробует Claude по
подписке владельца, а когда бинарника на сервере нет — молча падает на
ChatGPT. Человек при этом уверен, что писал Claude.

Поэтому здесь два правила, и они важнее кода:

1. ЯРЛЫК НЕ ВРЁТ. Позиция «Claude Sonnet» показывается ТОЛЬКО когда
   ANTHROPIC_API_KEY задан и канал реально жив. Нет ключа — позиции нет
   ни в списке, ни в цене (админ видит её отдельно, с пометкой «ключа
   нет» — это его сигнал, а не витрина).
2. ФОЛБЭКА НЕТ. Упавший платный канал возвращает ошибку и токены, а не
   подменяется шлюзом. Молчаливая подмена — это ровно та болезнь, из-за
   которой владелец и завёл этот выбор.

ЦЕНА. Реестр хранит себестоимость ОДНОГО вызова в долларах, а цену в
токенах считает main.py той же функцией _points_of_usd(), что и картинки
с видео: второй кассы в сервисе быть не должно. Исключение ровно одно —
шлюз стоит НОЛЬ токенов, а не GATEWAY_POINTS: текстовые шаги специально
бесплатны, иначе бесплатный тариф не доживает до первого клипа
(см. COST_STORY/COST_SCENES в main.py).

КАНАЛ ДО ANTHROPIC. С наших серверов api.anthropic.com отвечает 403, а
через мост внешнего выхода (SOCKS 172.18.0.1:1081) — 401, то есть до API
мы доезжаем и всё упирается в ключ. Отсюда EGRESS_PROXY: официальный SDK
принимает свой httpx-клиент, ему и передаём прокси.
"""
from __future__ import annotations

import logging
import os
import time

log = logging.getLogger("rapclips.textgen")


class TextGenError(RuntimeError):
    pass


# ─────────────────────────── ключи и каналы ───────────────────────────
# Ключи живут ТОЛЬКО в infra/.env. Ни один из них не отдаётся наружу
# никаким роутом: админка показывает лишь факт «задан / не задан».
ANTHROPIC_API_KEY = os.environ.get("ANTHROPIC_API_KEY", "")
ANTHROPIC_BASE_URL = os.environ.get("ANTHROPIC_BASE_URL", "")
OPENROUTER_API_KEY = os.environ.get("OPENROUTER_API_KEY", "")
OPENROUTER_URL = os.environ.get(
    "OPENROUTER_URL", "https://openrouter.ai/api/v1/chat/completions")
# Мост внешнего выхода организма. Пусто — идём напрямую (локальная разработка).
EGRESS_PROXY = os.environ.get("EGRESS_PROXY", "socks5://172.18.0.1:1081")
GATEWAY_URL = os.environ.get("AGENT_GATEWAY_URL", "http://172.18.0.1:8765") + "/complete"

TEXT_TIMEOUT = float(os.environ.get("TEXT_TIMEOUT_S", "600"))
# Раскадровка длинного трека — это три десятка сцен по три английских
# промпта в каждой. 12–16 тысяч токенов ответа здесь норма, а не запас.
TEXT_MAX_TOKENS = int(os.environ.get("TEXT_MAX_TOKENS", "32000"))

# Порядок тарифов. Модель открыта с тарифа min_plan и выше.
PLAN_ORDER = ("free", "pro", "pro_max", "studio")

# ─────────────────────────── реестр моделей ───────────────────────────
# usd — СРЕДНЯЯ себестоимость одного вызова сценарного шага (вход ≈5 тыс.
# токенов, выход ≈12 тыс.: именно столько весит раскадровка на 30 сцен).
# Это ПРАЙС-ЛИСТ, а не факт: точные числа калибруются по usage после
# первых прогонов, ровно как у движков картинок в mediagen.
TEXT_ENGINES: dict[str, dict] = {
    "gateway": {
        "title": "Авто (подписка сервиса)",
        "title_en": "Auto (service subscription)",
        "channel": "gateway",
        "model": "",
        "min_plan": "free",
        "usd": 0.0,
        "note": "Идёт через подписку сервиса. Бесплатно и всегда доступно.",
    },
    "claude-sonnet": {
        "title": "Claude Sonnet",
        "title_en": "Claude Sonnet",
        "channel": "anthropic",
        "model": os.environ.get("ANTHROPIC_SONNET_MODEL", "claude-sonnet-5"),
        "min_plan": "pro",
        "usd": float(os.environ.get("TEXT_USD_SONNET", "0.12")),
        "note": "Сильнее держит структуру и характеры. Открыт с тарифа PRO.",
    },
    "claude-opus": {
        "title": "Claude Opus",
        "title_en": "Claude Opus",
        "channel": "anthropic",
        "model": os.environ.get("ANTHROPIC_OPUS_MODEL", "claude-opus-5"),
        "min_plan": "pro_max",
        "usd": float(os.environ.get("TEXT_USD_OPUS", "0.40")),
        "note": "Самая сильная драматургия и связность сцен. С тарифа PRO MAX.",
    },
    # ЗАПАСНОЙ КАНАЛ, а не второй ассортимент. Он существует, чтобы при
    # недоступности прямого Anthropic переключиться переменной окружения,
    # а не переписыванием кода. Отдельных не-Claude моделей здесь нет
    # намеренно: у сервиса уже восемь движков картинок и видео, третий
    # реестр «на выбор» — это выбор ради выбора.
    "or-claude-sonnet": {
        "title": "Claude Sonnet (запасной канал)",
        "title_en": "Claude Sonnet (backup route)",
        "channel": "openrouter",
        "model": os.environ.get("OPENROUTER_SONNET_MODEL", "anthropic/claude-sonnet-4.5"),
        "min_plan": "pro",
        "usd": float(os.environ.get("TEXT_USD_OR_SONNET", "0.14")),
        "note": "Тот же Claude через агрегатор — на случай, если прямой канал закрыт.",
    },
}

DEFAULT_ENGINE = os.environ.get("TEXT_ENGINE_DEFAULT", "gateway")
TEXT_ENGINE_IDS = tuple(TEXT_ENGINES)


def text_engine_usd(engine: str) -> float:
    """Себестоимость одного вызова в долларах. main.py переводит её в
    токены тем же _points_of_usd(), что и всё остальное."""
    return float((TEXT_ENGINES.get(engine) or {}).get("usd", 0.0))


def is_gateway(engine: str) -> bool:
    return (TEXT_ENGINES.get(engine) or {}).get("channel") == "gateway"


def plan_rank(plan: str) -> int:
    try:
        return PLAN_ORDER.index(str(plan or "free"))
    except ValueError:
        # Незнакомый тариф — это платный тариф, заведённый позже этого
        # файла. Считать его ниже free было бы худшим из двух вариантов.
        return len(PLAN_ORDER) - 1


def plan_allows(engine: str, plan: str) -> bool:
    spec = TEXT_ENGINES.get(engine)
    if not spec:
        return False
    return plan_rank(plan) >= plan_rank(spec.get("min_plan", "free"))


def engine_live(engine: str) -> bool:
    """Можно ли позвать движок ПРЯМО СЕЙЧАС — по ключам, а не по обещанию."""
    spec = TEXT_ENGINES.get(engine)
    if not spec:
        return False
    ch = spec["channel"]
    if ch == "gateway":
        return True
    if ch == "anthropic":
        # Либо прямой API-ключ, либо живой Claude-шлюз подписки владельца
        # (ask() выбирает канал сам). Ни того ни другого — честное «нет».
        return anthropic_channel() != "none"
    if ch == "openrouter":
        return bool(OPENROUTER_API_KEY)
    return False


_gw_health: dict = {"ts": 0.0, "ok": False}
_GW_HEALTH_TTL = 60.0


def gateway_alive() -> bool:
    """Жив ли шлюз (GET на корень/health с коротким таймаутом), кэш 60 с."""
    now = time.time()
    if now - _gw_health["ts"] < _GW_HEALTH_TTL:
        return bool(_gw_health["ok"])
    ok = False
    base = GATEWAY_URL.rsplit("/", 1)[0]
    try:
        import httpx  # noqa: PLC0415
        with httpx.Client(timeout=2.0) as c:
            for url in (base + "/health", base + "/"):
                try:
                    r = c.get(url)
                except httpx.HTTPError:
                    continue
                if r.status_code < 500:
                    ok = True
                    break
    except Exception:  # noqa: BLE001
        ok = False
    _gw_health.update({"ts": now, "ok": ok})
    return ok


def anthropic_channel() -> str:
    """'key' — прямой ANTHROPIC_API_KEY (и SDK), 'gateway' — Claude-шлюз
    подписки владельца отвечает, 'none' — ни того ни другого."""
    if ANTHROPIC_API_KEY and _sdk_available():
        return "key"
    if gateway_alive():
        return "gateway"
    return "none"


def text_engines_live() -> list[str]:
    return [eid for eid in TEXT_ENGINES if engine_live(eid)]


def _sdk_available() -> bool:
    """Официальный SDK Anthropic установлен. Без него канал не живой —
    и мы говорим это вслух, а не падаем ImportError'ом в треде воркера."""
    try:
        import anthropic  # noqa: F401, PLC0415
    except Exception:  # noqa: BLE001
        return False
    return True


def resolve_text_engine(*, wanted: str = "", track: str = "", project: str = "",
                        plan: str = "free") -> str:
    """Цепочка «запрос → объект → проект → шлюз», та же, что у движков
    картинок (_resolve_image_engine в main.py).

    Закрытый тарифом или неживой движок разрешается в шлюз ЗДЕСЬ, до
    списания и до запуска. Это не молчаливая подмена на лету: интерфейс
    такую позицию человеку и не показывает, а работа обязана поехать."""
    for want in (wanted, track, project, DEFAULT_ENGINE):
        eid = str(want or "").strip()
        if not eid or eid not in TEXT_ENGINES:
            continue
        if not engine_live(eid):
            if eid != DEFAULT_ENGINE:
                log.info("текстовая модель %s не настроена — беру шлюз", eid)
            continue
        if not plan_allows(eid, plan):
            log.info("текстовая модель %s закрыта тарифом %s — беру шлюз", eid, plan)
            continue
        return eid
    return "gateway"


def public_engines(plan: str = "free", *, admin: bool = False,
                   current: str = "") -> list[dict]:
    """Список для интерфейса. Неживое обычному человеку НЕ показываем:
    продавать позицию, которой нет, нельзя. Админу показываем с пометкой —
    ему это сигнал «положи ключ», а не витрина."""
    out = []
    for eid, spec in TEXT_ENGINES.items():
        live = engine_live(eid)
        if not live and not admin:
            continue
        out.append({
            "id": eid,
            "title": spec["title"],
            "title_en": spec.get("title_en", spec["title"]),
            "channel": spec["channel"],
            # Честный канал для admin/models: key | gateway | none.
            "live_channel": (anthropic_channel() if spec["channel"] == "anthropic"
                             else ("gateway" if live else "none")),
            "min_plan": spec.get("min_plan", "free"),
            "locked": not plan_allows(eid, plan),
            "live": live,
            "current": eid == current,
            "usd": round(float(spec.get("usd", 0.0)), 4) if admin else None,
            "note": spec.get("note", ""),
        })
    return out


# ────────────────────────────── вызов ──────────────────────────────

async def _ask_gateway(prompt: str, system: str, model: str = "") -> str:
    import httpx  # noqa: PLC0415
    timeout = httpx.Timeout(TEXT_TIMEOUT, connect=15.0)
    payload = {
        "prompt": prompt,
        "system": system,
        "subscription_provider": "claude",
        "cwd": "rapclips",
        "timeout": int(TEXT_TIMEOUT) - 20,
    }
    # Конкретная модель Claude (sonnet/opus) — когда движок выбран человеком,
    # а не «авто»: шлюз передаёт её в CLI подписки владельца.
    if model:
        payload["model"] = model
    async with httpx.AsyncClient(timeout=timeout) as client:
        r = await client.post(GATEWAY_URL, json=payload)
    if r.status_code != 200:
        raise TextGenError(f"шлюз ответил {r.status_code}: {r.text[:300]}")
    text = (r.json() or {}).get("text", "")
    if not text:
        raise TextGenError("пустой ответ шлюза")
    return text


async def _ask_anthropic(prompt: str, system: str, spec: dict) -> str:
    """Прямой Anthropic через официальный SDK.

    Стрим, а не обычный вызов: раскадровка легко выбирает десятки тысяч
    токенов, и на таком max_tokens SDK сам требует стрим, иначе запрос
    упирается в HTTP-таймаут. get_final_message() отдаёт готовый ответ."""
    import anthropic  # noqa: PLC0415
    from anthropic import AsyncAnthropic  # noqa: PLC0415

    kwargs: dict = {"api_key": ANTHROPIC_API_KEY, "timeout": TEXT_TIMEOUT,
                    "max_retries": 2}
    if ANTHROPIC_BASE_URL:
        kwargs["base_url"] = ANTHROPIC_BASE_URL
    if EGRESS_PROXY:
        # Напрямую с наших адресов api.anthropic.com отдаёт 403; через мост
        # организма — 401, то есть доезжаем. Берём клиент SDK, а не голый
        # httpx.AsyncClient: он сохраняет таймауты и лимиты пула. getattr —
        # на случай сборки со старым SDK, где этого класса ещё нет.
        http_cls = getattr(anthropic, "DefaultAsyncHttpxClient", None)
        if http_cls is not None:
            kwargs["http_client"] = http_cls(proxy=EGRESS_PROXY)
        else:
            import httpx  # noqa: PLC0415
            kwargs["http_client"] = httpx.AsyncClient(proxy=EGRESS_PROXY)
    client = AsyncAnthropic(**kwargs)
    try:
        async with client.messages.stream(
            model=spec["model"],
            max_tokens=TEXT_MAX_TOKENS,
            system=system,
            # Раскадровка — это как раз тот случай, ради которого думание и
            # существует: связность сцен, монтажный ритм, единый герой.
            thinking={"type": "adaptive"},
            messages=[{"role": "user", "content": prompt}],
        ) as stream:
            msg = await stream.get_final_message()
    except anthropic.APIStatusError as e:
        raise TextGenError(f"Anthropic ответил {e.status_code}: {str(e)[:200]}") from e
    except anthropic.APIConnectionError as e:
        raise TextGenError(f"Anthropic недоступен (мост {EGRESS_PROXY or 'нет'}): "
                           f"{str(e)[:200]}") from e
    finally:
        await client.close()
    if getattr(msg, "stop_reason", "") == "refusal":
        raise TextGenError("Anthropic отказался отвечать на этот запрос")
    parts = [b.text for b in msg.content if getattr(b, "type", "") == "text"]
    text = "\n".join(p for p in parts if p)
    if not text.strip():
        raise TextGenError("пустой ответ Anthropic")
    return text


async def _ask_openrouter(prompt: str, system: str, spec: dict) -> str:
    import httpx  # noqa: PLC0415
    timeout = httpx.Timeout(TEXT_TIMEOUT, connect=15.0)
    async with httpx.AsyncClient(timeout=timeout, proxy=EGRESS_PROXY or None) as client:
        r = await client.post(OPENROUTER_URL, headers={
            "Authorization": f"Bearer {OPENROUTER_API_KEY}",
            "Content-Type": "application/json",
        }, json={
            "model": spec["model"],
            "max_tokens": TEXT_MAX_TOKENS,
            "messages": [{"role": "system", "content": system},
                         {"role": "user", "content": prompt}],
        })
    if r.status_code != 200:
        raise TextGenError(f"OpenRouter ответил {r.status_code}: {r.text[:300]}")
    data = r.json() or {}
    try:
        text = data["choices"][0]["message"]["content"]
    except (KeyError, IndexError, TypeError) as e:
        raise TextGenError(f"OpenRouter вернул непонятный ответ: {str(data)[:200]}") from e
    if not (text or "").strip():
        raise TextGenError("пустой ответ OpenRouter")
    return text


async def ask(prompt: str, system: str, engine: str = "") -> str:
    """ЕДИНСТВЕННАЯ дверь текстовой генерации сервиса.

    Пустой engine = шлюз: так работал весь конвейер до этого файла, и
    старое поведение обязано остаться поведением по умолчанию."""
    eid = str(engine or "").strip() or "gateway"
    spec = TEXT_ENGINES.get(eid)
    if not spec:
        raise TextGenError(f"неизвестная текстовая модель: {eid!r}")
    if not engine_live(eid):
        # Сюда попадаем только в обход resolve_text_engine — например,
        # если ключ убрали между постановкой задачи и её выполнением.
        raise TextGenError(f"{spec['title']} не настроен на сервере")
    ch = spec["channel"]
    if ch == "gateway":
        return await _ask_gateway(prompt, system)
    if ch == "anthropic":
        # Без API-ключа канал живёт через Claude-шлюз подписки владельца:
        # та же модель (sonnet/opus), но по подписке, а не по ключу.
        if not ANTHROPIC_API_KEY:
            return await _ask_gateway(prompt, system, model=spec.get("model", ""))
        return await _ask_anthropic(prompt, system, spec)
    if ch == "openrouter":
        return await _ask_openrouter(prompt, system, spec)
    raise TextGenError(f"неизвестный канал модели: {ch!r}")


def state() -> dict:
    """Правда о ключах для админки. ЗНАЧЕНИЙ ЗДЕСЬ НЕТ И НЕ БУДЕТ —
    только «задан / не задан»: редактор ключей в вебе это способ увести
    их одним XSS."""
    return {
        "anthropic_key": bool(ANTHROPIC_API_KEY),
        "anthropic_channel": anthropic_channel(),
        "gateway_alive": gateway_alive(),
        "anthropic_sdk": _sdk_available(),
        "openrouter_key": bool(OPENROUTER_API_KEY),
        "egress_proxy": EGRESS_PROXY or "",
        "gateway_url": GATEWAY_URL,
        "default": DEFAULT_ENGINE,
    }
