"""add news_summary_cache

Revision ID: 0007
Revises: 0006
Create Date: 2026-07-26

"""
from alembic import op
import sqlalchemy as sa

revision = "0007"
down_revision = "0006"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "news_summary_cache",
        sa.Column("link", sa.String, primary_key=True),
        sa.Column("source", sa.String, nullable=False),
        sa.Column("title", sa.String, nullable=False),
        sa.Column("summary", sa.String, nullable=False),
        sa.Column("cached_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )


def downgrade() -> None:
    op.drop_table("news_summary_cache")
