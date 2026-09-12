"""
deletion.py — auto-delete for delivered files.

Every scheduled deletion is written to the `pending_deletions` collection
first, so if the bot restarts mid-wait, restore_pending_deletions() (called
from bot.py at startup) picks it back up instead of losing it.
"""
import asyncio
import logging
from datetime import datetime, timedelta

from pyrogram.types import InlineKeyboardButton, InlineKeyboardMarkup

from plugins.helper.db import db

logger = logging.getLogger(__name__)


async def _delete_later(
    client, job_id, chat_id: int, message_ids: list, delay: float,
    notice_id: int = None, code: str = None,
):
    try:
        await asyncio.sleep(delay)

        # Delete the delivered file(s) and, if present, the "will be
        # auto-deleted in X" warning message together — leaving that notice
        # behind after the files are gone is confusing, so it goes too.
        all_ids = list(message_ids)
        if notice_id:
            all_ids.append(notice_id)
        await client.delete_messages(chat_id, all_ids)

        # Instead of a single "Get It Again" link back to just this one
        # file's code, offer a button that re-triggers /start on tap — the
        # full start photo + material links are only sent then, not
        # automatically, so we don't spam a second message every time.
        if code:
            keyboard = InlineKeyboardMarkup(
                [[InlineKeyboardButton("🔄 Get Files Again", callback_data="trigger:start")]]
            )
            await client.send_message(
                chat_id,
                "<b><blockquote>🗑️ FILES AUTO-DELETED</blockquote>\n"
                "▬▬▬▬▬▬▬▬▬▬▬▬▬▬▬\n"
                "The files were automatically deleted\n"
                "to keep this chat clean. 🧹\n\n"
                "😊 No worries"
                "  • Tap the button below to get them again 👇</b>",
                reply_markup=keyboard,
            ),
        
    except Exception as e:
        logger.warning(f"Auto-delete failed for job {job_id}: {e}")
    finally:
        await db.remove_pending_deletion(job_id)


async def schedule_deletion(
    client, chat_id: int, message_ids: list, delay: int,
    notice_id: int = None, code: str = None,
):
    """Schedule `message_ids` (and optionally the auto-delete `notice_id`
    message) in `chat_id` to be deleted `delay` seconds from now. If `code`
    is given, a "get it again" button is sent right after deletion."""
    if delay <= 0 or not message_ids:
        return
    doc = {
        "chat_id": chat_id,
        "message_ids": message_ids,
        "delete_at": datetime.utcnow() + timedelta(seconds=delay),
        "notice_id": notice_id,
        "code": code,
    }
    job_id = await db.add_pending_deletion(doc)
    asyncio.create_task(
        _delete_later(client, job_id, chat_id, message_ids, delay, notice_id, code)
    )


async def restore_pending_deletions(client):
    """Call once at startup to resume any auto-delete jobs that survived a restart."""
    jobs = await db.get_all_pending_deletions()
    now = datetime.utcnow()
    for job in jobs:
        remaining = (job["delete_at"] - now).total_seconds()
        asyncio.create_task(
            _delete_later(
                client, job["_id"], job["chat_id"], job["message_ids"], max(remaining, 0),
                job.get("notice_id"), job.get("code"),
            )
        )
    if jobs:
        logger.info(f"Restored {len(jobs)} pending auto-delete job(s).")

