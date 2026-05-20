"""Business connection lifecycle (add / remove / permission changes)."""

from __future__ import annotations

import logging
from typing import Any

from aiogram import Bot, Router
from aiogram.types import BusinessConnection

from bot import db, ui

router = Router(name="business_connection")

log = logging.getLogger(__name__)


def extract_can_reply(conn: BusinessConnection) -> bool:
    """``can_reply`` lives on ``rights`` in newer API versions and on the
    object itself in older ones — try both."""

    rights: Any = getattr(conn, "rights", None)
    if rights is not None:
        return bool(getattr(rights, "can_reply", False))
    return bool(getattr(conn, "can_reply", False))


def _format_connected(conn: BusinessConnection, can_reply: bool) -> str:
    return "\n".join(
        [
            ui.header("Бот подключён", "✅"),
            ui.kv("аккаунт", conn.user.full_name),
            ui.kv_code("user_id", conn.user.id),
            ui.kv_code("connection_id", conn.id),
            ui.kv("право отвечать", "да" if can_reply else "нет"),
            "",
            f"{ui.i('Откройте ')}{ui.code('/settings')}"
            f"{ui.i(' — настроить захват медиа и трекинг.')}",
            f"{ui.i('Откройте ')}{ui.code('/autoreply')}"
            f"{ui.i(' — добавить шаблоны автоответа.')}",
        ]
    )


def _format_disabled() -> str:
    return "\n".join(
        [
            ui.header("Бот отключён", "🚫"),
            ui.i(
                "Вы отключили меня в настройках Telegram Business. "
                "Я больше не вижу новые сообщения."
            ),
            "",
            f"{ui.i('Включить обратно: ')}{ui.b('Настройки → Бизнес → Чат-боты')}",
        ]
    )


@router.business_connection()
async def on_business_connection(
    business_connection: BusinessConnection, bot: Bot
) -> None:
    can_reply = extract_can_reply(business_connection)
    await db.upsert_business_connection(
        connection_id=business_connection.id,
        user_id=business_connection.user.id,
        is_enabled=business_connection.is_enabled,
        can_reply=can_reply,
        username=business_connection.user.username,
        first_name=business_connection.user.first_name,
    )
    log.info(
        "business connection %s (user=%s) is_enabled=%s can_reply=%s",
        business_connection.id,
        business_connection.user.id,
        business_connection.is_enabled,
        can_reply,
    )

    target_chat = business_connection.user_chat_id or business_connection.user.id
    text = (
        _format_connected(business_connection, can_reply)
        if business_connection.is_enabled
        else _format_disabled()
    )

    try:
        await bot.send_message(target_chat, text)
    except Exception as exc:  # noqa: BLE001
        log.warning("failed to notify user about business connection: %s", exc)
