"""
admin_panel.py — /menu_admin: an inline-button CRUD panel over the
menu_nodes tree (see plugins/helper/db.py).

Two small per-admin state dicts drive the "type something / send
something" steps a plain inline button can't do alone:

  LABEL_AWAITING[admin_id]  -> {"action": "addmenu"|"addcontent"|"rename",
                                "parent_id": str|None, "node_id": str|None}
                               set right before asking the admin to type a
                               label; consumed by the next text message.

  CONTENT_SESSIONS[admin_id] -> {"node_id": str, "items": [...]}
                               set while collecting new file(s) for a
                               content node (both for a brand new node and
                               for "Change file(s)" on an existing one).
                               `items` uses the exact same pending-item /
                               finalize pipeline as /batch — see
                               plugins/filestore/upload.py's
                               _pending_item / _finalize_entries / _claim_group,
                               imported below rather than re-implemented.

Both dicts are also imported by plugins/menu/navigation.py so an admin
mid-flow here never has a reply misread as menu navigation.
"""
import logging

from pyrogram import Client, filters
from pyrogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message

import config
from plugins.helper.db import db
from plugins.helper.filters import admin_filter
from plugins.menu.content import deliver_node_messages
from plugins.filestore.upload import (
    MEDIA_FILTER,
    _claim_group,
    _finalize_entries,
    _pending_item,
    _report_failures,
)

logger = logging.getLogger(__name__)

LABEL_AWAITING: dict = {}
CONTENT_SESSIONS: dict = {}
CONTENT_STATUS_MSG: dict = {}


def _content_controls(count: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([[
        InlineKeyboardButton(f"✅ Done ({count})", callback_data="madm:cfdone"),
        InlineKeyboardButton("❌ Cancel", callback_data="madm:cfcancel"),
    ]])


def _button_label(node: dict) -> str:
    return node["label"]


async def _render_menu_screen(node: dict):
    """Returns (text, markup) for a 'menu' type node's management screen."""
    children = await db.get_menu_children(node["_id"])
    is_root = node.get("parent_id") is None

    rows = []
    n = len(children)
    for i, child in enumerate(children):
        row = [InlineKeyboardButton(_button_label(child), callback_data=f"madm:open:{child['_id']}")]
        if n > 1:
            if i > 0:
                row.append(InlineKeyboardButton("⬆️", callback_data=f"madm:up:{child['_id']}"))
            if i < n - 1:
                row.append(InlineKeyboardButton("⬇️", callback_data=f"madm:down:{child['_id']}"))
        rows.append(row)

    rows.append([
        InlineKeyboardButton("➕ Menu", callback_data=f"madm:addmenu:{node['_id']}"),
        InlineKeyboardButton("➕ Content", callback_data=f"madm:addcontent:{node['_id']}"),
    ])
    if not is_root:
        rows.append([
            InlineKeyboardButton("✏️ Rename", callback_data=f"madm:rename:{node['_id']}"),
            InlineKeyboardButton("🗑 Delete", callback_data=f"madm:delete:{node['_id']}"),
        ])
        rows.append([InlineKeyboardButton("⬅ Back", callback_data=f"madm:open:{node['parent_id']}")])

    text = (
        f"📂 <b>{node['label']}</b>{' (root)' if is_root else ''}\n"
        f"{n} item(s) inside.\n\n"
        "Tap an item to manage it, or add a new one below."
    )
    return text, InlineKeyboardMarkup(rows)


def _content_screen_markup(node: dict) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton("🔁 Change file(s)", callback_data=f"madm:changefiles:{node['_id']}"),
            InlineKeyboardButton("◀️ Keep & Back", callback_data=f"madm:open:{node['parent_id']}"),
        ],
        [
            InlineKeyboardButton("✏️ Rename", callback_data=f"madm:rename:{node['_id']}"),
            InlineKeyboardButton("🗑 Delete", callback_data=f"madm:delete:{node['_id']}"),
        ],
    ])


