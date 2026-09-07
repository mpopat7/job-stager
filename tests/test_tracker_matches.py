"""User-scoped matching between discovered jobs and optional Sheet history."""

from fastapi.testclient import TestClient
import pytest

from core.config.schema import CandidateProfile
from core.registry.store import CompanyRegistry
from core.scrapers.base import ATSProvider, JobPosting
from core.store.profiles import ProfileStore
from core.store.users import UserStore
from core.tracker.local_db import LocalTracker
from core.tracker.matches import MatchStore


def _job(
    job_id: str,
    title: str,
    *,
    company: str = "Acme",
    company_slug: str = "acme",
    url: str | None = None,
) -> JobPosting:
    url = url or f"https://jobs.ashbyhq.com/{company_slug}/{job_id}"
    return JobPosting(
        id=job_id,
        title=title,
        company=company,
        company_slug=company_slug,
        location="Remote",
        url=url,
        apply_url=url,
        provider=ATSProvider.ASHBY,
        is_internship=True,
    )


def _sheet_row(company: str, role: str, link: str = "") -> dict:
    return {
        "Company": company,
        "Role": role,
        "Link": link,
        "Stage": "Applied",
        "Date Applied": "2026-09-01",
        "Comp/Notes": "",
        "row": 2,
    }


@pytest.fixture
def tracker_state(tmp_path, monkeypatch):
    import core.registry.store as registry_mod
    import core.store.db as db_mod
    import web.auth as auth
    import web.server as server

    app_db = tmp_path / "jobstager.db"
    registry_db = tmp_path / "companies.db"
    db_mod.reset_engines()
    monkeypatch.setattr(db_mod, "DEFAULT_DB_PATH", app_db)
    monkeypatch.setattr(registry_mod, "DEFAULT_DB_PATH", registry_db)

    users = UserStore(app_db)
    profiles = ProfileStore(app_db)
    first = users.create_user("first", "first-password")
    second = users.create_user("second", "second-password")
    blank = CandidateProfile.model_validate({
        "candidate": {
            "first_name": "",
            "last_name": "",
            "email": "",
            "phone": "",
            "location": "",
        }
    })
    profiles.save(first, blank)
    profiles.save(second, blank)

    registry = CompanyRegistry(registry_db)
    auth.users = server.users = users
    auth.profiles = server.profiles = profiles
    monkeypatch.setattr(server, "CompanyRegistry", lambda: registry)

    yield app_db, registry, users, first, second
    db_mod.reset_engines()


def test_normalized_url_confirms_query_and_trailing_slash_variants(tracker_state):
    app_db, registry, _, user_id, _ = tracker_state
    registry.upsert_jobs([_job(
        "role-123",
        "Software Engineer Intern",
        url="https://jobs.ashbyhq.com/acme/role-123/",
    )])

    result = MatchStore(user_id, app_db).reconcile_sheet(registry, [
        _sheet_row(
            "Acme",
            "Software Engineer Intern",
            "https://jobs.ashbyhq.com/acme/role-123?utm_source=handshake#apply",
        )
    ])

    assert result["confirmed_count"] == 1
    match = MatchStore(user_id, app_db).list_matches(origin="sheet")[0]
    assert match["status"] == "confirmed"
    assert match["matched_by"] == "normalized_url"


def test_one_sheet_row_cannot_claim_multiple_similar_internships(tracker_state):
    app_db, registry, _, user_id, _ = tracker_state
    jobs = [
        _job(
            "stripe-nyc",
            "Software Engineer Intern",
            company="Stripe",
            company_slug="stripe",
        ),
        _job(
            "stripe-seattle",
            "Software Engineer Intern",
            company="Stripe",
            company_slug="stripe",
        ),
        _job(
            "stripe-platform",
            "Software Engineering Intern, Platform",
            company="Stripe",
            company_slug="stripe",
        ),
    ]
    registry.upsert_jobs(jobs)

    MatchStore(user_id, app_db).reconcile_sheet(registry, [
        _sheet_row("Stripe", "Software Engineer Intern", jobs[0].url)
    ])

    matches = MatchStore(user_id, app_db).list_matches(origin="sheet")
    assert len(matches) == 1
    assert matches[0]["job_id"].endswith(":stripe-nyc")


def test_unique_company_and_normalized_title_is_confirmed(tracker_state):
    app_db, registry, _, user_id, _ = tracker_state
    registry.upsert_jobs([_job(
        "summer-2027",
        "Software Engineer Intern (Summer 2027)",
        company="Acme, Inc.",
    )])

    MatchStore(user_id, app_db).reconcile_sheet(registry, [
        _sheet_row("ACME INC", "software engineer intern - summer 2027")
    ])

    match = MatchStore(user_id, app_db).list_matches(origin="sheet")[0]
    assert match["status"] == "confirmed"
    assert match["matched_by"] == "company_title"


