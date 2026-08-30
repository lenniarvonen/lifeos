"""scope gmail_processed_messages by account (for multi-account Gmail support)

Revision ID: 0012
Revises: 0011
Create Date: 2026-08-06

"""
from alembic import op
import sqlalchemy as sa

revision = "0012"
down_revision = "0011"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "gmail_processed_messages",
        sa.Column("account", sa.String, nullable=False, server_default="gmail"),
    )
    op.drop_constraint("gmail_processed_messages_pkey", "gmail_processed_messages", type_="primary")
    op.create_primary_key(
        "gmail_processed_messages_pkey",
        "gmail_processed_messages",
        ["account", "message_id", "purpose"],
    )


def downgrade() -> None:
    op.drop_constraint("gmail_processed_messages_pkey", "gmail_processed_messages", type_="primary")
    op.create_primary_key(
        "gmail_processed_messages_pkey",
        "gmail_processed_messages",
        ["message_id", "purpose"],
    )
    op.drop_column("gmail_processed_messages", "account")
