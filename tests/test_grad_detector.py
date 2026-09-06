"""Tests for graduation cohort auto-detection from job posting content."""

import pytest
from core.config.grad_detector import detect_grad_year


def test_detect_explicit_grad_year():
    text = "Must have a graduation date of Spring 2029 or later."
    yr, reason = detect_grad_year(text, available_years=[2028, 2029], default_year=2028)
    assert yr == 2029
    assert "2029" in reason


def test_detect_class_of_cohort():
    text = "Summer 2026 Software Engineering Intern (Class of 2028)"
    yr, reason = detect_grad_year(text, available_years=[2027, 2028, 2029], default_year=2028)
    assert yr == 2028
    assert "2028" in reason


def test_detect_academic_level_freshman():
    text = "We are hiring first-year freshman students for our early discovery program."
    yr, reason = detect_grad_year(text, available_years=[2028, 2029], default_year=2028)
    assert yr == 2029
    assert "freshman" in reason


def test_detect_academic_level_junior():
    text = "Must be a rising senior or junior enrolled in a BS in Computer Science."
    yr, reason = detect_grad_year(text, available_years=[2027, 2028, 2029], default_year=2028)
    assert yr == 2027
    assert "junior" in reason


def test_detect_fallback_to_default():
    text = "Software Engineer Intern - Build scalable backend distributed microservices in Go and Python."
    yr, reason = detect_grad_year(text, available_years=[2028, 2029], default_year=2028)
    assert yr == 2028
    assert "default" in reason
