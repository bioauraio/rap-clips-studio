"""Каталог промтов lolq.ai — витрина раздела «Промты».

ДВА СЛОЯ, РАЗНЫЕ ОБЪЕКТЫ. Это главное правило файла.

    STYLES / CLIP_PRESETS   — ПУБЛИЧНОЕ. Уходит в /api/styles, в лендинг,
                              в SEO-страницы, в мини-апп. Здесь НЕТ ни одного
                              символа промпта.
    _PROMPTS / _SEEDS /     — ЗАКРЫТОЕ. Живёт только на сервере, читается
    _ANCHORS / _STRUCTURE     функциями внизу файла и уходит в модель, а не
                              в браузер.

Разделение сделано ОБЪЕКТАМИ, а не флагами внутри одного словаря: если
публичное и закрытое лежат в одном дикте, любая невнимательная сериализация
(`jsonable_encoder(style)`, `dict(**row)`, новый эндпоинт, дебажный лог)
однажды выкинет промпт наружу. Здесь для утечки нужно явно позвать функцию
с именем `style_prompt()` — случайно так не напишешь.

ПОЧЕМУ ЭТО ВООБЩЕ АКТИВ. Шесть пресетов сняты покадровым разбором виральных
аккаунтов (docs/styles-research-full.md: 640+ роликов, 5 аккаунтов, метрики
просмотров). Это единственное, что нельзя повторить, прочитав нашу витрину.
Отдать текст — значит отдать ров: конкурент вставит его к себе за минуту.

ГДЕ СЕЙЧАС ДЫРА (на 2026-08-21, до внедрения этого файла):
  1. frontend/app.js:102 — STYLE_PRESETS с полными текстами уезжает каждому
     гостю вместе с бандлом.
  2. backend/main.py:1239 и :1352 — /api/tracks отдаёт наружу поле `style`,
     а это ТОТ ЖЕ полный текст, уже записанный в трек. Даже после переноса
     реестра на сервер промпт продолжит утекать, пока это поле в ответе.
Закрывать надо обе, иначе смысла нет.

СВЯЗЬ С КОНВЕЙЕРОМ (backend/claude.py):
  style   → SCENES_SYSTEM.replace("{STYLE}", ...) — каждый image_prompt обязан
            начинаться с указания стиля;
  каркас  → Project.story (сквозной сюжет) + Track.director_note, а для
            punch-каркасов Track.no_story=True, и тогда claude.py уходит в
            ветку «РЕЖИМ БЕЗ СЮЖЕТА»: независимые панч-кадры вместо истории.
            Каркас — это не второй стиль, это ответ на вопрос «что снимаем».
"""
from __future__ import annotations

# ─────────────────────────────────────────────────────────────────────────────
# ГРУППЫ. Ось одна — ВИЗУАЛЬНАЯ СЕМЬЯ (как выглядит кадр).
#
# «Вирусные» намеренно НЕ группа: виральность — это происхождение пресета, а не
# его вид. Стиль не может лежать в двух группах сразу, а ДРИМКЛАД одновременно
# и плёнка, и вирусный. Поэтому происхождение живёт тегом `viral` и подборкой
# COLLECTIONS, а группа отвечает ровно на «как это выглядит».
# ─────────────────────────────────────────────────────────────────────────────
GROUPS = [
    {
        "key": "animation",
        "label": {"en": "Animation", "ru": "Анимация"},
        "hint": {
            "en": "Drawn and 3D worlds. Safe, warm, nothing that can look grim.",
            "ru": "Рисованные и 3D-миры. Безопасно, тепло, ничего мрачного.",
        },
    },
    {
        "key": "cinema",
        "label": {"en": "Cinema", "ru": "Кино"},
        "hint": {
            "en": "A staged frame: real light, real lenses, poster compositions.",
            "ru": "Постановочный кадр: живой свет, настоящая оптика, постерные композиции.",
        },
    },
    {
        "key": "analog",
        "label": {"en": "Film", "ru": "Плёнка"},
        "hint": {
            "en": "AI wearing analog make-up: grain, VHS, flash. The frame reads as real footage.",
            "ru": "Нейросеть под аналоговым гримом: зерно, VHS, вспышка. Кадр читается как реальная съёмка.",
        },
    },
    {
        "key": "craft",
        "label": {"en": "Handmade", "ru": "Ручная работа"},
        "hint": {
            "en": "Plasticine and thread. Physical texture no clean render can buy.",
            "ru": "Пластилин и нить. Физическая фактура, которой не купить чистым рендером.",
        },
    },
    {
        "key": "surreal",
        "label": {"en": "Found footage", "ru": "Найденное видео"},
        "hint": {
            "en": "Something impossible in an ordinary place, filmed by accident. Nobody reacts.",
            "ru": "Что-то невозможное в обычном месте, снятое случайно. Никто не реагирует.",
        },
    },
]

