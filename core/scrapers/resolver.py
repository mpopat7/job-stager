"""URL resolver and company ATS probe engine."""

from __future__ import annotations

import asyncio
import logging
import re
from typing import Dict, List, Optional, Tuple
from urllib.parse import parse_qs, urlparse
import httpx

from core.scrapers.base import ATSProvider, CompanyBoard

logger = logging.getLogger(__name__)


# Regular expressions for identifying ATS platforms and extracting slugs/job IDs
URL_PATTERNS = [
    # Greenhouse
    (
        ATSProvider.GREENHOUSE,
        re.compile(r"boards\.greenhouse\.io/(?:embed/job_app\?.*for=([a-zA-Z0-9_\-]+).*token=([0-9]+)|([a-zA-Z0-9_\-]+)/jobs/([0-9]+))"),
    ),
    (
        ATSProvider.GREENHOUSE,
        re.compile(r"job-boards\.greenhouse\.io/([a-zA-Z0-9_\-]+)/jobs/([0-9]+)"),
    ),
    (
        ATSProvider.GREENHOUSE,
        re.compile(r"boards\.greenhouse\.io/([a-zA-Z0-9_\-]+)"),
    ),
    # Lever
    (
        ATSProvider.LEVER,
        re.compile(r"jobs\.lever\.co/([a-zA-Z0-9_\-]+)/([a-f0-9\-]+)"),
    ),
    (
        ATSProvider.LEVER,
        re.compile(r"jobs\.lever\.co/([a-zA-Z0-9_\-]+)"),
    ),
    # Ashby
    (
        ATSProvider.ASHBY,
        re.compile(r"jobs\.ashbyhq\.com/([a-zA-Z0-9_\-]+)/([a-f0-9\-]+)"),
    ),
    (
        ATSProvider.ASHBY,
        re.compile(r"jobs\.ashbyhq\.com/([a-zA-Z0-9_\-]+)"),
    ),
    # Workday
    (
        ATSProvider.WORKDAY,
        re.compile(r"https?://([a-zA-Z0-9_\-]+\.wd[0-9]+\.myworkdayjobs\.com)/[^/]+/([^/]+)/job/[^/]+/([^/?#]+)"),
    ),
    (
        ATSProvider.WORKDAY,
        re.compile(r"https?://([a-zA-Z0-9_\-]+\.wd[0-9]+\.myworkdayjobs\.com)/[^/]+/([^/?#]+)"),
    ),
]


def resolve_url(url: str) -> Tuple[ATSProvider, str, Optional[str]]:
    """Resolve an ATS URL into (provider, company_slug, job_id).

    Returns:
        (ATSProvider.UNKNOWN, "", None) if the URL cannot be identified.
    """
    cleaned_url = url.strip()

    # Greenhouse embed or query parameter on custom domain (e.g. ?gh_jid=8128745)
    parsed = urlparse(cleaned_url)
    params = parse_qs(parsed.query)

    if "gh_jid" in params:
        job_id = params["gh_jid"][0]
        # Extract slug from hostname (e.g. stripe.com -> stripe)
        host_parts = parsed.netloc.split(".")
        slug = host_parts[-2] if len(host_parts) >= 2 and host_parts[-2] not in ["com", "org", "io", "co"] else host_parts[0]
        return ATSProvider.GREENHOUSE, slug, job_id

    # Greenhouse special handling for embed query params
    if "boards.greenhouse.io/embed/job_app" in cleaned_url:
        slug = params.get("for", [""])[0]
        job_id = params.get("token", [None])[0]
        if slug:
            return ATSProvider.GREENHOUSE, slug, job_id

    for provider, pattern in URL_PATTERNS:
        match = pattern.search(cleaned_url)
        if not match:
            continue

        groups = [g for g in match.groups() if g is not None]
        if not groups:
            continue

        if provider == ATSProvider.GREENHOUSE:
            if len(groups) >= 2:
                return provider, groups[0], groups[1]
            return provider, groups[0], None

        elif provider == ATSProvider.LEVER:
            if len(groups) >= 2:
                return provider, groups[0], groups[1]
            return provider, groups[0], None

        elif provider == ATSProvider.ASHBY:
            if len(groups) >= 2:
                return provider, groups[0], groups[1]
            return provider, groups[0], None

        elif provider == ATSProvider.WORKDAY:
            # groups: host, portal, [job_title_req]
            host = groups[0]
            tenant = host.split(".")[0]
            portal = groups[1]
            job_id = groups[2] if len(groups) > 2 else None
            slug = f"{host}/{tenant}/{portal}"
            return provider, slug, job_id

    # Check for custom domain Greenhouse if data-gh-job-id or iframe
    return ATSProvider.UNKNOWN, "", None


