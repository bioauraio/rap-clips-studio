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

    # Новые слои базы промтов проверяются своей функцией: она длинная и
    # относится к другим данным, но ошибка должна приезжать одним списком.
    err += _validate_v2()

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



# ═════════════════════════════════════════════════════════════════════════════
#
#                    БАЗА ПРОМТОВ: ЧЕТЫРЕ НОВЫЕ СУЩНОСТИ
#
# Выше в этом файле лежат ПРИЁМЫ (SHOTS) — минимальная единица «как снята одна
# сцена». Ниже — четыре слоя, которые владелец просил отдельно, и они не
# заменяют приём, а стоят вокруг него:
#
#   SCRIPTS   сюжет ЦЕЛИКОМ: логлайн, акты, роль героя, сквозной мотив.
#             Уезжает в Project.story и Track.director_note — то есть в тот же
#             конвейер, что и закрытые каркасы prompts_catalog._SEEDS.
#   BOARDS    ГОТОВАЯ СЦЕНА: заполняет shot_size, camera_move, image_prompt,
#             image_prompt_last, motion_prompt и shot_note РАЗОМ.
#   MOTIONS   только движение: камера, тело, физика, склейка. Подменяет
#             motion_prompt на уже собранной сцене, не трогая кадры.
#   LIGHTS    свет и цвет: дописка в конец обоих кадров. Стиль главнее — см.
#             ниже про `level`.
#
# ЧЕМ BOARDS ОТЛИЧАЮТСЯ ОТ SHOTS, И ПОЧЕМУ ЭТО НЕ ДУБЛЬ.
# Приём отвечает на вопрос «какой здесь операторский ход» — он назван по ходу
# (наезд, контровой, склейка на движении) и разложен по оси «что настраивает».
# Заготовка отвечает на вопрос «какая это сцена в клипе» — она названа по
# драматургической функции (открывающая, проход, портрет, финал, переход между
# локациями) и несёт три поля, которых у приёма нет и не должно быть:
#   note     — shot_note, человеческая подпись сцены в раскадровке;
#   solo     — вариант motion для Grok, который оживляет ТОЛЬКО первый кадр;
#   bracket  — команда камеры в скобках для MiniMax, который читает только их.
# Приём — это ремесло, заготовка — это готовая строка раскадровки. Слить их в
# один список значило бы либо потерять ось «функция в клипе», либо навсегда
# смешать «наезд» и «открывающий кадр» в одном фильтре.
#
# ЯЗЫК. Тексты промптов английские — они уезжают в модель. Подписи, описания и
# режиссёрские заметки русские и английские. Единственное исключение — поле
# `story` у сценария: claude.py требует сюжет ПО-РУССКИ (SCENES_SYSTEM), и
# английский seed там просто не сработает. Тот же порядок, что у закрытых
# _SEEDS в prompts_catalog.
#
# ПЛЕЙСХОЛДЕРЫ те же и словарь тот же — SLOTS выше. Витрина показывает русские
# подписи ({персонаж}, {локация}, {предмет}, {время суток}), в тексте промпта
# стоят английские ключи ({character}, {location}, {prop}, {time}). Второго
# словаря подстановок в файле нет и быть не может.
# ═════════════════════════════════════════════════════════════════════════════

#: Черты карточки — контролируемый словарь для МИКСОВАНИЯ. Свободные строки
#: здесь запрещены по той же причине, что свободные слоты: через полгода
#: «night», «nighttime» и «dark» стали бы тремя несовместимыми осями.
TRAITS = {
    "locked":       {"en": "locked-off camera",  "ru": "камера неподвижна"},
    "moving_camera":{"en": "camera moves",       "ru": "камера едет"},
    "handheld":     {"en": "handheld",           "ru": "с рук"},
    "close":        {"en": "tight framing",      "ru": "тесная крупность"},
    "wide_frame":   {"en": "wide framing",       "ru": "общий план"},
    "fast":         {"en": "fast",               "ru": "быстро"},
    "slow":         {"en": "slow",               "ru": "медленно"},
    "night":        {"en": "night",              "ru": "ночь"},
    "daylight":     {"en": "daylight",           "ru": "день"},
    "interior":     {"en": "interior",           "ru": "интерьер"},
    "exterior":     {"en": "exterior",           "ru": "натура"},
    "crowd":        {"en": "crowd in frame",     "ru": "толпа в кадре"},
    "solo":         {"en": "one person",         "ru": "герой один"},
    "hard_light":   {"en": "hard light",         "ru": "жёсткий свет"},
    "soft_light":   {"en": "soft light",         "ru": "мягкий свет"},
    "low_key":      {"en": "low key",            "ru": "низкий ключ"},
    "high_key":     {"en": "high key",           "ru": "высокий ключ"},
    "hero_face":    {"en": "face carries it",    "ru": "держит лицо"},
    "object":       {"en": "object carries it",  "ru": "держит предмет"},
    "weather":      {"en": "weather in frame",   "ru": "погода в кадре"},
}

#: Пары черт, которые нельзя ставить в одну сцену. Это и есть машинная часть
#: правил миксования: check_mix() не спрашивает автора карточки, конфликтует ли
#: его комбинация, — он считает по этим парам.
#: Почему именно они: каждая пара — взаимоисключающее физическое условие.
#: «Камера стоит» и «камера едет» не бывают одновременно; «полдень» и «ночь»
#: тоже. Модель на противоречии не выбирает одно — она портит кадр целиком.
CONFLICT_PAIRS = (
    ("locked", "moving_camera"),
    ("locked", "handheld"),
    ("fast", "slow"),
    ("night", "daylight"),
    ("interior", "exterior"),
    ("close", "wide_frame"),
    ("hard_light", "soft_light"),
    ("low_key", "high_key"),
    ("crowd", "solo"),
)


