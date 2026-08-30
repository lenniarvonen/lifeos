"""add courses.university, scope uniqueness by it too

Revision ID: 0016
Revises: 0015
Create Date: 2026-08-19

"""
from alembic import op
import sqlalchemy as sa

revision = "0016"
down_revision = "0015"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("courses", sa.Column("university", sa.String, nullable=True))
    op.execute("UPDATE courses SET university = 'Aalto' WHERE university IS NULL")
    op.alter_column("courses", "university", nullable=False)

    op.drop_constraint("uq_courses_code_period_start", "courses", type_="unique")
    op.create_unique_constraint(
        "uq_courses_university_code_period_start", "courses", ["university", "code", "period_start"]
    )


def downgrade() -> None:
    op.drop_constraint("uq_courses_university_code_period_start", "courses", type_="unique")
    op.create_unique_constraint("uq_courses_code_period_start", "courses", ["code", "period_start"])
    op.drop_column("courses", "university")
