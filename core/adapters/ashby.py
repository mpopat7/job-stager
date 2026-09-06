"""Ashby browser staging adapter."""

from __future__ import annotations

import logging
from typing import Dict, Optional
from playwright.async_api import Page

from core.adapters.base import BaseStagingAdapter, StagingResult
from core.config.schema import CandidateProfile
from core.scrapers.base import ATSProvider

logger = logging.getLogger(__name__)


class AshbyAdapter(BaseStagingAdapter):
    """Playwright staging adapter for Ashby job application forms."""

    provider = ATSProvider.ASHBY

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

        apply_url = url if "application" in url else f"{url.rstrip('/')}/application"

        try:
            logger.info(f"Navigating to Ashby application: {apply_url}")
            await page.goto(apply_url, wait_until="domcontentloaded", timeout=30000)

            # Wait for dynamic Ashby components to hydrate
            await page.wait_for_timeout(2500)

            target_frame = page.main_frame
            for frame in page.frames:
                if "ashbyhq.com" in frame.url:
                    target_frame = frame
                    break

            # If no inputs visible, look for 'Apply' button
            inputs = await target_frame.query_selector_all("input")
            if len(inputs) == 0:
                apply_btn = await page.query_selector("a:has-text('Apply for this job'), a:has-text('Apply now'), a:has-text('Apply'), button:has-text('Apply')")
                if apply_btn:
                    await apply_btn.click()
                    await page.wait_for_timeout(2000)
                    for frame in page.frames:
                        if "ashbyhq.com" in frame.url:
                            target_frame = frame
                            break

            # Pre-fill all fields using intelligent label-aware filling & smooth scrolling
            fields_filled, resume_attached = await self.smart_fill_form(
                page=page,
                frame=target_frame,
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
                provider=ATSProvider.ASHBY,
                url=apply_url,
                fields_filled=fields_filled,
                resume_attached=resume_attached,
                grad_year=profile.education.graduation_year,
                message=f"Successfully staged Ashby application ({fields_filled} fields pre-filled).",
            )

        except Exception as e:
            logger.error(f"Failed to stage Ashby application: {e}")
            return StagingResult(
                success=False,
                provider=ATSProvider.ASHBY,
                url=apply_url,
                fields_filled=fields_filled,
                resume_attached=resume_attached,
                error=str(e),
            )
