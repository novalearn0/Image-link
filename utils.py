"""
utils.py - Utility functions: link generation, caption building, media checks
"""

from __future__ import annotations

import re
from typing import Optional

from pyrogram.types import Message
from loguru import logger

from config import CAPTION_LIMIT

# ── Separator injected between original caption and the link block ───────────
LINK_SEPARATOR = "\n\n----------------\n🔗 Original Post:\n"

# Characters consumed by the separator + URL (rough upper bound for a t.me link)
_LINK_BLOCK_MAX = len(LINK_SEPARATOR) + 60   # 60 chars is plenty for any t.me URL


# ─────────────────────────────────────────────────────────────────────────────
# Link generation
# ─────────────────────────────────────────────────────────────────────────────

def build_post_link(channel_id: int | str, message_id: int) -> str:
    """
    Generate the correct Telegram deep-link for a post.

    Public  channel  → https://t.me/username/MESSAGE_ID
    Private channel  → https://t.me/c/INTERNAL_ID/MESSAGE_ID
    """
    if isinstance(channel_id, str):
        # Strip leading '@' for the URL
        username = channel_id.lstrip("@")
        return f"https://t.me/{username}/{message_id}"

    # Numeric ID:  Pyrogram uses full negative IDs like -1001234567890.
    # The "internal" id used in t.me/c/ URLs strips the leading -100.
    full_id = abs(channel_id)
    internal_id = int(str(full_id)[3:]) if str(full_id).startswith("100") else full_id
    return f"https://t.me/c/{internal_id}/{message_id}"


def resolve_channel_ref(raw: str) -> int | str | None:
    """
    Resolve a CHANNEL argument (as typed by the owner, e.g. in /range) into
    a Pyrogram-ready chat reference (int for private/numeric, str for username).

    Accepts:
        https://t.me/c/2611102464/124425  → -1002611102464  (post link, msg id ignored)
        https://t.me/c/2611102464         → -1002611102464  (channel-only link)
        https://t.me/somechannel          → "@somechannel"
        -1002611102464                    → -1002611102464
        2611102464                        → -1002611102464  (bare internal id)
        @somechannel / somechannel        → "@somechannel"

    Returns None if `raw` can't be parsed into anything usable.
    """
    raw = raw.strip()
    if not raw:
        return None

    # t.me/c/<id>[/<msg>]  → private channel link
    m = re.match(r"https?://t\.me/c/(\d+)(?:/\d+)?", raw)
    if m:
        return int(f"-100{m.group(1)}")

    # t.me/<username>[/<msg>]  → public channel link
    m = re.match(r"https?://t\.me/([A-Za-z][A-Za-z0-9_]{3,})(?:/\d+)?", raw)
    if m:
        return f"@{m.group(1)}"

    # Already a full negative internal id, e.g. -1002611102464
    if re.fullmatch(r"-100\d+", raw):
        return int(raw)

    # Bare numeric → treat as internal id, prepend -100
    if raw.lstrip("-").isdigit():
        numeric = int(raw)
        return int(f"-100{numeric}") if numeric > 0 else numeric

    # Username, with or without leading @
    if re.fullmatch(r"@?[A-Za-z][A-Za-z0-9_]{3,}", raw):
        return raw if raw.startswith("@") else f"@{raw}"

    return None


def extract_channel_id_from_link(link: str) -> tuple[str | int, int] | None:
    """
    Parse a Telegram post link and return (channel_ref, message_id).

    Supported formats:
        https://t.me/c/2611102464/124425   → private (-1002611102464, 124425)
        https://t.me/channelusername/123   → public ("@channelusername", 123)
        https://t.me/username/123          → public ("@username", 123)
    """
    # Private channel  (t.me/c/<id>/<msg>)
    m = re.match(r"https?://t\.me/c/(\d+)/(\d+)", link.strip())
    if m:
        internal_id = int(m.group(1))
        message_id  = int(m.group(2))
        channel_ref = int(f"-100{internal_id}")
        return channel_ref, message_id

    # Public channel  (t.me/<username>/<msg>)
    m = re.match(r"https?://t\.me/([A-Za-z][A-Za-z0-9_]{3,})/(\d+)", link.strip())
    if m:
        username   = f"@{m.group(1)}"
        message_id = int(m.group(2))
        return username, message_id

    return None


# ─────────────────────────────────────────────────────────────────────────────
# Caption helpers
# ─────────────────────────────────────────────────────────────────────────────

def build_caption(original_caption: Optional[str], post_link: str) -> str:
    """
    Compose the final caption, respecting Telegram's 1024-char limit.

    Layout:
        <original caption>

        ----------------
        🔗 Original Post:
        <post_link>

    If there is no original caption:
        🔗 Original Post:
        <post_link>
    """
    link_block = f"🔗 Original Post:\n{post_link}"

    if not original_caption or not original_caption.strip():
        result = link_block
    else:
        result = original_caption + LINK_SEPARATOR + post_link

    # Enforce caption limit
    if len(result) > CAPTION_LIMIT:
        result = _truncate_caption(original_caption or "", post_link)

    return result


def _truncate_caption(original: str, post_link: str) -> str:
    """
    Truncate the original caption so that, together with the link block,
    the total stays within CAPTION_LIMIT.  Appends '…' to signal truncation.
    """
    link_block     = LINK_SEPARATOR + post_link
    available      = CAPTION_LIMIT - len(link_block) - 1   # -1 for '…'
    if available < 0:
        # Link block itself exceeds limit — just return the link block (truncated)
        return link_block[:CAPTION_LIMIT]
    truncated_orig = original[:available] + "…"
    return truncated_orig + link_block


# ─────────────────────────────────────────────────────────────────────────────
# Media-type checks
# ─────────────────────────────────────────────────────────────────────────────

def is_photo(message: Message) -> bool:
    return message.photo is not None


def is_skippable_media(message: Message) -> bool:
    """
    Returns True if the message contains media we must skip:
    video, document, audio, voice, sticker, animation, poll, etc.
    """
    return any([
        message.video,
        message.document,
        message.audio,
        message.voice,
        message.sticker,
        message.animation,
        message.video_note,
        message.poll,
        message.contact,
        message.location,
        message.venue,
        message.game,
        message.web_page,
    ])


def is_photo_only_album(messages: list[Message]) -> bool:
    """
    An album (media group) is processable only when EVERY item is a photo.
    """
    for msg in messages:
        if not is_photo(msg):
            return False
    return bool(messages)


# ─────────────────────────────────────────────────────────────────────────────
# Misc
# ─────────────────────────────────────────────────────────────────────────────

def format_duration(seconds: float) -> str:
    """Human-readable duration string."""
    if seconds < 60:
        return f"{seconds:.1f}s"
    minutes, secs = divmod(int(seconds), 60)
    if minutes < 60:
        return f"{minutes}m {secs}s"
    hours, mins = divmod(minutes, 60)
    return f"{hours}h {mins}m {secs}s"


def safe_int(value: str, label: str = "value") -> Optional[int]:
    try:
        return int(value)
    except (ValueError, TypeError):
        logger.warning(f"Could not parse {label}: {value!r}")
        return None
