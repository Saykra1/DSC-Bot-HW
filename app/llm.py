"""Клиент LLM через OpenAI-совместимый API.

Подходит для OpenRouter, DeepSeek, NVIDIA (build.nvidia.com), Gemini (OpenAI-compatible endpoint),
локальной Ollama и т.п. Провайдер задаётся через LLM_BASE_URL / LLM_API_KEY / LLM_MODEL.
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
import time
from collections import deque

import openai
from openai import AsyncOpenAI

from app.config import Settings

log = logging.getLogger(__name__)


class LLMError(RuntimeError):
    pass


class LLMRateLimited(LLMError):
    """Провайдер ответил 429. wait — через сколько секунд пробовать снова, daily — кончился дневной лимит."""

    def __init__(self, message: str, wait: float, daily: bool = False):
        super().__init__(message)
        self.wait = wait
        self.daily = daily


class RateLimiter:
    """Не больше rpm запросов за скользящую минуту на весь процесс (общий для всех воркеров проверки).

    У бесплатных моделей OpenRouter лимит 20 запросов в минуту на аккаунт: если его превысить, OpenRouter
    отвечает 429 без Retry-After, клиент быстро исчерпывает повторы и проверка падает с ошибкой.
    Поэтому лучше самим не превышать лимит, а лишние запросы подождут своей очереди.
    """

    def __init__(self, rpm: int):
        self.rpm = rpm
        self._times: deque[float] = deque()
        self._lock = asyncio.Lock()
        self._blocked_until = 0.0

    def block(self, seconds: float) -> None:
        """После 429 не отправлять новые запросы ещё seconds секунд."""
        self._blocked_until = max(self._blocked_until, time.monotonic() + seconds)

    async def acquire(self) -> None:
        async with self._lock:
            while True:
                now = time.monotonic()
                if now < self._blocked_until:
                    await asyncio.sleep(self._blocked_until - now)
                    continue
                if self.rpm <= 0:
                    return
                while self._times and now - self._times[0] >= 60:
                    self._times.popleft()
                if len(self._times) < self.rpm:
                    self._times.append(now)
                    return
                await asyncio.sleep(60 - (now - self._times[0]) + 0.05)


def _rate_limit_info(e: openai.RateLimitError) -> tuple[float, bool]:
    """Сколько ждать после 429 и не дневной ли это лимит (по заголовкам и тексту ошибки OpenRouter)."""
    headers = getattr(getattr(e, "response", None), "headers", None) or {}
    text = str(e).lower()
    daily = "per-day" in text or "per day" in text or "daily" in text
    wait = None
    try:
        if headers.get("retry-after"):
            wait = float(headers["retry-after"])
        elif headers.get("x-ratelimit-reset"):
            reset = float(headers["x-ratelimit-reset"])
            reset = reset / 1000 if reset > 1e11 else reset  # OpenRouter отдаёт миллисекунды
            wait = reset - time.time()
    except ValueError:
        wait = None
    if wait is not None and wait > 3600:
        daily = True
    return min(max(wait if wait is not None else 20.0, 3.0), 120.0), daily


class LLMClient:
    """LLM_MODEL может содержать несколько моделей через запятую: если первая недоступна
    (убрали из бесплатных, кончился лимит, ошибка сервера), берётся следующая."""

    def __init__(self, settings: Settings):
        self.settings = settings
        self._client = AsyncOpenAI(
            base_url=settings.llm_base_url,
            api_key=settings.llm_api_key or "not-needed",
            timeout=settings.llm_timeout,
            # Повторы делаем сами в _create: так каждый повтор проходит через ограничитель запросов,
            # а 429 не «сжигает» лимит быстрыми повторами клиента.
            max_retries=0,
        )
        self.models = [m.strip() for m in settings.llm_model.split(",") if m.strip()]
        self._json_mode = {m: settings.llm_json_mode for m in self.models}
        self.last_model: str | None = None
        self.limiter = RateLimiter(settings.llm_rpm_limit)

    async def complete_json(self, system: str, user: str) -> dict:
        """Просит модель ответить JSON-объектом. Одна повторная попытка при невалидном JSON."""
        messages = [{"role": "system", "content": system}, {"role": "user", "content": user}]
        last_text = ""
        for attempt in range(2):
            text = await self._chat(messages)
            last_text = text
            try:
                return parse_json_object(text)
            except ValueError:
                log.warning("LLM вернула невалидный JSON (попытка %s): %.300s", attempt + 1, text)
                messages = messages + [
                    {"role": "assistant", "content": text},
                    {"role": "user", "content": "Ответ не является валидным JSON. Пришли только JSON-объект по схеме, без пояснений."},
                ]
        raise LLMError(f"Модель не вернула валидный JSON: {last_text[:300]}")

    async def _chat(self, messages: list[dict]) -> str:
        started = time.monotonic()
        while True:
            # Начинаем с модели, которая сработала в прошлый раз
            order = sorted(self.models, key=lambda m: m != self.last_model)
            errors = []
            waits = []
            for model in order:
                try:
                    text = await self._chat_one(model, messages)
                except LLMRateLimited as e:
                    if e.daily:
                        log.warning("Модель %s: закончился дневной лимит запросов", model)
                        errors.append(f"{model}: дневной лимит запросов исчерпан ({e})")
                    else:
                        log.warning("Модель %s: лимит запросов в минуту, жду %.0f с", model, e.wait)
                        errors.append(f"{model}: {e}")
                        waits.append(e.wait)
                    continue
                except LLMError as e:
                    log.warning("Модель %s не ответила: %s", model, e)
                    errors.append(f"{model}: {e}")
                    continue
                if model != self.last_model:
                    log.info("Проверяет модель %s", model)
                self.last_model = model
                return text
            # Все модели упёрлись в минутный лимит — это временно: ждём и пробуем снова, а не валим проверку
            if waits and time.monotonic() - started + min(waits) <= self.settings.llm_rate_wait:
                self.limiter.block(min(waits))
                await asyncio.sleep(min(waits))
                continue
            raise LLMError("Ни одна модель не ответила. " + " | ".join(errors))

    async def _create(self, **kwargs):
        retries = max(self.settings.llm_max_retries, 0)
        for attempt in range(retries + 1):
            await self.limiter.acquire()
            try:
                return await self._client.chat.completions.create(**kwargs)
            except openai.RateLimitError as e:
                wait, daily = _rate_limit_info(e)
                if not daily:
                    self.limiter.block(wait)
                raise LLMRateLimited(f"Лимит запросов: {e}", wait, daily) from e
            except (openai.APIConnectionError, openai.InternalServerError) as e:
                # Сеть и 5xx — временные: повторяем с паузой 1, 2, 4, 8... с
                if attempt >= retries:
                    raise
                log.warning("Сбой запроса к модели (%s), повтор через %s с", type(e).__name__, 2 ** attempt)
                await asyncio.sleep(2 ** attempt)
        raise AssertionError("unreachable")

    async def _chat_one(self, model: str, messages: list[dict]) -> str:
        kwargs = dict(model=model, messages=messages, temperature=self.settings.llm_temperature)
        if self._json_mode.get(model):
            kwargs["response_format"] = {"type": "json_object"}
        try:
            resp = await self._create(**kwargs)
        except openai.BadRequestError as e:
            # Не все модели умеют response_format: пробуем без него и запоминаем.
            msg = str(e).lower()
            if self._json_mode.get(model) and ("response_format" in msg or "json" in msg):
                log.warning("Модель %s не поддерживает response_format, выключаю JSON-режим", model)
                self._json_mode[model] = False
                kwargs.pop("response_format", None)
                try:
                    resp = await self._create(**kwargs)
                except openai.APIError as e2:
                    raise LLMError(f"Ошибка запроса к модели: {e2}") from e2
            else:
                raise LLMError(f"Ошибка запроса к модели: {e}") from e
        except openai.APIError as e:
            raise LLMError(f"Ошибка API модели: {e}") from e
        if not resp.choices:
            raise LLMError("Модель вернула пустой ответ")
        content = resp.choices[0].message.content or ""
        if not content.strip():
            raise LLMError("Модель вернула пустой ответ")
        return content


_FENCE = re.compile(r"```(?:json)?\s*(.*?)```", re.S)
_THINK = re.compile(r"<think>.*?</think>", re.S)


def parse_json_object(text: str) -> dict:
    """Достаёт JSON-объект из ответа модели (с учётом ```json```-блоков и <think> у reasoning-моделей)."""
    text = _THINK.sub("", text).strip()
    candidates = [text]
    m = _FENCE.search(text)
    if m:
        candidates.append(m.group(1).strip())
    start, end = text.find("{"), text.rfind("}")
    if start != -1 and end > start:
        candidates.append(text[start : end + 1])
    for c in candidates:
        try:
            obj = json.loads(c)
        except json.JSONDecodeError:
            continue
        if isinstance(obj, dict):
            return obj
    raise ValueError("no JSON object found")
