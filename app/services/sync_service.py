"""Orchestrates: source (Google Calendar / MyCourses) -> Postgres -> Notion."""
import dataclasses
import datetime as dt
import logging
import re
import uuid

from sqlalchemy import select

from config import settings
from db import SessionLocal
from models import CalendarEvent
from services import (
    aplus_client,
    digicampus_client,
    event_extraction,
    google_calendar,
    ical_calendar,
    notion_client,
)
from services.course_sync import COURSE_CODE_PATTERN
from services.types import NormalizedEvent

logger = logging.getLogger("sync_service")

_LLM_CATEGORIZED_SOURCES = {"telegram", "gmail", "gmail_founders", "telegram_bot"}

# Some sources (Sisu, MyCourses, Exchange) relabel a cancelled event's title/
# description in place rather than removing it from the feed -- this catches
# that regardless of source, English or Finnish wording.
_CANCELLED_PATTERN = re.compile(r"\b(cancelled|canceled|peruttu|peruutettu)\b", re.IGNORECASE)


def _is_cancelled(title: str, description: str | None) -> bool:
    return bool(_CANCELLED_PATTERN.search(f"{title} {description or ''}"))


ASSIGNMENT_DUE_SOON_WINDOW = dt.timedelta(hours=24)

# How far ahead of its due date an assignment's Notion page stays visible --
# further out than this, sync_assignment_display archives it so the table
# doesn't stack up with every deadline through the end of the semester.
ASSIGNMENT_VISIBILITY_WINDOW = dt.timedelta(days=14)


def _compute_assignment_status(due_at: dt.datetime, now: dt.datetime) -> str:
    if now > due_at:
        return "Overdue"
    if due_at - now <= ASSIGNMENT_DUE_SOON_WINDOW:
        return "Due soon"
    return "Upcoming"


def _assign_category(source: str, title: str, description: str | None) -> str | None:
    if source in ("mycourses", "aplus", "digicampus"):
        return "Assignments"
    if source == "sisu":
        return "Classes"
    if source == "sisu_helsinki":
        return "Classes (Helsinki)"
    if source == "exchange_calendar":
        return "Meetings"
    if source == "google_calendar_work":
        return "Work"
    if source in _LLM_CATEGORIZED_SOURCES:
        return event_extraction.classify_calendar_category(title, description)
    return None


def _upsert_postgres(
    session, events: list[NormalizedEvent], source: str, container_id: str
) -> list[CalendarEvent]:
    touched: list[CalendarEvent] = []

    for event in events:
        existing = session.scalar(
            select(CalendarEvent).where(
                CalendarEvent.source == source,
                CalendarEvent.external_id == event.external_id,
            )
        )

        if event.is_deleted and existing is None:
            continue  # never synced, nothing to do

        if (
            existing
            and existing.source_etag == event.etag
            and not event.is_deleted
            and not existing.is_deleted
        ):
            continue  # unchanged since last sync, skip
        # A row locally marked deleted (e.g. by _mark_dropped_from_window_as_deleted
        # during a flaky partial feed) but still present in the feed must NOT take
        # the skip above even when its content is byte-identical -- it has to fall
        # through so the branch below can clear is_deleted. The etag-only skip
        # otherwise leaves it tombstoned forever with no path back.

        if existing is None:
            existing = CalendarEvent(source=source, external_id=event.external_id)
            session.add(existing)

        if event.is_deleted:
            existing.is_deleted = True
        else:
            existing.calendar_id = container_id
            existing.title = event.title
            existing.description = event.description
            existing.location = event.location
            if event.course_code is not None:
                existing.course_code = event.course_code
            # Category is sticky: only assigned the first time an event is ever
            # categorized. Never recomputed afterward, even if the source event's
            # content changes later -- so a manual move to a different category
            # database in Notion is permanent, not just until the next content edit.
            if existing.category is None:
                existing.category = _assign_category(source, event.title, event.description)
            existing.start_at = event.start_at
            existing.end_at = event.end_at
            existing.is_all_day = event.is_all_day
            existing.timezone = event.timezone
            # A source that keeps a cancelled event in its feed but relabels it
            # (rather than dropping it, which event.is_deleted above already
            # handles) still needs to disappear from the calendar/Notion --
            # _push_to_notion already archives the page for any is_deleted event.
            existing.is_deleted = _is_cancelled(event.title, event.description)

        existing.source_etag = event.etag
        existing.source_updated_at = event.source_updated_at
        existing.sync_status = "pending"
        touched.append(existing)

    session.flush()
    return touched