async def _open_node(client, chat_id: int, node: dict, edit_message: Message = None):
    """Shows node's management screen. For a content node this DELIVERS
    its stored file(s) first (per spec: tapping it sends the file(s) plus
    a change/keep prompt), so it always sends fresh message(s) rather than
    editing in place. For a menu node it edits in place when possible
    (nicer nav feel), falling back to a new message."""
    if node["type"] == "content":
        sent = await deliver_node_messages(client, chat_id, node.get("messages", []))
        note = f"{len(sent)} file(s) delivered above." if sent else "⚠️ No file(s) stored here yet."
        await client.send_message(
            chat_id,
            f"📄 <b>{node['label']}</b>\n{note}\n\nChange these file(s), or keep as-is?",
            reply_markup=_content_screen_markup(node),
        )
        return

    text, markup = await _render_menu_screen(node)
    if edit_message is not None:
        try:
            await edit_message.edit_text(text, reply_markup=markup)
            return
        except Exception:
            pass  # message too old / unchanged / not editable — fall back to a new one
    await client.send_message(chat_id, text, reply_markup=markup)


@Client.on_message(filters.command("menu_admin") & filters.private & admin_filter)
async def menu_admin_command(client, message: Message):
    root = await db.get_menu_root()
    await _open_node(client, message.chat.id, root)


@Client.on_message(filters.command("menu_cancel") & filters.private & admin_filter)
async def menu_cancel_command(client, message: Message):
    had = LABEL_AWAITING.pop(message.from_user.id, None) or CONTENT_SESSIONS.pop(message.from_user.id, None)
    CONTENT_STATUS_MSG.pop(message.from_user.id, None)
    await message.reply_text("❎ Cancelled." if had else "Nothing to cancel.")


@Client.on_callback_query(filters.regex(r"^madm:open:") & admin_filter)
async def cb_open(client, query: CallbackQuery):
    node_id = query.data.split(":", 2)[2]
    node = await db.get_menu_node(node_id)
    if not node:
        await query.answer("That item no longer exists.", show_alert=True)
        return
    await query.answer()
    await _open_node(client, query.message.chat.id, node, edit_message=query.message)


@Client.on_callback_query(filters.regex(r"^madm:(up|down):") & admin_filter)
async def cb_reorder(client, query: CallbackQuery):
    direction_word, node_id = query.data.split(":", 2)[1:]
    node = await db.get_menu_node(node_id)
    if not node:
        await query.answer("That item no longer exists.", show_alert=True)
        return
    moved = await db.reorder_menu_node(node_id, -1 if direction_word == "up" else 1)
    if not moved:
        await query.answer("Already at that end.")
        return
    await query.answer()
    parent = await db.get_menu_node(node["parent_id"]) or await db.get_menu_root()
    await _open_node(client, query.message.chat.id, parent, edit_message=query.message)


@Client.on_callback_query(filters.regex(r"^madm:addmenu:") & admin_filter)
async def cb_add_menu(client, query: CallbackQuery):
    parent_id = query.data.split(":", 2)[2]
    LABEL_AWAITING[query.from_user.id] = {"action": "addmenu", "parent_id": parent_id, "node_id": None}
    await query.answer()
    await query.message.reply_text(
        "✏️ Send the label for the new <b>submenu</b>.\n(/menu_cancel to abort)"
    )


@Client.on_callback_query(filters.regex(r"^madm:addcontent:") & admin_filter)
async def cb_add_content(client, query: CallbackQuery):
    parent_id = query.data.split(":", 2)[2]
    LABEL_AWAITING[query.from_user.id] = {"action": "addcontent", "parent_id": parent_id, "node_id": None}
    await query.answer()
    await query.message.reply_text(
        "✏️ Send the label for the new <b>content item</b>.\n(/menu_cancel to abort)"
    )


@Client.on_callback_query(filters.regex(r"^madm:rename:") & admin_filter)
async def cb_rename(client, query: CallbackQuery):
    node_id = query.data.split(":", 2)[2]
    node = await db.get_menu_node(node_id)
    if not node:
        await query.answer("That item no longer exists.", show_alert=True)
        return
    LABEL_AWAITING[query.from_user.id] = {"action": "rename", "parent_id": str(node["parent_id"]), "node_id": node_id}
    await query.answer()
    await query.message.reply_text(
        f"✏️ Send the new label for <b>{node['label']}</b>.\n(/menu_cancel to abort)"
    )


