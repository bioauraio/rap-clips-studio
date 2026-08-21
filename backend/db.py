"""SQLite-хранилище проекта. Личный однопользовательский инструмент —
без миграционного фреймворка: схема создаётся при старте, если её нет."""
from __future__ import annotations

import os
from datetime import datetime, timezone

from sqlalchemy import (
    Boolean, Column, DateTime, ForeignKey, Integer, String, Text, create_engine,
)
from sqlalchemy.orm import DeclarativeBase, relationship, sessionmaker

DB_PATH = os.environ.get("DB_PATH", "/data/rapclips.db")
os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)

engine = create_engine(f"sqlite:///{DB_PATH}", connect_args={"check_same_thread": False})
SessionLocal = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)


def now() -> datetime:
    return datetime.now(timezone.utc)


class Base(DeclarativeBase):
    pass


class User(Base):
    """Пользователь публичного сервиса qlolvideo.

    login без UNIQUE-констрейнта: базу не пересоздаём (мягкая миграция ALTER'ом),
    уникальность проверяется кодом в момент установки логина (/api/register)."""
    __tablename__ = "users"
    id = Column(Integer, primary_key=True)
    created_at = Column(DateTime, default=now)
    name = Column(String, nullable=False, default="")
    login = Column(String, nullable=False, default="")
    password_hash = Column(String, nullable=False, default="")
    is_admin = Column(Boolean, nullable=False, default=False)
    # Очки генераций: защита кошелька владельца — генерации идут через его
    # подписки. Гость стартует с месячной нормы плана free (держи это число
    # равным PLANS["free"]["points"] в main.py), админ — с бесконечностью.
    gen_points = Column(Integer, nullable=False, default=120)
    # Тариф: free — видео только через Grok (наша подписка, бесплатно),
    # pro — открывается Seedance (платные кредиты владельца сервиса).
    plan = Column(String, nullable=False, default="free")
    # Период подписки: month | year. Нужен и для витрины (какой вариант
    # оплачен), и для продления — годовую нельзя продлевать на 30 дней.
    plan_period = Column(String, nullable=False, default="month")
    # Ступень объёма верхнего тарифа (ULTRA): "" | u1..u4. ПУСТАЯ СТРОКА
    # читается как первая ступень — поэтому все, кто уже купил STUDIO,
    # остаются валидны без единого UPDATE. Список ступеней — PLAN_TIERS
    # в main.py, сюда попадает только выбранный id.
    plan_tier = Column(String, nullable=False, default="")
    # Запланированное ПОНИЖЕНИЕ ступени: применяется в момент продления.
    # Понижать сразу нельзя — за текущий месяц уже заплачено.
    plan_tier_next = Column(String, nullable=False, default="")
    # Помесячная выдача очков ГОДОВОЙ подписки. Раньше год начислял норму ×12
    # разом, и годовой ULTRA означал бы $15600 обязательства в день оплаты.
    # Теперь оплата даёт первый транш, остальные 11 капают раз в PLAN_DAYS.
    points_drip_left = Column(Integer, nullable=False, default=0)
    points_drip_size = Column(Integer, nullable=False, default=0)
    points_drip_at = Column(DateTime, nullable=True)
    # Онбординг «первый клип»: csv пройденных шагов и момент первого собранного
    # клипа. СЕРВЕРНОЕ, а не localStorage: человек начинает на десктопе и
    # продолжает с телефона, и чеклист обязан помнить, где он остановился.
    onboarding = Column(String, nullable=False, default="")
    onboarding_done = Column(DateTime, nullable=True)
    # Внешние входы: аккаунт создаётся и опознаётся по Telegram-ID или Яндекс-ID,
    # пароль при этом не нужен. Уникальность проверяется кодом (мягкие миграции
    # не умеют добавлять UNIQUE к существующей таблице).
    tg_id = Column(String, nullable=False, default="")
    tg_username = Column(String, nullable=False, default="")
    yandex_id = Column(String, nullable=False, default="")
    google_id = Column(String, nullable=False, default="")
    email = Column(String, nullable=False, default="")
    # Подписка ЮKassa: способ оплаты для автосписания и дата продления.
    pay_method_id = Column(String, nullable=False, default="")
    plan_until = Column(DateTime, nullable=True)
    autopay = Column(Boolean, nullable=False, default=True)
    # Подписка Stripe (международная аудитория). Продление там делает сам
    # Stripe и присылает invoice.paid — своего автосписания для него нет,
    # поэтому часовой воркер такие подписки не трогает (см. main.py).
    stripe_customer_id = Column(String, nullable=False, default="")
    stripe_subscription_id = Column(String, nullable=False, default="")
    # Подписка Telegram Stars. Храним charge_id ПЕРВОГО платежа: только им
    # Telegram даёт отменить подписку (editUserStarSubscription), последний не
    # подходит. Пока колонки не было, отменить со своей стороны было нечем.
    # Заодно это признак «продлевает Telegram»: такие подписки часовой воркер
    # НЕ трогает, иначе он снимал бы с тарифа живых плательщиков.
    stars_sub_charge_id = Column(String, nullable=False, default="")
    # '' | active | canceled | failed | expired — приезжает апдейтом subscription.
    stars_sub_state = Column(String, nullable=False, default="")
    avatar_url = Column(String, nullable=False, default="")
    # Партнёрка «амбассадор». ref_code пустой, пока человек не подключился:
    # так поиск владельца кода не цепляет обычных пользователей.
    ref_code = Column(String, nullable=False, default="")
    # Кто привёл. Ставится ОДИН раз (первое касание) и больше не меняется —
    # иначе последний, кто дал ссылку, забирал бы чужого реферала.
    referred_by = Column(Integer, nullable=True)
    is_ambassador = Column(Boolean, nullable=False, default=False)
    # Деньги партнёрки в копейках: начислено за всё время и выплачено руками.
    # Доступное к выплате НЕ храним — оно считается как начислено − выплачено −
    # суммы заявок в работе, чтобы одни и те же деньги нельзя было заказать дважды.
    ref_balance_kopeks = Column(Integer, nullable=False, default=0)
    ref_paid_kopeks = Column(Integer, nullable=False, default=0)
    payout_details = Column(Text, nullable=False, default="")


