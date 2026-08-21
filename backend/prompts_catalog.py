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


# ─────────────────────────────────────────────────────────────────────────────
# СТИЛИ — ПУБЛИЧНАЯ ЧАСТЬ КАРТОЧКИ. 15 штук.
#
# `label` и `desc` взяты дословно из frontend/i18n.js (styles.*) — переводы уже
# вычитаны, плодить второй источник правды незачем.
# `gain` — новое: не «как выглядит», а «что человек получит». Именно эта строка
# продаёт стиль, и именно её сейчас нигде нет.
#
# ДВЕ НЕЗАВИСИМЫЕ ОСИ, их постоянно путают:
#   prompt_class — ПОКАЗЫВАЕМ ли мы устройство промпта («closed» — никогда,
#                  «school» — разбор по блокам на PRO+). Про наш ров.
#   tier         — КОМУ доступно снимать этим стилем. Про деньги.
# Поэтому dreamclad = closed + free: текст не отдаём никому, а снимать им может
# и бесплатный — это лучший хук, который у нас есть.
#
# mix_role: "base"     — задаёт мир целиком, ставится первым чипом;
#           "modifier" — обработка поверх чужого мира (грейд, фактура);
#           "both"     — работает и так, и так.
# Все семь closed-пресетов — строго "base": подмешать фирменный стиль третьим
# чипом значит размыть ровно то, что делает его виральным.
# ─────────────────────────────────────────────────────────────────────────────
STYLES = [
    {
        "key": "ghibli",
        "group": "animation",
        "tier": "free",
        "prompt_class": "school",
        "label": {"en": "Hayao Miyazaki (warm anime)", "ru": "Хаяо Миядзаки (ламповое аниме)"},
        "desc": {
            "en": "Hand-drawn anime with watercolour backgrounds and cosy light — a frame out of Ghibli.",
            "ru": "Тёплое рисованное аниме с акварельными фонами и уютным светом — как кадр из Гибли.",
        },
        "gain": {
            "en": "Your track turns into a warm memory: watercolour skies, dust in the sunbeams, faces you want to protect.",
            "ru": "Трек превращается в тёплое воспоминание: акварельное небо, пылинки в луче, лица, которые хочется беречь.",
        },
        "tags": ["cozy", "nostalgic", "warm", "painterly", "nature", "daylight"],
        "music": {
            "en": "Lyrical rap about home and childhood, indie folk, lo-fi, acoustic.",
            "ru": "Лиричный рэп про дом и детство, инди-фолк, лоу-фай, акустика.",
            "genres": ["lo-fi", "indie folk", "acoustic", "melodic rap"],
        },
        "tempo": {"bpm": [60, 95], "cut": "slow"},
        "mix_role": "base",
        "mix_with": ["shinkai", "flat2d", "embroidery"],
        "avoid_mix": ["punkrf", "katsumi", "noir"],
        "engines": ["kling-3.0", "seedance-2-mini"],
        "research": "",
    },
    {
        "key": "pixar",
        "group": "animation",
        "tier": "free",
        "prompt_class": "school",
        "label": {"en": "3D cartoon (Pixar-style)", "ru": "3D мультяшный (Pixar-style)"},
        "desc": {
            "en": "Glossy 3D animation: expressive characters, rich cinematic light.",
            "ru": "Глянцевый 3D-мультфильм: выразительные герои, сочный кинематографичный свет.",
        },
        "gain": {
            "en": "Characters that read as heroes on the very first frame: big eyes, soft skin, light that flatters everyone.",
            "ru": "Герои читаются как герои с первого кадра: большие глаза, мягкая кожа, свет, который всех красит.",
        },
        "tags": ["playful", "saturated", "clean", "warm", "daylight"],
        "music": {
            "en": "Pop, upbeat and comedic rap, kids' tracks, bright electronica.",
            "ru": "Поп, задорный и комедийный рэп, детские треки, яркая электроника.",
            "genres": ["pop", "comedy rap", "kids", "electro-pop"],
        },
        "tempo": {"bpm": [90, 130], "cut": "mid"},
        "mix_role": "base",
        "mix_with": ["clay", "flat2d"],
        "avoid_mix": ["cinema", "dreamclad", "punkrf"],
        "engines": ["seedance-2-5", "kling-3.0-pro"],
        "research": "",
    },
    {
        "key": "shinkai",
        "group": "animation",
        "tier": "free",
        "prompt_class": "school",
        "label": {"en": "Cinematic anime (Shinkai)", "ru": "Кинематографичное аниме (Синкай)"},
        "desc": {
            "en": "Modern anime with impossibly beautiful skies, lens flares and emotional gradients.",
            "ru": "Современное аниме с гиперкрасивыми небесами, бликами и эмоциональными градиентами.",
        },
        "gain": {
            "en": "Skies too beautiful to be real and one small human underneath — the packaging that makes a chorus sound enormous.",
            "ru": "Невозможно красивое небо и маленький человек под ним — упаковка, от которой припев звучит громаднее.",
        },
        "tags": ["melancholy", "epic", "saturated", "cold", "painterly", "night"],
        "music": {
            "en": "Emo rap, dramatic pop, hyperpop ballads, atmospheric electronica.",
            "ru": "Эмо-рэп, драматичный поп, гиперпоп-баллады, атмосферная электроника.",
            "genres": ["emo rap", "dramatic pop", "hyperpop", "ambient"],
        },
        "tempo": {"bpm": [70, 140], "cut": "mid"},
        "mix_role": "base",
        "mix_with": ["ghibli", "cinema", "noir"],
        "avoid_mix": ["clay", "embroidery"],
        "engines": ["seedance-2-5", "seedance-2-0"],
        "research": "",
    },
    {
        "key": "cinema",
        "group": "cinema",
        "tier": "free",
        "prompt_class": "school",
        "label": {"en": "Realism (film)", "ru": "Реализм (кино)"},
        "desc": {
            "en": "Photoreal cinema shot on film: honest light, skin texture, a little grain.",
            "ru": "Фотореалистичное кино на плёнке: честный свет, фактура кожи, лёгкое зерно.",
        },
        "gain": {
            "en": "The safest bet in the catalogue: real light, real skin, nothing that screams “made by AI”.",
            "ru": "Самый безопасный выбор в каталоге: живой свет, настоящая кожа, ничего, что кричит «это нейросеть».",
        },
        "tags": ["nostalgic", "muted", "grain", "clean", "street", "daylight"],
        "music": {
            "en": "Works with anything: rap, rock, soul, ballads. The default when the track matters more than the look.",
            "ru": "Работает подо всё: рэп, рок, соул, баллады. Выбор по умолчанию, когда важнее трек, а не упаковка.",
            "genres": ["rap", "rock", "soul", "ballad"],
        },
        "tempo": {"bpm": [55, 200], "cut": "mid"},
        "mix_role": "both",
        "mix_with": ["noir", "dreamclad", "fanuel", "shinkai"],
        "avoid_mix": ["pixar", "flat2d"],
        "engines": ["seedance-2-5", "kling-3.0-pro"],
        "research": "",
    },
    {
        "key": "flat2d",
        "group": "animation",
        "tier": "free",
        "prompt_class": "school",
        "label": {"en": "Flat 2D animation", "ru": "2D плоская анимация"},
        "desc": {
            "en": "Bright flat vector animation: simple shapes, bold outlines, poster-like compositions.",
            "ru": "Яркая плоская векторная анимация: простые формы, смелые контуры, постерные композиции.",
        },
        "gain": {
            "en": "A poster on every beat: three colours, thick outlines, still readable in a phone-sized thumbnail.",
            "ru": "Плакат на каждую долю: три цвета, толстый контур, читается даже в превью размером с ноготь.",
        },
        "tags": ["playful", "saturated", "clean", "daylight"],
        "music": {
            "en": "Funk, disco, electro-pop, anything with a bouncy beat.",
            "ru": "Фанк, диско, электро-поп, всё с прыгучим битом.",
            "genres": ["funk", "disco", "electro-pop"],
        },
        "tempo": {"bpm": [100, 128], "cut": "fast"},
        "mix_role": "both",
        "mix_with": ["pixar", "embroidery", "ghibli"],
        "avoid_mix": ["cinema", "dreamclad", "katsumi"],
        "engines": ["kling-3.0", "seedance-2-mini"],
        "research": "",
    },
    {
        "key": "noir",
        "group": "cinema",
        "tier": "free",
        "prompt_class": "school",
        "label": {"en": "Noir comic", "ru": "Нуарный комикс"},
        "desc": {
            "en": "Black and white noir with one accent colour: deep shadows, rain, neon.",
            "ru": "Чёрно-белый нуар с одним цветовым акцентом: глубокие тени, дождь, неон.",
        },
        "gain": {
            "en": "Half the frame is black and one colour cuts through it — tension without needing a plot.",
            "ru": "Половина кадра — чернота, и один цвет режет её насквозь: напряжение без всякого сюжета.",
        },
        "tags": ["menacing", "monochrome", "neon", "night", "street"],
        "music": {
            "en": "Dark boom bap, drill, jazz rap, trip-hop.",
            "ru": "Тёмный бумбап, дрилл, джаз-рэп, трип-хоп.",
            "genres": ["boom bap", "drill", "jazz rap", "trip-hop"],
        },
        "tempo": {"bpm": [60, 95], "cut": "slow"},
        "mix_role": "modifier",
        "mix_with": ["cinema", "dreamclad", "shinkai", "longheads"],
        "avoid_mix": ["pixar", "ghibli"],
        "engines": ["kling-3.0-pro", "seedance-2-5-480"],
        "research": "",
    },
    {
        "key": "longheads",
        "group": "analog",
        "tier": "pro",
        "prompt_class": "closed",
        "label": {"en": "Long heads (90s analog surrealism)", "ru": "Длинные бошки (аналоговый сюр 90-х)"},
        "desc": {
            "en": "90s analog film: surreal long-headed characters living an ordinary street life.",
            "ru": "Аналоговая плёнка 90-х: сюрреалистичные длинноголовые персонажи в обычной уличной жизни.",
        },
        "gain": {
            "en": "Ordinary street life where nobody notices the heads are wrong — the joke lands precisely because nothing reacts.",
            "ru": "Обычная уличная жизнь, где никто не замечает, что головы неправильные, — шутка работает именно потому, что никто не реагирует.",
        },
        "tags": ["deadpan", "muted", "grain", "flash", "street", "viral"],
        "music": {
            "en": "Alt rap, plugg, lo-fi, anything with an odd off-kilter beat.",
            "ru": "Альт-рэп, плагг, лоу-фай, любой странный сбитый бит.",
            "genres": ["alt rap", "plugg", "lo-fi"],
        },
        "tempo": {"bpm": [80, 110], "cut": "mid"},
        "mix_role": "base",
        "mix_with": ["katsumi", "punkrf", "dreamclad"],
        "avoid_mix": ["pixar", "ghibli", "flat2d"],
        "engines": ["seedance-2-mini", "seedance-2-5-480", "kling-3.0"],
        "research": "docs/styles-research-summary.md — назван среди самых виральных аналоговых упаковок",
    },
    {
        "key": "embroidery",
        "group": "craft",
        "tier": "free",
        "prompt_class": "school",
        "label": {"en": "Cardboard (thread embroidery)", "ru": "Картон (вышивка нитью)"},
        "desc": {
            "en": "The whole frame stitched in thread on cream felt and kraft board — handmade and warm.",
            "ru": "Весь кадр вышит нитью по кремовому фетру и крафту — тёплая ручная работа.",
        },
        "gain": {
            "en": "Every frame looks hand-sewn — the rarest texture in the feed, impossible to mistake for stock AI.",
            "ru": "Каждый кадр как будто вышит руками — самая редкая фактура в ленте, её не спутать со стоковой нейронкой.",
        },
        "tags": ["cozy", "handmade", "saturated", "warm"],
        "music": {
            "en": "Folk, ethno, warm lo-fi, indie acoustic.",
            "ru": "Фолк, этно, тёплый лоу-фай, инди-акустика.",
            "genres": ["folk", "ethno", "lo-fi"],
        },
        "tempo": {"bpm": [70, 100], "cut": "slow"},
        "mix_role": "modifier",
        "mix_with": ["flat2d", "clay", "ghibli"],
        "avoid_mix": ["cinema", "punkrf", "munir"],
        "engines": ["kling-3.0", "seedance-2-mini"],
        "research": "",
    },
    {
        "key": "spike",
        "group": "analog",
        "tier": "pro",
        "prompt_class": "closed",
        "label": {"en": "SPIKE (Russian cine-surrealism, cameos)", "ru": "СПАЙК (русский кино-сюр, камео)"},
        "desc": {
            "en": "Night-time Russian cine-surrealism: panel blocks, old Ladas, smoke and cartoon cameos played dead straight.",
            "ru": "Ночной русский кино-сюр: хрущёвки, Лады, дым и мультяшные камео на серьёзных щах.",
        },
        "gain": {
            "en": "A post-Soviet courtyard shot like an epic music video, with a cartoon guest nobody in the frame reacts to.",
            "ru": "Постсоветский двор, снятый как эпический клип, и мультяшный гость, на которого в кадре никто не реагирует.",
        },
        "tags": ["deadpan", "nostalgic", "grain", "night", "street", "money", "viral"],
        "music": {
            "en": "Russian rap, trap, yard-rap with a story.",
            "ru": "Русский рэп, трэп, дворовый рэп с историей.",
            "genres": ["russian rap", "trap"],
        },
        "tempo": {"bpm": [70, 150], "cut": "mid"},
        "mix_role": "base",
        "mix_with": ["punkrf", "dreamclad", "cinema"],
        "avoid_mix": ["ghibli", "pixar", "embroidery"],
        "engines": ["seedance-2-mini", "kling-3.0", "seedance-2-5-480"],
        "research": "spikeandred: 186 роликов, медиана 183 тыс., топ-1 — 14,1 млн (77× медианы)",
    },
    {
        "key": "munir",
        "group": "analog",
        "tier": "pro",
        "prompt_class": "closed",
        "label": {"en": "MUNIR (Gulf, flash, fisheye)", "ru": "МУНИР (залив, вспышка, фиш-ай)"},
        "desc": {
            "en": "Gulf street photography with flash and a fisheye: rings pushed into the lens, a G63, Dobermans.",
            "ru": "Уличная съёмка Залива со вспышкой и фиш-аем: кольца в объектив, G63, доберманы.",
        },
        "gain": {
            "en": "Flash in the face, fisheye, a ring shoved into the lens — the punch lands in half a second, before anyone reads a caption.",
            "ru": "Вспышка в лицо, фиш-ай, перстень в объектив — панч доходит за полсекунды, раньше любой подписи.",
        },
        "tags": ["deadpan", "saturated", "flash", "luxury", "night", "street", "viral"],
        "music": {
            "en": "Trap, arabic drill, club beats.",
            "ru": "Трэп, арабик-дрилл, клубный бит.",
            "genres": ["trap", "drill", "club"],
        },
        "tempo": {"bpm": [95, 150], "cut": "fast"},
        "mix_role": "base",
        "mix_with": ["katsumi", "dreamclad"],
        "avoid_mix": ["ghibli", "shinkai", "embroidery"],
        "engines": ["seedance-2-mini", "seedance-2-5-480", "kling-3.0"],
        "research": "muniraalkharaz: 224 ролика, разрыв топ/медиана 595×, свежая формула подняла медиану в 19 раз",
    },
    {
        "key": "fanuel",
        "group": "cinema",
        "tier": "pro",
        "prompt_class": "closed",
        "label": {"en": "FANUEL (cinematic surrealism, fire)", "ru": "ФАНУЕЛ (кино-сюрреализм, огонь)"},
        "desc": {
            "en": "Surreal fashion film: a lone figure in a suit inside impossible landscapes, fire everywhere.",
            "ru": "Сюрреалистичный fashion-фильм: одинокая фигура в костюме среди невозможных пейзажей и огня.",
        },
        "gain": {
            "en": "One figure in a coloured suit inside an impossible world — every single frame works as a standalone poster.",
            "ru": "Одна фигура в цветном костюме посреди невозможного мира — каждый кадр работает как самостоятельный постер.",
        },
        "tags": ["epic", "melancholy", "saturated", "warm", "clean", "viral"],
        "music": {
            "en": "Atmospheric rap, soul, ambient hip-hop, big cinematic beats.",
            "ru": "Атмосферный рэп, соул, эмбиент-хоп, крупные кинематографичные биты.",
            "genres": ["atmospheric rap", "soul", "ambient hip-hop"],
        },
        "tempo": {"bpm": [60, 100], "cut": "slow"},
        "mix_role": "base",
        "mix_with": ["cinema", "shinkai"],
        "avoid_mix": ["punkrf", "katsumi", "clay"],
        "engines": ["seedance-2-5", "seedance-2-0", "kling-3.0-pro"],
        "research": "fanuel_leul: 24 ролика, топ-3 = 52% всех просмотров, инфоповод в подписи удваивает охват",
    },
    {
        "key": "clay",
        "group": "craft",
        "tier": "free",
        "prompt_class": "school",
        "label": {"en": "Claymation (plasticine)", "ru": "Клеймация (пластилин)"},
        "desc": {
            "en": "Plasticine stop-motion: fingerprints in the clay, miniature sets, warm practical light.",
            "ru": "Пластилиновая стоп-моушен анимация: отпечатки пальцев, миниатюрные декорации, тёплый свет.",
        },
        "gain": {
            "en": "Fingerprints in the plasticine and honest stop-motion jitter — warmth that no clean render can buy.",
            "ru": "Отпечатки пальцев в пластилине и честная дрожь стоп-моушена — тепло, которого не купить чистым рендером.",
        },
        "tags": ["playful", "handmade", "warm", "cozy"],
        "music": {
            "en": "Comedy rap, indie, funk, kids' tracks.",
            "ru": "Комедийный рэп, инди, фанк, детские треки.",
            "genres": ["comedy rap", "indie", "funk"],
        },
        "tempo": {"bpm": [90, 120], "cut": "mid"},
        "mix_role": "base",
        "mix_with": ["pixar", "embroidery"],
        "avoid_mix": ["cinema", "munir", "punkrf"],
        "engines": ["kling-3.0", "seedance-2-mini"],
        "research": "",
    },
    {
        "key": "punkrf",
        "group": "surreal",
        "tier": "pro",
        "prompt_class": "closed",
        "label": {"en": "PUNKRF (found footage, Russian chaos)", "ru": "ПАНКРФ (найденное видео, дичь РФ)"},
        "desc": {
            "en": "Hyperreal “random video” of night-time Russia: dashcams and VHS, red neon, absurdity in the middle of traffic.",
            "ru": "Гиперреалистичное «случайное видео» ночной России: регистраторы и VHS, красный неон, абсурд среди пробок и панелек.",
        },
        "gain": {
            "en": "A clip that reads as “somebody actually filmed this”: dashcam, red neon, one impossible thing in the middle of traffic.",
            "ru": "Ролик, который читается как «это кто-то реально снял»: регистратор, красный неон и одна невозможная вещь посреди пробки.",
        },
        "tags": ["deadpan", "menacing", "vhs", "neon", "cold", "night", "street", "viral"],
        "music": {
            "en": "Phonk, hardcore rap, drum and bass, aggressive trap.",
            "ru": "Фонк, хардкор-рэп, драм-н-бэйс, агрессивный трэп.",
            "genres": ["phonk", "hardcore rap", "dnb", "trap"],
        },
        "tempo": {"bpm": [130, 175], "cut": "fast"},
        "mix_role": "base",
        "mix_with": ["spike", "katsumi", "longheads"],
        "avoid_mix": ["ghibli", "pixar", "embroidery", "fanuel"],
        "engines": ["seedance-2-5-480", "seedance-2-mini", "kling-3.0"],
        "research": "собран по общей формуле пяти аккаунтов: аналоговый грим + одна невозможность в бытовухе",
    },
    {
        "key": "dreamclad",
        "group": "analog",
        "tier": "free",
        "prompt_class": "closed",
        "label": {"en": "DREAMCLAD (90s hood cinema)", "ru": "ДРИМКЛАД (hood-кино 90-х)"},
        "desc": {
            "en": "90s hood cinema on film: grain, white tanks and bandanas, money, doves and crosses, icon-like frontal framing.",
            "ru": "Плёночное hood-кино 90-х: зерно, белые майки и банданы, деньги, голуби и кресты, иконописные фронтальные композиции.",
        },
        "gain": {
            "en": "90s hood cinema: grain, white tanks, doves and crosses — frontal frames that come out looking like icons.",
            "ru": "Hood-кино 90-х: зерно, белые майки, голуби и кресты — фронтальные кадры, которые выходят похожими на иконы.",
        },
        "tags": ["nostalgic", "menacing", "grain", "muted", "street", "money", "sacred", "crowd", "viral"],
        "music": {
            "en": "Boom bap, east coast, classic hip-hop, drill.",
            "ru": "Бумбап, ист-кост, классический хип-хоп, дрилл.",
            "genres": ["boom bap", "east coast", "hip-hop", "drill"],
        },
        "tempo": {"bpm": [85, 140], "cut": "mid"},
        "mix_role": "base",
        "mix_with": ["noir", "cinema", "spike"],
        "avoid_mix": ["pixar", "flat2d", "ghibli"],
        "engines": ["seedance-2-mini", "kling-3.0", "seedance-2-5-480"],
        "research": "dreamerclub.now: 88 роликов, медиана 41 тыс., топ-1 — 2,2 млн (толпа в масках)",
    },
    {
        "key": "katsumi",
        "group": "surreal",
        "tier": "pro",
        "prompt_class": "closed",
        "label": {"en": "KATSUMI (found footage, surreal)", "ru": "КАТСУМИ (найденная плёнка, сюр)"},
        "desc": {
            "en": "Hyperreal found footage: rats, monks and aliens doing ordinary human things dead straight, shot on a 90s camcorder with flash.",
            "ru": "Гиперреалистичная «найденная плёнка»: крысы, монахи и алиены на полном серьёзе живут бытовухой под камкордер со вспышкой из 90-х.",
        },
        "gain": {
            "en": "A rat, a monk or an alien running your errands with a completely straight face, shot on a 90s camcorder.",
            "ru": "Крыса, монах или алиен с каменным лицом занимаются вашей бытовухой под камкордер 90-х.",
        },
        "tags": ["deadpan", "menacing", "vhs", "flash", "muted", "night", "viral"],
        "music": {
            "en": "Meme rap, phonk, plugg, weird trap.",
            "ru": "Мем-рэп, фонк, плагг, странный трэп.",
            "genres": ["meme rap", "phonk", "plugg", "trap"],
        },
        "tempo": {"bpm": [100, 150], "cut": "mid"},
        "mix_role": "base",
        "mix_with": ["longheads", "punkrf", "munir"],
        "avoid_mix": ["ghibli", "pixar", "shinkai", "fanuel"],
        "engines": ["seedance-2-mini", "seedance-2-5-480", "kling-3.0"],
        "research": "ota.visual: 80 роликов, медиана 134 тыс., топ-1 — 13,7 млн (100× медианы), ER топа 6–10%",
    },
]