# ─────────────────────────────────────────────────────────────────────────────
# 1. СЦЕНАРНЫЕ ПРОМТЫ — 17 штук.
#
# Единица: ВЕСЬ КЛИП. Карточка отвечает на пять вопросов, на которые стиль и
# приём не отвечают никогда: про что это, кто герой, что повторяется, чем
# открывается и чем заканчивается.
#
# Поля:
#   key           — адрес /prompts/scripts/<key>
#   tier          — free | pro, как у приёмов: закрыт ровно `story`
#   label         — подпись, два языка
#   music         — ДЛЯ КАКОЙ МУЗЫКИ. Не «для рэпа», а темп, плотность, подача
#   bpm / cut     — рабочий диапазон и средняя длина плана (slow|mid|fast)
#   logline       — одна фраза, по которой человек выбирает
#   hero          — РОЛЬ героя: что он делает в этой истории, а не кто он
#   motif         — сквозной мотив: предмет, композиция или жест, который
#                   возвращается. Без него клип рассыпается на красивые кадры
#   opens/closes  — чем открывается и чем заканчивается, словами
#   acts          — структура по актам: доля хронометража, крупность, что
#                   происходит, и КАКИЕ ЗАГОТОВКИ сюда ложатся (ключи BOARDS)
#   scenes        — сколько сцен типично (мин/типовое/макс)
#   open_board /
#   close_board   — конкретная заготовка на первую и последнюю сцену
#   styles_fit    — стили prompts_catalog, с которыми это дружит
#   preset        — родственный каркас prompts_catalog, если есть
#   story         — СЕЙД СЮЖЕТА, по-русски, уезжает в Project.story
#   dnote         — режиссёрская заметка, уезжает в Track.director_note
#   slots_hint    — что человек заполняет один раз на весь клип
#   tags          — из prompts_catalog.TAGS
#
# `share` актов в сумме равна 1.0 — проверяется машинно, как у каркасов.
# ─────────────────────────────────────────────────────────────────────────────
SCRIPTS: list[dict] = [
    {
        "key": "night_shift",
        "tier": "free",
        "label": {"en": "Night Shift", "ru": "Ночная смена"},
        "music": {
            "en": "Slow rap and cloud rap, 70-95 BPM, sparse beat, tired delivery.",
            "ru": "Медленный рэп и клауд, 70–95 BPM, разреженный бит, усталая подача.",
        },
        "bpm": [70, 95], "cut": "slow",
        "logline": {
            "en": "He works while the city sleeps and goes home when it wakes. Nothing happens except the hours.",
            "ru": "Он работает, пока город спит, и уходит домой, когда город просыпается. Не происходит ничего, кроме часов.",
        },
        "hero": {
            "en": "A worker, not a star: he is the only moving thing in a place built for crowds.",
            "ru": "Работник, а не звезда: единственное движущееся в месте, рассчитанном на толпу.",
        },
        "motif": {
            "en": "The same clock or the same lamp comes back three times, later each time.",
            "ru": "Одни и те же часы или один и тот же фонарь возвращаются трижды, каждый раз позже.",
        },
        "opens": {"en": "An empty lit space before anyone enters it.",
                  "ru": "Пустое освещённое пространство до того, как в него кто-то вошёл."},
        "closes": {"en": "First light on the same space, now switched off.",
                   "ru": "Первый свет на том же пространстве, уже выключенном."},
        "acts": [
            {"key": "in", "share": 0.2, "shot": "establishing",
             "label": {"en": "Coming on shift", "ru": "Заступает"},
             "en": "The place is shown before the person: lights on, nobody there yet.",
             "ru": "Место показано раньше человека: свет горит, людей ещё нет.",
             "boards": ["open_empty_place", "interior_doorframe", "travel_corridor_push"]},
            {"key": "work", "share": 0.35, "shot": "medium",
             "label": {"en": "The hours", "ru": "Часы"},
             "en": "The same action repeated at three different framings. Repetition is the content.",
             "ru": "Одно и то же действие в трёх разных крупностях. Повтор и есть содержание.",
             "boards": ["detail_hands_work", "portrait_hold_still", "interior_table_sit"]},
            {"key": "crack", "share": 0.25, "shot": "close-up",
             "label": {"en": "The one break", "ru": "Единственный сбой"},
             "en": "One small thing goes wrong or one look lands. The only emotional peak.",
             "ru": "Одна мелочь ломается или один взгляд попадает. Единственный эмоциональный пик.",
             "boards": ["portrait_breath_break", "detail_object_pickup", "night_screen_face"]},
            {"key": "out", "share": 0.2, "shot": "wide",
             "label": {"en": "Going home", "ru": "Домой"},
             "en": "Outside, first light, the shift is over and nothing was resolved.",
             "ru": "Улица, первый свет, смена кончилась и ничего не решилось.",
             "boards": ["travel_walk_away", "final_pull_to_wide"]},
        ],
        "scenes": {"min": 14, "typ": 28, "max": 40},
        "open_board": "open_empty_place", "close_board": "final_pull_to_wide",
        "styles_fit": ["cinema", "noir", "dreamclad", "longheads", "katsumi"],
        "preset": "one_day",
        "story": "Ночная смена героя от захода до рассвета. Драматургии в привычном смысле нет: конфликт заменён "
                 "временем, и это осознанно. Первый акт показывает место без человека — освещённый цех, склад, кухня "
                 "или зал, где ещё никого нет. Второй акт повторяет одно и то же рабочее действие минимум трижды, "
                 "каждый раз другой крупностью: общий, средний, макро на руках. Третий акт — единственный сбой: "
                 "разбитое, пролитое, чужой взгляд, звонок; ровно один пик и никакого объяснения. Четвёртый акт "
                 "выводит героя наружу в первый свет, где город только начинает жить. Сквозной мотив — часы или "
                 "лампа, попадающие в кадр трижды: в начале смены, на сбое и в финале. Финальный кадр повторяет "
                 "композицию первого, но свет уже дневной и помещение выключено.",
        "dnote": "Планы длинные, монтаж не гонится за битом. Камера почти неподвижна, движение только внутри кадра. "
                 "Ни одной улыбки и ни одного кадра, объясняющего, кем герой работает.",
        "slots_hint": ["character", "location", "prop", "time"],
        "tags": ["melancholy", "muted", "night"],
        "needs_lyrics": False,
    },
    {
        "key": "two_rooms",
        "tier": "free",
        "label": {"en": "Two Rooms", "ru": "Две комнаты"},
        "music": {
            "en": "Duets, answer verses, sung hooks, 85-110 BPM. Any track with two voices or two moods.",
            "ru": "Дуэты, ответные куплеты, спетые припевы, 85–110 BPM. Любой трек с двумя голосами или двумя настроениями.",
        },
        "bpm": [85, 110], "cut": "mid",
        "logline": {
            "en": "Two people, two rooms, one wall. They never meet and the whole clip is about the wall.",
            "ru": "Двое, две комнаты, одна стена. Они не встречаются, и весь клип про стену.",
        },
        "hero": {
            "en": "Two heroes with equal screen time. Neither is a guest in the other's story.",
            "ru": "Два героя с равным экранным временем. Ни один не гость в истории другого.",
        },
        "motif": {
            "en": "The same object exists in both rooms in a different state.",
            "ru": "Один и тот же предмет есть в обеих комнатах, но в разном состоянии.",
        },
        "opens": {"en": "One room, empty, with the sound of the other implied by a shared wall.",
                  "ru": "Одна комната, пустая, — вторая только подразумевается общей стеной."},
        "closes": {"en": "Both rooms in one frame at last, still separated.",
                   "ru": "Обе комнаты наконец в одном кадре и по-прежнему разделены."},
        "acts": [
            {"key": "left", "share": 0.3, "shot": "medium",
             "label": {"en": "Room one", "ru": "Комната первая"},
             "en": "Establish the first person entirely: their light, their objects, their rhythm.",
             "ru": "Полностью заявляем первого: его свет, его предметы, его ритм.",
             "boards": ["interior_window_side", "portrait_hold_still", "detail_object_pickup"]},
            {"key": "right", "share": 0.3, "shot": "medium",
             "label": {"en": "Room two", "ru": "Комната вторая"},
             "en": "The mirrored version: same framings, opposite light, opposite tempo.",
             "ru": "Зеркальная версия: те же крупности, противоположный свет, противоположный темп.",
             "boards": ["interior_table_sit", "portrait_profile_to_front", "night_screen_face"]},
            {"key": "wall", "share": 0.25, "shot": "close-up",
             "label": {"en": "The wall", "ru": "Стена"},
             "en": "Cut them together tighter and tighter until the pattern is unmistakable.",
             "ru": "Сводим их всё теснее, пока рифма не станет очевидной.",
             "boards": ["bridge_match_shape", "detail_texture_macro", "interior_mirror"]},
            {"key": "one", "share": 0.15, "shot": "wide",
             "label": {"en": "One frame", "ru": "Один кадр"},
             "en": "A single wide holding both rooms — and the wall still between them.",
             "ru": "Один общий, где помещаются обе комнаты, — и стена по-прежнему между ними.",
             "boards": ["final_pull_to_wide"]},
        ],
        "scenes": {"min": 16, "typ": 30, "max": 42},
        "open_board": "interior_window_side", "close_board": "final_pull_to_wide",
        "styles_fit": ["cinema", "katsumi", "longheads", "flat2d", "clay"],
        "preset": "two_worlds",
        "story": "Две комнаты, разделённые одной стеной, и два героя, которые не встречаются ни в одном кадре. "
                 "Первый акт целиком отдан первому герою: его свет, его предметы, его темп. Второй акт зеркалит "
                 "первый — те же крупности и те же композиции, но противоположные по свету и ритму: если у первого "
                 "мягкий боковой свет и медленные движения, у второго жёсткий верхний и рваные. Третий акт сводит их "
                 "монтажом всё теснее, склейка за склейкой, пока рифма не станет очевидной зрителю. Финал — один общий "
                 "план, в который помещаются обе комнаты сразу, и стена всё ещё между ними. Сквозной мотив — один и "
                 "тот же предмет, существующий в обеих комнатах в разном состоянии: целый и разбитый, полный и пустой.",
        "dnote": "Крупности во втором акте обязаны повторять первый акт кадр в кадр. Ни одного кадра, где герои "
                 "видят друг друга. Стена должна попасть в кадр не меньше четырёх раз.",
        "slots_hint": ["character", "location", "prop", "accent"],
        "tags": ["melancholy", "cold", "deadpan"],
        "needs_lyrics": True,
    },
    {
        "key": "the_return",
        "tier": "free",
        "label": {"en": "The Return", "ru": "Возвращение"},
        "music": {
            "en": "Mid-tempo storytelling rap, 85-100 BPM, verse-heavy, small chorus.",
            "ru": "Среднетемповый повествовательный рэп, 85–100 BPM, много куплета, маленький припев.",
        },
        "bpm": [85, 100], "cut": "mid",
        "logline": {
            "en": "He comes back to the block he left. Everything is in place; he is not.",
            "ru": "Он возвращается в район, который покинул. Всё на месте — не на месте он.",
        },
        "hero": {
            "en": "A visitor in his own history: he knows every corner and belongs to none of them.",
            "ru": "Гость в собственной истории: он знает каждый угол и не принадлежит ни одному.",
        },
        "motif": {
            "en": "One doorway shot three times: from far, from close, from inside.",
            "ru": "Один дверной проём, снятый трижды: издалека, вплотную, изнутри.",
        },
        "opens": {"en": "Arrival shot from behind, face withheld.",
                  "ru": "Приезд, снятый со спины: лицо не показываем."},
        "closes": {"en": "Leaving by the same road, the light colder.",
                   "ru": "Уезжает по той же дороге, свет холоднее."},
        "acts": [
            {"key": "arrive", "share": 0.2, "shot": "wide",
             "label": {"en": "Arrival", "ru": "Приезд"},
             "en": "The place first, the face later. Withhold the character for three scenes.",
             "ru": "Сначала место, лицо позже. Держим героя закрытым три сцены.",
             "boards": ["open_door_out", "travel_car_window", "open_detail_first"]},
            {"key": "same", "share": 0.3, "shot": "medium",
             "label": {"en": "Everything in place", "ru": "Всё на месте"},
             "en": "Familiar geography filmed like a foreign country.",
             "ru": "Знакомая география, снятая как чужая страна.",
             "boards": ["travel_side_track", "interior_doorframe", "crowd_queue_line"]},
            {"key": "mismatch", "share": 0.3, "shot": "close-up",
             "label": {"en": "The mismatch", "ru": "Несовпадение"},
             "en": "Old friends, old rooms, new distance. Nobody says it out loud.",
             "ru": "Старые друзья, старые комнаты, новая дистанция. Вслух этого не говорят.",
             "boards": ["portrait_two_shot", "interior_table_sit", "portrait_breath_break"]},
            {"key": "leave", "share": 0.2, "shot": "establishing",
             "label": {"en": "Leaving", "ru": "Отъезд"},
             "en": "He goes back the way he came. The rhyme is the point.",
             "ru": "Он уезжает тем же путём. Рифма и есть смысл.",
             "boards": ["final_rhyme_open", "final_walk_out_frame"]},
        ],
        "scenes": {"min": 16, "typ": 30, "max": 44},
        "open_board": "open_door_out", "close_board": "final_rhyme_open",
        "styles_fit": ["cinema", "longheads", "punkrf", "noir", "dreamclad"],
        "preset": "homecoming",
        "story": "Герой возвращается в район, из которого уехал, проводит там один день и уезжает обратно. Первый акт "
                 "снимает приезд со спины и не показывает лицо первые три сцены: сначала место, потом человек. Второй "
                 "акт проходит по знакомой географии — двор, лестница, магазин, — но снимает её как чужую страну: "
                 "широкие статичные планы, герой мелкий в кадре. Третий акт даёт встречи: старые друзья, старая "
                 "кухня, новая дистанция, которую никто не называет словами; конфликт держится молчанием и "
                 "крупностью, а не репликами. Финал — отъезд тем же путём, что и приезд, кадр в кадр с первым актом, "
                 "но свет холоднее. Сквозной мотив — один дверной проём, показанный трижды: издалека, вплотную и "
                 "изнутри.",
        "dnote": "Никаких флешбэков и никаких надписей с датами. Разница времён показывается только светом и тем, "
                 "насколько мелким герой выглядит в кадре.",
        "slots_hint": ["character", "location", "outfit", "time"],
        "tags": ["nostalgic", "muted", "street"],
        "needs_lyrics": True,
    },
    {
        "key": "run_the_block",
        "tier": "free",
        "label": {"en": "Run the Block", "ru": "Забег по кварталу"},
        "music": {
            "en": "Fast drill and hard trap, 130-160 BPM, dense hi-hats, aggressive delivery.",
            "ru": "Быстрый дрилл и жёсткий трэп, 130–160 BPM, плотные хэты, агрессивная подача.",
        },
        "bpm": [130, 160], "cut": "fast",
        "logline": {
            "en": "One run through one neighbourhood, cut to the hats, and it never slows down.",
            "ru": "Один забег по одному кварталу, нарезанный под хэты, и он не сбавляет.",
        },
        "hero": {
            "en": "A body in motion. The face matters less than the momentum.",
            "ru": "Тело в движении. Лицо здесь важно меньше, чем инерция.",
        },
        "motif": {
            "en": "The same corner is passed three times, faster each time.",
            "ru": "Один и тот же угол проходится трижды, каждый раз быстрее.",
        },
        "opens": {"en": "Already mid-run: no setup, no establishing.",
                  "ru": "Уже на бегу: без завязки и без заявочного плана."},
        "closes": {"en": "A dead stop, breathing, holding the frame.",
                   "ru": "Резкая остановка, дыхание, кадр держится."},
        "acts": [
            {"key": "go", "share": 0.25, "shot": "medium",
             "label": {"en": "Go", "ru": "Побежал"},
             "en": "In medias res. Short locked frames with fast motion inside them.",
             "ru": "С места в карьер. Короткие статичные кадры с быстрым движением внутри.",
             "boards": ["open_mid_action", "action_run_toward", "travel_stairs_down"]},
            {"key": "block", "share": 0.3, "shot": "wide",
             "label": {"en": "The block", "ru": "Квартал"},
             "en": "Geography at speed: fences, stairs, courtyards, one after another.",
             "ru": "География на скорости: заборы, лестницы, дворы — одно за другим.",
             "boards": ["travel_side_track", "bridge_body_wipe", "crowd_part_for_hero"]},
            {"key": "peak", "share": 0.3, "shot": "close-up",
             "label": {"en": "Peak", "ru": "Пик"},
             "en": "Shortest scenes of the clip. Impact, jump, landing, faces.",
             "ru": "Самые короткие сцены клипа. Удар, прыжок, приземление, лица.",
             "boards": ["action_jump_land", "action_impact_stop", "crowd_hands_up"]},
            {"key": "stop", "share": 0.15, "shot": "extreme close-up",
             "label": {"en": "Dead stop", "ru": "Стоп"},
             "en": "Everything stops at once. Let the last frame breathe.",
             "ru": "Всё останавливается разом. Дать последнему кадру продышаться.",
             "boards": ["final_last_look"]},
        ],
        "scenes": {"min": 24, "typ": 40, "max": 60},
        "open_board": "open_mid_action", "close_board": "final_last_look",
        "styles_fit": ["punkrf", "spike", "munir", "noir", "cinema"],
        "preset": "",
        "story": "Один непрерывный забег героя через один квартал, снятый как погоня без преследователя. Клип "
                 "открывается уже на бегу: ни завязки, ни заявочного плана, первый кадр застаёт движение в середине. "
                 "Второй акт проходит географию на скорости — лестницы, заборы, дворы, подворотни, — и каждая "
                 "локация держится ровно столько, чтобы её успели прочитать. Третий акт даёт пик: прыжок, "
                 "приземление, удар в стену, лица встречных, самые короткие сцены во всём клипе. Финал — резкая "
                 "полная остановка и один длинный кадр дыхания после неё, вдвое длиннее всех остальных. Сквозной "
                 "мотив — один и тот же угол или проём, который герой проходит трижды, каждый раз быстрее.",
        "dnote": "Быстрый монтаж не терпит движущейся камеры: на пиковых сценах камера стоит, а бежит герой. Движение "
                 "камеры оставь второму акту.",
        "slots_hint": ["character", "location", "crowd", "time"],
        "tags": ["menacing", "saturated", "street"],
        "needs_lyrics": False,
    },
    {
        "key": "cold_call",
        "tier": "free",
        "label": {"en": "The Call", "ru": "Звонок"},
        "music": {
            "en": "Dark mid-tempo, 90-115 BPM, one hook repeated, tension over melody.",
            "ru": "Тёмный средний темп, 90–115 BPM, один повторяющийся хук, напряжение вместо мелодии.",
        },
        "bpm": [90, 115], "cut": "mid",
        "logline": {
            "en": "One phone call splits the night into before and after.",
            "ru": "Один звонок делит ночь на до и после.",
        },
        "hero": {
            "en": "The one who answers. Everything is measured against his face before and after.",
            "ru": "Тот, кто взял трубку. Всё меряется его лицом до и после.",
        },
        "motif": {
            "en": "The lit phone screen returns in every act, dimmer each time.",
            "ru": "Светящийся экран телефона возвращается в каждом акте, каждый раз тусклее.",
        },
        "opens": {"en": "An ordinary frame that will be re-read after the call.",
                  "ru": "Обычный кадр, который после звонка прочитается иначе."},
        "closes": {"en": "The same ordinary frame, nothing changed except him.",
                   "ru": "Тот же обычный кадр: не изменилось ничего, кроме него."},
        "acts": [
            {"key": "before", "share": 0.25, "shot": "medium",
             "label": {"en": "Before", "ru": "До"},
             "en": "Calm, warm, unremarkable. Earn the contrast.",
             "ru": "Спокойно, тепло, ничем не примечательно. Заработать контраст.",
             "boards": ["interior_table_sit", "detail_hands_work", "portrait_hold_still"]},
            {"key": "call", "share": 0.2, "shot": "extreme close-up",
             "label": {"en": "The call", "ru": "Звонок"},
             "en": "Screen, hand, face. Three scenes, no more.",
             "ru": "Экран, рука, лицо. Три сцены, не больше.",
             "boards": ["night_screen_face", "detail_pocket_reveal", "portrait_breath_break"]},
            {"key": "after", "share": 0.35, "shot": "wide",
             "label": {"en": "After", "ru": "После"},
             "en": "He goes out. The same city reads hostile with no change of location.",
             "ru": "Он выходит. Тот же город читается враждебно без смены локации.",
             "boards": ["night_lamp_pass", "night_headlights", "travel_walk_away"]},
            {"key": "return", "share": 0.2, "shot": "medium",
             "label": {"en": "Back", "ru": "Обратно"},
             "en": "Return to the opening frame, changed. Say nothing about what was said.",
             "ru": "Возврат в первый кадр, изменившимся. О содержании звонка не говорим.",
             "boards": ["final_rhyme_open", "interior_window_side"]},
        ],
        "scenes": {"min": 14, "typ": 28, "max": 38},
        "open_board": "interior_table_sit", "close_board": "final_rhyme_open",
        "styles_fit": ["noir", "cinema", "dreamclad", "katsumi", "longheads"],
        "preset": "",
        "story": "Один телефонный звонок делит ночь героя на до и после, и содержание звонка зрителю не сообщается "
                 "никогда. Первый акт намеренно скучный и тёплый: кухня, руки, чай, обычный вечер — этот акт "
                 "существует только для того, чтобы заработать контраст. Второй акт занимает три сцены: экран, рука, "
                 "лицо. Третий акт выводит героя на улицу, и та же самая ночная география, снятая теми же ракурсами, "
                 "должна читаться враждебно — меняется не место, а свет, крупность и скорость. Финал возвращает "
                 "первый кадр первого акта: та же кухня, та же композиция, тот же свет, изменился только человек в "
                 "ней. Сквозной мотив — светящийся экран телефона, возвращающийся в каждом акте и каждый раз тусклее.",
        "dnote": "Ни одного кадра с собеседником и ни одной надписи на экране. Всё, что зритель знает о звонке, он "
                 "читает по лицу и по тому, как изменился темп монтажа.",
        "slots_hint": ["character", "location", "prop", "time"],
        "tags": ["menacing", "cold", "night"],
        "needs_lyrics": False,
    },
    {
        "key": "last_train",
        "tier": "free",
        "label": {"en": "Last Train", "ru": "Последний поезд"},
        "music": {
            "en": "Melodic rap and sung hooks, 75-100 BPM, reverb-heavy, sad but not slow.",
            "ru": "Мелодичный рэп и спетые хуки, 75–100 BPM, много реверба, грустно, но не медленно.",
        },
        "bpm": [75, 100], "cut": "mid",
        "logline": {
            "en": "A journey with no destination stated. He rides until the line ends.",
            "ru": "Поездка без объявленной цели. Он едет, пока не кончится ветка.",
        },
        "hero": {
            "en": "A passenger — the only role where doing nothing reads as a decision.",
            "ru": "Пассажир — единственная роль, в которой бездействие читается как решение.",
        },
        "motif": {
            "en": "The window: the same framing, a different landscape, four times.",
            "ru": "Окно: одна и та же рамка, другой пейзаж, четыре раза.",
        },
        "opens": {"en": "The platform, empty, before the train.",
                  "ru": "Платформа, пустая, до поезда."},
        "closes": {"en": "The last station, doors open, he does not get off.",
                   "ru": "Конечная, двери открыты, он не выходит."},
        "acts": [
            {"key": "board", "share": 0.2, "shot": "wide",
             "label": {"en": "Boarding", "ru": "Посадка"},
             "en": "Platform, doors, choosing a seat. Establish the geometry.",
             "ru": "Платформа, двери, выбор места. Заявляем геометрию.",
             "boards": ["open_empty_place", "crowd_queue_line", "interior_doorframe"]},
            {"key": "ride", "share": 0.35, "shot": "medium",
             "label": {"en": "The ride", "ru": "Дорога"},
             "en": "Window, reflection, other passengers. The rhythm of the carriage.",
             "ru": "Окно, отражение, другие пассажиры. Ритм вагона.",
             "boards": ["travel_car_window", "interior_mirror", "crowd_one_still"]},
            {"key": "empty", "share": 0.3, "shot": "close-up",
             "label": {"en": "Emptying out", "ru": "Пустеет"},
             "en": "Passengers leave one by one until he is alone. Nobody comments on it.",
             "ru": "Пассажиры выходят один за другим, пока он не остаётся один. Это не комментируется.",
             "boards": ["portrait_hold_still", "portrait_profile_to_front", "detail_texture_macro"]},
            {"key": "end", "share": 0.15, "shot": "establishing",
             "label": {"en": "End of line", "ru": "Конечная"},
             "en": "Doors open onto nothing in particular and stay open.",
             "ru": "Двери открываются в никуда и остаются открытыми.",
             "boards": ["final_walk_out_frame", "final_pull_to_wide"]},
        ],
        "scenes": {"min": 14, "typ": 26, "max": 36},
        "open_board": "open_empty_place", "close_board": "final_pull_to_wide",
        "styles_fit": ["shinkai", "cinema", "katsumi", "longheads", "dreamclad"],
        "preset": "long_drive",
        "story": "Герой садится в поезд и едет до конечной, и цель поездки зрителю не сообщается. Первый акт заявляет "
                 "геометрию: пустая платформа, двери, выбор места — всё общими и средними планами. Второй акт живёт "
                 "ритмом вагона: окно с меняющимся пейзажем, отражение героя в стекле поверх этого пейзажа, чужие "
                 "пассажиры, которых мы никогда не показываем крупно. Третий акт медленно опустошает вагон — люди "
                 "выходят по одному, и это никак не комментируется, пока герой не остаётся один. Финал: конечная "
                 "станция, двери открыты, за ними ничего примечательного, герой не выходит. Сквозной мотив — окно: "
                 "одна и та же рамка кадра с четырьмя разными пейзажами, от города к пустоте.",
        "dnote": "Окно снимай ровно одной и той же композицией все четыре раза, иначе мотив не прочитается. "
                 "Пассажиров держи в расфокусе и не давай им лиц.",
        "slots_hint": ["character", "location", "crowd", "time"],
        "tags": ["melancholy", "cold", "nostalgic"],
        "needs_lyrics": False,
    },
    {
        "key": "the_offer",
        "tier": "pro",
        "label": {"en": "The Offer", "ru": "Предложение"},
        "music": {
            "en": "Menacing mid-tempo, 90-110 BPM, low end, sparse arrangement, spoken-word feel.",
            "ru": "Угрожающий средний темп, 90–110 BPM, низ, разреженная аранжировка, почти речитатив.",
        },
        "bpm": [90, 110], "cut": "mid",
        "logline": {
            "en": "Someone offers him something. The clip never shows what, only the price.",
            "ru": "Ему что-то предлагают. Клип не показывает что — только цену.",
        },
        "hero": {
            "en": "The one being tested. He is looked at more than he looks.",
            "ru": "Тот, кого испытывают. На него смотрят чаще, чем смотрит он.",
        },
        "motif": {
            "en": "An outstretched hand, three times, never taken in frame.",
            "ru": "Протянутая рука, трижды, и ни разу не пожатая в кадре.",
        },
        "opens": {"en": "Two chairs, one occupied.",
                  "ru": "Два стула, занят один."},
        "closes": {"en": "One chair pushed back, the room empty.",
                   "ru": "Отодвинутый стул, комната пустая."},
        "acts": [
            {"key": "room", "share": 0.2, "shot": "wide",
             "label": {"en": "The room", "ru": "Комната"},
             "en": "Symmetry and ceremony. The space does the threatening.",
             "ru": "Симметрия и церемония. Угрожает пространство.",
             "boards": ["open_empty_place", "interior_doorframe", "interior_table_sit"]},
            {"key": "offer", "share": 0.3, "shot": "close-up",
             "label": {"en": "The offer", "ru": "Предложение"},
             "en": "Hands, objects, the other party seen only in fragments.",
             "ru": "Руки, предметы, вторая сторона — только фрагментами.",
             "boards": ["detail_object_pickup", "detail_pocket_reveal", "portrait_two_shot"]},
            {"key": "weigh", "share": 0.3, "shot": "extreme close-up",
             "label": {"en": "Weighing it", "ru": "Взвешивает"},
             "en": "The longest scenes in the clip. Nothing moves except the face.",
             "ru": "Самые длинные сцены клипа. Не двигается ничего, кроме лица.",
             "boards": ["portrait_hold_still", "portrait_breath_break", "interior_mirror"]},
            {"key": "answer", "share": 0.2, "shot": "medium",
             "label": {"en": "The answer", "ru": "Ответ"},
             "en": "He leaves or he stays. Do not show which by dialogue — show it by light.",
             "ru": "Он уходит или остаётся. Показать это не репликой, а светом.",
             "boards": ["final_walk_out_frame", "final_last_look"]},
        ],
        "scenes": {"min": 12, "typ": 24, "max": 34},
        "open_board": "open_empty_place", "close_board": "final_walk_out_frame",
        "styles_fit": ["noir", "cinema", "munir", "fanuel", "longheads"],
        "preset": "the_trial",
        "story": "Герою делают предложение, и зритель никогда не узнаёт какое: клип показывает не суть сделки, а её "
                 "цену. Первый акт заявляет комнату — симметричную, церемониальную, слишком большую для двоих; "
                 "угрожает здесь пространство, а не человек. Второй акт даёт саму сделку через руки и предметы: "
                 "вторая сторона существует только фрагментами — плечо, манжета, ладонь, — и лицо её не показывается "
                 "ни разу. Третий акт целиком про взвешивание: самые длинные планы клипа, где не двигается ничего, "
                 "кроме лица героя. Финал — ответ, показанный не репликой и не жестом, а сменой света в комнате и "
                 "тем, остаётся ли герой в кадре. Сквозной мотив — протянутая рука, появляющаяся трижды и ни разу не "
                 "пожатая в кадре.",
        "dnote": "Второй персонаж не должен получить ни одного узнаваемого кадра лица — это ломает приём. Камера "
                 "почти неподвижна во всех актах, кроме финального.",
        "slots_hint": ["character", "location", "prop", "emotion"],
        "tags": ["menacing", "muted", "deadpan"],
        "needs_lyrics": True,
    },
    {
        "key": "market_day",
        "tier": "free",
        "label": {"en": "Market Day", "ru": "Базарный день"},
        "music": {
            "en": "Warm, sample-driven, 90-105 BPM, brass or strings in the loop, upbeat but not hyper.",
            "ru": "Тёплое семплированное, 90–105 BPM, духовые или струнные в петле, бодро, но не разгонно.",
        },
        "bpm": [90, 105], "cut": "mid",
        "logline": {
            "en": "One day at a crowded market, told through hands and goods rather than faces.",
            "ru": "Один день на рынке, рассказанный руками и товаром, а не лицами.",
        },
        "hero": {
            "en": "Part of the crowd, not above it. The clip earns his close-up only once.",
            "ru": "Часть толпы, а не над ней. Крупный план он получает ровно один раз.",
        },
        "motif": {
            "en": "Money changing hands, shot the same way each time.",
            "ru": "Деньги переходят из рук в руки, снятые каждый раз одинаково.",
        },
        "opens": {"en": "Empty stalls before opening.",
                  "ru": "Пустые прилавки до открытия."},
        "closes": {"en": "The same stalls, packed up, litter on the ground.",
                   "ru": "Те же прилавки, свёрнутые, мусор на земле."},
        "acts": [
            {"key": "setup", "share": 0.2, "shot": "establishing",
             "label": {"en": "Setting up", "ru": "Разворачиваются"},
             "en": "Before the crowd: crates, tarpaulins, first light.",
             "ru": "До толпы: ящики, тенты, первый свет.",
             "boards": ["open_empty_place", "detail_hands_work", "interior_doorframe"]},
            {"key": "peak", "share": 0.35, "shot": "medium",
             "label": {"en": "Full flow", "ru": "Поток"},
             "en": "Crowd density as the subject. Cut on movement, not on faces.",
             "ru": "Плотность толпы как содержание. Режем по движению, а не по лицам.",
             "boards": ["crowd_part_for_hero", "crowd_one_still", "travel_side_track"]},
            {"key": "trade", "share": 0.25, "shot": "close-up",
             "label": {"en": "The trade", "ru": "Сделка"},
             "en": "Goods, hands, money, weight. One close-up of the hero, and only one.",
             "ru": "Товар, руки, деньги, вес. Один крупный план героя — и только один.",
             "boards": ["detail_object_pickup", "detail_texture_macro", "portrait_hold_still"]},
            {"key": "after", "share": 0.2, "shot": "wide",
             "label": {"en": "After", "ru": "После"},
             "en": "The same wide as the opening, emptied.",
             "ru": "Тот же общий, что в начале, только опустевший.",
             "boards": ["final_rhyme_open", "final_pull_to_wide"]},
        ],
        "scenes": {"min": 16, "typ": 30, "max": 42},
        "open_board": "open_empty_place", "close_board": "final_rhyme_open",
        "styles_fit": ["ghibli", "cinema", "clay", "longheads", "katsumi"],
        "preset": "",
        "story": "Один день на большом рынке от разворачивания прилавков до вывоза мусора, рассказанный руками и "
                 "товаром, а не лицами. Первый акт снимает рынок до толпы: ящики, тенты, первый свет, единичные "
                 "фигуры в огромном пустом пространстве. Второй акт живёт плотностью: толпа как главный герой, "
                 "монтаж режется по движению внутри кадра, а не по лицам, ни одного портрета. Третий акт спускается "
                 "к сделке — товар, руки, вес, деньги — и здесь герой получает свой единственный крупный план за "
                 "весь клип, ровно один. Финал повторяет композицию первого акта: те же прилавки, свёрнутые, тот же "
                 "ракурс, мусор на земле. Сквозной мотив — деньги, переходящие из рук в руки, снятые каждый раз "
                 "одинаковым кадром.",
        "dnote": "Ни одного кадра толпы с числом людей меньше десяти: разреженная массовка убивает приём. Лица в "
                 "толпе держи в расфокусе.",
        "slots_hint": ["character", "location", "crowd", "object"],
        "tags": ["warm", "playful", "crowd"],
        "needs_lyrics": False,
    },
    {
        "key": "factory_hymn",
        "tier": "pro",
        "label": {"en": "Factory Hymn", "ru": "Гимн цеха"},
        "music": {
            "en": "Industrial, phonk, hard electronic, 100-140 BPM, mechanical loop, little melody.",
            "ru": "Индастриал, фонк, жёсткая электроника, 100–140 BPM, механическая петля, мало мелодии.",
        },
        "bpm": [100, 140], "cut": "fast",
        "logline": {
            "en": "Machines set the tempo and the human keeps up. Then he stops keeping up.",
            "ru": "Темп задают машины, человек успевает. Потом перестаёт успевать.",
        },
        "hero": {
            "en": "One human among mechanisms — the only thing in frame that can get tired.",
            "ru": "Один человек среди механизмов — единственное в кадре, что умеет уставать.",
        },
        "motif": {
            "en": "One repeating mechanical movement, four times, at four framings.",
            "ru": "Одно повторяющееся движение механизма, четыре раза, в четырёх крупностях.",
        },
        "opens": {"en": "A machine part in extreme close-up, moving on its own.",
                  "ru": "Деталь механизма макро, движется сама."},
        "closes": {"en": "The machine still moving, the human gone from frame.",
                   "ru": "Механизм ещё движется, человека в кадре уже нет."},
        "acts": [
            {"key": "machine", "share": 0.25, "shot": "extreme close-up",
             "label": {"en": "The machine", "ru": "Механизм"},
             "en": "Mechanisms before people. Establish the tempo the clip will obey.",
             "ru": "Сначала механизмы, потом люди. Заявляем темп, которому подчинится клип.",
             "boards": ["detail_texture_macro", "detail_hands_work", "open_detail_first"]},
            {"key": "keeping", "share": 0.3, "shot": "medium",
             "label": {"en": "Keeping up", "ru": "Успевает"},
             "en": "Human motion matched to machine rhythm. Cutting on the beat is allowed here.",
             "ru": "Движение человека подогнано под ритм машины. Здесь резать в долю можно.",
             "boards": ["action_impact_stop", "portrait_hold_still", "travel_corridor_push"]},
            {"key": "falling", "share": 0.3, "shot": "close-up",
             "label": {"en": "Falling behind", "ru": "Отстаёт"},
             "en": "The human slows, the machine does not. Contrast in speed, not in cutting.",
             "ru": "Человек замедляется, машина нет. Контраст в скорости, а не в монтаже.",
             "boards": ["portrait_breath_break", "action_fall_back", "night_screen_face"]},
            {"key": "on", "share": 0.15, "shot": "wide",
             "label": {"en": "It keeps going", "ru": "Оно продолжает"},
             "en": "Empty station, machine still running.",
             "ru": "Пустое рабочее место, машина работает.",
             "boards": ["final_walk_out_frame"]},
        ],
        "scenes": {"min": 20, "typ": 34, "max": 48},
        "open_board": "open_detail_first", "close_board": "final_walk_out_frame",
        "styles_fit": ["punkrf", "noir", "munir", "cinema", "spike"],
        "preset": "",
        "story": "Клип о человеке, который держит темп механизма, пока не перестаёт его держать. Первый акт "
                 "показывает только механизмы, без людей: детали макро, повторяющиеся движения, шестерни, ленты — "
                 "здесь задаётся темп, которому подчинится весь монтаж. Второй акт вводит человека и подгоняет его "
                 "движения под ритм машины: именно в этом акте разрешено резать ровно в долю, потому что "
                 "механичность и есть смысл. Третий акт разводит их скорости — человек замедляется, машина нет; "
                 "контраст создаётся не темпом монтажа, а движением внутри кадра. Финал: пустое рабочее место, "
                 "механизм продолжает работать сам. Сквозной мотив — одно повторяющееся движение механизма, снятое "
                 "четыре раза в четырёх разных крупностях.",
        "dnote": "Ни одного кадра, где видно лицо и механизм одновременно резко: либо человек, либо машина в фокусе. "
                 "Никакой символики протеста, только физика усталости.",
        "slots_hint": ["character", "location", "prop", "accent"],
        "tags": ["menacing", "cold", "grain"],
        "needs_lyrics": False,
    },
    {
        "key": "sea_line",
        "tier": "free",
        "label": {"en": "Sea Line", "ru": "До моря"},
        "music": {
            "en": "Warm melodic, sung chorus, 80-105 BPM, open arrangement, hopeful.",
            "ru": "Тёплое мелодичное, спетый припев, 80–105 BPM, открытая аранжировка, светлое.",
        },
        "bpm": [80, 105], "cut": "slow",
        "logline": {
            "en": "A road that ends at water. The destination is known from the first frame.",
            "ru": "Дорога, которая кончается водой. Пункт назначения известен с первого кадра.",
        },
        "hero": {
            "en": "A traveller who is already sure. No doubt, no obstacle — only distance.",
            "ru": "Путник, который уже уверен. Ни сомнения, ни препятствия — только расстояние.",
        },
        "motif": {
            "en": "The horizon line sits at the same height in every wide shot.",
            "ru": "Линия горизонта стоит на одной высоте во всех общих планах.",
        },
        "opens": {"en": "A road sign or a road, and nothing else.",
                  "ru": "Дорога или указатель — и больше ничего."},
        "closes": {"en": "Water filling the frame, the character small at the edge.",
                   "ru": "Вода на весь кадр, герой мелкий у края."},
        "acts": [
            {"key": "leave", "share": 0.25, "shot": "wide",
             "label": {"en": "Setting out", "ru": "Выезд"},
             "en": "Leaving the built-up world. Every scene has more sky than the last.",
             "ru": "Выход из застроенного мира. В каждой сцене неба больше, чем в предыдущей.",
             "boards": ["open_door_out", "travel_car_window", "travel_walk_away"]},
            {"key": "road", "share": 0.3, "shot": "establishing",
             "label": {"en": "The road", "ru": "Дорога"},
             "en": "Distance as content. Long scenes, slow moves, empty space.",
             "ru": "Расстояние как содержание. Длинные сцены, медленные движения, пустота.",
             "boards": ["travel_side_track", "bridge_light_to_dark", "portrait_profile_to_front"]},
            {"key": "near", "share": 0.25, "shot": "medium",
             "label": {"en": "Getting close", "ru": "Ближе"},
             "en": "Signs of water before water: wind, light, salt, birds.",
             "ru": "Признаки воды до воды: ветер, свет, соль, птицы.",
             "boards": ["detail_texture_macro", "portrait_hold_still", "night_rain_reflect"]},
            {"key": "water", "share": 0.2, "shot": "establishing",
             "label": {"en": "Water", "ru": "Вода"},
             "en": "The arrival, held longer than comfortable.",
             "ru": "Прибытие, которое держится дольше, чем удобно.",
             "boards": ["final_pull_to_wide", "final_walk_out_frame"]},
        ],
        "scenes": {"min": 12, "typ": 24, "max": 34},
        "open_board": "open_door_out", "close_board": "final_pull_to_wide",
        "styles_fit": ["shinkai", "ghibli", "cinema", "dreamclad", "clay"],
        "preset": "long_drive",
        "story": "Дорога героя до моря, где цель известна с первого кадра и препятствий нет вообще: драматургия "
                 "держится расстоянием, а не конфликтом. Первый акт выводит героя из застроенного мира, и в каждой "
                 "следующей сцене неба в кадре больше, чем в предыдущей — это правило соблюдается буквально. Второй "
                 "акт про расстояние: длинные планы, медленные движения камеры, пустое пространство, минимум "
                 "событий. Третий акт даёт признаки воды до самой воды — ветер в траве, изменившийся свет, соль на "
                 "стекле, птицы, — и герой начинает торопиться. Финал — приезд, снятый одним планом, который "
                 "держится дольше, чем комфортно. Сквозной мотив — линия горизонта, стоящая на одной и той же высоте "
                 "во всех общих планах клипа.",
        "dnote": "Никакой драмы в дороге: ни поломок, ни погони, ни сомнений. Единственный источник напряжения — "
                 "сколько ещё осталось.",
        "slots_hint": ["character", "location", "vehicle", "time"],
        "tags": ["nostalgic", "warm", "nature"],
        "needs_lyrics": False,
    },
    {
        "key": "yard_saints",
        "tier": "pro",
        "label": {"en": "Saints of the Yard", "ru": "Святые двора"},
        "music": {
            "en": "Soulful boom bap or gospel-sampled rap, 85-95 BPM, choir in the hook.",
            "ru": "Душевный бумбап или рэп с госпел-семплом, 85–95 BPM, хор в припеве.",
        },
        "bpm": [85, 95], "cut": "mid",
        "logline": {
            "en": "The courtyard of childhood shot with the reverence usually reserved for churches.",
            "ru": "Двор детства, снятый с почтением, которое обычно оставляют для храмов.",
        },
        "hero": {
            "en": "A witness. He remembers the place; the place does not remember him.",
            "ru": "Свидетель. Он помнит место, место его — нет.",
        },
        "motif": {
            "en": "Light through a gap — a stairwell window, a gate, branches — repeated as a rhyme.",
            "ru": "Свет в проёме — окно подъезда, арка, ветки — повторяется рифмой.",
        },
        "opens": {"en": "A low shot of a doorway with light behind it.",
                  "ru": "Нижний кадр проёма со светом за ним."},
        "closes": {"en": "The same doorway with nobody in it.",
                   "ru": "Тот же проём, и в нём никого."},
        "acts": [
            {"key": "place", "share": 0.25, "shot": "wide",
             "label": {"en": "The yard", "ru": "Двор"},
             "en": "Symmetry and centred composition. Treat concrete as architecture.",
             "ru": "Симметрия и центр. Обращайся с бетоном как с архитектурой.",
             "boards": ["open_empty_place", "interior_doorframe", "bridge_light_to_dark"]},
            {"key": "people", "share": 0.3, "shot": "medium",
             "label": {"en": "The people", "ru": "Люди"},
             "en": "Portraits of everyone else, held longer than portraits of the hero.",
             "ru": "Портреты всех остальных, и они держатся дольше портретов героя.",
             "boards": ["portrait_two_shot", "crowd_one_still", "portrait_profile_to_front"]},
            {"key": "then", "share": 0.25, "shot": "close-up",
             "label": {"en": "What is gone", "ru": "Чего нет"},
             "en": "Absence filmed as presence: worn steps, marks, empty benches.",
             "ru": "Отсутствие, снятое как присутствие: стёртые ступени, метки, пустые скамейки.",
             "boards": ["detail_written_trace", "detail_texture_macro", "interior_mirror"]},
            {"key": "amen", "share": 0.2, "shot": "establishing",
             "label": {"en": "Amen", "ru": "Аминь"},
             "en": "The opening frame again, empty, with the light moved.",
             "ru": "Первый кадр снова, пустой, свет сместился.",
             "boards": ["final_rhyme_open"]},
        ],
        "scenes": {"min": 14, "typ": 28, "max": 38},
        "open_board": "open_empty_place", "close_board": "final_rhyme_open",
        "styles_fit": ["fanuel", "cinema", "longheads", "munir", "embroidery"],
        "preset": "",
        "story": "Двор детства героя, снятый с почтением, которое обычно оставляют для храмов: симметрия, центр, "
                 "нижние ракурсы, свет в проёмах. Первый акт заявляет место как архитектуру — бетон, лестницы, арки "
                 "и трансформаторные будки снимаются фронтально и симметрично, без иронии и без чернухи. Второй акт "
                 "отдан портретам всех остальных: соседи, дети, старики, и каждый такой портрет держится в монтаже "
                 "дольше, чем портрет самого героя. Третий акт снимает отсутствие как присутствие — стёртые ступени, "
                 "процарапанные метки, пустые скамейки, — и здесь становится ясно, что герой помнит место, а место "
                 "его нет. Финал повторяет первый кадр, но в проёме уже никого и свет сместился. Сквозной мотив — "
                 "свет в проёме: окно подъезда, арка, ветки над головой.",
        "dnote": "Ни одного кадра сверху вниз на людей — только фронтально или снизу. Ни одной надписи и ни одного "
                 "документального «социального» ракурса.",
        "slots_hint": ["character", "location", "crowd", "detail"],
        "tags": ["sacred", "nostalgic", "warm"],
        "needs_lyrics": True,
    },
    {
        "key": "black_car",
        "tier": "pro",
        "label": {"en": "Black Car", "ru": "Чёрная машина"},
        "music": {
            "en": "Cold trap, 120-140 BPM, sub bass, few elements, threatening space.",
            "ru": "Холодный трэп, 120–140 BPM, саб, мало элементов, угрожающее пространство.",
        },
        "bpm": [120, 140], "cut": "mid",
        "logline": {
            "en": "A car follows him all night. It never catches up and never leaves.",
            "ru": "Машина едет за ним всю ночь. Она не догоняет и не отстаёт.",
        },
        "hero": {
            "en": "The pursued, who refuses to run. His pace is the whole performance.",
            "ru": "Преследуемый, который отказывается бежать. Его шаг и есть вся игра.",
        },
        "motif": {
            "en": "Headlights entering the frame from behind, three times, closer each time.",
            "ru": "Свет фар входит в кадр сзади трижды, каждый раз ближе.",
        },
        "opens": {"en": "An empty street with headlights far away.",
                  "ru": "Пустая улица, фары далеко."},
        "closes": {"en": "The car stopped, the doors closed, nobody gets out.",
                   "ru": "Машина остановилась, двери закрыты, из неё никто не выходит."},
        "acts": [
            {"key": "notice", "share": 0.25, "shot": "wide",
             "label": {"en": "Noticing", "ru": "Замечает"},
             "en": "He is not sure yet. Keep the car out of focus and out of the centre.",
             "ru": "Он ещё не уверен. Держим машину в расфокусе и не в центре.",
             "boards": ["night_lamp_pass", "travel_walk_away", "night_headlights"]},
            {"key": "sure", "share": 0.3, "shot": "medium",
             "label": {"en": "Certain", "ru": "Убедился"},
             "en": "Now he knows. Same route, tighter framings, faster steps.",
             "ru": "Теперь он знает. Тот же маршрут, теснее кадры, быстрее шаг.",
             "boards": ["travel_stairs_down", "bridge_body_wipe", "portrait_breath_break"]},
            {"key": "test", "share": 0.25, "shot": "close-up",
             "label": {"en": "Testing it", "ru": "Проверяет"},
             "en": "He stops on purpose. The car stops too. Hold both stills.",
             "ru": "Он нарочно останавливается. Машина тоже. Держим обе статики.",
             "boards": ["portrait_hold_still", "action_impact_stop", "night_rain_reflect"]},
            {"key": "still", "share": 0.2, "shot": "extreme close-up",
             "label": {"en": "Nobody gets out", "ru": "Никто не выходит"},
             "en": "The threat stays unresolved on purpose.",
             "ru": "Угроза намеренно остаётся неразрешённой.",
             "boards": ["final_last_look", "final_pull_to_wide"]},
        ],
        "scenes": {"min": 16, "typ": 30, "max": 42},
        "open_board": "night_lamp_pass", "close_board": "final_pull_to_wide",
        "styles_fit": ["noir", "dreamclad", "cinema", "punkrf", "munir"],
        "preset": "",
        "story": "Всю ночь за героем едет машина: она не догоняет и не отстаёт, и клип ни разу не показывает, кто "
                 "внутри. Первый акт держит неопределённость: машина в расфокусе, не в центре кадра, герой ещё не "
                 "уверен, что она за ним. Второй акт снимает тот же маршрут теснее и быстрее — крупности "
                 "сокращаются, шаг ускоряется, но герой не бежит ни разу за весь клип. Третий акт — проверка: герой "
                 "нарочно останавливается, машина останавливается тоже, и две статики держатся рядом дольше, чем "
                 "комфортно. Финал: машина стоит, двери закрыты, из неё никто не выходит, угроза намеренно остаётся "
                 "неразрешённой. Сквозной мотив — свет фар, входящий в кадр из-за спины героя трижды и каждый раз "
                 "ближе.",
        "dnote": "Ни одного кадра салона и ни одного лица водителя. Герой не бежит: как только он побежал, приём "
                 "сломался и клип стал обычной погоней.",
        "slots_hint": ["character", "location", "vehicle", "time"],
        "tags": ["menacing", "night", "cold"],
        "needs_lyrics": False,
    },
    {
        "key": "stage_and_after",
        "tier": "free",
        "label": {"en": "Stage and After", "ru": "Сцена и после"},
        "music": {
            "en": "Live-energy tracks, 95-125 BPM, big hook, crowd-ready.",
            "ru": "Треки с концертной энергией, 95–125 BPM, большой хук, под толпу.",
        },
        "bpm": [95, 125], "cut": "fast",
        "logline": {
            "en": "Twenty minutes of being everything, then a corridor where nobody looks at you.",
            "ru": "Двадцать минут быть всем — и коридор, где на тебя никто не смотрит.",
        },
        "hero": {
            "en": "The performer, filmed twice: as a silhouette on stage and as a person after.",
            "ru": "Артист, снятый дважды: силуэтом на сцене и человеком после.",
        },
        "motif": {
            "en": "A towel, a bottle or a jacket carried through both halves.",
            "ru": "Полотенце, бутылка или куртка, проходящие через обе половины.",
        },
        "opens": {"en": "The dark before the first light cue.",
                  "ru": "Темнота до первой засветки."},
        "closes": {"en": "An empty room with the sound still in his ears.",
                   "ru": "Пустое помещение, звук ещё в ушах."},
        "acts": [
            {"key": "dark", "share": 0.15, "shot": "extreme close-up",
             "label": {"en": "Before", "ru": "До"},
             "en": "Hands, breath, dark. Two or three scenes at most.",
             "ru": "Руки, дыхание, темнота. Две-три сцены максимум.",
             "boards": ["open_from_black", "detail_hands_work"]},
            {"key": "on", "share": 0.35, "shot": "wide",
             "label": {"en": "On stage", "ru": "На сцене"},
             "en": "Backlight, crowd, silhouettes. The shortest cuts of the clip.",
             "ru": "Контровой, толпа, силуэты. Самые короткие склейки клипа.",
             "boards": ["crowd_hands_up", "night_neon_wall", "action_run_toward"]},
            {"key": "off", "share": 0.3, "shot": "medium",
             "label": {"en": "Off stage", "ru": "За сценой"},
             "en": "Corridors and flat light. Everything slows to half speed.",
             "ru": "Коридоры и ровный свет. Всё замедляется вдвое.",
             "boards": ["travel_corridor_push", "portrait_breath_break", "interior_mirror"]},
            {"key": "empty", "share": 0.2, "shot": "establishing",
             "label": {"en": "Empty room", "ru": "Пустой зал"},
             "en": "The same wide as the show, with nobody in it.",
             "ru": "Тот же общий, что и на концерте, и в нём никого.",
             "boards": ["final_pull_to_wide", "final_walk_out_frame"]},
        ],
        "scenes": {"min": 18, "typ": 34, "max": 46},
        "open_board": "open_from_black", "close_board": "final_pull_to_wide",
        "styles_fit": ["cinema", "punkrf", "noir", "spike", "munir"],
        "preset": "from_the_crowd",
        "story": "Клип из двух неравных половин: концерт и то, что сразу после него. Первый акт — темнота перед "
                 "выходом: руки, дыхание, две-три сцены, ни одного общего плана. Второй акт снимает сцену только "
                 "контровым светом и только через толпу: артист существует силуэтом, лицо почти не читается, "
                 "склейки самые короткие во всём клипе. Третий акт выключает музыку визуально — коридоры, ровный "
                 "служебный свет, полотенце, вода, зеркало в гримёрке, — и всё замедляется вдвое, планы удлиняются. "
                 "Финал: тот же общий план зала, что был на концерте, только пустой. Сквозной мотив — один предмет "
                 "(полотенце, бутылка, куртка), проходящий через обе половины клипа.",
        "dnote": "На сцене — ни одного чистого портрета артиста: только силуэт и фрагменты. Все портреты отдай "
                 "третьему акту, там они и сработают.",
        "slots_hint": ["character", "location", "crowd", "prop"],
        "tags": ["epic", "neon", "crowd"],
        "needs_lyrics": False,
    },
    {
        "key": "mirror_year",
        "tier": "pro",
        "label": {"en": "A Year in the Mirror", "ru": "Год в зеркале"},
        "music": {
            "en": "Reflective mid-tempo, 80-100 BPM, verse-driven, one repeated line.",
            "ru": "Рефлексивный средний темп, 80–100 BPM, куплетный, одна повторяющаяся строка.",
        },
        "bpm": [80, 100], "cut": "slow",
        "logline": {
            "en": "The same mirror, four seasons, one person changing in it.",
            "ru": "Одно зеркало, четыре сезона, один человек, который в нём меняется.",
        },
        "hero": {
            "en": "Both the subject and the observer — he is the only witness of his own change.",
            "ru": "И объект, и наблюдатель: он единственный свидетель собственной перемены.",
        },
        "motif": {
            "en": "The mirror frame is identical in every act; only what is in it changes.",
            "ru": "Рамка зеркала одинакова во всех актах; меняется только то, что в ней.",
        },
        "opens": {"en": "The mirror empty, the room behind it lit.",
                  "ru": "Зеркало пустое, комната за ним освещена."},
        "closes": {"en": "The mirror empty again, the room dark.",
                   "ru": "Зеркало снова пустое, комната тёмная."},
        "acts": [
            {"key": "first", "share": 0.25, "shot": "medium",
             "label": {"en": "First look", "ru": "Первый взгляд"},
             "en": "Establish the exact framing that will be repeated three more times.",
             "ru": "Заявляем ту самую композицию, которая повторится ещё трижды.",
             "boards": ["interior_mirror", "interior_window_side", "detail_hands_work"]},
            {"key": "change", "share": 0.3, "shot": "close-up",
             "label": {"en": "Changing", "ru": "Меняется"},
             "en": "Same frame, different hair, different light, different posture.",
             "ru": "Тот же кадр, другие волосы, другой свет, другая осанка.",
             "boards": ["portrait_profile_to_front", "portrait_turn_to_lens", "detail_texture_macro"]},
            {"key": "break", "share": 0.25, "shot": "extreme close-up",
             "label": {"en": "The break", "ru": "Слом"},
             "en": "The one act where he cannot look at himself.",
             "ru": "Единственный акт, где он не может на себя смотреть.",
             "boards": ["portrait_breath_break", "detail_written_trace", "night_screen_face"]},
            {"key": "last", "share": 0.2, "shot": "medium",
             "label": {"en": "Last look", "ru": "Последний взгляд"},
             "en": "Same frame, empty. He walked out of it.",
             "ru": "Тот же кадр, пустой. Он из него вышел.",
             "boards": ["final_walk_out_frame", "final_rhyme_open"]},
        ],
        "scenes": {"min": 12, "typ": 24, "max": 32},
        "open_board": "interior_mirror", "close_board": "final_rhyme_open",
        "styles_fit": ["cinema", "katsumi", "longheads", "dreamclad", "clay"],
        "preset": "one_character",
        "story": "Год жизни героя, показанный через одно и то же зеркало в одной и той же комнате. Первый акт "
                 "заявляет композицию, которая повторится ещё трижды кадр в кадр: рамка зеркала, положение камеры и "
                 "точка съёмки фиксируются раз и навсегда. Второй акт повторяет эту композицию с изменениями — "
                 "другая длина волос, другой свет из окна, другая осанка, другая одежда, — и зритель считывает время "
                 "именно по разнице, а не по подписи. Третий акт ломает приём ровно один раз: герой не может на себя "
                 "смотреть, и зеркало снимается со спины или отражает пустую комнату. Финал возвращает исходную "
                 "композицию пустой: он из неё вышел. Сквозной мотив — сама рамка зеркала, неизменная во всех "
                 "четырёх актах.",
        "dnote": "Композиция зеркала обязана совпадать пиксель в пиксель во всех актах — это единственное, что "
                 "держит клип. Никаких календарей, дат и надписей.",
        "slots_hint": ["character", "location", "outfit", "time"],
        "tags": ["melancholy", "muted", "deadpan"],
        "needs_lyrics": True,
    },
    {
        "key": "three_wishes",
        "tier": "pro",
        "label": {"en": "Three Wishes", "ru": "Три желания"},
        "music": {
            "en": "Playful or surreal production, 95-120 BPM, unusual samples, punchline-driven.",
            "ru": "Игровая или сюрреальная продакшн-подача, 95–120 BPM, необычные семплы, панчи.",
        },
        "bpm": [95, 120], "cut": "fast",
        "logline": {
            "en": "Three things he asks for arrive, and each arrives wrong.",
            "ru": "Три вещи, которые он просит, приходят — и каждая приходит криво.",
        },
        "hero": {
            "en": "The one who asks. He reacts more than he acts, and that is the joke.",
            "ru": "Тот, кто просит. Он больше реагирует, чем действует, — в этом и шутка.",
        },
        "motif": {
            "en": "The same gesture of asking, repeated before each wish.",
            "ru": "Один и тот же жест просьбы, повторённый перед каждым желанием.",
        },
        "opens": {"en": "An absolutely ordinary frame, deliberately boring.",
                  "ru": "Абсолютно обычный кадр, нарочито скучный."},
        "closes": {"en": "The ordinary frame again, with one impossible detail left in it.",
                   "ru": "Тот же обычный кадр, и в нём осталась одна невозможная деталь."},
        "acts": [
            {"key": "ask1", "share": 0.25, "shot": "medium",
             "label": {"en": "First wish", "ru": "Первое желание"},
             "en": "Ordinary world, one impossible thing, played straight.",
             "ru": "Обычный мир, одна невозможная вещь, сыграно всерьёз.",
             "boards": ["open_direct_look", "detail_object_pickup", "interior_table_sit"]},
            {"key": "ask2", "share": 0.25, "shot": "wide",
             "label": {"en": "Second wish", "ru": "Второе желание"},
             "en": "Bigger, and the cost starts showing in the background.",
             "ru": "Больше, и цена начинает проступать на фоне.",
             "boards": ["crowd_part_for_hero", "bridge_match_shape", "action_throw_away"]},
            {"key": "ask3", "share": 0.3, "shot": "close-up",
             "label": {"en": "Third wish", "ru": "Третье желание"},
             "en": "The biggest and the worst. Reaction, not spectacle.",
             "ru": "Самое большое и самое неудачное. Реакция, а не аттракцион.",
             "boards": ["portrait_breath_break", "action_fall_back", "bridge_door_through"]},
            {"key": "left", "share": 0.2, "shot": "medium",
             "label": {"en": "What is left", "ru": "Что осталось"},
             "en": "Back to the boring frame with one thing that should not be there.",
             "ru": "Возврат в скучный кадр, где осталось одно лишнее.",
             "boards": ["final_rhyme_open", "final_last_look"]},
        ],
        "scenes": {"min": 16, "typ": 30, "max": 40},
        "open_board": "open_direct_look", "close_board": "final_rhyme_open",
        "styles_fit": ["clay", "pixar", "flat2d", "dreamclad", "katsumi"],
        "preset": "randoms",
        "story": "Герой трижды получает то, что просил, и каждый раз получает это криво. Первый акт держит мир "
                 "абсолютно обычным и вводит ровно одну невозможную вещь, сыгранную всерьёз: никакой иронии в "
                 "подаче, чем серьёзнее, тем смешнее. Второй акт увеличивает масштаб желания и начинает показывать "
                 "цену — не репликой, а тем, что происходит на фоне, пока герой радуется. Третий акт даёт самое "
                 "большое и самое неудачное желание, и снимается он через реакцию героя, а не через аттракцион: "
                 "крупные планы лица, а не спецэффект в общем плане. Финал возвращает скучный первый кадр, в котором "
                 "осталась одна невозможная деталь, и никто её не комментирует. Сквозной мотив — один и тот же жест "
                 "просьбы, повторяющийся перед каждым желанием.",
        "dnote": "Невозможное показывай ровно по одному предмету на сцену: два чуда в одном кадре читаются как "
                 "мусор. Реакция важнее эффекта — держи камеру на лице.",
        "slots_hint": ["character", "location", "prop", "accent"],
        "tags": ["playful", "saturated", "handmade"],
        "needs_lyrics": False,
    },
    {
        "key": "unpacking",
        "tier": "pro",
        "label": {"en": "Unpacking", "ru": "Распаковка"},
        "music": {
            "en": "Clean commercial-friendly beats, 100-120 BPM, no profanity, hook in the first 5 seconds.",
            "ru": "Чистые коммерческие биты, 100–120 BPM, без мата, хук в первые 5 секунд.",
        },
        "bpm": [100, 120], "cut": "fast",
        "logline": {
            "en": "A product arrives, is opened and is used. The ritual is the story.",
            "ru": "Товар приезжает, вскрывается и работает. Ритуал и есть сюжет.",
        },
        "hero": {
            "en": "Hands, mostly. The face appears twice and both times on purpose.",
            "ru": "В основном руки. Лицо появляется дважды, и оба раза намеренно.",
        },
        "motif": {
            "en": "The object returns to the same surface between every act.",
            "ru": "Предмет возвращается на одну и ту же поверхность между актами.",
        },
        "opens": {"en": "The sealed object alone in frame.",
                  "ru": "Запечатанный предмет один в кадре."},
        "closes": {"en": "The object in use, the packaging gone.",
                   "ru": "Предмет в работе, упаковки нет."},
        "acts": [
            {"key": "arrive", "share": 0.2, "shot": "close-up",
             "label": {"en": "Arrival", "ru": "Приехало"},
             "en": "Sealed, untouched, lit like a portrait.",
             "ru": "Запечатано, нетронуто, снято как портрет.",
             "boards": ["open_detail_first", "detail_texture_macro"]},
            {"key": "open", "share": 0.3, "shot": "extreme close-up",
             "label": {"en": "Opening", "ru": "Вскрытие"},
             "en": "Hands only. Every scene is one gesture.",
             "ru": "Только руки. Каждая сцена — один жест.",
             "boards": ["detail_hands_work", "detail_object_pickup", "detail_pocket_reveal"]},
            {"key": "use", "share": 0.3, "shot": "medium",
             "label": {"en": "In use", "ru": "В деле"},
             "en": "The object in the world, with the person, in real light.",
             "ru": "Предмет в мире, с человеком, в реальном свете.",
             "boards": ["interior_window_side", "portrait_hold_still", "travel_side_track"]},
            {"key": "hero", "share": 0.2, "shot": "close-up",
             "label": {"en": "Hero frame", "ru": "Герой-кадр"},
             "en": "One frontal frame worth freezing as a cover.",
             "ru": "Один фронтальный кадр, годный на обложку.",
             "boards": ["final_last_look", "final_pull_to_wide"]},
        ],
        "scenes": {"min": 10, "typ": 20, "max": 30},
        "open_board": "open_detail_first", "close_board": "final_last_look",
        "styles_fit": ["cinema", "clay", "flat2d", "katsumi", "longheads"],
        "preset": "product_ritual",
        "story": "Товар приезжает, вскрывается и начинает работать — и весь клип держится на ритуале, а не на "
                 "сюжете. Первый акт снимает запечатанный предмет как портрет: один объект в кадре, вокруг воздух, "
                 "свет поставлен на фактуру упаковки. Второй акт — только руки: каждая сцена содержит ровно один "
                 "жест, и жесты идут в физически правильном порядке, без пропусков. Третий акт выводит предмет в мир "
                 "и в реальный свет, рядом с человеком, и здесь впервые появляется лицо. Финал — один фронтальный "
                 "симметричный кадр, годный на обложку и на карточку товара. Сквозной мотив — предмет возвращается "
                 "на одну и ту же поверхность между актами, и она узнаётся.",
        "dnote": "Никаких надписей и логотипов крупным планом: модели пишут с ошибками. Лицо ровно дважды за клип, "
                 "остальное — руки и предмет.",
        "slots_hint": ["object", "surface", "character", "accent"],
        "tags": ["clean", "luxury", "daylight"],
        "needs_lyrics": False,
    },
    {
        "key": "paper_trail",
        "tier": "free",
        "label": {"en": "Paper Trail", "ru": "Бумажный след"},
        "music": {
            "en": "Storytelling rap, 85-100 BPM, dense lyrics, small or no chorus.",
            "ru": "Повествовательный рэп, 85–100 BPM, плотный текст, маленький припев или без него.",
        },
        "bpm": [85, 100], "cut": "mid",
        "logline": {
            "en": "A stack of papers tells a story the hero refuses to tell.",
            "ru": "Стопка бумаг рассказывает историю, которую герой рассказывать отказывается.",
        },
        "hero": {
            "en": "The one who owes or is owed. He is defined by documents, not dialogue.",
            "ru": "Тот, кто должен или кому должны. Его определяют документы, а не реплики.",
        },
        "motif": {
            "en": "The same envelope or folder moves through every act.",
            "ru": "Один и тот же конверт или папка проходит через все акты.",
        },
        "opens": {"en": "Paper on a table, no hands yet.",
                  "ru": "Бумага на столе, рук ещё нет."},
        "closes": {"en": "The same paper, torn or burnt or filed.",
                   "ru": "Та же бумага — порванная, сожжённая или подшитая."},
        "acts": [
            {"key": "arrive", "share": 0.25, "shot": "close-up",
             "label": {"en": "It arrives", "ru": "Приходит"},
             "en": "The object first, the person second. Never show readable text.",
             "ru": "Сначала предмет, потом человек. Читаемого текста не показываем.",
             "boards": ["open_detail_first", "detail_pocket_reveal", "interior_doorframe"]},
            {"key": "carry", "share": 0.3, "shot": "medium",
             "label": {"en": "Carrying it", "ru": "Носит с собой"},
             "en": "It goes everywhere he goes, always in frame, never discussed.",
             "ru": "Оно ездит с ним везде, всегда в кадре и никогда не обсуждается.",
             "boards": ["travel_walk_away", "travel_car_window", "interior_table_sit"]},
            {"key": "read", "share": 0.25, "shot": "extreme close-up",
             "label": {"en": "Reading it", "ru": "Читает"},
             "en": "Face, hands, paper edge. What is written stays unknown.",
             "ru": "Лицо, руки, край бумаги. Что написано — остаётся неизвестным.",
             "boards": ["detail_written_trace", "portrait_breath_break", "night_screen_face"]},
            {"key": "end", "share": 0.2, "shot": "medium",
             "label": {"en": "Disposal", "ru": "Развязка"},
             "en": "One physical action decides it: tear, burn, sign, file.",
             "ru": "Решает одно физическое действие: порвать, сжечь, подписать, подшить.",
             "boards": ["action_throw_away", "final_walk_out_frame"]},
        ],
        "scenes": {"min": 14, "typ": 26, "max": 36},
        "open_board": "open_detail_first", "close_board": "final_walk_out_frame",
        "styles_fit": ["noir", "cinema", "longheads", "dreamclad", "katsumi"],
        "preset": "unsent_letter",
        "story": "Через весь клип проходит одна бумага — конверт, папка, повестка или письмо, — и она рассказывает "
                 "историю, которую герой рассказывать отказывается. Первый акт показывает предмет раньше человека: "
                 "бумага на столе, рук в кадре ещё нет. Второй акт возит её везде: она попадает в кадр в каждой "
                 "локации, лежит на приборной панели, торчит из кармана, но её никто не обсуждает. Третий акт — "
                 "чтение: лицо, руки, край листа, и содержание остаётся неизвестным зрителю до конца. Финал решается "
                 "одним физическим действием: порвать, сжечь, подписать или подшить, — и именно оно, а не текст, "
                 "объясняет всё. Сквозной мотив — сам предмет, проходящий через все акты в одном и том же состоянии "
                 "до самого финала.",
        "dnote": "Читаемого текста в кадре быть не должно вообще: модели врут в надписях, и одна кривая строчка "
                 "убивает сцену. Держи бумагу под углом, в расфокусе или обрезанной рамкой.",
        "slots_hint": ["character", "location", "prop", "detail"],
        "tags": ["melancholy", "muted", "grain"],
        "needs_lyrics": True,
    },
]


