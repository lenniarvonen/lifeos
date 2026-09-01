from pydantic import field_validator
from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    database_url: str
    notion_token: str
    notion_database_id: str
    google_client_secret_path: str = "/run/secrets/google_client_secret.json"
    google_token_path: str = "/run/secrets/google_token.json"
    mycourses_ical_url: str | None = None
    sisu_ical_url: str | None = None
    sisu_helsinki_ical_url: str | None = None
    # DigiCampus (digicampus.fi) Moodle calendar export URL -- University of
    # Helsinki course assignment deadlines. The URL embeds a personal Moodle
    # authtoken; it's per-deployment personal data, kept in .env like the other
    # ical feed URLs. See services/digicampus_client.py.
    digicampus_ical_url: str | None = None
    exchange_calendar_ical_url: str | None = None
    google_work_calendar_id: str | None = None

    # A+ (plus.cs.aalto.fi) assignment-deadline source. Pulls module-level
    # closing times for every course the token owner is enrolled in (discovered
    # via /users/me/), mirrored as Assignments the same way MyCourses deadlines
    # are. The token is a personal API token from the A+ profile page, mounted
    # as a Docker secret file rather than kept in .env.
    aplus_enabled: bool = False
    aplus_api_token_path: str = "/run/secrets/aplus_token"
    aplus_api_base_url: str = "https://plus.cs.aalto.fi/api/v2/"

    # A second, separate Google login (e.g. a work/organization account) -- not
    # just another calendar on the main account. Shares the same OAuth client
    # (google_client_secret_path) but needs its own token file since a token is
    # tied to one end-user account. Calendar itself is connected directly in the
    # Notion Calendar app now, not pulled through here -- only the mailbox
    # (Gmail) is still synced by us. Its display name/icon come from
    # channel_display_names/channel_icons under the "gmail_founders" key.
    google_founders_token_path: str = "/run/secrets/google_founders_token.json"
    google_founders_gmail_enabled: bool = False

    telegram_api_id: int | None = None
    telegram_api_hash: str | None = None
    telegram_session_path: str = "/run/secrets/telegram"
    telegram_channels: str | None = None

    telegram_bot_token: str | None = None
    telegram_bot_allowed_user_id: int | None = None

    anthropic_api_key: str | None = None
    notion_suggestions_database_id: str | None = None

    gmail_suggestions_enabled: bool = False

    notion_digest_page_id: str | None = None
    notion_personal_emails_database_id: str | None = None

    notion_classes_database_id: str | None = None
    notion_classes_helsinki_database_id: str | None = None
    notion_assignments_database_id: str | None = None
    notion_freetime_database_id: str | None = None
    notion_meetings_database_id: str | None = None
    notion_work_database_id: str | None = None
    notion_workouts_database_id: str | None = None
    notion_courses_database_id: str | None = None
    notion_tasks_database_id: str | None = None
    notion_replies_database_id: str | None = None

    news_feed_urls: str | None = None

    sync_interval_minutes: int = 15

    # Telegram channel identifier (or a Gmail account key like "gmail"/
    # "gmail_founders") -> human-readable display name / a single-character
    # page icon shown in Notion. Deliberately not shipped with real values --
    # this is per-deployment personal/organizational data (which channels you
    # follow, what you call your accounts), not something to hardcode into a
    # shared template. Unset entries just fall back to the raw channel id and
    # no icon -- see notion_suggestions.py's _display_name/_channel_icon. Set
    # as a JSON object string, e.g. CHANNEL_DISPLAY_NAMES={"gmail_founders": "Work Gmail"}.
    channel_display_names: dict[str, str] = {}
    channel_icons: dict[str, str] = {}

    @field_validator("channel_display_names", "channel_icons", mode="before")
    @classmethod
    def _blank_to_empty_dict(cls, value):
        return {} if value == "" else value

    @field_validator("telegram_api_id", "mycourses_ical_url", "sisu_ical_url", "sisu_helsinki_ical_url",
                      "digicampus_ical_url", "exchange_calendar_ical_url",
                      "google_work_calendar_id", "telegram_api_hash", "telegram_channels",
                      "telegram_bot_token", "telegram_bot_allowed_user_id",
                      "anthropic_api_key", "notion_suggestions_database_id", "notion_digest_page_id",
                      "notion_personal_emails_database_id", "notion_classes_database_id", "notion_classes_helsinki_database_id",
                      "notion_assignments_database_id", "notion_freetime_database_id", "notion_meetings_database_id",
                      "notion_work_database_id", "notion_workouts_database_id", "notion_courses_database_id",
                      "notion_tasks_database_id", "notion_replies_database_id", "news_feed_urls", mode="before")
    @classmethod
    def _blank_to_none(cls, value):
        return None if value == "" else value

    @field_validator("gmail_suggestions_enabled", "google_founders_gmail_enabled", "aplus_enabled", mode="before")
    @classmethod
    def _blank_to_false(cls, value):
        return False if value == "" else value

    class Config:
        env_file = ".env"
        case_sensitive = False


settings = Settings()
