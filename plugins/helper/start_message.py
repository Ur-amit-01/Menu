"""
start_message.py — builds/sends the bot's "start" content: an optional
photo plus the start_text setting, with the NEET material list rendered
as inline buttons (instead of text links). Shared by:
  - plugins/filestore/start.py   (bare /start, and the "🔄 Get Files Again"
    button callback fired from an auto-delete notice — see deletion.py)

Kept in its own module (rather than inside start.py) so deletion.py can
import it without a circular import through delivery.py -> deletion.py.
"""
from pyrogram.types import InlineKeyboardButton, InlineKeyboardMarkup

from plugins.helper.settings import settings
from plugins.filestore.linking import build_deep_link

# A row is filled with buttons as long as the combined label length stays
# under this, so short labels pair up ("HC Verma" + "NCERT Punch") while
# long ones get a full row to themselves and never get visually cramped.
_MAX_ROW_WIDTH = 30
# Hard cap on buttons per row — even if two short labels would fit width-
# wise, more than this starts looking cramped on narrow phone screens.
_MAX_PER_ROW = 2

# Static extra button shown below the material buttons on every start
# menu — not tied to start_materials, just a fixed link. Edit the label/URL
# here to change it.
_TEST_SERIES_BUTTON = InlineKeyboardButton(
    "📝 Test Series Bot", url="https://t.me/NEETopedia_test_bot"
)


def _arrange_buttons(buttons: list) -> list:
    """Greedily pack InlineKeyboardButton objects into rows, using each
    button's label length as its "width". Keeps short-label buttons
    together on one row and gives long-label buttons a row of their own,
    so no button's text ever gets visually cramped or cut off."""
    rows, row, row_width = [], [], 0
    for button in buttons:
        label_width = len(button.text)
        if row and (
            row_width + label_width > _MAX_ROW_WIDTH
            or len(row) >= _MAX_PER_ROW
        ):
            rows.append(row)
            row, row_width = [], 0
        row.append(button)
        row_width += label_width
    if row:
        rows.append(row)
    return rows


def _materials_keyboard() -> InlineKeyboardMarkup:
    materials = settings.get("start_materials") or []
    buttons = [
        InlineKeyboardButton(label, url=build_deep_link(code))
        for label, code in materials
    ]
    rows = _arrange_buttons(buttons) if buttons else []
    rows.append([_TEST_SERIES_BUTTON])
    return InlineKeyboardMarkup(rows)


async def send_start_message(client, chat_id: int, mention: str = None):
    """Send the admin-configured start photo (if any) with the start_text
    caption/message, plus a button for every configured material, to chat_id."""
    text = settings.get("start_text")
    if mention:
        try:
            text = text.format(mention=mention)
        except (KeyError, IndexError):
            pass  # text has no {mention} placeholder (or a stray brace) — send as-is

    reply_markup = _materials_keyboard()
    photo = settings.get("start_photo")
    if photo:
        await client.send_photo(chat_id, photo=photo, caption=text, reply_markup=reply_markup)
    else:
        await client.send_message(
            chat_id, text, disable_web_page_preview=True, reply_markup=reply_markup
        )
      
