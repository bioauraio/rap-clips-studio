"""SQLite-хранилище проекта. Личный однопользовательский инструмент —
без миграционного фреймворка: схема создаётся при старте, если её нет."""
from __future__ import annotations

import os
from datetime import datetime, timezone

from sqlalchemy import (
    Boolean, Column, DateTime, Float, ForeignKey, Integer, String, Text, create_engine,
)
from sqlalchemy.orm import DeclarativeBase, backref, relationship, sessionmaker

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
    # Токены генераций: защита кошелька владельца — генерации идут через его
    # подписки. Гость стартует с месячной нормы плана free (держи это число
    # равным PLANS["free"]["points"] в main.py), админ — с бесконечностью.
    #
    # 150, а не 120: норму FREE подняли до 150 в main.py, а СТАРТОВОЕ значение
    # осталось прежним — и каждый новый гость получал 120 токенов вместо
    # обещанных 150. Ровно те 30 токенов, которых не хватало на последнюю сцену
    # трёхминутного клипа, то есть обещание «первый клип за наш счёт»
    # не выполнялось. Теперь оба числа в одном месте правды.
    gen_points = Column(Integer, nullable=False, default=150)
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
    # Помесячная выдача токенов ГОДОВОЙ подписки. Раньше год начислял норму ×12
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

    # ─── CRM: активность, блокировка, согласия на рассылку ───
    # last_seen_at пишется НЕ на каждый запрос: фронт поллит /api/me раз в
    # три секунды, а база — SQLite, и UPDATE на каждый опрос означал бы
    # блокировки на ровном месте. Обновляем не чаще раза в LAST_SEEN_EVERY_S
    # (см. main.py), поэтому колонка отвечает на «когда был», а не «сейчас».
    last_seen_at = Column(DateTime, nullable=True, index=True)
    # Блокировка проверяется в current_user, а не только в интерфейсе: иначе
    # заблокированный продолжал бы жечь наши деньги через generate-video.
    is_blocked = Column(Boolean, nullable=False, default=False)
    blocked_reason = Column(String, nullable=False, default="")
    # Отписки от рассылок по каналам. Транзакционные сообщения (оплата
    # прошла, подписка истекает) ими НЕ глушатся — только маркетинговые.
    unsub_email = Column(Boolean, nullable=False, default=False)
    unsub_tg = Column(Boolean, nullable=False, default=False)
    unsub_all = Column(Boolean, nullable=False, default=False)


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
    """Платежи, по которым тариф или пакет токенов уже выданы (оба провайдера).

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
    points = Column(Integer, nullable=False, default=0)            # сколько токенов выдали
    amount_cents = Column(Integer, nullable=False, default=0)      # сумма в центах, если USD
    currency = Column(String, nullable=False, default="RUB")
    created_at = Column(DateTime, default=now)


class FileOwner(Base):
    """Владелец каждого файла в UPLOAD_DIR: /api/media отдаёт чужие файлы
    только админу, чтобы приватные кадры/треки никуда не утекали.

    Это же — ИНДЕКС АРХИВА. Раньше в таблице было две колонки (имя и
    владелец), и «папка со всеми файлами, разложенная по датам, видам и
    проектам» строиться из неё не могла в принципе: ни даты, ни вида, ни
    связи с проектом здесь не лежало. Собирать архив обходом
    scenes/tracks/characters/chat_messages значило бы шесть запросов на
    страницу и полное отсутствие сортировки по дате.

    Метаданные проставляет _reg_file в момент создания файла, пробелы
    (легаси и то, что регистрируется без контекста) добирает _files_sweep."""
    __tablename__ = "file_owners"
    filename = Column(String, primary_key=True)
    user_id = Column(Integer, nullable=False, default=0)
    created_at = Column(DateTime, default=now, index=True)
    # frame | frame_last | midframe | ref | video | clip | storyboard |
    # cover | model | photo | attr | audio | chat | other
    kind = Column(String, nullable=False, default="", index=True)
    project_id = Column(Integer, nullable=False, default=0, index=True)
    track_id = Column(Integer, nullable=False, default=0)
    scene_id = Column(Integer, nullable=False, default=0)
    size_bytes = Column(Integer, nullable=False, default=0)
    # Мягкое удаление: файл исчезает из архива сразу, с диска — отложенным
    # проходом. Живой файл, на который ссылается сцена, удалить нельзя —
    # сцена без image_filename ломает половину кнопок карточки.
    deleted_at = Column(DateTime, nullable=True)
    # ФИЗИЧЕСКИЙ КЛЮЧ ФАЙЛА («устройство:инод»). Копия проекта не копирует
    # байты — она делает ЖЁСТКУЮ ССЫЛКУ (см. _clone_media в main.py), и у
    # одного куска диска появляется несколько имён и несколько строк здесь.
    # Без этого ключа сумма size_bytes считала бы такой файл столько раз,
    # сколько у него имён, и архив с квотой врали бы человеку ровно на объём
    # копий — то есть тем сильнее, чем активнее он копирует.
    phys_key = Column(String, nullable=False, default="", index=True)
    # Откуда взялось имя (для копий). Связь по phys_key живёт, пока жив
    # инод; это поле переживает удаление оригинала и объясняет происхождение.
    src_filename = Column(String, nullable=False, default="")


class FrameCache(Base):
    """Кадр, который УЖЕ рисовали ровно этим промптом и этим движком.

    Смысл один — не платить дважды за одну и ту же картинку. «То же самое
    ещё раз» в журнале прода не редкость (у одной сцены шесть прогонов
    кадров подряд), и каждый такой прогон — живые деньги на kie.ai и ещё
    один файл на диске.

    СТРОГО В ПРЕДЕЛАХ ОДНОГО ПОЛЬЗОВАТЕЛЯ: общий кэш был бы утечкой чужих
    приватных кадров — совпал промпт, увидел чужую картинку."""
    __tablename__ = "frame_cache"
    id = Column(Integer, primary_key=True)
    user_id = Column(Integer, nullable=False, default=0, index=True)
    key_hash = Column(String, nullable=False, default="", index=True)
    engine = Column(String, nullable=False, default="")
    filename = Column(String, nullable=False, default="")
    created_at = Column(DateTime, default=now, index=True)


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
    # Текстовая модель сценарного конвейера (id из textgen.TEXT_ENGINES).
    # Живёт на ПРОЕКТЕ, а не на треке: сюжет, библия, сценарий серии и
    # раскадровка — один конвейер, и разные модели на соседних шагах дают
    # разъезд тона. Пусто = «как решит тариф» (шлюз).
    text_engine = Column(String, nullable=False, default="")
    # Обложка проекта (файл в UPLOAD_DIR): визуальный якорь альбома —
    # заливается владельцем, при замене старый файл удаляется.
    cover_filename = Column(String, nullable=False, default="")
    # ПРОИСХОЖДЕНИЕ КОПИИ. Копия проекта — не «новый проект с похожим
    # именем»: карточка обязана уметь сказать, из чего она сделана, иначе
    # через неделю два одинаковых названия в списке ничем не различаются.
    # 0 = оригинал. Не ForeignKey: оригинал могут удалить, а копия обязана
    # пережить это без каскада.
    copied_from_id = Column(Integer, nullable=False, default=0)
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

    # CASCADE НА СТОРОНЕ ПРОЕКТА ОБЯЗАТЕЛЕН, и это не украшательство. Без него
    # SQLAlchemy при удалении проекта пытается ОТВЯЗАТЬ героя (project_id=NULL),
    # а колонка NOT NULL — то есть удаление любого проекта с персонажами падало
    # 500 на IntegrityError и не удаляло НИЧЕГО. Ровно это здесь и подразумевали:
    # main.delete_project сам стирает с диска фото героев и их атрибутов прямо
    # перед db.delete(project). Герой не существует вне проекта (project_id
    # NOT NULL), так что «осиротеть» ему некуда.
    project = relationship("Project",
                           backref=backref("characters",
                                           cascade="all, delete-orphan"))
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
    """Атрибут персонажа: его фирменная вещь (шляпа, токены, квадрик, тачка).

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
    # ГЕОМЕТРИЯ КАДРА. До режима мокапов аспект был константой 9:16 в шести
    # местах mediagen — клип, ролик и серия все вертикальные, и спорить было
    # не с чем. Карточка маркетплейса живёт в квадрате, и вертикаль там
    # обрезается по краям ровно по упаковке. Пусто = аспект режима.
    aspect = Column(String, nullable=False, default="")          # '' | 9:16 | 1:1 | 4:5
    # Разрешение картинки (1K|2K|4K) у движков семейства Nano Banana. У клипа
    # его решает тариф, у мокапа оно важнее всего остального: этикетку читают
    # с экрана телефона, и 1K её замыливает.
    image_resolution = Column(String, nullable=False, default="")
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
    # СКОЛЬКО СЦЕН БЫЛО В МОМЕНТ ЗАКАЗА ЛИСТА. Сетка листа считается от числа
    # сцен (sheet_grid), поэтому «продлить кадр», копия кадра и любое ручное
    # добавление делают уже нарисованный лист неправильным: нарезка порежет
    # мимо панелей. Отдельного флага «лист устарел» не заводим — флаг можно
    # забыть выставить в одном из пяти мест; расхождение ЧИСЕЛ забыть нельзя.
    # 0 = лист рисовали до появления этого поля, состояние неизвестно.
    storyboard_scenes = Column(Integer, nullable=False, default=0)
    storyboard_status = Column(String, nullable=False, default="")
    # Сетка листа «столбцыxстроки», зафиксированная В МОМЕНТ генерации.
    # Без неё нарезка считала сетку заново от текущего числа сцен и резала
    # мимо панелей: лист 4x2, а резали 3x2 — куски захватывали соседей.
    storyboard_grid = Column(String, nullable=False, default="")
    storyboard_error = Column(Text, nullable=False, default="")

    # Итоговый клип трека (склейка утверждённых сцен + дорожка).
    clip_filename = Column(String, nullable=False, default="")
    clip_status = Column(String, nullable=False, default="")
    clip_error = Column(Text, nullable=False, default="")
    # КОГДА КЛИП В ПОСЛЕДНИЙ РАЗ СМОТРЕЛИ ИЛИ СКАЧИВАЛИ. Клип — единственный
    # крупный артефакт, который восстанавливается ЗА НОЛЬ ТОКЕНОВ (ffmpeg
    # склеит его заново из тех же видео). Поэтому именно он уезжает первым,
    # когда на диске тесно, — но только тот, к которому давно не возвращались.
    clip_seen_at = Column(DateTime, nullable=True)

    # Плёночное зерно на весь собранный клип (ffmpeg-фильтр при склейке).
    film_grain = Column(Boolean, nullable=False, default=False)
    # «Без сюжета»: раскадровка = независимые рандомные панч-кадры по комментарию,
    # сквозной сюжет проекта не требуется и не используется.
    no_story = Column(Boolean, nullable=False, default=False)

    # «Супергенерация»: конвейер сюжет→сцены→кадры→видео→сборка одним нажатием.
    # note — живой прогресс для строки статуса на карточке трека.
    supergen_status = Column(String, nullable=False, default="")  # '' | queued | running | done | error
    supergen_note = Column(Text, nullable=False, default="")

    # ─────────────────────────── РЕСТАЙЛ ───────────────────────────
    # Под какой набор стилей ПИСАНЫ ТЕКСТЫ сцен. Пусто у новых треков:
    # им стиль в image_prompt больше не пишут вовсе (claude.SCENES_SYSTEM),
    # и перерисовка для них — это просто рендер новыми чипами, без единого
    # обращения к модели. Непусто и не совпадает со style_keys — значит
    # раскадровка старая, промпты несут в себе прежний стиль, и перед
    # отрисовкой их надо переписать (claude.restyle_prompts).
    prompts_style_keys = Column(String, nullable=False, default="")
    restyle_status = Column(String, nullable=False, default="")  # '' | queued | running | done | error
    restyle_note = Column(Text, nullable=False, default="")
    # Собранный клип снят в ПРЕЖНЕМ стиле. Не ошибка и не повод стирать
    # файл: человек имеет право оставить старую склейку и пересобрать
    # позже. Но кнопка сборки обязана сказать об этом вслух.
    clip_stale = Column(Boolean, nullable=False, default=False)
    # Переопределение текстовой модели на объекте. В интерфейсе пока не
    # показываем — выбор живёт на проекте; поле есть, чтобы порядок
    # разрешения совпадал с движками картинок (запрос → объект → проект).
    text_engine = Column(String, nullable=False, default="")
    # Из какого объекта скопирован (0 = оригинал). См. Project.copied_from_id.
    copied_from_id = Column(Integer, nullable=False, default=0)

    created_at = Column(DateTime, default=now)
    updated_at = Column(DateTime, default=now, onupdate=now)

    project = relationship("Project", back_populates="tracks")
    scenes = relationship("Scene", back_populates="track", cascade="all, delete-orphan",
                           order_by="Scene.position")
    # Фото товара (режим мокапов). Каскад обязателен: без него удаление трека
    # оставляло бы висячие строки с именами уже удалённых файлов.
    photos = relationship("TrackPhoto", cascade="all, delete-orphan",
                          order_by="TrackPhoto.position")


