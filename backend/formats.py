"""Реестр РЕЖИМОВ lolq.ai и каркасов внутри них.

ЗАЧЕМ ОТДЕЛЬНЫЙ ФАЙЛ. Режим — это не новый сервис и не новый конвейер.
Иерархия Project → Track → Scene и всё, что на ней висит (персонажи с
модельками и атрибутами, стили, движки, кадры → видео → сборка, обложки,
публикация, звук), у всех режимов ОДНА И ТА ЖЕ. Различаются ровно четыре
вещи: как называется объект второго уровня, откуда берётся ритм, какие
сценарные документы нужны и каким каркасом режется содержимое. Только это
здесь и лежит. Новый режим = один объект в MODES, а не правки в трёх файлах.

ДВА СЛОЯ, как в prompts_catalog:

    MODES / SERIES_FORMATS / UGC_FORMATS  — ПУБЛИЧНОЕ. Уходит в /api/modes,
                                            в витрину, в мини-апп. Ни символа
                                            промпта.
    _SERIES_RULES / _UGC_RULES / _SEEDS   — ЗАКРЫТОЕ. Читается только
                                            backend/claude.py и уходит в
                                            модель, а не в браузер.

Разделение объектами, а не флагом внутри одного дикта: если публичное и
закрытое лежат вместе, однажды их выкинет наружу невнимательная
сериализация — и приёмы, снятые разбором чужих роликов, окажутся в бандле.

СВЯЗЬ С БАЗОЙ:
    mode.kinds      → Project.kind          (album|single|ugc|series)
    format key      → Track.format_key
    season_beats    → Doc(kind="beatsheet") → Track.season_no/episode_no
    episode_beats   → Doc(kind="script")    → Scene.act
"""
from __future__ import annotations

# ─────────────────────────────────────────────────────────────────────────────
# РЕЖИМЫ
#
# `steps` — КОНТЕКСТ РЕЖИМА, а не «этапы клипа»: у rap clips это 5 шагов, у
# сериала 6 (добавляется сезонный слой), у UGC 4. Подписи шагов НЕ здесь —
# они i18n-ключи фронта (modes.<id>.steps.<step>), иначе перевод режимов
# расползётся между сервером и словарём.
#
# `panel` — data-nav-stage разметки. Значения обязаны совпадать с реальными
# панелями index.html: docs | chars | tracks | setup | board | anim.
# Спека docs/navigation.md разъехалась с кодом ровно на этом месте — там
# шаги ссылались на панели plot и final, которых в разметке уже нет.
# ─────────────────────────────────────────────────────────────────────────────

MODES = [
    {
        "id": "clip",
        "icon": "🎬",
        "kinds": ["album", "single"],
        "default_kind": "album",
        "object": "track",            # ключ i18n: modes.object.track
        "needs_audio": True,
        "needs_lyrics": True,
        "format_catalog": "clip",     # каркасы берутся из prompts_catalog.CLIP_PRESETS
        "scenes": {"min": 12, "typ": 30, "max": 45, "slot": [2, 10]},
        "docs": [],                   # сюжет клипа живёт в Project.story
        "track_docs": [],
        "group_by": "",
        "steps": [
            {"id": "story",  "num": 1, "icon": "✍",  "scope": "project", "panel": "story"},
            {"id": "chars",  "num": 2, "icon": "🎭", "scope": "project", "panel": "chars"},
            {"id": "tracks", "num": 3, "icon": "🎵", "scope": "project", "panel": "tracks", "pane": "setup"},
            {"id": "board",  "num": 4, "icon": "🎞", "scope": "track",   "panel": "tracks", "pane": "board"},
            {"id": "anim",   "num": 5, "icon": "▶",  "scope": "track",   "panel": "tracks", "pane": "anim"},
        ],
    },
    {
        "id": "ugc",
        "icon": "📱",
        "kinds": ["ugc"],
        "default_kind": "ugc",
        "object": "reel",
        # Ритм даёт не дорожка, а слоты: 5–8 секунд на кадр. Аудио не нужно,
        # лирики нет — есть реплика блогера, она же озвучка.
        "needs_audio": False,
        "needs_lyrics": False,
        "format_catalog": "ugc",
        "scenes": {"min": 6, "typ": 8, "max": 10, "slot": [5, 8]},
        "docs": ["persona"],
        "track_docs": ["brief"],
        "group_by": "",
        "steps": [
            {"id": "persona", "num": 1, "icon": "🙋", "scope": "project", "panel": "docs"},
            {"id": "chars",   "num": 2, "icon": "🎭", "scope": "project", "panel": "chars"},
            {"id": "reels",   "num": 3, "icon": "📋", "scope": "project", "panel": "tracks", "pane": "setup"},
            {"id": "board",   "num": 4, "icon": "🎞", "scope": "track",   "panel": "tracks", "pane": "board"},
            {"id": "anim",    "num": 5, "icon": "▶",  "scope": "track",   "panel": "tracks", "pane": "anim"},
        ],
    },
    {
        "id": "series",
        "icon": "📺",
        "kinds": ["series"],
        "default_kind": "series",
        "object": "episode",
        "needs_audio": False,
        "needs_lyrics": False,
        "format_catalog": "series",
        # Серия 5–8 минут ≈ 50–80 сцен по 6 секунд. Верх намеренно не 140:
        # 140 сцен на Seedance 2 Mini — это 3080 очков, почти весь месячный
        # PRO MAX на одну серию. Ограничение тут денежное, а не техническое,
        # и честнее показать его до нажатия, чем после списания.
        "scenes": {"min": 30, "typ": 60, "max": 100, "slot": [3, 10]},
        "docs": ["logline", "synopsis", "arc", "beatsheet"],
        "track_docs": ["script"],
        "group_by": "season_no",
        "steps": [
            {"id": "bible",   "num": 1, "icon": "📖", "scope": "project", "panel": "docs"},
            {"id": "chars",   "num": 2, "icon": "🎭", "scope": "project", "panel": "chars"},
            {"id": "season",  "num": 3, "icon": "🗓",  "scope": "project", "panel": "docs"},
            {"id": "episode", "num": 4, "icon": "📝", "scope": "project", "panel": "tracks", "pane": "setup"},
            {"id": "board",   "num": 5, "icon": "🎞", "scope": "track",   "panel": "tracks", "pane": "board"},
            {"id": "anim",    "num": 6, "icon": "▶",  "scope": "track",   "panel": "tracks", "pane": "anim"},
        ],
    },
]