# ─────────────────────────────────────────────────────────────────────────────
# КАРКАСЫ КЛИПА — ПУБЛИЧНАЯ ЧАСТЬ. 15 штук.
#
# Зачем они вообще. Стиль отвечает «как выглядит», и это всё, что у нас есть
# сегодня. Человек выбирает ДРИМКЛАД, жмёт «собрать» и получает красивую
# бессвязицу, потому что на вопрос «что снимаем» никто не ответил. У конкурента
# пресет даёт готовый результат именно потому, что в нём зашит сюжет.
#
# Как ложится на конвейер (backend/claude.py):
#   kind="story" → seed каркаса пишется в Project.story, режиссёрская заметка
#                  в Track.director_note, Track.no_story=False. claude.py ведёт
#                  сквозную драматургию.
#   kind="punch" → Track.no_story=True. claude.py уходит в готовую ветку
#                  «РЕЖИМ БЕЗ СЮЖЕТА»: каждый кадр — независимый панч-образ.
#                  Это не костыль, это ровно тот формат, которым живут виральные
#                  аккаунты: «один панч на кадр, никакого нарратива».
#
# `share` — ДОЛЯ ХРОНОМЕТРАЖА, не число кадров. SCENES_SYSTEM требует, чтобы
# сумма duration_sec равнялась длине трека, а число кадров плавает. Доли
# масштабируются на любой трек: и на 90 секунд, и на 4 минуты.
# Сумма share внутри каркаса всегда 1.0 — это проверяет validate().
#
# `shot` — из словаря крупностей SCENES_SYSTEM: establishing / wide / medium /
# close-up / extreme close-up. Значения подставляются в раскадровку как есть.
# ─────────────────────────────────────────────────────────────────────────────
CLIP_PRESETS = [
    {
        "key": "night_walk",
        "kind": "story",
        "label": {"en": "Night Walk", "ru": "Ночной проход"},
        "logline": {
            "en": "He leaves the building and walks until the city ends. Nothing chases him except the track.",
            "ru": "Он выходит из подъезда и идёт, пока не кончится город. За ним не гонится никто, кроме трека.",
        },
        "tempo": {"bpm": [80, 105], "cut": "mid"},
        "scenes": {"min": 12, "typ": 30, "max": 45},
        "styles_fit": ["dreamclad", "noir", "cinema", "punkrf", "shinkai"],
        "engines": ["seedance-2-mini", "kling-3.0"],
        "needs_lyrics": False,
        "research": "",
        "beats": [
            {"key": "exit",   "share": 0.12, "shot": "establishing",
             "en": "The door of the building shuts behind him. The street is empty.",
             "ru": "Дверь подъезда закрывается за спиной. Улица пустая."},
            {"key": "street", "share": 0.18, "shot": "wide",
             "en": "He walks. The city is bigger than him and does not notice.",
             "ru": "Он идёт. Город больше него и не замечает его."},
            {"key": "look",   "share": 0.15, "shot": "close-up",
             "en": "Someone looks back at him. One frame of eye contact that changes the mood.",
             "ru": "Кто-то встречный смотрит в ответ. Один кадр зрительного контакта — и настроение меняется."},
            {"key": "run",    "share": 0.25, "shot": "medium",
             "en": "The pace breaks into movement: crossings, headlights, breath.",
             "ru": "Шаг срывается в движение: переходы, фары, дыхание."},
            {"key": "stop",   "share": 0.15, "shot": "extreme close-up",
             "en": "He stops. Hands, breath, the details the wide shots hid.",
             "ru": "Он останавливается. Руки, дыхание, детали, которых не было видно на общих планах."},
            {"key": "dawn",   "share": 0.15, "shot": "establishing",
             "en": "First light. The same street, a different colour.",
             "ru": "Первый свет. Та же улица другого цвета."},
        ],
    },
    {
        "key": "one_day",
        "kind": "story",
        "label": {"en": "One Ordinary Day", "ru": "Один день"},
        "logline": {
            "en": "From the alarm to the dark window. Routine filmed like an epic — the strongest frame is the most boring one.",
            "ru": "От будильника до тёмного окна. Бытовуха, снятая как эпос, — самый сильный кадр тут самый скучный.",
        },
        "tempo": {"bpm": [70, 95], "cut": "slow"},
        "scenes": {"min": 14, "typ": 30, "max": 40},
        "styles_fit": ["cinema", "ghibli", "katsumi", "longheads", "clay"],
        "engines": ["kling-3.0", "seedance-2-mini"],
        "needs_lyrics": False,
        "research": "КАТСУМИ: «один герой + одна бытовая ситуация» — базовая формула топовых роликов",
        "beats": [
            {"key": "alarm",   "share": 0.12, "shot": "extreme close-up",
             "en": "The alarm. A hand, a screen, a ceiling.",
             "ru": "Будильник. Рука, экран, потолок."},
            {"key": "commute", "share": 0.18, "shot": "medium",
             "en": "The way there. Transport, crowd, nobody talking.",
             "ru": "Дорога. Транспорт, толпа, никто не разговаривает."},
            {"key": "routine", "share": 0.20, "shot": "wide",
             "en": "The same motion repeated until it stops meaning anything.",
             "ru": "Одно и то же движение, повторённое до потери смысла."},
            {"key": "crack",   "share": 0.20, "shot": "close-up",
             "en": "Something small breaks the pattern. Not a catastrophe — a glance, a spill, a pause.",
             "ru": "Что-то мелкое ломает шаблон. Не катастрофа — взгляд, пролитое, пауза."},
            {"key": "evening", "share": 0.15, "shot": "medium",
             "en": "The way back, carrying whatever that was.",
             "ru": "Дорога обратно, уже с этим внутри."},
            {"key": "window",  "share": 0.15, "shot": "establishing",
             "en": "The window from outside. One light in a hundred.",
             "ru": "Окно снаружи. Один свет из сотни."},
        ],
    },
    {
        "key": "come_up",
        "kind": "story",
        "label": {"en": "The Come Up", "ru": "Подъём"},
        "logline": {
            "en": "Empty fridge to gold chain. The oldest structure in rap and it still works — because the last frame looks back.",
            "ru": "От пустого холодильника до золотой цепи. Самая старая структура в рэпе, и она до сих пор работает — потому что последний кадр смотрит назад.",
        },
        "tempo": {"bpm": [90, 145], "cut": "mid"},
        "scenes": {"min": 14, "typ": 32, "max": 45},
        "styles_fit": ["dreamclad", "spike", "munir", "cinema", "noir"],
        "engines": ["seedance-2-mini", "kling-3.0", "seedance-2-5-480"],
        "needs_lyrics": False,
        "research": "«деньги и статус как мотив — в топах у четырёх из пяти аккаунтов»",
        "beats": [
            {"key": "empty",     "share": 0.15, "shot": "wide",
             "en": "The starting point, shown without pity: a room, a fridge, a bus stop.",
             "ru": "Точка старта, показанная без жалости: комната, холодильник, остановка."},
            {"key": "grind",     "share": 0.20, "shot": "medium",
             "en": "Work nobody films: hands, boxes, night shifts.",
             "ru": "Работа, которую никто не снимает: руки, коробки, ночные смены."},
            {"key": "first",     "share": 0.15, "shot": "extreme close-up",
             "en": "The first money. Counted slowly, because it is still countable.",
             "ru": "Первые деньги. Считает медленно, потому что их ещё можно сосчитать."},
            {"key": "shine",     "share": 0.25, "shot": "wide",
             "en": "The reward, filmed as ceremony: the car, the chain, the room full of people.",
             "ru": "Награда, снятая как церемония: машина, цепь, комната, полная людей."},
            {"key": "look_back", "share": 0.15, "shot": "close-up",
             "en": "He looks back at the first location. Same face, different eyes.",
             "ru": "Он смотрит назад, на первую локацию. То же лицо, другие глаза."},
            {"key": "crown",     "share": 0.10, "shot": "establishing",
             "en": "The frame that would work as the cover.",
             "ru": "Кадр, который сработал бы обложкой."},
        ],
    },
    {
        "key": "from_the_crowd",
        "kind": "story",
        "label": {"en": "From the Crowd", "ru": "Концерт глазами зрителя"},
        "logline": {
            "en": "The whole show from inside the audience. The artist is never the main character — the crowd is.",
            "ru": "Весь концерт изнутри толпы. Артист тут не главный герой — главная толпа.",
        },
        "tempo": {"bpm": [120, 165], "cut": "fast"},
        "scenes": {"min": 18, "typ": 36, "max": 50},
        "styles_fit": ["punkrf", "dreamclad", "cinema", "noir", "munir"],
        "engines": ["seedance-2-5-480", "seedance-2-mini"],
        "needs_lyrics": False,
        "research": "ДРИМКЛАД: «монолитная масса (толпа в одинаковом) — сильнейший образ»",
        "beats": [
            {"key": "queue",  "share": 0.12, "shot": "wide",
             "en": "Outside. Breath in the cold, tickets, the doors still shut.",
             "ru": "Снаружи. Пар изо рта, билеты, двери ещё закрыты."},
            {"key": "dark",   "share": 0.10, "shot": "establishing",
             "en": "Inside, before anything starts. Darkness and noise.",
             "ru": "Внутри, до начала. Темнота и гул."},
            {"key": "first",  "share": 0.18, "shot": "medium",
             "en": "The first hit of the beat. Light finds faces.",
             "ru": "Первый удар бита. Свет находит лица."},
            {"key": "crowd",  "share": 0.25, "shot": "wide",
             "en": "The mass moving as one body. This is the money shot.",
             "ru": "Масса двигается как одно тело. Это и есть главный кадр."},
            {"key": "hands",  "share": 0.20, "shot": "close-up",
             "en": "Details from inside: hands, sweat, phones, someone crying.",
             "ru": "Детали изнутри: руки, пот, телефоны, кто-то плачет."},
            {"key": "after",  "share": 0.15, "shot": "establishing",
             "en": "The empty room afterwards. Cups on the floor.",
             "ru": "Пустой зал после. Стаканы на полу."},
        ],
    },
    {
        "key": "dream_wake",
        "kind": "story",
        "label": {"en": "Dream & Wake", "ru": "Сон и пробуждение"},
        "logline": {
            "en": "Two worlds and one object that crosses between them. Lets you be surreal without looking random.",
            "ru": "Два мира и один предмет, который переходит между ними. Позволяет уйти в сюр, не выглядя случайным.",
        },
        "tempo": {"bpm": [60, 90], "cut": "slow"},
        "scenes": {"min": 12, "typ": 28, "max": 40},
        "styles_fit": ["shinkai", "fanuel", "ghibli", "longheads", "cinema"],
        "engines": ["seedance-2-5", "kling-3.0-pro", "kling-3.0"],
        "needs_lyrics": False,
        "research": "",
        "beats": [
            {"key": "asleep",     "share": 0.15, "shot": "close-up",
             "en": "Falling asleep. The real world, last honest frame.",
             "ru": "Засыпание. Реальный мир, последний честный кадр."},
            {"key": "distortion", "share": 0.15, "shot": "medium",
             "en": "The room stops obeying the rules, slowly enough to be missed.",
             "ru": "Комната перестаёт слушаться правил — достаточно медленно, чтобы это можно было не заметить."},
            {"key": "dream",      "share": 0.25, "shot": "wide",
             "en": "The dream world in full. This is where the budget goes.",
             "ru": "Мир сна целиком. Сюда и уходит бюджет."},
            {"key": "peak",       "share": 0.20, "shot": "extreme close-up",
             "en": "The thing the dream was actually about.",
             "ru": "То, ради чего сон и был."},
            {"key": "fall",       "share": 0.10, "shot": "medium",
             "en": "The fall out of it.",
             "ru": "Падение обратно."},
            {"key": "wake",       "share": 0.15, "shot": "close-up",
             "en": "Awake. The object from the dream is on the table.",
             "ru": "Проснулся. Предмет из сна лежит на столе."},
        ],
    },
    {
        "key": "the_double",
        "kind": "story",
        "label": {"en": "The Double", "ru": "Двойник"},
        "logline": {
            "en": "He meets the version of himself he could have been. One character, two costumes, zero extra characters to keep consistent.",
            "ru": "Он встречает ту версию себя, которой мог бы стать. Один персонаж, два костюма и ни одного лишнего героя, за которым нужно следить.",
        },
        "tempo": {"bpm": [85, 115], "cut": "mid"},
        "scenes": {"min": 12, "typ": 28, "max": 40},
        "styles_fit": ["cinema", "noir", "dreamclad", "shinkai", "punkrf"],
        "engines": ["seedance-2-5", "kling-3.0-pro"],
        "needs_lyrics": False,
        "research": "",
        "beats": [
            {"key": "reflection", "share": 0.15, "shot": "close-up",
             "en": "A reflection that lags by half a second.",
             "ru": "Отражение, которое отстаёт на полсекунды."},
            {"key": "in_crowd",   "share": 0.20, "shot": "wide",
             "en": "He spots himself in a crowd, wearing what he doesn't own.",
             "ru": "Он замечает себя в толпе — в одежде, которой у него нет."},
            {"key": "pursuit",    "share": 0.25, "shot": "medium",
             "en": "The chase, which is really an argument.",
             "ru": "Погоня, которая на самом деле спор."},
            {"key": "face",       "share": 0.25, "shot": "close-up",
             "en": "Face to face. Two identical frames cut against each other.",
             "ru": "Лицом к лицу. Два одинаковых кадра, срезанных друг о друга."},
            {"key": "swap",       "share": 0.15, "shot": "extreme close-up",
             "en": "One detail proves they swapped. Never say which one won.",
             "ru": "Одна деталь доказывает, что они поменялись. Кто победил — не говорим."},
        ],
    },
    {
        "key": "unsent_letter",
        "kind": "story",
        "label": {"en": "The Unsent Letter", "ru": "Письмо, которое не отправят"},
        "logline": {
            "en": "A whole clip addressed to someone who is not in it. The best frame is the empty half of the bed.",
            "ru": "Целый клип, обращённый к человеку, которого в нём нет. Лучший кадр — пустая половина кровати.",
        },
        "tempo": {"bpm": [55, 85], "cut": "slow"},
        "scenes": {"min": 10, "typ": 24, "max": 34},
        "styles_fit": ["cinema", "ghibli", "shinkai", "embroidery", "fanuel"],
        "engines": ["kling-3.0-pro", "seedance-2-5", "kling-3.0"],
        "needs_lyrics": True,
        "research": "",
        "beats": [
            {"key": "empty_room", "share": 0.18, "shot": "establishing",
             "en": "The apartment, told through what is missing from it.",
             "ru": "Квартира, рассказанная через то, чего в ней нет."},
            {"key": "flashback",  "share": 0.25, "shot": "medium",
             "en": "The warm past. Different light, different lens, obviously another time.",
             "ru": "Тёплое прошлое. Другой свет, другая оптика — очевидно другое время."},
            {"key": "break",      "share": 0.17, "shot": "close-up",
             "en": "The moment it ended, shown once and not explained.",
             "ru": "Момент, когда всё кончилось, показан один раз и не объяснён."},
            {"key": "present",    "share": 0.20, "shot": "wide",
             "en": "Back to now. The same locations, colder.",
             "ru": "Обратно в сейчас. Те же локации, но холоднее."},
            {"key": "let_go",     "share": 0.20, "shot": "extreme close-up",
             "en": "The letter is not sent. Something else happens instead.",
             "ru": "Письмо не отправлено. Вместо этого происходит что-то другое."},
        ],
    },
    {
        "key": "the_trial",
        "kind": "story",
        "label": {"en": "The Trial", "ru": "Суд"},
        "logline": {
            "en": "He answers for something — in a courtroom, or in his own head. Built-in tension without a single action scene.",
            "ru": "Он отвечает за что-то — в зале суда или у себя в голове. Готовое напряжение без единой экшен-сцены.",
        },
        "tempo": {"bpm": [80, 105], "cut": "mid"},
        "scenes": {"min": 12, "typ": 28, "max": 38},
        "styles_fit": ["dreamclad", "noir", "cinema", "spike"],
        "engines": ["seedance-2-mini", "kling-3.0"],
        "needs_lyrics": True,
        "research": "ДРИМКЛАД, топ-10: «Get Out» — зал суда, 389 тыс. просмотров",
        "beats": [
            {"key": "corridor", "share": 0.15, "shot": "establishing",
             "en": "The corridor before the room. Waiting is the scene.",
             "ru": "Коридор перед залом. Сцена — само ожидание."},
            {"key": "room",     "share": 0.15, "shot": "wide",
             "en": "The room, symmetrical, everyone already seated.",
             "ru": "Зал, симметричный, все уже сидят."},
            {"key": "faces",    "share": 0.20, "shot": "close-up",
             "en": "The faces judging him. No dialogue, just weight.",
             "ru": "Лица, которые его судят. Без реплик, только тяжесть."},
            {"key": "statement","share": 0.25, "shot": "medium",
             "en": "His answer — this is where the strongest verse goes.",
             "ru": "Его ответ — сюда ставится самый сильный куплет."},
            {"key": "verdict",  "share": 0.15, "shot": "extreme close-up",
             "en": "The verdict, shown as a detail, never as a word.",
             "ru": "Приговор, показанный деталью, а не словом."},
            {"key": "door",     "share": 0.10, "shot": "wide",
             "en": "The door. Which side he ends up on is your choice.",
             "ru": "Дверь. По какую сторону он окажется — решаете вы."},
        ],
    },
    {
        "key": "homecoming",
        "kind": "story",
        "label": {"en": "Homecoming", "ru": "Возвращение домой"},
        "logline": {
            "en": "He comes back to the block he left. Everything is where it was, and that is the problem.",
            "ru": "Он возвращается в район, из которого уехал. Всё на месте — в этом и проблема.",
        },
        "tempo": {"bpm": [70, 95], "cut": "slow"},
        "scenes": {"min": 12, "typ": 28, "max": 38},
        "styles_fit": ["dreamclad", "cinema", "spike", "ghibli", "longheads"],
        "engines": ["seedance-2-mini", "kling-3.0"],
        "needs_lyrics": False,
        "research": "",
        "beats": [
            {"key": "road_in",  "share": 0.18, "shot": "establishing",
             "en": "Arrival: a train window, a highway sign, a bus door.",
             "ru": "Приезд: окно поезда, указатель на трассе, дверь автобуса."},
            {"key": "street",   "share": 0.20, "shot": "wide",
             "en": "The street he knows by heart, shot like a stranger would.",
             "ru": "Улица, которую он знает наизусть, снятая как чужим человеком."},
            {"key": "faces",    "share": 0.22, "shot": "close-up",
             "en": "People who stayed. They aged, he changed — different things.",
             "ru": "Те, кто остался. Они постарели, он изменился — это разные вещи."},
            {"key": "yard",     "share": 0.20, "shot": "medium",
             "en": "The yard where it all started, now the wrong size.",
             "ru": "Двор, где всё началось, теперь не того размера."},
            {"key": "not_same", "share": 0.20, "shot": "close-up",
             "en": "He realises he can't stay. He doesn't say it.",
             "ru": "Он понимает, что не может остаться. Вслух не говорит."},
        ],
    },
    {
        "key": "long_drive",
        "kind": "story",
        "label": {"en": "The Long Drive", "ru": "Дорога без конца"},
        "logline": {
            "en": "One car, one night, one road. The cheapest structure to shoot — the whole clip fits in three locations.",
            "ru": "Одна машина, одна ночь, одна дорога. Самая дешёвая структура: весь клип помещается в три локации.",
        },
        "tempo": {"bpm": [75, 105], "cut": "mid"},
        "scenes": {"min": 10, "typ": 26, "max": 36},
        "styles_fit": ["punkrf", "cinema", "noir", "spike", "dreamclad"],
        "engines": ["seedance-2-mini", "seedance-2-5-480", "kling-3.0"],
        "needs_lyrics": False,
        "research": "",
        "beats": [
            {"key": "gas",      "share": 0.18, "shot": "establishing",
             "en": "A petrol station at night — the most cinematic free location there is.",
             "ru": "Заправка ночью — самая кинематографичная бесплатная локация на свете."},
            {"key": "highway",  "share": 0.22, "shot": "wide",
             "en": "Empty road. Headlights and nothing.",
             "ru": "Пустая трасса. Фары и больше ничего."},
            {"key": "passenger","share": 0.20, "shot": "close-up",
             "en": "The passenger seat: occupied, or pointedly not.",
             "ru": "Пассажирское сиденье: занятое или подчёркнуто пустое."},
            {"key": "stop",     "share": 0.20, "shot": "medium",
             "en": "They stop. Something is said or decided outside the car.",
             "ru": "Останавливаются. Что-то сказано или решено снаружи машины."},
            {"key": "sunrise",  "share": 0.20, "shot": "establishing",
             "en": "Sunrise through the windscreen. The road keeps going.",
             "ru": "Рассвет через лобовое. Дорога продолжается."},
        ],
    },
    {
        "key": "randoms",
        "kind": "punch",
        "no_story": True,
        "label": {"en": "Randoms", "ru": "Рандомы"},
        "logline": {
            "en": "Five unrelated punch scenes in a row. No plot on purpose — this is the format the viral accounts actually run.",
            "ru": "Пять несвязанных панч-сцен подряд. Сюжета нет намеренно — именно так работают виральные аккаунты.",
        },
        "tempo": {"bpm": [95, 160], "cut": "fast"},
        "scenes": {"min": 8, "typ": 24, "max": 40},
        "styles_fit": ["munir", "katsumi", "punkrf", "longheads", "spike"],
        "engines": ["seedance-2-mini", "seedance-2-5-480"],
        "needs_lyrics": False,
        "research": "МУНИР: формат «Randoms» — 3–5 несвязанных мем-сцен в одном риле",
        "beats": [
            {"key": "contrast", "share": 0.20, "shot": "medium",
             "en": "Punch 1 — two incompatible worlds in one frame.",
             "ru": "Панч 1 — два несовместимых мира в одном кадре."},
            {"key": "animal",   "share": 0.20, "shot": "close-up",
             "en": "Punch 2 — an animal doing something entirely human.",
             "ru": "Панч 2 — животное делает что-то полностью человеческое."},
            {"key": "money",    "share": 0.20, "shot": "extreme close-up",
             "en": "Punch 3 — money where money should not be.",
             "ru": "Панч 3 — деньги там, где денег быть не должно."},
            {"key": "elder",    "share": 0.20, "shot": "medium",
             "en": "Punch 4 — an old person in a young person's role.",
             "ru": "Панч 4 — пожилой человек в роли молодого."},
            {"key": "machine",  "share": 0.20, "shot": "wide",
             "en": "Punch 5 — a machine behaving like a living thing.",
             "ru": "Панч 5 — машина ведёт себя как живое."},
        ],
    },
    {
        "key": "one_character",
        "kind": "punch",
        "no_story": True,
        "label": {"en": "One Character, Ten Situations", "ru": "Один герой, десять ситуаций"},
        "logline": {
            "en": "The same odd hero doing ordinary errands. This is how a hit becomes a franchise instead of a one-off.",
            "ru": "Один и тот же странный герой занимается обычными делами. Так хит превращается в франшизу, а не в разовый ролик.",
        },
        "tempo": {"bpm": [90, 140], "cut": "mid"},
        "scenes": {"min": 10, "typ": 26, "max": 40},
        "styles_fit": ["katsumi", "longheads", "clay", "spike", "munir"],
        "engines": ["seedance-2-mini", "kling-3.0"],
        "needs_lyrics": False,
        "research": "КАТСУМИ и СПАЙК: «франшизы-персонажи сериями» возвращают аудиторию и превращают хит в коллекцию",
        "beats": [
            {"key": "morning",  "share": 0.20, "shot": "medium",
             "en": "Waking up and getting ready, played completely straight.",
             "ru": "Просыпается и собирается — на полном серьёзе."},
            {"key": "shopping", "share": 0.20, "shot": "wide",
             "en": "A shop. Queue, shelves, nobody staring.",
             "ru": "Магазин. Очередь, полки, никто не пялится."},
            {"key": "work",     "share": 0.20, "shot": "medium",
             "en": "At work, doing the job properly.",
             "ru": "На работе, делает работу как следует."},
            {"key": "conflict", "share": 0.20, "shot": "close-up",
             "en": "A tiny everyday conflict. Parking, change, a queue.",
             "ru": "Мелкий бытовой конфликт. Парковка, сдача, очередь."},
            {"key": "rest",     "share": 0.20, "shot": "wide",
             "en": "Rest. The frame that makes people say “he's just like me”.",
             "ru": "Отдых. Кадр, после которого пишут «он такой же, как я»."},
        ],
    },
    {
        "key": "two_worlds",
        "kind": "punch",
        "no_story": True,
        "label": {"en": "Two Worlds", "ru": "Контраст двух миров"},
        "logline": {
            "en": "Every frame collides two things that must not meet. The universal formula behind all five accounts we studied.",
            "ru": "Каждый кадр сталкивает две вещи, которые не должны встречаться. Универсальная формула всех пяти разобранных аккаунтов.",
        },
        "tempo": {"bpm": [100, 150], "cut": "fast"},
        "scenes": {"min": 8, "typ": 24, "max": 40},
        "styles_fit": ["munir", "dreamclad", "fanuel", "katsumi", "spike"],
        "engines": ["seedance-2-mini", "kling-3.0", "seedance-2-5-480"],
        "needs_lyrics": False,
        "research": "«контраст двух миров — универсальная формула»: традиция×люкс, сакральное×криминал, зверёк×бытовуха, звезда×чужой контекст, этно×космос",
        "beats": [
            {"key": "tradition_luxury", "share": 0.20, "shot": "medium",
             "en": "Tradition meets luxury: old dress, new metal.",
             "ru": "Традиция и люкс: старая одежда, новый металл."},
            {"key": "sacred_street",    "share": 0.20, "shot": "wide",
             "en": "Sacred meets street: candles and crosses where they don't belong.",
             "ru": "Сакральное и улица: свечи и кресты там, где им не место."},
            {"key": "animal_human",     "share": 0.20, "shot": "close-up",
             "en": "Animal meets human routine.",
             "ru": "Зверь и человеческая рутина."},
            {"key": "old_future",       "share": 0.20, "shot": "wide",
             "en": "Past meets future: clay walls, chrome visors.",
             "ru": "Прошлое и будущее: глиняные стены, хромовые визоры."},
            {"key": "poor_rich",        "share": 0.20, "shot": "medium",
             "en": "Poor meets rich in one unbroken frame.",
             "ru": "Бедное и богатое в одном неразрезанном кадре."},
        ],
    },
    {
        "key": "the_lineup",
        "kind": "punch",
        "no_story": True,
        "label": {"en": "The Lineup", "ru": "Каталог"},
        "logline": {
            "en": "Portraits, one after another, dead centre, straight into the lens. Cheapest clip to animate and it looks like an icon screen.",
            "ru": "Портреты один за другим, строго по центру, взгляд в объектив. Самый дешёвый в оживлении клип, а выглядит как иконостас.",
        },
        "tempo": {"bpm": [60, 100], "cut": "slow"},
        "scenes": {"min": 8, "typ": 20, "max": 32},
        "styles_fit": ["dreamclad", "munir", "cinema", "noir", "embroidery"],
        "engines": ["grok", "seedance-2-mini", "kling-3.0"],
        "needs_lyrics": False,
        "research": "ДРИМКЛАД: «фронтальные, симметричные, почти церемониальные композиции, взгляд прямо в объектив»",
        "beats": [
            {"key": "solo",   "share": 0.20, "shot": "medium",
             "en": "One person, centred, looking straight at you.",
             "ru": "Один человек по центру, смотрит прямо на вас."},
            {"key": "pair",   "share": 0.20, "shot": "medium",
             "en": "Two, same posture, same distance.",
             "ru": "Двое, одинаковая поза, одинаковая дистанция."},
            {"key": "group",  "share": 0.20, "shot": "wide",
             "en": "A group dressed identically — the monolithic mass.",
             "ru": "Группа в одинаковом — монолитная масса."},
            {"key": "detail", "share": 0.20, "shot": "extreme close-up",
             "en": "Hands, rings, a chain: the portrait without the face.",
             "ru": "Руки, перстни, цепь: портрет без лица."},
            {"key": "crowd",  "share": 0.20, "shot": "wide",
             "en": "The full crowd, still frontal, still staring.",
             "ru": "Толпа целиком, всё так же фронтально, всё так же в упор."},
        ],
    },
    {
        "key": "product_ritual",
        "kind": "punch",
        "no_story": True,
        "label": {"en": "Product Ritual", "ru": "Витрина товара"},
        "logline": {
            "en": "The object is the hero: a chain, sneakers, a stack, a car. Doubles as a real ad, which is why brands buy this one.",
            "ru": "Герой — предмет: цепь, кроссовки, пачка, машина. Заодно работает настоящей рекламой — за это его и покупают бренды.",
        },
        "tempo": {"bpm": [90, 140], "cut": "mid"},
        "scenes": {"min": 8, "typ": 22, "max": 34},
        "styles_fit": ["munir", "dreamclad", "cinema", "fanuel", "flat2d"],
        "engines": ["seedance-2-5", "kling-3.0-pro", "seedance-2-mini"],
        "needs_lyrics": False,
        "research": "МУНИР: макро-кадр грилз собрал 1 млн; «деньги и статус» в топах у четырёх аккаунтов из пяти",
        "beats": [
            {"key": "reveal", "share": 0.20, "shot": "extreme close-up",
             "en": "The object arriving: box, wrapping, first light on it.",
             "ru": "Предмет появляется: коробка, упаковка, первый свет на нём."},
            {"key": "worn",   "share": 0.20, "shot": "medium",
             "en": "On a body. Now it means status, not material.",
             "ru": "На теле. Теперь это статус, а не материал."},
            {"key": "macro",  "share": 0.20, "shot": "extreme close-up",
             "en": "Macro: texture, scratches, reflections. The frame that sells it.",
             "ru": "Макро: фактура, царапины, отражения. Кадр, который продаёт."},
            {"key": "motion", "share": 0.20, "shot": "medium",
             "en": "In motion — the object doing what it is for.",
             "ru": "В движении — предмет делает то, для чего он есть."},
            {"key": "altar",  "share": 0.20, "shot": "wide",
             "en": "Displayed like a relic: centred, lit, untouchable.",
             "ru": "Выставлен как реликвия: по центру, в свету, неприкосновенно."},
        ],
    },
]


