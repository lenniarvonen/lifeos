import uuid
from datetime import date, datetime

from sqlalchemy import BigInteger, Boolean, Date, DateTime, ForeignKey, Index, String, UniqueConstraint, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from db import Base


class Task(Base):
    """Added via the Telegram bot's /task command -- always dated today at
    creation (editable afterward in Notion). One-directional sync (Postgres ->
    Notion push only); the Done checkbox is not synced back."""

    __tablename__ = "tasks"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)

    title: Mapped[str] = mapped_column(String, nullable=False)
    date: Mapped[date] = mapped_column(Date, nullable=False)
    done: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)

    # Last date a Telegram due-reminder was sent for this task -- caps reminders
    # to once per day per task. See task_service.send_due_reminders.
    last_reminded_date: Mapped[date | None] = mapped_column(Date, nullable=True)

    notion_page_id: Mapped[str | None] = mapped_column(String, nullable=True)
    sync_status: Mapped[str] = mapped_column(String, nullable=False, default="pending")
    sync_error: Mapped[str | None] = mapped_column(String, nullable=True)
    last_synced_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class Course(Base):
    """Derived from MyCourses calendar titles, which embed "<CODE> - <Name>, <type>,
    <start>-<end>" -- one row per (university, code, period_start) so retaking/
    re-offering the same course code in a different semester is a distinct row,
    and course codes never collide across universities. See course_sync.py."""

    __tablename__ = "courses"
    __table_args__ = (
        UniqueConstraint("university", "code", "period_start", name="uq_courses_university_code_period_start"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)

    university: Mapped[str] = mapped_column(String, nullable=False, default="Aalto")
    code: Mapped[str] = mapped_column(String, nullable=False)
    name: Mapped[str] = mapped_column(String, nullable=False)
    period_start: Mapped[date] = mapped_column(Date, nullable=False)
    period_end: Mapped[date] = mapped_column(Date, nullable=False)
    status: Mapped[str] = mapped_column(String, nullable=False, default="Upcoming")

    notion_page_id: Mapped[str | None] = mapped_column(String, nullable=True)
    sync_status: Mapped[str] = mapped_column(String, nullable=False, default="pending")
    sync_error: Mapped[str | None] = mapped_column(String, nullable=True)
    last_synced_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class CalendarEvent(Base):
    __tablename__ = "calendar_events"
    __table_args__ = (
        UniqueConstraint("source", "external_id", name="uq_calendar_events_source_external_id"),
        Index("idx_calendar_events_sync_status", "sync_status"),
        Index("idx_calendar_events_start_at", "start_at"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)

    source: Mapped[str] = mapped_column(String, nullable=False, default="google_calendar")
    external_id: Mapped[str] = mapped_column(String, nullable=False)
    calendar_id: Mapped[str | None] = mapped_column(String, nullable=True)

    title: Mapped[str] = mapped_column(String, nullable=False)
    description: Mapped[str | None] = mapped_column(String, nullable=True)
    location: Mapped[str | None] = mapped_column(String, nullable=True)
    category: Mapped[str | None] = mapped_column(String, nullable=True)

    # Only set for Assignment events (mycourses/aplus/digicampus) matched to a
    # course by code (see course_sync.py) -- drives the Notion relation to the
    # Courses db.
    course_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("courses.id"), nullable=True)
    course: Mapped["Course | None"] = relationship("Course")

    # Set for Assignment events: parsed out of the raw MyCourses title suffix
    # (see sync_service._split_course_details), or supplied by aplus_client /
    # digicampus_client. Kept so course_sync can match the assignment to its
    # Course row by exact code even when the code doesn't appear in `title` or
    # `description`.
    course_code: Mapped[str | None] = mapped_column(String, nullable=True)

    # Only meaningful for category == "Assignments". "Upcoming"/"Due soon"/"Overdue"
    # are auto-computed from the due date on every sync; "In progress"/"Done" are
    # set by the user in Notion and never overwritten once set -- see
    # sync_service.sync_assignment_statuses.
    assignment_status: Mapped[str | None] = mapped_column(String, nullable=True)

    # Only meaningful for category == "Assignments". LLM-compressed 1-2 word
    # version of `title`, computed once (see sync_service.sync_assignment_display)
    # and written to a "Short Title" property so the dashboard table reads
    # tersely; `title` is left untouched as the full-text source of truth.
    short_title: Mapped[str | None] = mapped_column(String, nullable=True)

    # Only meaningful for category == "Assignments". Tracks whether this event's
    # Notion page is currently archived for being outside the visibility window
    # (see sync_service.ASSIGNMENT_VISIBILITY_WINDOW) -- distinct from is_deleted,
    # which means the source event itself was cancelled/removed.
    archived_in_notion: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)

    start_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    end_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    is_all_day: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    timezone: Mapped[str | None] = mapped_column(String, nullable=True)

    source_etag: Mapped[str | None] = mapped_column(String, nullable=True)
    source_updated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    notion_page_id: Mapped[str | None] = mapped_column(String, nullable=True)
    sync_status: Mapped[str] = mapped_column(String, nullable=False, default="pending")
    sync_error: Mapped[str | None] = mapped_column(String, nullable=True)
    last_synced_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    is_deleted: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)

    # Telegram-bot duration calibration (source == "telegram_bot" only): after a
    # bot-created event's end_at passes, a check-in message asks how long it
    # actually took; the reply feeds back into future duration estimates via
    # event_extraction's calibration context. See telegram_bot_service.py.
    duration_asked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    duration_checkin_message_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    duration_confirmed_minutes: Mapped[int | None] = mapped_column(BigInteger, nullable=True)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class EventSuggestion(Base):
    __tablename__ = "event_suggestions"
    __table_args__ = (
        UniqueConstraint("channel", "message_id", name="uq_event_suggestions_channel_message_id"),
        Index("idx_event_suggestions_status", "status"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)

    channel: Mapped[str] = mapped_column(String, nullable=False)
    message_id: Mapped[str] = mapped_column(String, nullable=False)
    message_text: Mapped[str] = mapped_column(String, nullable=False)
    message_date: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    title: Mapped[str | None] = mapped_column(String, nullable=True)
    description: Mapped[str | None] = mapped_column(String, nullable=True)
    location: Mapped[str | None] = mapped_column(String, nullable=True)
    start_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    end_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    is_all_day: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)

    status: Mapped[str] = mapped_column(String, nullable=False, default="pending")
    notion_page_id: Mapped[str | None] = mapped_column(String, nullable=True)
    sync_status: Mapped[str] = mapped_column(String, nullable=False, default="pending")
    sync_error: Mapped[str | None] = mapped_column(String, nullable=True)
    last_synced_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class TelegramSyncState(Base):
    __tablename__ = "telegram_sync_state"

    channel: Mapped[str] = mapped_column(String, primary_key=True)
    last_message_id: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)


