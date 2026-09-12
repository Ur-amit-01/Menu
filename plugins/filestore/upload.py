
import asyncio
import logging
import time

from pyrogram import Client, filters
from pyrogram.errors import FloodWait
from pyrogram.types import (
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Message,
)

from config import BACKUP_CHANNEL, LOG_CHANNEL
from plugins.helper.filters import admin_filter
from plugins.helper.media import extract_media
from plugins.filestore.linking import save_link, build_deep_link
from plugins.filestore.admin_settings import AWAITING as SETTINGS_AWAITING

logger = logging.getLogger(__name__)

# admin_id -> list of collected "pending items" while a batch session is open.
# A pending item is just a pointer (src_chat_id/src_message_id) to a message
# that hasn't been copied anywhere yet.
BATCH_SESSIONS: dict = {}

# admin_id -> asyncio.Lock. Every collect-then-append happens under this
# lock, and the "✅ Yes, finish" button acquires it too, so a batch can
# never be finalized while a file sent moments earlier is still mid-append
# — otherwise that file would silently miss the link.
_ADMIN_LOCKS: dict = {}


def _lock_for(admin_id: int) -> asyncio.Lock:
    lock = _ADMIN_LOCKS.get(admin_id)
    if lock is None:
        lock = asyncio.Lock()
        _ADMIN_LOCKS[admin_id] = lock
    return lock


# admin_id -> list of pending items awaiting a Yes/No link-generation confirmation
PENDING_SINGLE: dict = {}

# admin_id -> the "N file(s) received in batch" status Message. Replaced
# (deleted + resent) each time a new group of files arrives, so there's
# always exactly one counter message and it's always the most recent.
_BATCH_STATUS_MSG: dict = {}

# A batch session auto-expires if no new file arrives within this many
# seconds — after that, /batch must be run again.
_BATCH_TIMEOUT_SECONDS = 5 * 60

# admin_id -> asyncio.Task counting down the inactivity timeout for an open
# batch session. Cancelled and re-armed every time a new file/album is
# appended; if it ever fires uninterrupted, the batch is considered
# abandoned and gets auto-expired.
_BATCH_TIMEOUT_TASKS: dict = {}


def _cancel_batch_timeout(admin_id: int):
    task = _BATCH_TIMEOUT_TASKS.pop(admin_id, None)
    if task and not task.done():
        task.cancel()


def _arm_batch_timeout(client, admin_id: int):
    """(Re)starts the inactivity countdown for admin_id's batch session."""
    _cancel_batch_timeout(admin_id)
    _BATCH_TIMEOUT_TASKS[admin_id] = asyncio.create_task(
        _batch_timeout_worker(client, admin_id)
    )


async def _batch_timeout_worker(client, admin_id: int):
    try:
        await asyncio.sleep(_BATCH_TIMEOUT_SECONDS)
    except asyncio.CancelledError:
        return  # a new file arrived (or the batch finished) — timer was reset/cleared

    async with _lock_for(admin_id):
        if admin_id not in BATCH_SESSIONS:
            return  # already finished or cancelled by something else
        BATCH_SESSIONS.pop(admin_id, None)
        status_msg = _BATCH_STATUS_MSG.pop(admin_id, None)

    if status_msg:
        try:
            await status_msg.delete()
        except Exception:
            pass
    try:
        await client.send_message(
            admin_id,
            "<b><blockquote>⏱ Batch Expired</blockquote>\n"
            "No files received for 5 minutes, so the session was closed.\n"
            "Run /batch again to start a new one.</b>",
        )
    except Exception:
        pass
    _BATCH_TIMEOUT_TASKS.pop(admin_id, None)

# Delay between successive copies into BACKUP_CHANNEL. Telegram enforces a
# roughly 1-message/second limit per chat; firing copies back-to-back into
# the same channel is exactly what trips FloodWait mid-batch.
_COPY_SPACING_SECONDS = 0.4

