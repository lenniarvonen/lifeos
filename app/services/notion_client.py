"""Thin wrapper around the Notion API for the calendar-mirror database."""
import zoneinfo
from datetime import datetime

from notion_client import Client

from config import settings
from models import CalendarEvent, Course, Task, TelegramPendingReply

_client = Client(auth=settings.notion_token)

LOCAL_TZ = zoneinfo.ZoneInfo("Europe/Helsinki")

CATEGORY_DATABASES = {
    "Classes": settings.notion_classes_database_id,
    "Classes (Helsinki)": settings.notion_classes_helsinki_database_id,
    "Assignments": settings.notion_assignments_database_id,
    "Free time": settings.notion_freetime_database_id,
    "Meetings": settings.notion_meetings_database_id,
    "Work": settings.notion_work_database_id,
    "Workouts": settings.notion_workouts_database_id,
}


def _database_id_for(event: CalendarEvent) -> str:
    if event.category and CATEGORY_DATABASES.get(event.category):
        return CATEGORY_DATABASES[event.category]
    return settings.notion_database_id


def _properties_for(event: CalendarEvent) -> dict:
    props: dict = {
        "Title": {"title": [{"text": {"content": event.title}}]},
        "Start": {
            "date": {
                "start": event.start_at.isoformat(),
                "end": event.end_at.isoformat() if event.end_at else None,
            }
        },
        "Location": {"rich_text": [{"text": {"content": event.location or ""}}]},
        "Source": {"select": {"name": event.source}},
        "External ID": {"rich_text": [{"text": {"content": event.external_id}}]},
        "Calendar": {"rich_text": [{"text": {"content": event.calendar_id or ""}}]},
    }
    if event.last_synced_at:
        props["Last Synced"] = {"date": {"start": event.last_synced_at.isoformat()}}
    # Course relation only applies to matched Assignment events (see course_sync.py) --
    # the Assignments database is the only one with a "Course" relation property.
    if event.category == "Assignments" and event.course and event.course.notion_page_id:
        props["Course"] = {"relation": [{"id": event.course.notion_page_id}]}
    if event.category == "Assignments" and event.assignment_status:
        props["Status"] = {"select": {"name": event.assignment_status}}
    if event.category == "Assignments" and event.short_title:
        # "<compressed title> — <deadline>", e.g. "Assignment 1 — Sep 6", so the
        # assignments table shows the due date without the reader opening the card
        # or widening it to the Start column. short_title itself stays the pure
        # compressed form in Postgres; the date is stitched on at write time so
        # it stays current whenever the page is repushed, without short_title
        # needing to be regenerated.
        deadline = event.start_at.astimezone(LOCAL_TZ).strftime("%b %-d")
        props["Short Title"] = {
            "rich_text": [{"text": {"content": f"{event.short_title} — {deadline}"}}]
        }
    return props


def find_page_by_external_id(external_id: str, category: str | None = None) -> str | None:
    """Recovery-path lookup, only used when notion_page_id is unknown locally."""
    database_id = CATEGORY_DATABASES.get(category) or settings.notion_database_id
    result = _client.databases.query(
        database_id=database_id,
        filter={"property": "External ID", "rich_text": {"equals": external_id}},
    )
    results = result.get("results", [])
    return results[0]["id"] if results else None


def create_page(event: CalendarEvent) -> str:
    page = _client.pages.create(
        parent={"database_id": _database_id_for(event)}, properties=_properties_for(event)
    )
    return page["id"]


def update_page(page_id: str, event: CalendarEvent) -> None:
    """Notion rejects property updates on an archived page outright ("Can't
    edit block that is archived"), which a visibility-archived assignment
    (see sync_service.sync_assignment_display) would otherwise hit any time
    something else needs to push a property change to it (e.g. a status
    override, or course_sync linking a Course relation) -- temporarily
    unarchive, update, then restore the archived state so callers never need
    to know or care whether the page is currently hidden."""
    page = _client.pages.retrieve(page_id=page_id)
    was_archived = page.get("archived", False)
    if was_archived:
        _client.pages.update(page_id=page_id, archived=False)
    _client.pages.update(page_id=page_id, properties=_properties_for(event))
    if was_archived:
        _client.pages.update(page_id=page_id, archived=True)


def archive_page(page_id: str) -> None:
    page = _client.pages.retrieve(page_id=page_id)
    if page.get("archived"):
        return
    _client.pages.update(page_id=page_id, archived=True)


def restore_page(page_id: str) -> None:
    """Counterpart to archive_page -- used to bring an assignment's page back
    once its due date re-enters the visibility window (see
    sync_service.sync_assignment_display)."""
    page = _client.pages.retrieve(page_id=page_id)
    if not page.get("archived"):
        return
    _client.pages.update(page_id=page_id, archived=False)


def _course_properties_for(course: Course) -> dict:
    return {
        "Title": {"title": [{"text": {"content": f"{course.code} - {course.name} ({course.period_start.strftime('%-d.%-m.%Y')}-{course.period_end.strftime('%-d.%-m.%Y')})"}}]},
        "Code": {"rich_text": [{"text": {"content": course.code}}]},
        "Period Start": {"date": {"start": course.period_start.isoformat()}},
        "Period End": {"date": {"start": course.period_end.isoformat()}},
        "Status": {"select": {"name": course.status}},
        "University": {"select": {"name": course.university}},
    }


