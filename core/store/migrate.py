"""Import a file-based profile.yaml into the multi-user store."""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Optional

from core.config.loader import load_profile
from core.config.schema import Address, Availability, Compensation, Preferences
from core.store.profiles import ProfileStore
from core.store.users import UserStore

logger = logging.getLogger(__name__)


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