# media_group_id -> claimed_at timestamp. Telegram fires the handler once per
# item in an album; we only want to process the album once, on whichever
# item arrives first.
_CLAIMED_GROUPS: dict = {}
_GROUP_CLAIM_TTL = 30  # seconds; comfortably longer than an album can take to arrive/process

MEDIA_FILTER = (
    filters.document | filters.video | filters.photo | filters.audio
    | filters.voice | filters.animation | filters.video_note | filters.sticker
)


def _not_awaiting_settings_reply(_, __, message: Message) -> bool:
    """Blocks handle_admin_media from swallowing a photo (or any other
    media) that was actually meant for an in-progress /setting field —
    most notably 'start_photo', where an admin sending the picture should
    set it as the start photo, not get an upload/link-generation prompt."""
    return not (
        message.from_user is not None
        and message.from_user.id in SETTINGS_AWAITING
    )


def _is_batch_active(_, __, message: Message) -> bool:
    """Only true once /batch has opened a session for this admin — this is
    what lets a plain text message be treated as batch content (a caption-
    only "file", e.g. a note between a PDF and an image) instead of being
    ignored or misread as ordinary chat."""
    return message.from_user is not None and message.from_user.id in BATCH_SESSIONS


def _not_a_command(_, __, message: Message) -> bool:
    """Lets /cancel, /done, or any other command through untouched instead
    of swallowing it as batch content."""
    return not (message.text or "").startswith("/")


# Plain text messages are only ever swept into a batch — never into the
# single-item direct-link flow — so an admin's ordinary chat with the bot
# outside of /batch is never mistaken for file-store content.
TEXT_FILTER = (
    filters.text
    & filters.create(_not_a_command)
    & filters.create(_not_awaiting_settings_reply)
    & filters.create(_is_batch_active)
)


def _claim_group(gid: str) -> bool:
    """Returns True if this call is the one that should process album `gid`."""
    now = time.time()
    for g, ts in list(_CLAIMED_GROUPS.items()):
        if now - ts > _GROUP_CLAIM_TTL:
            _CLAIMED_GROUPS.pop(g, None)
    if gid in _CLAIMED_GROUPS:
        return False
    _CLAIMED_GROUPS[gid] = now
    return True


def _pending_item(message: Message) -> dict:
    """A lightweight reference to a not-yet-backed-up message. Nothing is
    copied anywhere at this point — that only happens in _finalize_entries()."""
    return {
        "src_chat_id": message.chat.id,
        "src_message_id": message.id,
        "media_group_id": message.media_group_id,
    }


def _group_pending(items: list) -> list:
    """Group consecutive pending items sharing a media_group_id into sub-lists."""
    groups, current, current_gid = [], [], object()
    for item in items:
        gid = item.get("media_group_id")
        if gid and gid == current_gid:
            current.append(item)
        else:
            if current:
                groups.append(current)
            current = [item]
            current_gid = gid
    if current:
        groups.append(current)
    return groups


async def _copy_with_flood_retry(coro_fn, *args, max_retries: int = 4, **kwargs):
    """
    Awaits coro_fn(*args, **kwargs), retrying on FloodWait instead of letting
    it bubble up as a generic failure. Pyrogram already auto-sleeps for short
    FloodWaits (below its sleep_threshold), but for a real batch the wait
    time between successive copies into the same channel can stack up past
    that threshold — this catches those explicitly rather than treating a
    rate limit as "the source message was deleted".
    """
    last_err = None
    for attempt in range(max_retries + 1):
        try:
            return await coro_fn(*args, **kwargs)
        except FloodWait as e:
            last_err = e
            wait = e.value + 1
            logger.warning(f"FloodWait: sleeping {wait}s (attempt {attempt + 1}/{max_retries})")
            await asyncio.sleep(wait)
    raise last_err


