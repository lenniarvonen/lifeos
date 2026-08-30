"""LLM-based event extraction from free-text Telegram messages."""
import datetime as dt
import zoneinfo
from dataclasses import dataclass

from anthropic import Anthropic

from config import settings

MODEL = "claude-haiku-4-5-20251001"


@dataclass
class ExtractedEvent:
    title: str
    location: str | None
    start_at: dt.datetime | None
    end_at: dt.datetime | None


@dataclass
class PersonalEmailSummary:
    summary: str


LOCAL_TZ = zoneinfo.ZoneInfo("Europe/Helsinki")

_client = Anthropic(api_key=settings.anthropic_api_key) if settings.anthropic_api_key else None

_SYSTEM_PROMPT = """You classify Telegram messages for a personal calendar-suggestion pipeline.

Set is_event=true only for original announcements of a specific event with an identifiable date/time \
(e.g. a club posting about a training session, party, talk, or meetup it is organizing).

Set is_event=false for:
- Individual ticket resale/swap/buy posts (e.g. "selling my ticket to X", "anyone have a spare ticket to Y", \
"WTS/WTB ticket for Z") -- these are about a single person's ticket, not the event itself, even though they \
mention a real event name and date. The organizer's own announcement of that same event (if it appears as a \
separate message) should still be is_event=true.
- Recaps, reactions, or discussion about an event that already happened.
- General chat, announcements with no concrete date/time, or promotional messages with no specific occurrence \
(e.g. "check out our website", recurring generic reminders with no date).

When in doubt about whether a message is an original announcement vs. a resale post, look for phrasing that \
centers on an individual's ticket/spot ("my ticket", "I have a spare", "selling", "buying") as the signal for \
is_event=false."""

_TOOL = {
    "name": "extract_event",
    "description": "Extract event details from a message, if it describes a concrete event with a date/time.",
    "input_schema": {
        "type": "object",
        "properties": {
            "is_event": {
                "type": "boolean",
                "description": "True only if the message is an original event announcement with an identifiable date/time -- false for ticket resale/swap posts, recaps, or vague chat.",
            },
            "title": {"type": "string", "description": "Short event title."},
            "start_at": {
                "type": "string",
                "description": "ISO 8601 datetime with UTC offset, e.g. 2026-08-01T18:00:00+03:00. Resolve relative/natural dates against the message date given.",
            },
            "end_at": {
                "type": ["string", "null"],
                "description": "ISO 8601 datetime with UTC offset, or null if unknown/not mentioned.",
            },
            "location": {"type": ["string", "null"], "description": "Venue/location, or null if not mentioned."},
        },
        "required": ["is_event"],
    },
}


def extract_event(message_text: str, message_date: dt.datetime) -> ExtractedEvent | None:
    if _client is None:
        raise RuntimeError("ANTHROPIC_API_KEY is not configured")

    local_message_date = message_date.astimezone(LOCAL_TZ).isoformat()

    response = _client.messages.create(
        model=MODEL,
        max_tokens=512,
        system=_SYSTEM_PROMPT,
        tools=[_TOOL],
        tool_choice={"type": "tool", "name": "extract_event"},
        messages=[
            {
                "role": "user",
                "content": (
                    f"Message sent at {local_message_date} (Europe/Helsinki):\n\n{message_text}\n\n"
                    "Determine whether this describes a concrete event with a date/time, and extract its details."
                ),
            }
        ],
    )

    tool_use = next(block for block in response.content if block.type == "tool_use")
    result = tool_use.input

    if not result.get("is_event"):
        return None

    start_at = dt.datetime.fromisoformat(result["start_at"]) if result.get("start_at") else None
    end_at = dt.datetime.fromisoformat(result["end_at"]) if result.get("end_at") else None

    return ExtractedEvent(
        title=result.get("title") or "(no title)",
        location=result.get("location"),
        start_at=start_at,
        end_at=end_at,
    )


