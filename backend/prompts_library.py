"""Библиотека приёмов lolq.ai — третий слой каталога, «как это снять».

ТРИ СЛОЯ, И ОНИ НЕ ЗАМЕНЯЮТ ДРУГ ДРУГА:

    STYLES        (prompts_catalog) — КАК ВЫГЛЯДИТ весь клип. Мир целиком.
    CLIP_PRESETS  (prompts_catalog) — ЧТО СНИМАЕМ. Каркас на весь трек.
    SHOTS         (этот файл)       — КАК СНЯТА ОДНА СЦЕНА. Кадр и движение.

Приём — самая мелкая единица каталога: он не задаёт мир и не задаёт сюжет,
он ложится на одну карточку кадра поверх любого стиля и любого каркаса.

ПОЧЕМУ ТЕКСТЫ ЗДЕСЬ ОТКРЫТЫ, А В prompts_catalog ЗАКРЫТЫ.
Это не непоследовательность, это граница актива. В prompts_catalog закрыт ров:
семь пресетов сняты покадровым разбором виральных аккаунтов, их нельзя
повторить, прочитав витрину. Здесь — ремесло: наезд, контровой свет, склейка
на движении. Оно есть в любом учебнике операторского дела, конкурентного
преимущества в нём ноль, а органики, SEO и доверия — много. Прятать ремесло
значит платить репутацией за то, что и так знают все.
Машинная гарантия границы — validate() внизу файла: он ищет тексты закрытых
промптов внутри открытых карточек и роняет сборку на совпадении.

ПОЧЕМУ ПРОМПТЫ СВОИ, А НЕ СКАЧАННЫЕ У КОНКУРЕНТОВ.
Две причины, и вторая важнее первой.
  1. Чужой каталог остаётся чужим произведением, даже когда рядом с ним
     нарисована кнопка «скопировать». Наш собственный аргумент против кражи
     пятнадцати закрытых промптов перестанет работать в день, когда мы украдём.
  2. Чужие банки промптов описывают движение камеры ВНУТРИ одного ролика:
     «drone orbit, радиус 8 метров, 84 градуса». У нас сцена задаётся ПАРОЙ
     КАДРОВ — первым и последним, — и траектория в неё не ложится вообще.
     Скопированный банк был бы у нас нерабочим украшением.
Отсюда единица этого файла: приём — это РАЗНИЦА МЕЖДУ ДВУМЯ КАДРАМИ, а не
траектория во времени. Такого каталога нет ни у кого, потому что ни у кого
нет нашей пары кадров.

СТИЛЯ В ПРИЁМЕ НЕТ И БЫТЬ НЕ МОЖЕТ. claude.py требует, чтобы стиль в
image_prompt не дублировался: его подставляет конвейер (SCENES_SYSTEM,
{STYLE}). Поэтому в текстах ниже нет ни одного слова про плёнку, аниме или
грейд — только композиция, свет, действие. Тот же приём обязан одинаково
работать и на Гибли, и на VHS.

КУДА ЭТО ПРИМЕНЯЕТСЯ. scene_patch() отдаёт ровно те поля, которые принимает
PATCH /api/scenes/{id} (main.py:3877): image_prompt, image_prompt_last,
motion_prompt, shot_size, camera_move. Никакого нового write-эндпоинта под
кнопку «применить» не нужно — она собирается из уже существующего.

ПЛЕЙСХОЛДЕРЫ. В тексте промпта они английские и фигурные: {character},
{location}. Русскими их сделать нельзя — промпт уходит в модель на английском,
и подстановка «{персонаж}» оставила бы в нём кириллицу. Человеческие названия
слотов лежат в SLOTS на двух языках, и витрина показывает их, а не ключи.
"""
from __future__ import annotations

import re

# ─────────────────────────────────────────────────────────────────────────────
# КАТЕГОРИИ. Ось одна — ЧТО ИМЕННО НАСТРАИВАЕТ ПРИЁМ. Не «жанр», не
# «настроение»: и то и другое уже есть тегами у стиля, а дублирующая ось
# превращает фильтр в лотерею.
# ─────────────────────────────────────────────────────────────────────────────
CATEGORIES = [
    {
        "key": "shot",
        "label": {"en": "Framing and camera", "ru": "Кадр и камера"},
        "hint": {
            "en": "Shot size and what changes between the first and the last frame.",
            "ru": "Крупность плана и то, что меняется между первым и последним кадром.",
        },
    },
    {
        "key": "character",
        "label": {"en": "Characters and portraits", "ru": "Персонажи и портреты"},
        "hint": {
            "en": "How a person enters the frame, holds it and stays recognisable.",
            "ru": "Как человек входит в кадр, держит его и остаётся узнаваемым.",
        },
    },
    {
        "key": "location",
        "label": {"en": "Locations and environment", "ru": "Локации и среда"},
        "hint": {
            "en": "The place as a character: scale, depth, weather, emptiness.",
            "ru": "Место как персонаж: масштаб, глубина, погода, пустота.",
        },
    },
    {
        "key": "product",
        "label": {"en": "Product shots", "ru": "Предметная съёмка"},
        "hint": {
            "en": "An object filmed so it can be sold: hero shot, hand, texture, series.",
            "ru": "Предмет, снятый так, чтобы его можно было продать: герой-кадр, рука, фактура, серия.",
        },
    },
    {
        "key": "motion",
        "label": {"en": "Motion and action", "ru": "Движение и экшен"},
        "hint": {
            "en": "Scenes whose whole point is that something moves — and moves readably.",
            "ru": "Сцены, весь смысл которых в том, что что-то движется — и движется читаемо.",
        },
    },
    {
        "key": "light",
        "label": {"en": "Light and time of day", "ru": "Свет и время суток"},
        "hint": {
            "en": "One decision that changes a frame more than any other: where the light comes from.",
            "ru": "Одно решение, которое меняет кадр сильнее прочих: откуда идёт свет.",
        },
    },
    {
        "key": "cut",
        "label": {"en": "Editing devices", "ru": "Приёмы монтажа"},
        "hint": {
            "en": "Pairs of scenes. Half of what a clip feels like happens at the cut, not inside the shot.",
            "ru": "Пары сцен. Половина ощущения от клипа живёт на склейке, а не внутри кадра.",
        },
    },
]

# ─────────────────────────────────────────────────────────────────────────────
# СЛОТЫ — контролируемый словарь подстановок, как TAGS у стилей. Свободные
# плейсхолдеры запрещены той же логикой: через полгода {hero}, {character} и
# {person} станут тремя разными полями в одной форме.
# `example` — не украшение: подставляется, когда человек нажал «применить», не
# заполнив слот. Пустой {character} в промпте хуже любого умолчания.
# ─────────────────────────────────────────────────────────────────────────────
SLOTS = {
    "character": {
        "label": {"en": "character", "ru": "персонаж"},
        "hint": {"en": "Who is in the frame. Use the character name from the project.",
                 "ru": "Кто в кадре. Подставляй имя персонажа из проекта."},
        "example": {"en": "young man in a black hooded jacket",
                    "ru": "парень в чёрной куртке с капюшоном"},
    },
    "location": {
        "label": {"en": "location", "ru": "локация"},
        "hint": {"en": "Where it happens. One place, named concretely.",
                 "ru": "Где происходит. Одно место, названное конкретно."},
        "example": {"en": "narrow residential courtyard",
                    "ru": "узкий двор жилого дома"},
    },
    "outfit": {
        "label": {"en": "outfit", "ru": "одежда"},
        "hint": {"en": "Clothes the model must keep identical across scenes.",
                 "ru": "Одежда, которую модель обязана сохранять от сцены к сцене."},
        "example": {"en": "oversized grey coat over a white tee",
                    "ru": "оверсайз серое пальто поверх белой футболки"},
    },
    "prop": {
        "label": {"en": "prop", "ru": "реквизит"},
        "hint": {"en": "The one object in the character's hands.",
                 "ru": "Единственный предмет в руках персонажа."},
        "example": {"en": "cracked phone", "ru": "телефон с разбитым экраном"},
    },
    "object": {
        "label": {"en": "product", "ru": "товар"},
        "hint": {"en": "The item being sold. Name the material and the shape.",
                 "ru": "Предмет, который продаём. Назови материал и форму."},
        "example": {"en": "matte black glass bottle",
                    "ru": "матовый чёрный стеклянный флакон"},
    },
    "surface": {
        "label": {"en": "surface", "ru": "поверхность"},
        "hint": {"en": "What the object stands on. Decides half the light.",
                 "ru": "На чём стоит предмет. Решает половину света."},
        "example": {"en": "wet dark stone", "ru": "мокрый тёмный камень"},
    },
    "accent": {
        "label": {"en": "accent colour", "ru": "акцентный цвет"},
        "hint": {"en": "The single colour allowed to be loud. Keep it the same all clip.",
                 "ru": "Единственный цвет, которому позволено кричать. Держи его одним на весь клип."},
        "example": {"en": "sodium orange", "ru": "натриевый оранжевый"},
    },
    "time": {
        "label": {"en": "time of day", "ru": "время суток"},
        "hint": {"en": "Late dusk, 3 a.m., first light — not just 'night'.",
                 "ru": "Поздние сумерки, три часа ночи, первый свет — не просто «ночь»."},
        "example": {"en": "3 a.m.", "ru": "три часа ночи"},
    },
    "weather": {
        "label": {"en": "weather", "ru": "погода"},
        "hint": {"en": "Weather is free production value — it fills empty frames.",
                 "ru": "Погода — бесплатный продакшн: она заполняет пустые кадры."},
        "example": {"en": "fine drizzle", "ru": "мелкая морось"},
    },
    "emotion": {
        "label": {"en": "state", "ru": "состояние"},
        "hint": {"en": "What the face is doing. Avoid naming the feeling — name the face.",
                 "ru": "Что делает лицо. Не называй чувство — называй лицо."},
        "example": {"en": "jaw set, eyes not blinking",
                    "ru": "сжатая челюсть, немигающий взгляд"},
    },
    "action": {
        "label": {"en": "action", "ru": "действие"},
        "hint": {"en": "One verb. Two verbs in one scene read as neither.",
                 "ru": "Один глагол. Два глагола в одной сцене не читаются вовсе."},
        "example": {"en": "pulling the hood up", "ru": "натягивает капюшон"},
    },
    "crowd": {
        "label": {"en": "crowd", "ru": "толпа"},
        "hint": {"en": "Who fills the background. Uniform crowds read strongest.",
                 "ru": "Кто заполняет фон. Сильнее всего читается однородная толпа."},
        "example": {"en": "commuters in identical dark coats",
                    "ru": "пассажиры в одинаковых тёмных пальто"},
    },
    "vehicle": {
        "label": {"en": "vehicle", "ru": "транспорт"},
        "hint": {"en": "Car, train, bus. Name the era — it dates the whole frame.",
                 "ru": "Машина, поезд, автобус. Назови эпоху — она датирует весь кадр."},
        "example": {"en": "nineties sedan with one headlight out",
                    "ru": "седан девяностых с одной разбитой фарой"},
    },
    "detail": {
        "label": {"en": "detail", "ru": "деталь"},
        "hint": {"en": "The small thing the extreme close-up is about.",
                 "ru": "Мелочь, ради которой снимается деталь."},
        "example": {"en": "chipped ring on the middle finger",
                    "ru": "щербатое кольцо на среднем пальце"},
    },
    "text": {
        "label": {"en": "on-screen text", "ru": "надпись в кадре"},
        "hint": {"en": "Sign, tattoo, screen. Keep it short — models spell badly.",
                 "ru": "Вывеска, тату, экран. Держи коротким — модели пишут с ошибками."},
        "example": {"en": "CLOSED", "ru": "CLOSED"},
    },
}

#: Крупности — тот же словарь, что у SCENES_SYSTEM в claude.py. Второго списка
#: быть не может: значение уезжает в поле shot_size как есть.
SHOT_SIZES = ("extreme close-up", "close-up", "medium", "wide", "establishing")

#: Движки, на которые ссылаются карточки. Дублировать сюда весь mediagen не
#: нужно — validate() сверяет пересечение, если mediagen импортируется.
ENGINE_KEYS = ("grok", "seedance-2-mini", "seedance-2-0", "seedance-2-5",
               "seedance-2-5-480", "kling-3.0", "kling-3.0-pro", "minimax-h3")

#: Движки, умеющие первый+последний кадр. Grok оживляет только первый — и это
#: не мелочь, а граница половины каталога.
FIRST_LAST_ENGINES = ("seedance-2-mini", "seedance-2-5-480", "kling-3.0",
                      "seedance-2-0", "seedance-2-5", "kling-3.0-pro")

#: Дешёвая пара «на чём это гонять по умолчанию»: одна платная лошадка и одна
#: подороже. Списки в карточках короткие намеренно — витрина с восемью
#: движками в каждой карточке не помогает выбрать, она мешает.
_CHEAP = ["seedance-2-mini", "kling-3.0"]
_ANY = ["grok", "seedance-2-mini", "kling-3.0"]