class TelegramPendingReply(Base):
    """One row per 1:1 DM chat currently awaiting a reply (see
    telegram_client.fetch_pending_replies). Re-evaluated every sync: a chat
    that no longer qualifies (you replied) gets resolved=True and its Notion
    page archived automatically -- no manual done-marking needed. A chat can
    be reopened (resolved -> False) if a new unanswered message arrives later,
    which is why chat_id is the PK rather than one-row-per-message."""

    __tablename__ = "telegram_pending_replies"

    chat_id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    sender_name: Mapped[str] = mapped_column(String, nullable=False)
    message_text: Mapped[str] = mapped_column(String, nullable=False)
    message_date: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    resolved: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)

    notion_page_id: Mapped[str | None] = mapped_column(String, nullable=True)
    sync_status: Mapped[str] = mapped_column(String, nullable=False, default="pending")
    sync_error: Mapped[str | None] = mapped_column(String, nullable=True)
    last_synced_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class GmailProcessedMessage(Base):
    """Records that a Gmail message has already been run through a given LLM
    classification (purpose='event' for suggestion extraction, 'digest' for the
    daily personal-email summary), regardless of the classification's outcome.
    Without this, an email classified as "not relevant" leaves no trace anywhere
    else, so as long as it stays unread it gets reclassified (and re-billed) on
    every subsequent sync cycle -- this table is what stops that.

    `account` scopes message_id per Gmail account ("gmail" for the primary
    account, "gmail_founders" for the secondary account, etc.) -- message
    IDs are only unique within a single mailbox, not globally."""

    __tablename__ = "gmail_processed_messages"

    account: Mapped[str] = mapped_column(String, primary_key=True, default="gmail")
    message_id: Mapped[str] = mapped_column(String, primary_key=True)
    purpose: Mapped[str] = mapped_column(String, primary_key=True)
    processed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class NewsSummaryCache(Base):
    """Caches a news headline's LLM summary by article link, so the dashboard
    refresh (every sync_interval_minutes) doesn't re-bill Haiku for the same
    top-of-feed headlines on every single run -- only genuinely new links get
    summarized. Pruned by age in digest_service, not by explicit eviction."""

    __tablename__ = "news_summary_cache"

    link: Mapped[str] = mapped_column(String, primary_key=True)
    source: Mapped[str] = mapped_column(String, nullable=False)
    title: Mapped[str] = mapped_column(String, nullable=False)
    summary: Mapped[str] = mapped_column(String, nullable=False)
    cached_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
