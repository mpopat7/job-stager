"""Google sign-in: who an external identity resolves to, and what the callback refuses.

The network is never touched here. `exchange` is the only part that talks to Google, so
it is replaced and everything around it -- state checking, account creation, linking,
the provider switches -- is exercised for real.
"""

from pathlib import Path

import httpx
import pytest

from web import oauth_google
from web.server import app

FIXTURE_PROFILE = Path(__file__).parent / "fixtures" / "profile.test.yaml"


@pytest.fixture
def isolated(tmp_path, monkeypatch):
    """A server with its own empty database and a configured Google client."""
    monkeypatch.setattr("core.store.db.DEFAULT_DB_PATH", tmp_path / "oauth.db")
    import web.auth as auth
    import web.server as server
    from core.store.profiles import ProfileStore
    from core.store.users import UserStore

    auth.users = server.users = UserStore(tmp_path / "oauth.db")
    auth.profiles = server.profiles = ProfileStore(tmp_path / "oauth.db")
    monkeypatch.setenv("GOOGLE_OAUTH_CLIENT_ID", "test-client.apps.googleusercontent.com")
    monkeypatch.setenv("GOOGLE_OAUTH_CLIENT_SECRET", "test-secret")
    return auth


def _client():
    return httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test",
        follow_redirects=False,
    )


def _google_returns(monkeypatch, subject, email, verified=True, name="Test Person"):
    async def fake_exchange(code, verifier, base_url):
        return oauth_google.GoogleIdentity(
            subject=subject, email=email, email_verified=verified, name=name
        )
    monkeypatch.setattr(oauth_google, "exchange", fake_exchange)


async def _sign_in_with_google(client):
    """Walk the real two-leg flow, so the state cookie is the one /start issued."""
    start = await client.get("/api/auth/google/start")
    assert start.status_code == 302
    state = httpx.URL(start.headers["location"]).params["state"]
    return await client.get(f"/api/auth/google/callback?code=abc&state={state}")


# -- the redirect out ---------------------------------------------------------

@pytest.mark.asyncio
async def test_start_sends_the_browser_to_google_with_pkce(isolated):
    async with _client() as client:
        res = await client.get("/api/auth/google/start")
    assert res.status_code == 302
    url = httpx.URL(res.headers["location"])
    assert str(url).startswith(oauth_google.AUTH_ENDPOINT)
    assert url.params["code_challenge_method"] == "S256"
    assert url.params["scope"] == "openid email profile"
    assert url.params["client_id"] == "test-client.apps.googleusercontent.com"
    # The verifier itself must never appear in the URL -- that is the whole point of it.
    assert url.params["code_challenge"] not in str(res.headers.get("set-cookie", ""))


@pytest.mark.asyncio
async def test_start_is_absent_when_no_client_is_configured(isolated, monkeypatch):
    monkeypatch.delenv("GOOGLE_OAUTH_CLIENT_ID")
    async with _client() as client:
        res = await client.get("/api/auth/google/start")
    assert res.status_code == 404


# -- the callback -------------------------------------------------------------

@pytest.mark.asyncio
async def test_a_callback_with_no_flow_cookie_is_refused(isolated):
    async with _client() as client:
        res = await client.get("/api/auth/google/callback?code=abc&state=whatever")
    assert res.status_code == 302
    assert "auth_error=expired" in res.headers["location"]


@pytest.mark.asyncio
async def test_a_mismatched_state_is_refused(isolated):
    async with _client() as client:
        await client.get("/api/auth/google/start")
        res = await client.get("/api/auth/google/callback?code=abc&state=forged")
    assert "auth_error=expired" in res.headers["location"]
    assert client.cookies.get("jobstager_session") is None


@pytest.mark.asyncio
async def test_pressing_cancel_comes_back_without_a_session(isolated):
    async with _client() as client:
        await client.get("/api/auth/google/start")
        res = await client.get("/api/auth/google/callback?error=access_denied")
    assert "auth_error=cancelled" in res.headers["location"]
    assert client.cookies.get("jobstager_session") is None


@pytest.mark.asyncio
async def test_a_first_sign_in_creates_an_account_and_a_session(isolated, monkeypatch):
    _google_returns(monkeypatch, "google-sub-1", "ada@example.com")
    async with _client() as client:
        res = await _sign_in_with_google(client)
        assert res.status_code == 302
        assert res.headers["location"] == "/"
        token = client.cookies.get("jobstager_session")
        assert token

    user_id = isolated.users.user_for_session(token)
    assert user_id is not None
    assert isolated.users.handle_for(user_id) == "ada"
    assert not isolated.users.has_password(user_id)
    # A new account is seeded, so the dashboard is not staring at a missing profile.
    assert isolated.profiles.get(user_id) is not None


@pytest.mark.asyncio
async def test_signing_in_twice_reuses_the_same_account(isolated, monkeypatch):
    _google_returns(monkeypatch, "google-sub-1", "ada@example.com")
    async with _client() as client:
        await _sign_in_with_google(client)
        first = isolated.users.user_for_session(client.cookies.get("jobstager_session"))
    async with _client() as client:
        await _sign_in_with_google(client)
        second = isolated.users.user_for_session(client.cookies.get("jobstager_session"))
    assert first == second
    assert isolated.users.count_users() == 1


