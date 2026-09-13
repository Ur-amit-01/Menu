"""
start_message.py — builds/sends the bot's "start" content: an optional
photo plus the start_text setting.

No buttons are attached here — the dynamic nested menu
(plugins/menu/navigation.py) is what users tap through now, shown right
after this via show_root_menu(). Shared by:
  - plugins/filestore/start.py   (bare /start, and the "🔄 Get Files Again"
    button callback fired from an auto-delete notice — see deletion.py)

Kept in its own module (rather than inside start.py) so deletion.py can
import it without a circular import through delivery.py -> deletion.py.
"""
from plugins.helper.settings import settings


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
      
