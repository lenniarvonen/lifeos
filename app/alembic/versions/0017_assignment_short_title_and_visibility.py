"""add calendar_events.short_title and archived_in_notion

Revision ID: 0017
Revises: 0016
Create Date: 2026-08-24

"""
from alembic import op
import sqlalchemy as sa

revision = "0017"
down_revision = "0016"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("calendar_events", sa.Column("short_title", sa.String, nullable=True))
    op.add_column(
        "calendar_events",
        sa.Column("archived_in_notion", sa.Boolean, nullable=False, server_default=sa.false()),
    )


def downgrade() -> None:
    op.drop_column("calendar_events", "archived_in_notion")
    op.drop_column("calendar_events", "short_title")
