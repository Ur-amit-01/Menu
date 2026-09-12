from pyrogram import Client, filters
from pyrogram.types import Message

from config import BACKUP_CHANNEL
from plugins.helper.db import db
from plugins.helper.filters import admin_filter
from plugins.filestore.linking import extract_code


@Client.on_message(filters.command("delete") & filters.private & admin_filter)
async def delete_link(client, message: Message):
    if len(message.command) < 2:
        await message.reply_text(
            "Usage: <code>/delete CODE</code> or <code>/delete FULL_LINK</code>"
        )
        return

    # Accepts either a bare code or a full deep link — extract_code()
    # pulls the code out of the `?start=` param when a link is given,
    # and passes a bare code straight through unchanged.
    code = extract_code(message.command[1])
    file_doc = await db.get_file_link(code)
    if not file_doc:
        await message.reply_text("❌ No such link.")
        return

    message_ids = [e["message_id"] for e in file_doc["messages"]]
    try:
        await client.delete_messages(BACKUP_CHANNEL, message_ids)
    except Exception:
        pass  # backup copies may already be gone — link record deletion still proceeds

    await db.delete_file_link(code)
    await message.reply_text(f"🗑 Link <code>{code}</code> and its backed-up file(s) deleted.")
    
