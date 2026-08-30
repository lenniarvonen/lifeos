"""Syncs Telegram DMs awaiting a reply into the Messages to Answer Notion
database. Bidirectional: replying/reading in Telegram resolves the Notion page
automatically (fully self-correcting, re-evaluated every run), and checking
"Read" on a page marks that chat read in Telegram too -- both directions
collapse into the same "no longer unread" state, so only one resolve path is
needed."""
import datetime as dt
import logging

from sqlalchemy import select

from config import settings
from db import SessionLocal
from models import TelegramPendingReply
from services import notion_client, telegram_client

logger = logging.getLogger("telegram_reply_sync")


def _apply_notion_read_marks(session) -> None:
    """Checking "Read" in Notion marks the chat read in Telegram itself -- done
    before fetch_pending_replies() below so the now-read chat is correctly
    excluded from `current` in this same run, and gets archived by the normal
    resolve path further down without any separate handling."""
    read_page_ids = notion_client.find_pages_with_checkbox(settings.notion_replies_database_id, "Read")
    if not read_page_ids:
        return

    chat_ids = [
        row.chat_id
        for row in session.scalars(
            select(TelegramPendingReply).where(
                TelegramPendingReply.resolved.is_(False), TelegramPendingReply.notion_page_id.in_(read_page_ids)
            )
        )
    ]
    try:
        telegram_client.mark_chats_read(
            settings.telegram_api_id, settings.telegram_api_hash, settings.telegram_session_path, chat_ids
        )
    except Exception:  # noqa: BLE001 -- fall through to the normal fetch either way
        logger.exception("Failed to mark chats read in Telegram: %s", chat_ids)


def sync_pending_replies() -> dict:
    if not settings.telegram_api_id or not settings.notion_replies_database_id:
        return {"pending": 0, "resolved": 0, "errors": 0}

    session = SessionLocal()
    try:
        _apply_notion_read_marks(session)

        current = telegram_client.fetch_pending_replies(
            settings.telegram_api_id, settings.telegram_api_hash, settings.telegram_session_path
        )
        current_by_chat = {p.chat_id: p for p in current}

        tracked = session.scalars(
            select(TelegramPendingReply).where(TelegramPendingReply.resolved.is_(False))
        ).all()

        resolved = 0
        for row in tracked:
            if row.chat_id not in current_by_chat:
                if row.notion_page_id:
                    try:
                        notion_client.archive_page(row.notion_page_id)
                    except Exception:  # noqa: BLE001 -- still mark resolved locally even if the archive call fails
                        logger.exception("Failed to archive resolved reply page for chat %s", row.chat_id)
                row.resolved = True
                resolved += 1

        errors = 0
        for chat_id, pending in current_by_chat.items():
            existing = session.get(TelegramPendingReply, chat_id)
            if existing is None:
                existing = TelegramPendingReply(chat_id=chat_id)
                session.add(existing)

            unchanged = (
                not existing.resolved
                and existing.message_text == pending.message_text
                and existing.notion_page_id is not None
            )
            existing.sender_name = pending.sender_name
            existing.message_text = pending.message_text
            existing.message_date = pending.message_date
            existing.resolved = False
            if unchanged:
                continue

            try:
                if existing.notion_page_id:
                    notion_client.update_reply_page(existing.notion_page_id, existing)
                else:
                    existing.notion_page_id = notion_client.create_reply_page(existing)
                existing.sync_status = "synced"
                existing.sync_error = None
                existing.last_synced_at = dt.datetime.now(dt.timezone.utc)
            except Exception as exc:  # noqa: BLE001 -- one bad chat shouldn't sink the batch
                logger.exception("Failed to sync pending reply for chat %s", chat_id)
                existing.sync_status = "error"
                existing.sync_error = str(exc)
                errors += 1

        session.commit()
        return {"pending": len(current_by_chat), "resolved": resolved, "errors": errors}
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()