class TrackPhoto(Base):
    """Фото ТОВАРА для режима мокапов: точная копия механики CharacterPhoto,
    но привязанная к объекту второго уровня, а не к персонажу.

    Почему не персонаж: в мокап-проекте объект второго уровня И ЕСТЬ товар
    (Track = SKU), а персонажей нет вовсе — роль «узнаваемого лица» играет
    сама упаковка. Заводить персонажа на каждый артикул значило бы держать
    два параллельных списка одного и того же.

    Правило то же, что у персонажа: в разворот идут фото kind="photo",
    в кадры — последний kind="model", если он есть."""
    __tablename__ = "track_photos"
    id = Column(Integer, primary_key=True)
    track_id = Column(Integer, ForeignKey("tracks.id"), nullable=False, index=True)
    position = Column(Integer, nullable=False, default=0)
    filename = Column(String, nullable=False)
    kind = Column(String, nullable=False, default="photo")   # photo | model
    from_photos = Column(Integer, nullable=False, default=0)
    created_at = Column(DateTime, default=now)


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

    # Сколько токенов за ЭТУ сцену уже списано. Сцена — единица тарификации:
    # кадры берут аванс, видео добирает разницу до цены своего движка, а
    # перегенерация уже оплаченного не списывает второй раз. Без счётчика
    # пришлось бы либо брать за кадры отдельно (двойная плата за сцену),
    # либо отдавать кадры даром (бесконечная перерисовка на нашей подписке).
    charged_points = Column(Integer, nullable=False, default=0)

    # Каким набором стилей СНЯТЫ нынешние кадры. Заполняется в момент
    # отрисовки. Смешанный трек (перерисовали только припев) — законное
    # состояние, но оно обязано быть видимым: карточка сравнивает это поле
    # со стилем трека и честно помечает кадр «снят в прежнем стиле».
    style_keys = Column(String, nullable=False, default="")
    # ОТПЕЧАТОК ТОГО, ПОД ЧТО СНЯТЫ НЫНЕШНИЕ КАДРЫ: промпты + стиль. По нему
    # «перерисовать все кадры» отличает сцены, которые реально изменились, от
    # тех, где не поменялось ничего. Без него кнопка брала деньги за весь
    # трек всегда — включая тридцать сцен, к которым никто не притрагивался.
    frames_sig = Column(String, nullable=False, default="")

    # ПРОИСХОЖДЕНИЕ КАДРА. copied_from_id — копия (тот же момент времени),
    # continued_from_id — ПРОДОЛЖЕНИЕ: сцена начинается там, где предыдущая
    # закончилась, её первый кадр физически и есть последний кадр той сцены.
    # Разные вещи: продолжение карточка показывает стрелкой и подмешивает в
    # промпт анимации «continues the previous shot», копия — нет.
    copied_from_id = Column(Integer, nullable=False, default=0)
    continued_from_id = Column(Integer, nullable=False, default=0)
    # ВИДЕО СНЯТО НЕ ПОД ЭТОТ СЛОТ. Пересчёт таймингов (вставка кадра рядом)
    # меняет длительность соседей, и уже оплаченное видео перестаёт совпадать
    # со слотом. Удалять его нельзя — это до 152 токенов чужих денег; сборка
    # подрежет по слоту, а карточка обязана сказать об этом вслух.
    video_stale = Column(Boolean, nullable=False, default=False)

    created_at = Column(DateTime, default=now)
    updated_at = Column(DateTime, default=now, onupdate=now)

    track = relationship("Track", back_populates="scenes")
    refs = relationship("SceneRef", back_populates="scene", cascade="all, delete-orphan",
                        order_by="SceneRef.position")
    versions = relationship("SceneVersion", back_populates="scene",
                            cascade="all, delete-orphan",
                            order_by="SceneVersion.id.desc()")


