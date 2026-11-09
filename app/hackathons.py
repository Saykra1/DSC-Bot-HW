"""Хакатоны: организаторы загружают данные и ответы, участники присылают CSV с предсказаниями, считаем метрику и лидерборд."""

from __future__ import annotations

import shutil
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta
from html import escape
from pathlib import Path

from sqlalchemy import func, select
from sqlalchemy.orm import selectinload

from app import metrics
from app.db import Hackathon, HackathonSubmission, Team, TeamMember, User, utcnow
from app.formatting import as_utc, fmt_dt
from app.service import HomeworkService, ServiceError
from app import teams as tm
from app.teams import TEAM_MAX_LIMIT, TeamService

PUBLIC_FILES = ("train", "test", "baseline", "task")
MAX_CSV_MB = 50


@dataclass
class UploadedFile:
    name: str
    data: bytes


def status_of(h: Hackathon) -> str:
    now = utcnow()
    if now < as_utc(h.starts_at):
        return "upcoming"
    if now <= as_utc(h.ends_at):
        return "running"
    return "finished"


class HackathonService:
    status_of = staticmethod(status_of)

    def __init__(self, hw: HomeworkService):
        self.hw = hw
        self.sm = hw.sm
        self.settings = hw.settings
        self.root = hw.files_dir / "hackathons"
        self.root.mkdir(parents=True, exist_ok=True)
        self.teams = TeamService(self)
        hw.periodic_hooks.append(self.notify_due)

    # ---------- организаторы ----------

    def _validate(self, h: Hackathon, test_raw: bytes, solution_raw: bytes) -> None:
        if h.metric not in metrics.METRICS:
            raise ServiceError("Неизвестная метрика")
        if as_utc(h.ends_at) <= as_utc(h.starts_at):
            raise ServiceError("Окончание должно быть позже начала")
        if not h.id_column or not h.target_column:
            raise ServiceError("Укажите колонку id и целевую колонку")
        if h.team_mode:
            if not 2 <= (h.team_max or 0) <= TEAM_MAX_LIMIT:
                raise ServiceError(f"Размер команды — от 2 до {TEAM_MAX_LIMIT} человек")
            if not h.teams_formed and h.team_deadline is not None and as_utc(h.team_deadline) > as_utc(h.starts_at):
                raise ServiceError("Команды должны сформироваться не позже старта хакатона")
        try:
            test_ids = metrics.ids_of(test_raw, h.id_column, "Test")
            sol_ids = metrics.ids_of(solution_raw, h.id_column, "Ответы (solution)")
            header, _ = metrics.read_csv(solution_raw, "Ответы (solution)")
            if h.target_column not in header:
                raise ServiceError(f"В файле ответов нет колонки «{h.target_column}»")
            # Ответы должны считаться сами с собой без ошибок
            metrics.score(solution_raw, solution_raw, h.id_column, h.target_column, h.metric)
        except metrics.SubmissionError as e:
            raise ServiceError(str(e)) from e
        except ValueError as e:
            raise ServiceError(str(e)) from e
        if len(set(sol_ids)) != len(sol_ids):
            raise ServiceError("В ответах повторяются id")
        if set(test_ids) != set(sol_ids):
            diff = (set(test_ids) ^ set(sol_ids))
            raise ServiceError(f"id в test и в ответах не совпадают (например: {', '.join(list(diff)[:3])})")

    def _save(self, folder: Path, kind: str, f: UploadedFile) -> str:
        suffix = Path(f.name).suffix or ".csv"
        path = folder / f"{kind}{suffix}"
        for old in folder.glob(f"{kind}.*"):
            old.unlink()
        path.write_bytes(f.data)
        return f.name

    async def create(
        self,
        *,
        title: str,
        description: str,
        starts_at: datetime,
        ends_at: datetime,
        metric: str,
        id_column: str,
        target_column: str,
        daily_limit: int,
        train: UploadedFile,
        test: UploadedFile,
        solution: UploadedFile,
        baseline: UploadedFile | None = None,
        task: UploadedFile | None = None,
        team_mode: bool = False,
        team_max: int | None = None,
        team_deadline: datetime | None = None,
    ) -> Hackathon:
        if not (title or "").strip():
            raise ServiceError("Укажите название")
        h = Hackathon(
            title=title.strip()[:200],
            description=_join_description(description, task),
            starts_at=starts_at,
            ends_at=ends_at,
            metric=metric,
            id_column=id_column.strip(),
            target_column=target_column.strip(),
            daily_limit=max(1, daily_limit or 5),
            folder="",
            files={},
            is_active=True,
            announced=False,
            start_notified=False,
            team_mode=bool(team_mode),
            team_max=(team_max or 5) if team_mode else None,
            team_deadline=team_deadline if team_mode else None,
            teams_formed=False if team_mode else None,
        )
        self._validate(h, test.data, solution.data)
        folder = self.root / uuid.uuid4().hex
        folder.mkdir(parents=True)
        files = {}
        for kind, f in (("train", train), ("test", test), ("solution", solution), ("baseline", baseline), ("task", task)):
            if f is not None:
                files[kind] = self._save(folder, kind, f)
        h.folder = str(folder)
        h.files = files
        async with self.sm() as s:
            s.add(h)
            await s.commit()
        self.hw.spawn(self.notify_due())
        return h

    async def update(
        self,
        hackathon_id: int,
        *,
        title: str | None = None,
        description: str | None = None,
        starts_at: datetime | None = None,
        ends_at: datetime | None = None,
        metric: str | None = None,
        id_column: str | None = None,
        target_column: str | None = None,
        daily_limit: int | None = None,
        is_active: bool | None = None,
        train: UploadedFile | None = None,
        test: UploadedFile | None = None,
        solution: UploadedFile | None = None,
        baseline: UploadedFile | None = None,
        task: UploadedFile | None = None,
        team_mode: bool | None = None,
        team_max: int | None = None,
        team_deadline: datetime | None | bool = False,
    ) -> tuple[Hackathon, int]:
        """Правка хакатона. Если поменялись ответы/метрика/колонки — пересчитываем все решения.
        Возвращает (хакатон, сколько решений пересчитано)."""
        async with self.sm() as s:
            h = await s.get(Hackathon, hackathon_id)
            if h is None:
                raise ServiceError("Хакатон не найден")
            folder = Path(h.folder)
            if title is not None:
                if not title.strip():
                    raise ServiceError("Название не может быть пустым")
                h.title = title.strip()[:200]
            if description is not None or task is not None:
                h.description = _join_description(description if description is not None else "", task) or h.description
            if starts_at is not None:
                h.starts_at = starts_at
                h.start_notified = False if utcnow() < as_utc(starts_at) else h.start_notified
            if ends_at is not None:
                h.ends_at = ends_at
            if daily_limit is not None:
                h.daily_limit = max(1, daily_limit)
            if is_active is not None:
                h.is_active = is_active
            if team_mode is not None and bool(team_mode) != bool(h.team_mode):
                has_subs = await s.scalar(select(func.count()).select_from(HackathonSubmission)
                                          .where(HackathonSubmission.hackathon_id == h.id))
                if has_subs:
                    raise ServiceError("Режим (одиночный / командный) нельзя менять, когда уже есть решения")
                h.team_mode = bool(team_mode)
                if h.team_mode and h.teams_formed is None:
                    h.teams_formed = False
            if h.team_mode:
                if team_max is not None:
                    biggest = max((await self.teams._team_sizes(s, h.id)).values(), default=0)
                    if team_max < biggest:
                        raise ServiceError(f"Уже есть команда из {biggest} человек — максимум не может быть меньше")
                    h.team_max = team_max
                h.team_max = h.team_max or 5
                if team_deadline is not False and not h.teams_formed:
                    h.team_deadline = team_deadline
            # Пересчитываем решения, только если реально поменялось то, от чего зависит метрика
            rescore = solution is not None or test is not None or any(
                new is not None and new.strip() != old
                for new, old in ((metric, h.metric), (id_column, h.id_column), (target_column, h.target_column))
            )
            if metric is not None:
                h.metric = metric
            if id_column is not None:
                h.id_column = id_column.strip()
            if target_column is not None:
                h.target_column = target_column.strip()
            test_raw = test.data if test else self._read(h, "test")
            sol_raw = solution.data if solution else self._read(h, "solution")
            self._validate(h, test_raw, sol_raw)
            files = dict(h.files or {})
            for kind, f in (("train", train), ("test", test), ("solution", solution), ("baseline", baseline), ("task", task)):
                if f is not None:
                    files[kind] = self._save(folder, kind, f)
            h.files = files
            await s.commit()
        n = await self.rescore_all(hackathon_id) if rescore else 0
        return h, n

    async def rescore_all(self, hackathon_id: int) -> int:
        h = await self.get(hackathon_id)
        sol = self._read(h, "solution")
        async with self.sm() as s:
            subs = list((await s.scalars(
                select(HackathonSubmission).where(HackathonSubmission.hackathon_id == hackathon_id)
            )).all())
            for sub in subs:
                try:
                    sub.score = metrics.score(Path(sub.file_path).read_bytes(), sol, h.id_column, h.target_column, h.metric)
                    sub.status, sub.error = "done", None
                except (metrics.SubmissionError, ValueError, OSError) as e:
                    sub.score, sub.status, sub.error = None, "error", str(e)
            await s.commit()
        return len(subs)

    # ---------- общее ----------

    async def get(self, hackathon_id: int) -> Hackathon:
        async with self.sm() as s:
            h = await s.get(Hackathon, hackathon_id)
            if h is None:
                raise ServiceError("Хакатон не найден")
            return h

    def _path(self, h: Hackathon, kind: str) -> Path | None:
        matches = list(Path(h.folder).glob(f"{kind}.*"))
        return matches[0] if matches else None

    def _read(self, h: Hackathon, kind: str) -> bytes:
        p = self._path(h, kind)
        if p is None:
            raise ServiceError(f"Файл {kind} не загружен")
        return p.read_bytes()

    async def list_all(self, user_id: int) -> list[dict]:
        admin = self.hw.is_admin(user_id)
        async with self.sm() as s:
            q = select(Hackathon).order_by(Hackathon.starts_at)
            if not admin:
                q = q.where(Hackathon.is_active.is_(True))
            items = list((await s.scalars(q)).all())
        return [await self.to_dict(h, user_id) for h in items]

    async def to_dict(self, h: Hackathon, user_id: int) -> dict:
        admin = self.hw.is_admin(user_id)
        st = status_of(h)
        mine = await self._my_stats(h, user_id)
        files = {k: v for k, v in (h.files or {}).items() if k in PUBLIC_FILES or admin}
        m = metrics.METRICS[h.metric]
        team = {"team_mode": False}
        if tm.is_team(h) and not h.teams_formed and utcnow() >= tm.team_deadline(h):
            await self.teams.form_teams(h.id)  # дедлайн прошёл, а планировщик ещё не успел
            h = await self.get(h.id)
        if tm.is_team(h):
            team = {"team_mode": True, "team_max": tm.team_max(h), "team_deadline": tm.team_deadline(h).isoformat(),
                    "teams_formed": bool(h.teams_formed)}
            if not admin:
                team |= await self.teams.brief(h, user_id)
        return {
            "id": h.id,
            "title": h.title,
            "status": st,
            "starts_at": as_utc(h.starts_at).isoformat(),
            "ends_at": as_utc(h.ends_at).isoformat(),
            "description": h.description if (st != "upcoming" or admin) else "",
            "metric": h.metric,
            "metric_title": m.title,
            "higher_is_better": m.higher_is_better,
            "id_column": h.id_column,
            "target_column": h.target_column,
            "daily_limit": h.daily_limit,
            "is_active": h.is_active,
            "files": files if (st != "upcoming" or admin) else {},
            **team,
            **mine,
        }

    async def _whose(self, h: Hackathon, user_id: int):
        """Чьи решения считать «моими»: в командном хакатоне — всей команды."""
        if tm.is_team(h) and not self.hw.is_admin(user_id):
            team_id = await self.teams.my_team_id(h.id, user_id)
            if team_id is not None:
                return HackathonSubmission.team_id == team_id
        return HackathonSubmission.user_id == user_id

    async def _my_stats(self, h: Hackathon, user_id: int) -> dict:
        whose = await self._whose(h, user_id)
        async with self.sm() as s:
            rows = list((await s.scalars(
                select(HackathonSubmission).where(
                    HackathonSubmission.hackathon_id == h.id,
                    whose,
                    HackathonSubmission.status == "done",
                )
            )).all())
        best = None
        for r in rows:
            if best is None or metrics.better(r.score, best, h.metric):
                best = r.score
        return {"my_best": best, "my_count": len(rows), "used_today": self._count_today(rows)}

    def _count_today(self, rows: list[HackathonSubmission]) -> int:
        tz = self.settings.tz
        today = utcnow().astimezone(tz).date()
        return sum(1 for r in rows if as_utc(r.created_at).astimezone(tz).date() == today)

    async def file(self, hackathon_id: int, kind: str, user_id: int) -> tuple[str, bytes]:
        h = await self.get(hackathon_id)
        admin = self.hw.is_admin(user_id)
        if kind not in PUBLIC_FILES and not (admin and kind == "solution"):
            raise ServiceError("Файл недоступен")
        if not admin and (not h.is_active or status_of(h) == "upcoming"):
            raise ServiceError("Файлы откроются после начала хакатона")
        name = (h.files or {}).get(kind)
        if not name:
            raise ServiceError("Такого файла нет")
        return name, self._read(h, kind)

    # ---------- участники ----------

    async def submit(self, user_id: int, hackathon_id: int, raw: bytes, file_name: str) -> HackathonSubmission:
        h = await self.get(hackathon_id)
        admin = self.hw.is_admin(user_id)
        st = status_of(h)
        if not admin:
            if not h.is_active:
                raise ServiceError("Хакатон закрыт")
            if st == "upcoming":
                raise ServiceError(f"Хакатон начнётся {fmt_dt(h.starts_at, self.settings.tz)} (МСК)")
            if st == "finished":
                raise ServiceError("Приём решений закончился")
        if len(raw) > MAX_CSV_MB * 1024 * 1024:
            raise ServiceError(f"Файл слишком большой (максимум {MAX_CSV_MB} МБ)")
        if file_name and not file_name.lower().endswith((".csv", ".txt")):
            raise ServiceError("Нужен файл .csv")
        team_id = None
        if tm.is_team(h) and not admin:
            await self.teams._ready(h.id)
            h = await self.get(hackathon_id)
            if not h.teams_formed:
                raise ServiceError("Команды ещё формируются. Отправка решений откроется, когда составы будут закреплены.")
            team_id = await self.teams.my_team_id(h.id, user_id)
            if team_id is None:
                raise ServiceError("Хакатон командный: сначала вступи в команду или выбери «Нет команды» в мини аппе")
        mine = await self._my_stats(h, user_id)
        if not admin and mine["used_today"] >= h.daily_limit:
            who = " на команду" if team_id else ""
            raise ServiceError(f"Лимит на сегодня исчерпан: {h.daily_limit} решений в день{who}. Новые попытки — завтра (по МСК).")

        folder = Path(h.folder) / "submissions"
        folder.mkdir(exist_ok=True)
        path = folder / f"{uuid.uuid4().hex}.csv"
        path.write_bytes(raw)
        sub = HackathonSubmission(hackathon_id=h.id, user_id=user_id, team_id=team_id, file_path=str(path),
                                  file_name=file_name[:256])
        try:
            sub.score = metrics.score(raw, self._read(h, "solution"), h.id_column, h.target_column, h.metric)
            sub.status = "done"
        except metrics.SubmissionError as e:
            sub.status, sub.error = "error", str(e)
        async with self.sm() as s:
            s.add(sub)
            await s.commit()
        return sub

    async def my_submissions(self, user_id: int, hackathon_id: int) -> list[dict]:
        h = await self.get(hackathon_id)
        whose = await self._whose(h, user_id)
        async with self.sm() as s:
            rows = list((await s.scalars(
                select(HackathonSubmission)
                .where(HackathonSubmission.hackathon_id == hackathon_id, whose)
                .order_by(HackathonSubmission.id)
                .options(selectinload(HackathonSubmission.user))
            )).all())
        out = []
        for i, r in enumerate(rows, start=1):
            d = _sub_dict(r, i)
            if r.team_id is not None:
                d["author"] = r.user.display_name
                d["is_mine"] = r.user_id == user_id
            out.append(d)
        return out[::-1]

    async def all_submissions(self, hackathon_id: int) -> list[dict]:
        async with self.sm() as s:
            rows = list((await s.scalars(
                select(HackathonSubmission)
                .where(HackathonSubmission.hackathon_id == hackathon_id)
                .order_by(HackathonSubmission.id)
                .options(selectinload(HackathonSubmission.user))
            )).all())
        async with self.sm() as s:
            names = {t.id: t.name for t in (await s.scalars(select(Team).where(Team.hackathon_id == hackathon_id))).all()}
        counters: dict = {}
        out = []
        for r in rows:
            key = ("t", r.team_id) if r.team_id else ("u", r.user_id)
            counters[key] = counters.get(key, 0) + 1
            d = _sub_dict(r, counters[key]) | {"user": r.user.to_dict()}
            if r.team_id:
                d["team_name"] = names.get(r.team_id, "команда удалена")
            out.append(d)
        return out[::-1]

    async def leaderboard(self, hackathon_id: int, viewer_id: int) -> list[dict]:
        h = await self.get(hackathon_id)
        admin = self.hw.is_admin(viewer_id)
        team_mode = tm.is_team(h)
        async with self.sm() as s:
            rows = list((await s.scalars(
                select(HackathonSubmission)
                .where(HackathonSubmission.hackathon_id == hackathon_id, HackathonSubmission.status == "done")
                .order_by(HackathonSubmission.id)
                .options(selectinload(HackathonSubmission.user))
            )).all())
            teams, members = {}, {}
            if team_mode:
                teams = {t.id: t for t in (await s.scalars(select(Team).where(Team.hackathon_id == hackathon_id))).all()}
                for m in (await s.scalars(select(TeamMember).where(TeamMember.hackathon_id == hackathon_id,
                                                                   TeamMember.team_id.is_not(None))
                                          .order_by(TeamMember.joined_at, TeamMember.id)
                                          .options(selectinload(TeamMember.user)))).all():
                    members.setdefault(m.team_id, []).append(m)
        my_team = next((tid for tid, ms in members.items() if any(m.user_id == viewer_id for m in ms)), None)
        best: dict = {}
        count: dict = {}
        last: dict = {}
        for r in rows:
            if self.hw.is_admin(r.user_id):
                continue  # пробные решения организаторов в зачёт не идут
            if team_mode and r.team_id not in teams:
                continue  # команда распалась до старта — таких решений быть не должно
            key = r.team_id if team_mode else r.user_id
            count[key] = count.get(key, 0) + 1
            last[key] = r.created_at
            if key not in best or metrics.better(r.score, best[key].score, h.metric):
                best[key] = r
        sign = -1 if metrics.METRICS[h.metric].higher_is_better else 1
        ordered = sorted(best.items(), key=lambda kv: (sign * kv[1].score, kv[1].id))
        table = []
        for place, (key, r) in enumerate(ordered, start=1):
            row = {
                "place": place,
                "score": r.score,
                "attempts": count[key],
                "best_at": as_utc(r.created_at).isoformat(),
                "last_at": as_utc(last[key]).isoformat(),
            }
            if team_mode:
                row |= {
                    "name": teams[key].name,
                    "team_id": key,
                    "is_me": key == my_team,
                    "members": [m.user.display_name for m in members.get(key, [])],
                }
                if admin:
                    row["users"] = [m.user.to_dict() for m in members.get(key, [])]
            else:
                u: User = r.user
                row |= {"name": u.display_name, "is_me": r.user_id == viewer_id}
                if admin:
                    row["user"] = u.to_dict()
            table.append(row)
        return table

    # ---------- уведомления ----------

    async def notify_due(self) -> None:
        async with self.sm() as s:
            items = list((await s.scalars(select(Hackathon).where(Hackathon.is_active.is_(True)))).all())
        now = utcnow()
        for h in items:
            if tm.is_team(h) and not h.teams_formed and now >= tm.team_deadline(h):
                await self.teams.form_teams(h.id)
        async with self.sm() as s:
            items = list((await s.scalars(select(Hackathon).where(Hackathon.is_active.is_(True)))).all())
            announce = [h for h in items if h.announced is False]
            start = [h for h in items if h.start_notified is False and status_of(h) != "upcoming"]
            for h in announce:
                h.announced = True
            for h in start:
                h.start_notified = True
            await s.commit()
        tz = self.settings.tz
        for h in announce:
            if status_of(h) == "upcoming":
                await self.hw.broadcast(
                    f"🏆 Анонс хакатона <b>{escape(h.title)}</b>\n"
                    f"Пройдёт с {fmt_dt(h.starts_at, tz)} по {fmt_dt(h.ends_at, tz)} (МСК).\n"
                    "Данные и условие появятся в мини аппе во вкладке «Хакатоны» в момент старта."
                    + (self.teams.announce_line(h) if tm.is_team(h) else "")
                )
        for h in start:
            await self.hw.broadcast(
                f"🚀 Хакатон <b>{escape(h.title)}</b> начался!\n"
                f"Решения принимаются до {fmt_dt(h.ends_at, tz)} (МСК), метрика: {metrics.METRICS[h.metric].title}.\n"
                "Данные, условие и лидерборд — в мини аппе во вкладке «Хакатоны» или командой /hackathon."
                + ("\n👥 Хакатон командный: в лидерборде — лучший результат команды, лимит попыток общий." if tm.is_team(h) else "")
            )


def _sub_dict(r: HackathonSubmission, number: int) -> dict:
    return {
        "id": r.id,
        "number": number,
        "status": r.status,
        "score": r.score,
        "error": r.error,
        "file_name": r.file_name,
        "created_at": as_utc(r.created_at).isoformat(),
    }


def _join_description(text: str | None, task: UploadedFile | None) -> str:
    parts = [(text or "").strip()]
    if task is not None:
        try:
            parts.append(task.data.decode("utf-8-sig").strip())
        except UnicodeDecodeError as e:
            raise ServiceError("Файл с заданием должен быть в UTF-8 (.md или .txt)") from e
    return "\n\n".join(p for p in parts if p)


def hackathons_for(hw: HomeworkService) -> HackathonService:
    """Один HackathonService на приложение (его использует и бот, и мини апп)."""
    hs = getattr(hw, "hackathons", None)
    if hs is None:
        hs = HackathonService(hw)
        hw.hackathons = hs
    return hs
