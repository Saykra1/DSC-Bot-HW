"""Модели БД (SQLite по умолчанию)."""

from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import JSON, BigInteger, Boolean, DateTime, ForeignKey, Integer, String, Text, inspect, text
from sqlalchemy.ext.asyncio import AsyncEngine, async_sessionmaker, create_async_engine
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class Base(DeclarativeBase):
    pass


class User(Base):
    __tablename__ = "users"

    tg_id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    username: Mapped[str | None] = mapped_column(String(64))
    full_name: Mapped[str] = mapped_column(String(256), default="")  # имя из профиля Telegram
    nickname: Mapped[str | None] = mapped_column(String(64))  # ник внутри бота (задаёт участник)
    fio: Mapped[str | None] = mapped_column(String(256))  # ФИО для организаторов
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

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
    template_path: Mapped[str] = mapped_column(String(500))
    reference_path: Mapped[str] = mapped_column(String(500))
    task_count: Mapped[int] = mapped_column(Integer, default=0)
    max_attempts: Mapped[int] = mapped_column(Integer, default=3)
    deadline: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    submissions: Mapped[list["Submission"]] = relationship(back_populates="assignment")


class Submission(Base):
    __tablename__ = "submissions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    assignment_id: Mapped[int] = mapped_column(ForeignKey("assignments.id"), index=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.tg_id"), index=True)
    file_path: Mapped[str] = mapped_column(String(500))
    file_name: Mapped[str] = mapped_column(String(256), default="")
    # queued -> checking -> done | error
    status: Mapped[str] = mapped_column(String(16), default="queued", index=True)
    score: Mapped[int | None] = mapped_column(Integer)
    max_score: Mapped[int | None] = mapped_column(Integer)
    result: Mapped[dict | None] = mapped_column(JSON)
    error: Mapped[str | None] = mapped_column(Text)
    auto_score: Mapped[int | None] = mapped_column(Integer)  # оценка агента до правок организатора
    edited_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))  # когда организатор менял баллы
    organizer_comment: Mapped[str | None] = mapped_column(Text)
    comment_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    checked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    assignment: Mapped[Assignment] = relationship(back_populates="submissions")
    user: Mapped[User] = relationship()


def make_engine(url: str) -> AsyncEngine:
    return create_async_engine(url, future=True)


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
