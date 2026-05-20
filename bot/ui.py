"""HTML formatting helpers and reusable UI snippets.

The bot uses ``ParseMode.HTML`` everywhere. Always escape user content via
``safe()`` before injecting into templates.
"""

from __future__ import annotations

from html import escape
from typing import Any, Optional

# ─── primitive formatters ────────────────────────────────────────────────────


def safe(value: Any) -> str:
    """HTML-escape a value (returns empty string for None)."""

    if value is None:
        return ""
    return escape(str(value))


def b(value: Any) -> str:
    return f"<b>{safe(value)}</b>"


def i(value: Any) -> str:
    return f"<i>{safe(value)}</i>"


def u(value: Any) -> str:
    return f"<u>{safe(value)}</u>"


def s(value: Any) -> str:
    return f"<s>{safe(value)}</s>"


def code(value: Any) -> str:
    return f"<code>{safe(value)}</code>"


def pre(value: Any, lang: str = "") -> str:
    body = safe(value)
    if lang:
        return f'<pre><code class="language-{escape(lang)}">{body}</code></pre>'
    return f"<pre>{body}</pre>"


def spoiler(value: Any) -> str:
    return f"<tg-spoiler>{safe(value)}</tg-spoiler>"


def quote(value: Any, *, expandable: bool = False) -> str:
    tag = "blockquote expandable" if expandable else "blockquote"
    return f"<{tag}>{safe(value)}</blockquote>"


def link(text: Any, url: str) -> str:
    return f'<a href="{escape(url, quote=True)}">{safe(text)}</a>'


def user_mention(name: Any, user_id: int) -> str:
    return f'<a href="tg://user?id={int(user_id)}">{safe(name)}</a>'


# ─── reusable layout ─────────────────────────────────────────────────────────

THICK = "━━━━━━━━━━━━━━━━━━━━"
THIN = "──────────────────"
DOTS = "·  ·  ·  ·  ·  ·  ·"


def header(title: str, emoji: str = "") -> str:
    """Bold title followed by a thick divider."""

    prefix = f"{emoji} " if emoji else ""
    return f"{prefix}{b(title)}\n{THICK}"


def section(title: str) -> str:
    """A subtle in-message section divider."""

    return f"\n{THIN}\n{b(title)}"


def kv(key: str, value: Any) -> str:
    """``key: value`` row with bold key and plain (escaped) value."""

    return f"{b(key)}: {safe(value)}"


def kv_code(key: str, value: Any) -> str:
    """``key: <code>value</code>`` row."""

    return f"{b(key)}: {code(value)}"


def kv_italic(key: str, value: Any) -> str:
    return f"{b(key)}: {i(value)}"


def bullet(text: str) -> str:
    return f"•  {text}"


# ─── content-type → human label ──────────────────────────────────────────────

CONTENT_LABELS: dict[str, str] = {
    "text": "текст",
    "photo": "фото",
    "video": "видео",
    "video_note": "видеокружок",
    "voice": "голосовое",
    "audio": "аудио",
    "animation": "GIF",
    "sticker": "стикер",
    "document": "документ",
    "contact": "контакт",
    "location": "локация",
    "poll": "опрос",
    "dice": "дайс",
    "story": "история",
}


def content_label(kind: Optional[str]) -> str:
    if not kind:
        return "неизвестно"
    return CONTENT_LABELS.get(kind, kind)
