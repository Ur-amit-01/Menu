from datetime import datetime

from pyrogram import Client, filters
from pyrogram.types import Message

from plugins.helper.db import db
from plugins.helper.filters import admin_filter, is_admin

START_TIME = datetime.utcnow()

ADMIN_HELP = """<b>🛠 Admin commands</b>

• Send any file — stores it and hands back a shareable link right away
• /batch — start collecting several files and/or text messages into one link, finish with the ✅ button
• Use the ❌ button on the batch progress message to discard the batch currently in progress
• /range_files SOURCE_CHAT_ID FIRST_ID LAST_ID — bulk-import an existing range of messages
• /delete CODE_OR_LINK — remove a link and its backed-up file(s)
• /broadcast — reply to a message (or add text) to send it to every user
• /setting — view/edit force-sub channels, auto-delete timer, protect content, etc.
• /stats — usage statistics
"""

USER_HELP = """<b>ℹ️ Help</b>

Open a link someone shared with you (t.me/<i>bot</i>?start=CODE) and I'll deliver
the file(s) behind it — you may need to join a channel or two first if asked.
"""


@Client.on_message(filters.command("help") & filters.private)
async def help_command(client, message: Message):
    text = USER_HELP
    if is_admin(message.from_user.id):
        text += "\n" + ADMIN_HELP
    await message.reply_text(text)


@Client.on_message(filters.command("stats") & filters.private & admin_filter)
async def stats_command(client, message: Message):
    users = await db.total_users_count()
    links = await db.total_links_count()
    files = await db.total_files_stored()
    uptime = datetime.utcnow() - START_TIME

    await message.reply_text(
        "📊 <b>Stats</b>\n\n"
        f"• Users: <code>{users}</code>\n"
        f"• Links created: <code>{links}</code>\n"
        f"• Files stored: <code>{files}</code>\n"
        f"• Uptime: <code>{str(uptime).split('.')[0]}</code>"
    )
    
