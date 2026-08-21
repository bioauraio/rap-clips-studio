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

ДОСТУП. Все уровни, 0–4, открыты полностью и без регистрации: это и обучение,
и двигатель органики. Платные уровни отличаются не «секретным текстом»,
которого у нас нет, а применимостью — урок читается всеми, а кнопка
«применить» упирается в тариф.
Поле `access` во фронтматтере (free|pro|pro_max|studio) — механизм на будущее:
сегодня все шестнадцать уроков открыты, и врать об этом интерфейс не должен.
Тариф сегодня решает не «покажем ли текст», а «применится ли артефакт»: замок
живёт на наборе приёмов (prompts_library.PACKS), а не на уроке.
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
                # АРТЕФАКТ УРОКА. Урок, после которого нечего нажать, — не урок,
                # а статья. Эти четыре поля и есть кнопка «Открыть в студии»:
                #   pack   — набор приёмов из prompts_library.PACKS;
                #   preset — сюжетный каркас из prompts_catalog.CLIP_PRESETS;
                #   styles — стили, отмеченные заранее;
                #   mode   — режим студии (clip|ugc|series|mockup), frontend/nav.js.
                # Валидируются они НЕ здесь: learn.py не имеет права зависеть от
                # каталога промтов ради разбора маркдауна. Ссылки проверяет
                # tools/check_learn_artifacts.py — один проход по обоим языкам.
                "pack": str(meta.get("pack") or ""),
                "preset": str(meta.get("preset") or ""),
                "styles": list(meta.get("styles") or []),
                "mode": str(meta.get("mode") or "clip"),
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
    открытии раздела, и таскать в нём семнадцать полных уроков незачем."""
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
        # Карточка списка обязана знать, есть ли у урока артефакт: витрина
        # рисует на ней метку «с набором», и без этого поля она бы грузила
        # все уроки целиком только чтобы посмотреть фронтматтер.
        "pack": lesson["pack"],
        "preset": lesson["preset"],
        "mode": lesson["mode"],
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


# ─────────────────────────── ПРОГРАММА АКАДЕМИИ ───────────────────────────
#
# КУРС = УРОВЕНЬ, а не третья сущность. Соблазн завести отдельный реестр
# курсов со своим списком уроков велик — и это была бы вторая правда о том,
# что куда входит: уровень урока лежит во фронтматтере, и любой новый урок
# попадал бы в уровень сам, а в курс — только руками, через правку реестра.
# Один забытый слаг = урок, который написан, лежит в образе, индексируется
# страницей, но в академии не показан вообще. Поэтому курс собирается ИЗ
# уровня, а реестр ниже добавляет уровню только то, чего в уроке нет:
# человеческое обещание («что ты будешь уметь») и порядок показа.
#
# Это же снимает вопрос «почему у нас курсы длиннее, чем у Higgsfield».
# У них курс — 5–19 модулей по 5 минут, потому что курс продаёт сертификат.
# У нас курс продаёт СЛЕДУЮЩИЙ КЛИП, и мерить его надо не модулями, а тем,
# что человек может сделать в конце. Отсюда outcome — обязательное поле:
# уровень без внятного «в конце ты…» не имеет права быть курсом.
COURSES = {
    0: {
        "lead": {
            "en": "The shortest route from a track to a finished file. Nothing here explains why — only what to press.",
            "ru": "Самый короткий путь от трека до готового файла. Здесь ничего не объясняется «почему» — только что нажимать.",
        },
        "outcome": {
            "en": ["An mp4 of your own, made today",
                   "A clear idea of what the tokens were spent on"],
            "ru": ["Свой mp4, собранный сегодня",
                   "Понимание, за что списались токены"],
        },
    },
    1: {
        "lead": {
            "en": "Why the clip came out exactly like that: styles, a character who survives to scene thirty, a storyboard, shots and light.",
            "ru": "Почему клип получился именно таким: стили, персонаж, доживающий до тридцатой сцены, раскадровка, приёмы и свет.",
        },
        "outcome": {
            "en": ["A second clip that is better on purpose, not by luck",
                   "A storyboard that reads as a story, not as thirty postcards"],
            "ru": ["Второй клип, который лучше намеренно, а не случайно",
                   "Раскадровка, которая читается историей, а не тридцатью открытками"],
        },
    },
    2: {
        "lead": {
            "en": "Where the money goes and how the thing gets assembled: engines, motion, cuts, the final file and what to do when a frame comes out wrong.",
            "ru": "Куда уходят деньги и как это собирается: движки, движение, монтаж, финальный файл и что делать, когда кадр вышел не тот.",
        },
        "outcome": {
            "en": ["The same clip length for half the tokens",
                   "A published file instead of a folder of frames"],
            "ru": ["Та же длина клипа за вдвое меньше токенов",
                   "Опубликованный файл вместо папки с кадрами"],
        },
    },
    3: {
        "lead": {
            "en": "When a clip stops being a one-off job: a recurring hero, an asset library, UGC and product shots.",
            "ru": "Когда клип перестаёт быть разовой работой: возвращающийся герой, библиотека ассетов, UGC и предметная съёмка.",
        },
        "outcome": {
            "en": ["An episode that looks like the previous one on purpose",
                   "A shooting kit you reuse instead of inventing each time"],
            "ru": ["Выпуск, похожий на предыдущий намеренно",
                   "Набор для съёмки, который переиспользуется, а не придумывается заново"],
        },
    },
    4: {
        "lead": {
            "en": "One clip that worked, taken apart frame by frame — what exactly holds the attention and what is decoration.",
            "ru": "Один залетевший клип, разобранный покадрово: что именно держит внимание, а что украшение.",
        },
        "outcome": {
            "en": ["The habit of reading someone else's clip as a set of decisions"],
            "ru": ["Привычка читать чужой клип как набор решений"],
        },
    },
}


def program(lang: str = "en", *, plan_id: str = "free", is_admin: bool = False,
            done: set | None = None) -> list[dict]:
    """Академия: уровни с уроками, обещанием и прогрессом.

    Прогресс считается ПО ОТМЕТКАМ, а не по открытым страницам: открыть урок
    и уйти можно за секунду, и такой процент не значил бы ничего.

    Пустой уровень В СПИСОК НЕ ПОПАДАЕТ. Уровни 3 и 4 были объявлены в LEVELS
    задолго до того, как в них появились уроки, и витрина всё это время
    обещала больше, чем есть, — курс без единого урока обязан отсутствовать,
    а не показываться пустым."""
    done = done or set()
    lang = lang if lang in LANGS else "en"
    out: list[dict] = []
    for lesson_ in _load(lang):
        lvl = lesson_["level"]
        row = next((r for r in out if r["level"] == lvl), None)
        if row is None:
            meta = COURSES.get(lvl, {})
            row = {
                "level": lvl,
                "title": LEVELS.get(lvl, {}).get(lang, ""),
                "lead": (meta.get("lead") or {}).get(lang, ""),
                "outcome": list((meta.get("outcome") or {}).get(lang, [])),
                "lessons": [],
                "minutes": 0,
                "done": 0,
                # Артефакты курса — то, что применяется в проект кнопкой.
                # Считаем здесь же: витрина обещает «с наборами», и цифра
                # обязана приезжать из тех же уроков, а не из вёрстки.
                "packs": [],
            }
            out.append(row)
        row["lessons"].append(
            card(lesson_, plan_id=plan_id, is_admin=is_admin,
                 done=lesson_["slug"] in done))
        row["minutes"] += int(lesson_["minutes"] or 0)
        if lesson_["slug"] in done:
            row["done"] += 1
        if lesson_["pack"] and lesson_["pack"] not in row["packs"]:
            row["packs"].append(lesson_["pack"])
    for row in out:
        total = len(row["lessons"])
        row["total"] = total
        row["percent"] = int(round(100 * row["done"] / total)) if total else 0
    return sorted(out, key=lambda r: r["level"])


def progress(lang: str = "en", done: set | None = None) -> dict:
    """Прогресс по академии целиком — одной строкой в шапке кабинета."""
    done = done or set()
    lessons = _load(lang if lang in LANGS else "en")
    total = len(lessons)
    seen = len([x for x in lessons if x["slug"] in done])
    return {"total": total, "done": seen,
            "percent": int(round(100 * seen / total)) if total else 0,
            "minutes": sum(int(x["minutes"] or 0) for x in lessons)}
