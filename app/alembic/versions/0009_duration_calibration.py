"""add duration calibration columns to calendar_events

Revision ID: 0009
Revises: 0008
Create Date: 2026-07-27

"""
from alembic import op
import sqlalchemy as sa

revision = "0009"
down_revision = "0008"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("calendar_events", sa.Column("duration_asked_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("calendar_events", sa.Column("duration_checkin_message_id", sa.BigInteger, nullable=True))
    op.add_column("calendar_events", sa.Column("duration_confirmed_minutes", sa.BigInteger, nullable=True))


def downgrade() -> None:
    op.drop_column("calendar_events", "duration_confirmed_minutes")
    op.drop_column("calendar_events", "duration_checkin_message_id")
    op.drop_column("calendar_events", "duration_asked_at")
