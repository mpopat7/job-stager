"""Greenhouse browser staging adapter."""

from __future__ import annotations

import logging
from typing import Dict, Optional
from playwright.async_api import Page

from core.adapters.base import BaseStagingAdapter, StagingResult
from core.config.schema import CandidateProfile
from core.scrapers.base import ATSProvider

logger = logging.getLogger(__name__)


class GreenhouseAdapter(BaseStagingAdapter):
    """Playwright staging adapter for Greenhouse job application pages."""

    provider = ATSProvider.GREENHOUSE

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

        try:
            # If URL is a custom career site with slug and job_id, direct embed is often fastest and cleanest
            target_url = url
            logger.info(f"Navigating to Greenhouse application: {target_url}")
            await page.goto(target_url, wait_until="domcontentloaded", timeout=30000)

            # Wait a brief moment for dynamic React or iframes to initialize
            await page.wait_for_timeout(2000)

            # Locate the frame containing the application (main page or greenhouse iframe)
            target_frame = page.main_frame
            for frame in page.frames:
                if "greenhouse.io" in frame.url:
                    target_frame = frame
                    break

            # If on a custom domain with an "Apply" button, click it if no inputs found
            if len(await target_frame.query_selector_all("input")) == 0:
                apply_btn = await page.query_selector("a:has-text('Apply now'), a:has-text('Apply'), button:has-text('Apply')")
                if apply_btn:
                    await apply_btn.click()
                    await page.wait_for_timeout(2000)
                    for frame in page.frames:
                        if "greenhouse.io" in frame.url:
                            target_frame = frame
                            break

            # Pre-fill all fields using intelligent label-aware filling & smooth scrolling
            fields_filled, resume_attached = await self.smart_fill_form(
                page=page,
                frame=target_frame,
                profile=profile,
                answers=answers,
            )

            # Extract job title from page for banner
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
                provider=ATSProvider.GREENHOUSE,
                url=url,
                fields_filled=fields_filled,
                resume_attached=resume_attached,
                grad_year=profile.education.graduation_year,
                message=f"Successfully staged Greenhouse application ({fields_filled} fields pre-filled).",
            )

        except Exception as e:
            logger.error(f"Failed to stage Greenhouse application: {e}")
            return StagingResult(
                success=False,
                provider=ATSProvider.GREENHOUSE,
                url=url,
                fields_filled=fields_filled,
                resume_attached=resume_attached,
                error=str(e),
            )
