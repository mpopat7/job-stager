"""Tests for ATS scrapers with mocked HTTP responses."""

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
