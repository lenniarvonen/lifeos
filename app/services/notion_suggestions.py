"""Thin wrapper around the Notion API for the Event Suggestions database."""
from notion_client import Client

from config import settings
from models import EventSuggestion

_client = Client(auth=settings.notion_token)

# Telegram channel identifier (username or numeric ID, whichever TELEGRAM_CHANNELS
# uses -- kept as the stable internal key for matching/dedup) -> human-readable
# display name shown in Notion. Purely cosmetic; the internal `channel` field on
# EventSuggestion/TelegramSyncState is unaffected.
CHANNEL_DISPLAY_NAMES = {
    "aaltorunningclub": "Aalto Running Club",
    "2471509288": "Prodekon tapahtumat",
    "1240815471": "AaltoDJ",
    "3528274345": "Prodekoaktiivit '26",
    "1153054935": "Aaltoes",
    "1547083319": "Aalto Investment Club",
    "prodekotiedotus": "Prodekon tiedotus",
    "hackjunction": "Junction info channel",
    "sikajuhlat": "Sikajuhlat",
    "gmail": "Gmail",
    "gmail_founders": "Founders House Gmail",
}

# Same keys as CHANNEL_DISPLAY_NAMES -- used as the page icon so suggestions are
# visually distinguishable by source channel at a glance in the gallery view.
CHANNEL_ICONS = {
    "aaltorunningclub": "🏃",
    "2471509288": "🎉",
    "1240815471": "🎧",
    "3528274345": "🧑‍💼",
    "1153054935": "🚀",
    "1547083319": "📈",
    "prodekotiedotus": "📢",
    "hackjunction": "💻",
    "sikajuhlat": "🐷",
    "gmail": "📧",
    "gmail_founders": "🏢",
}
DEFAULT_CHANNEL_ICON = "💬"


def _display_name(channel: str) -> str:
    return CHANNEL_DISPLAY_NAMES.get(channel, channel)


def _channel_icon(channel: str) -> str:
    return CHANNEL_ICONS.get(channel, DEFAULT_CHANNEL_ICON)


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
    page = _client.pages.create(
        parent={"database_id": settings.notion_suggestions_database_id},
        icon={"type": "emoji", "emoji": _channel_icon(suggestion.channel)},
        properties={**_properties_for(suggestion), "Status": {"select": {"name": "Pending"}}},
    )
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
