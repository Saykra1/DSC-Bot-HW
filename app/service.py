"""Бизнес-логика: задания, сдачи, очередь проверки. Используется и ботом, и мини аппом."""

from __future__ import annotations

import asyncio
import csv
import io
import logging
import uuid
from dataclasses import dataclass
from datetime import datetime
from html import escape
from pathlib import Path
from typing import Awaitable, Callable

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import async_sessionmaker
from sqlalchemy.orm import selectinload

from app.checker import CheckResult, JsonLLM, PreparedAssignment, check_submission, prepare_assignment
from app.config import Settings
from app.db import Assignment, Submission, User, utcnow
from app.formatting import as_utc, feedback_messages
from app.notebook import NotebookError, load_notebook, strip_outputs

log = logging.getLogger(__name__)

# notifier(tg_id, [сообщения в HTML])
Notifier = Callable[[int, list[str]], Awaitable[None]]


class ServiceError(Exception):
    """Ошибка, текст которой можно показать пользователю."""


@dataclass
class AssignmentView:
    assignment: Assignment
    attempts_used: int
    best_score: int | None
    max_score: int

    def to_dict(self, tz) -> dict:
        a = self.assignment
        return {
            "id": a.id,
            "title": a.title,
            "description": a.description,
            "task_count": a.task_count,
            "max_attempts": a.max_attempts,
            "attempts_used": self.attempts_used,
            "deadline": as_utc(a.deadline).isoformat() if a.deadline else None,
            "is_active": a.is_active,
            "best_score": self.best_score,
            "max_score": self.max_score,
        }


def submission_to_dict(s: Submission, include_user: bool = False) -> dict:
    d = {
        "id": s.id,
        "assignment_id": s.assignment_id,
        "status": s.status,
        "score": s.score,
        "max_score": s.max_score,
        "file_name": s.file_name,
        "created_at": as_utc(s.created_at).isoformat() if s.created_at else None,
        "checked_at": as_utc(s.checked_at).isoformat() if s.checked_at else None,
        "result": s.result,
        "error": s.error,
    }
    if include_user and s.user is not None:
        d["user"] = {"tg_id": s.user.tg_id, "name": s.user.display_name}
    return d


