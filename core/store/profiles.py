"""Per-user candidate profiles, stored as validated JSON."""

from __future__ import annotations

from datetime import datetime, timezone
import json
import logging
from pathlib import Path
import sqlite3
from typing import Dict, List, Optional

from core.config.schema import CandidateProfile
from core.store.db import get_connection, init_db

logger = logging.getLogger(__name__)

# Voluntary self-identification. Never inferred, never extracted from a resume, and
# excluded from any export that is not explicitly asked for.
SENSITIVE_PATHS = (
    "disclosures.gender",
    "disclosures.race_ethnicity",
    "disclosures.veteran_status",
    "disclosures.disability_status",
)


class ProfileStore:
    """Reads and writes one CandidateProfile per user."""

    def __init__(self, db_path: Optional[Path | str] = None):
        self.db_path = db_path
        init_db(db_path)

    def _conn(self) -> sqlite3.Connection:
        return get_connection(self.db_path)

    def get(self, user_id: int) -> Optional[CandidateProfile]:
        with self._conn() as conn:
            row = conn.execute(
                "SELECT payload FROM profiles WHERE user_id = ?", (user_id,)
            ).fetchone()
        if not row:
            return None
        try:
            return CandidateProfile.model_validate(json.loads(row["payload"]))
        except Exception as err:
            logger.error(f"Profile for user {user_id} failed validation: {err}")
            return None

    def save(self, user_id: int, profile: CandidateProfile) -> None:
        payload = json.dumps(profile.model_dump(mode="json"), ensure_ascii=False)
        now = datetime.now(timezone.utc).isoformat()
        with self._conn() as conn:
            conn.execute(
                "INSERT INTO profiles (user_id, payload, updated_at) VALUES (?, ?, ?) "
                "ON CONFLICT(user_id) DO UPDATE SET payload = excluded.payload, "
                "updated_at = excluded.updated_at",
                (user_id, payload, now),
            )
            conn.commit()

    def set_provenance(self, user_id: int, field_path: str, source: str) -> None:
        """Record where a value came from: extracted, confirmed, typed, or learned.

        Only confirmed and typed values are trusted enough to submit without a flag; a
        value a model extracted stays marked until a person has looked at it.
        """
        confirmed = (
            datetime.now(timezone.utc).isoformat()
            if source in ("confirmed", "typed")
            else None
        )
        with self._conn() as conn:
            conn.execute(
                "INSERT INTO field_provenance (user_id, field_path, source, confirmed_at) "
                "VALUES (?, ?, ?, ?) ON CONFLICT(user_id, field_path) DO UPDATE SET "
                "source = excluded.source, confirmed_at = excluded.confirmed_at",
                (user_id, field_path, source, confirmed),
            )
            conn.commit()

    def provenance(self, user_id: int) -> Dict[str, str]:
        with self._conn() as conn:
            rows = conn.execute(
                "SELECT field_path, source FROM field_provenance WHERE user_id = ?",
                (user_id,),
            ).fetchall()
        return {r["field_path"]: r["source"] for r in rows}

    def unconfirmed(self, user_id: int) -> List[str]:
        """Fields a model proposed that nobody has approved yet."""
        return [p for p, src in self.provenance(user_id).items() if src == "extracted"]