_MODE_BY_ID = {m["id"]: m for m in MODES}
_MODE_BY_KIND = {k: m for m in MODES for k in m["kinds"]}

# Виды проектов для окна «новый проект»: kind → режим.
PROJECT_KINDS = ["album", "single", "ugc", "series"]

# Все виды документов, которые вообще бывают. Валидатор роутов сверяется с
# этим множеством, чтобы в docs.kind не приезжала произвольная строка.
DOC_KINDS = ("logline", "synopsis", "arc", "beatsheet", "script",
             "recap", "brief", "persona", "location")

# Акты серии. Порядок значим: по нему сортируются сцены и рисуются подписи.
ACTS = ("cold_open", "act1", "act2", "act3", "tag")


def mode_of_kind(kind: str) -> dict:
    """Режим по виду проекта. Неизвестный вид — это старый проект: клип."""
    return _MODE_BY_KIND.get(str(kind or "").strip()) or _MODE_BY_ID["clip"]


def mode(mode_id: str) -> dict | None:
    return _MODE_BY_ID.get(str(mode_id or "").strip())


def norm_kind(kind: str) -> str:
    k = str(kind or "").strip()
    return k if k in _MODE_BY_KIND else "album"


# ─────────────────────────────────────────────────────────────────────────────
# КАРКАСЫ СЕРИАЛА. Двухуровневые, по образцу prompts_catalog.CLIP_PRESETS:
# `season_beats` — арка сезона (что происходит от серии к серии),
# `episode_beats` — структура одной серии (акты).
#
# `share` — ДОЛЯ, а не число: у сезона доля серий, у серии доля хронометража.
# Сумма внутри каждого списка = 1.0, это проверяет validate().
# ─────────────────────────────────────────────────────────────────────────────

