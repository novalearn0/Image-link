"""
config.py - Environment configuration and validation
"""

import os
from dotenv import load_dotenv
from loguru import logger

load_dotenv()


def _get_required(key: str) -> str:
    value = os.getenv(key)
    if not value:
        raise EnvironmentError(f"Required environment variable '{key}' is missing.")
    return value.strip()


def _get_int(key: str) -> int:
    raw = _get_required(key)
    try:
        return int(raw)
    except ValueError:
        raise EnvironmentError(f"Environment variable '{key}' must be an integer, got: {raw!r}")


def _parse_channel(raw: str) -> int | str:
    """
    Return an int (negative for private) or a username string for public channels.
    Accepts:
        -1001234567890   → private channel (int)
        1234567890       → will be negated → -1001234567890
        @channelusername → public channel (str)
        channelusername  → public channel (str, @ prepended)
    """
    raw = raw.strip()
    if raw.startswith("-100"):
        return int(raw)
    if raw.lstrip("-").isdigit():
        numeric = int(raw)
        # Ensure proper negative format for private channels
        if numeric > 0:
            return int(f"-100{numeric}")
        return numeric
    # Username
    return raw if raw.startswith("@") else f"@{raw}"


# ── Core credentials ────────────────────────────────────────────────────────
API_ID: int = _get_int("API_ID")
API_HASH: str = _get_required("API_HASH")
BOT_TOKEN: str = _get_required("BOT_TOKEN")
OWNER_ID: int = _get_int("OWNER_ID")

# ── Channel config ───────────────────────────────────────────────────────────
# SOURCE_CHANNEL is now OPTIONAL. If set, it acts as the default channel for
# /range when no channel is passed explicitly. DESTINATION_CHANNEL is still
# required since the bot always needs somewhere to send processed posts.
_SOURCE_RAW = os.getenv("SOURCE_CHANNEL")
_DEST_RAW   = _get_required("DESTINATION_CHANNEL")

SOURCE_CHANNEL: int | str | None = _parse_channel(_SOURCE_RAW) if _SOURCE_RAW and _SOURCE_RAW.strip() else None
DESTINATION_CHANNEL = _parse_channel(_DEST_RAW)

# ── Telegram limits ──────────────────────────────────────────────────────────
CAPTION_LIMIT = 1024   # Telegram's max caption length in characters
MESSAGE_LIMIT = 4096   # Telegram's max message text length

# ── Performance tuning ───────────────────────────────────────────────────────
BATCH_SIZE          = 100   # Messages fetched per iter_messages call
FLOOD_WAIT_SLEEP    = 5     # Extra seconds added on top of flood-wait duration
MAX_RETRIES         = 3     # Retry attempts for recoverable errors
RETRY_DELAY         = 2     # Base delay (seconds) between retries

# ── Session file name (Pyrogram) ─────────────────────────────────────────────
SESSION_NAME = "telegram_bot_session"

logger.info(
    f"Config loaded | source={SOURCE_CHANNEL} | dest={DESTINATION_CHANNEL}"
)
