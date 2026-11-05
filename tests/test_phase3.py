"""Номера попыток, кэш оценок, эталон после дедлайна, отложенные задания, правка, хакатоны."""

import time
from datetime import datetime, timedelta, timezone

from tests.test_checker import REFERENCE, TEMPLATE, FakeLLM, make_student
from tests.test_web import ADMIN, STUDENT, create_assignment, ctx, hdr, wait_done  # noqa: F401

MSK = timezone(timedelta(hours=3))


def local(dt: datetime) -> str:
    """Как присылает <input type=datetime-local>: без пояса, в МСК."""
    return dt.astimezone(MSK).strftime("%Y-%m-%dT%H:%M")


def submit(client, aid, raw, user=STUDENT):
    r = client.post(f"/api/assignments/{aid}/submissions", headers=hdr(user), files={"file": ("hw.ipynb", raw)})
    assert r.status_code == 200, r.text
    return wait_done(client, r.json()["id"], user)


def test_same_code_gets_same_grade_from_cache(ctx):
    client, service, _ = ctx
    service.settings.default_max_attempts = 5
    aid = create_assignment(client)
    calls = []

    class Flaky(FakeLLM):
        async def complete_json(self, system, user):
            calls.append(user)
            r = await super().complete_json(system, user)
            if len(calls) > 1:  # если бы модель спросили снова, она бы ответила иначе
                for t in r["tasks"]:
                    t["issues"] = [{"severity": "major", "text": "другое мнение"}]
            return r

    service.llm = Flaky()
    code = "X_train['weight'] = X_train['weight'].fillna(X_train['weight'].median())\n"
    first = submit(client, aid, make_student({14: code}))
    # тот же код с другими пробелами и комментарием -> та же оценка без нового запроса к модели
    second = submit(client, aid, make_student({14: "# моё решение\n" + code.replace(" = ", "=") + "\n\n"}), user=3)
    assert first["score"] == second["score"] == 8
    assert first["result"]["tasks"][0]["feedback"] == second["result"]["tasks"][0]["feedback"]
    assert len(calls) == 1


def test_attempt_numbers(ctx):
    client, _, _ = ctx
    aid = create_assignment(client)
    a = submit(client, aid, make_student({14: "x = 1\n"}))
    b = submit(client, aid, REFERENCE)
    assert (a["attempt"], b["attempt"]) == (1, 2)
    mine = client.get(f"/api/submissions?assignment_id={aid}", headers=hdr(STUDENT)).json()
    assert [s["attempt"] for s in mine] == [2, 1]
    admin = client.get(f"/api/admin/assignments/{aid}/submissions", headers=hdr(ADMIN)).json()
    assert {(s["id"], s["attempt"]) for s in admin} == {(a["id"], 1), (b["id"], 2)}


def test_reference_opens_after_deadline(ctx):
    client, service, _ = ctx
    aid = create_assignment(client)
    sub = submit(client, aid, make_student({14: "x = 1\n"}))
    assert client.get(f"/api/assignments/{aid}/reference", headers=hdr(STUDENT)).status_code == 403
    assert client.get(f"/api/assignments/{aid}/reference/file", headers=hdr(STUDENT)).status_code == 403
    code = client.get(f"/api/submissions/{sub['id']}/code", headers=hdr(STUDENT)).json()
    assert code["tasks"][0]["code"].strip() == "x = 1" and code["tasks"][0]["reference"] is None
    assert client.get(f"/api/submissions/{sub['id']}/code", headers=hdr(3)).status_code == 403
    # организатору эталон доступен всегда
    assert client.get(f"/api/assignments/{aid}/reference", headers=hdr(ADMIN)).status_code == 200

    past = datetime.now(timezone.utc) - timedelta(minutes=1)
    r = client.post(f"/api/admin/assignments/{aid}/edit", headers=hdr(ADMIN), data={"deadline": local(past)})
    assert r.status_code == 200, r.text
    lst = client.get("/api/assignments", headers=hdr(STUDENT)).json()
    assert lst[0]["reference_available"] is True
    ref = client.get(f"/api/assignments/{aid}/reference", headers=hdr(STUDENT)).json()
    assert "fillna(median)" in ref[0]["reference"]
    assert client.get(f"/api/assignments/{aid}/reference/file", headers=hdr(STUDENT)).status_code == 200
    code = client.get(f"/api/submissions/{sub['id']}/code", headers=hdr(STUDENT)).json()
    assert "fillna(median)" in code["tasks"][0]["reference"]