SERIES_FORMATS = [
    {
        "key": "procedural",
        "label": {"en": "Case of the week", "ru": "Дело недели"},
        "logline": {
            "en": "One closed case per episode, one crack in the hero that never closes.",
            "ru": "Одно закрытое дело за серию и одна трещина в герое, которая не закрывается.",
        },
        "episodes": {"min": 6, "typ": 8, "max": 12},
        "styles_fit": ["noir", "cinema", "dreamclad"],
        "engines": ["seedance-2-mini", "kling-3.0"],
        "needs_recap": True,
        "season_beats": [
            {"key": "pilot", "share": 0.12,
             "en": "Pilot: the world, the rules and the price of breaking them.",
             "ru": "Пилот: мир, правила и цена их нарушения."},
            {"key": "cases", "share": 0.50,
             "en": "Cases pile up. Each one takes something small from the hero.",
             "ru": "Дела копятся. Каждое забирает у героя что-то небольшое."},
            {"key": "midpoint", "share": 0.13,
             "en": "One case turns out to be personal. The method stops working.",
             "ru": "Одно дело оказывается личным. Метод перестаёт работать."},
            {"key": "finale", "share": 0.25,
             "en": "The season closes and leaves one door open.",
             "ru": "Сезон закрывается и оставляет одну дверь открытой.",
             },
        ],
        "episode_beats": [
            {"key": "cold_open", "share": 0.10, "act": "cold_open", "shot": "establishing",
             "en": "Cold open before the titles: the event that starts the case.",
             "ru": "Холодное открытие до титров: событие, с которого начинается дело."},
            {"key": "act1", "share": 0.25, "act": "act1", "shot": "medium",
             "en": "Act 1: the hero takes the case and names the obvious answer.",
             "ru": "Акт 1: герой берётся за дело и называет очевидный ответ."},
            {"key": "act2", "share": 0.35, "act": "act2", "shot": "close-up",
             "en": "Act 2: the obvious answer collapses, the cost goes up.",
             "ru": "Акт 2: очевидный ответ рушится, цена растёт."},
            {"key": "act3", "share": 0.22, "act": "act3", "shot": "wide",
             "en": "Act 3: the case closes, the hero pays for it.",
             "ru": "Акт 3: дело закрывается, герой за это платит."},
            {"key": "tag", "share": 0.08, "act": "tag", "shot": "close-up",
             "en": "Tag: one shot that makes the next episode necessary.",
             "ru": "Тег: один кадр, из-за которого следующая серия обязана быть."},
        ],
    },
    {
        "key": "serial_drama",
        "label": {"en": "Serial drama", "ru": "Сквозная драма"},
        "logline": {
            "en": "One story cut into episodes. Nothing closes until the finale.",
            "ru": "Одна история, разрезанная на серии. До финала не закрывается ничего.",
        },
        "episodes": {"min": 6, "typ": 8, "max": 10},
        "styles_fit": ["cinema", "noir", "dreamclad", "shinkai"],
        "engines": ["seedance-2-mini", "kling-3.0", "seedance-2-5-480"],
        "needs_recap": True,
        "season_beats": [
            {"key": "ordinary", "share": 0.14,
             "en": "The world before. Shown so that losing it hurts.",
             "ru": "Мир до. Показан так, чтобы его было жалко потерять."},
            {"key": "break", "share": 0.18,
             "en": "The break-in event. There is no way back from here.",
             "ru": "Событие-слом. Отсюда дороги назад уже нет."},
            {"key": "rise", "share": 0.28,
             "en": "The hero learns the new rules and starts to win by them.",
             "ru": "Герой осваивает новые правила и начинает по ним выигрывать."},
            {"key": "fall", "share": 0.22,
             "en": "The rules turn on him. The false victory costs the most.",
             "ru": "Правила оборачиваются против него. Ложная победа стоит дороже всего."},
            {"key": "finale", "share": 0.18,
             "en": "The finale answers the pilot's question and asks a bigger one.",
             "ru": "Финал отвечает на вопрос пилота и задаёт вопрос крупнее.",
             },
        ],
        "episode_beats": [
            {"key": "recap_hook", "share": 0.08, "act": "cold_open", "shot": "close-up",
             "en": "Open on the consequence of the previous episode, not on a recap.",
             "ru": "Открываемся последствием прошлой серии, а не пересказом."},
            {"key": "act1", "share": 0.24, "act": "act1", "shot": "wide",
             "en": "Act 1: what the hero wants this week and who stands in the way.",
             "ru": "Акт 1: чего герой хочет на этой неделе и кто стоит поперёк."},
            {"key": "act2", "share": 0.36, "act": "act2", "shot": "medium",
             "en": "Act 2: two lines collide, the secret leaks to the wrong person.",
             "ru": "Акт 2: две линии сталкиваются, секрет утекает не тому человеку."},
            {"key": "act3", "share": 0.22, "act": "act3", "shot": "close-up",
             "en": "Act 3: a decision that cannot be taken back.",
             "ru": "Акт 3: решение, которое нельзя отменить."},
            {"key": "tag", "share": 0.10, "act": "tag", "shot": "establishing",
             "en": "Tag: cliffhanger, held one beat too long.",
             "ru": "Тег: клиффхэнгер, удержанный на один такт дольше нужного."},
        ],
    },
    {
        "key": "anthology",
        "label": {"en": "Anthology", "ru": "Антология"},
        "logline": {
            "en": "New people every episode, same world and same rule that breaks them.",
            "ru": "Каждую серию новые люди, тот же мир и то же правило, которое их ломает.",
        },
        "episodes": {"min": 4, "typ": 6, "max": 10},
        "styles_fit": ["cinema", "noir", "clay", "ghibli"],
        "engines": ["seedance-2-mini", "kling-3.0"],
        "needs_recap": False,
        "season_beats": [
            {"key": "rule", "share": 0.20,
             "en": "Episode one states the rule of the world in the plainest way.",
             "ru": "Первая серия проговаривает правило мира максимально просто."},
            {"key": "variations", "share": 0.55,
             "en": "Each episode breaks the same rule from a different side.",
             "ru": "Каждая серия ломает то же правило с другой стороны."},
            {"key": "mirror", "share": 0.25,
             "en": "The last episode rhymes with the first and answers it.",
             "ru": "Последняя серия рифмуется с первой и отвечает ей.",
             },
        ],
        "episode_beats": [
            {"key": "cold_open", "share": 0.12, "act": "cold_open", "shot": "establishing",
             "en": "A stranger, a place, and the rule already in force.",
             "ru": "Незнакомец, место и уже действующее правило."},
            {"key": "act1", "share": 0.26, "act": "act1", "shot": "medium",
             "en": "Act 1: they think the rule does not apply to them.",
             "ru": "Акт 1: они думают, что правило не про них."},
            {"key": "act2", "share": 0.34, "act": "act2", "shot": "close-up",
             "en": "Act 2: it applies. They try to buy their way out.",
             "ru": "Акт 2: оно про них. Они пробуют откупиться."},
            {"key": "act3", "share": 0.20, "act": "act3", "shot": "wide",
             "en": "Act 3: the price is paid in full, on camera.",
             "ru": "Акт 3: цена платится полностью, в кадре."},
            {"key": "tag", "share": 0.08, "act": "tag", "shot": "extreme close-up",
             "en": "Tag: the same object as in the cold open, in another hand.",
             "ru": "Тег: тот же предмет, что в открытии, но в другой руке."},
        ],
    },
    {
        "key": "sitcom",
        "label": {"en": "Sitcom", "ru": "Ситком"},
        "logline": {
            "en": "A closed set, two lines per episode, and people who never learn.",
            "ru": "Замкнутая площадка, две линии за серию и люди, которые ничему не учатся.",
        },
        "episodes": {"min": 6, "typ": 10, "max": 12},
        "styles_fit": ["clay", "ghibli", "katsumi", "cinema"],
        "engines": ["grok", "seedance-2-mini"],
        "needs_recap": False,
        "season_beats": [
            {"key": "setup", "share": 0.15,
             "en": "The set, the roles and who annoys whom.",
             "ru": "Площадка, роли и кто кого раздражает."},
            {"key": "engine", "share": 0.55,
             "en": "The same characters, new misunderstandings. Nobody changes.",
             "ru": "Те же герои, новые недоразумения. Никто не меняется."},
            {"key": "wobble", "share": 0.15,
             "en": "One of them almost changes. It is put back by the end.",
             "ru": "Один почти меняется. К концу его возвращают на место."},
            {"key": "finale", "share": 0.15,
             "en": "A finale that resets the set for the next season.",
             "ru": "Финал, который обнуляет площадку под следующий сезон.",
             },
        ],
        "episode_beats": [
            {"key": "cold_open", "share": 0.12, "act": "cold_open", "shot": "medium",
             "en": "Cold open: a joke that has nothing to do with the plot.",
             "ru": "Холодное открытие: шутка, никак не связанная с сюжетом."},
            {"key": "a_line", "share": 0.34, "act": "act1", "shot": "medium",
             "en": "A-line: the main misunderstanding starts.",
             "ru": "Линия А: запускается главное недоразумение."},
            {"key": "b_line", "share": 0.24, "act": "act2", "shot": "close-up",
             "en": "B-line: a small parallel disaster with the second pair.",
             "ru": "Линия Б: маленькая параллельная катастрофа со второй парой."},
            {"key": "collide", "share": 0.22, "act": "act3", "shot": "wide",
             "en": "Both lines collide in one room at the worst moment.",
             "ru": "Обе линии сталкиваются в одной комнате в худший момент."},
            {"key": "tag", "share": 0.08, "act": "tag", "shot": "close-up",
             "en": "Tag: the smallest character gets the last word.",
             "ru": "Тег: последнее слово достаётся самому мелкому персонажу."},
        ],
    },
    {
        "key": "mockumentary",
        "label": {"en": "Mockumentary", "ru": "Мокьюментари"},
        "logline": {
            "en": "A fake documentary: talking heads to camera, b-roll, a voice that lies.",
            "ru": "Фальшивый документальный фильм: интервью в камеру, б-ролл и закадровый голос, который врёт.",
        },
        "episodes": {"min": 4, "typ": 8, "max": 12},
        "styles_fit": ["cinema", "noir", "punkrf"],
        "engines": ["seedance-2-mini", "grok", "kling-3.0"],
        "needs_recap": False,
        # Лучший стартовый формат на наших движках: интервью в камеру и
        # закадровый голос обходят и отсутствие липсинка, и невозможность
        # держать двух говорящих в одном кадре. Ограничения становятся
        # приёмом жанра, а не заметным браком.
        "season_beats": [
            {"key": "subject", "share": 0.18,
             "en": "Who we are filming and why they agreed.",
             "ru": "Кого мы снимаем и почему он согласился."},
            {"key": "access", "share": 0.42,
             "en": "The crew gets deeper access than anyone intended.",
             "ru": "Съёмочная группа получает доступ глубже, чем кто-либо планировал."},
            {"key": "turn", "share": 0.22,
             "en": "The subject starts performing for the camera and gives himself away.",
             "ru": "Герой начинает играть на камеру и выдаёт себя."},
            {"key": "finale", "share": 0.18,
             "en": "The footage says one thing, the voiceover another.",
             "ru": "Плёнка говорит одно, закадровый голос — другое.",
             },
        ],
        "episode_beats": [
            {"key": "cold_open", "share": 0.12, "act": "cold_open", "shot": "medium",
             "en": "Straight to camera: the claim of the episode, stated confidently.",
             "ru": "Прямо в камеру: заявление серии, произнесённое уверенно."},
            {"key": "act1", "share": 0.26, "act": "act1", "shot": "wide",
             "en": "B-roll of the claim being true.",
             "ru": "Б-ролл, на котором заявление выглядит правдой."},
            {"key": "act2", "share": 0.34, "act": "act2", "shot": "close-up",
             "en": "The second interview contradicts the first.",
             "ru": "Второе интервью противоречит первому."},
            {"key": "act3", "share": 0.20, "act": "act3", "shot": "medium",
             "en": "The camera catches what nobody meant to give it.",
             "ru": "Камера ловит то, что ей никто не собирался отдавать."},
            {"key": "tag", "share": 0.08, "act": "tag", "shot": "close-up",
             "en": "Tag: the subject asks to cut that bit. It stays in.",
             "ru": "Тег: герой просит это вырезать. Это остаётся."},
        ],
    },
]

