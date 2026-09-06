"""Lever browser staging adapter."""

from __future__ import annotations

import logging
from typing import Dict, Optional
from playwright.async_api import Page

from core.adapters.base import BaseStagingAdapter, StagingResult
from core.config.schema import CandidateProfile
from core.scrapers.base import ATSProvider

logger = logging.getLogger(__name__)


class LeverAdapter(BaseStagingAdapter):
    """Playwright staging adapter for Lever job application pages."""

    provider = ATSProvider.LEVER

    async def stage(
        self,
        page: Page,
        url: str,
        profile: CandidateProfile,
        answers: Optional[Dict[str, str]] = None,
    ) -> StagingResult:
        answers = answers or {}
        fields_filled = 0
        resume_attached = False

        # Lever apply URL is usually {url}/apply
        apply_url = url if url.endswith("/apply") else f"{url.rstrip('/')}/apply"

        try:
            logger.info(f"Navigating to Lever application: {apply_url}")
            await page.goto(apply_url, wait_until="domcontentloaded", timeout=30000)

            # Wait for dynamic Lever components to render
            await page.wait_for_timeout(2000)

            # Pre-fill all fields using intelligent label-aware filling & smooth scrolling
            fields_filled, resume_attached = await self.smart_fill_form(
                page=page,
                frame=page.main_frame,
                profile=profile,
                answers=answers,
            )

            # Extract job title from page
            page_title = await page.title()
            job_title = page_title.split("|")[0].split("-")[0].strip() if page_title else "Internship"

            # Inject top review banner with on-site 'Mark as Applied' popup
            await self.inject_review_banner(
                page=page,
                fields_filled=fields_filled,
                resume_attached=resume_attached,
                company="Employer",
                role=job_title,
                grad_year=profile.education.graduation_year,
            )

            return StagingResult(
                success=True,
                provider=ATSProvider.LEVER,
                url=apply_url,
                fields_filled=fields_filled,
                resume_attached=resume_attached,
                grad_year=profile.education.graduation_year,
                message=f"Successfully staged Lever application ({fields_filled} fields pre-filled).",
            )

        except Exception as e:
            logger.error(f"Failed to stage Lever application: {e}")
            return StagingResult(
                success=False,
                provider=ATSProvider.LEVER,
                url=apply_url,
                fields_filled=fields_filled,
                resume_attached=resume_attached,
                error=str(e),
            )