# ─────────────────────────────────────────────────────────────────────────────
# 2. РАСКАДРОВОЧНЫЕ ПРОМТЫ — 47 готовых сцен.
#
# Единица: ОДНА СЦЕНА РАСКАДРОВКИ. Карточка заполняет шесть полей сцены разом:
# shot_size, camera_move, image_prompt, image_prompt_last, motion_prompt и
# shot_note. Это и просил владелец: не абзац текста, а готовая строка.
#
# Ось рубрикации — ФУНКЦИЯ В КЛИПЕ, а не операторский приём. Приёмы уже
# разложены выше по оси «что настраивает», и вторая копия той же оси сделала бы
# фильтр лотереей: человек, которому нужен «финальный кадр», не должен
# перебирать «наезды».
#
# Поля, которых нет у приёма, и почему они здесь:
#   note     — shot_note. Человеческая подпись сцены в раскадровке: по ней
#              ориентируется автор, а не модель. Слотов в ней нет намеренно —
#              подставленное английское значение внутри русской подписи читается
#              как брак.
#   solo     — motion для Grok. Grok оживляет ТОЛЬКО первый кадр (mediagen:
#              first_last=False), и «движение между кадрами» ему бессмысленно.
#              Без этого поля половина каталога на нём не работает вообще.
#   bracket  — команда камеры в квадратных скобках для MiniMax H3: он читает
#              их буквально и игнорирует словесное описание движения. Seedance
#              и Kling скобок не понимают и утащат их в кадр как текст, поэтому
#              приклеивает их только рендер и только для minimax.
#   negative — отдельный канал запретов. Kling 3.0 ждёт их отдельным полем, а
#              не текстом в промпте; там, где канала нет, значение просто не
#              отправляется.
#   traits   — черты для миксования, см. TRAITS и CONFLICT_PAIRS.
#
# `styles_fit` — это СОВМЕСТИМОСТЬ, а не вкус составителя. Карточка, которая
# держится композицией (деталь, портрет, проём, пустой финальный кадр),
# работает на любом стиле, включая рисованные, и обязана быть им помечена:
# человек, выбравший Пиксар, не должен видеть каталог из трёх сцен. Сужаем
# только там, где сцена опирается на свет или фактуру, которых у стиля нет.
#
# ЧЕГО В ТЕКСТАХ НЕТ. Ни грейда, ни плёнки, ни палитры, ни зерна: их ставит
# стиль трека, и дублирование ломало картинку — это уже чинилось. Проверяется
# машинно списком _BAN_GRADE. Свет здесь есть, но только ГЕОМЕТРИЕЙ: откуда
# идёт, куда падает, что в тени.
# ─────────────────────────────────────────────────────────────────────────────
BOARD_GROUPS = [
    {"key": "opening",  "label": {"en": "Opening shots", "ru": "Открывающие кадры"},
     "hint": {"en": "The first three seconds decide whether the rest is watched.",
              "ru": "Первые три секунды решают, досмотрят ли остальное."}},
    {"key": "travel",   "label": {"en": "Travelling and movement", "ru": "Проходы и движение"},
     "hint": {"en": "Scenes that make a track feel like it is going somewhere.",
              "ru": "Сцены, от которых трек начинает казаться идущим куда-то."}},
    {"key": "portrait", "label": {"en": "Portraits and emotion", "ru": "Портреты и эмоция"},
     "hint": {"en": "A face held long enough to be believed.",
              "ru": "Лицо, которое держат достаточно долго, чтобы поверить."}},
    {"key": "detail",   "label": {"en": "Details and objects", "ru": "Детали и предметы"},
     "hint": {"en": "The cheapest scenes to generate and the ones that sell the world.",
              "ru": "Самые дешёвые в генерации сцены — и те, что продают мир."}},
    {"key": "action",   "label": {"en": "Action", "ru": "Действие и экшен"},
     "hint": {"en": "Movement that has to stay readable at two seconds.",
              "ru": "Движение, которое обязано читаться за две секунды."}},
    {"key": "crowd",    "label": {"en": "Crowds", "ru": "Толпа и массовка"},
     "hint": {"en": "Mass as a character. Never count people — models cannot.",
              "ru": "Масса как персонаж. Людей не считаем: модели не умеют."}},
    {"key": "interior", "label": {"en": "Interiors", "ru": "Интерьеры"},
     "hint": {"en": "Rooms where the light source is visible in frame.",
              "ru": "Комнаты, где источник света виден в кадре."}},
    {"key": "night",    "label": {"en": "Night and light", "ru": "Ночь и свет"},
     "hint": {"en": "Night is not darkness — it is a small number of sources.",
              "ru": "Ночь — это не темнота, а малое число источников."}},
    {"key": "final",    "label": {"en": "Closing shots", "ru": "Финальные кадры"},
     "hint": {"en": "How a clip ends decides what it was about.",
              "ru": "То, чем клип кончается, решает, о чём он был."}},
    {"key": "bridge",   "label": {"en": "Location bridges", "ru": "Переходы между локациями"},
     "hint": {"en": "Pairs of scenes: half the feel of a clip lives at the cut.",
              "ru": "Пары сцен: половина ощущения от клипа живёт на склейке."}},
]

