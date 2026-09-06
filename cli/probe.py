"""CLI probe command: probe and register a company's ATS board."""

from __future__ import annotations

import asyncio
import sys
from typing import Optional

from core.registry.store import CompanyRegistry
from core.scrapers.resolver import probe_company


async def run_probe(company_name: str, register: bool = True) -> int:
    """Probe ATS platforms for a company and display results."""
    print(f"🔍 Probing ATS endpoints for '{company_name}'...")
    board = await probe_company(company_name)

    if not board:
        print(f"❌ Could not detect an active Greenhouse, Lever, or Ashby board for '{company_name}'.")
        return 1

    print("\n✅ Found Active Board:")
    print(f"   Company:    {board.company_name}")
    print(f"   Provider:   {board.provider.value.upper()}")
    print(f"   Slug:       {board.slug}")
    print(f"   Board URL:  {board.board_url}")
    print(f"   Live Jobs:  {board.job_count}")

    if register:
        registry = CompanyRegistry()
        registry.add_company(board)
        print(f"   Registry:   Saved to local database ({registry.count_companies()} total companies).")

    return 0


def main():
    if len(sys.argv) < 2:
        print("Usage: job-stager probe <company_name>")
        sys.exit(1)

    company_name = " ".join(sys.argv[1:])
    exit_code = asyncio.run(run_probe(company_name))
    sys.exit(exit_code)


if __name__ == "__main__":
    main()