def _mark_dropped_from_window_as_deleted(
    session,
    source: str,
    live_external_ids: set[str],
    *,
    past_days: int | None = None,
    future_days: int | None = None,
) -> list[CalendarEvent]:
    """ical_calendar.fetch_events only covers a rolling window (see its
    FETCH_WINDOW_*_DAYS), so an event's absence from one fetch doesn't
    reliably mean it was removed at the source -- it may simply have aged
    past the window's near edge. But for an event whose start time falls
    safely inside the window that was JUST fetched (with a day of buffer off
    each edge to absorb clock drift), the feed is authoritative for that
    date: if it's not present, the source really did cancel/reschedule it
    (e.g. a dropped exercise session), and Postgres/Notion should reflect
    that instead of keeping a stale entry around forever. Skipped entirely
    if the feed came back empty, so a transient empty response can't wipe
    out everything.

    past_days/future_days default to ical_calendar's window but are overridable
    for a source whose feed covers a different span -- DigiCampus's export only
    reaches ~60 days ahead, so it passes its own narrower bounds rather than
    tombstoning every deadline 45-180 days out that just isn't in the feed yet."""
    if not live_external_ids:
        return []

    past_days = ical_calendar.FETCH_WINDOW_PAST_DAYS if past_days is None else past_days
    future_days = ical_calendar.FETCH_WINDOW_FUTURE_DAYS if future_days is None else future_days

    now = dt.datetime.now(dt.timezone.utc)
    safe_start = now - dt.timedelta(days=past_days - 1)
    safe_end = now + dt.timedelta(days=future_days - 1)

    stale = session.scalars(
        select(CalendarEvent).where(
            CalendarEvent.source == source,
            CalendarEvent.is_deleted.is_(False),
            CalendarEvent.start_at >= safe_start,
            CalendarEvent.start_at <= safe_end,
            CalendarEvent.external_id.not_in(live_external_ids),
        )
    ).all()

    for event in stale:
        event.is_deleted = True
        event.sync_status = "pending"

    if stale:
        session.flush()
    return stale


def _push_to_notion(session, events: list[CalendarEvent]) -> tuple[int, int, int]:
    created = updated = errors = 0

    for event in events:
        try:
            if event.is_deleted:
                if event.notion_page_id:
                    notion_client.archive_page(event.notion_page_id)
                event.sync_status = "synced"
                event.last_synced_at = dt.datetime.now(dt.timezone.utc)
                continue

            if event.notion_page_id is None:
                recovered_id = notion_client.find_page_by_external_id(event.external_id, event.category)
                if recovered_id:
                    event.notion_page_id = recovered_id
                    notion_client.update_page(recovered_id, event)
                    updated += 1
                else:
                    event.notion_page_id = notion_client.create_page(event)
                    created += 1
            else:
                notion_client.update_page(event.notion_page_id, event)
                updated += 1

            event.sync_status = "synced"
            event.sync_error = None
            event.last_synced_at = dt.datetime.now(dt.timezone.utc)
        except Exception as exc:  # noqa: BLE001 -- one bad event shouldn't abort the batch
            logger.exception("Failed to sync event %s to Notion", event.external_id)
            event.sync_status = "error"
            event.sync_error = str(exc)
            errors += 1

    session.flush()
    return created, updated, errors


def _sync_google_calendar(
    session, calendar_id: str = "primary", source: str = "google_calendar", token_path: str | None = None
) -> tuple[list[CalendarEvent], int]:
    raw_events = google_calendar.list_events(
        settings.google_client_secret_path, token_path or settings.google_token_path, calendar_id=calendar_id
    )
    normalized = [google_calendar.normalize(raw) for raw in raw_events]
    touched = _upsert_postgres(session, normalized, source, calendar_id)
    return touched, len(raw_events)


_MYCOURSES_OPENS_PATTERN = re.compile(r"\bopens\b", re.IGNORECASE)

