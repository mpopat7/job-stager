"""Unified data models and base class for ATS scrapers."""

from __future__ import annotations

from abc import ABC, abstractmethod
from datetime import datetime
from enum import Enum
import re
from typing import Any, Dict, List, Optional
from pydantic import BaseModel, Field


class ATSProvider(str, Enum):
    GREENHOUSE = "greenhouse"
    LEVER = "lever"
    ASHBY = "ashby"
    WORKDAY = "workday"
    WORKABLE = "workable"
    BAMBOOHR = "bamboohr"
    JAZZHR = "jazzhr"
    UNKNOWN = "unknown"


class FieldType(str, Enum):
    TEXT = "text"
    TEXTAREA = "textarea"
    SELECT = "select"
    RADIO = "radio"
    CHECKBOX = "checkbox"
    FILE = "file"
    MULTISELECT = "multiselect"
    DATE = "date"
    NUMBER = "number"


class FormField(BaseModel):
    id: str
    name: str
    label: str
    field_type: FieldType = FieldType.TEXT
    required: bool = False
    options: List[str] = Field(default_factory=list)
    description: str = ""
    value: Optional[str] = None


class FormSchema(BaseModel):
    job_id: str
    company_slug: str
    provider: ATSProvider
    fields: List[FormField] = Field(default_factory=list)
    resume_required: bool = True
    cover_letter_required: bool = False
    custom_questions: List[FormField] = Field(default_factory=list)


class JobPosting(BaseModel):
    id: str
    title: str
    company: str
    company_slug: str
    location: str = "Unknown"
    url: str
    apply_url: str
    provider: ATSProvider
    department: Optional[str] = None
    employment_type: Optional[str] = None
    is_internship: bool = False
    status: str = "discovered"
    stage: Optional[str] = None
    applied_date: Optional[str] = None
    notes: Optional[str] = None
    updated_at: Optional[str] = None
    discovered_at: Optional[str] = None
    description: Optional[str] = None
    raw_payload: Dict[str, Any] = Field(default_factory=dict)

    @property
    def post_date_display(self) -> str:
        """Format updated_at or discovered_at into a human-friendly string."""
        raw = self.updated_at or self.discovered_at
        if not raw:
            return "Recently"
        try:
            # ISO timestamp or YYYY-MM-DD
            clean = raw.split("T")[0].split(" ")[0].strip()
            parts = clean.split("-")
            if len(parts) == 3:
                dt = datetime(int(parts[0]), int(parts[1]), int(parts[2]))
                return dt.strftime("%b %-d, %Y")
        except Exception:
            pass
        return str(raw)[:10]

    def matches_keywords(self, keywords: List[str]) -> bool:
        """Check if title or department matches any of the given keywords."""
        if not keywords:
            return True
        text = f"{self.title} {self.department or ''}".lower()
        return any(k.lower() in text for k in keywords)


class CompanyBoard(BaseModel):
    company_name: str
    slug: str
    provider: ATSProvider
    board_url: str
    active: bool = True
    job_count: int = 0
    last_scanned: Optional[str] = None


INTERNSHIP_KEYWORDS = [
    "intern",
    "internship",
    "co-op",
    "coop",
    "fellow",
    "fellowship",
    "student",
    "undergraduate",
]


def detect_is_internship(title: str, department: Optional[str] = None) -> bool:
    """Detect whether a job posting is an internship based on title and department."""
    target = f"{title} {department or ''}".lower()
    return any(re.search(r"\b" + re.escape(kw) + r"\b", target) for kw in INTERNSHIP_KEYWORDS)


class BaseScraper(ABC):
    """Abstract base class for all ATS scrapers."""

    provider: ATSProvider = ATSProvider.UNKNOWN

    @abstractmethod
    async def fetch_jobs(self, slug: str) -> List[JobPosting]:
        """Fetch all current job postings for a company slug."""
        pass

    @abstractmethod
    async def fetch_form_schema(self, slug: str, job_id: str) -> FormSchema:
        """Fetch the application form schema and custom questions for a job."""
        pass