BOARDS: list[dict] = [

    # ══════════════ ОТКРЫВАЮЩИЕ КАДРЫ ══════════════
    {
        "key": "open_door_out",
        "group": "opening", "tier": "free",
        "label": {"en": "Out of the door", "ru": "Выход из двери"},
        "desc": {"en": "The character leaves a doorway with their back to us. The face is withheld.",
                 "ru": "Герой выходит из проёма спиной к нам. Лицо придерживаем."},
        "shot": "wide", "camera": "static, low angle",
        "first": "Wide shot from outside {location}: {character} stands in a lit doorway with their back to the "
                 "camera, one hand still on the door, the street dark and empty in front of them at {time}. "
                 "Vertical framing, the doorway occupying the upper centre, wet ground in the lower third.",
        "last": "The same {character} in the same doorway of the same {location} at the same {time}, now two steps "
                "out on the street with the door swinging shut behind them, still seen from behind, same clothing "
                "and same light from the doorway.",
        "motion": "{character} takes two steps forward away from the camera and the door falls shut behind them; "
                  "the light from the doorway narrows to a line and the figure settles into the darker street.",
        "solo": "{character} steps forward away from the camera, the door swings shut behind them, and the movement "
                "comes to rest as they stop on the pavement. The camera does not move.",
        "bracket": "[Static shot]",
        "note": {"en": "Exit from the doorway, seen from behind", "ru": "Выход из проёма, со спины"},
        "negative": "face visible, camera shake, duplicated door, warped hands, text on the wall",
        "slots": ["character", "location", "time"],
        "traits": ["locked", "wide_frame", "exterior", "solo", "slow"],
        "needs_last": True, "engines": _CHEAP,
        "fits_with": ["m_pull_open", "m_coat_inertia", "l_practical_only", "l_blue_hour"],
        "conflicts_with": ["l_harsh_noon", "m_handheld_drift"],
        "styles_fit": ["noir", "cinema", "dreamclad", "punkrf", "longheads"],
        "tags": ["melancholy", "night", "street"],
    },
    {
        "key": "open_empty_place",
        "group": "opening", "tier": "free",
        "label": {"en": "The place before anyone", "ru": "Место до людей"},
        "desc": {"en": "An establishing shot with no character in it at all. The location is the first character.",
                 "ru": "Заявочный план вообще без героя. Первый персонаж — место."},
        "shot": "establishing", "camera": "static, eye level",
        "first": "Establishing wide of {location} at {time}, completely empty of people, composed symmetrically with "
                 "the vanishing point in the centre of the vertical frame, {weather} visible in the air, every "
                 "light source in the scene switched on.",
        "last": "The same empty {location} from the same camera position at the same {time} with the same lights on: "
                "only {weather} has moved through the frame and one distant light has gone out.",
        "motion": "Nothing enters the frame. Only the air moves: {weather} drifts across the space, one distant "
                  "light dies, and the shot holds on the empty place.",
        "solo": "Hold the empty {location} and let only the air move through it — {weather} drifting across the "
                "frame for six seconds until it settles. The camera stays locked off.",
        "bracket": "[Static shot]",
        "note": {"en": "Establishing: the location alone", "ru": "Заявочный: только локация"},
        "negative": "people, animals, moving vehicles, camera drift, warped architecture",
        "slots": ["location", "time", "weather"],
        "traits": ["locked", "wide_frame", "exterior", "slow"],
        "needs_last": False, "engines": _ANY,
        "fits_with": ["m_smoke_curl", "m_pan_link", "l_blue_hour", "l_deep_night", "l_first_light"],
        "conflicts_with": ["m_handheld_drift", "m_arc_quarter"],
        "styles_fit": ["cinema", "shinkai", "ghibli", "noir", "longheads", "flat2d"],
        "tags": ["deadpan", "muted", "street"],
    },
    {
        "key": "open_detail_first",
        "group": "opening", "tier": "free",
        "label": {"en": "Detail before face", "ru": "Деталь до лица"},
        "desc": {"en": "Open on an extreme close-up of one object. The person arrives in the next scene.",
                 "ru": "Открываемся макро на одном предмете. Человек появится следующей сценой."},
        "shot": "extreme close-up", "camera": "static, macro",
        "first": "Extreme close-up of {prop} lying on a surface in {location}, filling most of the vertical frame, "
                 "lit from one side so the texture reads, shallow depth of field, no person in shot.",
        "last": "The same {prop} on the same surface in the same {location} with the same side light, now with a "
                "hand entering the lower edge of the frame and touching it; the framing and focus are unchanged.",
        "motion": "A hand enters from the bottom edge of the frame and comes to rest on {prop}. Nothing else moves "
                  "and the camera holds its position.",
        "solo": "A hand slides into the bottom of the frame and stops on {prop}; the fingers settle and the shot "
                "holds. The camera does not move.",
        "bracket": "[Static shot]",
        "note": {"en": "Macro on the object, hand enters", "ru": "Макро на предмете, входит рука"},
        "negative": "full body, face, second hand, readable text, warped fingers",
        "slots": ["prop", "location"],
        "traits": ["locked", "close", "object", "interior"],
        "needs_last": True, "engines": _CHEAP,
        "fits_with": ["m_rack_focus", "m_hand_raise", "l_hard_single", "l_window_motivated"],
        "conflicts_with": ["m_crane_rise", "m_steadi_follow"],
        "styles_fit": ["cinema", "noir", "clay", "katsumi", "longheads", "embroidery"],
        "tags": ["deadpan", "muted", "clean"],
    },
    {
        "key": "open_direct_look",
        "group": "opening", "tier": "free",
        "label": {"en": "Straight into the lens", "ru": "Прямо в объектив"},
        "desc": {"en": "The hardest hook in vertical: a person looking directly at the viewer on the first beat.",
                 "ru": "Самый жёсткий хук вертикали: человек смотрит прямо на зрителя с первой доли."},
        "shot": "close-up", "camera": "static, eye level",
        "first": "Close-up of {character} in {location} facing the camera dead on, eyes directly in the lens, head "
                 "in the upper centre of the vertical frame, {emotion}, shoulders squared, background falling away "
                 "out of focus behind them.",
        "last": "The same {character} in the same {location} in the same light, framing unchanged, still looking "
                "into the lens but now half a step closer to the camera, {emotion} settled into stillness.",
        "motion": "{character} leans a fraction closer to the lens and stops; the eyes never leave the camera and "
                  "the shot holds on that stillness.",
        "solo": "{character} holds the look into the lens, blinks once, leans a fraction closer and stops. The "
                "camera stays where it is.",
        "bracket": "[Static shot]",
        "note": {"en": "Direct look into the lens", "ru": "Прямой взгляд в объектив"},
        "negative": "looking away, crossed eyes, warped iris, second person in frame, camera shake",
        "slots": ["character", "location", "emotion"],
        "traits": ["locked", "close", "hero_face", "solo"],
        "needs_last": True, "engines": _CHEAP,
        "fits_with": ["m_eyes_to_lens", "m_head_snap", "l_rim_back", "l_screen_glow"],
        "conflicts_with": ["m_steadi_follow", "m_crane_rise"],
        "styles_fit": ["cinema", "punkrf", "noir", "spike", "munir", "pixar", "flat2d"],
        "tags": ["menacing", "deadpan", "street"],
    },
    {
        "key": "open_from_black",
        "group": "opening", "tier": "free",
        "label": {"en": "Out of the dark", "ru": "Из темноты"},
        "desc": {"en": "The frame starts almost black and resolves into an image. Buys two seconds of attention.",
                 "ru": "Кадр начинается почти чёрным и проявляется в изображение. Покупает две секунды внимания."},
        "shot": "close-up", "camera": "static, slow reveal",
        "first": "Close-up in {location} at {time} where almost the whole vertical frame is in darkness: only a "
                 "narrow edge of {character}'s face and shoulder catches a weak light from one side, everything "
                 "else unlit and unreadable.",
        "last": "The same {character} in the same {location} at the same {time} from the same angle, now clearly "
                "lit across the face by the same single source, the surrounding darkness unchanged.",
        "motion": "The light on {character} strengthens until the face reads clearly, then holds. Nothing else in "
                  "the frame moves and the camera stays still.",
        "solo": "The single light on {character}'s face strengthens over four seconds until the face reads, then "
                "settles. The camera holds its position and the darkness around stays.",
        "bracket": "[Static shot]",
        "note": {"en": "Emerging out of darkness", "ru": "Проявление из темноты"},
        "negative": "flat even lighting, second light source, camera movement, background detail",
        "slots": ["character", "location", "time"],
        "traits": ["locked", "close", "low_key", "hero_face", "solo"],
        "needs_last": True, "engines": _CHEAP,
        "fits_with": ["m_blur_resolve", "m_breath_fog", "l_chiaroscuro", "l_hard_single"],
        "conflicts_with": ["l_harsh_noon", "l_overcast_flat"],
        "styles_fit": ["noir", "cinema", "dreamclad", "munir", "fanuel"],
        "tags": ["menacing", "monochrome", "night"],
    },
    {
        "key": "open_mid_action",
        "group": "opening", "tier": "free",
        "label": {"en": "Already running", "ru": "Уже на бегу"},
        "desc": {"en": "No setup at all: the first frame catches the action at its middle.",
                 "ru": "Без завязки: первый кадр застаёт действие в середине."},
        "shot": "medium", "camera": "static, low, subject crosses frame",
        "first": "Medium shot in {location} at {time}: {character} caught mid-stride running across the frame from "
                 "left to right, both feet off the ground, body leaning forward, motion blur on the arms, the "
                 "background compressed behind them.",
        "last": "The same {character} in the same {location} at the same {time}, same clothing and same light, now "
                "on the right edge of the frame with the leading foot planted and the body upright.",
        "motion": "{character} completes the stride across the frame and plants the leading foot, the body coming "
                  "upright as the movement stops at the edge of the shot.",
        "solo": "{character} finishes the running stride across the frame, plants the front foot and comes to a "
                "stop at the edge. The camera holds still while they cross it.",
        "bracket": "[Static shot]",
        "note": {"en": "Opening on a run already in progress", "ru": "Открываемся посреди бега"},
        "negative": "slow motion, floating feet, sliding feet, warped legs, empty frame",
        "slots": ["character", "location", "time"],
        "traits": ["locked", "fast", "exterior", "solo"],
        "needs_last": True, "engines": _CHEAP,
        "fits_with": ["m_weight_step", "m_whip_out", "m_dust_bloom", "l_hard_single"],
        "conflicts_with": ["m_pull_open", "l_soft_wrap"],
        "styles_fit": ["punkrf", "spike", "cinema", "munir", "noir"],
        "tags": ["epic", "saturated", "street"],
    },

    # ══════════════ ПРОХОДЫ И ДВИЖЕНИЕ ══════════════
    {
        "key": "travel_walk_away",
        "group": "travel", "tier": "free",
        "label": {"en": "Walking away", "ru": "Уход от камеры"},
        "desc": {"en": "The character walks away down the vertical of the frame. The street does the composing.",
                 "ru": "Герой уходит вглубь по вертикали кадра. Композицию строит улица."},
        "shot": "wide", "camera": "steadicam follow from behind, lowering to waist height",
        "first": "Wide shot from behind: {character} steps off the kerb into a narrow street in {location} at "
                 "{time}, shoulders up, hands in pockets, street lamps receding into the distance along the "
                 "vertical of the frame, puddles holding their reflections, plenty of headroom.",
        "last": "The same {character} in the same street in {location} at the same {time}, same clothing and same "
                "lamps, now framed from the waist up and much closer, passing directly under one lamp so the light "
                "falls on the shoulders from above.",
        "motion": "The camera follows from behind at walking pace and lowers to waist height as {character} walks "
                  "away, then settles as they pass under the lamp; the coat swings a beat behind the step.",
        "solo": "{character} walks away from the camera down the street; the camera follows at the same pace and "
                "lowers slightly, then settles as they reach the lamp. The coat swings a beat behind each step.",
        "bracket": "[Tracking shot]",
        "note": {"en": "Walking away, camera follows", "ru": "Уход по улице, камера следом"},
        "negative": "face turning back, foot sliding, background shifting, extra pedestrians, camera jitter",
        "slots": ["character", "location", "time"],
        "traits": ["moving_camera", "wide_frame", "exterior", "solo", "slow"],
        "needs_last": True, "engines": _CHEAP,
        "fits_with": ["m_steadi_follow", "m_coat_inertia", "l_practical_only", "l_deep_night"],
        "conflicts_with": ["m_freeze_settle", "l_harsh_noon"],
        "styles_fit": ["noir", "cinema", "dreamclad", "shinkai", "punkrf"],
        "tags": ["melancholy", "night", "street"],
    },
    {
        "key": "travel_side_track",
        "group": "travel", "tier": "free",
        "label": {"en": "Alongside", "ru": "Сбоку, вровень"},
        "desc": {"en": "The camera travels beside the character at their own speed. The background moves, they do not.",
                 "ru": "Камера едет рядом с героем на его скорости. Движется фон, а не он."},
        "shot": "medium", "camera": "tracking alongside, matched speed",
        "first": "Medium shot of {character} walking through {location} at {time}, seen from the side at their own "
                 "height, held in the same place in the frame, the background already streaked by the movement, "
                 "foreground posts sweeping past close to the lens.",
        "last": "The same {character} in the same {location} at the same {time}, same posture and same clothing, "
                "held in the same place in the frame, but the background behind them is now a different part of "
                "the street.",
        "motion": "The camera trucks sideways at walking pace, keeping {character} fixed in the frame while the "
                  "background sweeps past, then settles as the pace steadies.",
        "solo": "The camera trucks sideways at walking pace beside {character}, holding them in the same place in "
                "frame while the background sweeps past, then settles into a steady glide.",
        "bracket": "[Truck left]",
        "note": {"en": "Side tracking at walking pace", "ru": "Тревеллинг сбоку на шаге"},
        "negative": "subject drifting in frame, foot sliding, background stuttering, morphing buildings",
        "slots": ["character", "location", "time"],
        "traits": ["moving_camera", "exterior", "solo"],
        "needs_last": True, "engines": _CHEAP,
        "fits_with": ["m_truck_side", "m_weight_step", "l_golden_hour", "l_overcast_flat"],
        "conflicts_with": ["m_arc_quarter", "m_rack_focus"],
        "styles_fit": ["cinema", "longheads", "ghibli", "katsumi", "punkrf", "flat2d", "pixar"],
        "tags": ["deadpan", "street", "daylight"],
    },
    {
        "key": "travel_stairs_down",
        "group": "travel", "tier": "free",
        "label": {"en": "Down the stairs", "ru": "Лестница вниз"},
        "desc": {"en": "A stairwell is a vertical corridor — the one geometry made for a 9:16 frame.",
                 "ru": "Лестница — вертикальный коридор, единственная геометрия, придуманная под 9:16."},
        "shot": "medium", "camera": "handheld descent, slightly behind",
        "first": "Medium shot inside a stairwell in {location} at {time}: {character} descending, seen from half a "
                 "flight above and behind, the railing running down the left of the vertical frame, light coming "
                 "from a window on the landing below.",
        "last": "The same {character} in the same stairwell of {location} at the same {time}, same clothing and "
                "same railing, now a full flight lower and standing on the landing in the window light.",
        "motion": "The camera descends behind {character} at the pace of the steps, one flight, and settles when "
                  "they stop on the landing; a small handheld sway stays in the move.",
        "solo": "{character} walks down one flight of stairs away from the camera, which descends behind them at "
                "the same pace and settles when they reach the landing.",
        "bracket": "[Pedestal down]",
        "note": {"en": "Descending a stairwell", "ru": "Спуск по лестнице"},
        "negative": "floating steps, warped railing, duplicated landings, face to camera, jitter",
        "slots": ["character", "location", "time"],
        "traits": ["handheld", "moving_camera", "interior", "solo"],
        "needs_last": True, "engines": _CHEAP,
        "fits_with": ["m_handheld_drift", "m_pedestal_down", "l_window_motivated", "l_practical_only"],
        "conflicts_with": ["m_crane_rise", "l_harsh_noon"],
        "styles_fit": ["noir", "cinema", "punkrf", "dreamclad", "longheads", "pixar"],
        "tags": ["menacing", "muted", "night"],
    },
    {
        "key": "travel_car_window",
        "group": "travel", "tier": "free",
        "label": {"en": "Through the window", "ru": "Через окно"},
        "desc": {"en": "The world runs past outside while the character stays still inside. Cheap, and it never fails.",
                 "ru": "Мир бежит снаружи, герой внутри неподвижен. Дёшево и не подводит никогда."},
        "shot": "medium", "camera": "static inside the vehicle",
        "first": "Medium shot inside {vehicle} in {location} at {time}: {character} sits by the window seen from "
                 "the side, face half turned to the glass, the landscape outside already blurred by speed, the "
                 "window frame cutting the vertical shot in two.",
        "last": "The same {character} in the same {vehicle} at the same {time}, same seat and same window frame, "
                "now with the head resting against the glass and a completely different landscape outside.",
        "motion": "The landscape outside the window streams past while {character} slowly lets the head rest "
                  "against the glass and stops there; the camera inside does not move.",
        "solo": "The landscape outside the window streams past and {character} slowly lowers the head against the "
                "glass and stops. The camera inside the {vehicle} stays locked off.",
        "bracket": "[Static shot]",
        "note": {"en": "Seated by a moving window", "ru": "У окна на ходу"},
        "negative": "camera shake, warped reflections, duplicated window frame, readable signs outside",
        "slots": ["character", "vehicle", "location", "time"],
        "traits": ["locked", "interior", "solo", "slow"],
        "needs_last": True, "engines": _CHEAP,
        "fits_with": ["m_rack_focus", "m_hair_lag", "l_window_motivated", "l_blue_hour"],
        "conflicts_with": ["m_steadi_follow", "m_arc_quarter"],
        "styles_fit": ["shinkai", "cinema", "katsumi", "dreamclad", "longheads"],
        "tags": ["melancholy", "nostalgic", "night"],
    },
    {
        "key": "travel_corridor_push",
        "group": "travel", "tier": "pro",
        "label": {"en": "Down the corridor", "ru": "По коридору"},
        "desc": {"en": "A corridor gives the frame its own perspective lines. The camera only has to go straight.",
                 "ru": "Коридор сам даёт кадру перспективу. Камере остаётся только ехать прямо."},
        "shot": "wide", "camera": "slow push-in along the corridor",
        "first": "Wide shot down a long corridor in {location} at {time}: {character} stands far away at the end of "
                 "it, small and centred, the ceiling lights running away in two converging lines above them, the "
                 "floor reflecting each light as a stripe.",
        "last": "The same {character} in the same corridor of {location} at the same {time}, same lights and same "
                "clothing, now framed from the chest up and close to the camera, the corridor collapsed to a "
                "shallow band behind them.",
        "motion": "The camera pushes straight down the corridor toward {character} at a steady speed and settles "
                  "when the face fills the frame; {character} does not walk toward the camera.",
        "solo": "The camera pushes slowly straight down the corridor toward {character}, who stands still at the "
                "far end, and settles once they fill the frame.",
        "bracket": "[Push in]",
        "note": {"en": "Push down a corridor", "ru": "Наезд по коридору"},
        "negative": "subject walking toward camera, warped perspective, duplicated doors, camera roll",
        "slots": ["character", "location", "time"],
        "traits": ["moving_camera", "interior", "solo", "slow"],
        "needs_last": True, "engines": _CHEAP,
        "fits_with": ["m_push_settle", "m_breath_fog", "l_practical_only", "l_chiaroscuro"],
        "conflicts_with": ["m_truck_side", "l_golden_hour"],
        "styles_fit": ["noir", "cinema", "munir", "dreamclad", "fanuel"],
        "tags": ["menacing", "cold", "night"],
    },

    # ══════════════ ПОРТРЕТЫ И ЭМОЦИЯ ══════════════
    {
        "key": "portrait_hold_still",
        "group": "portrait", "tier": "free",
        "label": {"en": "The held portrait", "ru": "Держим портрет"},
        "desc": {"en": "A face doing almost nothing for six seconds. The hardest scene to write and the one that lands.",
                 "ru": "Лицо, которое шесть секунд почти ничего не делает. Самая трудная сцена — и та, что попадает."},
        "shot": "close-up", "camera": "static, eye level",
        "first": "Close-up of {character} in {location}, head and shoulders filling the vertical frame, lit from one "
                 "side so half the face falls into shadow, {emotion}, eyes fixed on something just past the lens, "
                 "background dark and unreadable.",
        "last": "The same {character} in the same {location} with the same single-side light and the same framing, "
                "the head turned a few degrees further into the light, {emotion} unchanged.",
        "motion": "{character} turns the head a few degrees into the light, blinks once and stops; nothing else in "
                  "the frame moves and the camera holds.",
        "solo": "{character} blinks once, turns the head a few degrees into the light and stops. The camera does "
                "not move and the background stays dark.",
        "bracket": "[Static shot]",
        "note": {"en": "Held close-up, minimal movement", "ru": "Крупный план, минимум движения"},
        "negative": "smiling, exaggerated expression, head bobbing, warped eyes, second face",
        "slots": ["character", "location", "emotion"],
        "traits": ["locked", "close", "hero_face", "solo", "slow"],
        "needs_last": True, "engines": _CHEAP,
        "fits_with": ["m_push_settle", "m_shoulder_drop", "l_rim_back", "l_hard_single", "l_soft_wrap"],
        "conflicts_with": ["m_whip_out", "m_crane_rise"],
        "styles_fit": ["cinema", "noir", "longheads", "katsumi", "fanuel", "flat2d"],
        "tags": ["melancholy", "muted", "deadpan"],
    },
    {
        "key": "portrait_turn_to_lens",
        "group": "portrait", "tier": "free",
        "label": {"en": "Turning to the lens", "ru": "Поворот к камере"},
        "desc": {"en": "Starts as an unaware profile, ends as eye contact. One move, whole meaning.",
                 "ru": "Начинается неосознанным профилем, заканчивается зрительным контактом. Одно движение — весь смысл."},
        "shot": "close-up", "camera": "static, eye level",
        "first": "Close-up of {character} in {location} in profile, facing left out of the frame, unaware of the "
                 "camera, lit from the front-left so the near cheek is bright and the far side is in shadow, "
                 "{emotion}.",
        "last": "The same {character} in the same {location} in the same light and framing, now turned fully to "
                "face the camera with the eyes directly in the lens, {emotion}.",
        "motion": "{character} turns the head from profile to the lens in one continuous move and stops there, "
                  "eyes settling on the camera; the hair follows a beat behind the turn.",
        "solo": "{character} turns from profile toward the camera, the hair trailing a beat behind, and stops with "
                "the eyes in the lens. The camera stays still.",
        "bracket": "[Static shot]",
        "note": {"en": "Profile turning into eye contact", "ru": "Профиль поворачивается в контакт"},
        "negative": "head rotating past the camera, neck stretching, warped ear, blinking out of sync, jitter",
        "slots": ["character", "location", "emotion"],
        "traits": ["locked", "close", "hero_face", "solo"],
        "needs_last": True, "engines": _CHEAP,
        "fits_with": ["m_turn_to_lens", "m_hair_lag", "l_rim_back", "l_window_motivated"],
        "conflicts_with": ["m_truck_side", "m_body_wipe"],
        "styles_fit": ["cinema", "katsumi", "noir", "dreamclad", "shinkai", "pixar", "flat2d"],
        "tags": ["melancholy", "deadpan", "muted"],
    },
    {
        "key": "portrait_breath_break",
        "group": "portrait", "tier": "pro",
        "label": {"en": "The break", "ru": "Слом"},
        "desc": {"en": "The face holds, then stops holding. One scene carries the emotional peak of a whole clip.",
                 "ru": "Лицо держится — и перестаёт держаться. Одна сцена тянет эмоциональный пик всего клипа."},
        "shot": "extreme close-up", "camera": "static, slightly low",
        "first": "Extreme close-up of {character}'s face in {location}, jaw set and mouth closed, {emotion}, lit by "
                 "one hard source from the side, the eyes dry and fixed forward, breath held.",
        "last": "The same {character} in the same {location} under the same hard side light, same framing, now with "
                "the jaw loosened, the mouth slightly open and the shoulders dropped after a long exhale.",
        "motion": "{character} holds the breath, then releases it: the jaw loosens, the shoulders drop, and the "
                  "face settles into the exhale without any other movement.",
        "solo": "{character} holds a breath for three seconds, then exhales; the jaw loosens and the shoulders drop "
                "as the face settles. The camera does not move.",
        "bracket": "[Static shot]",
        "note": {"en": "Held breath released", "ru": "Задержанное дыхание отпускается"},
        "negative": "crying, theatrical grimace, head turning away, warped mouth, camera movement",
        "slots": ["character", "location", "emotion"],
        "traits": ["locked", "close", "hero_face", "solo", "slow"],
        "needs_last": True, "engines": _CHEAP,
        "fits_with": ["m_shoulder_drop", "m_breath_fog", "l_hard_single", "l_chiaroscuro"],
        "conflicts_with": ["m_whip_out", "l_high_key_bounce"],
        "styles_fit": ["cinema", "noir", "fanuel", "longheads", "munir"],
        "tags": ["melancholy", "monochrome", "deadpan"],
    },
    {
        "key": "portrait_profile_to_front",
        "group": "portrait", "tier": "free",
        "label": {"en": "Profile to front", "ru": "Профиль в фас"},
        "desc": {"en": "Not a head turn but a body turn: the whole person squares up to the camera.",
                 "ru": "Поворот не головы, а корпуса: человек разворачивается к камере целиком."},
        "shot": "medium", "camera": "static, chest level",
        "first": "Medium shot of {character} in {location} standing in profile, weight on the back foot, arms "
                 "loose, seen from the side against a plain wall, one light source behind them tracing the edge of "
                 "the shoulder and jaw.",
        "last": "The same {character} in the same {location} against the same wall with the same light behind them, "
                "now squared up to the camera with the weight settled on both feet and the shoulders level.",
        "motion": "{character} turns the whole body from profile to face the camera, the weight transferring from "
                  "the back foot to both feet, and stops squared to the lens.",
        "solo": "{character} turns the whole body from profile toward the camera, transferring weight from the back "
                "foot to both, and stops facing the lens. The camera holds still.",
        "bracket": "[Static shot]",
        "note": {"en": "Body turn to face camera", "ru": "Разворот корпусом к камере"},
        "negative": "feet sliding, hips detaching, warped shoulders, background shifting, extra arm",
        "slots": ["character", "location"],
        "traits": ["locked", "hero_face", "solo"],
        "needs_last": True, "engines": _CHEAP,
        "fits_with": ["m_weight_step", "m_turn_to_lens", "l_rim_back", "l_three_point"],
        "conflicts_with": ["m_handheld_drift", "m_crane_rise"],
        "styles_fit": ["cinema", "munir", "spike", "punkrf", "longheads"],
        "tags": ["epic", "deadpan", "street"],
    },
    {
        "key": "portrait_two_shot",
        "group": "portrait", "tier": "pro",
        "label": {"en": "Two people, one frame", "ru": "Двое в одном кадре"},
        "desc": {"en": "The distance between them is the subject. Nobody has to say anything.",
                 "ru": "Тема кадра — расстояние между ними. Говорить никому не нужно."},
        "shot": "medium", "camera": "static, eye level, symmetrical",
        "first": "Medium two-shot in {location} at {time}: {character} on the left of the vertical frame and a "
                 "second person on the right, a clear gap of empty space between them, both facing forward rather "
                 "than at each other, one light source between them lighting the gap.",
        "last": "The same two people in the same {location} at the same {time} in the same light and framing, now "
                "with one of them turned toward the other while the second still faces forward; the gap between "
                "them is unchanged.",
        "motion": "One of the two turns toward the other and stops; the second does not react, and the empty space "
                  "between them holds the frame.",
        "solo": "One of the two people turns toward the other and stops; the other does not move at all. The camera "
                "holds the gap between them.",
        "bracket": "[Static shot]",
        "note": {"en": "Two-shot with a gap between them", "ru": "Двое с разрывом между ними"},
        "negative": "characters merging, identical faces, touching, warped limbs, camera drift",
        "slots": ["character", "location", "time"],
        "traits": ["locked", "interior", "slow"],
        "needs_last": True, "engines": _CHEAP,
        "fits_with": ["m_turn_to_lens", "m_shoulder_drop", "l_practical_only", "l_soft_wrap"],
        "conflicts_with": ["m_arc_quarter", "m_whip_out"],
        "styles_fit": ["cinema", "katsumi", "longheads", "clay", "noir", "pixar"],
        "tags": ["melancholy", "deadpan", "muted"],
    },

    # ══════════════ ДЕТАЛИ И ПРЕДМЕТЫ ══════════════
    {
        "key": "detail_hands_work",
        "group": "detail", "tier": "free",
        "label": {"en": "Hands working", "ru": "Руки работают"},
        "desc": {"en": "No face at all. Hands are the cheapest believable performance a model can give you.",
                 "ru": "Без лица вообще. Руки — самая дешёвая правдоподобная игра, которую даёт модель."},
        "shot": "close-up", "camera": "static, high angle over the hands",
        "first": "Close-up looking down at a pair of hands in {location} working on {prop} on a worn surface, "
                 "sleeves pushed up, one hard light from the left throwing the shadows of the fingers across the "
                 "surface, no face in the frame.",
        "last": "The same hands on the same surface in the same {location} under the same hard left light, {prop} "
                "now finished and set down flat, the fingers withdrawn to the edge of the frame.",
        "motion": "The hands complete the work on {prop}, set it down and withdraw to the edge of the frame, where "
                  "the movement stops; the camera stays overhead.",
        "solo": "The hands finish working on {prop}, set it down flat and withdraw to the edge of the frame, then "
                "stop. The camera stays overhead and does not move.",
        "bracket": "[Static shot]",
        "note": {"en": "Hands only, working an object", "ru": "Только руки, работа с предметом"},
        "negative": "face in frame, six fingers, warped knuckles, third hand, readable text",
        "slots": ["prop", "location"],
        "traits": ["locked", "close", "object", "interior"],
        "needs_last": True, "engines": _CHEAP,
        "fits_with": ["m_hand_raise", "m_rack_focus", "l_hard_single", "l_window_motivated"],
        "conflicts_with": ["m_crane_rise", "m_steadi_follow"],
        "styles_fit": ["cinema", "clay", "ghibli", "longheads", "katsumi", "embroidery", "pixar"],
        "tags": ["cozy", "warm", "handmade"],
    },
    {
        "key": "detail_object_pickup",
        "group": "detail", "tier": "free",
        "label": {"en": "Picking it up", "ru": "Берёт предмет"},
        "desc": {"en": "An object on a surface, then in a hand. The smallest complete action there is.",
                 "ru": "Предмет на поверхности — и в руке. Самое маленькое законченное действие."},
        "shot": "close-up", "camera": "static, low, level with the surface",
        "first": "Close-up level with {surface} in {location}: {prop} sits alone in the centre of the vertical "
                 "frame, lit from behind so its outline separates from the darker background, with empty space "
                 "around it.",
        "last": "The same {prop} in the same {location} in the same backlight, now lifted off {surface} and held in "
                "a hand at the top of the frame, the empty surface visible below it.",
        "motion": "A hand enters, closes around {prop} and lifts it clear of {surface}, then stops with the object "
                  "held still in the upper frame.",
        "solo": "A hand enters the frame, closes around {prop}, lifts it off {surface} and stops with it held "
                "still. The camera does not move.",
        "bracket": "[Static shot]",
        "note": {"en": "Object lifted off the surface", "ru": "Предмет поднимают с поверхности"},
        "negative": "object floating, hand passing through the object, duplicated object, warped fingers",
        "slots": ["prop", "surface", "location"],
        "traits": ["locked", "close", "object"],
        "needs_last": True, "engines": _CHEAP,
        "fits_with": ["m_hand_raise", "m_rack_focus", "l_rim_back", "l_hard_single"],
        "conflicts_with": ["m_truck_side", "m_handheld_drift"],
        "styles_fit": ["cinema", "noir", "clay", "flat2d", "longheads", "embroidery", "pixar"],
        "tags": ["deadpan", "clean", "muted"],
    },
    {
        "key": "detail_texture_macro",
        "group": "detail", "tier": "free",
        "label": {"en": "Texture macro", "ru": "Макро фактуры"},
        "desc": {"en": "So close the subject becomes an abstraction. Buys texture the wide shots never show.",
                 "ru": "Так близко, что предмет становится абстракцией. Даёт фактуру, которой нет на общих."},
        "shot": "extreme close-up", "camera": "static macro, shallow focus",
        "first": "Extreme macro of the surface of {object} in {location}, filling the whole vertical frame, raking "
                 "light from one side revealing every fibre and scratch of the material, depth of field so shallow "
                 "that only a narrow band is sharp.",
        "last": "The same surface of the same {object} in the same {location} under the same raking light, the "
                "sharp band of focus now sitting further across the material and a drop of moisture forming at "
                "the edge.",
        "motion": "The plane of focus travels slowly across the surface of {object} and comes to rest, a single "
                  "drop of moisture gathering at the edge as it stops.",
        "solo": "The plane of focus drifts slowly across the surface of {object} and settles, while a drop of "
                "moisture gathers at the edge. The camera stays fixed.",
        "bracket": "[Static shot]",
        "note": {"en": "Macro on material", "ru": "Макро по материалу"},
        "negative": "whole object visible, flat lighting, duplicated texture, readable branding",
        "slots": ["object", "location"],
        "traits": ["locked", "close", "object", "slow"],
        "needs_last": True, "engines": _CHEAP,
        "fits_with": ["m_rack_focus", "m_water_drip", "l_hard_single", "l_window_motivated"],
        "conflicts_with": ["m_crane_rise", "m_steadi_follow"],
        "styles_fit": ["cinema", "clay", "embroidery", "longheads", "katsumi"],
        "tags": ["clean", "luxury", "handmade"],
    },
    {
        "key": "detail_pocket_reveal",
        "group": "detail", "tier": "free",
        "label": {"en": "Out of the pocket", "ru": "Из кармана"},
        "desc": {"en": "An object appears from clothing. Reveals a plot point without a single word.",
                 "ru": "Предмет появляется из одежды. Сюжетный поворот без единого слова."},
        "shot": "close-up", "camera": "static, chest level",
        "first": "Close-up on the chest and hands of {character} in {location} wearing {outfit}, one hand halfway "
                 "into a pocket, the fabric pulled taut around the wrist, the face cropped out above the top edge "
                 "of the frame.",
        "last": "The same {character} in the same {outfit} in the same {location} and the same light, the hand now "
                "out of the pocket and holding {prop} in front of the chest, the fabric fallen loose again.",
        "motion": "The hand comes out of the pocket holding {prop} and stops in front of the chest; the fabric of "
                  "{outfit} falls loose a beat after the wrist stops moving.",
        "solo": "The hand pulls {prop} out of the pocket and stops with it in front of the chest, the fabric of "
                "{outfit} settling a beat later. The camera holds still.",
        "bracket": "[Static shot]",
        "note": {"en": "Object drawn from a pocket", "ru": "Предмет достают из кармана"},
        "negative": "face in frame, object morphing, extra hand, warped fabric, readable text",
        "slots": ["character", "outfit", "prop", "location"],
        "traits": ["locked", "close", "object", "solo"],
        "needs_last": True, "engines": _CHEAP,
        "fits_with": ["m_hand_raise", "m_hem_settle", "l_hard_single", "l_practical_only"],
        "conflicts_with": ["m_crane_rise", "m_arc_quarter"],
        "styles_fit": ["noir", "cinema", "punkrf", "munir", "longheads", "flat2d"],
        "tags": ["menacing", "muted", "street"],
    },
    {
        "key": "detail_written_trace",
        "group": "detail", "tier": "pro",
        "label": {"en": "The mark left behind", "ru": "Оставленный след"},
        "desc": {"en": "A scratch, a stain, a worn step: history without text. Models spell badly — marks they draw well.",
                 "ru": "Царапина, пятно, стёртая ступень: история без текста. Надписи модель врёт, следы — рисует."},
        "shot": "extreme close-up", "camera": "static, angled across the surface",
        "first": "Extreme close-up of {detail} on a worn surface in {location}, shot at a steep angle across the "
                 "material so the light rakes it, dust caught in the recesses, no lettering and no signage "
                 "anywhere in the frame.",
        "last": "The same {detail} on the same surface in the same {location}, the same angle and the same raking "
                "light, now with a finger tracing along it and dust lifted where it passed.",
        "motion": "A fingertip traces slowly along {detail} and stops at the end of it, lifting a thin line of "
                  "dust that settles behind the movement.",
        "solo": "A fingertip traces slowly along {detail} and stops at the end, lifting a thin line of dust that "
                "settles behind it. The camera does not move.",
        "bracket": "[Static shot]",
        "note": {"en": "Tracing a mark on a surface", "ru": "Палец ведёт по следу"},
        "negative": "letters, numbers, signage, warped finger, flat lighting, duplicated marks",
        "slots": ["detail", "location"],
        "traits": ["locked", "close", "object", "slow"],
        "needs_last": True, "engines": _CHEAP,
        "fits_with": ["m_rack_focus", "m_dust_bloom", "l_hard_single", "l_chiaroscuro"],
        "conflicts_with": ["m_whip_out", "l_overcast_flat"],
        "styles_fit": ["cinema", "fanuel", "longheads", "noir", "embroidery"],
        "tags": ["nostalgic", "sacred", "muted"],
    },

    # ══════════════ ДЕЙСТВИЕ И ЭКШЕН ══════════════
    {
        "key": "action_run_toward",
        "group": "action", "tier": "free",
        "label": {"en": "Running at the camera", "ru": "Бег на камеру"},
        "desc": {"en": "The subject grows in frame without the camera moving. Safest action scene there is.",
                 "ru": "Герой растёт в кадре без движения камеры. Самая безопасная экшен-сцена."},
        "shot": "wide", "camera": "static, low",
        "first": "Wide low shot in {location} at {time}: {character} far away at the end of the frame, already "
                 "running toward the camera, both arms driving, the ground stretching between them and the lens.",
        "last": "The same {character} in the same {location} at the same {time}, same clothing, now filling the "
                "frame from the knees up, one foot planted hard in the foreground and the body braked.",
        "motion": "{character} runs straight at the camera and brakes hard in the foreground, planting one foot; "
                  "the body pitches forward and settles as the run stops.",
        "solo": "{character} runs straight toward the camera and brakes hard close to the lens, planting a foot and "
                "coming to a stop. The camera stays low and still.",
        "bracket": "[Static shot]",
        "note": {"en": "Run toward camera, hard stop", "ru": "Бег на камеру, резкая остановка"},
        "negative": "sliding feet, floating body, motion blur on the background, warped legs, jitter",
        "slots": ["character", "location", "time"],
        "traits": ["locked", "fast", "exterior", "solo"],
        "needs_last": True, "engines": _CHEAP,
        "fits_with": ["m_weight_step", "m_dust_bloom", "l_hard_single", "l_rim_back"],
        "conflicts_with": ["m_push_settle", "l_soft_wrap"],
        "styles_fit": ["punkrf", "spike", "cinema", "munir", "noir"],
        "tags": ["epic", "saturated", "street"],
    },
    {
        "key": "action_impact_stop",
        "group": "action", "tier": "pro",
        "label": {"en": "Impact", "ru": "Удар"},
        "desc": {"en": "The frame lands on a beat because something in it hits something else.",
                 "ru": "Кадр попадает в долю, потому что внутри него что-то во что-то ударяет."},
        "shot": "medium", "camera": "static, chest level",
        "first": "Medium shot in {location}: {character} with the arm drawn fully back and the shoulder loaded, "
                 "body coiled toward a solid surface at the edge of the frame, dust already hanging in the light "
                 "from the side.",
        "last": "The same {character} in the same {location} in the same side light, same clothing, now with the "
                "hand flat against that surface, the shoulder collapsed forward and dust knocked loose into the "
                "air around the contact point.",
        "motion": "{character} drives the arm forward into the surface and stops dead on contact; dust bursts away "
                  "from the point of impact and settles.",
        "solo": "{character} drives the arm forward into the surface and stops dead on contact, dust bursting away "
                "from the impact and settling. The camera does not move.",
        "bracket": "[Static shot]",
        "note": {"en": "Strike and dead stop on contact", "ru": "Удар и мёртвая остановка"},
        "negative": "arm passing through the wall, rubber limbs, blood, warped hand, camera shake",
        "slots": ["character", "location"],
        "traits": ["locked", "fast", "solo", "hard_light"],
        "needs_last": True, "engines": _CHEAP,
        "fits_with": ["m_freeze_settle", "m_dust_bloom", "l_hard_single", "l_chiaroscuro"],
        "conflicts_with": ["m_push_settle", "l_soft_wrap"],
        "styles_fit": ["punkrf", "noir", "spike", "munir", "cinema"],
        "tags": ["menacing", "saturated", "street"],
    },
    {
        "key": "action_jump_land",
        "group": "action", "tier": "free",
        "label": {"en": "Jump and landing", "ru": "Прыжок и приземление"},
        "desc": {"en": "Two phases of one movement: airborne and grounded. The pair does all the work.",
                 "ru": "Две фазы одного движения: в воздухе и на земле. Всю работу делает пара."},
        "shot": "medium", "camera": "static, low",
        "first": "Medium low shot in {location} at {time}: {character} in mid-air above the ground, knees tucked, "
                 "arms out for balance, nothing but sky and rooftops behind them in the vertical frame.",
        "last": "The same {character} in the same {location} at the same {time}, same clothing, now landed in a "
                "deep crouch with both hands touching the ground and dust pushed outward around the feet.",
        "motion": "{character} drops out of the air into a deep crouch, both hands reaching the ground, and the "
                  "body settles as the dust pushes outward from the landing.",
        "solo": "{character} drops down into a deep crouch, hands touching the ground, and the body settles while "
                "dust pushes outward around the feet. The camera holds low and still.",
        "bracket": "[Static shot]",
        "note": {"en": "Airborne to landing crouch", "ru": "Из воздуха в присед"},
        "negative": "floating landing, feet through the ground, rubber legs, cape physics, jitter",
        "slots": ["character", "location", "time"],
        "traits": ["locked", "fast", "exterior", "solo"],
        "needs_last": True, "engines": _CHEAP,
        "fits_with": ["m_weight_step", "m_dust_bloom", "m_freeze_settle", "l_rim_back"],
        "conflicts_with": ["m_push_settle", "m_rack_focus"],
        "styles_fit": ["spike", "punkrf", "cinema", "munir", "pixar"],
        "tags": ["epic", "saturated", "street"],
    },
    {
        "key": "action_throw_away",
        "group": "action", "tier": "free",
        "label": {"en": "Throwing it away", "ru": "Бросок"},
        "desc": {"en": "A decision made physical. The object leaves the hand and the frame keeps going.",
                 "ru": "Решение, ставшее физическим. Предмет покидает руку, а кадр продолжается."},
        "shot": "medium", "camera": "static, eye level",
        "first": "Medium shot of {character} in {location} at {time} holding {prop} out at arm's length, the arm "
                 "fully extended and the fingers already opening, the body turned away from the direction of the "
                 "throw.",
        "last": "The same {character} in the same {location} at the same {time} in the same light, the hand now "
                "empty and dropping, {prop} gone from the frame entirely and the eyes following it out.",
        "motion": "{character} releases {prop} and lets the arm fall; the object leaves the frame and the hand "
                  "comes to rest against the thigh.",
        "solo": "{character} opens the hand, lets {prop} go out of frame and drops the arm to rest against the "
                "thigh. The camera does not move.",
        "bracket": "[Static shot]",
        "note": {"en": "Release and the arm falls", "ru": "Отпускает, рука падает"},
        "negative": "object multiplying, object returning, warped arm, slow motion, camera pan",
        "slots": ["character", "prop", "location", "time"],
        "traits": ["locked", "solo", "exterior"],
        "needs_last": True, "engines": _CHEAP,
        "fits_with": ["m_hand_raise", "m_paper_scatter", "l_overcast_flat", "l_golden_hour"],
        "conflicts_with": ["m_rack_focus", "m_body_wipe"],
        "styles_fit": ["cinema", "punkrf", "longheads", "katsumi", "clay", "pixar", "flat2d"],
        "tags": ["melancholy", "street", "muted"],
    },
    {
        "key": "action_fall_back",
        "group": "action", "tier": "pro",
        "label": {"en": "Falling backwards", "ru": "Падение назад"},
        "desc": {"en": "Loss of control shown by physics rather than by acting.",
                 "ru": "Потеря контроля через физику, а не через игру."},
        "shot": "medium", "camera": "static, low",
        "first": "Medium low shot of {character} in {location} at the moment balance goes: heels off the ground, "
                 "arms starting to rise, the torso already past vertical, one hard light from above throwing the "
                 "shadow down the wall behind.",
        "last": "The same {character} in the same {location} under the same hard light from above, same clothing, "
                "now flat on the ground with the arms out to the sides and the hair spread against the floor.",
        "motion": "{character} loses balance and falls backwards, the arms lifting and then dropping to the sides "
                  "as the body comes to rest flat on the ground.",
        "solo": "{character} loses balance and falls backwards out of the standing pose, the arms rising and then "
                "dropping as the body comes to rest on the ground. The camera stays low and still.",
        "bracket": "[Static shot]",
        "note": {"en": "Balance lost, body settles flat", "ru": "Потеря равновесия, тело ложится"},
        "negative": "bouncing, rubber spine, floating hair, blood, camera shake",
        "slots": ["character", "location"],
        "traits": ["locked", "fast", "solo", "hard_light"],
        "needs_last": True, "engines": _CHEAP,
        "fits_with": ["m_hair_lag", "m_freeze_settle", "l_hard_single", "l_chiaroscuro"],
        "conflicts_with": ["m_steadi_follow", "l_soft_wrap"],
        "styles_fit": ["noir", "cinema", "dreamclad", "munir", "punkrf"],
        "tags": ["menacing", "monochrome", "night"],
    },

    # ══════════════ ТОЛПА И МАССОВКА ══════════════
    {
        "key": "crowd_part_for_hero",
        "group": "crowd", "tier": "pro",
        "label": {"en": "The crowd parts", "ru": "Толпа расступается"},
        "desc": {"en": "Status shown by other people's behaviour instead of by props.",
                 "ru": "Статус, показанный поведением других людей, а не реквизитом."},
        "shot": "wide", "camera": "static, chest level, subject moves toward camera",
        "first": "Wide shot in {location} at {time}: a dense crowd of {crowd} fills the frame edge to edge, packed "
                 "and facing away, with {character} just visible deep inside it and no clear path through.",
        "last": "The same dense crowd of {crowd} in the same {location} at the same {time} under the same light, "
                "now opened into a corridor down the centre of the frame with {character} standing in the gap, "
                "closer to the camera.",
        "motion": "The crowd opens outward from the centre and {character} moves forward into the gap, stopping "
                  "when the corridor is clear; the crowd settles into its new shape.",
        "solo": "The crowd shifts outward from the centre, opening a corridor, and {character} steps forward into "
                "it and stops. The camera stays where it is.",
        "bracket": "[Static shot]",
        "note": {"en": "A path opens through the crowd", "ru": "В толпе открывается коридор"},
        "negative": "counted people, duplicated faces, merging bodies, warped limbs, empty background",
        "slots": ["crowd", "character", "location", "time"],
        "traits": ["locked", "crowd", "wide_frame"],
        "needs_last": True, "engines": _CHEAP,
        "fits_with": ["m_push_settle", "m_body_wipe", "l_rim_back", "l_practical_only"],
        "conflicts_with": ["m_rack_focus", "l_chiaroscuro"],
        "styles_fit": ["cinema", "munir", "spike", "punkrf", "fanuel", "flat2d"],
        "tags": ["epic", "crowd", "saturated"],
    },
    {
        "key": "crowd_one_still",
        "group": "crowd", "tier": "free",
        "label": {"en": "Everyone moves, he does not", "ru": "Все движутся, он стоит"},
        "desc": {"en": "One motionless figure in a moving mass. Reads instantly at thumbnail size.",
                 "ru": "Одна неподвижная фигура в движущейся массе. Читается сразу в размере превью."},
        "shot": "wide", "camera": "static, slightly high",
        "first": "Wide shot of {location} at {time} filled with a dense stream of {crowd} moving through the frame "
                 "in both directions, {character} standing motionless in the middle of the stream facing the "
                 "camera, everyone else blurred by their own movement.",
        "last": "The same {location} at the same {time} with the same dense stream of {crowd} and the same light, "
                "{character} still motionless in the same spot while the people around them are in completely "
                "different positions.",
        "motion": "The crowd streams past in both directions while {character} stays completely still; the flow "
                  "thins for a moment around them and then settles back.",
        "solo": "The crowd streams past in both directions around {character}, who stays completely still; the flow "
                "thins for a moment and then settles. The camera does not move.",
        "bracket": "[Static shot]",
        "note": {"en": "Still figure in a moving crowd", "ru": "Неподвижный в потоке"},
        "negative": "hero moving, counted people, duplicated faces, morphing coats, camera drift",
        "slots": ["crowd", "character", "location", "time"],
        "traits": ["locked", "crowd", "wide_frame"],
        "needs_last": True, "engines": _CHEAP,
        "fits_with": ["m_push_settle", "m_freeze_settle", "l_overcast_flat", "l_practical_only"],
        "conflicts_with": ["m_steadi_follow", "m_arc_quarter"],
        "styles_fit": ["cinema", "longheads", "katsumi", "noir", "flat2d"],
        "tags": ["deadpan", "crowd", "muted"],
    },
    {
        "key": "crowd_hands_up",
        "group": "crowd", "tier": "free",
        "label": {"en": "Hands up", "ru": "Руки вверх"},
        "desc": {"en": "The concert frame. Shot from inside the crowd, never from the stage.",
                 "ru": "Концертный кадр. Снимается изнутри толпы и никогда со сцены."},
        "shot": "medium", "camera": "handheld, held above head height",
        "first": "Medium shot from inside a dense crowd of {crowd} in {location} at {time}, camera held above head "
                 "height: forearms and raised hands fill the lower half of the vertical frame, a strong light "
                 "source behind the crowd turning everyone into silhouettes.",
        "last": "The same dense crowd of {crowd} in the same {location} at the same {time} with the same backlight, "
                "the arms now dropped to shoulder height and the silhouettes packed closer together.",
        "motion": "The raised arms drop together to shoulder height and the crowd presses forward a step, then "
                  "settles; a small handheld sway stays in the shot.",
        "solo": "The raised arms drop together to shoulder height and the crowd presses forward a step and settles. "
                "A slight handheld sway stays in the frame.",
        "bracket": "[Shake]",
        "note": {"en": "Raised arms inside the crowd", "ru": "Поднятые руки изнутри толпы"},
        "negative": "stage view, counted people, warped fingers, readable banners, clean tripod look",
        "slots": ["crowd", "location", "time"],
        "traits": ["handheld", "crowd", "night"],
        "needs_last": True, "engines": _CHEAP,
        "fits_with": ["m_handheld_drift", "m_freeze_settle", "l_rim_back", "l_screen_glow"],
        "conflicts_with": ["m_push_settle", "l_overcast_flat"],
        "styles_fit": ["punkrf", "cinema", "spike", "munir", "noir"],
        "tags": ["epic", "neon", "crowd"],
    },
    {
        "key": "crowd_queue_line",
        "group": "crowd", "tier": "free",
        "label": {"en": "The queue", "ru": "Очередь"},
        "desc": {"en": "A crowd arranged in a line reads as order, waiting and rules.",
                 "ru": "Толпа, выстроенная в линию, читается как порядок, ожидание и правила."},
        "shot": "wide", "camera": "static, low, along the line",
        "first": "Wide shot along a queue of {crowd} standing in {location} at {time}, the line running away from "
                 "the camera along the vertical of the frame, everyone facing the same direction, {character} "
                 "somewhere in the middle of it and indistinguishable from the rest.",
        "last": "The same queue of {crowd} in the same {location} at the same {time} in the same light, the whole "
                "line shifted forward by one place so {character} now stands nearer the front.",
        "motion": "The whole line shuffles forward one place and stops together; only feet and shoulders move and "
                  "the shot settles as the line closes up.",
        "solo": "The whole line shuffles forward one place and stops together, feet and shoulders moving and then "
                "settling. The camera stays low and still.",
        "bracket": "[Static shot]",
        "note": {"en": "Queue moves up one place", "ru": "Очередь сдвинулась на шаг"},
        "negative": "counted people, duplicated faces, readable signage, crowd dispersing, camera pan",
        "slots": ["crowd", "character", "location", "time"],
        "traits": ["locked", "crowd", "wide_frame", "slow"],
        "needs_last": True, "engines": _CHEAP,
        "fits_with": ["m_pan_link", "m_weight_step", "l_overcast_flat", "l_practical_only"],
        "conflicts_with": ["m_arc_quarter", "m_whip_out"],
        "styles_fit": ["longheads", "cinema", "katsumi", "flat2d", "noir", "pixar"],
        "tags": ["deadpan", "crowd", "muted"],
    },

    # ══════════════ ИНТЕРЬЕРЫ ══════════════
    {
        "key": "interior_window_side",
        "group": "interior", "tier": "free",
        "label": {"en": "By the window", "ru": "У окна"},
        "desc": {"en": "One window, one person, one direction of light. The most reliable interior there is.",
                 "ru": "Одно окно, один человек, одно направление света. Самый надёжный интерьер."},
        "shot": "medium", "camera": "static, eye level",
        "first": "Medium shot inside {location} at {time}: {character} stands beside a tall window in profile, lit "
                 "entirely from the window on one side while the rest of the room falls into shadow, dust visible "
                 "in the shaft of light, the window filling the left of the vertical frame.",
        "last": "The same {character} beside the same window in the same {location} at the same {time}, same light "
                "direction, now turned toward the window with the forehead almost touching the glass and the face "
                "fully in the light.",
        "motion": "{character} turns toward the window and leans until the forehead nearly touches the glass, then "
                  "stops; the dust in the light drifts on undisturbed.",
        "solo": "{character} turns toward the window and leans in until the forehead nearly touches the glass, then "
                "stops. Dust drifts in the shaft of light and the camera stays still.",
        "bracket": "[Static shot]",
        "note": {"en": "Window light on one side", "ru": "Свет из окна с одной стороны"},
        "negative": "second light source, flat exposure, warped window frame, readable view outside, camera drift",
        "slots": ["character", "location", "time"],
        "traits": ["locked", "interior", "solo", "soft_light", "slow"],
        "needs_last": True, "engines": _CHEAP,
        "fits_with": ["m_push_settle", "m_breath_fog", "l_window_motivated", "l_soft_wrap"],
        "conflicts_with": ["m_steadi_follow", "l_deep_night"],
        "styles_fit": ["cinema", "katsumi", "ghibli", "shinkai", "longheads", "clay", "pixar"],
        "tags": ["melancholy", "warm", "cozy"],
    },
    {
        "key": "interior_doorframe",
        "group": "interior", "tier": "free",
        "label": {"en": "Framed by the doorway", "ru": "В дверном проёме"},
        "desc": {"en": "A frame inside the frame. Costs nothing and makes any room look composed.",
                 "ru": "Рамка внутри рамки. Не стоит ничего и делает любую комнату выстроенной."},
        "shot": "wide", "camera": "static, centred on the doorway",
        "first": "Wide shot from a dark room in {location} at {time} looking through an open doorway into a lit "
                 "space beyond: the doorway sits centred in the vertical frame, the near room black and empty, "
                 "{character} standing in the bright rectangle beyond, small and centred.",
        "last": "The same doorway in the same {location} at the same {time} with the same lit space beyond, now "
                "with {character} standing right in the opening, filling most of the bright rectangle.",
        "motion": "{character} walks from the far side of the lit room to the doorway and stops in the opening, "
                  "the silhouette growing until it fills the rectangle.",
        "solo": "{character} walks toward the camera through the lit room, stops in the doorway and stands still, "
                "filling the bright rectangle. The camera does not move.",
        "bracket": "[Static shot]",
        "note": {"en": "Doorway as a frame within the frame", "ru": "Проём как рамка в кадре"},
        "negative": "light in the near room, warped doorframe, second figure, camera drift, duplicate doors",
        "slots": ["character", "location", "time"],
        "traits": ["locked", "interior", "low_key", "solo"],
        "needs_last": True, "engines": _CHEAP,
        "fits_with": ["m_push_settle", "m_enter_frame", "l_chiaroscuro", "l_practical_only"],
        "conflicts_with": ["m_handheld_drift", "l_overcast_flat"],
        "styles_fit": ["noir", "cinema", "fanuel", "dreamclad", "munir", "clay", "pixar"],
        "tags": ["menacing", "monochrome", "night"],
    },
    {
        "key": "interior_table_sit",
        "group": "interior", "tier": "free",
        "label": {"en": "At the table", "ru": "За столом"},
        "desc": {"en": "The most ordinary interior scene, which is exactly why it earns the contrast later.",
                 "ru": "Самая обычная интерьерная сцена — именно поэтому она зарабатывает контраст дальше."},
        "shot": "medium", "camera": "static, table height",
        "first": "Medium shot at table height inside {location} at {time}: {character} sits at a table with {prop} "
                 "in front of them, elbows down, one practical lamp above the table lighting only the tabletop and "
                 "the face, the rest of the room dark.",
        "last": "The same {character} at the same table in the same {location} at the same {time} under the same "
                "lamp, now leaned back away from the table with the hands off it and {prop} left untouched.",
        "motion": "{character} leans back from the table, taking the hands off it, and stops there; {prop} stays "
                  "exactly where it was and the lamp above does not move.",
        "solo": "{character} leans back from the table, lifting the hands off it, and stops. {prop} stays exactly "
                "where it is and the camera holds still.",
        "bracket": "[Static shot]",
        "note": {"en": "Seated at a lit table", "ru": "Сидит за освещённым столом"},
        "negative": "overhead room light, second person, warped tabletop, readable print, camera pan",
        "slots": ["character", "prop", "location", "time"],
        "traits": ["locked", "interior", "solo", "low_key"],
        "needs_last": True, "engines": _CHEAP,
        "fits_with": ["m_shoulder_drop", "m_hand_raise", "l_practical_only", "l_hard_single"],
        "conflicts_with": ["m_crane_rise", "l_harsh_noon"],
        "styles_fit": ["cinema", "noir", "longheads", "katsumi", "clay", "pixar", "embroidery"],
        "tags": ["melancholy", "warm", "cozy"],
    },
    {
        "key": "interior_mirror",
        "group": "interior", "tier": "pro",
        "label": {"en": "In the mirror", "ru": "В зеркале"},
        "desc": {"en": "Two versions of the same person in one frame without any effect at all.",
                 "ru": "Две версии одного человека в кадре вообще без эффектов."},
        "shot": "medium", "camera": "static, slightly off-axis to the glass",
        "first": "Medium shot inside {location} at {time}: {character} stands in front of a mirror seen slightly "
                 "from the side, so both the back of the head and the reflected face are in the vertical frame, "
                 "one lamp beside the mirror lighting the reflection more than the person.",
        "last": "The same {character} in front of the same mirror in the same {location} at the same {time} with "
                "the same lamp, now leaned in close to the glass so the reflected face fills most of the frame and "
                "the real head is cropped at the edge.",
        "motion": "{character} leans in toward the mirror until the reflection fills the frame, then stops; the "
                  "reflection moves exactly with the body and nothing else changes.",
        "solo": "{character} leans in toward the mirror until the reflection fills the frame and stops. The "
                "reflection moves exactly with the body and the camera stays still.",
        "bracket": "[Static shot]",
        "note": {"en": "Person and reflection in one frame", "ru": "Человек и отражение в одном кадре"},
        "negative": "reflection out of sync, camera visible in mirror, doubled room, warped glass, extra person",
        "slots": ["character", "location", "time"],
        "traits": ["locked", "interior", "solo", "hero_face"],
        "needs_last": True, "engines": _CHEAP,
        "fits_with": ["m_push_settle", "m_turn_to_lens", "l_practical_only", "l_screen_glow"],
        "conflicts_with": ["m_arc_quarter", "m_steadi_follow"],
        "styles_fit": ["cinema", "noir", "dreamclad", "katsumi", "longheads"],
        "tags": ["melancholy", "muted", "deadpan"],
    },

    # ══════════════ НОЧЬ И СВЕТ ══════════════
    {
        "key": "night_lamp_pass",
        "group": "night", "tier": "free",
        "label": {"en": "Under the lamp", "ru": "Под фонарём"},
        "desc": {"en": "Night is a sequence of lit islands. Walking between two of them is a whole scene.",
                 "ru": "Ночь — это цепочка освещённых островов. Пройти между двумя из них — уже целая сцена."},
        "shot": "medium", "camera": "static, low, subject walks into the light",
        "first": "Medium shot in {location} at {time}: {character} walks in near darkness between two street "
                 "lamps, only the outline of the shoulders catching the light from behind, the pool of lamp light "
                 "waiting empty in the lower part of the vertical frame.",
        "last": "The same {character} in the same street in {location} at the same {time}, same clothing and the "
                "same two lamps, now standing inside the pool of light with the face lit hard from directly above "
                "and the eye sockets in shadow.",
        "motion": "{character} walks forward into the pool of lamp light and stops inside it; the light from above "
                  "hardens across the face as the movement settles.",
        "solo": "{character} walks forward into the pool of lamp light and stops inside it, the overhead light "
                "hardening across the face. The camera stays low and still.",
        "bracket": "[Static shot]",
        "note": {"en": "Walking into the lamp light", "ru": "Входит в пятно фонаря"},
        "negative": "even street lighting, second light source, warped face, camera movement, daylight",
        "slots": ["character", "location", "time"],
        "traits": ["locked", "night", "exterior", "solo", "hard_light"],
        "needs_last": True, "engines": _CHEAP,
        "fits_with": ["m_weight_step", "m_breath_fog", "l_practical_only", "l_deep_night"],
        "conflicts_with": ["l_harsh_noon", "l_overcast_flat"],
        "styles_fit": ["noir", "cinema", "dreamclad", "punkrf", "longheads"],
        "tags": ["menacing", "night", "street"],
    },
    {
        "key": "night_headlights",
        "group": "night", "tier": "free",
        "label": {"en": "Headlights across the wall", "ru": "Фары по стене"},
        "desc": {"en": "Time passing inside a still frame: light moves, nothing else has to.",
                 "ru": "Время внутри статичного кадра: движется свет, больше ничему двигаться не нужно."},
        "shot": "medium", "camera": "static, facing the wall",
        "first": "Medium shot in {location} at {time}: {character} stands with their back against a bare wall in "
                 "near darkness, only a weak spill of light on the shoulders, the wall beside them unlit and "
                 "empty in the vertical frame.",
        "last": "The same {character} against the same wall in the same {location} at the same {time}, now with a "
                "hard beam of vehicle light crossing the wall behind them and their shadow thrown long and sharp "
                "along it.",
        "motion": "A beam of vehicle light sweeps across the wall from one side to the other, dragging the shadow "
                  "of {character} along it, and the darkness settles back as it passes.",
        "solo": "A beam of vehicle light sweeps across the wall behind {character}, dragging their shadow with it, "
                "and the darkness settles back once it passes. The camera does not move.",
        "bracket": "[Static shot]",
        "note": {"en": "Headlights sweep the wall", "ru": "Фары проходят по стене"},
        "negative": "visible car, even lighting, flickering, warped shadow, camera pan",
        "slots": ["character", "location", "time"],
        "traits": ["locked", "night", "exterior", "solo", "hard_light"],
        "needs_last": True, "engines": _CHEAP,
        "fits_with": ["m_freeze_settle", "m_smoke_curl", "l_deep_night", "l_chiaroscuro"],
        "conflicts_with": ["l_harsh_noon", "l_golden_hour"],
        "styles_fit": ["noir", "cinema", "dreamclad", "munir", "punkrf"],
        "tags": ["menacing", "night", "monochrome"],
    },
    {
        "key": "night_screen_face",
        "group": "night", "tier": "free",
        "label": {"en": "Lit by a screen", "ru": "Свет от экрана"},
        "desc": {"en": "The most contemporary light there is, and the cheapest to describe: one source, from below.",
                 "ru": "Самый современный свет и самый дешёвый в описании: один источник, снизу."},
        "shot": "close-up", "camera": "static, eye level",
        "first": "Close-up of {character} in {location} at {time} with the face lit only from below by a small "
                 "screen held out of frame, the light cold and moving slightly, everything above the eyebrows "
                 "falling away into darkness, {emotion}.",
        "last": "The same {character} in the same {location} at the same {time}, lit by the same screen from "
                "below, now with the head lifted so the light falls on the throat and jaw instead of the eyes, "
                "{emotion}.",
        "motion": "The screen light flickers as its content changes while {character} lifts the head away from it "
                  "and stops, leaving the eyes in darkness.",
        "solo": "The screen light flickers as its content changes and {character} slowly lifts the head away from "
                "it and stops, the eyes falling into darkness. The camera stays still.",
        "bracket": "[Static shot]",
        "note": {"en": "Face lit by a screen from below", "ru": "Лицо от экрана снизу"},
        "negative": "visible phone screen content, readable text, second light source, warped eyes, camera drift",
        "slots": ["character", "location", "time", "emotion"],
        "traits": ["locked", "night", "close", "solo", "hero_face"],
        "needs_last": True, "engines": _CHEAP,
        "fits_with": ["m_shoulder_drop", "m_breath_fog", "l_screen_glow", "l_deep_night"],
        "conflicts_with": ["l_harsh_noon", "l_golden_hour"],
        "styles_fit": ["cinema", "noir", "dreamclad", "katsumi", "longheads"],
        "tags": ["melancholy", "cold", "night"],
    },
    {
        "key": "night_neon_wall",
        "group": "night", "tier": "pro",
        "label": {"en": "Against the signs", "ru": "У вывесок"},
        "desc": {"en": "Coloured practicals in frame do the work a lighting rig would otherwise do.",
                 "ru": "Цветные источники прямо в кадре делают работу, ради которой иначе нужен был бы свет."},
        "shot": "medium", "camera": "static, eye level",
        "first": "Medium shot of {character} standing with their back to a wall of glowing signs in {location} at "
                 "{time}, the signs out of focus behind them, their light falling on one side of the face and "
                 "leaving the other in shadow, {accent} dominating the glow.",
        "last": "The same {character} against the same wall of signs in the same {location} at the same {time}, "
                "same {accent} glow, now turned so the lit side of the face is away from the camera and the "
                "shadowed side is toward it.",
        "motion": "{character} turns in place so the lit and shadowed halves of the face swap, then stops; the "
                  "signs behind keep their steady glow.",
        "solo": "{character} turns in place so the lit and shadowed halves of the face swap, then stops. The signs "
                "behind keep glowing steadily and the camera does not move.",
        "bracket": "[Static shot]",
        "note": {"en": "Turning against lit signs", "ru": "Разворот у светящихся вывесок"},
        "negative": "readable signage, flickering strobe, even lighting, warped face, camera pan",
        "slots": ["character", "location", "time", "accent"],
        "traits": ["locked", "night", "exterior", "solo"],
        "needs_last": True, "engines": _CHEAP,
        "fits_with": ["m_turn_to_lens", "m_hair_lag", "l_one_accent", "l_deep_night"],
        "conflicts_with": ["l_harsh_noon", "l_overcast_flat"],
        "styles_fit": ["dreamclad", "noir", "punkrf", "cinema", "munir"],
        "tags": ["neon", "night", "menacing"],
    },
    {
        "key": "night_rain_reflect",
        "group": "night", "tier": "free",
        "label": {"en": "Rain and reflections", "ru": "Дождь и отражения"},
        "desc": {"en": "Wet ground doubles every light source. Free production value in one word.",
                 "ru": "Мокрая земля удваивает каждый источник света. Бесплатный продакшн в одном слове."},
        "shot": "wide", "camera": "static, very low, close to the ground",
        "first": "Wide shot from ground level in {location} at {time} during {weather}: the wet surface fills the "
                 "lower half of the vertical frame and holds the reflection of every light, {character} standing "
                 "far off in the upper half, small between the lights.",
        "last": "The same {location} at the same {time} in the same {weather} from the same ground-level angle, the "
                "reflections unchanged, now with {character} much nearer the camera and their reflection stretched "
                "toward the lens across the wet surface.",
        "motion": "{character} walks toward the camera across the wet ground and stops; the reflection stretches "
                  "toward the lens and the rings from the rain settle around their feet.",
        "solo": "{character} walks toward the camera across the wet ground and stops, their reflection stretching "
                "toward the lens while rain rings settle around their feet. The camera stays low.",
        "bracket": "[Static shot]",
        "note": {"en": "Low angle on wet ground", "ru": "Нижний ракурс по мокрому"},
        "negative": "dry ground, mismatched reflection, warped puddles, umbrella, camera shake",
        "slots": ["character", "location", "time", "weather"],
        "traits": ["locked", "night", "exterior", "weather", "wide_frame"],
        "needs_last": True, "engines": _CHEAP,
        "fits_with": ["m_water_drip", "m_weight_step", "l_deep_night", "l_practical_only"],
        "conflicts_with": ["l_harsh_noon", "l_golden_hour"],
        "styles_fit": ["noir", "dreamclad", "cinema", "shinkai", "punkrf"],
        "tags": ["melancholy", "neon", "night"],
    },

    # ══════════════ ФИНАЛЬНЫЕ КАДРЫ ══════════════
    {
        "key": "final_pull_to_wide",
        "group": "final", "tier": "free",
        "label": {"en": "Pull back to nothing", "ru": "Отъезд в пустоту"},
        "desc": {"en": "The character becomes small. The oldest ending in the book and it still works.",
                 "ru": "Герой становится маленьким. Древнейший финал на свете — и он работает."},
        "shot": "medium", "camera": "slow pull-back to wide",
        "first": "Medium shot of {character} standing still in {location} at {time}, framed from the waist up, "
                 "facing the camera, the background reading only as texture behind them.",
        "last": "The same {character} in the same {location} at the same {time} in the same light, now tiny and "
                "off-centre in the lower third of a very wide frame, the whole space open around them.",
        "motion": "The camera retreats in a straight line until the space opens out around {character}, then "
                  "settles; the figure stays exactly where it is and does not walk away.",
        "solo": "The camera retreats in a straight line away from {character}, opening the space around them, and "
                "settles when the frame is wide. {character} stays exactly where they are.",
        "bracket": "[Pull out]",
        "note": {"en": "Pull back until the hero is small", "ru": "Отъезд, пока герой не станет мелким"},
        "negative": "subject walking away, zoom artefacts, warped background, camera roll, cut",
        "slots": ["character", "location", "time"],
        "traits": ["moving_camera", "wide_frame", "slow"],
        "needs_last": True, "engines": _CHEAP,
        "fits_with": ["m_pull_open", "m_coat_inertia", "l_blue_hour", "l_overcast_flat"],
        "conflicts_with": ["m_push_settle", "m_whip_out"],
        "styles_fit": ["cinema", "shinkai", "longheads", "noir", "ghibli"],
        "tags": ["melancholy", "muted", "epic"],
    },
    {
        "key": "final_walk_out_frame",
        "group": "final", "tier": "free",
        "label": {"en": "Leaving the empty frame", "ru": "Пустой кадр после ухода"},
        "desc": {"en": "The person leaves and the camera stays. The last second is a place with nobody in it.",
                 "ru": "Человек уходит, камера остаётся. Последняя секунда — место, в котором никого."},
        "shot": "wide", "camera": "static, eye level",
        "first": "Wide static shot of {location} at {time} with {character} standing in the centre of the vertical "
                 "frame, facing away, the space around them composed and symmetrical.",
        "last": "The identical frame of the same {location} at the same {time} with the same light and the same "
                "composition, now completely empty: {character} has left the shot and nothing has been rearranged.",
        "motion": "{character} walks out of the frame at one side and the camera holds on the empty space until "
                  "the movement is gone and only the place remains.",
        "solo": "{character} walks out of frame to one side while the camera holds still, and the shot rests on "
                "the empty place after they are gone.",
        "bracket": "[Static shot]",
        "note": {"en": "Character exits, empty frame holds", "ru": "Герой выходит, кадр остаётся пустым"},
        "negative": "camera following, second person entering, changed background, warped architecture",
        "slots": ["character", "location", "time"],
        "traits": ["locked", "wide_frame", "slow"],
        "needs_last": True, "engines": _CHEAP,
        "fits_with": ["m_coat_inertia", "m_smoke_curl", "l_blue_hour", "l_practical_only"],
        "conflicts_with": ["m_steadi_follow", "m_push_settle"],
        "styles_fit": ["cinema", "longheads", "noir", "katsumi", "shinkai", "flat2d", "pixar"],
        "tags": ["melancholy", "deadpan", "muted"],
    },
    {
        "key": "final_rhyme_open",
        "group": "final", "tier": "pro",
        "label": {"en": "Rhyme with the opening", "ru": "Рифма с первым кадром"},
        "desc": {"en": "The same composition as scene one, with one thing changed. Makes a clip feel written.",
                 "ru": "Та же композиция, что в первой сцене, с одним изменением. От этого клип кажется написанным."},
        "shot": "establishing", "camera": "static, matching the opening angle",
        "first": "Establishing shot of {location} at {time} repeating the exact composition of the opening scene: "
                 "same camera position, same symmetry, same vanishing point, with {character} standing where the "
                 "clip began and the light now coming from the opposite side.",
        "last": "The same composition of the same {location} at the same {time} from the same camera position, "
                "with the same opposite-side light, now with {character} gone and only the place left standing.",
        "motion": "{character} steps out of the composition and the frame holds, matching the opening shot exactly "
                  "except that the light comes from the other side; the shot settles empty.",
        "solo": "{character} steps out of the composition while the camera holds the exact opening framing, and "
                "the shot settles on the place with the light coming from the other side.",
        "bracket": "[Static shot]",
        "note": {"en": "Closing frame rhyming with the opening", "ru": "Финал рифмуется с открытием"},
        "negative": "different angle, different lens, new objects, camera movement, warped architecture",
        "slots": ["character", "location", "time"],
        "traits": ["locked", "wide_frame", "slow"],
        "needs_last": True, "engines": _CHEAP,
        "fits_with": ["m_pull_open", "m_smoke_curl", "l_first_light", "l_blue_hour"],
        "conflicts_with": ["m_handheld_drift", "m_whip_out"],
        "styles_fit": ["cinema", "shinkai", "longheads", "fanuel", "ghibli", "flat2d", "clay"],
        "tags": ["nostalgic", "muted", "epic"],
    },
    {
        "key": "final_last_look",
        "group": "final", "tier": "free",
        "label": {"en": "The last look", "ru": "Последний взгляд"},
        "desc": {"en": "Eye contact on the final bar, then darkness. The most direct ending available.",
                 "ru": "Зрительный контакт на последнем такте — и темнота. Самый прямой финал из возможных."},
        "shot": "extreme close-up", "camera": "static, eye level",
        "first": "Extreme close-up of {character}'s face in {location}, eyes looking slightly off the lens, "
                 "{emotion}, one hard light from the side leaving half the face dark, the background completely "
                 "unreadable behind.",
        "last": "The same {character} in the same {location} under the same hard side light and the same framing, "
                "the eyes now looking directly into the lens and the face otherwise unchanged, {emotion}.",
        "motion": "The eyes of {character} come round to the lens and stop there; nothing else moves and the "
                  "shot holds on the look.",
        "solo": "The eyes of {character} come round to the lens and stop there, holding the look while nothing "
                "else in the frame moves.",
        "bracket": "[Static shot]",
        "note": {"en": "Eyes find the lens and hold", "ru": "Глаза находят объектив и держат"},
        "negative": "smiling, blinking repeatedly, head turning, warped iris, camera movement",
        "slots": ["character", "location", "emotion"],
        "traits": ["locked", "close", "hero_face", "solo"],
        "needs_last": True, "engines": _CHEAP,
        "fits_with": ["m_eyes_to_lens", "m_freeze_settle", "l_hard_single", "l_rim_back"],
        "conflicts_with": ["m_steadi_follow", "m_crane_rise"],
        "styles_fit": ["cinema", "noir", "punkrf", "munir", "fanuel"],
        "tags": ["menacing", "deadpan", "monochrome"],
    },

    # ══════════════ ПЕРЕХОДЫ МЕЖДУ ЛОКАЦИЯМИ ══════════════
    {
        "key": "bridge_body_wipe",
        "group": "bridge", "tier": "pro",
        "label": {"en": "Wiped by a body", "ru": "Перекрытие телом"},
        "desc": {"en": "Something crosses the lens and hides the cut. Two locations become one movement.",
                 "ru": "Что-то проходит перед объективом и прячет склейку. Две локации становятся одним движением."},
        "shot": "medium", "camera": "static, subject crosses close to the lens",
        "first": "Medium shot in {location} at {time}: {character} stands in the middle distance while a dark "
                 "shape — a passing figure — enters the very close foreground at one edge, already blurred by its "
                 "own speed.",
        "last": "The same framing in the same {location} at the same {time} with the same light, now completely "
                "filled edge to edge by that dark blurred shape, {character} no longer visible behind it.",
        "motion": "The dark shape crosses the lens and fills the frame completely, then stops filling it, leaving "
                  "the shot dark and unreadable at the end.",
        "solo": "A dark blurred shape crosses close to the lens, fills the whole frame and holds there, leaving "
                "the shot dark and unreadable. The camera does not move.",
        "bracket": "[Static shot]",
        "note": {"en": "Foreground body wipes the frame", "ru": "Тело закрывает кадр"},
        "negative": "clean visible cut, sharp foreground, recognisable second face, camera pan, flicker",
        "slots": ["character", "location", "time"],
        "traits": ["locked", "fast"],
        "needs_last": True, "engines": _CHEAP,
        "fits_with": ["m_body_wipe", "m_whip_out", "l_practical_only", "l_deep_night"],
        "conflicts_with": ["m_push_settle", "m_rack_focus"],
        "styles_fit": ["cinema", "noir", "punkrf", "munir", "spike"],
        "tags": ["menacing", "street", "night"],
    },
    {
        "key": "bridge_match_shape",
        "group": "bridge", "tier": "pro",
        "label": {"en": "Same shape, other place", "ru": "Та же форма, другое место"},
        "desc": {"en": "End one location on a shape, open the next on the same shape. The cut disappears.",
                 "ru": "Заканчиваем локацию формой и открываем следующую той же формой. Склейка исчезает."},
        "shot": "close-up", "camera": "static, centred on the shape",
        "first": "Close-up in {location} at {time} of a bright circular shape dead centre of the vertical frame — "
                 "a lamp, a ring, a hole in a wall — with the rest of the frame dark and unreadable around it.",
        "last": "The same circular shape in the same position and the same size in the centre of the frame in the "
                "same {location} at the same {time}, now read as a different object entirely, with a hand of "
                "{character} entering beside it.",
        "motion": "The bright circular shape holds dead centre while everything around it shifts and a hand enters "
                  "beside it; the shape does not move and the frame settles around it.",
        "solo": "The bright circular shape holds dead centre while the surroundings shift around it and a hand of "
                "{character} enters beside it, then everything settles.",
        "bracket": "[Static shot]",
        "note": {"en": "Matching shape across the cut", "ru": "Совпадение формы на склейке"},
        "negative": "shape moving off centre, size change, duplicated shapes, camera drift, readable text",
        "slots": ["character", "location", "time"],
        "traits": ["locked", "close", "object"],
        "needs_last": True, "engines": _CHEAP,
        "fits_with": ["m_rack_focus", "m_blur_resolve", "l_chiaroscuro", "l_hard_single"],
        "conflicts_with": ["m_steadi_follow", "m_arc_quarter"],
        "styles_fit": ["cinema", "flat2d", "noir", "katsumi", "dreamclad"],
        "tags": ["deadpan", "monochrome", "night"],
    },
    {
        "key": "bridge_door_through",
        "group": "bridge", "tier": "free",
        "label": {"en": "Through the door", "ru": "Через дверь"},
        "desc": {"en": "Enter one place, come out somewhere else. The cheapest teleport in film.",
                 "ru": "Вошёл в одном месте, вышел в другом. Самая дешёвая телепортация в кино."},
        "shot": "medium", "camera": "static, behind the character",
        "first": "Medium shot from behind {character} in {location} at {time} as they reach for the handle of a "
                 "closed door that fills the upper half of the vertical frame, the light around them coming from "
                 "behind the camera.",
        "last": "The same {character} seen from behind in the same {location} at the same {time}, same clothing, "
                "the door now open in front of them and a completely different space beyond it, brighter than the "
                "room they are standing in.",
        "motion": "{character} pulls the door open and light from the space beyond spills over them; they stop on "
                  "the threshold without stepping through.",
        "solo": "{character} pulls the door open, light from beyond spills over them, and they stop on the "
                "threshold without stepping through. The camera stays behind them.",
        "bracket": "[Static shot]",
        "note": {"en": "Door opens onto a new space", "ru": "Дверь открывается в другое пространство"},
        "negative": "face visible, warped door, duplicate handles, dark space beyond, camera movement",
        "slots": ["character", "location", "time"],
        "traits": ["locked", "interior", "solo"],
        "needs_last": True, "engines": _CHEAP,
        "fits_with": ["m_enter_frame", "m_hand_raise", "l_chiaroscuro", "l_window_motivated"],
        "conflicts_with": ["m_arc_quarter", "m_truck_side"],
        "styles_fit": ["cinema", "noir", "ghibli", "dreamclad", "clay", "pixar", "flat2d"],
        "tags": ["nostalgic", "warm", "night"],
    },
    {
        "key": "bridge_light_to_dark",
        "group": "bridge", "tier": "free",
        "label": {"en": "Out of light into dark", "ru": "Из света в темноту"},
        "desc": {"en": "The exposure change carries the cut. Works between any two locations.",
                 "ru": "Склейку тянет перепад экспозиции. Работает между любыми двумя локациями."},
        "shot": "medium", "camera": "static, subject walks away from the light",
        "first": "Medium shot in {location} at {time}: {character} stands in a bright patch of open light with the "
                 "mouth of a dark passage directly behind them in the vertical frame, the boundary between the two "
                 "cutting the shot in half.",
        "last": "The same {character} in the same {location} at the same {time}, same clothing, now inside the "
                "dark passage with only the outline of the shoulders and hair catching the light left behind.",
        "motion": "{character} walks backwards out of the bright patch into the dark passage until only the "
                  "outline is left, then stops; the light behind stays exactly as it was.",
        "solo": "{character} walks away from the camera out of the bright patch into the dark passage until only "
                "an outline is left, then stops. The camera does not move.",
        "bracket": "[Static shot]",
        "note": {"en": "Crossing from light into shadow", "ru": "Переход из света в тень"},
        "negative": "even exposure, fill light in the passage, warped silhouette, camera follow, flicker",
        "slots": ["character", "location", "time"],
        "traits": ["locked", "low_key", "solo"],
        "needs_last": True, "engines": _CHEAP,
        "fits_with": ["m_blur_resolve", "m_coat_inertia", "l_chiaroscuro", "l_hard_single"],
        "conflicts_with": ["l_overcast_flat", "l_soft_wrap"],
        "styles_fit": ["noir", "cinema", "dreamclad", "munir", "fanuel"],
        "tags": ["menacing", "monochrome", "night"],
    },
]


