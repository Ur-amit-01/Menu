# File Store Bot

Admins send files to this bot → it backs them up in a private channel → returns a
shareable deep link (`t.me/bot?start=CODE`). Anyone opening that link gets the
file(s) delivered, optionally gated by a "join these channels first" check, with
optional auto-delete after a timer.

## Architecture

Two separate settings layers, on purpose:

| Layer | File | Editable while running? | Holds |
|---|---|---|---|
| **config** | `config.py` | No — env vars read once at boot | API/bot tokens, DB creds, admin id list, backup channel id, port |
| **settings** | `plugins/helper/settings.py` | Yes — via `/setting`, stored in Mongo, cached in memory | force-sub channels, auto-delete timer, protect-content flag, start text, custom caption |

### Database (MongoDB)

* **users** — one doc per user (`join_date`, `last_seen`), plus a `join_requests`
  map used for "request to join" style force-sub channels: when a user submits a
  join request to one of those channels, it's recorded here and treated as
  satisfying the force-sub check even before a human approves it.
* **files** — one doc per shareable link: `_id` is the short link code, `messages`
  is the list of `{message_id, media_type, file_id, caption, media_group_id}`
  entries stored in the backup channel, plus per-link overrides for
  `protect_content` / `auto_delete` (both `None` = fall back to the global setting).
* **settings** — the raw key/value store behind the settings layer above.
* **pending_deletions** — auto-delete jobs, so a restart doesn't lose them
  (`plugins/filestore/deletion.py` restores these at boot).

### Filters (`plugins/helper/filters.py`)

* `admin_filter` — gates every admin-only command against `config.ADMINS`.
* `ensure_subscribed()` — the force-sub check, called at the top of `/start`
  (and easy to add to any other handler). Admins always bypass it.

### File sender (`plugins/filestore/delivery.py`)

1. Tries a single `forward_messages()` call straight from the backup channel —
   fast, and Telegram keeps albums grouped automatically.
2. If that fails (backup message deleted, etc.) it re-uploads each item from its
   stored `file_id`, regrouping consecutive album items via `send_media_group`.

### Auto-delete (`plugins/filestore/deletion.py`)

Every scheduled deletion is written to `pending_deletions` before the
`asyncio.sleep`, so `restore_pending_deletions()` (called once at boot) can
resume anything still pending after a restart.

## Commands

**Everyone**
* `/start` — bare: welcome message. With a payload (`/start CODE`): delivers
  that link's file(s), after the force-sub check.
* `/help`

**Admins only**
* Send any file directly → stored + linked immediately.
* `/batch` → `/done` (or `/cancel`) — collect several files into one link.
  *Batch sessions live in memory; a restart mid-batch loses the in-progress
  batch, not any already-finished link.*
* `/range_files SOURCE_CHAT_ID FIRST_ID LAST_ID` — bulk-import an existing
  range of messages the bot can already see into one new link.
* `/delete_link CODE` — deletes the link and its backed-up copies.
* `/broadcast` — reply to a message (or `/broadcast some text`) to send it to
  every known user.
* `/setting` — inline panel to edit force-sub channels, auto-delete timer,
  protect-content, start text, custom caption.
* `/stats` — users / links / files / uptime.

## Environment variables

```
API_ID=
API_HASH=
BOT_TOKEN=
DB_URL=mongodb://...
DB_NAME=filestore_bot
ADMINS=123456 234567
PREMIUM_USERS=123456 234567  # optional — space-separated ids; skip force-sub + promo sticker
BACKUP_CHANNEL=-100...      # bot must be admin here
LOG_CHANNEL=-100...          # optional
PORT=8080
START_PIC=                   # optional
```

## Premium users & promo sticker

* **Premium users** (`PREMIUM_USERS` env var, space-separated Telegram ids) skip
  the force-sub gate and never see the promo sticker — see
  `plugins/helper/filters.py:is_premium()`.
* **Promo sticker + button** — configurable live via `/setting`: send a sticker
  to set it, plus an optional button label/URL. It's shown right after the
  start message and right after every file delivery (unless the recipient is
  a premium user). See `plugins/helper/promo.py`.

## Running

```bash
pip install -r requirements.txt
python bot.py
```

Or with Docker:

```bash
docker build -t filestore-bot .
docker run --env-file .env filestore-bot
```

`app.py` is a minimal Flask app for platforms (Koyeb, Render, etc.) that require
a bound port to keep a free-tier service alive; it doesn't do anything else.
