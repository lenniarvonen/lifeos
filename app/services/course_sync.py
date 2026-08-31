"""Derives a Courses database from MyCourses calendar titles (which embed
"<CODE> - <Name>, <type>, <start>-<end>") and mirrors it to Notion, with a
running-period-based Upcoming/Ongoing/Completed status. Also links MyCourses
Assignment events to their course via a Notion relation, matched by course
code appearing in the assignment's title/description.

NOTE: course<->assignment matching is unverified against real deadline data --
no deadlines have been published yet (see sync_service._sync_mycourses), so
this is a best-effort substring match that may need adjusting once real
MyCourses deadline events start appearing and their actual title format is
known."""
import datetime as dt
import logging
import re

import httpx
import icalendar
from sqlalchemy import select

from config import settings
from db import SessionLocal
from models import CalendarEvent, Course
from services import notion_client

logger = logging.getLogger("course_sync")

# Aalto uses two course-code shapes: hyphenated ("CS-C3240", "ELEC-A7100") and
# an older dotted form seen on some thesis/seminar courses ("SCI3025.kand").
# Shared with sync_service._MYCOURSES_COURSE_SUFFIX_PATTERN, which strips this
# same suffix off assignment titles -- keep both in sync if the shape changes.
COURSE_CODE_PATTERN = r"[A-Z]{2,}(?:-[A-Z0-9]+|\d+\.[a-zA-Z]+)"

_TITLE_PATTERN = re.compile(
    rf"^.*?/\s*(?P<code>{COURSE_CODE_PATTERN})\s*-\s*(?P<name>.+?),\s*[^,]+,\s*"
    r"(?P<start>\d{1,2}\.\d{1,2}\.\d{4})-(?P<end>\d{1,2}\.\d{1,2}\.\d{4})\s*$"
)


def _parse_finnish_date(value: str) -> dt.date:
    return dt.datetime.strptime(value, "%d.%m.%Y").date()


def _parse_course_title(summary: str) -> tuple[str, str, dt.date, dt.date] | None:
    match = _TITLE_PATTERN.match(summary)
    if not match:
        return None
    return (
        match.group("code"),
        match.group("name").strip(),
        _parse_finnish_date(match.group("start")),
        _parse_finnish_date(match.group("end")),
    )


def _compute_status(period_start: dt.date, period_end: dt.date, today: dt.date) -> str:
    if today < period_start:
        return "Upcoming"
    if today > period_end:
        return "Completed"
    return "Ongoing"


def _fetch_unique_courses(ical_url: str) -> dict[tuple[str, dt.date], tuple[str, dt.date]]:
    """Returns {(code, period_start): (name, period_end)}, deduped across every
    event in the raw feed (course metadata is embedded on every session event,
    not just once)."""
    response = httpx.get(ical_url, timeout=30, follow_redirects=True)
    response.raise_for_status()
    calendar = icalendar.Calendar.from_ical(response.content)

    courses: dict[tuple[str, dt.date], tuple[str, dt.date]] = {}
    for component in calendar.walk("VEVENT"):
        parsed = _parse_course_title(str(component.get("SUMMARY", "")))
        if parsed:
            code, name, period_start, period_end = parsed
            courses[(code, period_start)] = (name, period_end)
    return courses


def sync_courses(session, ical_url: str, university: str) -> dict:
    if not ical_url or not settings.notion_courses_database_id:
        return {"courses": 0}

    today = dt.datetime.now(dt.timezone.utc).date()
    parsed_courses = _fetch_unique_courses(ical_url)

    touched = 0
    for (code, period_start), (name, period_end) in parsed_courses.items():
        status = _compute_status(period_start, period_end, today)
        existing = session.scalar(
            select(Course).where(
                Course.university == university, Course.code == code, Course.period_start == period_start
            )
        )
        if existing is None:
            existing = Course(
                university=university, code=code, name=name, period_start=period_start, period_end=period_end
            )
            session.add(existing)
            session.flush()
        elif (
            existing.name == name
            and existing.period_end == period_end
            and existing.status == status
            and existing.notion_page_id is not None
        ):
            continue  # unchanged AND already synced successfully -- skip the Notion round-trip

        existing.name = name
        existing.period_end = period_end
        existing.status = status

        try:
            if existing.notion_page_id:
                notion_client.update_course_page(existing.notion_page_id, existing)
            else:
                existing.notion_page_id = notion_client.create_course_page(existing)
            existing.sync_status = "synced"
            existing.sync_error = None
            existing.last_synced_at = dt.datetime.now(dt.timezone.utc)
        except Exception as exc:  # noqa: BLE001 -- one bad course shouldn't sink the batch
            logger.exception("Failed to sync course %s to Notion", code)
            existing.sync_status = "error"
            existing.sync_error = str(exc)

        touched += 1

    session.commit()
    return {"courses": touched}


def match_assignments_to_courses(session) -> int:
    """Best-effort: link unmatched MyCourses Assignment events to a course by
    checking whether the course's code appears in the assignment's title or
    description. See module docstring -- unverified against real deadline data.

    Pushes the new Course relation to Notion immediately (rather than just
    setting course_id and waiting for some other sync step to touch the
    event) since nothing else is guaranteed to push it: the event's etag
    won't change from this, and sync_assignment_statuses/sync_assignment_display
    only push when status/short_title/visibility actually change. A push
    failure here is only logged, not retried -- course_id is already set
    afterward, so this event won't be reconsidered on a later run."""
    courses = session.scalars(select(Course)).all()
    if not courses:
        return 0

    unmatched = session.scalars(
        select(CalendarEvent).where(
            CalendarEvent.source == "mycourses",
            CalendarEvent.category == "Assignments",
            CalendarEvent.course_id.is_(None),
            CalendarEvent.is_deleted.is_(False),
        )
    ).all()

    matched = 0
    for event in unmatched:
        # Preferred: exact match on the code parsed off the raw title suffix
        # (see sync_service._split_course_details). Fallback for rows that
        # predate that column: substring-scan the title/description.
        if event.course_code:
            candidates = [c for c in courses if c.code == event.course_code]
        else:
            haystack = f"{event.title} {event.description or ''}"
            candidates = [c for c in courses if c.code in haystack]
        if not candidates:
            continue
        # Prefer the course whose period actually contains the assignment's date;
        # fall back to the most recently-started matching course code.
        containing = [c for c in candidates if c.period_start <= event.start_at.date() <= c.period_end]
        best = containing[0] if containing else max(candidates, key=lambda c: c.period_start)
        event.course = best
        matched += 1

        if event.notion_page_id:
            try:
                notion_client.update_page(event.notion_page_id, event)
            except Exception:  # noqa: BLE001 -- one bad page shouldn't sink the batch
                logger.exception("Failed to push course relation for assignment %s", event.external_id)

    if matched:
        session.commit()
        logger.info("Matched %d assignment(s) to courses", matched)
    return matched


def run() -> dict:
    session = SessionLocal()
    try:
        courses = 0
        if settings.mycourses_ical_url:
            courses += sync_courses(session, settings.mycourses_ical_url, "Aalto")["courses"]
        # Helsinki course-derivation isn't wired up yet: at time of writing the
        # Helsinki Moodle feed has no real course data (only site-wide notices),
        # and Helsinki Sisu's title format ("CODE, Name, Name, type date-date")
        # doesn't match _TITLE_PATTERN, which is tuned to Aalto MyCourses's
        # "session / CODE - Name, type, date-date" shape. Add once real Helsinki
        # deadline/course data exists to verify parsing against.

        matched = match_assignments_to_courses(session)
        return {"courses": courses, "assignments_matched": matched}
    finally:
        session.close()
