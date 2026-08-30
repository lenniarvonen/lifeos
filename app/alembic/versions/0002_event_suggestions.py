"""create event_suggestions and telegram_sync_state

Revision ID: 0002
Revises: 0001
Create Date: 2026-07-23

"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "event_suggestions",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("channel", sa.String, nullable=False),
        sa.Column("message_id", sa.BigInteger, nullable=False),
        sa.Column("message_text", sa.String, nullable=False),
        sa.Column("message_date", sa.DateTime(timezone=True), nullable=False),
        sa.Column("title", sa.String, nullable=True),
        sa.Column("description", sa.String, nullable=True),
        sa.Column("location", sa.String, nullable=True),
        sa.Column("start_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("end_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("is_all_day", sa.Boolean, nullable=False, server_default=sa.false()),
        sa.Column("status", sa.String, nullable=False, server_default="pending"),
        sa.Column("notion_page_id", sa.String, nullable=True),
        sa.Column("sync_status", sa.String, nullable=False, server_default="pending"),
        sa.Column("sync_error", sa.String, nullable=True),
        sa.Column("last_synced_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.UniqueConstraint("channel", "message_id", name="uq_event_suggestions_channel_message_id"),
    )
    op.create_index("idx_event_suggestions_status", "event_suggestions", ["status"])

    op.create_table(
        "telegram_sync_state",
        sa.Column("channel", sa.String, primary_key=True),
        sa.Column("last_message_id", sa.BigInteger, nullable=False, server_default="0"),
    )


def downgrade() -> None:
    op.drop_table("telegram_sync_state")
    op.drop_index("idx_event_suggestions_status", table_name="event_suggestions")
    op.drop_table("event_suggestions")
