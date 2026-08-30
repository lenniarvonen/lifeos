"""Interactive Telegram bot: long-polls for messages from the allowed user and
lets them add calendar events via free text + inline-button confirmation. Also
runs a duration-calibration loop: after a bot-added event ends, it asks how
long it actually took, and feeds confirmed durations back into future
extraction prompts so estimates improve over time (see event_extraction.py)."""
import datetime as dt
import logging
import threading
import zoneinfo

from sqlalchemy import select

from config import settings
from db import SessionLocal
from models import CalendarEvent
from services import event_extraction, sync_service, task_service, telegram_bot_client

logger = logging.getLogger("telegram_bot_service")

LOCAL_TZ = zoneinfo.ZoneInfo("Europe/Helsinki")

CALIBRATION_LOOKBACK = 50
# Only check in on events that ended within this window and haven't been asked
# yet -- caps how far back a missed check-in (e.g. app was asleep) gets chased.
CHECKIN_LOOKBACK = dt.timedelta(days=2)

_stop_event = threading.Event()
_thread: threading.Thread | None = None

# In-memory pending add-event confirmations, keyed by (chat_id, message_id) of the
# bot's own confirmation message -- ephemeral by design; losing one on restart just
# means the user re-sends the event text, no need to persist this to Postgres.
_pending: dict[tuple[int, int], event_extraction.ExtractedEvent] = {}

_CONFIRM_KEYBOARD = {
    "inline_keyboard": [[
        {"text": "✅ Add", "callback_data": "confirm"},
        {"text": "❌ Cancel", "callback_data": "cancel"},
    ]]
}


def _format_confirmation(extracted: event_extraction.ExtractedEvent) -> str:
    start_local = extracted.start_at.astimezone(LOCAL_TZ)
    lines = [f"*{extracted.title}*", f"🕐 {start_local.strftime('%a %d %b · %H:%M')}"]
    if extracted.location:
        lines.append(f"📍 {extracted.location}")
    lines.append("\nAdd this to your calendar?")
    return "\n".join(lines)


