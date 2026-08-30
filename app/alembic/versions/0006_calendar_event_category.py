"""add category to calendar_events

Revision ID: 0006
Revises: 0005
Create Date: 2026-07-25

"""
from alembic import op
import sqlalchemy as sa

revision = "0006"
down_revision = "0005"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("calendar_events", sa.Column("category", sa.String, nullable=True))


def downgrade() -> None:
    op.drop_column("calendar_events", "category")
