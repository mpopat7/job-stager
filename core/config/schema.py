"""User profile and candidate ground-truth Pydantic models.

Enforces strict schemas for candidate personal info, work authorizations,
education, resume paths, and experience highlights to prevent AI hallucinations.
"""

from __future__ import annotations

from datetime import date
from pathlib import Path
import re
from typing import Any, ClassVar, Dict, List, Optional
from pydantic import BaseModel, Field


class CandidateLinks(BaseModel):
    linkedin: Optional[str] = None
    github: Optional[str] = None
    portfolio: Optional[str] = None
    twitter: Optional[str] = None
    other: Dict[str, str] = Field(default_factory=dict)


class CandidateInfo(BaseModel):
    first_name: str
    last_name: str
    email: str
    phone: str
    location: str  # e.g. "Bloomington, IN"
    links: CandidateLinks = Field(default_factory=CandidateLinks)

    @property
    def full_name(self) -> str:
        return f"{self.first_name} {self.last_name}"


class Disclosures(BaseModel):
    work_authorization: str = "US Citizen"
    requires_sponsorship: bool = False
    requires_future_sponsorship: bool = False
    gender: Optional[str] = None
    race_ethnicity: Optional[str] = None
    veteran_status: Optional[str] = None
    disability_status: Optional[str] = None
    us_authorized: bool = True
    canada_authorized: bool = False
    open_to_relocation: bool = True


class Address(BaseModel):
    street: Optional[str] = None
    city: Optional[str] = None
    state: Optional[str] = None
    postal_code: Optional[str] = None
    country: str = "United States"

    def one_line(self) -> str:
        parts = [p for p in (self.city, self.state) if p]
        return ", ".join(parts)


class Availability(BaseModel):
    """When the candidate can actually work."""

    start_date: Optional[str] = None       # "2026-05-18"
    end_date: Optional[str] = None         # "2026-08-14"
    term_label: Optional[str] = None       # "Summer 2026 (4-month)"
    full_time: bool = True
    notice_period: Optional[str] = None

    def formatted(self, value: Optional[str], style: str = "long") -> Optional[str]:
        """Render an ISO date the way an application asks for it."""
        if not value:
            return None
        try:
            d = date.fromisoformat(value)
        except ValueError:
            return value
        if style == "numeric":
            return d.strftime("%m/%d/%Y")
        if style == "month_year":
            return d.strftime("%B %Y")
        return f"{d.strftime('%B')} {d.day}, {d.year}"


class Compensation(BaseModel):
    hourly_rate: Optional[str] = None      # "50"
    salary_expectation: Optional[str] = None
    note: str = "Negotiable"


class Preferences(BaseModel):
    """What the candidate wants, as opposed to what is true about them.

    Ranked lists are matched against the options a form actually offers, so a posting
    that only lists Remote and Onsite still gets an answer without a rule per site.
    """

    availability: Availability = Field(default_factory=Availability)
    address: Address = Field(default_factory=Address)
    # Best-first. The first entry the form offers wins.
    work_model_ranked: List[str] = Field(default_factory=lambda: ["Hybrid", "Onsite", "Remote"])
    locations_ranked: List[str] = Field(default_factory=list)
    compensation: Compensation = Field(default_factory=Compensation)
    pronouns: Optional[str] = None
    current_title: Optional[str] = None
    referral_source: str = "LinkedIn"

    @staticmethod
    def _norm(value: str) -> str:
        """Fold case and punctuation so "on-site" and "Onsite" compare equal."""
        return re.sub(r"[^a-z0-9]", "", (value or "").lower())

    # Postings say the same three things a dozen ways.
    SYNONYMS: ClassVar[Dict[str, List[str]]] = {
        "remote": ["remote", "work from home", "wfh", "fully distributed", "anywhere", "virtual"],
        "onsite": ["onsite", "on site", "in person", "in office", "in the office", "office based"],
        "hybrid": ["hybrid", "flexible", "partially remote", "split", "mix of remote and office"],
    }

    def _expand(self, want: str) -> List[str]:
        w = self._norm(want)
        for key, words in self.SYNONYMS.items():
            if w == self._norm(key) or any(w == self._norm(x) for x in words):
                return words
        return [want]

    def location_queries(self) -> List[str]:
        """Queries to try against a geo autocomplete, narrowest first.

        Pickers disagree on which phrasing they echo back: some resolve "City, ST",
        others only the bare city, so the fallback chain matters more than any one string.
        """
        a = self.address
        out: List[str] = []
        if a.city and a.state:
            out.append(f"{a.city}, {a.state}")
        if a.city:
            out.append(a.city)
        for extra in self.locations_ranked:
            if extra and extra not in out:
                out.append(extra)
        if a.country and a.country not in out:
            out.append(a.country)
        return out

    def rank_pick(self, ranked: List[str], offered: List[str]) -> Optional[str]:
        """Return the highest-ranked preference that appears among the offered options.

        Exact matches beat partial ones across the whole option list, so a lower-ranked
        option cannot win just by appearing earlier on the page.
        """
        normed = [(o, self._norm(o)) for o in offered]
        for want in ranked:
            forms = [self._norm(f) for f in self._expand(want) if self._norm(f)]
            if not forms:
                continue
            for original, have in normed:
                if have in forms:
                    return original
            for original, have in normed:
                if any(f in have or have in f for f in forms):
                    return original
        return None


