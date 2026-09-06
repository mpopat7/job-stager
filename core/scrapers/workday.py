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


class WorkdayScraper(BaseScraper):
    """Scrapes modern Workday portals via their CXS (Candidate Experience Service) REST API."""

    provider = ATSProvider.WORKDAY

    def __init__(self, client: Optional[httpx.AsyncClient] = None):
        self._client = client

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
        """Extract host, tenant, portal, and optional externalPath from a Workday URL.

        Example:
            https://nvidia.wd5.myworkdayjobs.com/en-US/NVIDIAExternalCareerSite/job/US-CA-Santa-Clara/Intern_JR123
        Returns:
            {"host": "nvidia.wd5.myworkdayjobs.com", "tenant": "nvidia", "portal": "NVIDIAExternalCareerSite", "job_path": ...}
        """
        parsed = urlparse(url)
        host = parsed.netloc
        if "myworkdayjobs.com" not in host:
            return None

        # host usually format: {tenant}.wd{n}.myworkdayjobs.com
        tenant_match = re.match(r"^([^.]+)", host)
        tenant = tenant_match.group(1) if tenant_match else ""

        # path format usually: /{locale}/{portal}/job/{location}/{req_title}_{req_id}
        parts = [p for p in parsed.path.strip("/").split("/") if p]
        portal = ""
        job_path = ""

        if len(parts) >= 2:
            # Check if part 0 is locale e.g. en-US
            if "-" in parts[0] or parts[0].lower() in ["en", "fr", "de", "es"]:
                portal = parts[1]
                if len(parts) > 2 and parts[2] == "job":
                    job_path = "/" + "/".join(parts[2:])
            else:
                portal = parts[0]
                if len(parts) > 1 and parts[1] == "job":
                    job_path = "/" + "/".join(parts[1:])

        return {
            "host": host,
            "tenant": tenant,
            "portal": portal or "default",
            "job_path": job_path,
        }

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

        jobs: List[JobPosting] = []
        payload = {
            "appliedFacets": {},
            "limit": 50,
            "offset": 0,
            "searchText": "",
        }

        try:
            resp = await client.post(endpoint, json=payload)
            if resp.status_code == 404:
                # Try without tenant prefix if tenant matches host
                logger.warning(f"Workday CXS returned 404 on {endpoint}")
                return []
            resp.raise_for_status()
            data = resp.json()
        except Exception as e:
            logger.error(f"Error requesting Workday CXS at {endpoint}: {e}")
            return []
        finally:
            if should_close:
                await client.aclose()

        postings = data.get("jobPostings", [])
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

            full_url = f"https://{host}/en-US/{portal}/job{ext_path}"
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
