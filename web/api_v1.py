"""The API the browser extension talks to.

The extension owns the page and this owns the answers. It sends a description of every
control it found; it gets back what to put in each one. Nothing here touches a browser,
which is the whole reason the split works: `core.solver.resolver` was already a pure
function of (question, kind, offered options), so the extension can be a thin content
script instead of a second copy of the fill logic.

A content script sees the whole form at once, unlike the Playwright adapter walking one
element at a time, so `offered` always arrives complete and every choice answer comes
back as one exact option string the extension can match without guessing.
"""

from __future__ import annotations

import logging
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

from core.config.grad_detector import detect_grad_year
from core.solver.questions import Kind
from core.solver.resolver import AnswerResolver, pick_option
from web.auth import current_user, profile_for

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/v1", tags=["extension"])

# A form with more controls than this is not a form; it is a page that got scraped wrong.
MAX_FIELDS = 400
# Enough of the posting to name a season and a graduation cohort, and no more.
MAX_PAGE_TEXT = 8000


class FieldDescriptor(BaseModel):
    """One control the extension found on the page."""

    ref: str = Field(description="The extension's own handle for this element.")
    kind: Kind = Kind.TEXT
    question: str = ""
    # Every option the control offers. A radio group is sent once with all its options,
    # not once per radio.
    offered: List[str] = Field(default_factory=list)
    required: bool = False
    max_length: Optional[int] = None


class ResolveRequest(BaseModel):
    url: str = ""
    # Posting text, used only to pick the graduation cohort when the user has more
    # than one resume on file.
    page_text: str = ""
    grad_year: Optional[int] = None
    fields: List[FieldDescriptor] = Field(default_factory=list)


class ResolvedAnswer(BaseModel):
    ref: str
    # What to type, or the exact offered option to choose. Null means nothing to fill.
    value: Optional[str] = None
    # Set only for a lone checkbox, which has no options to choose between.
    check: Optional[bool] = None
    key: Optional[str] = None
    source: str = "none"
    # False when the answer is a guess from the shape of the control rather than
    # something the profile actually says. The extension marks these for review.
    confident: bool = True
    # The profile has nothing for a field the user would expect to be filled.
    needs_attention: bool = False


class ResolveResponse(BaseModel):
    grad_year: int
    filled: int
    flagged: int
    answers: List[ResolvedAnswer]


@router.post("/resolve", response_model=ResolveResponse)
async def resolve_fields(
    req: ResolveRequest, user_id: int = Depends(current_user)
) -> ResolveResponse:
    """Answer every control on one application form."""
    if len(req.fields) > MAX_FIELDS:
        raise HTTPException(
            status_code=413, detail=f"Too many fields ({len(req.fields)}); limit {MAX_FIELDS}."
        )

    profile = profile_for(user_id)
    grad_year = req.grad_year or _cohort_for(profile, req)
    resolver = AnswerResolver(profile, grad_year=grad_year)

    answers: List[ResolvedAnswer] = []
    for f in req.fields:
        answers.append(_answer_field(resolver, f))

    return ResolveResponse(
        grad_year=grad_year,
        filled=sum(1 for a in answers if a.value is not None or a.check),
        flagged=sum(1 for a in answers if a.needs_attention or not a.confident),
        answers=answers,
    )


def _answer_field(resolver: AnswerResolver, f: FieldDescriptor) -> ResolvedAnswer:
    offered = [o for o in f.offered if o and o.strip()]
    ans = resolver.resolve(f.question, f.kind, offered=offered or None)

    out = ResolvedAnswer(
        ref=f.ref,
        key=ans.key.value if ans.key else None,
        source=ans.source,
        confident=ans.confident,
        needs_attention=ans.needs_attention,
    )

    if f.kind is Kind.TEXT:
        out.value = ans.text
        if out.value is None and _looks_open_ended(f):
            open_ans = resolver.open_response(f.question)
            out.value = open_ans.text
            if out.value is not None:
                out.source = open_ans.source
                out.confident = False
        return out

    if offered:
        out.value = pick_option(ans.candidates, offered) if ans else None
        if f.kind is Kind.CHECKBOX:
            out.check = out.value is not None
        return out

    # A checkbox on its own: the label is the question, and ticking it is the answer.
    if f.kind is Kind.CHECKBOX:
        out.check = bool(ans)
        return out

    # A combobox with no visible options until it is opened. The extension types the
    # best candidate and lets the widget filter.
    out.value = ans.text
    return out


def _looks_open_ended(f: FieldDescriptor) -> bool:
    """Whether an unanswered text box is an essay prompt rather than a missing fact."""
    return bool(f.max_length and f.max_length > 200) or len(f.question) > 60


def _cohort_for(profile, req: ResolveRequest) -> int:
    """Pick the graduation cohort this posting is hiring for."""
    available = profile.resumes.get_available_years()
    if len(available) <= 1:
        return profile.education.graduation_year
    text = f"{req.url}\n{req.page_text[:MAX_PAGE_TEXT]}"
    year, reason = detect_grad_year(
        text, available_years=available, default_year=profile.education.graduation_year
    )
    logger.info(f"Cohort {year} for {req.url or 'posting'} ({reason})")
    return year


@router.get("/resume")
async def get_resume(grad_year: Optional[int] = None, user_id: int = Depends(current_user)):
    """The resume matching a graduation cohort, for the extension to attach."""
    profile = profile_for(user_id)
    path = profile.resumes.resolve_resume(grad_year)
    if path is None or not path.exists():
        raise HTTPException(status_code=404, detail="No resume on file for that cohort.")
    return FileResponse(path, media_type="application/pdf", filename=path.name)


@router.get("/me")
async def whoami(user_id: int = Depends(current_user)):
    """What the extension needs to render its panel before a form is scanned."""
    profile = profile_for(user_id)
    return {
        "user_id": user_id,
        "name": profile.candidate.full_name,
        "cohorts": profile.resumes.get_available_years(),
        "default_cohort": profile.education.graduation_year,
    }