def _calibration_context(session) -> str | None:
    events = session.scalars(
        select(CalendarEvent)
        .where(CalendarEvent.source == "telegram_bot", CalendarEvent.duration_confirmed_minutes.is_not(None))
        .order_by(CalendarEvent.start_at.desc())
        .limit(CALIBRATION_LOOKBACK)
    ).all()
    if not events:
        return None

    lines = []
    for event in events:
        start_local = event.start_at.astimezone(LOCAL_TZ)
        planned = int((event.end_at - event.start_at).total_seconds() // 60) if event.end_at else None
        lines.append(
            f"{start_local.strftime('%a %H:%M')} '{event.title}' planned {planned}min "
            f"actual {event.duration_confirmed_minutes}min"
        )
    return "\n".join(lines)


def _find_pending_checkin(session, message: dict) -> CalendarEvent | None:
    """A reply to one of our check-in messages answers that specific event's duration
    question -- matched via Telegram's native reply-to. Strict: never guesses which
    event a reply-to is about."""
    reply_to = message.get("reply_to_message")
    if not reply_to:
        return None
    return session.scalar(
        select(CalendarEvent).where(
            CalendarEvent.source == "telegram_bot",
            CalendarEvent.duration_checkin_message_id == reply_to["message_id"],
            CalendarEvent.duration_confirmed_minutes.is_(None),
        )
    )


def _find_latest_unanswered_checkin(session) -> CalendarEvent | None:
    return session.scalar(
        select(CalendarEvent)
        .where(
            CalendarEvent.source == "telegram_bot",
            CalendarEvent.duration_asked_at.is_not(None),
            CalendarEvent.duration_confirmed_minutes.is_(None),
        )
        .order_by(CalendarEvent.duration_asked_at.desc())
    )


def _resolve_duration_minutes(text: str, event: CalendarEvent) -> int | None:
    normalized = text.strip().lower()
    planned_minutes = int((event.end_at - event.start_at).total_seconds() // 60) if event.end_at else None
    if normalized in ("same", "as planned", "as expected") and planned_minutes is not None:
        return planned_minutes
    return event_extraction.parse_duration_reply(text)


def _apply_duration(session, chat_id: int, event: CalendarEvent, minutes: int) -> None:
    event.duration_confirmed_minutes = minutes
    session.commit()
    telegram_bot_client.send_message(
        settings.telegram_bot_token, chat_id, f"Got it -- logged {minutes} min for '{event.title}'."
    )


def _handle_task_command(chat_id: int, text: str) -> None:
    token = settings.telegram_bot_token
    title = text[len("/task") :].strip()
    if not title:
        telegram_bot_client.send_message(token, chat_id, "Usage: /task <description>")
        return

    session = SessionLocal()
    try:
        task = task_service.create_task(session, title)
        synced = task.sync_status == "synced"  # read while still attached to an open session
        task_title = task.title
    finally:
        session.close()

    if synced:
        telegram_bot_client.send_message(token, chat_id, f"✅ Added task: {task_title}")
    else:
        telegram_bot_client.send_message(
            token, chat_id, f"⚠️ Saved '{task_title}' locally but Notion sync failed -- check the logs."
        )


def _handle_message(message: dict) -> None:
    token = settings.telegram_bot_token
    chat_id = message["chat"]["id"]
    text = message.get("text")
    if not text:
        return

    if text.startswith("/task"):
        _handle_task_command(chat_id, text)
        return

    session = SessionLocal()
    try:
        # Strict match: a native reply-to always answers that specific check-in,
        # even if the reply text doesn't look like a duration -- tell the user we
        # couldn't parse it rather than silently treating it as a new event.
        strict_match = _find_pending_checkin(session, message)
        if strict_match is not None:
            minutes = _resolve_duration_minutes(text, strict_match)
            if minutes is None:
                telegram_bot_client.send_message(
                    token, chat_id, "Couldn't parse a duration from that -- try e.g. '1.5 hours' or '45 min'."
                )
            else:
                _apply_duration(session, chat_id, strict_match, minutes)
            return

        # Loose fallback: no reply-to (easy to forget), but there's an outstanding
        # check-in and this message parses as a duration -- treat it as the answer.
        # If it doesn't parse as a duration, fall through to normal event-add below,
        # so a genuine new event sent while a check-in is pending isn't misread.
        fallback_match = _find_latest_unanswered_checkin(session)
        if fallback_match is not None:
            minutes = _resolve_duration_minutes(text, fallback_match)
            if minutes is not None:
                _apply_duration(session, chat_id, fallback_match, minutes)
                return

        calibration = _calibration_context(session)
    finally:
        session.close()

    extracted = event_extraction.parse_event_command(text, dt.datetime.now(dt.timezone.utc), calibration)
    if extracted is None:
        telegram_bot_client.send_message(
            token, chat_id, "Couldn't find an event in that -- try including a date/time."
        )
        return

    sent = telegram_bot_client.send_message(
        token, chat_id, _format_confirmation(extracted), reply_markup=_CONFIRM_KEYBOARD
    )
    _pending[(chat_id, sent["message_id"])] = extracted


def _handle_callback_query(callback_query: dict) -> None:
    token = settings.telegram_bot_token
    data = callback_query.get("data")
    message = callback_query["message"]
    chat_id = message["chat"]["id"]
    message_id = message["message_id"]

    extracted = _pending.pop((chat_id, message_id), None)
    if extracted is None:
        telegram_bot_client.answer_callback_query(
            token, callback_query["id"], "This one's expired -- just send the event again."
        )
        return

    if data == "confirm":
        session = SessionLocal()
        try:
            event = sync_service.create_bot_event(session, extracted)
            telegram_bot_client.edit_message_text(
                token, chat_id, message_id, f"✅ Added to *{event.category}*: {event.title}"
            )
        except Exception:  # noqa: BLE001 -- report failure in-chat rather than crash the poll loop
            logger.exception("Failed to create bot event")
            telegram_bot_client.edit_message_text(
                token, chat_id, message_id, "⚠️ Something went wrong adding that -- check the logs."
            )
        finally:
            session.close()
    else:
        telegram_bot_client.edit_message_text(token, chat_id, message_id, "❌ Cancelled")

    telegram_bot_client.answer_callback_query(token, callback_query["id"])


def _handle_update(update: dict) -> None:
    if "message" in update:
        sender_id = update["message"].get("from", {}).get("id")
        if sender_id != settings.telegram_bot_allowed_user_id:
            logger.warning("Ignoring Telegram bot message from unauthorized user %s", sender_id)
            return
        _handle_message(update["message"])

    elif "callback_query" in update:
        sender_id = update["callback_query"].get("from", {}).get("id")
        if sender_id != settings.telegram_bot_allowed_user_id:
            logger.warning("Ignoring Telegram bot callback from unauthorized user %s", sender_id)
            return
        _handle_callback_query(update["callback_query"])


def send_duration_checkins() -> int:
    """Ask about any bot-added event whose end_at has passed and hasn't been asked
    about yet. Called on the regular sync interval, same as the other sync jobs."""
    if not settings.telegram_bot_token or not settings.telegram_bot_allowed_user_id:
        return 0

    token = settings.telegram_bot_token
    chat_id = settings.telegram_bot_allowed_user_id
    now = dt.datetime.now(dt.timezone.utc)

    session = SessionLocal()
    try:
        due = session.scalars(
            select(CalendarEvent).where(
                CalendarEvent.source == "telegram_bot",
                CalendarEvent.is_deleted.is_(False),
                CalendarEvent.end_at.is_not(None),
                CalendarEvent.end_at <= now,
                CalendarEvent.end_at > now - CHECKIN_LOOKBACK,
                CalendarEvent.duration_asked_at.is_(None),
            )
        ).all()

        for event in due:
            sent = telegram_bot_client.send_message(
                token, chat_id, f"How long did *{event.title}* actually take? (e.g. '1.5 hours', '45 min', 'same')"
            )
            event.duration_asked_at = now
            event.duration_checkin_message_id = sent["message_id"]

        session.commit()
        return len(due)
    finally:
        session.close()


def _poll_loop() -> None:
    token = settings.telegram_bot_token
    offset = None
    while not _stop_event.is_set():
        try:
            updates = telegram_bot_client.get_updates(token, offset, timeout=25)
        except Exception:  # noqa: BLE001 -- keep polling alive across transient network errors
            logger.exception("Telegram bot getUpdates failed, retrying shortly")
            _stop_event.wait(5)
            continue

        for update in updates:
            offset = update["update_id"] + 1
            try:
                _handle_update(update)
            except Exception:  # noqa: BLE001 -- one bad update shouldn't kill the poll loop
                logger.exception("Failed to handle Telegram bot update")


def start() -> None:
    global _thread
    if not settings.telegram_bot_token or not settings.telegram_bot_allowed_user_id:
        return
    _stop_event.clear()
    _thread = threading.Thread(target=_poll_loop, daemon=True, name="telegram-bot-poll")
    _thread.start()


def stop() -> None:
    _stop_event.set()
