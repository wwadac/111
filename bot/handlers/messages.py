"""Capture incoming business messages, track edits and deletions.

This module owns:

* ``business_message`` — saves the message, captures one-time / spoiler media,
  honours the *reply-trigger* (a reply to any media re-sends a copy to the
  owner), and feeds the autoresponder.
* ``edited_business_message`` — compares the stored copy with the new version
  and sends a *was → now* diff to the owner.
* ``deleted_business_messages`` — replays the last known content of each
  deleted message.
"""

from __future__ import annotations

import logging
import time
from typing import Optional

from aiogram import Bot, Router
from aiogram.exceptions import TelegramBadRequest
from aiogram.types import (
    BufferedInputFile,
    BusinessMessagesDeleted,
    Message,
)

from bot import db, ui
from bot.handlers.autoreply import try_autoreply

router = Router(name="business_messages")
log = logging.getLogger(__name__)


# Dedup window: do not re-forward the same source message within this many
# seconds (useful when several replies arrive in quick succession).
_CAPTURE_DEDUP_SEC = 600
_recently_captured: dict[tuple[str, int, int], float] = {}


def _dedup_capture(connection_id: str, chat_id: int, message_id: int) -> bool:
    key = (connection_id, int(chat_id), int(message_id))
    now = time.monotonic()
    # purge stale entries opportunistically
    if len(_recently_captured) > 1024:
        for k, ts in list(_recently_captured.items()):
            if now - ts > _CAPTURE_DEDUP_SEC:
                _recently_captured.pop(k, None)
    last = _recently_captured.get(key, 0.0)
    if now - last < _CAPTURE_DEDUP_SEC:
        return False
    _recently_captured[key] = now
    return True


# ─── helpers ────────────────────────────────────────────────────────────────


MEDIA_KINDS = (
    "photo",
    "video",
    "video_note",
    "voice",
    "audio",
    "animation",
    "document",
    "sticker",
)


def detect_content_type(msg: Message) -> str:
    for kind in MEDIA_KINDS:
        if getattr(msg, kind, None):
            return kind
    if msg.text:
        return "text"
    if msg.poll:
        return "poll"
    if msg.contact:
        return "contact"
    if msg.location:
        return "location"
    if msg.dice:
        return "dice"
    if getattr(msg, "story", None):
        return "story"
    return "unknown"


def extract_file(msg: Message) -> tuple[Optional[str], Optional[str], Optional[str]]:
    """Return ``(file_id, file_unique_id, file_type)`` if the message has media."""

    if msg.photo:
        photo = msg.photo[-1]
        return photo.file_id, photo.file_unique_id, "photo"
    if msg.video:
        return msg.video.file_id, msg.video.file_unique_id, "video"
    if msg.video_note:
        return msg.video_note.file_id, msg.video_note.file_unique_id, "video_note"
    if msg.voice:
        return msg.voice.file_id, msg.voice.file_unique_id, "voice"
    if msg.audio:
        return msg.audio.file_id, msg.audio.file_unique_id, "audio"
    if msg.animation:
        return msg.animation.file_id, msg.animation.file_unique_id, "animation"
    if msg.document:
        return msg.document.file_id, msg.document.file_unique_id, "document"
    if msg.sticker:
        return msg.sticker.file_id, msg.sticker.file_unique_id, "sticker"
    return None, None, None


def sender_label(msg: Message) -> str:
    if not msg.from_user:
        return "—"
    name = msg.from_user.full_name or "—"
    if msg.from_user.username:
        return f"{name} (@{msg.from_user.username})"
    return name


def chat_label(msg: Message) -> str:
    chat = msg.chat
    if chat.title:
        return chat.title
    if chat.username:
        return f"@{chat.username}"
    if chat.first_name or chat.last_name:
        return " ".join(filter(None, [chat.first_name, chat.last_name]))
    return f"chat#{chat.id}"


def is_likely_one_time(msg: Message) -> bool:
    """Conservative heuristic — the Bot API does not expose the explicit
    «view-once» TTL flag, so we treat ``has_media_spoiler`` as the only
    definite signal of one-time / blurred media.

    The main workflow for capturing genuine «view-once» content is the
    **reply-trigger**: the user (or anyone) replies to the media in the
    business chat, and :func:`on_business_message` forwards the original.
    """

    return bool(msg.has_media_spoiler)


