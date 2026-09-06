"""Session authentication for the JobStager API.

A profile holds a home address, a phone number and voluntary EEO self-identification.
Once more than one account can exist, every route that touches profile data has to know
who is asking, so the dependency below is required rather than advisory.
"""

from __future__ import annotations

import logging
from typing import Optional

from fastapi import Cookie, Depends, HTTPException, Response

from core.config.loader import load_profile
from core.config.schema import CandidateProfile
from core.store.profiles import ProfileStore
from core.store.users import UserStore

logger = logging.getLogger(__name__)

SESSION_COOKIE = "jobstager_session"

users = UserStore()
profiles = ProfileStore()


def set_session_cookie(response: Response, token: str) -> None:
    response.set_cookie(
        SESSION_COOKIE,
        token,
        httponly=True,
        samesite="lax",
        max_age=60 * 60 * 24 * 14,
        # The dashboard is served over http on localhost; a deployment behind TLS should
        # set JOBSTAGER_SECURE_COOKIES=1 so the cookie never travels in the clear.
        secure=_secure_cookies(),
    )


def clear_session_cookie(response: Response) -> None:
    response.delete_cookie(SESSION_COOKIE)


def _secure_cookies() -> bool:
    import os

    return os.getenv("JOBSTAGER_SECURE_COOKIES", "").strip() in ("1", "true", "yes")


def setup_required() -> bool:
    """True while no account exists, so the first run can create one."""
    return users.count_users() == 0


async def current_user(
    jobstager_session: Optional[str] = Cookie(default=None),
) -> int:
    """Resolve the signed-in user, or refuse the request."""
    user_id = users.user_for_session(jobstager_session)
    if user_id is None:
        raise HTTPException(status_code=401, detail="Not signed in")
    return user_id


async def optional_user(
    jobstager_session: Optional[str] = Cookie(default=None),
) -> Optional[int]:
    return users.user_for_session(jobstager_session)


def profile_for(user_id: Optional[int]) -> CandidateProfile:
    """The signed-in user's profile, falling back to profile.yaml for CLI use.

    The file fallback exists so the command line and the tests keep working on a machine
    with no accounts; it is never reached once a session is present.
    """
    if user_id is not None:
        stored = profiles.get(user_id)
        if stored is not None:
            return stored
    return load_profile()