async def _finalize_entries(client, items: list) -> tuple:
    """
    The ONLY place media is ever copied into BACKUP_CHANNEL. Runs once a
    link is actually about to be created (tapping "✅ Yes"), never before.
    Returns (entries, failures) — entries are ready for save_link(), and
    failures is a list of (src_chat_id, src_message_id, error_str) for
    anything that genuinely couldn't be copied, so callers can report the
    real reason instead of guessing.
    """
    entries = []
    failures = []
    groups = _group_pending(items)
    for i, group in enumerate(groups):
        src_chat_id = group[0]["src_chat_id"]
        src_message_id = group[0]["src_message_id"]
        try:
            if len(group) > 1:
                copied = await _copy_with_flood_retry(
                    client.copy_media_group, BACKUP_CHANNEL, src_chat_id, src_message_id
                )
            else:
                copied = [await _copy_with_flood_retry(
                    client.copy_message, BACKUP_CHANNEL, src_chat_id, src_message_id
                )]
        except Exception as e:
            logger.warning(f"Couldn't back up message {src_chat_id}/{src_message_id}: {e}")
            failures.append((src_chat_id, src_message_id, str(e)))
            continue  # source message may have been deleted/edited meanwhile — skip it

        for cp in copied:
            media = extract_media(cp)
            if not media:
                continue
            media_type, file_id, caption = media
            entries.append({
                "message_id": cp.id,
                "media_type": media_type,
                "file_id": file_id,
                "caption": caption,
                "media_group_id": group[0]["media_group_id"],
            })

        # Space out successive copies so we don't trigger the very FloodWait
        # we're trying to avoid. No point sleeping after the last group.
        if i < len(groups) - 1:
            await asyncio.sleep(_COPY_SPACING_SECONDS)

    return entries, failures


async def _report_failures(client, failures: list, context: str):
    """Best-effort: dump the real exception text to LOG_CHANNEL so failures
    are actually diagnosable instead of living only in a log file nobody sees."""
    if not failures or not LOG_CHANNEL:
        return
    lines = [f"⚠️ {context}: {len(failures)} item(s) failed to back up:"]
    for src_chat_id, src_message_id, err in failures[:10]:
        lines.append(f"• {src_chat_id}/{src_message_id} — {err}")
    if len(failures) > 10:
        lines.append(f"...and {len(failures) - 10} more.")
    try:
        await client.send_message(LOG_CHANNEL, "\n".join(lines))
    except Exception:
        pass


def _batch_controls(count: int) -> InlineKeyboardMarkup:
    """The Yes/No-style controls attached to the running counter message —
    the only way to finish or discard a batch."""
    return InlineKeyboardMarkup([[
        InlineKeyboardButton(f"✅ Yes, finish ({count})", callback_data="batchctl:done"),
        InlineKeyboardButton("❌ No, cancel", callback_data="batchctl:cancel"),
    ]])


async def _finalize_and_report_batch(client, admin_id: int, items: list, status: Message, mention: str):
    """Shared tail-end of the batch flow, used by the ✅ Yes button."""
    entries, failures = await _finalize_entries(client, items)
    await _report_failures(client, failures, f"Batch by {mention}")
    if not entries:
        detail = failures[0][2] if failures else "unknown error"
        note = " Check the log channel for details." if LOG_CHANNEL else f" Reason: {detail}"
        await status.edit_text(
            "<blockquote>❌ Backup Failed</blockquote>\n"
            f"<b>Couldn't back up any of those {len(items)} file(s) — likely a Telegram rate "
            f"limit mid-batch, not deleted files. Try /batch again with fewer files, or wait "
            f"a moment.{note}</b>"
        )
        return
    if failures:
        await status.edit_text(
            "<blockquote>⚠️ Partial Success</blockquote>\n"
            f"<b>{len(failures)} of {len(items)} file(s) were skipped (likely rate-limited). "
            f"Generating a link for the {len(entries)} that succeeded...</b>"
        )

    code = await save_link(admin_id, entries, is_batch=True)
    link = build_deep_link(code)
    await status.edit_text(
        f"<b><blockquote>✅ Batch Link Ready ({len(entries)}) files</blockquote>\n\n"
        f"<code>{link}</code>\n\n<blockquote>{link}</blockquote></b>"
    )
    if LOG_CHANNEL:
        await client.send_message(LOG_CHANNEL, f"📦 Batch link created by {mention}: {link}")