# ─── media forwarding ───────────────────────────────────────────────────────


async def _send_by_file_id(
    bot: Bot, chat_id: int, msg: Message, caption: str
) -> None:
    if msg.photo:
        await bot.send_photo(chat_id, msg.photo[-1].file_id, caption=caption)
    elif msg.video:
        await bot.send_video(chat_id, msg.video.file_id, caption=caption)
    elif msg.video_note:
        await bot.send_message(chat_id, caption)
        await bot.send_video_note(chat_id, msg.video_note.file_id)
    elif msg.voice:
        await bot.send_voice(chat_id, msg.voice.file_id, caption=caption)
    elif msg.audio:
        await bot.send_audio(chat_id, msg.audio.file_id, caption=caption)
    elif msg.animation:
        await bot.send_animation(chat_id, msg.animation.file_id, caption=caption)
    elif msg.document:
        await bot.send_document(chat_id, msg.document.file_id, caption=caption)
    elif msg.sticker:
        await bot.send_message(chat_id, caption)
        await bot.send_sticker(chat_id, msg.sticker.file_id)
    else:
        await bot.send_message(chat_id, caption)


async def _send_by_download(
    bot: Bot, chat_id: int, msg: Message, caption: str
) -> None:
    file_id, _unique, file_type = extract_file(msg)
    if not file_id:
        await bot.send_message(chat_id, caption)
        return

    file = await bot.get_file(file_id)
    if not file.file_path:
        raise RuntimeError("Telegram returned empty file_path")
    buf = await bot.download_file(file.file_path)
    if buf is None:
        raise RuntimeError("download_file returned None")
    data = buf.read()
    filename = file.file_path.rsplit("/", 1)[-1] or f"{file_type or 'file'}"
    input_file = BufferedInputFile(data, filename=filename)

    if file_type == "photo":
        await bot.send_photo(chat_id, input_file, caption=caption)
    elif file_type == "video":
        await bot.send_video(chat_id, input_file, caption=caption)
    elif file_type == "video_note":
        await bot.send_message(chat_id, caption)
        await bot.send_video_note(chat_id, input_file)
    elif file_type == "voice":
        await bot.send_voice(chat_id, input_file, caption=caption)
    elif file_type == "audio":
        await bot.send_audio(chat_id, input_file, caption=caption)
    elif file_type == "animation":
        await bot.send_animation(chat_id, input_file, caption=caption)
    elif file_type == "document":
        await bot.send_document(chat_id, input_file, caption=caption)
    elif file_type == "sticker":
        await bot.send_message(chat_id, caption)
        await bot.send_sticker(chat_id, input_file)
    else:
        await bot.send_document(chat_id, input_file, caption=caption)


async def forward_media(
    bot: Bot,
    msg: Message,
    owner_id: int,
    *,
    title: str,
    source: str,
) -> None:
    """Send a copy of the media in ``msg`` to ``owner_id`` with a stylish
    info-card caption. Falls back to download + re-upload on any error."""

    kind = detect_content_type(msg)
    file_id, file_unique, file_type = extract_file(msg)

    parts: list[str] = [
        ui.header(title, "🔥" if "одноразов" in title.lower() else "📥"),
        ui.kv("от", sender_label(msg)),
    ]
    if msg.from_user:
        parts.append(ui.kv_code("user_id", msg.from_user.id))
    parts.append(ui.kv("чат", chat_label(msg)))
    parts.append(ui.kv("тип", ui.content_label(kind)))
    parts.append(ui.kv_code("message_id", msg.message_id))
    parts.append(ui.kv_italic("источник", source))
    if file_unique:
        parts.append(ui.kv_code("file_unique_id", file_unique))

    if msg.caption:
        body = msg.html_text or ui.safe(msg.caption)
        parts.append("")
        parts.append(ui.b("Подпись:"))
        parts.append(ui.quote(body) if len(body) < 600 else body)

    caption = "\n".join(parts)
    # Telegram caption hard-limit is 1024 chars; trim conservatively.
    if len(caption) > 1000:
        caption = caption[:990] + "…"

    try:
        await _send_by_file_id(bot, owner_id, msg, caption)
        return
    except TelegramBadRequest as exc:
        log.info("file_id forward failed (%s) — falling back to download", exc)
    except Exception as exc:  # noqa: BLE001
        log.warning("file_id forward unexpected error: %s — falling back", exc)

    try:
        await _send_by_download(bot, owner_id, msg, caption)
    except Exception as exc:  # noqa: BLE001
        log.exception("media forward fully failed: %s", exc)
        # The terminal notification can itself fail (e.g. owner blocked
        # the bot or never started it); swallow that secondary error so
        # the update handler still returns cleanly.
        try:
            await bot.send_message(
                owner_id,
                caption
                + "\n\n"
                + ui.i(f"(не удалось скопировать файл: {ui.safe(str(exc))})"),
            )
        except Exception as notify_exc:  # noqa: BLE001
            log.warning(
                "failed to notify owner about media forward failure: %s",
                notify_exc,
            )


