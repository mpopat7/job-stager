"""Tests for truthful question solver and grounded answering."""

import pytest
from core.config.loader import load_profile
from core.scrapers.base import FieldType, FormField
from core.solver.llm import QuestionSolver


@pytest.mark.asyncio
async def test_solver_work_auth():
    profile = load_profile("profile.example.yaml")
    solver = QuestionSolver(profile)

    q = FormField(
        id="q1",
        name="work_auth",
        label="Are you legally authorized to work in the United States?",
        field_type=FieldType.SELECT,
        options=["Yes", "No"],
    )
    ans = await solver.solve(q)
    assert ans == "Yes"


@pytest.mark.asyncio
async def test_solver_sponsorship():
    profile = load_profile("profile.example.yaml")
    solver = QuestionSolver(profile)

    q = FormField(
        id="q2",
        name="sponsorship",
        label="Will you now or in the future require visa sponsorship?",
        field_type=FieldType.SELECT,
        options=["Yes", "No"],
    )
    ans = await solver.solve(q)
    assert ans == "No"


@pytest.mark.asyncio
async def test_solver_school_and_grad():
    profile = load_profile("profile.example.yaml")
    solver = QuestionSolver(profile)

    q_school = FormField(
        id="q3",
        name="school",
        label="What university or college do you currently attend?",
    )
    ans_school = await solver.solve(q_school)
    assert profile.education.school in ans_school

    q_grad = FormField(
        id="q4",
        name="grad_year",
        label="Expected graduation year / date?",
    )
    ans_grad = await solver.solve(q_grad)
    assert str(profile.education.graduation_year) in ans_grad
