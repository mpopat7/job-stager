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