def create_course_page(course: Course) -> str:
    page = _client.pages.create(
        parent={"database_id": settings.notion_courses_database_id}, properties=_course_properties_for(course)
    )
    return page["id"]


def update_course_page(page_id: str, course: Course) -> None:
    _client.pages.update(page_id=page_id, properties=_course_properties_for(course))


def create_task_page(task: Task) -> str:
    page = _client.pages.create(
        parent={"database_id": settings.notion_tasks_database_id},
        properties={
            "Title": {"title": [{"text": {"content": task.title}}]},
            "Date": {"date": {"start": task.date.isoformat()}},
            "Done": {"checkbox": task.done},
            # "Tila" is a user-added Status property (Not started/Done groups) --
            # kept in lockstep with the Done checkbox so neither looks stale.
            "Tila": {"status": {"name": "Done" if task.done else "Not started"}},
        },
    )
    return page["id"]


def set_task_done(page_id: str) -> None:
    """Marks both the Done checkbox and the Tila status as Done -- used to
    reconcile a task where only one of the two was set by the user, so they
    never end up looking out of sync with each other."""
    _client.pages.update(
        page_id=page_id,
        properties={"Done": {"checkbox": True}, "Tila": {"status": {"name": "Done"}}},
    )


def _reply_properties_for(reply: TelegramPendingReply) -> dict:
    return {
        "Title": {"title": [{"text": {"content": reply.sender_name}}]},
        "Message": {"rich_text": [{"text": {"content": reply.message_text[:1900]}}]},
        "Date": {"date": {"start": reply.message_date.isoformat()}},
    }


def create_reply_page(reply: TelegramPendingReply) -> str:
    page = _client.pages.create(
        parent={"database_id": settings.notion_replies_database_id},
        properties={**_reply_properties_for(reply), "Read": {"checkbox": False}},
    )
    return page["id"]


def update_reply_page(page_id: str, reply: TelegramPendingReply) -> None:
    # Never writes "Read" -- that's user-controlled in Notion; overwriting it here
    # would fight with the checkbox they just ticked.
    _client.pages.update(page_id=page_id, properties=_reply_properties_for(reply))


def find_manually_set_assignment_statuses() -> dict[str, str]:
    """{page_id: status} for assignments currently set to "In progress" or "Done"
    in Notion -- the live source of truth for manual overrides, since the user
    edits Status directly in Notion, not through anything we control."""
    statuses: dict[str, str] = {}
    cursor: str | None = None
    while True:
        kwargs = {
            "database_id": settings.notion_assignments_database_id,
            "filter": {
                "or": [
                    {"property": "Status", "select": {"equals": "In progress"}},
                    {"property": "Status", "select": {"equals": "Done"}},
                ]
            },
        }
        if cursor:
            kwargs["start_cursor"] = cursor
        response = _client.databases.query(**kwargs)
        for page in response.get("results", []):
            status = page["properties"].get("Status", {}).get("select")
            if status:
                statuses[page["id"]] = status["name"]
        if not response.get("has_more"):
            break
        cursor = response.get("next_cursor")
    return statuses


def find_pages_with_checkbox(database_id: str, property_name: str) -> set[str]:
    """Page IDs where the given checkbox property is currently checked -- the
    live source of truth for a user-controlled checkbox, since it's edited
    directly in Notion, not through anything we control."""
    page_ids: set[str] = set()
    cursor: str | None = None
    while True:
        kwargs = {"database_id": database_id, "filter": {"property": property_name, "checkbox": {"equals": True}}}
        if cursor:
            kwargs["start_cursor"] = cursor
        response = _client.databases.query(**kwargs)
        page_ids.update(page["id"] for page in response.get("results", []))
        if not response.get("has_more"):
            break
        cursor = response.get("next_cursor")
    return page_ids


def find_pages_with_status(database_id: str, property_name: str, status_name: str) -> set[str]:
    """Page IDs where the given Notion status-type property currently equals
    status_name -- same purpose as find_pages_with_checkbox, for a "Status"
    property (to_do/in_progress/complete groups) instead of a plain checkbox."""
    page_ids: set[str] = set()
    cursor: str | None = None
    while True:
        kwargs = {"database_id": database_id, "filter": {"property": property_name, "status": {"equals": status_name}}}
        if cursor:
            kwargs["start_cursor"] = cursor
        response = _client.databases.query(**kwargs)
        page_ids.update(page["id"] for page in response.get("results", []))
        if not response.get("has_more"):
            break
        cursor = response.get("next_cursor")
    return page_ids


def list_active_page_ids(database_id: str) -> set[str]:
    """All non-archived page IDs in a database -- databases.query excludes
    archived/trashed pages by default, which is exactly the "still active" set
    needed to detect pages the user deleted directly in Notion."""
    page_ids: set[str] = set()
    cursor: str | None = None
    while True:
        kwargs = {"database_id": database_id}
        if cursor:
            kwargs["start_cursor"] = cursor
        response = _client.databases.query(**kwargs)
        page_ids.update(page["id"] for page in response.get("results", []))
        if not response.get("has_more"):
            break
        cursor = response.get("next_cursor")
    return page_ids