class RefEvent(Base):
    """Событие партнёрки: приход реферала и каждый его платёж.

    Это одновременно лента для кабинета амбассадора и защита от повторного
    начисления: ЮKassa штатно дублирует вебхуки, а UNIQUE на payment_id делает
    второе начисление по тому же платежу физически невозможным. У signup-событий
    payment_id = NULL — в SQLite несколько NULL уникальности не нарушают."""
    __tablename__ = "ref_events"
    id = Column(Integer, primary_key=True)
    ambassador_id = Column(Integer, nullable=False, index=True)
    referral_id = Column(Integer, nullable=False, index=True)
    kind = Column(String, nullable=False, default="signup")  # signup | payment
    amount_kopeks = Column(Integer, nullable=False, default=0)   # сколько заплатил реферал
    reward_kopeks = Column(Integer, nullable=False, default=0)   # сколько начислено амбассадору
    payment_id = Column(String, nullable=True, unique=True, default=None)
    created_at = Column(DateTime, default=now)


class Payout(Base):
    """Заявка амбассадора на выплату. Деньги владелец переводит руками —
    автоматических выплат нет, статус двигается админским эндпоинтом.

    Пока заявка в new, её сумма считается зарезервированной и в доступный
    баланс не входит; rejected сам возвращает деньги в доступные."""
    __tablename__ = "payouts"
    id = Column(Integer, primary_key=True)
    ambassador_id = Column(Integer, nullable=False, index=True)
    amount_kopeks = Column(Integer, nullable=False, default=0)
    details = Column(Text, nullable=False, default="")
    status = Column(String, nullable=False, default="new")  # new | paid | rejected
    comment = Column(Text, nullable=False, default="")
    created_at = Column(DateTime, default=now)
    updated_at = Column(DateTime, default=now, onupdate=now)


class ProcessedPayment(Base):
    """Платежи, по которым тариф или пакет очков уже выданы (оба провайдера).

    Обе платёжки повторяют событие, пока не получат 200, и дублируют его на
    ретраях сети. Без этой таблицы каждый дубль двигал plan_until ещё на
    месяц: человек платил один раз, а подписка росла на глазах. UNIQUE на
    payment_id закрывает и гонку двух одновременно приехавших вебхуков.

    payment_id хранится С ПРЕФИКСОМ провайдера ("stripe:in_…", "yookassa:…"):
    идентификаторы двух систем живут в одной колонке и пересечься не должны."""
    __tablename__ = "processed_payments"
    id = Column(Integer, primary_key=True)
    payment_id = Column(String, nullable=False, unique=True)
    user_id = Column(Integer, nullable=True, index=True)
    plan = Column(String, nullable=False, default="")
    amount_kopeks = Column(Integer, nullable=False, default=0)
    # Ниже — колонки международного контура (добавлены мягкой миграцией).
    provider = Column(String, nullable=False, default="yookassa")  # yookassa | stripe
    kind = Column(String, nullable=False, default="plan")          # plan | topup
    period = Column(String, nullable=False, default="month")       # month | year
    points = Column(Integer, nullable=False, default=0)            # сколько очков выдали
    amount_cents = Column(Integer, nullable=False, default=0)      # сумма в центах, если USD
    currency = Column(String, nullable=False, default="RUB")
    created_at = Column(DateTime, default=now)


