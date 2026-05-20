"""Runtime configuration loaded from environment variables."""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Optional

from dotenv import load_dotenv


@dataclass(frozen=True)
class Settings:
    bot_token: str
    database_path: str = "bot.db"
    log_level: str = "INFO"
    autoreply_cooldown_sec: int = 60


_settings: Optional[Settings] = None


def get_settings() -> Settings:
    """Load settings on first access. Raises SystemExit if BOT_TOKEN missing."""

    global _settings
    if _settings is not None:
        return _settings

    load_dotenv()

    token = os.getenv("BOT_TOKEN", "").strip()
    if not token:
        raise SystemExit(
            "BOT_TOKEN is not set. Copy .env.example to .env and put your "
            "@BotFather token in BOT_TOKEN."
        )

    try:
        cooldown = int(os.getenv("AUTOREPLY_COOLDOWN_SEC", "60"))
    except ValueError:
        cooldown = 60

    _settings = Settings(
        bot_token=token,
        database_path=os.getenv("DATABASE_PATH", "bot.db"),
        log_level=os.getenv("LOG_LEVEL", "INFO").upper(),
        autoreply_cooldown_sec=cooldown,
    )
    return _settings
