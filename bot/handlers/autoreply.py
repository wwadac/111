"""Autoresponder: per-trigger templates + default reply.

* Configuration lives in ``/start`` private DM via the ``/autoreply`` family of
  commands and a small FSM for adding rules.
* When a new ``business_message`` arrives, :func:`try_autoreply` is called from
  :mod:`bot.handlers.messages` — it picks the first matching rule, sends the
  reply on behalf of the user via ``business_connection_id``, and throttles
  per-chat to avoid spam.
"""

from __future__ import annotations

import logging
import re
import time
from typing import Optional

from aiogram import Bot, F, Router
from aiogram.filters import Command, CommandObject
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import (
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Message,
)
from aiogram.utils.keyboard import InlineKeyboardBuilder

from bot import db, ui
from bot.config import get_settings

router = Router(name="autoreply")
log = logging.getLogger(__name__)

# Only run the configuration commands in private DM.
router.message.filter(F.chat.type == "private", ~F.business_connection_id)


# ─── FSM ────────────────────────────────────────────────────────────────────


class AutoreplyAdd(StatesGroup):
    waiting_trigger = State()
    waiting_match_type = State()
    waiting_reply = State()


class AutoreplyDefault(StatesGroup):
    waiting_text = State()


# ─── cooldown ───────────────────────────────────────────────────────────────


_last_reply: dict[tuple[str, int], float] = {}


def _cooldown_ok(connection_id: str, chat_id: int) -> bool:
    cooldown = get_settings().autoreply_cooldown_sec
    key = (connection_id, int(chat_id))
    now = time.monotonic()
    last = _last_reply.get(key, 0.0)
    if now - last < cooldown:
        return False
    _last_reply[key] = now
    return True


# ─── matching ───────────────────────────────────────────────────────────────


def _match(text: str, trigger: str, match_type: str) -> bool:
    if not text:
        return False
    body = text.casefold()
    needle = trigger.casefold()
    if match_type == "exact":
        return body.strip() == needle.strip()
    if match_type == "starts":
        return body.lstrip().startswith(needle)
    if match_type == "regex":
        try:
            return bool(re.search(trigger, text, re.IGNORECASE | re.DOTALL))
        except re.error:
            log.warning("invalid regex trigger: %r", trigger)
            return False
    # default: contains
    return needle in body


MATCH_TYPES = {
    "contains": "содержит",
    "exact": "точное совпадение",
    "starts": "начинается с",
    "regex": "regex",
}


async def try_autoreply(
    bot: Bot, message: Message, owner_id: int, settings: dict
) -> None:
    """Match ``message`` against owner's autoreplies and send a response."""

    if not settings.get("autoreply_enabled"):
        return
    if not message.business_connection_id:
        return

    text = (message.text or message.caption or "").strip()
    if not text:
        return

    rules = await db.list_autoreplies(owner_id)
    matched: Optional[dict] = None
    for rule in rules:
        if not rule["is_enabled"]:
            continue
        if _match(text, rule["trigger"], rule["match_type"]):
            matched = rule
            break

    reply_text: Optional[str] = None
    if matched:
        reply_text = matched["reply"]
    elif settings.get("autoreply_default"):
        reply_text = settings["autoreply_default"]

    if not reply_text:
        return

    if not _cooldown_ok(message.business_connection_id, message.chat.id):
        log.debug("autoreply throttled for chat %s", message.chat.id)
        return

    try:
        await bot.send_message(
            chat_id=message.chat.id,
            text=reply_text,
            business_connection_id=message.business_connection_id,
            reply_to_message_id=message.message_id,
        )
    except Exception as exc:  # noqa: BLE001
        log.warning("autoreply send failed: %s", exc)


# ─── keyboards ──────────────────────────────────────────────────────────────