# Strips the "<CODE> - <Name>, <type>, <start>-<end>" course-detail suffix that
# Moodle appends to assignment titles (e.g. "coding test closes / CS-C3240 -
# Machine Learning, Lecture, 1.9.2025-10.10.2025" -> "coding test closes") --
# redundant now that the assignment is linked to its course via a relation
# (see course_sync.py), same suffix shape course_sync.py parses out. The course
# code is captured so _split_course_details can keep it after stripping -- it's
# the only place the code appears, so course_sync would otherwise have nothing
# to match the assignment to its Course row on.
_MYCOURSES_COURSE_SUFFIX_PATTERN = re.compile(
    rf"\s*/\s*(?P<code>{COURSE_CODE_PATTERN})\s*-\s*.+?,\s*[^,]+,\s*\d{{1,2}}\.\d{{1,2}}\.\d{{4}}-\d{{1,2}}\.\d{{1,2}}\.\d{{4}}\s*$"
)


def _split_course_details(title: str) -> tuple[str, str | None]:
    """(title without the "<CODE> - <Name>, <type>, <dates>" suffix, the CODE) --
    code is None when the title carries no such suffix."""
    match = _MYCOURSES_COURSE_SUFFIX_PATTERN.search(title)
    stripped = _MYCOURSES_COURSE_SUFFIX_PATTERN.sub("", title).strip()
    return stripped, (match.group("code") if match else None)


def _sync_mycourses(session) -> tuple[list[CalendarEvent], int]:
    normalized = ical_calendar.fetch_events(settings.mycourses_ical_url)
    # MyCourses re-exports Sisu's teaching schedule alongside genuine deadlines --
    # class-session events synced in from Sisu are marked with a "(Sisu)" suffix
    # on their description and would otherwise duplicate what Sisu already
    # provides as Classes/Exam. Only genuine deadlines should come from here.
    deadlines = [e for e in normalized if not (e.description or "").strip().endswith("(Sisu)")]
    # Moodle exports each assignment as an "X opens"/"X closes" pair -- only the
    # closing (deadline) event is wanted, not the opening announcement.
    deadlines = [e for e in deadlines if not _MYCOURSES_OPENS_PATTERN.search(e.title)]
    split = []
    for e in deadlines:
        stripped, code = _split_course_details(e.title)
        split.append(dataclasses.replace(e, title=stripped, course_code=code))
    deadlines = split
    touched = _upsert_postgres(session, deadlines, "mycourses", "mycourses")
    # course_code is derived from the title suffix, which _strip drops without
    # changing the content etag -- so a row stored before this parsing existed
    # (or before its course code could be read) is skipped by _upsert_postgres's
    # etag check and never gets it. Backfill it directly, independent of etag.
    codes_by_ext = {e.external_id: e.course_code for e in deadlines if e.course_code}
    if codes_by_ext:
        for row in session.scalars(
            select(CalendarEvent).where(
                CalendarEvent.source == "mycourses",
                CalendarEvent.external_id.in_(list(codes_by_ext)),
            )
        ):
            if row.course_code != codes_by_ext[row.external_id]:
                row.course_code = codes_by_ext[row.external_id]
        session.flush()
    # Membership is checked against the raw (pre-filter) feed, not `deadlines`:
    # a previously-stored mycourses row's external_id always came from an event
    # that passed the filter at creation time, so checking it against the full
    # raw feed (not the filtered-down creation set) is the correct liveness test.
    touched += _mark_dropped_from_window_as_deleted(session, "mycourses", {e.external_id for e in normalized})
    return touched, len(deadlines)


def _sync_aplus(session) -> tuple[list[CalendarEvent], int]:
    """A+ module deadlines -> Assignments, same shape as _sync_mycourses. Unlike
    the MyCourses ical feed, A+ is queried per enrolled course and returns every
    module regardless of date, so aplus_client already clips to the ical fetch
    window -- meaning _mark_dropped_from_window_as_deleted's window bounds line
    up and a module that genuinely disappears from A+ gets tombstoned. course_code
    is set on every event by aplus_client and folded into the etag, so no
    separate backfill pass is needed (cf. _sync_mycourses)."""
    normalized = aplus_client.fetch_events(
        settings.aplus_api_token_path,
        settings.aplus_api_base_url,
        ical_calendar.FETCH_WINDOW_PAST_DAYS,
        ical_calendar.FETCH_WINDOW_FUTURE_DAYS,
    )
    touched = _upsert_postgres(session, normalized, "aplus", "aplus")
    touched += _mark_dropped_from_window_as_deleted(session, "aplus", {e.external_id for e in normalized})
    return touched, len(normalized)


