"""One-off data moves into the multi-user store: a profile.yaml, or the old registry file.

    uv run python3 -m core.store.migrate import-registry [path/to/companies.db]
"""

from __future__ import annotations

from datetime import datetime, timezone
import logging
from pathlib import Path
import sqlite3
from typing import Optional

from core.config.loader import load_profile
from core.config.schema import Address, Availability, Compensation, Preferences
from core.store.db import companies_table, get_engine, init_db, jobs_table, upsert
from core.store.profiles import ProfileStore
from core.store.users import UserStore

logger = logging.getLogger(__name__)

LEGACY_REGISTRY_PATH = Path(__file__).parent.parent.parent / "companies.db"


def _utc(value) -> Optional[datetime]:
    """Parse SQLite's CURRENT_TIMESTAMP text, which is UTC with no zone marker."""
    if not value:
        return None
    parsed = datetime.fromisoformat(str(value))
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def import_legacy_registry(
    sqlite_path: Path | str = LEGACY_REGISTRY_PATH,
    db_path: Optional[Path | str] = None,
) -> tuple[int, int]:
    """Copy boards and jobs from the standalone companies.db the registry used to keep.

    Returns (boards, jobs) read from the file. Safe to run again: rows already present are
    updated, not duplicated. The old jobs table's status/stage/applied_date/notes columns
    are left behind on purpose -- application state is per-user now, and reconciling
    against the tracker rebuilds it in `job_matches`.
    """
    source = Path(sqlite_path)
    if not source.exists():
        raise FileNotFoundError(f"No legacy registry at {source}")

    src = sqlite3.connect(source)
    src.row_factory = sqlite3.Row
    try:
        boards = [
            {
                "name": r["name"],
                "slug": r["slug"],
                "provider": r["provider"],
                "board_url": r["board_url"],
                "career_url": r["career_url"],
                "verified": bool(r["verified"]) if r["verified"] is not None else True,
                "active": bool(r["active"]) if r["active"] is not None else True,
                "job_count": r["job_count"] or 0,
                "last_scanned": str(r["last_scanned"]) if r["last_scanned"] else None,
                "created_at": _utc(r["created_at"]),
            }
            for r in src.execute("SELECT * FROM companies")
        ]
        jobs = [
            {
                "id": r["id"],
                "company": r["company"],
                "company_slug": r["company_slug"],
                "provider": r["provider"],
                "title": r["title"],
                "location": r["location"],
                "url": r["url"],
                "apply_url": r["apply_url"],
                "is_internship": bool(r["is_internship"]),
                "updated_at": r["updated_at"],
                "discovered_at": _utc(r["discovered_at"]),
            }
            for r in src.execute("SELECT * FROM jobs")
        ]
    finally:
        src.close()

    init_db(db_path)
    engine = get_engine(db_path)
    with engine.begin() as conn:
        if boards:
            stmt = upsert(engine, companies_table, None, ("provider", "slug"),
                          [k for k in boards[0] if k not in ("provider", "slug")])
            conn.execute(stmt, boards)
        if jobs:
            stmt = upsert(engine, jobs_table, None, ("id",), [k for k in jobs[0] if k != "id"])
            conn.execute(stmt, jobs)
    logger.info(f"Imported {len(boards)} boards and {len(jobs)} jobs from {source}")
    return len(boards), len(jobs)


def seed_preferences_from_legacy(profile) -> Preferences:
    """Build a Preferences block from what the profile already knows.

    The values these replace were literals inside the fill logic, and some of them
    disagreed with each other -- a Bloomington street address paired with a Houston city
    and Houston postal code. Anything that cannot be derived is left empty on purpose so
    it flags for review rather than shipping a wrong guess to an employer.
    """
    city, state = None, None
    location = (profile.candidate.location or "").split(",")
    if location and location[0].strip():
        city = location[0].strip()
    if len(location) > 1 and location[1].strip():
        state = location[1].strip()

    return Preferences(
        availability=Availability(
            start_date="2026-05-18",
            end_date="2026-08-14",
            term_label="Summer 2026 (4-month)",
            full_time=True,
        ),
        address=Address(street=None, city=city, state=state, postal_code=None),
        work_model_ranked=["Hybrid", "Onsite", "Remote"],
        locations_ranked=[city] if city else [],
        compensation=Compensation(hourly_rate="50", salary_expectation=None, note="Negotiable"),
        pronouns="He/Him",
        current_title=None,
        referral_source="LinkedIn",
    )


def migrate_yaml_to_store(
    handle: str,
    password: str,
    yaml_path: Optional[Path | str] = None,
    db_path: Optional[Path | str] = None,
) -> int:
    """Create an account from profile.yaml and return its user id."""
    profile = load_profile(yaml_path)
    if profile.preferences == Preferences():
        profile.preferences = seed_preferences_from_legacy(profile)

    users, profiles = UserStore(db_path), ProfileStore(db_path)
    user_id = users.create_user(handle, password, email=profile.candidate.email)
    profiles.save(user_id, profile)

    # Everything imported came from a file the person wrote themselves.
    for path in ("candidate.email", "candidate.phone", "education.school", "education.gpa"):
        profiles.set_provenance(user_id, path, "typed")
    for path in ("preferences.address.street", "preferences.address.postal_code"):
        profiles.set_provenance(user_id, path, "missing")

    logger.info(f"Migrated profile.yaml into user {user_id} ({handle})")
    return user_id


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="One-off data moves into the JobStager database.")
    commands = parser.add_subparsers(dest="command", required=True)
    registry_cmd = commands.add_parser(
        "import-registry", help="Copy the old standalone companies.db into the main database."
    )
    registry_cmd.add_argument("path", nargs="?", default=str(LEGACY_REGISTRY_PATH))
    args = parser.parse_args()

    boards, jobs = import_legacy_registry(args.path)
    print(f"Imported {boards} boards and {jobs} jobs from {args.path}")
