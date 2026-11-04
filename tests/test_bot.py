"""Смоук-тесты бота: прогоняем апдейты через диспетчер с подменённой сессией Telegram API."""

import io
import json
from datetime import datetime, timezone
from pathlib import Path

import pytest
from aiogram import Bot
from aiogram.client.session.base import BaseSession
from aiogram.methods import GetFile, SendDocument, SendMessage, TelegramMethod
from aiogram.types import Chat, File, Message, Update, User

from app.bot import build_dispatcher
from app.config import Settings
from app.db import init_db, make_engine, make_sessionmaker
from app.service import HomeworkService
from tests.test_checker import REFERENCE, TEMPLATE, FakeLLM, make_student

ADMIN, STUDENT = 1, 2


class FakeSession(BaseSession):
    def __init__(self, files: dict[str, bytes]):
        super().__init__()
        self.requests: list[TelegramMethod] = []
        self.files = files

    async def make_request(self, bot, method, timeout=None):
        self.requests.append(method)
        if isinstance(method, (SendMessage, SendDocument)):
            return Message(message_id=len(self.requests), date=datetime.now(timezone.utc),
                           chat=Chat(id=method.chat_id, type="private"), text=getattr(method, "text", None))
        if isinstance(method, GetFile):
            return File(file_id=method.file_id, file_unique_id=method.file_id, file_path=method.file_id)
        return True

    async def stream_content(self, url, headers=None, timeout=30, chunk_size=65536, raise_for_status=True):
        file_id = url.rsplit("/", 1)[-1]
        yield self.files[file_id]

    async def close(self):
        pass

    def texts(self) -> list[str]:
        return [r.text for r in self.requests if isinstance(r, SendMessage)]


@pytest.fixture
async def env(tmp_path: Path):
    settings = Settings(_env_file=None, bot_token="1:x", admin_ids=[ADMIN], data_dir=tmp_path)
    engine = make_engine(settings.db_url)
    await init_db(engine)
    service = HomeworkService(settings, make_sessionmaker(engine), FakeLLM())
    files = {"tpl": TEMPLATE, "ref": REFERENCE, "hw": make_student({14: "x = 1\n"})}
    session = FakeSession(files)
    bot = Bot("42:TEST", session=session)
    dp = build_dispatcher(service, webapp_url="https://example.com")
    counter = {"n": 0}

    async def send(user_id: int, text: str | None = None, doc: str | None = None, ext: str = ".ipynb"):
        counter["n"] += 1
        msg = {
            "message_id": counter["n"], "date": int(datetime.now().timestamp()),
            "chat": {"id": user_id, "type": "private"},
            "from": {"id": user_id, "is_bot": False, "first_name": f"U{user_id}"},
        }
        if text is not None:
            msg["text"] = text
            if text.startswith("/"):
                msg["entities"] = [{"type": "bot_command", "offset": 0, "length": len(text.split()[0])}]
        if doc is not None:
            msg["document"] = {"file_id": doc, "file_unique_id": doc, "file_name": f"{doc}{ext}"}
        await dp.feed_update(bot, Update.model_validate({"update_id": counter["n"], "message": msg}, context={"bot": bot}))

    async def press(user_id: int, data: str):
        counter["n"] += 1
        upd = {"update_id": counter["n"], "callback_query": {
            "id": str(counter["n"]), "chat_instance": "c", "data": data,
            "from": {"id": user_id, "is_bot": False, "first_name": f"U{user_id}"},
            "message": {"message_id": 1, "date": 0, "chat": {"id": user_id, "type": "private"}, "text": "x"},
        }}
        await dp.feed_update(bot, Update.model_validate(upd, context={"bot": bot}))

    yield service, session, send, press
    await engine.dispose()


async def test_admin_creates_assignment_and_student_submits(env):
    service, session, send, press = env

    await send(STUDENT, "/start")
    assert "Привет" in session.texts()[-1]

    await send(ADMIN, "/new")
    await send(ADMIN, "Предобработка")
    await send(ADMIN, "-")
    await send(ADMIN, doc="tpl")
    await send(ADMIN, doc="ref")
    await send(ADMIN, "2 2099-01-01 12:00")
    created = session.texts()[-1]
    assert "создано" in created and "1. " in created and "2. " in created

    a = (await service.list_assignments(STUDENT))[0].assignment
    assert a.max_attempts == 2 and a.deadline is not None

    # участник не может пользоваться командами организатора
    n = len(session.requests)
    await send(STUDENT, "/new")
    assert len(session.requests) == n

    # участник просто присылает файл, бот спрашивает задание
    await send(STUDENT, doc="hw")
    assert "К какому заданию" in session.texts()[-1]
    await press(STUDENT, f"subf:{a.id}")
    assert "Принято" in session.texts()[-1]

    subs = await service.list_user_submissions(STUDENT)
    assert len(subs) == 1 and subs[0].status == "queued"
    await service.process(subs[0].id)
    assert (await service.get_submission(subs[0].id)).score == 8

    # шаблон отправляется документом
    await press(STUDENT, f"tpl:{a.id}")
    assert any(isinstance(r, SendDocument) for r in session.requests)