@Client.on_message(filters.command("batch") & filters.private & admin_filter)
async def start_batch(client, message: Message):
    admin_id = message.from_user.id
    BATCH_SESSIONS[admin_id] = []
    _BATCH_STATUS_MSG.pop(admin_id, None)
    _arm_batch_timeout(client, admin_id)
    await message.reply_text(
        "<b><blockquote>📚 Batch Mode</blockquote>\n"
        "• Send all the files in sequence.\n\n"
        "• Session expires after 5 mins of inactivity.</b>"
    )


@Client.on_callback_query(filters.regex(r"^batchctl:") & admin_filter)
async def batch_controls(client, query: CallbackQuery):
    admin_id = query.from_user.id
    decision = query.data.split(":", 1)[1]

    async with _lock_for(admin_id):
        items = BATCH_SESSIONS.pop(admin_id, None)
        _BATCH_STATUS_MSG.pop(admin_id, None)
    _cancel_batch_timeout(admin_id)

    if items is None:
        await query.answer("That batch already finished or expired.", show_alert=True)
        return

    if decision == "cancel":
        await query.answer()
        await query.message.edit_text(
            "<b><blockquote>🗑 Batch Discarded</blockquote>\n• Nothing was ever stored.</b>",
            reply_markup=None,
        )
        return

    if not items:
        await query.answer()
        await query.message.edit_text(
            "<blockquote>Batch Empty</blockquote>\n<b>Nothing to link.</b>",
            reply_markup=None,
        )
        return

    await query.answer()
    await query.message.edit_text(
        "<b><blockquote>⏳ Working</blockquote>\n• Saving files and generating your link...</b>",
        reply_markup=None,
    )
    await _finalize_and_report_batch(
        client, admin_id, items, query.message, query.from_user.mention
    )


async def _append_to_batch(client, message: Message, admin_id: int, items: list) -> bool:
    """Appends `items` to admin_id's open batch session and refreshes the
    progress counter. Returns True if a batch session was actually open
    (and the append happened), False otherwise — callers fall back to the
    direct single-item flow when this returns False."""
    # Held for the append, so the "finish" button can't pop the session
    # mid-write — otherwise an item sent moments earlier could silently
    # miss the batch.
    async with _lock_for(admin_id):
        if admin_id not in BATCH_SESSIONS:
            return False
        BATCH_SESSIONS[admin_id].extend(items)
        # Handlers for different messages can run concurrently (e.g. an
        # album's get_media_group() await lets a later, faster single-item
        # message finish appending first). message.id is monotonically
        # increasing per chat, so re-sorting on every append guarantees
        # the stored order always matches the order messages were
        # actually sent in, regardless of which handler finished first.
        BATCH_SESSIONS[admin_id].sort(key=lambda it: it["src_message_id"])
        try:
            await message.react(emoji="👍")
        except Exception:
            pass
        _arm_batch_timeout(client, admin_id)

        # One counter per group of items received "in one go" (a single
        # file/text message, or a whole album) — every new group replaces
        # the old counter message (delete + resend) rather than editing it,
        # so the counter always reappears at the bottom of the chat.
        count = len(BATCH_SESSIONS[admin_id])
        label = "item" if count == 1 else "items"
        text = (
            "<b><blockquote>📥 Batch Progress</blockquote>\n"
            f"• {count} {label} received so far.</b>"
        )
        markup = _batch_controls(count)
        old_status_msg = _BATCH_STATUS_MSG.pop(admin_id, None)
        if old_status_msg:
            try:
                await old_status_msg.delete()
            except Exception:
                pass
        try:
            _BATCH_STATUS_MSG[admin_id] = await message.reply_text(text, reply_markup=markup)
        except Exception:
            pass
        return True


