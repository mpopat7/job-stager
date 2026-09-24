"""Re-derive Workday board slugs that the old URL resolver got wrong.

The resolver's catch-all Workday pattern assumed every URL starts with a locale segment.
The feed mostly hands out URLs without one (`host/UR_External/job/...`), so it took the
portal as the locale and the literal `job` as the portal. The registry filled with boards
like `abb.wd3.myworkdayjobs.com/abb/job`, every one of which 404s, and jobs from several
real portals of one tenant were filed under that single fake board.

Each Workday job's slug is re-derived from its own URL. Job ids embed the slug
(`workday:<slug>:<id>`), so the ids are rewritten too -- leaving them would duplicate every
job on the next feed import -- and `job_matches` is re-pointed at the new ids. Boards whose
slug names no real portal are then deleted; the corrected boards replace them.

The URL parser is frozen here rather than imported: a migration has to keep producing the
same result after the application's parser changes again.

Revision ID: 0005_workday_board_slugs
Revises: 0004_stamped_users_columns
"""
import json
import re
from typing import Optional, Sequence, Union
from urllib.parse import urlparse

from alembic import op
import sqlalchemy as sa

revision: str = "0005_workday_board_slugs"
down_revision: Union[str, Sequence[str], None] = "0004_stamped_users_columns"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_LOCALE = re.compile(r"^[a-z]{2}(?:-[A-Za-z]{2,4})?(?:-[A-Z]{2})?$")
_NOT_A_PORTAL = {"job", "details", "wday", "login", "apply"}


def _slug_from_url(url: str) -> Optional[str]:
    parsed = urlparse(url or "")
    host = parsed.netloc.lower()
    if not host.endswith(".myworkdayjobs.com"):
        return None
    parts = [p for p in parsed.path.split("/") if p]
    if parts and _LOCALE.match(parts[0]):
        parts = parts[1:]
    if not parts or parts[0].lower() in _NOT_A_PORTAL:
        return None
    return f"{host}/{host.split('.', 1)[0]}/{parts[0]}"


def _slug_is_valid(slug: str) -> bool:
    tokens = slug.split("/")
    return len(tokens) == 3 and tokens[2].lower() not in _NOT_A_PORTAL


def upgrade() -> None:
    conn = op.get_bind()
    meta = sa.MetaData()
    companies = sa.Table("companies", meta, autoload_with=conn)
    jobs = sa.Table("jobs", meta, autoload_with=conn)
    matches = sa.Table("job_matches", meta, autoload_with=conn)

    names = {
        row.slug: row.name
        for row in conn.execute(
            sa.select(companies.c.slug, companies.c.name).where(companies.c.provider == "workday")
        )
    }
    existing_ids = set(
        conn.execute(sa.select(jobs.c.id).where(jobs.c.provider == "workday")).scalars()
    )

    renamed: dict[str, str] = {}
    new_boards: dict[str, str] = {}
    rows = conn.execute(
        sa.select(jobs.c.id, jobs.c.company, jobs.c.company_slug, jobs.c.url)
        .where(jobs.c.provider == "workday")
    ).all()
    for row in rows:
        slug = _slug_from_url(row.url)
        if not slug or slug == row.company_slug:
            continue
        prefix = f"workday:{row.company_slug}:"
        if not row.id.startswith(prefix):
            continue
        new_id = f"workday:{slug}:{row.id[len(prefix):]}"
        if slug not in names:
            new_boards.setdefault(slug, names.get(row.company_slug) or row.company or slug)

        if new_id in existing_ids:
            # The corrected job is already present (a later scan found it); drop the copy.
            conn.execute(sa.delete(jobs).where(jobs.c.id == row.id))
        else:
            conn.execute(
                sa.update(jobs).where(jobs.c.id == row.id).values(id=new_id, company_slug=slug)
            )
            existing_ids.add(new_id)
        existing_ids.discard(row.id)
        renamed[row.id] = new_id

    for slug, name in new_boards.items():
        host, _tenant, portal = slug.split("/")
        conn.execute(sa.insert(companies).values(
            name=name, slug=slug, provider="workday", board_url=f"https://{host}/{portal}",
            verified=True, active=True, job_count=0,
        ))

    if renamed:
        for match in conn.execute(
            sa.select(matches.c.id, matches.c.job_id, matches.c.candidate_job_ids)
        ).all():
            values = {}
            if match.job_id in renamed:
                values["job_id"] = renamed[match.job_id]
            if match.candidate_job_ids:
                try:
                    candidates = json.loads(match.candidate_job_ids)
                except ValueError:
                    candidates = None
                if isinstance(candidates, list) and any(c in renamed for c in candidates):
                    values["candidate_job_ids"] = json.dumps([renamed.get(c, c) for c in candidates])
            if values:
                conn.execute(sa.update(matches).where(matches.c.id == match.id).values(**values))

    bad = [slug for slug in names if not _slug_is_valid(slug)]
    for i in range(0, len(bad), 500):
        conn.execute(sa.delete(companies).where(
            companies.c.provider == "workday", companies.c.slug.in_(bad[i:i + 500])
        ))


def downgrade() -> None:
    # A data repair: the wrong slugs are not worth reconstructing.
    pass
