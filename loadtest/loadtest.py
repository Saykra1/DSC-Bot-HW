"""Нагрузочный тест мини аппа: N участников одновременно открывают мини апп, решают тест, сдают ноутбук
и ждут проверки (опрашивают статус раз в 4 с, как настоящий мини апп).

Работает против ОТДЕЛЬНОГО тестового экземпляра бота (см. loadtest/README.md): скрипт создаёт тестовое
задание и сотни сдач от выдуманных пользователей, поэтому на боевой экземпляр его направлять нельзя.

Пример:
  python loadtest/loadtest.py --url http://127.0.0.1:8080 --users 100 --duration 120 --pid 1234

Зависимости: только стандартная библиотека и aiohttp.
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import hmac
import json
import os
import random
import statistics
import sys
import time
from collections import defaultdict
from urllib.parse import urlencode

import aiohttp

ADMIN_ID = 900_000_001
USER_BASE = 910_000_000

# ---------- подпись initData так же, как это делает Telegram ----------


def init_data(user_id: int, token: str) -> str:
    fields = {
        "auth_date": str(int(time.time())),
        "query_id": f"q{user_id}",
        "user": json.dumps({"id": user_id, "first_name": "Load", "last_name": str(user_id), "username": f"load{user_id}"}),
    }
    check = "\n".join(f"{k}={v}" for k, v in sorted(fields.items()))
    secret = hmac.new(b"WebAppData", token.encode(), hashlib.sha256).digest()
    fields["hash"] = hmac.new(secret, check.encode(), hashlib.sha256).hexdigest()
    return urlencode(fields)


# ---------- тестовое задание ----------


def _cell(kind: str, src: str) -> dict:
    c = {"cell_type": kind, "metadata": {}, "source": src.splitlines(keepends=True)}
    if kind == "code":
        c |= {"outputs": [], "execution_count": None}
    return c


def notebook(cells: list[dict]) -> bytes:
    return json.dumps({"cells": cells, "metadata": {}, "nbformat": 4, "nbformat_minor": 5}, ensure_ascii=False).encode()


TASKS = [
    ("Задание 1. Заполните пропуски в колонке age медианой.", "df['age'] = df['age'].fillna(df['age'].median())"),
    ("Задание 2. Создайте признак bmi = weight / height**2.", "df['bmi'] = df['weight'] / df['height'] ** 2"),
    ("Задание 3. Удалите дубликаты строк.", "df = df.drop_duplicates()"),
]


def template_and_reference() -> tuple[bytes, bytes]:
    head = [_cell("markdown", "# Нагрузочный тест\nТестовое задание, создано loadtest.py"),
            _cell("code", "import pandas as pd\ndf = pd.read_csv('data.csv')")]
    tpl, ref = list(head), list(head)
    for text, solution in TASKS:
        tpl += [_cell("markdown", text), _cell("code", "# ваш код здесь\n")]
        ref += [_cell("markdown", text), _cell("code", solution)]
    return notebook(tpl), notebook(ref)


def student_notebook(user_id: int, attempt: int) -> bytes:
    """Решение «со своим почерком», чтобы кэш оценок не срабатывал и каждая сдача шла в LLM (худший случай)."""
    rnd = random.Random(user_id * 10 + attempt)
    cells = [_cell("markdown", "# Нагрузочный тест"), _cell("code", "import pandas as pd\ndf = pd.read_csv('data.csv')")]
    for i, (text, solution) in enumerate(TASKS, 1):
        k = rnd.randint(1, 10**6)
        code = f"{solution}\ntmp_{i}_{k} = len(df) * {k}"
        cells += [_cell("markdown", text), _cell("code", code)]
    return notebook(cells)


QUIZ = {
    "attempts": 3,
    "questions": [
        {"type": "single", "text": "2 + 2 = ?", "points": 1,
         "options": [{"id": "a", "text": "3"}, {"id": "b", "text": "4"}], "correct": ["b"]},
        {"type": "multiple", "text": "Чётные числа", "points": 2,
         "options": [{"id": "a", "text": "2"}, {"id": "b", "text": "3"}, {"id": "c", "text": "4"}], "correct": ["a", "c"]},
        {"type": "number", "text": "sqrt(2) с точностью 0.01", "points": 1, "value": 1.41, "tolerance": 0.01},
        {"type": "text", "text": "Как называется операция A^T?", "points": 1, "answers": ["транспонирование"]},
    ],
}


def quiz_answers(rnd: random.Random, quiz: dict | None) -> dict:
    """Ответы по id вопросов (id приходят с сервера вместе с тестом)."""
    ids = [q["id"] for q in (quiz or {}).get("questions", [])]
    if len(ids) != 4:
        return {}
    values = [rnd.choice(["a", "b"]), rnd.sample(["a", "b", "c"], 2), str(rnd.choice([1.41, 1.5])),
              rnd.choice(["транспонирование", "не знаю"])]
    return dict(zip(ids, values))


# ---------- статистика ----------


class Stats:
    def __init__(self) -> None:
        self.lat: dict[str, list[float]] = defaultdict(list)
        self.errors: dict[str, dict[str, int]] = defaultdict(lambda: defaultdict(int))
        self.queue_wait: list[float] = []  # от сдачи до результата, с
        self.pending: set[int] = set()
        self.submitted = 0
        self.checked = 0
        self.check_errors = 0
        self.started = time.monotonic()
        self.samples: list[tuple[float, float, float]] = []  # (t, cpu%, rss МБ)

    def add(self, name: str, seconds: float, status: int | str) -> None:
        self.lat[name].append(seconds)
        if status != 200:
            self.errors[name][str(status)] += 1


def pct(values: list[float], p: float) -> float:
    if not values:
        return 0.0
    s = sorted(values)
    return s[min(len(s) - 1, int(round(p / 100 * (len(s) - 1))))]


# ---------- клиент ----------


class Client:
    def __init__(self, base: str, token: str, user_id: int, stats: Stats, timeout: float):
        self.base = base.rstrip("/")
        self.user_id = user_id
        self.stats = stats
        self.headers = {"X-Telegram-Init-Data": init_data(user_id, token)}
        self.session = aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=timeout))

    async def close(self) -> None:
        await self.session.close()

    async def req(self, method: str, path: str, name: str | None = None, **kw):
        name = name or f"{method} {path}"
        t0 = time.monotonic()
        try:
            async with self.session.request(method, self.base + path, headers=self.headers, **kw) as r:
                body = await r.read()
                self.stats.add(name, time.monotonic() - t0, r.status)
                if r.status != 200:
                    return None
                ctype = r.headers.get("content-type", "")
                return json.loads(body) if "json" in ctype else body
        except asyncio.TimeoutError:
            self.stats.add(name, time.monotonic() - t0, "timeout")
        except aiohttp.ClientError as e:
            self.stats.add(name, time.monotonic() - t0, type(e).__name__)
        return None


async def seed(base: str, token: str, stats: Stats, timeout: float) -> int:
    admin = Client(base, token, ADMIN_ID, stats, timeout)
    try:
        tpl, ref = template_and_reference()
        form = aiohttp.FormData()
        form.add_field("title", f"Нагрузочный тест {time.strftime('%H:%M:%S')}")
        form.add_field("description", "Создано loadtest.py")
        form.add_field("max_attempts", "5")
        form.add_field("deadline", "2099-01-01T00:00")
        form.add_field("quiz", json.dumps(QUIZ, ensure_ascii=False))
        form.add_field("template", tpl, filename="template.ipynb", content_type="application/json")
        form.add_field("reference", ref, filename="reference.ipynb", content_type="application/json")
        r = await admin.req("POST", "/api/admin/assignments", "seed", data=form)
        if not r:
            errs = dict(stats.errors.get("seed", {}))
            sys.exit(f"Не удалось создать тестовое задание (ответ: {errs}). ADMIN_IDS тестового экземпляра должен "
                     f"содержать {ADMIN_ID}, а --token совпадать с его BOT_TOKEN.")
        return r["id"]
    finally:
        await admin.close()


async def think(lo: float, hi: float) -> None:
    await asyncio.sleep(random.uniform(lo, hi))


async def open_app(c: Client) -> None:
    """Открытие мини аппа: страница, статика, профиль, главная."""
    await c.req("GET", "/", "GET /")
    await asyncio.gather(*(c.req("GET", f"/static/{f}", "GET /static/*") for f in ("app.css", "app.js", "quiz.js")))
    await c.req("GET", "/api/me")
    await asyncio.gather(c.req("GET", "/api/assignments"), c.req("GET", "/api/submissions"), c.req("GET", "/api/hackathons"))


async def wait_result(c: Client, sid: int, t_submit: float, deadline: float, queue: dict | None) -> None:
    stats = c.stats
    while time.monotonic() < deadline:
        # Как мини апп: раз в 4 с, а если перед тобой больше 10 работ — раз в 15 с
        await asyncio.sleep(15 if (queue or {}).get("ahead", 0) > 10 else 4)
        s, _ = await asyncio.gather(c.req("GET", f"/api/submissions/{sid}", "GET /api/submissions/{id}"),
                                    c.req("GET", "/api/assignments"))
        queue = (s or {}).get("queue")
        if s and s.get("status") in ("done", "error"):
            stats.pending.discard(sid)
            stats.queue_wait.append(time.monotonic() - t_submit)
            if s["status"] == "done":
                stats.checked += 1
            else:
                stats.check_errors += 1
            return


async def user_flow(idx: int, args, aid: int, stats: Stats, start_at: float, spike_at: float | None, stop_at: float,
                    drain_until: float) -> None:
    rnd = random.Random(idx)
    await asyncio.sleep(max(0.0, start_at - time.monotonic()))
    c = Client(args.url, args.token, USER_BASE + idx, stats, args.timeout)
    try:
        await open_app(c)
        await think(1, 4)
        # Тест
        quiz = await c.req("GET", f"/api/assignments/{aid}/quiz", "GET /api/assignments/{id}/quiz")
        await think(3, 10)
        await c.req("POST", f"/api/assignments/{aid}/quiz", "POST /api/assignments/{id}/quiz",
                    json={"answers": quiz_answers(rnd, quiz)})
        await c.req("GET", "/api/submissions")
        # Ноутбук
        wait_task = None
        if rnd.random() < args.submit_share:
            if spike_at is not None:
                await asyncio.sleep(max(0.0, spike_at - time.monotonic()) + rnd.uniform(0, 3))
            else:
                await think(2, 8)
            form = aiohttp.FormData()
            form.add_field("file", student_notebook(c.user_id, 1), filename="hw.ipynb", content_type="application/json")
            t_submit = time.monotonic()
            sub = await c.req("POST", f"/api/assignments/{aid}/submissions", "POST /api/assignments/{id}/submissions", data=form)
            if sub:
                stats.submitted += 1
                stats.pending.add(sub["id"])
                wait_task = asyncio.create_task(wait_result(c, sub["id"], t_submit, drain_until, sub.get("queue")))
        # Дальше просто ходит по мини аппу, пока идёт тест
        pages = [
            lambda: asyncio.gather(c.req("GET", "/api/assignments"), c.req("GET", "/api/submissions"), c.req("GET", "/api/hackathons")),
            lambda: asyncio.gather(c.req("GET", "/api/me"), c.req("GET", "/api/submissions"), c.req("GET", "/api/assignments")),
            lambda: c.req("GET", "/api/hackathons"),
            lambda: c.req("GET", f"/api/assignments/{aid}/quiz", "GET /api/assignments/{id}/quiz"),
        ]
        while time.monotonic() < stop_at:
            await think(args.think_min, args.think_max)
            if time.monotonic() >= stop_at:
                break
            await rnd.choice(pages)()
        if wait_task:
            await wait_task
    finally:
        await c.close()


async def sample_process(pid: int, stats: Stats, stop: asyncio.Event) -> None:
    """CPU и память процесса бота (если он на этой же машине / в этом же контейнере)."""
    tick = os.sysconf("SC_CLK_TCK")

    def read() -> tuple[float, float]:
        with open(f"/proc/{pid}/stat") as f:
            parts = f.read().rsplit(")", 1)[1].split()
        cpu = (int(parts[11]) + int(parts[12])) / tick
        with open(f"/proc/{pid}/status") as f:
            rss = next(int(line.split()[1]) for line in f if line.startswith("VmRSS:")) / 1024
        return cpu, rss

    try:
        prev_cpu, _ = read()
    except OSError:
        print(f"! Нет процесса {pid}, CPU/память не меряю")
        return
    prev_t = time.monotonic()
    while not stop.is_set():
        await asyncio.sleep(1)
        try:
            cpu, rss = read()
        except OSError:
            return
        now = time.monotonic()
        stats.samples.append((now - stats.started, 100 * (cpu - prev_cpu) / (now - prev_t), rss))
        prev_cpu, prev_t = cpu, now


async def progress(stats: Stats, stop: asyncio.Event) -> None:
    while not stop.is_set():
        await asyncio.sleep(10)
        n = sum(len(v) for v in stats.lat.values())
        errs = sum(sum(v.values()) for v in stats.errors.values())
        cpu = f", CPU {stats.samples[-1][1]:.0f}%, RSS {stats.samples[-1][2]:.0f} МБ" if stats.samples else ""
        print(f"  t={time.monotonic() - stats.started:5.0f}s  запросов {n}, ошибок {errs}, сдано {stats.submitted}, "
              f"проверено {stats.checked}, в очереди {len(stats.pending)}{cpu}", flush=True)


def report(stats: Stats, args, wall: float) -> bool:
    print("\n=== Итог ===")
    print(f"Участников: {args.users}, длительность: {wall:.0f} с")
    rows = sorted(stats.lat.items(), key=lambda kv: -len(kv[1]))
    print(f"{'запрос':42} {'кол-во':>7} {'ошибки':>7} {'p50,мс':>8} {'p95,мс':>8} {'p99,мс':>8} {'max,мс':>8}")
    total = errs_total = 0
    all_lat: list[float] = []
    for name, lat in rows:
        errs = sum(stats.errors[name].values())
        total += len(lat)
        errs_total += errs
        all_lat += lat
        print(f"{name[:42]:42} {len(lat):7} {errs:7} {pct(lat, 50) * 1000:8.0f} {pct(lat, 95) * 1000:8.0f} "
              f"{pct(lat, 99) * 1000:8.0f} {max(lat) * 1000:8.0f}")
    for name, e in stats.errors.items():
        if e:
            print(f"  ошибки {name}: {dict(e)}")
    print(f"Всего запросов: {total} ({total / wall:.1f} в секунду), ошибок: {errs_total} "
          f"({100 * errs_total / max(total, 1):.2f}%), p95 по всем: {pct(all_lat, 95) * 1000:.0f} мс")
    print(f"Ноутбуков сдано: {stats.submitted}, проверено: {stats.checked}, ошибок проверки: {stats.check_errors}, "
          f"не дождались: {len(stats.pending)}")
    if stats.queue_wait:
        print(f"Ожидание результата: медиана {statistics.median(stats.queue_wait):.0f} с, "
              f"максимум {max(stats.queue_wait):.0f} с")
    if stats.samples:
        cpus = [s[1] for s in stats.samples]
        print(f"Бот: CPU среднее {statistics.mean(cpus):.0f}%, p95 {pct(cpus, 95):.0f}%, "
              f"память макс {max(s[2] for s in stats.samples):.0f} МБ")
    ok = errs_total / max(total, 1) < 0.01 and pct(all_lat, 95) < args.p95_ms / 1000 and not stats.check_errors
    print("ВЕРДИКТ:", "OK" if ok else "ПЛОХО",
          f"(критерий: ошибок < 1%, p95 < {args.p95_ms:.0f} мс, ни одна проверка не упала)")
    return ok


async def main_async(args) -> bool:
    stats = Stats()
    aid = args.assignment or await seed(args.url, args.token, stats, args.timeout)
    print(f"Тестовое задание #{aid}. Запускаю {args.users} участников...", flush=True)
    stats.lat.pop("seed", None)
    t0 = time.monotonic()
    stats.started = t0
    stop_at = t0 + args.duration
    spike_at = t0 + args.ramp + 15 if args.spike else None
    drain_until = stop_at + args.drain
    stop = asyncio.Event()
    bg = [asyncio.create_task(progress(stats, stop))]
    if args.pid:
        bg.append(asyncio.create_task(sample_process(args.pid, stats, stop)))
    await asyncio.gather(*(
        user_flow(i, args, aid, stats, t0 + args.ramp * i / max(args.users, 1), spike_at, stop_at, drain_until)
        for i in range(args.users)
    ))
    stop.set()
    for t in bg:
        t.cancel()
    return report(stats, args, time.monotonic() - t0)


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--url", default="http://127.0.0.1:8080", help="адрес ТЕСТОВОГО экземпляра бота")
    p.add_argument("--token", default="", help="BOT_TOKEN тестового экземпляра (для подписи initData)")
    p.add_argument("--users", type=int, default=100)
    p.add_argument("--duration", type=float, default=120, help="сколько секунд участники ходят по мини аппу")
    p.add_argument("--ramp", type=float, default=30, help="за сколько секунд подключаются все участники")
    p.add_argument("--think-min", type=float, default=2)
    p.add_argument("--think-max", type=float, default=6)
    p.add_argument("--submit-share", type=float, default=1.0, help="доля участников, которые сдают ноутбук")
    p.add_argument("--spike", action="store_true", help="все сдают ноутбук почти одновременно (как перед дедлайном)")
    p.add_argument("--drain", type=float, default=0, help="сколько ещё ждать проверки после окончания, с")
    p.add_argument("--timeout", type=float, default=30, help="таймаут одного запроса, с")
    p.add_argument("--p95-ms", type=float, default=1000)
    p.add_argument("--assignment", type=int, default=0, help="использовать существующее задание вместо создания")
    p.add_argument("--pid", type=int, default=0, help="PID процесса бота для замера CPU и памяти")
    args = p.parse_args()
    ok = asyncio.run(main_async(args))
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