# ─────────────────────────────────────────────────────────────────────────────
# ПРИЁМЫ. 56 карточек.
#
# Поля карточки:
#   key            — ключ, он же адрес /prompts/shots/<key>
#   category       — из CATEGORIES
#   tier           — free | pro. КОМУ ОТКРЫТ ТЕКСТ, а не кто может снимать.
#                    Приём с tier="pro" виден всем подписью и описанием;
#                    закрыт ровно английский текст и кнопка «применить».
#   label/desc     — подпись и «для чего», два языка
#   gain           — что это даёт КЛИПУ (а не «как выглядит»). Продаёт карточку
#   shot           — крупность, уезжает в поле shot_size как есть
#   camera         — уезжает в поле camera_move как есть
#   first/last     — промпты первого и последнего кадра, английские, со слотами
#   motion         — промпт анимации (motion_prompt)
#   slots          — какие подстановки использует карточка
#   needs_last     — нужен ли движок, монтирующий между двумя кадрами
#   engines        — на чём снимать
#   styles_fit     — с какими стилями сочетается (ключи prompts_catalog.STYLES)
#   tags           — из prompts_catalog.TAGS, для общего фильтра витрины
#   result         — пример результата СЛОВАМИ: что человек увидит на экране
# ─────────────────────────────────────────────────────────────────────────────
SHOTS: list[dict] = [

    # ══════════════ КАДР И КАМЕРА ══════════════
    {
        "key": "push_in",
        "category": "shot", "tier": "free",
        "label": {"en": "Push in on the face", "ru": "Наезд на лицо"},
        "desc": {
            "en": "The frame gets closer without cutting: same person, same light, less air.",
            "ru": "Кадр приближается без склейки: тот же человек, тот же свет, меньше воздуха.",
        },
        "gain": {
            "en": "Turns a neutral verse line into a moment of attention. The cheapest way to make a scene feel intentional.",
            "ru": "Превращает нейтральную строчку куплета в момент внимания. Самый дешёвый способ сделать сцену осмысленной.",
        },
        "shot": "medium", "camera": "slow push-in",
        "first": "Medium shot of {character} standing still in {location}, framed from the waist up, centred, "
                 "plenty of empty space above the head, {emotion}, looking slightly past the lens.",
        "last": "The same {character} in the same {location} with the same light and the same clothing, now framed "
                "from the shoulders up, the head filling two thirds of the frame, eyes level with the lens, {emotion}.",
        "motion": "The camera moves steadily closer on one axis. The subject does not move toward the camera; only the "
                  "distance shrinks. No pan, no roll, no cut.",
        "slots": ["character", "location", "emotion"],
        "needs_last": True, "engines": _CHEAP,
        "styles_fit": ["cinema", "noir", "dreamclad", "ghibli", "shinkai"],
        "tags": ["deadpan", "muted"],
        "result": {
            "en": "Six seconds in which nothing happens except that you end up much closer to a face than you started.",
            "ru": "Шесть секунд, за которые не происходит ничего, кроме одного: ты оказываешься гораздо ближе к лицу, чем был.",
        },
    },
    {
        "key": "pull_back_reveal",
        "category": "shot", "tier": "free",
        "label": {"en": "Pull back to reveal", "ru": "Отъезд-раскрытие"},
        "desc": {
            "en": "Starts on a detail, ends on the whole place. The scene explains itself backwards.",
            "ru": "Начинается на детали, заканчивается на всём месте целиком. Сцена объясняет себя задом наперёд.",
        },
        "gain": {
            "en": "One scene that carries a whole chorus. Reveals scale you would otherwise need three shots to show.",
            "ru": "Одна сцена, которая тянет целый припев. Показывает масштаб, на который иначе ушло бы три кадра.",
        },
        "shot": "close-up", "camera": "slow pull-back",
        "first": "Close-up of {character} in {location}, face filling the frame, background almost unreadable, {emotion}.",
        "last": "Wide shot of the same {location} from the same direction: {character} is now small and off-centre in the "
                "lower third, the full space visible around them, same light, same time of day.",
        "motion": "The camera retreats in a straight line, revealing more of the space every second. The subject stays "
                  "where they are and does not shrink by walking away.",
        "slots": ["character", "location", "emotion"],
        "needs_last": True, "engines": _CHEAP,
        "styles_fit": ["cinema", "shinkai", "noir", "longheads"],
        "tags": ["melancholy", "muted"],
        "result": {
            "en": "You think you are watching a portrait until the room appears around it and turns out to be enormous and empty.",
            "ru": "Кажется, что смотришь портрет, — пока вокруг не проявляется комната, и она оказывается огромной и пустой.",
        },
    },
    {
        "key": "static_hold",
        "category": "shot", "tier": "free",
        "label": {"en": "Locked-off frame", "ru": "Статика с микрожизнью"},
        "desc": {
            "en": "The camera does not move at all. Only the world inside the frame does.",
            "ru": "Камера не двигается вообще. Двигается только мир внутри кадра.",
        },
        "gain": {
            "en": "The one shot every clip needs and nobody writes: a place to breathe between moving scenes. Also the "
                 "safest scene on the cheap engine — nothing to smear.",
            "ru": "Кадр, который нужен каждому клипу и который никто не пишет: место, чтобы выдохнуть между движущимися "
                  "сценами. Заодно самая безопасная сцена на дешёвом движке — нечему размазаться.",
        },
        "shot": "wide", "camera": "static",
        "first": "Locked-off wide shot of {location}, {time}, composed symmetrically, {character} small and still in the "
                 "centre of the frame, nothing else moving.",
        "last": "The identical frame a few seconds later: same camera position, same composition, same light. Only "
                "{weather} and small ambient details have shifted.",
        "motion": "The camera is completely static, as if on a tripod. Motion comes only from the air, the weather and "
                  "small background life. The subject holds their position.",
        "slots": ["location", "character", "time", "weather"],
        "needs_last": False, "engines": _ANY,
        "styles_fit": ["ghibli", "cinema", "katsumi", "longheads", "clay"],
        "tags": ["deadpan", "cozy"],
        "result": {
            "en": "A frame you could hang on a wall, with just enough drift in it to prove it is not a photograph.",
            "ru": "Кадр, который можно повесить на стену, — с ровно таким движением, чтобы стало понятно: это не фотография.",
        },
    },
    {
        "key": "low_angle_rise",
        "category": "shot", "tier": "free",
        "label": {"en": "From the ground up", "ru": "С земли вверх"},
        "desc": {
            "en": "Camera at boot level looking up. The oldest way to make someone look larger than the frame.",
            "ru": "Камера на уровне ботинок, взгляд вверх. Древнейший способ сделать человека больше кадра.",
        },
        "gain": {
            "en": "Status without props. Works on a person, a building or a bottle, and costs nothing but the angle.",
            "ru": "Статус без реквизита. Работает на человеке, здании и флаконе, а стоит одного ракурса.",
        },
        "shot": "wide", "camera": "low angle, slow tilt up",
        "first": "Extreme low angle from ground level in {location}: {character} seen from below the knees, towering "
                 "against the sky, wide lens, strong vertical lines, {time}.",
        "last": "The same low angle, the framing now higher: {character} filling the upper half of the frame from the "
                "chest up, sky behind them, same lens, same light.",
        "motion": "The camera stays on the ground and tilts upward. The subject remains planted. Verticals stretch "
                  "slightly toward the top of the frame.",
        "slots": ["character", "location", "time"],
        "needs_last": True, "engines": _CHEAP,
        "styles_fit": ["spike", "munir", "cinema", "punkrf", "dreamclad"],
        "tags": ["epic", "saturated"],
        "result": {
            "en": "An ordinary person shot the way monuments are shot, with the sky doing the work behind them.",
            "ru": "Обычный человек, снятый как памятник, — и небо за спиной делает всю работу.",
        },
    },
    {
        "key": "overhead_drop",
        "category": "shot", "tier": "free",
        "label": {"en": "Straight down", "ru": "Сверху вниз"},
        "desc": {
            "en": "The camera directly above, looking at the floor. People become shapes.",
            "ru": "Камера ровно сверху, смотрит в пол. Люди превращаются в фигуры.",
        },
        "gain": {
            "en": "Instant graphic frame. Reads at thumbnail size, which is the only size that matters in a feed.",
            "ru": "Мгновенно графичный кадр. Читается в размере превью — а других размеров в ленте не бывает.",
        },
        "shot": "wide", "camera": "top-down, static",
        "first": "Directly overhead shot looking straight down at {location}: {character} lying or standing at the "
                 "centre of a flat graphic surface, arms clearly readable as shapes, {time}.",
        "last": "The same overhead angle: {character} has changed position on the same surface — the composition is "
                "still symmetric, the light unchanged.",
        "motion": "The camera hangs still, perfectly perpendicular to the ground. Only the subject rearranges "
                  "themselves within the frame.",
        "slots": ["location", "character", "time"],
        "needs_last": True, "engines": _CHEAP,
        "styles_fit": ["flat2d", "katsumi", "cinema", "clay", "longheads"],
        "tags": ["deadpan", "saturated"],
        "result": {
            "en": "A frame that looks designed rather than filmed — and stops the scroll for exactly that reason.",
            "ru": "Кадр, который выглядит нарисованным, а не снятым, — и именно поэтому останавливает пролистывание.",
        },
    },
    {
        "key": "walk_with",
        "category": "shot", "tier": "free",
        "label": {"en": "Walking alongside", "ru": "Идём рядом"},
        "desc": {
            "en": "The camera travels at the character's speed, level with them. The background moves, they do not.",
            "ru": "Камера едет со скоростью героя, вровень с ним. Движется фон, а не он.",
        },
        "gain": {
            "en": "The default scene of every walking clip, and the one that makes a track feel like it is going somewhere.",
            "ru": "Базовая сцена любого клипа-прохода и главная причина, по которой трек начинает казаться движущимся куда-то.",
        },
        "shot": "medium", "camera": "tracking alongside, matched speed",
        "first": "Medium tracking shot of {character} walking through {location}, filmed from the side at their own "
                 "height, background already blurred by movement, {time}.",
        "last": "The same side-on framing of {character} still walking, same posture and clothing, but the background "
                "behind them is a completely different part of {location}.",
        "motion": "The camera glides parallel to the subject at exactly their walking speed, so they stay locked in the "
                  "same part of the frame while the background streams past.",
        "slots": ["character", "location", "time"],
        "needs_last": True, "engines": _CHEAP,
        "styles_fit": ["dreamclad", "noir", "cinema", "punkrf", "shinkai"],
        "tags": ["street", "muted"],
        "result": {
            "en": "Someone walking who never gets anywhere, which is exactly what you want under a verse.",
            "ru": "Человек идёт и никуда не приходит — ровно то, что нужно под куплет.",
        },
    },
    {
        "key": "detail_insert",
        "category": "shot", "tier": "free",
        "label": {"en": "Cut-in detail", "ru": "Врезка-деталь"},
        "desc": {
            "en": "One object, huge, out of context. The punctuation mark of a clip.",
            "ru": "Один предмет, огромный, без контекста. Знак препинания в клипе.",
        },
        "gain": {
            "en": "Breaks up a run of medium shots for the price of the cheapest scene in the studio — nothing has to "
                 "move for it to work.",
            "ru": "Разбивает череду средних планов ценой самой дешёвой сцены в студии: чтобы она сработала, ничему не "
                  "обязательно двигаться.",
        },
        "shot": "extreme close-up", "camera": "static macro",
        "first": "Extreme close-up of {detail}, filling the entire frame, shallow depth of field, the surrounding "
                 "{location} reduced to unreadable blur behind it.",
        "last": "The same extreme close-up of {detail} a moment later, from the same distance, with a small physical "
                "change: it has been moved, touched or lit slightly differently.",
        "motion": "Almost no camera movement — a breath of drift at most. The only motion is inside the object itself.",
        "slots": ["detail", "location"],
        "needs_last": False, "engines": _ANY,
        "styles_fit": ["cinema", "noir", "dreamclad", "clay", "embroidery"],
        "tags": ["deadpan", "muted"],
        "result": {
            "en": "Three seconds of a single object that make the next wide shot land harder than it would have alone.",
            "ru": "Три секунды одного предмета, после которых следующий общий план бьёт сильнее, чем ударил бы сам по себе.",
        },
    },
    {
        "key": "orbit_quarter",
        "category": "shot", "tier": "pro",
        "label": {"en": "Quarter orbit", "ru": "Четверть оборота"},
        "desc": {
            "en": "The camera swings a quarter of the way around a standing subject. Not a full circle — a full circle "
                 "is where cheap engines fall apart.",
            "ru": "Камера обходит стоящего героя на четверть круга. Не полный оборот: на полном дешёвые движки разваливаются.",
        },
        "gain": {
            "en": "Volume. One scene that proves the character is a body in a space and not a flat picture.",
            "ru": "Объём. Одна сцена, доказывающая, что персонаж — тело в пространстве, а не плоская картинка.",
        },
        "shot": "medium", "camera": "quarter arc around subject",
        "first": "Medium shot of {character} standing still in {location}, filmed straight on from the front, {emotion}, "
                 "the background clearly readable behind them.",
        "last": "The same {character} in the same position and clothing, now filmed from a three-quarter angle roughly "
                "45 degrees to their side, at the same height and distance; a different part of {location} is behind them.",
        "motion": "The camera arcs sideways around the subject on a fixed radius, keeping them centred and the same size "
                  "in frame. The subject does not turn.",
        "slots": ["character", "location", "emotion"],
        "needs_last": True, "engines": ["kling-3.0", "seedance-2-mini"],
        "styles_fit": ["cinema", "pixar", "spike", "munir"],
        "tags": ["epic", "clean"],
        "result": {
            "en": "The background slides sideways behind a person who never moves — the frame becomes a room.",
            "ru": "Фон уезжает вбок за человеком, который не двинулся, — и кадр превращается в комнату.",
        },
    },
    {
        "key": "rack_focus",
        "category": "shot", "tier": "pro",
        "label": {"en": "Focus handover", "ru": "Перевод фокуса"},
        "desc": {
            "en": "Two things in one frame at different distances. Sharpness moves from one to the other.",
            "ru": "Две вещи в одном кадре на разном расстоянии. Резкость переходит с одной на другую.",
        },
        "gain": {
            "en": "Tells the viewer what to look at without a cut. The most cinematic thing you can do inside a single scene.",
            "ru": "Говорит зрителю, куда смотреть, без склейки. Самое киношное, что можно сделать внутри одной сцены.",
        },
        "shot": "close-up", "camera": "rack focus, static frame",
        "first": "Close-up composition with two subjects at different depths: {prop} sharp in the foreground filling the "
                 "left of the frame, {character} standing behind it and completely out of focus, {location}.",
        "last": "The identical composition and camera position: now {character} is sharp and clearly readable, and "
                "{prop} in the foreground has gone soft.",
        "motion": "The camera does not move. Focus travels smoothly from the foreground object to the person behind it. "
                  "Nothing else changes.",
        "slots": ["prop", "character", "location"],
        "needs_last": True, "engines": ["kling-3.0", "seedance-2-mini"],
        "styles_fit": ["cinema", "noir", "fanuel", "dreamclad"],
        "tags": ["menacing", "muted"],
        "result": {
            "en": "You are looking at an object, and then, without anything cutting, you are looking at the person who owns it.",
            "ru": "Смотришь на предмет — и вдруг, без единой склейки, смотришь на того, кому он принадлежит.",
        },
    },
    {
        "key": "dolly_zoom",
        "category": "shot", "tier": "pro",
        "label": {"en": "The floor drops out", "ru": "Пол уходит из-под ног"},
        "desc": {
            "en": "The subject stays the same size while the space behind them stretches. Named after the Hitchcock shot.",
            "ru": "Герой остаётся того же размера, а пространство за ним растягивается. Тот самый хичкоковский кадр.",
        },
        "gain": {
            "en": "Dread on demand, with no extra elements in the frame. One scene per clip, no more — it is loud.",
            "ru": "Тревога по требованию, без единого лишнего предмета в кадре. Одна сцена на клип, не больше: приём громкий.",
        },
        "shot": "medium", "camera": "dolly zoom",
        "first": "Medium shot of {character} standing motionless in {location}, shot on a normal lens, the background "
                 "at natural scale and distance behind them, {emotion}.",
        "last": "The same {character} at exactly the same size in frame and the same position, but the background of "
                "{location} is now dramatically wider, further away and distorted at the edges.",
        "motion": "The camera moves toward the subject while the field of view widens by the same amount, so the subject "
                  "holds their size while the background appears to pull away. The subject stays completely still.",
        "slots": ["character", "location", "emotion"],
        "needs_last": True, "engines": ["kling-3.0", "seedance-2-0"],
        "styles_fit": ["cinema", "noir", "punkrf", "katsumi"],
        "tags": ["menacing", "cold"],
        "result": {
            "en": "A person standing perfectly still while the world quietly refuses to stay the same distance away.",
            "ru": "Человек стоит совершенно неподвижно, а мир тихо отказывается оставаться на прежнем расстоянии.",
        },
    },

    # ══════════════ ПЕРСОНАЖИ И ПОРТРЕТЫ ══════════════
    {
        "key": "model_sheet",
        "category": "character", "tier": "free",
        "label": {"en": "Character model sheet", "ru": "Лист персонажа"},
        "desc": {
            "en": "Three views of one person on a neutral background. Not a scene — the reference every other scene "
                 "will be measured against.",
            "ru": "Три ракурса одного человека на нейтральном фоне. Не сцена, а эталон, по которому сверяются все остальные сцены.",
        },
        "gain": {
            "en": "The single most valuable frame in the project: it is why the face survives to scene thirty instead "
                 "of drifting by scene six.",
            "ru": "Самый ценный кадр проекта: именно из-за него лицо доживает до тридцатой сцены, а не уплывает к шестой.",
        },
        "shot": "medium", "camera": "static, eye level",
        "first": "Character reference sheet on a flat neutral grey background: {character} wearing {outfit}, shown "
                 "three times side by side — front view, three-quarter view and profile — same height, same lighting, "
                 "even soft light, no shadows on the background, full body visible, neutral expression, arms relaxed.",
        "last": "The same three-view reference sheet, identical framing and lighting, with the character's face shown "
                "larger: head and shoulders in all three views.",
        "motion": "No motion. This frame is a reference, not a shot.",
        "slots": ["character", "outfit"],
        "needs_last": False, "engines": ["grok"],
        "styles_fit": ["ghibli", "pixar", "flat2d", "clay", "cinema"],
        "tags": ["clean", "daylight"],
        "result": {
            "en": "A flat, boring, extremely useful image that every later frame quietly copies from.",
            "ru": "Плоская, скучная и крайне полезная картинка, с которой потом тихо срисовывается каждый следующий кадр.",
        },
    },
    {
        "key": "hero_intro",
        "category": "character", "tier": "free",
        "label": {"en": "First appearance", "ru": "Первое появление"},
        "desc": {
            "en": "The scene where the audience meets the character. Back or silhouette first, face second.",
            "ru": "Сцена, в которой зритель знакомится с героем. Сначала спина или силуэт, лицо — вторым.",
        },
        "gain": {
            "en": "Buys you thirty seconds of curiosity for free. A face shown immediately is a face nobody waits for.",
            "ru": "Бесплатно покупает тридцать секунд любопытства. Лицо, показанное сразу, — лицо, которого никто не ждал.",
        },
        "shot": "wide", "camera": "slow push-in from behind",
        "first": "Wide shot of {character} seen from behind in {location}, wearing {outfit}, standing still and facing "
                 "away from the camera, face not visible, {time}.",
        "last": "The same {character} in the same {location} and the same {outfit}, now turned toward the camera in a "
                "medium shot, face fully visible for the first time, {emotion}.",
        "motion": "The camera closes in from behind while the person turns their head and then their shoulders toward "
                  "the lens. One continuous move, no cut.",
        "slots": ["character", "location", "outfit", "time", "emotion"],
        "needs_last": True, "engines": _CHEAP,
        "styles_fit": ["dreamclad", "cinema", "noir", "spike", "shinkai"],
        "tags": ["menacing", "muted"],
        "result": {
            "en": "Six seconds of a back, and then a face — and the face is worth more than it would have been at second zero.",
            "ru": "Шесть секунд спины, потом лицо — и это лицо стоит дороже, чем стоило бы на нулевой секунде.",
        },
    },
    {
        "key": "portrait_flash",
        "category": "character", "tier": "free",
        "label": {"en": "Flash portrait", "ru": "Портрет со вспышкой"},
        "desc": {
            "en": "Direct hard flash straight at the face, dark background falling away behind.",
            "ru": "Жёсткая прямая вспышка в лицо, тёмный фон проваливается за спиной.",
        },
        "gain": {
            "en": "Reads as a real photograph taken by a real person, which is the whole trick of the film-look presets.",
            "ru": "Читается как настоящая фотография, снятая живым человеком, — на этом и держатся плёночные пресеты.",
        },
        "shot": "close-up", "camera": "static, handheld feel",
        "first": "Close-up portrait of {character} in {location} at {time}, lit by a single hard direct flash from the "
                 "camera position: bright evenly lit face, hard-edged shadow just behind the shoulders, background "
                 "dropping into darkness, {emotion}, looking directly into the lens.",
        "last": "The same flash-lit close-up of {character}, same distance and lighting, with the head turned slightly "
                "and the eyes now looking away from the lens.",
        "motion": "The camera holds with a slight handheld unsteadiness. The subject makes one small movement of the "
                  "head. The lighting does not change.",
        "slots": ["character", "location", "time", "emotion"],
        "needs_last": True, "engines": _CHEAP,
        "styles_fit": ["dreamclad", "punkrf", "munir", "spike", "longheads"],
        "tags": ["flash", "night"],
        "result": {
            "en": "A face that looks photographed rather than generated, with the background swallowed whole.",
            "ru": "Лицо, которое выглядит сфотографированным, а не сгенерированным, — и фон, съеденный целиком.",
        },
    },
    {
        "key": "over_shoulder",
        "category": "character", "tier": "free",
        "label": {"en": "Over the shoulder", "ru": "Через плечо"},
        "desc": {
            "en": "We stand behind one person and look at what they are looking at.",
            "ru": "Мы стоим за спиной одного человека и смотрим на то, на что смотрит он.",
        },
        "gain": {
            "en": "Puts the viewer inside the scene instead of in front of it. Costs one shoulder in the foreground.",
            "ru": "Помещает зрителя внутрь сцены вместо того, чтобы поставить перед ней. Ценой одного плеча на переднем плане.",
        },
        "shot": "medium", "camera": "static, shallow depth",
        "first": "Over-the-shoulder medium shot in {location}: the back of {character}'s shoulder and head occupying the "
                 "left foreground out of focus, {prop} sharp in the middle distance ahead of them, {time}.",
        "last": "The same over-the-shoulder framing, the foreground shoulder unchanged, but the middle distance has "
                "changed: {prop} is closer, or something new has entered the space beyond it.",
        "motion": "The camera holds behind the shoulder. Movement happens ahead of the subject, in the part of the "
                  "frame they are looking at.",
        "slots": ["character", "location", "prop", "time"],
        "needs_last": True, "engines": _CHEAP,
        "styles_fit": ["cinema", "noir", "fanuel", "katsumi"],
        "tags": ["deadpan", "muted"],
        "result": {
            "en": "You are not watching a person — you are standing behind them, which is a completely different feeling.",
            "ru": "Ты не смотришь на человека — ты стоишь у него за спиной, и это совсем другое ощущение.",
        },
    },
    {
        "key": "hands_only",
        "category": "character", "tier": "free",
        "label": {"en": "Hands, no face", "ru": "Только руки"},
        "desc": {
            "en": "The character is present but the face is not in frame. Hands do the acting.",
            "ru": "Персонаж в кадре, лица нет. Играют руки.",
        },
        "gain": {
            "en": "Every clip has scenes where the face drifts. This one cannot drift, because there is no face to "
                 "get wrong — and it still carries emotion.",
            "ru": "В каждом клипе есть сцены, где лицо уплывает. Эта уплыть не может: лица, которое можно испортить, "
                  "тут нет, — а эмоция всё равно есть.",
        },
        "shot": "close-up", "camera": "static, slight drift",
        "first": "Close-up of a pair of hands in {location}, framed from the forearms down, {action}, the face and body "
                 "of {character} outside the frame entirely, {time}.",
        "last": "The same close-up of the same hands from the same angle, the action now finished: the position of the "
                "fingers and {prop} has visibly changed.",
        "motion": "The camera holds close on the hands with the faintest drift. All movement belongs to the fingers and "
                  "the object they hold.",
        "slots": ["location", "action", "character", "prop", "time"],
        "needs_last": True, "engines": _CHEAP,
        "styles_fit": ["cinema", "clay", "embroidery", "noir", "dreamclad"],
        "tags": ["melancholy", "handmade"],
        "result": {
            "en": "Two hands doing one small thing, and somehow you know exactly how the person feels.",
            "ru": "Две руки делают одно мелкое действие — и почему-то понятно ровно то, что человек чувствует.",
        },
    },
    {
        "key": "two_shot_tension",
        "category": "character", "tier": "pro",
        "label": {"en": "Two people, one frame", "ru": "Двое в кадре"},
        "desc": {
            "en": "Two characters sharing a shot with visible space between them. The space is the subject.",
            "ru": "Два персонажа в одном кадре с видимым воздухом между ними. Этот воздух и есть содержание кадра.",
        },
        "gain": {
            "en": "The cheapest way to put a relationship on screen: you never have to say what it is.",
            "ru": "Самый дешёвый способ показать отношения: не приходится ни разу объяснять, какие они.",
        },
        "shot": "wide", "camera": "static, symmetrical",
        "first": "Wide two-shot in {location}: {character} on the left edge of the frame and a second person on the "
                 "right edge, a large empty gap between them, both facing forward, neither looking at the other, {time}.",
        "last": "The same wide two-shot from the same camera position: the gap between the two people has changed — one "
                "of them has moved, or turned their head toward the other.",
        "motion": "The camera is locked off. The only change is the distance and the direction of attention between the "
                  "two people.",
        "slots": ["location", "character", "time"],
        "needs_last": True, "engines": ["kling-3.0", "seedance-2-mini"],
        "styles_fit": ["cinema", "ghibli", "fanuel", "longheads", "katsumi"],
        "tags": ["melancholy", "deadpan"],
        "result": {
            "en": "Two people who never touch and never speak, and the whole verse lands on the distance between them.",
            "ru": "Двое, которые не касаются и не говорят, — и весь куплет ложится на расстояние между ними.",
        },
    },
    {
        "key": "mirror_double",
        "category": "character", "tier": "pro",
        "label": {"en": "The reflection", "ru": "Отражение"},
        "desc": {
            "en": "The character and their reflection in the same frame, and the two do not quite agree.",
            "ru": "Герой и его отражение в одном кадре, и эти двое не вполне согласны друг с другом.",
        },
        "gain": {
            "en": "Puts an idea on screen — doubt, a second self, a lie — without a single word of lyric doing the work.",
            "ru": "Выносит на экран идею — сомнение, второе «я», ложь — без единой строчки текста в помощь.",
        },
        "shot": "medium", "camera": "static, slight tilt",
        "first": "Medium shot of {character} in {location} standing in front of a reflective surface — a window, a "
                 "mirror, still water — so that both the person and their reflection are clearly visible in the same "
                 "frame, {emotion}, {time}.",
        "last": "The identical framing: the person has moved slightly, but the reflection is holding an unmistakably "
                "different pose or looking in a different direction.",
        "motion": "The camera holds still. The person makes one small movement. The reflection does not follow it "
                  "exactly.",
        "slots": ["character", "location", "emotion", "time"],
        "needs_last": True, "engines": ["kling-3.0", "seedance-2-0"],
        "styles_fit": ["noir", "cinema", "katsumi", "dreamclad", "punkrf"],
        "tags": ["menacing", "cold"],
        "result": {
            "en": "You notice the mismatch about a second after the scene ends, which is exactly when it should land.",
            "ru": "Несовпадение замечаешь примерно через секунду после конца сцены — ровно тогда, когда и надо.",
        },
    },
    {
        "key": "crowd_single",
        "category": "character", "tier": "pro",
        "label": {"en": "One against the uniform", "ru": "Один против одинаковых"},
        "desc": {
            "en": "A crowd dressed identically, and one person who is not. The frame does the pointing.",
            "ru": "Толпа в одинаковом и один человек, который не такой. Кадр показывает пальцем сам.",
        },
        "gain": {
            "en": "The strongest single image in the research: a uniform mass reads instantly at thumbnail size and "
                 "needs no context at all.",
            "ru": "Сильнейший одиночный образ из исследования: однородная масса читается мгновенно в размере превью "
                  "и вообще не нуждается в контексте.",
        },
        "shot": "wide", "camera": "static, slow push-in",
        "first": "Wide shot of {location} filled edge to edge with {crowd}, all dressed alike and all facing the same "
                 "direction, with {character} standing among them in {outfit} — the only element that breaks the "
                 "pattern, {time}.",
        "last": "The same wide shot: the uniform crowd has shifted as one body, and {character} is now more isolated "
                "in the frame than before.",
        "motion": "The camera creeps forward very slowly. The crowd moves in unison, as a single mass. The odd person "
                  "out stays where they are.",
        "slots": ["location", "crowd", "character", "outfit", "time"],
        "needs_last": True, "engines": ["seedance-2-mini", "kling-3.0"],
        "styles_fit": ["dreamclad", "punkrf", "katsumi", "noir", "longheads"],
        "tags": ["crowd", "menacing"],
        "result": {
            "en": "Four hundred identical coats and one that is not, and you find it before you know you are looking.",
            "ru": "Четыреста одинаковых пальто и одно другое — и ты находишь его раньше, чем понимаешь, что искал.",
        },
    },

    # ══════════════ ЛОКАЦИИ И СРЕДА ══════════════
    {
        "key": "establishing_city",
        "category": "location", "tier": "free",
        "label": {"en": "The city establishing shot", "ru": "Заявочный город"},
        "desc": {
            "en": "The frame that says where we are, once, so no later scene has to.",
            "ru": "Кадр, который один раз говорит, где мы находимся, — чтобы этого не делала ни одна следующая сцена.",
        },
        "gain": {
            "en": "Bought once, spent all clip. Without it, every interior scene has to explain itself.",
            "ru": "Куплен один раз, тратится весь клип. Без него каждая сцена в интерьере вынуждена объясняться сама.",
        },
        "shot": "establishing", "camera": "static wide, very slow drift",
        "first": "Establishing wide shot of {location} at {time}, seen from a high vantage point, deep perspective, "
                 "layered depth from foreground to horizon, {weather} in the air, no single subject dominating.",
        "last": "The same establishing view from the same point: the light has shifted a little and the traffic and "
                "windows across the frame have changed state.",
        "motion": "The camera barely moves — a slow drift at most. Life happens across the whole frame at small scale: "
                  "traffic, windows, birds, weather.",
        "slots": ["location", "time", "weather"],
        "needs_last": False, "engines": _ANY,
        "styles_fit": ["shinkai", "cinema", "noir", "dreamclad", "longheads"],
        "tags": ["night", "cold"],
        "result": {
            "en": "One frame that saves you from explaining the geography for the rest of the clip.",
            "ru": "Один кадр, который избавляет от объяснения географии до конца клипа.",
        },
    },
    {
        "key": "interior_window_light",
        "category": "location", "tier": "free",
        "label": {"en": "One room, one window", "ru": "Комната и одно окно"},
        "desc": {
            "en": "An interior lit by a single window. Everything else falls off into shadow.",
            "ru": "Интерьер, освещённый одним окном. Всё остальное проваливается в тень.",
        },
        "gain": {
            "en": "The most reliable interior in the catalogue: one light source means the model has one decision to "
                 "make, and it usually makes it right.",
            "ru": "Самый надёжный интерьер в каталоге: один источник — одно решение у модели, и обычно она принимает "
                  "его правильно.",
        },
        "shot": "wide", "camera": "static",
        "first": "Wide interior shot of {location}, lit only by daylight falling through one window: a bright pool of "
                 "light on the floor, the far corners of the room in deep shadow, dust visible in the beam, "
                 "{character} standing at the edge of the light, {time}.",
        "last": "The identical interior and camera position: the person has moved fully into the light, or fully out "
                "of it, and the room is otherwise unchanged.",
        "motion": "The camera does not move. Dust drifts in the beam. The subject crosses the boundary between light "
                  "and shadow once.",
        "slots": ["location", "character", "time"],
        "needs_last": True, "engines": _CHEAP,
        "styles_fit": ["ghibli", "cinema", "fanuel", "shinkai", "clay"],
        "tags": ["cozy", "warm"],
        "result": {
            "en": "A room that looks lived in, and a person stepping into or out of the only light it has.",
            "ru": "Комната, в которой живут, и человек, который входит в её единственный свет или выходит из него.",
        },
    },
    {
        "key": "corridor_perspective",
        "category": "location", "tier": "free",
        "label": {"en": "Corridor to the vanishing point", "ru": "Коридор в точку схода"},
        "desc": {
            "en": "A long space shot straight down its own centre line.",
            "ru": "Длинное пространство, снятое ровно по своей осевой линии.",
        },
        "gain": {
            "en": "Depth for free. A symmetrical corridor is the one composition that looks deliberate even when "
                 "nothing else in the frame does.",
            "ru": "Бесплатная глубина. Симметричный коридор — единственная композиция, которая выглядит намеренной, "
                  "даже когда всё остальное в кадре нет.",
        },
        "shot": "wide", "camera": "slow dolly forward, centred",
        "first": "Wide symmetrical shot straight down the centre of {location}: parallel lines converging to a single "
                 "vanishing point in the middle of the frame, {character} small and centred far down the space, {time}.",
        "last": "The same symmetrical corridor from the same axis: the camera is now much deeper into the space and "
                "{character} is significantly closer and larger, still dead centre.",
        "motion": "The camera moves straight forward along the centre line without drifting sideways. Symmetry is "
                  "preserved for the whole shot.",
        "slots": ["location", "character", "time"],
        "needs_last": True, "engines": _CHEAP,
        "styles_fit": ["noir", "cinema", "katsumi", "punkrf", "longheads"],
        "tags": ["menacing", "cold"],
        "result": {
            "en": "A perfectly symmetrical hallway getting shorter, with a figure at the end of it getting nearer.",
            "ru": "Идеально симметричный коридор становится короче, а фигура в его конце — ближе.",
        },
    },
    {
        "key": "rooftop_edge",
        "category": "location", "tier": "free",
        "label": {"en": "Roof and horizon", "ru": "Крыша и горизонт"},
        "desc": {
            "en": "A person on a roof with the city below and nothing above.",
            "ru": "Человек на крыше: город внизу, сверху ничего.",
        },
        "gain": {
            "en": "The chorus frame. Sky is empty space, and empty space above a head is where a title or a hook goes.",
            "ru": "Кадр под припев. Небо — это пустое место, а пустое место над головой — туда ложится титр или хук.",
        },
        "shot": "wide", "camera": "static, slow tilt",
        "first": "Wide shot from a rooftop at {time}: {character} standing near the edge with their back to the camera, "
                 "the city spread out far below and behind them, a large empty sky filling the upper half of the frame, "
                 "{weather}.",
        "last": "The same rooftop composition: the person has turned to face the camera, or stepped back from the edge; "
                "the city and sky behind them are unchanged.",
        "motion": "The camera holds wide with a slow upward tilt. Wind moves clothing and hair. The city below stays "
                  "distant and small.",
        "slots": ["time", "character", "weather"],
        "needs_last": True, "engines": _CHEAP,
        "styles_fit": ["shinkai", "cinema", "dreamclad", "spike", "noir"],
        "tags": ["epic", "melancholy"],
        "result": {
            "en": "A small person, a very large sky, and the exact amount of empty frame a chorus needs.",
            "ru": "Маленький человек, очень большое небо и ровно столько пустого кадра, сколько нужно припеву.",
        },
    },
    {
        "key": "empty_transit",
        "category": "location", "tier": "free",
        "label": {"en": "Empty transport at night", "ru": "Пустой транспорт ночью"},
        "desc": {
            "en": "A carriage, a bus, a platform — built for hundreds, holding one.",
            "ru": "Вагон, автобус, платформа — рассчитанные на сотни, вмещающие одного.",
        },
        "gain": {
            "en": "Loneliness that needs no explanation, plus practical lighting the engine already knows how to render.",
            "ru": "Одиночество, которое не надо объяснять, плюс встроенный в кадр свет, который движок и так умеет рисовать.",
        },
        "shot": "wide", "camera": "static, gentle sway",
        "first": "Wide interior shot of an empty {vehicle} at {time}: hard overhead fluorescent light, rows of empty "
                 "seats, {character} sitting alone at the far end of the frame, black windows reflecting the interior.",
        "last": "The same interior from the same seat height: the view through the windows has completely changed and "
                "the person has shifted position slightly.",
        "motion": "The camera sways gently with the movement of the vehicle. Light from outside sweeps through the "
                  "windows at intervals. The passenger barely moves.",
        "slots": ["vehicle", "time", "character"],
        "needs_last": True, "engines": _CHEAP,
        "styles_fit": ["katsumi", "noir", "cinema", "dreamclad", "longheads"],
        "tags": ["melancholy", "night"],
        "result": {
            "en": "Somewhere designed for a crowd, with one person in it, at an hour when that is normal.",
            "ru": "Место, спроектированное под толпу, с одним человеком внутри — в час, когда это нормально.",
        },
    },
    {
        "key": "nature_scale",
        "category": "location", "tier": "pro",
        "label": {"en": "Human against scale", "ru": "Человек против масштаба"},
        "desc": {
            "en": "A landscape so large the person in it becomes a unit of measurement.",
            "ru": "Пейзаж настолько большой, что человек в нём становится единицей измерения.",
        },
        "gain": {
            "en": "Production value that costs one prompt. This is the frame people screenshot.",
            "ru": "Бюджет на экране ценой одного промпта. Именно этот кадр люди скриншотят.",
        },
        "shot": "establishing", "camera": "static, extreme wide",
        "first": "Extreme wide landscape of {location} at {time}, {weather}: the terrain fills almost the whole frame, "
                 "and {character} appears as a tiny figure in the lower third, barely large enough to identify as a person.",
        "last": "The same landscape from the same position: the figure has moved a short distance across the terrain "
                "and the light has shifted slightly.",
        "motion": "The camera stays fixed. Only the weather and the tiny figure move. The scale relationship never "
                  "changes.",
        "slots": ["location", "time", "weather", "character"],
        "needs_last": True, "engines": ["kling-3.0", "seedance-2-mini"],
        "styles_fit": ["shinkai", "ghibli", "cinema", "fanuel", "longheads"],
        "tags": ["epic", "nature"],
        "result": {
            "en": "You look for the person for half a second, find them, and immediately understand how big everything else is.",
            "ru": "Полсекунды ищешь человека, находишь — и сразу понимаешь, насколько велико всё остальное.",
        },
    },
    {
        "key": "weather_wall",
        "category": "location", "tier": "pro",
        "label": {"en": "Wall of weather", "ru": "Стена погоды"},
        "desc": {
            "en": "Rain, snow or dust dense enough to become the subject of the shot.",
            "ru": "Дождь, снег или пыль такой плотности, что становятся содержанием кадра.",
        },
        "gain": {
            "en": "Weather hides everything the engine renders badly and adds motion to a scene where nothing else "
                 "happens. Two problems, one prompt.",
            "ru": "Погода прячет всё, что движок рисует плохо, и добавляет движение сцене, в которой больше ничего "
                  "не происходит. Две проблемы, один промпт.",
        },
        "shot": "medium", "camera": "static, slight handheld",
        "first": "Medium shot of {character} in {location} during heavy {weather}, the air visibly full of falling "
                 "particles lit from behind, background almost erased by the density, {time}.",
        "last": "The same framing a few seconds later: the weather has intensified and the background is now completely "
                "unreadable; only the silhouette of the person remains clear.",
        "motion": "The camera holds with a small handheld tremor. The weather moves in continuous sheets across the "
                  "frame. The person braces against it without walking.",
        "slots": ["character", "location", "weather", "time"],
        "needs_last": True, "engines": ["seedance-2-mini", "kling-3.0"],
        "styles_fit": ["cinema", "noir", "punkrf", "dreamclad", "shinkai"],
        "tags": ["menacing", "cold"],
        "result": {
            "en": "Six seconds where the loudest thing on screen is the air, and it works on every engine you own.",
            "ru": "Шесть секунд, где самое громкое на экране — воздух, и это работает на любом твоём движке.",
        },
    },
    {
        "key": "same_place_two_times",
        "category": "location", "tier": "pro",
        "label": {"en": "One place, two times of day", "ru": "Одно место, два времени"},
        "desc": {
            "en": "The first frame is the location at one hour; the last frame is the same location at another. The "
                 "engine has to travel between them.",
            "ru": "Первый кадр — локация в один час, последний — она же в другой. Движку приходится проехать между ними.",
        },
        "gain": {
            "en": "The clearest demonstration of what our two-frame scene can do and no single-frame engine can: time "
                 "passing inside one continuous shot.",
            "ru": "Самая наглядная демонстрация того, что умеет наша сцена из двух кадров и не умеет однокадровый "
                  "движок: время, проходящее внутри одного непрерывного плана.",
        },
        "shot": "establishing", "camera": "static, locked off",
        "first": "Locked-off establishing shot of {location} in full daylight: every element of the composition clearly "
                 "placed, no people, hard midday shadows.",
        "last": "The exact same composition of {location} from the exact same camera position at {time}: identical "
                "buildings, identical framing, but the light, the colour and the artificial lights are completely different.",
        "motion": "The camera never moves. Light, shadow and colour travel across the whole frame from one time of day "
                  "to the other. Nothing in the geometry changes.",
        "slots": ["location", "time"],
        "needs_last": True, "engines": ["kling-3.0", "seedance-2-0"],
        "styles_fit": ["shinkai", "cinema", "ghibli", "longheads"],
        "tags": ["nostalgic", "warm"],
        "result": {
            "en": "A street that goes from noon to night without a cut, which is the single best argument for paying "
                 "for a first-and-last-frame engine.",
            "ru": "Улица, которая проходит путь от полудня до ночи без склейки, — лучший аргумент за то, чтобы платить "
                  "за движок с первым и последним кадром.",
        },
    },

    # ══════════════ ПРЕДМЕТНАЯ СЪЁМКА ══════════════
    {
        "key": "product_hero",
        "category": "product", "tier": "free",
        "label": {"en": "Hero shot", "ru": "Герой-кадр товара"},
        "desc": {
            "en": "The object alone, lit properly, on a plain surface. The frame that goes on the card.",
            "ru": "Предмет один, правильно освещённый, на простой поверхности. Кадр, который идёт на карточку.",
        },
        "gain": {
            "en": "The only product frame you actually need. Everything else in this category is decoration on top of it.",
            "ru": "Единственный продуктовый кадр, который реально нужен. Всё остальное в этой категории — украшение поверх него.",
        },
        "shot": "medium", "camera": "static, slow push-in",
        "first": "Product shot of {object} standing alone at the centre of {surface}, lit by one large soft light from "
                 "the upper left and a subtle rim light from behind, clean uncluttered background, {accent} as the only "
                 "saturated colour in the frame, no hands, no text.",
        "last": "The same {object} on the same {surface} with identical lighting, framed noticeably closer so the "
                "material and the edges of the object fill more of the frame.",
        "motion": "The camera pushes in slowly and evenly on the object. The object itself does not move or rotate. "
                  "Reflections travel across its surface as the distance closes.",
        "slots": ["object", "surface", "accent"],
        "needs_last": True, "engines": _CHEAP,
        "styles_fit": ["cinema", "pixar", "clay", "fanuel"],
        "tags": ["clean", "luxury"],
        "result": {
            "en": "A product photograph that gets slowly more expensive-looking for six seconds.",
            "ru": "Предметная фотография, которая шесть секунд подряд становится всё дороже на вид.",
        },
    },
    {
        "key": "in_hand",
        "category": "product", "tier": "free",
        "label": {"en": "In the hand", "ru": "Товар в руке"},
        "desc": {
            "en": "The object held by a real hand, at the scale a person actually meets it.",
            "ru": "Предмет в живой руке, в том масштабе, в котором человек с ним встречается.",
        },
        "gain": {
            "en": "Answers 'how big is it' silently. Product cards that skip this question lose the sale to it.",
            "ru": "Молча отвечает на вопрос «какого оно размера». Карточки, которые этот вопрос пропускают, на нём и теряют продажу.",
        },
        "shot": "close-up", "camera": "static, handheld",
        "first": "Close-up of a hand holding {object} in {location}, natural skin texture visible, the object held at a "
                 "slight angle toward the lens, soft directional daylight, shallow depth of field.",
        "last": "The same hand and the same {object} from the same distance, now turned to show a different face of the "
                "object; the fingers have shifted their grip.",
        "motion": "The camera holds close with a natural handheld feel. The hand rotates the object once, unhurriedly. "
                  "Nothing else enters the frame.",
        "slots": ["object", "location"],
        "needs_last": True, "engines": _CHEAP,
        "styles_fit": ["cinema", "dreamclad", "clay", "fanuel"],
        "tags": ["clean", "daylight"],
        "result": {
            "en": "Six seconds that tell you the weight of the thing without anyone saying a word.",
            "ru": "Шесть секунд, за которые становится понятен вес предмета, и никто ничего не сказал.",
        },
    },
    {
        "key": "texture_macro",
        "category": "product", "tier": "free",
        "label": {"en": "Texture macro", "ru": "Макро фактуры"},
        "desc": {
            "en": "So close that the material stops being an object and becomes a landscape.",
            "ru": "Так близко, что материал перестаёт быть предметом и становится ландшафтом.",
        },
        "gain": {
            "en": "The cheapest scene that looks expensive. Also the safest one on a weak engine: at this scale there "
                 "is nothing for it to get anatomically wrong.",
            "ru": "Самая дешёвая сцена, которая выглядит дорого. Заодно самая безопасная на слабом движке: в этом "
                  "масштабе ему нечего испортить анатомически.",
        },
        "shot": "extreme close-up", "camera": "static macro, slow drift",
        "first": "Extreme macro of the surface of {object}: the material fills the entire frame, individual grain, "
                 "weave or pores clearly visible, raking light from one side picking out every ridge, extremely "
                 "shallow depth of field.",
        "last": "The same macro surface, the plane of focus now resting on a different part of the material and the "
                "raking light striking it from a slightly changed angle.",
        "motion": "The camera drifts a few millimetres across the surface. Focus travels with it. Nothing else happens.",
        "slots": ["object"],
        "needs_last": False, "engines": _ANY,
        "styles_fit": ["cinema", "embroidery", "clay", "fanuel"],
        "tags": ["clean", "handmade"],
        "result": {
            "en": "A material you can almost feel, in a shot that cost the least of anything in the clip.",
            "ru": "Материал, который почти можно потрогать, в кадре, который обошёлся дешевле всего в клипе.",
        },
    },
    {
        "key": "unbox_reveal",
        "category": "product", "tier": "pro",
        "label": {"en": "The opening", "ru": "Раскрытие"},
        "desc": {
            "en": "Closed box in the first frame, open box in the last. The oldest format on the internet.",
            "ru": "В первом кадре коробка закрыта, в последнем открыта. Старейший формат в интернете.",
        },
        "gain": {
            "en": "A complete beginning-and-end in six seconds, which is exactly the shape a short-form platform rewards.",
            "ru": "Полноценные начало и конец за шесть секунд — ровно та форма, которую награждают короткие платформы.",
        },
        "shot": "medium", "camera": "static, top-down at slight angle",
        "first": "Medium shot from slightly above of a closed package on {surface}, hands resting on the lid, even soft "
                 "light, {accent} visible on the packaging, nothing else in frame.",
        "last": "The same framing and the same {surface}: the package is now open, the lid set aside, and {object} is "
                "visible inside it, catching the light.",
        "motion": "The camera stays above the surface at a fixed angle. The hands lift the lid in one continuous "
                  "movement and withdraw from the frame.",
        "slots": ["surface", "accent", "object"],
        "needs_last": True, "engines": ["kling-3.0", "seedance-2-mini"],
        "styles_fit": ["cinema", "pixar", "clay"],
        "tags": ["clean", "playful"],
        "result": {
            "en": "A closed box becomes an open box, and the thing inside arrives at exactly the moment the beat does.",
            "ru": "Закрытая коробка становится открытой, и то, что внутри, появляется ровно на доле.",
        },
    },
    {
        "key": "product_scene",
        "category": "product", "tier": "pro",
        "label": {"en": "Product in a life", "ru": "Товар в среде"},
        "desc": {
            "en": "The object where it is actually used, with the mess of a real place around it.",
            "ru": "Предмет там, где им реально пользуются, — с беспорядком настоящего места вокруг.",
        },
        "gain": {
            "en": "Turns a catalogue image into a reason to want it. The background is doing the selling, not the object.",
            "ru": "Превращает каталожную картинку в повод захотеть. Продаёт фон, а не предмет.",
        },
        "shot": "medium", "camera": "static, shallow depth",
        "first": "Medium shot of {object} sitting in {location} amid the ordinary clutter of a used space, natural "
                 "window light, the object sharp and everything around it slightly soft, {time}.",
        "last": "The same scene from the same position: a hand has entered the frame and is reaching for the object, "
                "or has just moved it, and the surrounding space is unchanged.",
        "motion": "The camera holds still with shallow focus on the object. Life happens softly out of focus behind it "
                  "and one hand enters the frame.",
        "slots": ["object", "location", "time"],
        "needs_last": True, "engines": ["kling-3.0", "seedance-2-mini"],
        "styles_fit": ["cinema", "ghibli", "fanuel", "katsumi"],
        "tags": ["cozy", "warm"],
        "result": {
            "en": "A thing in a real room, which is far more persuasive than the same thing on white.",
            "ru": "Вещь в настоящей комнате — а это гораздо убедительнее, чем та же вещь на белом.",
        },
    },
    {
        "key": "reflection_gloss",
        "category": "product", "tier": "pro",
        "label": {"en": "Gloss and reflection", "ru": "Глянец и отражение"},
        "desc": {
            "en": "A highlight travelling across a polished surface. The entire luxury advertising language in one move.",
            "ru": "Блик, идущий по полированной поверхности. Весь язык люксовой рекламы одним движением.",
        },
        "gain": {
            "en": "The difference between 'a photo of a product' and 'an advert'. It is one moving highlight and "
                 "nothing else.",
            "ru": "Разница между «фото товара» и «рекламой». Это один движущийся блик и больше ничего.",
        },
        "shot": "close-up", "camera": "slow lateral slide",
        "first": "Close-up of {object} on {surface} in near darkness, a single narrow highlight running along one edge "
                 "of its polished surface, deep black background, {accent} glowing faintly in the reflection.",
        "last": "The same close-up of the same object: the highlight has travelled to the opposite edge, revealing the "
                "shape of the object as it passes.",
        "motion": "The camera slides slowly sideways past the object while the light source stays fixed, so the "
                  "highlight sweeps across the surface. The object does not move.",
        "slots": ["object", "surface", "accent"],
        "needs_last": True, "engines": ["kling-3.0", "seedance-2-0"],
        "styles_fit": ["cinema", "noir", "fanuel", "munir"],
        "tags": ["luxury", "monochrome"],
        "result": {
            "en": "A line of light walking across an object in the dark, and the object looks like it costs more than it does.",
            "ru": "Полоса света идёт по предмету в темноте, и предмет выглядит дороже, чем стоит.",
        },
    },
    {
        "key": "set_lineup",
        "category": "product", "tier": "pro",
        "label": {"en": "The line-up", "ru": "Линейка"},
        "desc": {
            "en": "Several items of one family in one frame, evenly lit and evenly spaced.",
            "ru": "Несколько предметов одной линейки в одном кадре, равномерно освещённых и равномерно расставленных.",
        },
        "gain": {
            "en": "One frame that carries a whole catalogue page. Needed for cards, and painful to shoot any other way.",
            "ru": "Один кадр, который тянет целую страницу каталога. Нужен для карточек и мучителен в съёмке любым другим способом.",
        },
        "shot": "wide", "camera": "static, straight on",
        "first": "Straight-on wide product shot of several variants of {object} lined up in a row on {surface}, evenly "
                 "spaced, identical height and lighting on each, plain seamless background, {accent} repeating across "
                 "the row, no text.",
        "last": "The identical line-up from the same angle, framed slightly closer, with a soft shadow falling "
                "consistently under every item.",
        "motion": "The camera moves almost imperceptibly closer. The objects stay exactly where they are. Only the "
                  "light on their surfaces changes.",
        "slots": ["object", "surface", "accent"],
        "needs_last": True, "engines": ["kling-3.0", "seedance-2-mini"],
        "styles_fit": ["cinema", "pixar", "flat2d", "fanuel"],
        "tags": ["clean", "luxury"],
        "result": {
            "en": "A row of products that looks like it was shot in a studio with a budget, in a scene that took one prompt.",
            "ru": "Ряд товаров, снятый будто в студии с бюджетом, — в сцене, которая стоила одного промпта.",
        },
    },

    # ══════════════ ДВИЖЕНИЕ И ЭКШЕН ══════════════
    {
        "key": "run_toward",
        "category": "motion", "tier": "free",
        "label": {"en": "Running at the camera", "ru": "Бег на камеру"},
        "desc": {
            "en": "Far in the first frame, close in the last. The simplest motion an engine can get right.",
            "ru": "В первом кадре далеко, в последнем близко. Простейшее движение, которое движок способен не испортить.",
        },
        "gain": {
            "en": "Energy with no choreography. Put it under the first bar of a chorus and the chorus lands.",
            "ru": "Энергия без хореографии. Поставь под первый такт припева — и припев сработает.",
        },
        "shot": "wide", "camera": "static, subject approaches",
        "first": "Wide shot of {character} far away in {location}, mid-stride and running directly toward the camera, "
                 "full body visible, {time}, motion blur in the limbs.",
        "last": "The same {character} in the same {location}, now very close to the camera in a medium shot, still "
                "running, upper body filling most of the frame, {emotion}.",
        "motion": "The camera holds its position. The runner covers real distance toward it, growing in the frame the "
                  "whole time. Their stride never breaks.",
        "slots": ["character", "location", "time", "emotion"],
        "needs_last": True, "engines": _CHEAP,
        "styles_fit": ["punkrf", "dreamclad", "spike", "cinema", "katsumi"],
        "tags": ["epic", "saturated"],
        "result": {
            "en": "Someone starts far away and ends up in your face in six seconds, and the beat did it.",
            "ru": "Человек начинает далеко и за шесть секунд оказывается вплотную — и это сделал бит.",
        },
    },
    {
        "key": "turn_to_camera",
        "category": "motion", "tier": "free",
        "label": {"en": "The turn", "ru": "Разворот к камере"},
        "desc": {
            "en": "Back to the lens, then face to the lens. One movement, whole scene.",
            "ru": "Сначала спина к объективу, потом лицо. Одно движение — вся сцена.",
        },
        "gain": {
            "en": "The most reliable 'something happened' in the catalogue. Works on every engine and never smears, "
                 "because only one body part rotates.",
            "ru": "Самое надёжное «что-то произошло» в каталоге. Работает на любом движке и никогда не мажет: "
                  "поворачивается ровно одна часть тела.",
        },
        "shot": "medium", "camera": "static",
        "first": "Medium shot of {character} in {location} with their back fully to the camera, shoulders squared, "
                 "head down, {time}.",
        "last": "The same {character} from the same distance and position, now turned to face the camera directly, "
                "eyes on the lens, {emotion}.",
        "motion": "The camera is locked off. The person rotates once toward the lens — head first, then shoulders — "
                  "and stops. Their feet stay planted.",
        "slots": ["character", "location", "time", "emotion"],
        "needs_last": True, "engines": _CHEAP,
        "styles_fit": ["dreamclad", "noir", "cinema", "punkrf", "spike"],
        "tags": ["menacing", "deadpan"],
        "result": {
            "en": "A back, a turn, and a face — the smallest possible event that still counts as an event.",
            "ru": "Спина, поворот, лицо — минимально возможное событие, которое всё ещё считается событием.",
        },
    },
    {
        "key": "fabric_wind",
        "category": "motion", "tier": "free",
        "label": {"en": "Cloth in the wind", "ru": "Ткань и ветер"},
        "desc": {
            "en": "Nobody moves. The clothes do.",
            "ru": "Никто не двигается. Двигается одежда.",
        },
        "gain": {
            "en": "Motion in a scene where you deliberately want stillness. Cheap engines animate fabric well and "
                 "faces badly — this plays to that.",
            "ru": "Движение в сцене, где нужна неподвижность. Дешёвые движки хорошо анимируют ткань и плохо лица — "
                  "приём играет на этом.",
        },
        "shot": "medium", "camera": "static",
        "first": "Medium shot of {character} standing perfectly still in {location} wearing {outfit}, the loose fabric "
                 "hanging straight down, {time}.",
        "last": "The same shot with the same person in the same pose: the fabric of the {outfit} is now lifted and "
                "extended sideways by wind, hair pushed back, body still unmoved.",
        "motion": "The camera does not move and neither does the person. Wind moves the fabric and the hair "
                  "continuously across the whole shot.",
        "slots": ["character", "location", "outfit", "time"],
        "needs_last": True, "engines": _CHEAP,
        "styles_fit": ["shinkai", "ghibli", "cinema", "fanuel", "spike"],
        "tags": ["epic", "melancholy"],
        "result": {
            "en": "A completely still person in a scene that is nonetheless obviously alive.",
            "ru": "Совершенно неподвижный человек в сцене, которая при этом очевидно живая.",
        },
    },
    {
        "key": "car_pass",
        "category": "motion", "tier": "free",
        "label": {"en": "The pass-by", "ru": "Проезд мимо"},
        "desc": {
            "en": "Something large crosses the frame and briefly hides everything behind it.",
            "ru": "Что-то большое пересекает кадр и на секунду закрывает всё, что за ним.",
        },
        "gain": {
            "en": "A free wipe transition. Whatever is behind the object can be completely different when it clears.",
            "ru": "Бесплатная шторка. То, что за объектом, может оказаться совершенно другим, когда он уедет.",
        },
        "shot": "wide", "camera": "static, subject crosses frame",
        "first": "Wide shot of {location} at {time}: {vehicle} entering the frame from the left edge, headlights on, "
                 "the rest of the street clearly visible behind it, {weather}.",
        "last": "The same wide shot of the same street from the same camera position: the vehicle has almost left the "
                "frame on the right, and {character} is now standing in the space it had blocked.",
        "motion": "The camera stays locked. The vehicle crosses the full width of the frame at speed, briefly filling "
                  "it, and reveals what is behind it as it clears.",
        "slots": ["location", "time", "vehicle", "weather", "character"],
        "needs_last": True, "engines": _CHEAP,
        "styles_fit": ["dreamclad", "noir", "punkrf", "cinema", "munir"],
        "tags": ["street", "night"],
        "result": {
            "en": "A car goes past and leaves a person standing where nobody was, without a single cut.",
            "ru": "Мимо проезжает машина и оставляет человека там, где никого не было, — без единой склейки.",
        },
    },
    {
        "key": "impact_freeze",
        "category": "motion", "tier": "pro",
        "label": {"en": "Impact and hold", "ru": "Удар и стоп"},
        "desc": {
            "en": "Motion runs into a wall and stops dead. Cut it exactly on the beat.",
            "ru": "Движение врезается в стену и встаёт намертво. Резать ровно по доле.",
        },
        "gain": {
            "en": "The one device that makes an edit feel scored rather than assembled. Use once per clip, on the "
                 "loudest bar.",
            "ru": "Единственный приём, от которого монтаж кажется положенным на музыку, а не собранным. Один раз на "
                  "клип, на самом громком такте.",
        },
        "shot": "medium", "camera": "static, hard stop",
        "first": "Medium shot of {character} in {location} at full speed mid-{action}, body extended, motion blur "
                 "through the limbs, dust or debris already lifting off the ground.",
        "last": "The identical framing at the instant everything stops: the body locked in an extreme pose, every "
                "particle of dust suspended motionless in the air around it, absolutely no blur.",
        "motion": "Fast, blurred motion for most of the shot, then an abrupt total stop. Nothing eases out — the "
                  "movement ends on a single frame and holds.",
        "slots": ["character", "location", "action"],
        "needs_last": True, "engines": ["kling-3.0", "seedance-2-0"],
        "styles_fit": ["punkrf", "spike", "munir", "katsumi", "cinema"],
        "tags": ["epic", "saturated"],
        "result": {
            "en": "Everything is flying, then everything is frozen, and the cut happens in the silence after.",
            "ru": "Всё летит, потом всё замирает, и склейка приходится на тишину после.",
        },
    },
    {
        "key": "jump_apex",
        "category": "motion", "tier": "pro",
        "label": {"en": "Top of the jump", "ru": "Верхняя точка прыжка"},
        "desc": {
            "en": "Take-off in the first frame, the moment of weightlessness in the last. Never the landing.",
            "ru": "Отталкивание в первом кадре, момент невесомости в последнем. Приземления не бывает.",
        },
        "gain": {
            "en": "Ends the scene on the peak instead of the fall — which is why it feels triumphant rather than merely athletic.",
            "ru": "Заканчивает сцену на пике, а не на падении, — поэтому она читается как триумф, а не как спорт.",
        },
        "shot": "wide", "camera": "static low angle",
        "first": "Low-angle wide shot of {character} in {location} crouched at the instant of take-off, both feet still "
                 "touching the ground, weight loaded, {time}.",
        "last": "The same low angle: {character} is airborne at the top of the jump, both feet clear of the ground, "
                "body extended against the sky, nothing beneath them.",
        "motion": "The camera stays low and does not move. The subject leaves the ground and rises to the apex. The "
                  "shot ends before any descent begins.",
        "slots": ["character", "location", "time"],
        "needs_last": True, "engines": ["kling-3.0", "seedance-2-mini"],
        "styles_fit": ["spike", "punkrf", "pixar", "cinema", "dreamclad"],
        "tags": ["epic", "playful"],
        "result": {
            "en": "A person in the air who never comes down, because the scene ends first.",
            "ru": "Человек в воздухе, который так и не приземляется, потому что сцена кончается раньше.",
        },
    },
    {
        "key": "crowd_surge",
        "category": "motion", "tier": "pro",
        "label": {"en": "The surge", "ru": "Волна толпы"},
        "desc": {
            "en": "A crowd moving as one body, filmed from inside it.",
            "ru": "Толпа, которая движется как одно тело, снятая изнутри.",
        },
        "gain": {
            "en": "Scale you cannot fake with individuals. From inside, the engine never has to render a face correctly.",
            "ru": "Масштаб, который не подделать отдельными людьми. Изнутри движку ни разу не приходится правильно "
                  "нарисовать лицо.",
        },
        "shot": "medium", "camera": "handheld, inside the crowd",
        "first": "Handheld medium shot from inside a dense {crowd} in {location}, shoulders and raised arms crowding "
                 "the frame on all sides, hard backlight cutting through the gaps, {time}.",
        "last": "The same viewpoint from within the crowd after it has surged: bodies pressed closer, arms in different "
                "positions, the light now hitting the frame from a visibly different angle.",
        "motion": "The camera is buffeted by the movement of the bodies around it, never steady. The whole crowd moves "
                  "in one direction as a single mass.",
        "slots": ["crowd", "location", "time"],
        "needs_last": True, "engines": ["seedance-2-mini", "kling-3.0"],
        "styles_fit": ["punkrf", "dreamclad", "munir", "cinema", "noir"],
        "tags": ["crowd", "night"],
        "result": {
            "en": "Six seconds where you are in the fourth row and cannot see anything except that it is happening.",
            "ru": "Шесть секунд, в которые ты в четвёртом ряду и не видишь ничего, кроме того, что это происходит.",
        },
    },
    {
        "key": "slow_collapse",
        "category": "motion", "tier": "pro",
        "label": {"en": "Giving way", "ru": "Медленное оседание"},
        "desc": {
            "en": "Standing in the first frame, on the ground in the last, and the whole scene is the descent.",
            "ru": "В первом кадре стоит, в последнем на земле, и вся сцена — это спуск.",
        },
        "gain": {
            "en": "The scene for the last chorus. It is the only motion in the catalogue that reads as a decision "
                 "rather than an action.",
            "ru": "Сцена под последний припев. Единственное движение в каталоге, которое читается как решение, а не как действие.",
        },
        "shot": "wide", "camera": "static, slow tilt down",
        "first": "Wide shot of {character} standing upright and alone in the centre of {location}, arms at their sides, "
                 "{emotion}, {time}.",
        "last": "The same wide shot from the same camera position: the person is now on the floor, knees down, head "
                "lowered, occupying the lower part of the frame, with the empty space above them unchanged.",
        "motion": "The camera holds and tilts down slightly as the person goes down. The descent is slow and "
                  "continuous, with no stumble and no sudden fall.",
        "slots": ["character", "location", "emotion", "time"],
        "needs_last": True, "engines": ["kling-3.0", "seedance-2-0"],
        "styles_fit": ["cinema", "noir", "fanuel", "shinkai", "longheads"],
        "tags": ["melancholy", "muted"],
        "result": {
            "en": "Somebody goes down over six seconds and the empty space above them gets bigger the whole time.",
            "ru": "Человек опускается шесть секунд, и пустое место над ним всё это время становится больше.",
        },
    },

    # ══════════════ СВЕТ И ВРЕМЯ СУТОК ══════════════
    {
        "key": "golden_hour",
        "category": "light", "tier": "free",
        "label": {"en": "Golden hour", "ru": "Золотой час"},
        "desc": {
            "en": "Low sun, long shadows, warm skin. The hour everything looks good in.",
            "ru": "Низкое солнце, длинные тени, тёплая кожа. Час, в который хорошо выглядит всё.",
        },
        "gain": {
            "en": "The single largest quality upgrade available for one line of prompt. If a scene looks flat, put it "
                 "at this hour before changing anything else.",
            "ru": "Самое большое улучшение картинки, доступное за одну строчку промпта. Если сцена выглядит плоско, "
                  "сначала перенеси её в этот час, а уже потом меняй остальное.",
        },
        "shot": "medium", "camera": "static, slight drift",
        "first": "Medium shot of {character} in {location} in the last hour of daylight: the sun very low and behind "
                 "them at an angle, long shadows stretching across the ground, warm rim light on the hair and "
                 "shoulders, air visibly hazy.",
        "last": "The same shot a few minutes later in the same light: the shadows are longer, the colour warmer and "
                "deeper, and the sun sits closer to the horizon.",
        "motion": "Almost no camera movement. The light itself does the work: haze drifts, dust catches the sun, "
                  "shadows creep.",
        "slots": ["character", "location"],
        "needs_last": False, "engines": _ANY,
        "styles_fit": ["ghibli", "shinkai", "cinema", "fanuel", "dreamclad"],
        "tags": ["warm", "nostalgic"],
        "result": {
            "en": "A frame that looks like a memory, and it took one sentence about the sun.",
            "ru": "Кадр, похожий на воспоминание, — и всё это одно предложение про солнце.",
        },
    },
    {
        "key": "contre_jour",
        "category": "light", "tier": "free",
        "label": {"en": "Against the light", "ru": "Контровой"},
        "desc": {
            "en": "The light source is behind the subject, so the subject becomes a shape.",
            "ru": "Источник света за спиной героя, поэтому герой превращается в форму.",
        },
        "gain": {
            "en": "Hides a face the engine keeps getting wrong and turns the problem into a style choice.",
            "ru": "Прячет лицо, которое движок упорно портит, и превращает проблему в стилистическое решение.",
        },
        "shot": "wide", "camera": "static",
        "first": "Wide shot of {character} in {location} standing directly in front of a bright light source — a "
                 "window, headlights, the low sun — so the body reads as a dark silhouette with a bright halo around "
                 "its edges, facial features barely visible, {time}.",
        "last": "The same backlit composition: the person has stepped slightly to one side, so the light source is "
                "partly blocked and a hard flare cuts across the frame.",
        "motion": "The camera stays put. The subject shifts just enough to change how much of the light source they "
                  "cover; the flare responds.",
        "slots": ["character", "location", "time"],
        "needs_last": True, "engines": _CHEAP,
        "styles_fit": ["cinema", "noir", "shinkai", "dreamclad", "longheads"],
        "tags": ["menacing", "monochrome"],
        "result": {
            "en": "A person you recognise entirely by shape, which is often more memorable than the face would have been.",
            "ru": "Человек, которого узнаёшь только по силуэту, — и это часто запоминается лучше, чем лицо.",
        },
    },
    {
        "key": "single_source_night",
        "category": "light", "tier": "free",
        "label": {"en": "Night, one lamp", "ru": "Ночь и один источник"},
        "desc": {
            "en": "One practical light in the dark, and everything the light does not reach is genuinely black.",
            "ru": "Один живой источник в темноте, и всё, куда он не достаёт, по-настоящему чёрное.",
        },
        "gain": {
            "en": "Night scenes fail because the model lights everything a little. Naming one source and demanding "
                 "real black fixes it in one line.",
            "ru": "Ночные сцены разваливаются оттого, что модель подсвечивает всё понемногу. Назвать один источник и "
                  "потребовать настоящий чёрный — и всё чинится одной строкой.",
        },
        "shot": "medium", "camera": "static",
        "first": "Medium shot in {location} at night lit by exactly one visible practical source — a street lamp, a "
                 "bare bulb, a fire: {character} standing inside its pool of light, everything beyond that pool "
                 "falling to true black with no fill light whatsoever.",
        "last": "The same frame and the same single source: the person has moved to the edge of the light so half "
                "their body is fully in darkness.",
        "motion": "The camera does not move. The single light source flickers or sways slightly. The subject moves "
                  "between lit and unlit space.",
        "slots": ["location", "character"],
        "needs_last": True, "engines": _CHEAP,
        "styles_fit": ["noir", "cinema", "dreamclad", "punkrf", "munir"],
        "tags": ["night", "menacing"],
        "result": {
            "en": "Real darkness with one honest light in it, instead of the grey soup night scenes usually come out as.",
            "ru": "Настоящая темнота с одним честным светом вместо серой каши, которой обычно получаются ночные сцены.",
        },
    },
    {
        "key": "neon_wash",
        "category": "light", "tier": "free",
        "label": {"en": "Neon wash", "ru": "Неоновая заливка"},
        "desc": {
            "en": "Two coloured light sources from two directions, and nothing neutral anywhere in the frame.",
            "ru": "Два цветных источника с двух сторон, и ни одного нейтрального места в кадре.",
        },
        "gain": {
            "en": "Instant night-city language. Also the cheapest way to unify twenty scenes: keep the same two colours "
                 "and they look like one clip.",
            "ru": "Мгновенный язык ночного города. Заодно самый дешёвый способ сшить двадцать сцен: держи те же два "
                  "цвета — и они станут одним клипом.",
        },
        "shot": "medium", "camera": "static, slight handheld",
        "first": "Medium shot of {character} in {location} at night lit by two coloured sources from opposite sides: "
                 "{accent} striking one side of the face and a cold contrasting colour the other, wet reflective "
                 "surfaces underfoot, no neutral white anywhere in the frame.",
        "last": "The same two-colour lighting on the same person: they have turned, so the colours have swapped sides "
                "of their face.",
        "motion": "The camera holds with a small handheld drift. Signs flicker. The subject turns once between the two "
                  "colours.",
        "slots": ["character", "location", "accent"],
        "needs_last": True, "engines": _CHEAP,
        "styles_fit": ["punkrf", "katsumi", "dreamclad", "noir", "munir"],
        "tags": ["neon", "night"],
        "result": {
            "en": "A face split by two colours, which reads as a city at night faster than any establishing shot.",
            "ru": "Лицо, разделённое двумя цветами, читается как ночной город быстрее любого заявочного плана.",
        },
    },
    {
        "key": "overcast_flat",
        "category": "light", "tier": "free",
        "label": {"en": "Flat overcast", "ru": "Пасмурно и ровно"},
        "desc": {
            "en": "No sun, no shadows, no drama. Deliberately boring light.",
            "ru": "Ни солнца, ни теней, ни драмы. Намеренно скучный свет.",
        },
        "gain": {
            "en": "Documentary honesty. Every scene cannot be golden hour — the flat ones are what make the golden "
                 "ones land.",
            "ru": "Документальная честность. Не может быть весь клип золотым часом: именно ровные сцены заставляют "
                  "золотые работать.",
        },
        "shot": "wide", "camera": "static",
        "first": "Wide shot of {location} under a completely overcast sky: flat even light with no visible shadows, "
                 "desaturated colours, white featureless sky, {character} standing in the middle distance, {weather}.",
        "last": "The same flat-lit wide shot: the person has moved a few steps and the sky is unchanged.",
        "motion": "The camera is static. The light does not change at all — that is the point. Only the person and the "
                  "weather move.",
        "slots": ["location", "character", "weather"],
        "needs_last": True, "engines": _CHEAP,
        "styles_fit": ["katsumi", "longheads", "cinema", "clay", "ghibli"],
        "tags": ["deadpan", "muted"],
        "result": {
            "en": "A frame with no opinion about itself, which is exactly what a documentary-feeling clip needs between highs.",
            "ru": "Кадр, у которого нет мнения о себе, — ровно то, что нужно документальному клипу между пиками.",
        },
    },
    {
        "key": "practical_screen",
        "category": "light", "tier": "free",
        "label": {"en": "Lit by a screen", "ru": "Свет от экрана"},
        "desc": {
            "en": "The only light on the face comes from a phone, a monitor or a TV.",
            "ru": "Единственный свет на лице идёт от телефона, монитора или телевизора.",
        },
        "gain": {
            "en": "Contemporary and specific. Also structurally useful: the light source is inside the frame, so the "
                 "engine knows exactly where the shadows go.",
            "ru": "Современно и конкретно. Заодно структурно удобно: источник внутри кадра, и движок точно знает, куда "
                  "кладутся тени.",
        },
        "shot": "close-up", "camera": "static",
        "first": "Close-up of {character}'s face in a dark {location}, lit entirely from below by the cold light of a "
                 "screen just out of frame, the light source clearly under the chin, everything behind them black, "
                 "{emotion}.",
        "last": "The same close-up with the same underlighting: the colour and brightness of the screen light have "
                "changed, as if the content on it has changed.",
        "motion": "The camera does not move. The screen light shifts in colour and intensity across the shot, moving "
                  "shadows across the face. The subject barely blinks.",
        "slots": ["character", "location", "emotion"],
        "needs_last": True, "engines": _CHEAP,
        "styles_fit": ["katsumi", "noir", "cinema", "punkrf", "longheads"],
        "tags": ["cold", "night"],
        "result": {
            "en": "A face lit the way faces are actually lit at two in the morning.",
            "ru": "Лицо, освещённое ровно так, как лица освещены в два часа ночи.",
        },
    },
    {
        "key": "headlight_sweep",
        "category": "light", "tier": "pro",
        "label": {"en": "Headlights passing", "ru": "Свет фар проходит"},
        "desc": {
            "en": "A moving light crosses a still face. The frame changes without the camera changing.",
            "ru": "Движущийся свет проходит по неподвижному лицу. Кадр меняется, хотя камера — нет.",
        },
        "gain": {
            "en": "Motion in a static scene, on the beat, from a source you never have to show.",
            "ru": "Движение в статичной сцене, по доле, от источника, который вообще не обязательно показывать.",
        },
        "shot": "close-up", "camera": "static",
        "first": "Close-up of {character} standing motionless in a dark {location}, almost nothing visible except the "
                 "faint outline of the face, {emotion}.",
        "last": "The same close-up at the instant a hard beam of light from a passing vehicle rakes across the face "
                "from one side, briefly revealing every feature and throwing a hard shadow behind.",
        "motion": "The camera is completely still and so is the subject. A hard beam of light travels across the face "
                  "from one edge of the frame to the other, and the darkness returns behind it.",
        "slots": ["character", "location", "emotion"],
        "needs_last": True, "engines": ["kling-3.0", "seedance-2-mini"],
        "styles_fit": ["noir", "cinema", "dreamclad", "munir", "punkrf"],
        "tags": ["night", "menacing"],
        "result": {
            "en": "A face that exists for half a second and then is gone again, exactly on the snare.",
            "ru": "Лицо, которое существует полсекунды и снова исчезает — ровно на малом барабане.",
        },
    },
    {
        "key": "dawn_shift",
        "category": "light", "tier": "pro",
        "label": {"en": "Night into dawn", "ru": "Ночь переходит в рассвет"},
        "desc": {
            "en": "The scene begins at night and ends at first light without cutting.",
            "ru": "Сцена начинается ночью и заканчивается на первом свете без склейки.",
        },
        "gain": {
            "en": "The closing scene of half the clips ever made, and here it happens inside one shot instead of "
                 "across three.",
            "ru": "Финальная сцена половины клипов на свете — только здесь она происходит внутри одного плана, а не в трёх.",
        },
        "shot": "establishing", "camera": "static, locked off",
        "first": "Locked-off establishing shot of {location} in full night: artificial lights on, sky completely dark, "
                 "{character} small in the frame, {weather}.",
        "last": "The exact same composition from the exact same position at first light: the sky is pale, the "
                "artificial lights are still on but no longer dominant, and the whole frame has turned cold blue.",
        "motion": "The camera never moves. The sky and the colour of the whole frame travel from night to first light "
                  "continuously. The figure stays where it is.",
        "slots": ["location", "character", "weather"],
        "needs_last": True, "engines": ["kling-3.0", "seedance-2-0"],
        "styles_fit": ["shinkai", "cinema", "dreamclad", "noir", "longheads"],
        "tags": ["melancholy", "cold"],
        "result": {
            "en": "Six seconds in which a night ends, and it costs one scene instead of an act.",
            "ru": "Шесть секунд, за которые кончается ночь, — ценой одной сцены, а не целого акта.",
        },
    },

    # ══════════════ ПРИЁМЫ МОНТАЖА ══════════════
    {
        "key": "match_on_action",
        "category": "cut", "tier": "free",
        "label": {"en": "Cut on the movement", "ru": "Склейка на движении"},
        "desc": {
            "en": "Two scenes in a row: the second starts in the middle of the movement the first one began.",
            "ru": "Две сцены подряд: вторая начинается посреди того движения, которое начала первая.",
        },
        "gain": {
            "en": "Makes a cut invisible. Use it wherever a jump between locations would otherwise feel like a mistake.",
            "ru": "Делает склейку незаметной. Ставь везде, где прыжок между локациями иначе читался бы как ошибка.",
        },
        "shot": "medium", "camera": "static, cut on motion",
        "first": "Medium shot of {character} in {location} beginning {action}: the movement is clearly underway but "
                 "not finished, the body caught mid-gesture.",
        "last": "The same {character} completing the identical movement — same posture, same speed, same direction of "
                "travel — but standing in a completely different place.",
        "motion": "One continuous movement carries across the change of location. The gesture never pauses or resets; "
                  "only the surroundings change.",
        "slots": ["character", "location", "action"],
        "needs_last": True, "engines": _CHEAP,
        "styles_fit": ["cinema", "dreamclad", "katsumi", "punkrf", "ghibli"],
        "tags": ["deadpan", "muted"],
        "result": {
            "en": "The location changes and you do not notice for another second, because the arm never stopped moving.",
            "ru": "Локация меняется, и ты замечаешь это на секунду позже, потому что рука не переставала двигаться.",
        },
    },
    {
        "key": "color_bridge",
        "category": "cut", "tier": "free",
        "label": {"en": "Colour bridge", "ru": "Мост по цвету"},
        "desc": {
            "en": "Two unrelated scenes joined because one colour occupies the same place in both frames.",
            "ru": "Две никак не связанные сцены, сшитые тем, что один цвет занимает в обоих кадрах одно и то же место.",
        },
        "gain": {
            "en": "Lets you cut between anything at all and have it feel deliberate. The cheapest continuity there is.",
            "ru": "Позволяет резать между чем угодно и выглядеть намеренно. Самая дешёвая непрерывность из существующих.",
        },
        "shot": "medium", "camera": "static",
        "first": "Medium shot in {location} composed so that a large area of {accent} sits in the centre of the frame "
                 "and dominates the colour of the whole image, everything else desaturated.",
        "last": "A completely different subject and place, composed so that the same {accent} occupies the same "
                "position and roughly the same area of the frame, everything else desaturated.",
        "motion": "The camera holds still in both halves. The colour block stays in place while everything around it "
                  "changes.",
        "slots": ["location", "accent"],
        "needs_last": True, "engines": _CHEAP,
        "styles_fit": ["cinema", "flat2d", "katsumi", "punkrf", "munir"],
        "tags": ["saturated", "playful"],
        "result": {
            "en": "A cut between two scenes that share nothing except one red shape, and it feels rhymed.",
            "ru": "Склейка двух сцен, у которых нет ничего общего, кроме одного красного пятна, — и она читается как рифма.",
        },
    },
    {
        "key": "scale_jump",
        "category": "cut", "tier": "free",
        "label": {"en": "Jump of scale", "ru": "Прыжок крупности"},
        "desc": {
            "en": "Establishing shot straight into extreme close-up. Nothing in between.",
            "ru": "С заявочного плана сразу в деталь. Ничего между.",
        },
        "gain": {
            "en": "The fix for a flat storyboard. Most clips read dull because every scene is a medium shot; this is "
                 "the correction.",
            "ru": "Лекарство от плоской раскадровки. Большинство клипов скучны оттого, что всё снято средним планом. "
                  "Это исправление.",
        },
        "shot": "establishing", "camera": "hard cut, no move",
        "first": "Establishing wide shot of {location} at {time}: the whole space visible, {character} small within it, "
                 "no single element emphasised.",
        "last": "An extreme close-up of {detail} belonging to that same scene, filling the entire frame, the wider "
                "space no longer visible at all.",
        "motion": "Neither half moves much. The change of scale happens at the cut, not inside the shot.",
        "slots": ["location", "time", "character", "detail"],
        "needs_last": True, "engines": _CHEAP,
        "styles_fit": ["cinema", "noir", "katsumi", "dreamclad", "longheads"],
        "tags": ["deadpan", "muted"],
        "result": {
            "en": "A whole city, and then a thumbnail, and the jump between them does more than either shot alone.",
            "ru": "Целый город — и сразу ноготь на пальце, и прыжок между ними делает больше, чем каждый кадр по отдельности.",
        },
    },
    {
        "key": "hard_silence",
        "category": "cut", "tier": "free",
        "label": {"en": "The still frame", "ru": "Кадр без движения"},
        "desc": {
            "en": "One scene in the clip where absolutely nothing moves. Placed where the music drops out.",
            "ru": "Одна сцена в клипе, где не двигается совсем ничего. Ставится туда, где музыка проваливается.",
        },
        "gain": {
            "en": "Silence is a device. A clip of uninterrupted motion has no dynamics at all, and this is the "
                 "cheapest scene you will ever generate.",
            "ru": "Тишина — это приём. У клипа из непрерывного движения нет динамики вовсе, а эта сцена — самая "
                  "дешёвая из всех, что ты сгенеришь.",
        },
        "shot": "medium", "camera": "frozen",
        "first": "Medium shot of {character} in {location}, absolutely motionless, staring directly into the lens, "
                 "{emotion}, no wind, no background movement, no atmospheric particles.",
        "last": "The identical frame, unchanged in every detail.",
        "motion": "Nothing moves at all. No camera drift, no breathing, no hair movement, no background life. The image "
                  "is held completely frozen for the full duration.",
        "slots": ["character", "location", "emotion"],
        "needs_last": False, "engines": _ANY,
        "styles_fit": ["katsumi", "longheads", "noir", "cinema", "punkrf"],
        "tags": ["deadpan", "menacing"],
        "result": {
            "en": "Two seconds where the video looks broken, and then the beat comes back and it turns out to have been on purpose.",
            "ru": "Две секунды, в которые кажется, что видео зависло, — потом возвращается бит, и оказывается, что так и было задумано.",
        },
    },
    {
        "key": "match_on_shape",
        "category": "cut", "tier": "pro",
        "label": {"en": "Match on shape", "ru": "Склейка по форме"},
        "desc": {
            "en": "Two different objects that occupy the same silhouette in the frame, cut one after the other.",
            "ru": "Два разных предмета, которые занимают в кадре один и тот же силуэт, поставленные встык.",
        },
        "gain": {
            "en": "The device that makes a clip look authored. It is the one edit viewers remember and repeat.",
            "ru": "Приём, от которого клип выглядит авторским. Единственная склейка, которую зритель запоминает и пересказывает.",
        },
        "shot": "close-up", "camera": "static, matched framing",
        "first": "Close-up of {detail} centred in the frame against a plain background, its outline clearly readable as "
                 "a simple shape.",
        "last": "A completely different object in a completely different {location}, framed at exactly the same size, "
                "position and angle, so that its outline matches the previous shape almost exactly.",
        "motion": "Both halves are static. The two objects are held in matched positions so the eye reads them as one "
                  "continuous form across the cut.",
        "slots": ["detail", "location"],
        "needs_last": True, "engines": ["kling-3.0", "seedance-2-mini"],
        "styles_fit": ["cinema", "flat2d", "katsumi", "clay", "longheads"],
        "tags": ["playful", "clean"],
        "result": {
            "en": "A ring becomes a moon becomes a headlight, and someone in the comments says they watched it four times.",
            "ru": "Кольцо становится луной, луна — фарой, и кто-то в комментариях пишет, что пересмотрел четыре раза.",
        },
    },
    {
        "key": "whip_pan_link",
        "category": "cut", "tier": "pro",
        "label": {"en": "Whip-pan link", "ru": "Связка смазом"},
        "desc": {
            "en": "The camera snaps sideways so fast the frame smears, and the smear becomes the transition.",
            "ru": "Камера рвётся вбок так быстро, что кадр смазывается, и этот смаз становится переходом.",
        },
        "gain": {
            "en": "Speed on demand. Cut two of these together and you have a transition nobody has to render for you.",
            "ru": "Скорость по требованию. Поставь две такие подряд — и получишь переход, который никому не пришлось рендерить.",
        },
        "shot": "medium", "camera": "whip pan",
        "first": "Medium shot of {character} in {location}, sharp and clearly composed, {time}.",
        "last": "The same shot at the end of a violent horizontal camera whip: the entire frame is reduced to "
                "horizontal streaks of colour with nothing recognisable left.",
        "motion": "The camera holds briefly, then snaps sideways at extreme speed, smearing the whole frame into "
                  "horizontal motion blur by the final frame.",
        "slots": ["character", "location", "time"],
        "needs_last": True, "engines": ["seedance-2-mini", "kling-3.0"],
        "styles_fit": ["punkrf", "spike", "munir", "katsumi", "dreamclad"],
        "tags": ["epic", "saturated"],
        "result": {
            "en": "A scene that ends in a blur, next to one that starts in a blur, and together they read as one move.",
            "ru": "Сцена, которая кончается смазом, рядом со сценой, которая смазом начинается, — вместе читаются как одно движение.",
        },
    },
    {
        "key": "loop_close",
        "category": "cut", "tier": "pro",
        "label": {"en": "Closing the loop", "ru": "Замыкание"},
        "desc": {
            "en": "The last scene of the clip reproduces the first one exactly, with one thing changed.",
            "ru": "Последняя сцена клипа в точности повторяет первую — с одним изменением.",
        },
        "gain": {
            "en": "The reason people watch a short video twice. On a looping platform, that is the entire game.",
            "ru": "Причина, по которой короткое видео смотрят дважды. На зацикленной платформе в этом вся игра.",
        },
        "shot": "establishing", "camera": "static, matched to opening",
        "first": "Establishing shot of {location} at {time}, composed exactly as the opening scene of the clip: same "
                 "camera position, same framing, same light, {character} in the same spot.",
        "last": "The identical composition with one deliberate difference from the opening: something present at the "
                "start is now missing, or something new has appeared in the same place.",
        "motion": "The camera holds in the same position as the first scene of the clip. Nothing moves except the one "
                  "element that has changed.",
        "slots": ["location", "time", "character"],
        "needs_last": True, "engines": ["kling-3.0", "seedance-2-mini"],
        "styles_fit": ["cinema", "shinkai", "ghibli", "longheads", "katsumi"],
        "tags": ["nostalgic", "melancholy"],
        "result": {
            "en": "The clip ends where it started, and the viewer immediately scrolls back to check what changed.",
            "ru": "Клип кончается там же, где начался, и зритель тут же отматывает назад — проверить, что изменилось.",
        },
    },
]


