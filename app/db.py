"""Модели БД (SQLite по умолчанию)."""

from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import (
    JSON,
    event,
    BigInteger,
    Boolean,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
    inspect,
    text,
)
from sqlalchemy.types import TypeDecorator
from sqlalchemy.ext.asyncio import AsyncEngine, async_sessionmaker, create_async_engine
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class UTCDateTime(TypeDecorator):
    """Дата-время, которое всегда хранится в UTC и читается с часовым поясом.

    SQLite не хранит пояс: без этого 23:59 по МСК сохранялось бы как 23:59 UTC.
    """

    impl = DateTime
    cache_ok = True

    def process_bind_param(self, value, dialect):
        if value is None:
            return None
        if value.tzinfo is None:
            value = value.replace(tzinfo=timezone.utc)
        return value.astimezone(timezone.utc).replace(tzinfo=None)

    def process_result_value(self, value, dialect):
        if value is None:
            return None
        return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value.astimezone(timezone.utc)


class Base(DeclarativeBase):
    pass


class User(Base):
    __tablename__ = "users"

    tg_id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    username: Mapped[str | None] = mapped_column(String(64))
    full_name: Mapped[str] = mapped_column(String(256), default="")  # имя из профиля Telegram
    nickname: Mapped[str | None] = mapped_column(String(64))  # ник внутри бота (задаёт участник)
    fio: Mapped[str | None] = mapped_column(String(256))  # ФИО для организаторов
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)

    @property
    def display_name(self) -> str:
        """Как показывать участника: ник в боте, иначе имя из Telegram."""
        return self.nickname or self.full_name or (f"@{self.username}" if self.username else str(self.tg_id))

    @property
    def admin_label(self) -> str:
        """Подробная подпись для организаторов: ник · ФИО · @username."""
        parts = [self.display_name]
        if self.fio:
            parts.append(self.fio)
        if self.username:
            parts.append(f"@{self.username}")
        return " · ".join(dict.fromkeys(parts))

    def to_dict(self) -> dict:
        return {
            "tg_id": self.tg_id,
            "username": self.username,
            "tg_name": self.full_name,
            "nickname": self.nickname,
            "fio": self.fio,
            "name": self.display_name,
            "label": self.admin_label,
        }


class Assignment(Base):
    __tablename__ = "assignments"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    title: Mapped[str] = mapped_column(String(200))
    description: Mapped[str] = mapped_column(Text, default="")
    # Ноутбучная часть. Пустые пути — у задания только тест.
    template_path: Mapped[str] = mapped_column(String(500))
    reference_path: Mapped[str] = mapped_column(String(500))
    task_count: Mapped[int] = mapped_column(Integer, default=0)
    max_attempts: Mapped[int] = mapped_column(Integer, default=3)
    deadline: Mapped[datetime | None] = mapped_column(UTCDateTime)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    # Когда задание появится у участников (None — сразу). Время хранится в UTC.
    publish_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
    # Разослано ли уведомление о новом задании. NULL у старых записей = уже не рассылать.
    announced: Mapped[bool | None] = mapped_column(Boolean)
    # Тестовая часть (см. app/quiz.py). None — теста нет.
    quiz: Mapped[dict | None] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)

    submissions: Mapped[list["Submission"]] = relationship(back_populates="assignment")


class Submission(Base):
    __tablename__ = "submissions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    assignment_id: Mapped[int] = mapped_column(ForeignKey("assignments.id"), index=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.tg_id"), index=True)
    # notebook (NULL у старых записей) | quiz
    kind: Mapped[str | None] = mapped_column(String(16))
    file_path: Mapped[str] = mapped_column(String(500))  # у попытки теста пустой
    file_name: Mapped[str] = mapped_column(String(256), default="")
    # queued -> checking -> done | error (попытка теста сразу done)
    status: Mapped[str] = mapped_column(String(16), default="queued", index=True)
    score: Mapped[int | None] = mapped_column(Integer)
    max_score: Mapped[int | None] = mapped_column(Integer)
    result: Mapped[dict | None] = mapped_column(JSON)
    error: Mapped[str | None] = mapped_column(Text)
    auto_score: Mapped[int | None] = mapped_column(Integer)  # оценка агента до правок организатора
    edited_at: Mapped[datetime | None] = mapped_column(UTCDateTime)  # когда организатор менял баллы
    edited_by: Mapped[str | None] = mapped_column(String(100))  # имя организатора, который менял баллы
    organizer_comment: Mapped[str | None] = mapped_column(Text)
    comment_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
    comment_by: Mapped[str | None] = mapped_column(String(100))  # имя организатора, который оставил комментарий
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)
    checked_at: Mapped[datetime | None] = mapped_column(UTCDateTime)

    assignment: Mapped[Assignment] = relationship(back_populates="submissions")
    user: Mapped[User] = relationship()