class HomeworkService:
    def __init__(self, settings: Settings, sessionmaker: async_sessionmaker, llm: JsonLLM | None):
        self.settings = settings
        self.sm = sessionmaker
        self.llm = llm
        self.notifier: Notifier | None = None
        self._queue: asyncio.Queue[int] = asyncio.Queue()
        self._workers: list[asyncio.Task] = []
        self._prepared: dict[int, PreparedAssignment] = {}
        self.files_dir = settings.data_dir / "files"
        self.files_dir.mkdir(parents=True, exist_ok=True)

    # ---------- пользователи ----------

    async def upsert_user(self, tg_id: int, username: str | None, full_name: str) -> User:
        async with self.sm() as s:
            user = await s.get(User, tg_id)
            if user is None:
                user = User(tg_id=tg_id, username=username, full_name=full_name or "")
                s.add(user)
            else:
                user.username = username
                user.full_name = full_name or user.full_name
            await s.commit()
            return user

    def is_admin(self, tg_id: int) -> bool:
        return self.settings.is_admin(tg_id)

    # ---------- задания ----------

    async def create_assignment(
        self,
        title: str,
        description: str,
        template_raw: bytes,
        reference_raw: bytes,
        max_attempts: int | None = None,
        deadline: datetime | None = None,
    ) -> tuple[Assignment, list[str]]:
        title = (title or "").strip()
        if not title:
            raise ServiceError("Укажите название задания")
        try:
            prepared = prepare_assignment(template_raw, reference_raw, self.settings.task_markers)
        except NotebookError as e:
            raise ServiceError(str(e)) from e

        folder = self.files_dir / "assignments" / uuid.uuid4().hex
        folder.mkdir(parents=True, exist_ok=True)
        tpl_path = folder / "template.ipynb"
        ref_path = folder / "reference.ipynb"
        tpl_path.write_bytes(strip_outputs(template_raw))
        ref_path.write_bytes(reference_raw)

        async with self.sm() as s:
            a = Assignment(
                title=title[:200],
                description=(description or "").strip(),
                template_path=str(tpl_path),
                reference_path=str(ref_path),
                task_count=len(prepared.tasks),
                max_attempts=max_attempts or self.settings.default_max_attempts,
                deadline=deadline,
                is_active=True,
            )
            s.add(a)
            await s.commit()
        self._prepared[a.id] = prepared
        return a, prepared.warnings

    async def set_active(self, assignment_id: int, active: bool) -> Assignment:
        async with self.sm() as s:
            a = await s.get(Assignment, assignment_id)
            if a is None:
                raise ServiceError("Задание не найдено")
            a.is_active = active
            await s.commit()
            return a

    async def get_assignment(self, assignment_id: int) -> Assignment:
        async with self.sm() as s:
            a = await s.get(Assignment, assignment_id)
            if a is None:
                raise ServiceError("Задание не найдено")
            return a

    async def list_assignments(self, user_id: int, include_inactive: bool = False) -> list[AssignmentView]:
        async with self.sm() as s:
            q = select(Assignment).order_by(Assignment.id)
            if not include_inactive:
                q = q.where(Assignment.is_active.is_(True))
            assignments = list((await s.scalars(q)).all())
            stats = {}
            rows = await s.execute(
                select(Submission.assignment_id, func.count(Submission.id), func.max(Submission.score))
                .where(Submission.user_id == user_id, Submission.status != "error")
                .group_by(Submission.assignment_id)
            )
            for aid, cnt, best in rows:
                stats[aid] = (cnt, best)
        return [
            AssignmentView(a, stats.get(a.id, (0, None))[0], stats.get(a.id, (0, None))[1], a.task_count * 10)
            for a in assignments
        ]

    async def template_bytes(self, assignment_id: int) -> tuple[str, bytes]:
        a = await self.get_assignment(assignment_id)
        return f"{_safe_name(a.title)}.ipynb", Path(a.template_path).read_bytes()

    async def _get_prepared(self, a: Assignment) -> PreparedAssignment:
        if a.id not in self._prepared:
            self._prepared[a.id] = prepare_assignment(
                Path(a.template_path).read_bytes(), Path(a.reference_path).read_bytes(), self.settings.task_markers
            )
        return self._prepared[a.id]

    # ---------- сдачи ----------

    async def submit(self, user_id: int, assignment_id: int, raw: bytes, file_name: str) -> Submission:
        if len(raw) > self.settings.max_upload_mb * 1024 * 1024:
            raise ServiceError(f"Файл слишком большой (максимум {self.settings.max_upload_mb} МБ)")
        if file_name and not file_name.lower().endswith(".ipynb"):
            raise ServiceError("Нужен файл .ipynb")
        try:
            load_notebook(raw)
        except NotebookError as e:
            raise ServiceError(str(e)) from e

        async with self.sm() as s:
            a = await s.get(Assignment, assignment_id)
            if a is None:
                raise ServiceError("Задание не найдено")
            if not a.is_active:
                raise ServiceError("Приём работ по этому заданию закрыт")
            if a.deadline and utcnow() > as_utc(a.deadline):
                raise ServiceError("Дедлайн по этому заданию уже прошёл")
            used = await s.scalar(
                select(func.count(Submission.id)).where(
                    Submission.user_id == user_id,
                    Submission.assignment_id == assignment_id,
                    Submission.status != "error",
                )
            )
            if not self.is_admin(user_id) and used >= a.max_attempts:
                raise ServiceError(f"Попытки закончились ({a.max_attempts} из {a.max_attempts})")
            in_progress = await s.scalar(
                select(func.count(Submission.id)).where(
                    Submission.user_id == user_id,
                    Submission.assignment_id == assignment_id,
                    Submission.status.in_(["queued", "checking"]),
                )
            )
            if in_progress:
                raise ServiceError("Предыдущая работа ещё проверяется, дождись результата")

            path = self.files_dir / "submissions" / f"{uuid.uuid4().hex}.ipynb"
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(raw)
            sub = Submission(
                assignment_id=assignment_id, user_id=user_id, file_path=str(path), file_name=file_name[:256], status="queued"
            )
            s.add(sub)
            await s.commit()
        await self._queue.put(sub.id)
        return sub

    async def attempt_number(self, submission: Submission) -> int:
        async with self.sm() as s:
            return await s.scalar(
                select(func.count(Submission.id)).where(
                    Submission.user_id == submission.user_id,
                    Submission.assignment_id == submission.assignment_id,
                    Submission.id <= submission.id,
                    Submission.status != "error",
                )
            ) or 1

    async def get_submission(self, submission_id: int) -> Submission:
        async with self.sm() as s:
            sub = await s.scalar(
                select(Submission)
                .where(Submission.id == submission_id)
                .options(selectinload(Submission.user), selectinload(Submission.assignment))
            )
            if sub is None:
                raise ServiceError("Сдача не найдена")
            return sub

    async def list_user_submissions(self, user_id: int, assignment_id: int | None = None) -> list[Submission]:
        async with self.sm() as s:
            q = select(Submission).where(Submission.user_id == user_id).order_by(Submission.id.desc())
            if assignment_id is not None:
                q = q.where(Submission.assignment_id == assignment_id)
            q = q.options(selectinload(Submission.assignment))
            return list((await s.scalars(q)).all())

    async def list_assignment_submissions(self, assignment_id: int) -> list[Submission]:
        async with self.sm() as s:
            q = (
                select(Submission)
                .where(Submission.assignment_id == assignment_id)
                .order_by(Submission.id.desc())
                .options(selectinload(Submission.user))
            )
            return list((await s.scalars(q)).all())

    async def recheck(self, submission_id: int) -> Submission:
        async with self.sm() as s:
            sub = await s.get(Submission, submission_id)
            if sub is None:
                raise ServiceError("Сдача не найдена")
            if sub.status in ("queued", "checking"):
                raise ServiceError("Сдача уже в очереди")
            sub.status = "queued"
            sub.error = None
            await s.commit()
        await self._queue.put(submission_id)
        return sub

    async def export_csv(self, assignment_id: int) -> str:
        """Лучший результат каждого участника по заданию."""
        a = await self.get_assignment(assignment_id)
        subs = await self.list_assignment_submissions(assignment_id)
        best: dict[int, Submission] = {}
        attempts: dict[int, int] = {}
        for sub in subs:
            if sub.status != "done":
                continue
            attempts[sub.user_id] = attempts.get(sub.user_id, 0) + 1
            if sub.user_id not in best or (sub.score or 0) > (best[sub.user_id].score or 0):
                best[sub.user_id] = sub
        buf = io.StringIO()
        w = csv.writer(buf)
        header = ["tg_id", "username", "name", "best_score", "max_score", "attempts"]
        header += [f"task_{i}" for i in range(1, a.task_count + 1)]
        w.writerow(header)
        for uid, sub in sorted(best.items(), key=lambda kv: -(kv[1].score or 0)):
            task_scores = [t.get("score") for t in (sub.result or {}).get("tasks", [])]
            w.writerow([uid, sub.user.username or "", sub.user.full_name, sub.score, sub.max_score, attempts[uid], *task_scores])
        return buf.getvalue()

    # ---------- очередь проверки ----------

    async def start(self) -> None:
        async with self.sm() as s:
            pending = list(
                (await s.scalars(select(Submission.id).where(Submission.status.in_(["queued", "checking"])))).all()
            )
        for sid in pending:
            await self._queue.put(sid)
        for _ in range(max(1, self.settings.check_concurrency)):
            self._workers.append(asyncio.create_task(self._worker()))

    async def stop(self) -> None:
        for w in self._workers:
            w.cancel()
        await asyncio.gather(*self._workers, return_exceptions=True)
        self._workers.clear()

    async def wait_idle(self) -> None:
        await self._queue.join()

    async def _worker(self) -> None:
        while True:
            sid = await self._queue.get()
            try:
                await self.process(sid)
            except Exception:  # noqa: BLE001 — воркер не должен падать
                log.exception("Ошибка при обработке сдачи %s", sid)
            finally:
                self._queue.task_done()

    async def process(self, submission_id: int) -> None:
        async with self.sm() as s:
            sub = await s.get(Submission, submission_id)
            if sub is None or sub.status == "done":
                return
            a = await s.get(Assignment, sub.assignment_id)
            sub.status = "checking"
            await s.commit()

        try:
            prepared = await self._get_prepared(a)
            result: CheckResult = await check_submission(prepared, Path(sub.file_path).read_bytes(), self.llm, a.title)
        except Exception as e:  # noqa: BLE001
            log.exception("Проверка сдачи %s не удалась", submission_id)
            async with self.sm() as s:
                sub = await s.get(Submission, submission_id)
                sub.status = "error"
                sub.error = str(e)[:2000]
                await s.commit()
            await self._notify(
                sub.user_id,
                [f"⚠️ Не получилось автоматически проверить работу по «{escape(a.title)}». "
                 "Попытка не засчитана, организаторы уже в курсе."],
            )
            for admin in self.settings.admin_ids:
                await self._notify(admin, [f"⚠️ Ошибка проверки сдачи #{submission_id} ({escape(a.title)}): {escape(str(e)[:500])}\n"
                                           f"Перепроверить: /recheck {submission_id}"])
            return

        async with self.sm() as s:
            sub = await s.get(Submission, submission_id)
            sub.status = "done"
            sub.score = result.score
            sub.max_score = result.max_score
            sub.result = result.to_dict()
            sub.checked_at = utcnow()
            await s.commit()
        attempt = await self.attempt_number(sub)
        await self._notify(sub.user_id, feedback_messages(a.title, attempt, result))

    async def _notify(self, tg_id: int, messages: list[str]) -> None:
        if self.notifier is None:
            return
        try:
            await self.notifier(tg_id, messages)
        except Exception:  # noqa: BLE001
            log.exception("Не удалось отправить сообщение %s", tg_id)


def _safe_name(title: str) -> str:
    keep = "".join(ch if ch.isalnum() or ch in " _-" else "_" for ch in title).strip()
    return (keep or "homework").replace(" ", "_")[:60]
