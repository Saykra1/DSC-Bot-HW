"""Клиент LLM через OpenAI-совместимый API.

Подходит для OpenRouter, DeepSeek, NVIDIA (build.nvidia.com), Gemini (OpenAI-compatible endpoint),
локальной Ollama и т.п. Провайдер задаётся через LLM_BASE_URL / LLM_API_KEY / LLM_MODEL.
"""

from __future__ import annotations

import json
import logging
import re

import openai
from openai import AsyncOpenAI

from app.config import Settings

log = logging.getLogger(__name__)


class LLMError(RuntimeError):
    pass


class LLMClient:
    """LLM_MODEL может содержать несколько моделей через запятую: если первая недоступна
    (убрали из бесплатных, кончился лимит, ошибка сервера), берётся следующая."""

    def __init__(self, settings: Settings):
        self.settings = settings
        self._client = AsyncOpenAI(
            base_url=settings.llm_base_url,
            api_key=settings.llm_api_key or "not-needed",
            timeout=settings.llm_timeout,
            max_retries=settings.llm_max_retries,
        )
        self.models = [m.strip() for m in settings.llm_model.split(",") if m.strip()]
        self._json_mode = {m: settings.llm_json_mode for m in self.models}
        self.last_model: str | None = None

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
        # Начинаем с модели, которая сработала в прошлый раз
        order = sorted(self.models, key=lambda m: m != self.last_model)
        errors = []
        for model in order:
            try:
                text = await self._chat_one(model, messages)
            except LLMError as e:
                log.warning("Модель %s не ответила: %s", model, e)
                errors.append(f"{model}: {e}")
                continue
            if model != self.last_model:
                log.info("Проверяет модель %s", model)
            self.last_model = model
            return text
        raise LLMError("Ни одна модель не ответила. " + " | ".join(errors))

    async def _chat_one(self, model: str, messages: list[dict]) -> str:
        kwargs = dict(model=model, messages=messages, temperature=self.settings.llm_temperature)
        if self._json_mode.get(model):
            kwargs["response_format"] = {"type": "json_object"}
        try:
            resp = await self._client.chat.completions.create(**kwargs)
        except openai.BadRequestError as e:
            # Не все модели умеют response_format: пробуем без него и запоминаем.
            msg = str(e).lower()
            if self._json_mode.get(model) and ("response_format" in msg or "json" in msg):
                log.warning("Модель %s не поддерживает response_format, выключаю JSON-режим", model)
                self._json_mode[model] = False
                kwargs.pop("response_format", None)
                try:
                    resp = await self._client.chat.completions.create(**kwargs)
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