# ─────────────────────────────────────────────────────────────────────────────
# КАРКАСЫ UGC. ИИ-блогер — это НЕ формат, это ПЕРСОНАЖ: один и тот же
# виртуальный человек снимает и обзор, и распаковку, и говорящую голову.
# Поэтому режим один, а форматов — список; «доктор» из старого прототипа
# режимов живёт здесь как формат `expert`, а не отдельным режимом.
# ─────────────────────────────────────────────────────────────────────────────

UGC_FORMATS = [
    {
        "key": "review",
        "label": {"en": "Review", "ru": "Обзор"},
        "logline": {"en": "Honest-looking verdict on one product.",
                    "ru": "Похожий на честный вердикт об одном продукте."},
        "slots": {"min": 6, "typ": 8, "max": 10},
        "engines": ["grok", "seedance-2-mini"],
        "beats": [
            {"key": "hook", "share": 0.12, "shot": "close-up",
             "en": "1.5 seconds: the promise or the objection, said out loud.",
             "ru": "Полторы секунды: обещание или возражение, произнесённое вслух."},
            {"key": "problem", "share": 0.16, "shot": "medium",
             "en": "The problem, shown as a scene and not described.",
             "ru": "Проблема, показанная сценой, а не описанная."},
            {"key": "product", "share": 0.16, "shot": "extreme close-up",
             "en": "The product in hands. It must already be in the frame.",
             "ru": "Продукт в руках. Он обязан уже быть в кадре."},
            {"key": "proof", "share": 0.32, "shot": "medium",
             "en": "Three concrete details, one per shot, no adjectives.",
             "ru": "Три конкретики, по одной на кадр, без прилагательных."},
            {"key": "verdict", "share": 0.14, "shot": "close-up",
             "en": "Verdict, including who this is not for.",
             "ru": "Вердикт, включая то, кому это не подойдёт."},
            {"key": "cta", "share": 0.10, "shot": "medium",
             "en": "Back to the promise of the first shot.",
             "ru": "Возврат к обещанию первого кадра."},
        ],
    },
    {
        "key": "unboxing",
        "label": {"en": "Unboxing", "ru": "Распаковка"},
        "logline": {"en": "The box, the opening and the first real reaction.",
                    "ru": "Коробка, вскрытие и первая настоящая реакция."},
        "slots": {"min": 6, "typ": 7, "max": 9},
        "engines": ["grok", "seedance-2-mini"],
        "beats": [
            {"key": "box", "share": 0.14, "shot": "medium",
             "en": "The sealed box on the table, hands already on it.",
             "ru": "Запечатанная коробка на столе, руки уже на ней."},
            {"key": "open", "share": 0.22, "shot": "extreme close-up",
             "en": "The opening itself, filmed as the main event.",
             "ru": "Само вскрытие, снятое как главное событие."},
            {"key": "reaction", "share": 0.20, "shot": "close-up",
             "en": "The face before the words.",
             "ru": "Лицо раньше слов."},
            {"key": "details", "share": 0.28, "shot": "extreme close-up",
             "en": "Details in the hands: texture, weight, what is missing.",
             "ru": "Детали в руках: фактура, вес, чего не хватает."},
            {"key": "verdict", "share": 0.16, "shot": "medium",
             "en": "Would they buy it again, in one sentence.",
             "ru": "Купил бы ещё раз — одним предложением."},
        ],
    },
    {
        "key": "talking_head",
        "label": {"en": "Talking head", "ru": "Говорящая голова"},
        "logline": {"en": "One person, one confession, one turn.",
                    "ru": "Один человек, одно признание, один поворот."},
        "slots": {"min": 5, "typ": 6, "max": 8},
        # Слабейший формат на наших движках: липсинка нет ни у одного из
        # восьми, поэтому кадровка обязана прятать рот (см. _UGC_RULES).
        "engines": ["grok", "seedance-2-mini"],
        "beats": [
            {"key": "hook", "share": 0.16, "shot": "close-up",
             "en": "A confession that costs the speaker something.",
             "ru": "Признание, которое чего-то стоит говорящему."},
            {"key": "context", "share": 0.24, "shot": "medium",
             "en": "How they got there. B-roll, not the face.",
             "ru": "Как он до этого дошёл. Б-ролл, а не лицо."},
            {"key": "turn", "share": 0.30, "shot": "medium",
             "en": "The turn: what they were wrong about.",
             "ru": "Поворот: в чём он ошибался."},
            {"key": "lesson", "share": 0.18, "shot": "close-up",
             "en": "What they do differently now, concretely.",
             "ru": "Что он делает иначе теперь, конкретно."},
            {"key": "close", "share": 0.12, "shot": "wide",
             "en": "Back to the first frame, changed.",
             "ru": "Возврат к первому кадру, но изменившемуся."},
        ],
    },
    {
        "key": "reaction",
        "label": {"en": "Reaction", "ru": "Реакция"},
        "logline": {"en": "Something happens, the face carries the video.",
                    "ru": "Что-то происходит, видео держится на лице."},
        "slots": {"min": 5, "typ": 6, "max": 8},
        "engines": ["grok", "seedance-2-mini"],
        "beats": [
            {"key": "source", "share": 0.22, "shot": "medium",
             "en": "What they are reacting to, shown fully.",
             "ru": "То, на что реагируют, показано целиком."},
            {"key": "face", "share": 0.26, "shot": "extreme close-up",
             "en": "The face. No words yet.",
             "ru": "Лицо. Слов пока нет."},
            {"key": "take", "share": 0.34, "shot": "medium",
             "en": "The take: what exactly is wrong with it.",
             "ru": "Разбор: что именно с этим не так."},
            {"key": "verdict", "share": 0.18, "shot": "close-up",
             "en": "One-line verdict, no hedging.",
             "ru": "Вердикт одной строкой, без оговорок."},
        ],
    },
    {
        "key": "expert",
        "label": {"en": "Expert explains", "ru": "Эксперт объясняет"},
        "logline": {"en": "A specialist explains the mechanism, not the conclusion.",
                    "ru": "Специалист объясняет механизм, а не вывод."},
        "slots": {"min": 6, "typ": 7, "max": 9},
        "engines": ["grok", "seedance-2-mini"],
        "beats": [
            {"key": "myth", "share": 0.16, "shot": "close-up",
             "en": "The belief almost everyone holds.",
             "ru": "Убеждение, которое разделяют почти все."},
            {"key": "mechanism", "share": 0.34, "shot": "medium",
             "en": "How it actually works, one step per shot.",
             "ru": "Как это работает на самом деле, по шагу на кадр."},
            {"key": "evidence", "share": 0.24, "shot": "extreme close-up",
             "en": "The detail you can see with your own eyes.",
             "ru": "Деталь, которую видно собственными глазами."},
            {"key": "so_what", "share": 0.16, "shot": "medium",
             "en": "What to do with this tomorrow morning.",
             "ru": "Что с этим делать завтра утром."},
            {"key": "close", "share": 0.10, "shot": "close-up",
             "en": "The myth, restated and dropped.",
             "ru": "Миф, произнесённый снова и отброшенный."},
        ],
    },
    {
        "key": "street",
        "label": {"en": "Street handoff", "ru": "Отдал незнакомцу"},
        "logline": {"en": "The main shot is a stranger's face, not the host's.",
                    "ru": "Главный кадр — лицо незнакомца, а не ведущего."},
        "slots": {"min": 6, "typ": 7, "max": 9},
        "engines": ["grok", "seedance-2-mini"],
        "beats": [
            {"key": "setup", "share": 0.18, "shot": "wide",
             "en": "The street, the object, the intent stated to camera.",
             "ru": "Улица, предмет, намерение проговорено в камеру."},
            {"key": "approach", "share": 0.20, "shot": "medium",
             "en": "The approach. Handheld, from behind the shoulder.",
             "ru": "Подход. С рук, из-за плеча."},
            {"key": "handoff", "share": 0.24, "shot": "extreme close-up",
             "en": "The handoff itself: two hands and the object.",
             "ru": "Сама передача: две руки и предмет."},
            {"key": "reaction", "share": 0.26, "shot": "close-up",
             "en": "The stranger's face. This is the video.",
             "ru": "Лицо незнакомца. Это и есть ролик."},
            {"key": "walk", "share": 0.12, "shot": "wide",
             "en": "Walking away without explaining.",
             "ru": "Уходит, ничего не объясняя."},
        ],
    },
]