class FileOwner(Base):
    """Владелец каждого файла в UPLOAD_DIR: /api/media отдаёт чужие файлы
    только админу, чтобы приватные кадры/треки никуда не утекали."""
    __tablename__ = "file_owners"
    filename = Column(String, primary_key=True)
    user_id = Column(Integer, nullable=False, default=0)


class Project(Base):
    __tablename__ = "projects"
    id = Column(Integer, primary_key=True)
    # Владелец проекта; NULL у легаси-строк до усыновления админом при старте.
    owner_id = Column(Integer, ForeignKey("users.id"), nullable=True)
    name = Column(String, nullable=False, default="Без названия")
    # ВИД ПРОЕКТА — он же РЕЖИМ. Одно поле вместо второго реестра:
    #   album | single — «rap clips» (клип под свой трек);
    #   ugc            — канал ИИ-блогера, объект второго уровня = ролик;
    #   series         — сериал, объект второго уровня = серия.
    # Реестр режимов (что показывать и чем генерить) — backend/formats.py.
    # Старые проекты остаются album/single и ничего не замечают.
    kind = Column(String, nullable=False, default="album")
    # Библия героя: внешность, характер, неизменные детали — вставляется в
    # КАЖДЫЙ промпт кадра, чтобы герой не "плыл" между сценами и треками.
    character_bible = Column(Text, nullable=False, default="")
    # Сквозной сюжет всего клипа (генерится по всем трекам разом).
    story = Column(Text, nullable=False, default="")
    story_status = Column(String, nullable=False, default="")  # '' | queued | running | error
    story_error = Column(Text, nullable=False, default="")
    # Обложка проекта (файл в UPLOAD_DIR): визуальный якорь альбома —
    # заливается владельцем, при замене старый файл удаляется.
    cover_filename = Column(String, nullable=False, default="")
    created_at = Column(DateTime, default=now)
    updated_at = Column(DateTime, default=now, onupdate=now)

    tracks = relationship("Track", back_populates="project", cascade="all, delete-orphan",
                           order_by="Track.position")


class Character(Base):
    """Персонаж альбома: имя, характер и фото-модельки лица/образа.

    Персонажи живут на ПРОЕКТ (весь альбом): Claude вписывает их имена в
    промпты кадров, к промпту всегда идёт их словесное описание, а при
    генерации картинки моделька уходит референсом. Меняется только стилистика
    подачи — по стилю конкретного трека."""
    __tablename__ = "characters"
    id = Column(Integer, primary_key=True)
    project_id = Column(Integer, ForeignKey("projects.id"), nullable=False)
    position = Column(Integer, nullable=False, default=0)
    name = Column(String, nullable=False, default="")
    # Характер + внешность словами (используется в промптах ВСЕГДА,
    # даже когда есть фото: генератор должен знать детали, которых нет в кадре).
    description = Column(Text, nullable=False, default="")
    is_main = Column(Boolean, nullable=False, default=False)
    # Голос персонажа (id голоса ElevenLabs, см. audio.py). Сквозной по всему
    # проекту: в сериале реплики серии озвучиваются голосом того, кто говорит,
    # а не «общим диктором», иначе герой меняет голос между сериями.
    voice_id = Column(String, nullable=False, default="")
    created_at = Column(DateTime, default=now)
    updated_at = Column(DateTime, default=now, onupdate=now)

    project = relationship("Project", backref="characters")
    photos = relationship("CharacterPhoto", back_populates="character",
                          cascade="all, delete-orphan", order_by="CharacterPhoto.position")
    attributes = relationship("CharacterAttribute", back_populates="character",
                              cascade="all, delete-orphan",
                              order_by="CharacterAttribute.position")


