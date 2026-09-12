"""
media.py — turn a pyrogram Message into a small, storable record, and turn
that record back into a delivered message when we can't just forward it.
"""
from typing import Optional, Tuple

from pyrogram.enums import ParseMode
from pyrogram.types import Message

# media_type -> attribute name on Message
_MEDIA_ATTRS = [
    "document", "video", "audio", "photo", "voice",
    "animation", "video_note", "sticker",
]


def extract_media(message: Message) -> Optional[Tuple[str, str, str]]:
    """Returns (media_type, file_id, caption) for the first media found, or None."""
    for media_type in _MEDIA_ATTRS:
        media = getattr(message, media_type, None)
        if media:
            caption = message.caption.html if message.caption else ""
            return media_type, media.file_id, caption
    if message.text:
        return "text", "", message.text.html
    return None


async def send_by_file_id(
    client,
    chat_id: int,
    media_type: str,
    file_id: str,
    caption: str = "",
    protect_content: bool = False,
    reply_markup=None,
):
    """Re-upload a piece of media purely from its file_id (no forwarding).

    caption is always the already-HTML-formatted string produced by
    extract_media() (Message.caption.html / Message.text.html), so
    parse_mode is pinned to HTML here — otherwise Pyrogram's default
    "combined" markdown+HTML parser can reinterpret stray characters
    (*, _, `, [ ) in the caption's plain-text portions and subtly mangle
    formatting that was already exactly right as HTML.
    """
    kwargs = dict(
        chat_id=chat_id,
        caption=caption or None,
        parse_mode=ParseMode.HTML,
        protect_content=protect_content,
        reply_markup=reply_markup,
    )
    if media_type == "document":
        return await client.send_document(document=file_id, **kwargs)
    if media_type == "video":
        return await client.send_video(video=file_id, **kwargs)
    if media_type == "photo":
        return await client.send_photo(photo=file_id, **kwargs)
    if media_type == "audio":
        return await client.send_audio(audio=file_id, **kwargs)
    if media_type == "voice":
        return await client.send_voice(voice=file_id, **kwargs)
    if media_type == "animation":
        return await client.send_animation(animation=file_id, **kwargs)
    if media_type == "video_note":
        kwargs.pop("caption", None)
        return await client.send_video_note(video_note=file_id, chat_id=chat_id,
                                             protect_content=protect_content,
                                             reply_markup=reply_markup)
    if media_type == "sticker":
        kwargs.pop("caption", None)
        return await client.send_sticker(sticker=file_id, chat_id=chat_id,
                                          protect_content=protect_content,
                                          reply_markup=reply_markup)
    if media_type == "text":
        return await client.send_message(chat_id=chat_id, text=caption or "‌",
                                          parse_mode=ParseMode.HTML,
                                          protect_content=protect_content,
                                          reply_markup=reply_markup)
    raise ValueError(f"Unsupported media_type: {media_type}")


def build_input_media(media_type: str, file_id: str, caption: str = ""):
    """Build an InputMedia* for use inside send_media_group (albums only)."""
    from pyrogram.types import InputMediaDocument, InputMediaVideo, InputMediaPhoto, InputMediaAudio

    if media_type == "document":
        return InputMediaDocument(media=file_id, caption=caption or None, parse_mode=ParseMode.HTML)
    if media_type == "video":
        return InputMediaVideo(media=file_id, caption=caption or None, parse_mode=ParseMode.HTML)
    if media_type == "photo":
        return InputMediaPhoto(media=file_id, caption=caption or None, parse_mode=ParseMode.HTML)
    if media_type == "audio":
        return InputMediaAudio(media=file_id, caption=caption or None, parse_mode=ParseMode.HTML)
    raise ValueError(f"{media_type} can't be part of an album")