# ─────────────────────────────────────────────────────────────────────────────
# ТЕГИ — контролируемый словарь. Свободные теги запрещены: через полгода
# «ночь», «ночной» и «night» станут тремя разными фильтрами.
# ─────────────────────────────────────────────────────────────────────────────
TAGS = {
    "mood": {
        "cozy":       {"en": "cosy",        "ru": "уютное"},
        "nostalgic":  {"en": "nostalgic",   "ru": "ностальгия"},
        "menacing":   {"en": "menacing",    "ru": "тихая угроза"},
        "deadpan":    {"en": "deadpan",     "ru": "дедпан"},
        "epic":       {"en": "epic",        "ru": "эпично"},
        "playful":    {"en": "playful",     "ru": "весёлое"},
        "melancholy": {"en": "melancholy",  "ru": "меланхолия"},
    },
    "palette": {
        "warm":       {"en": "warm",        "ru": "тёплая"},
        "cold":       {"en": "cold",        "ru": "холодная"},
        "muted":      {"en": "muted",       "ru": "приглушённая"},
        "saturated":  {"en": "saturated",   "ru": "насыщенная"},
        "monochrome": {"en": "monochrome",  "ru": "монохром"},
        "neon":       {"en": "neon",        "ru": "неон"},
    },
    "texture": {
        "grain":      {"en": "film grain",  "ru": "зерно"},
        "vhs":        {"en": "VHS",         "ru": "VHS"},
        "flash":      {"en": "direct flash", "ru": "вспышка в лоб"},
        "handmade":   {"en": "handmade",    "ru": "ручная фактура"},
        "clean":      {"en": "clean",       "ru": "чистая картинка"},
        "painterly":  {"en": "painterly",   "ru": "живопись"},
    },
    "content": {
        "street":     {"en": "street",      "ru": "улица"},
        "luxury":     {"en": "luxury",      "ru": "люкс"},
        "money":      {"en": "money",       "ru": "деньги"},
        "sacred":     {"en": "sacred",      "ru": "сакральное"},
        "nature":     {"en": "nature",      "ru": "природа"},
        "night":      {"en": "night",       "ru": "ночь"},
        "daylight":   {"en": "daylight",    "ru": "день"},
        "crowd":      {"en": "crowd",       "ru": "толпа"},
    },
    "origin": {
        # Единственный тег, который влияет на деньги: им помечены пресеты,
        # снятые разбором виральных аккаунтов. Их текст закрыт навсегда.
        "viral":      {"en": "viral format", "ru": "вирусный формат"},
        "signature":  {"en": "lolq signature", "ru": "фирменный lolq"},
    },
}

# ─────────────────────────────────────────────────────────────────────────────
# ПОДБОРКИ — вторая, необязательная навигация поверх групп. Человек приходит
# не за «плёнкой», а за «чтобы залетело» или «под мою грустную песню».
# ─────────────────────────────────────────────────────────────────────────────
COLLECTIONS = [
    {
        "key": "viral",
        "label": {"en": "Viral formats", "ru": "Вирусные форматы"},
        "desc": {
            "en": "Seven packagings reverse-engineered frame by frame from accounts with 640+ videos and tens of millions of views.",
            "ru": "Семь упаковок, снятых покадрово с аккаунтов на 640+ роликов и десятки миллионов просмотров.",
        },
        "styles": ["dreamclad", "katsumi", "spike", "munir", "punkrf", "longheads", "fanuel"],
        "featured": True,
    },
    {
        "key": "warm",
        "label": {"en": "For a soft track", "ru": "Под лиричное"},
        "desc": {
            "en": "Slow tempo, no aggression, faces you want to protect.",
            "ru": "Медленный темп, без агрессии, лица, которые хочется беречь.",
        },
        "styles": ["ghibli", "shinkai", "embroidery", "cinema", "fanuel"],
        "featured": False,
    },
    {
        "key": "hard",
        "label": {"en": "For a hard beat", "ru": "Под жёсткий бит"},
        "desc": {
            "en": "Fast cuts, grain, night, one accent colour.",
            "ru": "Быстрый монтаж, зерно, ночь, один акцентный цвет.",
        },
        "styles": ["punkrf", "dreamclad", "noir", "munir", "katsumi"],
        "featured": False,
    },
    {
        "key": "free",
        "label": {"en": "Free plan", "ru": "Доступно на бесплатном"},
        "desc": {
            "en": "Everything you can shoot on the free plan, including one signature preset.",
            "ru": "Всё, чем можно снимать на бесплатном тарифе, включая один фирменный пресет.",
        },
        "styles": ["ghibli", "pixar", "shinkai", "cinema", "flat2d", "noir", "clay", "embroidery", "dreamclad"],
        "featured": False,
    },
    {
        "key": "cheap",
        "label": {"en": "Cheap to animate", "ru": "Дёшево оживлять"},
        "desc": {
            "en": "Grain and shake hide compression — these look right even on the cheapest engine.",
            "ru": "Зерно и тряска прячут сжатие — эти стили выглядят правильно даже на самом дешёвом движке.",
        },
        "styles": ["punkrf", "katsumi", "longheads", "munir", "noir"],
        "featured": False,
    },
]
