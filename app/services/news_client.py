"""RSS feed fetching for the daily briefing's news section."""
import datetime as dt
from dataclasses import dataclass
from email.utils import parsedate_to_datetime
from xml.etree import ElementTree

import httpx


@dataclass
class NewsItem:
    source: str
    title: str
    description: str
    link: str
    published_at: dt.datetime | None


def _parse_pubdate(value: str | None) -> dt.datetime | None:
    if not value:
        return None
    try:
        return parsedate_to_datetime(value)
    except (TypeError, ValueError):
        return None


def fetch_feed(url: str, max_items: int) -> list[NewsItem]:
    response = httpx.get(url, headers={"User-Agent": "Mozilla/5.0"}, timeout=15, follow_redirects=True)
    response.raise_for_status()
    channel = ElementTree.fromstring(response.content).find("channel")

    source = channel.findtext("title") or url

    items = []
    for item in channel.findall("item")[:max_items]:
        items.append(
            NewsItem(
                source=source,
                title=(item.findtext("title") or "").strip(),
                description=(item.findtext("description") or "").strip(),
                link=item.findtext("link") or "",
                published_at=_parse_pubdate(item.findtext("pubDate")),
            )
        )
    return items
