"""Gmail unread-message fetching. Uses the same OAuth credentials as Calendar
(see google_auth.py) -- gmail.readonly must be included in the granted scopes."""
import base64
import dataclasses
import datetime as dt
import logging

from googleapiclient.discovery import build

from services.google_auth import load_credentials

logger = logging.getLogger("gmail_client")


@dataclasses.dataclass
class GmailMessage:
    message_id: str
    subject: str
    sender: str
    body_text: str
    date: dt.datetime


def _header(headers: list[dict], name: str) -> str:
    for h in headers:
        if h["name"].lower() == name.lower():
            return h["value"]
    return ""


def _extract_text(payload: dict) -> str:
    if payload.get("mimeType") == "text/plain" and payload.get("body", {}).get("data"):
        return base64.urlsafe_b64decode(payload["body"]["data"]).decode("utf-8", errors="replace")

    for part in payload.get("parts", []):
        text = _extract_text(part)
        if text:
            return text

    return ""


def fetch_unread_messages(client_secret_path: str, token_path: str, max_results: int = 50) -> list[GmailMessage]:
    creds = load_credentials(client_secret_path, token_path)
    service = build("gmail", "v1", credentials=creds)

    list_response = service.users().messages().list(userId="me", q="is:unread", maxResults=max_results).execute()
    message_refs = list_response.get("messages", [])

    if list_response.get("resultSizeEstimate", 0) > max_results:
        logger.warning(
            "More unread messages exist than the %d-per-sync cap -- some will be picked up next run", max_results
        )

    messages: list[GmailMessage] = []
    for ref in message_refs:
        full = service.users().messages().get(userId="me", id=ref["id"], format="full").execute()
        headers = full["payload"].get("headers", [])
        messages.append(
            GmailMessage(
                message_id=full["id"],
                subject=_header(headers, "Subject"),
                sender=_header(headers, "From"),
                body_text=_extract_text(full["payload"]),
                date=dt.datetime.fromtimestamp(int(full["internalDate"]) / 1000, tz=dt.timezone.utc),
            )
        )

    return messages
