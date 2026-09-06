"""Loader and validator for candidate profile YAML configurations."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Optional
import yaml

from core.config.schema import CandidateProfile


DEFAULT_SEARCH_PATHS = [
    Path("profile.yaml"),
    Path.home() / ".config" / "job-stager" / "profile.yaml",
    Path(__file__).parent.parent.parent / "profile.example.yaml",
]


def load_profile(path: Optional[Path | str] = None) -> CandidateProfile:
    """Load a candidate profile from the specified path or standard search locations.

    Falls back to profile.example.yaml if no personal profile is found.
    """
    candidate_path: Optional[Path] = None

    if path:
        p = Path(path).expanduser().resolve()
        if not p.exists():
            raise FileNotFoundError(f"Profile not found at specified path: {path}")
        candidate_path = p
    else:
        for p in DEFAULT_SEARCH_PATHS:
            if p.exists():
                candidate_path = p.resolve()
                break

    if not candidate_path:
        raise FileNotFoundError(
            "No profile.yaml found in current directory, ~/.config/job-stager/profile.yaml, "
            "or repository root."
        )

    with open(candidate_path, "r", encoding="utf-8") as f:
        data = yaml.safe_load(f)

    if not isinstance(data, dict):
        raise ValueError(f"Invalid YAML content in {candidate_path}: expected dictionary root")

    return CandidateProfile.model_validate(data)


def save_profile(profile: CandidateProfile, path: Optional[Path | str] = None) -> Path:
    """Save candidate profile to profile.yaml or specified path."""
    dest = Path(path).expanduser().resolve() if path else Path("profile.yaml").resolve()
    dest.parent.mkdir(parents=True, exist_ok=True)
    data = profile.model_dump(mode="python")
    with open(dest, "w", encoding="utf-8") as f:
        yaml.safe_dump(data, f, sort_keys=False, default_flow_style=False)
    return dest