_SERIES_BY_KEY = {f["key"]: f for f in SERIES_FORMATS}
_UGC_BY_KEY = {f["key"]: f for f in UGC_FORMATS}

PUBLIC_SERIES_FIELDS = ("key", "label", "logline", "episodes",
                        "styles_fit", "engines", "needs_recap")
PUBLIC_UGC_FIELDS = ("key", "label", "logline", "slots", "engines")


# ─────────────────────────────────────────────────────────────────────────────
# ЗАКРЫТОЕ: правила, которые уходят в модель. Наружу не отдаются никогда.
#
# Правила UGC сняты с работающего конвейера матрицы BIOAURA
# (infra/agents/matrix_idea_worker.py, UGC_STORYBOARD_SPEC) и очищены от
# брендовой специфики: сервис международный и публичный, банков постов и
# формулировок «поддерживает / не лечит» здесь быть не может.
# ─────────────────────────────────────────────────────────────────────────────

_UGC_RULES = """ЖЁСТКИЕ ПРАВИЛА UGC-РОЛИКА (нарушение = ролик рассыпается на склейке):

1. ЕДИНОЕ ДОСЛОВНОЕ ОПИСАНИЕ. Внешность блогера и описание локации
   повторяются В КАЖДОМ image_prompt СЛОВО В СЛОВО, одной и той же формулой.
   Не «a young woman» и не «the same kitchen», а полная формула целиком.
   Между кадрами НЕ МЕНЯЮТСЯ: лицо, причёска, одежда, маникюр, интерьер,
   свет, посуда, предметы на фоне. Меняются ТОЛЬКО действие, поза и ракурс.
2. НОВАЯ КРУПНОСТЬ В КАЖДОМ СЛОТЕ. Два одинаковых плана подряд запрещены.
3. КРЮЧОК В ПЕРВЫЕ ПОЛТОРЫ СЕКУНДЫ. Первый кадр обещает, последний к этому
   обещанию возвращается.
4. РЕПЛИКА 12–20 СЛОВ на слот 5–8 секунд. Длиннее — герой продолжает молча
   шевелить губами полкадра; короче — кадр провисает.
5. MOTION SELF-CONTAINED. Движок анимирует каждый кадр ИЗОЛИРОВАННО, не видя
   соседних. Слова same / again / continues / previous / as before в
   motion_prompt запрещены. Описывай только движение внутри этого кадра.
6. ПРЕДМЕТ ТОЛЬКО ИЗ КАДРА. В motion_prompt нельзя вносить предмет, которого
   нет в image_prompt того же кадра: анимация его не создаст, модель
   дорисует чужой.
7. МАКСИМУМ ДВА ЧЕЛОВЕКА В КАДРЕ, говорит всегда один.
8. ЛИПСИНКА НЕТ НИ У ОДНОГО ДВИЖКА. Кадры с длинной репликой снимай так,
   чтобы рот не был крупно: средний план, со спины, руки и предмет, б-ролл,
   профиль, реакция слушающего. Крупный план лица — только под короткую
   фразу или молчание.
9. Формат вертикальный 9:16, съёмка любительская: смартфон, живой свет,
   лёгкая неровность кадра. Никакой рекламной вылизанности."""