# ─────────────────────────────────────────────────────────────────────────────
# 3. ПРОМТЫ ДВИЖЕНИЯ — 32 карточки.
#
# Единица: ТОЛЬКО motion_prompt. Кадры карточка не трогает — она кладётся на
# уже собранную сцену и отвечает на один вопрос: что происходит между первым и
# последним кадром.
#
# Три текста в каждой карточке, и это не избыточность, а три разных движка:
#   text     — пара кадров (Seedance, Kling, MiniMax). Описывает РАЗНИЦУ.
#   solo     — Grok. Он оживляет только первый кадр, поэтому фраза обязана быть
#              самодостаточной: «она поворачивается к камере и замирает», а не
#              «между кадрами она поворачивается».
#   bracket  — MiniMax H3 читает команды камеры в квадратных скобках буквально
#              и по описанию словами их не восстанавливает.
#
# ЖЁСТКИЕ ПРАВИЛА, зашитые в тексты и проверяемые машинно:
#   • ОДНО движение камеры на карточку. Два движения модели не исполняют — они
#     смешивают их в кашу. Поэтому «наезд с облётом» здесь не существует.
#   • У каждого движения ЕСТЬ КОНЕЦ: settles / stops / holds / comes to rest.
#     Движение без конечного состояния подвешивает генерацию и даёт дрейф.
#   • Орбита — не больше четверти оборота на сцену 5-6 секунд. Полный облёт
#     разваливает геометрию лица, это видно уже на трети.
#   • Никаких ссылок на соседние сцены. Модель анимирует кадр изолированно.
#   • Никаких градусов и метров: «на четверть оборота» модель понимает, «на 84°»
#     игнорирует.
#
# `physics` — не украшение карточки, а её содержание: вторичное движение (ткань,
# волосы, пыль, дыхание) это то, из-за чего кадр читается снятым, а не собранным.
# Модель почти всегда сама его не добавит.
# ─────────────────────────────────────────────────────────────────────────────
MOTION_GROUPS = [
    {"key": "camera",     "label": {"en": "Camera", "ru": "Камера"},
     "hint": {"en": "One move per scene. Amplitude in words, never in degrees.",
              "ru": "Одно движение на сцену. Амплитуда словами, никаких градусов."}},
    {"key": "body",       "label": {"en": "The body", "ru": "Тело"},
     "hint": {"en": "What the person does when the camera does nothing.",
              "ru": "Что делает человек, когда камера не делает ничего."}},
    {"key": "physics",    "label": {"en": "Physics", "ru": "Физика"},
     "hint": {"en": "Secondary motion: cloth, hair, dust, smoke, water, breath.",
              "ru": "Вторичное движение: ткань, волосы, пыль, дым, вода, дыхание."}},
    {"key": "transition", "label": {"en": "Transitions", "ru": "Переходы"},
     "hint": {"en": "Motion whose job is to hide or land a cut.",
              "ru": "Движение, работа которого — спрятать склейку или посадить её."}},
]

