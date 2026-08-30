"""create gmail_processed_messages

Revision ID: 0005
Revises: 0004
Create Date: 2026-07-25

"""
from alembic import op
import sqlalchemy as sa

revision = "0005"
down_revision = "0004"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "gmail_processed_messages",
        sa.Column("message_id", sa.String, primary_key=True),
        sa.Column("purpose", sa.String, primary_key=True),
        sa.Column("processed_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )


def downgrade() -> None:
    op.drop_table("gmail_processed_messages")
