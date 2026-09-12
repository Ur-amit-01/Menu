"""
admin_settings.py — /setting panel.

Buttons trigger a "waiting for reply" state per-admin (AWAITING dict); the
next plain text message that admin sends is captured as the new value.

The "Reset to Defaults" button asks for confirmation via an inline
Yes/No keyboard (Telegram bots have no native two-choice confirm dialog),
then wipes every setting back to DEFAULTS and posts the pre-reset values
back as a plain text backup so nothing customized is lost for good.
"""
import html as html_lib

from pyrogram import Client, filters
from pyrogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message

from plugins.helper.filters import admin_filter
from plugins.helper.promo import store_promo_sticker_ref
from plugins.helper.settings import settings, DEFAULTS
from plugins.helper.start_message import _arrange_buttons
from plugins.helper.time_parser import format_time, parse_time

# admin_id -> setting key currently being edited
AWAITING: dict = {}

FIELD_LABELS = {
    "force_sub_channels": "Force-sub channels (space separated ids/usernames, or 'none')",
    "auto_delete_time": "Auto-delete timer (e.g. '10m', '1h', or '0' to disable)",
    "protect_content": "Protect content — reply 'on' or 'off'",
    "start_text": "Start message text (use {mention} for the user's mention)",
    "start_photo": "Start message photo — send a photo, or reply with a direct image URL, or 'none' to remove it",
    "custom_caption": "Extra caption line appended to delivered files (or 'none')",
    "leave_message": "DM sent when a user leaves a force-sub channel (use {mention}, {chat_title}, or 'none' to disable)",
    "promo_sticker": "Promotional sticker — send a sticker, or 'none' to remove it (sent after the start message and after every file delivery)",
    "promo_button_text": "Promo sticker button label (or 'none' to remove the button)",
    "promo_button_url": "Promo sticker button URL — must start with http:// or https:// (or 'none' to remove the button)",
}


def _panel_text() -> str:
    s = settings.all()
    fsub = ", ".join(str(c) for c in s["force_sub_channels"]) or "none"
    auto_del = format_time(s["auto_delete_time"]) if s["auto_delete_time"] else "disabled"
    return (
        "⚙️ <b>Bot settings</b>\n\n"
        f"• Force-sub channels: <code>{fsub}</code>\n"
        f"• Auto-delete: <code>{auto_del}</code>\n"
        f"• Protect content: <code>{s['protect_content']}</code>\n"
        f"• Custom caption: <code>{s['custom_caption'] or 'none'}</code>\n"
        f"• Start photo: <code>{'set' if s.get('start_photo') else 'none'}</code>\n"
        f"• Leave message: <code>{'enabled' if s['leave_message'] else 'disabled'}</code>\n"
        f"• Promo sticker: <code>{'set' if s.get('promo_sticker') else 'none'}</code>\n"
        f"• Promo button: <code>{s['promo_button_text'] + ' -> ' + s['promo_button_url'] if s.get('promo_button_text') and s.get('promo_button_url') else 'none'}</code>\n\n"
        "Tap a field below to change it."
    )


def _panel_buttons() -> InlineKeyboardMarkup:
    # Packed by label size (short labels pair up, long ones get their own
    # row) via the same _arrange_buttons helper the start-message materials
    # keyboard uses, so the panel doesn't waste space with one-per-row
    # buttons of wildly different widths.
    field_buttons = [
        InlineKeyboardButton("📡 Force-sub channels", callback_data="setting:force_sub_channels"),
        InlineKeyboardButton("⏱ Auto-delete timer", callback_data="setting:auto_delete_time"),
        InlineKeyboardButton("🛡 Protect content", callback_data="setting:protect_content"),
        InlineKeyboardButton("📝 Start text", callback_data="setting:start_text"),
        InlineKeyboardButton("🖼 Start photo", callback_data="setting:start_photo"),
        InlineKeyboardButton("💬 Custom caption", callback_data="setting:custom_caption"),
        InlineKeyboardButton("💔 Leave message", callback_data="setting:leave_message"),
        InlineKeyboardButton("🎉 Promo sticker", callback_data="setting:promo_sticker"),
        InlineKeyboardButton("🔗 Promo button text", callback_data="setting:promo_button_text"),
        InlineKeyboardButton("🔗 Promo button URL", callback_data="setting:promo_button_url"),
    ]
    rows = _arrange_buttons(field_buttons)
    # Reset stays on its own full-width row — it's a destructive action,
    # not just another field, so it shouldn't get packed in next to one.
    rows.append([InlineKeyboardButton("🔄 Reset to Defaults", callback_data="reset:ask")])
    return InlineKeyboardMarkup(rows)


@Client.on_message(filters.command("setting") & filters.private & admin_filter)
async def setting_panel(client, message: Message):
    await message.reply_text(_panel_text(), reply_markup=_panel_buttons())


@Client.on_callback_query(filters.regex(r"^setting:") & admin_filter)
async def setting_pick(client, query: CallbackQuery):
    key = query.data.split(":", 1)[1]
    AWAITING[query.from_user.id] = key
    await query.answer()
    await query.message.reply_text(
        f"✏️ Send the new value.\n<i>{FIELD_LABELS[key]}</i>"
    )


# ── Reset to defaults ────────────────────────────────────────────────────

@Client.on_callback_query(filters.regex(r"^reset:ask$") & admin_filter)
async def reset_ask(client, query: CallbackQuery):
    await query.answer()
    await query.message.reply_text(
        "⚠️ <b>Reset ALL settings to default?</b>\n\n"
        "Every field above will be overwritten back to its original "
        "default value. I'll send you the current values as a backup "
        "right before doing it, but this can't be undone automatically.\n\n"
        "Do you want to continue?",
        reply_markup=InlineKeyboardMarkup([[
            InlineKeyboardButton("✅ Yes, reset everything", callback_data="reset:yes"),
            InlineKeyboardButton("❌ Cancel", callback_data="reset:no"),
        ]]),
    )