def _list_keyboard(rules: list[dict]) -> InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()
    for rule in rules:
        emoji = "✅" if rule["is_enabled"] else "⬜️"
        label = rule["trigger"]
        if len(label) > 24:
            label = label[:22] + "…"
        kb.row(
            InlineKeyboardButton(
                text=f"{emoji} {label}",
                callback_data=f"ar:toggle:{rule['id']}",
            ),
            InlineKeyboardButton(
                text="🗑",
                callback_data=f"ar:delete:{rule['id']}",
            ),
        )
    kb.row(
        InlineKeyboardButton(text="➕ Добавить", callback_data="ar:add"),
        InlineKeyboardButton(text="🔄 Обновить", callback_data="ar:refresh"),
    )
    return kb.as_markup()


def _match_type_keyboard() -> InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()
    for value, label in MATCH_TYPES.items():
        kb.row(
            InlineKeyboardButton(
                text=label,
                callback_data=f"ar:mtype:{value}",
            )
        )
    return kb.as_markup()


# ─── /autoreply command ─────────────────────────────────────────────────────


def _help_text() -> str:
    return "\n".join(
        [
            ui.header("Автоответчик", "🤖"),
            "",
            f"{ui.code('/autoreply add')}   — добавить новый шаблон",
            f"{ui.code('/autoreply list')}  — список шаблонов",
            f"{ui.code('/autoreply on')}    — включить",
            f"{ui.code('/autoreply off')}   — выключить",
            f"{ui.code('/autoreply default')} — задать ответ по умолчанию",
            f"{ui.code('/autoreply clear_default')} — сбросить «по умолчанию»",
            f"{ui.code('/autoreply purge')} — удалить все шаблоны",
            "",
            ui.i(
                "Триггер сравнивается с входящим текстом без учёта регистра. "
                "Типы матча: contains, exact, starts, regex."
            ),
            "",
            ui.i(
                f"Cooldown: одному чату не чаще раза в "
                f"{get_settings().autoreply_cooldown_sec} сек."
            ),
        ]
    )


@router.message(Command("autoreply"))
async def cmd_autoreply(
    message: Message, command: CommandObject, state: FSMContext
) -> None:
    if not message.from_user:
        return
    args = (command.args or "").strip().split(maxsplit=1)
    sub = args[0].lower() if args else ""

    if not sub:
        await message.answer(_help_text())
        return

    if sub == "add":
        await state.set_state(AutoreplyAdd.waiting_trigger)
        await message.answer(
            "\n".join(
                [
                    ui.header("Новый шаблон · шаг 1/3", "➕"),
                    ui.b("Триггер"),
                    ui.i(
                        "Пришлите текст, который должен встретиться во входящем "
                        "сообщении (или regex)."
                    ),
                    "",
                    ui.i("Отменить — ") + ui.code("/cancel"),
                ]
            )
        )
        return

    if sub == "list":
        rules = await db.list_autoreplies(message.from_user.id)
        if not rules:
            await message.answer(
                ui.header("Шаблоны", "🤖")
                + "\n"
                + ui.i("пока пусто — добавьте через ")
                + ui.code("/autoreply add"),
                reply_markup=_list_keyboard([]),
            )
            return
        lines = [ui.header("Шаблоны", "🤖"), ""]
        for rule in rules:
            on = "✅" if rule["is_enabled"] else "⬜️"
            mt = MATCH_TYPES.get(rule["match_type"], rule["match_type"])
            preview = rule["reply"]
            if len(preview) > 80:
                preview = preview[:78] + "…"
            lines.append(
                f"{on} {ui.b('#' + str(rule['id']))} · "
                f"{ui.code(rule['trigger'])} ({ui.i(mt)})"
            )
            lines.append(f"   ↳ {ui.i(preview)}")
        await message.answer("\n".join(lines), reply_markup=_list_keyboard(rules))
        return

    if sub == "on":
        await db.update_setting(message.from_user.id, "autoreply_enabled", 1)
        await message.answer(f"🤖 Автоответчик: {ui.b('включён')}")
        return

    if sub == "off":
        await db.update_setting(message.from_user.id, "autoreply_enabled", 0)
        await message.answer(f"🤖 Автоответчик: {ui.b('выключен')}")
        return

    if sub == "default":
        await state.set_state(AutoreplyDefault.waiting_text)
        await message.answer(
            "\n".join(
                [
                    ui.header("Ответ по умолчанию", "💬"),
                    ui.i(
                        "Пришлите текст, который буду слать, когда ни один "
                        "триггер не совпал."
                    ),
                    "",
                    ui.i("Отменить — ") + ui.code("/cancel"),
                ]
            )
        )
        return

    if sub == "clear_default":
        await db.update_setting(message.from_user.id, "autoreply_default", None)
        await message.answer("💬 Ответ по умолчанию сброшен.")
        return

    if sub == "purge":
        n = await db.clear_autoreplies(message.from_user.id)
        await message.answer(f"🗑 Удалено шаблонов: {ui.b(n)}")
        return

    await message.answer(_help_text())