class GradeCacheEntry(Base):
    """Оценка уже проверенного решения задания — чтобы одинаковый код получал одинаковую оценку."""

    __tablename__ = "grade_cache"
    __table_args__ = (UniqueConstraint("assignment_id", "task_number", "code_hash"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    assignment_id: Mapped[int] = mapped_column(ForeignKey("assignments.id"), index=True)
    task_number: Mapped[int] = mapped_column(Integer)
    code_hash: Mapped[str] = mapped_column(String(64))
    data: Mapped[dict] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)


class Hackathon(Base):
    __tablename__ = "hackathons"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    title: Mapped[str] = mapped_column(String(200))
    description: Mapped[str] = mapped_column(Text, default="")  # условие (текст или содержимое .md)
    starts_at: Mapped[datetime] = mapped_column(UTCDateTime)
    ends_at: Mapped[datetime] = mapped_column(UTCDateTime)
    metric: Mapped[str] = mapped_column(String(32))
    id_column: Mapped[str] = mapped_column(String(100))
    target_column: Mapped[str] = mapped_column(String(100))
    daily_limit: Mapped[int] = mapped_column(Integer, default=5)
    folder: Mapped[str] = mapped_column(String(500))  # папка с train/test/baseline/solution
    # Имена файлов для участников: {"train": "train.csv", "test": "test.csv", "baseline": "baseline.ipynb"}
    files: Mapped[dict] = mapped_column(JSON, default=dict)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    announced: Mapped[bool | None] = mapped_column(Boolean)
    start_notified: Mapped[bool | None] = mapped_column(Boolean)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)


class HackathonSubmission(Base):
    __tablename__ = "hackathon_submissions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    hackathon_id: Mapped[int] = mapped_column(ForeignKey("hackathons.id"), index=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.tg_id"), index=True)
    file_path: Mapped[str] = mapped_column(String(500))
    file_name: Mapped[str] = mapped_column(String(256), default="")
    status: Mapped[str] = mapped_column(String(16), default="done")  # done | error
    score: Mapped[float | None] = mapped_column(Float)
    error: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)

    user: Mapped[User] = relationship()


# Сколько миллисекунд запрос ждёт, если база занята другой записью, прежде чем упасть с «database is locked»
SQLITE_BUSY_TIMEOUT_MS = 15000


def make_engine(url: str) -> AsyncEngine:
    if not url.startswith("sqlite"):
        return create_async_engine(url, future=True)
    engine = create_async_engine(url, future=True, connect_args={"timeout": SQLITE_BUSY_TIMEOUT_MS / 1000})

    @event.listens_for(engine.sync_engine, "connect")
    def _sqlite_pragmas(dbapi_conn, _record):
        # WAL: чтения не ждут записи и наоборот (важно, когда много участников разом и идёт бэкап).
        # synchronous=NORMAL в WAL безопасен для целостности и заметно быстрее на записи.
        cur = dbapi_conn.cursor()
        if ":memory:" not in url:
            cur.execute("PRAGMA journal_mode=WAL")
        cur.execute("PRAGMA synchronous=NORMAL")
        cur.execute(f"PRAGMA busy_timeout={SQLITE_BUSY_TIMEOUT_MS}")
        cur.close()

    return engine


def make_sessionmaker(engine: AsyncEngine) -> async_sessionmaker:
    return async_sessionmaker(engine, expire_on_commit=False)


async def init_db(engine: AsyncEngine) -> None:
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
        await conn.run_sync(_add_missing_columns)


def _add_missing_columns(sync_conn) -> None:
    """Простейшая миграция: добавляет в существующие таблицы новые колонки (все они допускают NULL).

    Нужна, чтобы обновление кода не требовало удалять базу с уже сданными работами.
    """
    insp = inspect(sync_conn)
    for table in Base.metadata.sorted_tables:
        existing = {c["name"] for c in insp.get_columns(table.name)}
        for col in table.columns:
            if col.name not in existing:
                col_type = col.type.compile(dialect=sync_conn.dialect)
                sync_conn.execute(text(f'ALTER TABLE "{table.name}" ADD COLUMN "{col.name}" {col_type}'))
