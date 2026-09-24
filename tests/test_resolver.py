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


def test_resolve_workday_without_a_locale_takes_the_portal_not_job():
    # The feed's usual shape. The old pattern read `UR_External` as a locale and `job` as
    # the portal, registering a board that 404s.
    url = "https://equifax.wd5.myworkdayjobs.com/UR_External/job/USA-Alpharetta/Data-Intern_J00171081"
    provider, slug, job_id = resolve_url(url)
    assert provider == ATSProvider.WORKDAY
    assert slug == "equifax.wd5.myworkdayjobs.com/equifax/UR_External"
    assert job_id == "Data-Intern_J00171081"


def test_resolve_workday_board_page_and_hyphenated_portal():
    provider, slug, job_id = resolve_url("https://acme.wd1.myworkdayjobs.com/en-US/Acme-External")
    assert slug == "acme.wd1.myworkdayjobs.com/acme/Acme-External"
    assert job_id is None
    # A hyphen alone is not a locale.
    _, slug, _ = resolve_url("https://acme.wd1.myworkdayjobs.com/Acme-External/job/X/Intern_1")
    assert slug == "acme.wd1.myworkdayjobs.com/acme/Acme-External"


def test_resolve_workday_url_with_no_portal_is_unknown():
    provider, slug, _ = resolve_url("https://acme.wd1.myworkdayjobs.com/en-US/job/X/Intern_1")
    assert provider == ATSProvider.UNKNOWN
    assert slug == ""
