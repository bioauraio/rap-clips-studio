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
    # подписки. Гость стартует с 60, админ получает практически бесконечность.
    gen_points = Column(Integer, nullable=False, default=60)
    # Внешние входы: аккаунт создаётся и опознаётся по Telegram-ID или Яндекс-ID,
    # пароль при этом не нужен. Уникальность проверяется кодом (мягкие миграции
    # не умеют добавлять UNIQUE к существующей таблице).
    tg_id = Column(String, nullable=False, default="")
    tg_username = Column(String, nullable=False, default="")
    yandex_id = Column(String, nullable=False, default="")
    avatar_url = Column(String, nullable=False, default="")


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
    # album — альбом на несколько треков, single — сингл с одним треком.
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
    created_at = Column(DateTime, default=now)
    updated_at = Column(DateTime, default=now, onupdate=now)

    project = relationship("Project", backref="characters")
    photos = relationship("CharacterPhoto", back_populates="character",
                          cascade="all, delete-orphan", order_by="CharacterPhoto.position")
    attributes = relationship("CharacterAttribute", back_populates="character",
                              cascade="all, delete-orphan",
                              order_by="CharacterAttribute.position")


class CharacterPhoto(Base):
    __tablename__ = "character_photos"
    id = Column(Integer, primary_key=True)
    character_id = Column(Integer, ForeignKey("characters.id"), nullable=False)
    position = Column(Integer, nullable=False, default=0)
    filename = Column(String, nullable=False)
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
    # Визуальный стиль ЭТОГО трека: "3D мультяшный", "аниме", "реализм", своя формулировка.
    style = Column(String, nullable=False, default="")
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
