"""Thin wrapper around the Telegram Bot API (raw HTTP, long-polling -- no
webhook/public URL available in local dev). Distinct from telegram_client.py,
which uses a Telethon *user* session to passively read channels; this is a
proper bot account the user commands directly."""
import httpx

API_BASE = "https://api.telegram.org/bot{token}"


def _url(token: str, method: str) -> str:
    return f"{API_BASE.format(token=token)}/{method}"


def get_updates(token: str, offset: int | None, timeout: int = 25) -> list[dict]:
    params = {"timeout": timeout}
    if offset is not None:
        params["offset"] = offset
    response = httpx.get(_url(token, "getUpdates"), params=params, timeout=timeout + 10)
    response.raise_for_status()
    return response.json()["result"]


def send_message(token: str, chat_id: int, text: str, reply_markup: dict | None = None) -> dict:
    payload = {"chat_id": chat_id, "text": text, "parse_mode": "Markdown"}
    if reply_markup:
        payload["reply_markup"] = reply_markup
    response = httpx.post(_url(token, "sendMessage"), json=payload, timeout=15)
    response.raise_for_status()
    return response.json()["result"]


def edit_message_text(token: str, chat_id: int, message_id: int, text: str) -> None:
    payload = {"chat_id": chat_id, "message_id": message_id, "text": text, "parse_mode": "Markdown"}
    response = httpx.post(_url(token, "editMessageText"), json=payload, timeout=15)
    response.raise_for_status()


def answer_callback_query(token: str, callback_query_id: str, text: str | None = None) -> None:
    payload = {"callback_query_id": callback_query_id}
    if text:
        payload["text"] = text
    response = httpx.post(_url(token, "answerCallbackQuery"), json=payload, timeout=15)
    response.raise_for_status()