class CharacterPhoto(Base):
    """Картинка персонажа: ЛИБО загруженное фото, ЛИБО сгенерированный разворот.

    Раньше всё лежало одной кучей, и это ломало две вещи разом:
      1. сгенерированный лист попадал в референсы СЛЕДУЮЩЕЙ генерации —
         моделька рисовалась с модельки, живое фото вытеснялось, дрейф
         компаундировался за две-три итерации;
      2. кадры сцен брали photos[0], то есть самое старое СЕЛФИ, а не
         разворот, ради которого кнопку «сгенерировать модельку» и жмут.
    kind разводит эти две сущности: в референсы разворота идут только
    kind="photo", в кадры сцен — последний kind="model"."""
    __tablename__ = "character_photos"
    id = Column(Integer, primary_key=True)
    character_id = Column(Integer, ForeignKey("characters.id"), nullable=False)
    position = Column(Integer, nullable=False, default=0)
    filename = Column(String, nullable=False)
    # photo — загружено человеком; model — сгенерированный лист ракурсов.
    # Старым строкам мягкая миграция проставит "photo": это безопасная сторона
    # ошибки (лишнее фото в референсах хуже не делает, а вот потерянный
    # разворот сломал бы кадры уже готовых проектов).
    kind = Column(String, nullable=False, default="photo")
    # Какой это разворот: 3d | real | anime | closeup ("" у обычных фото).
    pose_kind = Column(String, nullable=False, default="")
    # По скольким загруженным фото собран разворот — интерфейс говорит это
    # вслух, чтобы «моделька не похожа» не было загадкой.
    from_photos = Column(Integer, nullable=False, default=0)
    created_at = Column(DateTime, default=now)

    character = relationship("Character", back_populates="photos")


class CharacterAttribute(Base):
    """Атрибут персонажа: его фирменная вещь (шляпа, очки, квадрик, тачка).

    У атрибута свой набор фото-моделек — ракурсы ПРЕДМЕТА, а не лица. Когда
    кадр строится вокруг вещи (её имя встречается в тексте сцены), референсом
    генерации идёт фото атрибута вместо фото персонажа — так предмет остаётся
    узнаваемым от кадра к кадру, как и сам герой."""
    __tablename__ = "character_attributes"
    id = Column(Integer, primary_key=True)
    character_id = Column(Integer, ForeignKey("characters.id"), nullable=False)
    position = Column(Integer, nullable=False, default=0)
    name = Column(String, nullable=False, default="")
    description = Column(Text, nullable=False, default="")
    created_at = Column(DateTime, default=now)
    updated_at = Column(DateTime, default=now, onupdate=now)

    character = relationship("Character", back_populates="attributes")
    photos = relationship("AttributePhoto", back_populates="attribute",
                          cascade="all, delete-orphan", order_by="AttributePhoto.position")


class AttributePhoto(Base):
    __tablename__ = "attribute_photos"
    id = Column(Integer, primary_key=True)
    attribute_id = Column(Integer, ForeignKey("character_attributes.id"), nullable=False)
    position = Column(Integer, nullable=False, default=0)
    filename = Column(String, nullable=False)
    created_at = Column(DateTime, default=now)

    attribute = relationship("CharacterAttribute", back_populates="photos")