def test_scheduled_assignment_and_announcement(ctx):
    client, service, sent = ctx
    client.get("/api/me", headers=hdr(STUDENT))
    future = datetime.now(timezone.utc) + timedelta(hours=2)
    r = client.post(
        "/api/admin/assignments", headers=hdr(ADMIN),
        data={"title": "Отложенное", "publish_at": local(future)},
        files={"template": ("t.ipynb", TEMPLATE), "reference": ("r.ipynb", REFERENCE)},
    )
    aid = r.json()["id"]
    assert client.get("/api/assignments", headers=hdr(STUDENT)).json() == []
    assert client.get(f"/api/assignments/{aid}/template", headers=hdr(STUDENT)).status_code == 404
    r = client.post(f"/api/assignments/{aid}/submissions", headers=hdr(STUDENT), files={"file": ("hw.ipynb", REFERENCE)})
    assert r.status_code == 400
    admin_list = client.get("/api/assignments", headers=hdr(ADMIN)).json()
    assert admin_list[0]["published"] is False and admin_list[0]["publish_at"]
    time.sleep(0.2)
    assert not any("Отложенное" in m[0] for _, m in sent)

    # время наступило: организатор сдвинул публикацию на «сейчас»
    past = datetime.now(timezone.utc) - timedelta(minutes=1)
    client.post(f"/api/admin/assignments/{aid}/edit", headers=hdr(ADMIN), data={"publish_at": local(past)})
    for _ in range(50):
        if any("Отложенное" in m[0] for _, m in sent):
            break
        time.sleep(0.05)
    notes = [tg for tg, m in sent if "Новое задание" in m[0] and "Отложенное" in m[0]]
    assert notes == [STUDENT]  # только участникам, один раз
    assert client.get("/api/assignments", headers=hdr(STUDENT)).json()[0]["id"] == aid


def test_edit_assignment_files_reset_cache_and_validate(ctx):
    client, service, _ = ctx
    aid = create_assignment(client)
    r = client.post(f"/api/admin/assignments/{aid}/edit", headers=hdr(STUDENT), data={"title": "x"})
    assert r.status_code == 403
    r = client.post(f"/api/admin/assignments/{aid}/edit", headers=hdr(ADMIN),
                    data={"title": "Новое имя", "max_attempts": "5", "description": "d2"})
    assert r.status_code == 200 and r.json()["files_changed"] is False
    a = client.get("/api/assignments", headers=hdr(STUDENT)).json()[0]
    assert (a["title"], a["max_attempts"], a["description"]) == ("Новое имя", 5, "d2")

    r = client.post(f"/api/admin/assignments/{aid}/edit", headers=hdr(ADMIN),
                    files={"reference": ("r.ipynb", b"not a notebook")})
    assert r.status_code == 400
    r = client.post(f"/api/admin/assignments/{aid}/edit", headers=hdr(ADMIN),
                    files={"reference": ("r.ipynb", REFERENCE)})
    assert r.status_code == 200 and r.json()["files_changed"] is True and len(r.json()["tasks"]) == 2

    sub = submit(client, aid, REFERENCE)
    assert client.post(f"/api/admin/assignments/{aid}/recheck", headers=hdr(ADMIN)).json()["queued"] == 1
    assert wait_done(client, sub["id"], STUDENT)["score"] == 20


# ---------- хакатоны ----------

TRAIN = b"id,f,target\n1,0.1,0\n2,0.9,1\n"
TEST = b"id,f\n10,0.2\n11,0.8\n12,0.7\n13,0.1\n"
SOLUTION = b"id,target\n10,0\n11,1\n12,1\n13,0\n"


def create_hackathon(client, starts: datetime, ends: datetime, **extra):
    data = {"title": "Пульс", "description": "Предскажите target", "starts_at": local(starts), "ends_at": local(ends),
            "metric": "accuracy", "id_column": "id", "target_column": "target", "daily_limit": "2"} | extra
    files = {"train": ("train.csv", TRAIN), "test": ("test.csv", TEST), "solution": ("solution.csv", SOLUTION),
             "task": ("task.md", "# Условие\nСделайте лучше бейзлайна".encode())}
    return client.post("/api/admin/hackathons", headers=hdr(ADMIN), data=data, files=files)


