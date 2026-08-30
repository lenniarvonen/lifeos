"""drop digest_state

The dashboard now refreshes on the same interval as the other syncs instead of
once daily (see scheduler.py), so a missed run is simply picked up on the next
interval fire -- the last-run-date catch-up tracking this table existed for is
no longer needed.

Revision ID: 0008
Revises: 0007
Create Date: 2026-07-26

"""
from alembic import op
import sqlalchemy as sa

revision = "0008"
down_revision = "0007"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.drop_table("digest_state")


def downgrade() -> None:
    op.create_table(
        "digest_state",
        sa.Column("id", sa.String, primary_key=True),
        sa.Column("last_run_date", sa.Date, nullable=True),
    )