MOTIONS: list[dict] = [

    # ══════════════ КАМЕРА ══════════════
    {
        "key": "m_push_settle", "group": "camera", "tier": "free",
        "label": {"en": "Push in and settle", "ru": "Наезд с остановкой"},
        "desc": {"en": "The frame closes on the subject and stops. No zoom — the camera actually travels.",
                 "ru": "Кадр смыкается на герое и останавливается. Не зум: камера действительно едет."},
        "camera": "slow push-in",
        "text": "The camera moves steadily closer along one axis and settles. {character} does not move toward the "
                "camera; only the distance between them shrinks, and the framing comes to rest tighter than it began.",
        "solo": "Slow push-in on {character} over six seconds: the framing tightens, they hold still and blink "
                "once, and the move settles without any pan or roll.",
        "bracket": "[Push in]",
        "physics": {"en": "Perspective changes as the camera travels — the background grows behind the subject.",
                    "ru": "Перспектива меняется, потому что камера едет: фон за героем растёт."},
        "slots": ["character"], "needs_last": False, "engines": _ANY,
        "traits": ["moving_camera", "slow"],
        "fits_with": ["portrait_hold_still", "travel_corridor_push", "interior_doorframe"],
        "conflicts_with": ["m_pull_open", "m_whip_out"],
    },
    {
        "key": "m_pull_open", "group": "camera", "tier": "free",
        "label": {"en": "Pull back and open", "ru": "Отъезд с раскрытием"},
        "desc": {"en": "The space opens around the subject. The scene explains itself backwards.",
                 "ru": "Пространство раскрывается вокруг героя. Сцена объясняет себя задом наперёд."},
        "camera": "slow pull-back",
        "text": "The camera retreats in a straight line and settles, opening more of the space every second. "
                "{character} stays exactly where they are and does not walk away from the lens.",
        "solo": "Slow pull-back away from {character} over six seconds, opening the space around them until the "
                "move settles. They stay where they are and do not follow the camera.",
        "bracket": "[Pull out]",
        "physics": {"en": "Foreground edges enter the frame as the camera retreats — they sell the movement.",
                    "ru": "По краям входит передний план — именно он продаёт движение."},
        "slots": ["character"], "needs_last": False, "engines": _ANY,
        "traits": ["moving_camera", "slow", "wide_frame"],
        "fits_with": ["final_pull_to_wide", "open_empty_place", "final_rhyme_open"],
        "conflicts_with": ["m_push_settle", "m_dolly_zoom"],
    },
    {
        "key": "m_truck_side", "group": "camera", "tier": "free",
        "label": {"en": "Truck sideways", "ru": "Тревеллинг вбок"},
        "desc": {"en": "The camera slides across the scene at walking pace. Depth for free.",
                 "ru": "Камера едет поперёк сцены на скорости шага. Глубина бесплатно."},
        "camera": "truck right at walking pace",
        "text": "The camera trucks sideways at walking pace, holding {character} in the same place in the frame "
                "while the background sweeps past, and settles once the pace steadies.",
        "solo": "The camera trucks sideways past {character} at walking pace, foreground objects sweeping close to "
                "the lens, and settles into a steady glide.",
        "bracket": "[Truck left]",
        "physics": {"en": "Near objects sweep fast, far ones crawl — that difference is the whole effect.",
                    "ru": "Близкое проносится, дальнее ползёт — вся суть приёма в этой разнице."},
        "slots": ["character"], "needs_last": False, "engines": _ANY,
        "traits": ["moving_camera"],
        "fits_with": ["travel_side_track", "crowd_queue_line", "crowd_queue_line"],
        "conflicts_with": ["m_push_settle", "m_arc_quarter"],
    },
    {
        "key": "m_pedestal_down", "group": "camera", "tier": "free",
        "label": {"en": "Pedestal down", "ru": "Камера опускается"},
        "desc": {"en": "The camera loses height without tilting. Status drains out of the frame.",
                 "ru": "Камера теряет высоту, не наклоняясь. Из кадра утекает статус."},
        "camera": "pedestal down to waist height",
        "text": "The camera lowers from head height to waist height in one smooth move and settles, keeping "
                "{character} the same size in the frame throughout.",
        "solo": "The camera lowers from head height to waist height on {character} in one smooth move and settles, "
                "keeping them the same size in the frame.",
        "bracket": "[Pedestal down]",
        "physics": {"en": "The horizon rises through the frame; the ground gains weight.",
                    "ru": "Горизонт поднимается по кадру, земля набирает вес."},
        "slots": ["character"], "needs_last": False, "engines": _ANY,
        "traits": ["moving_camera", "slow"],
        "fits_with": ["travel_stairs_down", "travel_walk_away"],
        "conflicts_with": ["m_crane_rise", "m_tilt_up"],
    },
    {
        "key": "m_arc_quarter", "group": "camera", "tier": "pro",
        "label": {"en": "Quarter arc", "ru": "Четверть облёта"},
        "desc": {"en": "A quarter turn around the subject — never more. Beyond that the geometry falls apart.",
                 "ru": "Четверть оборота вокруг героя — и не больше. Дальше геометрия разваливается."},
        "camera": "arc a quarter turn to the left",
        "text": "The camera arcs a quarter turn around {character} to the left and settles, keeping them centred "
                "the whole way. The subject stays planted and the background rotates behind them.",
        "solo": "The camera arcs a quarter turn around {character} to the left, keeping them centred, and settles. "
                "They stay planted while the background rotates behind them.",
        "bracket": "",
        "physics": {"en": "Only the background moves. The subject must not counter-rotate to stay facing the lens.",
                    "ru": "Двигается только фон. Герой не должен доворачиваться вслед за камерой."},
        "slots": ["character"], "needs_last": False, "engines": ["seedance-2-5", "kling-3.0-pro", "kling-3.0"],
        "traits": ["moving_camera"],
        "fits_with": ["portrait_profile_to_front", "action_impact_stop"],
        "conflicts_with": ["m_truck_side", "m_rack_focus"],
    },
    {
        "key": "m_crane_rise", "group": "camera", "tier": "pro",
        "label": {"en": "Crane up", "ru": "Кран вверх"},
        "desc": {"en": "The camera leaves the ground and the scene turns into a map.",
                 "ru": "Камера отрывается от земли, и сцена превращается в карту."},
        "camera": "crane up and back",
        "text": "The camera rises from head height up above the scene in one continuous move and settles looking "
                "down, {character} shrinking into the space below without moving.",
        "solo": "The camera rises from head height up above {character} in one continuous move and settles looking "
                "down on the scene, while they stay exactly where they are.",
        "bracket": "",
        "physics": {"en": "Everything below stays put; only the viewpoint climbs. No tilt on the way up.",
                    "ru": "Внизу ничто не меняется, поднимается только точка зрения. Без наклона по дороге."},
        "slots": ["character"], "needs_last": False, "engines": ["seedance-2-5", "kling-3.0-pro"],
        "traits": ["moving_camera", "wide_frame"],
        "fits_with": ["final_pull_to_wide", "crowd_one_still"],
        "conflicts_with": ["m_pedestal_down", "m_rack_focus"],
    },
    {
        "key": "m_steadi_follow", "group": "camera", "tier": "free",
        "label": {"en": "Follow from behind", "ru": "Идём следом"},
        "desc": {"en": "The camera walks behind the character. The most watchable movement in a music clip.",
                 "ru": "Камера идёт за героем. Самое смотрибельное движение в клипе."},
        "camera": "steadicam follow from behind",
        "text": "The camera follows {character} from behind at their own walking pace in one unbroken move and "
                "settles when they stop; the frame stays level and the distance stays the same.",
        "solo": "The camera follows {character} from behind at walking pace in one unbroken move and settles when "
                "they stop, holding the same distance the whole way.",
        "bracket": "[Tracking shot]",
        "physics": {"en": "A small vertical bounce in time with the steps keeps it from looking mechanical.",
                    "ru": "Мелкая вертикальная качка в такт шагам не даёт движению стать механическим."},
        "slots": ["character"], "needs_last": False, "engines": _ANY,
        "traits": ["moving_camera"],
        "fits_with": ["travel_walk_away", "travel_stairs_down"],
        "conflicts_with": ["m_freeze_settle", "m_push_settle"],
    },
    {
        "key": "m_handheld_drift", "group": "camera", "tier": "free",
        "label": {"en": "Handheld drift", "ru": "Дрейф с рук"},
        "desc": {"en": "Small breathing sway with no direction. Makes a frame read as witnessed, not staged.",
                 "ru": "Мелкая дышащая качка без направления. Кадр начинает читаться как увиденный, а не поставленный."},
        "camera": "handheld, slight drift",
        "text": "The camera drifts with a small breathing sway and no fixed direction, staying on {character} the "
                "whole time, and settles into stillness at the end.",
        "solo": "The camera drifts with a small breathing sway around {character} for six seconds, with no fixed "
                "direction, and settles into stillness.",
        "bracket": "[Shake]",
        "physics": {"en": "Amplitude must stay small: past a couple of degrees it reads as a mistake, not a style.",
                    "ru": "Амплитуда обязана быть мелкой: больше пары градусов читается как брак, а не приём."},
        "slots": ["character"], "needs_last": False, "engines": _ANY,
        "traits": ["handheld"],
        "fits_with": ["crowd_hands_up", "travel_stairs_down"],
        "conflicts_with": ["m_freeze_settle", "m_push_settle"],
    },
    {
        "key": "m_rack_focus", "group": "camera", "tier": "free",
        "label": {"en": "Rack focus", "ru": "Перевод фокуса"},
        "desc": {"en": "Attention moves without a cut and without the camera moving at all.",
                 "ru": "Внимание переходит без склейки и без единого движения камеры."},
        "camera": "static, focus pull",
        "text": "The plane of focus travels from {prop} in the foreground to {character} behind it and comes to "
                "rest there; the foreground falls soft and the camera itself does not move.",
        "solo": "The plane of focus travels from {prop} in the foreground back to {character} and comes to rest "
                "there, the foreground going soft. The camera itself stays still.",
        "bracket": "[Static shot]",
        "physics": {"en": "Depth of field must be shallow in the first frame or there is nothing to pull.",
                    "ru": "В первом кадре глубина резкости обязана быть малой, иначе переводить нечего."},
        "slots": ["prop", "character"], "needs_last": False, "engines": _ANY,
        "traits": ["locked", "close"],
        "fits_with": ["detail_texture_macro", "open_detail_first", "travel_car_window"],
        "conflicts_with": ["m_crane_rise", "m_arc_quarter"],
    },
    {
        "key": "m_tilt_up", "group": "camera", "tier": "free",
        "label": {"en": "Tilt up the vertical", "ru": "Наклон вверх"},
        "desc": {"en": "The camera stays put and looks up. The subject gains height it does not have.",
                 "ru": "Камера стоит и поднимает взгляд. Герой получает рост, которого у него нет."},
        "camera": "tilt up from the ground",
        "text": "The camera stays in place and tilts upward from the ground to {character}, settling once their "
                "face is in the upper third of the frame. Verticals stretch slightly toward the top of the shot.",
        "solo": "The camera stays in place and tilts up from the ground to {character}, settling once the face "
                "reaches the upper third of the frame.",
        "bracket": "[Tilt up]",
        "physics": {"en": "Tilt is not a rise: the camera does not gain height, only angle.",
                    "ru": "Наклон — не подъём: камера не набирает высоту, только угол."},
        "slots": ["character"], "needs_last": False, "engines": _ANY,
        "traits": ["locked"],
        "fits_with": ["portrait_profile_to_front", "night_lamp_pass"],
        "conflicts_with": ["m_pedestal_down", "m_crane_rise"],
    },
    {
        "key": "m_pan_link", "group": "camera", "tier": "free",
        "label": {"en": "Pan to link two points", "ru": "Панорама, связывающая две точки"},
        "desc": {"en": "One movement proves two things are in the same place. Cheaper than an establishing shot.",
                 "ru": "Одно движение доказывает, что две вещи в одном месте. Дешевле заявочного плана."},
        "camera": "slow pan right",
        "text": "The camera stays in place and pans slowly to the right across {location}, ending on {character} "
                "and settling there. No zoom, no travel, one continuous sweep.",
        "solo": "The camera stays in place and pans slowly right across {location}, ending on {character} and "
                "settling there in one continuous sweep.",
        "bracket": "[Pan right]",
        "physics": {"en": "Near objects smear more than far ones — pan too fast and the frame strobes.",
                    "ru": "Ближнее смазывается сильнее дальнего: слишком быстрая панорама даёт строб."},
        "slots": ["location", "character"], "needs_last": False, "engines": _ANY,
        "traits": ["locked", "slow"],
        "fits_with": ["open_empty_place", "crowd_queue_line"],
        "conflicts_with": ["m_push_settle", "m_body_wipe"],
    },
    {
        "key": "m_dolly_zoom", "group": "camera", "tier": "pro",
        "label": {"en": "Dolly zoom", "ru": "Долли-зум"},
        "desc": {"en": "The background compresses while the subject stays the same size. Use once per clip at most.",
                 "ru": "Фон сжимается, герой остаётся того же размера. Не чаще одного раза за клип."},
        "camera": "dolly in while zooming out",
        "text": "The camera travels toward {character} while the lens widens at the same rate, so their size in "
                "frame stays constant and the background compresses behind them, then the move settles.",
        "solo": "The camera travels toward {character} while the lens widens at the same rate: their size stays "
                "constant, the background compresses behind them, and the move settles.",
        "bracket": "",
        "physics": {"en": "The subject must be still and centred, or the effect turns into a smear.",
                    "ru": "Герой обязан быть неподвижен и по центру, иначе приём превращается в мазню."},
        "slots": ["character"], "needs_last": False,
        "engines": ["seedance-2-5", "kling-3.0-pro"],
        "traits": ["moving_camera"],
        "fits_with": ["portrait_hold_still", "travel_corridor_push"],
        "conflicts_with": ["m_pull_open", "m_handheld_drift"],
    },

    # ══════════════ ТЕЛО ══════════════
    {
        "key": "m_turn_to_lens", "group": "body", "tier": "free",
        "label": {"en": "Turn to the lens", "ru": "Поворот к объективу"},
        "desc": {"en": "From unaware to eye contact in one move. The most reliable performance beat there is.",
                 "ru": "От «не замечает» до контакта глазами в одно движение. Самая надёжная актёрская доля."},
        "camera": "static, eye level",
        "text": "{character} turns the head from profile toward the camera in one continuous move and stops with "
                "the eyes in the lens; the hair follows a beat behind the turn.",
        "solo": "{character} turns from profile toward the camera in one continuous move and stops with the eyes "
                "in the lens, the hair trailing a beat behind.",
        "bracket": "[Static shot]",
        "physics": {"en": "The neck leads, the hair lags, the shoulders barely move.",
                    "ru": "Ведёт шея, волосы отстают, плечи почти не участвуют."},
        "slots": ["character"], "needs_last": False, "engines": _ANY,
        "traits": ["locked", "hero_face"],
        "fits_with": ["portrait_turn_to_lens", "night_neon_wall", "interior_mirror"],
        "conflicts_with": ["m_body_wipe", "m_whip_out"],
    },
    {
        "key": "m_weight_step", "group": "body", "tier": "free",
        "label": {"en": "Weight transfer", "ru": "Перенос веса"},
        "desc": {"en": "One step, described the way a body actually takes it. Kills the floating-feet look.",
                 "ru": "Один шаг, описанный так, как его делает тело. Убивает эффект скользящих ног."},
        "camera": "static",
        "text": "{character} shifts their weight from the back foot to the front, the hips leading and the "
                "shoulders arriving a beat later, and comes to rest with both feet planted.",
        "solo": "{character} shifts weight from the back foot to the front, hips leading and shoulders arriving a "
                "beat later, and comes to rest with both feet planted.",
        "bracket": "[Static shot]",
        "physics": {"en": "Hips first, shoulders second. Reverse it and the walk reads as a puppet.",
                    "ru": "Сначала бёдра, потом плечи. Наоборот — походка марионетки."},
        "slots": ["character"], "needs_last": False, "engines": _ANY,
        "traits": ["locked"],
        "fits_with": ["action_run_toward", "open_mid_action", "night_lamp_pass"],
        "conflicts_with": ["m_crane_rise"],
    },
    {
        "key": "m_head_snap", "group": "body", "tier": "free",
        "label": {"en": "Head snap", "ru": "Рывок головой"},
        "desc": {"en": "A fast look off-axis and back. Lands a beat without any camera work.",
                 "ru": "Быстрый взгляд в сторону и обратно. Сажает долю без всякой камеры."},
        "camera": "static",
        "text": "{character} snaps the head to one side, holds for an instant and returns to the original "
                "position, where the movement stops; the hair overshoots and settles a beat later.",
        "solo": "{character} snaps the head to one side, holds an instant and returns, the hair overshooting and "
                "settling a beat later. The camera does not move.",
        "bracket": "[Static shot]",
        "physics": {"en": "Overshoot is the whole trick: hair and collar arrive after the head has stopped.",
                    "ru": "Весь фокус в перелёте: волосы и воротник приходят после того, как голова встала."},
        "slots": ["character"], "needs_last": False, "engines": _ANY,
        "traits": ["locked", "fast"],
        "fits_with": ["open_direct_look", "crowd_one_still"],
        "conflicts_with": ["m_dolly_zoom", "m_crane_rise"],
    },
    {
        "key": "m_sit_to_stand", "group": "body", "tier": "free",
        "label": {"en": "Sit to stand", "ru": "Из сидя в стоя"},
        "desc": {"en": "A whole decision expressed as one physical action.",
                 "ru": "Целое решение, выраженное одним физическим действием."},
        "camera": "static, chest level",
        "text": "{character} pushes up from the seat, the head rising out of the top of the frame and then "
                "settling back into it as they straighten and stop.",
        "solo": "{character} pushes up out of the seat, straightens and stops standing, the head rising out of the "
                "frame and settling back into it.",
        "bracket": "[Static shot]",
        "physics": {"en": "Weight goes forward before it goes up — otherwise the body levitates.",
                    "ru": "Вес сначала идёт вперёд и только потом вверх, иначе тело левитирует."},
        "slots": ["character"], "needs_last": False, "engines": _ANY,
        "traits": ["locked"],
        "fits_with": ["interior_table_sit", "portrait_two_shot"],
        "conflicts_with": ["m_crane_rise", "m_rack_focus"],
    },
    {
        "key": "m_hand_raise", "group": "body", "tier": "free",
        "label": {"en": "Hand into frame", "ru": "Рука входит в кадр"},
        "desc": {"en": "The safest way to animate a detail shot: one hand, one direction, one stop.",
                 "ru": "Самый безопасный способ оживить деталь: одна рука, одно направление, одна остановка."},
        "camera": "static, macro",
        "text": "A hand enters from the bottom edge of the frame, reaches {prop} and comes to rest on it; nothing "
                "else in the shot moves.",
        "solo": "A hand slides into the frame from the bottom edge, reaches {prop} and comes to rest on it. "
                "Nothing else moves.",
        "bracket": "[Static shot]",
        "physics": {"en": "Fingers close after contact, not before — early closing is what reads as fake.",
                    "ru": "Пальцы смыкаются после касания, а не до: ранний захват и читается как подделка."},
        "slots": ["prop"], "needs_last": False, "engines": _ANY,
        "traits": ["locked", "close", "object"],
        "fits_with": ["open_detail_first", "detail_object_pickup", "detail_hands_work"],
        "conflicts_with": ["m_steadi_follow", "m_crane_rise"],
    },
    {
        "key": "m_enter_frame", "group": "body", "tier": "free",
        "label": {"en": "Walk into the frame", "ru": "Входит в кадр"},
        "desc": {"en": "The scene starts without the character and acquires one. Buys a beat of anticipation.",
                 "ru": "Сцена начинается без героя и обзаводится им. Покупает долю ожидания."},
        "camera": "static",
        "text": "{character} walks into the frame from one edge, crosses to the centre and stops there facing the "
                "camera; the rest of the scene stays exactly as it was.",
        "solo": "{character} walks into the frame from one edge, crosses to the centre and stops facing the "
                "camera. Everything else in the scene stays as it was.",
        "bracket": "[Static shot]",
        "physics": {"en": "Deceleration takes a step and a half — stopping dead reads as a glitch.",
                    "ru": "Торможение занимает полтора шага: мгновенная остановка читается как сбой."},
        "slots": ["character"], "needs_last": False, "engines": _ANY,
        "traits": ["locked"],
        "fits_with": ["interior_doorframe", "open_empty_place", "bridge_door_through"],
        "conflicts_with": ["m_steadi_follow"],
    },
    {
        "key": "m_shoulder_drop", "group": "body", "tier": "free",
        "label": {"en": "Shoulders drop", "ru": "Плечи падают"},
        "desc": {"en": "Exhaustion or relief without any facial acting at all.",
                 "ru": "Усталость или облегчение вообще без мимики."},
        "camera": "static, close",
        "text": "{character} lets the shoulders drop on a long exhale, the chest falling and the head tipping "
                "forward a fraction, and the body settles there.",
        "solo": "{character} exhales, the shoulders drop and the head tips forward a fraction, and the body "
                "settles there. The camera does not move.",
        "bracket": "[Static shot]",
        "physics": {"en": "Chest falls first, head second. Simultaneous movement reads as a slump, not a breath.",
                    "ru": "Сначала опускается грудь, потом голова. Одновременно — это обвал, а не выдох."},
        "slots": ["character"], "needs_last": False, "engines": _ANY,
        "traits": ["locked", "close", "hero_face"],
        "fits_with": ["portrait_breath_break", "interior_table_sit", "night_screen_face"],
        "conflicts_with": ["m_whip_out", "m_head_snap"],
    },
    {
        "key": "m_eyes_to_lens", "group": "body", "tier": "free",
        "label": {"en": "Eyes find the lens", "ru": "Глаза находят объектив"},
        "desc": {"en": "The head does not move — only the eyes. Smaller than a turn and twice as unsettling.",
                 "ru": "Голова не двигается, двигаются только глаза. Меньше поворота и вдвое тревожнее."},
        "camera": "static, close",
        "text": "The eyes of {character} travel across to the lens and stop there while the head stays completely "
                "still; the shot holds on the look.",
        "solo": "The eyes of {character} travel across to the lens and stop there while the head stays completely "
                "still, and the shot holds on that look.",
        "bracket": "[Static shot]",
        "physics": {"en": "One blink before the eyes arrive; none after. Blinking on arrival breaks the moment.",
                    "ru": "Один моргок до прихода взгляда и ни одного после: моргание на приходе рушит момент."},
        "slots": ["character"], "needs_last": False, "engines": _ANY,
        "traits": ["locked", "close", "hero_face"],
        "fits_with": ["final_last_look", "open_direct_look", "portrait_hold_still"],
        "conflicts_with": ["m_handheld_drift", "m_steadi_follow"],
    },

    # ══════════════ ФИЗИКА ══════════════
    {
        "key": "m_coat_inertia", "group": "physics", "tier": "free",
        "label": {"en": "Coat inertia", "ru": "Инерция пальто"},
        "desc": {"en": "Heavy clothing arrives after the body. One line, and the walk stops looking generated.",
                 "ru": "Тяжёлая одежда приходит после тела. Одна строка — и походка перестаёт выглядеть сгенерённой."},
        "camera": "",
        "text": "The {outfit} swings a beat behind every step {character} takes, the hem lifting on the forward "
                "swing and settling against the legs when the movement stops.",
        "solo": "The {outfit} swings a beat behind each step {character} takes, the hem lifting and then settling "
                "against the legs as the movement stops.",
        "bracket": "",
        "physics": {"en": "Cloth never leads the body. If the hem moves first, the shot is wrong.",
                    "ru": "Ткань никогда не ведёт тело. Если подол пошёл первым — кадр неправильный."},
        "slots": ["outfit", "character"], "needs_last": False, "engines": _ANY,
        "traits": [],
        "fits_with": ["travel_walk_away", "open_door_out", "final_walk_out_frame"],
        "conflicts_with": [],
    },
    {
        "key": "m_hair_lag", "group": "physics", "tier": "free",
        "label": {"en": "Hair lag", "ru": "Запаздывание волос"},
        "desc": {"en": "Hair overshoots the turn and comes back. The cheapest realism there is.",
                 "ru": "Волосы перелетают поворот и возвращаются. Самый дешёвый реализм на свете."},
        "camera": "",
        "text": "The hair of {character} follows the head a beat late, overshoots slightly at the end of the "
                "movement and settles back across the shoulders.",
        "solo": "The hair of {character} follows the head a beat late, overshoots at the end of the movement and "
                "settles back across the shoulders.",
        "bracket": "",
        "physics": {"en": "Overshoot then return. Hair that stops with the head reads as a helmet.",
                    "ru": "Перелёт и возврат. Волосы, встающие вместе с головой, читаются как шлем."},
        "slots": ["character"], "needs_last": False, "engines": _ANY,
        "traits": [],
        "fits_with": ["portrait_turn_to_lens", "night_neon_wall", "action_fall_back"],
        "conflicts_with": [],
    },
    {
        "key": "m_hem_settle", "group": "physics", "tier": "free",
        "label": {"en": "Fabric settles", "ru": "Ткань укладывается"},
        "desc": {"en": "The last half second of any movement belongs to the clothes.",
                 "ru": "Последние полсекунды любого движения принадлежат одежде."},
        "camera": "",
        "text": "After {character} stops, the {outfit} keeps moving for a moment: the folds swing once, the sleeve "
                "drops and the fabric comes to rest against the body.",
        "solo": "After {character} stops moving, the {outfit} keeps going for a moment — the folds swing once, the "
                "sleeve drops, and the fabric comes to rest against the body.",
        "bracket": "",
        "physics": {"en": "The heavier the fabric, the longer the delay. Denim settles faster than a coat.",
                    "ru": "Чем тяжелее ткань, тем длиннее задержка. Джинса встаёт быстрее пальто."},
        "slots": ["character", "outfit"], "needs_last": False, "engines": _ANY,
        "traits": [],
        "fits_with": ["detail_pocket_reveal", "portrait_profile_to_front"],
        "conflicts_with": [],
    },
    {
        "key": "m_dust_bloom", "group": "physics", "tier": "free",
        "label": {"en": "Dust burst", "ru": "Пыльный выброс"},
        "desc": {"en": "Impact made visible. Air is what proves the contact happened.",
                 "ru": "Удар, ставший видимым. Воздух и доказывает, что контакт был."},
        "camera": "",
        "text": "Dust bursts outward from the point of contact, hangs in the light for a moment and settles back "
                "toward the ground.",
        "solo": "Dust bursts outward from the point of contact, hangs in the light for a moment and settles back "
                "toward the ground.",
        "bracket": "",
        "physics": {"en": "Dust rises then falls: a cloud that only expands reads as smoke, not impact.",
                    "ru": "Пыль сначала поднимается, потом падает. Только расширяющееся облако — это дым, не удар."},
        "slots": [], "needs_last": False, "engines": _ANY,
        "traits": ["fast"],
        "fits_with": ["action_impact_stop", "action_jump_land", "action_run_toward"],
        "conflicts_with": [],
    },
    {
        "key": "m_smoke_curl", "group": "physics", "tier": "free",
        "label": {"en": "Smoke drift", "ru": "Дым тянется"},
        "desc": {"en": "The one motion that makes a locked-off frame alive without anyone acting.",
                 "ru": "Единственное движение, оживляющее статичный кадр без всякой игры."},
        "camera": "",
        "text": "Smoke drifts upward through the beam of light in a slow curl, breaks apart near the top of the "
                "frame and settles into a thin haze.",
        "solo": "Smoke drifts upward through the beam of light in a slow curl, breaks apart near the top of the "
                "frame and settles into a thin haze.",
        "bracket": "",
        "physics": {"en": "It rises, then spreads sideways when it cools. Straight vertical smoke looks fake.",
                    "ru": "Сначала поднимается, остывая — расходится вбок. Строго вертикальный дым выглядит фальшиво."},
        "slots": [], "needs_last": False, "engines": _ANY,
        "traits": ["slow"],
        "fits_with": ["open_empty_place", "night_headlights", "final_walk_out_frame"],
        "conflicts_with": [],
    },
    {
        "key": "m_water_drip", "group": "physics", "tier": "free",
        "label": {"en": "Water and rings", "ru": "Вода и круги"},
        "desc": {"en": "Water gives a scene a clock. Drops arrive on a rhythm the eye trusts.",
                 "ru": "Вода даёт сцене часы. Капли приходят в ритме, которому глаз верит."},
        "camera": "",
        "text": "A drop gathers, falls and strikes the surface below, sending a single ring outward that spreads "
                "and settles flat.",
        "solo": "A drop gathers, falls and strikes the surface below, sending one ring outward that spreads and "
                "settles flat.",
        "bracket": "",
        "physics": {"en": "One ring per drop. Multiple rings from a single drop is the classic generated tell.",
                    "ru": "Один круг на каплю. Несколько кругов от одной — классический признак генерации."},
        "slots": [], "needs_last": False, "engines": _ANY,
        "traits": ["slow"],
        "fits_with": ["night_rain_reflect", "detail_texture_macro"],
        "conflicts_with": [],
    },
    {
        "key": "m_breath_fog", "group": "physics", "tier": "free",
        "label": {"en": "Visible breath", "ru": "Видимое дыхание"},
        "desc": {"en": "Proof of cold and of life, in one detail that costs nothing.",
                 "ru": "Доказательство холода и жизни в одной детали, которая не стоит ничего."},
        "camera": "",
        "text": "The breath of {character} shows as a small cloud that leaves the mouth, drifts up past the face "
                "and disperses, then settles into a slow steady rhythm.",
        "solo": "The breath of {character} shows as a small cloud leaving the mouth, drifting up past the face and "
                "dispersing, then settling into a slow steady rhythm.",
        "bracket": "",
        "physics": {"en": "The cloud must disperse before the next breath, or it reads as smoke.",
                    "ru": "Облачко обязано разойтись до следующего выдоха, иначе читается как дым."},
        "slots": ["character"], "needs_last": False, "engines": _ANY,
        "traits": ["night"],
        "fits_with": ["night_lamp_pass", "portrait_breath_break", "travel_corridor_push"],
        "conflicts_with": [],
    },
    {
        "key": "m_paper_scatter", "group": "physics", "tier": "free",
        "label": {"en": "Paper scatter", "ru": "Бумага разлетается"},
        "desc": {"en": "Light objects moving in air read as chaos more clearly than any camera shake.",
                 "ru": "Лёгкие предметы в воздухе читаются как хаос яснее любой тряски камеры."},
        "camera": "",
        "text": "Loose sheets lift off the surface, turn over once in the air and settle across the ground in a "
                "scattered line.",
        "solo": "Loose sheets lift off the surface, turn over once in the air and settle across the ground in a "
                "scattered line.",
        "bracket": "",
        "physics": {"en": "Paper turns as it falls; it never drops flat. Flat-falling sheets look like cards.",
                    "ru": "Бумага переворачивается при падении и никогда не падает плашмя — иначе это карты."},
        "slots": [], "needs_last": False, "engines": _ANY,
        "traits": ["fast"],
        "fits_with": ["action_throw_away", "detail_written_trace"],
        "conflicts_with": [],
    },

    # ══════════════ ПЕРЕХОДЫ ══════════════
    {
        "key": "m_whip_out", "group": "transition", "tier": "pro",
        "label": {"en": "Whip out of the scene", "ru": "Рывок из сцены"},
        "desc": {"en": "The frame smears sideways and the cut hides inside the blur.",
                 "ru": "Кадр смазывается вбок, и склейка прячется внутри смаза."},
        "camera": "fast whip pan right",
        "text": "The camera whips to the right, the frame smearing into horizontal blur, and the movement stops "
                "with the shot unreadable. Nothing in the scene moves on its own.",
        "solo": "The camera whips to the right so the frame smears into horizontal blur and stops there, leaving "
                "the shot unreadable. Nothing else moves.",
        "bracket": "[Pan right]",
        "physics": {"en": "Only the last second may be blurred — a whole scene of blur is unwatchable.",
                    "ru": "Смазана только последняя секунда: сцена из сплошного смаза не смотрится."},
        "slots": [], "needs_last": False, "engines": ["seedance-2-mini", "seedance-2-5", "grok"],
        "traits": ["fast", "moving_camera"],
        "fits_with": ["bridge_body_wipe", "open_mid_action"],
        "conflicts_with": ["m_push_settle", "m_rack_focus", "m_eyes_to_lens"],
    },
    {
        "key": "m_blur_resolve", "group": "transition", "tier": "free",
        "label": {"en": "Resolve out of blur", "ru": "Проявление из размытия"},
        "desc": {"en": "The scene opens out of focus and sharpens. Reads as waking up or arriving.",
                 "ru": "Сцена открывается не в фокусе и наводится. Читается как пробуждение или приход."},
        "camera": "static, focus resolve",
        "text": "The shot begins completely out of focus and sharpens onto {character}, coming to rest once the "
                "face is clear; the camera itself never moves.",
        "solo": "The shot begins completely out of focus and sharpens onto {character}, coming to rest once the "
                "face is clear. The camera itself does not move.",
        "bracket": "[Static shot]",
        "physics": {"en": "Focus arrives once and stops — hunting back and forth reads as a broken lens.",
                    "ru": "Фокус приходит один раз и встаёт: рысканье туда-сюда читается как сломанный объектив."},
        "slots": ["character"], "needs_last": False, "engines": _ANY,
        "traits": ["locked"],
        "fits_with": ["open_from_black", "bridge_match_shape", "bridge_light_to_dark"],
        "conflicts_with": ["m_whip_out", "m_crane_rise"],
    },
    {
        "key": "m_body_wipe", "group": "transition", "tier": "free",
        "label": {"en": "Wiped by a foreground body", "ru": "Перекрытие передним планом"},
        "desc": {"en": "Something dark crosses the lens; the cut lives inside it.",
                 "ru": "Тёмное проходит перед объективом, и склейка живёт внутри него."},
        "camera": "static",
        "text": "A dark shape crosses close to the lens from one edge and fills the frame completely, and the shot "
                "ends holding on that darkness.",
        "solo": "A dark shape crosses close to the lens from one edge, fills the frame completely and the shot "
                "holds on that darkness.",
        "bracket": "[Static shot]",
        "physics": {"en": "The shape must be out of focus. A sharp foreground reads as a mistake, not a wipe.",
                    "ru": "Перекрывающее обязано быть не в фокусе: резкий передний план читается как брак."},
        "slots": [], "needs_last": False, "engines": _ANY,
        "traits": ["fast"],
        "fits_with": ["bridge_body_wipe", "crowd_part_for_hero"],
        "conflicts_with": ["m_turn_to_lens", "m_eyes_to_lens"],
    },
    {
        "key": "m_freeze_settle", "group": "transition", "tier": "free",
        "label": {"en": "Stop dead", "ru": "Мёртвая остановка"},
        "desc": {"en": "Everything halts at once and the frame holds. The visual equivalent of a silence.",
                 "ru": "Всё встаёт разом, и кадр держится. Визуальный эквивалент паузы."},
        "camera": "static",
        "text": "All movement in the frame halts at once and the shot holds completely still; only the smallest "
                "secondary motion — cloth, hair, dust — settles a beat after everything else has stopped.",
        "solo": "All movement in the frame halts at once and the shot holds still, with only cloth, hair and dust "
                "settling a beat after everything else has stopped.",
        "bracket": "[Static shot]",
        "physics": {"en": "The secondary motion is what proves it was a stop and not a still image.",
                    "ru": "Именно вторичное движение доказывает, что это остановка, а не стоп-кадр."},
        "slots": [], "needs_last": False, "engines": _ANY,
        "traits": ["locked"],
        "fits_with": ["action_impact_stop", "crowd_hands_up", "final_last_look"],
        "conflicts_with": ["m_handheld_drift", "m_steadi_follow"],
    },
]


# ─────────────────────────────────────────────────────────────────────────────
# 4. СВЕТ И ЦВЕТ — 22 модификатора.
#
# Единица: ОДНА ФРАЗА, которая дописывается в конец обоих кадров сцены. Это не
# самостоятельная сцена и не приём: карточка ничего не снимает, она уточняет
# уже собранный кадр.
#
# ГЛАВНОЕ ПРАВИЛО ФАЙЛА, И ОНО НЕ КОСМЕТИЧЕСКОЕ. Стиль трека главнее. Он
# подставляется рендером ПЕРВЫМ блоком промпта и отвечает за палитру, грейд,
# фактуру, зерно и оптику; дублирование этих же вещей в image_prompt уже ломало
# картинку и уже чинилось. Поэтому карточки делятся на два уровня:
#
#   level="scene"  — ГЕОМЕТРИЯ СВЕТА: откуда идёт, куда падает, что в тени,
#                    сколько источников. Это свойство сцены, а не стиля, и
#                    писать это в кадр можно всегда. Слова про плёнку, зерно,
#                    грейд и палитру здесь запрещены машинно (_BAN_GRADE).
#   level="style"  — ПАЛИТРА И ГРЕЙД: то, чем распоряжается стиль. Такие
#                    карточки существуют, потому что владелец просил палитру, —
#                    но формулируются они как УСТУПКА: каждая начинается с
#                    «without overriding the track style». Это не вежливость, а
#                    работающая конструкция: модель получает пожелание, а не
#                    вторую конфликтующую инструкцию по грейду.
#
# Наличие уступки в тексте level="style" проверяется машинно. Карточка без неё
# не проходит validate() — иначе через полгода палитра из сцены начнёт спорить
# с палитрой из стиля, и никто не вспомнит почему.
# ─────────────────────────────────────────────────────────────────────────────
LIGHT_GROUPS = [
    {"key": "scheme",  "label": {"en": "Lighting setups", "ru": "Схемы света"},
     "hint": {"en": "Where the light comes from. Changes a frame more than any other decision.",
              "ru": "Откуда идёт свет. Меняет кадр сильнее любого другого решения."}},
    {"key": "time",    "label": {"en": "Time of day", "ru": "Время суток"},
     "hint": {"en": "Terms the engines read literally — 'golden hour' works, 'nice light' does not.",
              "ru": "Термины, которые движки читают буквально: «golden hour» работает, «красивый свет» — нет."}},
    {"key": "palette", "label": {"en": "Palette", "ru": "Палитра"},
     "hint": {"en": "Defers to the track style. A wish, not a second instruction.",
              "ru": "Уступает стилю трека. Пожелание, а не вторая инструкция."}},
    {"key": "grade",   "label": {"en": "Contrast and grade", "ru": "Контраст и грейд"},
     "hint": {"en": "The style owns this. These cards only nudge it.",
              "ru": "Этим распоряжается стиль. Эти карточки лишь подталкивают."}},
]

#: Фраза-уступка. Ровно одна на весь файл: два разных вежливых оборота — это
#: два разных поведения модели и ноль возможности проверить их машинно.
DEFER = "without overriding the track style"