async def test_profile_and_admin_review_commands(env):
    service, session, send, press = env
    await send(STUDENT, "/start")
    assert "Укажи ФИО" in session.texts()[-1]
    await send(STUDENT, "/fio Иванов")
    assert "❌" in session.texts()[-1]
    await send(STUDENT, "/fio Иванов Иван Иванович")
    await send(STUDENT, "/name Кот")
    await send(STUDENT, "/profile")
    assert "Кот" in session.texts()[-1] and "Иванов Иван Иванович" in session.texts()[-1]

    a, _ = await service.create_assignment("ДЗ", "", TEMPLATE, REFERENCE)
    sub = await service.submit(STUDENT, a.id, make_student({14: "x = 1\n"}), "hw.ipynb")
    await service.process(sub.id)

    n = len(session.requests)
    await send(STUDENT, f"/score {sub.id} 1=10")  # не организатор — молчим
    assert len(session.requests) == n

    sent = []

    async def notifier(tg_id, messages):
        sent.append((tg_id, messages))

    service.notifier = notifier
    await send(ADMIN, f"/score {sub.id} 1=10")
    assert "10/20" in session.texts()[-1]
    await send(ADMIN, f"/comment {sub.id} Отлично!")
    assert [tg for tg, _ in sent] == [STUDENT, STUDENT]
    assert "8/20 → 10/20" in sent[0][1][0] and "Отлично!" in sent[1][1][0]

    await send(ADMIN, f"/sub {sub.id}")
    texts = "\n".join(session.texts()[-4:])
    assert "Кот" in texts and "Иванов Иван Иванович" in texts and "Комментарий организатора" in texts


async def test_admin_changes_webapp_url(env):
    service, session, send, press = env
    from aiogram.methods import SetChatMenuButton
    await send(STUDENT, "/webapp https://evil.example")
    assert service.webapp_url == ""
    await send(ADMIN, "/webapp http://insecure")
    assert "❌" in session.texts()[-1]
    await send(ADMIN, "/webapp https://new.pinggy.link/")
    assert service.webapp_url == "https://new.pinggy.link"
    assert any(isinstance(r, SetChatMenuButton) and r.menu_button.web_app.url == "https://new.pinggy.link" for r in session.requests)
    await send(STUDENT, "/start")
    kb = session.requests[-1].reply_markup
    assert kb.inline_keyboard[0][0].web_app.url == "https://new.pinggy.link"


async def test_scheduled_assignment_via_bot(env):
    service, session, send, press = env
    await send(STUDENT, "/start")
    await send(ADMIN, "/new")
    await send(ADMIN, "Позже")
    await send(ADMIN, "-")
    await send(ADMIN, doc="tpl")
    await send(ADMIN, doc="ref")
    await send(ADMIN, "2 2099-01-01 12:00 2098-12-01 10:00")
    assert "01.12.2098 10:00" in session.texts()[-1]
    assert await service.list_assignments(STUDENT) == []
    await service.announce_due()
    assert not any(isinstance(r, SendMessage) and r.chat_id == STUDENT and "Новое задание" in r.text
                   for r in session.requests)


async def test_hackathon_in_bot(env):
    from datetime import timedelta

    from app.hackathons import UploadedFile, hackathons_for

    service, session, send, press = env
    await send(STUDENT, "/hackathon")
    assert "пока нет" in session.texts()[-1]

    hacks = hackathons_for(service)
    now = datetime.now(timezone.utc)
    h = await hacks.create(
        title="Пульс", description="Предскажите target", starts_at=now - timedelta(minutes=1),
        ends_at=now + timedelta(hours=1), metric="accuracy", id_column="id", target_column="target", daily_limit=3,
        train=UploadedFile("train.csv", b"id,target\n1,0\n"), test=UploadedFile("test.csv", b"id\n10\n11\n"),
        solution=UploadedFile("s.csv", b"id,target\n10,0\n11,1\n"),
    )
    await send(STUDENT, "/hackathon")
    assert "Пульс" in session.texts()[-1] and "Идёт" in session.texts()[-1]

    session.files["pred"] = b"id,target\n10,0\n11,0\n"
    await send(STUDENT, doc="pred", ext=".csv")
    assert "Решение №1" in session.texts()[-1] and "0.5" in session.texts()[-1] and "1 из 1" in session.texts()[-1]
    session.files["bad"] = b"id,target\n10,0\n"
    await send(STUDENT, doc="bad", ext=".csv")
    assert "не принято" in session.texts()[-1]

    await press(STUDENT, f"hlb:{h.id}")
    assert "Лидерборд" in session.texts()[-1] and "← ты" in session.texts()[-1]
    await press(STUDENT, f"hmy:{h.id}")
    assert "№2" in session.texts()[-1] and "№1" in session.texts()[-1]
    await press(STUDENT, f"hf:{h.id}:solution")
    assert not any(isinstance(r, SendDocument) for r in session.requests[-1:])