_COMMAND_SYSTEM_PROMPT = """You extract event details from a direct instruction to add an event to a \
personal calendar (e.g. a Telegram bot command like "dinner with Alex Friday 7pm at Konstan Moljä"). \
Unlike screening a channel for announcements, the sender is always explicitly asking to add this \
specific event -- extract it. Only set is_valid_event=false if the message doesn't describe anything \
resembling an event with an identifiable date/time (e.g. "hello", "what's up").

Special notes for classification:
- Lunches are meetings

Interpreting times:
- A bare numeric time (e.g. "18:00", "at 7", "at 14") is already in 24-hour format -- use it as-is, \
never assume am/pm for it.
- If the message explicitly says am/pm (e.g. "7pm", "7:30am"), convert that to the corresponding \
24-hour time.

Format the title:
- Capitalize the first letter of every person's name (e.g. "lunch with alex" -> names become "Alex").
- Replace every occurrence of the word "with" in the title with "w/" (e.g. "Dinner with Alex" -> \
"Dinner w/ Alex").
- Replace every occurrence of the word "and" in the title with "&" (e.g. "Alex and Maria" -> \
"Alex & Maria").

Estimating duration (end_at), when the message doesn't state one explicitly:
If given a list of the user's actual durations for past similar events, use it to calibrate your \
estimate -- e.g. if past Friday lunches ran ~1.5h and past Monday lunches ran ~45min, prefer those \
patterns over a flat default for a new similarly-described event on a matching day. Only let a past \
event influence the estimate if its title is genuinely similar in kind (same activity/person), not \
just because it shares a day of week. Fall back to one hour after start_at if nothing relevant applies."""

_COMMAND_TOOL = {
    "name": "parse_event_command",
    "description": "Extract event details from a direct 'add this event' instruction.",
    "input_schema": {
        "type": "object",
        "properties": {
            "is_valid_event": {
                "type": "boolean",
                "description": "False only if the message has no identifiable event/date-time at all.",
            },
            "title": {"type": "string", "description": "Short event title."},
            "start_at": {
                "type": "string",
                "description": "ISO 8601 datetime with UTC offset, e.g. 2026-08-01T18:00:00+03:00. Resolve relative/natural dates against the message time given.",
            },
            "end_at": {
                "type": ["string", "null"],
                "description": "ISO 8601 datetime with UTC offset. If no duration/end is mentioned, default to one hour after start_at.",
            },
            "location": {"type": ["string", "null"], "description": "Venue/location, or null if not mentioned."},
        },
        "required": ["is_valid_event"],
    },
}


def parse_event_command(
    message_text: str, message_date: dt.datetime, calibration_context: str | None = None
) -> ExtractedEvent | None:
    if _client is None:
        raise RuntimeError("ANTHROPIC_API_KEY is not configured")

    local_message_date = message_date.astimezone(LOCAL_TZ).isoformat()

    content = f"Message sent at {local_message_date} (Europe/Helsinki):\n\n{message_text}"
    if calibration_context:
        content += f"\n\nPast actual durations for similar bot-added events:\n{calibration_context}"

    response = _client.messages.create(
        model=MODEL,
        max_tokens=512,
        system=_COMMAND_SYSTEM_PROMPT,
        tools=[_COMMAND_TOOL],
        tool_choice={"type": "tool", "name": "parse_event_command"},
        messages=[{"role": "user", "content": content}],
    )

    tool_use = next(block for block in response.content if block.type == "tool_use")
    result = tool_use.input

    # is_valid_event is an escape hatch for genuinely event-less messages ("hello") --
    # Haiku frequently omits it from the tool call even when it *did* extract a real
    # title/start_at, so only an explicit False should count as a decline. Requiring
    # start_at is what actually matters: no start_at means nothing usable was extracted.
    if result.get("is_valid_event") is False or not result.get("start_at"):
        return None

    return ExtractedEvent(
        title=result.get("title") or "(no title)",
        location=result.get("location"),
        start_at=dt.datetime.fromisoformat(result["start_at"]),
        end_at=dt.datetime.fromisoformat(result["end_at"]) if result.get("end_at") else None,
    )