def test_possible_match_stays_in_jobs_without_approval(tracker_state):
    app_db, registry, users, user_id, _ = tracker_state
    registry.upsert_jobs([_job("backend", "Software Developer Intern")])
    stored_job = registry.get_jobs()[0]

    result = MatchStore(user_id, app_db).reconcile_sheet(
        registry, [_sheet_row("Acme", "Software Engineering Intern")]
    )
    matches = MatchStore(user_id, app_db).list_matches(origin="sheet")

    assert result["possible_count"] == 1
    assert matches[0]["status"] == "possible"
    assert matches[0]["matched_by"] == "company_title_tokens"
    assert MatchStore(user_id, app_db).confirmed_job_ids() == set()

    headers = {"Authorization": f"Bearer {users.start_session(user_id)}"}
    from web.server import app

    with TestClient(app) as client:
        jobs = client.get("/api/jobs", headers=headers).json()["jobs"]

    assert {job["id"] for job in jobs} == {stored_job.id}


def test_confirmed_match_is_hidden_only_for_its_owner(tracker_state):
    app_db, registry, users, first, second = tracker_state
    registry.upsert_jobs([_job("shared-role", "Software Engineer Intern")])
    job_id = registry.get_jobs()[0].id
    MatchStore(first, app_db).reconcile_sheet(registry, [
        _sheet_row(
            "Acme",
            "Software Engineer Intern",
            "https://jobs.ashbyhq.com/acme/shared-role/?ref=school",
        )
    ])

    first_headers = {"Authorization": f"Bearer {users.start_session(first)}"}
    second_headers = {"Authorization": f"Bearer {users.start_session(second)}"}
    from web.server import app

    with TestClient(app) as client:
        first_jobs = client.get("/api/jobs", headers=first_headers).json()["jobs"]
        second_jobs = client.get("/api/jobs", headers=second_headers).json()["jobs"]
        first_matches = client.get("/api/matches", headers=first_headers).json()
        second_matches = client.get("/api/matches", headers=second_headers).json()
        first_stats = client.get("/api/stats", headers=first_headers).json()
        second_stats = client.get("/api/stats", headers=second_headers).json()

    assert job_id not in {job["id"] for job in first_jobs}
    assert job_id in {job["id"] for job in second_jobs}
    assert first_matches["count"] == 1
    assert first_matches["matches"][0]["job_id"] == job_id
    assert second_matches == {
        "count": 0,
        "confirmed_count": 0,
        "possible_count": 0,
        "matches": [],
    }
    assert (first_stats["applied_jobs"], first_stats["unapplied_jobs"]) == (1, 0)
    assert (second_stats["applied_jobs"], second_stats["unapplied_jobs"]) == (0, 1)
    assert MatchStore(second, app_db).confirmed_job_ids() == set()


def test_sheet_history_does_not_enter_jobstager_tracker(tracker_state):
    app_db, registry, _, user_id, _ = tracker_state
    registry.upsert_jobs([_job("sheet-only", "Data Science Intern")])

    MatchStore(user_id, app_db).reconcile_sheet(registry, [
        _sheet_row(
            "Acme",
            "Data Science Intern",
            "https://jobs.ashbyhq.com/acme/sheet-only",
        )
    ])

    assert LocalTracker(user_id, app_db).read_applications() == []
    assert MatchStore(user_id, app_db).list_matches(origin="sheet")[0]["origin"] == "sheet"


def test_in_app_tracker_works_without_a_sheet_connection(tracker_state):
    _, _, users, user_id, _ = tracker_state
    import web.auth as auth
    from core.tracker import tracker_for

    tracker = tracker_for(user_id, auth.profiles.get(user_id))
    assert isinstance(tracker, LocalTracker)
    assert tracker.log_application(_job("local-only", "Security Intern"), 2028)

    headers = {"Authorization": f"Bearer {users.start_session(user_id)}"}
    from web.server import app

    with TestClient(app) as client:
        response = client.get("/api/applications", headers=headers)

    assert response.status_code == 200
    assert response.json()["count"] == 1
    assert response.json()["applications"][0]["role"] == "Security Intern"
    assert response.json()["sheet_url"] is None


def test_reconcile_without_a_sheet_is_a_clear_no_op(tracker_state):
    _, _, users, user_id, _ = tracker_state
    headers = {"Authorization": f"Bearer {users.start_session(user_id)}"}
    from web.server import app

    with TestClient(app) as client:
        response = client.post("/api/reconcile", headers=headers)

    assert response.status_code == 200
    assert response.json()["status"] == "not_connected"
    assert response.json()["sheet_count"] == 0
    assert response.json()["matched_count"] == 0
