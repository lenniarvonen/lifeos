"""Google Calendar event fetching. See google_auth.py for OAuth setup."""
import datetime as dt

from googleapiclient.discovery import build

from services.google_auth import load_credentials
from services.types import NormalizedEvent


def list_events(client_secret_path: str, token_path: str, calendar_id: str = "primary") -> list[dict]:
    """Fetch upcoming events (past 7 days through 180 days ahead) from a calendar."""
    creds = load_credentials(client_secret_path, token_path)
    service = build("calendar", "v3", credentials=creds)

    now = dt.datetime.now(dt.timezone.utc)
    time_min = (now - dt.timedelta(days=7)).isoformat()
    time_max = (now + dt.timedelta(days=180)).isoformat()

    events: list[dict] = []
    page_token = None
    while True:
        response = (
            service.events()
            .list(
                calendarId=calendar_id,
                timeMin=time_min,
                timeMax=time_max,
                singleEvents=True,
                orderBy="startTime",
                showDeleted=True,
                pageToken=page_token,
            )
            .execute()
        )
        events.extend(response.get("items", []))
        page_token = response.get("nextPageToken")
        if not page_token:
            break

    return events


def _parse_event_time(time_dict: dict) -> tuple[dt.datetime, bool]:
    """Google returns either {'date': 'YYYY-MM-DD'} (all-day) or {'dateTime': ..., 'timeZone': ...}."""
    if "date" in time_dict:
        d = dt.date.fromisoformat(time_dict["date"])
        return dt.datetime(d.year, d.month, d.day, tzinfo=dt.timezone.utc), True
    return dt.datetime.fromisoformat(time_dict["dateTime"]), False


def normalize(raw: dict) -> NormalizedEvent:
    is_cancelled = raw.get("status") == "cancelled"

    start_at, is_all_day = (
        _parse_event_time(raw["start"]) if not is_cancelled and "start" in raw else (None, False)
    )
    end_at, _ = _parse_event_time(raw["end"]) if not is_cancelled and "end" in raw else (None, False)

    return NormalizedEvent(
        external_id=raw["id"],
        title=raw.get("summary", "(no title)"),
        description=raw.get("description"),
        location=raw.get("location"),
        start_at=start_at,
        end_at=end_at,
        is_all_day=is_all_day,
        timezone=raw.get("start", {}).get("timeZone"),
        etag=raw.get("etag"),
        source_updated_at=dt.datetime.fromisoformat(raw["updated"]) if raw.get("updated") else None,
        is_deleted=is_cancelled,
    )
