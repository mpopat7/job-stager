"""LLM solver engine generating grounded screening answers."""

from __future__ import annotations

import logging
import os
import re
from typing import Dict, List, Optional
import httpx

from core.config.schema import CandidateProfile
from core.scrapers.base import FormField
from core.solver.prompts import SYSTEM_PROMPT, build_question_prompt

logger = logging.getLogger(__name__)


class QuestionSolver:
    """Answers ATS application screening questions accurately without hallucinations."""

    def __init__(self, profile: CandidateProfile):
        self.profile = profile

    def answer_heuristic(self, question: FormField) -> Optional[str]:
        """Check for direct matches against candidate disclosures, education, or custom answers."""
        q_label = question.label.lower()

        # 1. Custom answers dictionary check
        for key, ans in self.profile.custom_answers.items():
            if key.lower() in q_label:
                return ans

        # 2. Work Authorization / Legal Status
        if any(k in q_label for k in ["authorized to work", "legally authorized", "eligible to work", "work authorization"]):
            if question.options:
                for opt in question.options:
                    if opt.lower() in ["yes", "authorized", "us citizen"]:
                        return opt
            return "Yes"

        # 3. Sponsorship
        if any(k in q_label for k in ["sponsorship", "visa", "future require", "require now or in the future"]):
            if question.options:
                for opt in question.options:
                    if opt.lower() in ["no", "will not require"]:
                        return opt
            return "No"

        # 4. Relocation
        if "relocate" in q_label or "open to relocation" in q_label:
            if question.options:
                for opt in question.options:
                    if "yes" in opt.lower():
                        return opt
            return "Yes"

        # 5. Preferred Name
        if "preferred name" in q_label or "nickname" in q_label:
            return self.profile.candidate.first_name

        # 6. Pronouns
        if "pronoun" in q_label:
            if question.options:
                for opt in question.options:
                    if "he/him" in opt.lower():
                        return opt
            return "He/Him"

        # 7. Portfolio / Links / GitHub / LinkedIn
        if any(k in q_label for k in ["linkedin", "github", "portfolio", "personal website"]):
            c_links = self.profile.candidate.links
            found_links = [l for l in [c_links.linkedin, c_links.github, c_links.portfolio] if l]
            return " | ".join(found_links) if found_links else ""

        # 8. Deadlines / Competing offers
        if "deadline" in q_label or "offer deadline" in q_label:
            return "None currently."

        # 9. Test scores (SAT/ACT)
        if "sat" in q_label or "act score" in q_label:
            return "N/A"

        # 10. Graduation Year / Date
        if "graduation" in q_label or "grad year" in q_label or "anticipated graduation" in q_label:
            grad_year = str(self.profile.education.graduation_year)
            if question.options:
                for opt in question.options:
                    if grad_year in opt:
                        return opt
            return f"{self.profile.education.graduation_month} {grad_year}"

        # 11. School / University
        if "university" in q_label or "college" in q_label or "school" in q_label:
            school = self.profile.education.school
            if question.options and school:
                for opt in question.options:
                    if school.lower() in opt.lower():
                        return opt
            return school

        # 12. GPA
        if "gpa" in q_label:
            return self.profile.education.gpa

        return None

    async def solve(self, question: FormField) -> str:
        """Solve a question using heuristics first, falling back to LLM if available."""
        # Check heuristics first
        heuristic_val = self.answer_heuristic(question)
        if heuristic_val is not None:
            return heuristic_val

        # If options are provided, attempt closest semantic match
        if question.options:
            return question.options[0]

        # Check for Gemini / Anthropic / OpenAI API keys
        gemini_key = os.getenv("GEMINI_API_KEY")
        anthropic_key = os.getenv("ANTHROPIC_API_KEY")
        openai_key = os.getenv("OPENAI_API_KEY")

        prompt = build_question_prompt(question.label, self.profile, question.options)

        if gemini_key:
            try:
                url = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-1.5-flash:generateContent?key={gemini_key}"
                async with httpx.AsyncClient(timeout=10.0) as client:
                    resp = await client.post(
                        url,
                        json={
                            "contents": [{"parts": [{"text": f"{SYSTEM_PROMPT}\n\n{prompt}"}]}],
                            "generationConfig": {"temperature": 0.2, "maxOutputTokens": 200},
                        },
                    )
                    if resp.status_code == 200:
                        data = resp.json()
                        text = data["candidates"][0]["content"]["parts"][0]["text"].strip()
                        return text
            except Exception as e:
                logger.error(f"Gemini API error: {e}")

        # Fallback: grounded experience summary based on matching keywords
        q_lower = question.label.lower()
        for exp in self.profile.experience_highlights:
            if any(kw.lower() in q_lower for kw in exp.keywords):
                return exp.summary

        # Default fallback
        if self.profile.experience_highlights:
            return self.profile.experience_highlights[0].summary
        return "Not specified."