@pytest.mark.asyncio
async def test_a_changed_address_still_lands_on_the_subject_s_account(isolated, monkeypatch):
    """Google addresses can be renamed. The subject is what identifies the person."""
    _google_returns(monkeypatch, "google-sub-1", "ada@example.com")
    async with _client() as client:
        await _sign_in_with_google(client)
        first = isolated.users.user_for_session(client.cookies.get("jobstager_session"))

    _google_returns(monkeypatch, "google-sub-1", "ada.lovelace@example.com")
    async with _client() as client:
        await _sign_in_with_google(client)
        second = isolated.users.user_for_session(client.cookies.get("jobstager_session"))
    assert first == second


@pytest.mark.asyncio
async def test_a_verified_address_links_to_the_existing_password_account(isolated, monkeypatch):
    async with _client() as client:
        res = await client.post("/api/auth/register", json={
            "handle": "ada", "password": "a-real-password", "email": "ada@example.com",
        })
        assert res.status_code == 200
        existing = isolated.users.user_for_session(client.cookies.get("jobstager_session"))

    _google_returns(monkeypatch, "google-sub-9", "ada@example.com", verified=True)
    async with _client() as client:
        await _sign_in_with_google(client)
        linked = isolated.users.user_for_session(client.cookies.get("jobstager_session"))

    assert linked == existing
    assert isolated.users.count_users() == 1
    # The password still works: linking adds a way in, it does not replace one.
    assert isolated.users.verify("ada", "a-real-password") == existing


@pytest.mark.asyncio
async def test_an_unverified_address_never_takes_over_an_account(isolated, monkeypatch):
    """Without the verified flag the address proves nothing, so it gets its own account."""
    async with _client() as client:
        await client.post("/api/auth/register", json={
            "handle": "ada", "password": "a-real-password", "email": "ada@example.com",
        })
        existing = isolated.users.user_for_session(client.cookies.get("jobstager_session"))

    _google_returns(monkeypatch, "attacker-sub", "ada@example.com", verified=False)
    async with _client() as client:
        await _sign_in_with_google(client)
        signed_in = isolated.users.user_for_session(client.cookies.get("jobstager_session"))

    assert signed_in != existing
    assert isolated.users.count_users() == 2
    assert isolated.users.handle_for(signed_in) == "ada2"


# -- which providers an install offers ----------------------------------------

@pytest.mark.asyncio
async def test_status_reports_both_providers(isolated):
    async with _client() as client:
        st = (await client.get("/api/auth/status")).json()
    assert st["google_auth"] is True
    assert st["password_auth"] is True


@pytest.mark.asyncio
async def test_password_routes_disappear_when_the_install_is_google_only(
    isolated, monkeypatch
):
    monkeypatch.setenv("JOBSTAGER_PASSWORD_AUTH", "0")
    async with _client() as client:
        st = (await client.get("/api/auth/status")).json()
        assert st["password_auth"] is False
        res = await client.post(
            "/api/auth/register", json={"handle": "x", "password": "password123"}
        )
        assert res.status_code == 404
        res = await client.post(
            "/api/auth/login", json={"handle": "x", "password": "password123"}
        )
        assert res.status_code == 404


@pytest.mark.asyncio
async def test_a_passwordless_account_cannot_be_signed_into_with_a_password(
    isolated, monkeypatch
):
    _google_returns(monkeypatch, "google-sub-1", "ada@example.com")
    async with _client() as client:
        await _sign_in_with_google(client)

    assert isolated.users.verify("ada", "") is None
    assert isolated.users.verify("ada", "anything-at-all") is None


def test_a_google_only_deployment_needs_a_client_configured(monkeypatch):
    """The boot check catches a deployment that has turned off every way in."""
    import web.auth as auth

    monkeypatch.setenv("JOBSTAGER_MULTI_TENANT", "1")
    monkeypatch.setenv("JOBSTAGER_PASSWORD_AUTH", "0")
    monkeypatch.setenv("JOBSTAGER_SECURE_COOKIES", "1")
    monkeypatch.setenv("JOBSTAGER_SECRET_KEY", "x" * 32)
    monkeypatch.delenv("GOOGLE_OAUTH_CLIENT_ID", raising=False)
    with pytest.raises(RuntimeError, match="GOOGLE_OAUTH_CLIENT_ID"):
        auth.check_deployment_config()


def test_the_redirect_uri_is_stated_rather_than_guessed_behind_a_proxy(monkeypatch):
    monkeypatch.setenv("JOBSTAGER_PUBLIC_URL", "https://jobstager.example.com")
    assert oauth_google.redirect_uri("http://10.0.0.4:8000/") == (
        "https://jobstager.example.com/api/auth/google/callback"
    )
    monkeypatch.setenv("GOOGLE_OAUTH_REDIRECT_URI", "https://elsewhere.test/cb")
    assert oauth_google.redirect_uri("http://10.0.0.4:8000/") == "https://elsewhere.test/cb"
