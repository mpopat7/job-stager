"""Tests for the extension-facing API.

The extension never sees the profile: it sends a description of the controls it found
and gets back values. These check that contract, and that the bearer token the extension
uses is the same credential the dashboard issues.
"""

from pathlib import Path
import httpx
import pytest

from web.server import app

FIXTURE_PROFILE = Path(__file__).parent / "fixtures" / "profile.test.yaml"


@pytest.fixture
def isolated(tmp_path, monkeypatch):
    """A server with its own empty database, so accounts here touch nothing real."""
    monkeypatch.setattr("core.store.db.DEFAULT_DB_PATH", tmp_path / "api.db")
    import web.auth as auth
    import web.server as server
    from core.store.profiles import ProfileStore
    from core.store.users import UserStore

    auth.users = server.users = UserStore(tmp_path / "api.db")
    auth.profiles = server.profiles = ProfileStore(tmp_path / "api.db")
    return auth


async def _register(client, handle="apitest"):
    res = await client.post(
        "/api/auth/register", json={"handle": handle, "password": "api-test-password"}
    )
    assert res.status_code == 200
    return res


def _client():
    return httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    )


async def _seed_profile(auth, client):
    """Give the account the fictional test profile rather than whatever is on disk."""
    from core.config.loader import load_profile

    user_id = auth.users.user_for_session(client.cookies.get("jobstager_session"))
    auth.profiles.save(user_id, load_profile(FIXTURE_PROFILE))
    return user_id


@pytest.mark.asyncio
async def test_resolve_requires_a_session(isolated):
    async with _client() as client:
        res = await client.post("/v1/resolve", json={"fields": []})
        assert res.status_code == 401


@pytest.mark.asyncio
async def test_a_bearer_token_authenticates_the_extension(isolated):
    async with _client() as client:
        await _register(client)
        token = client.cookies.get("jobstager_session")
        assert token

    # A fresh client with no cookie jar, the way the extension talks to the server.
    async with _client() as bare:
        res = await bare.get("/v1/me", headers={"Authorization": f"Bearer {token}"})
        assert res.status_code == 200
        assert res.json()["user_id"]

        assert (await bare.get("/v1/me")).status_code == 401
        assert (
            await bare.get("/v1/me", headers={"Authorization": "Bearer not-a-token"})
        ).status_code == 401


@pytest.mark.asyncio
async def test_resolve_answers_a_whole_form(isolated):
    async with _client() as client:
        await _register(client)
        await _seed_profile(isolated, client)

        res = await client.post("/v1/resolve", json={
            "url": "https://job-boards.greenhouse.io/acme/jobs/1",
            "fields": [
                {"ref": "a", "kind": "text", "question": "First Name"},
                {"ref": "b", "kind": "text", "question": "Email Address"},
                {"ref": "c", "kind": "radio",
                 "question": "Are you legally authorized to work in the United States?",
                 "offered": ["Yes", "No"]},
                {"ref": "d", "kind": "radio",
                 "question": "Will you now or in the future require visa sponsorship?",
                 "offered": ["Yes", "No"]},
                {"ref": "e", "kind": "select", "question": "Protected Veteran Status",
                 "offered": ["I am not a protected veteran", "Protected Veteran"]},
            ],
        })
        assert res.status_code == 200
        body = res.json()
        by_ref = {a["ref"]: a for a in body["answers"]}

        assert by_ref["a"]["value"] == "Jordan"
        assert by_ref["b"]["value"] == "jordan.rivera@example.com"
        assert by_ref["c"]["value"] == "Yes"
        assert by_ref["d"]["value"] == "No"
        assert by_ref["e"]["value"] == "I am not a protected veteran"
        assert body["filled"] == 5


@pytest.mark.asyncio
async def test_a_choice_answer_is_returned_in_the_forms_own_wording(isolated):
    """The extension must not have to guess which option a "Yes" meant."""
    async with _client() as client:
        await _register(client)
        await _seed_profile(isolated, client)

        res = await client.post("/v1/resolve", json={"fields": [
            {"ref": "a", "kind": "select",
             "question": "Are you legally authorized to work in the United States?",
             "offered": ["Yes, I am authorized to work in the US", "No, I am not"]},
        ]})
        assert res.json()["answers"][0]["value"] == "Yes, I am authorized to work in the US"


@pytest.mark.asyncio
async def test_a_lone_checkbox_comes_back_as_a_tick(isolated):
    async with _client() as client:
        await _register(client)
        await _seed_profile(isolated, client)

        res = await client.post("/v1/resolve", json={"fields": [
            {"ref": "a", "kind": "checkbox",
             "question": "I certify that my responses are accurate"},
            {"ref": "b", "kind": "checkbox", "question": "Subscribe to our newsletter"},
        ]})
        by_ref = {a["ref"]: a for a in res.json()["answers"]}
        assert by_ref["a"]["check"] is True
        assert by_ref["b"]["check"] is False


