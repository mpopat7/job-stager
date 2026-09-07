"""Tests for JobStager web server and API endpoints."""

import httpx
import pytest
from web.server import app


@pytest.mark.asyncio
async def test_serve_terminal_ui():
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        res = await client.get("/")
        assert res.status_code == 200
        assert "job-stager" in res.text
        assert "TERMINAL LOG" in res.text
        # The header renders whoever is signed in. It used to be the operator's own
        # handle in the markup, which is wrong once more than one person has an account.
        assert 'id="candidate-name"' in res.text
        assert "milen@" not in res.text


@pytest.mark.asyncio
async def test_api_stats():
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        res = await client.get("/api/stats")
        assert res.status_code == 200
        data = res.json()
        assert "total_jobs" in data
        assert "applied_jobs" in data
        assert "unapplied_jobs" in data
        assert "candidate" in data
        assert "tracker" in data


@pytest.mark.asyncio
async def test_api_jobs_filtering():
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        res = await client.get("/api/jobs?limit=5&hide_applied=true")
        assert res.status_code == 200
        data = res.json()
        assert "count" in data
        assert "jobs" in data
        assert len(data["jobs"]) <= 5
        if data["jobs"]:
            assert "posted_date" in data["jobs"][0]
            assert "updated_at" in data["jobs"][0]


@pytest.mark.asyncio
async def test_resume_config_requires_a_session():
    """Cohorts and uploads are profile data, so they are not readable signed out."""
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        assert (await client.get("/api/resumes")).status_code == 401
        assert (await client.post("/api/resumes/cohorts", json={"active_cohorts": [2028]})).status_code == 401


@pytest.mark.asyncio
async def test_api_resumes_endpoints(tmp_path, monkeypatch):
    monkeypatch.setattr("core.store.db.DEFAULT_DB_PATH", tmp_path / "web.db")
    import web.auth as auth
    from core.store.profiles import ProfileStore
    from core.store.users import UserStore
    import web.server as server
    auth.users = server.users = UserStore(tmp_path / "web.db")
    auth.profiles = server.profiles = ProfileStore(tmp_path / "web.db")

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        reg = await client.post(
            "/api/auth/register", json={"handle": "webtest", "password": "web-test-password"}
        )
        assert reg.status_code == 200

        # GET /api/resumes
        res = await client.get("/api/resumes")
        assert res.status_code == 200
        data = res.json()
        assert "active_cohorts" in data
        assert "cohorts" in data
        assert "2028" in data["cohorts"]
        assert "2029" in data["cohorts"]

        # POST /api/resumes/cohorts
        cohort_res = await client.post(
            "/api/resumes/cohorts",
            json={"active_cohorts": [2027, 2028, 2029], "default_cohort": 2028},
        )
        assert cohort_res.status_code == 200
        assert cohort_res.json()["active_cohorts"] == [2027, 2028, 2029]

        # POST /api/resumes/upload
        dummy_pdf = b"%PDF-1.4 mock resume content"
        files = {"file": ("test_resume_2027.pdf", dummy_pdf, "application/pdf")}
        upload_res = await client.post(
            "/api/resumes/upload",
            data={"grad_year": 2027},
            files=files,
        )
        assert upload_res.status_code == 200
        upload_data = upload_res.json()
        assert upload_data["status"] == "uploaded"
        assert upload_data["grad_year"] == 2027
