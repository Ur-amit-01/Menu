"""
navigation.py — the reply-keyboard nested menu everyone (not just admins)
navigates.

How matching works (see the design note this was built from): a user's
current position in the tree is tracked per-user in `menu_sessions`
(a stack of node ids, root always at index 0). An incoming text message is
only ever compared against the CURRENT node's direct children — never the
whole tree — so identical labels in unrelated branches (e.g. "PW" under
both NEET and JEE) never collide. Two reserved labels, "🔙 Back" and
"🏠 Main Menu", are handled before that lookup and pop/reset the stack.

/start (a command) always resets the session to root — see the hook added
in plugins/filestore/start.py — so a stale or corrupted session can never
strand a user.
"""
import logging

from pyrogram import Client, filters
from pyrogram.types import KeyboardButton, Message, ReplyKeyboardMarkup

from plugins.helper.db import db
from plugins.menu.content import deliver_node_messages

logger = logging.getLogger(__name__)

BACK_LABEL = "🔙 Back"
MAIN_MENU_LABEL = "🏠 Main Menu"
_RESERVED = {BACK_LABEL, MAIN_MENU_LABEL}


def _button_text(node: dict) -> str:
    return node["label"]


def _pack_rows(labels: list, max_row_width: int = 26, max_per_row: int = 3) -> list:
    """Greedy width-aware packing, order preserved: keeps adding labels to
    the current row until the next one would push the row past
    max_row_width (character-count estimate) or hit max_per_row, then
    starts a new row. This is why short labels ("NEET") end up two/three
    to a row while long ones ("Complete 12th Part - 2 Notes") get a row
    to themselves instead of being squeezed and wrapped."""
    rows, row, row_width = [], [], 0
    for label in labels:
        added_width = len(label) + (2 if row else 0)  # +2 ~ gap between buttons
        if row and (row_width + added_width > max_row_width or len(row) >= max_per_row):
            rows.append(row)
            row, row_width = [], 0
        row.append(label)
        row_width += added_width
    if row:
        rows.append(row)
    return rows


def build_keyboard(children: list, at_root: bool) -> ReplyKeyboardMarkup:
    labels = [_button_text(c) for c in children]
    rows = [[KeyboardButton(t) for t in row] for row in _pack_rows(labels)]

    nav_row = []
    if not at_root:
        nav_row.append(KeyboardButton(BACK_LABEL))
    nav_row.append(KeyboardButton(MAIN_MENU_LABEL))
    rows.append(nav_row)

    return ReplyKeyboardMarkup(rows, resize_keyboard=True)


async def show_menu(client, chat_id: int, node: dict):
    children = await db.get_menu_children(node["_id"])
    at_root = node.get("parent_id") is None
    if not children:
        text = f"<b>{node['label']}</b>\n\n<i>Nothing here yet.</i>"
    else:
        text = f"<b>{node['label']}</b>\nPick an option below."
    await client.send_message(chat_id, text, reply_markup=build_keyboard(children, at_root))


async def show_root_menu(client, chat_id: int, user_id: int):
    """Resets user_id's session to root and shows the root keyboard. Safe
    to call unconditionally — used by /start and /menu."""
    root = await db.get_menu_root()
    await db.set_menu_session(user_id, [str(root["_id"])])
    await show_menu(client, chat_id, root)


@Client.on_message(filters.command("menu") & filters.private)
async def menu_command(client, message: Message):
    await show_root_menu(client, message.chat.id, message.from_user.id)


def _not_a_command(_, __, message: Message) -> bool:
    return not (message.text or "").startswith("/")


def _no_conflicting_admin_state(_, __, message: Message) -> bool:
    """Admins running /batch, /setting, or the menu-admin panel's own
    "type a label" / "send new file(s)" prompts must NOT have their reply
    swallowed as menu navigation. Imported lazily to dodge a circular
    import (admin_panel.py never needs anything from this module)."""
    if message.from_user is None:
        return True
    uid = message.from_user.id

    from plugins.filestore.upload import BATCH_SESSIONS
    if uid in BATCH_SESSIONS:
        return False

    from plugins.filestore.admin_settings import AWAITING as SETTINGS_AWAITING
    if uid in SETTINGS_AWAITING:
        return False

    from plugins.menu.admin_panel import LABEL_AWAITING, CONTENT_SESSIONS
    if uid in LABEL_AWAITING or uid in CONTENT_SESSIONS:
        return False

    return True


NAV_TEXT_FILTER = (
    filters.text
    & filters.private
    & filters.create(_not_a_command)
    & filters.create(_no_conflicting_admin_state)
)


@Client.on_message(NAV_TEXT_FILTER)
async def handle_menu_text(client, message: Message):
    user_id = message.from_user.id
    text = (message.text or "").strip()

    root = await db.get_menu_root()
    stack = await db.get_menu_session(user_id, root["_id"])

    if text == MAIN_MENU_LABEL:
        await show_root_menu(client, message.chat.id, user_id)
        return

    if text == BACK_LABEL:
        if len(stack) > 1:
            stack = stack[:-1]
            await db.set_menu_session(user_id, stack)
        node = await db.get_menu_node(stack[-1]) or root
        await show_menu(client, message.chat.id, node)
        return

    current = await db.get_menu_node(stack[-1]) or root
    children = await db.get_menu_children(current["_id"])
    match = next((c for c in children if _button_text(c) == text), None)
    if not match:
        # Not a recognized button for wherever this user currently is —
        # ignore rather than guess; ordinary chat isn't otherwise expected
        # here, and staying silent beats a confusing wrong-menu jump.
        return

    if match["type"] == "menu":
        stack = stack + [str(match["_id"])]
        await db.set_menu_session(user_id, stack)
        await show_menu(client, message.chat.id, match)
    else:
        sent = await deliver_node_messages(client, message.chat.id, match.get("messages", []))
        if not sent:
            await message.reply_text("⚠️ Nothing is stored here yet — ask an admin to add file(s).")
            
