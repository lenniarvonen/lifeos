"""Telegram channel reading via a user-account client (Telethon).

The Bot API can't read channels the account merely follows as a subscriber --
only channels/groups where a bot was explicitly added as admin. Reading
followed channels requires logging in as the user's own account, same as the
Telegram app itself does.

Run this module directly on the host (not inside Docker) once, to complete the
interactive login (phone number, code, optional 2FA password) and produce the
session file that gets mounted into the container for all subsequent runs:

    cd app && python3 -m services.telegram_client --authorize
"""
from __future__ import annotations

import argparse
import dataclasses
import datetime as dt
import os
import sys

from telethon.sync import TelegramClient

BACKFILL_WINDOW_DAYS = 14
BACKFILL_MAX_MESSAGES = 200


@dataclasses.dataclass
class TelegramMessage:
    channel: str
    message_id: int
    text: str
    date: dt.datetime


@dataclasses.dataclass
class PendingReply:
    chat_id: int
    sender_name: str
    message_text: str
    message_date: dt.datetime


def _client(api_id: int, api_hash: str, session_path: str) -> TelegramClient:
    return TelegramClient(session_path, api_id, api_hash)


def authorize(api_id: int, api_hash: str, session_path: str) -> None:
    """One-time interactive login, run on the host."""
    with _client(api_id, api_hash, session_path) as client:
        client.start()
        print(f"Saved session to {session_path}")


def list_channels(api_id: int, api_hash: str, session_path: str) -> None:
    """Print every channel/group the account is a member of, with its ID.

    Channels without a public @username need to be referenced by this numeric
    ID in TELEGRAM_CHANNELS instead.
    """
    with _client(api_id, api_hash, session_path) as client:
        for dialog in client.iter_dialogs():
            entity = dialog.entity
            if not (getattr(entity, "broadcast", False) or getattr(entity, "megagroup", False)):
                continue
            username = getattr(entity, "username", None)
            print(f"{dialog.name!r:40} username={username or '(none, use id below)'!s:20} id={entity.id}")


def _resolve_channel(channel: str) -> str | int:
    """TELEGRAM_CHANNELS entries are usernames for public channels, or numeric
    IDs (from --list-channels) for private ones."""
    stripped = channel.lstrip("-")
    return int(channel) if stripped.isdigit() else channel


def fetch_new_messages(
    api_id: int, api_hash: str, session_path: str, channels: list[str], cursors: dict[str, int]
) -> list[TelegramMessage]:
    messages: list[TelegramMessage] = []
    cutoff = dt.datetime.now(dt.timezone.utc) - dt.timedelta(days=BACKFILL_WINDOW_DAYS)

    with _client(api_id, api_hash, session_path) as client:
        for channel in channels:
            min_id = cursors.get(channel, 0)
            entity = _resolve_channel(channel)
            for msg in client.iter_messages(entity, min_id=min_id, limit=BACKFILL_MAX_MESSAGES):
                if not msg.text:
                    continue
                if min_id == 0 and msg.date < cutoff:
                    continue
                messages.append(
                    TelegramMessage(channel=channel, message_id=msg.id, text=msg.text, date=msg.date)
                )

    return messages


def fetch_pending_replies(api_id: int, api_hash: str, session_path: str) -> list[PendingReply]:
    """1:1 person DMs (not groups/channels/bots) whose most recent message is
    unread and incoming -- i.e. still awaiting a reply."""
    results: list[PendingReply] = []
    with _client(api_id, api_hash, session_path) as client:
        for dialog in client.iter_dialogs():
            if not dialog.is_user or getattr(dialog.entity, "bot", False):
                continue
            if dialog.unread_count <= 0:
                continue
            last_message = dialog.message
            if last_message is None or last_message.out:  # .out = sent by me
                continue
            results.append(
                PendingReply(
                    chat_id=dialog.entity.id,
                    sender_name=dialog.name,
                    message_text=last_message.text or "",
                    message_date=last_message.date,
                )
            )
    return results


def mark_chats_read(api_id: int, api_hash: str, session_path: str, chat_ids: list[int]) -> None:
    """Marks each chat as read in Telegram itself (not just in our own tracking) --
    used when the user checks "Read" on a pending-reply's Notion page."""
    if not chat_ids:
        return
    with _client(api_id, api_hash, session_path) as client:
        for chat_id in chat_ids:
            client.send_read_acknowledge(chat_id)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--authorize", action="store_true")
    parser.add_argument("--list-channels", action="store_true")
    parser.add_argument("--api-id", type=int, default=os.environ.get("TELEGRAM_API_ID"))
    parser.add_argument("--api-hash", default=os.environ.get("TELEGRAM_API_HASH"))
    parser.add_argument("--session-path", default=os.environ.get("TELEGRAM_SESSION_PATH", "../secrets/telegram"))
    args = parser.parse_args()

    if args.authorize:
        authorize(args.api_id, args.api_hash, args.session_path)
    elif args.list_channels:
        list_channels(args.api_id, args.api_hash, args.session_path)
    else:
        print("Nothing to do. Pass --authorize or --list-channels.", file=sys.stderr)
        sys.exit(1)