class Track(Base):
    __tablename__ = "tracks"
    id = Column(Integer, primary_key=True)
    project_id = Column(Integer, ForeignKey("projects.id"), nullable=False)
    position = Column(Integer, nullable=False, default=0)
    title = Column(String, nullable=False, default="")
    lyrics = Column(Text, nullable=False, default="")
    comment = Column(Text, nullable=False, default="")
    # Визуальный стиль ЭТОГО трека — ПОЛНЫЙ текст промпта, который уходит в
    # модель. НАРУЖУ НЕ ОТДАЁТСЯ (см. track_dict в main.py): это тот же текст,
    # что и в закрытом реестре prompts_catalog, и раздача его через /api/tracks
    # сводила бы на нет перенос реестра на сервер.
    style = Column(String, nullable=False, default="")
    # Из чего собран стиль: ключи пресетов через запятую, ПЕРВЫЙ — основа.
    # Это и есть публичная часть: витрина подсвечивает чипы по ключам, а не
    # ищет подстроки промпта в тексте, как делал старый фронт.
    style_keys = Column(String, nullable=False, default="")
    # Приписка, которую человек написал сам. Она его — её и возвращаем наружу.
    style_extra = Column(Text, nullable=False, default="")
    # Сюжетный каркас («что снимаем») из prompts_catalog.CLIP_PRESETS.
    clip_preset_key = Column(String, nullable=False, default="")

    # ─── режимы «сериалы» и «UGC»: тот же Track, другая роль ───
    # Сезон живёт КОЛОНКОЙ, а не таблицей. Полноценный Season сменил бы FK у
    # самой горячей таблицы (на tracks висят render, поллинг, супергенерация и
    # сборка), а вся семантика сезона исчерпывается номером и группировкой.
    # 0 = «вне сезонов» — так выглядят все существующие треки после миграции.
    season_no = Column(Integer, nullable=False, default=0)
    episode_no = Column(Integer, nullable=False, default=0)
    # Ключ каркаса из formats.SERIES_FORMATS / formats.UGC_FORMATS. У клипа
    # свой каркас лежит в clip_preset_key — смешивать их в одно поле нельзя:
    # это разные реестры с разными beats.
    format_key = Column(String, nullable=False, default="")
    # «Библия локации»: интерьер, свет, посуда, вид из окна — то, что обязано
    # повторяться слово в слово во ВСЕХ кадрах объекта. У проекта есть
    # character_bible (кто), здесь — где. Без неё UGC-ролик меняет кухню
    # между кадрами, и склейка рассыпается сильнее, чем от смены лица.
    location_bible = Column(Text, nullable=False, default="")

    # ДВИЖКИ НА УРОВНЕ ОБЪЕКТА. Раньше выбор жил только в карточке кадра, где
    # он (а) повторялся тридцать раз, (б) никуда не сохранялся, (в) полностью
    # игнорировался кнопками «все кадры»/«все видео». Здесь — один выбор на
    # весь трек; Scene.image_engine/video_engine остаются ПЕРЕОПРЕДЕЛЕНИЕМ
    # (пусто = наследует трек).
    video_engine = Column(String, nullable=False, default="")
    image_engine = Column(String, nullable=False, default="")
    # Режиссёрская заметка от генерации сюжета — ОТДЕЛЬНО от комментария
    # владельца: раньше дописывалась прямо в comment и пачкала его.
    director_note = Column(Text, nullable=False, default="")
    # Профиль звука: энергия/динамика по сегментам, считается ffmpeg'ом при
    # загрузке аудио — чтобы сюжет и раскадровка опирались на реальный трек.
    audio_profile = Column(Text, nullable=False, default="")
    audio_filename = Column(String, nullable=False, default="")
    audio_duration_sec = Column(Integer, nullable=False, default=0)
    # Обложка трека (файл в UPLOAD_DIR): у каждого трека своя картинка,
    # независимая от обложки проекта.
    cover_filename = Column(String, nullable=False, default="")
    scenes_status = Column(String, nullable=False, default="")  # '' | queued | running | error
    scenes_error = Column(Text, nullable=False, default="")

    # Раскадровка ОДНИМ листом: все кадры трека на одной картинке. Рисуется
    # раньше покадровой генерации — по ней видно, целостно ли выглядит клип,
    # и она же идёт контекстом в промпты отдельных кадров.
    storyboard_filename = Column(String, nullable=False, default="")
    storyboard_status = Column(String, nullable=False, default="")
    storyboard_error = Column(Text, nullable=False, default="")

    # Итоговый клип трека (склейка утверждённых сцен + дорожка).
    clip_filename = Column(String, nullable=False, default="")
    clip_status = Column(String, nullable=False, default="")
    clip_error = Column(Text, nullable=False, default="")

    # Плёночное зерно на весь собранный клип (ffmpeg-фильтр при склейке).
    film_grain = Column(Boolean, nullable=False, default=False)
    # «Без сюжета»: раскадровка = независимые рандомные панч-кадры по комментарию,
    # сквозной сюжет проекта не требуется и не используется.
    no_story = Column(Boolean, nullable=False, default=False)

    # «Супергенерация»: конвейер сюжет→сцены→кадры→видео→сборка одним нажатием.
    # note — живой прогресс для строки статуса на карточке трека.
    supergen_status = Column(String, nullable=False, default="")  # '' | queued | running | done | error
    supergen_note = Column(Text, nullable=False, default="")
    created_at = Column(DateTime, default=now)
    updated_at = Column(DateTime, default=now, onupdate=now)

    project = relationship("Project", back_populates="tracks")
    scenes = relationship("Scene", back_populates="track", cascade="all, delete-orphan",
                           order_by="Scene.position")