_DIGEST_SYSTEM_PROMPT = """You screen unread emails for a personal morning-briefing dashboard.

Set is_personal_and_interesting=true only for messages worth a human glancing at over morning coffee: \
personal correspondence from a real person (friend, family, colleague), or a genuinely interesting update \
from someone/something the user actually cares about.

Set is_personal_and_interesting=false for:
- Marketing, promotions, newsletters, and mailing-list digests.
- Automated notifications (receipts, invoices, shipping updates, "your statement is ready", security alerts, \
password resets, calendar reminders, app/service notifications).
- Anything that describes a concrete event with a date/time (a separate pipeline already handles those).
- Generic spam or bulk-sent content with no personal relevance.

When true, write a single short sentence (not a fragment, not a headline) summarizing what the email is about, \
suitable for a one-line dashboard entry."""

_DIGEST_TOOL = {
    "name": "summarize_personal_email",
    "description": "Classify whether an email is personal/interesting enough for a morning briefing, and summarize it if so.",
    "input_schema": {
        "type": "object",
        "properties": {
            "is_personal_and_interesting": {
                "type": "boolean",
                "description": "True only for personal correspondence or a genuinely interesting update -- false for marketing, automated notifications, or event-shaped emails.",
            },
            "summary": {
                "type": "string",
                "description": "One short sentence summarizing the email, for a dashboard entry. Omit or leave empty if not personal/interesting.",
            },
        },
        "required": ["is_personal_and_interesting"],
    },
}


def summarize_personal_email(message_text: str, sender: str, subject: str) -> PersonalEmailSummary | None:
    if _client is None:
        raise RuntimeError("ANTHROPIC_API_KEY is not configured")

    response = _client.messages.create(
        model=MODEL,
        max_tokens=256,
        system=_DIGEST_SYSTEM_PROMPT,
        tools=[_DIGEST_TOOL],
        tool_choice={"type": "tool", "name": "summarize_personal_email"},
        messages=[
            {
                "role": "user",
                "content": f"From: {sender}\nSubject: {subject}\n\n{message_text}",
            }
        ],
    )

    tool_use = next(block for block in response.content if block.type == "tool_use")
    result = tool_use.input

    if not result.get("is_personal_and_interesting") or not result.get("summary"):
        return None

    return PersonalEmailSummary(summary=result["summary"])


_CATEGORY_SYSTEM_PROMPT = """You classify a calendar event into exactly one of four subcalendars: \
"Free time", "Meetings", "Work", or "Workouts".

"Meetings" = scheduled, structured meetings/calls/appointments with other people: 1:1s, calls, \
interviews, official appointments -- work-related or not.

"Work" = dedicated work-time blocks that are not a specific meeting with others: work shifts, \
office hours, "working on X", focus/deep-work blocks -- time spent working rather than meeting.

"Workouts" = physical training/exercise: gym sessions, runs, sports practice, classes (yoga, \
pilates, etc.), matches, or any other fitness activity.

"Free time" = everything else: social hangouts, casual plans, personal activities, hobbies, \
errands, parties, informal get-togethers.

Every event must get exactly one label -- pick whichever is the closer fit."""

_CATEGORY_TOOL = {
    "name": "classify_calendar_category",
    "description": "Classify a calendar event as informal free time, a structured meeting, dedicated work time, or a workout.",
    "input_schema": {
        "type": "object",
        "properties": {
            "category": {
                "type": "string",
                "enum": ["Free time", "Meetings", "Work", "Workouts"],
                "description": "Which of the four subcalendars this event belongs to.",
            },
        },
        "required": ["category"],
    },
}


_DURATION_SYSTEM_PROMPT = """You parse a reply to "how long did this event actually take?" into a \
number of minutes. Handle phrasings like "1.5 hours", "45 min", "about 2h", "an hour and a half", \
"just under an hour". If the reply doesn't contain a usable duration, set minutes to null."""

_DURATION_TOOL = {
    "name": "parse_duration_reply",
    "description": "Parse a free-text duration reply into a number of minutes.",
    "input_schema": {
        "type": "object",
        "properties": {
            "minutes": {"type": ["integer", "null"], "description": "The duration in minutes, or null if unparseable."},
        },
        "required": ["minutes"],
    },
}