# ─────────────────────────────────────────────────────────────────────────────
# НАБОРЫ — «артефакт урока». Урок, после которого нечего нажать, не урок.
#
# Набор — это то, что применяется в проект ОДНОЙ КНОПКОЙ: несколько приёмов
# подряд, при желании с каркасом и предвыбранными стилями. Именно ключ набора
# лежит в поле `pack` во фронтматтере урока (docs/learn/*/NN-*.md), и именно
# по нему страница урока рисует кнопку «Открыть в студии».
#
# `slots_hint` — какие слоты человек должен заполнить один раз на весь набор:
# {character} и {location} в шести карточках подряд обязаны быть одним и тем же
# персонажем и одним и тем же местом, иначе набор не соберётся в клип.
# ─────────────────────────────────────────────────────────────────────────────
PACKS: list[dict] = [
    {
        "key": "basic_shots",
        "lesson": "shots",
        "tier": "free",
        "label": {"en": "Six shots that make a clip", "ru": "Шесть кадров, из которых собирается клип"},
        "desc": {
            "en": "The minimum set that stops a storyboard reading flat: one establishing, one push-in, one detail, "
                 "one still, one walk and one reveal.",
            "ru": "Минимальный набор, после которого раскадровка перестаёт читаться плоско: заявочный, наезд, деталь, "
                  "статика, проход и раскрытие.",
        },
        "shots": ["establishing_city", "push_in", "detail_insert", "static_hold",
                  "walk_with", "pull_back_reveal"],
        "preset": "", "styles": [],
        "slots_hint": ["character", "location", "time"],
    },
    {
        "key": "light_basics",
        "lesson": "light",
        "tier": "free",
        "label": {"en": "Five lights", "ru": "Пять источников"},
        "desc": {
            "en": "One scene per lighting decision, so you can see on your own footage what each one costs and pays.",
            "ru": "По одной сцене на каждое световое решение — чтобы на своём же материале увидеть, чего каждое стоит и что даёт.",
        },
        "shots": ["golden_hour", "contre_jour", "single_source_night", "neon_wash", "practical_screen"],
        "preset": "", "styles": [],
        "slots_hint": ["character", "location", "accent"],
    },
    {
        "key": "motion_basics",
        "lesson": "motion",
        "tier": "free",
        "label": {"en": "Motion that survives a cheap engine", "ru": "Движение, которое переживёт дешёвый движок"},
        "desc": {
            "en": "Five scenes built so the engine has exactly one thing to animate. This is why they come out clean.",
            "ru": "Пять сцен, устроенных так, что движку достаётся ровно одно дело. Поэтому они и выходят чистыми.",
        },
        "shots": ["turn_to_camera", "run_toward", "fabric_wind", "car_pass", "hands_only"],
        "preset": "", "styles": [],
        "slots_hint": ["character", "location", "outfit"],
    },
    {
        "key": "cut_basics",
        "lesson": "cuts",
        "tier": "free",
        "label": {"en": "Four cuts", "ru": "Четыре склейки"},
        "desc": {
            "en": "Applied as consecutive scene pairs, because a cut cannot be demonstrated on one scene.",
            "ru": "Применяется парами соседних сцен: склейку нельзя показать на одной сцене.",
        },
        "shots": ["match_on_action", "color_bridge", "scale_jump", "hard_silence"],
        "preset": "", "styles": [],
        "slots_hint": ["character", "location", "accent", "detail"],
    },
    {
        "key": "series_kit",
        "lesson": "series",
        "tier": "pro",
        "label": {"en": "Recurring hero kit", "ru": "Набор сквозного героя"},
        "desc": {
            "en": "A model sheet, an entrance, a location and a loop — the four scenes that make episode two look like "
                 "episode one on purpose.",
            "ru": "Лист персонажа, появление, локация и замыкание — четыре сцены, из-за которых второй выпуск выглядит "
                  "продолжением первого намеренно.",
        },
        "shots": ["model_sheet", "hero_intro", "interior_window_light", "same_place_two_times", "loop_close"],
        "preset": "one_day", "styles": [],
        "slots_hint": ["character", "outfit", "location"],
    },
    {
        "key": "ugc_kit",
        "lesson": "ugc",
        "tier": "pro",
        "label": {"en": "UGC kit", "ru": "Набор под UGC"},
        "desc": {
            "en": "A hook, a hand, a face and a product, in the order a short-form platform rewards.",
            "ru": "Хук, рука, лицо и товар — в том порядке, который награждают короткие платформы.",
        },
        "shots": ["portrait_flash", "in_hand", "practical_screen", "product_scene", "hard_silence"],
        "preset": "", "styles": [],
        "slots_hint": ["character", "object", "location"],
    },
    {
        "key": "product_kit",
        "lesson": "product",
        "tier": "pro",
        "label": {"en": "Product card kit", "ru": "Набор под карточку товара"},
        "desc": {
            "en": "Five frames that fill a marketplace card: hero, hand, macro, environment, line-up.",
            "ru": "Пять кадров, закрывающих карточку на маркетплейсе: герой, рука, макро, среда, линейка.",
        },
        "shots": ["product_hero", "in_hand", "texture_macro", "product_scene", "set_lineup"],
        "preset": "", "styles": ["cinema"],
        "slots_hint": ["object", "surface", "accent", "location"],
    },
    {
        "key": "teardown_kit",
        "lesson": "teardown",
        "tier": "pro_max",
        "label": {"en": "Teardown kit", "ru": "Набор для разбора"},
        "desc": {
            "en": "The five devices that show up in almost every clip that travels. Applied together so you can see "
                 "them working on your own track instead of someone else's.",
            "ru": "Пять приёмов, которые встречаются почти в каждом залетевшем клипе. Применяются вместе, чтобы "
                  "увидеть их на своём треке, а не на чужом.",
        },
        "shots": ["crowd_single", "match_on_shape", "impact_freeze", "headlight_sweep", "loop_close"],
        "preset": "from_the_crowd", "styles": [],
        "slots_hint": ["character", "crowd", "location", "detail"],
    },
]


