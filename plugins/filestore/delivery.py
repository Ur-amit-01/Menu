"""
delivery.py — delivers the files behind a link code to a user.

Strategy:
  1. Copy every item straight from the backup channel to the user via
     copy_message / copy_media_group. This delivers a clean message with no
     "Forwarded from" tag, and albums are copied as a single grouped unit so
     they arrive exactly as they were sent.
  2. If a copy fails (message purged from the backup channel, etc.) fall
     back to re-uploading each item from its stored file_id — grouping
     consecutive items that share a media_group_id back into albums via
     send_media_group.
"""
import logging

from config import BACKUP_CHANNEL
from plugins.helper.db import db
from plugins.helper.media import build_input_media, send_by_file_id
from plugins.helper.promo import send_promo_sticker
from plugins.helper.settings import settings
from plugins.filestore.deletion import schedule_deletion
from plugins.helper.time_parser import format_time

logger = logging.getLogger(__name__)


def _group_by_album(entries: list) -> list:
    """Group consecutive entries sharing a media_group_id into sub-lists."""
    groups, current, current_gid = [], [], object()
    for entry in entries:
        gid = entry.get("media_group_id")
        if gid and gid == current_gid:
            current.append(entry)
        else:
            if current:
                groups.append(current)
            current = [entry]
            current_gid = gid
    if current:
        groups.append(current)
    return groups


async def _deliver_by_copy(client, user_id: int, entries: list, protect_content: bool) -> list:
    """Copy every group straight from the backup channel — no forward tag,
    albums stay grouped."""
    sent = []
    for group in _group_by_album(entries):
        if len(group) > 1:
            copied = await client.copy_media_group(
                user_id, BACKUP_CHANNEL, group[0]["message_id"],
                protect_content=protect_content,
            )
            sent.extend(copied)
        else:
            e = group[0]
            msg = await client.copy_message(
                user_id, BACKUP_CHANNEL, e["message_id"],
                protect_content=protect_content,
            )
            sent.append(msg)
    return sent


async def _reupload(client, user_id: int, entries: list, protect_content: bool) -> list:
    sent = []
    for group in _group_by_album(entries):
        if len(group) > 1 and all(e["media_type"] in ("document", "video", "photo", "audio") for e in group):
            media = [build_input_media(e["media_type"], e["file_id"], e.get("caption", "")) for e in group]
            msgs = await client.send_media_group(user_id, media, protect_content=protect_content)
            sent.extend(msgs)
        else:
            for e in group:
                msg = await send_by_file_id(
                    client, user_id, e["media_type"], e["file_id"],
                    caption=e.get("caption", ""), protect_content=protect_content,
                )
                sent.append(msg)
    return sent


async def deliver(client, user_id: int, code: str) -> bool:
    """Delivers the file(s) behind `code` to `user_id`. Returns False if invalid."""
    file_doc = await db.get_file_link(code)
    if not file_doc:
        await client.send_message(user_id, "❌ This link is invalid or has been deleted.")
        return False

    entries = file_doc["messages"]
    protect_content = file_doc.get("protect_content")
    if protect_content is None:
        protect_content = settings.get("protect_content")

    try:
        sent_messages = await _deliver_by_copy(client, user_id, entries, protect_content)
    except Exception as e:
        logger.info(f"Copy failed for code={code}, falling back to re-upload: {e}")
        sent_messages = await _reupload(client, user_id, entries, protect_content)

    await db.increment_views(code)

    delay = file_doc.get("auto_delete")
    if delay is None:
        delay = settings.get("auto_delete_time")
    if delay and sent_messages:
        ids = [m.id for m in sent_messages]
        notice = settings.get("auto_delete_notice").format(time=format_time(delay))
        notice_msg = await client.send_message(user_id, notice)
        # Pass the notice's own message id (so it gets deleted alongside the
        # files, not left behind) and the code (so we can offer a "get it
        # again" button once everything's gone).
        await schedule_deletion(client, user_id, ids, delay, notice_id=notice_msg.id, code=code)

    # Fired after every successful delivery — bare file link, doesn't
    # matter which caller — so it's here rather than duplicated elsewhere.
    await send_promo_sticker(client, user_id)

    return True

