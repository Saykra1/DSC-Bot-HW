"""Настройки приложения. Всё читается из переменных окружения или файла .env."""

from functools import lru_cache
from pathlib import Path
from zoneinfo import ZoneInfo

from pydantic import field_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict
from typing import Annotated

DEFAULT_TASK_MARKERS = [
    "пишите ваш код",
    "ваш код здесь",
    "your code here",
    "напишите ваш код",
    "допишите код",
]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    # Telegram
    bot_token: str = ""
    # Telegram ID организаторов через запятую: 123,456
    admin_ids: Annotated[list[int], NoDecode] = []
    # Прокси для api.telegram.org, если он недоступен напрямую: http://host:port или socks5://host:port
    bot_proxy: str = ""
    # HTTPS-адрес, по которому открывается мини апп (например, адрес туннеля cloudflared)
    webapp_url: str = ""

    # LLM: любой OpenAI-совместимый API (OpenRouter, DeepSeek, NVIDIA, Gemini, Ollama...)
    llm_base_url: str = "https://openrouter.ai/api/v1"
    llm_api_key: str = ""
    llm_model: str = ""
    # Просить у модели ответ в режиме JSON (response_format). Если модель не умеет, выключите.
    llm_json_mode: bool = True
    llm_temperature: float = 0.2
    llm_timeout: float = 180.0
    llm_max_retries: int = 4
    # Сколько проверок идёт параллельно (у бесплатных моделей жёсткие лимиты)
    check_concurrency: int = 1

    # Хранилище
    data_dir: Path = Path("data")
    database_url: str = ""  # по умолчанию sqlite в data_dir

    # Веб-сервер мини аппа
    host: str = "0.0.0.0"
    port: int = 8080
    # Только для локальной отладки мини аппа в обычном браузере: подставляет этого пользователя
    dev_user_id: int | None = None

    # Правила сдачи
    default_max_attempts: int = 3
    max_upload_mb: int = 10
    timezone: str = "Europe/Moscow"
    # Подстроки (без учёта регистра), по которым в шаблоне ищутся ячейки-задания.
    # Плюс любая ячейка, где есть строка, состоящая только из "...".
    task_markers: Annotated[list[str], NoDecode] = DEFAULT_TASK_MARKERS

    @field_validator("admin_ids", mode="before")
    @classmethod
    def _split_ids(cls, v):
        if isinstance(v, str):
            ids = []
            for x in v.replace(";", ",").split(","):
                x = x.strip()
                if not x:
                    continue
                if not x.lstrip("-").isdigit():
                    raise ValueError(
                        f"«{x}» не похоже на Telegram ID. Нужны числа через запятую, "
                        "а не username. Свой ID можно узнать у @userinfobot."
                    )
                ids.append(int(x))
            return ids
        return v

    @field_validator("task_markers", mode="before")
    @classmethod
    def _split_markers(cls, v):
        if isinstance(v, str):
            return [x.strip().lower() for x in v.split("|") if x.strip()]
        return v

    @field_validator("dev_user_id", mode="before")
    @classmethod
    def _empty_none(cls, v):
        return None if v == "" else v

    @property
    def db_url(self) -> str:
        if self.database_url:
            return self.database_url
        return f"sqlite+aiosqlite:///{(self.data_dir / 'bot.db').as_posix()}"

    @property
    def tz(self) -> ZoneInfo:
        return ZoneInfo(self.timezone)

    @property
    def llm_enabled(self) -> bool:
        local = "localhost" in self.llm_base_url or "127.0.0.1" in self.llm_base_url
        return bool(self.llm_model) and (bool(self.llm_api_key) or local)

    def is_admin(self, tg_id: int) -> bool:
        return tg_id in self.admin_ids


@lru_cache
def get_settings() -> Settings:
    return Settings()
