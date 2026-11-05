"""Тестовая часть заданий: разбор .md, проверка ответов, API участника и организатора."""

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from app import quiz as qz
from tests.test_checker import REFERENCE, TEMPLATE
from tests.test_web import ADMIN, STUDENT, ctx, hdr, wait_done  # noqa: F401 — фикстура ctx

EXAMPLE = (Path(__file__).parent.parent / "docs" / "quiz_example.md").read_text(encoding="utf-8")


def local(dt: datetime) -> str:
    return dt.astimezone(timezone(timedelta(hours=3))).replace(tzinfo=None).isoformat(timespec="minutes")


# ---------- разбор .md ----------


def test_parse_example_file():
    data, warnings = qz.parse_markdown(EXAMPLE)
    assert warnings == []
    assert data["title"] == "Линейная алгебра: разминка"
    qs = data["questions"]
    assert [q["type"] for q in qs] == ["single", "multiple", "number", "number", "text", "single"]
    q1 = qs[0]
    assert q1["points"] == 2  # из заголовка «(2 балла)»
    assert r"\|a\|\,\|b\|\cos\theta" in q1["text"]  # формула не испорчена
    assert q1["options"][1]["text"] == "$0$" and q1["correct"] == [q1["options"][1]["id"]]
    assert "косинус" in q1["explanation"]
    assert len(qs[1]["correct"]) == 2
    assert qs[3]["value"] == 1.41 and qs[3]["tolerance"] == 0.01
    assert qs[4]["answers"] == ["транспонирование", "транспонирования"]
    # варианты A) B) C) и «Ответ: B», код в условии не разбирается как варианты
    assert "```python" in qs[5]["text"] and qs[5]["points"] == 2
    assert qs[5]["correct"] == [qs[5]["options"][1]["id"]]


def test_parse_numbered_without_headings_and_markers_inside_code():
    md = """1. Что выведет код?
```python
# - [x] это не вариант
print("Ответ: 5")
```
- (x) 5
- ( ) 6

2) Сколько будет 2+2?
Ответ: 4
"""
    data, warnings = qz.parse_markdown(md)
    assert warnings == []
    q1, q2 = data["questions"]
    assert q1["type"] == "single" and len(q1["options"]) == 2 and "- [x] это не вариант" in q1["text"]
    assert q2["type"] == "number" and q2["value"] == 4


def test_parse_warnings_and_errors():
    data, warnings = qz.parse_markdown("## Вопрос\n- [ ] а\n- [ ] б\n\n## Второй\nбез ответа\n")
    assert any("не отмечен правильный" in w for w in warnings)
    assert any("не указан" in w for w in warnings)
    with pytest.raises(qz.QuizError):
        qz.parse_markdown("просто текст без вопросов")
    with pytest.raises(qz.QuizError):
        qz.parse_markdown("   ")


def test_markdown_roundtrip():
    data, _ = qz.parse_markdown(EXAMPLE)
    quiz = qz.normalize_quiz(data)
    again, warnings = qz.parse_markdown(qz.to_markdown(quiz, "T"))
    assert warnings == []
    for a, b in zip(quiz["questions"], again["questions"]):
        assert a["type"] == b["type"] and a["text"] == b["text"] and a["points"] == b["points"]
        assert a.get("explanation") == b.get("explanation")


# ---------- проверка ----------


def test_normalize_validates():
    with pytest.raises(qz.QuizError, match="Вопрос 1"):
        qz.normalize_quiz({"questions": [{"type": "single", "text": "?", "options": ["a", "b"], "correct": []}]})
    with pytest.raises(qz.QuizError, match="несколько верных"):
        qz.normalize_quiz({"questions": [{"type": "single", "text": "?", "options": [{"id": "a", "text": "1"}, {"id": "b", "text": "2"}],
                                          "correct": ["a", "b"]}]})
    with pytest.raises(qz.QuizError, match="число"):
        qz.normalize_quiz({"questions": [{"type": "number", "text": "?", "value": "abc"}]})
    assert qz.normalize_quiz({"questions": []}) is None