LIGHTS: list[dict] = [

    # ══════════════ СХЕМЫ СВЕТА ══════════════
    {
        "key": "l_rim_back", "group": "scheme", "tier": "free", "level": "scene",
        "label": {"en": "Backlight rim", "ru": "Контровой ободок"},
        "desc": {"en": "A hard source behind the subject draws a line along the shoulders and hair.",
                 "ru": "Жёсткий источник за спиной рисует линию по плечам и волосам."},
        "add": "Lit from behind by one strong source, a bright rim tracing the shoulders and hair while the face "
               "stays mostly in shadow.",
        "note": {"en": "Separates a person from a dark background with no extra light at all.",
                 "ru": "Отрывает человека от тёмного фона вообще без дополнительного света."},
        "slots": [], "traits": ["hard_light", "low_key"],
        "fits_with": ["portrait_hold_still", "crowd_hands_up", "detail_object_pickup"],
        "conflicts_with": ["l_soft_wrap", "l_overcast_flat", "l_high_key_bounce"],
    },
    {
        "key": "l_hard_single", "group": "scheme", "tier": "free", "level": "scene",
        "label": {"en": "One hard source", "ru": "Один жёсткий источник"},
        "desc": {"en": "A single undiffused light from one side. Sharp-edged shadows, no fill.",
                 "ru": "Один нерассеянный источник сбоку. Тени с резкой границей, без заполнения."},
        "add": "Lit by a single hard undiffused source from one side, sharp-edged shadows falling across the "
               "surfaces behind, no fill light anywhere in the frame.",
        "note": {"en": "The default of every dramatic frame. Costs nothing and reads immediately.",
                 "ru": "Умолчание любого драматичного кадра. Ничего не стоит и читается сразу."},
        "slots": [], "traits": ["hard_light", "low_key"],
        "fits_with": ["portrait_breath_break", "detail_hands_work", "action_impact_stop"],
        "conflicts_with": ["l_soft_wrap", "l_overcast_flat", "l_high_key_bounce"],
    },
    {
        "key": "l_soft_wrap", "group": "scheme", "tier": "free", "level": "scene",
        "label": {"en": "Soft wrap", "ru": "Мягкий обёртывающий"},
        "desc": {"en": "A large diffused source close to the subject. Skin, calm, no drama.",
                 "ru": "Большой рассеянный источник близко к герою. Кожа, покой, без драмы."},
        "add": "Lit by one large diffused source close to the subject, the light wrapping around the face with a "
               "gentle falloff and shadows without hard edges.",
        "note": {"en": "The only scheme that forgives a badly generated face.",
                 "ru": "Единственная схема, прощающая плохо сгенерённое лицо."},
        "slots": [], "traits": ["soft_light"],
        "fits_with": ["interior_window_side", "portrait_two_shot", "portrait_hold_still"],
        "conflicts_with": ["l_hard_single", "l_chiaroscuro", "l_rim_back"],
    },
    {
        "key": "l_three_point", "group": "scheme", "tier": "free", "level": "scene",
        "label": {"en": "Three-point setup", "ru": "Три точки"},
        "desc": {"en": "Key, fill and backlight. The neutral, professional, deliberately unremarkable option.",
                 "ru": "Рисующий, заполняющий, контровой. Нейтрально, профессионально, нарочито незаметно."},
        "add": "Lit with a three-point setup: a soft key from one side, a weak fill on the other, and a hard "
               "backlight separating the subject from the background.",
        "note": {"en": "Use when the frame must not have an opinion — product, interview, hero shot.",
                 "ru": "Когда кадр не должен иметь мнения: товар, интервью, герой-кадр."},
        "slots": [], "traits": ["soft_light"],
        "fits_with": ["portrait_profile_to_front", "detail_texture_macro"],
        "conflicts_with": ["l_chiaroscuro", "l_practical_only"],
    },
    {
        "key": "l_practical_only", "group": "scheme", "tier": "free", "level": "scene",
        "label": {"en": "Practicals only", "ru": "Только источники в кадре"},
        "desc": {"en": "Lit exclusively by lamps and signs visible in the shot. Reads as filmed, not staged.",
                 "ru": "Освещено только тем, что видно в кадре. Читается как снято, а не поставлено."},
        "add": "Lit only by the light sources visible inside the frame — lamps, windows and signs — with no "
               "additional light from outside the shot.",
        "note": {"en": "The single cheapest way to make a generated frame look documented.",
                 "ru": "Самый дешёвый способ сделать сгенерённый кадр похожим на задокументированный."},
        "slots": [], "traits": ["low_key"],
        "fits_with": ["night_lamp_pass", "interior_table_sit", "travel_walk_away"],
        "conflicts_with": ["l_three_point", "l_high_key_bounce"],
    },
    {
        "key": "l_window_motivated", "group": "scheme", "tier": "free", "level": "scene",
        "label": {"en": "Motivated by the window", "ru": "Мотивирован окном"},
        "desc": {"en": "All the light in the room comes from one window and is stronger than daylight really is.",
                 "ru": "Весь свет в комнате идёт из одного окна и сильнее, чем бывает днём."},
        "add": "All the light in the room comes from one window, spilling across the floor in a hard-edged shape "
               "and leaving the far side of the space unlit.",
        "note": {"en": "Interiors read best with one direction of light and one direction only.",
                 "ru": "Интерьеры лучше всего читаются при одном и только одном направлении света."},
        "slots": [], "traits": ["soft_light", "interior"],
        "fits_with": ["interior_window_side", "detail_hands_work", "travel_car_window"],
        "conflicts_with": ["l_deep_night", "l_screen_glow"],
    },
    {
        "key": "l_screen_glow", "group": "scheme", "tier": "free", "level": "scene",
        "label": {"en": "Screen from below", "ru": "Экран снизу"},
        "desc": {"en": "One cold source under the chin that flickers as its content changes.",
                 "ru": "Один холодный источник под подбородком, мерцающий вместе с содержимым."},
        "add": "Lit only from below by a small screen out of frame, the light cold and unsteady, everything above "
               "the brow falling away into darkness.",
        "note": {"en": "The most contemporary light there is, and it needs no set at all.",
                 "ru": "Самый современный свет из существующих, и декорация ему не нужна."},
        "slots": [], "traits": ["night", "low_key"],
        "fits_with": ["night_screen_face", "portrait_hold_still", "interior_mirror"],
        "conflicts_with": ["l_harsh_noon", "l_window_motivated", "l_golden_hour"],
    },
    {
        "key": "l_chiaroscuro", "group": "scheme", "tier": "pro", "level": "scene",
        "label": {"en": "One shaft, nothing else", "ru": "Один луч и больше ничего"},
        "desc": {"en": "Extreme low key: a single shaft of light and unlit blackness around it.",
                 "ru": "Экстремальный низкий ключ: один луч света и неосвещённая чернота вокруг."},
        "add": "Lit by a single narrow shaft of light cutting across the frame, everything outside it completely "
               "unlit and unreadable.",
        "note": {"en": "Hides everything the model draws badly. Also the fastest way to look pretentious — use once.",
                 "ru": "Прячет всё, что модель рисует плохо. И быстрее всего выглядит претенциозно — не чаще раза."},
        "slots": [], "traits": ["hard_light", "low_key"],
        "fits_with": ["interior_doorframe", "bridge_light_to_dark", "detail_written_trace"],
        "conflicts_with": ["l_overcast_flat", "l_soft_wrap", "l_high_key_bounce"],
    },
    {
        "key": "l_high_key_bounce", "group": "scheme", "tier": "free", "level": "scene",
        "label": {"en": "High key", "ru": "Высокий ключ"},
        "desc": {"en": "Bright, even, almost shadowless. Advertising, comedy, relief after darkness.",
                 "ru": "Ярко, ровно, почти без теней. Реклама, комедия, отдых после темноты."},
        "add": "Lit high key: several soft sources bouncing evenly across the space, exposure bright and open, "
               "almost no visible shadows anywhere.",
        "note": {"en": "Works as contrast. Two high-key scenes in a row and the clip goes flat.",
                 "ru": "Работает как контраст. Две подряд — и клип становится плоским."},
        "slots": [], "traits": ["soft_light", "high_key"],
        "fits_with": ["detail_texture_macro", "portrait_two_shot"],
        "conflicts_with": ["l_chiaroscuro", "l_hard_single", "l_deep_night", "l_rim_back"],
    },

    # ══════════════ ВРЕМЯ СУТОК ══════════════
    {
        "key": "l_blue_hour", "group": "time", "tier": "free", "level": "scene",
        "label": {"en": "Blue hour", "ru": "Синий час"},
        "desc": {"en": "After sunset, before dark: the sky still lights the scene and every lamp is already on.",
                 "ru": "После заката, до темноты: небо ещё светит, а лампы уже горят."},
        "add": "Shot after sunset while the sky is still brighter than the ground, every lamp in the scene already "
               "switched on and competing with the last daylight.",
        "note": {"en": "The one time of day where interiors and exteriors balance without any trickery.",
                 "ru": "Единственное время суток, когда интерьер и натура сходятся без ухищрений."},
        "slots": [], "traits": ["night"],
        "fits_with": ["open_door_out", "final_pull_to_wide", "open_empty_place"],
        "conflicts_with": ["l_harsh_noon", "l_deep_night", "l_first_light"],
    },
    {
        "key": "l_golden_hour", "group": "time", "tier": "free", "level": "scene",
        "label": {"en": "Golden hour", "ru": "Золотой час"},
        "desc": {"en": "Low sun, long shadows, light coming almost horizontally into the lens.",
                 "ru": "Низкое солнце, длинные тени, свет идёт почти горизонтально в объектив."},
        "add": "Lit by a low sun close to the horizon, shadows stretched long across the ground and the light "
               "striking the subject almost horizontally from one side.",
        "note": {"en": "Sells any frame. Also the most overused hour in the catalogue — earn it.",
                 "ru": "Продаёт любой кадр. И самый заезженный час в каталоге — его надо заслужить."},
        "slots": [], "traits": ["daylight", "hard_light"],
        "fits_with": ["travel_side_track", "action_throw_away"],
        "conflicts_with": ["l_deep_night", "l_screen_glow", "l_blue_hour"],
    },
    {
        "key": "l_harsh_noon", "group": "time", "tier": "free", "level": "scene",
        "label": {"en": "Harsh midday", "ru": "Жёсткий полдень"},
        "desc": {"en": "Sun overhead, short black shadows, blown highlights. Uncomfortable on purpose.",
                 "ru": "Солнце сверху, короткие чёрные тени, выбитые блики. Неудобно намеренно."},
        "add": "Lit by an overhead midday sun, shadows short and black directly beneath everything, highlights "
               "burnt out on every bright surface.",
        "note": {"en": "The light nobody chooses, which is exactly why it reads as real.",
                 "ru": "Свет, который никто не выбирает, — именно поэтому он читается как настоящий."},
        "slots": [], "traits": ["daylight", "hard_light", "high_key"],
        "fits_with": ["crowd_queue_line", "action_run_toward"],
        "conflicts_with": ["l_deep_night", "l_blue_hour", "l_screen_glow", "l_chiaroscuro"],
    },
    {
        "key": "l_overcast_flat", "group": "time", "tier": "free", "level": "scene",
        "label": {"en": "Overcast", "ru": "Пасмурно"},
        "desc": {"en": "One enormous soft source: the sky. No shadows, no direction, no drama.",
                 "ru": "Один огромный мягкий источник — небо. Ни теней, ни направления, ни драмы."},
        "add": "Lit by a completely overcast sky acting as one enormous soft source, no visible shadows and no "
               "clear direction to the light.",
        "note": {"en": "Documentary by default. Everything looks honest and slightly sad.",
                 "ru": "По умолчанию документально. Всё выглядит честным и слегка грустным."},
        "slots": [], "traits": ["daylight", "soft_light"],
        "fits_with": ["crowd_one_still", "final_pull_to_wide", "travel_side_track"],
        "conflicts_with": ["l_chiaroscuro", "l_hard_single", "l_rim_back", "l_deep_night"],
    },
    {
        "key": "l_first_light", "group": "time", "tier": "free", "level": "scene",
        "label": {"en": "First light", "ru": "Первый свет"},
        "desc": {"en": "The sky is lighter than the ground and nothing has warmed up yet.",
                 "ru": "Небо светлее земли, и ничто ещё не прогрелось."},
        "add": "Shot at first light with the sky brighter than the ground, the air still and cold, the street "
               "lamps beginning to look unnecessary.",
        "note": {"en": "The natural ending of any night clip: same place, different hour.",
                 "ru": "Естественный финал любого ночного клипа: то же место, другой час."},
        "slots": [], "traits": ["daylight"],
        "fits_with": ["final_rhyme_open", "open_empty_place", "travel_walk_away"],
        "conflicts_with": ["l_deep_night", "l_harsh_noon", "l_blue_hour"],
    },
    {
        "key": "l_deep_night", "group": "time", "tier": "free", "level": "scene",
        "label": {"en": "Deep night", "ru": "Глухая ночь"},
        "desc": {"en": "No sky light at all. Everything visible is lit by something switched on.",
                 "ru": "Небо не светит вообще. Всё видимое освещено чем-то включённым."},
        "add": "Shot in full darkness with no light from the sky at all: everything visible is lit by lamps, "
               "windows or headlights, and the space between them is black.",
        "note": {"en": "Night is not darkness — it is a small number of sources. Name them or the frame goes muddy.",
                 "ru": "Ночь — это не темнота, а малое число источников. Назови их, иначе кадр поплывёт."},
        "slots": [], "traits": ["night", "low_key"],
        "fits_with": ["night_headlights", "night_rain_reflect", "night_lamp_pass"],
        "conflicts_with": ["l_harsh_noon", "l_overcast_flat", "l_golden_hour", "l_first_light"],
    },

    # ══════════════ ПАЛИТРА (уступает стилю) ══════════════
    {
        "key": "l_one_accent", "group": "palette", "tier": "free", "level": "style",
        "label": {"en": "One accent colour", "ru": "Один акцентный цвет"},
        "desc": {"en": "Everything quiet except one object. The eye is led by colour, not by composition.",
                 "ru": "Всё тихо, кроме одного предмета. Взгляд ведёт цвет, а не композиция."},
        "add": "Colour note, " + DEFER + ": let a single {accent} element be the only loud colour in the frame "
               "while everything else stays quiet.",
        "note": {"en": "Keep the same accent all clip or it stops being an accent.",
                 "ru": "Держи один и тот же акцент весь клип, иначе он перестаёт быть акцентом."},
        "slots": ["accent"], "traits": [],
        "fits_with": ["night_neon_wall", "detail_object_pickup", "crowd_one_still"],
        "conflicts_with": ["l_three_colours"],
    },
    {
        "key": "l_complementary", "group": "palette", "tier": "free", "level": "style",
        "label": {"en": "Warm skin, cold shadows", "ru": "Тёплая кожа, холодные тени"},
        "desc": {"en": "The oldest contrast in the book: warm subject against cold surroundings.",
                 "ru": "Древнейший контраст: тёплый герой на холодном окружении."},
        "add": "Colour note, " + DEFER + ": keep the skin tones warm against cooler surroundings so the figure "
               "separates from the background by temperature.",
        "note": {"en": "Works on any style that has colour at all. Skip it on monochrome styles.",
                 "ru": "Работает на любом цветном стиле. На монохромных пропускать."},
        "slots": [], "traits": [],
        "fits_with": ["portrait_hold_still", "night_lamp_pass"],
        "conflicts_with": ["l_analogous_warm"],
    },
    {
        "key": "l_three_colours", "group": "palette", "tier": "pro", "level": "style",
        "label": {"en": "Three colours only", "ru": "Только три цвета"},
        "desc": {"en": "A deliberately limited palette. Makes a frame look designed rather than filmed.",
                 "ru": "Намеренно ограниченная палитра. Кадр начинает выглядеть спроектированным."},
        "add": "Colour note, " + DEFER + ": hold the frame to three colours only — one dark, one light and "
               "{accent} — and keep everything else out.",
        "note": {"en": "The strongest palette rule and the hardest to keep across thirty scenes.",
                 "ru": "Самое сильное палитрное правило и самое трудное для тридцати сцен."},
        "slots": ["accent"], "traits": [],
        "fits_with": ["bridge_match_shape", "crowd_queue_line"],
        "conflicts_with": ["l_one_accent", "l_analogous_warm"],
    },
    {
        "key": "l_analogous_warm", "group": "palette", "tier": "free", "level": "style",
        "label": {"en": "All warm", "ru": "Всё тёплое"},
        "desc": {"en": "Nothing cold anywhere in frame. Unity instead of contrast.",
                 "ru": "Ничего холодного в кадре. Единство вместо контраста."},
        "add": "Colour note, " + DEFER + ": keep every colour in the frame on the warm side, with nothing cold "
               "anywhere in the shot.",
        "note": {"en": "Reads as memory. Pairs badly with anything that needs a cold accent.",
                 "ru": "Читается как воспоминание. Плохо дружит со всем, чему нужен холодный акцент."},
        "slots": [], "traits": [],
        "fits_with": ["interior_table_sit", "detail_hands_work", "bridge_door_through"],
        "conflicts_with": ["l_complementary", "l_three_colours"],
    },

    # ══════════════ КОНТРАСТ И ГРЕЙД (уступает стилю) ══════════════
    {
        "key": "l_contrast_hold", "group": "grade", "tier": "free", "level": "style",
        "label": {"en": "Hold the contrast", "ru": "Держать контраст"},
        "desc": {"en": "Ask for separation between light and dark without touching the palette.",
                 "ru": "Просим разделения света и тени, не трогая палитру."},
        "add": "Contrast note, " + DEFER + ": keep a clear separation between the lit and unlit parts of the "
               "frame rather than an even exposure.",
        "note": {"en": "Use on scenes that came back flat. Not a fix for a bad lighting setup.",
                 "ru": "Для сцен, которые вернулись плоскими. Не лечит плохую схему света."},
        "slots": [], "traits": [],
        "fits_with": ["portrait_breath_break", "interior_doorframe"],
        "conflicts_with": ["l_soft_bloom"],
    },
    {
        "key": "l_crushed_blacks", "group": "grade", "tier": "pro", "level": "style",
        "label": {"en": "Let the blacks go", "ru": "Отпустить чёрное"},
        "desc": {"en": "Shadows with no detail at all. Hides everything the model draws badly.",
                 "ru": "Тени вообще без деталей. Прячет всё, что модель рисует плохо."},
        "add": "Contrast note, " + DEFER + ": let the shadows go completely black with no detail recovered in "
               "them.",
        "note": {"en": "A production trick, not a look: less visible area means fewer visible mistakes.",
                 "ru": "Приём производства, а не стиля: меньше видимой площади — меньше видимых ошибок."},
        "slots": [], "traits": [],
        "fits_with": ["bridge_body_wipe", "night_headlights", "open_from_black"],
        "conflicts_with": ["l_soft_bloom", "l_high_key_bounce"],
    },
    {
        "key": "l_soft_bloom", "group": "grade", "tier": "free", "level": "style",
        "label": {"en": "Let the highlights bloom", "ru": "Дать светам расцвести"},
        "desc": {"en": "Bright sources allowed to spread softly instead of staying sharp.",
                 "ru": "Яркие источники расплываются мягко, а не остаются резкими."},
        "add": "Highlight note, " + DEFER + ": let the bright sources in frame spread softly at their edges "
               "instead of staying hard.",
        "note": {"en": "Turns lamps and signs into the subject. Fights any style that wants clean edges.",
                 "ru": "Делает лампы и вывески главными в кадре. Спорит с любым стилем, которому нужны чистые края."},
        "slots": [], "traits": [],
        "fits_with": ["night_neon_wall", "night_rain_reflect", "travel_car_window"],
        "conflicts_with": ["l_contrast_hold", "l_crushed_blacks"],
    },
]


# ─────────────────────────────────────────────────────────────────────────────
# 5. МИКСОВАНИЕ — правила, по которым четыре слоя складываются в одну сцену.
#
# Порядок сборки один и обратному не подлежит:
#
#     СТИЛЬ ТРЕКА  →  ЗАГОТОВКА  →  ДВИЖЕНИЕ  →  СВЕТ И ЦВЕТ
#     (рендер)        (BOARDS)      (MOTIONS)    (LIGHTS)
#
# Стиль подставляет конвейер первым блоком — карточки его не видят и видеть не
# должны. Заготовка даёт все шесть полей сцены. Движение ЗАМЕНЯЕТ motion_prompt
# целиком, а не дописывается к нему: два описания движения в одном поле — это
# ровно то противоречие, на котором модель ломается. Свет ДОПИСЫВАЕТСЯ в конец
# обоих кадров, потому что он уточняет картинку, а не переопределяет её.
#
# ПОТОЛКИ. Один каркас, одна заготовка, одно движение, не больше двух световых
# модификаторов и не больше одного из них уровня style. Потолок не эстетический:
# каждый модификатор — это ещё одно указание в промпте длиной в две-три фразы,
# и после третьего движки начинают выбирать между ними случайно.
#
# КАК СЧИТАЕТСЯ КОНФЛИКТ. Двумя способами, и оба нужны:
#   1. По чертам (TRAITS + CONFLICT_PAIRS) — машинно и без участия автора
#      карточки. «Камера стоит» и «камера едет», «полдень» и «ночь», «жёсткий»
#      и «мягкий» несовместимы физически, а не по вкусу.
#   2. По явным спискам conflicts_with / fits_with в самих карточках — там, где
#      конфликт содержательный и чертами не выражается (наезд против отъезда).
# Первый способ ловит то, о чём автор не подумал; второй — то, о чём подумал.
# ─────────────────────────────────────────────────────────────────────────────
MIX_RULES = {
    "max_boards": 1,
    "max_motions": 1,
    "max_lights": 2,
    "max_style_lights": 1,
    "order": ["style", "board", "motion", "light"],
    "note": {
        "en": "Style first and untouchable, then one board, then one motion, then at most two light modifiers "
              "(only one of them may be a palette or grade card).",
        "ru": "Сначала стиль, он неприкосновенен; затем одна заготовка, одно движение и не больше двух световых "
              "модификаторов, из которых не больше одного — палитра или грейд.",
    },
}

#: Черты, которыми распоряжается камера. Карточка движения их перебивает.
_CAMERA_TRAITS = {"locked", "moving_camera", "handheld"}

#: Движки, у которых нет последнего кадра: motion берётся из `solo`.
_SOLO_ENGINES = ("grok",)
#: Движок, читающий команды камеры в скобках.
_BRACKET_ENGINES = ("minimax-h3",)

_BOARD_BY_KEY = {b["key"]: b for b in BOARDS}
_MOTION_BY_KEY = {m["key"]: m for m in MOTIONS}
_LIGHT_BY_KEY = {l["key"]: l for l in LIGHTS}
_SCRIPT_BY_KEY = {s["key"]: s for s in SCRIPTS}

BOARD_KEYS = tuple(_BOARD_BY_KEY)
MOTION_KEYS = tuple(_MOTION_BY_KEY)
LIGHT_KEYS = tuple(_LIGHT_BY_KEY)
SCRIPT_KEYS = tuple(_SCRIPT_BY_KEY)

#: Тексты заготовки — то, что закрывается тарифом. Всё остальное в карточке
#: (подпись, описание, крупность, движение камеры, черты) видно всем: человек
#: должен понимать, чего лишён, а не смотреть в пустое место.
_BOARD_TEXTS = ("first", "last", "motion", "solo", "negative")
_MOTION_TEXTS = ("text", "solo")
_LIGHT_TEXTS = ("add",)

PUBLIC_BOARD_FIELDS = ("key", "group", "tier", "label", "desc", "shot", "camera",
                       "bracket", "note", "slots", "traits", "needs_last",
                       "engines", "fits_with", "conflicts_with", "styles_fit", "tags")
PUBLIC_MOTION_FIELDS = ("key", "group", "tier", "label", "desc", "camera", "bracket",
                        "physics", "slots", "traits", "needs_last", "engines",
                        "fits_with", "conflicts_with")
PUBLIC_LIGHT_FIELDS = ("key", "group", "tier", "level", "label", "desc", "note",
                       "slots", "traits", "fits_with", "conflicts_with")
PUBLIC_SCRIPT_FIELDS = ("key", "tier", "label", "music", "bpm", "cut", "logline",
                        "hero", "motif", "opens", "closes", "acts", "scenes",
                        "open_board", "close_board", "styles_fit", "preset",
                        "slots_hint", "tags", "needs_lyrics")


def _slot_values(slots: list[str], given: dict | None) -> dict:
    """Значения подстановки: что дал человек, иначе английский пример из SLOTS.
    Пустой {character} в промпте хуже любого умолчания — см. fill() выше."""
    given = given or {}
    return {k: str(given.get(k) or "").strip() or SLOTS[k]["example"]["en"] for k in slots}


def _sub(text: str, values: dict) -> str:
    return _SLOT_RE.sub(lambda m: values.get(m.group(1), m.group(0)), text)


def board_fill(key: str, slots: dict | None = None) -> dict | None:
    """Подставить слоты в тексты заготовки."""
    b = _BOARD_BY_KEY.get(key)
    if not b:
        return None
    values = _slot_values(b["slots"], slots)
    out = {f: _sub(b[f], values) for f in _BOARD_TEXTS}
    out["used"] = values
    return out


def motion_text(key: str, slots: dict | None = None, *, engine: str = "") -> str:
    """Готовый motion_prompt приёма движения под конкретный движок.

    Grok получает `solo` (у него нет последнего кадра), MiniMax — команду в
    скобках плюс текст, все остальные — текст пары кадров. Это единственное
    место в файле, где движок вообще влияет на текст."""
    m = _MOTION_BY_KEY.get(key)
    if not m:
        return ""
    values = _slot_values(m["slots"], slots)
    if engine in _SOLO_ENGINES:
        return _sub(m["solo"], values)
    body = _sub(m["text"], values)
    if engine in _BRACKET_ENGINES and m["bracket"]:
        return f"{m['bracket']} {body}"
    return body


def board_patch(key: str, slots: dict | None = None, *, lang: str = "ru",
                engine: str = "") -> dict | None:
    """Готовое тело для PATCH /api/scenes/{id} из одной заготовки.

    Ключи — ровно те, что принимает update_scene() в main.py, плюс shot_note,
    который тот же роут тоже принимает. Ничего лишнего: лишнее поле бэкенд
    молча уронит, и «применить» применит половину карточки."""
    body = board_fill(key, slots)
    if not body:
        return None
    b = _BOARD_BY_KEY[key]
    motion = body["solo"] if engine in _SOLO_ENGINES else body["motion"]
    if engine in _BRACKET_ENGINES and b["bracket"]:
        motion = f"{b['bracket']} {motion}"
    return {
        "image_prompt": body["first"],
        "image_prompt_last": body["last"],
        "motion_prompt": motion,
        "shot_size": b["shot"],
        "camera_move": b["camera"],
        "shot_note": _localise(b["note"], lang),
    }


def check_mix(board: str = "", motion: str = "", lights: list[str] | None = None) -> list[dict]:
    """Проверить сочетаемость до генерации. Возвращает список конфликтов.

    Пустой список означает «собирается». Ничего не блокируется: карточка может
    конфликтовать осознанно, и решает человек. Наше дело — сказать, чем он
    платит, а не запретить."""
    lights = list(lights or [])
    out: list[dict] = []

    if len(lights) > MIX_RULES["max_lights"]:
        out.append({"kind": "limit", "a": "lights", "b": "",
                    "en": f"More than {MIX_RULES['max_lights']} light modifiers: the engine starts choosing "
                          f"between them at random.",
                    "ru": f"Больше {MIX_RULES['max_lights']} световых модификаторов: движок начинает выбирать "
                          f"между ними наугад."})
    style_lights = [k for k in lights if (_LIGHT_BY_KEY.get(k) or {}).get("level") == "style"]
    if len(style_lights) > MIX_RULES["max_style_lights"]:
        out.append({"kind": "limit", "a": "lights", "b": "",
                    "en": "Two palette or grade cards at once — they argue with the track style and with each other.",
                    "ru": "Две карточки палитры или грейда сразу — они спорят и со стилем трека, и между собой."})

    cards = []
    if board and board in _BOARD_BY_KEY:
        cards.append(("board", board, _BOARD_BY_KEY[board]))
    if motion and motion in _MOTION_BY_KEY:
        cards.append(("motion", motion, _MOTION_BY_KEY[motion]))
    for k in lights:
        if k in _LIGHT_BY_KEY:
            cards.append(("light", k, _LIGHT_BY_KEY[k]))

    # 1. Явные списки: то, о чём автор карточки подумал.
    for _, key_a, a in cards:
        for _, key_b, _b in cards:
            if key_a != key_b and key_b in a.get("conflicts_with", ()):
                out.append({"kind": "explicit", "a": key_a, "b": key_b,
                            "en": "The cards are marked as incompatible.",
                            "ru": "Карточки помечены как несочетаемые."})

    # 2. Черты: то, о чём он не подумал. Физически взаимоисключающие условия.
    #
    # Одно исключение, и оно принципиальное: карточка движения ЗАМЕНЯЕТ камеру
    # заготовки (mix() переписывает camera_move), а не спорит с ней. Поэтому
    # наезд на статичную заготовку — это не конфликт, а ровно то, ради чего
    # карточки движения существуют. Сравниваем «камерные» черты заготовки
    # только тогда, когда движение о камере ничего не говорит.
    motion_card = _MOTION_BY_KEY.get(motion) if motion else None
    motion_owns_camera = bool(motion_card and (set(motion_card["traits"]) & _CAMERA_TRAITS))
    eff = {}
    for kind, key_c, c in cards:
        traits = set(c.get("traits", ()))
        if kind == "board" and motion_owns_camera:
            traits -= _CAMERA_TRAITS
        eff[key_c] = traits
    for i, (_, key_a, a) in enumerate(cards):
        for _, key_b, b in cards[i + 1:]:
            for ta in eff[key_a]:
                for tb in eff[key_b]:
                    pair = (ta, tb)
                    if pair in CONFLICT_PAIRS or (tb, ta) in CONFLICT_PAIRS:
                        out.append({
                            "kind": "trait", "a": key_a, "b": key_b,
                            "en": f"{TRAITS[ta]['en']} and {TRAITS[tb]['en']} cannot both be true in one frame.",
                            "ru": f"«{TRAITS[ta]['ru']}» и «{TRAITS[tb]['ru']}» не бывают в одном кадре одновременно.",
                        })
    # Один и тот же конфликт может прийти обоими путями — показываем один раз.
    seen, uniq = set(), []
    for c in out:
        sig = (c["kind"], tuple(sorted((c["a"], c["b"]))), c["en"])
        if sig not in seen:
            seen.add(sig)
            uniq.append(c)
    return uniq


#: Поля сцены, которыми распоряжается сборка. Ровно те, что принимает
#: update_scene() в main.py, и ровно те, что показывает предпросмотр «было →
#: станет». Список здесь один: разъехавшись, он показал бы одно, а записал другое.
MIX_FIELDS = ("image_prompt", "image_prompt_last", "motion_prompt",
              "shot_size", "camera_move", "shot_note")


def light_tail(keys, slots: dict | None = None) -> str:
    """Хвост световых модификаторов одной строкой — то, что ДОПИСЫВАЕТСЯ в конец
    обоих кадров. Свет не переписывает кадр, а уточняет его: переписывающий
    модификатор был бы вторым описанием сцены, и движок выбирал бы между ними."""
    parts = []
    for k in keys or ():
        card = _LIGHT_BY_KEY.get(k)
        if card:
            parts.append(_sub(card["add"], _slot_values(card["slots"], slots)))
    return " ".join(parts)


def mix_scene(board: str = "", *, motion: str = "", lights: list[str] | None = None,
              slots: dict | None = None, lang: str = "ru", engine: str = "",
              base: dict | None = None) -> dict:
    """ОБЩАЯ сборка: заготовка, движение и свет в любом сочетании, в том числе
    поверх уже написанного кадра.

    `base` — текущие поля сцены. Он и есть причина, по которой функция общая, а
    не «применить заготовку»: владелец просил подмешивать движение и свет к УЖЕ
    существующему кадру, а не только к свежесозданному. Без base карточка света
    не имела бы к чему дописаться, и «подмешать» означало бы «стереть и написать
    заново» — то есть потерю ручной правки промпта.

    Порядок жёсткий и он же порядок MIX_RULES: стиль трека (его здесь нет и быть
    не может — он подставляется на генерации) → заготовка → движение → свет.
    Движение ЗАМЕНЯЕТ motion_prompt и camera_move целиком; свет ДОПИСЫВАЕТСЯ.

    Неизвестные ключи молча пропускаются: 404 и 403 — работа роута, который
    знает про тариф и про человека, а не словаря.

    Ничего не блокируется. Конфликты возвращаются словами, решает человек."""
    lights = [k for k in (lights or []) if k in _LIGHT_BY_KEY]

    if board and board in _BOARD_BY_KEY:
        patch = board_patch(board, slots, lang=lang, engine=engine) or {}
    elif base:
        patch = {f: str(base.get(f) or "") for f in MIX_FIELDS}
        board = board if board in _BOARD_BY_KEY else ""
    else:
        patch, board = {}, ""

    if motion in _MOTION_BY_KEY:
        text = motion_text(motion, slots, engine=engine)
        if text:
            patch["motion_prompt"] = text
            cam = _MOTION_BY_KEY[motion]["camera"]
            if cam:
                patch["camera_move"] = cam
    else:
        motion = ""

    tail = light_tail(lights, slots)
    if tail:
        # Дописываем ТОЛЬКО в непустой кадр. Свет в одиночку не сочиняет сцену:
        # строка «Lit from behind…» без описания того, что снято, — это не кадр,
        # а половина указания, и движок дорисует вторую половину сам.
        for f in ("image_prompt", "image_prompt_last"):
            if patch.get(f):
                patch[f] = f"{patch[f]} {tail}"

    used: dict = {}
    if board:
        used.update(_slot_values(_BOARD_BY_KEY[board]["slots"], slots))
    if motion:
        used.update(_slot_values(_MOTION_BY_KEY[motion]["slots"], slots))
    for k in lights:
        used.update(_slot_values(_LIGHT_BY_KEY[k]["slots"], slots))

    return {
        "board": board, "motion": motion, "lights": lights,
        "scene": patch,
        "add": tail,
        "used": used,
        "negative": _BOARD_BY_KEY[board]["negative"] if board else "",
        "engine": engine or "",
        "conflicts": check_mix(board, motion, lights),
    }


def mix(board: str, *, motion: str = "", lights: list[str] | None = None,
        slots: dict | None = None, lang: str = "ru", engine: str = "") -> dict | None:
    """Собрать сцену ИЗ ЗАГОТОВКИ. Частный случай mix_scene() и намеренно
    отдельная функция: примеры и документация собираются только так, с
    обязательной заготовкой, и `None` на неизвестном ключе им нужен.

    Возвращает готовое тело PATCH, отдельно негатив (его принимает не всякий
    канал) и список конфликтов. Конфликты НЕ блокируют сборку: решает человек,
    а наше дело — предупредить."""
    if board not in _BOARD_BY_KEY:
        return None
    return mix_scene(board, motion=motion, lights=lights, slots=slots,
                     lang=lang, engine=engine)


def script_seed(key: str) -> dict:
    """Сюжетный seed сценария: story → Project.story, note → Track.director_note.
    Та же форма, что у prompts_catalog.preset_seed — вызывающий код не должен
    знать, из какого слоя пришёл каркас."""
    s = _SCRIPT_BY_KEY.get(key)
    if not s:
        return {"story": "", "note": ""}
    return {"story": s["story"], "note": s["dnote"]}


