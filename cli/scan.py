"""CLI scan command: high-concurrency board scanner."""

from __future__ import annotations

import argparse
import asyncio
import sys
from typing import List

from core.registry.seed import seed_registry
from core.registry.store import CompanyRegistry
from core.scrapers.base import ATSProvider, CompanyBoard
from core.scrapers.crawler import AsyncBoardCrawler


async def run_scan(
    company: str | None = None,
    provider: str | None = None,
    keywords: List[str] | None = None,
    internships_only: bool = False,
    concurrency: int = 25,
) -> int:
    registry = CompanyRegistry()

    if registry.count_companies() == 0:
        print("⚡ Registry empty. Pre-seeding known tech employers...")
        seed_registry(registry)

    boards: List[CompanyBoard] = []
    if company:
        b = registry.get_company(company)
        if b:
            boards = [b]
        else:
            print(f"❌ Company '{company}' not found in registry. Run `job-stager probe '{company}'` first.")
            return 1
    else:
        ats_enum = ATSProvider(provider.lower()) if provider else None
        boards = registry.list_companies(provider=ats_enum, active_only=True)

    print(f"🚀 Scanning {len(boards)} company boards (concurrency={concurrency})...")
    if keywords:
        print(f"   Filtering keywords: {', '.join(keywords)}")
    if internships_only:
        print("   Filtering: Internships / Co-ops only")

    crawler = AsyncBoardCrawler(concurrency=concurrency)
    matched_jobs = await crawler.crawl_all(
        boards=boards,
        keywords=keywords,
        internships_only=internships_only,
    )

    print(f"\n📊 Scan Complete: Found {len(matched_jobs)} matching opportunities.")
    if matched_jobs:
        # Save to registry
        saved = registry.upsert_jobs(matched_jobs)
        print(f"   Saved {saved} jobs to database.\n")

        print("Top Matches:")
        for i, job in enumerate(matched_jobs[:15], 1):
            intern_tag = " [INTERN]" if job.is_internship else ""
            print(f" {i:2d}. {job.company:<16} | {job.title}{intern_tag}")
            print(f"     Location: {job.location}")
            print(f"     Stage:    job-stager stage \"{job.url}\"")
            print()

        if len(matched_jobs) > 15:
            print(f"... and {len(matched_jobs) - 15} more jobs saved to database.")

    return 0


def main(args: list[str] | None = None):
    parser = argparse.ArgumentParser(description="Scan ATS boards for job openings")
    parser.add_argument("--company", "-c", help="Specific company name or slug to scan")
    parser.add_argument("--provider", "-p", choices=["greenhouse", "lever", "ashby", "workday"], help="Filter by ATS provider")
    parser.add_argument("--keywords", "-k", nargs="+", default=["intern", "engineer", "software", "data", "machine learning"], help="Keywords to match")
    parser.add_argument("--all-roles", action="store_true", help="Include all roles, not just internships")
    parser.add_argument("--concurrency", type=int, default=25, help="Number of concurrent requests")

    parsed = parser.parse_args(args)
    exit_code = asyncio.run(
        run_scan(
            company=parsed.company,
            provider=parsed.provider,
            keywords=None if parsed.all_roles else parsed.keywords,
            internships_only=not parsed.all_roles,
            concurrency=parsed.concurrency,
        )
    )
    sys.exit(exit_code)


if __name__ == "__main__":
    main()
