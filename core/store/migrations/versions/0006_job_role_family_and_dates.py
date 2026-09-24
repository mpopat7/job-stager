"""Store each job's role family, and make every posting date sortable.

The Jobs tab fetched the 50 newest rows and then classified and filtered *those*, so a
13,891-job registry showed seven software roles. Filtering and counting by family has to
happen in SQL, which needs the family stored on the row.

`updated_at` is sorted as text, and the Workday fix filled it with "Posted 3 Days Ago",
which sorts above every real date, so "newest" meant a month-old Workday listing. Lever's
epoch milliseconds were wrong the other way. Relative phrases are resolved against the
day the row was discovered; anything unreadable becomes NULL rather than sorting as junk.

The family backfill imports the live classifier on purpose: it is derived data, and the
scheduled crawl reclassifies rows whenever the classifier changes. The date parser is
frozen here because what a stored phrase meant cannot change.

Revision ID: 0006_job_role_family_and_dates
Revises: 0005_workday_board_slugs
"""
from datetime import datetime, timedelta, timezone
import re
from typing import Optional, Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "0006_job_role_family_and_dates"
down_revision: Union[str, Sequence[str], None] = "0005_workday_board_slugs"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_RELATIVE = re.compile(r"posted\s+(today|yesterday|(\d+)\+?\s+days?\s+ago)", re.I)


def _normalize(value: Optional[str], seen) -> Optional[str]:
    if value is None or value == "":
        return None
    text = str(value).strip()
    if re.fullmatch(r"\d{10}(\d{3})?", text):
        seconds = int(text) / (1000 if len(text) == 13 else 1)
        return datetime.fromtimestamp(seconds, tz=timezone.utc).date().isoformat()
    if re.match(r"\d{4}-\d{2}-\d{2}", text):
        return text
    m = _RELATIVE.search(text)
    if m:
        phrase = m.group(1).lower()
        days = 0 if phrase == "today" else 1 if phrase == "yesterday" else int(m.group(2))
        return (seen - timedelta(days=days)).isoformat()
    return None


def _day(value):
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, str) and value:
        try:
            return datetime.fromisoformat(value.replace("Z", "+00:00")).date()
        except ValueError:
            pass
    return datetime.now(timezone.utc).date()


def upgrade() -> None:
    from core.registry.roles import classify_role

    with op.batch_alter_table("jobs") as batch:
        batch.add_column(sa.Column("role_family", sa.String(40)))
        batch.create_index("ix_jobs_role_family", ["role_family"])

    conn = op.get_bind()
    jobs = sa.Table("jobs", sa.MetaData(), autoload_with=conn)
    updates = []
    for row in conn.execute(sa.select(jobs.c.id, jobs.c.title, jobs.c.updated_at, jobs.c.discovered_at)):
        updates.append({
            "row_id": row.id,
            "family": classify_role(row.title),
            "posted": _normalize(row.updated_at, _day(row.discovered_at)),
        })
    stmt = (
        sa.update(jobs)
        .where(jobs.c.id == sa.bindparam("row_id"))
        .values(role_family=sa.bindparam("family"), updated_at=sa.bindparam("posted"))
    )
    for i in range(0, len(updates), 1000):
        conn.execute(stmt, updates[i:i + 1000])


def downgrade() -> None:
    with op.batch_alter_table("jobs") as batch:
        batch.drop_index("ix_jobs_role_family")
        batch.drop_column("role_family")
