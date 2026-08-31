"""Telegram/Gmail -> event suggestions -> (human review in Notion) -> promote to calendar_events."""
import datetime as dt
import difflib
import logging

from sqlalchemy import select

from config import settings
from db import SessionLocal
from models import CalendarEvent, EventSuggestion, GmailProcessedMessage, TelegramSyncState
from services import event_extraction, gmail_client, notion_suggestions, telegram_client
from services.sync_service import _push_to_notion, _upsert_postgres
from services.types import NormalizedEvent

logger = logging.getLogger("suggestion_service")

DUPLICATE_TITLE_SIMILARITY_THRESHOLD = 0.6
DUPLICATE_DATE_WINDOW = dt.timedelta(days=1)
GMAIL_CHANNEL = "gmail"
GMAIL_FOUNDERS_CHANNEL = "gmail_founders"
GMAIL_MAX_UNREAD_PER_SYNC = 50
GMAIL_EVENT_PURPOSE = "event"


def _gmail_accounts() -> list[tuple[str, str]]:
    """(channel, token_path) for every enabled Gmail account -- all share the
    same OAuth client (google_client_secret_path), just different end-user
    accounts/tokens."""
    accounts = [(GMAIL_CHANNEL, settings.google_token_path)] if settings.gmail_suggestions_enabled else []
    if settings.google_founders_gmail_enabled:
        accounts.append((GMAIL_FOUNDERS_CHANNEL, settings.google_founders_token_path))
    return accounts


def _channels() -> list[str]:
    if not settings.telegram_channels:
        return []
    return [c.strip() for c in settings.telegram_channels.split(",") if c.strip()]


def _titles_match(a: str, b: str) -> bool:
    ratio = difflib.SequenceMatcher(None, a.lower().strip(), b.lower().strip()).ratio()
    return ratio >= DUPLICATE_TITLE_SIMILARITY_THRESHOLD


def _is_duplicate(session, title: str, start_at: dt.datetime) -> bool:
    """A similar-titled entry already exists (as a suggestion or a confirmed calendar
    event) around the same date -- skip creating a new suggestion for it."""
    window_start = start_at - DUPLICATE_DATE_WINDOW
    window_end = start_at + DUPLICATE_DATE_WINDOW

    existing_suggestions = session.scalars(
        select(EventSuggestion).where(EventSuggestion.start_at.between(window_start, window_end))
    )
    if any(s.title and _titles_match(s.title, title) for s in existing_suggestions):
        return True

    existing_events = session.scalars(
        select(CalendarEvent).where(
            CalendarEvent.start_at.between(window_start, window_end),
            CalendarEvent.is_deleted.is_(False),
        )
    )
    return any(_titles_match(e.title, title) for e in existing_events)


def _is_past(start_at: dt.datetime) -> bool:
    return start_at < dt.datetime.now(dt.timezone.utc)


def _create_suggestion(session, channel: str, message_id: str, message_text: str, message_date: dt.datetime, extracted) -> EventSuggestion:
    suggestion = EventSuggestion(
        channel=channel,
        message_id=message_id,
        message_text=message_text,
        message_date=message_date,
        title=extracted.title,
        location=extracted.location,
        start_at=extracted.start_at,
        end_at=extracted.end_at,
    )
    session.add(suggestion)
    session.flush()

    try:
        suggestion.notion_page_id = notion_suggestions.create_page(suggestion)
        suggestion.sync_status = "synced"
    except Exception as exc:  # noqa: BLE001
        logger.exception("Failed to create Notion suggestion page for %s:%s", channel, message_id)
        suggestion.sync_status = "error"
        suggestion.sync_error = str(exc)

    return suggestion


def ingest_telegram() -> dict:
    channels = _channels()
    if not channels:
        return {"fetched": 0, "suggested": 0}

    session = SessionLocal()
    try:
        cursors = {
            row.channel: row.last_message_id
            for row in session.scalars(select(TelegramSyncState).where(TelegramSyncState.channel.in_(channels)))
        }

        messages = telegram_client.fetch_new_messages(
            settings.telegram_api_id, settings.telegram_api_hash, settings.telegram_session_path, channels, cursors
        )

        suggested = 0
        duplicates_skipped = 0
        past_events_skipped = 0
        max_seen: dict[str, int] = {}
        for msg in messages:
            max_seen[msg.channel] = max(max_seen.get(msg.channel, 0), msg.message_id)

            extracted = event_extraction.extract_event(msg.text, msg.date)
            if extracted is None:
                continue

            if extracted.start_at and _is_past(extracted.start_at):
                past_events_skipped += 1
                continue

            if extracted.start_at and _is_duplicate(session, extracted.title, extracted.start_at):
                duplicates_skipped += 1
                continue

            _create_suggestion(session, msg.channel, str(msg.message_id), msg.text, msg.date, extracted)
            suggested += 1

        for channel, last_id in max_seen.items():
            state = session.get(TelegramSyncState, channel)
            if state is None:
                state = TelegramSyncState(channel=channel, last_message_id=last_id)
                session.add(state)
            else:
                state.last_message_id = max(state.last_message_id, last_id)

        session.commit()
        return {
            "fetched": len(messages),
            "suggested": suggested,
            "duplicates_skipped": duplicates_skipped,
            "past_events_skipped": past_events_skipped,
        }
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


