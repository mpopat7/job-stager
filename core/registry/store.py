"""Company and job registry.

Public boards and the postings scraped from them, shared by every account and owned by
none. The tables live in the main database (`core/store/db.py`), so a deployment keeps
them without a disk of its own. Whether a given person applied to a job is private and
lives in `job_matches`, never on these rows.
"""

from __future__ import annotations

from datetime import datetime, timezone
import logging
from pathlib import Path
from typing import List, Optional, Tuple

from sqlalchemy import bindparam, func, nulls_last, or_, select
from sqlalchemy.exc import SQLAlchemyError

from core.registry.roles import OTHER, classify_role
from core.scrapers.base import ATSProvider, CompanyBoard, JobPosting, normalize_posted
from core.scrapers.resolver import resolve_url
from core.store.db import companies_table, get_engine, init_db, jobs_table, upsert

logger = logging.getLogger(__name__)

# What a re-scan may overwrite. The discovery time is kept from the first sighting.
_REFRESHED_JOB_COLUMNS = ("company", "title", "location", "apply_url", "updated_at", "role_family")


def _board(row) -> CompanyBoard:
    return CompanyBoard(
        company_name=row.name,
        slug=row.slug,
        provider=ATSProvider(row.provider),
        board_url=row.board_url,
        active=bool(row.active),
        job_count=row.job_count,
        last_scanned=row.last_scanned,
    )


def _timestamp_text(value) -> Optional[str]:
    """The `YYYY-MM-DD HH:MM:SS` UTC text SQLite's CURRENT_TIMESTAMP used to return.

    Postgres hands back an aware datetime in the session's zone and SQLite a naive UTC
    one; both render the same way so nothing downstream sees which database it ran on.
    """
    if value is None:
        return None
    if isinstance(value, datetime):
        if value.tzinfo is not None:
            value = value.astimezone(timezone.utc)
        return value.strftime("%Y-%m-%d %H:%M:%S")
    return str(value)


