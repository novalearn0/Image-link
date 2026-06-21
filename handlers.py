"""
handlers.py - Pyrogram command handlers for the Telegram bot.

Handlers:
    /start   - Welcome message
    /help    - Usage guide
    /status  - Runtime statistics
    /single  - Process a single post by link
    /range   - Batch process a range of message IDs
"""

from __future__ import annotations

import asyncio
import time
from collections import defaultdict
from datetime import date
from typing import Dict, List, Optional

from pyrogram import Client, filters
from pyrogram.errors import (
    FloodWait,
    MessageIdInvalid,
    ChannelPrivate,
    ChatAdminRequired,
    RPCError,
)
from pyrogram.types import Message, InputMediaPhoto
from loguru import logger

import config
from utils import (
    build_caption,
    build_post_link,
    extract_channel_id_from_link,
    format_duration,
    is_photo,
    is_photo_only_album,
    is_skippable_media,
    resolve_channel_ref,
    safe_int,
)

# ─────────────────────────────────────────────────────────────────────────────
# Runtime statistics (resets at bot restart; grouped by date for /status)
# ─────────────────────────────────────────────────────────────────────────────

class Stats:
    def __init__(self):
        self._processed: Dict[date, int] = defaultdict(int)
        self._skipped:   Dict[date, int] = defaultdict(int)
        self._errors:    Dict[date, int] = defaultdict(int)

    def _today(self) -> date:
        return date.today()

    def inc_processed(self, n: int = 1): self._processed[self._today()] += n
    def inc_skipped(self,   n: int = 1): self._skipped  [self._today()] += n
    def inc_errors(self,    n: int = 1): self._errors   [self._today()] += n

    @property
    def today_processed(self): return self._processed[self._today()]
    @property
    def today_skipped(self):   return self._skipped  [self._today()]
    @property
    def today_errors(self):    return self._errors   [self._today()]


stats = Stats()


# ─────────────────────────────────────────────────────────────────────────────
# Owner-only filter
# ─────────────────────────────────────────────────────────────────────────────

def owner_only(_, __, message: Message) -> bool:
    return message.from_user is not None and message.from_user.id == config.OWNER_ID

owner_filter = filters.create(owner_only)


# ─────────────────────────────────────────────────────────────────────────────
# Core processing helpers
# ─────────────────────────────────────────────────────────────────────────────

async def _send_with_retry(
    client: Client,
    *,
    photo,
    caption: str,
    parse_mode: str = "html",
) -> bool:
    """Send a single photo to DESTINATION_CHANNEL with retry logic."""
    for attempt in range(1, config.MAX_RETRIES + 1):
        try:
            await client.send_photo(
                chat_id=config.DESTINATION_CHANNEL,
                photo=photo,
                caption=caption,
                parse_mode=parse_mode,
            )
            return True
        except FloodWait as e:
            wait = e.value + config.FLOOD_WAIT_SLEEP
            logger.warning(f"FloodWait {e.value}s on attempt {attempt}. Sleeping {wait}s.")
            await asyncio.sleep(wait)
        except RPCError as e:
            logger.error(f"RPCError on attempt {attempt}: {e}")
            if attempt < config.MAX_RETRIES:
                await asyncio.sleep(config.RETRY_DELAY * attempt)
    return False


async def _send_album_with_retry(
    client: Client,
    media_group: List[InputMediaPhoto],
) -> bool:
    """Send a photo album to DESTINATION_CHANNEL with retry logic."""
    for attempt in range(1, config.MAX_RETRIES + 1):
        try:
            await client.send_media_group(
                chat_id=config.DESTINATION_CHANNEL,
                media=media_group,
            )
            return True
        except FloodWait as e:
            wait = e.value + config.FLOOD_WAIT_SLEEP
            logger.warning(f"FloodWait {e.value}s on attempt {attempt}. Sleeping {wait}s.")
            await asyncio.sleep(wait)
        except RPCError as e:
            logger.error(f"RPCError on attempt {attempt}: {e}")
            if attempt < config.MAX_RETRIES:
                await asyncio.sleep(config.RETRY_DELAY * attempt)
    return False


