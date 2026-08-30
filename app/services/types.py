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
