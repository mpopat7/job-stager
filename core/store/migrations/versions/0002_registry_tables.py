"""Move the company and job registry into the main database.

It used to be a separate `companies.db` SQLite file, which a host without a persistent
disk loses on every restart. The application-state columns the old `jobs` table carried
(status, stage, applied_date, notes) are not recreated: that state is per-user and lives
in `job_matches`. `python3 -m core.store.migrate import-registry` copies an old file in.

Revision ID: 0002_registry_tables
Revises: 0001_baseline
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "0002_registry_tables"
down_revision: Union[str, Sequence[str], None] = "0001_baseline"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "companies",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("name", sa.String(300), nullable=False),
        sa.Column("slug", sa.String(200), nullable=False),
        sa.Column("provider", sa.String(40), nullable=False),
        sa.Column("board_url", sa.Text(), nullable=False),
        sa.Column("career_url", sa.Text()),
        sa.Column("verified", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("active", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("job_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("last_scanned", sa.String(40)),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.UniqueConstraint("provider", "slug", name="uq_company_board"),
    )
    op.create_table(
        "jobs",
        sa.Column("id", sa.String(500), primary_key=True),
        sa.Column("company", sa.String(300)),
        sa.Column("company_slug", sa.String(200), nullable=False),
        sa.Column("provider", sa.String(40), nullable=False),
        sa.Column("title", sa.Text(), nullable=False),
        sa.Column("location", sa.Text()),
        sa.Column("url", sa.Text(), nullable=False),
        sa.Column("apply_url", sa.Text()),
        sa.Column("is_internship", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("updated_at", sa.String(40)),
        sa.Column("discovered_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )
    op.create_index("ix_jobs_url", "jobs", ["url"])


def downgrade() -> None:
    op.drop_table("jobs")
    op.drop_table("companies")
