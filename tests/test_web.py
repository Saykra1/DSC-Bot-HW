import hashlib
import hmac
import json
import time
from contextlib import asynccontextmanager
from pathlib import Path
from urllib.parse import urlencode

import pytest
from fastapi.testclient import TestClient

from app.config import Settings
from app.db import init_db, make_engine, make_sessionmaker
from app.service import HomeworkService
from app.web import build_app
from tests.test_checker import REFERENCE, TEMPLATE, FakeLLM, make_student

BOT_TOKEN = "123456:TEST-token"
ADMIN = 1
STUDENT = 2


def init_data(user_id: int, token: str = BOT_TOKEN, auth_date: int | None = None) -> str:
    fields = {
        "auth_date": str(auth_date or int(time.time())),
        "query_id": "q",
        "user": json.dumps({"id": user_id, "first_name": "Test", "username": f"user{user_id}"}),
    }
    check = "\n".join(f"{k}={v}" for k, v in sorted(fields.items()))
    secret = hmac.new(b"WebAppData", token.encode(), hashlib.sha256).digest()
    fields["hash"] = hmac.new(secret, check.encode(), hashlib.sha256).hexdigest()
    return urlencode(fields)


def hdr(user_id: int) -> dict:
    return {"X-Telegram-Init-Data": init_data(user_id)}


@pytest.fixture
def ctx(tmp_path: Path):
    settings = Settings(_env_file=None, bot_token=BOT_TOKEN, admin_ids=[ADMIN], data_dir=tmp_path, default_max_attempts=2)
    engine = make_engine(settings.db_url)
    service = HomeworkService(settings, make_sessionmaker(engine), FakeLLM())
    sent: list[tuple[int, list[str]]] = []

    async def notifier(tg_id, messages):
        sent.append((tg_id, messages))

    service.notifier = notifier

    @asynccontextmanager
    async def lifespan(app):
        await init_db(engine)
        await service.start()
        yield
        await service.stop()
        await engine.dispose()

    app = build_app(service, None, lifespan)
    with TestClient(app) as client:
        yield client, service, sent


