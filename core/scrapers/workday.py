"""Workday CXS REST API scraper for modern Workday career portals."""

from __future__ import annotations

import logging
import re
from typing import Any, Dict, List, Optional
from urllib.parse import urlparse
import httpx

from core.scrapers.base import (
    ATSProvider,
    BaseScraper,
    FieldType,
    FormField,
    FormSchema,
    JobPosting,
    detect_is_internship,
)

logger = logging.getLogger(__name__)

# A locale segment is a language code with an optional region: en, en-US, fr-CA, zh-Hans-CN.
# The old test was "contains a hyphen", which read a portal named e.g. `UR-External` as a
# locale and took the next segment -- usually the literal `job` -- as the portal.
_LOCALE = re.compile(r"^[a-z]{2}(?:-[A-Za-z]{2,4})?(?:-[A-Z]{2})?$")
# Path segments that follow a portal, never name one. Not `jobs`: Carrier's portal is named that.
_NOT_A_PORTAL = {"job", "details", "wday", "login", "apply"}

# CXS answers 400 to any page larger than this.
PAGE_SIZE = 20
# 500 postings per board is far past any intern search; the cap bounds a runaway board.
MAX_PAGES = 25


class WorkdayScraper(BaseScraper):
    """Scrapes modern Workday portals via their CXS (Candidate Experience Service) REST API."""

    provider = ATSProvider.WORKDAY

    def __init__(self, client: Optional[httpx.AsyncClient] = None, search_text: str = ""):
        self._client = client
        # Workday boards run to thousands of postings; searching server-side for "intern"
        # when only internships are wanted keeps a large board to a page or two.
        self.search_text = search_text

    async def _get_client(self) -> httpx.AsyncClient:
        if self._client and not self._client.is_closed:
            return self._client
        return httpx.AsyncClient(
            timeout=20.0,
            follow_redirects=True,
            headers={
                "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36",
                "Accept": "application/json",
                "Content-Type": "application/json",
            },
        )

    @staticmethod
    def parse_workday_url(url: str) -> Optional[Dict[str, str]]:
        """Extract host, tenant, portal, and optional job path from a Workday URL.

        Both shapes occur in the wild, and the feed mostly hands out the second:
            https://nvidia.wd5.myworkdayjobs.com/en-US/NVIDIAExternalCareerSite/job/US-CA/Intern_JR123
            https://equifax.wd5.myworkdayjobs.com/UR_External/job/USA-Alpharetta/Data-Intern_J00171081
        Returns None when the URL names no portal, rather than inventing one: a guessed
        portal is a board the crawler will 404 on every run.
        """
        parsed = urlparse(url)
        host = parsed.netloc.lower()
        if not host.endswith(".myworkdayjobs.com"):
            return None
        tenant = host.split(".", 1)[0]

        parts = [p for p in parsed.path.split("/") if p]
        if parts and _LOCALE.match(parts[0]):
            parts = parts[1:]
        if not parts or parts[0].lower() in _NOT_A_PORTAL:
            return None

        portal = parts[0]
        rest = parts[1:]
        job_path = "/" + "/".join(rest) if rest and rest[0] == "job" else ""
        return {"host": host, "tenant": tenant, "portal": portal, "job_path": job_path}

    async def fetch_jobs(self, slug_or_url: str) -> List[JobPosting]:
        """Fetch jobs from a Workday portal using CXS REST API.

        Accepts either a full Workday URL or a formatted slug: `host/tenant/portal`.
        """
        if "://" in slug_or_url:
            parsed = self.parse_workday_url(slug_or_url)
            if not parsed:
                logger.error(f"Could not parse Workday URL: {slug_or_url}")
                return []
            host = parsed["host"]
            tenant = parsed["tenant"]
            portal = parsed["portal"]
        else:
            # Expected format: host/tenant/portal or host|tenant|portal
            tokens = slug_or_url.replace("|", "/").split("/")
            if len(tokens) >= 3:
                host, tenant, portal = tokens[0], tokens[1], tokens[2]
            else:
                logger.error(f"Invalid Workday slug format: {slug_or_url}")
                return []

        endpoint = f"https://{host}/wday/cxs/{tenant}/{portal}/jobs"
        client = await self._get_client()
        should_close = client != self._client

        postings: List[Dict[str, Any]] = []
        try:
            total = None
            for page in range(MAX_PAGES):
                payload = {
                    "appliedFacets": {},
                    "limit": PAGE_SIZE,
                    "offset": page * PAGE_SIZE,
                    "searchText": self.search_text,
                }
                resp = await client.post(endpoint, json=payload)
                if resp.status_code == 404:
                    logger.warning(f"Workday CXS returned 404 on {endpoint}")
                    break
                resp.raise_for_status()
                data = resp.json()
                batch = data.get("jobPostings") or []
                postings.extend(batch)
                # Only the first page reports `total`; later pages say 0.
                if total is None:
                    total = data.get("total") or 0
                if len(batch) < PAGE_SIZE or len(postings) >= total:
                    break
        except Exception as e:
            # Keep whatever pages arrived before the failure.
            logger.error(f"Error requesting Workday CXS at {endpoint}: {e}")
        finally:
            if should_close:
                await client.aclose()

        jobs: List[JobPosting] = []
        company_name = tenant.replace("-", " ").title()

        for item in postings:
            ext_path = item.get("externalPath", "")
            title = item.get("title", "Unknown Title")
            location = item.get("locationsText") or "Unspecified"
            
            # Extract req id if in bulletFields or ext_path
            req_id = ""
            if item.get("bulletFields"):
                req_id = item["bulletFields"][0]
            elif "_" in ext_path:
                req_id = ext_path.rsplit("_", 1)[-1]
            else:
                req_id = ext_path.strip("/").split("/")[-1] if ext_path else title

            full_url = f"https://{host}/{portal}{ext_path}"
            apply_url = f"{full_url}/apply"

            job = JobPosting(
                id=req_id,
                title=title,
                company=company_name,
                company_slug=f"{host}/{tenant}/{portal}",
                location=location,
                url=full_url,
                apply_url=apply_url,
                provider=ATSProvider.WORKDAY,
                is_internship=detect_is_internship(title),
                updated_at=item.get("postedOn"),
                raw_payload=item,
            )
            jobs.append(job)

        return jobs

    async def fetch_form_schema(self, slug_or_url: str, job_id: str) -> FormSchema:
        """Workday form schemas are dynamic and multi-step (account creation + questionnaire).

        Returns standard Workday multi-step schema for staging adapters.
        """
        parsed = self.parse_workday_url(slug_or_url) if "://" in slug_or_url else None
        slug = f"{parsed['host']}/{parsed['tenant']}/{parsed['portal']}" if parsed else slug_or_url

        standard_fields = [
            FormField(id="legalName.firstName", name="firstName", label="First Name", field_type=FieldType.TEXT, required=True),
            FormField(id="legalName.lastName", name="lastName", label="Last Name", field_type=FieldType.TEXT, required=True),
            FormField(id="email", name="email", label="Email Address", field_type=FieldType.TEXT, required=True),
            FormField(id="phoneNumber", name="phoneNumber", label="Phone Number", field_type=FieldType.TEXT, required=True),
            FormField(id="address.city", name="city", label="City", field_type=FieldType.TEXT, required=True),
            FormField(id="address.state", name="state", label="State/Province", field_type=FieldType.TEXT, required=True),
            FormField(id="address.country", name="country", label="Country", field_type=FieldType.SELECT, required=True),
            FormField(id="resume", name="resume", label="Resume / CV", field_type=FieldType.FILE, required=True),
        ]

        return FormSchema(
            job_id=job_id,
            company_slug=slug,
            provider=ATSProvider.WORKDAY,
            fields=standard_fields,
            resume_required=True,
            cover_letter_required=False,
            custom_questions=[],
        )
