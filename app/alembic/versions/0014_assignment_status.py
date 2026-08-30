"""add calendar_events.assignment_status

Revision ID: 0014
Revises: 0013
Create Date: 2026-08-17

"""
from alembic import op
import sqlalchemy as sa

revision = "0014"
down_revision = "0013"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("calendar_events", sa.Column("assignment_status", sa.String, nullable=True))


def downgrade() -> None:
    op.drop_column("calendar_events", "assignment_status")
