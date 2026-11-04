"""Точка входа: один процесс запускает бота (polling), очередь проверки и веб-сервер мини аппа."""

from __future__ import annotations

import asyncio
import contextlib
import logging
from contextlib import asynccontextmanager

import uvicorn
from aiogram.exceptions import TelegramNetworkError, TelegramUnauthorizedError
from pydantic import ValidationError

from app.bot import build_bot, build_dispatcher, make_notifier, setup_bot_ui
from app.config import get_settings
from app.db import init_db, make_engine, make_sessionmaker
from app.llm import LLMClient
from app.service import HomeworkService
from app.web import build_app

log = logging.getLogger("app")


async def run_bot(bot, dp, service: HomeworkService) -> None:
    """Подключается к Telegram и держит polling. При обрыве связи повторяет попытки, а не умирает молча."""
    delay = 5
    while True:
        try:
            me = await bot.get_me()
            log.info("Бот @%s подключён к Telegram", me.username)
            try:
                await setup_bot_ui(bot, service.webapp_url)
            except Exception:  # noqa: BLE001
                log.exception("Не удалось настроить меню бота")
            await dp.start_polling(bot, handle_signals=False)
            return  # штатная остановка
        except TelegramUnauthorizedError:
            log.error("BOT_TOKEN неверный: Telegram его не принимает. Проверь токен в @BotFather.")
            return
        except asyncio.CancelledError:
            raise
        except TelegramNetworkError as e:
            log.error(
                "Нет связи с api.telegram.org (%s). Похоже, Telegram API недоступен из этой сети: попробуй другую "
                "сеть (например, раздачу с телефона), VPN или BOT_PROXY в .env. Повтор через %s с.", e, delay,
            )
        except Exception:  # noqa: BLE001
            log.exception("Бот упал, перезапуск через %s с", delay)
        await asyncio.sleep(delay)
        delay = min(delay * 2, 60)


def create_app():
    try:
        settings = get_settings()
    except ValidationError as e:
        problems = "\n".join(f"  {'.'.join(map(str, err['loc'])).upper()}: {err['msg'].removeprefix('Value error, ')}" for err in e.errors())
        raise SystemExit(f"Ошибка в настройках (.env):\n{problems}") from None
    settings.data_dir.mkdir(parents=True, exist_ok=True)
    engine = make_engine(settings.db_url)
    sm = make_sessionmaker(engine)

    if settings.llm_enabled:
        llm = LLMClient(settings)
        log.info("LLM: %s @ %s", settings.llm_model, settings.llm_base_url)
    else:
        llm = None
        log.warning("LLM не настроена (LLM_MODEL / LLM_API_KEY). Проверка работ будет падать с ошибкой.")

    service = HomeworkService(settings, sm, llm)
    bot = build_bot(settings.bot_token, settings.bot_proxy) if settings.bot_token else None
    dp = build_dispatcher(service, settings.webapp_url)
    if bot is None:
        log.warning("BOT_TOKEN не задан: запускаю только веб-часть.")
    if settings.dev_user_id:
        log.warning("DEV_USER_ID=%s: запросы без подписи Telegram идут от этого пользователя. Только для отладки!",
                    settings.dev_user_id)
    if not settings.admin_ids:
        log.warning("ADMIN_IDS не заданы: создавать задания будет некому.")

    @asynccontextmanager
    async def lifespan(app):
        await init_db(engine)
        await service.start()
        polling = None
        if bot is not None:
            service.notifier = make_notifier(bot)
            polling = asyncio.create_task(run_bot(bot, dp, service))
        try:
            yield
        finally:
            if polling is not None:
                with contextlib.suppress(Exception):
                    await dp.stop_polling()
                polling.cancel()
                with contextlib.suppress(asyncio.CancelledError, Exception):
                    await polling
                await bot.session.close()
            await service.stop()
            await engine.dispose()

    return build_app(service, bot, lifespan), settings


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    app, settings = create_app()
    uvicorn.run(app, host=settings.host, port=settings.port, log_level="info")


if __name__ == "__main__":
    main()
