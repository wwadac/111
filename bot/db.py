"""Async SQLite persistence layer."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Optional

import aiosqlite

from bot.config import get_settings


def _db_path() -> str:
    return get_settings().database_path


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


SCHEMA = """
CREATE TABLE IF NOT EXISTS business_connections (
    connection_id TEXT PRIMARY KEY,
    user_id       INTEGER NOT NULL,
    is_enabled    INTEGER NOT NULL DEFAULT 1,
    can_reply     INTEGER NOT NULL DEFAULT 0,
    username      TEXT,
    first_name    TEXT,
    created_at    TEXT NOT NULL,
    updated_at    TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_connections_user
    ON business_connections(user_id);

CREATE TABLE IF NOT EXISTS messages (
    business_connection_id TEXT NOT NULL,
    chat_id                INTEGER NOT NULL,
    message_id             INTEGER NOT NULL,
    from_user_id           INTEGER,
    from_user_name         TEXT,
    chat_title             TEXT,
    content_type           TEXT,
    text                   TEXT,
    caption                TEXT,
    file_id                TEXT,
    file_unique_id         TEXT,
    file_type              TEXT,
    is_one_time            INTEGER NOT NULL DEFAULT 0,
    has_media_spoiler      INTEGER NOT NULL DEFAULT 0,
    created_at             TEXT NOT NULL,
    PRIMARY KEY (business_connection_id, chat_id, message_id)
);

CREATE INDEX IF NOT EXISTS idx_messages_chat
    ON messages(chat_id);
CREATE INDEX IF NOT EXISTS idx_messages_connection
    ON messages(business_connection_id);

CREATE TABLE IF NOT EXISTS autoreplies (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    owner_id    INTEGER NOT NULL,
    trigger     TEXT NOT NULL,
    reply       TEXT NOT NULL,
    match_type  TEXT NOT NULL DEFAULT 'contains',
    is_enabled  INTEGER NOT NULL DEFAULT 1,
    created_at  TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_autoreplies_owner
    ON autoreplies(owner_id);

CREATE TABLE IF NOT EXISTS user_settings (
    owner_id              INTEGER PRIMARY KEY,
    capture_one_time      INTEGER NOT NULL DEFAULT 1,
    capture_all_media     INTEGER NOT NULL DEFAULT 0,
    track_edits           INTEGER NOT NULL DEFAULT 1,
    track_deletes         INTEGER NOT NULL DEFAULT 1,
    autoreply_enabled     INTEGER NOT NULL DEFAULT 0,
    autoreply_default     TEXT
);
"""


async def init_db() -> None:
    async with aiosqlite.connect(_db_path()) as db:
        await db.executescript(SCHEMA)
        await db.commit()


# ─── business connections ────────────────────────────────────────────────────


async def upsert_business_connection(
    *,
    connection_id: str,
    user_id: int,
    is_enabled: bool,
    can_reply: bool,
    username: Optional[str],
    first_name: Optional[str],
) -> None:
    async with aiosqlite.connect(_db_path()) as db:
        await db.execute(
            """
            INSERT INTO business_connections (
                connection_id, user_id, is_enabled, can_reply,
                username, first_name, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(connection_id) DO UPDATE SET
                user_id    = excluded.user_id,
                is_enabled = excluded.is_enabled,
                can_reply  = excluded.can_reply,
                username   = excluded.username,
                first_name = excluded.first_name,
                updated_at = excluded.updated_at
            """,
            (
                connection_id,
                int(user_id),
                int(is_enabled),
                int(can_reply),
                username,
                first_name,
                now_iso(),
                now_iso(),
            ),
        )
        await db.commit()


async def get_connection_by_user(user_id: int) -> Optional[dict]:
    async with aiosqlite.connect(_db_path()) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            """
            SELECT * FROM business_connections
            WHERE user_id = ? AND is_enabled = 1
            ORDER BY updated_at DESC
            LIMIT 1
            """,
            (int(user_id),),
        ) as cur:
            row = await cur.fetchone()
            return dict(row) if row else None


async def get_owner_by_connection(connection_id: str) -> Optional[int]:
    async with aiosqlite.connect(_db_path()) as db:
        async with db.execute(
            "SELECT user_id FROM business_connections WHERE connection_id = ?",
            (connection_id,),
        ) as cur:
            row = await cur.fetchone()
            return int(row[0]) if row else None


async def get_connection(connection_id: str) -> Optional[dict]:
    async with aiosqlite.connect(_db_path()) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            "SELECT * FROM business_connections WHERE connection_id = ?",
            (connection_id,),
        ) as cur:
            row = await cur.fetchone()
            return dict(row) if row else None


# ─── messages ────────────────────────────────────────────────────────────────


_MESSAGE_COLUMNS = (
    "business_connection_id",
    "chat_id",
    "message_id",
    "from_user_id",
    "from_user_name",
    "chat_title",
    "content_type",
    "text",
    "caption",
    "file_id",
    "file_unique_id",
    "file_type",
    "is_one_time",
    "has_media_spoiler",
    "created_at",
)


async def save_message(**fields: Any) -> None:
    fields.setdefault("created_at", now_iso())
    payload = {k: fields.get(k) for k in _MESSAGE_COLUMNS}
    columns = ", ".join(payload.keys())
    placeholders = ", ".join("?" * len(payload))
    async with aiosqlite.connect(_db_path()) as db:
        await db.execute(
            f"INSERT OR REPLACE INTO messages ({columns}) VALUES ({placeholders})",
            tuple(payload.values()),
        )
        await db.commit()


async def get_message(
    business_connection_id: str, chat_id: int, message_id: int
) -> Optional[dict]:
    async with aiosqlite.connect(_db_path()) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            """
            SELECT * FROM messages
            WHERE business_connection_id = ? AND chat_id = ? AND message_id = ?
            """,
            (business_connection_id, int(chat_id), int(message_id)),
        ) as cur:
            row = await cur.fetchone()
            return dict(row) if row else None


# ─── autoreplies ─────────────────────────────────────────────────────────────


async def add_autoreply(
    *,
    owner_id: int,
    trigger: str,
    reply: str,
    match_type: str = "contains",
) -> int:
    async with aiosqlite.connect(_db_path()) as db:
        cur = await db.execute(
            """
            INSERT INTO autoreplies (owner_id, trigger, reply, match_type, created_at)
            VALUES (?, ?, ?, ?, ?)
            """,
            (int(owner_id), trigger, reply, match_type, now_iso()),
        )
        await db.commit()
        return int(cur.lastrowid or 0)


async def list_autoreplies(owner_id: int) -> list[dict]:
    async with aiosqlite.connect(_db_path()) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            "SELECT * FROM autoreplies WHERE owner_id = ? ORDER BY id",
            (int(owner_id),),
        ) as cur:
            return [dict(row) for row in await cur.fetchall()]


async def delete_autoreply(owner_id: int, autoreply_id: int) -> bool:
    async with aiosqlite.connect(_db_path()) as db:
        cur = await db.execute(
            "DELETE FROM autoreplies WHERE id = ? AND owner_id = ?",
            (int(autoreply_id), int(owner_id)),
        )
        await db.commit()
        return cur.rowcount > 0


async def toggle_autoreply(owner_id: int, autoreply_id: int) -> Optional[bool]:
    async with aiosqlite.connect(_db_path()) as db:
        async with db.execute(
            "SELECT is_enabled FROM autoreplies WHERE id = ? AND owner_id = ?",
            (int(autoreply_id), int(owner_id)),
        ) as cur:
            row = await cur.fetchone()
        if not row:
            return None
        new_state = 0 if row[0] else 1
        await db.execute(
            "UPDATE autoreplies SET is_enabled = ? WHERE id = ?",
            (new_state, int(autoreply_id)),
        )
        await db.commit()
        return bool(new_state)


async def clear_autoreplies(owner_id: int) -> int:
    async with aiosqlite.connect(_db_path()) as db:
        cur = await db.execute(
            "DELETE FROM autoreplies WHERE owner_id = ?",
            (int(owner_id),),
        )
        await db.commit()
        return cur.rowcount


# ─── per-user settings ───────────────────────────────────────────────────────

DEFAULT_SETTINGS: dict[str, Any] = {
    "capture_one_time": 1,
    "capture_all_media": 0,
    "track_edits": 1,
    "track_deletes": 1,
    "autoreply_enabled": 0,
    "autoreply_default": None,
}

BOOLEAN_SETTINGS = {
    "capture_one_time",
    "capture_all_media",
    "track_edits",
    "track_deletes",
    "autoreply_enabled",
}


async def get_settings_row(owner_id: int) -> dict:
    async with aiosqlite.connect(_db_path()) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            "SELECT * FROM user_settings WHERE owner_id = ?",
            (int(owner_id),),
        ) as cur:
            row = await cur.fetchone()
        if row is not None:
            return dict(row)
        await db.execute(
            "INSERT OR IGNORE INTO user_settings (owner_id) VALUES (?)",
            (int(owner_id),),
        )
        await db.commit()
    return {"owner_id": int(owner_id), **DEFAULT_SETTINGS}


async def update_setting(owner_id: int, key: str, value: Any) -> None:
    if key not in DEFAULT_SETTINGS:
        raise ValueError(f"Unknown setting: {key}")
    async with aiosqlite.connect(_db_path()) as db:
        await db.execute(
            "INSERT OR IGNORE INTO user_settings (owner_id) VALUES (?)",
            (int(owner_id),),
        )
        await db.execute(
            f"UPDATE user_settings SET {key} = ? WHERE owner_id = ?",
            (value, int(owner_id)),
        )
        await db.commit()


async def toggle_setting(owner_id: int, key: str) -> bool:
    if key not in BOOLEAN_SETTINGS:
        raise ValueError(f"Setting is not boolean: {key}")
    current = await get_settings_row(owner_id)
    new_value = 0 if current.get(key) else 1
    await update_setting(owner_id, key, new_value)
    return bool(new_value)