# ═════════════════════════════════════════════════════════════════════════════
#                        ↓↓↓  ЗАКРЫТАЯ ЧАСТЬ  ↓↓↓
#
#  Ниже — актив. Ничего отсюда не имеет права попасть в HTTP-ответ.
#  Тексты перенесены ДОСЛОВНО из frontend/app.js:102 (STYLE_PRESETS.value).
#  После переноса из app.js массив STYLE_PRESETS удаляется целиком, вместе с
#  buildFusionStyle/styleExcerpt/styleKeysFromValue — их работу делает fusion()
#  внизу этого файла.
#
#  Второй адрес утечки, который надо закрыть тем же коммитом: backend/main.py
#  строки 1239 и 1352 отдают в /api/tracks поле `style` — это тот же самый
#  текст, уже записанный в трек. Вместо него отдавать style_keys + style_label.
# ═════════════════════════════════════════════════════════════════════════════
_PROMPTS: dict[str, str] = {
    'ghibli':
        'Hand-painted Studio Ghibli style anime inspired by Hayao Miyazaki films, vertical 9:16. Soft watercolor backgrounds with visible brush texture, lush painterly clouds and greenery, warm golden-hour sunlight or cozy lamp glow through windows. Gentle pleasant palette: warm cream, soft sky blue, grass green, sunset amber — nothing acidic, everything nostalgic and comforting. Characters drawn in classic 2D anime cel style with simple expressive faces, natural relaxed poses, wind gently moving hair and clothes. Quiet magical realism mood: dust motes in sunbeams, steam from food, fireflies, rustling leaves. Every frame feels like a warm memory — calm, humane, a little wistful. No harsh shadows, no neon, no 3D render look, no text.',
    'pixar':
        'High-end 3D animated feature film style like Pixar and DreamWorks, vertical 9:16, ultra HD render. Rounded appealing character design with large expressive eyes, soft subsurface scattering skin, detailed hair and fabric simulation. Rich cinematic lighting: warm key light, colorful bounce light, gentle rim light separating character from background. Vibrant but tasteful saturated palette, shallow depth of field with creamy bokeh, subtle film grain. Polished storytelling composition, emotional facial expressions. No text, no watermark.',
    'shinkai':
        'Modern cinematic anime film style inspired by Makoto Shinkai, vertical 9:16. Breathtaking hyper-detailed backgrounds: glowing skies with layered clouds, lens flares, glittering city lights, rain droplets catching light. Emotional color grading with luminous gradients — deep blues into warm oranges and pinks. Crisp 2D character animation with delicate lighting on hair and eyes. Dramatic sense of scale: vast skies over small human figures. Melancholic-hopeful atmosphere. No text, no watermark.',
    'cinema':
        'Photorealistic cinematic film still, vertical 9:16, shot on ARRI Alexa with anamorphic lenses. Natural skin texture and imperfections, real physical lighting: practical sources, soft window light or hard sun with true shadows. Film color grading with gentle teal-orange balance, subtle 35mm grain, shallow depth of field. Documentary-authentic staging: real locations, lived-in details, honest emotion on faces. No CGI look, no oversharpening, no text.',
    'flat2d':
        'Bold flat 2D vector animation style, vertical 9:16. Clean geometric shapes, thick confident outlines, limited harmonious palette of 4-6 colors per scene, flat color fills with simple two-tone shading. Playful exaggerated proportions and snappy poses, minimal but expressive faces. Mid-century modern and contemporary motion-design influence: textured paper grain overlay, simple patterned backgrounds. Cheerful, graphic, poster-like compositions. No gradients overload, no 3D, no text.',
    'noir':
        'Gritty noir graphic novel style like Sin City and Batman animated classics, vertical 9:16. High-contrast chiaroscuro: deep ink-black shadows swallowing half of every frame, stark white or single warm accent color (red neon, amber streetlight) cutting through darkness. Heavy dramatic hatching and ink texture, rain-slick streets reflecting light, cigarette smoke curling through venetian-blind shadows. Hard-boiled atmosphere: trench coats, brooding silhouettes, low camera angles. Monochrome with one accent color per scene. No text, no captions.',
    'longheads':
        '1990s analog film street photography, scanned 35mm frame with heavy grain and slightly faded Kodak colors, candid documentary framing. Surreal characters with elongated non-human heads on long necks (ostrich-like, greyhound, pale alien with almond eyes, porcelain mannequin mask) on completely ordinary human bodies in baggy 90s streetwear: oversized denim jackets, loose white shirts, wide pants, chunky chains, plastic grocery bags, coffee cups. Deadpan poses, mundane everyday activities, nobody reacts to the surrealism. Locations: laundromats, convenience stores, crosswalks, chain-link fences, boxy 80s sedans, night streets with neon signage and wet asphalt reflections. Muted denim-blue palette with warm cream skin tones and red/neon accents, harsh daylight or direct flash by day, deep black sky and neon glow by night. Vertical 9:16, no text.',
    'embroidery':
        'Hand-embroidered thread-art illustration: the entire image is stitched in dense chain-stitch and satin-stitch embroidery with clearly visible thread loops and fiber texture, like a lovingly hand-sewn patch. Background of warm cream felt and kraft cardboard with soft fabric grain. Bold simplified shapes with clean dark outlines; characters rendered in colored thread, directional stitching following the forms of faces, hair and clothes; flames, smoke and effects also stitched in swirling orange-red-amber threads. Cozy handcrafted feel, slightly naive proportions, saturated yarn colors against the neutral textile background, tiny loose thread ends visible. Vertical 9:16, no text.',
    'spike':
        'Cinematic photorealistic night scene shot on vintage anamorphic lenses, warm tungsten and smoky haze, heavy 35mm film grain with teal-and-amber grade. Post-Soviet Russian setting reimagined with subtle Atomic Heart retrofuturism: khrushchyovka courtyards, cramped old Lada interiors, kiosks, snow-dusted parking lots, delivery couriers in Ozon blue jackets and yellow Yandex thermo-bag backpacks. Photorealistic larger-than-life characters and deadpan cartoon-headed cameos ride together in old cars filled with smoke, count worn banknotes in shabby ornate bedrooms, stare into the lens with calm swagger. Golden chains, tracksuit textures, cigarette smoke curling in headlight beams, wet asphalt reflections. Everyday grit filmed like an epic music video, nobody reacts to the surreal cameos. Vertical 9:16, no text.',
    'munir':
        'Gulf street documentary photography with direct on-camera flash at night and harsh daylight, ultra-wide fisheye lens distortion, saturated 35mm film colors with crushed shadows. Middle Eastern everyday swagger played deadpan: elderly men in red-checkered ghutra headdress and white thobes grinning as they push a fist with a chunky custom name-ring straight into the lens, women in black abayas fueling a black G63 at a midnight gas station, a Doberman with a heavy chain collar lunging toward the camera, corner grocery shops with Arabic signage and packed shelves, plastic chairs, dates and spice jars. Objects thrust toward the ultra-wide lens so they loom huge in the foreground, faces close and warped at the edges, flash bleaching the foreground against deep black night. Humor and quiet confidence, mundane life shot like a rap video. Vertical 9:16, no text.',
    'fanuel':
        'Hyperreal cinematic surreal fashion film, epic single-frame worldbuilding. One elegant figure in a sharply tailored suit of a single bold color (burnt orange, saffron yellow, deep crimson) stands or walks calmly inside an impossible landscape: on the open sea at dusk, along the rings of a giant planet, across endless dunes, under colossal celestial bodies. Recurring fire motif — burning umbrellas, floating flames, embers, fire reflected in water. Deadpan composed poses, quiet confidence, no reaction to the impossible. Painterly dusk palettes: violet-pink-orange gradient skies, deep ocean blues, warm firelight against cool darkness; volumetric cinematic lighting, anamorphic depth, ultra-detailed photorealistic rendering with epic scale contrast between the small figure and the vast world. Vertical 9:16, no text.',
    'clay':
        'Handcrafted claymation stop-motion style (Aardman/Laika vibe): visible fingerprints in plasticine, slightly imperfect frame-to-frame jitter, miniature set with real fabric and cardboard props, warm practical lighting, shallow depth of field macro look, expressive oversized eyes, vertical 9:16, no text.',
    'punkrf':
        'Hyperreal Russian street found-footage: night dashcam, GoPro, phone or CCTV camera look with heavy VHS grain, analog noise and motion blur; harsh headlights, red neon gas-station canopies, wet asphalt, dense traffic with glowing tail lights, grey soviet panel blocks, ruined brick factories, dusty supercars in wastelands. One absurd event unfolds in the middle of mundane Russian reality — animals rearing between cars, flying couriers, delivery drones, aliens in queues — filmed like an accidental viral video: documentary believability, realistic physics, nobody poses, aggressive dynamic framing, violent handheld shake, strobing flash by night. Muted cold palette with red neon accents. Vertical 9:16, no text.',
    'dreamclad':
        '1990s American hood-cinema still, shot on grainy 35mm film: faded low-contrast color grade with warm orange-brown skin tones and dusty teal shadows (or deep-grain black-and-white), heavy film grain, soft halation, subtle gate weave and VHS-era imperfections. Brick-block New York / LA streets of the 90s — bodegas with graffiti, chain-link fences, stone staircases, boxy sedans and vintage Cadillacs — or night-time mansion gates and museum halls lit by warm tungsten windows and headlights. Young men in white tank tops, bandana masks, hoodies and baggy denim; crowds dressed identically like a uniform; recurring icons of money stacks, doves, crosses, candles and classical statues — sacred mixed with street. Frontal, symmetric, almost ceremonial compositions, subjects staring straight into the lens, or candid through-the-windshield documentary angles; overexposed hazy daylight or moody night backlight. Cinematic, nostalgic, quietly menacing, music-video energy. No clean digital look, no HDR, no modern cars or clothing, no neon cyberpunk, no glossy skin, no watermarks. Vertical 9:16, no text.',
    'katsumi':
        'Hyperrealistic absurdist found-footage aesthetic: a deadpan surreal protagonist (animal or costumed figure) doing mundane human things with total seriousness, shot like accidental amateur documentary footage from the 1990s–2000s — handheld camcorder or disposable-camera look with harsh direct on-camera flash at night, or flat overcast daylight; heavy analog film grain, VHS noise, slight chromatic aberration, motion blur, fisheye or wide-angle distortion, tilted imperfect framing with the subject too close to the lens, often staring straight into the camera. Muted dirty palette of swampy olive, tobacco brown, dusty grey and desaturated flesh tones, background falling into deep black shadow, with one rare accent color (neon sign, police lights, orange robe, gold chain). Gritty tactile textures: wet fur, greasy pavement, cigarette smoke, scuffed metal, cheap floral motel interiors, cluttered convenience-store shelves. Cinematic realism, not cartoon — everything must look physically shot, grimy street-punk mood, crime-scene-snapshot lighting, deadpan comedy with zero wink. Avoid: clean digital sharpness, glossy studio light, saturated candy colors, cartoon or 3D-render look, symmetry, beauty-filter smoothness. Vertical 9:16, no text.',
}