class CompanyRegistry:
    """Known ATS boards and the jobs discovered on them."""

    def __init__(self, db_path: Optional[Path | str] = None):
        init_db(db_path)
        self.engine = get_engine(db_path)
        self._legacy_matches: dict[str, dict] = {}

    def add_company(self, board: CompanyBoard) -> bool:
        """Add or update a company board in the registry."""
        values = {
            "name": board.company_name,
            "slug": board.slug,
            "provider": board.provider.value,
            "board_url": board.board_url,
            "active": board.active,
            "job_count": board.job_count,
            "last_scanned": board.last_scanned or datetime.now().isoformat(),
        }
        stmt = upsert(
            self.engine, companies_table, values, ("provider", "slug"),
            ("name", "board_url", "active", "job_count", "last_scanned"),
        )
        try:
            with self.engine.begin() as conn:
                conn.execute(stmt)
            return True
        except SQLAlchemyError as e:
            logger.error(f"Error adding company {board.company_name}: {e}")
            return False

    def get_company(self, name_or_slug: str) -> Optional[CompanyBoard]:
        """Find a company by slug or name."""
        c = companies_table.c
        needle = name_or_slug.lower()
        query = (
            select(companies_table)
            .where(or_(func.lower(c.slug) == needle, func.lower(c.name) == needle))
            .limit(1)
        )
        with self.engine.connect() as conn:
            row = conn.execute(query).first()
        return _board(row) if row else None

    def list_companies(
        self,
        provider: Optional[ATSProvider] = None,
        active_only: bool = True,
    ) -> List[CompanyBoard]:
        """List all company boards in the registry."""
        c = companies_table.c
        query = select(companies_table)
        if provider:
            query = query.where(c.provider == provider.value)
        if active_only:
            query = query.where(c.active.is_(True))
        query = query.order_by(c.name.asc())

        with self.engine.connect() as conn:
            return [_board(row) for row in conn.execute(query)]

    def count_companies(self) -> int:
        """Return total number of registered companies."""
        with self.engine.connect() as conn:
            return conn.execute(select(func.count()).select_from(companies_table)).scalar_one()

    def upsert_jobs(self, jobs: List[JobPosting]) -> int:
        """Insert or update discovered jobs, returns count of new jobs inserted."""
        rows = {}
        by_url: dict[str, str] = {}
        for job in jobs:
            key = f"{job.provider.value}:{job.company_slug}:{job.id}"
            # One posting reached from two sources in one batch keeps the first key.
            key = by_url.setdefault(job.url, key)
            rows[key] = {
                "id": key,
                "company": job.company,
                "company_slug": job.company_slug,
                "provider": job.provider.value,
                "title": job.title,
                "location": job.location,
                "url": job.url,
                "apply_url": job.apply_url,
                "is_internship": job.is_internship,
                "updated_at": normalize_posted(job.updated_at),
                "role_family": classify_role(job.title),
            }
        if not rows:
            return 0

        stmt = upsert(self.engine, jobs_table, None, ("id",), _REFRESHED_JOB_COLUMNS)
        with self.engine.begin() as conn:
            # The feed and a board scan give one posting different ids -- Workday's feed id
            # is a UUID, its scan id the requisition number -- but the same URL. The row
            # already stored keeps its id, so the Jobs list does not show the posting twice.
            urls = list(by_url)
            for i in range(0, len(urls), 500):
                for stored_id, url in conn.execute(
                    select(jobs_table.c.id, jobs_table.c.url)
                    .where(jobs_table.c.url.in_(urls[i:i + 500]))
                ):
                    key = by_url[url]
                    if key != stored_id and key in rows:
                        rows[stored_id] = {**rows.pop(key), "id": stored_id}
                        by_url[url] = stored_id

            keys = list(rows)
            existing: set[str] = set()
            # Chunked so a large feed stays under both databases' bound-parameter limits.
            for i in range(0, len(keys), 500):
                chunk = keys[i:i + 500]
                existing.update(
                    conn.execute(select(jobs_table.c.id).where(jobs_table.c.id.in_(chunk))).scalars()
                )
            conn.execute(stmt, list(rows.values()))
        return len(rows.keys() - existing)

    def reclassify_roles(self) -> int:
        """Bring every stored role family in line with the current classifier.

        Only rows whose family changed are written, so after a classifier edit this
        touches the affected rows and otherwise touches none. Returns rows changed.
        """
        j = jobs_table.c
        with self.engine.begin() as conn:
            changed = [
                {"row_id": row_id, "family": family}
                for row_id, title, stored in conn.execute(select(j.id, j.title, j.role_family))
                if (family := classify_role(title)) != stored
            ]
            stmt = (
                jobs_table.update()
                .where(j.id == bindparam("row_id"))
                .values(role_family=bindparam("family"))
            )
            for i in range(0, len(changed), 1000):
                conn.execute(stmt, changed[i:i + 1000])
        return len(changed)

    def count_jobs(self) -> int:
        """Return total number of discovered jobs."""
        with self.engine.connect() as conn:
            return conn.execute(select(func.count()).select_from(jobs_table)).scalar_one()

    def count_applied_jobs(self) -> int:
        """Compatibility count for callers still using instance-local reconciliation."""
        return len(self._legacy_matches)

    def job_records(self) -> list[dict]:
        """Return public job metadata for matching without application state columns."""
        with self.engine.connect() as conn:
            rows = conn.execute(select(jobs_table)).mappings().all()
        return [{**row, "discovered_at": _timestamp_text(row["discovered_at"])} for row in rows]

    def find_job(self, url: Optional[str] = None, job_id: Optional[str] = None) -> Optional[dict]:
        """One job by its exact posting URL or registry id, whichever is given."""
        j = jobs_table.c
        conditions = []
        if url:
            conditions.append(j.url == url)
        if job_id:
            conditions.append(j.id == job_id)
        if not conditions:
            return None
        query = select(j.id, j.company, j.company_slug, j.title, j.url).where(or_(*conditions))
        with self.engine.connect() as conn:
            row = conn.execute(query.limit(1)).mappings().first()
        return dict(row) if row else None

    def reconcile_with_tracker(self, applications: List[dict]) -> Tuple[int, List[dict]]:
        """Compatibility shim that no longer writes private state onto public jobs."""
        from core.tracker.matches import match_applications

        jobs = {job["id"]: job for job in self.job_records()}
        matches = [
            match for match in match_applications(applications, list(jobs.values()))
            if match["status"] == "confirmed"
        ]
        self._legacy_matches = {match["job_id"]: match for match in matches}
        records = [
            {
                "job_id": match["job_id"],
                "sheet_company": match["company"],
                "sheet_role": match["role"],
                "db_title": jobs[match["job_id"]]["title"],
                "stage": match["stage"],
                "date_applied": match["date_applied"],
                "matched_by": match["matched_by"],
            }
            for match in matches
        ]
        return len(matches), records

    def _job_filters(
        self,
        keywords: Optional[List[str]],
        provider: Optional[ATSProvider],
        hide_applied: bool,
        exclude_ids: Optional[set[str]],
    ) -> list:
        """WHERE clauses shared by the job list and its per-family counts."""
        j = jobs_table.c
        where = []
        hidden = set(exclude_ids or ())
        if hide_applied:
            hidden.update(self._legacy_matches)
        if hidden:
            where.append(j.id.not_in(sorted(hidden)))
        if provider:
            where.append(j.provider == provider.value)
        if keywords:
            clauses = []
            for kw in keywords:
                pattern = f"%{kw.lower()}%"
                clauses.extend([
                    func.lower(j.title).like(pattern),
                    func.lower(j.location).like(pattern),
                    func.lower(j.company).like(pattern),
                ])
            where.append(or_(*clauses))
        return where

    def count_jobs_by_role(
        self,
        keywords: Optional[List[str]] = None,
        provider: Optional[ATSProvider] = None,
        hide_applied: bool = False,
        exclude_ids: Optional[set[str]] = None,
    ) -> dict[str, int]:
        """Jobs per role family across the whole filtered registry, largest first."""
        j = jobs_table.c
        family = func.coalesce(j.role_family, OTHER)
        query = select(family, func.count()).where(
            *self._job_filters(keywords, provider, hide_applied, exclude_ids)
        ).group_by(family)
        with self.engine.connect() as conn:
            counts = {name: n for name, n in conn.execute(query)}
        return dict(sorted(counts.items(), key=lambda kv: (-kv[1], kv[0])))

    def get_jobs(
        self,
        keywords: Optional[List[str]] = None,
        provider: Optional[ATSProvider] = None,
        limit: int = 50,
        hide_applied: bool = False,
        exclude_ids: Optional[set[str]] = None,
        role: Optional[str] = None,
        sort: str = "recent",
        offset: int = 0,
    ) -> List[JobPosting]:
        """Query public jobs, optionally excluding caller-owned confirmed matches.

        Filtering, ordering and paging all happen in SQL, so a page is a slice of the
        whole filtered registry rather than a filter applied to one page.
        """
        j = jobs_table.c
        query = select(jobs_table).where(
            *self._job_filters(keywords, provider, hide_applied, exclude_ids)
        )
        if role and role.lower() != "all":
            query = query.where(func.coalesce(j.role_family, OTHER) == role)

        # Postgres sorts NULLs first under DESC and SQLite last; say which one is meant.
        recent = (nulls_last(j.updated_at.desc()), nulls_last(j.discovered_at.desc()))
        # A board whose name was never recorded is shown by its slug, so it sorts by it too.
        company = func.lower(func.coalesce(j.company, j.company_slug))
        if sort == "company":
            order = (company.asc(), *recent)
        elif sort == "title":
            order = (func.lower(j.title).asc(), *recent)
        elif sort == "role":
            order = (func.coalesce(j.role_family, OTHER).asc(), company.asc())
        else:
            order = recent
        # The id breaks ties, so paging never repeats or skips a row between pages.
        query = query.order_by(*order, j.id.asc())
        if offset:
            query = query.offset(offset)
        if limit:
            query = query.limit(limit)

        jobs: List[JobPosting] = []
        with self.engine.connect() as conn:
            for r in conn.execute(query).mappings():
                company_name = r["company"] or r["company_slug"].replace("-", " ").replace(".", " ").title()
                legacy = self._legacy_matches.get(r["id"])
                jobs.append(
                    JobPosting(
                        id=r["id"],
                        title=r["title"],
                        company=company_name,
                        company_slug=r["company_slug"],
                        location=r["location"] or "Unspecified",
                        url=r["url"],
                        apply_url=r["apply_url"] or r["url"],
                        provider=ATSProvider(r["provider"]),
                        is_internship=bool(r["is_internship"]),
                        status="applied" if legacy else "discovered",
                        stage=legacy["stage"] if legacy else None,
                        applied_date=legacy["date_applied"] if legacy else None,
                        notes=legacy["notes"] if legacy else None,
                        updated_at=r["updated_at"],
                        discovered_at=_timestamp_text(r["discovered_at"]),
                    )
                )
        return jobs

    def auto_register_from_url(self, url: str) -> Optional[CompanyBoard]:
        """Parse an ATS URL, register the company board if new, and return it."""
        provider, slug, _ = resolve_url(url)
        if provider == ATSProvider.UNKNOWN or not slug:
            return None

        existing = self.get_company(slug)
        if existing:
            return existing

        board_url = url
        company_name = slug.replace("-", " ").title()

        if provider == ATSProvider.GREENHOUSE:
            board_url = f"https://boards.greenhouse.io/{slug}"
        elif provider == ATSProvider.LEVER:
            board_url = f"https://jobs.lever.co/{slug}"
        elif provider == ATSProvider.ASHBY:
            board_url = f"https://jobs.ashbyhq.com/{slug}"

        board = CompanyBoard(
            company_name=company_name,
            slug=slug,
            provider=provider,
            board_url=board_url,
            active=True,
        )
        self.add_company(board)
        logger.info(f"Self-expanded registry with new board: {company_name} ({provider.value})")
        return board
