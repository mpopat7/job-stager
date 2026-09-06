"""High-concurrency async multi-board job crawler."""

from __future__ import annotations

import asyncio
import logging
from typing import Dict, List, Optional
import httpx

from core.scrapers.ashby import AshbyScraper
from core.scrapers.base import ATSProvider, CompanyBoard, JobPosting
from core.scrapers.greenhouse import GreenhouseScraper
from core.scrapers.lever import LeverScraper
from core.scrapers.workday import WorkdayScraper

logger = logging.getLogger(__name__)


class AsyncBoardCrawler:
    """High-throughput async crawler that scans multiple company ATS boards concurrently."""

    def __init__(
        self,
        concurrency: int = 25,
        timeout: float = 15.0,
    ):
        self.concurrency = concurrency
        self.timeout = timeout
        self.semaphore = asyncio.Semaphore(concurrency)

    async def crawl_board(
        self,
        board: CompanyBoard,
        client: httpx.AsyncClient,
        keywords: Optional[List[str]] = None,
        internships_only: bool = False,
    ) -> List[JobPosting]:
        """Crawl a single company board with concurrency control."""
        async with self.semaphore:
            scraper_map = {
                ATSProvider.GREENHOUSE: GreenhouseScraper(client),
                ATSProvider.LEVER: LeverScraper(client),
                ATSProvider.ASHBY: AshbyScraper(client),
                ATSProvider.WORKDAY: WorkdayScraper(client),
            }

            scraper = scraper_map.get(board.provider)
            if not scraper:
                logger.debug(f"Unsupported provider {board.provider} for {board.company_name}")
                return []

            try:
                jobs = await scraper.fetch_jobs(board.slug)
            except Exception as e:
                logger.error(f"Error crawling {board.company_name} ({board.provider}): {e}")
                return []

            # Filter jobs
            matched: List[JobPosting] = []
            for j in jobs:
                if internships_only and not j.is_internship:
                    continue
                if keywords and not j.matches_keywords(keywords):
                    continue
                matched.append(j)

            return matched

    async def crawl_all(
        self,
        boards: List[CompanyBoard],
        keywords: Optional[List[str]] = None,
        internships_only: bool = False,
    ) -> List[JobPosting]:
        """Crawl a collection of company boards concurrently."""
        limits = httpx.Limits(max_connections=100, max_keepalive_connections=25)
        headers = {
            "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36",
            "Accept": "application/json",
        }

        async with httpx.AsyncClient(limits=limits, timeout=self.timeout, headers=headers, follow_redirects=True) as client:
            tasks = [
                self.crawl_board(
                    board=board,
                    client=client,
                    keywords=keywords,
                    internships_only=internships_only,
                )
                for board in boards
                if board.active
            ]

            results = await asyncio.gather(*tasks, return_exceptions=True)

        all_jobs: List[JobPosting] = []
        seen_urls = set()

        for res in results:
            if isinstance(res, list):
                for job in res:
                    if job.url not in seen_urls:
                        seen_urls.add(job.url)
                        all_jobs.append(job)
            elif isinstance(res, Exception):
                logger.error(f"Crawl task exception: {res}")

        return all_jobs
