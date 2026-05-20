"""``/start`` and ``/help`` for the bot's own DM."""

from __future__ import annotations

from aiogram import Bot, F, Router
from aiogram.filters import Command, CommandStart
from aiogram.types import Message

from bot import db, ui

router = Router(name="start")

# Run only in the bot's private DM with the user (not inside business chats).
router.message.filter(F.chat.type == "private", ~F.business_connection_id)


def build_welcome(bot_username: str) -> str:
    return "\n".join(
        [
            f"✨ {ui.b('Business Saver Bot')}",
            ui.i("личный ассистент для бизнес-аккаунта Telegram"),
            "",
            ui.quote(
                "Сохраняю одноразовые медиа, ловлю правки и удаления, "
                "отвечаю за вас по шаблонам."
            ),
            "",
            ui.header("Как подключить", "🛠"),
            f"  {ui.b('1.')} Откройте {ui.b('Настройки → Бизнес → Чат-боты')} в Telegram",
            f"     {ui.i('(нужен Telegram Premium)')}",
            f"  {ui.b('2.')} Вставьте имя бота: {ui.code('@' + bot_username)}",
            f"  {ui.b('3.')} Разрешите ему {ui.u('читать сообщения')} и "
            f"{ui.u('отвечать')} (для автоответов)",
            f"  {ui.b('4.')} Я пришлю подтверждение прямо сюда",
            "",
            ui.header("Возможности", "⚡"),
            ui.bullet(f"{ui.b('Одноразовые медиа')} — сохраняю фото, видео, голос, кружки"),
            ui.bullet(f"{ui.b('Правки сообщений')} — присылаю было/стало"),
            ui.bullet(f"{ui.b('Удалённые сообщения')} — восстанавливаю текст и медиа"),
            ui.bullet(f"{ui.b('Автоответчик')} — отвечает за вас по триггерам"),
            "",
            ui.header("Команды", "📜"),
            f"  {ui.code('/status')}    — состояние подключения",
            f"  {ui.code('/settings')}  — настройки фич",
            f"  {ui.code('/autoreply')} — управление автоответами",
            f"  {ui.code('/help')}      — подробная справка",
            f"  {ui.code('/cancel')}    — отменить ввод",
            "",
            ui.i("Данные хранятся локально на сервере бота."),
        ]
    )


def build_help() -> str:
    return "\n".join(
        [
            ui.header("Подробная справка", "📖"),
            "",
            ui.b("Одноразовые медиа"),
            ui.i(
                "Когда кто-то присылает в ваш бизнес-чат фото или видео "
                "«посмотреть один раз», я ловлю файл и отправляю копию сюда."
            ),
            ui.bullet(
                f"режим по умолчанию: только {ui.b('одноразовые')} и "
                f"{ui.b('защищённые')} медиа"
            ),
            ui.bullet(
                f"в {ui.code('/settings')} можно включить "
                f"{ui.b('капчу всех медиа')} (любое фото/видео)"
            ),
            ui.bullet(
                f"{ui.b('reply-триггер')}: ответьте на любое медиа в "
                "бизнес-чате — пришлю копию сюда"
            ),
            "",
            ui.b("Правки и удаления"),
            ui.bullet("ловлю редактирование сообщения и отправляю «было → стало»"),
            ui.bullet("при удалении присылаю последнее сохранённое содержимое"),
            ui.bullet(f"можно выключить в {ui.code('/settings')}"),
            "",
            ui.b("Автоответчик"),
            ui.bullet(f"{ui.code('/autoreply add')} — добавить триггер и ответ"),
            ui.bullet(f"{ui.code('/autoreply list')} — список с кнопками"),
            ui.bullet(f"{ui.code('/autoreply default')} — ответ «по умолчанию»"),
            ui.bullet(f"{ui.code('/autoreply on')} / {ui.code('/autoreply off')} — общий тумблер"),
            ui.bullet(
                f"{ui.b('cooldown')}: одному и тому же чату не чаще 1 раза "
                f"в {ui.code('60 сек')}"
            ),
            "",
            ui.b("Безопасность"),
            ui.bullet("бот пишет в чат только при включённом автоответчике"),
            ui.bullet("копии медиа отправляются только вам в ЛС"),
            ui.bullet("отключите бота в Настройках Telegram, и я тут же забуду подключение"),
        ]
    )


@router.message(CommandStart())
async def cmd_start(message: Message, bot: Bot) -> None:
    me = await bot.get_me()
    await message.answer(build_welcome(me.username or "your_bot"))


@router.message(Command("help"))
async def cmd_help(message: Message) -> None:
    await message.answer(build_help())


@router.message(Command("status"))
async def cmd_status(message: Message) -> None:
    if not message.from_user:
        return

    conn = await db.get_connection_by_user(message.from_user.id)
    settings = await db.get_settings_row(message.from_user.id)

    if not conn:
        await message.answer(
            "\n".join(
                [
                    ui.header("Статус", "📡"),
                    f"{ui.b('Подключение:')} {ui.i('нет')}",
                    "",
                    ui.i(
                        "Подключите бота в Telegram: "
                        "Настройки → Бизнес → Чат-боты"
                    ),
                ]
            )
        )
        return

    flags = {
        "capture_one_time": "одноразовые",
        "capture_all_media": "все медиа",
        "track_edits": "правки",
        "track_deletes": "удаления",
        "autoreply_enabled": "автоответ",
    }
    flag_lines = [
        f"  {'✅' if settings[key] else '⬜️'} {label}"
        for key, label in flags.items()
    ]

    text = "\n".join(
        [
            ui.header("Статус", "📡"),
            ui.kv_code("connection_id", conn["connection_id"]),
            ui.kv("включён", "да" if conn["is_enabled"] else "нет"),
            ui.kv("право отвечать", "да" if conn["can_reply"] else "нет"),
            "",
            ui.b("Фичи:"),
            *flag_lines,
            "",
            ui.i(f"обновлено: {conn['updated_at']}"),
        ]
    )
    await message.answer(text)
