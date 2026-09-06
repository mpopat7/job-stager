"""Tests for candidate profile configuration and validation."""

import pytest
from pathlib import Path
from core.config.loader import load_profile
from core.config.schema import CandidateProfile


def test_load_profile_example():
    profile = load_profile("profile.example.yaml")
    assert profile.candidate.first_name == "Jordan"
    assert profile.candidate.last_name == "Rivera"
    assert profile.candidate.email == "jordan.rivera@example.com"
    assert profile.education.school == "Ohio State University"
    assert profile.education.graduation_year in [2028, 2029]
    assert profile.disclosures.us_authorized is True
    assert profile.disclosures.requires_sponsorship is False


def test_dual_grad_year_resume():
    profile = load_profile("profile.example.yaml")
    # Check that candidate profile has both 2028 and 2029 grad year paths configured
    assert profile.resumes.grad_2028 is not None
    assert profile.resumes.grad_2029 is not None
