# Business Saver Bot

Telegram-бот в режиме **Business Mode**: подключается к вашему аккаунту через
*Telegram Premium → Бизнес → Чат-боты* и тихо работает за вас.

## Что умеет

- 🔥 **Одноразовые медиа** — ловит фото / видео / голосовые / кружки в
  бизнес-чатах и присылает копию вам в ЛС.
- 💬 **Reply-триггер** — ответьте на любое медиа в бизнес-чате, и бот
  пришлёт его копию.
- ✏️ **Правки** — присылает **было → стало** при редактировании.
- 🗑 **Удаления** — восстанавливает текст и (если успел сохранить) сам
  файл удалённого сообщения.
- 🤖 **Автоответчик** — отвечает за вас по триггерам:
  `contains` / `exact` / `starts` / `regex`; есть «ответ по умолчанию» и
  cooldown на чат.
- ⚙️ **Inline-настройки** — `/settings` с тумблерами всех фич.

Текст бота оформлен с использованием `<b>`, `<i>`, `<code>`, `<u>`,
`<blockquote>`, `<tg-spoiler>` — HTML parse mode по умолчанию.

## Стек

- Python **3.10+** (рекомендуется 3.11/3.12)
- [`aiogram`](https://pypi.org/project/aiogram/) **3.15+**
- [`aiosqlite`](https://pypi.org/project/aiosqlite/) — локальная SQLite

Всё состояние (подключения, шаблоны автоответов, последний снимок сообщений
для трекинга правок/удалений) хранится в `bot.db` в рабочей директории.

## Быстрый старт

```bash
# 1. Клонируем и заходим в репо
git clone https://github.com/wwadac/111.git
cd 111

# 2. Виртуальное окружение и зависимости
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt

# 3. Конфигурация
cp .env.example .env
# отредактируйте .env и впишите BOT_TOKEN от @BotFather

# 4. Запуск
python -m bot
```

После запуска:

1. Откройте Telegram → **Настройки → Бизнес → Чат-боты**
   *(нужен Telegram Premium)*.
2. Вставьте username вашего бота — `@your_bot`.
3. Включите права **Чтение сообщений** и **Ответы** (если хотите автоответы).
4. Бот пришлёт подтверждение прямо в ЛС.

В ЛС с ботом доступны команды:

| Команда | Что делает |
| --- | --- |
| `/start` | приветствие и инструкция |
| `/help` | подробная справка |
| `/status` | состояние подключения и флаги |
| `/settings` | inline-меню с тумблерами фич |
| `/autoreply` | управление автоответами (FSM-добавление) |
| `/cancel` | отменить ввод в FSM |

## Конфигурация (`.env`)

| Переменная | Значение по умолчанию | Описание |
| --- | --- | --- |
| `BOT_TOKEN` | *обязательная* | токен от `@BotFather` |
| `DATABASE_PATH` | `bot.db` | путь к SQLite-файлу |
| `LOG_LEVEL` | `INFO` | `DEBUG` / `INFO` / `WARNING` / `ERROR` |
| `AUTOREPLY_COOLDOWN_SEC` | `60` | минимальный интервал между автоответами в одном чате |

## Деплой через systemd (опционально)

`/etc/systemd/system/business-saver-bot.service`:

```ini
[Unit]
Description=Business Saver Bot
After=network-online.target

[Service]
User=botuser
WorkingDirectory=/opt/business-saver-bot
EnvironmentFile=/opt/business-saver-bot/.env
ExecStart=/opt/business-saver-bot/.venv/bin/python -m bot
Restart=on-failure
RestartSec=5

[Install]
WantedBy=multi-user.target
```

```bash
sudo systemctl daemon-reload
sudo systemctl enable --now business-saver-bot
sudo journalctl -u business-saver-bot -f
```

## Структура проекта

```
bot/
├── __init__.py
├── __main__.py            # точка входа: python -m bot
├── config.py              # .env → Settings
├── db.py                  # aiosqlite: connections, messages, autoreplies, settings
├── ui.py                  # HTML-форматтеры и UI-сниппеты
└── handlers/
    ├── start.py           # /start, /help, /status
    ├── settings.py        # /settings + inline-тумблеры
    ├── autoreply.py       # /autoreply + FSM + try_autoreply()
    ├── business.py        # business_connection
    └── messages.py        # business_message / edited / deleted
```

## Заметки про одноразовые медиа

Bot API не выдаёт явного флага «view-once» для медиа. Бот использует
эвристику + два режима:

- **Capture one-time** *(по умолчанию)* — ловятся медиа с
  `has_media_spoiler`, голосовые, кружки и одиночные фото/видео без
  подписи (это типичный профиль «отправить один раз»).
- **Capture all media** — ловится вообще всё (включая обычные фото).

Плюс работает **reply-триггер**: любой ответ на сообщение с медиа
немедленно отправляет копию владельцу — даже если автозахват выключен.
Это и есть «отправить любое сообщение на одноразовый файл, чтобы бот его
прочёл».

## Безопасность

- В бизнес-чате бот пишет только при включённом автоответчике.
- Все копии медиа и нотификации уходят строго в ЛС владельца.
- Удалили бота в настройках Telegram — он тут же помечает подключение
  как `is_enabled = 0` и перестаёт обрабатывать чужие сообщения.
