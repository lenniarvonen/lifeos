"""add courses table and calendar_events.course_id

Revision ID: 0010
Revises: 0009
Create Date: 2026-08-04

"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "0010"
down_revision = "0009"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "courses",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("code", sa.String, nullable=False),
        sa.Column("name", sa.String, nullable=False),
        sa.Column("period_start", sa.Date, nullable=False),
        sa.Column("period_end", sa.Date, nullable=False),
        sa.Column("status", sa.String, nullable=False, server_default="Upcoming"),
        sa.Column("notion_page_id", sa.String, nullable=True),
        sa.Column("sync_status", sa.String, nullable=False, server_default="pending"),
        sa.Column("sync_error", sa.String, nullable=True),
        sa.Column("last_synced_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.UniqueConstraint("code", "period_start", name="uq_courses_code_period_start"),
    )
    op.add_column(
        "calendar_events",
        sa.Column("course_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("courses.id"), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("calendar_events", "course_id")
    op.drop_table("courses")
