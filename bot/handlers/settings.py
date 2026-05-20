"""``/settings`` — inline keyboard to toggle feature flags."""

from __future__ import annotations

from aiogram import F, Router
from aiogram.filters import Command
from aiogram.types import (
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Message,
)
from aiogram.utils.keyboard import InlineKeyboardBuilder

from bot import db, ui

router = Router(name="settings")
router.message.filter(F.chat.type == "private", ~F.business_connection_id)


FLAGS: list[tuple[str, str, str]] = [
    ("capture_one_time", "Захват одноразовых", "капчу spoiler/single-media"),
    ("capture_all_media", "Захват всех медиа", "любое фото/видео/файл"),
    ("track_edits", "Трекинг правок", "присылать «было/стало»"),
    ("track_deletes", "Трекинг удалений", "присылать удалённый контент"),
    ("autoreply_enabled", "Автоответчик", "общий тумблер"),
]


def _keyboard(settings: dict) -> InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()
    for key, label, _desc in FLAGS:
        check = "✅" if settings.get(key) else "⬜️"
        kb.row(
            InlineKeyboardButton(
                text=f"{check}  {label}",
                callback_data=f"set:toggle:{key}",
            )
        )
    return kb.as_markup()


def _render(settings: dict) -> str:
    lines = [ui.header("Настройки", "⚙️"), ""]
    for key, label, desc in FLAGS:
        state = ui.b("ON") if settings.get(key) else ui.s("off")
        lines.append(f"{ui.b(label)}  ·  {state}")
        lines.append(f"   {ui.i(desc)}")
    default = settings.get("autoreply_default")
    lines.append("")
    if default:
        preview = default if len(default) <= 120 else default[:118] + "…"
        lines.append(f"{ui.b('Default reply:')} {ui.code(preview)}")
    else:
        lines.append(f"{ui.b('Default reply:')} {ui.i('не задан')}")
    lines.append("")
    lines.append(ui.i("Тапните по кнопке, чтобы переключить."))
    return "\n".join(lines)


@router.message(Command("settings"))
async def cmd_settings(message: Message) -> None:
    if not message.from_user:
        return
    settings = await db.get_settings_row(message.from_user.id)
    await message.answer(_render(settings), reply_markup=_keyboard(settings))


@router.callback_query(F.data.startswith("set:toggle:"))
async def cb_toggle(query: CallbackQuery) -> None:
    if not query.from_user or not query.data:
        return
    key = query.data.split(":", 2)[2]
    if key not in {flag[0] for flag in FLAGS}:
        await query.answer("неизвестный флаг", show_alert=True)
        return
    new_value = await db.toggle_setting(query.from_user.id, key)
    settings = await db.get_settings_row(query.from_user.id)
    if isinstance(query.message, Message):
        try:
            await query.message.edit_text(
                _render(settings), reply_markup=_keyboard(settings)
            )
        except Exception:  # noqa: BLE001
            await query.message.answer(
                _render(settings), reply_markup=_keyboard(settings)
            )
    await query.answer("включено" if new_value else "выключено")
