"""«Школа lolq» — обучающие материалы, привязанные к тарифу.

ИСТОЧНИК ПРАВДЫ — МАРКДАУН, А НЕ БАЗА. Уроки лежат в docs/learn/{en,ru}/*.md
и попадают в образ файлами (см. backend/Dockerfile). Причины ровно те же, что
и у блога: правка урока = коммит и деплой, версионирование и откат бесплатны,
а страница не зависит от живости API. База понадобилась бы только под
прогресс — он и лежит в базе, а тексты нет.

ОДИН ИСТОЧНИК, ДВА ВЫХОДА:
  * публичный /learn/<slug> — статические страницы, их собирает
    tools/build_content.py тем же проходом, что и блог (SEO, индексация);
  * /api/learn — тот же текст внутри студии, но с замками по тарифу
    и с отметками пройденного.

ДОСТУП. Уровни 0–2 открыты полностью и без регистрации: это и обучение, и
двигатель органики. Платные уровни отличаются не «секретным текстом»,
которого у нас нет, а применимостью — урок читается всеми, а кнопка
«применить» упирается в движок, который и так открыт только на тарифе.
Поле `access` во фронтматтере (free|pro|pro_max|studio) — механизм на будущее:
сегодня все восемь уроков открыты, и врать об этом интерфейс не должен.
"""
from __future__ import annotations

import logging
import os
import re

# Каталог с уроками. В образе — /app/learn (COPY docs/learn/), в рабочей копии
# — docs/learn рядом с репозиторием.
LEARN_DIR = os.environ.get(
    "LEARN_DIR",
    os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                 "docs", "learn"),
)
LANGS = ("en", "ru")

#: Порядок тарифов по возрастанию — им же меряется доступ к уроку.
PLAN_ORDER = ("free", "pro", "pro_max", "studio")

#: Заголовки уровней. В маркдауне лежит только номер уровня — человеческие
#: названия нужны обеим витринам, и держать их в девяти файлах нельзя.
LEVELS = {
    0: {"en": "First clip", "ru": "Первый клип"},
    1: {"en": "Craft", "ru": "Ремесло"},
    2: {"en": "Engines and finishing", "ru": "Движки и финал"},
    3: {"en": "Production mode", "ru": "Продакшн-режим"},
    4: {"en": "Teardowns", "ru": "Разборы"},
}

_CACHE: dict[str, list[dict]] = {}

log = logging.getLogger("rapclips")


def _parse_front_matter(raw: str) -> tuple[dict, str]:
    """--- ключ: значение --- в начале файла. Тот же формат, что у блога."""
    if not raw.startswith("---"):
        return {}, raw
    end = raw.find("\n---", 3)
    if end < 0:
        return {}, raw
    head, body = raw[3:end], raw[end + 4:]
    meta: dict = {}
    for line in head.splitlines():
        line = line.strip()
        if not line or line.startswith("#") or ":" not in line:
            continue
        key, _, val = line.partition(":")
        val = val.strip()
        if val.startswith("[") and val.endswith("]"):
            meta[key.strip()] = [v.strip().strip("\"'")
                                 for v in val[1:-1].split(",") if v.strip()]
        else:
            meta[key.strip()] = val.strip("\"'")
    return meta, body.lstrip("\n")


