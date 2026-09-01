"""A+ (plus.cs.aalto.fi) REST API v2 client -- fetches module-level assignment
deadlines for every course the token owner is enrolled in.

A+ models submission deadlines at the course-module level ("Round 5: Recursion"),
not per individual exercise, so one NormalizedEvent is emitted per non-hidden
module that has a closing_time and at least one submittable exercise. These are
fed into sync_service as source="aplus" and categorised as Assignments, exactly
like MyCourses deadlines -- including the course-code -> Course relation match
in course_sync.py (A+ course codes like "CS-A1120" are the same shape).

Auth is a personal API token from the A+ profile page, sent as an
"Authorization: Token <token>" header and mounted as a Docker secret file
(APLUS_API_TOKEN_PATH) rather than kept in .env.
"""
import datetime as dt
import hashlib
import logging
import re

import httpx

from services.types import NormalizedEvent

logger = logging.getLogger("aplus_client")

# A+ reverse URLs come back without a trailing slash, e.g.
# "https://plus.cs.aalto.fi/api/v2/courses/557".
_COURSE_ID_PATTERN = re.compile(r"/courses/(\d+)")

# A+ embeds translations inline as "|en:English text|fi:Suomenkielinen teksti|"
# runs anywhere in a string field (module display_name especially, e.g.
# "5. |en:Hashing|fi:Hajautus|"). Collapse each run to one language.
_I18N_RUN = re.compile(r"\|((?:[a-z]{2}:[^|]*\|)+)")


def _pick_language(text: str, language: str = "en") -> str:
    def replace(match: re.Match) -> str:
        by_lang: dict[str, str] = {}
        for segment in match.group(1).rstrip("|").split("|"):
            code, _, value = segment.partition(":")
            by_lang[code] = value
        return by_lang.get(language) or next(iter(by_lang.values()), "")

    return _I18N_RUN.sub(replace, text).strip()


def _read_token(token_path: str) -> str:
    with open(token_path) as f:
        return f.read().strip()


def _parse_dt(value: str | None) -> dt.datetime | None:
    if not value:
        return None
    parsed = dt.datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=dt.timezone.utc)
    return parsed


def _content_etag(*parts: str) -> str:
    return hashlib.sha256("|".join(parts).encode()).hexdigest()


def fetch_events(
    token_path: str, base_url: str, window_past_days: int, window_future_days: int
) -> list[NormalizedEvent]:
    """One NormalizedEvent per live A+ module deadline across all enrolled
    courses, clipped to [now - window_past_days, now + window_future_days] so the
    stored start_at range matches the ical feeds' -- sync_service._mark_dropped_
    from_window_as_deleted reuses those same bounds as its "feed is authoritative
    for this date" window when tombstoning modules that vanish from A+.

    Any per-course fetch failure propagates rather than returning a partial list:
    a silently-short result would wrongly tombstone a whole course's deadlines."""
    token = _read_token(token_path)
    now = dt.datetime.now(dt.timezone.utc)
    window_start = now - dt.timedelta(days=window_past_days)
    window_end = now + dt.timedelta(days=window_future_days)

    events: list[NormalizedEvent] = []
    with httpx.Client(
        base_url=base_url,
        headers={"Authorization": f"Token {token}"},
        timeout=30,
        follow_redirects=True,
    ) as client:
        me = client.get("users/me/")
        me.raise_for_status()
        enrolled = me.json().get("enrolled_courses", [])

        for course in enrolled:
            match = _COURSE_ID_PATTERN.search(course.get("url", ""))
            if not match:
                logger.warning("A+ course has no parseable id: %r", course.get("url"))
                continue
            course_id = match.group(1)
            code = course.get("code") or None
            course_name = _pick_language(course.get("name") or "") or None

            response = client.get(f"courses/{course_id}/exercises/")
            response.raise_for_status()

            for module in response.json().get("results", []):
                if module.get("is_hidden"):
                    continue
                closing = _parse_dt(module.get("closing_time"))
                if closing is None:
                    continue
                if not module.get("exercises"):
                    continue  # chapter-only module, no submission deadline
                if not window_start <= closing <= window_end:
                    continue

                title = _pick_language(module.get("display_name") or "") or "(no title)"
                events.append(
                    NormalizedEvent(
                        external_id=f"aplus:module:{module['id']}",
                        title=title,
                        description=course_name,
                        location=module.get("html_url") or None,
                        start_at=closing,
                        end_at=None,
                        is_all_day=False,
                        timezone=None,
                        etag=_content_etag(title, closing.isoformat(), code or "", course_name or ""),
                        source_updated_at=None,
                        is_deleted=False,
                        course_code=code,
                    )
                )

    return events