_SERIES_RULES = """ЖЁСТКИЕ ПРАВИЛА СЕРИИ (ограничения генеративного видео, не стилистика):

1. ОДИН ГОВОРЯЩИЙ В КАДРЕ. Двое в одном кадре расплываются: движки держат по
   референсу одного персонажа. Диалог монтируй восьмёркой — реплика, ответ,
   реакция; каждая реплика в своём кадре.
2. ЛИПСИНКА НЕТ. Длинную реплику снимай не крупным планом лица: средний,
   со спины, руки, предмет, б-ролл, слушающий. Крупный план лица — под
   молчание и короткие фразы.
3. РЕПЛИКА 12–20 СЛОВ на кадр в 6 секунд. Это физический предел слота.
4. ГЕРОЙ ОПИСЫВАЕТСЯ ДОСЛОВНО в каждом кадре, где он есть, — одной и той же
   формулой из его карточки. Между кадрами у него не меняются лицо,
   причёска, одежда и приметы.
5. КАЖДУЮ СМЕНУ ЛОКАЦИИ открывай establishing-планом.
6. АКТ ОБЯЗАТЕЛЕН у каждого кадра: cold_open | act1 | act2 | act3 | tag.
7. MOTION SELF-CONTAINED: движок не видит соседних кадров, ссылок на них
   (same, again, continues, previous) быть не может.
8. Серия обязана заканчиваться так, чтобы следующая была нужна."""