async def _process_single_message(
    client: Client,
    message: Message,
    channel_ref,
) -> tuple[str, Optional[str]]:
    """
    Process one Message object.

    Returns:
        ("processed", None)          on success
        ("skipped",   reason_str)    when intentionally skipped
        ("error",     error_str)     on failure
    """
    msg_id = message.id

    # ── Albums (media groups) ─────────────────────────────────────────────────
    if message.media_group_id:
        # Handled separately via _process_album; skip here to avoid double processing.
        return "album_member", None

    # ── Single photo ──────────────────────────────────────────────────────────
    if is_photo(message):
        link    = build_post_link(channel_ref, msg_id)
        caption = build_caption(message.caption, link)

        try:
            # Download photo bytes then upload (do NOT forward)
            photo_bytes = await client.download_media(message, in_memory=True)
        except Exception as e:
            logger.error(f"[{msg_id}] Download failed: {e}")
            return "error", str(e)

        ok = await _send_with_retry(client, photo=photo_bytes, caption=caption)
        if ok:
            logger.info(f"[{msg_id}] ✅ Photo processed → dest")
            return "processed", None
        else:
            logger.error(f"[{msg_id}] ❌ Failed to upload after retries")
            return "error", "upload failed after retries"

    # ── Skippable media ───────────────────────────────────────────────────────
    if is_skippable_media(message):
        media_type = _detect_media_type(message)
        logger.info(f"[{msg_id}] ⏭ Skipped ({media_type})")
        return "skipped", media_type

    # ── Text-only or unknown ──────────────────────────────────────────────────
    logger.debug(f"[{msg_id}] ⏭ Skipped (no relevant media)")
    return "skipped", "no_media"


async def _process_album(
    client: Client,
    messages: List[Message],
    channel_ref,
) -> tuple[str, Optional[str]]:
    """
    Process a complete media-group (album).
    Only processes if ALL items are photos.
    """
    if not is_photo_only_album(messages):
        logger.info(
            f"[album:{messages[0].media_group_id}] ⏭ Skipped (contains non-photo media)"
        )
        return "skipped", "album_mixed_media"

    # Use the first message's ID for the post link
    first_msg = messages[0]
    link      = build_post_link(channel_ref, first_msg.id)

    # Build InputMediaPhoto list; caption goes on the first item only
    media_group: List[InputMediaPhoto] = []
    for i, msg in enumerate(messages):
        caption = build_caption(msg.caption, link) if i == 0 else (msg.caption or "")
        try:
            photo_bytes = await client.download_media(msg, in_memory=True)
        except Exception as e:
            logger.error(f"[album:{first_msg.media_group_id}] Download error on msg {msg.id}: {e}")
            return "error", str(e)

        media_group.append(
            InputMediaPhoto(media=photo_bytes, caption=caption, parse_mode="html")
        )

    ok = await _send_album_with_retry(client, media_group)
    if ok:
        logger.info(f"[album:{first_msg.media_group_id}] ✅ Album processed ({len(messages)} photos)")
        return "processed", None
    return "error", "album upload failed"


def _detect_media_type(message: Message) -> str:
    if message.video:       return "video"
    if message.document:    return "document"
    if message.audio:       return "audio"
    if message.voice:       return "voice"
    if message.sticker:     return "sticker"
    if message.animation:   return "animation"
    if message.video_note:  return "video_note"
    if message.poll:        return "poll"
    if message.contact:     return "contact"
    if message.location:    return "location"
    if message.game:        return "game"
    return "unknown"


# ─────────────────────────────────────────────────────────────────────────────
# /start
# ─────────────────────────────────────────────────────────────────────────────