# ─────────────────────────────────────────────────────────────────────────────
# РАЗБОР СТРУКТУРЫ — только для prompt_class="school" и только на PRO+.
#
# Это НЕ промпт и не его пересказ, из которого можно собрать копию. Это разбор
# приёма по пяти слотам: чем снято, как освещено, какая палитра, какая фактура,
# что запрещено. Читается как урок, применяется через конструктор промта —
# и одновременно это готовый контент для уровня 3 «Школы lolq».
#
# У семи closed-пресетов структуры нет и не будет: там разбор фактически
# восстанавливает текст.
# ─────────────────────────────────────────────────────────────────────────────
_STRUCTURE: dict[str, dict] = {
    "ghibli": {
        "optics": "Мягкий фокус, обычное поле зрения, без длиннофокусного сжатия — кадр должен дышать, а не давить.",
        "light":  "Золотой час или свет лампы сквозь окно. Один тёплый источник, мягкая тень, видимые пылинки в луче.",
        "palette": "Кремовый, небесно-голубой, травяной, закатный янтарь. Кислотных цветов нет вообще.",
        "texture": "Видимый мазок акварели на фонах, зерно бумаги, живописные облака и листва.",
        "bans": "Жёсткие тени, неон, 3D-рендер, глянец, любой текст в кадре.",
    },
    "pixar": {
        "optics": "Короткий фокус, малая глубина, сливочное боке — фон всегда мягче героя.",
        "light":  "Схема из трёх: тёплый ключ, цветной отскок, контровой, отделяющий героя от фона.",
        "palette": "Насыщенная, но не кислотная; цвет фона всегда дополняет цвет героя.",
        "texture": "Подповерхностное рассеивание кожи, симуляция волос и ткани, едва заметное зерно поверх рендера.",
        "bans": "Плоские тени, текст, вотермарки, фотореалистичные поры кожи.",
    },
    "shinkai": {
        "optics": "Широкий план с вертикальным масштабом: огромное небо, маленькая фигура. Блики линзы обязательны.",
        "light":  "Контровой закат, светящиеся градиенты, свет по краю волос и в глазах.",
        "palette": "Глубокий синий, переходящий в оранжевый и розовый. Градиент — главный инструмент.",
        "texture": "Гиперподробные фоны: слоистые облака, капли, огни города, отражения.",
        "bans": "Грязь, плоские фоны, приглушённая палитра, текст.",
    },
    "cinema": {
        "optics": "ARRI Alexa с анаморфотом, малая глубина. Никакого перешарпа — резкость только там, где её дал бы объектив.",
        "light":  "Только физически возможные источники: окно, лампа, солнце. Тень всегда честная.",
        "palette": "Деликатный teal-orange, без выкрученного контраста.",
        "texture": "Зерно 35 мм, поры и неровности кожи, следы жизни на локации.",
        "bans": "CGI-глянец, HDR, вылизанная кожа, невозможный свет, текст.",
    },
    "flat2d": {
        "optics": "Фронтальная плоскость вместо перспективы: композиция строится как плакат.",
        "light":  "Света нет — есть двухтоновая заливка. Тень это второй цвет, а не затемнение.",
        "palette": "4–6 цветов на сцену, жёстко. Больше — каша.",
        "texture": "Бумажное зерно поверх заливок, простые узоры на фонах.",
        "bans": "Градиентная каша, объёмные тени, 3D, реалистичные пропорции.",
    },
    "noir": {
        "optics": "Низкая точка съёмки, тени от жалюзи, дождь как оптический фильтр.",
        "light":  "Киароскуро: один источник, половина кадра проваливается в чёрное.",
        "palette": "Монохром плюс РОВНО один акцентный цвет на сцену. Два акцента убивают приём.",
        "texture": "Штриховка и тушь, дым, мокрый асфальт с отражениями.",
        "bans": "Второй акцентный цвет, мягкий рассеянный свет, серые полутона вместо чёрного, подписи.",
    },
    "embroidery": {
        "optics": "Плоско и близко: макро к ткани, никакой глубины сцены.",
        "light":  "Ровный рассеянный свет; вся тень берётся от рельефа самой нити.",
        "palette": "Насыщенная пряжа на нейтральном кремовом фетре и крафте.",
        "texture": "Цепной и гладьевой стежок, направление стежка повторяет форму, торчащие концы нити.",
        "bans": "Фотореализм, глянец, объёмная перспектива, гладкие градиенты.",
    },
    "clay": {
        "optics": "Макро на миниатюрную сцену, малая глубина — так читается масштаб декорации.",
        "light":  "Тёплый практический свет настольного размера, мягкие тени от реквизита.",
        "palette": "Пластилиновая, слегка грязная — чистых цветов у пластилина не бывает.",
        "texture": "Отпечатки пальцев, ткань и картон реквизита, покадровая дрожь.",
        "bans": "Гладкий 3D-рендер, идеально плавное движение, чистые поверхности, текст.",
    },
}