# Режиссёрский seed каркаса: он же ответ на «что мы вообще снимаем».
_SEEDS: dict[str, dict] = {
    "procedural": {
        "note": "Дело закрывается внутри серии, трещина в герое — нет. Каждая "
                "серия обязана забрать у героя что-то, чего он не вернёт.",
    },
    "serial_drama": {
        "note": "Ни одна линия не закрывается внутри серии. Серия — это акт, "
                "а не история: она обязана обрываться на решении.",
    },
    "anthology": {
        "note": "Герои новые, мир и правило те же. Связь между сериями — "
                "предмет, место или фраза, а не персонаж.",
    },
    "sitcom": {
        "note": "Две линии, A и B, сталкиваются в финале в одной комнате. "
                "Никто не меняется: к следующей серии площадка обнулена.",
    },
    "mockumentary": {
        "note": "Интервью в камеру и закадровый голос. Плёнка и голос обязаны "
                "противоречить друг другу хотя бы раз за серию.",
    },
    "review": {"note": "Три конкретики вместо десяти прилагательных. Вердикт "
                       "обязан назвать, кому это НЕ подойдёт."},
    "unboxing": {"note": "Главное событие — вскрытие, а не продукт. Лицо "
                         "показываем раньше слов."},
    "talking_head": {"note": "Признание, которое чего-то стоит говорящему. "
                             "Без поворота это не ролик, а монолог."},
    "reaction": {"note": "Видео держится на лице, а не на исходнике. "
                         "Исходник показываем ровно один раз."},
    "expert": {"note": "Объясняем механизм, а не вывод. Ни одного утверждения "
                       "без наблюдаемой детали."},
    "street": {"note": "Главный кадр — лицо незнакомца. Ведущий в кадре "
                       "нужен ровно дважды: в начале и в конце."},
}


# ── публичное API каталога ───────────────────────────────────────────────────

def _localize(row: dict, fields: tuple, lang: str) -> dict:
    out = dict(row)
    if lang in ("en", "ru"):
        for f in fields:
            if isinstance(out.get(f), dict) and ("en" in out[f] or "ru" in out[f]):
                out[f] = out[f].get(lang, out[f].get("en", ""))
    return out


def _beats(rows: list, lang: str) -> list:
    out = []
    for b in rows:
        item = {"key": b["key"], "share": b["share"]}
        if b.get("shot"):
            item["shot"] = b["shot"]
        if b.get("act"):
            item["act"] = b["act"]
        item["text"] = b.get(lang, b.get("en", "")) if lang in ("en", "ru") else \
            {"en": b.get("en", ""), "ru": b.get("ru", "")}
        out.append(item)
    return out


def public_series_format(key: str, *, lang: str = "") -> dict | None:
    f = _SERIES_BY_KEY.get(key)
    if not f:
        return None
    out = _localize({k: f[k] for k in PUBLIC_SERIES_FIELDS if k in f},
                    ("label", "logline"), lang)
    out["season_beats"] = _beats(f["season_beats"], lang)
    out["episode_beats"] = _beats(f["episode_beats"], lang)
    return out


def public_ugc_format(key: str, *, lang: str = "") -> dict | None:
    f = _UGC_BY_KEY.get(key)
    if not f:
        return None
    out = _localize({k: f[k] for k in PUBLIC_UGC_FIELDS if k in f},
                    ("label", "logline"), lang)
    out["beats"] = _beats(f["beats"], lang)
    return out


