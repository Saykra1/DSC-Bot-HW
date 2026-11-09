"""Командные хакатоны: команды, приглашения, заявки, автораспределение, общий лидерборд и лимит."""

import random
from datetime import datetime, timedelta, timezone

from tests.test_phase3 import SOLUTION, create_hackathon, local
from tests.test_web import ADMIN, ctx, hdr  # noqa: F401

NOW = datetime.now(timezone.utc)


def team_hack(client, starts=None, ends=None, **extra):
    starts = starts or NOW + timedelta(days=1)
    ends = ends or starts + timedelta(days=1)
    r = create_hackathon(client, starts, ends, team_mode="true", **({"team_max": "3"} | extra))
    assert r.status_code == 200, r.text
    return r.json()


def call(client, method, path, user, **kw):
    r = getattr(client, method)(f"/api{path}", headers=hdr(user), **kw)
    return r


def ok(r):
    assert r.status_code == 200, r.text
    return r.json()


def texts(sent, user):
    out = []
    for tg, msgs in sent:
        if tg == user:
            out += [m["text"] if isinstance(m, dict) else m for m in msgs]
    return out


def buttons(sent, user):
    return [b for tg, msgs in sent if tg == user for m in msgs if isinstance(m, dict) for row in m["buttons"] for b in row]


def test_mode_and_validation(ctx):
    client, _, _ = ctx
    h = team_hack(client)
    assert (h["team_mode"], h["team_max"], h["teams_formed"]) == (True, 3, False)
    assert h["team_deadline"] == h["starts_at"]  # по умолчанию — старт
    bad = create_hackathon(client, NOW + timedelta(days=1), NOW + timedelta(days=2), team_mode="true", team_max="1")
    assert bad.status_code == 400
    late = create_hackathon(client, NOW + timedelta(days=1), NOW + timedelta(days=2), team_mode="true",
                            team_deadline=local(NOW + timedelta(days=1, hours=1)))
    assert late.status_code == 400 and "старта" in late.json()["detail"]
    solo = ok(create_hackathon(client, NOW + timedelta(days=1), NOW + timedelta(days=2)))
    assert solo["team_mode"] is False
    assert call(client, "get", f"/hackathons/{solo['id']}/team", 2).status_code == 400
    # организатор не участвует
    assert call(client, "post", f"/hackathons/{h['id']}/team", ADMIN, json={"name": "Орги"}).status_code == 400


