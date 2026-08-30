"""add tasks table

Revision ID: 0011
Revises: 0010
Create Date: 2026-08-04

"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "0011"
down_revision = "0010"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "tasks",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("title", sa.String, nullable=False),
        sa.Column("date", sa.Date, nullable=False),
        sa.Column("done", sa.Boolean, nullable=False, server_default=sa.false()),
        sa.Column("notion_page_id", sa.String, nullable=True),
        sa.Column("sync_status", sa.String, nullable=False, server_default="pending"),
        sa.Column("sync_error", sa.String, nullable=True),
        sa.Column("last_synced_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )


def downgrade() -> None:
    op.drop_table("tasks")