def register_handlers(app: Client):

    @app.on_message(filters.command("start") & owner_filter)
    async def cmd_start(client: Client, message: Message):
        await message.reply_text(
            "👋 **Telegram Caption Bot is running!**\n\n"
            "Use /help to see available commands.",
            parse_mode="markdown",
        )

    # ─────────────────────────────────────────────────────────────────────────
    # /help
    # ─────────────────────────────────────────────────────────────────────────

    @app.on_message(filters.command("help") & owner_filter)
    async def cmd_help(client: Client, message: Message):
        src  = config.SOURCE_CHANNEL or "_not set — pass one to /range_"
        dest = config.DESTINATION_CHANNEL
        await message.reply_text(
            "📖 **Bot Commands**\n\n"
            f"**Default source channel:** `{src}`\n"
            f"**Destination channel:** `{dest}`\n\n"
            "━━━━━━━━━━━━━━━━━━━━━\n"
            "**Single post:**\n"
            "`/single https://t.me/c/CHANNEL_ID/MSG_ID`\n\n"
            "**Range of posts (default channel):**\n"
            "`/range START_ID END_ID`\n"
            "_Example:_ `/range 1000 2000`\n\n"
            "**Range of posts (any channel):**\n"
            "`/range CHANNEL START_ID END_ID`\n"
            "_Example:_ `/range @somechannel 1000 2000`\n"
            "_Example:_ `/range https://t.me/c/2611102464 1000 2000`\n\n"
            "━━━━━━━━━━━━━━━━━━━━━\n"
            "📌 Only **photo** posts are processed.\n"
            "📌 Albums with non-photo media are skipped.\n"
            "📌 Posts are re-uploaded (not forwarded).",
            parse_mode="markdown",
        )

    # ─────────────────────────────────────────────────────────────────────────
    # /status
    # ─────────────────────────────────────────────────────────────────────────

    @app.on_message(filters.command("status") & owner_filter)
    async def cmd_status(client: Client, message: Message):
        me = await client.get_me()
        src = config.SOURCE_CHANNEL or "not set"
        await message.reply_text(
            "📊 **Bot Status**\n\n"
            f"🤖 Bot: @{me.username}\n"
            f"📥 Default source: `{src}`\n"
            f"📤 Destination: `{config.DESTINATION_CHANNEL}`\n\n"
            "━━━━━━━━━━━━━━━━━━━━━\n"
            f"✅ Processed today: **{stats.today_processed}**\n"
            f"⏭ Skipped today:   **{stats.today_skipped}**\n"
            f"❌ Errors today:    **{stats.today_errors}**",
            parse_mode="markdown",
        )

    # ─────────────────────────────────────────────────────────────────────────
    # /single
    # ─────────────────────────────────────────────────────────────────────────

    @app.on_message(filters.command("single") & owner_filter)
    async def cmd_single(client: Client, message: Message):
        parts = message.text.strip().split(maxsplit=1)
        if len(parts) < 2:
            await message.reply_text(
                "❌ Usage: `/single POST_LINK`\n"
                "Example: `/single https://t.me/c/2611102464/124425`",
                parse_mode="markdown",
            )
            return

        link = parts[1].strip()
        parsed = extract_channel_id_from_link(link)
        if not parsed:
            await message.reply_text(
                "❌ Could not parse the post link. Make sure it looks like:\n"
                "`https://t.me/c/CHANNEL_ID/MSG_ID`  or\n"
                "`https://t.me/username/MSG_ID`",
                parse_mode="markdown",
            )
            return

        channel_ref, msg_id = parsed
        status_msg = await message.reply_text(f"⏳ Fetching message `{msg_id}`…", parse_mode="markdown")

        # ── Fetch target message ───────────────────────────────────────────
        try:
            target: Message = await client.get_messages(channel_ref, msg_id)
        except (MessageIdInvalid, ChannelPrivate, ChatAdminRequired) as e:
            await status_msg.edit_text(f"❌ Cannot fetch message: `{e}`", parse_mode="markdown")
            stats.inc_errors()
            return
        except FloodWait as e:
            await status_msg.edit_text(
                f"⏳ Rate limited. Waiting {e.value}s then retrying…", parse_mode="markdown"
            )
            await asyncio.sleep(e.value + config.FLOOD_WAIT_SLEEP)
            try:
                target = await client.get_messages(channel_ref, msg_id)
            except Exception as e2:
                await status_msg.edit_text(f"❌ Retry failed: `{e2}`", parse_mode="markdown")
                stats.inc_errors()
                return
        except Exception as e:
            await status_msg.edit_text(f"❌ Unexpected error: `{e}`", parse_mode="markdown")
            stats.inc_errors()
            return

        if target.empty:
            await status_msg.edit_text("❌ Message not found (deleted or inaccessible).")
            stats.inc_errors()
            return

        # ── Handle albums ──────────────────────────────────────────────────
        if target.media_group_id:
            await status_msg.edit_text("📦 Detected album. Fetching all items…")
            album_msgs = []
            async for album_msg in client.get_media_group(channel_ref, msg_id):
                album_msgs.append(album_msg)
            album_msgs.sort(key=lambda m: m.id)

            result, detail = await _process_album(client, album_msgs, channel_ref)
        else:
            result, detail = await _process_single_message(client, target, channel_ref)

        # ── Report back ────────────────────────────────────────────────────
        if result == "processed":
            stats.inc_processed()
            await status_msg.edit_text(
                f"✅ Done! Message `{msg_id}` processed and sent to destination.",
                parse_mode="markdown",
            )
        elif result == "skipped":
            stats.inc_skipped()
            await status_msg.edit_text(
                f"⏭ Message `{msg_id}` skipped — **{detail}**.\n"
                "Only photo posts are processed.",
                parse_mode="markdown",
            )
        else:
            stats.inc_errors()
            await status_msg.edit_text(
                f"❌ Error processing message `{msg_id}`: `{detail}`",
                parse_mode="markdown",
            )

    # ─────────────────────────────────────────────────────────────────────────
    # /range
    # ─────────────────────────────────────────────────────────────────────────

    @app.on_message(filters.command("range") & owner_filter)
    async def cmd_range(client: Client, message: Message):
        parts = message.text.strip().split()

        # ── Parse arguments: /range START END   OR   /range CHANNEL START END ──
        if len(parts) == 3:
            # No channel given — fall back to default SOURCE_CHANNEL
            if config.SOURCE_CHANNEL is None:
                await message.reply_text(
                    "❌ No default channel configured.\n"
                    "Usage: `/range CHANNEL START_ID END_ID`\n"
                    "_Example:_ `/range @somechannel 1000 2000`",
                    parse_mode="markdown",
                )
                return
            channel_ref = config.SOURCE_CHANNEL
            start_raw, end_raw = parts[1], parts[2]
        elif len(parts) == 4:
            channel_ref = resolve_channel_ref(parts[1])
            if channel_ref is None:
                await message.reply_text(
                    f"❌ Could not parse channel: `{parts[1]}`\n"
                    "Use a channel link, `@username`, or numeric ID.",
                    parse_mode="markdown",
                )
                return
            start_raw, end_raw = parts[2], parts[3]
        else:
            await message.reply_text(
                "❌ Usage:\n"
                "`/range START_ID END_ID` _(uses default channel)_\n"
                "`/range CHANNEL START_ID END_ID` _(any channel)_\n\n"
                "_Example:_ `/range 1000 2000`\n"
                "_Example:_ `/range @somechannel 1000 2000`",
                parse_mode="markdown",
            )
            return

        start_id = safe_int(start_raw, "START_ID")
        end_id   = safe_int(end_raw, "END_ID")

        if start_id is None or end_id is None:
            await message.reply_text("❌ START_ID and END_ID must be integers.")
            return

        if start_id > end_id:
            start_id, end_id = end_id, start_id   # auto-correct order

        total_range = end_id - start_id + 1
        status_msg = await message.reply_text(
            f"⏳ Starting range processing on `{channel_ref}`: "
            f"`{start_id}` → `{end_id}` "
            f"({total_range} IDs)\n\n_This may take a while for large ranges…_",
            parse_mode="markdown",
        )

        # ── Counters ───────────────────────────────────────────────────────
        count_processed = 0
        count_skipped   = 0
        count_errors    = 0
        skip_breakdown: Dict[str, int] = defaultdict(int)

        start_time = time.monotonic()

        # ── We iterate in batches for efficiency ───────────────────────────
        # Collect IDs in this range, fetch in BATCH_SIZE chunks
        all_ids = list(range(start_id, end_id + 1))
        seen_album_ids: set[str] = set()   # track processed media_group_ids

        progress_interval = max(1, total_range // 20)  # update ~every 5%

        for batch_start in range(0, len(all_ids), config.BATCH_SIZE):
            batch_ids = all_ids[batch_start: batch_start + config.BATCH_SIZE]

            # Fetch batch
            for attempt in range(1, config.MAX_RETRIES + 1):
                try:
                    messages_batch: List[Message] = await client.get_messages(
                        channel_ref, batch_ids
                    )
                    break
                except FloodWait as e:
                    wait = e.value + config.FLOOD_WAIT_SLEEP
                    logger.warning(f"FloodWait during batch fetch. Sleeping {wait}s.")
                    await asyncio.sleep(wait)
                except Exception as e:
                    logger.error(f"Batch fetch error (attempt {attempt}): {e}")
                    if attempt == config.MAX_RETRIES:
                        count_errors += len(batch_ids)
                        messages_batch = []
                    else:
                        await asyncio.sleep(config.RETRY_DELAY * attempt)

            # Sort by ID ascending for sequential processing
            messages_batch.sort(key=lambda m: m.id)

            for msg in messages_batch:
                if msg is None or msg.empty:
                    logger.debug(f"Message empty/deleted, skipping.")
                    count_skipped += 1
                    skip_breakdown["deleted"] += 1
                    continue

                msg_id = msg.id

                # ── Album handling ─────────────────────────────────────────
                if msg.media_group_id:
                    grp_id = msg.media_group_id
                    if grp_id in seen_album_ids:
                        continue   # already processed this album
                    seen_album_ids.add(grp_id)

                    # Fetch all members of this album
                    try:
                        album_msgs = []
                        async for am in client.get_media_group(channel_ref, msg_id):
                            album_msgs.append(am)
                        album_msgs.sort(key=lambda m: m.id)
                    except Exception as e:
                        logger.error(f"[album:{grp_id}] Failed to fetch group: {e}")
                        count_errors += 1
                        continue

                    result, detail = await _process_album(client, album_msgs, channel_ref)

                    if result == "processed":
                        count_processed += len(album_msgs)
                        stats.inc_processed(len(album_msgs))
                    elif result == "error":
                        count_errors += 1
                        stats.inc_errors()
                    else:
                        count_skipped += len(album_msgs)
                        skip_breakdown[detail or "album_skipped"] += len(album_msgs)
                        stats.inc_skipped(len(album_msgs))

                    continue   # skip per-message logic below

                # ── Single message ─────────────────────────────────────────
                result, detail = await _process_single_message(
                    client, msg, channel_ref
                )
                if result == "processed":
                    count_processed += 1
                    stats.inc_processed()
                elif result == "error":
                    count_errors += 1
                    stats.inc_errors()
                elif result != "album_member":
                    count_skipped += 1
                    skip_breakdown[detail or "other"] += 1
                    stats.inc_skipped()

                # ── Progress update ────────────────────────────────────────
                processed_so_far = batch_start + messages_batch.index(msg) + 1
                if processed_so_far % progress_interval == 0:
                    pct = processed_so_far / total_range * 100
                    elapsed = time.monotonic() - start_time
                    try:
                        await status_msg.edit_text(
                            f"⏳ Progress: {processed_so_far}/{total_range} ({pct:.0f}%)\n"
                            f"✅ {count_processed} | ⏭ {count_skipped} | ❌ {count_errors}\n"
                            f"⏱ Elapsed: {format_duration(elapsed)}",
                            parse_mode="markdown",
                        )
                    except Exception:
                        pass   # status edit is non-critical

        # ── Final report ───────────────────────────────────────────────────
        elapsed = time.monotonic() - start_time

        # Build skip breakdown string
        skip_lines = "\n".join(
            f"    ⏭ {k}: {v}" for k, v in sorted(skip_breakdown.items(), key=lambda x: -x[1])
        ) or "    (none)"

        report = (
            f"✅ **Range Processing Complete**\n\n"
            f"📋 Range: `{start_id}` — `{end_id}`\n\n"
            f"✅ Images Processed: **{count_processed}**\n"
            f"⏭ Skipped: **{count_skipped}**\n"
            f"{skip_lines}\n"
            f"❌ Errors: **{count_errors}**\n\n"
            f"⏱ Time Taken: **{format_duration(elapsed)}**"
        )

        try:
            await status_msg.edit_text(report, parse_mode="markdown")
        except Exception:
            await message.reply_text(report, parse_mode="markdown")

        logger.info(
            f"Range {start_id}-{end_id} done | "
            f"processed={count_processed} skipped={count_skipped} "
            f"errors={count_errors} time={format_duration(elapsed)}"
        )