def _ingest_gmail_account(session, channel: str, token_path: str) -> dict:
    messages = gmail_client.fetch_unread_messages(
        settings.google_client_secret_path, token_path, max_results=GMAIL_MAX_UNREAD_PER_SYNC
    )

    suggested = 0
    duplicates_skipped = 0
    past_events_skipped = 0
    already_seen_skipped = 0
    for msg in messages:
        already_processed = session.get(GmailProcessedMessage, (channel, msg.message_id, GMAIL_EVENT_PURPOSE))
        if already_processed is not None:
            already_seen_skipped += 1
            continue

        message_text = f"From: {msg.sender}\nSubject: {msg.subject}\n\n{msg.body_text}"
        extracted = event_extraction.extract_event(message_text, msg.date)
        session.add(GmailProcessedMessage(account=channel, message_id=msg.message_id, purpose=GMAIL_EVENT_PURPOSE))
        session.flush()

        if extracted is None:
            continue

        if extracted.start_at and _is_past(extracted.start_at):
            past_events_skipped += 1
            continue

        if extracted.start_at and _is_duplicate(session, extracted.title, extracted.start_at):
            duplicates_skipped += 1
            continue

        _create_suggestion(session, channel, msg.message_id, message_text, msg.date, extracted)
        suggested += 1

    return {
        "fetched": len(messages),
        "suggested": suggested,
        "duplicates_skipped": duplicates_skipped,
        "past_events_skipped": past_events_skipped,
        "already_seen_skipped": already_seen_skipped,
    }


def ingest_gmail() -> dict:
    accounts = _gmail_accounts()
    if not accounts:
        return {"fetched": 0, "suggested": 0}

    session = SessionLocal()
    try:
        totals = {"fetched": 0, "suggested": 0, "duplicates_skipped": 0, "past_events_skipped": 0, "already_seen_skipped": 0}
        for channel, token_path in accounts:
            try:
                result = _ingest_gmail_account(session, channel, token_path)
                for key, value in result.items():
                    totals[key] += value
            except Exception:  # noqa: BLE001 -- one account failing shouldn't sink the other
                logger.exception("Gmail ingest failed for account %s", channel)

        session.commit()
        return totals
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


def process_reviewed() -> dict:
    session = SessionLocal()
    try:
        reviewed_pages = notion_suggestions.find_reviewed_pages()

        accepted = declined = 0
        promoted_by_source: dict[str, list[NormalizedEvent]] = {}

        for page in reviewed_pages:
            suggestion = session.scalar(
                select(EventSuggestion).where(EventSuggestion.notion_page_id == page["id"])
            )
            if suggestion is None or suggestion.status != "pending":
                continue

            status_value = page["properties"]["Status"]["select"]["name"]

            if status_value == "Accepted":
                source = suggestion.channel if suggestion.channel in (GMAIL_CHANNEL, GMAIL_FOUNDERS_CHANNEL) else "telegram"
                promoted_by_source.setdefault(source, []).append(
                    NormalizedEvent(
                        external_id=f"{suggestion.channel}:{suggestion.message_id}",
                        title=suggestion.title or "(no title)",
                        description=suggestion.message_text[:500],
                        location=suggestion.location,
                        start_at=suggestion.start_at or suggestion.message_date,
                        end_at=suggestion.end_at,
                        is_all_day=suggestion.is_all_day,
                        timezone=None,
                        etag=None,
                        source_updated_at=None,
                        is_deleted=False,
                    )
                )
                new_status = "accepted"
            elif status_value == "Declined":
                new_status = "declined"
            else:
                continue

            try:
                notion_suggestions.archive_page(suggestion.notion_page_id)
            except Exception:  # noqa: BLE001 -- one bad page shouldn't roll back every other reviewed
                # suggestion in this batch (previously an unhandled exception here aborted the whole
                # process_reviewed() call, undoing every accept/decline already processed in the same
                # run). Left "pending" so it's retried next sync -- safe to retry since an accepted
                # promotion is idempotent (matched by external_id/etag in _upsert_postgres).
                logger.exception("Failed to archive suggestion page %s", suggestion.notion_page_id)
                continue

            suggestion.status = new_status
            if new_status == "accepted":
                accepted += 1
            else:
                declined += 1

        all_touched = []
        for source, events in promoted_by_source.items():
            all_touched.extend(_upsert_postgres(session, events, source, source))
        if all_touched:
            _push_to_notion(session, all_touched)

        session.commit()
        return {"reviewed": len(reviewed_pages), "accepted": accepted, "declined": declined}
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()