@Client.on_message(
    filters.private & admin_filter & MEDIA_FILTER
    & filters.create(_not_awaiting_settings_reply)
)
async def handle_admin_media(client, message: Message):
    admin_id = message.from_user.id

    if message.media_group_id:
        if not _claim_group(message.media_group_id):
            return  # another item from this same album already triggered processing
        try:
            # Non-destructive: just reads the album's messages, copies nothing.
            group_msgs = await client.get_media_group(message.chat.id, message.id)
        except Exception as e:
            logger.warning(f"get_media_group failed, falling back to single item: {e}")
            group_msgs = [message]
        items = [_pending_item(m) for m in group_msgs]
    else:
        items = [_pending_item(message)]

    if await _append_to_batch(client, message, admin_id, items):
        return

    # Direct mode — hold the references and ask for confirmation before
    # touching the backup channel at all.
    PENDING_SINGLE[admin_id] = items
    label = f"this {len(items)}-item album" if len(items) > 1 else "this file"
    await message.reply_text(
        f"<blockquote>Generate Link?</blockquote>\n<b>Create a shareable link for {label}?</b>",
        reply_markup=InlineKeyboardMarkup([[
            InlineKeyboardButton("✅ Yes", callback_data="linkgen:yes"),
            InlineKeyboardButton("❌ No", callback_data="linkgen:no"),
        ]]),
    )


@Client.on_message(filters.private & admin_filter & TEXT_FILTER)
async def handle_admin_batch_text(client, message: Message):
    """Lets a plain text message count as batch content — so a batch can
    mix in one or more normal text messages anywhere in the sequence
    (e.g. PDF → text, or PDF → image → text), not just media."""
    admin_id = message.from_user.id
    await _append_to_batch(client, message, admin_id, [_pending_item(message)])

@Client.on_callback_query(filters.regex(r"^linkgen:") & admin_filter)
async def confirm_link(client, query: CallbackQuery):
    admin_id = query.from_user.id
    items = PENDING_SINGLE.pop(admin_id, None)
    if items is None:
        await query.answer("Nothing pending — that request expired.", show_alert=True)
        return

    decision = query.data.split(":", 1)[1]

    if decision == "no":
        # Nothing was ever copied to the backup channel, so there's nothing
        # to delete — discarding is just forgetting the pending reference.
        await query.answer()
        await query.message.edit_text(
            "<b><blockquote>🗑 Discarded</blockquote>\n• No link was generated, nothing was stored.</b>"
        )
        return

    await query.answer()
    await query.message.edit_text(
        "<b><blockquote>⏳ Working</blockquote>\nSaving files and generating your link...</b>"
    )

    entries, failures = await _finalize_entries(client, items)
    await _report_failures(client, failures, f"Single upload by {query.from_user.mention}")
    if not entries:
        detail = failures[0][2] if failures else "unknown error"
        note = " Check the log channel for details." if LOG_CHANNEL else f" Reason: {detail}"
        await query.message.edit_text(
            f"<blockquote>❌ Backup Failed</blockquote>\n<b>Couldn't back up that file.{note}</b>"
        )
        return

    code = await save_link(admin_id, entries, is_batch=len(entries) > 1)
    link = build_deep_link(code)
    await query.message.edit_text(
        f"<b><blockquote>✅ Link Generated</blockquote>\n\n<code>{link}</code>\n\n<blockquote>{link}</blockquote></b>"
    )
    if LOG_CHANNEL:
        await client.send_message(
            LOG_CHANNEL, f"📄 Link created by {query.from_user.mention}: {link}"
        )