class Education(BaseModel):
    school: str = ""
    degree: str = ""
    major: str = ""
    minor: Optional[str] = None
    graduation_year: int = 0
    graduation_month: str = ""
    gpa: Optional[str] = None


class ResumesConfig(BaseModel):
    default: Optional[str] = None
    grad_2028: Optional[str] = None
    grad_2029: Optional[str] = None
    variants: Dict[str, str] = Field(default_factory=dict)
    active_cohorts: List[int] = Field(default_factory=lambda: [2028, 2029])

    def get_available_years(self) -> List[int]:
        """Returns sorted list of graduation years with configured resumes."""
        years = set()
        if self.grad_2028:
            years.add(2028)
        if self.grad_2029:
            years.add(2029)
        for k in self.variants.keys():
            try:
                years.add(int(k))
            except ValueError:
                pass
        for c in self.active_cohorts:
            years.add(int(c))
        return sorted(list(years)) if years else [2028, 2029]

    def resolve_resume(self, grad_year: Optional[int] = None) -> Optional[Path]:
        """Resolves the appropriate resume PDF path."""
        target = None
        if grad_year:
            year_str = str(grad_year)
            if year_str in self.variants and self.variants[year_str]:
                target = self.variants[year_str]
            elif grad_year == 2028 and self.grad_2028:
                target = self.grad_2028
            elif grad_year == 2029 and self.grad_2029:
                target = self.grad_2029

        if not target:
            target = (
                self.default
                or self.variants.get("2028")
                or self.variants.get("2029")
                or self.grad_2028
                or self.grad_2029
            )
            if not target and self.variants:
                target = next(iter(self.variants.values()))

        if target:
            path = Path(target).expanduser().resolve()
            if path.exists():
                return path
        return None


class ExperienceHighlight(BaseModel):
    topic: str
    summary: str
    keywords: List[str] = Field(default_factory=list)


class TrackerConfig(BaseModel):
    spreadsheet_id: str = ""
    sheet_tab: str = "Applications"
    credentials_path: str = "~/.config/gcp/sheets-bot.json"
    auto_sync_sheets: bool = True


class CandidateProfile(BaseModel):
    candidate: CandidateInfo
    disclosures: Disclosures = Field(default_factory=Disclosures)
    education: Education = Field(default_factory=Education)
    resumes: ResumesConfig = Field(default_factory=ResumesConfig)
    preferences: Preferences = Field(default_factory=Preferences)
    tracker: TrackerConfig = Field(default_factory=TrackerConfig)
    experience_highlights: List[ExperienceHighlight] = Field(default_factory=list)
    custom_answers: Dict[str, str] = Field(default_factory=dict)
