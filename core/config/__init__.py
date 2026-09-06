"""Configuration and candidate profile management."""

from core.config.loader import load_profile
from core.config.schema import (
    CandidateInfo,
    CandidateLinks,
    CandidateProfile,
    Disclosures,
    Education,
    ExperienceHighlight,
    ResumesConfig,
)

__all__ = [
    "load_profile",
    "CandidateProfile",
    "CandidateInfo",
    "CandidateLinks",
    "Disclosures",
    "Education",
    "ExperienceHighlight",
    "ResumesConfig",
]
