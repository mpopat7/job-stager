"""Ashby ATS scraper via public Posting API."""

from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional
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


class AshbyScraper(BaseScraper):
    """Scrapes Ashby job boards using the public REST API."""

    provider = ATSProvider.ASHBY
    BASE_URL = "https://api.ashbyhq.com/posting-api/job-board"

    def __init__(self, client: Optional[httpx.AsyncClient] = None):
        self._client = client

    async def _get_client(self) -> httpx.AsyncClient:
        if self._client and not self._client.is_closed:
            return self._client
        return httpx.AsyncClient(timeout=15.0, follow_redirects=True)

    async def fetch_jobs(self, slug: str) -> List[JobPosting]:
        """Fetch all public job postings for an Ashby company slug."""
        url = f"{self.BASE_URL}/{slug}?includeCompensation=true"
        client = await self._get_client()
        should_close = client != self._client

        try:
            response = await client.get(url)
            if response.status_code == 404:
                logger.warning(f"Ashby board not found for slug: {slug}")
                return []
            response.raise_for_status()
            data = response.json()
        except Exception as e:
            logger.error(f"Error fetching Ashby jobs for {slug}: {e}")
            return []
        finally:
            if should_close:
                await client.aclose()

        jobs: List[JobPosting] = []
        raw_jobs = data.get("jobs", [])

        for item in raw_jobs:
            job_id = str(item.get("id"))
            title = item.get("title", "Unknown Title")
            location = item.get("location") or "Remote / Unspecified"
            dept = item.get("department") or item.get("team")
            emp_type = item.get("employmentType")
            job_url = item.get("jobUrl") or f"https://jobs.ashbyhq.com/{slug}/{job_id}"
            apply_url = f"{job_url}/application"

            is_intern = (
                detect_is_internship(title, dept)
                or (emp_type and "intern" in emp_type.lower())
            )

            job = JobPosting(
                id=job_id,
                title=title,
                company=slug.replace("-", " ").title(),
                company_slug=slug,
                location=location,
                url=job_url,
                apply_url=apply_url,
                provider=ATSProvider.ASHBY,
                department=dept,
                employment_type=emp_type,
                is_internship=bool(is_intern),
                updated_at=item.get("publishedAt"),
                description=item.get("descriptionHtml"),
                raw_payload=item,
            )
            jobs.append(job)

        return jobs

    async def fetch_form_schema(self, slug: str, job_id: str) -> FormSchema:
        """Fetch application form fields and questions for an Ashby job."""
        client = await self._get_client()
        should_close = client != self._client

        form_url = f"{self.BASE_URL}/{slug}/application-form"
        data = {}

        try:
            # Try POST first with json payload
            resp = await client.post(form_url, json={"jobPostingId": job_id})
            if resp.status_code == 200:
                data = resp.json()
            else:
                # Fallback to GET with query param
                resp = await client.get(form_url, params={"jobPostingId": job_id})
                if resp.status_code == 200:
                    data = resp.json()
        except Exception as e:
            logger.error(f"Error fetching Ashby form schema for {slug}/{job_id}: {e}")
        finally:
            if should_close:
                await client.aclose()

        fields: List[FormField] = []
        custom_questions: List[FormField] = []
        raw_sections = data.get("sections", [])

        # Default required Ashby fields if API doesn't return full schema
        if not raw_sections:
            return FormSchema(
                job_id=job_id,
                company_slug=slug,
                provider=ATSProvider.ASHBY,
                fields=[
                    FormField(id="name", name="name", label="Full Name", field_type=FieldType.TEXT, required=True),
                    FormField(id="email", name="email", label="Email", field_type=FieldType.TEXT, required=True),
                    FormField(id="phone", name="phone", label="Phone", field_type=FieldType.TEXT, required=False),
                    FormField(id="resume", name="resume", label="Resume", field_type=FieldType.FILE, required=True),
                ],
                resume_required=True,
            )

        for sec in raw_sections:
            for item in sec.get("fields", []):
                f_id = item.get("id") or item.get("path", "")
                f_title = item.get("title", "")
                f_type_str = item.get("type", "String").lower()
                is_req = item.get("isRequired", False)
                options: List[str] = []

                field_type = FieldType.TEXT
                if "file" in f_type_str:
                    field_type = FieldType.FILE
                elif "textarea" in f_type_str or "paragraph" in f_type_str:
                    field_type = FieldType.TEXTAREA
                elif "select" in f_type_str or "dropdown" in f_type_str:
                    field_type = FieldType.SELECT
                    options = [o.get("label", "") for o in item.get("options", []) if isinstance(o, dict)]
                elif "checkbox" in f_type_str:
                    field_type = FieldType.CHECKBOX

                field = FormField(
                    id=f_id,
                    name=item.get("path") or f_id,
                    label=f_title or f_id,
                    field_type=field_type,
                    required=is_req,
                    options=options,
                    description=item.get("description", ""),
                )
                fields.append(field)
                if not f_id.startswith("candidate."):
                    custom_questions.append(field)

        return FormSchema(
            job_id=job_id,
            company_slug=slug,
            provider=ATSProvider.ASHBY,
            fields=fields,
            resume_required=True,
            cover_letter_required=False,
            custom_questions=custom_questions,
        )