def test_hackathon_validation(ctx):
    client, _, _ = ctx
    now = datetime.now(timezone.utc)
    assert create_hackathon(client, now, now - timedelta(hours=1)).status_code == 400
    r = create_hackathon(client, now, now + timedelta(hours=1), target_column="y")
    assert r.status_code == 400 and "y" in r.json()["detail"]
    r = client.post("/api/admin/hackathons", headers=hdr(ADMIN),
                    data={"title": "x", "starts_at": local(now), "ends_at": local(now + timedelta(hours=1)),
                          "metric": "accuracy", "id_column": "id", "target_column": "target"},
                    files={"train": ("a.csv", TRAIN), "test": ("t.csv", b"id\n10\n99\n"), "solution": ("s.csv", SOLUTION)})
    assert r.status_code == 400 and "не совпадают" in r.json()["detail"]
    assert create_hackathon(client, now, now + timedelta(hours=1), metric="magic").status_code == 400


def test_hackathon_upcoming_then_running(ctx):
    client, service, sent = ctx
    client.get("/api/me", headers=hdr(STUDENT))
    assert client.get("/api/hackathons", headers=hdr(STUDENT)).json() == []

    now = datetime.now(timezone.utc)
    r = create_hackathon(client, now + timedelta(days=1), now + timedelta(days=2))
    assert r.status_code == 200, r.text
    hid = r.json()["id"]
    h = client.get("/api/hackathons", headers=hdr(STUDENT)).json()[0]
    assert h["status"] == "upcoming" and h["files"] == {} and h["description"] == ""
    assert client.get(f"/api/hackathons/{hid}/files/train", headers=hdr(STUDENT)).status_code == 403
    r = client.post(f"/api/hackathons/{hid}/submissions", headers=hdr(STUDENT), files={"file": ("p.csv", SOLUTION)})
    assert r.status_code == 400 and "начнётся" in r.json()["detail"]
    for _ in range(50):
        if any("Анонс" in m[0] for _, m in sent):
            break
        time.sleep(0.05)
    assert [tg for tg, m in sent if "Анонс" in m[0]] == [STUDENT]

    # организатор переносит старт на «сейчас»
    r = client.post(f"/api/admin/hackathons/{hid}/edit", headers=hdr(ADMIN),
                    data={"starts_at": local(now - timedelta(minutes=1))})
    assert r.status_code == 200, r.text
    h = client.get(f"/api/hackathons/{hid}", headers=hdr(STUDENT)).json()
    assert h["status"] == "running" and set(h["files"]) == {"train", "test", "task"}
    assert "Сделайте лучше бейзлайна" in h["description"] and "Предскажите" in h["description"]
    assert client.get(f"/api/hackathons/{hid}/files/train", headers=hdr(STUDENT)).content == TRAIN
    assert client.get(f"/api/hackathons/{hid}/files/solution", headers=hdr(STUDENT)).status_code == 403
    assert client.get(f"/api/hackathons/{hid}/files/solution", headers=hdr(ADMIN)).content == SOLUTION


def test_hackathon_submissions_limits_and_leaderboard(ctx):
    client, service, _ = ctx
    now = datetime.now(timezone.utc)
    hid = create_hackathon(client, now - timedelta(minutes=1), now + timedelta(hours=1)).json()["id"]

    def send(user, raw):
        return client.post(f"/api/hackathons/{hid}/submissions", headers=hdr(user), files={"file": ("pred.csv", raw)})

    bad = send(STUDENT, b"id,target\n10,0\n")
    assert bad.status_code == 200 and bad.json()["status"] == "error" and "нет предсказаний" in bad.json()["error"]
    a = send(STUDENT, b"id,target\n10,0\n11,0\n12,1\n13,0\n").json()
    assert a["status"] == "done" and a["score"] == 0.75 and a["number"] == 2
    b = send(STUDENT, b"id,target\n10,0\n11,1\n12,1\n13,1\n").json()
    assert b["score"] == 0.75
    # лимит 2 в день: ошибочная попытка не считается, а третья удачная — уже нет
    r = send(STUDENT, SOLUTION)
    assert r.status_code == 400 and "Лимит" in r.json()["detail"]
    other = send(3, SOLUTION).json()
    assert other["score"] == 1.0

    mine = client.get(f"/api/hackathons/{hid}/submissions", headers=hdr(STUDENT)).json()
    assert [x["number"] for x in mine] == [3, 2, 1]
    lb = client.get(f"/api/hackathons/{hid}/leaderboard", headers=hdr(STUDENT)).json()
    assert [(r["place"], r["score"], r["attempts"]) for r in lb] == [(1, 1.0, 1), (2, 0.75, 2)]
    assert lb[1]["is_me"] and "user" not in lb[0]
    assert "user" in client.get(f"/api/hackathons/{hid}/leaderboard", headers=hdr(ADMIN)).json()[0]
    info = client.get(f"/api/hackathons/{hid}", headers=hdr(STUDENT)).json()
    assert info["my_best"] == 0.75 and info["used_today"] == 2

    # форма правки присылает те же метрику и колонки — пересчёта нет
    r = client.post(f"/api/admin/hackathons/{hid}/edit", headers=hdr(ADMIN),
                    data={"metric": "accuracy", "id_column": "id", "target_column": "target", "daily_limit": "2"})
    assert r.status_code == 200 and r.json()["rescored"] == 0

    # организатор исправил ответы — всё пересчитано
    fixed = b"id,target\n10,0\n11,1\n12,1\n13,1\n"
    r = client.post(f"/api/admin/hackathons/{hid}/edit", headers=hdr(ADMIN), files={"solution": ("s.csv", fixed)})
    assert r.status_code == 200 and r.json()["rescored"] == 4
    lb = client.get(f"/api/hackathons/{hid}/leaderboard", headers=hdr(STUDENT)).json()
    assert lb[0]["score"] == 1.0 and lb[0]["is_me"]
    assert len(client.get(f"/api/admin/hackathons/{hid}/submissions", headers=hdr(ADMIN)).json()) == 4

    # после окончания приём закрыт
    client.post(f"/api/admin/hackathons/{hid}/edit", headers=hdr(ADMIN),
                data={"starts_at": local(now - timedelta(hours=2)), "ends_at": local(now - timedelta(minutes=1))})
    r = send(3, SOLUTION)
    assert r.status_code == 400 and "закончился" in r.json()["detail"]