# ─────────────────────────────────────────────────────────────────────────────
# ПУБЛИЧНАЯ ЧАСТЬ. Белый список полей, как у prompts_catalog: новое поле,
# дописанное в карточку по невнимательности, само наружу не поедет.
#
# Тексты промптов здесь ОТДАЮТСЯ — но не всем и не всегда: у карточки с
# tier="pro" наружу уходит всё, кроме first/last/motion. Витрина показывает
# такую карточку целиком, кнопка «применить» отключена, и это честно: человек
# видит, чего лишён, а не пустое место.
# ─────────────────────────────────────────────────────────────────────────────
PUBLIC_SHOT_FIELDS = ("key", "category", "tier", "label", "desc", "gain", "shot",
                      "camera", "slots", "needs_last", "engines", "styles_fit",
                      "tags", "result")
_TEXT_FIELDS = ("first", "last", "motion")

_BY_KEY = {s["key"]: s for s in SHOTS}
_PACK_BY_KEY = {p["key"]: p for p in PACKS}
SHOT_KEYS = tuple(_BY_KEY)
PACK_KEYS = tuple(_PACK_BY_KEY)

#: {slot} в тексте. Ровно один шаблон на весь файл — иначе fill() и validate()
#: однажды разойдутся в том, что считают плейсхолдером.
_SLOT_RE = re.compile(r"\{([a-z_]+)\}")

