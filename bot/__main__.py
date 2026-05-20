"""Entrypoint: ``python -m bot``."""

from __future__ import annotations

import asyncio
import logging

from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import BotCommand, BotCommandScopeDefault

from bot import db
from bot.config import get_settings
from bot.handlers import autoreply, business, messages, settings, start

log = logging.getLogger(__name__)


def _setup_logging(level_name: str) -> None:
    level = getattr(logging, level_name.upper(), logging.INFO)
    logging.basicConfig(
        level=level,
        format="%(asctime)s · %(levelname)-7s · %(name)s · %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )
    # aiogram itself is noisy at DEBUG — bump it down unless the user opted in
    if level > logging.DEBUG:
        logging.getLogger("aiogram.event").setLevel(logging.WARNING)
        logging.getLogger("aiogram.dispatcher").setLevel(logging.WARNING)


ALLOWED_UPDATES = [
    "message",
    "edited_message",
    "callback_query",
    "business_connection",
    "business_message",
    "edited_business_message",
    "deleted_business_messages",
]


COMMANDS = [
    BotCommand(command="start", description="приветствие и инструкция"),
    BotCommand(command="help", description="подробная справка"),
    BotCommand(command="status", description="состояние подключения"),
    BotCommand(command="settings", description="настройки фич"),
    BotCommand(command="autoreply", description="автоответчик"),
    BotCommand(command="cancel", description="отменить ввод"),
]


async def _set_commands(bot: Bot) -> None:
    try:
        await bot.set_my_commands(COMMANDS, scope=BotCommandScopeDefault())
    except Exception as exc:  # noqa: BLE001
        log.warning("set_my_commands failed: %s", exc)


async def main() -> None:
    cfg = get_settings()
    _setup_logging(cfg.log_level)
    log.info("starting bot")

    await db.init_db()

    bot = Bot(
        token=cfg.bot_token,
        default=DefaultBotProperties(parse_mode=ParseMode.HTML),
    )
    dp = Dispatcher(storage=MemoryStorage())

    dp.include_routers(
        start.router,
        settings.router,
        autoreply.router,
        business.router,
        messages.router,
    )

    me = await bot.get_me()
    log.info("authorized as @%s (id=%s)", me.username, me.id)

    await _set_commands(bot)
    await bot.delete_webhook(drop_pending_updates=True)
    try:
        await dp.start_polling(bot, allowed_updates=ALLOWED_UPDATES)
    finally:
        await bot.session.close()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except (KeyboardInterrupt, SystemExit):
        pass