class SceneVersion(Base):
    """ИСТОРИЯ ВАРИАНТОВ КАДРА. Снимок кадров и видео сцены перед тем, как
    на их место лягут новые.

    Изначально это был снимок ТОЛЬКО под рестайл, а во всех остальных
    случаях старое просто стиралось: перерисовка кадра сносила оплаченное
    видео сцены, повторная генерация видео молча затирала предыдущий дубль.
    Человек платил за работу, которую сервис выбрасывал у него на глазах.
    Теперь снимок делается ВСЕГДА, и это одновременно две вещи: история
    вариантов проекта и защита денег.

    Вторую таблицу под «историю креатора» не заводим: эта уже знает про
    файлы (уборщик видит их через _file_in_use), уже проиндексирована по
    сцене и времени и уже показывается в карточке. Ленте кабинета не хватало
    только денормализованных user_id/project_id/track_id — их и добавили,
    ровно тем же приёмом, что в PointEvent.

    Отдельная таблица, а не JSON-колонка в сцене, по двум причинам. Первая:
    у версии есть ФАЙЛЫ на диске, и уборщик (_files_worker) обязан уметь их
    находить — иначе запрос «что ещё занято» перестаёт быть правдой и версия
    однажды указывает в пустоту. Вторая: «снеси версии старше N» по
    JSON-колонке — это полный скан таблицы сцен.

    Видео уезжает СЮДА, а не в мусор. Сцена на Seedance 2.5 стоит 152
    токена, и рестайл, который молча стирает самую дорогую работу человека,
    — это не функция, а потеря данных. В сборку старое видео не попадёт
    (оно снято по другим кадрам), но вернуть его откатом можно.
    """
    __tablename__ = "scene_versions"
    id = Column(Integer, primary_key=True)
    scene_id = Column(Integer, ForeignKey("scenes.id"), nullable=False, index=True)
    created_at = Column(DateTime, default=now, index=True)
    # Денормализация ради ленты «история креатора»: иначе на каждой плитке
    # кабинета нужен join scenes→tracks→projects, а лента листается курсором
    # по времени и обязана быть одним индексным проходом.
    user_id = Column(Integer, nullable=False, default=0, index=True)
    project_id = Column(Integer, nullable=False, default=0, index=True)
    track_id = Column(Integer, nullable=False, default=0)
    # Что именно сменилось: frames | video | midframes | restyle | extend | manual.
    # Нужно и человеку (фильтр в ленте), и вытеснению: видео стоит до 152
    # токенов и держится дольше кадров.
    kind = Column(String, nullable=False, default="")
    # Во сколько обошёлся ЭТОТ вариант и какой строкой журнала он оплачен.
    # Галерея говорит «этот дубль стоил 152 токена → задача kie abc123»:
    # без этого спорную генерацию нечем разобрать.
    cost_points = Column(Integer, nullable=False, default=0)
    point_event_id = Column(Integer, nullable=False, default=0)
    # ЗАКРЕПЛЁННЫЙ вариант не вытесняется ретенцией и не протухает по сроку.
    # Место в квоте он при этом занимает: «не удаляйте это» — законное
    # желание, «храните это бесплатно и вечно» — нет.
    pinned = Column(Boolean, nullable=False, default=False)
    # Сколько весит вариант. Считается в момент снимка, чтобы «сколько
    # занимает история» отвечалось из базы, а не обходом диска.
    bytes = Column(Integer, nullable=False, default=0)
    # Чем снято: ключи стилей и человеческая подпись микса на момент съёмки.
    style_keys = Column(String, nullable=False, default="")
    style_label = Column(String, nullable=False, default="")
    image_filename = Column(String, nullable=False, default="")
    image_last_filename = Column(String, nullable=False, default="")
    image_prompt = Column(Text, nullable=False, default="")
    image_prompt_last = Column(Text, nullable=False, default="")
    video_filename = Column(String, nullable=False, default="")
    audio_filename = Column(String, nullable=False, default="")
    image_engine = Column(String, nullable=False, default="")
    video_engine = Column(String, nullable=False, default="")
    note = Column(String, nullable=False, default="")

    scene = relationship("Scene", back_populates="versions")