def test_create_invite_request_and_leave(ctx):
    client, _, sent = ctx
    hid = team_hack(client)["id"]
    for u in (2, 3, 4, 5, 6):
        ok(call(client, "get", "/me", u))
    ok(call(client, "patch", "/profile", 4, json={"fio": "Петров Пётр Петрович"}))

    st = ok(call(client, "post", f"/hackathons/{hid}/team", 2, json={"name": "  Нейро   коты "}))
    assert st["state"] == "team" and st["team"]["name"] == "Нейро коты" and st["team"]["is_captain"]
    assert call(client, "post", f"/hackathons/{hid}/team", 3, json={"name": "нейро КОТЫ"}).status_code == 400  # имя занято

    # поиск по ФИО (от 4 символов) — в выдаче только фамилия с инициалами
    found = ok(call(client, "get", f"/hackathons/{hid}/people?q=петров", 2))
    assert [x["tg_id"] for x in found] == [4] and found[0]["fio"] == "Петров П. П."
    assert ok(call(client, "get", f"/hackathons/{hid}/people?q=пет", 2)) == []
    assert [x["tg_id"] for x in ok(call(client, "get", f"/hackathons/{hid}/people?q=@user3", 2))] == [3]
    assert call(client, "get", f"/hackathons/{hid}/people?q=user", 3).status_code == 400  # не капитан

    # приглашение -> кнопки в боте -> принять
    inv = ok(call(client, "post", f"/hackathons/{hid}/team/invites", 2, json={"user_id": 4}))
    assert call(client, "post", f"/hackathons/{hid}/team/invites", 2, json={"user_id": 4}).status_code == 400
    assert any("treq:accept:" in b[1] for b in buttons(sent, 4))
    assert ok(call(client, "get", f"/hackathons/{hid}/team", 4))["invites"][0]["team_name"] == "Нейро коты"
    assert call(client, "post", f"/team-requests/{inv['id']}", 3, json={"action": "accept"}).status_code == 400
    ok(call(client, "post", f"/team-requests/{inv['id']}", 4, json={"action": "accept"}))
    assert any("принял" in t for t in texts(sent, 2))

    # заявка по названию -> капитан принимает
    teams = ok(call(client, "get", f"/hackathons/{hid}/teams?q=коты", 3))
    assert teams[0]["size"] == 2 and teams[0]["captain"]
    req = ok(call(client, "post", f"/hackathons/{hid}/teams/{teams[0]['id']}/requests", 3))
    assert ok(call(client, "get", f"/hackathons/{hid}/team", 3))["my_requests"][0]["id"] == req["id"]
    assert ok(call(client, "get", f"/hackathons/{hid}/team", 2))["team"]["requests"][0]["tg_id"] == 3
    assert call(client, "post", f"/team-requests/{req['id']}", 4, json={"action": "accept"}).status_code == 400
    ok(call(client, "post", f"/team-requests/{req['id']}", 2, json={"action": "accept"}))
    team = ok(call(client, "get", f"/hackathons/{hid}/team", 3))["team"]
    assert team["size"] == 3 and not team["is_captain"]

    # мест нет (максимум 3)
    assert call(client, "post", f"/hackathons/{hid}/teams/{team['id']}/requests", 5).status_code == 400
    assert call(client, "post", f"/hackathons/{hid}/team/invites", 2, json={"user_id": 5}).status_code == 400

    # капитан уходит — капитаном становится следующий
    ok(call(client, "post", f"/hackathons/{hid}/team/leave", 2))
    team = ok(call(client, "get", f"/hackathons/{hid}/team", 4))["team"]
    assert team["size"] == 2 and team["is_captain"]
    assert any("капитан — ты" in t for t in texts(sent, 4))
    # капитан убирает участника
    ok(call(client, "post", f"/hackathons/{hid}/team/members/3/remove", 4))
    assert ok(call(client, "get", f"/hackathons/{hid}/team", 3))["state"] == "none"


def test_invite_and_request_meet(ctx):
    """Если капитан позвал, а человек сам попросился (или наоборот) — сразу в команде."""
    client, _, _ = ctx
    hid = team_hack(client)["id"]
    ok(call(client, "get", "/me", 3))
    tid = ok(call(client, "post", f"/hackathons/{hid}/team", 2, json={"name": "Альфа"}))["team"]["id"]
    ok(call(client, "post", f"/hackathons/{hid}/teams/{tid}/requests", 3))
    assert ok(call(client, "post", f"/hackathons/{hid}/team/invites", 2, json={"user_id": 3}))["accepted"]
    assert ok(call(client, "get", f"/hackathons/{hid}/team", 3))["team"]["id"] == tid


def test_distribution_balanced_and_locked(ctx):
    client, service, sent = ctx
    random.seed(1)
    hid = team_hack(client, team_max="5")["id"]
    ok(call(client, "post", f"/hackathons/{hid}/team", 2, json={"name": "Своя"}))
    pool = list(range(10, 17))  # 7 человек без команды
    for u in pool:
        ok(call(client, "post", f"/hackathons/{hid}/pool", u, json={"intent": "pool"}))
    ok(call(client, "post", f"/hackathons/{hid}/pool", 17, json={"intent": "join"}))  # ждал ответа капитана — тоже в пул
    assert ok(call(client, "post", f"/admin/hackathons/{hid}/teams/form", ADMIN))["placed"] == 8
    ov = ok(call(client, "get", f"/admin/hackathons/{hid}/teams", ADMIN))
    sizes = sorted(t["size"] for t in ov["teams"] if t["is_auto"])
    assert sizes == [4, 4] and ov["pool"] == [] and ov["formed"]
    assert all(any("распределили" in t for t in texts(sent, u)) for u in pool)
    # составы закреплены
    assert call(client, "post", f"/hackathons/{hid}/team/leave", 10).status_code == 400
    # опоздавший с «нет команды» сразу попадает в сборную со свободным местом
    st = ok(call(client, "post", f"/hackathons/{hid}/pool", 30, json={"intent": "pool"}))
    assert st["state"] == "team" and st["team"]["is_auto"] and st["team"]["size"] == 5
    # а создать свою команду всё ещё можно
    assert ok(call(client, "post", f"/hackathons/{hid}/team", 31, json={"name": "Поздние"}))["state"] == "team"


