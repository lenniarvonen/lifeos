"""event_suggestions.message_id: BIGINT -> TEXT (Gmail IDs are hex strings)

Revision ID: 0003
Revises: 0002
Create Date: 2026-07-24

"""
from alembic import op
import sqlalchemy as sa

revision = "0003"
down_revision = "0002"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.alter_column(
        "event_suggestions",
        "message_id",
        type_=sa.String,
        postgresql_using="message_id::TEXT",
    )


def downgrade() -> None:
    op.alter_column(
        "event_suggestions",
        "message_id",
        type_=sa.BigInteger,
        postgresql_using="message_id::BIGINT",
    )