# ─────────────────────────────────────────────────────────────────────────────
# SEED-ы КАРКАСОВ — тоже закрытая часть.
#
# `story` уходит в Project.story, `note` — в Track.director_note. Публично мы
# показываем логлайн и структуру битов человеческим языком; сама режиссёрская
# инструкция для модели остаётся у нас — по той же причине, что и промпты.
# ─────────────────────────────────────────────────────────────────────────────
_SEEDS: dict[str, dict] = {
    "night_walk": {
        "story": "Герой выходит из дома ночью и идёт через весь город до рассвета. Внешнего конфликта нет: город не преследует его, город его не замечает. Драматургия строится на смене дистанции — сначала герой мелкий в огромном кадре, к середине камера подходит вплотную, к финалу снова отходит, но свет уже другой. Каждая смена локации открывается establishing-планом. Финальный кадр рифмуется с первым по композиции и отличается только цветом света.",
        "note": "Держи один непрерывный маршрут: локации должны соединяться географически, а не быть случайным набором. Ускорение ритма монтажа в третьей четверти, замедление в финале.",
    },
    "one_day": {
        "story": "Один обычный день героя от будильника до тёмного окна. Ничего выдающегося не происходит — и именно это снимается как эпос: рутинное действие подаётся крупностью и светом, положенными кульминации. Ровно один раз шаблон дня ломается мелким событием (пролитое, чужой взгляд, пауза), и это единственный эмоциональный пик. Финал — окно квартиры снаружи, одно светящееся из сотни.",
        "note": "Повтори одно и то же бытовое действие минимум трижды с разной крупностью — повтор и есть смысл. Никакой иронии: подача абсолютно серьёзная.",
    },
    "come_up": {
        "story": "Путь героя из точки нужды в точку достатка. Пять ступеней: исходная бедность без жалости и без эстетизации; невидимая работа; первые деньги, которые ещё можно пересчитать; награда, снятая как церемония; и возврат в первую локацию другим человеком. Обязателен финальный кадр-обложка: фронтальный, симметричный, годный на постер.",
        "note": "Ступени должны читаться по свету: от холодного и тусклого к тёплому и контрастному. В кадре наград не более одного статусного предмета за сцену — иначе превращается в рекламу.",
    },
    "from_the_crowd": {
        "story": "Весь концерт показан изнутри зрительской толпы, ни одного кадра со сцены глазами артиста. Главный герой — масса: она собирается, ждёт, взрывается и растворяется. Артист появляется только как силуэт в контровом свете. Финал — пустой зал после, снятый тем же общим планом, что и полный.",
        "note": "Ритм монтажа жёстко привязан к аудиопрофилю: тихие сегменты — длинные планы ожидания, врывы — короткие склейки по 2 секунды. Ручная камера, честная тряска.",
    },
    "dream_wake": {
        "story": "Герой засыпает, проваливается в мир, живущий по другим правилам, и просыпается. Реальность и сон различаются оптикой и палитрой, но не героем: он один и тот же. Через оба мира проходит один предмет — он появляется в реальности до сна, играет ключевую роль во сне и лежит на столе после пробуждения. Переход между мирами постепенный, а не склейкой.",
        "note": "Искажение вводить медленно: первые кадры сна почти неотличимы от реальности. Кульминация — extreme close-up того самого предмета.",
    },
    "the_double": {
        "story": "Герой встречает собственную версию, прожившую другую жизнь. Двойник одет иначе и держится иначе, но это то же лицо. Три акта: обнаружение (отражение отстаёт), преследование, очная ставка. Финал — одна деталь, доказывающая, что они поменялись местами; кто победил, не объясняется.",
        "note": "Оба героя описываются одинаковым текстом внешности, различаются только одеждой и осанкой. В сценах очной ставки чередуй зеркальные ракурсы.",
    },
    "unsent_letter": {
        "story": "Клип целиком обращён к человеку, которого в кадре нет. Настоящее время снимается через пустоты: половина кровати, вторая чашка, дверь. Прошлое даётся тёплым флешбэком с другой оптикой и другим светом — так, чтобы времена не путались. Момент разрыва показывается ровно один раз и не объясняется. Письмо в финале не отправляется.",
        "note": "Ни одного кадра с ссорой и криком. Всё через предметы и свет. Планы длинные, движение камеры почти отсутствует.",
    },
    "the_trial": {
        "story": "Герой отвечает за содеянное — в зале суда, на разговоре или у себя в голове. Пространство симметричное и церемониальное. Лица тех, кто судит, даются крупно и без реплик. Самый сильный куплет ложится на монолог героя. Приговор показывается деталью, а не словом; финальная дверь оставляет исход открытым.",
        "note": "Композиции фронтальные и симметричные, камера почти статична — движение только в монологе. Никаких надписей, документов крупным планом с читаемым текстом.",
    },
    "homecoming": {
        "story": "Герой возвращается в район, который покинул. Всё на месте, изменился он. Драматургия через несовпадение: знакомая улица снята как чужая, двор детства оказывается не того размера, оставшиеся друзья постарели, а он стал другим — это разные вещи, и клип их различает. Он понимает, что не может остаться, и не говорит этого вслух.",
        "note": "Одна и та же локация должна появиться дважды: в начале издалека и в конце вплотную. Свет к финалу холоднее, чем в начале.",
    },
    "long_drive": {
        "story": "Ночь, трасса, одна машина. Три локации на весь клип: заправка, салон, обочина. Пассажирское сиденье — отдельный герой: занятое оно или подчёркнуто пустое, решает смысл всего ролика. В середине они останавливаются, и что-то решается снаружи машины. Финал — рассвет через лобовое стекло, дорога продолжается.",
        "note": "Максимум кадров снимай из салона: это дёшево и держит единство. Свет только от фар, приборной панели и вывесок.",
    },
    "randoms": {
        "story": "",
        "note": "РЕЖИМ БЕЗ СЮЖЕТА. Пять независимых панч-кадров, между собой не связанных ничем, кроме стиля. Каждый читается за секунду без звука и без языка. Никакой сквозной драматургии, никаких повторяющихся героев. Каждый кадр — отдельная законченная шутка, поданная абсолютно серьёзно: никто в кадре не реагирует на абсурд.",
    },
    "one_character": {
        "story": "",
        "note": "РЕЖИМ БЕЗ СЮЖЕТА, но с единым героем. Один и тот же странный персонаж (зверь, костюмированная фигура, существо) проживает не связанные между собой бытовые ситуации. Внешность героя описывается дословно одинаково в каждом кадре — это его франшиза. Абсолютная серьёзность подачи: герой не осознаёт своей странности, окружающие не реагируют.",
    },
    "two_worlds": {
        "story": "",
        "note": "РЕЖИМ БЕЗ СЮЖЕТА. Каждый кадр — столкновение двух несовместимых миров в одной рамке, без склейки и без монтажной подсказки: оба мира присутствуют физически одновременно. Контраст должен считываться за полсекунды. Пары не повторяются между кадрами.",
    },
    "the_lineup": {
        "story": "",
        "note": "РЕЖИМ БЕЗ СЮЖЕТА. Серия портретов: строго фронтально, объект по центру, взгляд прямо в объектив, одинаковая дистанция и одинаковая высота камеры во всех кадрах. Меняется только кто в кадре и сколько их. Движение минимальное — дыхание, ветер, моргание. Композиция церемониальная, почти иконописная.",
    },
    "product_ritual": {
        "story": "",
        "note": "РЕЖИМ БЕЗ СЮЖЕТА. Герой — предмет. Человек в кадре присутствует только как носитель. Пять регистров: появление, ношение, макро-фактура, работа предмета, экспозиция как реликвии. Свет всегда работает на материал: металл — контровой блик, ткань — боковой, стекло — отражение.",
    },
}


