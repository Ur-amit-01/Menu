"""
filters.py — the admin check used at the top of every admin-only command:
  * admin_filter -> is this user an admin (config.ADMINS)?
  * is_admin     -> plain function version, used where a pyrogram filter
                     object doesn't fit (e.g. inside another module's logic)

Force-subscribe logic (ensure_subscribed, get_missing_channels, the
join-request tracker) lives in plugins/helper/force_sub.py instead — kept
separate so force-sub issues can be debugged in one file without touching
this one.
"""
from pyrogram import filters
from pyrogram.types import Message

from config import ADMINS, PREMIUM_USERS

# ---------------------------------------------------------------- admin ---- #


async def _is_admin(_, __, message: Message) -> bool:
    return bool(message.from_user) and message.from_user.id in ADMINS


admin_filter = filters.create(_is_admin)


def is_admin(user_id: int) -> bool:
    return user_id in ADMINS


# -------------------------------------------------------------- premium ---- #


def is_premium(user_id: int) -> bool:
    """Hardcoded premium users (config.PREMIUM_USERS) skip force-sub gating
    and the promotional sticker — see plugins/helper/force_sub.py and
    plugins/helper/promo.py."""
    return user_id in PREMIUM_USERS

