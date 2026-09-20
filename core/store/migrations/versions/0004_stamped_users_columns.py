"""Give a stamped database the users columns the baseline only ever declared.

`0001_baseline` declares `email_verified`, but a database that predates migrations was
*stamped* at that revision rather than built by it -- `init_db` creates the baseline
tables that are missing and then stamps, so a `users` table that already existed was
left exactly as it was. The column has therefore been absent from those databases since
migrations landed, while the SQLAlchemy model insisted it was there. Nothing read it
until Google sign-in wrote `email_verified` on an account, which is when it would have
failed.

Guarded rather than unconditional because a database built by the migrations has the
column already, and this has to be a no-op there.

Revision ID: 0004_stamped_users_columns
Revises: 0003_oauth_identities
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "0004_stamped_users_columns"
down_revision: Union[str, Sequence[str], None] = "0003_oauth_identities"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    columns = {c["name"] for c in sa.inspect(op.get_bind()).get_columns("users")}
    if "email_verified" in columns:
        return
    with op.batch_alter_table("users") as batch:
        batch.add_column(sa.Column(
            "email_verified", sa.Boolean(), nullable=False, server_default="0"
        ))


def downgrade() -> None:
    # Deliberately not dropped: the baseline says this column exists, so removing it
    # would leave the database further from its declared schema, not closer.
    pass