@pytest.mark.asyncio
async def test_an_unanswerable_personal_field_is_flagged_not_silently_blank(isolated):
    async with _client() as client:
        await _register(client)
        user_id = await _seed_profile(isolated, client)

        stored = isolated.profiles.get(user_id)
        stored.preferences.address.street = None
        isolated.profiles.save(user_id, stored)

        res = await client.post("/v1/resolve", json={"fields": [
            {"ref": "a", "kind": "text", "question": "Street Address"},
        ]})
        answer = res.json()["answers"][0]
        assert answer["value"] is None
        assert answer["needs_attention"] is True
        assert res.json()["flagged"] == 1


@pytest.mark.asyncio
async def test_a_guessed_screening_answer_is_marked_unconfident(isolated):
    async with _client() as client:
        await _register(client)
        await _seed_profile(isolated, client)

        res = await client.post("/v1/resolve", json={"fields": [
            {"ref": "a", "kind": "radio", "question": "Have you read the job description?",
             "offered": ["Yes", "No"]},
        ]})
        answer = res.json()["answers"][0]
        assert answer["value"] == "Yes"
        assert answer["confident"] is False


@pytest.mark.asyncio
async def test_the_cohort_is_detected_from_the_posting(isolated):
    async with _client() as client:
        await _register(client)
        user_id = await _seed_profile(isolated, client)

        stored = isolated.profiles.get(user_id)
        stored.resumes.active_cohorts = [2028, 2029]
        isolated.profiles.save(user_id, stored)

        res = await client.post("/v1/resolve", json={
            "url": "https://job-boards.greenhouse.io/acme/jobs/1",
            "page_text": "Software Engineer Intern, Summer 2029. For students graduating in 2029.",
            "fields": [{"ref": "a", "kind": "text", "question": "Graduation Year"}],
        })
        body = res.json()
        assert body["grad_year"] == 2029
        assert body["answers"][0]["value"] == "2029"


@pytest.mark.asyncio
async def test_an_explicit_cohort_overrides_detection(isolated):
    async with _client() as client:
        await _register(client)
        await _seed_profile(isolated, client)

        res = await client.post("/v1/resolve", json={
            "grad_year": 2027,
            "fields": [{"ref": "a", "kind": "text", "question": "Graduation Year"}],
        })
        assert res.json()["answers"][0]["value"] == "2027"


@pytest.mark.asyncio
async def test_an_oversized_form_is_refused(isolated):
    async with _client() as client:
        await _register(client)
        await _seed_profile(isolated, client)

        fields = [{"ref": str(i), "kind": "text", "question": "Name"} for i in range(500)]
        res = await client.post("/v1/resolve", json={"fields": fields})
        assert res.status_code == 413


@pytest.mark.asyncio
async def test_a_shared_deployment_never_seeds_an_account_from_local_files(isolated, monkeypatch):
    """On a personal install the first account adopts profile.yaml. On a shared one that
    same line would hand the first stranger who signed up the operator's own data."""
    monkeypatch.setenv("JOBSTAGER_MULTI_TENANT", "1")
    async with _client() as client:
        await _register(client)
        res = await client.post("/v1/resolve", json={"fields": [
            {"ref": "a", "kind": "text", "question": "First Name"},
            {"ref": "b", "kind": "text", "question": "GPA"},
            {"ref": "c", "kind": "radio", "question": "Race / Ethnicity",
             "offered": ["Asian", "White", "Black or African American"]},
        ]})
        assert res.status_code == 200
        by_ref = {a["ref"]: a for a in res.json()["answers"]}
        assert not by_ref["a"]["value"]
        assert not by_ref["b"]["value"]
        assert by_ref["c"]["value"] is None


def test_a_shared_deployment_refuses_to_fall_back_to_local_files(isolated, monkeypatch):
    """An account with no stored profile is an error, not an invitation to read disk."""
    from fastapi import HTTPException
    from web.auth import profile_for

    monkeypatch.setenv("JOBSTAGER_MULTI_TENANT", "1")
    with pytest.raises(HTTPException) as err:
        profile_for(999_999)
    assert err.value.status_code == 404


@pytest.mark.asyncio
async def test_a_shared_deployment_refuses_to_open_a_browser(isolated, monkeypatch):
    monkeypatch.setenv("JOBSTAGER_MULTI_TENANT", "1")
    async with _client() as client:
        await _register(client)
        res = await client.post("/api/stage", json={
            "url": "https://job-boards.greenhouse.io/acme/jobs/1"
        })
        assert res.status_code == 501
