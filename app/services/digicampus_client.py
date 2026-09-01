"""DigiCampus (digicampus.fi -- the Moodle instance shared by several Finnish
universities, used here for University of Helsinki courses) assignment-deadline
client.

Structurally this is another Moodle deadline feed like MyCourses, but different
enough that it gets its own client rather than going through ical_calendar:

  * The course code lives in CATEGORIES ("MAT12001_s2026"), not embedded in the
    SUMMARY suffix the way Aalto MyCourses does it. Helsinki codes are
    "<letters><digits>" with no separator, so they don't match
    course_sync.COURSE_CODE_PATTERN -- but we read them straight off CATEGORIES,
    not by pattern-matching a title, so that's fine.
  * Event titles are Finnish. Moodle exports each assignment as several timed
    events (submission opens / submission due / peer-review opens / grading
    period ends); only the genuine cut-off events are wanted, matched by Finnish
    deadline wording (see _is_deadline).
  * The export only covers Moodle's "recent and upcoming" range (~last week +
    next ~60 days), narrower than the 180-day horizon ical_calendar assumes. So
    this clips to its own shorter window, and sync_service._sync_digicampus
    feeds those same bounds to _mark_dropped_from_window_as_deleted so a
    deadline that simply hasn't entered the feed yet isn't tombstoned.

Fed into sync_service as source="digicampus" and categorised as Assignments,
exactly like MyCourses/A+ deadlines -- including the opportunistic course-code ->
Course relation match in course_sync.py (which only links if a Course row for
that code already exists; nothing derives Helsinki courses yet).
"""
import datetime as dt
import hashlib
import logging
import re

import httpx
import icalendar

from services.types import NormalizedEvent

logger = logging.getLogger("digicampus_client")

# Kept deliberately narrower than ical_calendar.FETCH_WINDOW_FUTURE_DAYS: the
# DigiCampus export horizon is ~60 days, so anything past this is "not in the
# feed yet", not "removed from the feed". sync_service._sync_digicampus passes
# these to _mark_dropped_from_window_as_deleted as the authoritative window.
FETCH_WINDOW_PAST_DAYS = 7
FETCH_WINDOW_FUTURE_DAYS = 45

# Moodle appends a semester tag to the course shortname in CATEGORIES, e.g.
# "MAT12001_s2026" (s = syksy/autumn, k = kevät/spring). Strip it back to the
# bare code so it matches a Course.code.
_SEMESTER_SUFFIX = re.compile(r"_[a-zA-ZäöåÄÖÅ]{0,6}\d{2,4}$")

# Finnish (and English, just in case the site language flips) wording that marks
# a real cut-off rather than an "opens" announcement. "loppu" catches
# "arviointiaika loppuu", "päätty" catches "päättyy", etc.
_DEADLINE_WORDS = re.compile(
    r"(määräaika|viimeistään|päätty|loppu|sulkeutu|umpeutu|deadline|closes|due)",
    re.IGNORECASE,
)
_OPENS_WORDS = re.compile(
    r"(alkaa|aukea|avautu|opens)",
    re.IGNORECASE,
)


def _is_deadline(title: str) -> bool:
    return bool(_DEADLINE_WORDS.search(title)) and not _OPENS_WORDS.search(title)


def _course_code(categories) -> str | None:
    """First CATEGORIES entry with the semester suffix stripped, or None."""
    if categories is None:
        return None
    if isinstance(categories, list):
        categories = categories[0] if categories else None
        if categories is None:
            return None
    values = [str(c) for c in getattr(categories, "cats", [])] or [str(categories)]
    first = values[0].strip()
    if not first:
        return None
    return _SEMESTER_SUFFIX.sub("", first) or None


def _as_utc(value) -> dt.datetime:
    """icalendar hands back a date for all-day events and a datetime otherwise;
    DigiCampus deadlines are always timed and UTC ("...T080000Z"), but be
    defensive about naive values and bare dates all the same."""
    if isinstance(value, dt.datetime):
        if value.tzinfo is None:
            return value.replace(tzinfo=dt.timezone.utc)
        return value.astimezone(dt.timezone.utc)
    return dt.datetime(value.year, value.month, value.day, tzinfo=dt.timezone.utc)


def _content_etag(*parts: str) -> str:
    return hashlib.sha256("|".join(parts).encode()).hexdigest()


def fetch_events(
    ical_url: str,
    window_past_days: int = FETCH_WINDOW_PAST_DAYS,
    window_future_days: int = FETCH_WINDOW_FUTURE_DAYS,
) -> list[NormalizedEvent]:
    response = httpx.get(ical_url, timeout=30, follow_redirects=True)
    response.raise_for_status()
    calendar = icalendar.Calendar.from_ical(response.content)

    now = dt.datetime.now(dt.timezone.utc)
    window_start = now - dt.timedelta(days=window_past_days)
    window_end = now + dt.timedelta(days=window_future_days)

    events: list[NormalizedEvent] = []
    for component in calendar.walk("VEVENT"):
        title = str(component.get("SUMMARY", "")).strip()
        if not _is_deadline(title):
            continue

        start_at = _as_utc(component["DTSTART"].dt)
        if not window_start <= start_at <= window_end:
            continue

        description = str(component["DESCRIPTION"]).strip() if "DESCRIPTION" in component else None
        code = _course_code(component.get("CATEGORIES"))

        last_modified = component.get("LAST-MODIFIED")
        source_updated_at = _as_utc(last_modified.dt) if last_modified else None

        events.append(
            NormalizedEvent(
                external_id=f"digicampus:{component['UID']}",
                title=title,
                description=description,
                location=None,
                start_at=start_at,
                end_at=None,
                is_all_day=False,
                timezone=None,
                etag=_content_etag(title, start_at.isoformat(), code or "", description or ""),
                source_updated_at=source_updated_at,
                is_deleted=False,
                course_code=code,
            )
        )

    return events
