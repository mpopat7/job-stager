"""Company and job registry backed by SQLite."""

from __future__ import annotations

from datetime import datetime
import logging
from pathlib import Path
import re
import sqlite3
from typing import List, Optional, Tuple

from core.scrapers.base import ATSProvider, CompanyBoard, JobPosting
from core.scrapers.resolver import resolve_url

logger = logging.getLogger(__name__)

DEFAULT_DB_PATH = Path(__file__).parent.parent.parent / "companies.db"


class CompanyRegistry:
    """SQLite-backed registry for known company ATS boards and staged jobs."""

    def __init__(self, db_path: Optional[Path | str] = None):
        self.db_path = Path(db_path or DEFAULT_DB_PATH).resolve()
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._init_db()

    def _get_connection(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        return conn

    def _init_db(self) -> None:
        """Create database tables if they do not exist."""
        with self._get_connection() as conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS companies (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    name TEXT NOT NULL,
                    slug TEXT NOT NULL,
                    provider TEXT NOT NULL,
                    board_url TEXT NOT NULL,
                    career_url TEXT,
                    verified INTEGER DEFAULT 1,
                    active INTEGER DEFAULT 1,
                    job_count INTEGER DEFAULT 0,
                    last_scanned TIMESTAMP,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    UNIQUE(provider, slug)
                )
            """)
            conn.execute("""
                CREATE TABLE IF NOT EXISTS jobs (
                    id TEXT PRIMARY KEY,
                    company TEXT,
                    company_slug TEXT NOT NULL,
                    provider TEXT NOT NULL,
                    title TEXT NOT NULL,
                    location TEXT,
                    url TEXT NOT NULL,
                    apply_url TEXT,
                    is_internship INTEGER DEFAULT 0,
                    status TEXT DEFAULT 'discovered',
                    updated_at TEXT,
                    discovered_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            """)
            conn.execute("CREATE INDEX IF NOT EXISTS idx_companies_provider_slug ON companies(provider, slug)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_jobs_status ON jobs(status)")
            for col in ["company", "stage", "applied_date", "notes"]:
                try:
                    conn.execute(f"ALTER TABLE jobs ADD COLUMN {col} TEXT")
                except sqlite3.OperationalError:
                    pass
            conn.commit()

    def add_company(self, board: CompanyBoard) -> bool:
        """Add or update a company board in the registry."""
        with self._get_connection() as conn:
            try:
                conn.execute(
                    """
                    INSERT INTO companies (name, slug, provider, board_url, active, job_count, last_scanned)
                    VALUES (?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(provider, slug) DO UPDATE SET
                        name=excluded.name,
                        board_url=excluded.board_url,
                        active=excluded.active,
                        job_count=excluded.job_count,
                        last_scanned=coalesce(excluded.last_scanned, companies.last_scanned)
                    """,
                    (
                        board.company_name,
                        board.slug,
                        board.provider.value,
                        board.board_url,
                        1 if board.active else 0,
                        board.job_count,
                        board.last_scanned or datetime.now().isoformat(),
                    ),
                )
                conn.commit()
                return True
            except Exception as e:
                logger.error(f"Error adding company {board.company_name}: {e}")
                return False

    def get_company(self, name_or_slug: str) -> Optional[CompanyBoard]:
        """Find a company by slug or name."""
        with self._get_connection() as conn:
            cursor = conn.execute(
                """
                SELECT name, slug, provider, board_url, active, job_count, last_scanned
                FROM companies
                WHERE lower(slug) = lower(?) OR lower(name) = lower(?)
                LIMIT 1
                """,
                (name_or_slug, name_or_slug),
            )
            row = cursor.fetchone()
            if row:
                return CompanyBoard(
                    company_name=row["name"],
                    slug=row["slug"],
                    provider=ATSProvider(row["provider"]),
                    board_url=row["board_url"],
                    active=bool(row["active"]),
                    job_count=row["job_count"],
                    last_scanned=row["last_scanned"],
                )
        return None

    def list_companies(
        self,
        provider: Optional[ATSProvider] = None,
        active_only: bool = True,
    ) -> List[CompanyBoard]:
        """List all company boards in the registry."""
        query = "SELECT name, slug, provider, board_url, active, job_count, last_scanned FROM companies WHERE 1=1"
        params: list = []

        if provider:
            query += " AND provider = ?"
            params.append(provider.value)
        if active_only:
            query += " AND active = 1"

        query += " ORDER BY name ASC"

        boards: List[CompanyBoard] = []
        with self._get_connection() as conn:
            cursor = conn.execute(query, params)
            for row in cursor.fetchall():
                boards.append(
                    CompanyBoard(
                        company_name=row["name"],
                        slug=row["slug"],
                        provider=ATSProvider(row["provider"]),
                        board_url=row["board_url"],
                        active=bool(row["active"]),
                        job_count=row["job_count"],
                        last_scanned=row["last_scanned"],
                    )
                )
        return boards

    def count_companies(self) -> int:
        """Return total number of registered companies."""
        with self._get_connection() as conn:
            cursor = conn.execute("SELECT COUNT(*) FROM companies")
            return cursor.fetchone()[0]

    def upsert_jobs(self, jobs: List[JobPosting]) -> int:
        """Insert or update discovered jobs, returns count of new jobs inserted."""
        inserted = 0
        with self._get_connection() as conn:
            for job in jobs:
                job_key = f"{job.provider.value}:{job.company_slug}:{job.id}"
                cursor = conn.execute(
                    """
                    INSERT INTO jobs (id, company, company_slug, provider, title, location, url, apply_url, is_internship, updated_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(id) DO UPDATE SET
                        company=excluded.company,
                        title=excluded.title,
                        location=excluded.location,
                        apply_url=excluded.apply_url,
                        updated_at=excluded.updated_at
                    """,
                    (
                        job_key,
                        job.company,
                        job.company_slug,
                        job.provider.value,
                        job.title,
                        job.location,
                        job.url,
                        job.apply_url,
                        1 if job.is_internship else 0,
                        job.updated_at,
                    ),
                )
                if cursor.rowcount > 0:
                    inserted += 1
            conn.commit()
        return inserted

    def count_jobs(self) -> int:
        """Return total number of discovered jobs."""
        with self._get_connection() as conn:
            cursor = conn.execute("SELECT COUNT(*) FROM jobs")
            return cursor.fetchone()[0]

    def count_applied_jobs(self) -> int:
        """Return total number of jobs cross-referenced as applied."""
        with self._get_connection() as conn:
            cursor = conn.execute("SELECT COUNT(*) FROM jobs WHERE status = 'applied'")
            return cursor.fetchone()[0]

    def reconcile_with_tracker(self, applications: List[dict]) -> Tuple[int, List[dict]]:
        """Cross-reference Google Sheet applications with discovered jobs in SQLite.

        Marks matching jobs with status='applied', saving their stage and applied date.
        """
        def clean_str(s: str) -> str:
            if not s:
                return ""
            s = s.lower()
            s = re.sub(r"\b(inc|llc|corp|corporation|technologies|tech|co|company|the)\b", "", s)
            return re.sub(r"[^a-z0-9]", "", s)

        def clean_url(u: str) -> str:
            if not u:
                return ""
            return u.split("?")[0].rstrip("/").lower()

        matched_records = []
        updated_count = 0

        with self._get_connection() as conn:
            db_jobs = conn.execute("SELECT id, company, company_slug, provider, title, url FROM jobs").fetchall()

            for app in applications:
                comp = app.get("Company", "").strip()
                role = app.get("Role", "").strip()
                link = app.get("Link", "").strip()
                stage = app.get("Stage", "Applied").strip()
                date_applied = app.get("Date Applied", "").strip()
                notes = app.get("Comp/Notes", "").strip()

                clean_comp = clean_str(comp)
                clean_link = clean_url(link)
                role_lower = role.lower()

                for job in db_jobs:
                    j_id = job["id"]
                    j_url = clean_url(job["url"])
                    j_comp = clean_str(job["company"]) or clean_str(job["company_slug"])
                    j_title = job["title"].lower()

                    is_match = False

                    # Rule 1: Exact or substring URL match
                    if clean_link and (clean_link in j_url or j_url in clean_link):
                        is_match = True
                    # Rule 2: Same company AND role similarity
                    elif clean_comp and (clean_comp == j_comp or clean_comp in j_comp):
                        if "intern" in role_lower or "co-op" in role_lower or "fellow" in role_lower:
                            is_swe = any(k in role_lower for k in ["software", "swe", "developer"]) and any(k in j_title for k in ["software", "swe", "developer"])
                            is_data = any(k in role_lower for k in ["data", "analytics", "data analyst"]) and any(k in j_title for k in ["data", "analytics", "data analyst"])
                            is_ml = any(k in role_lower for k in ["machine learning", "ai", "ml"]) and any(k in j_title for k in ["machine learning", "ai", "ml"])
                            if is_swe or is_data or is_ml:
                                is_match = True

                    if is_match:
                        cursor = conn.execute(
                            """
                            UPDATE jobs
                            SET status = 'applied', stage = ?, applied_date = ?, notes = ?
                            WHERE id = ?
                            """,
                            (stage, date_applied, notes, j_id),
                        )
                        if cursor.rowcount > 0:
                            updated_count += 1
                            matched_records.append({
                                "job_id": j_id,
                                "sheet_company": comp,
                                "sheet_role": role,
                                "db_title": job["title"],
                                "stage": stage,
                                "date_applied": date_applied,
                            })
            conn.commit()

        return updated_count, matched_records

    def get_jobs(
        self,
        keywords: Optional[List[str]] = None,
        provider: Optional[ATSProvider] = None,
        limit: int = 50,
        hide_applied: bool = False,
    ) -> List[JobPosting]:
        """Query discovered jobs with optional keyword, provider, and applied status filtering."""
        query = "SELECT id, company, company_slug, provider, title, location, url, apply_url, is_internship, status, stage, applied_date, notes, updated_at, discovered_at FROM jobs WHERE 1=1"
        params: list = []

        if hide_applied:
            query += " AND (status != 'applied' OR status IS NULL)"

        if provider:
            query += " AND provider = ?"
            params.append(provider.value)

        if keywords:
            kw_clauses = []
            for kw in keywords:
                kw_clauses.append("(lower(title) LIKE ? OR lower(location) LIKE ? OR lower(company) LIKE ?)")
                params.extend([f"%{kw.lower()}%", f"%{kw.lower()}%", f"%{kw.lower()}%"])
            query += f" AND ({' OR '.join(kw_clauses)})"

        query += " ORDER BY updated_at DESC, discovered_at DESC LIMIT ?"
        params.append(limit)

        jobs: List[JobPosting] = []
        with self._get_connection() as conn:
            cursor = conn.execute(query, params)
            for r in cursor.fetchall():
                company_name = r["company"] or r["company_slug"].replace("-", " ").replace(".", " ").title()
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
                        status=r["status"] or "discovered",
                        stage=r["stage"],
                        applied_date=r["applied_date"],
                        notes=r["notes"],
                        updated_at=r["updated_at"],
                        discovered_at=str(r["discovered_at"]) if r["discovered_at"] else None,
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