class Scene(Base):
    __tablename__ = "scenes"
    id = Column(Integer, primary_key=True)
    track_id = Column(Integer, ForeignKey("tracks.id"), nullable=False)
    position = Column(Integer, nullable=False, default=0)
    start_sec = Column(Integer, nullable=False, default=0)
    duration_sec = Column(Integer, nullable=False, default=6)
    lyric_line = Column(Text, nullable=False, default="")
    # Имена персонажей в кадре (через запятую) — по ним подтягиваются
    # описания и фото-модельки при генерации картинки.
    characters = Column(Text, nullable=False, default="")
    # Атрибуты (вещи) персонажей, ЯВНО выбранные для этого кадра: id через
    # запятую. Пусто = старое поведение (ищем упоминание вещи в тексте сцены).
    attribute_ids = Column(Text, nullable=False, default="")
    # Монтажная грамматика: крупность плана и движение камеры — управляют
    # ритмом раскадровки (чередование крупных/дальних, см. claude.py).
    shot_size = Column(String, nullable=False, default="")
    camera_move = Column(String, nullable=False, default="")
    # Акт серии: cold_open | act1 | act2 | act3 | tag (у клипа и UGC пусто).
    act = Column(String, nullable=False, default="")
    # Кто говорит в кадре. РЕПЛИКА при этом лежит в lyric_line — второго поля
    # под текст не заводим: у клипа это строка трека, у серии — реплика, и
    # различие ровно в подписи. Слот в 6 секунд держит одну фразу, диалог
    # монтируется восьмёркой (по одному говорящему в кадре).
    speaker = Column(String, nullable=False, default="")
    # Промпт картинки кадра (для генерации изображения) — на английском.
    image_prompt = Column(Text, nullable=False, default="")
    # Промпт анимации ЭТОГО кадра (self-contained, без ссылок на другие кадры).
    motion_prompt = Column(Text, nullable=False, default="")
    shot_note = Column(Text, nullable=False, default="")  # по-русски: что происходит

    # ПЕРВЫЙ и ПОСЛЕДНИЙ кадр сцены (ChatGPT-подписка → фолбэк Grok), 4К.
    # Seedance интерполирует видео между ними — отсюда связный монтаж.
    image_filename = Column(String, nullable=False, default="")       # первый кадр
    image_last_filename = Column(String, nullable=False, default="")  # последний кадр
    image_status = Column(String, nullable=False, default="")  # '' | queued | running | error
    image_error = Column(Text, nullable=False, default="")
    # Промпт последнего кадра — пишется Claude'ом вместе со сценарием.
    image_prompt_last = Column(Text, nullable=False, default="")

    # Промежуточные кадры сцены: JSON-список [{"filename":…, "prompt":…}].
    # Промпты строятся интерполяцией первый→последний без Claude; файлы
    # дописываются в список по одному по мере готовности (см. _run_midframes).
    midframes_json = Column(Text, nullable=False, default="")

    # Отрезок трека под эту сцену (ffmpeg-нарезка при генерации видео).
    audio_filename = Column(String, nullable=False, default="")

    # Утверждение ВИДЕО (не картинки): утверждённые сцены идут в общий клип.
    approved = Column(Boolean, nullable=False, default=False)

    video_filename = Column(String, nullable=False, default="")
    video_status = Column(String, nullable=False, default="")  # '' | queued | running | error
    video_error = Column(Text, nullable=False, default="")
    video_provider = Column(String, nullable=False, default="seedance")
    # Конкретные движки этой сцены (id из mediagen.VIDEO_ENGINES/IMAGE_ENGINES).
    # video_provider — только СЕМЕЙСТВО (grok|seedance|kling), его знает фронт;
    # модель внутри семейства выбирает тариф, и стоят они по-разному, поэтому
    # писать её надо рядом. image_engine нужен ещё и для честного добора цены:
    # сцена = кадры ТЕМ движком, которым их реально нарисовали, плюс видео.
    image_engine = Column(String, nullable=False, default="")
    video_engine = Column(String, nullable=False, default="")

    # Сколько очков за ЭТУ сцену уже списано. Сцена — единица тарификации:
    # кадры берут аванс, видео добирает разницу до цены своего движка, а
    # перегенерация уже оплаченного не списывает второй раз. Без счётчика
    # пришлось бы либо брать за кадры отдельно (двойная плата за сцену),
    # либо отдавать кадры даром (бесконечная перерисовка на нашей подписке).
    charged_points = Column(Integer, nullable=False, default=0)

    created_at = Column(DateTime, default=now)
    updated_at = Column(DateTime, default=now, onupdate=now)

    track = relationship("Track", back_populates="scenes")
    refs = relationship("SceneRef", back_populates="scene", cascade="all, delete-orphan",
                        order_by="SceneRef.position")


