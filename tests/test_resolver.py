"""Tests for URL resolution and slug generation."""

import pytest
from core.scrapers.base import ATSProvider
from core.scrapers.resolver import generate_slug_candidates, resolve_url


def test_resolve_greenhouse_standard():
    url = "https://boards.greenhouse.io/stripe/jobs/8128745"
    provider, slug, job_id = resolve_url(url)
    assert provider == ATSProvider.GREENHOUSE
    assert slug == "stripe"
    assert job_id == "8128745"


def test_resolve_greenhouse_custom_domain():
    url = "https://stripe.com/jobs/search?gh_jid=8128745"
    provider, slug, job_id = resolve_url(url)
    assert provider == ATSProvider.GREENHOUSE
    assert slug == "stripe"
    assert job_id == "8128745"


def test_resolve_lever():
    url = "https://jobs.lever.co/palantir/57a0ec94-2751-4f11-9a4f-561bcf74eec7"
    provider, slug, job_id = resolve_url(url)
    assert provider == ATSProvider.LEVER
    assert slug == "palantir"
    assert job_id == "57a0ec94-2751-4f11-9a4f-561bcf74eec7"


def test_resolve_ashby():
    url = "https://jobs.ashbyhq.com/ramp/4e64ab86-4e30-403b-b1b9-41dc052570ce"
    provider, slug, job_id = resolve_url(url)
    assert provider == ATSProvider.ASHBY
    assert slug == "ramp"
    assert job_id == "4e64ab86-4e30-403b-b1b9-41dc052570ce"


def test_resolve_workday():
    url = "https://nvidia.wd5.myworkdayjobs.com/en-US/NVIDIAExternalCareerSite/job/US-CA-Santa-Clara/Intern_JR123"
    provider, slug, job_id = resolve_url(url)
    assert provider == ATSProvider.WORKDAY
    assert "nvidia" in slug
    assert job_id == "Intern_JR123"


def test_generate_slug_candidates():
    candidates = generate_slug_candidates("Ramp Financial Inc.")
    assert "ramp-financial" in candidates or "ramp" in candidates