def _sync_digicampus(session) -> tuple[list[CalendarEvent], int]:
    """DigiCampus (digicampus.fi Moodle) assignment deadlines -> Assignments,
    same shape as _sync_aplus. digicampus_client already filters the feed's
    open/close event pairs down to genuine deadlines and clips to its own
    window, which is narrower than the other ical feeds' because the DigiCampus
    export horizon is only ~60 days -- so _mark_dropped_from_window_as_deleted is
    handed those same bounds, otherwise a deadline that just hasn't entered the
    feed yet would be tombstoned on every run. course_code is set off CATEGORIES
    and folded into the etag, so no separate backfill pass is needed (cf.
    _sync_mycourses)."""
    normalized = digicampus_client.fetch_events(settings.digicampus_ical_url)
    touched = _upsert_postgres(session, normalized, "digicampus", "digicampus")
    touched += _mark_dropped_from_window_as_deleted(
        session,
        "digicampus",
        {e.external_id for e in normalized},
        past_days=digicampus_client.FETCH_WINDOW_PAST_DAYS,
        future_days=digicampus_client.FETCH_WINDOW_FUTURE_DAYS,
    )
    return touched, len(normalized)


def _sync_sisu(session, ical_url: str | None = None, source: str = "sisu") -> tuple[list[CalendarEvent], int]:
    normalized = ical_calendar.fetch_events(ical_url or settings.sisu_ical_url)
    touched = _upsert_postgres(session, normalized, source, source)
    touched += _mark_dropped_from_window_as_deleted(session, source, {e.external_id for e in normalized})
    return touched, len(normalized)


def _sync_exchange_calendar(session) -> tuple[list[CalendarEvent], int]:
    normalized = ical_calendar.fetch_events(settings.exchange_calendar_ical_url)
    touched = _upsert_postgres(session, normalized, "exchange_calendar", "exchange_calendar")
    touched += _mark_dropped_from_window_as_deleted(session, "exchange_calendar", {e.external_id for e in normalized})
    return touched, len(normalized)


def create_bot_event(session, extracted: event_extraction.ExtractedEvent) -> CalendarEvent:
    """Writes a user-confirmed Telegram-bot event straight to Postgres + Notion --
    no dedup needed since each confirmation is a deliberate one-off action, not a
    re-fetched feed, so a fresh external_id per call is correct."""
    normalized = NormalizedEvent(
        external_id=f"bot:{uuid.uuid4()}",
        title=extracted.title,
        description=None,
        location=extracted.location,
        start_at=extracted.start_at,
        end_at=extracted.end_at,
        is_all_day=False,
        timezone=None,
        etag=None,
        source_updated_at=None,
        is_deleted=False,
    )
    touched = _upsert_postgres(session, [normalized], "telegram_bot", "telegram_bot")
    _push_to_notion(session, touched)
    session.commit()
    return touched[0]


def sync_deletions_from_notion() -> int:
    """Reverse direction of the normal source -> Postgres -> Notion push: detect
    events whose Notion page was deleted (archived) directly in the Notion app,
    and soft-delete the corresponding Postgres row to match -- same is_deleted
    semantics as a cancelled Google Calendar/MyCourses event, so it stays out of
    the calendar/digest but the row/history isn't destroyed."""
    session = SessionLocal()
    try:
        active_page_ids: set[str] = set()
        for database_id in notion_client.CATEGORY_DATABASES.values():
            if database_id:
                active_page_ids |= notion_client.list_active_page_ids(database_id)

        candidates = session.scalars(
            select(CalendarEvent).where(
                CalendarEvent.is_deleted.is_(False),
                CalendarEvent.notion_page_id.is_not(None),
            )
        ).all()

        deleted = [e for e in candidates if e.notion_page_id not in active_page_ids]
        for event in deleted:
            event.is_deleted = True

        session.commit()
        if deleted:
            logger.info("Synced %d deletion(s) from Notion: %s", len(deleted), [e.title for e in deleted])
        return len(deleted)
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


