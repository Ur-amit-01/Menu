"""linking.py — shared code-generation + Files-doc creation used by upload & range_files."""
import secrets
import string
from urllib.parse import urlparse, parse_qs

import config
from config import BACKUP_CHANNEL
from plugins.helper.db import db

_ALPHABET = string.ascii_letters + string.digits


async def generate_code(length: int = 8) -> str:
    while True:
        code = "".join(secrets.choice(_ALPHABET) for _ in range(length))
        if not await db.get_file_link(code):
            return code


async def save_link(admin_id: int, messages: list, is_batch: bool) -> str:
    """messages: list of {message_id, media_type, media_group_id, caption}"""
    code = await generate_code()
    await db.create_file_link(
        code,
        {
            "messages": messages,
            "is_batch": is_batch,
            "backup_chat_id": BACKUP_CHANNEL,
            "created_by": admin_id,
            "protect_content": None,  # None = follow global setting
            "auto_delete": None,      # None = follow global setting
        },
    )
    return code


def build_deep_link(code: str) -> str:
    return f"https://t.me/{config.BOT_USERNAME}?start={code}"


def extract_code(text: str) -> str:
    """Accepts either a bare code or a full deep link (any of
    t.me/<bot>?start=<code>, telegram.me/<bot>?start=<code>, with or
    without a scheme) and returns just the code. If `text` isn't a
    recognizable link, it's returned unchanged/stripped so a bare code
    still passes straight through."""
    text = text.strip()
    looks_like_link = (
        "://" in text
        or text.lower().startswith("t.me/")
        or text.lower().startswith("telegram.me/")
        or text.lower().startswith("www.t.me/")
    )
    if not looks_like_link:
        return text

    url = text if "://" in text else f"https://{text}"
    query = parse_qs(urlparse(url).query)
    start_vals = query.get("start")
    if start_vals and start_vals[0]:
        return start_vals[0]

    # No ?start= param found (e.g. a malformed/partial link) — fall back
    # to the original text so the caller's "no such link" error is clear
    # rather than silently misbehaving.
    return text
    