def test_deadline_triggers_distribution(ctx):
    client, service, _ = ctx
    starts = NOW + timedelta(hours=2)
    hid = team_hack(client, starts=starts, team_deadline=local(NOW + timedelta(hours=1)))["id"]
    ok(call(client, "post", f"/hackathons/{hid}/pool", 2, json={"intent": "pool"}))
    ok(call(client, "post", f"/hackathons/{hid}/pool", 3, json={"intent": "pool"}))
    # дедлайн прошёл: следующий же запрос распределяет (не ждём планировщик)
    ok(client.post(f"/api/admin/hackathons/{hid}/edit", headers=hdr(ADMIN),
                   data={"team_deadline": local(NOW - timedelta(minutes=1))}))
    st = ok(call(client, "get", f"/hackathons/{hid}/team", 2))
    assert st["formed"] and st["state"] == "team" and st["team"]["size"] == 2


def test_team_submissions_shared_limit_and_leaderboard(ctx):
    client, _, _ = ctx
    hid = team_hack(client, starts=NOW - timedelta(minutes=5), ends=NOW + timedelta(hours=1))["id"]

    def send(user, raw=SOLUTION):
        return call(client, "post", f"/hackathons/{hid}/submissions", user, files={"file": ("p.csv", raw)})

    assert send(2).status_code == 400  # без команды сдавать нельзя
    tid = ok(call(client, "post", f"/hackathons/{hid}/team", 2, json={"name": "Альфа"}))["team"]["id"]
    ok(call(client, "post", f"/hackathons/{hid}/teams/{tid}/requests", 3))
    ok(call(client, "post", f"/team-requests/{ok(call(client, 'get', f'/hackathons/{hid}/team', 2))['team']['requests'][0]['id']}",
            2, json={"action": "accept"}))
    ok(call(client, "post", f"/hackathons/{hid}/team", 4, json={"name": "Бета"}))

    half = b"id,target\n10,1\n11,1\n12,1\n13,1\n"
    assert ok(send(2, half))["score"] == 0.5
    assert ok(send(3))["score"] == 1.0
    r = send(2)
    assert r.status_code == 400 and "на команду" in r.json()["detail"]  # лимит 2 в день на команду
    ok(send(4, half))

    lb = ok(call(client, "get", f"/hackathons/{hid}/leaderboard", 2))
    assert [(x["name"], x["score"], x["attempts"]) for x in lb] == [("Альфа", 1.0, 2), ("Бета", 0.5, 1)]
    assert lb[0]["is_me"] and sorted(lb[0]["members"]) == ["Test", "Test"]
    mine = ok(call(client, "get", f"/hackathons/{hid}/submissions", 3))
    assert len(mine) == 2 and {x["is_mine"] for x in mine} == {True, False}
    info = ok(call(client, "get", f"/hackathons/{hid}", 3))
    assert info["my_best"] == 1.0 and info["used_today"] == 2 and info["team"]["name"] == "Альфа"
    subs = ok(call(client, "get", f"/admin/hackathons/{hid}/submissions", ADMIN))
    assert {x["team_name"] for x in subs} == {"Альфа", "Бета"}


