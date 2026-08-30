"""create digest_state

Revision ID: 0004
Revises: 0003
Create Date: 2026-07-24

"""
from alembic import op
import sqlalchemy as sa

revision = "0004"
down_revision = "0003"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "digest_state",
        sa.Column("id", sa.String, primary_key=True),
        sa.Column("last_run_date", sa.Date, nullable=True),
    )


def downgrade() -> None:
    op.drop_table("digest_state")