#: Порядок тарифов — тот же, что в learn.py. Импортировать оттуда нельзя:
#: learn.py про уроки, а не про каталог, и обратная зависимость связала бы
#: два независимых модуля ради одного кортежа.
PLAN_ORDER = ("free", "pro", "pro_max", "studio")


def unlocked(tier: str, plan_id: str, *, is_admin: bool = False) -> bool:
    """Открыт ли ТЕКСТ приёма этому тарифу. Неизвестный тариф считаем
    бесплатным: ошибка в данных не должна запирать открытый материал."""
    if is_admin or tier == "free":
        return True
    plan = plan_id if plan_id in PLAN_ORDER else "free"
    return PLAN_ORDER.index(plan) >= PLAN_ORDER.index(tier if tier in PLAN_ORDER else "free")


def _localise(value, lang: str):
    if lang in ("en", "ru") and isinstance(value, dict) and "en" in value:
        return value.get(lang, value["en"])
    return value


def public_shot(key: str, *, lang: str = "", plan_id: str = "free",
                is_admin: bool = False) -> dict | None:
    """Карточка приёма для витрины.

    Тексты промптов вкладываются ТОЛЬКО если тариф их открывает; иначе поле
    `locked` = True, а first/last/motion в ответе отсутствуют совсем. Пустая
    строка вместо текста была бы хуже: фронт не отличил бы «нет доступа» от
    «в карточке забыли промпт»."""
    s = _BY_KEY.get(key)
    if not s:
        return None
    out = {f: s[f] for f in PUBLIC_SHOT_FIELDS if f in s}
    open_text = unlocked(s["tier"], plan_id, is_admin=is_admin)
    out["locked"] = not open_text
    if open_text:
        for f in _TEXT_FIELDS:
            out[f] = s[f]
    if lang in ("en", "ru"):
        for f in ("label", "desc", "gain", "result"):
            out[f] = _localise(out.get(f), lang)
        out["slots"] = [
            {"key": k,
             "label": _localise(SLOTS[k]["label"], lang),
             "hint": _localise(SLOTS[k]["hint"], lang),
             "example": _localise(SLOTS[k]["example"], lang)}
            for k in s["slots"]
        ]
    return out


