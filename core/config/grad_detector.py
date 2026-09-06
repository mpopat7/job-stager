"""Intelligent graduation year detection and cohort matching from job postings."""

from __future__ import annotations

import re
from typing import List, Optional, Tuple


def detect_grad_year(
    text: str,
    available_years: Optional[List[int]] = None,
    default_year: int = 2028,
) -> Tuple[int, str]:
    """Analyze job posting text and match against candidate available graduation years.

    Returns:
        (chosen_grad_year, reason_string)
    """
    available = available_years or [2028, 2029]
    if not text:
        return default_year, f"default ({default_year})"

    text_lower = text.lower()

    # 1. Look for explicit graduation phrasing
    # Matches: "graduating in 2029", "class of 2028", "expected graduation May 2029", "grad date 2028"
    grad_phrases = re.findall(
        r"(?:graduat(?:ing|ion|e|es)|class of|expected graduation|cohort|degree completion|anticipate(?:d)? graduation)[^.\n\r]{0,60}?\b(202[4-9]|203[0-5])\b",
        text_lower,
    )

    matched_from_phrases = []
    for yr_str in grad_phrases:
        try:
            yr = int(yr_str)
            if yr in available and yr not in matched_from_phrases:
                matched_from_phrases.append(yr)
        except ValueError:
            pass

    if matched_from_phrases:
        chosen = default_year if default_year in matched_from_phrases else matched_from_phrases[0]
        return chosen, f"matched graduation requirement: Class of {chosen}"

    # 2. Look for academic cohort level (for Summer 2026 internships)
    if any(k in text_lower for k in ["freshman", "freshmen", "first-year", "1st year", "first year"]):
        if 2029 in available:
            return 2029, "matched 'freshman / first-year' cohort requirement -> 2029"
    if any(k in text_lower for k in ["sophomore", "sophomores", "second-year", "2nd year", "second year"]):
        if 2028 in available:
            return 2028, "matched 'sophomore' cohort requirement -> 2028"
    if any(k in text_lower for k in ["junior", "juniors", "rising senior", "third-year", "3rd year"]):
        if 2027 in available:
            return 2027, "matched 'junior' cohort requirement -> 2027"

    # 3. Look for standalone mentions of available years in the text
    standalone = []
    for yr in available:
        if re.search(rf"\b{yr}\b", text_lower):
            standalone.append(yr)

    if len(standalone) == 1:
        return standalone[0], f"matched single cohort reference: {standalone[0]}"
    elif len(standalone) > 1 and default_year in standalone:
        return default_year, f"multiple cohorts matched ({standalone}), using primary {default_year}"

    return default_year, f"default ({default_year})"
