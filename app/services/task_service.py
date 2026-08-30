"""Daily tasks, added via the Telegram bot's /task command. No LLM parsing
involved -- the title (and any "tomorrow"/"on friday"-style date phrase) is
resolved with plain regex, so there's no misparse risk, no extra API cost, and
no need for a confirm-first step the way event-adding has."""
import datetime as dt
import logging
import re
import zoneinfo

from sqlalchemy import select

from config import settings
from db import SessionLocal
from models import Task
from services import notion_client, telegram_bot_client

logger = logging.getLogger("task_service")

LOCAL_TZ = zoneinfo.ZoneInfo("Europe/Helsinki")

_WEEKDAYS = ["monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday"]

_RELATIVE_DAYS_PATTERN = re.compile(r"\bin\s+(?P<n>\d+)\s+days?\b", re.IGNORECASE)
_DATE_PHRASE_PATTERN = re.compile(
    r"\b(?:(?P<next>next)\s+)?(?:(?:on|this)\s+)?"
    r"(?P<word>today|day after tomorrow|tomorrow|" + "|".join(_WEEKDAYS) + r")\b",
    re.IGNORECASE,
)


def _clean(text: str, start: int, end: int) -> str:
    remainder = text[:start] + text[end:]
    remainder = re.sub(r"\s{2,}", " ", remainder)
    return remainder.strip(" ,.-")


def resolve_date_phrase(text: str, today: dt.date) -> tuple[str, dt.date]:
    """Extracts a "tomorrow"/"on friday"/"next monday"/"in 3 days"-style phrase
    from `text`, returning (title_with_phrase_removed, resolved_date). Defaults
    to today if no recognizable phrase is found."""
    match = _RELATIVE_DAYS_PATTERN.search(text)
    if match:
        resolved = today + dt.timedelta(days=int(match.group("n")))
        return _clean(text, match.start(), match.end()), resolved

    match = _DATE_PHRASE_PATTERN.search(text)
    if match:
        word = match.group("word").lower()
        if word == "today":
            resolved = today
        elif word == "tomorrow":
            resolved = today + dt.timedelta(days=1)
        elif word == "day after tomorrow":
            resolved = today + dt.timedelta(days=2)
        else:
            days_ahead = (_WEEKDAYS.index(word) - today.weekday()) % 7
            if match.group("next"):
                days_ahead += 7
            resolved = today + dt.timedelta(days=days_ahead)
        return _clean(text, match.start(), match.end()), resolved

    return text.strip(), today


def create_task(session, text: str) -> Task:
    today = dt.datetime.now(LOCAL_TZ).date()
    title, task_date = resolve_date_phrase(text, today)

    task = Task(title=title, date=task_date)
    session.add(task)
    session.flush()

    try:
        task.notion_page_id = notion_client.create_task_page(task)
        task.sync_status = "synced"
        task.sync_error = None
        task.last_synced_at = dt.datetime.now(dt.timezone.utc)
    except Exception as exc:  # noqa: BLE001 -- report failure in-chat rather than crash the poll loop
        logger.exception("Failed to sync task %r to Notion", title)
        task.sync_status = "error"
        task.sync_error = str(exc)

    session.commit()
    return task


def sync_done_from_notion(session) -> int:
    """Reflects Notion's live done state back into Postgres -- checked before
    sending reminders so a task marked done in Notion correctly stops
    triggering them (Task.done is otherwise one-directional, Postgres ->
    Notion only).

    Two properties can mark a task done in Notion: the "Done" checkbox (ours)
    and "Tila" (a Status property the user added separately). Either one is
    treated as authoritative, and whichever one wasn't set gets reconciled to
    match so the two never look out of sync with each other."""
    if not settings.notion_tasks_database_id:
        return 0
    done_via_checkbox = notion_client.find_pages_with_checkbox(settings.notion_tasks_database_id, "Done")
    done_via_status = notion_client.find_pages_with_status(settings.notion_tasks_database_id, "Tila", "Done")
    done_page_ids = done_via_checkbox | done_via_status
    if not done_page_ids:
        return 0

    tasks = session.scalars(
        select(Task).where(Task.done.is_(False), Task.notion_page_id.in_(done_page_ids))
    ).all()
    for task in tasks:
        task.done = True
        if task.notion_page_id not in (done_via_checkbox & done_via_status):
            try:
                notion_client.set_task_done(task.notion_page_id)
            except Exception:  # noqa: BLE001 -- Postgres state is still correct either way
                logger.exception("Failed to reconcile Done/Tila for task %r", task.title)
    return len(tasks)


def send_due_reminders() -> int:
    """Reminds via the Telegram bot about any not-done task whose date has
    arrived, once per day per task, until it's marked done."""
    if not settings.telegram_bot_token or not settings.telegram_bot_allowed_user_id:
        return 0

    today = dt.datetime.now(LOCAL_TZ).date()
    session = SessionLocal()
    try:
        sync_done_from_notion(session)
        session.flush()  # SessionLocal has autoflush disabled -- without this, the query
        # below would still see the pre-sync done=False rows from the database.

        due = session.scalars(
            select(Task).where(Task.done.is_(False), Task.date <= today)
        ).all()
        due = [t for t in due if t.last_reminded_date is None or t.last_reminded_date < today]

        for task in due:
            days_overdue = (today - task.date).days
            if days_overdue <= 0:
                text = f"⏰ Reminder: *{task.title}* is due today."
            else:
                text = f"⏰ Reminder: *{task.title}* was due {days_overdue} day(s) ago."
            try:
                telegram_bot_client.send_message(settings.telegram_bot_token, settings.telegram_bot_allowed_user_id, text)
            except Exception:  # noqa: BLE001 -- one failed send shouldn't block the rest
                logger.exception("Failed to send due reminder for task %r", task.title)
                continue
            task.last_reminded_date = today

        session.commit()
        return len(due)
    finally:
        session.close()
