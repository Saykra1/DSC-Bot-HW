"""Защита от перегрузки: лимит запросов к LLM, ожидание 429, WAL в SQLite, позиция в очереди."""

import time

import httpx
import openai
import pytest
from sqlalchemy import text

from app.config import Settings
from app.db import init_db, make_engine, make_sessionmaker
from app.llm import LLMClient, LLMRateLimited, RateLimiter, _rate_limit_info
from app.service import HomeworkService
from tests.test_checker import REFERENCE, TEMPLATE, FakeLLM, make_student


def rate_limit_error(headers: dict, message: str = "Rate limit exceeded: free-models-per-min") -> openai.RateLimitError:
    req = httpx.Request("POST", "https://openrouter.ai/api/v1/chat/completions")
    resp = httpx.Response(429, headers=headers, request=req, json={"error": {"code": 429, "message": message}})
    return openai.RateLimitError(message, response=resp, body=None)


def test_auto_rpm_for_free_models():
    assert Settings(_env_file=None, llm_model="a:free,b:free").llm_rpm_limit == 18
    assert Settings(_env_file=None, llm_model="deepseek-chat").llm_rpm_limit == 0
    assert Settings(_env_file=None, llm_model="a:free", llm_rpm=-1).llm_rpm_limit == 0
    assert Settings(_env_file=None, llm_model="x", llm_rpm=5).llm_rpm_limit == 5


def test_rate_limit_info_reads_openrouter_reset_header():
    reset_ms = int((time.time() + 30) * 1000)
    wait, daily = _rate_limit_info(rate_limit_error({"X-RateLimit-Reset": str(reset_ms)}))
    assert 25 <= wait <= 31 and not daily
    wait, daily = _rate_limit_info(rate_limit_error({"Retry-After": "7"}))
    assert wait == 7 and not daily
    _, daily = _rate_limit_info(rate_limit_error({}, "Rate limit exceeded: free-models-per-day"))
    assert daily


async def test_limiter_spaces_requests():
    lim = RateLimiter(2)
    t0 = time.monotonic()
    await lim.acquire()
    await lim.acquire()
    assert time.monotonic() - t0 < 0.1
    lim._times[0] -= 59.8  # первый запрос «был» почти минуту назад
    await lim.acquire()
    assert 0.1 <= time.monotonic() - t0 < 1.5


async def test_chat_waits_out_minute_limit_instead_of_failing(monkeypatch):
    llm = LLMClient(Settings(_env_file=None, llm_model="a:free,b:free", llm_api_key="k", llm_rate_wait=60))
    calls = []

    async def fake_chat_one(model, messages):
        calls.append(model)
        if len(calls) <= 2:  # обе модели упёрлись в лимит в первом круге
            raise LLMRateLimited("429", wait=0.05)
        return '{"ok": true}'

    monkeypatch.setattr(llm, "_chat_one", fake_chat_one)
    assert await llm.complete_json("s", "u") == {"ok": True}
    assert len(calls) == 3


async def test_chat_gives_up_on_daily_limit(monkeypatch):
    llm = LLMClient(Settings(_env_file=None, llm_model="a:free", llm_api_key="k"))

    async def fake_chat_one(model, messages):
        raise LLMRateLimited("429", wait=5, daily=True)

    monkeypatch.setattr(llm, "_chat_one", fake_chat_one)
    with pytest.raises(Exception, match="дневной лимит"):
        await llm.complete_json("s", "u")


async def test_sqlite_uses_wal(tmp_path):
    engine = make_engine(f"sqlite+aiosqlite:///{tmp_path / 'x.db'}")
    async with engine.connect() as conn:
        assert (await conn.execute(text("PRAGMA journal_mode"))).scalar() == "wal"
        assert (await conn.execute(text("PRAGMA busy_timeout"))).scalar() == 15000
    await engine.dispose()


async def test_queue_info(tmp_path):
    settings = Settings(_env_file=None, data_dir=tmp_path, admin_ids=[1], check_concurrency=2)
    engine = make_engine(settings.db_url)
    await init_db(engine)
    service = HomeworkService(settings, make_sessionmaker(engine), FakeLLM())  # воркеры не запущены
    a, _ = await service.create_assignment("A", "", TEMPLATE, REFERENCE)
    subs = []
    for uid in (10, 11, 12):
        await service.upsert_user(uid, None, "U")
        subs.append(await service.submit(uid, a.id, make_student({14: "x = 1"}), "hw.ipynb"))
    assert await service.queue_info(subs[0].id) == {"ahead": 0, "eta_seconds": None}
    service._check_seconds.extend([30, 30])
    q = await service.queue_info(subs[2].id)
    assert q == {"ahead": 2, "eta_seconds": 45}  # 3 работы × 30 с / 2 воркера
    await service.process(subs[0].id)
    assert await service.queue_info(subs[0].id) is None
    await engine.dispose()