def script_boards(key: str) -> list[str]:
    """Все заготовки сценария по порядку актов, без повторов: это готовый
    черновик раскадровки, который остаётся разложить по сценам трека."""
    s = _SCRIPT_BY_KEY.get(key)
    if not s:
        return []
    out: list[str] = []
    for ref in [s["open_board"]] + [k for a in s["acts"] for k in a["boards"]] + [s["close_board"]]:
        if ref not in out:
            out.append(ref)
    return out


def _public(card: dict, fields, texts, *, lang: str, plan_id: str,
            is_admin: bool) -> dict:
    out = {f: card[f] for f in fields if f in card}
    open_text = unlocked(card["tier"], plan_id, is_admin=is_admin)
    out["locked"] = not open_text
    if open_text:
        for f in texts:
            out[f] = card[f]
    if lang in ("en", "ru"):
        for f in ("label", "desc", "note", "physics", "music", "logline", "hero",
                  "motif", "opens", "closes"):
            if f in out:
                out[f] = _localise(out[f], lang)
        if "slots" in out:
            out["slots"] = [
                {"key": k,
                 "label": _localise(SLOTS[k]["label"], lang),
                 "hint": _localise(SLOTS[k]["hint"], lang),
                 "example": _localise(SLOTS[k]["example"], lang)}
                for k in card["slots"]
            ]
        if "traits" in out:
            out["traits"] = [{"key": t, "label": _localise(TRAITS[t], lang)}
                             for t in card["traits"]]
    return out


def public_board(key: str, *, lang: str = "", plan_id: str = "free",
                 is_admin: bool = False) -> dict | None:
    b = _BOARD_BY_KEY.get(key)
    return _public(b, PUBLIC_BOARD_FIELDS, _BOARD_TEXTS, lang=lang,
                   plan_id=plan_id, is_admin=is_admin) if b else None


def public_boards(*, lang: str = "", group: str = "", tier: str = "",
                  style: str = "", plan_id: str = "free",
                  is_admin: bool = False) -> list[dict]:
    out = []
    for b in BOARDS:
        if group and b["group"] != group:
            continue
        if tier and b["tier"] != tier:
            continue
        if style and style not in b["styles_fit"]:
            continue
        out.append(public_board(b["key"], lang=lang, plan_id=plan_id, is_admin=is_admin))
    return out


def public_motion(key: str, *, lang: str = "", plan_id: str = "free",
                  is_admin: bool = False) -> dict | None:
    m = _MOTION_BY_KEY.get(key)
    return _public(m, PUBLIC_MOTION_FIELDS, _MOTION_TEXTS, lang=lang,
                   plan_id=plan_id, is_admin=is_admin) if m else None


def public_motions(*, lang: str = "", group: str = "", plan_id: str = "free",
                   is_admin: bool = False) -> list[dict]:
    return [public_motion(m["key"], lang=lang, plan_id=plan_id, is_admin=is_admin)
            for m in MOTIONS if not group or m["group"] == group]


def public_light(key: str, *, lang: str = "", plan_id: str = "free",
                 is_admin: bool = False) -> dict | None:
    l = _LIGHT_BY_KEY.get(key)
    return _public(l, PUBLIC_LIGHT_FIELDS, _LIGHT_TEXTS, lang=lang,
                   plan_id=plan_id, is_admin=is_admin) if l else None


def public_lights(*, lang: str = "", group: str = "", plan_id: str = "free",
                  is_admin: bool = False) -> list[dict]:
    return [public_light(l["key"], lang=lang, plan_id=plan_id, is_admin=is_admin)
            for l in LIGHTS if not group or l["group"] == group]


def public_script(key: str, *, lang: str = "", plan_id: str = "free",
                  is_admin: bool = False) -> dict | None:
    """Карточка сценария. Закрыт ровно `story` — сам сюжетный seed; акты,
    логлайн и роль героя видно всем, потому что по ним и выбирают."""
    s = _SCRIPT_BY_KEY.get(key)
    if not s:
        return None
    out = {f: s[f] for f in PUBLIC_SCRIPT_FIELDS if f in s}
    open_text = unlocked(s["tier"], plan_id, is_admin=is_admin)
    out["locked"] = not open_text
    if open_text:
        out["story"] = s["story"]
        out["dnote"] = s["dnote"]
    out["boards"] = script_boards(key)
    if lang in ("en", "ru"):
        for f in ("label", "music", "logline", "hero", "motif", "opens", "closes"):
            out[f] = _localise(out[f], lang)
        out["acts"] = [
            {"key": a["key"], "share": a["share"], "shot": a["shot"],
             "label": _localise(a["label"], lang),
             "text": a.get(lang) or a["en"],
             "boards": list(a["boards"])}
            for a in s["acts"]
        ]
    return out


def public_scripts(*, lang: str = "", cut: str = "", style: str = "",
                   plan_id: str = "free", is_admin: bool = False) -> list[dict]:
    out = []
    for s in SCRIPTS:
        if cut and s["cut"] != cut:
            continue
        if style and style not in s["styles_fit"]:
            continue
        out.append(public_script(s["key"], lang=lang, plan_id=plan_id, is_admin=is_admin))
    return out


# ─────────────────────────────────────────────────────────────────────────────
# 6. ПРИМЕРЫ. Владелец просил их прямо, и они здесь ДАННЫЕ, а не текст в
# документации: docs/prompts-library.md печатает их вызовом render_example(),
# поэтому пример физически не может разойтись с карточкой. Разошедшийся пример
# хуже отсутствующего — он учит формулировке, которой в продукте уже нет.
#
# По одному примеру на каждую группу заготовок плюс по одному на сценарий,
# движение и свет.
# ─────────────────────────────────────────────────────────────────────────────
EXAMPLES: list[dict] = [
    {
        "key": "ex_opening", "of": "board", "group": "opening",
        "label": {"en": "Opening: out of the door at dusk", "ru": "Открывающий: выход из подъезда в сумерках"},
        "script": "night_shift", "board": "open_door_out",
        "motion": "m_coat_inertia", "lights": ["l_practical_only", "l_blue_hour"],
        "slots": {"character": "young man in a black hooded jacket",
                  "location": "panel block courtyard", "time": "late dusk",
                  "outfit": "long grey coat"},
        "why": {"en": "Two scene-level light cards, no palette card: the track style already owns the colour.",
                "ru": "Два световых модификатора уровня сцены и ни одного палитрного: цветом уже распоряжается стиль."},
    },
    {
        "key": "ex_travel", "of": "board", "group": "travel",
        "label": {"en": "Travel: walking away down a wet street", "ru": "Проход: уход по мокрой улице"},
        "script": "black_car", "board": "travel_walk_away",
        "motion": "m_steadi_follow", "lights": ["l_deep_night"],
        "slots": {"character": "woman in a long coat", "location": "narrow one-way street",
                  "time": "3 a.m."},
        "why": {"en": "The motion card replaces camera_move as well — following is the shot, not a decoration.",
                "ru": "Карточка движения заменяет и camera_move: следование здесь и есть кадр, а не украшение."},
    },
    {
        "key": "ex_portrait", "of": "board", "group": "portrait",
        "label": {"en": "Portrait: held close-up", "ru": "Портрет: держим крупный план"},
        "script": "the_offer", "board": "portrait_hold_still",
        "motion": "m_push_settle", "lights": ["l_hard_single"],
        "slots": {"character": "man in his forties, shaved head",
                  "location": "empty office room", "emotion": "jaw set, eyes not blinking"},
        "why": {"en": "Push-in on a locked-off board: allowed, because the board's frame is static, not the camera.",
                "ru": "Наезд на статичную заготовку: можно, потому что статична композиция, а не камера."},
    },
    {
        "key": "ex_detail", "of": "board", "group": "detail",
        "label": {"en": "Detail: hands finishing the work", "ru": "Деталь: руки заканчивают работу"},
        "script": "night_shift", "board": "detail_hands_work",
        "motion": "m_hand_raise", "lights": ["l_hard_single", "l_analogous_warm"],
        "slots": {"prop": "steel kettle", "location": "canteen kitchen"},
        "why": {"en": "One scene light plus one palette card — the palette card defers to the style in its wording.",
                "ru": "Один свет уровня сцены плюс одна палитра — и палитра формулировкой уступает стилю."},
    },
    {
        "key": "ex_action", "of": "board", "group": "action",
        "label": {"en": "Action: running at the lens", "ru": "Экшен: бег на объектив"},
        "script": "run_the_block", "board": "action_run_toward",
        "motion": "m_dust_bloom", "lights": ["l_hard_single"],
        "slots": {"character": "teenager in a red tracksuit", "location": "concrete underpass",
                  "time": "midday"},
        "why": {"en": "A physics card instead of a camera card: on fast scenes the camera must stay still.",
                "ru": "Карточка физики вместо камеры: на быстрых сценах камера обязана стоять."},
    },
    {
        "key": "ex_crowd", "of": "board", "group": "crowd",
        "label": {"en": "Crowd: one still figure", "ru": "Толпа: одна неподвижная фигура"},
        "script": "market_day", "board": "crowd_one_still",
        "motion": "m_freeze_settle", "lights": ["l_overcast_flat"],
        "slots": {"crowd": "commuters in dark coats", "character": "girl with a shaved head",
                  "location": "station concourse", "time": "morning rush"},
        "why": {"en": "Flat overcast light on a crowd scene: shadows would turn a dense crowd into mush.",
                "ru": "Ровный пасмурный свет на толпе: тени превратили бы плотную массу в кашу."},
    },
    {
        "key": "ex_interior", "of": "board", "group": "interior",
        "label": {"en": "Interior: by the window", "ru": "Интерьер: у окна"},
        "script": "two_rooms", "board": "interior_window_side",
        "motion": "m_breath_fog", "lights": ["l_window_motivated"],
        "slots": {"character": "woman in a knitted jumper", "location": "one-room flat",
                  "time": "first light"},
        "why": {"en": "The light card repeats the board's own direction of light instead of adding a second source.",
                "ru": "Световая карточка повторяет направление света заготовки, а не добавляет второй источник."},
    },
    {
        "key": "ex_night", "of": "board", "group": "night",
        "label": {"en": "Night: into the lamp light", "ru": "Ночь: вход в пятно фонаря"},
        "script": "cold_call", "board": "night_lamp_pass",
        "motion": "m_weight_step", "lights": ["l_deep_night", "l_crushed_blacks"],
        "slots": {"character": "man in a leather jacket", "location": "empty embankment",
                  "time": "2 a.m."},
        "why": {"en": "The grade card is a production trick here: less visible area means fewer visible mistakes.",
                "ru": "Грейд здесь производственный приём: меньше видимой площади — меньше видимых ошибок."},
    },
    {
        "key": "ex_final", "of": "board", "group": "final",
        "label": {"en": "Final: pull back to nothing", "ru": "Финал: отъезд в пустоту"},
        "script": "last_train", "board": "final_pull_to_wide",
        "motion": "m_pull_open", "lights": ["l_first_light"],
        "slots": {"character": "man with a duffel bag", "location": "end-of-line platform",
                  "time": "first light"},
        "why": {"en": "Board and motion say the same thing on purpose — the motion card only sharpens the wording.",
                "ru": "Заготовка и движение говорят одно и то же намеренно: карточка движения лишь уточняет формулировку."},
    },
    {
        "key": "ex_bridge", "of": "board", "group": "bridge",
        "label": {"en": "Bridge: out of light into dark", "ru": "Переход: из света в темноту"},
        "script": "the_return", "board": "bridge_light_to_dark",
        "motion": "m_blur_resolve", "lights": ["l_chiaroscuro"],
        "slots": {"character": "man in a denim jacket", "location": "underpass mouth",
                  "time": "late afternoon"},
        "why": {"en": "A bridge is written as one scene but applied as a pair — the next scene opens on the dark end.",
                "ru": "Переход пишется как одна сцена, а применяется парой: следующая сцена открывается с тёмного конца."},
    },
    {
        "key": "ex_script", "of": "script", "group": "script",
        "label": {"en": "A script laid onto a storyboard", "ru": "Сценарий, разложенный на раскадровку"},
        "script": "night_shift", "board": "open_empty_place",
        "motion": "m_smoke_curl", "lights": ["l_practical_only"],
        "slots": {"location": "empty depot hall", "time": "half past midnight",
                  "weather": "cold draught", "character": "man in a work jacket"},
        "why": {"en": "script_boards() returns the whole draft storyboard in act order; this is its first scene.",
                "ru": "script_boards() отдаёт черновик раскадровки целиком по актам; это её первая сцена."},
    },
    {
        "key": "ex_motion", "of": "motion", "group": "motion",
        "label": {"en": "One board, three engines", "ru": "Одна заготовка, три движка"},
        "script": "", "board": "portrait_turn_to_lens",
        "motion": "m_turn_to_lens", "lights": [],
        "slots": {"character": "girl with wet hair", "location": "tiled bathroom",
                  "emotion": "mouth closed, eyes steady"},
        "why": {"en": "The same card renders differently for a first-last engine, for Grok and for MiniMax.",
                "ru": "Одна и та же карточка рендерится по-разному для движка с двумя кадрами, для Grok и для MiniMax."},
    },
    {
        "key": "ex_light", "of": "light", "group": "light",
        "label": {"en": "Light modifiers on one board", "ru": "Световые модификаторы на одной заготовке"},
        "script": "stage_and_after", "board": "night_neon_wall",
        "motion": "m_hair_lag", "lights": ["l_one_accent", "l_deep_night"],
        "slots": {"character": "rapper in a white vest", "location": "back alley behind the venue",
                  "time": "after midnight", "accent": "sodium orange"},
        "why": {"en": "One palette card and one time-of-day card: the limit, and the palette one defers to the style.",
                "ru": "Одна палитра и одно время суток — это потолок, и палитра уступает стилю формулировкой."},
    },
]


def render_example(key: str, *, lang: str = "ru", engine: str = "") -> dict | None:
    """Собрать пример так же, как его собрал бы человек кнопкой «применить».
    Именно этот вызов печатает документация — поэтому пример не может
    разойтись с карточкой."""
    ex = next((e for e in EXAMPLES if e["key"] == key), None)
    if not ex:
        return None
    res = mix(ex["board"], motion=ex["motion"], lights=ex["lights"],
              slots=ex["slots"], lang=lang, engine=engine)
    if not res:
        return None
    res["example"] = ex["key"]
    res["label"] = _localise(ex["label"], lang)
    res["why"] = _localise(ex["why"], lang)
    res["script"] = ex["script"]
    res["slots_used"] = dict(ex["slots"])
    return res


# ─────────────────────────────────────────────────────────────────────────────
# 7. САМОПРОВЕРКА НОВЫХ СЛОЁВ. Вызывается из validate() выше.
#
# Проверки не косметические: каждая ловит ошибку, которая иначе всплыла бы
# испорченным кадром у человека, а не падением у нас.
# ─────────────────────────────────────────────────────────────────────────────
#: Слова, которыми распоряжается СТИЛЬ. В кадре заготовки их быть не может:
#: дублирование грейда уже ломало картинку и уже чинилось.
_BAN_GRADE = ("film", "grain", "35mm", "16mm", "4k", "8k", "cinematic", "masterpiece",
              "vhs", "teal", "halation", "photorealistic", "hyperrealistic",
              "colour grade", "color grade", "anime", "painterly", "hdr")

#: Ссылки на соседние кадры. Модель анимирует сцену изолированно и «как раньше»
#: понять не может — она это просто выдумает.
_BAN_REF = ("same as before", "as before", "previously", "continues from",
            "in the previous", "like earlier", "as established")

#: Движение без конечного состояния подвешивает генерацию и даёт дрейф — это
#: ровно та болезнь, из-за которой ролик «застывает на 99%». Проверяется
#: регуляркой _SETTLE_RE ниже.


#: Конечное состояние ищется по основам слов: settles / settling / stopped /
#: holds / comes to rest. Список форм перечислять нельзя — забудешь одну, и
#: карточка проедет проверку.
_SETTLE_RE = re.compile(r"\b(settl\w*|stop\w*|hold\w*|halt\w*|rests?|resting|still|"
                        r"comes to rest)\b", re.I)


def _has(text: str, words) -> str:
    """Есть ли в тексте одно из слов — ПО ГРАНИЦАМ СЛОВ.

    Подстрокой искать нельзя: «withdrawn» содержит «hdr», «brainstorm» —
    «rain», и запрет начинает срабатывать на невиновных карточках."""
    low = text.lower()
    for w in words:
        if re.search(r"\b" + re.escape(w) + r"\b", low):
            return w
    return ""


def _settles(text: str) -> bool:
    return bool(_SETTLE_RE.search(text))


def _validate_v2() -> list[str]:                         # noqa: C901
    err: list[str] = []
    board_groups = {g["key"] for g in BOARD_GROUPS}
    motion_groups = {g["key"] for g in MOTION_GROUPS}
    light_groups = {g["key"] for g in LIGHT_GROUPS}

    # Ключи всех слоёв живут в разных словарях, но в одном URL-пространстве и в
    # одной голове. Совпадение ключа заготовки с ключом приёма — это две разные
    # карточки по одному адресу, и первый же «применить по ключу» применит не то.
    seen_keys: dict[str, str] = {k: "приём" for k in SHOT_KEYS}
    for label, keys in (("сценарий", SCRIPT_KEYS), ("заготовка", BOARD_KEYS),
                        ("движение", MOTION_KEYS), ("свет", LIGHT_KEYS)):
        for k in keys:
            if k in seen_keys:
                err.append(f"{label} {k}: ключ уже занят ({seen_keys[k]})")
            seen_keys[k] = label

    for pair in CONFLICT_PAIRS:
        for t in pair:
            if t not in TRAITS:
                err.append(f"CONFLICT_PAIRS: черта {t} вне словаря TRAITS")

    # ── заготовки ──
    for b in BOARDS:
        k = b["key"]
        if b["group"] not in board_groups:
            err.append(f"заготовка {k}: неизвестная группа {b['group']}")
        if b["tier"] not in ("free", "pro"):
            err.append(f"заготовка {k}: неизвестный тариф {b['tier']}")
        if b["shot"] not in SHOT_SIZES:
            err.append(f"заготовка {k}: крупность {b['shot']} вне словаря SCENES_SYSTEM")
        if not b["camera"].strip():
            err.append(f"заготовка {k}: пустое движение камеры")
        used: set[str] = set()
        for f in _BOARD_TEXTS:
            if not b.get(f, "").strip():
                err.append(f"заготовка {k}: пустой текст {f}")
            used |= set(_SLOT_RE.findall(b.get(f, "")))
        for f in ("first", "last"):
            bad = _has(b[f], _BAN_GRADE)
            if bad:
                err.append(f"заготовка {k}: в {f} слово «{bad}» — этим распоряжается стиль трека")
        for f in ("first", "last", "motion", "solo"):
            bad = _has(b[f], _BAN_REF)
            if bad:
                err.append(f"заготовка {k}: в {f} ссылка на соседний кадр «{bad}»")
        if "the same" not in b["last"].lower():
            err.append(f"заготовка {k}: в последнем кадре нет лока «The same …» — "
                       f"движок приедет другой комнатой и другим человеком")
        if not _settles(b["motion"]):
            err.append(f"заготовка {k}: в motion нет конечного состояния (settles/stops/holds)")
        if not _settles(b["solo"]):
            err.append(f"заготовка {k}: в solo нет конечного состояния")
        for slot in used:
            if slot not in SLOTS:
                err.append(f"заготовка {k}: слот {{{slot}}} вне словаря SLOTS")
            elif slot not in b["slots"]:
                err.append(f"заготовка {k}: слот {{{slot}}} в тексте, но не объявлен")
        for slot in b["slots"]:
            if slot not in used:
                err.append(f"заготовка {k}: слот {slot} объявлен, но в тексте не встречается")
        if _SLOT_RE.findall(b["note"]["ru"] + b["note"]["en"]):
            err.append(f"заготовка {k}: слот в shot_note — подставленный английский текст "
                       f"внутри русской подписи читается как брак")
        if b["bracket"] and not _valid_bracket(b["bracket"]):
            err.append(f"заготовка {k}: команда MiniMax {b['bracket']} вне словаря MINIMAX_MOVES")
        for eng in b["engines"]:
            if eng not in ENGINE_KEYS:
                err.append(f"заготовка {k}: неизвестный движок {eng}")
        if b["needs_last"] and "grok" in b["engines"]:
            err.append(f"заготовка {k}: needs_last, но в движках grok — он оживляет только первый кадр")
        for t in b["traits"]:
            if t not in TRAITS:
                err.append(f"заготовка {k}: черта {t} вне словаря TRAITS")
        for ref in b["fits_with"] + b["conflicts_with"]:
            if ref not in _MOTION_BY_KEY and ref not in _LIGHT_BY_KEY and ref not in _BOARD_BY_KEY:
                err.append(f"заготовка {k}: ссылка на несуществующую карточку {ref}")
        for f in ("label", "desc", "note"):
            if not (b[f].get("en") or "").strip() or not (b[f].get("ru") or "").strip():
                err.append(f"заготовка {k}: поле {f} не заполнено на двух языках")

    # ── движение ──
    for m in MOTIONS:
        k = m["key"]
        if m["group"] not in motion_groups:
            err.append(f"движение {k}: неизвестная группа {m['group']}")
        used = set()
        for f in _MOTION_TEXTS:
            if not m.get(f, "").strip():
                err.append(f"движение {k}: пустой текст {f}")
            used |= set(_SLOT_RE.findall(m.get(f, "")))
            bad = _has(m[f], _BAN_REF)
            if bad:
                err.append(f"движение {k}: в {f} ссылка на соседний кадр «{bad}»")
        if not _settles(m["text"]):
            err.append(f"движение {k}: в text нет конечного состояния — движок подвиснет на дрейфе")
        if not _settles(m["solo"]):
            err.append(f"движение {k}: в solo нет конечного состояния")
        for slot in used:
            if slot not in SLOTS:
                err.append(f"движение {k}: слот {{{slot}}} вне словаря SLOTS")
            elif slot not in m["slots"]:
                err.append(f"движение {k}: слот {{{slot}}} в тексте, но не объявлен")
        for slot in m["slots"]:
            if slot not in used:
                err.append(f"движение {k}: слот {slot} объявлен, но в тексте не встречается")
        if m["bracket"] and not _valid_bracket(m["bracket"]):
            err.append(f"движение {k}: команда MiniMax {m['bracket']} вне словаря MINIMAX_MOVES")
        for eng in m["engines"]:
            if eng not in ENGINE_KEYS:
                err.append(f"движение {k}: неизвестный движок {eng}")
        for t in m["traits"]:
            if t not in TRAITS:
                err.append(f"движение {k}: черта {t} вне словаря TRAITS")
        for ref in m["fits_with"] + m["conflicts_with"]:
            if ref not in _BOARD_BY_KEY and ref not in _MOTION_BY_KEY:
                err.append(f"движение {k}: ссылка на несуществующую карточку {ref}")
        for f in ("label", "desc", "physics"):
            if not (m[f].get("en") or "").strip() or not (m[f].get("ru") or "").strip():
                err.append(f"движение {k}: поле {f} не заполнено на двух языках")

    # ── свет ──
    for l in LIGHTS:
        k = l["key"]
        if l["group"] not in light_groups:
            err.append(f"свет {k}: неизвестная группа {l['group']}")
        if l["level"] not in ("scene", "style"):
            err.append(f"свет {k}: неизвестный уровень {l['level']}")
        if not l["add"].strip():
            err.append(f"свет {k}: пустая дописка")
        if l["level"] == "scene":
            bad = _has(l["add"], _BAN_GRADE)
            if bad:
                err.append(f"свет {k}: уровень scene, но в тексте «{bad}» — это епархия стиля, "
                           f"либо убрать слово, либо перевести карточку в level=style")
        else:
            if DEFER not in l["add"]:
                err.append(f"свет {k}: уровень style без фразы-уступки «{DEFER}» — "
                           f"такая карточка начнёт спорить со стилем трека")
        used = set(_SLOT_RE.findall(l["add"]))
        for slot in used:
            if slot not in SLOTS:
                err.append(f"свет {k}: слот {{{slot}}} вне словаря SLOTS")
            elif slot not in l["slots"]:
                err.append(f"свет {k}: слот {{{slot}}} в тексте, но не объявлен")
        for slot in l["slots"]:
            if slot not in used:
                err.append(f"свет {k}: слот {slot} объявлен, но в тексте не встречается")
        for t in l["traits"]:
            if t not in TRAITS:
                err.append(f"свет {k}: черта {t} вне словаря TRAITS")
        for ref in l["fits_with"] + l["conflicts_with"]:
            if ref not in _BOARD_BY_KEY and ref not in _LIGHT_BY_KEY:
                err.append(f"свет {k}: ссылка на несуществующую карточку {ref}")
        for f in ("label", "desc", "note"):
            if not (l[f].get("en") or "").strip() or not (l[f].get("ru") or "").strip():
                err.append(f"свет {k}: поле {f} не заполнено на двух языках")

    # ── сценарии ──
    for s in SCRIPTS:
        k = s["key"]
        if s["tier"] not in ("free", "pro"):
            err.append(f"сценарий {k}: неизвестный тариф {s['tier']}")
        if s["cut"] not in ("slow", "mid", "fast"):
            err.append(f"сценарий {k}: неизвестный темп монтажа {s['cut']}")
        share = round(sum(a["share"] for a in s["acts"]), 3)
        if share != 1.0:
            err.append(f"сценарий {k}: сумма долей актов {share}, а должна быть 1.0")
        for a in s["acts"]:
            if a["shot"] not in SHOT_SIZES:
                err.append(f"сценарий {k}: акт {a['key']} — крупность {a['shot']} вне словаря")
            for ref in a["boards"]:
                if ref not in _BOARD_BY_KEY:
                    err.append(f"сценарий {k}: акт {a['key']} ссылается на несуществующую заготовку {ref}")
            for f in ("en", "ru"):
                if not (a.get(f) or "").strip():
                    err.append(f"сценарий {k}: акт {a['key']} не описан на языке {f}")
        for ref in (s["open_board"], s["close_board"]):
            if ref not in _BOARD_BY_KEY:
                err.append(f"сценарий {k}: несуществующая заготовка {ref}")
        sc = s["scenes"]
        if not (sc["min"] <= sc["typ"] <= sc["max"]):
            err.append(f"сценарий {k}: число сцен min/typ/max не по возрастанию")
        for slot in s["slots_hint"]:
            if slot not in SLOTS:
                err.append(f"сценарий {k}: слот {slot} вне словаря SLOTS")
        # story уезжает в Project.story, а claude.py требует сюжет по-русски.
        if not any("а" <= ch.lower() <= "я" for ch in s["story"]):
            err.append(f"сценарий {k}: story не по-русски — claude.py ждёт русский сюжет")
        if len(s["story"].split()) < 60:
            err.append(f"сценарий {k}: story короче 60 слов — это не сюжет, а логлайн")
        if not s["dnote"].strip():
            err.append(f"сценарий {k}: пустая режиссёрская заметка")
        for f in ("label", "music", "logline", "hero", "motif", "opens", "closes"):
            if not (s[f].get("en") or "").strip() or not (s[f].get("ru") or "").strip():
                err.append(f"сценарий {k}: поле {f} не заполнено на двух языках")

    # ── примеры ──
    ex_groups = set()
    for e in EXAMPLES:
        if e["board"] not in _BOARD_BY_KEY:
            err.append(f"пример {e['key']}: несуществующая заготовка {e['board']}")
            continue
        if e["motion"] and e["motion"] not in _MOTION_BY_KEY:
            err.append(f"пример {e['key']}: несуществующее движение {e['motion']}")
        for ref in e["lights"]:
            if ref not in _LIGHT_BY_KEY:
                err.append(f"пример {e['key']}: несуществующий свет {ref}")
        if e["script"] and e["script"] not in _SCRIPT_BY_KEY:
            err.append(f"пример {e['key']}: несуществующий сценарий {e['script']}")
        # Пример, который сам себе противоречит, учит плохому.
        bad = check_mix(e["board"], e["motion"], e["lights"])
        if bad:
            err.append(f"пример {e['key']}: конфликтует сам с собой — {bad[0]['ru']}")
        if not render_example(e["key"]):
            err.append(f"пример {e['key']}: не собирается")
        ex_groups.add(e["group"])
    for g in board_groups:
        if g not in ex_groups:
            err.append(f"группа заготовок {g} осталась без примера")

    # ── связь со стилями и тегами каталога ──
    try:
        import prompts_catalog as _pc
    except Exception:                                    # noqa: BLE001
        err.append("prompts_catalog не импортируется — проверка стилей новых слоёв не выполнена")
    else:
        known = set(_pc.STYLE_KEYS)
        blob = repr(BOARDS) + repr(SCRIPTS) + repr(MOTIONS) + repr(LIGHTS)
        for sk in _pc.STYLE_KEYS:
            text = _pc.style_prompt(sk)
            if text and len(text) >= 60 and text[:60] in blob:
                err.append(f"УТЕЧКА: промпт стиля {sk} скопирован в базу промтов")
        for s in SCRIPTS:
            for ref in s["styles_fit"]:
                if ref not in known:
                    err.append(f"сценарий {s['key']}: несуществующий стиль {ref}")
            if s["preset"] and s["preset"] not in _pc.PRESET_KEYS:
                err.append(f"сценарий {s['key']}: несуществующий каркас {s['preset']}")
            for t in s["tags"]:
                if not any(t in axis for axis in _pc.TAGS.values()):
                    err.append(f"сценарий {s['key']}: тег {t} вне словаря TAGS")
            seed = _pc.preset_seed(s["preset"]) if s["preset"] else {}
            if seed.get("story") and seed["story"][:60] == s["story"][:60]:
                err.append(f"сценарий {s['key']}: story скопирован из закрытого каркаса {s['preset']}")
        for b in BOARDS:
            for ref in b["styles_fit"]:
                if ref not in known:
                    err.append(f"заготовка {b['key']}: несуществующий стиль {ref}")
            for t in b["tags"]:
                if not any(t in axis for axis in _pc.TAGS.values()):
                    err.append(f"заготовка {b['key']}: тег {t} вне словаря TAGS")

    return err


#: Команды камеры MiniMax H3. Это их словарь, а не наш: движок читает буквально
#: только эти пятнадцать и по описанию словами их не восстанавливает.
MINIMAX_MOVES = ("Truck left", "Truck right", "Pan left", "Pan right", "Push in",
                 "Pull out", "Pedestal up", "Pedestal down", "Tilt up", "Tilt down",
                 "Zoom in", "Zoom out", "Shake", "Tracking shot", "Static shot")


def _valid_bracket(value: str) -> bool:
    """Команда в скобках: одна или несколько через запятую, но не больше трёх —
    дальше MiniMax перестаёт их исполнять и начинает выбирать наугад."""
    v = value.strip()
    if not (v.startswith("[") and v.endswith("]")):
        return False
    parts = [p.strip() for p in v[1:-1].split(",")]
    return 0 < len(parts) <= 3 and all(p in MINIMAX_MOVES for p in parts)


def examples_markdown(lang: str = "ru") -> str:
    """Готовый markdown со всеми примерами — им наполняется раздел документации.

    Документация НЕ хранит примеры текстом: разошедшийся пример учит
    формулировке, которой в продукте уже нет. Перегенерация:
    `python3 backend/prompts_library.py --examples > /tmp/x.md`."""
    out: list[str] = []
    for ex in EXAMPLES:
        r = render_example(ex["key"], lang=lang)
        if not r:
            continue
        board = _BOARD_BY_KEY[r["board"]]
        parts = [f"### {r['label']}", ""]
        chosen = [f"заготовка **{r['board']}**"]
        if r["motion"]:
            chosen.append(f"движение **{r['motion']}**")
        if r["lights"]:
            chosen.append("свет **" + "**, **".join(r["lights"]) + "**")
        if r["script"]:
            chosen.insert(0, f"сценарий **{r['script']}**")
        parts.append("Выбрано: " + ", ".join(chosen) + ".")
        parts.append("")
        parts.append("Подставлено: " + ", ".join(
            f"`{{{k}}}` = {v}" for k, v in r["slots_used"].items()) + ".")
        parts.append("")
        parts.append("```")
        parts.append(f"shot_size   : {r['scene']['shot_size']}")
        parts.append(f"camera_move : {r['scene']['camera_move']}")
        parts.append(f"shot_note   : {r['scene']['shot_note']}")
        for field, title in (("image_prompt", "image_prompt (первый кадр)"),
                             ("image_prompt_last", "image_prompt_last (последний кадр)"),
                             ("motion_prompt", "motion_prompt")):
            parts.append("")
            parts.append(f"{title}:")
            parts.append(_wrap(r["scene"][field]))
        parts.append("")
        parts.append("negative (уходит отдельным каналом там, где он есть):")
        parts.append(_wrap(r["negative"]))
        parts.append("```")
        parts.append("")
        parts.append(f"Почему так: {r['why']}")
        if board["needs_last"]:
            parts.append("")
            parts.append(f"На Grok эта сцена идёт вариантом `solo`: "
                         f"`{render_example(ex['key'], lang=lang, engine='grok')['scene']['motion_prompt']}`")
        parts.append("")
        out.append("\n".join(parts))
    return "\n".join(out)


def _wrap(text: str, width: int = 76) -> str:
    words, line, lines = text.split(), "", []
    for w in words:
        if len(line) + len(w) + 1 > width:
            lines.append(line)
            line = w
        else:
            line = f"{line} {w}".strip()
    if line:
        lines.append(line)
    return "\n".join("  " + x for x in lines)


if __name__ == "__main__":
    import sys
    if "--examples" in sys.argv:
        print(examples_markdown("ru"))
        raise SystemExit(0)
    problems = validate()
    if problems:
        print("\n".join(problems))
    else:
        free = sum(1 for s in SHOTS if s["tier"] == "free")
        print(f"библиотека цела: {len(SHOTS)} приёмов "
              f"({free} открытых, {len(SHOTS) - free} по тарифу) "
              f"в {len(CATEGORIES)} категориях, {len(PACKS)} наборов, "
              f"{len(SLOTS)} слотов")
        print(f"база промтов цела: {len(SCRIPTS)} сценариев, "
              f"{len(BOARDS)} заготовок в {len(BOARD_GROUPS)} группах, "
              f"{len(MOTIONS)} движений, {len(LIGHTS)} модификаторов света, "
              f"{len(EXAMPLES)} примеров, {len(TRAITS)} черт")