# ─── persistence ────────────────────────────────────────────────────────────


async def _persist(msg: Message) -> None:
    if not msg.business_connection_id:
        return
    file_id, file_unique, file_type = extract_file(msg)
    await db.save_message(
        business_connection_id=msg.business_connection_id,
        chat_id=msg.chat.id,
        message_id=msg.message_id,
        from_user_id=msg.from_user.id if msg.from_user else None,
        from_user_name=sender_label(msg) if msg.from_user else None,
        chat_title=chat_label(msg),
        content_type=detect_content_type(msg),
        text=msg.text,
        caption=msg.caption,
        file_id=file_id,
        file_unique_id=file_unique,
        file_type=file_type,
        is_one_time=int(is_likely_one_time(msg)),
        has_media_spoiler=int(bool(msg.has_media_spoiler)),
    )


# ─── handlers ───────────────────────────────────────────────────────────────


@router.business_message()
async def on_business_message(message: Message, bot: Bot) -> None:
    if not message.business_connection_id:
        return

    await _persist(message)

    owner_id = await db.get_owner_by_connection(message.business_connection_id)
    if not owner_id:
        log.debug("unknown business connection %s", message.business_connection_id)
        return

    settings = await db.get_settings_row(owner_id)
    is_from_owner = bool(message.from_user and message.from_user.id == owner_id)
    is_from_bot = bool(getattr(message, "sender_business_bot", None))

    # 1) Reply-trigger: any reply to a message with media → forward that media.
    #    Decoupled from auto-capture toggles so it always works as the manual
    #    "save this" gesture documented in the README. We skip replies that
    #    originate from another bot acting on the user's behalf
    #    (``sender_business_bot``) to avoid feedback loops with our own
    #    autoreplies.
    reply = message.reply_to_message
    if (
        reply
        and not is_from_bot
        and any(getattr(reply, k, None) for k in MEDIA_KINDS)
    ):
        if _dedup_capture(
            message.business_connection_id, reply.chat.id, reply.message_id
        ):
            await forward_media(
                bot,
                reply,
                owner_id,
                title="Медиа по reply-триггеру",
                source=f"ответ от {sender_label(message)}",
            )

    # 2) Direct media capture for incoming messages.
    if not is_from_owner and not is_from_bot:
        if any(getattr(message, k, None) for k in MEDIA_KINDS):
            one_time = is_likely_one_time(message)
            should_capture = settings["capture_all_media"] or (
                settings["capture_one_time"] and one_time
            )
            if should_capture and _dedup_capture(
                message.business_connection_id, message.chat.id, message.message_id
            ):
                title = "Одноразовое медиа" if one_time else "Новое медиа"
                await forward_media(
                    bot,
                    message,
                    owner_id,
                    title=title,
                    source="входящее сообщение",
                )

    # 3) Autoreply only for incoming text-bearing messages.
    if not is_from_owner and not is_from_bot and (message.text or message.caption):
        await try_autoreply(bot, message, owner_id, settings)


