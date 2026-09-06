"""Greenhouse ATS scraper via public Boards API."""

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


class GreenhouseScraper(BaseScraper):
    """Scrapes Greenhouse boards using the public REST API."""

    provider = ATSProvider.GREENHOUSE
    BASE_URL = "https://boards-api.greenhouse.io/v1/boards"

    def __init__(self, client: Optional[httpx.AsyncClient] = None):
        self._client = client

    async def _get_client(self) -> httpx.AsyncClient:
        if self._client and not self._client.is_closed:
            return self._client
        return httpx.AsyncClient(timeout=15.0, follow_redirects=True)

    async def fetch_jobs(self, slug: str) -> List[JobPosting]:
        """Fetch all public job postings for a Greenhouse company slug."""
        url = f"{self.BASE_URL}/{slug}/jobs?content=true"
        client = await self._get_client()
        should_close = client != self._client

        try:
            response = await client.get(url)
            if response.status_code == 404:
                logger.warning(f"Greenhouse board not found for slug: {slug}")
                return []
            response.raise_for_status()
            data = response.json()
        except Exception as e:
            logger.error(f"Error fetching Greenhouse jobs for {slug}: {e}")
            return []
        finally:
            if should_close:
                await client.aclose()

        jobs: List[JobPosting] = []
        raw_jobs = data.get("jobs", [])

        for item in raw_jobs:
            job_id = str(item.get("id"))
            title = item.get("title", "Unknown Title")
            location_data = item.get("location", {})
            location = location_data.get("name") if isinstance(location_data, dict) else str(location_data) or "Remote / Unspecified"
            
            departments = item.get("departments", [])
            dept_name = departments[0].get("name") if departments and isinstance(departments[0], dict) else None

            absolute_url = item.get("absolute_url") or f"https://boards.greenhouse.io/{slug}/jobs/{job_id}"
            
            job = JobPosting(
                id=job_id,
                title=title,
                company=slug.replace("-", " ").title(),
                company_slug=slug,
                location=location,
                url=absolute_url,
                apply_url=f"{absolute_url}#app",
                provider=ATSProvider.GREENHOUSE,
                department=dept_name,
                is_internship=detect_is_internship(title, dept_name),
                updated_at=item.get("updated_at"),
                description=item.get("content"),
                raw_payload=item,
            )
            jobs.append(job)

        return jobs

    async def fetch_form_schema(self, slug: str, job_id: str) -> FormSchema:
        """Fetch job application form questions and schema."""
        url = f"{self.BASE_URL}/{slug}/jobs/{job_id}?questions=true"
        client = await self._get_client()
        should_close = client != self._client

        try:
            response = await client.get(url)
            response.raise_for_status()
            data = response.json()
        except Exception as e:
            logger.error(f"Error fetching Greenhouse form schema for {slug}/{job_id}: {e}")
            return FormSchema(job_id=job_id, company_slug=slug, provider=ATSProvider.GREENHOUSE)
        finally:
            if should_close:
                await client.aclose()

        fields: List[FormField] = []
        custom_questions: List[FormField] = []
        resume_req = False
        cover_letter_req = False

        raw_questions = data.get("questions", [])
        for q in raw_questions:
            label = q.get("label", "").strip()
            required = q.get("required", False)
            q_fields = q.get("fields", [])

            for f in q_fields:
                field_name = f.get("name", "")
                field_type_raw = f.get("type", "input_text")

                field_type = FieldType.TEXT
                options: List[str] = []

                if field_type_raw == "input_file":
                    field_type = FieldType.FILE
                    if "resume" in field_name.lower():
                        resume_req = required
                    elif "cover" in field_name.lower():
                        cover_letter_req = required
                elif field_type_raw == "textarea":
                    field_type = FieldType.TEXTAREA
                elif field_type_raw == "input_select":
                    field_type = FieldType.SELECT
                    values = f.get("values", [])
                    options = [v.get("label", "") for v in values if isinstance(v, dict)]
                elif field_type_raw == "multi_value_single_select":
                    field_type = FieldType.SELECT
                    values = f.get("values", [])
                    options = [v.get("label", "") for v in values if isinstance(v, dict)]
                elif field_type_raw == "multi_value_multi_select":
                    field_type = FieldType.MULTISELECT
                    values = f.get("values", [])
                    options = [v.get("label", "") for v in values if isinstance(v, dict)]

                form_field = FormField(
                    id=str(f.get("name") or field_name),
                    name=field_name,
                    label=label or field_name,
                    field_type=field_type,
                    required=required,
                    options=options,
                    description=f.get("description") or "",
                )

                fields.append(form_field)
                if field_name.startswith("question_") or field_name.startswith("custom_"):
                    custom_questions.append(form_field)

        return FormSchema(
            job_id=job_id,
            company_slug=slug,
            provider=ATSProvider.GREENHOUSE,
            fields=fields,
            resume_required=resume_req,
            cover_letter_required=cover_letter_req,
            custom_questions=custom_questions,
        )
