"""
range_files.py — bulk-import messages the bot can already see (e.g. an
existing archive channel) into one shareable batch link, without the admin
having to resend every file by hand.

Usage: /range_files <source_chat_id> <first_message_id> <last_message_id>
"""
from pyrogram import Client, filters
from pyrogram.types import Message

from config import BACKUP_CHANNEL
from plugins.helper.filters import admin_filter
from plugins.helper.media import extract_media
from plugins.filestore.linking import save_link, build_deep_link

MAX_RANGE = 500  # sanity cap so one command can't queue thousands of copies


@Client.on_message(filters.command("range_files") & filters.private & admin_filter)
async def range_files(client, message: Message):
    args = message.command[1:]
    if len(args) != 3:
        await message.reply_text(
            "Usage: <code>/range_files SOURCE_CHAT_ID FIRST_MSG_ID LAST_MSG_ID</code>\n"
            "The bot must already be a member/admin of SOURCE_CHAT_ID."
        )
        return

    try:
        source_chat = int(args[0])
        first_id, last_id = int(args[1]), int(args[2])
    except ValueError:
        await message.reply_text("❌ All three arguments must be numbers.")
        return

    if first_id > last_id:
        first_id, last_id = last_id, first_id
    if last_id - first_id + 1 > MAX_RANGE:
        await message.reply_text(f"❌ That's more than {MAX_RANGE} messages — narrow the range.")
        return

    status = await message.reply_text("⏳ Importing range, this may take a moment...")

    ids = list(range(first_id, last_id + 1))
    messages = await client.get_messages(source_chat, ids)
    if not isinstance(messages, list):
        messages = [messages]

    entries = []
    for src in messages:
        if not src or src.empty or src.service:
            continue
        try:
            backup_msg = await src.copy(chat_id=BACKUP_CHANNEL)
        except Exception:
            continue
        media = extract_media(backup_msg)
        if not media:
            continue
        media_type, file_id, caption = media
        entries.append({
            "message_id": backup_msg.id,
            "media_type": media_type,
            "file_id": file_id,
            "caption": caption,
            "media_group_id": src.media_group_id,
        })

    if not entries:
        await status.edit_text("❌ No importable messages found in that range.")
        return

    code = await save_link(message.from_user.id, entries, is_batch=True)
    link = build_deep_link(code)
    await status.edit_text(f"✅ Imported {len(entries)} file(s).\n\n<code>{link}</code>")
