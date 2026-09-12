"""
config.py — BOOT-TIME configuration only.

Everything here is read once from the environment when the process starts.
None of it can be changed while the bot is running — for anything an admin
should be able to tweak live (force-sub channels, auto-delete timer, etc.)
see plugins/helper/settings.py, which is the DB-backed runtime layer.
"""
import re
import os

id_pattern = re.compile(r"^-?\d+$")


def _as_id_list(raw: str):
    """Turn a space separated string of ids/usernames into a clean list."""
    out = []
    for item in raw.split():
        item = item.strip()
        if not item:
            continue
        out.append(int(item) if id_pattern.match(item) else item)
    return out


# ---- Telegram credentials -------------------------------------------------
API_ID = int(os.environ.get("API_ID", "22012880"))
API_HASH = os.environ.get("API_HASH", "5b0e07f5a96d48b704eb9850d274fe1d")
BOT_TOKEN = os.environ.get("BOT_TOKEN", "")

# ---- Database ---------------------------------------------------------------
DB_URL = os.environ.get("DB_URL", "mongodb://localhost:27017")
DB_NAME = os.environ.get("DB_NAME", "filestore_bot")

# ---- Access control ---------------------------------------------------------
# Users who can upload files, run /setting, /broadcast, /stats, /delete, etc.
ADMINS = _as_id_list(os.environ.get("ADMINS", "7150972327"))

# Hardcoded premium user ids — exempt from force-sub gating and from the
# promotional sticker (see plugins/helper/filters.py:is_premium(),
# plugins/helper/force_sub.py, plugins/helper/promo.py). Edit this list
# directly to add/remove premium users; no restart-free way to manage this
# yet, unlike the DB-backed settings.
PREMIUM_USERS = [
    1519459773,
    5753557653,
    7191448865,
]

# Private channel the bot backs every uploaded file up to.
# The bot MUST be an admin there with post/delete rights.
BACKUP_CHANNEL = int(os.environ.get("BACKUP_CHANNEL", "0"))

# Optional channel the bot posts logs to (new users, new links, broadcasts...).
# Set to 0 to disable.
LOG_CHANNEL = int(os.environ.get("LOG_CHANNEL", "0"))

# ---- Misc boot-time knobs ----------------------------------------------------
PORT = int(os.environ.get("PORT", "8080"))
START_PIC = os.environ.get("START_PIC", "")  # optional image shown on /start

# Bot username cache (filled in at runtime in bot.py, exposed here so every
# plugin can import it without an extra get_me() call).
BOT_USERNAME = ""


def missing_required():
    """
    Returns a list of (name, hint) for required settings that are still
    unset/empty. Called at every boot so misconfiguration is never silent.
    """
    problems = []
    if not API_ID:
        problems.append(("API_ID", "your api_id from my.telegram.org"))
    if not API_HASH:
        problems.append(("API_HASH", "your api_hash from my.telegram.org"))
    if not BOT_TOKEN:
        problems.append(("BOT_TOKEN", "the bot token from @BotFather"))
    if not ADMINS:
        problems.append(("ADMINS", "space-separated user id(s), e.g. '123456789'"))
    if not BACKUP_CHANNEL:
        problems.append(("BACKUP_CHANNEL", "the backup channel's id, e.g. '-1001234567890'"))
    return problems


