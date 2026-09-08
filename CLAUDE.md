# JobStager — Project & Handoff Guide

## Project Summary
JobStager is a local automation agent that pre-fills ATS application forms (Greenhouse, Lever, Ashby, Workday), auto-detects graduation cohort requirements (2028 vs 2029) to attach the matching tailored resume, and opens a visible Playwright browser for review before submission.

## Environment & Commands
- **Python**: `python3` via `uv` (never bare `python`).
- **Run Web Server**: `uv run python3 -m web.server` (running on `127.0.0.1:8000`).
- **Run Tests**: `uv run pytest -q`.
- **Run Scanner**: `python3 ~/dev/skills/all/unslop-ui/scripts/devibe_scan.py web/static/index.html` (currently 0 tells).
- **Git Policy**: Do not commit, push, or modify remote git state unless explicitly requested.

## Architecture
- `core/solver/questions.py`: `classify(text, kind)` — the single table naming what a form
  question is asking. Pattern order is behaviour, and each pattern declares which control
  kinds it may fire on.
- `core/solver/resolver.py`: `AnswerResolver.resolve(question, kind, offered=None)` — the
  single place a classified question becomes an answer, plus `pick_option`, which matches a
  ranked answer against the wording a form actually offers.
- `core/adapters/base.py`: Core form auto-filler (`smart_fill_form`, `fill_combobox`). Each
  control type applies what the resolver returned; none of them decide what to fill.
- `core/adapters/{greenhouse,ashby,lever,workday}.py`: ATS-specific adapters.
- `core/config/grad_detector.py`: Scans job posting text to select 2028 vs 2029 resume variant.
- `web/api_v1.py`: The API the browser extension talks to — it sends the controls it found,
  this returns what to put in each one. Nothing in it touches a browser.
- `core/store/db.py`: SQLAlchemy schema for both installs. `DATABASE_URL` unset means the
  local SQLite file; a deployment points it at Postgres and nothing else changes.
- `core/store/crypto.py`: Encrypts EEO self-identification at rest (`JOBSTAGER_SECRET_KEY`).
- `core/registry/`: SQLite company registry (`companies.db`) and job listings store.
- `core/tracker/local_db.py`: the primary, per-user in-app application tracker.
- `core/tracker/matches.py`: private matches between discovered jobs and application history.
  Confirmed Sheet matches are hidden from Jobs; possible matches remain visible with a warning.
- `core/tracker/sheets.py`: optional Google Sheets master-list integration. Sheet-only rows never
  enter the in-app Tracker tab; JobStager applications are mirrored when sync is enabled.
- `web/static/index.html`: De-vibed minimalist dashboard with live terminal log drawer.

## Working on the fill layer
A new question type is two edits, both in `core/solver/`: a pattern in `PATTERNS` and a branch
in `AnswerResolver._answer_for`. Nothing in `core/adapters/` should grow a question rule — that
duplication is what Phase 3 removed.

`tests/test_answers.py` runs without a browser and is the fast way to check a taxonomy change;
`tests/test_adapters.py` drives real DOM through Playwright and is the slow way to check that
the answer reaches the page.

Choice buttons commit for real via `native_click` (a synthetic JS `.click()` inside `evaluate()`
does not move React 18 state) — see `test_choice_controls_commit_state_in_tracked_forms`, which
fails if that regresses.

## Personal install vs shared deployment
`JOBSTAGER_MULTI_TENANT=1` says strangers share this process. It turns off three things that
are conveniences on one person's laptop and data leaks anywhere else: the `profile.yaml`
fallback in `profile_for`, seeding the first account from that file, and serving `/api/stage`
(which opens a browser on the machine running the server). With it set, startup also refuses
to boot without `JOBSTAGER_SECRET_KEY` and `JOBSTAGER_SECURE_COOKIES=1`.
