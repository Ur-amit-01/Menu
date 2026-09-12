"""
feedback.py — a lightweight two-way support channel with no commands
involved on either side.

  * Any private message from a non-admin user (text or any media) that
    isn't itself a command is forwarded (with Telegram's own "Forwarded
    from" tag) straight into every admin's DM, followed by a small header
    identifying who it's from.
  * Whichever admin swipe-replies to that relayed message (either the
    copied content or the header) has their reply copied straight back
    to the user who sent it.

The user <-> relayed-message mapping is persisted in the `feedback` DB
collection (see plugins/helper/db.py), so replying still works even if
the bot restarts between the user's message and the admin's reply.

IMPORTANT: relay_admin_reply runs in group=-1 (before every other admin
message handler, e.g. the file-upload flow in upload.py) but its filter
only matches when the replied-to message is *actually* a tracked feedback
relay — so it only ever intercepts genuine feedback replies and otherwise
gets out of the way, letting an admin's ordinary reply-to-something-else
(uploading a file, editing a setting, etc.) reach its normal handler
untouched.
"""
import html as html_lib
import logging

from pyrogram import Client, filters
from pyrogram.errors import InputUserDeactivated, PeerIdInvalid, UserIsBlocked
from pyrogram.types import Message

import config
from plugins.helper.db import db
from plugins.helper.filters import is_admin

logger = logging.getLogger(__name__)


def _not_admin(_, __, message: Message) -> bool:
    return bool(message.from_user) and not is_admin(message.from_user.id)


def _not_a_command(_, __, message: Message) -> bool:
    # Lets /start, /help etc. fall through to their own handlers instead of
    # being relayed to admins as "feedback".
    return not (message.text or "").startswith("/")


FEEDBACK_FILTER = (
    filters.private
    & filters.create(_not_admin)
    & filters.create(_not_a_command)
)


@Client.on_message(FEEDBACK_FILTER)
async def relay_to_admins(client: Client, message: Message):
    user = message.from_user
    name = html_lib.escape(user.first_name or "Unknown")
    header = (
        "<b><blockquote>💬 New Feedback</blockquote>\n"
        f'• From: <a href="tg://user?id={user.id}">{name}</a>\n'
        f"• User id: <code>{user.id}</code>\n"
        "Swipe-reply to this to answer.</b>"
    )

    delivered = False
    for admin_id in config.ADMINS:
        if not isinstance(admin_id, int):
            continue  # usernames in ADMINS can't be DMed by id
        try:
            forwarded = await message.forward(admin_id)
            info = await forwarded.reply_text(header)
            await db.save_feedback_message(admin_id, forwarded.id, user.id)
            await db.save_feedback_message(admin_id, info.id, user.id)
            delivered = True
        except (UserIsBlocked, InputUserDeactivated, PeerIdInvalid):
            logger.warning(f"Can't relay feedback to admin {admin_id} — blocked/unreachable.")
        except Exception as e:
            logger.warning(f"Failed to relay feedback to admin {admin_id}: {e}")

    if delivered:
        try:
            await message.reply_text(
                "✅ <b>Message sent to the admins.</b> They'll get back to you here soon."
            )
        except Exception:
            pass


async def _is_feedback_reply(_, __, message: Message) -> bool:
    """True only when this admin is replying to a message we actually
    relayed from a user — never true for an admin's ordinary reply to
    something else, so this filter can safely run ahead of every other
    handler without stealing unrelated replies from them."""
    if not (
        message.from_user
        and is_admin(message.from_user.id)
        and message.reply_to_message
    ):
        return False
    user_id = await db.get_feedback_user(message.chat.id, message.reply_to_message.id)
    if user_id is None:
        return False
    message._feedback_user_id = user_id  # stash for the handler, avoids a second lookup
    return True


FEEDBACK_REPLY_FILTER = filters.private & filters.create(_is_feedback_reply)


@Client.on_message(FEEDBACK_REPLY_FILTER, group=-1)
async def relay_admin_reply(client: Client, message: Message):
    user_id = message._feedback_user_id

    try:
        await message.copy(user_id)
        try:
            await message.react(emoji="✅")
        except Exception:
            pass
    except (UserIsBlocked, InputUserDeactivated, PeerIdInvalid):
        await message.reply_text(
            "⚠️ Couldn't deliver — that user has blocked the bot or is unreachable."
        )
    except Exception as e:
        logger.warning(f"Failed to relay admin reply to user {user_id}: {e}")
        await message.reply_text(f"⚠️ Couldn't deliver your reply: {e}")
