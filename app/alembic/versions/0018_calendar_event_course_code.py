"""add calendar_events.course_code

Revision ID: 0018
Revises: 0017
Create Date: 2026-08-31

"""
from alembic import op
import sqlalchemy as sa

revision = "0018"
down_revision = "0017"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("calendar_events", sa.Column("course_code", sa.String, nullable=True))


def downgrade() -> None:
    op.drop_column("calendar_events", "course_code")
