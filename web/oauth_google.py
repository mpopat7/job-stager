"""Sign in with Google.

Why Google and nothing else: a password account a stranger can create needs an email
service behind it -- verification, and a reset path for when they forget. Delegating both
to Google removes the whole surface, and with it the one recurring bill on a stack that
is otherwise free.

Scopes are `openid email profile` only. These are Google's non-sensitive scopes, so the
app needs no verification review to serve more than a handful of people. The Sheets
integration is the opposite case and is deliberately not requested here -- it is asked
for separately, later, and only from someone who wants it.
"""

from __future__ import annotations

import asyncio
import base64
import hashlib
import logging
import os
import secrets
from dataclasses import dataclass
from typing import Optional

import httpx
from google.auth.transport import requests as google_requests
from google.oauth2 import id_token as google_id_token

logger = logging.getLogger(__name__)

PROVIDER = "google"
AUTH_ENDPOINT = "https://accounts.google.com/o/oauth2/v2/auth"
TOKEN_ENDPOINT = "https://oauth2.googleapis.com/token"
SCOPES = "openid email profile"

# The flow's leg between /start and /callback lives in this cookie: a state value to
# compare against the one Google echoes back, and the PKCE verifier. It must be `lax`
# rather than `strict` -- Google's redirect is a cross-site top-level GET, and a strict
# cookie would not be sent on it, breaking every sign-in.
FLOW_COOKIE = "jobstager_oauth_flow"
FLOW_TTL_SECONDS = 600


class ConfigError(RuntimeError):
    """Google sign-in was asked for on an install that has no client configured."""


@dataclass(frozen=True)
class GoogleIdentity:
    subject: str
    email: Optional[str]
    email_verified: bool
    name: Optional[str]


def client_id() -> str:
    return os.getenv("GOOGLE_OAUTH_CLIENT_ID", "").strip()


def client_secret() -> str:
    return os.getenv("GOOGLE_OAUTH_CLIENT_SECRET", "").strip()


def configured() -> bool:
    """Whether this install can offer the button at all."""
    return bool(client_id() and client_secret())


def redirect_uri(request_base_url: str) -> str:
    """Where Google sends the browser back.

    Google matches this string against the console entry exactly, so an install behind a
    proxy has to state it: the request arrives as http on some internal port and guessing
    from it produces a URI that was never registered.
    """
    explicit = os.getenv("GOOGLE_OAUTH_REDIRECT_URI", "").strip()
    if explicit:
        return explicit
    public = os.getenv("JOBSTAGER_PUBLIC_URL", "").strip()
    base = (public or request_base_url).rstrip("/")
    return f"{base}/api/auth/google/callback"


def _pkce_pair() -> tuple[str, str]:
    """A verifier and its S256 challenge.

    PKCE is not strictly required for a flow with a client secret, but it costs two lines
    and closes the case where an authorization code leaks out of a redirect -- a log, a
    Referer header, a shared machine's history -- before it is exchanged.
    """
    verifier = secrets.token_urlsafe(64)[:128]
    digest = hashlib.sha256(verifier.encode("ascii")).digest()
    challenge = base64.urlsafe_b64encode(digest).decode("ascii").rstrip("=")
    return verifier, challenge


def start(base_url: str, prompt_select_account: bool = False) -> tuple[str, str, str]:
    """Build the URL to send the browser to. Returns (url, state, verifier)."""
    if not configured():
        raise ConfigError(
            "Google sign-in is not configured: set GOOGLE_OAUTH_CLIENT_ID and "
            "GOOGLE_OAUTH_CLIENT_SECRET."
        )
    state = secrets.token_urlsafe(32)
    verifier, challenge = _pkce_pair()
    params = {
        "client_id": client_id(),
        "redirect_uri": redirect_uri(base_url),
        "response_type": "code",
        "scope": SCOPES,
        "state": state,
        "code_challenge": challenge,
        "code_challenge_method": "S256",
        # No refresh token is requested: this is sign-in, not access to anything of
        # theirs, so there is nothing to call on their behalf once they leave.
        "access_type": "online",
    }
    if prompt_select_account:
        params["prompt"] = "select_account"
    return f"{AUTH_ENDPOINT}?{httpx.QueryParams(params)}", state, verifier


def _verify_id_token(raw: str) -> GoogleIdentity:
    """Check the signature, issuer, audience and expiry before believing a word of it."""
    claims = google_id_token.verify_oauth2_token(
        raw, google_requests.Request(), client_id()
    )
    if claims.get("iss") not in ("accounts.google.com", "https://accounts.google.com"):
        raise ValueError(f"unexpected issuer {claims.get('iss')!r}")
    subject = claims.get("sub")
    if not subject:
        raise ValueError("id_token carries no subject")
    return GoogleIdentity(
        subject=str(subject),
        email=claims.get("email"),
        email_verified=bool(claims.get("email_verified")),
        name=claims.get("name"),
    )


async def exchange(code: str, verifier: str, base_url: str) -> GoogleIdentity:
    """Trade the authorization code for an identity."""
    if not configured():
        raise ConfigError("Google sign-in is not configured.")
    async with httpx.AsyncClient(timeout=15) as client:
        response = await client.post(TOKEN_ENDPOINT, data={
            "code": code,
            "client_id": client_id(),
            "client_secret": client_secret(),
            "redirect_uri": redirect_uri(base_url),
            "grant_type": "authorization_code",
            "code_verifier": verifier,
        })
    if response.status_code != 200:
        # Google's body names the cause (redirect_uri_mismatch, invalid_grant) and is
        # worth a log line; it is never worth showing a stranger.
        logger.warning(f"Google token exchange failed {response.status_code}: {response.text}")
        raise ValueError("Google rejected the sign-in")

    raw = response.json().get("id_token")
    if not raw:
        raise ValueError("Google returned no id_token")

    # verify_oauth2_token fetches Google's signing certificates over the network, so it
    # blocks; off the event loop it goes.
    return await asyncio.to_thread(_verify_id_token, raw)
