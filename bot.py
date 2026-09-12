import logging
import sys

from pyrogram import Client

import config
from config import API_ID, API_HASH, BOT_TOKEN
from plugins.helper.settings import settings
from plugins.filestore.deletion import restore_pending_deletions

logging.basicConfig(
    level=logging.INFO,
    format="[%(asctime)s] [%(levelname)s] %(name)s: %(message)s",
)
logging.getLogger("pyrogram").setLevel(logging.WARNING)
logger = logging.getLogger(__name__)

# these have no working default — the bot can't even connect without them
FATAL_IF_MISSING = {"API_ID", "API_HASH", "BOT_TOKEN"}


def check_env():
    """Warn about every missing required variable, every single boot.
    Exits if any of the Telegram connection credentials are missing, since
    the bot can't do anything at all without those."""
    problems = config.missing_required()
    if not problems:
        return

    logger.warning("⚠️  Missing/empty environment variable(s):")
    for name, hint in problems:
        logger.warning(f"   - {name}  ({hint})")

    fatal = [name for name, _ in problems if name in FATAL_IF_MISSING]
    if fatal:
        logger.error(
            f"Cannot start: {', '.join(fatal)} must be set. Fix your environment and restart."
        )
        sys.exit(1)


class Bot(Client):
    def __init__(self):
        super().__init__(
            name="filestore_bot",
            api_id=API_ID,
            api_hash=API_HASH,
            bot_token=BOT_TOKEN,
            workers=50,
            plugins={"root": "plugins"},
            sleep_threshold=5,
        )

    async def start(self):
        await super().start()
        me = await self.get_me()
        config.BOT_USERNAME = me.username

        await settings.load()
        await restore_pending_deletions(self)
        await self._check_backup_channel()

        logger.info(f"{me.first_name} started as @{me.username}")

    async def _check_backup_channel(self):
        if not config.BACKUP_CHANNEL:
            return  # already warned about this in check_env()
        try:
            chat = await self.get_chat(config.BACKUP_CHANNEL)
            member = await self.get_chat_member(config.BACKUP_CHANNEL, "me")
            if not (member.privileges and member.privileges.can_post_messages):
                logger.warning(
                    f"Bot is a member of '{chat.title}' but can't post there — "
                    "give it admin rights with 'Post messages' enabled."
                )
        except Exception as e:
            logger.warning(
                f"Couldn't verify BACKUP_CHANNEL ({config.BACKUP_CHANNEL}): {e}. "
                "Make sure the id is correct and the bot has been added as an admin."
            )

    async def stop(self, *args):
        await super().stop()
        logger.info("Bot stopped.")


if __name__ == "__main__":
    check_env()
    Bot().run()
    