def test_grading_rules():
    quiz = qz.normalize_quiz({"attempts": 2, "questions": [
        {"id": "s", "type": "single", "text": "?", "points": 2,
         "options": [{"id": "a", "text": "1"}, {"id": "b", "text": "2"}], "correct": ["b"]},
        {"id": "m", "type": "multiple", "text": "?",
         "options": [{"id": "a", "text": "1"}, {"id": "b", "text": "2"}, {"id": "c", "text": "3"}], "correct": ["a", "c"]},
        {"id": "t", "type": "text", "text": "?", "answers": ["Транспонирование"]},
        {"id": "n", "type": "number", "text": "?", "value": 0.5, "tolerance": 0.01},
    ]})
    r = qz.grade(quiz, {"s": "b", "m": ["c", "a"], "t": "  транспонирование. ", "n": "1/2"})
    assert r["score"] == r["max_score"] == 5 and r["auto_comment"] == "Молодец, всё верно!"
    r = qz.grade(quiz, {"s": "a", "m": ["a"], "t": "нет", "n": "0,505"})
    assert [q["is_correct"] for q in r["questions"]] == [False, False, False, True]
    assert r["score"] == 1 and r["auto_comment"] == "В следующий раз получится лучше!"
    r = qz.grade(quiz, {"s": "b", "m": ["a", "c"], "t": "ТРАНСПОНИРОВАНИЕ", "n": "мимо"})
    assert r["score"] == 4 and r["auto_comment"] == "Совсем чуть-чуть не хватило до идеала."
    # мусор в ответах не ломает проверку
    r = qz.grade(quiz, {"s": ["b"], "m": "a", "t": None, "n": {"x": 1}, "zzz": "?"})
    assert r["score"] == 0 and r["answered"] == 0
    hidden = qz.hide_keys(r)
    assert all("correct" not in q and "answers" not in q and "value" not in q for q in hidden["questions"])


# ---------- API ----------


def make_quiz() -> dict:
    data, _ = qz.parse_markdown(EXAMPLE)
    return data


def create_quiz_assignment(client, with_notebook: bool = False, **extra) -> int:
    files = {"template": ("t.ipynb", TEMPLATE), "reference": ("r.ipynb", REFERENCE)} if with_notebook else None
    r = client.post("/api/admin/assignments", headers=hdr(ADMIN),
                    data={"title": "Векторы", "quiz": json.dumps(make_quiz()), **extra}, files=files)
    assert r.status_code == 200, r.text
    return r.json()["id"]


def answers_for(client, aid: int, all_right: bool = True) -> dict:
    full = client.get(f"/api/admin/assignments/{aid}/quiz", headers=hdr(ADMIN)).json()
    out = {}
    for q in full["questions"]:
        if q["type"] == "single":
            out[q["id"]] = q["correct"][0] if all_right else next(o["id"] for o in q["options"] if o["id"] not in q["correct"])
        elif q["type"] == "multiple":
            out[q["id"]] = q["correct"]
        elif q["type"] == "text":
            out[q["id"]] = q["answers"][0]
        else:
            out[q["id"]] = str(q["value"]).replace(".", ",")
    return out