# ─────────────────────────────────────────────────────────────────────────────
# ДОСТУП. Всё, что уходит в HTTP, проходит через эти функции.
# ─────────────────────────────────────────────────────────────────────────────

#: Поля карточки стиля, разрешённые к выдаче наружу. Белый список, а не
#: чёрный: новое закрытое поле, добавленное в STYLES по невнимательности,
#: наружу не поедет само по себе.
PUBLIC_STYLE_FIELDS = (
    "key", "group", "tier", "prompt_class", "label", "desc", "gain",
    "tags", "music", "tempo", "mix_role", "mix_with", "avoid_mix",
    "engines", "research",
)
PUBLIC_PRESET_FIELDS = (
    "key", "kind", "no_story", "label", "logline", "tempo", "scenes",
    "styles_fit", "engines", "needs_lyrics", "research", "beats",
)

_BUILTIN_BY_KEY = {s["key"]: s for s in STYLES}
_PRESET_BY_KEY = {p["key"]: p for p in CLIP_PRESETS}

# ─────────────────────────────────────────────────────────────────────────────
# НАЛОЖЕНИЕ АДМИНКИ (backend/crm.py → таблица style_overrides).
#
# Тонкий слой ВНУТРИ этого модуля, а не рядом с ним: весь остальной код
# (set_track_style, _frame_prompt, витрина, SEO-страницы /prompts/*) зовёт те
# же самые функции и не меняется вообще. Наложение читается на каждый вызов,
# но живёт в памяти — main.py зовёт set_overlay() на старте и после каждой
# правки. Перечитывать базу на каждый кадр нельзя: кадров тысячи.
#
# Правило двух слоёв здесь такое же, как во всём файле: закрытые поля
# (prompt / story_base / structure) в public_style() не попадают, потому что
# он собирает ответ по белому списку PUBLIC_STYLE_FIELDS.
# ─────────────────────────────────────────────────────────────────────────────
_OVERLAY: dict[str, dict] = {}

