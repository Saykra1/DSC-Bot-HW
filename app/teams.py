"""Команды в хакатонах: создание, приглашения, заявки, автораспределение тех, кто без команды.

Правила:
- Команды формируются до «дедлайна команд» (по умолчанию — старт хакатона). В этот момент всех
  зарегистрированных без команды случайно раскладываем по новым командам ровных размеров.
- После этого составы закреплены: выйти или выгнать участника нельзя (только организатор).
  Опоздавшие по-прежнему могут создать команду, попроситься в команду со свободным местом
  или попасть в случайную команду сразу.
- Приглашать и отправлять заявки можно только тем, кто сейчас без команды.
"""

from __future__ import annotations

import random
from html import escape
from typing import TYPE_CHECKING

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.db import Hackathon, Team, TeamMember, TeamRequest, User, utcnow
from app.formatting import as_utc, fmt_dt
from app.service import ServiceError

if TYPE_CHECKING:
    from app.hackathons import HackathonService

TEAM_MAX_LIMIT = 10
DEFAULT_TEAM_MAX = 5
NAME_MIN, NAME_MAX = 2, 40
MAX_PENDING_JOINS = 5


def is_team(h: Hackathon) -> bool:
    return bool(h.team_mode)


def team_max(h: Hackathon) -> int:
    return h.team_max or DEFAULT_TEAM_MAX


def team_deadline(h: Hackathon):
    return as_utc(h.team_deadline or h.starts_at)


def short_fio(fio: str | None) -> str | None:
    """«Иванов Иван Иванович» -> «Иванов И. И.»: капитан узнаёт человека, а полное ФИО остаётся у организаторов."""
    parts = (fio or "").split()
    if not parts:
        return None
    return " ".join([parts[0], *(p[0].upper() + "." for p in parts[1:3])])


def _norm(name: str) -> str:
    return " ".join(name.split()).casefold()


def _user_brief(u: User, *, admin: bool = False) -> dict:
    d = {"tg_id": u.tg_id, "name": u.display_name, "username": u.username}
    if admin:
        d["user"] = u.to_dict()
    return d


