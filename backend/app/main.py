"""Точка входу: одночасно бот (polling) + монітор + API (uvicorn)."""
import asyncio
import logging
import sys

import uvicorn
from aiogram import Bot, Dispatcher
from aiogram.fsm.storage.memory import MemoryStorage

from . import config, storage
from .api import create_app
from .bot import setup_handlers
from .monitor import UZMonitor

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)],
)
log = logging.getLogger(__name__)


async def main():
    problems = config.validate()
    if problems:
        for p in problems:
            log.error("Конфіг: %s", p)
        log.error("Заповніть .env і перезапустіть.")
        return

    storage.init()

    bot = Bot(token=config.TELEGRAM_BOT_TOKEN)
    dp = Dispatcher(storage=MemoryStorage())
    monitor = UZMonitor(bot)
    setup_handlers(dp)

    # Діагностика: показуємо, який саме бот відповідає цьому токену.
    try:
        me = await bot.get_me()
        log.info("Токен належить боту: @%s (id=%s)", me.username, me.id)
    except Exception as e:
        log.error("Невірний TELEGRAM_BOT_TOKEN? get_me впав: %s", e)

    app = create_app(monitor)
    server = uvicorn.Server(uvicorn.Config(
        app, host=config.HOST, port=config.PORT, log_level="info",
    ))

    monitor_task = asyncio.create_task(monitor.start())
    api_task = asyncio.create_task(server.serve())
    log.info("Бот + API + монітор запущено. Mini App: %s", config.WEBAPP_URL or "—")

    try:
        await dp.start_polling(bot, allowed_updates=["message"])
    finally:
        monitor_task.cancel()
        server.should_exit = True
        await asyncio.gather(api_task, return_exceptions=True)
        await monitor.close()
        await bot.session.close()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        log.info("Зупинено.")
