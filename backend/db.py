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


class Project(Base):
    __tablename__ = "projects"
    id = Column(Integer, primary_key=True)
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


class CharacterPhoto(Base):
    __tablename__ = "character_photos"
    id = Column(Integer, primary_key=True)
    character_id = Column(Integer, ForeignKey("characters.id"), nullable=False)
    position = Column(Integer, nullable=False, default=0)
    filename = Column(String, nullable=False)
    created_at = Column(DateTime, default=now)

    character = relationship("Character", back_populates="photos")


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
    audio_filename = Column(String, nullable=False, default="")
    audio_duration_sec = Column(Integer, nullable=False, default=0)
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