def public_shots(*, lang: str = "", category: str = "", tier: str = "",
                 style: str = "", plan_id: str = "free",
                 is_admin: bool = False) -> list[dict]:
    """Витрина с фильтрами. `style` — «покажи приёмы, которые дружат с этим
    стилем»: человек приходит в каталог уже выбрав, чем снимает."""
    out = []
    for s in SHOTS:
        if category and s["category"] != category:
            continue
        if tier and s["tier"] != tier:
            continue
        if style and style not in s["styles_fit"]:
            continue
        card = public_shot(s["key"], lang=lang, plan_id=plan_id, is_admin=is_admin)
        if card:
            out.append(card)
    return out


def public_pack(key: str, *, lang: str = "", plan_id: str = "free",
                is_admin: bool = False) -> dict | None:
    p = _PACK_BY_KEY.get(key)
    if not p:
        return None
    out = {
        "key": p["key"], "lesson": p["lesson"], "tier": p["tier"],
        "label": _localise(p["label"], lang), "desc": _localise(p["desc"], lang),
        "preset": p["preset"], "styles": list(p["styles"]),
        "locked": not unlocked(p["tier"], plan_id, is_admin=is_admin),
        "slots_hint": list(p["slots_hint"]),
        "shots": [public_shot(k, lang=lang, plan_id=plan_id, is_admin=is_admin)
                  for k in p["shots"]],
    }
    return out