def test_no_submissions_until_formed(ctx):
    client, service, _ = ctx
    hid = team_hack(client, starts=NOW - timedelta(minutes=5), ends=NOW + timedelta(hours=1))["id"]
    ok(call(client, "post", f"/hackathons/{hid}/team", 2, json={"name": "Альфа"}))
    # хакатон уже идёт, а команды ещё не сформированы (дедлайн команд сдвинули прямо в базе)
    from app.db import Hackathon

    async def unform():
        async with service.sm() as s:
            h = await s.get(Hackathon, hid)
            h.teams_formed, h.team_deadline = False, NOW + timedelta(minutes=30)
            await s.commit()

    client.portal.call(unform)
    r = call(client, "post", f"/hackathons/{hid}/submissions", 2, files={"file": ("p.csv", SOLUTION)})
    assert r.status_code == 400 and "формируются" in r.json()["detail"]
    ok(call(client, "post", f"/admin/hackathons/{hid}/teams/form", ADMIN))
    ok(call(client, "post", f"/hackathons/{hid}/submissions", 2, files={"file": ("p.csv", SOLUTION)}))


def test_admin_fixes_teams_after_lock(ctx):
    client, _, sent = ctx
    hid = team_hack(client, team_max="2")["id"]
    ok(call(client, "get", "/me", 4))
    a = ok(call(client, "post", f"/hackathons/{hid}/team", 2, json={"name": "Альфа"}))["team"]["id"]
    b = ok(call(client, "post", f"/hackathons/{hid}/team", 3, json={"name": "Бета"}))["team"]["id"]
    inv = ok(call(client, "post", f"/hackathons/{hid}/team/invites", 2, json={"user_id": 4}))
    ok(call(client, "post", f"/team-requests/{inv['id']}", 4, json={"action": "accept"}))
    ok(call(client, "post", f"/admin/hackathons/{hid}/teams/form", ADMIN))
    # участникам — нельзя
    assert call(client, "post", f"/hackathons/{hid}/team/leave", 4).status_code == 400
    assert call(client, "post", f"/hackathons/{hid}/team/members/4/remove", 2).status_code == 400
    # организатор переставляет 4 из Альфы в Бету
    ov = ok(call(client, "post", f"/admin/hackathons/{hid}/teams/move", ADMIN, json={"user_id": 4, "team_id": b}))
    sizes = {t["name"]: t["size"] for t in ov["teams"]}
    assert sizes == {"Альфа": 1, "Бета": 2}
    assert any("перевели" in t for t in texts(sent, 4))
    # в полную команду — нельзя
    assert call(client, "post", f"/admin/hackathons/{hid}/teams/move", ADMIN, json={"user_id": 2, "team_id": b}).status_code == 400
    # капитан Беты -> 4
    ov = ok(call(client, "post", f"/admin/hackathons/{hid}/teams/{b}/captain", ADMIN, json={"user_id": 4}))
    assert next(t for t in ov["teams"] if t["id"] == b)["captain_id"] == 4
    # убрать 3 из команды: остаётся в хакатоне без команды, потом добавляем его в Альфу
    ok(call(client, "post", f"/hackathons/{hid}/team/members/3/remove", ADMIN))
    ov = ok(call(client, "get", f"/admin/hackathons/{hid}/teams", ADMIN))
    assert [u["tg_id"] for u in ov["pool"]] == [3]
    ok(call(client, "post", f"/admin/hackathons/{hid}/teams/move", ADMIN, json={"user_id": 3, "team_id": a}))
    # добавить человека, который вообще не записывался
    ok(call(client, "get", "/me", 9))
    found = ok(call(client, "get", f"/admin/hackathons/{hid}/people?q=user9", ADMIN))
    assert found[0]["tg_id"] == 9 and found[0]["registered"] is False
    assert call(client, "post", f"/admin/hackathons/{hid}/teams/move", ADMIN, json={"user_id": 9, "team_id": a}).status_code == 400  # мест нет
    ov = ok(call(client, "post", f"/admin/hackathons/{hid}/teams/move", ADMIN, json={"user_id": 9}))
    assert 9 in [u["tg_id"] for u in ov["pool"]]
    assert call(client, "post", f"/admin/hackathons/{hid}/teams/move", 2, json={"user_id": 9}).status_code == 403
