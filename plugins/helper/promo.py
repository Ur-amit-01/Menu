"""
promo.py — the promotional sticker + button shown after the start message
and after every file delivery.

Mirrors plugins/helper/photo_ref.py's approach so the sticker survives a
BOT_TOKEN swap: an admin-supplied sticker is immediately reposted into
BACKUP_CHANNEL, and only a "promo_sticker:<message_id>" reference is stored
in settings — never the raw file_id.

Three settings drive this (see plugins/helper/settings.py):
  promo_sticker      -> "promo_sticker:<message_id>" ref, or "" to disable
  promo_button_text  -> label for the single button under the sticker
  promo_button_url   -> URL the button opens

The sticker is only sent if promo_sticker is set. The button is only
attached if BOTH promo_button_text and promo_button_url are set — a sticker
with no button (or no sticker at all) is a valid, deliberate configuration.
"""
import logging

from pyrogram.types import InlineKeyboardButton, InlineKeyboardMarkup

from config import BACKUP_CHANNEL
from plugins.helper.filters import is_premium
from plugins.helper.settings import settings

logger = logging.getLogger(__name__)

_PREFIX = "promo_sticker:"


async def store_promo_sticker_ref(client, file_id: str) -> str:
    """Reposts an admin-supplied sticker (a file_id straight off an incoming
    message.sticker) into BACKUP_CHANNEL and returns the reference string to
    save via settings.set("promo_sticker", ...).

    Raises whatever exception Telegram raises on failure — same caveat as
    store_photo_ref: a sticker forwarded from a "restrict saving content"
    source can't be reposted. Callers should catch this and ask the admin
    to send the sticker directly instead of forwarding it.
    """
    msg = await client.send_sticker(BACKUP_CHANNEL, sticker=file_id)
    return f"{_PREFIX}{msg.id}"


def _promo_button() -> InlineKeyboardMarkup | None:
    text = settings.get("promo_button_text")
    url = settings.get("promo_button_url")
    if not text or not url:
        return None
    return InlineKeyboardMarkup([[InlineKeyboardButton(text, url=url)]])


async def send_promo_sticker(client, chat_id: int):
    """Sends the admin-configured promo sticker (with its button, if one is
    set) to chat_id. No-op if no promo sticker is configured, or if chat_id
    belongs to a hardcoded premium user (config.PREMIUM_USERS) — premium
    users never see the promo sticker, same private-chat id doubling as the
    user id that plugins/filestore/start.py and delivery.py already rely on.
    Failures are logged and swallowed — a broken promo sticker must never
    break /start or file delivery."""
    if is_premium(chat_id):
        return

    ref = settings.get("promo_sticker")
    if not ref:
        return

    reply_markup = _promo_button()

    try:
        if ref.startswith(_PREFIX):
            message_id = int(ref[len(_PREFIX):])
            await client.copy_message(
                chat_id, BACKUP_CHANNEL, message_id, reply_markup=reply_markup
            )
        else:
            # Legacy raw file_id saved before this module existed, or a
            # value set some other way — only works as long as the bot
            # token hasn't changed since.
            await client.send_sticker(chat_id, sticker=ref, reply_markup=reply_markup)
    except Exception as e:
        logger.warning(f"promo_sticker ({ref!r}) failed to send: {e}")


