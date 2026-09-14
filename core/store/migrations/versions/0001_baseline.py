"""Baseline: the schema as `create_all` built it before migrations existed.

A database created before this revision already has every table here, so `init_db`
stamps it at this revision instead of running it.

Revision ID: 0001_baseline
Revises:
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "0001_baseline"
down_revision: Union[str, Sequence[str], None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "users",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("handle", sa.String(120), nullable=False, unique=True),
        sa.Column("email", sa.String(320)),
        sa.Column("password_hash", sa.Text(), nullable=False),
        sa.Column("password_salt", sa.Text(), nullable=False),
        sa.Column("email_verified", sa.Boolean(), nullable=False, server_default="0"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )
    op.create_table(
        "profiles",
        sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id", ondelete="CASCADE"),
                  primary_key=True),
        sa.Column("payload", sa.Text(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )
    op.create_table(
        "field_provenance",
        sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id", ondelete="CASCADE"),
                  primary_key=True),
        sa.Column("field_path", sa.String(200), primary_key=True),
        sa.Column("source", sa.String(40), nullable=False),
        sa.Column("confirmed_at", sa.DateTime(timezone=True)),
    )
    op.create_table(
        "sessions",
        sa.Column("token", sa.String(64), primary_key=True),
        sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id", ondelete="CASCADE"),
                  nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_table(
        "applications",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id", ondelete="CASCADE"),
                  nullable=False),
        sa.Column("date_applied", sa.String(20), nullable=False),
        sa.Column("company", sa.String(300), nullable=False),
        sa.Column("role", sa.String(400), nullable=False),
        sa.Column("source", sa.String(60), nullable=False),
        sa.Column("link", sa.Text(), nullable=False),
        sa.Column("stage", sa.String(60), nullable=False, server_default="Applied"),
        sa.Column("grad_year", sa.Integer(), nullable=False),
        sa.Column("notes", sa.Text()),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.UniqueConstraint("user_id", "company", "role", "link", name="uq_application_per_user"),
    )
    op.create_index("ix_applications_user_id", "applications", ["user_id"])
    op.create_table(
        "job_matches",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id", ondelete="CASCADE"),
                  nullable=False),
        sa.Column("job_id", sa.String(500)),
        sa.Column("candidate_job_ids", sa.Text()),
        sa.Column("origin", sa.String(20), nullable=False),
        sa.Column("status", sa.String(20), nullable=False),
        sa.Column("matched_by", sa.String(40), nullable=False),
        sa.Column("source_key", sa.String(100), nullable=False),
        sa.Column("external_row", sa.Integer()),
        sa.Column("company", sa.String(300), nullable=False),
        sa.Column("role", sa.String(400), nullable=False),
        sa.Column("link", sa.Text()),
        sa.Column("stage", sa.String(60)),
        sa.Column("date_applied", sa.String(40)),
        sa.Column("notes", sa.Text()),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.UniqueConstraint("user_id", "origin", "source_key", name="uq_match_source_per_user"),
    )
    op.create_index("ix_job_matches_user_id", "job_matches", ["user_id"])
    op.create_index("ix_job_matches_job_id", "job_matches", ["job_id"])
    op.create_table(
        "login_attempts",
        sa.Column("id", sa.BigInteger().with_variant(sa.Integer(), "sqlite"), primary_key=True,
                  autoincrement=True),
        sa.Column("bucket", sa.String(200), nullable=False),
        sa.Column("attempted_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_login_attempts_bucket", "login_attempts", ["bucket"])


def downgrade() -> None:
    for table in ("login_attempts", "job_matches", "applications", "sessions",
                  "field_provenance", "profiles", "users"):
        op.drop_table(table)