def parse_duration_reply(text: str) -> int | None:
    if _client is None:
        raise RuntimeError("ANTHROPIC_API_KEY is not configured")

    response = _client.messages.create(
        model=MODEL,
        max_tokens=128,
        system=_DURATION_SYSTEM_PROMPT,
        tools=[_DURATION_TOOL],
        tool_choice={"type": "tool", "name": "parse_duration_reply"},
        messages=[{"role": "user", "content": text}],
    )

    tool_use = next(block for block in response.content if block.type == "tool_use")
    return tool_use.input.get("minutes")


_NEWS_SYSTEM_PROMPT = """You write single-sentence summaries of news headlines for a personal morning \
briefing. Given a headline and its snippet, write one short, plain-language sentence capturing the key \
fact -- no fluff, no restating the headline verbatim."""

_NEWS_TOOL = {
    "name": "summarize_news_story",
    "description": "Summarize a news headline/snippet into one short sentence for a dashboard entry.",
    "input_schema": {
        "type": "object",
        "properties": {
            "summary": {"type": "string", "description": "One short sentence summarizing the story."},
        },
        "required": ["summary"],
    },
}


def summarize_news_story(title: str, description: str) -> str:
    if _client is None:
        raise RuntimeError("ANTHROPIC_API_KEY is not configured")

    response = _client.messages.create(
        model=MODEL,
        max_tokens=128,
        system=_NEWS_SYSTEM_PROMPT,
        tools=[_NEWS_TOOL],
        tool_choice={"type": "tool", "name": "summarize_news_story"},
        messages=[{"role": "user", "content": f"Headline: {title}\nSnippet: {description}"}],
    )

    tool_use = next(block for block in response.content if block.type == "tool_use")
    return tool_use.input["summary"]


_SHORT_TITLE_SYSTEM_PROMPT = """You compress a MyCourses assignment title into 1-2 words for a compact \
dashboard table cell. Keep whatever distinguishes it from other assignments in the same course (e.g. an \
assignment number, "Essay", "Quiz 3", "Lab report", "Peer review", "Exam"). Drop filler words like \
"submission", "deadline", "assignment" when a more specific word is available. Never exceed 2 words."""

_SHORT_TITLE_TOOL = {
    "name": "shorten_assignment_title",
    "description": "Compress an assignment title into 1-2 words for a compact dashboard cell.",
    "input_schema": {
        "type": "object",
        "properties": {
            "short_title": {"type": "string", "description": "The compressed title, 1-2 words."},
        },
        "required": ["short_title"],
    },
}


def shorten_assignment_title(title: str, description: str | None) -> str:
    if _client is None:
        raise RuntimeError("ANTHROPIC_API_KEY is not configured")

    content = f"Title: {title}"
    if description:
        content += f"\nDescription: {description}"

    response = _client.messages.create(
        model=MODEL,
        max_tokens=64,
        system=_SHORT_TITLE_SYSTEM_PROMPT,
        tools=[_SHORT_TITLE_TOOL],
        tool_choice={"type": "tool", "name": "shorten_assignment_title"},
        messages=[{"role": "user", "content": content}],
    )

    tool_use = next(block for block in response.content if block.type == "tool_use")
    return tool_use.input["short_title"]


def classify_calendar_category(title: str, description: str | None) -> str:
    if _client is None:
        raise RuntimeError("ANTHROPIC_API_KEY is not configured")

    content = f"Title: {title}"
    if description:
        content += f"\nDescription: {description}"

    response = _client.messages.create(
        model=MODEL,
        max_tokens=128,
        system=_CATEGORY_SYSTEM_PROMPT,
        tools=[_CATEGORY_TOOL],
        tool_choice={"type": "tool", "name": "classify_calendar_category"},
        messages=[{"role": "user", "content": content}],
    )

    tool_use = next(block for block in response.content if block.type == "tool_use")
    return tool_use.input["category"]