def _load(lang: str) -> list[dict]:
    """Уроки языка, в порядке имён файлов (00-, 01-, …). index.md — не урок,
    это витрина раздела, и в списке ему делать нечего."""
    lang = lang if lang in LANGS else "en"
    if lang in _CACHE:
        return _CACHE[lang]
    out: list[dict] = []
    folder = os.path.join(LEARN_DIR, lang)
    if os.path.isdir(folder):
        for name in sorted(os.listdir(folder)):
            if not name.endswith(".md") or name == "index.md":
                continue
            try:
                raw = open(os.path.join(folder, name), encoding="utf-8").read()
            except OSError:
                continue
            meta, body = _parse_front_matter(raw)
            slug = str(meta.get("slug") or name[:-3])
            try:
                level = int(meta.get("level") or 0)
            except (TypeError, ValueError):
                level = 0
            try:
                minutes = int(meta.get("minutes") or 5)
            except (TypeError, ValueError):
                minutes = 5
            access = str(meta.get("access") or "free")
            out.append({
                "slug": slug,
                "key": str(meta.get("translationKey") or slug),
                "lang": lang,
                "file": name,
                "title": str(meta.get("title") or slug),
                "description": str(meta.get("description") or ""),
                "level": level,
                "level_title": LEVELS.get(level, {}).get(lang, ""),
                "access": access if access in PLAN_ORDER else "free",
                "minutes": minutes,
                "cover": str(meta.get("cover") or ""),
                "tags": list(meta.get("tags") or []),
                "body": body,
            })
    if not out:
        # Пустой раздел — это почти всегда не «уроков нет», а не доехавшие в
        # образ файлы (см. COPY docs/learn в backend/Dockerfile и исключение
        # в .dockerignore). Такое обязано быть видно в логе, а не молча
        # превращаться в пустую витрину.
        log.warning("школа: в %s нет уроков для языка %r — раздел будет пустым",
                    folder, lang)
    _CACHE[lang] = out
    return out


def reload_cache() -> None:
    """Сбросить кэш — нужен тестам и локальной правке уроков без рестарта."""
    _CACHE.clear()


def allowed(access: str, plan_id: str, *, is_admin: bool = False) -> bool:
    """Открыт ли урок этому тарифу. Неизвестный тариф считаем бесплатным —
    ошибка в данных не должна запирать открытый материал."""
    if is_admin or access == "free":
        return True
    try:
        return PLAN_ORDER.index(plan_id if plan_id in PLAN_ORDER else "free") \
            >= PLAN_ORDER.index(access)
    except ValueError:
        return True


def card(lesson: dict, *, plan_id: str = "free", is_admin: bool = False,
         done: bool = False) -> dict:
    """Карточка урока для списка — БЕЗ текста. Список грузится на каждом
    открытии раздела, и таскать в нём девять полных уроков незачем."""
    return {
        "slug": lesson["slug"],
        "key": lesson["key"],
        "title": lesson["title"],
        "description": lesson["description"],
        "level": lesson["level"],
        "level_title": lesson["level_title"],
        "minutes": lesson["minutes"],
        "cover": lesson["cover"],
        "tags": lesson["tags"],
        "access": lesson["access"],
        "locked": not allowed(lesson["access"], plan_id, is_admin=is_admin),
        "done": bool(done),
        "url": f"/learn/{lesson['slug']}/" if lesson["lang"] == "en"
               else f"/ru/learn/{lesson['slug']}/",
    }


def index(lang: str = "en", *, plan_id: str = "free", is_admin: bool = False,
          done: set | None = None) -> list[dict]:
    done = done or set()
    return [card(x, plan_id=plan_id, is_admin=is_admin, done=x["slug"] in done)
            for x in _load(lang)]


def levels(lang: str = "en") -> list[dict]:
    """Уровни, реально представленные уроками этого языка."""
    lang = lang if lang in LANGS else "en"
    seen = []
    for x in _load(lang):
        if x["level"] not in [s["level"] for s in seen]:
            seen.append({"level": x["level"],
                         "title": LEVELS.get(x["level"], {}).get(lang, "")})
    return sorted(seen, key=lambda s: s["level"])


def lesson(slug: str, lang: str = "en") -> dict | None:
    for x in _load(lang):
        if x["slug"] == slug:
            return x
    # Ссылка пришла на другом языке — отдаём перевод по translationKey, а не
    # 404: адрес урока один, а языков два.
    for other in LANGS:
        for x in _load(other):
            if x["slug"] == slug:
                for y in _load(lang):
                    if y["key"] == x["key"]:
                        return y
                return x
    return None


def teaser(body: str, limit: int = 900) -> str:
    """Начало закрытого урока: столько текста, чтобы человек понял, о чём он,
    и ни строкой больше. Режем по границе абзаца, а не по символу."""
    plain = re.sub(r"^#.*$", "", body, flags=re.M).strip()
    if len(plain) <= limit:
        return plain
    cut = plain[:limit]
    stop = cut.rfind("\n\n")
    return (cut[:stop] if stop > 200 else cut).rstrip() + "…"
