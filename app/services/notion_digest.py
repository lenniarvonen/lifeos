"""Thin wrapper around the Notion API for the daily-digest dashboard page and
the embedded Personal Emails gallery database."""
import logging

from notion_client import Client

from config import settings

logger = logging.getLogger("notion_digest")

_client = Client(auth=settings.notion_token)

# Matched as a case-insensitive substring against heading text, not an exact
# match -- the dashboard's headings get hand-restyled (emoji added/removed,
# etc.) often enough that chasing exact text on every edit isn't worth it.
NEWS_HEADING = "top news"

_CONTAINER_TYPES = ("column_list", "column", "callout")


def _personal_emails_properties(sender: str, summary: str) -> dict:
    return {
        "Sender": {"title": [{"text": {"content": sender[:200]}}]},
        "Summary": {"rich_text": [{"text": {"content": summary}}]},
    }


def refresh_personal_emails(entries: list[tuple[str, str]]) -> None:
    """Archive yesterday's rows, create fresh rows for today's personal-email summaries."""
    database_id = settings.notion_personal_emails_database_id

    existing = _client.databases.query(database_id=database_id)
    for page in existing.get("results", []):
        _client.pages.update(page_id=page["id"], archived=True)

    for sender, summary in entries:
        _client.pages.create(
            parent={"database_id": database_id},
            properties=_personal_emails_properties(sender, summary),
        )


def _append_chunked(container_id: str, blocks: list[dict], after: str | None = None) -> None:
    # The children.append endpoint accepts at most 100 blocks per call.
    for i in range(0, len(blocks), 100):
        chunk = blocks[i : i + 100]
        if after:
            result = _client.blocks.children.append(block_id=container_id, children=chunk, after=after)
            after = result["results"][-1]["id"]
        else:
            _client.blocks.children.append(block_id=container_id, children=chunk)


def _find_heading(root_id: str, heading_type: str, text: str | None = None) -> dict | None:
    """Recursively search the block tree under `root_id` (diving into column_list/
    column/callout containers) for the first heading of `heading_type` whose text
    contains `text` case-insensitively (or the first one of that type if `text` is
    None). The dashboard page's layout is hand-edited in the Notion app (columns,
    callouts, emoji tweaks), so content is anchored to headings by a loose text
    match rather than a fixed block position or exact string."""
    children = _client.blocks.children.list(block_id=root_id).get("results", [])
    for block in children:
        if block["type"] == heading_type:
            block_text = "".join(t.get("plain_text", "") for t in block[heading_type]["rich_text"])
            if text is None or text.lower() in block_text.lower():
                return block
        if block["type"] in _CONTAINER_TYPES:
            found = _find_heading(block["id"], heading_type, text)
            if found:
                return found
    return None


def _replace_after(heading_block: dict, blocks: list[dict]) -> None:
    """Delete every sibling block after `heading_block` within its parent container,
    then append `blocks` right after it -- updates a section's content in place
    without disturbing whatever layout (columns, etc.) contains it."""
    parent = heading_block["parent"]
    container_id = parent.get("block_id") or parent.get("page_id")

    siblings = _client.blocks.children.list(block_id=container_id).get("results", [])
    index = next(i for i, b in enumerate(siblings) if b["id"] == heading_block["id"])
    for stale in siblings[index + 1 :]:
        _client.blocks.delete(block_id=stale["id"])

    _append_chunked(container_id, blocks, after=heading_block["id"])


def sync_dashboard(date_text: str, news_blocks: list[dict]) -> None:
    """Update the dashboard's date heading and the content under its "Top News"
    heading in place, wherever it's been moved to in the page's (possibly
    hand-edited, column-based) layout. Falls back to appending at the page root
    if the section can't be found, so the digest never silently fails to publish
    even if the page was reset to blank."""
    page_id = settings.notion_digest_page_id

    date_heading = _find_heading(page_id, "heading_1")
    if date_heading:
        _client.blocks.update(block_id=date_heading["id"], heading_1={"rich_text": [{"text": {"content": date_text}}]})
    else:
        _append_chunked(page_id, [{"object": "block", "type": "heading_1", "heading_1": {"rich_text": [{"text": {"content": date_text}}]}}])

    news_heading = _find_heading(page_id, "heading_2", NEWS_HEADING)
    if news_heading:
        _replace_after(news_heading, news_blocks)
    else:
        logger.warning("Dashboard heading %r not found -- appending at page root instead", NEWS_HEADING)
        fresh_heading = {
            "object": "block",
            "type": "heading_2",
            "heading_2": {"rich_text": [{"text": {"content": NEWS_HEADING}}]},
        }
        _append_chunked(page_id, [fresh_heading, *news_blocks])
