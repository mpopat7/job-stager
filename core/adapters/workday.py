"""Workday browser staging adapter."""

from __future__ import annotations

import logging
from typing import Dict, Optional
from playwright.async_api import Page

from core.adapters.base import BaseStagingAdapter, StagingResult
from core.config.schema import CandidateProfile
from core.scrapers.base import ATSProvider

logger = logging.getLogger(__name__)


class WorkdayAdapter(BaseStagingAdapter):
    """Playwright staging adapter for Workday career portals.

    Workday often presents an 'Apply' -> 'Apply Manually' flow or requires login.
    This adapter brings the candidate directly to the pre-filled staging point.
    """

    provider = ATSProvider.WORKDAY

    async def stage(
        self,
        page: Page,
        url: str,
        profile: CandidateProfile,
        answers: Optional[Dict[str, str]] = None,
    ) -> StagingResult:
        fields_filled = 0
        resume_attached = False

        try:
            logger.info(f"Navigating to Workday job: {url}")
            await page.goto(url, wait_until="domcontentloaded", timeout=30000)

            # Look for "Apply" button if not already on the application screen
            apply_btn = await page.query_selector("a[data-automation-id='adventureButton'], a:has-text('Apply'), button:has-text('Apply')")
            if apply_btn:
                await apply_btn.click()
                await page.wait_for_timeout(1500)

                # Look for "Apply Manually" button if modal opened
                manual_btn = await page.query_selector("a[data-automation-id='applyManually'], button:has-text('Apply Manually')")
                if manual_btn:
                    await manual_btn.click()
                    await page.wait_for_timeout(2000)

            # Pre-fill all fields using intelligent label-aware filling & smooth scrolling
            filled, resume_done = await self.smart_fill_form(
                page=page,
                frame=page.main_frame,
                profile=profile,
                answers=answers,
            )
            fields_filled += filled
            if resume_done:
                resume_attached = True

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
                provider=ATSProvider.WORKDAY,
                url=url,
                fields_filled=fields_filled,
                resume_attached=resume_attached,
                grad_year=profile.education.graduation_year,
                message=f"Successfully staged Workday application ({fields_filled} fields pre-filled).",
            )

        except Exception as e:
            logger.error(f"Failed to stage Workday application: {e}")
            return StagingResult(
                success=False,
                provider=ATSProvider.WORKDAY,
                url=url,
                fields_filled=fields_filled,
                resume_attached=resume_attached,
                error=str(e),
            )