#: Поля наложения, которые можно показывать наружу.
_OVERLAY_PUBLIC = ("label", "desc", "gain", "group", "tier", "prompt_class",
                   "tags", "music", "tempo", "mix_role", "mix_with",
                   "avoid_mix", "engines", "media")


def _rebuild_index() -> None:
    """Пересобрать производные реестры после смены наложения.

    STYLE_KEYS/CLOSED_KEYS — модульные переменные, и вызывающий код читает
    их через атрибут модуля (prompts_catalog.STYLE_KEYS), поэтому простое
    переприсваивание здесь видно всем сразу."""
    global _STYLE_BY_KEY, STYLE_KEYS, CLOSED_KEYS
    merged: dict[str, dict] = {}
    for k, base in _BUILTIN_BY_KEY.items():
        ov = _OVERLAY.get(k) or {}
        if ov and ov.get("enabled") is False:
            continue
        if not ov:
            merged[k] = base
            continue
        row = dict(base)
        for f in _OVERLAY_PUBLIC:
            if ov.get(f) not in (None, "", [], {}):
                row[f] = ov[f]
        merged[k] = row
    # Стили, заведённые владельцем целиком (builtin=False).
    for k, ov in _OVERLAY.items():
        if k in _BUILTIN_BY_KEY or ov.get("enabled") is False:
            continue
        row = {"key": k, "group": "cinema", "tier": "pro", "prompt_class": "closed",
               "label": {"en": k, "ru": k}, "desc": {"en": "", "ru": ""},
               "gain": {"en": "", "ru": ""}, "tags": [],
               "music": {"en": "", "ru": "", "genres": []},
               "tempo": {"bpm": [70, 140]}, "mix_role": "base",
               "mix_with": [], "avoid_mix": [], "engines": []}
        for f in _OVERLAY_PUBLIC:
            if ov.get(f) not in (None, "", [], {}):
                row[f] = ov[f]
        merged[k] = row
    _STYLE_BY_KEY = merged
    STYLE_KEYS = tuple(merged)
    CLOSED_KEYS = tuple(k for k, v in merged.items()
                        if v.get("prompt_class") == "closed")


def set_overlay(data: dict | None) -> None:
    """Заменить наложение целиком. Зовётся из main.py на старте и после
    каждой правки в админке — другого способа его поменять нет."""
    global _OVERLAY
    _OVERLAY = {str(k): v for k, v in (data or {}).items() if isinstance(v, dict)}
    _rebuild_index()


def overlay_keys() -> tuple:
    """Какие стили сейчас изменены владельцем. Нужно админке — показать
    метку «изменён» и кнопку «вернуть заводской»."""
    return tuple(_OVERLAY)


def is_builtin(key: str) -> bool:
    return key in _BUILTIN_BY_KEY


def builtin_style(key: str) -> dict | None:
    """Заводская карточка без наложения — для diff'а в редакторе."""
    return _BUILTIN_BY_KEY.get(key)


def builtin_prompt(key: str) -> str:
    return _PROMPTS.get(key, "")


def builtin_structure(key: str) -> dict | None:
    return _STRUCTURE.get(key)


_STYLE_BY_KEY: dict[str, dict] = dict(_BUILTIN_BY_KEY)
STYLE_KEYS = tuple(s["key"] for s in STYLES)
PRESET_KEYS = tuple(p["key"] for p in CLIP_PRESETS)
#: Ключи, текст которых не показываем никогда и никому.
CLOSED_KEYS = tuple(s["key"] for s in STYLES if s["prompt_class"] == "closed")


def media(key: str) -> dict:
    """Пути к превью карточки. Заводские файлы лежат в образе рядом с
    фронтом; всё, что владелец залил через админку стилей, приезжает
    наложением (StyleAsset → set_overlay) и перекрывает их."""
    ov = (_OVERLAY.get(key) or {}).get("media")
    if isinstance(ov, dict) and (ov.get("poster") or ov.get("shots") or ov.get("loop")):
        return {
            "poster": ov.get("poster") or f"/img/styles/{key}/poster.jpg",
            "loop": ov.get("loop") or f"/img/styles/{key}/loop.mp4",
            "shots": list(ov.get("shots") or
                          [f"/img/styles/{key}/shot{i}.jpg" for i in range(1, 7)]),
        }
    return {
        "poster": f"/img/styles/{key}/poster.jpg",
        "loop": f"/img/styles/{key}/loop.mp4",
        "shots": [f"/img/styles/{key}/shot{i}.jpg" for i in range(1, 7)],
    }


def public_style(key: str, *, lang: str = "", uses: int = 0) -> dict | None:
    """Карточка стиля для витрины. Промпта здесь нет и быть не может."""
    s = _STYLE_BY_KEY.get(key)
    if not s:
        return None
    out = {f: s[f] for f in PUBLIC_STYLE_FIELDS if f in s}
    out["media"] = media(key)
    out["uses"] = uses
    #: has_structure — показывать ли на карточке кнопку «разбор приёма».
    #: Сам разбор отдаётся отдельным запросом и только PRO+.
    out["has_structure"] = bool(style_structure_raw(key))
    if lang in ("en", "ru"):
        for f in ("label", "desc", "gain"):
            if isinstance(out.get(f), dict):
                out[f] = out[f].get(lang, out[f].get("en", ""))
        if isinstance(out.get("music"), dict):
            out["music"] = {"text": s["music"].get(lang, s["music"]["en"]),
                            "genres": s["music"]["genres"]}
    return out


