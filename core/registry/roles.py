"""Classify a posting title into a role family.

Titles are the only field every board fills in consistently, and the vocabulary is small
enough that matching beats guessing. Order matters: the first family whose pattern hits
wins, so the more specific families are checked before the general engineering one.
"""

from __future__ import annotations

import re
from typing import Dict, List, Optional

ROLE_FAMILIES: List[tuple[str, str]] = [
    ("Quant", r"\b(quant|quantitative|trading|trader|systematic)\b"),
    ("Machine Learning", r"\b(machine learning|ml engineer|ml intern|deep learning|nlp|computer vision|ai research|research scientist)\b"),
    ("Data Science", r"\b(data scien\w*|data analy\w*|analytics|business intelligence|\bbi\b|statistician)\b"),
    ("Data Engineering", r"\b(data engineer|analytics engineer|etl|data platform|data infrastructure)\b"),
    ("Security", r"\b(security|appsec|infosec|cryptograph\w*|penetration)\b"),
    ("Infrastructure", r"\b(devops|site reliability|\bsre\b|platform engineer|infrastructure|cloud engineer)\b"),
    ("Mobile", r"\b(ios|android|mobile engineer|react native|flutter)\b"),
    ("Frontend", r"\b(front[\s-]?end|ui engineer|web developer|javascript engineer)\b"),
    ("Backend", r"\b(back[\s-]?end|server engineer|api engineer|distributed systems)\b"),
    ("Hardware", r"\b(hardware|embedded|firmware|fpga|asic|electrical)\b"),
    ("Product", r"\b(product manager|product management|\bpm\b|program manager|technical program)\b"),
    ("Design", r"\b(design\w*|\bux\b|user experience)\b"),
    ("Finance", r"\b(investment|banking|financ\w*|accounting|audit\w*)\b"),
    ("Consulting", r"\b(consult\w*|strategy|advisory)\b"),
    ("Software Engineering", r"\b(software|swe|engineer|engineering|developer|programmer|full[\s-]?stack)\b"),
]

OTHER = "Other"


def classify_role(title: Optional[str]) -> str:
    """Return the role family for a posting title, or "Other"."""
    text = (title or "").lower()
    if not text.strip():
        return OTHER
    for family, pattern in ROLE_FAMILIES:
        if re.search(pattern, text):
            return family
    return OTHER


def role_counts(titles: List[Optional[str]]) -> Dict[str, int]:
    """Count postings per family, for populating a filter without a second query."""
    counts: Dict[str, int] = {}
    for t in titles:
        counts[classify_role(t)] = counts.get(classify_role(t), 0) + 1
    return dict(sorted(counts.items(), key=lambda kv: (-kv[1], kv[0])))


def all_families() -> List[str]:
    return [f for f, _ in ROLE_FAMILIES] + [OTHER]
