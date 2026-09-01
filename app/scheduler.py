import logging

from apscheduler.schedulers.background import BackgroundScheduler

from config import settings
from services import (
    course_sync,
    digest_service,
    suggestion_service,
    sync_service,
    task_service,
    telegram_bot_service,
    telegram_reply_sync,
)

logger = logging.getLogger("scheduler")

scheduler = BackgroundScheduler()


def _run_calendar_sync_job() -> None:
    try:
        sync_service.run_sync()
    except Exception:  # noqa: BLE001 -- keep the scheduler alive across failed runs
        logger.exception("Scheduled calendar sync run failed")


def _run_telegram_sync_job() -> None:
    try:
        suggestion_service.ingest_telegram()
        suggestion_service.process_reviewed()
    except Exception:  # noqa: BLE001
        logger.exception("Scheduled Telegram sync run failed")


def _run_gmail_sync_job() -> None:
    try:
        suggestion_service.ingest_gmail()
        suggestion_service.process_reviewed()
    except Exception:  # noqa: BLE001
        logger.exception("Scheduled Gmail sync run failed")


def _run_dashboard_refresh_job() -> None:
    try:
        digest_service.refresh_dashboard()
    except Exception:  # noqa: BLE001
        logger.exception("Scheduled dashboard refresh failed")


def _run_notion_deletion_sync_job() -> None:
    try:
        sync_service.sync_deletions_from_notion()
    except Exception:  # noqa: BLE001
        logger.exception("Scheduled Notion-deletion sync failed")


def _run_duration_checkin_job() -> None:
    try:
        telegram_bot_service.send_duration_checkins()
    except Exception:  # noqa: BLE001
        logger.exception("Scheduled duration check-in run failed")


def _run_course_sync_job() -> None:
    try:
        course_sync.run()
    except Exception:  # noqa: BLE001
        logger.exception("Scheduled course sync run failed")


def _run_telegram_reply_sync_job() -> None:
    try:
        telegram_reply_sync.sync_pending_replies()
    except Exception:  # noqa: BLE001
        logger.exception("Scheduled Telegram reply sync run failed")


def _run_task_reminder_job() -> None:
    try:
        task_service.send_due_reminders()
    except Exception:  # noqa: BLE001
        logger.exception("Scheduled task reminder run failed")


def start() -> None:
    scheduler.add_job(
        _run_calendar_sync_job,
        "interval",
        minutes=settings.sync_interval_minutes,
        id="calendar_sync",
    )
    if settings.telegram_channels:
        scheduler.add_job(
            _run_telegram_sync_job,
            "interval",
            minutes=settings.sync_interval_minutes,
            id="telegram_sync",
        )
    if settings.gmail_suggestions_enabled or settings.google_founders_gmail_enabled:
        scheduler.add_job(
            _run_gmail_sync_job,
            "interval",
            minutes=settings.sync_interval_minutes,
            id="gmail_sync",
        )
    scheduler.add_job(
        _run_dashboard_refresh_job,
        "interval",
        minutes=settings.sync_interval_minutes,
        id="dashboard_refresh",
    )
    scheduler.add_job(
        _run_notion_deletion_sync_job,
        "interval",
        minutes=settings.sync_interval_minutes,
        id="notion_deletion_sync",
    )
    if settings.telegram_bot_token:
        scheduler.add_job(
            _run_duration_checkin_job,
            "interval",
            minutes=settings.sync_interval_minutes,
            id="duration_checkin",
        )
    if (settings.mycourses_ical_url or settings.aplus_enabled) and settings.notion_courses_database_id:
        scheduler.add_job(
            _run_course_sync_job,
            "interval",
            minutes=settings.sync_interval_minutes,
            id="course_sync",
        )
    if settings.telegram_api_id and settings.notion_replies_database_id:
        scheduler.add_job(
            _run_telegram_reply_sync_job,
            "interval",
            minutes=settings.sync_interval_minutes,
            id="telegram_reply_sync",
        )
    if settings.telegram_bot_token:
        scheduler.add_job(
            _run_task_reminder_job,
            "interval",
            minutes=settings.sync_interval_minutes,
            id="task_reminder",
        )
    scheduler.start()


def shutdown() -> None:
    scheduler.shutdown(wait=False)