def generate_slug_candidates(name_or_domain: str) -> List[str]:
    """Generate potential ATS slugs from a company name or domain.

    Example: "Stripe" -> ["stripe"]
    Example: "Ramp Financial" -> ["ramp", "rampfinancial", "ramp-financial"]
    """
    clean = name_or_domain.lower().strip()
    if "." in clean and not clean.startswith("http"):
        clean = clean.split(".")[0]

    # Remove common corporate suffixes
    clean = re.sub(r"\b(inc|corp|corporation|llc|technologies|tech|ai|labs|io)\b", "", clean)
    clean = re.sub(r"[^a-z0-9\s\-]", "", clean).strip()

    words = clean.split()
    if not words:
        return [name_or_domain.lower().strip()]

    candidates = [
        "-".join(words),
        "".join(words),
        words[0],
    ]
    # Deduplicate while preserving order
    seen = set()
    return [c for c in candidates if c and not (c in seen or seen.add(c))]


async def probe_company(
    company_name: str,
    client: Optional[httpx.AsyncClient] = None,
) -> Optional[CompanyBoard]:
    """Probe public ATS REST endpoints concurrently to discover where a company hosts its board."""
    slugs = generate_slug_candidates(company_name)
    should_close = False
    headers = {
        "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36",
        "Accept": "application/json",
    }
    if client is None:
        client = httpx.AsyncClient(timeout=8.0, headers=headers, follow_redirects=True)
        should_close = True

    try:
        tasks = []
        for slug in slugs:
            # Greenhouse probe (lightweight, no content payload)
            tasks.append((ATSProvider.GREENHOUSE, slug, f"https://boards-api.greenhouse.io/v1/boards/{slug}/jobs"))
            # Lever probe (limit=1 for fast probe)
            tasks.append((ATSProvider.LEVER, slug, f"https://api.lever.co/v0/postings/{slug}?mode=json&limit=1"))
            # Ashby probe
            tasks.append((ATSProvider.ASHBY, slug, f"https://api.ashbyhq.com/posting-api/job-board/{slug}"))

        async def check_url(provider: ATSProvider, slug: str, url: str) -> Optional[CompanyBoard]:
            try:
                resp = await client.get(url)
                if resp.status_code == 200:
                    data = resp.json()
                    # Verify response actually contains jobs structure
                    job_count = 0
                    if provider == ATSProvider.GREENHOUSE and isinstance(data, dict) and "jobs" in data:
                        job_count = len(data.get("jobs", []))
                        board_url = f"https://boards.greenhouse.io/{slug}"
                    elif provider == ATSProvider.LEVER and isinstance(data, list):
                        job_count = len(data)
                        board_url = f"https://jobs.lever.co/{slug}"
                    elif provider == ATSProvider.ASHBY and isinstance(data, dict) and "jobs" in data:
                        job_count = len(data.get("jobs", []))
                        board_url = f"https://jobs.ashbyhq.com/{slug}"
                    else:
                        return None

                    return CompanyBoard(
                        company_name=company_name.title(),
                        slug=slug,
                        provider=provider,
                        board_url=board_url,
                        job_count=job_count,
                    )
            except Exception:
                return None
            return None

        results = await asyncio.gather(*(check_url(p, s, u) for p, s, u in tasks))
        for res in results:
            if res is not None:
                return res
        return None

    finally:
        if should_close:
            await client.aclose()