def public_formats(catalog: str, *, lang: str = "") -> list[dict]:
    if catalog == "series":
        return [public_series_format(f["key"], lang=lang) for f in SERIES_FORMATS]
    if catalog == "ugc":
        return [public_ugc_format(f["key"], lang=lang) for f in UGC_FORMATS]
    return []


def public_modes(*, lang: str = "") -> list[dict]:
    """Реестр режимов наружу. Подписей режимов и шагов здесь НЕТ — они
    i18n-ключи фронта (modes.<id>.*), иначе перевод расползётся между
    сервером и словарём."""
    out = []
    for m in MODES:
        row = {k: m[k] for k in
               ("id", "icon", "kinds", "default_kind", "object", "needs_audio",
                "needs_lyrics", "format_catalog", "scenes", "docs",
                "track_docs", "group_by", "steps")}
        row["formats"] = public_formats(m["format_catalog"], lang=lang)
        out.append(row)
    return out


# ── закрытое: зовётся только из бэкенда ──────────────────────────────────────

def format_spec(catalog: str, key: str) -> dict | None:
    """ПОЛНЫЙ каркас со всеми битами — для сборки промпта, не для HTTP."""
    if catalog == "series":
        return _SERIES_BY_KEY.get(key)
    if catalog == "ugc":
        return _UGC_BY_KEY.get(key)
    return None


def default_format(catalog: str) -> str:
    if catalog == "series":
        return "mockumentary"
    if catalog == "ugc":
        return "review"
    return ""


def rules(catalog: str) -> str:
    """Правила режима, которые уходят в системный промпт."""
    return {"ugc": _UGC_RULES, "series": _SERIES_RULES}.get(catalog, "")


def seed(key: str) -> dict:
    return dict(_SEEDS.get(key) or {"note": ""})


def beats_block(catalog: str, key: str, which: str, lang: str = "ru") -> str:
    """Каркас строкой для промпта: «доля — что происходит»."""
    spec = format_spec(catalog, key)
    if not spec:
        return ""
    rows = spec.get(which) or []
    lines = []
    for b in rows:
        pct = int(round(b["share"] * 100))
        act = f" [{b['act']}]" if b.get("act") else ""
        shot = f" ({b['shot']})" if b.get("shot") else ""
        lines.append(f"- {b['key']}{act} — {pct}%{shot}: {b.get(lang) or b.get('en', '')}")
    return "\n".join(lines)


# ─────────────────────────────────────────────────────────────────────────────
# САМОПРОВЕРКА. Гоняется тестом и вручную: каркас с долями, которые не
# складываются в единицу, молча растянет или обрежет серию.
# ─────────────────────────────────────────────────────────────────────────────

SHOT_SIZES = {"extreme close-up", "close-up", "medium", "wide", "establishing"}


def validate() -> list[str]:
    err: list[str] = []
    panels = {"story", "chars", "tracks", "docs"}
    for m in MODES:
        if m["format_catalog"] not in ("clip", "series", "ugc"):
            err.append(f"режим {m['id']}: неизвестный каталог каркасов")
        for s in m["steps"]:
            if s["panel"] not in panels:
                err.append(f"режим {m['id']}/{s['id']}: панель {s['panel']} вне разметки")
            if s["scope"] not in ("project", "track"):
                err.append(f"режим {m['id']}/{s['id']}: неизвестный scope {s['scope']}")
        for d in m["docs"] + m["track_docs"]:
            if d not in DOC_KINDS:
                err.append(f"режим {m['id']}: документ {d} вне DOC_KINDS")

    kinds = [k for m in MODES for k in m["kinds"]]
    if len(kinds) != len(set(kinds)):
        err.append("один вид проекта принадлежит двум режимам")

    for f in SERIES_FORMATS:
        for which in ("season_beats", "episode_beats"):
            total = round(sum(b["share"] for b in f[which]), 6)
            if total != 1.0:
                err.append(f"{f['key']}/{which}: сумма долей {total}, должна быть 1.0")
        for b in f["episode_beats"]:
            if b.get("act") not in ACTS:
                err.append(f"{f['key']}/{b['key']}: акт {b.get('act')} вне ACTS")
            if b.get("shot") not in SHOT_SIZES:
                err.append(f"{f['key']}/{b['key']}: крупность {b.get('shot')} вне словаря")
        if f["key"] not in _SEEDS:
            err.append(f"{f['key']}: нет seed")

    for f in UGC_FORMATS:
        total = round(sum(b["share"] for b in f["beats"]), 6)
        if total != 1.0:
            err.append(f"{f['key']}: сумма долей {total}, должна быть 1.0")
        for b in f["beats"]:
            if b.get("shot") not in SHOT_SIZES:
                err.append(f"{f['key']}/{b['key']}: крупность {b.get('shot')} вне словаря")
        if f["key"] not in _SEEDS:
            err.append(f"{f['key']}: нет seed")

    # Главная проверка: закрытые правила не видны в публичной выдаче.
    blob = repr(public_modes())
    for name, text in (("ugc", _UGC_RULES), ("series", _SERIES_RULES)):
        if text[:60] in blob:
            err.append(f"УТЕЧКА: правила {name} видны в публичном реестре")
    return err


if __name__ == "__main__":
    problems = validate()
    print("\n".join(problems) if problems else
          f"реестр цел: {len(MODES)} режима, {len(SERIES_FORMATS)} каркасов "
          f"сериала, {len(UGC_FORMATS)} форматов UGC")