def test_quiz_only_assignment_flow(ctx):
    client, service, sent = ctx
    future = datetime.now(timezone.utc) + timedelta(days=1)
    aid = create_quiz_assignment(client, deadline=local(future))
    lst = client.get("/api/assignments", headers=hdr(STUDENT)).json()
    a = lst[0]
    assert a["parts"] == ["quiz"] and a["notebook"] is None and a["max_score"] == 8
    assert a["quiz"] == {"questions": 6, "max_score": 8, "best_score": None, "attempts_used": 0, "max_attempts": 1}

    # у теста нет ноутбука: шаблон и сдача файла недоступны
    assert client.get(f"/api/assignments/{aid}/template", headers=hdr(STUDENT)).status_code == 404
    r = client.post(f"/api/assignments/{aid}/submissions", headers=hdr(STUDENT), files={"file": ("hw.ipynb", REFERENCE)})
    assert r.status_code == 400 and "нет ноутбука" in r.json()["detail"]

    # участник не видит ключей до дедлайна
    view = client.get(f"/api/assignments/{aid}/quiz", headers=hdr(STUDENT)).json()
    assert view["keys_visible"] is False and len(view["questions"]) == 6
    assert all("correct" not in q and "answers" not in q and "value" not in q and "explanation" not in q
               for q in view["questions"])

    answers = answers_for(client, aid)
    first = next(iter(answers))
    answers[first] = "нет-такого"  # ошибка в первом вопросе (2 балла)
    r = client.post(f"/api/assignments/{aid}/quiz", headers=hdr(STUDENT), json={"answers": answers})
    assert r.status_code == 200, r.text
    sub = r.json()
    assert sub["kind"] == "quiz" and sub["status"] == "done" and sub["attempt"] == 1
    assert sub["score"] == 6 and sub["max_score"] == 8
    assert sub["result"]["auto_comment"] == "Совсем чуть-чуть не хватило до идеала."
    assert sub["result"]["keys_hidden"] is True and "correct" not in sub["result"]["questions"][0]
    assert sub["result"]["questions"][0]["is_correct"] is False

    # попытка одна
    r = client.post(f"/api/assignments/{aid}/quiz", headers=hdr(STUDENT), json={"answers": answers})
    assert r.status_code == 400 and "Попытки закончились" in r.json()["detail"]

    mine = client.get("/api/submissions", headers=hdr(STUDENT)).json()
    assert mine[0]["kind"] == "quiz" and "correct" not in mine[0]["result"]["questions"][0]
    lst = client.get("/api/assignments", headers=hdr(STUDENT)).json()
    assert lst[0]["best_score"] == 6 and lst[0]["quiz"]["attempts_used"] == 1

    # организатор видит всё и меняет баллы за вопрос
    adm = client.get(f"/api/submissions/{sub['id']}", headers=hdr(ADMIN)).json()
    assert "correct" in adm["result"]["questions"][0]
    r = client.patch(f"/api/admin/submissions/{sub['id']}/review", headers=hdr(ADMIN),
                     json={"task_scores": {"1": 1}, "comment": "Почти, перечитай про косинус"})
    assert r.status_code == 200, r.text
    rev = r.json()
    assert rev["score"] == 7 and rev["auto_score"] == 6 and rev["edited_by"] == "Test" and rev["comment_by"] == "Test"
    assert rev["result"]["questions"][0]["auto_score"] == 0
    assert client.patch(f"/api/admin/submissions/{sub['id']}/review", headers=hdr(ADMIN),
                        json={"task_scores": {"1": 3}}).status_code == 400  # больше веса вопроса
    note = sent[-1][1][0]
    assert "твой тест" in note and "Вопрос 1: 0 → 1" in note and "Почти" in note

    data = client.get("/api/admin/participants", headers=hdr(ADMIN)).json()
    assert data["assignments"][0]["max_score"] == 8
    row = next(p for p in data["participants"] if p["tg_id"] == STUDENT)["assignments"][str(aid)]
    assert row["best_score"] == 7 and row["quiz_score"] == 7 and row["notebook_score"] is None

    csv_lines = client.get(f"/api/admin/assignments/{aid}/export.csv", headers=hdr(ADMIN)).content.decode("utf-8-sig").splitlines()
    assert csv_lines[0].endswith("quiz_score,q_1,q_2,q_3,q_4,q_5,q_6") and "task_1" not in csv_lines[0]
    assert csv_lines[1].split(",")[5:7] == ["7", "8"]

    # после дедлайна участник видит правильные ответы и пояснения
    past = datetime.now(timezone.utc) - timedelta(minutes=1)
    assert client.post(f"/api/admin/assignments/{aid}/edit", headers=hdr(ADMIN), data={"deadline": local(past)}).status_code == 200
    view = client.get(f"/api/assignments/{aid}/quiz", headers=hdr(STUDENT)).json()
    assert view["keys_visible"] is True and "explanation" in view["questions"][0]
    mine = client.get(f"/api/submissions/{sub['id']}", headers=hdr(STUDENT)).json()
    assert "correct" in mine["result"]["questions"][0] and "keys_hidden" not in mine["result"]


def test_combined_assignment_and_regrade(ctx):
    client, service, _ = ctx
    aid = create_quiz_assignment(client, with_notebook=True)
    a = client.get("/api/assignments", headers=hdr(STUDENT)).json()[0]
    assert a["parts"] == ["quiz", "notebook"] and a["max_score"] == 28
    assert a["notebook"]["task_count"] == 2 and a["notebook"]["max_score"] == 20

    sid = client.post(f"/api/assignments/{aid}/submissions", headers=hdr(STUDENT), files={"file": ("hw.ipynb", REFERENCE)}).json()["id"]
    nb = wait_done(client, sid, STUDENT)
    assert nb["kind"] == "notebook" and nb["attempt"] == 1
    answers = answers_for(client, aid, all_right=False)
    q = client.post(f"/api/assignments/{aid}/quiz", headers=hdr(STUDENT), json={"answers": answers}).json()
    assert q["attempt"] == 1  # нумерация попыток у теста своя
    a = client.get("/api/assignments", headers=hdr(STUDENT)).json()[0]
    assert a["notebook"]["best_score"] == 20 and a["quiz"]["best_score"] == q["score"]
    assert a["best_score"] == 20 + q["score"] and a["attempts_used"] == 1

    # организатор исправил ключ первого вопроса: попытка пересчитывается
    full = client.get(f"/api/admin/assignments/{aid}/quiz", headers=hdr(ADMIN)).json()
    q1 = full["questions"][0]
    q1["correct"] = [answers[q1["id"]]]
    r = client.post(f"/api/admin/assignments/{aid}/edit", headers=hdr(ADMIN), data={"quiz": json.dumps(full)})
    assert r.status_code == 200, r.text
    again = client.get(f"/api/submissions/{q['id']}", headers=hdr(ADMIN)).json()
    assert again["score"] == q["score"] + 2 and again["result"]["questions"][0]["is_correct"] is True

    # перепроверка относится только к ноутбукам
    assert client.post(f"/api/admin/assignments/{aid}/recheck", headers=hdr(ADMIN)).json()["queued"] == 1
    assert client.post(f"/api/admin/submissions/{q['id']}/recheck", headers=hdr(ADMIN)).status_code == 400
    assert client.get(f"/api/admin/submissions/{q['id']}/code", headers=hdr(ADMIN)).status_code == 404

    csv_head = client.get(f"/api/admin/assignments/{aid}/export.csv", headers=hdr(ADMIN)).content.decode("utf-8-sig").splitlines()[0]
    assert "notebook_score,task_1,task_2,quiz_score,q_1" in csv_head

    # убрать тест можно, раз есть ноутбук
    r = client.post(f"/api/admin/assignments/{aid}/edit", headers=hdr(ADMIN), data={"quiz": "null"})
    assert r.status_code == 200
    assert client.get("/api/assignments", headers=hdr(STUDENT)).json()[0]["parts"] == ["notebook"]


