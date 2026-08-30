"""add telegram_pending_replies table

Revision ID: 0013
Revises: 0012
Create Date: 2026-08-06

"""
from alembic import op
import sqlalchemy as sa

revision = "0013"
down_revision = "0012"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "telegram_pending_replies",
        sa.Column("chat_id", sa.BigInteger, primary_key=True),
        sa.Column("sender_name", sa.String, nullable=False),
        sa.Column("message_text", sa.String, nullable=False),
        sa.Column("message_date", sa.DateTime(timezone=True), nullable=False),
        sa.Column("resolved", sa.Boolean, nullable=False, server_default=sa.false()),
        sa.Column("notion_page_id", sa.String, nullable=True),
        sa.Column("sync_status", sa.String, nullable=False, server_default="pending"),
        sa.Column("sync_error", sa.String, nullable=True),
        sa.Column("last_synced_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )


def downgrade() -> None:
    op.drop_table("telegram_pending_replies")