# ─── FSM steps ──────────────────────────────────────────────────────────────


@router.message(Command("cancel"))
async def cmd_cancel(message: Message, state: FSMContext) -> None:
    current = await state.get_state()
    await state.clear()
    if current:
        await message.answer(f"❎ Ввод отменён ({ui.code(current)}).")
    else:
        await message.answer("Нечего отменять.")


@router.message(AutoreplyAdd.waiting_trigger)
async def autoreply_trigger(message: Message, state: FSMContext) -> None:
    if not message.text:
        await message.answer(ui.i("Нужен текстовый триггер."))
        return
    await state.update_data(trigger=message.text.strip())
    await state.set_state(AutoreplyAdd.waiting_match_type)
    await message.answer(
        "\n".join(
            [
                ui.header("Новый шаблон · шаг 2/3", "➕"),
                ui.b("Тип совпадения"),
                ui.bullet(f"{ui.b('contains')} — содержит подстроку"),
                ui.bullet(f"{ui.b('exact')} — точное совпадение"),
                ui.bullet(f"{ui.b('starts')} — начинается с"),
                ui.bullet(f"{ui.b('regex')} — регулярное выражение"),
            ]
        ),
        reply_markup=_match_type_keyboard(),
    )


@router.callback_query(F.data.startswith("ar:mtype:"), AutoreplyAdd.waiting_match_type)
async def autoreply_match_type_cb(query: CallbackQuery, state: FSMContext) -> None:
    if not query.data:
        return
    mtype = query.data.split(":", 2)[2]
    if mtype not in MATCH_TYPES:
        await query.answer("Неизвестный тип", show_alert=True)
        return
    await state.update_data(match_type=mtype)
    await state.set_state(AutoreplyAdd.waiting_reply)
    await query.answer(f"Тип: {MATCH_TYPES[mtype]}")
    if isinstance(query.message, Message):
        await query.message.answer(
            "\n".join(
                [
                    ui.header("Новый шаблон · шаг 3/3", "➕"),
                    ui.b("Ответ"),
                    ui.i(
                        "Пришлите текст ответа. Можно с HTML-форматированием: "
                    )
                    + ui.code("<b>")
                    + ", "
                    + ui.code("<i>")
                    + ", "
                    + ui.code("<code>")
                    + ", "
                    + ui.code("<u>")
                    + ".",
                ]
            )
        )


@router.message(AutoreplyAdd.waiting_reply)
async def autoreply_reply(message: Message, state: FSMContext) -> None:
    if not message.from_user:
        return
    data = await state.get_data()
    trigger = data.get("trigger")
    match_type = data.get("match_type", "contains")
    if not trigger:
        await state.clear()
        await message.answer(ui.i("Что-то пошло не так, начните заново: /autoreply add"))
        return
    reply_text = message.html_text or (message.text or "")
    if not reply_text.strip():
        await message.answer(ui.i("Пустой ответ нельзя."))
        return
    rule_id = await db.add_autoreply(
        owner_id=message.from_user.id,
        trigger=trigger,
        reply=reply_text,
        match_type=match_type,
    )
    await state.clear()
    await message.answer(
        "\n".join(
            [
                ui.header("Шаблон добавлен", "✅"),
                ui.kv_code("id", rule_id),
                ui.kv("триггер", trigger),
                ui.kv("тип", MATCH_TYPES.get(match_type, match_type)),
                "",
                ui.b("Ответ:"),
                ui.quote(message.text or reply_text),
                "",
                ui.i("Не забудьте включить автоответчик: ") + ui.code("/autoreply on"),
            ]
        )
    )


