"""The application tracker, one set of rows per user.

This used to be its own SQLite file with no owner column and a global
UNIQUE(company, role, link). On a shared deployment that constraint is a collision
between people, not within one: the second person to apply to a job would have
overwritten the first person's row. Ownership is part of the key now.
"""

from __future__ import annotations

from datetime import datetime
import logging
from pathlib import Path
from typing import List, Optional

from sqlalchemy import delete, func, select

from core.scrapers.base import JobPosting
from core.store.db import applications_table, get_engine, init_db, upsert
from core.tracker.base import BaseTracker

logger = logging.getLogger(__name__)


class LocalTracker(BaseTracker):
    """Applications one user has staged or sent."""

    def __init__(self, user_id: int, db_path: Optional[Path | str] = None):
        self.user_id = user_id
        self.db_path = db_path
        init_db(db_path)

    @property
    def engine(self):
        return get_engine(self.db_path)

    def read_applications(self, limit: int = 500) -> List[dict]:
        """This user's applications, newest first."""
        with self.engine.connect() as conn:
            rows = conn.execute(
                select(applications_table)
                .where(applications_table.c.user_id == self.user_id)
                .order_by(
                    applications_table.c.date_applied.desc(), applications_table.c.id.desc()
                )
                .limit(limit)
            ).fetchall()
        return [dict(r._mapping) for r in rows]

    def stage_counts(self) -> dict:
        with self.engine.connect() as conn:
            rows = conn.execute(
                select(applications_table.c.stage, func.count().label("n"))
                .where(applications_table.c.user_id == self.user_id)
                .group_by(applications_table.c.stage)
                .order_by(func.count().desc())
            ).fetchall()
        return {r.stage: r.n for r in rows}

    def log_application(
        self,
        job: JobPosting,
        grad_year: int,
        stage: str = "Applied",
        notes: str = "",
    ) -> bool:
        values = {
            "user_id": self.user_id,
            "date_applied": datetime.now().strftime("%Y-%m-%d"),
            "company": job.company,
            "role": job.title,
            "source": job.provider.value.title(),
            "link": job.url,
            "stage": stage,
            "grad_year": grad_year,
            "notes": notes,
        }
        try:
            with self.engine.begin() as conn:
                conn.execute(upsert(
                    self.engine, applications_table, values,
                    index_elements=["user_id", "company", "role", "link"],
                    update_cols=["stage", "notes"],
                ))
            try:
                from core.tracker.matches import MatchStore

                MatchStore(self.user_id, self.db_path).mark_jobstager(job)
            except Exception as err:
                logger.warning(f"Application saved, but its discovered-job match failed: {err}")
            logger.info(f"Logged {job.company} - {job.title} for user {self.user_id} ({stage})")
            return True
        except Exception as e:
            logger.error(f"Failed to log to applications table: {e}")
            return False

    def forget_user(self) -> int:
        """Delete every row this user owns, for an account deletion request."""
        with self.engine.begin() as conn:
            result = conn.execute(
                delete(applications_table).where(applications_table.c.user_id == self.user_id)
            )
        return int(result.rowcount or 0)