class SceneRef(Base):
    """Картинка-референс КАДРА: композиция, свет, вайб, энергия плана.

    Зачем отдельно от моделек персонажей: моделька отвечает только за
    узнаваемость лица/предмета, а «как это снято» владелец показывает
    картинкой. Стилистику при этом диктует стиль трека: реф уходит в генерацию
    первым в коллаже, но промпт прямо запрещает копировать его свет и палитру
    (см. _frame_prompt в main.py)."""
    __tablename__ = "scene_refs"
    id = Column(Integer, primary_key=True)
    scene_id = Column(Integer, ForeignKey("scenes.id"), nullable=False)
    position = Column(Integer, nullable=False, default=0)
    filename = Column(String, nullable=False)
    # vibe — композиция/свет/настроение (пока единственный вид референса).
    kind = Column(String, nullable=False, default="vibe")
    created_at = Column(DateTime, default=now)

    scene = relationship("Scene", back_populates="refs")


class Doc(Base):
    """Сценарный артефакт: логлайн, синопсис, арка, поэпизодный план, сценарий
    серии по актам, «в предыдущих сериях», UGC-бриф, персона блогера.

    ОДНА таблица на все режимы вместо N колонок в Project. У проекта уже есть
    story и character_bible; дописывать туда logline/synopsis/arc значит
    пачкать клиповый проект сериальными полями, которых он никогда не увидит.

    track_id = NULL — документ проекта (сезонный слой), заполнен — документ
    объекта (сценарий конкретной серии, бриф конкретного ролика).

    status/error повторяют паттерн Project.story_status: фронт поллит их той
    же логикой, что и всё остальное, и ничему новому учиться не надо."""
    __tablename__ = "docs"
    id = Column(Integer, primary_key=True)
    project_id = Column(Integer, ForeignKey("projects.id"), nullable=False, index=True)
    track_id = Column(Integer, ForeignKey("tracks.id"), nullable=True, index=True)
    # logline | synopsis | arc | beatsheet | script | recap | brief | persona
    kind = Column(String, nullable=False, default="")
    position = Column(Integer, nullable=False, default=0)
    title = Column(String, nullable=False, default="")
    body = Column(Text, nullable=False, default="")
    # Структурный вид того же документа (поэпизодный план — список карточек,
    # сценарий — список актов). Текст в body остаётся для человека.
    body_json = Column(Text, nullable=False, default="")
    status = Column(String, nullable=False, default="")  # '' | queued | running | error
    error = Column(Text, nullable=False, default="")
    created_at = Column(DateTime, default=now)
    updated_at = Column(DateTime, default=now, onupdate=now)


class PointEvent(Base):
    """Журнал очков: каждое списание и каждое начисление отдельной строкой.

    До него история расхода существовала ТОЛЬКО в log.info контейнера:
    _charge менял users.gen_points и писал строчку в лог. Поэтому кабинет не
    мог показать ни расход по дням, ни «на что ушло», ни возвраты — строить
    было не из чего.

    balance_after пишем рядом с delta намеренно: журнал должен объяснять
    остаток без пересчёта всей истории, иначе первая же дыра (ручная правка,
    админский грант) навсегда разъезжается с реальным балансом."""
    __tablename__ = "point_events"
    id = Column(Integer, primary_key=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False, index=True)
    created_at = Column(DateTime, default=now, index=True)
    delta = Column(Integer, nullable=False, default=0)      # <0 расход, >0 приход
    # frames | video | chat | audio | story | sheet | model | topup | plan | refund | drip
    kind = Column(String, nullable=False, default="")
    what = Column(String, nullable=False, default="")       # человеческая подпись
    ref_type = Column(String, nullable=False, default="")   # scene | track | chat_message | payment
    ref_id = Column(Integer, nullable=False, default=0)
    engine = Column(String, nullable=False, default="")
    balance_after = Column(Integer, nullable=False, default=0)


# ─────────────────────────── чат с переключением моделей ───────────────────────────
# Одно окно вместо вкладок «текст / картинки / видео»: намерение задаёт
# ВЫБРАННАЯ МОДЕЛЬ, а не режим экрана. Поэтому сообщение хранит и текст, и
# файл, и движок, и цену — одна лента, три вида содержимого.

