"""Anti-fluff, strictly grounded prompts for ATS screening questions."""

from __future__ import annotations

import json
from core.config.schema import CandidateProfile

SYSTEM_PROMPT = """You are JobStager's truthful screening answer engine for job applications.
Your job is to answer short-answer screening questions strictly grounded in the candidate's verified profile.

STRICT RULES:
1. TRUTHFULNESS: Only use facts explicitly provided in the candidate's profile. NEVER invent skills, degrees, past employers, projects, metrics, or personal details.
2. NO AI FLUFF: Never use conversational openers, throat-clearing, or buzzwords like:
   - "As a passionate and detail-oriented student..."
   - "I am thrilled to apply because..."
   - "In today's fast-paced environment..."
3. CONCISION: Provide clear, direct, and compact answers (1 to 3 sentences maximum for text answers, or a single direct choice for dropdowns/yes-no).
4. LEGAL & SPONSORSHIP: Adhere strictly to the candidate's disclosed work authorization (e.g., US citizen, no visa sponsorship required).
"""


def format_candidate_context(profile: CandidateProfile) -> str:
    """Format candidate profile into concise context string for the prompt."""
    highlights_text = "\n".join(
        f"- {h.topic}: {h.summary}" for h in profile.experience_highlights
    )

    return f"""CANDIDATE GROUND TRUTH:
Name: {profile.candidate.full_name}
Email: {profile.candidate.email}
Phone: {profile.candidate.phone}
Location: {profile.candidate.location}
Education: {profile.education.school}, {profile.education.degree} (Minor: {profile.education.minor or 'None'}), Graduating: {profile.education.graduation_month} {profile.education.graduation_year}, GPA: {profile.education.gpa}
Work Authorization: {profile.disclosures.work_authorization} (Requires sponsorship: {profile.disclosures.requires_sponsorship})
Open to relocation: {profile.disclosures.open_to_relocation}

Verified Experience Highlights:
{highlights_text}
"""


def build_question_prompt(question: str, profile: CandidateProfile, options: list[str] | None = None) -> str:
    """Build the prompt for answering a specific application question."""
    context = format_candidate_context(profile)
    options_text = f"\nAvailable Options (pick the exact best match):\n" + "\n".join(f"- {o}" for o in options) if options else ""

    return f"""{context}
APPLICATION QUESTION:
"{question}"
{options_text}

Provide only the direct answer text. Do not wrap in quotes or add commentary.
"""
