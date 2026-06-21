"""
bot.py - Pyrogram Client setup and lifecycle management.
"""

from __future__ import annotations

import asyncio
import sys

from pyrogram import Client
from pyrogram.errors import AuthKeyUnregistered, AccessTokenExpired
from loguru import logger

import config
from handlers import register_handlers


# ─────────────────────────────────────────────────────────────────────────────
# Logging setup  (loguru replaces the default handler)
# ─────────────────────────────────────────────────────────────────────────────

logger.remove()
logger.add(
    sys.stdout,
    format=(
        "<green>{time:YYYY-MM-DD HH:mm:ss}</green> | "
        "<level>{level: <8}</level> | "
        "<cyan>{name}</cyan>:<cyan>{function}</cyan>:<cyan>{line}</cyan> — "
        "<level>{message}</level>"
    ),
    level="INFO",
    colorize=True,
)
logger.add(
    "logs/bot.log",
    rotation="50 MB",
    retention="14 days",
    compression="gz",
    level="DEBUG",
    format="{time:YYYY-MM-DD HH:mm:ss} | {level: <8} | {name}:{function}:{line} — {message}",
)


def create_app() -> Client:
    """Instantiate and configure the Pyrogram client."""
    app = Client(
        name=config.SESSION_NAME,
        api_id=config.API_ID,
        api_hash=config.API_HASH,
        bot_token=config.BOT_TOKEN,
        # Keep the session file next to the script
        workdir=".",
    )
    register_handlers(app)
    return app


async def run_bot():
    app = create_app()

    logger.info("Starting Telegram Caption Bot…")
    try:
        await app.start()
        me = await app.get_me()
        logger.info(f"Bot logged in as @{me.username} (id={me.id})")
        logger.info(
            f"Source: {config.SOURCE_CHANNEL} | Destination: {config.DESTINATION_CHANNEL}"
        )
        logger.info("Bot is ready. Listening for commands…")

        # Keep the event loop alive until interrupted
        await asyncio.Event().wait()

    except AuthKeyUnregistered:
        logger.critical("Invalid API credentials. Check API_ID / API_HASH / BOT_TOKEN.")
        sys.exit(1)
    except AccessTokenExpired:
        logger.critical("BOT_TOKEN is invalid or expired.")
        sys.exit(1)
    except KeyboardInterrupt:
        logger.info("Shutdown requested via KeyboardInterrupt.")
    except Exception as e:
        logger.critical(f"Unexpected error during bot startup: {e}")
        raise
    finally:
        logger.info("Stopping bot…")
        await app.stop()
        logger.info("Bot stopped.")
