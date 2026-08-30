"""Continuously-refreshed dashboard: personal email summaries and top news ->
Notion dashboard page. Runs on the same interval as
the other syncs (see scheduler.py) rather than once a day -- a missed run
(e.g. laptop asleep) is simply picked up on the next interval fire, same as
every other sync job, so no separate catch-up/state tracking is needed."""
import datetime as dt
import logging
import zoneinfo

from config import settings
from db import SessionLocal
from models import GmailProcessedMessage, NewsSummaryCache
from services import event_extraction, gmail_client, news_client, notion_digest, suggestion_service

logger = logging.getLogger("digest_service")

LOCAL_TZ = zoneinfo.ZoneInfo("Europe/Helsinki")
GMAIL_MAX_UNREAD_PER_DIGEST = 50
GMAIL_DIGEST_PURPOSE = "digest"
NEWS_ITEMS_PER_FEED = 2
NEWS_CACHE_RETENTION_DAYS = 7


def _clean_sender(sender: str) -> str:
    """"Jane Doe <jane@example.com>" -> "Jane Doe"; falls back to the raw value."""
    if "<" in sender:
        return sender.split("<")[0].strip().strip('"') or sender
    return sender


def _personal_email_summaries(session) -> list[tuple[str, str]]:
    accounts = suggestion_service._gmail_accounts()
    if not accounts:
        return []

    summaries: list[tuple[str, str]] = []
    for channel, token_path in accounts:
        try:
            messages = gmail_client.fetch_unread_messages(
                settings.google_client_secret_path, token_path, max_results=GMAIL_MAX_UNREAD_PER_DIGEST
            )
        except Exception:  # noqa: BLE001 -- a Gmail fetch hiccup shouldn't sink the whole digest
            logger.exception("Failed to fetch unread Gmail messages for digest (account %s)", channel)
            continue

        for msg in messages:
            already_processed = session.get(GmailProcessedMessage, (channel, msg.message_id, GMAIL_DIGEST_PURPOSE))
            if already_processed is not None:
                continue

            try:
                result = event_extraction.summarize_personal_email(msg.body_text, msg.sender, msg.subject)
            except Exception:  # noqa: BLE001 -- one bad email shouldn't sink the digest, and don't mark it
                logger.exception("Failed to classify email %s for digest (account %s)", msg.message_id, channel)
                continue

            session.add(GmailProcessedMessage(account=channel, message_id=msg.message_id, purpose=GMAIL_DIGEST_PURPOSE))
            session.flush()

            if result is not None:
                summaries.append((_clean_sender(msg.sender), result.summary))

    return summaries


def _prune_news_cache(session) -> None:
    cutoff = dt.datetime.now(dt.timezone.utc) - dt.timedelta(days=NEWS_CACHE_RETENTION_DAYS)
    session.query(NewsSummaryCache).filter(NewsSummaryCache.cached_at < cutoff).delete()


def _top_news(session) -> list[tuple[str, str, str, str]]:
    """Returns (source, title, summary, link) tuples. Refetched every run (the
    dashboard now refreshes every sync_interval_minutes, not once a day), but
    summaries are cached by article link so re-billing Haiku only happens for
    genuinely new headlines -- most runs will just re-fetch the same top items."""
    if not settings.news_feed_urls:
        return []

    _prune_news_cache(session)

    stories: list[tuple[str, str, str, str]] = []
    for url in settings.news_feed_urls.split(","):
        url = url.strip()
        if not url:
            continue
        try:
            items = news_client.fetch_feed(url, NEWS_ITEMS_PER_FEED)
        except Exception:  # noqa: BLE001 -- one dead feed shouldn't sink the digest
            logger.exception("Failed to fetch news feed %s", url)
            continue

        for item in items:
            cached = session.get(NewsSummaryCache, item.link) if item.link else None
            if cached is not None:
                summary = cached.summary
            else:
                try:
                    summary = event_extraction.summarize_news_story(item.title, item.description)
                except Exception:  # noqa: BLE001 -- fall back to the feed's own snippet
                    logger.exception("Failed to summarize news item %r", item.title)
                    summary = item.description
                if item.link:
                    session.add(NewsSummaryCache(link=item.link, source=item.source, title=item.title, summary=summary))
                    session.flush()
            stories.append((item.source, item.title, summary, item.link))

    return stories


def _paragraph(text: str) -> dict:
    return {"paragraph": {"rich_text": [{"text": {"content": text}}]}, "object": "block", "type": "paragraph"}


def _news_bullet(source: str, title: str, summary: str, link: str) -> dict:
    rich_text = [{"text": {"content": title, "link": {"url": link} if link else None}, "annotations": {"bold": True}}]
    rich_text.append({"text": {"content": f" ({source}) — {summary}"}})
    return {"bulleted_list_item": {"rich_text": rich_text}, "object": "block", "type": "bulleted_list_item"}


def _news_blocks(news: list[tuple[str, str, str, str]]) -> list[dict]:
    if not news:
        return [_paragraph("No news available.")]
    return [_news_bullet(source, title, summary, link) for source, title, summary, link in news]


def refresh_dashboard() -> dict:
    session = SessionLocal()
    try:
        news = _top_news(session)
        # Committed before the Notion calls below: the classification dedup marks
        # should stick even if the Notion write fails afterwards, so a transient
        # Notion error doesn't cause the same emails to be re-billed next cycle.
        email_summaries = _personal_email_summaries(session)
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()

    date_text = dt.datetime.now(LOCAL_TZ).strftime("%A, %B %-d, %Y")
    notion_digest.refresh_personal_emails(email_summaries)
    notion_digest.sync_dashboard(date_text, _news_blocks(news))

    result = {"personal_emails": len(email_summaries), "news": len(news)}
    logger.info("Dashboard refreshed: %s", result)
    return result