def public_packs(*, lang: str = "", plan_id: str = "free",
                 is_admin: bool = False) -> list[dict]:
    return [public_pack(p["key"], lang=lang, plan_id=plan_id, is_admin=is_admin)
            for p in PACKS]


def pack_for_lesson(slug: str) -> dict | None:
    """Набор, привязанный к уроку. Фронтматтер урока хранит ключ набора, но
    страница урока приходит и по slug'у — искать по обоим дешевле, чем
    держать вторую карту соответствий."""
    return _PACK_BY_KEY.get(slug) or next(
        (p for p in PACKS if p["lesson"] == slug), None)


def fill(key: str, slots: dict | None = None, *, lang: str = "en") -> dict | None:
    """Подставить значения в тексты приёма.

    Незаполненный слот берёт `example` из словаря SLOTS — на языке, на котором
    человек работает? НЕТ: всегда английский. Промпт целиком английский, и
    русское умолчание внутри него — это кириллица, уехавшая в модель. `lang`
    здесь влияет только на подписи, которые вернутся рядом."""
    s = _BY_KEY.get(key)
    if not s:
        return None
    values = {k: str((slots or {}).get(k) or "").strip() or SLOTS[k]["example"]["en"]
              for k in s["slots"]}
    out = {f: _SLOT_RE.sub(lambda m: values.get(m.group(1), m.group(0)), s[f])
           for f in _TEXT_FIELDS}
    out["used"] = values
    out["label"] = _localise(s["label"], lang)
    return out