class TeamService:
    def __init__(self, hs: "HackathonService"):
        self.hs = hs
        self.hw = hs.hw
        self.sm = hs.sm
        self.tz = hs.settings.tz

    # ---------- общее ----------

    async def _hack(self, s: AsyncSession, hackathon_id: int) -> Hackathon:
        h = await s.get(Hackathon, hackathon_id)
        if h is None or (not h.is_active):
            raise ServiceError("Хакатон не найден")
        if not is_team(h):
            raise ServiceError("Это не командный хакатон")
        return h

    async def _ready(self, hackathon_id: int) -> None:
        """Если дедлайн команд прошёл, а планировщик ещё не успел — распределяем прямо сейчас."""
        async with self.sm() as s:
            h = await s.get(Hackathon, hackathon_id)
            due = h is not None and is_team(h) and not h.teams_formed and utcnow() >= team_deadline(h)
        if due:
            await self.form_teams(hackathon_id)

    def _check_open(self, h: Hackathon) -> None:
        if self.hs.status_of(h) == "finished":
            raise ServiceError("Хакатон уже закончился")

    def _no_admin(self, user_id: int) -> None:
        if self.hw.is_admin(user_id):
            raise ServiceError("Организаторы не участвуют в командах")

    async def _member(self, s: AsyncSession, hackathon_id: int, user_id: int) -> TeamMember | None:
        return await s.scalar(select(TeamMember).where(TeamMember.hackathon_id == hackathon_id, TeamMember.user_id == user_id))

    async def _team_members(self, s: AsyncSession, team_id: int) -> list[TeamMember]:
        return list((await s.scalars(
            select(TeamMember).where(TeamMember.team_id == team_id)
            .order_by(TeamMember.joined_at, TeamMember.id).options(selectinload(TeamMember.user))
        )).all())

    async def _team_sizes(self, s: AsyncSession, hackathon_id: int) -> dict[int, int]:
        rows = (await s.scalars(select(TeamMember.team_id).where(
            TeamMember.hackathon_id == hackathon_id, TeamMember.team_id.is_not(None)))).all()
        sizes: dict[int, int] = {}
        for tid in rows:
            sizes[tid] = sizes.get(tid, 0) + 1
        return sizes

    async def _check_name(self, s: AsyncSession, hackathon_id: int, name: str, skip_team: int | None = None) -> str:
        name = " ".join((name or "").split())
        if len(name) < NAME_MIN:
            raise ServiceError("Название команды слишком короткое")
        if len(name) > NAME_MAX:
            raise ServiceError(f"Название команды — не длиннее {NAME_MAX} символов")
        names = (await s.execute(select(Team.id, Team.name).where(Team.hackathon_id == hackathon_id))).all()
        if any(_norm(n) == _norm(name) and tid != skip_team for tid, n in names):
            raise ServiceError("Команда с таким названием уже есть")
        return name

    async def _cancel_pending(self, s: AsyncSession, hackathon_id: int, *, user_id: int | None = None,
                              team_id: int | None = None) -> None:
        q = select(TeamRequest).where(TeamRequest.hackathon_id == hackathon_id, TeamRequest.status == "pending")
        if user_id is not None:
            q = q.where(TeamRequest.user_id == user_id)
        if team_id is not None:
            q = q.where(TeamRequest.team_id == team_id)
        for r in (await s.scalars(q)).all():
            r.status, r.decided_at = "cancelled", utcnow()

    async def _put_in_team(self, s: AsyncSession, h: Hackathon, team: Team, user_id: int) -> None:
        """Добавляет человека в команду (проверяет места) и снимает его остальные заявки и приглашения."""
        sizes = await self._team_sizes(s, h.id)
        if sizes.get(team.id, 0) >= team_max(h):
            raise ServiceError(f"В команде «{team.name}» уже нет мест ({team_max(h)} из {team_max(h)})")
        m = await self._member(s, h.id, user_id)
        if m is not None and m.team_id is not None:
            raise ServiceError("Человек уже в команде")
        if m is None:
            m = TeamMember(hackathon_id=h.id, user_id=user_id)
            s.add(m)
        m.team_id, m.intent, m.joined_at = team.id, None, utcnow()
        await s.flush()
        await self._cancel_pending(s, h.id, user_id=user_id)

    async def _drop_member(self, s: AsyncSession, h: Hackathon, m: TeamMember) -> tuple[Team | None, int | None]:
        """Убирает человека из команды и из хакатона. Возвращает (команда или None, если распалась; новый капитан)."""
        return await self._detach(s, h, m, delete=True)

    async def _detach(self, s: AsyncSession, h: Hackathon, m: TeamMember, delete: bool = False) -> tuple[Team | None, int | None]:
        """Убирает человека из команды (delete — ещё и из хакатона, иначе он остаётся «без команды»).
        Возвращает (команда или None, если она распалась; новый капитан, если сменился)."""
        team = await s.get(Team, m.team_id)
        if delete:
            await s.delete(m)
        else:
            m.team_id, m.intent, m.joined_at = None, "pool", None
        await s.flush()
        rest = await self._team_members(s, team.id)
        if not rest:
            await self._cancel_pending(s, h.id, team_id=team.id)
            await s.delete(team)
            return None, None
        new_captain = None
        if team.captain_id == m.user_id:
            team.captain_id = new_captain = rest[0].user_id
        return team, new_captain

    async def notify(self, tg_id: int, text: str, buttons: list | None = None) -> None:
        await self.hw._notify(tg_id, [{"text": text, "buttons": buttons} if buttons else text])

    # ---------- что видит участник ----------

    async def state(self, hackathon_id: int, user_id: int) -> dict:
        await self._ready(hackathon_id)
        async with self.sm() as s:
            h = await self._hack(s, hackathon_id)
            m = await self._member(s, h.id, user_id)
            out = {
                "team_max": team_max(h),
                "deadline": team_deadline(h).isoformat(),
                "formed": bool(h.teams_formed),
                "state": "none" if m is None else ("team" if m.team_id else "pool"),
                "intent": m.intent if m and not m.team_id else None,
                "team": None,
                "invites": [],
                "my_requests": [],
            }
            if m is not None and m.team_id:
                out["team"] = await self._team_dict(s, h, await s.get(Team, m.team_id), user_id)
            else:
                reqs = list((await s.scalars(
                    select(TeamRequest).where(TeamRequest.hackathon_id == h.id, TeamRequest.user_id == user_id,
                                              TeamRequest.status == "pending")
                    .order_by(TeamRequest.id).options(selectinload(TeamRequest.team))
                )).all())
                sizes = await self._team_sizes(s, h.id)
                for r in reqs:
                    captain = await s.get(User, r.team.captain_id) if r.team.captain_id else None
                    item = {"id": r.id, "team_id": r.team_id, "team_name": r.team.name, "size": sizes.get(r.team_id, 0),
                            "captain": captain.display_name if captain else None, "created_at": as_utc(r.created_at).isoformat()}
                    (out["invites"] if r.kind == "invite" else out["my_requests"]).append(item)
            return out

    async def _team_dict(self, s: AsyncSession, h: Hackathon, team: Team, viewer_id: int, admin: bool = False) -> dict:
        members = await self._team_members(s, team.id)
        is_captain = team.captain_id == viewer_id
        d = {
            "id": team.id,
            "name": team.name,
            "is_auto": bool(team.is_auto),
            "captain_id": team.captain_id,
            "is_captain": is_captain,
            "size": len(members),
            "members": [_user_brief(x.user, admin=admin) | {"is_captain": x.user_id == team.captain_id,
                                                            "is_me": x.user_id == viewer_id} for x in members],
        }
        if is_captain or admin:
            reqs = list((await s.scalars(
                select(TeamRequest).where(TeamRequest.team_id == team.id, TeamRequest.status == "pending")
                .order_by(TeamRequest.id).options(selectinload(TeamRequest.user))
            )).all())
            d["requests"] = [{"id": r.id, "created_at": as_utc(r.created_at).isoformat()} | _user_brief(r.user, admin=admin)
                             for r in reqs if r.kind == "join"]
            d["invites_sent"] = [{"id": r.id, "created_at": as_utc(r.created_at).isoformat()} | _user_brief(r.user, admin=admin)
                                 for r in reqs if r.kind == "invite"]
        return d

    async def my_team_id(self, hackathon_id: int, user_id: int) -> int | None:
        async with self.sm() as s:
            m = await self._member(s, hackathon_id, user_id)
            return m.team_id if m else None

    async def brief(self, h: Hackathon, user_id: int) -> dict:
        """Короткая сводка для списка хакатонов и бота."""
        async with self.sm() as s:
            m = await self._member(s, h.id, user_id)
            if m is None:
                return {"team_state": "none", "team": None}
            if not m.team_id:
                return {"team_state": "pool", "team": None}
            team = await s.get(Team, m.team_id)
            size = (await self._team_sizes(s, h.id)).get(team.id, 0)
            return {"team_state": "team", "team": {"id": team.id, "name": team.name, "size": size,
                                                   "is_captain": team.captain_id == user_id}}

    async def list_teams(self, hackathon_id: int, user_id: int, q: str = "") -> list[dict]:
        await self._ready(hackathon_id)
        async with self.sm() as s:
            h = await self._hack(s, hackathon_id)
            teams = list((await s.scalars(select(Team).where(Team.hackathon_id == h.id).order_by(Team.id))).all())
            sizes = await self._team_sizes(s, h.id)
            mine = {r.team_id: r.id for r in (await s.scalars(select(TeamRequest).where(
                TeamRequest.hackathon_id == h.id, TeamRequest.user_id == user_id, TeamRequest.kind == "join",
                TeamRequest.status == "pending"))).all()}
            captains = {u.tg_id: u for u in (await s.scalars(
                select(User).where(User.tg_id.in_([t.captain_id for t in teams if t.captain_id])))).all()}
        needle = _norm(q)
        out = []
        for t in teams:
            if needle and needle not in _norm(t.name):
                continue
            cap = captains.get(t.captain_id)
            out.append({
                "id": t.id, "name": t.name, "size": sizes.get(t.id, 0), "full": sizes.get(t.id, 0) >= team_max(h),
                "is_auto": bool(t.is_auto), "captain": cap.display_name if cap else None,
                "request_id": mine.get(t.id),
            })
        out.sort(key=lambda x: (x["full"], x["name"].casefold()))
        return out

    async def search_people(self, hackathon_id: int, captain_id: int, q: str) -> list[dict]:
        """Поиск людей для приглашения: по нику, имени в Telegram, @username и (от 4 символов) по ФИО."""
        q = (q or "").strip().lstrip("@")
        if len(q) < 2:
            return []
        async with self.sm() as s:
            h = await self._hack(s, hackathon_id)
            me = await self._member(s, h.id, captain_id)
            team = await s.get(Team, me.team_id) if me and me.team_id else None
            if team is None or team.captain_id != captain_id:
                raise ServiceError("Приглашать может только капитан команды")
            users = list((await s.scalars(select(User))).all())
            members = {m.user_id: m.team_id for m in (await s.scalars(
                select(TeamMember).where(TeamMember.hackathon_id == h.id))).all()}
            invited = {r.user_id for r in (await s.scalars(select(TeamRequest).where(
                TeamRequest.team_id == team.id, TeamRequest.kind == "invite", TeamRequest.status == "pending"))).all()}
            asked = {r.user_id: r.id for r in (await s.scalars(select(TeamRequest).where(
                TeamRequest.team_id == team.id, TeamRequest.kind == "join", TeamRequest.status == "pending"))).all()}
            team_names = {t.id: t.name for t in (await s.scalars(select(Team).where(Team.hackathon_id == h.id))).all()}
        needle = q.casefold()
        found = []
        for u in users:
            if u.tg_id == captain_id or self.hw.is_admin(u.tg_id):
                continue
            hay = [u.nickname, u.full_name, u.username]
            by_fio = len(needle) >= 4 and needle in (u.fio or "").casefold()
            if not by_fio and not any(needle in (x or "").casefold() for x in hay):
                continue
            exact = (u.username or "").casefold() == needle
            tid = members.get(u.tg_id)
            found.append((not exact, (u.display_name or "").casefold(), {
                **_user_brief(u),
                "fio": short_fio(u.fio),
                "team_name": team_names.get(tid) if tid else None,
                "in_my_team": tid == team.id,
                "invited": u.tg_id in invited,
                "request_id": asked.get(u.tg_id),
            }))
        found.sort(key=lambda x: x[:2])
        return [x[2] for x in found[:20]]

    # ---------- действия участника ----------

    async def register(self, hackathon_id: int, user_id: int, intent: str) -> dict:
        """«Нет команды» (pool) или «ищу свою команду» (join). После формирования pool сразу даёт команду."""
        self._no_admin(user_id)
        if intent not in ("pool", "join"):
            raise ServiceError("Неизвестный выбор")
        await self._ready(hackathon_id)
        placed = None
        async with self.sm() as s:
            h = await self._hack(s, hackathon_id)
            self._check_open(h)
            m = await self._member(s, h.id, user_id)
            if m is not None and m.team_id:
                raise ServiceError("Ты уже в команде")
            if m is None:
                m = TeamMember(hackathon_id=h.id, user_id=user_id)
                s.add(m)
            m.intent = intent
            await s.flush()
            if intent == "pool" and h.teams_formed:
                placed = await self._place_late(s, h, user_id)
            await s.commit()
        if placed is not None:
            await self._announce_placed(hackathon_id, [placed], late_user=user_id)
        return await self.state(hackathon_id, user_id)

    async def unregister(self, hackathon_id: int, user_id: int) -> dict:
        async with self.sm() as s:
            h = await self._hack(s, hackathon_id)
            m = await self._member(s, h.id, user_id)
            if m is not None and m.team_id:
                raise ServiceError("Ты в команде — сначала выйди из неё")
            if m is not None:
                await s.delete(m)
            await self._cancel_pending(s, h.id, user_id=user_id)
            await s.commit()
        return await self.state(hackathon_id, user_id)

    async def create_team(self, hackathon_id: int, user_id: int, name: str) -> dict:
        self._no_admin(user_id)
        await self._ready(hackathon_id)
        async with self.sm() as s:
            h = await self._hack(s, hackathon_id)
            self._check_open(h)
            m = await self._member(s, h.id, user_id)
            if m is not None and m.team_id:
                raise ServiceError("Ты уже в команде")
            team = Team(hackathon_id=h.id, name=await self._check_name(s, h.id, name), captain_id=user_id, is_auto=False)
            s.add(team)
            await s.flush()
            await self._put_in_team(s, h, team, user_id)
            await s.commit()
        return await self.state(hackathon_id, user_id)

    async def rename(self, hackathon_id: int, user_id: int, name: str) -> dict:
        async with self.sm() as s:
            h = await self._hack(s, hackathon_id)
            team = await self._captain_team(s, h, user_id)
            team.name = await self._check_name(s, h.id, name, skip_team=team.id)
            await s.commit()
        return await self.state(hackathon_id, user_id)

    async def _captain_team(self, s: AsyncSession, h: Hackathon, user_id: int) -> Team:
        m = await self._member(s, h.id, user_id)
        team = await s.get(Team, m.team_id) if m and m.team_id else None
        if team is None or team.captain_id != user_id:
            raise ServiceError("Это может только капитан команды")
        return team

    async def leave(self, hackathon_id: int, user_id: int) -> dict:
        await self._ready(hackathon_id)
        async with self.sm() as s:
            h = await self._hack(s, hackathon_id)
            if h.teams_formed:
                raise ServiceError("Составы команд уже закреплены — выйти нельзя. Если очень нужно, напиши организаторам.")
            m = await self._member(s, h.id, user_id)
            if m is None or not m.team_id:
                raise ServiceError("Ты не в команде")
            me = await s.get(User, user_id)
            team, new_captain = await self._drop_member(s, h, m)
            await s.commit()
            rest = [x.user_id for x in await self._team_members(s, team.id)] if team else []
        for uid in rest:
            text = f"👋 {escape(me.display_name)} вышел(а) из команды «{escape(team.name)}»."
            if new_captain == uid:
                text += "\nТеперь капитан — ты: можешь приглашать людей и принимать заявки."
            await self.notify(uid, text)
        return await self.state(hackathon_id, user_id)

    async def remove_member(self, hackathon_id: int, actor_id: int, member_id: int) -> None:
        """Капитан убирает участника (до закрепления составов), организатор — в любой момент."""
        admin = self.hw.is_admin(actor_id)
        await self._ready(hackathon_id)
        async with self.sm() as s:
            h = await (self._hack_admin if admin else self._hack)(s, hackathon_id)
            m = await self._member(s, h.id, member_id)
            if m is None or not m.team_id:
                raise ServiceError("Человек не в команде")
            team = await s.get(Team, m.team_id)
            if not admin:
                if team.captain_id != actor_id:
                    raise ServiceError("Это может только капитан команды")
                if member_id == actor_id:
                    raise ServiceError("Чтобы уйти самому, нажми «Выйти из команды»")
                if h.teams_formed:
                    raise ServiceError("Составы команд уже закреплены. Если нужно, напиши организаторам.")
            name, title = team.name, h.title
            if admin:  # организатор: человек остаётся в хакатоне «без команды», его можно поставить в другую
                await self._detach(s, h, m)
            else:
                await self._drop_member(s, h, m)
            await s.commit()
        await self.notify(member_id, f"Тебя убрали из команды «{escape(name)}» в хакатоне «{escape(title)}». "
                                     + ("Организаторы скоро определят тебя в команду." if admin else
                                        "Выбери другую команду в мини аппе, во вкладке «Хакатоны»."))

    async def invite(self, hackathon_id: int, captain_id: int, user_id: int) -> dict:
        await self._ready(hackathon_id)
        async with self.sm() as s:
            h = await self._hack(s, hackathon_id)
            self._check_open(h)
            team = await self._captain_team(s, h, captain_id)
            if self.hw.is_admin(user_id):
                raise ServiceError("Организаторов пригласить нельзя")
            target = await s.get(User, user_id)
            if target is None:
                raise ServiceError("Человек не найден — пусть сначала напишет боту")
            tm = await self._member(s, h.id, user_id)
            if tm is not None and tm.team_id:
                raise ServiceError(f"{target.display_name} уже в команде")
            if (await self._team_sizes(s, h.id)).get(team.id, 0) >= team_max(h):
                raise ServiceError("В команде нет свободных мест")
            # Человек сам просился к нам — приглашение и есть согласие: сразу принимаем
            asked = await s.scalar(select(TeamRequest).where(
                TeamRequest.team_id == team.id, TeamRequest.user_id == user_id, TeamRequest.kind == "join",
                TeamRequest.status == "pending"))
            if asked is not None:
                rid = asked.id
            else:
                dup = await s.scalar(select(TeamRequest.id).where(
                    TeamRequest.team_id == team.id, TeamRequest.user_id == user_id, TeamRequest.kind == "invite",
                    TeamRequest.status == "pending"))
                if dup:
                    raise ServiceError("Приглашение уже отправлено")
                r = TeamRequest(hackathon_id=h.id, team_id=team.id, user_id=user_id, kind="invite")
                s.add(r)
                await s.commit()
                rid = None
                cap = await s.get(User, captain_id)
                text = (f"✉️ {escape(cap.display_name)} приглашает тебя в команду <b>«{escape(team.name)}»</b> "
                        f"на хакатон «{escape(h.title)}».")
                invite_id, team_name = r.id, team.name
        if rid is not None:
            await self.decide(rid, captain_id, "accept")
            return {"accepted": True}
        await self.notify(user_id, text, [[["✅ Вступить", f"treq:accept:{invite_id}"], ["Отклонить", f"treq:decline:{invite_id}"]]])
        return {"id": invite_id, "team_name": team_name}

    async def request_join(self, hackathon_id: int, user_id: int, team_id: int) -> dict:
        self._no_admin(user_id)
        await self._ready(hackathon_id)
        async with self.sm() as s:
            h = await self._hack(s, hackathon_id)
            self._check_open(h)
            team = await s.get(Team, team_id)
            if team is None or team.hackathon_id != h.id:
                raise ServiceError("Команда не найдена")
            m = await self._member(s, h.id, user_id)
            if m is not None and m.team_id:
                raise ServiceError("Ты уже в команде")
            if (await self._team_sizes(s, h.id)).get(team.id, 0) >= team_max(h):
                raise ServiceError("В этой команде уже нет мест")
            pending = list((await s.scalars(select(TeamRequest).where(
                TeamRequest.hackathon_id == h.id, TeamRequest.user_id == user_id, TeamRequest.status == "pending"))).all())
            invite = next((r for r in pending if r.team_id == team.id and r.kind == "invite"), None)
            if invite is None:
                if any(r.team_id == team.id and r.kind == "join" for r in pending):
                    raise ServiceError("Заявка уже отправлена, ждём капитана")
                if sum(r.kind == "join" for r in pending) >= MAX_PENDING_JOINS:
                    raise ServiceError(f"Можно ждать ответа не больше чем от {MAX_PENDING_JOINS} команд сразу")
                if m is None:
                    s.add(TeamMember(hackathon_id=h.id, user_id=user_id, intent="join"))
                r = TeamRequest(hackathon_id=h.id, team_id=team.id, user_id=user_id, kind="join")
                s.add(r)
                await s.commit()
                me = await s.get(User, user_id)
                who = escape(me.display_name) + (f" (@{escape(me.username)})" if me.username else "")
                text = f"🙋 {who} просится в твою команду <b>«{escape(team.name)}»</b> на хакатоне «{escape(h.title)}»."
                captain, rid = team.captain_id, r.id
        if invite is not None:  # капитан уже звал — заявка и есть согласие
            await self.decide(invite.id, user_id, "accept")
            return {"accepted": True}
        if captain:
            await self.notify(captain, text, [[["✅ Принять", f"treq:accept:{rid}"], ["Отклонить", f"treq:decline:{rid}"]]])
        return {"id": rid}

    async def decide(self, request_id: int, actor_id: int, action: str) -> dict:
        """accept / decline — тот, кому адресовано (приглашённый или капитан); cancel — тот, кто отправил."""
        if action not in ("accept", "decline", "cancel"):
            raise ServiceError("Неизвестное действие")
        async with self.sm() as s:
            r = await s.get(TeamRequest, request_id)
            if r is None:
                raise ServiceError("Заявка не найдена")
            hackathon_id = r.hackathon_id
        await self._ready(hackathon_id)
        notes: list[tuple[int, str]] = []
        async with self.sm() as s:
            r = await s.get(TeamRequest, request_id)
            if r.status != "pending":
                raise ServiceError({"accepted": "Уже принято", "declined": "Уже отклонено"}.get(r.status, "Заявка больше не действует"))
            h = await s.get(Hackathon, r.hackathon_id)
            team = await s.get(Team, r.team_id)
            user = await s.get(User, r.user_id)
            captain = await s.get(User, team.captain_id) if team.captain_id else None
            recipient = r.user_id if r.kind == "invite" else team.captain_id
            sender = team.captain_id if r.kind == "invite" else r.user_id
            if action == "cancel" and actor_id != sender:
                raise ServiceError("Отменить может только тот, кто отправил")
            if action != "cancel" and actor_id != recipient:
                raise ServiceError("Ответить может только тот, кому это адресовано" if r.kind == "invite"
                                   else "Принимать заявки может только капитан")
            if action == "accept":
                if self.hs.status_of(h) == "finished":
                    raise ServiceError("Хакатон уже закончился")
                await self._put_in_team(s, h, team, r.user_id)
                r.status = "accepted"
                if r.kind == "invite":
                    notes.append((team.captain_id, f"🎉 {escape(user.display_name)} принял(а) приглашение в «{escape(team.name)}»."))
                else:
                    notes.append((r.user_id, f"🎉 Капитан принял тебя в команду <b>«{escape(team.name)}»</b> "
                                             f"(хакатон «{escape(h.title)}»)."))
                for x in await self._team_members(s, team.id):
                    if x.user_id not in (r.user_id, team.captain_id):
                        notes.append((x.user_id, f"👋 В команде «{escape(team.name)}» новый участник: {escape(user.display_name)}."))
            elif action == "decline":
                r.status = "declined"
                if r.kind == "invite":
                    notes.append((team.captain_id, f"{escape(user.display_name)} отклонил(а) приглашение в «{escape(team.name)}»."))
                else:
                    notes.append((r.user_id, f"Капитан команды «{escape(team.name)}» отклонил заявку. "
                                             "Попробуй другую команду или выбери «Нет команды» — тогда мы распределим тебя сами."))
            else:
                r.status = "cancelled"
            r.decided_at = utcnow()
            await s.commit()
            result = {"status": r.status, "kind": r.kind, "team_name": team.name, "hackathon_id": h.id,
                      "user_name": user.display_name, "captain_name": captain.display_name if captain else None}
        for uid, text in notes:
            if uid and uid != actor_id:
                await self.notify(uid, text)
        return result

    # ---------- автораспределение ----------

    async def _place_late(self, s: AsyncSession, h: Hackathon, user_id: int) -> Team:
        """После формирования: в случайную «сборную» со свободным местом, иначе — новая сборная."""
        sizes = await self._team_sizes(s, h.id)
        autos = list((await s.scalars(select(Team).where(Team.hackathon_id == h.id, Team.is_auto.is_(True)))).all())
        free = [t for t in autos if sizes.get(t.id, 0) < team_max(h)]
        if free:
            smallest = min(sizes.get(t.id, 0) for t in free)
            team = random.choice([t for t in free if sizes.get(t.id, 0) == smallest])
        else:
            team = await self._new_auto_team(s, h, user_id)
        await self._put_in_team(s, h, team, user_id)
        return team

    async def _new_auto_team(self, s: AsyncSession, h: Hackathon, captain_id: int) -> Team:
        names = {_norm(n) for n in (await s.scalars(select(Team.name).where(Team.hackathon_id == h.id))).all()}
        n = 1
        while _norm(f"Сборная №{n}") in names:
            n += 1
        team = Team(hackathon_id=h.id, name=f"Сборная №{n}", captain_id=captain_id, is_auto=True)
        s.add(team)
        await s.flush()
        return team

    async def form_teams(self, hackathon_id: int) -> int:
        """Раскладывает всех без команды по новым командам ровных размеров и закрепляет составы."""
        teams: list[Team] = []
        placed = 0
        async with self.sm() as s:
            h = await s.get(Hackathon, hackathon_id)
            if h is None or not is_team(h) or h.teams_formed:
                return 0
            h.teams_formed = True
            if h.team_deadline is None or as_utc(h.team_deadline) > utcnow():
                h.team_deadline = utcnow()
            pool = list((await s.scalars(select(TeamMember).where(
                TeamMember.hackathon_id == h.id, TeamMember.team_id.is_(None)))).all())
            pool = [m for m in pool if not self.hw.is_admin(m.user_id)]
            random.shuffle(pool)
            placed = len(pool)
            if pool:
                k = -(-len(pool) // team_max(h))  # столько команд, чтобы влезли все
                groups = [pool[i::k] for i in range(k)]  # размеры отличаются не больше чем на 1
                for g in groups:
                    team = await self._new_auto_team(s, h, g[0].user_id)
                    for m in g:
                        await self._put_in_team(s, h, team, m.user_id)
                    teams.append(team)
            await s.commit()
        await self._announce_placed(hackathon_id, teams)
        return placed

    async def _members_of(self, team_id: int) -> list[TeamMember]:
        async with self.sm() as s:
            return await self._team_members(s, team_id)

    async def _announce_placed(self, hackathon_id: int, teams: list[Team], late_user: int | None = None) -> None:
        """Сообщает о распределении. late_user — опоздавший, которого добавили в уже существующую сборную."""
        async with self.sm() as s:
            h = await s.get(Hackathon, hackathon_id)
        for team in teams:
            members = await self._members_of(team.id)
            names = ", ".join(escape(m.user.display_name) + (" (капитан)" if m.user_id == team.captain_id else "")
                              for m in members)
            newbie = next((m for m in members if m.user_id == late_user), None)
            for m in members:
                if newbie is not None and m.user_id != late_user:
                    await self.notify(m.user_id, f"👋 В команду «{escape(team.name)}» распределили нового участника: "
                                                 f"{escape(newbie.user.display_name)}.")
                    continue
                await self.notify(m.user_id, f"🎲 Хакатон «{escape(h.title)}»: тебя распределили в команду "
                                             f"<b>«{escape(team.name)}»</b>.\nСостав: {names}.\n"
                                             "Напишите друг другу — контакты в мини аппе, во вкладке «Хакатоны».")

    # ---------- организатор ----------
    # Организатор может править составы в любой момент, в том числе после закрепления: это исправление ошибок.

    async def _hack_admin(self, s: AsyncSession, hackathon_id: int) -> Hackathon:
        h = await s.get(Hackathon, hackathon_id)
        if h is None:
            raise ServiceError("Хакатон не найден")
        if not is_team(h):
            raise ServiceError("Это не командный хакатон")
        return h

    async def admin_move(self, hackathon_id: int, user_id: int, team_id: int | None) -> dict:
        """Ставит человека в команду (из другой команды, из «без команды» или вообще не записанного).
        team_id = None — убрать из команды, но оставить в хакатоне без команды."""
        if self.hw.is_admin(user_id):
            raise ServiceError("Организаторы не участвуют в командах")
        notes: list[tuple[int, str]] = []
        async with self.sm() as s:
            h = await self._hack_admin(s, hackathon_id)
            user = await s.get(User, user_id)
            if user is None:
                raise ServiceError("Человек не найден")
            m = await self._member(s, h.id, user_id)
            target = None
            if team_id is not None:
                target = await s.get(Team, team_id)
                if target is None or target.hackathon_id != h.id:
                    raise ServiceError("Команда не найдена")
                if m is not None and m.team_id == target.id:
                    raise ServiceError(f"{user.display_name} уже в этой команде")
                if (await self._team_sizes(s, h.id)).get(target.id, 0) >= team_max(h):
                    raise ServiceError(f"В команде «{target.name}» нет мест ({team_max(h)} из {team_max(h)}). "
                                       "Сначала убери кого-нибудь или увеличь максимум в настройках хакатона.")
            old_name = None
            if m is not None and m.team_id:
                old = await s.get(Team, m.team_id)
                old_name = old.name
                rest_team, new_captain = await self._detach(s, h, m)
                if rest_team is not None and new_captain:
                    notes.append((new_captain, f"👑 Теперь ты капитан команды «{escape(rest_team.name)}»."))
            elif m is None:
                m = TeamMember(hackathon_id=h.id, user_id=user_id, intent="pool")
                s.add(m)
                await s.flush()
            if target is not None:
                if not (await self._team_members(s, target.id)):
                    target.captain_id = user_id  # в пустой команде (мало ли) человек сразу капитан
                await self._put_in_team(s, h, target, user_id)
                notes.append((user_id, f"🔀 Организаторы перевели тебя в команду <b>«{escape(target.name)}»</b> "
                                       f"(хакатон «{escape(h.title)}»)."))
                for x in await self._team_members(s, target.id):
                    if x.user_id != user_id:
                        notes.append((x.user_id, f"👋 Организаторы добавили в команду «{escape(target.name)}»: "
                                                 f"{escape(user.display_name)}."))
            elif old_name:
                notes.append((user_id, f"Организаторы убрали тебя из команды «{escape(old_name)}». Скоро определят в другую."))
            await s.commit()
        for uid, text in notes:
            await self.notify(uid, text)
        return await self.overview(hackathon_id)

    async def admin_set_captain(self, hackathon_id: int, team_id: int, user_id: int) -> dict:
        async with self.sm() as s:
            h = await self._hack_admin(s, hackathon_id)
            team = await s.get(Team, team_id)
            if team is None or team.hackathon_id != h.id:
                raise ServiceError("Команда не найдена")
            m = await self._member(s, h.id, user_id)
            if m is None or m.team_id != team.id:
                raise ServiceError("Этот человек не в команде")
            team.captain_id = user_id
            name = team.name
            await s.commit()
        await self.notify(user_id, f"👑 Организаторы сделали тебя капитаном команды «{escape(name)}».")
        return await self.overview(hackathon_id)

    async def admin_search(self, hackathon_id: int, q: str) -> list[dict]:
        """Поиск любого участника бота (по нику, имени, @username, ФИО) с текущей командой — для правки составов."""
        q = (q or "").strip().lstrip("@")
        if len(q) < 2:
            return []
        async with self.sm() as s:
            h = await self._hack_admin(s, hackathon_id)
            users = list((await s.scalars(select(User))).all())
            members = {m.user_id: m.team_id for m in (await s.scalars(
                select(TeamMember).where(TeamMember.hackathon_id == h.id))).all()}
            team_names = {t.id: t.name for t in (await s.scalars(select(Team).where(Team.hackathon_id == h.id))).all()}
        needle = q.casefold()
        out = []
        for u in users:
            if self.hw.is_admin(u.tg_id):
                continue
            if not any(needle in (x or "").casefold() for x in (u.nickname, u.full_name, u.username, u.fio)):
                continue
            tid = members.get(u.tg_id)
            out.append(_user_brief(u, admin=True) | {
                "registered": u.tg_id in members, "team_id": tid, "team_name": team_names.get(tid) if tid else None})
        out.sort(key=lambda x: (x["name"] or "").casefold())
        return out[:20]

    async def overview(self, hackathon_id: int) -> dict:
        await self._ready(hackathon_id)
        async with self.sm() as s:
            h = await s.get(Hackathon, hackathon_id)
            if h is None:
                raise ServiceError("Хакатон не найден")
            teams = list((await s.scalars(select(Team).where(Team.hackathon_id == h.id).order_by(Team.id))).all())
            out = [await self._team_dict(s, h, t, 0, admin=True) for t in teams]
            pool = list((await s.scalars(select(TeamMember).where(
                TeamMember.hackathon_id == h.id, TeamMember.team_id.is_(None)).options(selectinload(TeamMember.user)))).all())
        return {
            "team_max": team_max(h),
            "deadline": team_deadline(h).isoformat(),
            "formed": bool(h.teams_formed),
            "teams": out,
            "pool": [_user_brief(m.user, admin=True) | {"intent": m.intent} for m in pool],
        }

    def announce_line(self, h: Hackathon) -> str:
        return (f"\n👥 Командный: до {team_max(h)} человек в команде. Собери команду или попросись в чужую в мини аппе "
                f"до {fmt_dt(team_deadline(h), self.tz)} (МСК). Кто будет без команды — распределим случайно.")
