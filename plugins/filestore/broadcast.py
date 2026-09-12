import asyncio

from pyrogram import Client, filters
from pyrogram.errors import FloodWait, InputUserDeactivated, PeerIdInvalid, UserIsBlocked
from pyrogram.types import Message

from plugins.helper.db import db
from plugins.helper.filters import admin_filter


@Client.on_message(filters.command("broadcast") & filters.private & admin_filter)
async def broadcast(client, message: Message):
    source = message.reply_to_message
    text = None
    if not source:
        text = message.text.split(None, 1)[1] if len(message.command) > 1 else None
    if not source and not text:
        await message.reply_text(
            "Reply to a message with /broadcast to send it to every user, "
            "or use <code>/broadcast your text here</code>."
        )
        return

    user_ids = await db.get_all_user_ids()
    status = await message.reply_text(f"📢 Broadcasting to {len(user_ids)} user(s)...")

    sent = failed = blocked = 0
    for uid in user_ids:
        try:
            if source:
                await source.copy(uid)
            else:
                await client.send_message(uid, text)
            sent += 1
        except FloodWait as e:
            await asyncio.sleep(e.value)
            try:
                if source:
                    await source.copy(uid)
                else:
                    await client.send_message(uid, text)
                sent += 1
            except Exception:
                failed += 1
        except (UserIsBlocked, InputUserDeactivated, PeerIdInvalid):
            blocked += 1
        except Exception:
            failed += 1

    await status.edit_text(
        "📢 <b>Broadcast complete</b>\n\n"
        f"✅ Delivered: {sent}\n"
        f"🚫 Blocked/deleted accounts: {blocked}\n"
        f"⚠️ Other failures: {failed}"
    )
