"""Tests for ATS scrapers with mocked HTTP responses."""

import json

import pytest
import httpx
from core.scrapers.base import ATSProvider
from core.scrapers.greenhouse import GreenhouseScraper
from core.scrapers.lever import LeverScraper
from core.scrapers.ashby import AshbyScraper
from core.scrapers.workday import WorkdayScraper


@pytest.mark.asyncio
async def test_greenhouse_scraper_parsing():
    mock_jobs = {
        "jobs": [
            {
                "id": 12345,
                "title": "Software Engineer Intern",
                "location": {"name": "San Francisco, CA"},
                "departments": [{"name": "Engineering"}],
                "absolute_url": "https://boards.greenhouse.io/stripe/jobs/12345",
                "updated_at": "2026-03-01T00:00:00Z",
            }
        ]
    }

    def handler(request: httpx.Request):
        return httpx.Response(200, json=mock_jobs)

    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(transport=transport) as client:
        scraper = GreenhouseScraper(client=client)
        jobs = await scraper.fetch_jobs("stripe")

    assert len(jobs) == 1
    assert jobs[0].id == "12345"
    assert jobs[0].title == "Software Engineer Intern"
    assert jobs[0].provider == ATSProvider.GREENHOUSE
    assert jobs[0].is_internship is True


@pytest.mark.asyncio
async def test_lever_scraper_parsing():
    mock_postings = [
        {
            "id": "abc-123",
            "text": "Data Engineering Intern",
            "categories": {
                "location": "New York, NY",
                "department": "Analytics",
                "commitment": "Intern",
            },
            "hostedUrl": "https://jobs.lever.co/palantir/abc-123",
            "applyUrl": "https://jobs.lever.co/palantir/abc-123/apply",
        }
    ]

    def handler(request: httpx.Request):
        return httpx.Response(200, json=mock_postings)

    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(transport=transport) as client:
        scraper = LeverScraper(client=client)
        jobs = await scraper.fetch_jobs("palantir")

    assert len(jobs) == 1
    assert jobs[0].id == "abc-123"
    assert jobs[0].title == "Data Engineering Intern"
    assert jobs[0].provider == ATSProvider.LEVER
    assert jobs[0].is_internship is True


@pytest.mark.asyncio
async def test_ashby_scraper_parsing():
    mock_data = {
        "jobs": [
            {
                "id": "xyz-789",
                "title": "Machine Learning Intern",
                "department": "AI Research",
                "location": "San Francisco, CA",
                "jobUrl": "https://jobs.ashbyhq.com/ramp/xyz-789",
                "publishedAt": "2026-03-01T00:00:00Z",
            }
        ]
    }

    def handler(request: httpx.Request):
        return httpx.Response(200, json=mock_data)

    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(transport=transport) as client:
        scraper = AshbyScraper(client=client)
        jobs = await scraper.fetch_jobs("ramp")

    assert len(jobs) == 1
    assert jobs[0].id == "xyz-789"
    assert jobs[0].title == "Machine Learning Intern"
    assert jobs[0].provider == ATSProvider.ASHBY
    assert jobs[0].is_internship is True


@pytest.mark.asyncio
async def test_workday_scraper_pages_within_the_limit_cxs_accepts():
    """CXS answers 400 to `limit` over 20 and reports `total` on the first page only."""
    total = 45
    seen = []

    def handler(request: httpx.Request):
        body = json.loads(request.content)
        seen.append(body)
        if body["limit"] > 20:
            return httpx.Response(400)
        start = body["offset"]
        batch = [
            {"title": f"Software Intern {i}", "externalPath": f"/job/US-CA/Software-Intern_R{i}",
             "locationsText": "US-CA", "bulletFields": [f"R{i}"]}
            for i in range(start, min(start + body["limit"], total))
        ]
        return httpx.Response(200, json={"total": total if start == 0 else 0, "jobPostings": batch})

    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(transport=transport) as client:
        scraper = WorkdayScraper(client=client, search_text="intern")
        jobs = await scraper.fetch_jobs("acme.wd1.myworkdayjobs.com/acme/External")

    assert len(jobs) == total
    assert [b["offset"] for b in seen] == [0, 20, 40]
    assert all(b["searchText"] == "intern" for b in seen)
    assert jobs[0].url == (
        "https://acme.wd1.myworkdayjobs.com/External/job/US-CA/Software-Intern_R0")
    assert jobs[0].company_slug == "acme.wd1.myworkdayjobs.com/acme/External"
