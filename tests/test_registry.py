"""Tests for SQLite company and job registry."""

import pytest
from core.registry.store import CompanyRegistry
from core.scrapers.base import ATSProvider, CompanyBoard, JobPosting


def test_registry_crud(tmp_path):
    db_file = tmp_path / "test_companies.db"
    reg = CompanyRegistry(db_file)

    # 1. Add company
    board = CompanyBoard(
        company_name="TestCorp",
        slug="testcorp",
        provider=ATSProvider.GREENHOUSE,
        board_url="https://boards.greenhouse.io/testcorp",
        job_count=5,
    )
    assert reg.add_company(board) is True
    assert reg.count_companies() == 1

    # 2. Get company
    found = reg.get_company("testcorp")
    assert found is not None
    assert found.company_name == "TestCorp"
    assert found.provider == ATSProvider.GREENHOUSE

    # 3. Upsert job
    job = JobPosting(
        id="123",
        title="Software Intern",
        company="TestCorp",
        company_slug="testcorp",
        url="https://boards.greenhouse.io/testcorp/jobs/123",
        apply_url="https://boards.greenhouse.io/testcorp/jobs/123#app",
        provider=ATSProvider.GREENHOUSE,
        is_internship=True,
    )
    inserted = reg.upsert_jobs([job])
    assert inserted == 1

    # 4. Auto-register new URL
    auto_board = reg.auto_register_from_url("https://jobs.lever.co/newstartup/abc")
    assert auto_board is not None
    assert auto_board.slug == "newstartup"
    assert reg.count_companies() == 2


def test_reconcile_with_tracker(tmp_path):
    db_file = tmp_path / "test_reconcile.db"
    reg = CompanyRegistry(db_file)

    # Insert two jobs: one for Stripe, one for Datadog
    job1 = JobPosting(
        id="stripe-1",
        title="Software Engineer, Intern",
        company="Stripe",
        company_slug="stripe",
        url="https://stripe.com/jobs/search?gh_jid=8128745",
        apply_url="https://stripe.com/jobs/search?gh_jid=8128745",
        provider=ATSProvider.GREENHOUSE,
        is_internship=True,
    )
    job2 = JobPosting(
        id="datadog-1",
        title="Software Engineer Intern - Summer 2026",
        company="Datadog",
        company_slug="datadog",
        url="https://careers.datadoghq.com/detail/12345",
        apply_url="https://careers.datadoghq.com/detail/12345",
        provider=ATSProvider.GREENHOUSE,
        is_internship=True,
    )
    reg.upsert_jobs([job1, job2])
    assert reg.count_jobs() == 2
    assert reg.count_applied_jobs() == 0

    # Mock sheet applications matching Stripe
    applications = [
        {
            "Company": "Stripe",
            "Role": "Software Engineer Intern",
            "Link": "https://stripe.com/jobs/search?gh_jid=8128745",
            "Stage": "OA Completed",
            "Date Applied": "2026-09-01",
            "Comp/Notes": "Stripe test",
        }
    ]

    matched_count, records = reg.reconcile_with_tracker(applications)
    assert matched_count == 1
    assert reg.count_applied_jobs() == 1

    # Verify get_jobs with hide_applied=True (default behavior)
    unapplied = reg.get_jobs(hide_applied=True)
    assert len(unapplied) == 1
    assert unapplied[0].company == "Datadog"

    # Verify get_jobs with hide_applied=False includes Stripe marked as applied
    all_jobs = reg.get_jobs(hide_applied=False)
    assert len(all_jobs) == 2
    stripe_job = next(j for j in all_jobs if j.company == "Stripe")
    assert stripe_job.status == "applied"
    assert stripe_job.stage == "OA Completed"
    assert stripe_job.applied_date == "2026-09-01"


def test_a_posting_found_by_feed_and_scan_is_stored_once(tmp_path):
    """The feed names a Workday posting by UUID, a board scan by requisition number."""
    registry = CompanyRegistry(db_path=tmp_path / "dedupe.db")
    url = "https://acme.wd1.myworkdayjobs.com/External/job/US-CA/Software-Intern_R1"
    slug = "acme.wd1.myworkdayjobs.com/acme/External"

    def posting(job_id, title):
        return JobPosting(id=job_id, title=title, company="Acme", company_slug=slug,
                          location="US-CA", url=url, apply_url=url,
                          provider=ATSProvider.WORKDAY, is_internship=True)

    assert registry.upsert_jobs([posting("3b7e5b89-uuid", "Software Intern")]) == 1
    assert registry.upsert_jobs([posting("R1", "Software Intern (Summer 2027)")]) == 0
    assert registry.count_jobs() == 1
    job = registry.find_job(url=url)
    assert job["id"] == f"workday:{slug}:3b7e5b89-uuid"
    assert job["title"] == "Software Intern (Summer 2027)"


def test_normalize_posted_makes_every_board_date_sortable():
    from datetime import date
    from core.scrapers.base import normalize_posted

    seen = date(2026, 9, 24)
    assert normalize_posted("Posted Today", seen) == "2026-09-24"
    assert normalize_posted("Posted Yesterday", seen) == "2026-09-23"
    assert normalize_posted("Posted 3 Days Ago", seen) == "2026-09-21"
    assert normalize_posted("Posted 30+ Days Ago", seen) == "2026-08-25"
    assert normalize_posted("1788371280643") == "2026-09-02"
    assert normalize_posted("2026-09-01T12:00:00Z") == "2026-09-01T12:00:00Z"
    # A Workday tenant that puts a location in the date field sorts as no date, not first.
    assert normalize_posted("Tempe, AZ") is None
    assert normalize_posted(None) is None


def test_role_filter_and_counts_cover_the_whole_registry_not_one_page(tmp_path):
    """The Jobs tab used to fetch 50 rows and filter those, so a rare family read as 0."""
    reg = CompanyRegistry(tmp_path / "roles.db")

    def job(i, title, posted):
        url = f"https://jobs.ashbyhq.com/acme/{i}"
        return JobPosting(id=str(i), title=title, company="Acme", company_slug="acme",
                          location="Remote", url=url, apply_url=url,
                          provider=ATSProvider.ASHBY, is_internship=True, updated_at=posted)

    # 60 recent marketing roles bury 3 older software roles past the first page.
    reg.upsert_jobs([job(i, "Marketing Intern", "Posted Today") for i in range(60)]
                    + [job(100 + i, "Software Engineer Intern", "2026-01-01") for i in range(3)])

    counts = reg.count_jobs_by_role()
    assert counts["Software Engineering"] == 3
    assert sum(counts.values()) == 63
    swe = reg.get_jobs(role="Software Engineering", limit=50)
    assert [j.title for j in swe] == ["Software Engineer Intern"] * 3

    # Paging walks the full list once, newest first, with no repeats.
    first = reg.get_jobs(limit=50)
    rest = reg.get_jobs(limit=50, offset=50)
    ids = [j.id for j in first + rest]
    assert len(ids) == len(set(ids)) == 63
    assert first[0].title == "Marketing Intern"
    assert rest[-1].title == "Software Engineer Intern"


def test_named_engineering_disciplines_are_not_software():
    from core.registry.roles import classify_role

    assert classify_role("2027 Mechanical Engineer Intern") == "Other Engineering"
    assert classify_role("Chemist/Chemical Engineer Intern") == "Other Engineering"
    assert classify_role("Software Quality Engineer Intern") == "Software Engineering"
    assert classify_role("Engineering Intern") == "Software Engineering"