@Client.on_callback_query(filters.regex(r"^madm:delete:") & admin_filter)
async def cb_delete_ask(client, query: CallbackQuery):
    node_id = query.data.split(":", 2)[2]
    node = await db.get_menu_node(node_id)
    if not node:
        await query.answer("That item no longer exists.", show_alert=True)
        return
    await query.answer()
    warn = (
        " and everything nested under it" if node["type"] == "menu" else ""
    )
    await query.message.edit_text(
        f"⚠️ Delete <b>{node['label']}</b>{warn}?\n"
        "This removes it from the menu tree. Already-shared files stay in the backup channel.",
        reply_markup=InlineKeyboardMarkup([[
            InlineKeyboardButton("✅ Yes, delete", callback_data=f"madm:delyes:{node_id}"),
            InlineKeyboardButton("❌ No", callback_data=f"madm:open:{node_id}"),
        ]]),
    )


@Client.on_callback_query(filters.regex(r"^madm:delyes:") & admin_filter)
async def cb_delete_confirm(client, query: CallbackQuery):
    node_id = query.data.split(":", 2)[2]
    node = await db.get_menu_node(node_id)
    if not node:
        await query.answer("Already gone.", show_alert=True)
        return
    parent_id = node["parent_id"]
    count = await db.delete_menu_node_recursive(node_id)
    await query.answer(f"Deleted {count} item(s).")
    parent = await db.get_menu_node(parent_id) if parent_id else await db.get_menu_root()
    await _open_node(client, query.message.chat.id, parent, edit_message=query.message)


async def _begin_content_collection(client, chat_id: int, admin_id: int, node_id: str):
    CONTENT_SESSIONS[admin_id] = {"node_id": node_id, "items": []}
    CONTENT_STATUS_MSG.pop(admin_id, None)
    await client.send_message(
        chat_id,
        "<b>📥 Send the new file(s) now</b> (one by one, or as an album).\nTap Done when finished.",
        reply_markup=_content_controls(0),
    )


@Client.on_callback_query(filters.regex(r"^madm:changefiles:") & admin_filter)
async def cb_change_files(client, query: CallbackQuery):
    node_id = query.data.split(":", 2)[2]
    node = await db.get_menu_node(node_id)
    if not node:
        await query.answer("That item no longer exists.", show_alert=True)
        return
    await query.answer()
    await _begin_content_collection(client, query.message.chat.id, query.from_user.id, node_id)


def _is_content_edit_active(_, __, message: Message) -> bool:
    return message.from_user is not None and message.from_user.id in CONTENT_SESSIONS


def _is_label_awaiting(_, __, message: Message) -> bool:
    return (
        message.from_user is not None
        and message.from_user.id in LABEL_AWAITING
        and not (message.text or "").startswith("/")
    )


# Registered in group -1 so it's checked before /batch's own media handler
# (group 0) — an admin mid content-edit never has their file swallowed by
# the unrelated /batch "generate link?" flow, without upload.py needing to
# know anything about this module.
@Client.on_message(
    filters.private & admin_filter & MEDIA_FILTER & filters.create(_is_content_edit_active),
    group=-1,
)
async def handle_content_media(client, message: Message):
    admin_id = message.from_user.id
    if message.media_group_id:
        if not _claim_group(message.media_group_id):
            return
        try:
            group_msgs = await client.get_media_group(message.chat.id, message.id)
        except Exception as e:
            logger.warning(f"get_media_group failed in menu content edit, falling back: {e}")
            group_msgs = [message]
        items = [_pending_item(m) for m in group_msgs]
    else:
        items = [_pending_item(message)]

    session = CONTENT_SESSIONS.get(admin_id)
    if session is None:
        return  # session ended between the filter check and here — drop it
    session["items"].extend(items)
    session["items"].sort(key=lambda it: it["src_message_id"])
    try:
        await message.react(emoji="👍")
    except Exception:
        pass

    count = len(session["items"])
    old = CONTENT_STATUS_MSG.pop(admin_id, None)
    if old:
        try:
            await old.delete()
        except Exception:
            pass
    CONTENT_STATUS_MSG[admin_id] = await message.reply_text(
        f"<b>📥 {count} file(s) received so far.</b>", reply_markup=_content_controls(count)
    )


