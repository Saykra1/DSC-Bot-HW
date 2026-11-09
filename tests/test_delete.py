"""Удаление заданий и хакатонов организатором: подтверждение с последствиями, каскад по сдачам и командам, файлы."""

from datetime import datetime, timedelta, timezone
from pathlib import Path

from sqlalchemy import func, select

from app.db import GradeCacheEntry, Submission, Team, TeamMember, TeamRequest
from tests.test_checker import REFERENCE
from tests.test_phase3 import SOLUTION
from tests.test_teams import call, ok, team_hack
from tests.test_web import ADMIN, STUDENT, create_assignment, ctx, hdr, wait_done  # noqa: F401

NOW = datetime.now(timezone.utc)


async def count(service, model, **where):
    async with service.sm() as s:
        q = select(func.count()).select_from(model)
        for k, v in where.items():
            q = q.where(getattr(model, k) == v)
        return await s.scalar(q)


def run(client, coro):
    async def go():
        return await coro

    return client.portal.call(go)


def test_delete_assignment_cascades(ctx):
    client, service, _ = ctx
    aid = create_assignment(client)
    keep = create_assignment(client)
    r = client.post(f"/api/assignments/{aid}/submissions", headers=hdr(STUDENT), files={"file": ("hw.ipynb", REFERENCE)})
    sub = wait_done(client, r.json()["id"], STUDENT)
    ok(client.patch(f"/api/admin/submissions/{sub['id']}/review", headers=hdr(ADMIN), json={"comment": "Хорошо"}))
    a = run(client, service.get_assignment(aid))
    folder = Path(a.template_path).parent
    sub_file = Path(run(client, service.get_submission(sub["id"])).file_path)
    assert folder.exists() and sub_file.exists()

    # участнику нельзя ни смотреть последствия, ни удалять
    assert client.get(f"/api/admin/assignments/{aid}/delete", headers=hdr(STUDENT)).status_code == 403
    assert client.delete(f"/api/admin/assignments/{aid}", headers=hdr(STUDENT)).status_code == 403

    info = ok(client.get(f"/api/admin/assignments/{aid}/delete", headers=hdr(ADMIN)))
    assert (info["submissions"], info["participants"], info["comments"], info["checking"]) == (1, 1, 1, 0)

    ok(client.delete(f"/api/admin/assignments/{aid}", headers=hdr(ADMIN)))
    assert [x["id"] for x in client.get("/api/assignments", headers=hdr(STUDENT)).json()] == [keep]
    assert client.get(f"/api/submissions/{sub['id']}", headers=hdr(STUDENT)).status_code == 404
    assert client.get("/api/submissions", headers=hdr(STUDENT)).json() == []
    assert run(client, count(service, Submission, assignment_id=aid)) == 0
    assert run(client, count(service, GradeCacheEntry, assignment_id=aid)) == 0
    assert not folder.exists() and not sub_file.exists()
    assert client.delete(f"/api/admin/assignments/{aid}", headers=hdr(ADMIN)).status_code == 404
    # соседнее задание и его файлы на месте
    assert Path(run(client, service.get_assignment(keep)).template_path).exists()


def test_deleted_while_queued_is_skipped(ctx):
    client, service, sent = ctx
    aid = create_assignment(client)
    r = client.post(f"/api/assignments/{aid}/submissions", headers=hdr(STUDENT), files={"file": ("hw.ipynb", REFERENCE)})
    sid = r.json()["id"]
    ok(client.delete(f"/api/admin/assignments/{aid}", headers=hdr(ADMIN)))
    run(client, service.process(sid))  # воркер не падает на исчезнувшей сдаче
    run(client, service.wait_idle())
    assert run(client, count(service, Submission)) == 0


def test_delete_team_hackathon_cascades(ctx):
    client, service, _ = ctx
    hid = team_hack(client, starts=NOW - timedelta(minutes=5), ends=NOW + timedelta(hours=1))["id"]
    other = team_hack(client)["id"]
    ok(call(client, "get", "/me", 3))
    ok(call(client, "post", f"/hackathons/{hid}/team", 2, json={"name": "Альфа"}))
    ok(call(client, "post", f"/hackathons/{hid}/team/invites", 2, json={"user_id": 3}))  # висит приглашение
    ok(call(client, "post", f"/hackathons/{other}/team", 2, json={"name": "Альфа"}))
    ok(call(client, "post", f"/hackathons/{hid}/submissions", 2, files={"file": ("p.csv", SOLUTION)}))
    folder = Path(run(client, service.hackathons.get(hid)).folder)
    assert (folder / "submissions").exists()

    info = ok(call(client, "get", f"/admin/hackathons/{hid}/delete", ADMIN))
    assert (info["submissions"], info["participants"], info["teams"], info["members"]) == (1, 1, 1, 1)
    assert call(client, "delete", f"/admin/hackathons/{hid}", 2).status_code == 403

    ok(call(client, "delete", f"/admin/hackathons/{hid}", ADMIN))
    assert [h["id"] for h in ok(call(client, "get", "/hackathons", 2))] == [other]
    assert call(client, "get", f"/hackathons/{hid}", 2).status_code == 404
    for model in (Team, TeamMember, TeamRequest):
        assert run(client, count(service, model, hackathon_id=hid)) == 0
    assert run(client, count(service, Team, hackathon_id=other)) == 1
    assert not folder.exists()
    assert call(client, "delete", f"/admin/hackathons/{hid}", ADMIN).status_code == 404



def test_hide_hackathon_with_only_is_active(ctx):
    """Обратимая альтернатива из окна удаления: скрыть хакатон одним полем, ничего не трогая."""
    client, _, _ = ctx
    h = team_hack(client)
    r = client.post(f"/api/admin/hackathons/{h['id']}/edit", headers=hdr(ADMIN), data={"is_active": "false"})
    assert r.status_code == 200, r.text
    after = r.json()
    assert after["is_active"] is False and after["title"] == h["title"] and after["team_max"] == h["team_max"]
