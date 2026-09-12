"""
content.py — delivers a menu content-node's stored file(s) to a chat.

Deliberately reuses the exact same copy-then-reupload-fallback strategy as
plugins/filestore/delivery.py (deliver() for /start?code links), so a menu
leaf and a file-store link behave identically and any future fix to one
delivery path is easy to mirror in the other.
"""
import logging

from plugins.filestore.delivery import _deliver_by_copy, _reupload
from plugins.helper.settings import settings

logger = logging.getLogger(__name__)


async def deliver_node_messages(client, chat_id: int, messages: list) -> list:
    """messages: a menu node's stored `messages` list (same shape as a
    files-collection doc's `messages`). Returns the list of sent Message
    objects (empty if there was nothing to send)."""
    if not messages:
        return []
    protect_content = settings.get("protect_content")
    try:
        return await _deliver_by_copy(client, chat_id, messages, protect_content)
    except Exception as e:
        logger.info(f"Menu content copy failed, falling back to re-upload: {e}")
        return await _reupload(client, chat_id, messages, protect_content)
