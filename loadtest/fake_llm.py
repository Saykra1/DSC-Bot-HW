"""Фейковый OpenAI-совместимый сервер для нагрузочного теста: ведёт себя как бесплатная модель OpenRouter.

- отвечает с задержкой (по умолчанию 20–40 с, как типичная бесплатная модель на ноутбуке из 2–5 заданий);
- держит лимит запросов в минуту (по умолчанию 20 — лимит бесплатных моделей OpenRouter) и сверх него отдаёт 429
  так же, как OpenRouter: с заголовками X-RateLimit-*, но без Retry-After;
- возвращает валидный JSON в формате, который ждёт app/checker.py.

Запуск: python loadtest/fake_llm.py --port 9999 [--latency 20-40] [--rpm 20]
Только стандартная библиотека + aiohttp (он уже есть в образе бота как зависимость aiogram).
"""

from __future__ import annotations

import argparse
import asyncio
import json
import random
import re
import time
from collections import deque

from aiohttp import web

TASK_RE = re.compile(r"=== Задание (\d+):")


def make_app(lat_min: float, lat_max: float, rpm: int, retry_after: bool = False) -> web.Application:
    window: deque[float] = deque()
    stats = {"ok": 0, "rate_limited": 0, "in_flight": 0, "max_in_flight": 0}

    async def completions(request: web.Request) -> web.Response:
        body = await request.json()
        now = time.monotonic()
        while window and now - window[0] > 60:
            window.popleft()
        if rpm and len(window) >= rpm:
            stats["rate_limited"] += 1
            reset_ms = int((time.time() + 60 - (now - window[0])) * 1000)
            # Как OpenRouter на своих лимитах для :free: заголовки X-RateLimit-*, без Retry-After
            headers = {"X-RateLimit-Limit": str(rpm), "X-RateLimit-Remaining": "0", "X-RateLimit-Reset": str(reset_ms)}
            if retry_after:
                headers["Retry-After"] = str(max(1, int(60 - (now - window[0])) + 1))
            return web.json_response(
                {"error": {"code": 429, "message": "Rate limit exceeded: free-models-per-min",
                           "metadata": {"error_type": "rate_limit_exceeded"}}},
                status=429, headers=headers,
            )
        window.append(now)
        stats["in_flight"] += 1
        stats["max_in_flight"] = max(stats["max_in_flight"], stats["in_flight"])
        try:
            await asyncio.sleep(random.uniform(lat_min, lat_max))
        finally:
            stats["in_flight"] -= 1
        user = next((m["content"] for m in body.get("messages", []) if m.get("role") == "user"), "")
        tasks = []
        for n in TASK_RE.findall(user):
            issues = random.choice([[], [], [{"severity": "minor", "text": "Можно короче"}],
                                    [{"severity": "major", "text": "Сделано только для train"}]])
            tasks.append({"number": int(n), "attempted": True, "issues": issues,
                          "feedback": "Нагрузочный тест: отзыв модели.", "improvements": []})
        content = json.dumps({"tasks": tasks, "summary": "Нагрузочный тест."}, ensure_ascii=False)
        stats["ok"] += 1
        return web.json_response({
            "id": f"fake-{time.time_ns()}", "object": "chat.completion", "created": int(time.time()),
            "model": body.get("model", "fake"),
            "choices": [{"index": 0, "finish_reason": "stop", "message": {"role": "assistant", "content": content}}],
            "usage": {"prompt_tokens": 1000, "completion_tokens": 200, "total_tokens": 1200},
        })

    async def get_stats(request: web.Request) -> web.Response:
        return web.json_response(stats)

    app = web.Application(client_max_size=20 * 1024 * 1024)
    app.router.add_post("/v1/chat/completions", completions)
    app.router.add_post("/chat/completions", completions)
    app.router.add_get("/stats", get_stats)
    return app


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--host", default="127.0.0.1")
    p.add_argument("--port", type=int, default=9999)
    p.add_argument("--latency", default="20-40", help="задержка ответа в секундах: '30' или '20-40'")
    p.add_argument("--rpm", type=int, default=20, help="лимит запросов в минуту (0 — без лимита)")
    p.add_argument("--retry-after", action="store_true", help="добавлять Retry-After в ответ 429 (OpenRouter не добавляет)")
    a = p.parse_args()
    lo, _, hi = a.latency.partition("-")
    web.run_app(make_app(float(lo), float(hi or lo), a.rpm, a.retry_after), host=a.host, port=a.port, print=None)


if __name__ == "__main__":
    main()