def test_lower_is_better_leaderboard(ctx):
    client, _, _ = ctx
    now = datetime.now(timezone.utc)
    sol = b"id,y\n1,1\n2,2\n"
    r = client.post("/api/admin/hackathons", headers=hdr(ADMIN),
                    data={"title": "Регрессия", "starts_at": local(now - timedelta(minutes=1)),
                          "ends_at": local(now + timedelta(hours=1)), "metric": "rmse", "id_column": "id", "target_column": "y"},
                    files={"train": ("a.csv", b"id,y\n0,1\n"), "test": ("t.csv", b"id\n1\n2\n"), "solution": ("s.csv", sol)})
    hid = r.json()["id"]
    for user, raw in [(STUDENT, b"id,y\n1,1\n2,4\n"), (STUDENT, b"id,y\n1,1\n2,3\n"), (3, b"id,y\n1,1\n2,2.5\n")]:
        client.post(f"/api/hackathons/{hid}/submissions", headers=hdr(user), files={"file": ("p.csv", raw)})
    lb = client.get(f"/api/hackathons/{hid}/leaderboard", headers=hdr(ADMIN)).json()
    assert [r["user"]["tg_id"] for r in lb] == [3, STUDENT]
    assert lb[1]["attempts"] == 2


def test_deadline_in_moscow_time_is_stored_correctly(ctx):
    client, _, _ = ctx
    r = client.post("/api/admin/assignments", headers=hdr(ADMIN),
                    data={"title": "МСК", "deadline": "2030-11-20T23:59"},
                    files={"template": ("t.ipynb", TEMPLATE), "reference": ("r.ipynb", REFERENCE)})
    assert r.status_code == 200
    a = client.get("/api/assignments", headers=hdr(STUDENT)).json()[0]
    assert datetime.fromisoformat(a["deadline"]) == datetime(2030, 11, 20, 20, 59, tzinfo=timezone.utc)


def test_edit_can_remove_deadline_and_schedule(ctx):
    client, _, _ = ctx
    aid = create_assignment(client)
    later = datetime.now(timezone.utc) + timedelta(days=1)
    r = client.post(f"/api/admin/assignments/{aid}/edit", headers=hdr(ADMIN),
                    data={"deadline": local(later), "publish_at": local(later)})
    assert r.status_code == 200
    a = client.get("/api/assignments", headers=hdr(ADMIN)).json()[0]
    assert a["deadline"] and a["publish_at"]
    # пустое значение не трогает поле, "null" — убирает
    client.post(f"/api/admin/assignments/{aid}/edit", headers=hdr(ADMIN), data={"deadline": "", "publish_at": ""})
    assert client.get("/api/assignments", headers=hdr(ADMIN)).json()[0]["deadline"]
    client.post(f"/api/admin/assignments/{aid}/edit", headers=hdr(ADMIN), data={"deadline": "null", "publish_at": "null"})
    a = client.get("/api/assignments", headers=hdr(ADMIN)).json()[0]
    assert a["deadline"] is None and a["publish_at"] is None and a["published"] is True
