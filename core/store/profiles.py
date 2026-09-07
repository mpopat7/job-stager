"""Per-user candidate profiles, stored as validated JSON."""

from __future__ import annotations

from datetime import datetime, timezone
import json
import logging
from pathlib import Path
from typing import Dict, List, Optional

from sqlalchemy import select

from core.config.schema import CandidateProfile
from core.store.crypto import protect, unprotect
from core.store.db import (
    field_provenance_table, get_engine, init_db, profiles_table, upsert,
)

logger = logging.getLogger(__name__)

# Voluntary self-identification. Never inferred, never extracted from a resume, excluded
# from any export that is not explicitly asked for, and encrypted at rest.
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

    @property
    def engine(self):
        return get_engine(self.db_path)

    def get(self, user_id: int) -> Optional[CandidateProfile]:
        with self.engine.connect() as conn:
            row = conn.execute(
                select(profiles_table.c.payload).where(profiles_table.c.user_id == user_id)
            ).first()
        if row is None:
            return None
        try:
            payload = unprotect(json.loads(row.payload), SENSITIVE_PATHS)
            return CandidateProfile.model_validate(payload)
        except Exception as err:
            logger.error(f"Profile for user {user_id} failed validation: {err}")
            return None

    def save(self, user_id: int, profile: CandidateProfile) -> None:
        payload = protect(profile.model_dump(mode="json"), SENSITIVE_PATHS)
        values = {
            "user_id": user_id,
            "payload": json.dumps(payload, ensure_ascii=False),
            "updated_at": datetime.now(timezone.utc),
        }
        with self.engine.begin() as conn:
            conn.execute(upsert(
                self.engine, profiles_table, values,
                index_elements=["user_id"], update_cols=["payload", "updated_at"],
            ))

    def delete(self, user_id: int) -> None:
        from sqlalchemy import delete as sql_delete

        with self.engine.begin() as conn:
            conn.execute(sql_delete(profiles_table).where(profiles_table.c.user_id == user_id))

    def set_provenance(self, user_id: int, field_path: str, source: str) -> None:
        """Record where a value came from: extracted, confirmed, typed, or learned.

        Only confirmed and typed values are trusted enough to submit without a flag; a
        value a model extracted stays marked until a person has looked at it.
        """
        confirmed = (
            datetime.now(timezone.utc) if source in ("confirmed", "typed") else None
        )
        values = {
            "user_id": user_id,
            "field_path": field_path,
            "source": source,
            "confirmed_at": confirmed,
        }
        with self.engine.begin() as conn:
            conn.execute(upsert(
                self.engine, field_provenance_table, values,
                index_elements=["user_id", "field_path"],
                update_cols=["source", "confirmed_at"],
            ))

    def provenance(self, user_id: int) -> Dict[str, str]:
        with self.engine.connect() as conn:
            rows = conn.execute(
                select(field_provenance_table.c.field_path, field_provenance_table.c.source)
                .where(field_provenance_table.c.user_id == user_id)
            ).fetchall()
        return {r.field_path: r.source for r in rows}

    def unconfirmed(self, user_id: int) -> List[str]:
        """Fields a model proposed that nobody has approved yet."""
        return [p for p, src in self.provenance(user_id).items() if src == "extracted"]
