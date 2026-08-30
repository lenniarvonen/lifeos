"""Generic iCal calendar export fetching, shared by MyCourses, Sisu (Aalto/
Helsinki), and the Exchange calendar.

This module only ever returns non-deleted events -- it has no concept of
deletion, since a single fetch can't tell "genuinely removed" apart from
"aged past this window's edge." That distinction is made one level up, in
sync_service.py's _mark_dropped_from_window_as_deleted: an event whose start
time falls safely inside the window (not near either edge) but is absent
from the fetch is treated as really having been dropped at the source.
"""
import datetime as dt
import hashlib

import httpx
import icalendar
import recurring_ical_events

from services.types import NormalizedEvent

FETCH_WINDOW_PAST_DAYS = 7
FETCH_WINDOW_FUTURE_DAYS = 180


def _as_datetime(value) -> tuple[dt.datetime, bool]:
    """icalendar gives a date.date for all-day events, datetime.datetime otherwise."""
    if isinstance(value, dt.datetime):
        if value.tzinfo is None:
            value = value.replace(tzinfo=dt.timezone.utc)
        return value, False
    return dt.datetime(value.year, value.month, value.day, tzinfo=dt.timezone.utc), True


def _content_etag(title: str, start_at: dt.datetime, end_at: dt.datetime | None, location: str | None, description: str | None) -> str:
    """Change-detection key derived from the event's actual displayed content, not
    the source's own timestamp fields -- some feeds (e.g. Sisu) set DTSTAMP to the
    feed's generation time rather than the event's last-modified time, and lack
    LAST-MODIFIED entirely, which would otherwise make every event look "changed"
    on every single fetch regardless of whether anything actually did."""
    fingerprint = "|".join([title, start_at.isoformat(), end_at.isoformat() if end_at else "", location or "", description or ""])
    return hashlib.sha256(fingerprint.encode()).hexdigest()


def fetch_events(ical_url: str) -> list[NormalizedEvent]:
    response = httpx.get(ical_url, timeout=30, follow_redirects=True)
    response.raise_for_status()
    calendar = icalendar.Calendar.from_ical(response.content)

    now = dt.datetime.now(dt.timezone.utc)
    window_start = now - dt.timedelta(days=FETCH_WINDOW_PAST_DAYS)
    window_end = now + dt.timedelta(days=FETCH_WINDOW_FUTURE_DAYS)

    occurrences = recurring_ical_events.of(calendar).between(window_start, window_end)

    events: list[NormalizedEvent] = []
    for component in occurrences:
        start_at, is_all_day = _as_datetime(component["DTSTART"].dt)
        end_at = None
        if "DTEND" in component:
            end_at, _ = _as_datetime(component["DTEND"].dt)

        last_modified = component.get("LAST-MODIFIED") or component.get("DTSTAMP")
        source_updated_at = last_modified.dt if last_modified else None
        if isinstance(source_updated_at, dt.datetime) and source_updated_at.tzinfo is None:
            source_updated_at = source_updated_at.replace(tzinfo=dt.timezone.utc)

        title = str(component.get("SUMMARY", "(no title)"))
        description = str(component["DESCRIPTION"]) if "DESCRIPTION" in component else None
        location = str(component["LOCATION"]) if "LOCATION" in component else None

        events.append(
            NormalizedEvent(
                external_id=str(component["UID"]),
                title=title,
                description=description,
                location=location,
                start_at=start_at,
                end_at=end_at,
                is_all_day=is_all_day,
                timezone=None,
                etag=_content_etag(title, start_at, end_at, location, description),
                source_updated_at=source_updated_at,
                is_deleted=False,
            )
        )

    return events