@router.message(AutoreplyDefault.waiting_text)
async def autoreply_default_text(message: Message, state: FSMContext) -> None:
    if not message.from_user:
        return
    text = message.html_text or message.text
    if not text or not text.strip():
        await message.answer(ui.i("Пустой текст нельзя."))
        return
    await db.update_setting(message.from_user.id, "autoreply_default", text)
    await state.clear()
    await message.answer(
        "\n".join(
            [
                ui.header("Ответ по умолчанию сохранён", "💬"),
                ui.quote(message.text or text),
            ]
        )
    )


# ─── callbacks for /autoreply list ──────────────────────────────────────────


@router.callback_query(F.data == "ar:add")
async def cb_add(query: CallbackQuery, state: FSMContext) -> None:
    await state.set_state(AutoreplyAdd.waiting_trigger)
    await query.answer()
    if isinstance(query.message, Message):
        await query.message.answer(
            ui.header("Новый шаблон · шаг 1/3", "➕")
            + "\n"
            + ui.b("Триггер")
            + "\n"
            + ui.i("Пришлите текст триггера. Отменить — ")
            + ui.code("/cancel")
        )


@router.callback_query(F.data == "ar:refresh")
async def cb_refresh(query: CallbackQuery) -> None:
    if not query.from_user:
        return
    rules = await db.list_autoreplies(query.from_user.id)
    if isinstance(query.message, Message):
        if not rules:
            text = (
                ui.header("Шаблоны", "🤖")
                + "\n"
                + ui.i("пока пусто — добавьте через ")
                + ui.code("/autoreply add")
            )
        else:
            lines = [ui.header("Шаблоны", "🤖"), ""]
            for rule in rules:
                on = "✅" if rule["is_enabled"] else "⬜️"
                mt = MATCH_TYPES.get(rule["match_type"], rule["match_type"])
                preview = rule["reply"]
                if len(preview) > 80:
                    preview = preview[:78] + "…"
                lines.append(
                    f"{on} {ui.b('#' + str(rule['id']))} · "
                    f"{ui.code(rule['trigger'])} ({ui.i(mt)})"
                )
                lines.append(f"   ↳ {ui.i(preview)}")
            text = "\n".join(lines)
        try:
            await query.message.edit_text(text, reply_markup=_list_keyboard(rules))
        except Exception:  # noqa: BLE001
            await query.message.answer(text, reply_markup=_list_keyboard(rules))
    await query.answer("Обновлено")


@router.callback_query(F.data.startswith("ar:toggle:"))
async def cb_toggle(query: CallbackQuery) -> None:
    if not query.from_user or not query.data:
        return
    try:
        rule_id = int(query.data.split(":", 2)[2])
    except ValueError:
        await query.answer("bad id", show_alert=True)
        return
    new_state = await db.toggle_autoreply(query.from_user.id, rule_id)
    if new_state is None:
        await query.answer("не найдено", show_alert=True)
        return
    await query.answer("включено" if new_state else "выключено")
    await cb_refresh(query)


@router.callback_query(F.data.startswith("ar:delete:"))
async def cb_delete(query: CallbackQuery) -> None:
    if not query.from_user or not query.data:
        return
    try:
        rule_id = int(query.data.split(":", 2)[2])
    except ValueError:
        await query.answer("bad id", show_alert=True)
        return
    ok = await db.delete_autoreply(query.from_user.id, rule_id)
    await query.answer("удалено" if ok else "не найдено", show_alert=not ok)
    await cb_refresh(query)
