"""
force_sub.py — everything related to the force-subscribe gate lives here,
isolated from filters.py, so that if force-sub misbehaves (wrong channel id,
bot not admin in the channel, buttons not showing, "request to join" not
being recognized, etc.) there's exactly one file to open.

Contains:
  * _channel_join_button -> builds the "⚠️ Join N ⚠️" button for one channel
  * get_missing_channels -> which force-sub channels a user hasn't joined
  * ensure_subscribed    -> the gate called at the top of /start
  * track_join_request   -> records "request to join" submissions so
                             ensure_subscribed can treat them as satisfied
                             for private/request-only channels
"""
import logging

from pyrogram import Client
from pyrogram.errors import UserNotParticipant
from pyrogram.types import InlineKeyboardButton, InlineKeyboardMarkup, Message

from plugins.helper.db import db
from plugins.helper.settings import settings
from plugins.helper.filters import is_admin, is_premium

logger = logging.getLogger(__name__)

# Hardcoded promo link shown on every "please join" gate, alongside (not
# instead of) the real force-sub channel buttons above it. This is NOT a
# force-sub channel — membership here is never checked — it's just a static
# extra button. Its number continues on from the force-sub channel buttons
# (e.g. 1 force channel -> this becomes "ᴊᴏɪɴ 2"), so it's built inside
# _send_join_required() rather than as a fixed constant. Edit the URL here.
_EXTRA_JOIN_URL = "https://t.me/+tMf1rjw0ziQ3YWM1"


async def _channel_join_button(client, channel, index: int) -> InlineKeyboardButton:
    chat = await client.get_chat(channel)
    if chat.username:
        url = f"https://t.me/{chat.username}"
    else:
        url = chat.invite_link or (await client.export_chat_invite_link(chat.id))
    return InlineKeyboardButton(f"⚠️ ᴊᴏɪɴ {index} ⚠️", url=url)


async def get_missing_channels(client, user_id: int) -> list:
    """Returns the list of force-sub channels this user hasn't joined/requested."""
    missing = []
    for channel in settings.get("force_sub_channels"):
        try:
            await client.get_chat_member(channel, user_id)
            continue  # already a member
        except UserNotParticipant:
            # Not a member yet — but if they've sent a "request to join" for a
            # private/request-only channel, treat that as satisfying the check.
            chat = await client.get_chat(channel)
            if await db.has_pending_join_request(user_id, chat.id):
                continue
            missing.append(channel)
        except Exception as e:
            # BUGFIX: this used to be a silent `except Exception: continue`,
            # which means if the bot isn't actually an admin in the
            # configured force-sub channel (or the id/username is wrong),
            # EVERY user would silently skip that channel's check — the
            # force-sub requirement would appear completely disabled with no
            # error anywhere. We still don't want to block every user for a
            # misconfigured channel, but we now log it loudly so it's
            # obvious *why* subscribe-gating isn't kicking in.
            logger.warning(
                f"Force-sub check failed for channel={channel!r} "
                f"(bot likely isn't an admin there, or the id/username is wrong): {e}"
            )
            continue
    return missing


async def _send_join_required(client, chat_id: int, missing: list, resume_payload: str):
    """Shared by both ensure_subscribed() and ensure_subscribed_for_user() —
    builds and sends the "please join" message. `resume_payload` is whatever
    should be resumed via the Try Again button's deep link (a file code, a
    test-paper code, or "" for a bare /start)."""
    buttons = [
        [await _channel_join_button(client, ch, i)]
        for i, ch in enumerate(missing, start=1)
    ]
    extra_index = len(missing) + 1
    buttons.append([InlineKeyboardButton(f"⚠️ ᴊᴏɪɴ {extra_index} ⚠️", url=_EXTRA_JOIN_URL)])
    from config import BOT_USERNAME
    resume_url = (
        f"https://t.me/{BOT_USERNAME}?start={resume_payload}"
        if resume_payload else f"https://t.me/{BOT_USERNAME}?start=start"
    )
    buttons.append([InlineKeyboardButton("🔄 ᴛʀʏ ᴀɢᴀɪɴ", url=resume_url)])

    # Small-caps unicode message explaining the gate: due to heavy load,
    # only channel subscribers can use the bot right now.
    channel_word = "ᴄʜᴀɴɴᴇʟ" if len(missing) == 1 else "ᴄʜᴀɴɴᴇʟs"
    await client.send_message(
        chat_id,
        "🔐 <b>ᴀᴄᴄᴇss ʟᴏᴄᴋᴇᴅ</b>\n"
        "━━━━━━━━━━━━━━━━━━\n\n"
        f"ᴅᴜᴇ ᴛᴏ ʜᴇᴀᴠʏ ʟᴏᴀᴅ ᴏɴ ᴛʜɪs ʙᴏᴛ, ᴏɴʟʏ ᴏᴜʀ {channel_word} sᴜʙsᴄʀɪʙᴇʀs ᴄᴀɴ ᴜsᴇ ᴛʜɪs ʙᴏᴛ.\n\n"
        f"ᴊᴏɪɴ ᴛʜᴇ {channel_word} ᴜsɪɴɢ ᴛʜᴇ ʙᴜᴛᴛᴏɴs ʙᴇʟᴏᴡ, ᴛʜᴇɴ ᴛᴀᴘ <b>🔄 ᴛʀʏ ᴀɢᴀɪɴ</b>.",
        reply_markup=InlineKeyboardMarkup(buttons),
    )


async def ensure_subscribed(client, message: Message) -> bool:
    """
    Returns True if the user may proceed. If not, sends the "please join"
    message with buttons (including a Try Again button that resumes any
    deep-link payload) and returns False.
    """
    if not settings.get("force_sub_channels"):
        return True
    if message.from_user and (is_admin(message.from_user.id) or is_premium(message.from_user.id)):
        return True

    missing = await get_missing_channels(client, message.from_user.id)
    if not missing:
        return True

    payload = message.command[1] if len(message.command) > 1 else ""
    await _send_join_required(client, message.chat.id, missing, payload)
    return False


async def ensure_subscribed_for_user(
    client, user_id: int, chat_id: int, resume_payload: str = ""
) -> bool:
    """Callback-query-friendly variant of ensure_subscribed() — for gating
    actions triggered by an inline button tap (e.g. tapping a test paper in
    the Test Series menu) rather than a /start command, where there's no
    Message.command to pull a deep-link payload from. Pass resume_payload
    explicitly (e.g. the paper's code) so 'Try Again' resumes exactly what
    the user was trying to open."""
    if not settings.get("force_sub_channels"):
        return True
    if is_admin(user_id) or is_premium(user_id):
        return True

    missing = await get_missing_channels(client, user_id)
    if not missing:
        return True

    await _send_join_required(client, chat_id, missing, resume_payload)
    return False


# Track "request to join" submissions for force-sub channels that use
# join-request mode, so ensure_subscribed() can treat them as satisfied.
@Client.on_chat_join_request()
async def track_join_request(client, chat_join_request):
    channel_ids = {
        ch if isinstance(ch, int) else None
        for ch in settings.get("force_sub_channels")
    }
    chat_id = chat_join_request.chat.id
    if chat_id in channel_ids or chat_join_request.chat.username in settings.get("force_sub_channels"):
        await db.record_join_request(chat_join_request.from_user.id, chat_id)
     
