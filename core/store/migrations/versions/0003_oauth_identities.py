"""External sign-in identities, and passwords that are allowed to be absent.

Google-only sign-in is what lets a deployment skip an email service entirely: no
verification mail, no password reset, no address to send either one from. An account
created that way has no password, so the two credential columns stop being required.

SQLite cannot drop a NOT NULL in place, which is why the users change runs through
batch_alter_table -- it copies the table rather than altering it.

Revision ID: 0003_oauth_identities
Revises: 0002_registry_tables
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "0003_oauth_identities"
down_revision: Union[str, Sequence[str], None] = "0002_registry_tables"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "oauth_identities",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("user_id", sa.Integer(),
                  sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("provider", sa.String(40), nullable=False),
        sa.Column("subject", sa.String(255), nullable=False),
        sa.Column("email", sa.String(320)),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.UniqueConstraint("provider", "subject", name="uq_identity_per_provider"),
    )
    op.create_index("ix_oauth_identities_user_id", "oauth_identities", ["user_id"])

    with op.batch_alter_table("users") as batch:
        batch.alter_column("password_hash", existing_type=sa.Text(), nullable=True)
        batch.alter_column("password_salt", existing_type=sa.Text(), nullable=True)


def downgrade() -> None:
    # Reinstating NOT NULL would fail on any Google-only account, so the rows that have
    # no password go first. Losing them is the honest outcome: there is no password to
    # invent for an account that never had one.
    op.execute("DELETE FROM users WHERE password_hash IS NULL")
    with op.batch_alter_table("users") as batch:
        batch.alter_column("password_hash", existing_type=sa.Text(), nullable=False)
        batch.alter_column("password_salt", existing_type=sa.Text(), nullable=False)
    op.drop_index("ix_oauth_identities_user_id", table_name="oauth_identities")
    op.drop_table("oauth_identities")