class StyleOverride(Base):
    """Наложение админки поверх встроенного каталога стилей.

    ПОЧЕМУ БАЗА, А НЕ ФАЙЛ. Dockerfile копирует код в образ: правка файла
    внутри контейнера умирает при первой же пересборке, а писать в код
    прямо с прода — то, за что репозиторий уже получал расхождение с
    сервером. База лежит томом в /opt/rapclips/data и переживает деплой.

    ДВА СЛОЯ РАЗДЕЛЕНЫ И ЗДЕСЬ — ровно как в backend/prompts_catalog.py.
    Публичные поля (подпись, описание, теги, музыка) уходят на витрину;
    prompt / story_base / structure_json не уходят наружу НИКОГДА и
    читаются только функциями каталога. Публичный ответ собирается по
    белому списку PUBLIC_STYLE_FIELDS, поэтому забыть про новое закрытое
    поле физически нельзя.
    """
    __tablename__ = "style_overrides"
    key = Column(String, primary_key=True)
    # builtin=1 — правка стиля из кода (снятие наложения возвращает
    # заводской), 0 — стиль, заведённый владельцем целиком.
    builtin = Column(Boolean, nullable=False, default=True)
    enabled = Column(Boolean, nullable=False, default=True)

    # ── публичное ──
    label_json = Column(Text, nullable=False, default="")   # {"en": .., "ru": ..}
    desc_json = Column(Text, nullable=False, default="")
    gain_json = Column(Text, nullable=False, default="")
    group = Column(String, nullable=False, default="")
    tier = Column(String, nullable=False, default="")        # free | pro
    prompt_class = Column(String, nullable=False, default="")  # closed | school
    tags_json = Column(Text, nullable=False, default="")
    music_json = Column(Text, nullable=False, default="")
    tempo_json = Column(Text, nullable=False, default="")
    mix_role = Column(String, nullable=False, default="")
    mix_with_json = Column(Text, nullable=False, default="")
    avoid_mix_json = Column(Text, nullable=False, default="")
    engines_json = Column(Text, nullable=False, default="")

    # ── ЗАКРЫТОЕ: наружу не уходит ни при каких условиях ──
    prompt = Column(Text, nullable=False, default="")
    # Как стиль влияет на СЮЖЕТ, а не на картинку. Уходит отдельным блоком
    # в промпты сюжета и раскадровки: до этого стиль умел влиять только на
    # кадр, и «сценарии под стиль» были невозможны в принципе.
    story_base = Column(Text, nullable=False, default="")
    structure_json = Column(Text, nullable=False, default="")

    updated_by = Column(Integer, nullable=False, default=0)
    updated_at = Column(DateTime, default=now, onupdate=now)