def sync_assignment_statuses(session) -> int:
    """Recomputes Upcoming/Due soon/Overdue for every active Assignment event on
    every run, independent of the etag-skip logic in _upsert_postgres -- an
    assignment's due-date status needs to advance purely from the passage of
    time, even when nothing about the assignment itself has changed.

    Never overwrites a status the user has manually set to "In progress" or
    "Done" in Notion -- that live state (not our local copy of it) is the
    source of truth, since the user edits Status directly in Notion."""
    now = dt.datetime.now(dt.timezone.utc)
    manual_overrides = notion_client.find_manually_set_assignment_statuses()

    assignments = session.scalars(
        select(CalendarEvent).where(
            CalendarEvent.category == "Assignments",
            CalendarEvent.is_deleted.is_(False),
        )
    ).all()

    updated = 0
    for event in assignments:
        override = manual_overrides.get(event.notion_page_id) if event.notion_page_id else None
        if override:
            if event.assignment_status != override:
                event.assignment_status = override
                updated += 1
            continue

        new_status = _compute_assignment_status(event.start_at, now)
        if new_status == event.assignment_status:
            continue

        event.assignment_status = new_status
        if event.notion_page_id:
            try:
                notion_client.update_page(event.notion_page_id, event)
            except Exception:  # noqa: BLE001 -- one bad page shouldn't sink the batch
                logger.exception("Failed to sync assignment status for %s", event.external_id)
                continue
        updated += 1

    if updated:
        session.flush()
    return updated


def sync_assignment_display(session) -> int:
    """For every active Assignment event with a Notion page: backfills the
    Haiku-compressed `short_title` the first time it's missing, and archives/
    restores the Notion page based on ASSIGNMENT_VISIBILITY_WINDOW -- keeps
    the table down to what's actually imminent instead of every deadline
    through the end of the semester. An overdue assignment's start_at - now
    is negative, so it's always inside the window (always visible)."""
    now = dt.datetime.now(dt.timezone.utc)

    assignments = session.scalars(
        select(CalendarEvent).where(
            CalendarEvent.category == "Assignments",
            CalendarEvent.is_deleted.is_(False),
            CalendarEvent.notion_page_id.is_not(None),
        )
    ).all()

    touched = 0
    for event in assignments:
        needs_short_title = event.short_title is None
        should_archive = (event.start_at - now) > ASSIGNMENT_VISIBILITY_WINDOW
        needs_visibility_change = should_archive != event.archived_in_notion

        if not needs_short_title and not needs_visibility_change:
            continue

        try:
            if needs_short_title:
                event.short_title = event_extraction.shorten_assignment_title(event.title, event.description)
                notion_client.update_page(event.notion_page_id, event)
            if needs_visibility_change:
                if should_archive:
                    notion_client.archive_page(event.notion_page_id)
                else:
                    notion_client.restore_page(event.notion_page_id)
                event.archived_in_notion = should_archive
            touched += 1
        except Exception:  # noqa: BLE001 -- one bad assignment shouldn't sink the batch, retried next sync
            logger.exception("Failed to update Notion display for assignment %s", event.external_id)

    if touched:
        session.flush()
    return touched