def create_assignment(client) -> int:
    r = client.post(
        "/api/admin/assignments",
        headers=hdr(ADMIN),
        data={"title": "Предобработка", "description": "desc"},
        files={"template": ("t.ipynb", TEMPLATE), "reference": ("r.ipynb", REFERENCE)},
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert len(body["tasks"]) == 2 and body["warnings"] == []
    return body["id"]


def wait_done(client, sid: int, user: int) -> dict:
    for _ in range(100):
        r = client.get(f"/api/submissions/{sid}", headers=hdr(user)).json()
        if r["status"] in ("done", "error"):
            return r
        time.sleep(0.05)
    raise AssertionError("submission not processed")


def test_auth_required_and_signature_checked(ctx):
    client, _, _ = ctx
    assert client.get("/api/me").status_code == 401
    bad = {"X-Telegram-Init-Data": init_data(STUDENT, token="999:other")}
    assert client.get("/api/me", headers=bad).status_code == 401
    old = {"X-Telegram-Init-Data": init_data(STUDENT, auth_date=int(time.time()) - 3 * 24 * 3600)}
    assert client.get("/api/me", headers=old).status_code == 401
    r = client.get("/api/me", headers=hdr(STUDENT)).json()
    assert r["id"] == STUDENT and r["is_admin"] is False and r["profile"]["username"] == f"user{STUDENT}"


def test_student_cannot_use_admin_api(ctx):
    client, _, _ = ctx
    r = client.post("/api/admin/assignments", headers=hdr(STUDENT), data={"title": "x"},
                    files={"template": ("t.ipynb", TEMPLATE), "reference": ("r.ipynb", REFERENCE)})
    assert r.status_code == 403


def test_full_flow(ctx):
    client, service, sent = ctx
    aid = create_assignment(client)

    lst = client.get("/api/assignments", headers=hdr(STUDENT)).json()
    assert lst[0]["id"] == aid and lst[0]["attempts_used"] == 0 and lst[0]["max_score"] == 20

    tpl = client.get(f"/api/assignments/{aid}/template", headers=hdr(STUDENT))
    assert tpl.status_code == 200 and json.loads(tpl.content)["cells"]

    student = make_student({14: "X_train['weight'] = X_train['weight'].fillna(1)\n"})
    r = client.post(f"/api/assignments/{aid}/submissions", headers=hdr(STUDENT), files={"file": ("hw.ipynb", student)})
    assert r.status_code == 200, r.text
    res = wait_done(client, r.json()["id"], STUDENT)
    assert res["status"] == "done"
    assert res["score"] == 8 and res["max_score"] == 20
    assert res["result"]["tasks"][1]["verdict"] == "not_done"

    # уведомление ушло участнику
    assert sent and sent[-1][0] == STUDENT and "8/20" in sent[-1][1][0]

    # чужую сдачу смотреть нельзя, организатору можно
    assert client.get(f"/api/submissions/{res['id']}", headers=hdr(3)).status_code == 403
    assert client.get(f"/api/submissions/{res['id']}", headers=hdr(ADMIN)).json()["user"]["tg_id"] == STUDENT

    # второй попыткой сдаём эталон, третья уже не принимается (лимит 2)
    r2 = client.post(f"/api/assignments/{aid}/submissions", headers=hdr(STUDENT), files={"file": ("hw.ipynb", REFERENCE)})
    assert wait_done(client, r2.json()["id"], STUDENT)["score"] == 20
    r3 = client.post(f"/api/assignments/{aid}/submissions", headers=hdr(STUDENT), files={"file": ("hw.ipynb", REFERENCE)})
    assert r3.status_code == 400 and "Попытки закончились" in r3.json()["detail"]

    csv_text = client.get(f"/api/admin/assignments/{aid}/export.csv", headers=hdr(ADMIN)).content.decode("utf-8-sig")
    lines = csv_text.strip().splitlines()
    assert lines[0].startswith("tg_id") and lines[1].startswith(f"{STUDENT},user{STUDENT}") and ",20,20,2," in lines[1]


def test_rejects_bad_files_and_closed_assignment(ctx):
    client, _, _ = ctx
    aid = create_assignment(client)
    r = client.post(f"/api/assignments/{aid}/submissions", headers=hdr(STUDENT), files={"file": ("hw.ipynb", b"garbage")})
    assert r.status_code == 400
    r = client.post(f"/api/assignments/{aid}/submissions", headers=hdr(STUDENT), files={"file": ("hw.py", TEMPLATE)})
    assert r.status_code == 400

    r = client.patch(f"/api/admin/assignments/{aid}", headers=hdr(ADMIN), json={"is_active": False})
    assert r.status_code == 200
    assert client.get("/api/assignments", headers=hdr(STUDENT)).json() == []
    r = client.post(f"/api/assignments/{aid}/submissions", headers=hdr(STUDENT), files={"file": ("hw.ipynb", TEMPLATE)})
    assert r.status_code == 400 and "закрыт" in r.json()["detail"]


def test_deadline(ctx):
    client, _, _ = ctx
    r = client.post(
        "/api/admin/assignments", headers=hdr(ADMIN),
        data={"title": "Старое", "deadline": "2020-01-01T10:00"},
        files={"template": ("t.ipynb", TEMPLATE), "reference": ("r.ipynb", REFERENCE)},
    )
    aid = r.json()["id"]
    r = client.post(f"/api/assignments/{aid}/submissions", headers=hdr(STUDENT), files={"file": ("hw.ipynb", TEMPLATE)})
    assert r.status_code == 400 and "Дедлайн" in r.json()["detail"]


def test_llm_failure_marks_error_and_does_not_burn_attempt(ctx):
    client, service, sent = ctx
    aid = create_assignment(client)

    class Broken:
        async def complete_json(self, system, user):
            raise RuntimeError("rate limit")

    service.llm = Broken()
    student = make_student({14: "x = 1\n"})
    r = client.post(f"/api/assignments/{aid}/submissions", headers=hdr(STUDENT), files={"file": ("hw.ipynb", student)})
    res = wait_done(client, r.json()["id"], STUDENT)
    assert res["status"] == "error" and res["error"] is None  # участнику текст ошибки не показываем
    assert client.get("/api/assignments", headers=hdr(STUDENT)).json()[0]["attempts_used"] == 0
    assert any(tg == ADMIN for tg, _ in sent)

    # перепроверка, которая снова упала, не дёргает участника повторно
    student_msgs = sum(1 for tg, _ in sent if tg == STUDENT)
    client.post(f"/api/admin/submissions/{res['id']}/recheck", headers=hdr(ADMIN))
    assert wait_done(client, res["id"], STUDENT)["status"] == "error"
    assert sum(1 for tg, _ in sent if tg == STUDENT) == student_msgs

    service.llm = FakeLLM()
    client.post(f"/api/admin/submissions/{res['id']}/recheck", headers=hdr(ADMIN))
    assert wait_done(client, res["id"], STUDENT)["status"] == "done"


def test_profile_and_participants(ctx):
    client, _, _ = ctx
    aid = create_assignment(client)
    r = client.patch("/api/profile", headers=hdr(STUDENT), json={"nickname": "  Кот  ", "fio": "Иванов Иван Иванович"})
    assert r.status_code == 200 and r.json()["nickname"] == "Кот" and r.json()["fio"] == "Иванов Иван Иванович"
    assert client.patch("/api/profile", headers=hdr(STUDENT), json={"fio": "Иван"}).status_code == 400
    # nickname не трогаем, если поле не передано
    assert client.patch("/api/profile", headers=hdr(STUDENT), json={"fio": ""}).json()["nickname"] == "Кот"

    sid = client.post(f"/api/assignments/{aid}/submissions", headers=hdr(STUDENT), files={"file": ("hw.ipynb", REFERENCE)}).json()["id"]
    wait_done(client, sid, STUDENT)
    client.get("/api/me", headers=hdr(3))  # участник без сдач

    assert client.get("/api/admin/participants", headers=hdr(STUDENT)).status_code == 403
    data = client.get("/api/admin/participants", headers=hdr(ADMIN)).json()
    people = {p["tg_id"]: p for p in data["participants"]}
    assert ADMIN not in people
    assert people[STUDENT]["nickname"] == "Кот" and people[STUDENT]["assignments"][str(aid)]["best_score"] == 20
    assert people[3]["assignments"] == {}

    csv_text = client.get(f"/api/admin/assignments/{aid}/export.csv", headers=hdr(ADMIN)).content.decode("utf-8-sig")
    assert "nickname,fio" in csv_text.splitlines()[0] and ",Кот," in csv_text


def test_admin_review_changes_scores_and_notifies(ctx):
    client, _, sent = ctx
    aid = create_assignment(client)
    student = make_student({14: "x = 1\n", 25: "y = 2\n"})
    sid = client.post(f"/api/assignments/{aid}/submissions", headers=hdr(STUDENT), files={"file": ("hw.ipynb", student)}).json()["id"]
    assert wait_done(client, sid, STUDENT)["score"] == 16

    code = client.get(f"/api/admin/submissions/{sid}/code", headers=hdr(ADMIN)).json()
    assert code["tasks"][0]["code"].strip() == "x = 1" and "fillna(median)" in code["tasks"][0]["reference"]
    assert any("y = 2" in c["source"] for c in code["cells"])
    assert client.get(f"/api/admin/submissions/{sid}/code", headers=hdr(STUDENT)).status_code == 403

    assert client.patch(f"/api/admin/submissions/{sid}/review", headers=hdr(STUDENT), json={"comment": "x"}).status_code == 403
    assert client.patch(f"/api/admin/submissions/{sid}/review", headers=hdr(ADMIN), json={"task_scores": {"1": 11}}).status_code == 400

    sent.clear()
    r = client.patch(f"/api/admin/submissions/{sid}/review", headers=hdr(ADMIN),
                     json={"task_scores": {"1": 10, "2": 5}, "comment": "Молодец, но во втором задании нет X_test"})
    body = r.json()
    assert body["score"] == 15 and body["auto_score"] == 16 and body["edited_at"]
    assert body["result"]["tasks"][0]["auto_score"] == 8 and body["result"]["tasks"][1]["verdict"] == "partial"
    assert sent[-1][0] == STUDENT
    note = sent[-1][1][0]
    assert "16/20 → 15/20" in note and "Задание 2" in note and "нет X_test" in note

    # участник видит комментарий и правку
    mine = client.get(f"/api/submissions/{sid}", headers=hdr(STUDENT)).json()
    assert mine["organizer_comment"].startswith("Молодец") and mine["score"] == 15

    # тот же комментарий и те же баллы — повторного уведомления нет
    sent.clear()
    client.patch(f"/api/admin/submissions/{sid}/review", headers=hdr(ADMIN),
                 json={"task_scores": {"1": 10}, "comment": "Молодец, но во втором задании нет X_test"})
    assert sent == []

    # вернули оценку агента — пометка «изменено» снимается
    r = client.patch(f"/api/admin/submissions/{sid}/review", headers=hdr(ADMIN), json={"task_scores": {"1": 8, "2": 8}}).json()
    assert r["score"] == 16 and r["edited_at"] is None and r["result"]["tasks"][0]["auto_score"] is None


async def test_migration_adds_new_columns_to_old_db(tmp_path):
    import sqlite3
    db = tmp_path / "bot.db"
    con = sqlite3.connect(db)
    con.execute("CREATE TABLE users (tg_id BIGINT PRIMARY KEY, username VARCHAR(64), full_name VARCHAR(256), created_at DATETIME)")
    con.execute("INSERT INTO users VALUES (5, 'old', 'Old User', '2026-11-01 10:00:00')")
    con.commit(); con.close()

    settings = Settings(_env_file=None, data_dir=tmp_path)
    engine = make_engine(settings.db_url)
    await init_db(engine)
    service = HomeworkService(settings, make_sessionmaker(engine), None)
    u = await service.update_profile(5, nickname="new", fio="Петров Пётр")
    assert u.username == "old" and u.nickname == "new"
    await engine.dispose()