@Client.on_callback_query(filters.regex(r"^reset:no$") & admin_filter)
async def reset_no(client, query: CallbackQuery):
    await query.answer("Cancelled — nothing changed.")
    await query.message.edit_text("❎ Reset cancelled — nothing changed.")


async def _send_backup(client, chat_id: int, previous: dict):
    """Post the pre-reset values back as plain text, chunked to stay under
    Telegram's message length limit. HTML-escaped since some values (like
    start_text) contain raw <a> tags that would otherwise be parsed."""
    header = "📦 <b>Previous settings (before reset)</b>\n"
    chunks, buf = [], header
    for key, value in previous.items():
        line = f"\n<b>{html_lib.escape(str(key))}</b>:\n<code>{html_lib.escape(str(value))}</code>\n"
        if len(buf) + len(line) > 3500:
            chunks.append(buf)
            buf = ""
        buf += line
    if buf:
        chunks.append(buf)
    for chunk in chunks:
        await client.send_message(chat_id, chunk)


@Client.on_callback_query(filters.regex(r"^reset:yes$") & admin_filter)
async def reset_yes(client, query: CallbackQuery):
    await query.answer("Resetting…")

    previous = settings.all()  # snapshot before wiping

    await _send_backup(client, query.message.chat.id, previous)

    for key, value in DEFAULTS.items():
        await settings.set(key, value)

    await query.message.edit_text("✅ All settings have been reset to default.")
    await client.send_message(
        query.message.chat.id, _panel_text(), reply_markup=_panel_buttons()
    )


def _has_pending_setting(_, __, message: Message) -> bool:
    if not message.from_user or message.from_user.id not in AWAITING:
        return False
    # let ordinary commands (e.g. /cancel, /batch) through untouched
    return not (message.text or "").startswith("/")


def _has_pending_photo_setting(_, __, message: Message) -> bool:
    return (
        message.from_user is not None
        and AWAITING.get(message.from_user.id) == "start_photo"
    )


def _has_pending_sticker_setting(_, __, message: Message) -> bool:
    return (
        message.from_user is not None
        and AWAITING.get(message.from_user.id) == "promo_sticker"
    )


@Client.on_message(
    filters.private & filters.photo & admin_filter & filters.create(_has_pending_photo_setting)
)
async def setting_apply_photo(client, message: Message):
    AWAITING.pop(message.from_user.id)
    await settings.set("start_photo", message.photo.file_id)
    await message.reply_text(_panel_text(), reply_markup=_panel_buttons())


@Client.on_message(
    filters.private & filters.sticker & admin_filter & filters.create(_has_pending_sticker_setting)
)
async def setting_apply_sticker(client, message: Message):
    AWAITING.pop(message.from_user.id)

    # Reposting into BACKUP_CHANNEL (rather than saving message.sticker.file_id
    # directly) is what makes this sticker survive a future BOT_TOKEN swap —
    # see plugins/helper/promo.py. It also doubles as validation: a sticker
    # forwarded from a "restrict saving content" chat fails right here,
    # immediately, instead of silently breaking the promo sticker later.
    try:
        ref = await store_promo_sticker_ref(client, message.sticker.file_id)
    except Exception:
        await message.reply_text(
            "❌ Telegram won't let me reuse that sticker (this usually happens with "
            "stickers forwarded from a chat that has 'restrict saving content' "
            "enabled).\n\nPlease send the sticker directly rather than forwarding it."
        )
        AWAITING[message.from_user.id] = "promo_sticker"  # let them retry
        return

    await settings.set("promo_sticker", ref)
    await message.reply_text(_panel_text(), reply_markup=_panel_buttons())


@Client.on_message(
    filters.private & filters.text & admin_filter & filters.create(_has_pending_setting)
)
async def setting_apply(client, message: Message):
    key = AWAITING.pop(message.from_user.id)
    raw = message.text.strip()

    try:
        if key == "force_sub_channels":
            if raw.lower() == "none":
                value = []
            else:
                value = []
                for tok in raw.split():
                    value.append(int(tok) if tok.lstrip("-").isdigit() else tok.lstrip("@"))
        elif key == "auto_delete_time":
            value = 0 if raw == "0" else parse_time(raw)
        elif key == "protect_content":
            value = raw.lower() in ("on", "true", "yes", "1")
        elif key == "promo_sticker":
            if raw.lower() == "none":
                value = ""
            else:
                await message.reply_text(
                    "❌ That's not a sticker. Send a sticker directly, or send "
                    "'none' to remove the promo sticker."
                )
                AWAITING[message.from_user.id] = key  # let them retry
                return
        elif key == "promo_button_url":
            if raw.lower() == "none":
                value = ""
            elif raw.lower().startswith(("http://", "https://")):
                value = raw
            else:
                await message.reply_text(
                    "❌ That doesn't look like a direct URL (must start with "
                    "http:// or https://). Send a valid URL, or 'none' to remove "
                    "the promo button."
                )
                AWAITING[message.from_user.id] = key  # let them retry
                return
        elif key in ("start_text", "custom_caption", "leave_message", "start_photo", "promo_button_text"):
            value = "" if raw.lower() == "none" else raw
        else:
            await message.reply_text("Unknown setting.")
            return
    except ValueError as e:
        await message.reply_text(f"❌ Couldn't parse that: {e}")
        AWAITING[message.from_user.id] = key  # let them retry
        return

    await settings.set(key, value)
    await message.reply_text(_panel_text(), reply_markup=_panel_buttons())

    
