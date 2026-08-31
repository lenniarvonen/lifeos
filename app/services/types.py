from dataclasses import dataclass
from datetime import datetime


@dataclass
class NormalizedEvent:
    external_id: str
    title: str
    description: str | None
    location: str | None
    start_at: datetime | None
    end_at: datetime | None
    is_all_day: bool
    timezone: str | None
    etag: str | None
    source_updated_at: datetime | None
    is_deleted: bool
    # Only populated by _sync_mycourses for Assignment deadlines -- the course code
    # lifted off the raw title before its verbose suffix is stripped.
    course_code: str | None = None