def scene_patch(key: str, slots: dict | None = None) -> dict | None:
    """Готовое тело для PATCH /api/scenes/{id}.

    Ключи — ровно те, что принимает update_scene() в main.py: менять их без
    оглядки на тот список нельзя, лишнее поле бэкенд молча уронит, и «применить»
    перестанет применять половину карточки."""
    body = fill(key, slots)
    if not body:
        return None
    s = _BY_KEY[key]
    return {
        "image_prompt": body["first"],
        "image_prompt_last": body["last"],
        "motion_prompt": body["motion"],
        "shot_size": s["shot"],
        "camera_move": s["camera"],
    }


def pack_patches(key: str, slots: dict | None = None) -> list[dict]:
    """То же на весь набор — по одному телу на сцену, в порядке карточек.
    Это и есть «применяется одной кнопкой»: фронт берёт первые N сцен
    раскадровки и раскладывает по ним этот список."""
    p = _PACK_BY_KEY.get(key)
    if not p:
        return []
    return [dict(scene_patch(k, slots) or {}, shot_key=k) for k in p["shots"]]


# ─────────────────────────────────────────────────────────────────────────────
# САМОПРОВЕРКА. Гоняется тестом и на старте в DEBUG.
# ─────────────────────────────────────────────────────────────────────────────
#: Ведущие артикли, запрещённые в значениях слотов. Причина не косметическая:
#: шаблоны пишут «the same {location}», и значение «a flooded parking lot»
#: превращается в «the same a flooded parking lot» — грамматический мусор,
#: который уезжает в модель на каждой второй карточке.
_ARTICLES = ("a ", "an ", "the ")


def validate() -> list[str]:
    err: list[str] = []
    cats = {c["key"] for c in CATEGORIES}

    for k, s in SLOTS.items():
        ex = (s["example"]["en"] or "").lower()
        if ex.startswith(_ARTICLES):
            err.append(f"слот {k}: пример начинается с артикля — "
                       f"после подстановки в «the same {{{k}}}» получится мусор")
        for f in ("label", "hint", "example"):
            if not (s[f].get("en") or "").strip() or not (s[f].get("ru") or "").strip():
                err.append(f"слот {k}: поле {f} не заполнено на двух языках")

    for s in SHOTS:
        k = s["key"]
        if s["category"] not in cats:
            err.append(f"{k}: неизвестная категория {s['category']}")
        if s["tier"] not in ("free", "pro"):
            err.append(f"{k}: неизвестный тариф {s['tier']}")
        if s["shot"] not in SHOT_SIZES:
            err.append(f"{k}: крупность {s['shot']} вне словаря SCENES_SYSTEM")
        if not s["camera"].strip():
            err.append(f"{k}: пустое движение камеры")
        used: set[str] = set()
        for f in _TEXT_FIELDS:
            if not s.get(f, "").strip():
                err.append(f"{k}: пустой текст {f}")
            used |= set(_SLOT_RE.findall(s.get(f, "")))
        for slot in used:
            if slot not in SLOTS:
                err.append(f"{k}: слот {{{slot}}} вне словаря SLOTS")
            elif slot not in s["slots"]:
                err.append(f"{k}: слот {{{slot}}} в тексте, но не объявлен в slots")
        for slot in s["slots"]:
            if slot not in used:
                err.append(f"{k}: слот {slot} объявлен, но в тексте не встречается")
        for eng in s["engines"]:
            if eng not in ENGINE_KEYS:
                err.append(f"{k}: неизвестный движок {eng}")
        # Главная содержательная проверка файла. Grok оживляет ТОЛЬКО первый
        # кадр — карточка, весь смысл которой в разнице между двумя кадрами,
        # на нём не работает. Витрина, обещающая обратное, врёт человеку за
        # его же очки.
        if s["needs_last"] and "grok" in s["engines"]:
            err.append(f"{k}: needs_last, но в движках grok — он оживляет только первый кадр")
        if not s["needs_last"] and not any(e in FIRST_LAST_ENGINES or e == "grok"
                                           for e in s["engines"]):
            err.append(f"{k}: не указан ни один рабочий движок")
        for f in ("label", "desc", "gain", "result"):
            val = s.get(f) or {}
            if not (val.get("en") or "").strip() or not (val.get("ru") or "").strip():
                err.append(f"{k}: поле {f} не заполнено на двух языках")

    if len(SHOT_KEYS) != len(SHOTS):
        err.append("дублирующиеся ключи приёмов")

    for p in PACKS:
        for ref in p["shots"]:
            if ref not in _BY_KEY:
                err.append(f"набор {p['key']}: несуществующий приём {ref}")
        if p["tier"] not in PLAN_ORDER:
            err.append(f"набор {p['key']}: неизвестный тариф {p['tier']}")
        # Набор не может быть дешевле своих карточек: иначе бесплатный урок
        # покажет кнопку, которая упрётся в замок на первой же сцене.
        for ref in p["shots"]:
            card = _BY_KEY.get(ref)
            if card and not unlocked(card["tier"], p["tier"]):
                err.append(f"набор {p['key']} ({p['tier']}): приём {ref} требует {card['tier']}")
        for slot in p["slots_hint"]:
            if slot not in SLOTS:
                err.append(f"набор {p['key']}: слот {slot} вне словаря SLOTS")

    # ГРАНИЦА АКТИВА. Открытый каталог не имеет права содержать ни куска
    # закрытого промпта из prompts_catalog: там ров, здесь ремесло, и путать
    # их нельзя ни руками, ни копипастой. Порог в 60 символов — тот же, что
    # у validate() каталога, и по той же причине: короткое совпадение это
    # общий английский, а не утечка.
    try:
        import prompts_catalog as _pc
    except Exception:                                    # noqa: BLE001
        err.append("prompts_catalog не импортируется — проверка утечки не выполнена")
    else:
        blob = repr(SHOTS)
        for sk in _pc.STYLE_KEYS:
            text = _pc.style_prompt(sk)
            if text and len(text) >= 60 and text[:60] in blob:
                err.append(f"УТЕЧКА: промпт стиля {sk} скопирован в библиотеку приёмов")
        known = set(_pc.STYLE_KEYS)
        for s in SHOTS:
            for ref in s["styles_fit"]:
                if ref not in known:
                    err.append(f"{s['key']}: ссылка на несуществующий стиль {ref}")
            for t in s["tags"]:
                if not any(t in axis for axis in _pc.TAGS.values()):
                    err.append(f"{s['key']}: тег {t} вне словаря prompts_catalog.TAGS")
        for p in PACKS:
            if p["preset"] and p["preset"] not in _pc.PRESET_KEYS:
                err.append(f"набор {p['key']}: несуществующий каркас {p['preset']}")
            for st in p["styles"]:
                if st not in known:
                    err.append(f"набор {p['key']}: несуществующий стиль {st}")

    return err


if __name__ == "__main__":
    problems = validate()
    if problems:
        print("\n".join(problems))
    else:
        free = sum(1 for s in SHOTS if s["tier"] == "free")
        print(f"библиотека цела: {len(SHOTS)} приёмов "
              f"({free} открытых, {len(SHOTS) - free} по тарифу) "
              f"в {len(CATEGORIES)} категориях, {len(PACKS)} наборов, "
              f"{len(SLOTS)} слотов")