class Chat(Base):
    __tablename__ = "chats"
    id = Column(Integer, primary_key=True)
    owner_id = Column(Integer, ForeignKey("users.id"), nullable=False, index=True)
    title = Column(String, nullable=False, default="")
    # Последний выбранный движок: вернувшись в чат, человек продолжает тем же.
    model = Column(String, nullable=False, default="")
    archived = Column(Boolean, nullable=False, default=False)
    created_at = Column(DateTime, default=now)
    updated_at = Column(DateTime, default=now, onupdate=now)

    messages = relationship("ChatMessage", back_populates="chat",
                            cascade="all, delete-orphan",
                            order_by="ChatMessage.position")


class ChatMessage(Base):
    """Одно сообщение ленты: реплика человека или ответ модели.

    expires_at — СРОК ХРАНЕНИЯ ФАЙЛА, а не сообщения. Студия хранит медиа
    осмысленно (кадр нужен клипу), чат же плодит картинки пачками, и без
    ретенции диск съедается за недели. По сроку удаляется только файл: текст
    и промпт остаются навсегда, поэтому любую картинку можно повторить."""
    __tablename__ = "chat_messages"
    id = Column(Integer, primary_key=True)
    chat_id = Column(Integer, ForeignKey("chats.id"), nullable=False, index=True)
    position = Column(Integer, nullable=False, default=0)
    role = Column(String, nullable=False, default="user")      # user | assistant
    kind = Column(String, nullable=False, default="text")      # text | image | video
    text = Column(Text, nullable=False, default="")
    media_filename = Column(String, nullable=False, default="")
    engine = Column(String, nullable=False, default="")
    # Сколько очков списано ЗА ЭТО сообщение. Нужно для возврата при ошибке:
    # в студии упавшая сцена теряется в потоке, а в чате запросы одиночные —
    # молча съеденные 154 очка человек увидит сразу.
    points = Column(Integer, nullable=False, default=0)
    params_json = Column(Text, nullable=False, default="")
    status = Column(String, nullable=False, default="")        # '' | queued | running | error
    error = Column(Text, nullable=False, default="")
    expires_at = Column(DateTime, nullable=True)
    created_at = Column(DateTime, default=now)

    chat = relationship("Chat", back_populates="messages")
    files = relationship("ChatFile", back_populates="message",
                         cascade="all, delete-orphan", order_by="ChatFile.position")


class ChatFile(Base):
    """Вложение человека к сообщению чата.

    Файл заливается ДО отправки сообщения (кнопка «+»), поэтому message_id
    какое-то время пуст: висячие вложения старше суток подчищает тот же
    сборщик, что и просроченные медиа."""
    __tablename__ = "chat_files"
    id = Column(Integer, primary_key=True)
    owner_id = Column(Integer, nullable=False, default=0, index=True)
    message_id = Column(Integer, ForeignKey("chat_messages.id"), nullable=True, index=True)
    position = Column(Integer, nullable=False, default=0)
    filename = Column(String, nullable=False)
    created_at = Column(DateTime, default=now)

    message = relationship("ChatMessage", back_populates="files")


def init_db() -> None:
    Base.metadata.create_all(engine)
    # Мягкая миграция: новые колонки добавляем ALTER'ом, НЕ пересоздавая базу —
    # данные владельца (треки, сцены, утверждения) переживают любой деплой.
    from sqlalchemy import inspect, text as sqltext
    insp = inspect(engine)
    with engine.begin() as conn:
        for table in Base.metadata.sorted_tables:
            if not insp.has_table(table.name):
                continue
            existing = {c["name"] for c in insp.get_columns(table.name)}
            for col in table.columns:
                if col.name in existing:
                    continue
                coltype = col.type.compile(engine.dialect)
                default = ""
                if col.default is not None and getattr(col.default, "arg", None) is not None \
                        and not callable(col.default.arg):
                    arg = col.default.arg
                    if isinstance(arg, bool):
                        default = f" DEFAULT {int(arg)}"
                    elif isinstance(arg, (int, float)):
                        default = f" DEFAULT {arg}"
                    else:
                        default = f" DEFAULT '{arg}'"
                conn.execute(sqltext(
                    f'ALTER TABLE {table.name} ADD COLUMN {col.name} {coltype}{default}'
                ))
