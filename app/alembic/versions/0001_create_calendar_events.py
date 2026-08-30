"""create calendar_events

Revision ID: 0001
Revises:
Create Date: 2026-07-21

"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "0001"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute('CREATE EXTENSION IF NOT EXISTS "pgcrypto"')

    op.create_table(
        "calendar_events",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("source", sa.String, nullable=False, server_default="google_calendar"),
        sa.Column("external_id", sa.String, nullable=False),
        sa.Column("calendar_id", sa.String, nullable=True),
        sa.Column("title", sa.String, nullable=False),
        sa.Column("description", sa.String, nullable=True),
        sa.Column("location", sa.String, nullable=True),
        sa.Column("start_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("end_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("is_all_day", sa.Boolean, nullable=False, server_default=sa.false()),
        sa.Column("timezone", sa.String, nullable=True),
        sa.Column("source_etag", sa.String, nullable=True),
        sa.Column("source_updated_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("notion_page_id", sa.String, nullable=True),
        sa.Column("sync_status", sa.String, nullable=False, server_default="pending"),
        sa.Column("sync_error", sa.String, nullable=True),
        sa.Column("last_synced_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("is_deleted", sa.Boolean, nullable=False, server_default=sa.false()),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.UniqueConstraint("source", "external_id", name="uq_calendar_events_source_external_id"),
    )
    op.create_index("idx_calendar_events_sync_status", "calendar_events", ["sync_status"])
    op.create_index("idx_calendar_events_start_at", "calendar_events", ["start_at"])


def downgrade() -> None:
    op.drop_index("idx_calendar_events_start_at", table_name="calendar_events")
    op.drop_index("idx_calendar_events_sync_status", table_name="calendar_events")
    op.drop_table("calendar_events")
