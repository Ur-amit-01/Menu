"""
start_message.py — builds/sends the bot's "start" content: an optional
photo plus the start_text setting.

No buttons are attached to the start message itself anymore — the
dynamic nested menu (plugins/menu/navigation.py) is what users tap
through now, shown right after this via show_root_menu(). Shared by:
  - plugins/filestore/start.py   (bare /start, and the "🔄 Get Files Again"
    button callback fired from an auto-delete notice — see deletion.py)

Kept in its own module (rather than inside start.py) so deletion.py can
import it without a circular import through delivery.py -> deletion.py.

_arrange_buttons lives here (not admin_settings.py) purely for historical
reasons — it started life packing the old start-message material buttons
and admin_settings.py's /setting panel later reused it for its own field
buttons. It has nothing to do with the start message anymore, but moving
it would mean editing admin_settings.py's import too, so it stays put.
"""
from plugins.helper.settings import settings

# A row is filled with buttons as long as the combined label length stays
# under this, so short labels pair up while long ones get a full row to
# themselves and never get visually cramped. Used by admin_settings.py's
# /setting panel (see docstring above).
_MAX_ROW_WIDTH = 30
_MAX_PER_ROW = 2


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


async def send_start_message(client, chat_id: int, mention: str = None):
    """Send the admin-configured start photo (if any) with the start_text
    caption/message to chat_id. No reply_markup — see module docstring."""
    text = settings.get("start_text")
    if mention:
        try:
            text = text.format(mention=mention)
        except (KeyError, IndexError):
            pass  # text has no {mention} placeholder (or a stray brace) — send as-is

    photo = settings.get("start_photo")
    if photo:
        await client.send_photo(chat_id, photo=photo, caption=text)
    else:
        await client.send_message(chat_id, text, disable_web_page_preview=True)
      