@router.edited_business_message()
async def on_edited_business_message(message: Message, bot: Bot) -> None:
    if not message.business_connection_id:
        return

    owner_id = await db.get_owner_by_connection(message.business_connection_id)
    if not owner_id:
        return

    settings = await db.get_settings_row(owner_id)
    if not settings["track_edits"]:
        await _persist(message)
        return

    is_from_owner = bool(message.from_user and message.from_user.id == owner_id)
    if is_from_owner:
        # owner edits their own message — no need to notify
        await _persist(message)
        return

    old = await db.get_message(
        message.business_connection_id, message.chat.id, message.message_id
    )

    old_body = (old.get("text") if old else None) or (
        old.get("caption") if old else None
    )
    new_body = message.text or message.caption

    if old_body and new_body and old_body == new_body:
        # nothing interesting (could be media-only edit)
        await _persist(message)
        return

    parts = [
        ui.header("Сообщение отредактировано", "✏️"),
        ui.kv("от", sender_label(message)),
        ui.kv("чат", chat_label(message)),
        ui.kv_code("message_id", message.message_id),
        "",
        ui.b("Было:"),
        ui.quote(old_body) if old_body else ui.i("(нет данных в истории бота)"),
        "",
        ui.b("Стало:"),
        ui.quote(new_body) if new_body else ui.i("(пусто или медиа без подписи)"),
    ]
    try:
        await bot.send_message(owner_id, "\n".join(parts))
    except Exception as exc:  # noqa: BLE001
        log.warning("failed to send edit notification: %s", exc)

    await _persist(message)


@router.deleted_business_messages()
async def on_deleted_business_messages(
    business_messages: BusinessMessagesDeleted, bot: Bot
) -> None:
    connection_id = business_messages.business_connection_id
    owner_id = await db.get_owner_by_connection(connection_id)
    if not owner_id:
        return

    settings = await db.get_settings_row(owner_id)
    if not settings["track_deletes"]:
        return

    for message_id in business_messages.message_ids:
        record = await db.get_message(connection_id, business_messages.chat.id, message_id)

        # Skip own deletions
        if record and record.get("from_user_id") == owner_id:
            continue

        parts = [
            ui.header("Сообщение удалено", "🗑"),
            ui.kv_code("chat_id", business_messages.chat.id),
            ui.kv_code("message_id", message_id),
        ]
        if record:
            parts.extend(
                [
                    ui.kv("от", record.get("from_user_name") or "—"),
                    ui.kv("чат", record.get("chat_title") or "—"),
                    ui.kv("тип", ui.content_label(record.get("content_type"))),
                    ui.kv_italic("отправлено", record.get("created_at") or "—"),
                ]
            )
            body = record.get("text") or record.get("caption")
            if body:
                parts.append("")
                parts.append(ui.b("Содержимое:"))
                parts.append(ui.quote(body))
            if record.get("file_id") and record.get("file_type"):
                parts.append("")
                parts.append(
                    f"{ui.b('Медиа:')} {ui.code(record.get('file_type'))}"
                )
        else:
            parts.append("")
            parts.append(ui.i("В истории бота этого сообщения нет — пришлось пропустить тело."))

        try:
            await bot.send_message(owner_id, "\n".join(parts))
        except Exception as exc:  # noqa: BLE001
            log.warning("failed to send delete notification: %s", exc)
            continue

        # Try to re-send the actual media file we captured (if any)
        if record and record.get("file_id") and record.get("file_type"):
            try:
                file_type = record["file_type"]
                file_id = record["file_id"]
                if file_type == "photo":
                    await bot.send_photo(owner_id, file_id)
                elif file_type == "video":
                    await bot.send_video(owner_id, file_id)
                elif file_type == "video_note":
                    await bot.send_video_note(owner_id, file_id)
                elif file_type == "voice":
                    await bot.send_voice(owner_id, file_id)
                elif file_type == "audio":
                    await bot.send_audio(owner_id, file_id)
                elif file_type == "animation":
                    await bot.send_animation(owner_id, file_id)
                elif file_type == "document":
                    await bot.send_document(owner_id, file_id)
                elif file_type == "sticker":
                    await bot.send_sticker(owner_id, file_id)
            except Exception as exc:  # noqa: BLE001
                log.info("could not resend deleted media: %s", exc)