@Client.on_callback_query(filters.regex(r"^madm:cfcancel$") & admin_filter)
async def cb_content_cancel(client, query: CallbackQuery):
    admin_id = query.from_user.id
    CONTENT_SESSIONS.pop(admin_id, None)
    CONTENT_STATUS_MSG.pop(admin_id, None)
    await query.answer()
    await query.message.edit_text("❎ Cancelled — file(s) unchanged.")


@Client.on_callback_query(filters.regex(r"^madm:cfdone$") & admin_filter)
async def cb_content_done(client, query: CallbackQuery):
    admin_id = query.from_user.id
    session = CONTENT_SESSIONS.get(admin_id)
    if session is None:
        await query.answer("Nothing pending — that session expired.", show_alert=True)
        return
    if not session["items"]:
        await query.answer("Send at least one file first.", show_alert=True)
        return

    CONTENT_SESSIONS.pop(admin_id, None)
    CONTENT_STATUS_MSG.pop(admin_id, None)
    await query.answer()
    await query.message.edit_text("<b>⏳ Saving file(s)...</b>", reply_markup=None)

    entries, failures = await _finalize_entries(client, session["items"])
    await _report_failures(client, failures, f"Menu content update by {query.from_user.mention}")

    if not entries:
        detail = failures[0][2] if failures else "unknown error"
        note = " Check the log channel for details." if config.LOG_CHANNEL else f" Reason: {detail}"
        await query.message.edit_text(f"❌ <b>Couldn't back up any of those file(s).</b>{note}")
        return

    await db.set_menu_node_messages(session["node_id"], entries)
    node = await db.get_menu_node(session["node_id"])
    await query.message.edit_text(f"✅ <b>{len(entries)} file(s) saved</b> — this content item is updated.")
    parent = await db.get_menu_node(node["parent_id"]) if node else None
    if parent:
        await _open_node(client, query.message.chat.id, parent)


@Client.on_message(
    filters.private & admin_filter & filters.text & filters.create(_is_label_awaiting),
    group=-1,
)
async def handle_label_reply(client, message: Message):
    admin_id = message.from_user.id
    state = LABEL_AWAITING.pop(admin_id)
    label = message.text.strip()
    if not label:
        await message.reply_text("❌ Empty label — try again.")
        LABEL_AWAITING[admin_id] = state
        return

    action = state["action"]

    if action == "rename":
        node = await db.get_menu_node(state["node_id"])
        if not node:
            await message.reply_text("❌ That item no longer exists.")
            return
        if await db.menu_label_taken(node["parent_id"], label, exclude_id=state["node_id"]):
            await message.reply_text("❌ A sibling item already uses that label. Send a different one.")
            LABEL_AWAITING[admin_id] = state
            return
        await db.rename_menu_node(state["node_id"], label)
        node["label"] = label
        await message.reply_text(f"✅ Renamed to <b>{label}</b>.")
        await _open_node(client, message.chat.id, node)
        return

    # addmenu / addcontent
    parent_id = state["parent_id"]
    if await db.menu_label_taken(parent_id, label):
        await message.reply_text("❌ A sibling item already uses that label. Send a different one.")
        LABEL_AWAITING[admin_id] = state
        return

    node_type = "menu" if action == "addmenu" else "content"
    new_id = await db.create_menu_node(parent_id, label, node_type)

    if node_type == "menu":
        await message.reply_text(f"✅ Submenu <b>{label}</b> created.")
        parent = await db.get_menu_node(parent_id) or await db.get_menu_root()
        await _open_node(client, message.chat.id, parent)
    else:
        await message.reply_text(f"✅ Content item <b>{label}</b> created.")
        await _begin_content_collection(client, message.chat.id, admin_id, str(new_id))
      