def public_styles(*, lang: str = "", group: str = "", tier: str = "",
                  uses: dict[str, int] | None = None) -> list[dict]:
    uses = uses or {}
    out = []
    for key, s in _STYLE_BY_KEY.items():
        if group and s.get("group") != group:
            continue
        if tier and s.get("tier") != tier:
            continue
        card = public_style(key, lang=lang, uses=uses.get(key, 0))
        if card:
            out.append(card)
    return out


def public_preset(key: str, *, lang: str = "") -> dict | None:
    """Карточка каркаса. Режиссёрский seed остаётся на сервере."""
    p = _PRESET_BY_KEY.get(key)
    if not p:
        return None
    out = {f: p[f] for f in PUBLIC_PRESET_FIELDS if f in p}
    out.setdefault("no_story", False)
    if lang in ("en", "ru"):
        for f in ("label", "logline"):
            if isinstance(out.get(f), dict):
                out[f] = out[f].get(lang, out[f].get("en", ""))
        out["beats"] = [
            {"key": b["key"], "share": b["share"], "shot": b["shot"],
             "text": b.get(lang, b["en"])}
            for b in p["beats"]
        ]
    return out


def public_presets(*, lang: str = "", kind: str = "") -> list[dict]:
    return [public_preset(p["key"], lang=lang) for p in CLIP_PRESETS
            if not kind or p["kind"] == kind]


# ── закрытое: вызывается только из бэкенда ───────────────────────────────────

def style_prompt(key: str) -> str:
    """ПОЛНЫЙ текст стиля. Уходит в модель, НИКОГДА в HTTP-ответ.
    Наложение админки перекрывает заводской текст."""
    ov = (_OVERLAY.get(key) or {}).get("prompt")
    if isinstance(ov, str) and ov.strip():
        return ov
    return _PROMPTS.get(key, "")


def style_story_base(key: str) -> str:
    """Как стиль влияет на СЮЖЕТ, а не на картинку. Уходит отдельным блоком
    в промпты сюжета и раскадровки (claude.py).

    Заводского значения у этого поля нет: до админки стилей влияния стиля
    на сценарий не существовало вовсе. Пусто — блок просто не приезжает."""
    ov = (_OVERLAY.get(key) or {}).get("story_base")
    return ov if isinstance(ov, str) else ""


def style_gen_refs(key: str) -> list[str]:
    """Имена файлов-референсов стиля, помеченных «в генерацию».

    Это НЕ витрина: витринные постеры и примеры кадров живут в media().
    Сюда попадает только то, что владелец сознательно пустил в промпт
    кадра — и main.py подмешивает максимум две штуки, после персонажей."""
    refs = (_OVERLAY.get(key) or {}).get("gen_refs")
    return [str(x) for x in refs if x] if isinstance(refs, list) else []


def story_base(keys) -> str:
    """Сводная сценарная база микса: основа первой, подмешанные следом."""
    out = []
    for k in (keys or []):
        base = style_story_base(k).strip()
        if base:
            out.append(base)
    return "\n\n".join(out)


def style_structure_raw(key: str) -> dict | None:
    """Разбор приёма БЕЗ проверки тарифа — для валидации и админки."""
    ov = (_OVERLAY.get(key) or {}).get("structure")
    if isinstance(ov, dict) and ov:
        return ov
    return _STRUCTURE.get(key)


def style_structure(key: str, *, is_pro: bool) -> dict | None:
    """Разбор приёма по пяти слотам. Только school-стили и только PRO+.
    Для closed возвращает None при любом тарифе — там разбор равносилен тексту."""
    if not is_pro:
        return None
    s = _STYLE_BY_KEY.get(key) or {}
    if s.get("prompt_class") == "closed":
        return None
    return style_structure_raw(key)


def preset_seed(key: str) -> dict:
    """Режиссёрский seed каркаса: story → Project.story, note →
    Track.director_note. Для kind="punch" story пустой, и вызывающий обязан
    выставить Track.no_story=True — тогда claude.py уходит в ветку панч-кадров."""
    return dict(_SEEDS.get(key) or {"story": "", "note": ""})


def _excerpt(value: str) -> str:
    """Первые ~2 предложения промпта — ими подмешивается неосновной стиль.
    Дословный порт styleExcerpt() из frontend/app.js, чтобы уже сохранённые
    треки продолжали разбираться в те же ключи."""
    out, buf = [], ""
    for ch in value:
        buf += ch
        if ch == ".":
            out.append(buf)
            buf = ""
            if len(out) == 2:
                break
    return "".join(out).strip() or value


def keys_from_prompt(value: str) -> list[str]:
    """Обратный разбор: сохранённый текст стиля → ключи пресетов.

    Нужен ровно для СТАРЫХ треков, записанных фронтом до переноса реестра на
    сервер: у них есть текст и нет ключей. Дословный порт styleKeysFromValue()
    из frontend/app.js — основа лежит в тексте целиком и идёт первой,
    подмешанные стили — выжимками.

    Новые треки хранят ключи явно (tracks.style_keys) и сюда не попадают:
    поиск подстрок хрупок, любая правка промпта его молча ломает."""
    if not value:
        return []
    order = {k: i for i, k in enumerate(_STYLE_BY_KEY)}
    prompts = {k: style_prompt(k) for k in _STYLE_BY_KEY}
    base = [k for k, p in prompts.items() if p and p in value]
    extras = [k for k, p in prompts.items()
              if p and p not in value and _excerpt(p) in value]
    return (sorted(base, key=lambda k: order.get(k, 99))
            + sorted(extras, key=lambda k: order.get(k, 99)))


def labels(keys, lang: str = "en") -> str:
    """Человеческая подпись микса: «★ Дримклад ＋ Нуар».

    Первый ключ — основа, и подпись обязана это показывать: порядок выбора
    решает всё, но нигде не виден, и из интерфейса непонятно, что главный
    именно первый."""
    lang = lang if lang in ("en", "ru") else "en"
    out = []
    for i, k in enumerate(keys or []):
        s = _STYLE_BY_KEY.get(k)
        if s:
            out.append(("★ " if i == 0 else "＋ ")
                       + s["label"].get(lang, s["label"]["en"]))
    return " ".join(out)


def fusion(keys: list[str], extra: str = "") -> str:
    """Итоговый текст стиля трека. Порт buildFusionStyle() на сервер:
    первый ключ — основа целиком, остальные подмешиваются выжимками.
    `extra` — приписка, которую человек написал сам; она его, поэтому
    возвращать её ему наружу можно."""
    chosen = [k for k in keys if style_prompt(k)]
    if not chosen:
        return (extra or "").strip()
    base = style_prompt(chosen[0])
    if len(chosen) > 1:
        base += "\n\nBlend in elements of: " + " ".join(
            _excerpt(style_prompt(k)) for k in chosen[1:])
    if extra.strip():
        base += "\n\n" + extra.strip()
    return base


# ─────────────────────────────────────────────────────────────────────────────
# САМОПРОВЕРКА. Гоняется тестом и на старте в DEBUG: каталог обязан быть
# внутренне непротиворечив, иначе витрина показывает битые ссылки, а
# раскадровка получает доли, которые не складываются в трек.
# ─────────────────────────────────────────────────────────────────────────────
SHOT_SIZES = {"extreme close-up", "close-up", "medium", "wide", "establishing"}


def validate() -> list[str]:
    """Самопроверка каталога С УЧЁТОМ НАЛОЖЕНИЯ АДМИНКИ.

    Гоняется тестом, на старте и — главное — ПЕРЕД каждым сохранением стиля
    в админке. Админ, случайно вставивший промпт в поле «описание», получает
    отказ, а не тихую утечку на витрину: проверка утечки внизу гоняется по
    ЭФФЕКТИВНЫМ картам и эффективным промптам, а не по тому, что лежит в
    коде."""
    err: list[str] = []
    groups = {g["key"] for g in GROUPS}
    all_tags = {t for axis in TAGS.values() for t in axis}

    for k, s in _STYLE_BY_KEY.items():
        mark = k if is_builtin(k) else f"{k} (свой)"
        if s.get("group") not in groups:
            err.append(f"{mark}: неизвестная группа {s.get('group')}")
        if s.get("tier") not in ("free", "pro"):
            err.append(f"{mark}: неизвестный тариф {s.get('tier')}")
        if s.get("prompt_class") not in ("closed", "school"):
            err.append(f"{mark}: неизвестный prompt_class {s.get('prompt_class')}")
        if not style_prompt(k).strip():
            err.append(f"{mark}: нет промпта")
        for t in s.get("tags") or []:
            if t not in all_tags:
                err.append(f"{mark}: тег {t} вне словаря TAGS")
        for ref in list(s.get("mix_with") or []) + list(s.get("avoid_mix") or []):
            if ref not in _STYLE_BY_KEY:
                err.append(f"{mark}: ссылка на несуществующий стиль {ref}")
            if ref == k:
                err.append(f"{mark}: ссылается сам на себя")
        has_structure = bool(style_structure_raw(k))
        if s.get("prompt_class") == "closed" and has_structure:
            err.append(f"{mark}: closed-стиль не может иметь публикуемого разбора")
        if s.get("prompt_class") == "school" and not has_structure:
            err.append(f"{mark}: school-стиль без разбора — не о чем учить")
        if s.get("prompt_class") == "closed" and s.get("mix_role") != "base":
            err.append(f"{mark}: фирменный пресет обязан быть base")
        try:
            lo, hi = s["tempo"]["bpm"]
        except (KeyError, TypeError, ValueError):
            err.append(f"{mark}: нет диапазона bpm")
            continue
        if not (55 <= lo < hi <= 200):
            err.append(f"{mark}: bpm {lo}-{hi} вне диапазона audio_analysis (55–200)")

    for p in CLIP_PRESETS:
        k = p["key"]
        total = round(sum(b["share"] for b in p["beats"]), 6)
        if total != 1.0:
            err.append(f"{k}: сумма долей битов {total}, должна быть 1.0")
        for b in p["beats"]:
            if b["shot"] not in SHOT_SIZES:
                err.append(f"{k}/{b['key']}: крупность {b['shot']} вне словаря SCENES_SYSTEM")
        for ref in p["styles_fit"]:
            if ref not in _STYLE_BY_KEY:
                err.append(f"{k}: ссылка на несуществующий стиль {ref}")
        if p["kind"] == "punch" and not p.get("no_story"):
            err.append(f"{k}: punch-каркас обязан выставлять no_story")
        if p["kind"] == "story" and not _SEEDS.get(k, {}).get("story"):
            err.append(f"{k}: story-каркас без сквозного сюжета в _SEEDS")
        if k not in _SEEDS:
            err.append(f"{k}: нет seed")

    for c in COLLECTIONS:
        for ref in c["styles"]:
            if ref not in _STYLE_BY_KEY:
                err.append(f"подборка {c['key']}: несуществующий стиль {ref}")

    # Главная проверка: ни один публичный дикт не содержит текста промпта.
    # Промпты берём ЭФФЕКТИВНЫЕ — иначе редактор стилей однажды выложит
    # свеженаписанный закрытый текст прямо в поле «описание» на витрине.
    eff_prompts = {k: style_prompt(k) for k in _STYLE_BY_KEY}
    eff_prompts.update({k: v for k, v in _PROMPTS.items() if v})
    for key in _STYLE_BY_KEY:
        blob = repr(public_style(key))
        for pk, prompt in eff_prompts.items():
            if prompt and prompt[:60] in blob:
                err.append(f"УТЕЧКА: промпт {pk} виден в публичной карточке {key}")
        base = style_story_base(key)
        if base and base[:60] in blob:
            err.append(f"УТЕЧКА: сценарная база {key} видна в публичной карточке")
    for p in CLIP_PRESETS:
        blob = repr(public_preset(p["key"]))
        seed = _SEEDS.get(p["key"], {}).get("story", "")
        if seed and seed[:60] in blob:
            err.append(f"УТЕЧКА: seed {p['key']} виден в публичной карточке")
    return err


if __name__ == "__main__":
    problems = validate()
    print("\n".join(problems) if problems else
          f"каталог цел: {len(STYLES)} стилей, {len(CLIP_PRESETS)} каркасов, "
          f"{len(CLOSED_KEYS)} закрытых промптов")
