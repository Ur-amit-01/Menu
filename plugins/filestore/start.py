import html as html_lib
import logging

from pyrogram import Client, filters
from pyrogram.types import CallbackQuery, Message

import config
from plugins.helper.db import db
from plugins.helper.force_sub import ensure_subscribed
from plugins.helper.promo import send_promo_sticker
from plugins.helper.start_message import send_start_message
from plugins.filestore.delivery import deliver
from plugins.menu.navigation import show_root_menu

logger = logging.getLogger(__name__)


async def _notify_admins_new_user(client, user):
    """DM every admin the moment a brand-new user starts the bot. Skips
    silently for returning users — db.add_user() only reports True the
    first time a given user_id is ever seen."""
    total = await db.total_users_count()
    name = html_lib.escape(user.first_name or "Unknown")
    text = (
        "<b><blockquote>🆕 New User! </blockquote>\n"
        f"• Total: {total} Users\n"
        f'• Name: <a href="tg://user?id={user.id}">{name}</a>\n'
        f"• User id: <code>{user.id}</code></b>"
    )
    for admin_id in config.ADMINS:
        if not isinstance(admin_id, int):
            continue  # usernames in ADMINS can't be DMed by id
        try:
            await client.send_message(admin_id, text)
        except Exception as e:
            logger.warning(f"Couldn't notify admin {admin_id} of new user: {e}")


@Client.on_message(filters.private & filters.command("start"))
async def start(client, message: Message):
    is_new = await db.add_user(message.from_user.id)
    if is_new:
        await _notify_admins_new_user(client, message.from_user)

    if len(message.command) > 1:
        # Only gate access when a user is actually trying to open a file —
        # a bare /start (just browsing/opening the bot) is never blocked.
        if not await ensure_subscribed(client, message):
            return  # join-required message already sent
        code = message.command[1]
        await deliver(client, message.from_user.id, code)
        return

    await send_start_message(client, message.chat.id, message.from_user.mention)
    await send_promo_sticker(client, message.chat.id)
    await show_root_menu(client, message.chat.id, message.from_user.id)


@Client.on_callback_query(filters.regex(r"^trigger:start$"))
async def start_via_button(client, query: CallbackQuery):
    """Fired by the "🔄 Get Files Again" button on the auto-delete notice
    (see plugins/filestore/deletion.py). Behaves like a bare /start — no
    code attached, so there's nothing to gate behind force-sub here."""
    is_new = await db.add_user(query.from_user.id)
    if is_new:
        await _notify_admins_new_user(client, query.from_user)
    await query.answer()
    await send_start_message(client, query.message.chat.id, query.from_user.mention)
    await send_promo_sticker(client, query.message.chat.id)

