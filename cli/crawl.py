"""Scheduled crawl: refresh the shared job registry without anyone's laptop being on.

Runs the two discovery passes the dashboard otherwise triggers by hand -- the SimplifyJobs
feed, which also registers companies it has not seen, then a scan of every active board --
against whatever database `DATABASE_URL` names. GitHub Actions runs it on a schedule
(`.github/workflows/crawl.yml`); locally it is `uv run python3 -m cli.crawl`.

Only public posting metadata is written. Nothing here reads or touches a user's data.
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import sys

from cli.scan import run_scan
from core.registry.ingest import ingest_simplify_feed
from core.registry.store import CompanyRegistry

logger = logging.getLogger("crawl")


async def crawl(concurrency: int) -> int:
    registry = CompanyRegistry()
    failures = 0

    try:
        companies, jobs = await ingest_simplify_feed(registry, active_only=True)
        print(f"Simplify feed: {companies} companies registered, {jobs} jobs added.")
    except Exception as err:
        # The board scan is independent of the feed, so one failing is no reason to skip the other.
        logger.exception(f"Simplify feed failed: {err}")
        failures += 1

    try:
        if await run_scan(internships_only=True, keywords=None, concurrency=concurrency) != 0:
            failures += 1
    except Exception as err:
        logger.exception(f"Board scan failed: {err}")
        failures += 1

    try:
        # Cheap when nothing changed: only rows whose family differs are written.
        print(f"Role families updated on {registry.reclassify_roles()} jobs.")
    except Exception as err:
        logger.exception(f"Reclassifying roles failed: {err}")
        failures += 1

    print(f"Registry now holds {registry.count_companies()} companies and {registry.count_jobs()} jobs.")
    return 1 if failures else 0


def main(args: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="Refresh the job registry from every source")
    parser.add_argument("--concurrency", type=int, default=25)
    parsed = parser.parse_args(args)
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    sys.exit(asyncio.run(crawl(parsed.concurrency)))


if __name__ == "__main__":
    main()
