"""Ingestion engine for open-source internship feeds (SimplifyJobs / GitHub)."""

from __future__ import annotations

from datetime import datetime
import logging
import re
from typing import Dict, List, Optional, Tuple
from urllib.parse import urlparse
import httpx

from core.registry.store import CompanyRegistry
from core.scrapers.base import ATSProvider, CompanyBoard, JobPosting
from core.scrapers.resolver import resolve_url

logger = logging.getLogger(__name__)

SIMPLIFY_FEEDS = [
    # Active tech internships feed (16k+ entries)
    "https://raw.githubusercontent.com/SimplifyJobs/Summer2026-Internships/dev/.github/scripts/listings.json",
    "https://raw.githubusercontent.com/SimplifyJobs/Summer2027-Internships/dev/.github/scripts/listings.json",
]


async def ingest_simplify_feed(
    registry: CompanyRegistry,
    active_only: bool = True,
    client: Optional[httpx.AsyncClient] = None,
) -> Tuple[int, int]:
    """Download and ingest thousands of curated tech internships from SimplifyJobs into SQLite."""
    should_close = False
    if client is None:
        client = httpx.AsyncClient(timeout=30.0, follow_redirects=True)
        should_close = True

    total_companies = 0
    total_jobs = 0

    try:
        raw_items = []
        for feed_url in SIMPLIFY_FEEDS:
            try:
                logger.info(f"Fetching listings feed from {feed_url}...")
                resp = await client.get(feed_url)
                if resp.status_code == 200:
                    data = resp.json()
                    if isinstance(data, list):
                        raw_items.extend(data)
                        logger.info(f"Loaded {len(data)} items from {feed_url}")
                        break  # Summer2026/2027 share the primary dataset
            except Exception as e:
                logger.error(f"Error fetching feed {feed_url}: {e}")

        if not raw_items:
            return 0, 0

        logger.info(f"Processing {len(raw_items)} listings...")

        companies_to_add: Dict[str, CompanyBoard] = {}
        jobs_to_add: List[JobPosting] = []

        for item in raw_items:
            if active_only and not item.get("active", True):
                continue

            url = item.get("url", "").strip()
            if not url or not url.startswith("http"):
                continue

            company_name = item.get("company_name", "Unknown").strip()
            title = item.get("title", "Software Engineering Intern").strip()
            locations = ", ".join(item.get("locations", [])) or "Remote / Unspecified"
            
            # Resolve ATS Provider and Slug
            provider, slug, job_id = resolve_url(url)
            if provider == ATSProvider.UNKNOWN:
                # Check for workday host
                if "myworkdayjobs.com" in url:
                    provider = ATSProvider.WORKDAY
                    parsed = urlparse(url)
                    slug = parsed.netloc
                    job_id = url.split("/")[-1]
                else:
                    continue

            # Board record
            board_key = f"{provider.value}:{slug}"
            if board_key not in companies_to_add:
                board_url = url
                if provider == ATSProvider.GREENHOUSE:
                    board_url = f"https://boards.greenhouse.io/{slug}"
                elif provider == ATSProvider.LEVER:
                    board_url = f"https://jobs.lever.co/{slug}"
                elif provider == ATSProvider.ASHBY:
                    board_url = f"https://jobs.ashbyhq.com/{slug}"
                elif provider == ATSProvider.WORKDAY:
                    board_url = f"https://{slug}"

                companies_to_add[board_key] = CompanyBoard(
                    company_name=company_name,
                    slug=slug,
                    provider=provider,
                    board_url=board_url,
                    active=True,
                )

            # Job record
            date_posted = None
            if item.get("date_posted"):
                try:
                    date_posted = datetime.fromtimestamp(item["date_posted"]).strftime("%Y-%m-%d")
                except Exception:
                    date_posted = None

            posting = JobPosting(
                id=str(item.get("id") or job_id or f"{slug}_{len(jobs_to_add)}"),
                title=title,
                company=company_name,
                company_slug=slug,
                location=locations,
                url=url,
                apply_url=url,
                provider=provider,
                is_internship=True,
                updated_at=date_posted,
                raw_payload=item,
            )
            jobs_to_add.append(posting)

        # Batch insert companies into SQLite
        for board in companies_to_add.values():
            if registry.add_company(board):
                total_companies += 1

        # Batch insert jobs into SQLite
        total_jobs = registry.upsert_jobs(jobs_to_add)

        logger.info(f"Ingested {total_companies} companies and {total_jobs} active internship jobs.")
        return total_companies, total_jobs

    finally:
        if should_close:
            await client.aclose()
