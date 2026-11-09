import asyncio
from pathlib import Path

from app.config import Settings
from app.db import init_db, make_engine, make_sessionmaker
from app.service import HomeworkService


def test_concurrent_first_contact_creates_one_user(tmp_path: Path):
    """/start в боте и открытие мини аппа одновременно не должны падать на UNIQUE users.tg_id."""

    async def run():
        settings = Settings(_env_file=None, data_dir=tmp_path)
        engine = make_engine(settings.db_url)
        await init_db(engine)
        service = HomeworkService(settings, make_sessionmaker(engine), None)
        users = await asyncio.gather(*(service.upsert_user(42, "nick", "Name") for _ in range(5)))
        await engine.dispose()
        return users

    users = asyncio.run(run())
    assert {u.tg_id for u in users} == {42}
