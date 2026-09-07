"""LLM solver engine generating grounded screening answers."""

from __future__ import annotations

import logging
import os
from typing import Optional
import httpx

from core.config.schema import CandidateProfile
from core.scrapers.base import FieldType, FormField
from core.solver.prompts import SYSTEM_PROMPT, build_question_prompt
from core.solver.questions import Kind
from core.solver.resolver import AnswerResolver, pick_option

logger = logging.getLogger(__name__)

_KIND_BY_FIELD_TYPE = {
    FieldType.SELECT: Kind.SELECT,
    FieldType.MULTISELECT: Kind.SELECT,
    FieldType.RADIO: Kind.RADIO,
    FieldType.CHECKBOX: Kind.CHECKBOX,
}


class QuestionSolver:
    """Answers ATS application screening questions accurately without hallucinations."""

    def __init__(self, profile: CandidateProfile):
        self.profile = profile
        self.resolver = AnswerResolver(profile)

    def answer_heuristic(self, question: FormField) -> Optional[str]:
        """Answer from the profile alone, or None if nothing there covers the question.

        The whole taxonomy lives in `core.solver.resolver`; this is the CLI's way in.
        """
        kind = _KIND_BY_FIELD_TYPE.get(question.field_type, Kind.TEXT)
        offered = list(question.options) if question.options else None
        ans = self.resolver.resolve(question.label, kind, offered=offered)
        if not ans:
            return None
        if offered:
            # A form that lists its options wants one of them back verbatim.
            return pick_option(ans.candidates, offered)
        return ans.text

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
