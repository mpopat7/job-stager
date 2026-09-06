"""Account, session, and profile-API behaviour."""

import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setattr("core.store.db.DEFAULT_DB_PATH", tmp_path / "test.db")
    import importlib
    from core.store import users as users_mod, profiles as profiles_mod
    importlib.reload(users_mod)
    importlib.reload(profiles_mod)
    import web.auth as auth
    auth.users = users_mod.UserStore(tmp_path / "test.db")
    auth.profiles = profiles_mod.ProfileStore(tmp_path / "test.db")
    import web.server as server
    server.users, server.profiles = auth.users, auth.profiles
    return TestClient(server.app)


def test_profile_requires_a_session(client):
    assert client.get("/api/profile").status_code == 401
    assert client.put("/api/preferences", json={}).status_code == 401


def test_first_run_reports_setup_required(client):
    assert client.get("/api/auth/status").json()["setup_required"] is True


def test_register_login_logout_cycle(client):
    r = client.post("/api/auth/register", json={"handle": "milen", "password": "a-good-password"})
    assert r.status_code == 200
    assert client.get("/api/profile").status_code == 200

    client.post("/api/auth/logout")
    assert client.get("/api/profile").status_code == 401

    assert client.post("/api/auth/login", json={"handle": "milen", "password": "wrong"}).status_code == 401
    assert client.post("/api/auth/login", json={"handle": "milen", "password": "a-good-password"}).status_code == 200
    assert client.get("/api/profile").status_code == 200


def test_preferences_round_trip(client):
    client.post("/api/auth/register", json={"handle": "milen", "password": "a-good-password"})
    prefs = client.get("/api/preferences").json()
    prefs["work_model_ranked"] = ["Remote", "Hybrid", "Onsite"]
    prefs["address"]["postal_code"] = "77002"
    assert client.put("/api/preferences", json=prefs).status_code == 200
    back = client.get("/api/preferences").json()
    assert back["work_model_ranked"][0] == "Remote"
    assert back["address"]["postal_code"] == "77002"


def test_invalid_preferences_are_refused(client):
    client.post("/api/auth/register", json={"handle": "milen", "password": "a-good-password"})
    r = client.put("/api/preferences", json={"work_model_ranked": "not-a-list"})
    assert r.status_code == 400


def test_second_account_does_not_inherit_the_first_profile(client):
    client.post("/api/auth/register", json={"handle": "milen", "password": "a-good-password"})
    first = client.get("/api/profile").json()["profile"]["candidate"]["email"]
    client.post("/api/auth/logout")

    client.post("/api/auth/register", json={"handle": "stranger", "password": "another-password"})
    second = client.get("/api/profile").json()["profile"]["candidate"]
    assert second["email"] != first
    assert second["phone"] == ""
    assert second["first_name"] == ""


def test_a_user_cannot_read_another_users_profile(client):
    client.post("/api/auth/register", json={"handle": "milen", "password": "a-good-password"})
    client.put("/api/preferences", json={**client.get("/api/preferences").json(),
                                         "pronouns": "He/Him"})
    client.post("/api/auth/logout")
    client.post("/api/auth/register", json={"handle": "stranger", "password": "another-password"})
    assert client.get("/api/preferences").json()["pronouns"] is None