class StyleAsset(Base):
    """Файл стиля: превью витрины, пример кадра, референс в генерацию или
    текстовый файл с промптом.

    kind:
      poster | loop | shot  — витрина карточки и страницы стиля;
      ref                   — картинка, которая подмешивается в генерацию
                              кадра (только при in_generation=1);
      promptfile            — .txt/.md, из которого человек переносит текст
                              в поле prompt/story_base кнопкой. В модель
                              уходит ПОЛЕ, а не файл: два места правды —
                              это гарантированный вопрос «а что реально
                              ушло в генерацию».
    """
    __tablename__ = "style_assets"
    id = Column(Integer, primary_key=True)
    style_key = Column(String, nullable=False, index=True)
    kind = Column(String, nullable=False, default="shot")
    filename = Column(String, nullable=False, default="")
    position = Column(Integer, nullable=False, default=0)
    # Уходит ли этот референс в промпт кадра. Персонаж всегда важнее стиля:
    # main.py подмешивает такие картинки ПОСЛЕ персонажных и не более двух,
    # иначе рестайл вылечит один симптом и вернёт другой — «персонажи не
    # похожи», который только что чинили.
    in_generation = Column(Boolean, nullable=False, default=False)
    title = Column(String, nullable=False, default="")
    note = Column(Text, nullable=False, default="")
    created_at = Column(DateTime, default=now)


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
    # ВИД РЕФЕРЕНСА определяет, ЧТО с него берут. Раньше вид был один и промпт
    # трактовал любую приложенную картинку одинаково — «композиция, но не
    # палитра». Из-за этого нельзя было сказать «вот так выглядит место» или
    # «повтори этот кадр целиком»:
    #   vibe  — композиция, ракурс, энергия плана (прежнее поведение);
    #   style — свет, палитра, фактура, зерно (с него КОПИРУЕМ грейд);
    #   place — локация и окружение (обстановка, предметы, время суток);
    #   copy  — повторить кадр целиком, меняя только героя на нашего.
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
    """Журнал токенов: каждое списание и каждое начисление отдельной строкой.

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
    # СЕБЕСТОИМОСТЬ вызова в центах. Без неё маржа сервиса неизвестна в
    # принципе: выручка лежит в processed_payments, а расход — нигде.
    # Считается в момент списания из mediagen.*_engine_usd, то есть из того
    # же прайса, из которого выведена цена в токенах. НАРУЖУ НЕ ОТДАЁТСЯ:
    # из неё восстанавливается наша наценка (см. /api/account/usage).
    cost_cents = Column(Integer, nullable=False, default=0)
    # id внешней задачи (kie/seevio/kling). Списание происходит ДО постановки
    # задачи, поэтому пишется вторым шагом — UPDATE по id строки. Это то,
    # чем разбирается спор: «списали 154 → задача abc123 → упала → возврат».
    task_id = Column(String, nullable=False, default="")
    # Денормализация ради отчёта «сколько ушло на этот проект»: иначе на
    # каждой строке нужен join scenes→tracks→projects.
    project_id = Column(Integer, nullable=False, default=0, index=True)
    track_id = Column(Integer, nullable=False, default=0)


class AdminAction(Base):
    """Что админ сделал руками. Журнал токенов покрывает ТОЛЬКО токены: смена
    тарифа, блокировка и продление в него не ложатся, а знать, кто и когда
    включил человеку ULTRA руками, нужно ровно так же."""
    __tablename__ = "admin_actions"
    id = Column(Integer, primary_key=True)
    admin_id = Column(Integer, nullable=False, default=0, index=True)
    user_id = Column(Integer, nullable=False, default=0, index=True)
    action = Column(String, nullable=False, default="")   # points | plan | block | campaign
    payload_json = Column(Text, nullable=False, default="")
    created_at = Column(DateTime, default=now, index=True)


class Campaign(Base):
    """Рассылка: кому (сегмент), чем (канал), что (текст) и что из этого вышло.

    segment_json — ИМЯ сегмента и его параметры, а не готовый список id:
    список считается в момент отправки. Сохранённый список за неделю
    протухает, и рассылка «новичкам без генераций» уходила бы тем, кто уже
    сделал первый клип."""
    __tablename__ = "campaigns"
    id = Column(Integer, primary_key=True)
    title = Column(String, nullable=False, default="")
    channel = Column(String, nullable=False, default="inapp")  # inapp | tg | email
    segment = Column(String, nullable=False, default="all")
    segment_json = Column(Text, nullable=False, default="")
    subject = Column(String, nullable=False, default="")
    body = Column(Text, nullable=False, default="")
    # transactional=1 — сообщение по делу (оплата, конец подписки): отписки
    # его не глушат. Маркетинговые рассылки по умолчанию 0.
    transactional = Column(Boolean, nullable=False, default=False)
    status = Column(String, nullable=False, default="draft")  # draft | sending | done | error
    error = Column(Text, nullable=False, default="")
    total = Column(Integer, nullable=False, default=0)
    sent = Column(Integer, nullable=False, default=0)
    failed = Column(Integer, nullable=False, default=0)
    read = Column(Integer, nullable=False, default=0)
    created_by = Column(Integer, nullable=False, default=0)
    created_at = Column(DateTime, default=now, index=True)
    finished_at = Column(DateTime, nullable=True)


class CampaignRecipient(Base):
    """Одна строка на человека в рассылке. Она же — ВХОДЯЩЕЕ сообщение для
    канала inapp: отдельного ящика заводить не надо, статус и read_at и есть
    «доставлено / прочитано».

    UNIQUE(campaign_id, user_id) — не украшение: воркер перезапустится, и без
    него половина базы получит письмо дважды."""
    __tablename__ = "campaign_recipients"
    id = Column(Integer, primary_key=True)
    campaign_id = Column(Integer, ForeignKey("campaigns.id"), nullable=False, index=True)
    user_id = Column(Integer, nullable=False, default=0, index=True)
    address = Column(String, nullable=False, default="")   # email / tg_id / ''
    status = Column(String, nullable=False, default="queued")  # queued|sent|failed|skipped
    error = Column(String, nullable=False, default="")
    sent_at = Column(DateTime, nullable=True)
    read_at = Column(DateTime, nullable=True)
    created_at = Column(DateTime, default=now)


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
    # ИЗБРАННОЕ. Не украшение: закреплённая лента не подметается ретенцией
    # (см. chat.sweep_expired). Звезда, которая только сортирует список, а
    # файлы под ней всё равно исчезают по сроку, — врущий контрол.
    pinned = Column(Boolean, nullable=False, default=False)
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
    # ЧЕМ НАРИСОВАНО НА САМОМ ДЕЛЕ. generate_image_ex откатывается по цепочке
    # «запрошенный → ChatGPT-шлюз → Grok-шлюз» и честно возвращает engine, но
    # до этой колонки его никто не записывал: человек платил 8 токенов за
    # Nano Banana Pro и мог получить картинку со шлюза, которая стоит ноль.
    # Пусто = сработало то, что просили.
    engine_actual = Column(String, nullable=False, default="")
    # ВАРИАНТЫ ОДНОГО ЗАПУСКА. N ответов на одну реплику делят общий ключ и
    # показываются одной полосой миниатюр: четыре отдельных пузыря разносят
    # ленту, и сравнить варианты глазом становится нечем.
    group_id = Column(String, nullable=False, default="")
    # Закреплённое сообщение не подметается ретенцией — как и лента.
    pinned = Column(Boolean, nullable=False, default=False)
    # Сколько токенов списано ЗА ЭТО сообщение. Нужно для возврата при ошибке:
    # в студии упавшая сцена теряется в потоке, а в чате запросы одиночные —
    # молча съеденные 154 токена человек увидит сразу.
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
    # ВИД РЕФЕРЕНСА — то же, что у SceneRef.kind (vibe | style | place | copy),
    # и те же тексты правил (backend/refs.py). До этой колонки вложение чата
    # уходило в генерацию без вида, и промпт трактовал ЛЮБУЮ картинку как
    # «композиция, но не палитра»: сказать «вот так выглядит место» было нечем.
    kind = Column(String, nullable=False, default="vibe")
    created_at = Column(DateTime, default=now)

    message = relationship("ChatMessage", back_populates="files")


# ═════════════════════════════ раздел «Музыка» ═════════════════════════════
# Отдельная сущность, а НЕ поля в Track. Track — это объект клип-конвейера:
# он живёт внутри проекта, у него позиция в альбоме, сцены, раскадровка и
# сборка. Музыкальный релиз ничего этого не имеет и имеет своё: замеры
# громкости исходника, мастер, обложку 3000×3000, метаданные площадок и
# состояние заявки в лейбл. Слепить их в одну таблицу значило бы получить
# сорок колонок, половина которых у любой строки пустая, — и невозможность
# ответить на вопрос «сколько у нас релизов» без фильтра по трём признакам.

class MusicTrack(Base):
    """Трек музыкального раздела: загруженный или сгенерированный.

    ИСХОДНИК НЕ ПЕРЕЗАПИСЫВАЕТСЯ НИКОГДА. Мастер — отдельный файл рядом:
    сравнение «до/после» существует только пока есть оба файла, а «улучшить
    задним числом» — это молча подменить человеку то, что он уже слушал.
    """
    __tablename__ = "music_tracks"
    id = Column(Integer, primary_key=True)
    owner_id = Column(Integer, nullable=False, default=0, index=True)
    created_at = Column(DateTime, default=now, index=True)
    updated_at = Column(DateTime, default=now)
    # Мягкое удаление: файлы уносит уборщик, строка остаётся для истории токенов.
    deleted_at = Column(DateTime, nullable=True)

    # ─────────────────────────────── исходник ───────────────────────────────
    source_filename = Column(String, nullable=False, default="")
    # Имя, под которым файл лежал у человека. На диске он уже uuid, а в
    # интерфейсе список из шестнадцатеричных имён нечитаем.
    source_name = Column(String, nullable=False, default="")
    source_ext = Column(String, nullable=False, default="")
    source_bytes = Column(Integer, nullable=False, default=0)
    duration_sec = Column(Float, nullable=False, default=0.0)

    # Замеры исходника (backend/mastering.py measure()). Считаются один раз
    # фоном сразу после приёма файла — до всякой оплаты: это и есть та самая
    # бесплатная демонстрация компетентности из дизайн-системы, §4.12.
    lufs = Column(Float, nullable=False, default=0.0)
    true_peak = Column(Float, nullable=False, default=0.0)
    lra = Column(Float, nullable=False, default=0.0)
    # '' | queued | running | done | error — состояние ЗАМЕРА, не мастеринга.
    probe_status = Column(String, nullable=False, default="")
    probe_note = Column(Text, nullable=False, default="")

    # Разбор дорожки (backend/audio_analysis.py): темп, сетка, секции.
    bpm = Column(Integer, nullable=False, default=0)
    analysis_json = Column(Text, nullable=False, default="")
    # Огибающая для волны — отдельно от analysis_json: волну рисуют на каждом
    # открытии карточки, а полный разбор весит десятки килобайт.
    wave_json = Column(Text, nullable=False, default="")

    # upload — человек принёс свой файл; generated — сгенерировано у нас.
    origin = Column(String, nullable=False, default="upload")
    gen_prompt = Column(Text, nullable=False, default="")
    gen_model = Column(String, nullable=False, default="")

    # ─────────────────────────────── мастеринг ───────────────────────────────
    master_filename = Column(String, nullable=False, default="")
    master_status = Column(String, nullable=False, default="")  # '' | queued | running | done | error
    # На успехе — человеческий отчёт (чем мастерили и что произошло с числами),
    # на ошибке — текст ошибки. Состояние различает master_status.
    master_note = Column(Text, nullable=False, default="")
    master_engine = Column(String, nullable=False, default="")  # matchering | ffmpeg | roex
    master_target = Column(String, nullable=False, default="")  # streaming | club | youtube | cd
    master_ref_filename = Column(String, nullable=False, default="")
    master_ref_title = Column(String, nullable=False, default="")
    master_lufs = Column(Float, nullable=False, default=0.0)
    master_true_peak = Column(Float, nullable=False, default=0.0)
    master_lra = Column(Float, nullable=False, default=0.0)

    # ──────────────────────────── метаданные релиза ────────────────────────────
    title = Column(String, nullable=False, default="")
    artist = Column(String, nullable=False, default="")
    feat = Column(String, nullable=False, default="")
    genre = Column(String, nullable=False, default="")
    language = Column(String, nullable=False, default="")
    version = Column(String, nullable=False, default="")   # radio edit, remix, …
    release_date = Column(String, nullable=False, default="")  # YYYY-MM-DD, как просят площадки
    isrc = Column(String, nullable=False, default="")
    upc = Column(String, nullable=False, default="")
    explicit = Column(Boolean, nullable=False, default=False)
    # Раскрытие ИИ: none | music | vocals | all. Spotify и Deezer уже требуют
    # помечать сгенерированное, и врать здесь дороже, чем признаться.
    ai_disclosure = Column(String, nullable=False, default="")
    credits = Column(Text, nullable=False, default="")
    notes = Column(Text, nullable=False, default="")

    cover_filename = Column(String, nullable=False, default="")
    cover_w = Column(Integer, nullable=False, default=0)
    cover_h = Column(Integer, nullable=False, default=0)

    # ─────────────────────── видео и наши соцсети ───────────────────────
    # Ни Instagram, ни TikTok, ни YouTube не принимают голое аудио, поэтому
    # перед публикацией из обложки и дорожки собирается mp4.
    video_filename = Column(String, nullable=False, default="")
    video_status = Column(String, nullable=False, default="")
    video_note = Column(Text, nullable=False, default="")
    social_status = Column(String, nullable=False, default="")
    social_note = Column(Text, nullable=False, default="")
    social_url = Column(String, nullable=False, default="")
    social_platform = Column(String, nullable=False, default="")

    # ──────────────────────── пакет и заявка в лейбл ────────────────────────
    package_filename = Column(String, nullable=False, default="")
    package_at = Column(DateTime, nullable=True)
    # draft | submitted — состояние ЗАЯВКИ, а не релиза. Слова «опубликован»
    # здесь нет и не будет, пока нет договора с дистрибьютором.
    release_status = Column(String, nullable=False, default="draft")
    submitted_at = Column(DateTime, nullable=True)
    lead_id = Column(Integer, nullable=False, default=0)


class MusicLead(Base):
    """Заявка в лейбл qlolmusic.

    Два входа, одна таблица: форма на странице /music.html (source=music-page)
    и кнопка «Отправить на площадки» в разделе Музыка (source=studio, с
    track_id). Оба конца ведут к живому человеку, потому что автоматической
    отгрузки на площадки у нас нет — см. docs/qlolmusic.md."""
    __tablename__ = "music_leads"
    id = Column(Integer, primary_key=True)
    created_at = Column(DateTime, default=now, index=True)
    user_id = Column(Integer, nullable=False, default=0, index=True)
    track_id = Column(Integer, nullable=False, default=0, index=True)
    name = Column(String, nullable=False, default="")
    contact = Column(String, nullable=False, default="")
    demo = Column(String, nullable=False, default="")
    need = Column(String, nullable=False, default="")       # distribution | mastering | clip | all
    comment = Column(Text, nullable=False, default="")
    lang = Column(String, nullable=False, default="")
    source = Column(String, nullable=False, default="")
    ip = Column(String, nullable=False, default="")
    user_agent = Column(String, nullable=False, default="")
    status = Column(String, nullable=False, default="new")  # new | seen | done
    note = Column(Text, nullable=False, default="")


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
        # СОСТАВНЫЕ индексы. Одиночные index=True на user_id и created_at
        # SQLite не складывает: он возьмёт ОДИН из них, и лента кабинета за
        # 30 дней у активного человека пойдёт частичным сканом. Индекс на
        # engine сознательно НЕ заводим: кардинальность 13, фильтровать после
        # выборки по времени дешевле, чем держать ещё одно дерево.
        for name, ddl in (
            ("ix_pe_user_time", "point_events(user_id, created_at DESC)"),
            ("ix_pe_proj_time", "point_events(project_id, created_at)"),
            ("ix_pe_kind_time", "point_events(kind, created_at)"),
            ("ix_pe_task", "point_events(task_id)"),
            ("ix_fo_user_time", "file_owners(user_id, created_at DESC)"),
            ("ix_fo_user_kind", "file_owners(user_id, kind)"),
            ("ix_cr_camp_user", "campaign_recipients(campaign_id, user_id)"),
            # Версии кадров: лента версий у сцены и вытеснение самой старой
            # (SCENE_VERSIONS_KEEP) — оба запроса идут по scene_id + времени.
            ("ix_sv_scene_time", "scene_versions(scene_id, created_at DESC)"),
            # Лента истории в кабинете: курсор по (время, id) у одного
            # человека и та же лента, прибитая к проекту.
            ("ix_sv_user_time", "scene_versions(user_id, created_at DESC)"),
            ("ix_sv_proj", "scene_versions(project_id, id DESC)"),
            # Кэш кадров: попадание ищется по паре «человек + отпечаток».
            ("ix_fc_user_key", "frame_cache(user_id, key_hash)"),
            # Сумма занятого места по физическим файлам: жёсткие ссылки
            # схлопываются по этому ключу, иначе копия проекта «весит» второй раз.
            ("ix_fo_phys", "file_owners(phys_key)"),
            ("ix_sa_style_kind", "style_assets(style_key, kind, position)"),
        ):
            try:
                uniq = "UNIQUE " if name == "ix_cr_camp_user" else ""
                conn.execute(sqltext(
                    f"CREATE {uniq}INDEX IF NOT EXISTS {name} ON {ddl}"))
            except Exception:  # noqa: BLE001, PERF203
                # Уникальный индекс не встанет, если в старой таблице уже
                # лежат дубли. Это не повод не стартовать: рассылка сама
                # проверяет дубль перед вставкой.
                pass
