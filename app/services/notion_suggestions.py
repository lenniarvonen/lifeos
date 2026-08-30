"""Thin wrapper around the Notion API for the Event Suggestions database."""
from notion_client import Client

from config import settings
from models import EventSuggestion

_client = Client(auth=settings.notion_token)


def _display_name(channel: str) -> str:
    """Telegram channel identifier (username or numeric ID, whichever
    TELEGRAM_CHANNELS uses -- kept as the stable internal key for matching/
    dedup) or a Gmail account key ("gmail"/"gmail_founders") -> human-readable
    display name shown in Notion, from settings.channel_display_names (a
    per-deployment env setting -- see config.py). Purely cosmetic; the
    internal `channel` field on EventSuggestion/TelegramSyncState is
    unaffected. Falls back to the raw channel id if unconfigured."""
    return settings.channel_display_names.get(channel, channel)


def _channel_icon(channel: str) -> str | None:
    """Same keys as _display_name, from settings.channel_icons -- used as the
    page icon so suggestions are visually distinguishable by source channel at
    a glance in the gallery view. None (no icon set) if unconfigured."""
    return settings.channel_icons.get(channel)


def _properties_for(suggestion: EventSuggestion) -> dict:
    props: dict = {
        "Title": {"title": [{"text": {"content": suggestion.title or "(no title)"}}]},
        # Notion's 2000-char limit is counted in UTF-16 units, not Python code points --
        # a surrogate-pair character (e.g. certain emoji) counts as 2, so slicing to
        # exactly 2000 code points can still exceed it. Leave headroom instead.
        "Message": {"rich_text": [{"text": {"content": suggestion.message_text[:1900]}}]},
        "Channel": {"select": {"name": _display_name(suggestion.channel)}},
        "Location": {"rich_text": [{"text": {"content": suggestion.location or ""}}]},
        "Message Date": {"date": {"start": suggestion.message_date.isoformat()}},
    }
    if suggestion.start_at:
        props["Suggested Start"] = {
            "date": {
                "start": suggestion.start_at.isoformat(),
                "end": suggestion.end_at.isoformat() if suggestion.end_at else None,
            }
        }
    return props


def create_page(suggestion: EventSuggestion) -> str:
    kwargs = {
        "parent": {"database_id": settings.notion_suggestions_database_id},
        "properties": {**_properties_for(suggestion), "Status": {"select": {"name": "Pending"}}},
    }
    icon = _channel_icon(suggestion.channel)
    if icon:
        kwargs["icon"] = {"type": "emoji", "emoji": icon}
    page = _client.pages.create(**kwargs)
    return page["id"]


def archive_page(page_id: str) -> None:
    page = _client.pages.retrieve(page_id=page_id)
    if page.get("archived"):
        return
    _client.pages.update(page_id=page_id, archived=True)


def find_reviewed_pages() -> list[dict]:
    """Pages where Status has been changed away from 'Pending' by the user."""
    result = _client.databases.query(
        database_id=settings.notion_suggestions_database_id,
        filter={"property": "Status", "select": {"does_not_equal": "Pending"}},
    )
    return result.get("results", [])