def run_sync() -> dict:
    session = SessionLocal()
    try:
        all_touched: list[CalendarEvent] = []
        fetched_by_source: dict[str, int] = {}
        source_errors: dict[str, str] = {}

        # Primary personal calendar and the secondary account's calendar are also
        # connected directly in the Notion Calendar app (native Google
        # Calendar sync) for real-time viewing there. They're pulled into
        # Postgres here too, purely to build a complete events dataset for
        # future calendar-optimization work -- deliberately excluded from
        # the Notion push below (all_touched), since pushing them would
        # duplicate what the native connection already shows. Marked
        # "db_only" rather than left as the upsert's default "pending", to
        # distinguish "not synced to Notion on purpose" from "waiting to
        # sync." Their Gmail mailboxes are synced separately (event
        # suggestions, digest summaries) via
        # suggestion_service.py/digest_service.py.
        try:
            personal_touched, personal_fetched = _sync_google_calendar(
                session, calendar_id="primary", source="google_calendar"
            )
            for event in personal_touched:
                event.sync_status = "db_only"
            fetched_by_source["google_calendar"] = personal_fetched
        except Exception as exc:  # noqa: BLE001
            logger.exception("Personal Google Calendar fetch failed")
            source_errors["google_calendar"] = str(exc)

        try:
            founders_touched, founders_fetched = _sync_google_calendar(
                session,
                calendar_id="primary",
                source="google_calendar_founders",
                token_path=settings.google_founders_token_path,
            )
            for event in founders_touched:
                event.sync_status = "db_only"
            fetched_by_source["google_calendar_founders"] = founders_fetched
        except Exception as exc:  # noqa: BLE001
            logger.exception("Secondary Google Calendar fetch failed")
            source_errors["google_calendar_founders"] = str(exc)

        if settings.google_work_calendar_id:
            try:
                work_touched, work_fetched = _sync_google_calendar(
                    session, calendar_id=settings.google_work_calendar_id, source="google_calendar_work"
                )
                all_touched.extend(work_touched)
                fetched_by_source["google_calendar_work"] = work_fetched
            except Exception as exc:  # noqa: BLE001
                logger.exception("Work Google Calendar fetch failed")
                source_errors["google_calendar_work"] = str(exc)

        if settings.mycourses_ical_url:
            try:
                mycourses_touched, mycourses_fetched = _sync_mycourses(session)
                all_touched.extend(mycourses_touched)
                fetched_by_source["mycourses"] = mycourses_fetched
            except Exception as exc:  # noqa: BLE001
                logger.exception("MyCourses fetch failed")
                source_errors["mycourses"] = str(exc)

        if settings.aplus_enabled:
            try:
                aplus_touched, aplus_fetched = _sync_aplus(session)
                all_touched.extend(aplus_touched)
                fetched_by_source["aplus"] = aplus_fetched
            except Exception as exc:  # noqa: BLE001
                logger.exception("A+ fetch failed")
                source_errors["aplus"] = str(exc)

        if settings.digicampus_ical_url:
            try:
                digicampus_touched, digicampus_fetched = _sync_digicampus(session)
                all_touched.extend(digicampus_touched)
                fetched_by_source["digicampus"] = digicampus_fetched
            except Exception as exc:  # noqa: BLE001
                logger.exception("DigiCampus fetch failed")
                source_errors["digicampus"] = str(exc)

        if settings.sisu_ical_url:
            try:
                sisu_touched, sisu_fetched = _sync_sisu(session)
                all_touched.extend(sisu_touched)
                fetched_by_source["sisu"] = sisu_fetched
            except Exception as exc:  # noqa: BLE001
                logger.exception("Sisu fetch failed")
                source_errors["sisu"] = str(exc)

        if settings.sisu_helsinki_ical_url:
            try:
                sisu_helsinki_touched, sisu_helsinki_fetched = _sync_sisu(
                    session, ical_url=settings.sisu_helsinki_ical_url, source="sisu_helsinki"
                )
                all_touched.extend(sisu_helsinki_touched)
                fetched_by_source["sisu_helsinki"] = sisu_helsinki_fetched
            except Exception as exc:  # noqa: BLE001
                logger.exception("Helsinki Sisu fetch failed")
                source_errors["sisu_helsinki"] = str(exc)

        if settings.exchange_calendar_ical_url:
            try:
                exchange_touched, exchange_fetched = _sync_exchange_calendar(session)
                all_touched.extend(exchange_touched)
                fetched_by_source["exchange_calendar"] = exchange_fetched
            except Exception as exc:  # noqa: BLE001
                logger.exception("Exchange calendar fetch failed")
                source_errors["exchange_calendar"] = str(exc)

        created, updated, errors = _push_to_notion(session, all_touched)

        assignment_statuses_updated = sync_assignment_statuses(session)
        assignment_display_updated = sync_assignment_display(session)

        session.commit()

        result = {
            "fetched": fetched_by_source,
            "created": created,
            "updated": updated,
            "errors": errors,
            "source_errors": source_errors,
            "assignment_statuses_updated": assignment_statuses_updated,
            "assignment_display_updated": assignment_display_updated,
        }
        logger.info("Sync complete: %s", result)
        return result
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()
