"""Session authentication for the JobStager API.

A profile holds a home address, a phone number and voluntary EEO self-identification.
Once more than one account can exist, every route that touches profile data has to know
who is asking, so the dependency below is required rather than advisory.
"""

from __future__ import annotations

import logging
import os
from typing import Optional

from datetime import timedelta

from fastapi import Cookie, Depends, Header, HTTPException, Request, Response

from core.config.loader import load_profile
from core.config.schema import CandidateProfile
from core.store import crypto
from core.store.profiles import ProfileStore
from core.store.users import UserStore

logger = logging.getLogger(__name__)

SESSION_COOKIE = "jobstager_session"

# A browser extension has no same-origin cookie to send, so it presents the same
# session token as a bearer credential. One token table either way -- signing out of
# the dashboard signs out the extension.
BEARER_PREFIX = "bearer "

# Guessing a password is cheap for an attacker and expensive for this process: every
# attempt costs 240k PBKDF2 rounds, so an unthrottled login endpoint is also the
# cheapest way to take the server down.
LOGIN_LIMIT = 8
LOGIN_WINDOW = timedelta(minutes=15)
REGISTER_LIMIT = 5
REGISTER_WINDOW = timedelta(hours=1)

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
    return os.getenv("JOBSTAGER_SECURE_COOKIES", "").strip() in ("1", "true", "yes")


def setup_required() -> bool:
    """True while no account exists, so the first run can create one."""
    return users.count_users() == 0


def _token(cookie: Optional[str], authorization: Optional[str]) -> Optional[str]:
    """The session token from whichever channel the caller has."""
    if cookie:
        return cookie
    if authorization and authorization.lower().startswith(BEARER_PREFIX):
        return authorization[len(BEARER_PREFIX):].strip() or None
    return None


async def current_user(
    jobstager_session: Optional[str] = Cookie(default=None),
    authorization: Optional[str] = Header(default=None),
) -> int:
    """Resolve the signed-in user, or refuse the request."""
    user_id = users.user_for_session(_token(jobstager_session, authorization))
    if user_id is None:
        raise HTTPException(status_code=401, detail="Not signed in")
    return user_id


async def optional_user(
    jobstager_session: Optional[str] = Cookie(default=None),
    authorization: Optional[str] = Header(default=None),
) -> Optional[int]:
    return users.user_for_session(_token(jobstager_session, authorization))


def single_tenant() -> bool:
    """False on a shared deployment, where nobody may be served from local files."""
    return os.getenv("JOBSTAGER_MULTI_TENANT", "").strip() not in ("1", "true", "yes")


def profile_for(user_id: Optional[int]) -> CandidateProfile:
    """The signed-in user's profile.

    On a personal install this falls back to `profile.yaml` so the command line and the
    tests keep working with no account. That fallback is a data leak the moment strangers
    share the process -- it would serve the operator's own address and EEO answers to
    whoever asked -- so `JOBSTAGER_MULTI_TENANT=1` turns it off and makes a missing
    profile an error instead.
    """
    if user_id is not None:
        stored = profiles.get(user_id)
        if stored is not None:
            return stored
    if not single_tenant():
        raise HTTPException(
            status_code=404,
            detail="No profile on this account yet. Fill in your profile to continue.",
        )
    return load_profile()


def client_ip(request: Request) -> str:
    """The caller's address, trusting a proxy header only when told to.

    A deployment behind a load balancer sees the balancer's address on every request, so
    `JOBSTAGER_TRUST_PROXY=1` says to read `X-Forwarded-For` instead. Trusting it by
    default would let anyone reset their own rate limit with a header.
    """
    if os.getenv("JOBSTAGER_TRUST_PROXY", "").strip() in ("1", "true", "yes"):
        forwarded = request.headers.get("x-forwarded-for", "")
        if forwarded:
            return forwarded.split(",")[0].strip()
    return request.client.host if request.client else "unknown"


def throttle(bucket: str, limit: int, window: timedelta) -> None:
    """Refuse a credential attempt once a bucket has spent its budget."""
    if users.too_many_attempts(bucket, limit, window):
        raise HTTPException(
            status_code=429,
            detail="Too many attempts. Wait a few minutes and try again.",
        )
    users.record_attempt(bucket)


def check_deployment_config() -> None:
    """Refuse to start a shared deployment that is missing what protects its users.

    Each of these is a sensible default for a personal install and a hole once strangers
    share the process, so the check is the switch that separates the two.
    """
    if single_tenant():
        return
    missing = []
    if not crypto.have_key():
        missing.append(
            f"{crypto.ENV_KEY} (EEO self-identification would be stored in the clear)"
        )
    if not _secure_cookies():
        missing.append("JOBSTAGER_SECURE_COOKIES=1 (session cookies would travel in the clear)")
    if missing:
        raise RuntimeError(
            "JOBSTAGER_MULTI_TENANT is set but these are not: " + "; ".join(missing)
        )
