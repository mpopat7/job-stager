# AGENTS.md — JobStager

Project guide and instructions for AI agents working on **JobStager**.

## Project Summary
JobStager is an intelligent job application staging agent that automates the tedious parts of applying to jobs across Applicant Tracking Systems (Greenhouse, Lever, Ashby, Workday, etc.). Instead of a blind cloud auto-applier (like Jobbie.bot), JobStager pre-fills the form with AI-tailored answers, pops open the completed page for a 5-second human review before submission, and automatically logs the application to the configured Google Sheets tracker.

## Key Boundaries & Rules
- This repository is located in `~/dev/projects/personal/job-stager/` (public portfolio code).
- Always use `python3`, never bare `python`.
- Use `uv` for Python package management when applicable.
- Never add `Co-Authored-By` or generated-by-agent footers to commits.
- Do not commit or push to remote unless explicitly asked by Milen.

## Integrations
- **Tracker**: the Google Sheets tracker id and credentials path come from `tracker:` in `profile.yaml` (or `JOBSTAGER_SPREADSHEET_ID`). Never hardcode either.
- **Resumes**: `resumes:` in `profile.yaml` may hold one PDF per graduation-year variant; the adapter picks by the posting's detected cohort.
- **Skills**: Integrates with `/log-app`, `/rtail`, and `/cover-letter`.

## Architecture Overview
1. `core/`:
   - `config/`: Profile parsing and candidate ground-truth validation (`profile.yaml`).
   - `registry/`: Master company database (`companies.db`) containing ~15,000+ known company slugs across Greenhouse, Lever, Ashby, Workday CXS, and Tier-2 ATSs. Sourced from open datasets (YC, SimplifyJobs, `ats-scrapers`) and continuously self-expanding.
   - `scrapers/`: ATS job board & form question ingestion. High-throughput async HTTP (`httpx` + `asyncio`) querying public REST endpoints (no browser needed for discovery).
     - `base.py`: Unified `JobPosting`, `CompanyBoard`, and `FormSchema` dataclasses.
     - `crawler.py`: Async multi-board scanner and job matcher.
     - `greenhouse.py`: `boards-api.greenhouse.io` postings & question schema.
     - `lever.py`: `api.lever.co` postings extractor.
     - `ashby.py`: `api.ashbyhq.com` postings & application-form schema.
     - `workday.py`: CXS REST extractor (`/wday/cxs/.../jobs`).
     - `resolver.py`: Slug & provider auto-detection (`probe.py`).
   - `adapters/`: ATS DOM parsers and Playwright injectors for browser staging.
   - `solver/`: LLM prompt engine generating concise, grounded, truthful answers.
   - `tracker/`: Connector to Google Sheets (`internship-watcher`) and local database.
2. `cli/`:
   - `main.py`: `job-stager stage <url>` (browser staging & review).
   - `scan.py`: `job-stager scan [--company <name> | --all]` (async board crawler).
   - `probe.py`: `job-stager probe <company>` (probe & register company ATS).
3. `extension/`: Chrome Extension (Manifest V3 + Side Panel) for consumer distribution.

## Next Session Starting Point (Phase 1 MVP)
When starting implementation in a fresh session, execute in this order:
1. Initialize Python package (`pyproject.toml` managed via `uv`) with `pydantic`, `httpx`, `playwright`, `pyyaml`.
2. Implement `core/scrapers/base.py` (`JobPosting`, `FormSchema`, `CompanyBoard`).
3. Implement `core/scrapers/greenhouse.py`, `lever.py`, `ashby.py`, and `workday.py` (CXS REST).
4. Implement `core/registry/` to seed known company slugs and self-expand on new URLs.
5. Implement `core/adapters/` via Playwright for local browser staging.
6. Connect LLM solver (`core/solver/`) and tracker sync (`core/tracker/`).
