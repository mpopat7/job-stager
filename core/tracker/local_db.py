"""Local SQLite tracker for logging applications."""

from __future__ import annotations

from datetime import datetime
import logging
from pathlib import Path
import sqlite3
from typing import List, Optional

from core.scrapers.base import JobPosting
from core.tracker.base import BaseTracker

logger = logging.getLogger(__name__)

DEFAULT_TRACKER_DB = Path(__file__).parent.parent.parent / "applications.db"


class LocalTracker(BaseTracker):
    """Tracks applied and staged applications locally in SQLite."""

    def __init__(self, db_path: Optional[Path | str] = None):
        self.db_path = Path(db_path or DEFAULT_TRACKER_DB).resolve()
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._init_db()

    def _get_connection(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        return conn

    def _init_db(self) -> None:
        with self._get_connection() as conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS applications (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    date_applied TEXT NOT NULL,
                    company TEXT NOT NULL,
                    role TEXT NOT NULL,
                    source TEXT NOT NULL,
                    link TEXT NOT NULL,
                    stage TEXT NOT NULL DEFAULT 'Applied',
                    grad_year INTEGER NOT NULL,
                    notes TEXT,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    UNIQUE(company, role, link)
                )
            """)
            conn.commit()

    def read_applications(self, limit: int = 500) -> List[dict]:
        """Every application logged locally, newest first."""
        with self._get_connection() as conn:
            rows = conn.execute(
                "SELECT * FROM applications ORDER BY date_applied DESC, id DESC LIMIT ?",
                (limit,),
            ).fetchall()
        return [dict(r) for r in rows]

    def stage_counts(self) -> dict:
        with self._get_connection() as conn:
            rows = conn.execute(
                "SELECT stage, COUNT(*) AS n FROM applications GROUP BY stage ORDER BY n DESC"
            ).fetchall()
        return {r["stage"]: r["n"] for r in rows}

    def log_application(
        self,
        job: JobPosting,
        grad_year: int,
        stage: str = "Applied",
        notes: str = "",
    ) -> bool:
        today = datetime.now().strftime("%Y-%m-%d")
        with self._get_connection() as conn:
            try:
                conn.execute(
                    """
                    INSERT INTO applications (date_applied, company, role, source, link, stage, grad_year, notes)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(company, role, link) DO UPDATE SET
                        stage=excluded.stage,
                        notes=excluded.notes
                    """,
                    (
                        today,
                        job.company,
                        job.title,
                        job.provider.value.title(),
                        job.url,
                        stage,
                        grad_year,
                        notes,
                    ),
                )
                conn.commit()
                logger.info(f"Logged {job.company} - {job.title} to local tracker ({stage})")
                return True
            except Exception as e:
                logger.error(f"Failed to log to local applications db: {e}")
                return False