def test_quiz_admin_tools_and_validation(ctx):
    client, _, _ = ctx
    # пустое задание
    r = client.post("/api/admin/assignments", headers=hdr(ADMIN), data={"title": "Пусто"})
    assert r.status_code == 400 and "не может быть пустым" in r.json()["detail"]
    # ноутбук без эталона
    r = client.post("/api/admin/assignments", headers=hdr(ADMIN), data={"title": "X"}, files={"template": ("t.ipynb", TEMPLATE)})
    assert r.status_code == 400
    # ошибка в тесте
    bad = {"questions": [{"type": "single", "text": "?", "options": ["a", "b"], "correct": []}]}
    r = client.post("/api/admin/assignments", headers=hdr(ADMIN), data={"title": "X", "quiz": json.dumps(bad)})
    assert r.status_code == 400 and "Вопрос 1" in r.json()["detail"]

    # разбор .md файлом и текстом
    r = client.post("/api/admin/quiz/parse", headers=hdr(ADMIN), files={"file": ("q.md", EXAMPLE.encode())})
    assert r.status_code == 200 and len(r.json()["questions"]) == 6 and r.json()["warnings"] == []
    r = client.post("/api/admin/quiz/parse", headers=hdr(ADMIN), data={"text": "нет вопросов"})
    assert r.status_code == 400
    assert client.post("/api/admin/quiz/parse", headers=hdr(STUDENT), data={"text": "## a\nОтвет: 1"}).status_code == 403

    aid = create_quiz_assignment(client)
    md = client.get(f"/api/admin/assignments/{aid}/quiz.md", headers=hdr(ADMIN))
    assert md.status_code == 200 and "## Вопрос 1" in md.content.decode()
    # у задания только тест — убрать его нельзя
    r = client.post(f"/api/admin/assignments/{aid}/edit", headers=hdr(ADMIN), data={"quiz": "null"})
    assert r.status_code == 400
    # к тесту можно добавить ноутбук
    r = client.post(f"/api/admin/assignments/{aid}/edit", headers=hdr(ADMIN),
                    files={"template": ("t.ipynb", TEMPLATE), "reference": ("r.ipynb", REFERENCE)})
    assert r.status_code == 200, r.text
    assert len(r.json()["tasks"]) == 2
    assert client.get("/api/assignments", headers=hdr(STUDENT)).json()[0]["parts"] == ["quiz", "notebook"]


def test_quiz_closed_and_unpublished(ctx):
    client, _, _ = ctx
    later = datetime.now(timezone.utc) + timedelta(days=1)
    aid = create_quiz_assignment(client, publish_at=local(later))
    assert client.get(f"/api/assignments/{aid}/quiz", headers=hdr(STUDENT)).status_code == 404
    r = client.post(f"/api/assignments/{aid}/quiz", headers=hdr(STUDENT), json={"answers": {}})
    assert r.status_code == 400 and "не опубликовано" in r.json()["detail"]
    # организатор может пройти тест для проверки, без лимита попыток
    for _ in range(2):
        assert client.post(f"/api/assignments/{aid}/quiz", headers=hdr(ADMIN), json={"answers": {}}).status_code == 200
