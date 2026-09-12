"""
settings.py — the DB-backed, admin-editable settings layer.

This is deliberately separate from config.py:
  config.py  -> read once from the environment at boot, needs a restart to change
  settings   -> stored in Mongo, editable at runtime via /setting, cached in memory

Every key has a hard-coded default below so the bot works out of the box even
before an admin has touched /setting.
"""
from typing import Any, Dict

DEFAULTS: Dict[str, Any] = {
    # list of channel ids/usernames a user must join before using the bot.
    # entries can be plain "-100..." ids (normal channels) or channel ids that
    # use "request to join" — both are supported by the fsub filter.
    "force_sub_channels": [],
    # seconds to wait before deleting delivered files. 0 = never auto-delete.
    "auto_delete_time": 0,
    # shown right after delivering files when auto-delete is enabled.
    "auto_delete_notice": "**⚠️ These files will be automatically deleted in {time}.\n💾 Save or forward the files before they’re gone.**",
    # if True, delivered messages are sent with forwarding/saving disabled.
    "protect_content": False,
    # shown on a bare /start (no deep-link payload), and resent (without a
    # photo re-check — see start_photo below) after an auto-deleted file's
    # "get it again" moment. {mention} is available but optional.
    "start_text": (
        "> 📚 𝗡𝗘𝗘𝗧 𝗦𝗧𝗨𝗗𝗬 𝗠𝗔𝗧𝗘𝗥𝗜𝗔𝗟𝗦\n"
        "━━━━━━━━━━━━━━━━━━\n"
        "**     ✦ Choose a material below**\n"
        "**     ✦ Tap the button to get PDF**"
    ),
    # (label, deep-link code) pairs rendered as inline buttons under
    # start_text — see plugins/helper/start_message.py for how these are
    # laid out into rows.
    "start_materials": [
        ("DC Pandey", "QbLQSYo3"),
        ("HC Verma", "LVg647OT"),
        ("Anand Mani Pocket Book", "NcKnwabw"),
        ("All Med Easy", "5XQQYTD8"),
        ("Vedantu TATVA", "k2tRDpXR"),
        ("Unacademy Modules", "WOJdSLuG"),
        ("Allen Modules", "l0VEzVrW"),
        ("PW Modules", "YykwfCCx"),
        ("MTG (2025-26)", "5s97HpH1"),
        ("NEET Question Papers (1998-2026)", "3Z4RUIZ1"),
        ("NCERT Punch", "agLPRfWA"),
        ("All Institute PYQs", "4M4NIcgK"),
        ("Aakash Modules", "4AWLey4f"),
        ("Masterclass in Biology", "8IQiSfwP"),
    ],
    # file_id (after an admin uploads a photo via /setting) or a direct
    # image URL to send above start_text on /start and after an
    # auto-deleted file. Empty = text-only, no photo.
    "start_photo": "",
    # extra line appended under every delivered file's caption, {} unused if empty.
    "custom_caption": "",
    # DMed to a user the moment they leave/are removed from a force-sub
    # channel. {mention} and {chat_title} are available. Empty/"none" via
    # /setting disables this feature entirely.
    "leave_message": (
        "💔 {mention}, you just left {chat_title}...\n\n"
        "We really don't want to lose you like this. Come back? "
        "It only takes a second and we'd love to have you around again."
    ),
    # "promo_sticker:<message_id>" reference (see plugins/helper/promo.py) to
    # a sticker sent right after the start message, and again right after
    # every file delivery. "" = feature disabled, no sticker sent.
    "promo_sticker": "",
    # label for the single button shown under the promo sticker. Both this
    # and promo_button_url must be set for the button to appear.
    "promo_button_text": "",
    # URL the promo button opens. Both this and promo_button_text must be
    # set for the button to appear.
    "promo_button_url": "",
}


class SettingsManager:
    def __init__(self, db):
        self.db = db
        self._cache: Dict[str, Any] = dict(DEFAULTS)

    async def load(self):
        """Call once at boot: pulls saved overrides from Mongo into the cache."""
        stored = await self.db.get_all_settings()
        self._cache = dict(DEFAULTS)
        self._cache.update(stored)

    def get(self, key: str) -> Any:
        return self._cache.get(key, DEFAULTS.get(key))

    async def set(self, key: str, value: Any):
        self._cache[key] = value
        await self.db.save_setting(key, value)

    def all(self) -> Dict[str, Any]:
        return dict(self._cache)


from plugins.helper.db import db  # noqa: E402  (avoid circular import at module load)

# Single shared instance used by every plugin. Call `await settings.load()` at boot.
settings = SettingsManager(db)
