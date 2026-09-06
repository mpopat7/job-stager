# JobStager — Project & Handoff Guide

## Project Summary
JobStager is a local automation agent that pre-fills ATS application forms (Greenhouse, Lever, Ashby, Workday), auto-detects graduation cohort requirements (2028 vs 2029) to attach the matching tailored resume, and opens a visible Playwright browser for review before submission.

## Environment & Commands
- **Python**: `python3` via `uv` (never bare `python`).
- **Run Web Server**: `uv run python3 -m web.server` (running on `127.0.0.1:8000`).
- **Run Tests**: `uv run pytest -v` (29 tests currently passing).
- **Run Scanner**: `python3 ~/dev/skills/all/unslop-ui/scripts/devibe_scan.py web/static/index.html` (currently 0 tells).
- **Git Policy**: Do not commit, push, or modify remote git state unless explicitly requested.

## Architecture
- `core/adapters/base.py`: Core form auto-filler (`smart_fill_form`, `fill_combobox`).
- `core/adapters/{greenhouse,ashby,lever,workday}.py`: ATS-specific adapters.
- `core/config/grad_detector.py`: Scans job posting text to select 2028 vs 2029 resume variant.
- `core/registry/`: SQLite company registry (`companies.db`) and job listings store.
- `web/static/index.html`: De-vibed minimalist dashboard with live terminal log drawer.

## Active Problem: Choice Buttons Not Being Pressed
On live applications, interactive choice buttons (e.g. Yes/No option pills in Ashby like `_option_1svni_32`, Workday single-select button groups, or segmented radio buttons) are intermittently failing to be selected.

### Where to Look
- In `core/adapters/base.py`, Pass A (`fill_current_pass`, lines ~180-335):
  - Uses `current_frame.evaluate` with `btn.click()` inside a JS loop.
  - **Issue**: Synthetic JS `.click()` inside `evaluate()` often fails to trigger React 18 / Ashby synthetic event listeners or state setters.
  - **Recommended Direction**:
    1. Query button elements as Playwright `Locator` or `ElementHandle` objects and use native `await btn.click(force=True)` or `await btn.dispatch_event('click')`.
    2. Expand container selector matching so questions with custom wrapper classes or sibling labels are properly captured.
    3. Test against live target URLs using a script like `scratch/test_button_choices.py`.
