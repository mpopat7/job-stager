"""Lever ATS scraper via public Postings API."""

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


class LeverScraper(BaseScraper):
    """Scrapes Lever job boards using the public REST API."""

    provider = ATSProvider.LEVER
    BASE_URL = "https://api.lever.co/v0/postings"

    def __init__(self, client: Optional[httpx.AsyncClient] = None):
        self._client = client

    async def _get_client(self) -> httpx.AsyncClient:
        if self._client and not self._client.is_closed:
            return self._client
        return httpx.AsyncClient(timeout=15.0, follow_redirects=True)

    async def fetch_jobs(self, slug: str) -> List[JobPosting]:
        """Fetch all job postings for a Lever company slug."""
        url = f"{self.BASE_URL}/{slug}?mode=json"
        client = await self._get_client()
        should_close = client != self._client

        try:
            response = await client.get(url)
            if response.status_code == 404:
                logger.warning(f"Lever board not found for slug: {slug}")
                return []
            response.raise_for_status()
            data = response.json()
        except Exception as e:
            logger.error(f"Error fetching Lever jobs for {slug}: {e}")
            return []
        finally:
            if should_close:
                await client.aclose()

        jobs: List[JobPosting] = []
        if not isinstance(data, list):
            return jobs

        for item in data:
            job_id = str(item.get("id"))
            title = item.get("text", "Unknown Title")
            categories = item.get("categories", {})
            location = categories.get("location") or "Remote / Unspecified"
            dept = categories.get("department") or categories.get("team")
            commitment = categories.get("commitment")
            hosted_url = item.get("hostedUrl") or f"https://jobs.lever.co/{slug}/{job_id}"
            apply_url = item.get("applyUrl") or f"{hosted_url}/apply"

            is_intern = (
                detect_is_internship(title, dept)
                or (commitment and "intern" in commitment.lower())
            )

            job = JobPosting(
                id=job_id,
                title=title,
                company=slug.replace("-", " ").title(),
                company_slug=slug,
                location=location,
                url=hosted_url,
                apply_url=apply_url,
                provider=ATSProvider.LEVER,
                department=dept,
                employment_type=commitment,
                is_internship=bool(is_intern),
                updated_at=str(item.get("createdAt")),
                description=item.get("descriptionPlain"),
                raw_payload=item,
            )
            jobs.append(job)

        return jobs

    async def fetch_form_schema(self, slug: str, job_id: str) -> FormSchema:
        """Fetch single Lever posting and extract custom screening questions."""
        url = f"{self.BASE_URL}/{slug}/{job_id}"
        client = await self._get_client()
        should_close = client != self._client

        try:
            response = await client.get(url)
            response.raise_for_status()
            data = response.json()
        except Exception as e:
            logger.error(f"Error fetching Lever posting details for {slug}/{job_id}: {e}")
            return FormSchema(job_id=job_id, company_slug=slug, provider=ATSProvider.LEVER)
        finally:
            if should_close:
                await client.aclose()

        # Standard Lever fields
        fields: List[FormField] = [
            FormField(id="name", name="name", label="Full Name", field_type=FieldType.TEXT, required=True),
            FormField(id="email", name="email", label="Email", field_type=FieldType.TEXT, required=True),
            FormField(id="phone", name="phone", label="Phone", field_type=FieldType.TEXT, required=False),
            FormField(id="org", name="org", label="Current Company / School", field_type=FieldType.TEXT, required=False),
            FormField(id="resume", name="resume", label="Resume/CV", field_type=FieldType.FILE, required=True),
            FormField(id="urls[LinkedIn]", name="urls[LinkedIn]", label="LinkedIn URL", field_type=FieldType.TEXT, required=False),
            FormField(id="urls[GitHub]", name="urls[GitHub]", label="GitHub URL", field_type=FieldType.TEXT, required=False),
            FormField(id="urls[Portfolio]", name="urls[Portfolio]", label="Portfolio URL", field_type=FieldType.TEXT, required=False),
            FormField(id="comments", name="comments", label="Additional Information", field_type=FieldType.TEXTAREA, required=False),
        ]

        custom_questions: List[FormField] = []
        raw_custom = data.get("customQuestions", [])
        for q in raw_custom:
            q_id = q.get("id") or q.get("text", "")
            q_text = q.get("text", "").strip()
            required = q.get("required", False)
            q_type = q.get("type", "text")
            options = []

            field_type = FieldType.TEXT
            if q_type == "dropdown" or q_type == "multiple-choice":
                field_type = FieldType.SELECT
                options = [opt.get("text", "") for opt in q.get("options", []) if isinstance(opt, dict)]
            elif q_type == "textarea" or q_type == "paragraph":
                field_type = FieldType.TEXTAREA
            elif q_type == "checkbox":
                field_type = FieldType.CHECKBOX

            q_field = FormField(
                id=q_id,
                name=f"customQuestion_{q_id}",
                label=q_text,
                field_type=field_type,
                required=required,
                options=options,
            )
            fields.append(q_field)
            custom_questions.append(q_field)

        return FormSchema(
            job_id=job_id,
            company_slug=slug,
            provider=ATSProvider.LEVER,
            fields=fields,
            resume_required=True,
            cover_letter_required=False,
            custom_questions=custom_questions,
        )
