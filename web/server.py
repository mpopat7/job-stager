"""FastAPI server for JobStager web dashboard and staging control plane."""

from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
import logging
import os
from pathlib import Path
import re
from typing import Dict, List, Optional
from fastapi import (
    BackgroundTasks, Cookie, Depends, FastAPI, File, Form, HTTPException, Request,
    Response, UploadFile,
)
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, HTMLResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from core.config.loader import load_profile, save_profile
from core.config.schema import CandidateProfile, Preferences
from core.store.profiles import ProfileStore
from core.store.users import UserStore
from web.auth import (
    LOGIN_LIMIT, LOGIN_WINDOW, REGISTER_LIMIT, REGISTER_WINDOW,
    check_deployment_config, clear_session_cookie, client_ip, current_user,
    optional_user, profile_for, profiles, set_session_cookie, setup_required,
    single_tenant, throttle, users,
)
from core.registry.ingest import ingest_simplify_feed
from core.registry.roles import all_families, classify_role, role_counts
from core.registry.store import CompanyRegistry
from core.tracker.local_db import LocalTracker
from core.tracker.matches import MatchStore
from core.scrapers.base import ATSProvider
from core.scrapers.resolver import probe_company, resolve_url
from core.tracker.sheets import SheetsTracker
from cli.main import run_stage
from web import api_v1

logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(_app: FastAPI):
    """Refuse to serve a deployment that is missing what protects its users."""
    check_deployment_config()
    purged = users.purge_expired_sessions()
    if purged:
        logger.info(f"Purged {purged} expired session(s) at startup")
    yield


app = FastAPI(title="JobStager API", version="0.1.0", lifespan=lifespan)

# Credentials may not be shared with arbitrary origins: with a session cookie in play
# that would let any page a user visits act as them. The dashboard is same-origin, so an
# explicit localhost allowlist is all that is needed.
ALLOWED_ORIGINS = [
    o.strip()
    for o in os.getenv(
        "JOBSTAGER_ALLOWED_ORIGINS", "http://127.0.0.1:8000,http://localhost:8000"
    ).split(",")
    if o.strip()
]

# The extension is a separate origin (chrome-extension://<id>) and authenticates with a
# bearer token rather than the session cookie, so it is listed without credentials: an
# arbitrary page that guessed the id still has no token to send.
EXTENSION_ORIGINS = [
    o.strip()
    for o in os.getenv("JOBSTAGER_EXTENSION_ORIGINS", "").split(",")
    if o.strip()
]

app.add_middleware(
    CORSMiddleware,
    allow_origins=ALLOWED_ORIGINS + EXTENSION_ORIGINS,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(api_v1.router)

STATIC_DIR = Path(__file__).parent / "static"
STATIC_DIR.mkdir(parents=True, exist_ok=True)


class StageRequest(BaseModel):
    url: str
    grad_year: Optional[int] = None
    auto_log: bool = False


class ProbeRequest(BaseModel):
    company: str


class CohortsUpdateRequest(BaseModel):
    active_cohorts: List[int]
    default_cohort: Optional[int] = None


class CredentialsRequest(BaseModel):
    handle: str
    password: str
    email: Optional[str] = None


class ProfileUpdateRequest(BaseModel):
    """A partial profile update. Only the sections present are replaced."""

    candidate: Optional[Dict] = None
    disclosures: Optional[Dict] = None
    education: Optional[Dict] = None
    preferences: Optional[Dict] = None
    custom_answers: Optional[Dict[str, str]] = None


@app.get("/api/auth/status")
async def auth_status(user_id: Optional[int] = Depends(optional_user)):
    """Whether anyone is signed in, and whether this install still needs its first account."""
    return {
        "setup_required": setup_required(),
        "signed_in": user_id is not None,
        "handle": users.handle_for(user_id) if user_id else None,
    }


@app.post("/api/auth/register")
async def register(body: CredentialsRequest, request: Request, response: Response):
    throttle(f"register:{client_ip(request)}", REGISTER_LIMIT, REGISTER_WINDOW)
    try:
        user_id = users.create_user(body.handle, body.password, body.email)
    except ValueError as err:
        raise HTTPException(status_code=400, detail=str(err))

    # A new account starts blank. The one exception is the very first account on a
    # personal install, which adopts the operator's own profile.yaml so there is nothing
    # to retype -- on a shared deployment that same line would hand the first stranger
    # who signed up the operator's address, GPA and EEO answers.
    blank = CandidateProfile.model_validate(
        {"candidate": {
            "first_name": "", "last_name": "", "email": body.email or "",
            "phone": "", "location": "",
        }}
    )
    seed = blank
    if single_tenant() and users.count_users() == 1:
        try:
            seed = CandidateProfile.model_validate(load_profile().model_dump(mode="json"))
        except Exception as err:
            logger.info(f"No local profile to adopt for the first account: {err}")
    profiles.save(user_id, seed)

    set_session_cookie(response, users.start_session(user_id))
    return {"status": "registered", "user_id": user_id, "handle": body.handle}


@app.post("/api/auth/login")
async def login(body: CredentialsRequest, request: Request, response: Response):
    # Two buckets: one stops a flood from a single address, the other stops a slow
    # spray across many addresses from grinding away at one account.
    ip_bucket = f"login-ip:{client_ip(request)}"
    handle_bucket = f"login-handle:{body.handle.strip().lower()}"
    throttle(ip_bucket, LOGIN_LIMIT, LOGIN_WINDOW)
    throttle(handle_bucket, LOGIN_LIMIT, LOGIN_WINDOW)

    user_id = users.verify(body.handle, body.password)
    if user_id is None:
        raise HTTPException(status_code=401, detail="Incorrect handle or password")

    users.clear_attempts(ip_bucket)
    users.clear_attempts(handle_bucket)
    set_session_cookie(response, users.start_session(user_id))
    return {"status": "signed_in", "handle": body.handle}


@app.post("/api/auth/logout")
async def logout(response: Response, jobstager_session: Optional[str] = Cookie(default=None)):
    if jobstager_session:
        users.end_session(jobstager_session)
    clear_session_cookie(response)
    return {"status": "signed_out"}


@app.get("/api/profile")
async def get_profile(user_id: int = Depends(current_user)):
    profile = profile_for(user_id)
    return {
        "profile": profile.model_dump(mode="json"),
        "provenance": profiles.provenance(user_id),
        "unconfirmed": profiles.unconfirmed(user_id),
    }


@app.put("/api/profile")
async def update_profile(body: ProfileUpdateRequest, user_id: int = Depends(current_user)):
    """Replace the sections named in the request, leaving the rest untouched."""
    current = profile_for(user_id).model_dump(mode="json")
    changed: List[str] = []
    for section in ("candidate", "disclosures", "education", "preferences", "custom_answers"):
        incoming = getattr(body, section)
        if incoming is None:
            continue
        current[section] = incoming
        changed.append(section)

    try:
        updated = CandidateProfile.model_validate(current)
    except Exception as err:
        raise HTTPException(status_code=400, detail=f"Invalid profile: {err}")

    profiles.save(user_id, updated)
    # A person typed these, so they are trusted without a review flag.
    for section in changed:
        profiles.set_provenance(user_id, section, "typed")
    return {"status": "saved", "sections": changed}


@app.get("/api/preferences")
async def get_preferences(user_id: int = Depends(current_user)):
    return profile_for(user_id).preferences.model_dump(mode="json")


@app.put("/api/preferences")
async def update_preferences(body: Dict, user_id: int = Depends(current_user)):
    profile = profile_for(user_id)
    try:
        profile.preferences = Preferences.model_validate(body)
    except Exception as err:
        raise HTTPException(status_code=400, detail=f"Invalid preferences: {err}")
    profiles.save(user_id, profile)
    profiles.set_provenance(user_id, "preferences", "typed")
    return {"status": "saved", "preferences": profile.preferences.model_dump(mode="json")}


@app.get("/api/stats")
async def get_stats(user_id: Optional[int] = Depends(optional_user)):
    """Return live database counts and candidate tracker configuration."""
    reg = CompanyRegistry()
    profile = profile_for(user_id)
    total_jobs = reg.count_jobs()
    current_job_ids = {job["id"] for job in reg.job_records()}
    applied_jobs = (
        len(MatchStore(user_id).confirmed_job_ids() & current_job_ids) if user_id else 0
    )

    return {
        "total_jobs": total_jobs,
        "applied_jobs": applied_jobs,
        "unapplied_jobs": total_jobs - applied_jobs,
        "total_companies": reg.count_companies(),
        "candidate": {
            "name": profile.candidate.full_name,
            "school": profile.education.school,
            "degree": profile.education.degree,
            "default_grad_year": profile.education.graduation_year,
            "gpa": profile.education.gpa,
        },
        "resumes": {
            "active_cohorts": profile.resumes.get_available_years(),
            "default_cohort": profile.education.graduation_year,
        },
        "tracker": {
            "spreadsheet_id": profile.tracker.spreadsheet_id,
            "tab": profile.tracker.sheet_tab,
            "connected": bool(profile.tracker.spreadsheet_id),
        },
    }


@app.get("/api/jobs")
async def list_jobs(
    q: Optional[str] = None,
    provider: Optional[str] = None,
    role: Optional[str] = None,
    sort: str = "recent",
    hide_applied: bool = True,
    limit: int = 50,
    user_id: Optional[int] = Depends(optional_user),
):
    """List public jobs with the caller's private application state layered on top."""
    reg = CompanyRegistry()
    states = MatchStore(user_id).job_states() if user_id else {}
    confirmed = {
        job_id for job_id, state in states.items() if state["status"] == "confirmed"
    }
    ats_enum = None
    if provider and provider.lower() != "all":
        try:
            ats_enum = ATSProvider(provider.lower())
        except ValueError:
            pass

    keywords = q.split() if q else None
    jobs = reg.get_jobs(
        keywords=keywords,
        provider=ats_enum,
        limit=limit,
        hide_applied=False,
        exclude_ids=confirmed if hide_applied else None,
    )

    # Classify before filtering so the counts describe the whole result set, not the
    # slice left after the filter -- otherwise every tab reads zero except the active one.
    counts = role_counts([j.title for j in jobs])
    if role and role.lower() != "all":
        jobs = [j for j in jobs if classify_role(j.title) == role]

    if sort == "company":
        jobs = sorted(jobs, key=lambda j: (j.company or "").lower())
    elif sort == "title":
        jobs = sorted(jobs, key=lambda j: (j.title or "").lower())
    elif sort == "role":
        jobs = sorted(jobs, key=lambda j: (classify_role(j.title), (j.company or "").lower()))

    return {
        "count": len(jobs),
        "role_counts": counts,
        "role_families": all_families(),
        "jobs": [
            {
                "id": j.id,
                "title": j.title,
                "company": j.company,
                "location": j.location,
                "url": j.url,
                "provider": j.provider.value,
                "role_type": classify_role(j.title),
                "is_internship": j.is_internship,
                "status": (
                    "applied" if states.get(j.id, {}).get("status") == "confirmed"
                    else "discovered"
                ),
                "match_status": states.get(j.id, {}).get("status"),
                "match_source": states.get(j.id, {}).get("origin"),
                "matched_by": states.get(j.id, {}).get("matched_by"),
                "stage": states.get(j.id, {}).get("stage"),
                "applied_date": states.get(j.id, {}).get("date_applied"),
                "notes": states.get(j.id, {}).get("notes"),
                "updated_at": j.updated_at,
                "discovered_at": j.discovered_at,
                "posted_date": j.post_date_display,
            }
            for j in jobs
        ],
    }


@app.get("/api/matches")
async def list_matches(user_id: int = Depends(current_user), limit: int = 500):
    """Sheet-origin matches, kept separate from JobStager's own application tracker."""
    store = MatchStore(user_id)
    matches = store.list_matches(origin="sheet", limit=limit)
    jobs = {job["id"]: job for job in CompanyRegistry().job_records()}
    output = []
    for match in matches:
        job_ids = [match["job_id"]] if match["job_id"] else match["candidate_job_ids"]
        for job_id in job_ids:
            job = jobs.get(job_id)
            if not job:
                continue
            output.append({
                "job_id": job_id,
                "title": job["title"],
                "company": job.get("company") or match["company"],
                "location": job.get("location"),
                "url": job["url"],
                "provider": job["provider"],
                "match_status": match["status"],
                "matched_by": match["matched_by"],
                "source": "google_sheet",
                "date_applied": match["date_applied"],
                "stage": match["stage"],
                "external_role": match["role"],
            })
    counts = store.counts(origin="sheet")
    return {
        "count": counts["confirmed"] + counts["possible"],
        "confirmed_count": counts["confirmed"],
        "possible_count": counts["possible"],
        "matches": output,
    }


@app.get("/api/applications")
async def list_applications(user_id: int = Depends(current_user), limit: int = 500):
    """The tracker, read locally so the tab renders without a Sheets round trip."""
    tracker = LocalTracker(user_id)
    rows = tracker.read_applications(limit=limit)
    for r in rows:
        r["role_type"] = classify_role(r.get("role"))
    profile = profile_for(user_id)
    return {
        "count": len(rows),
        "applications": rows,
        "stages": tracker.stage_counts(),
        "role_counts": role_counts([r.get("role") for r in rows]),
        "sheet_url": (
            f"https://docs.google.com/spreadsheets/d/{profile.tracker.spreadsheet_id}/edit"
            if profile.tracker.spreadsheet_id else None
        ),
    }


@app.post("/api/reconcile")
async def reconcile_tracker(user_id: int = Depends(current_user)):
    """Reconcile SQLite discovered jobs with Google Sheets tracker rows."""
    try:
        profile = profile_for(user_id)
        if not profile.tracker.spreadsheet_id:
            return {
                "status": "not_connected",
                "sheet_count": 0,
                "matched_count": 0,
                "confirmed_count": 0,
                "possible_count": 0,
                "applied_total": len(MatchStore(user_id).confirmed_job_ids()),
            }
        tracker = SheetsTracker(
            user_id,
            spreadsheet_id=profile.tracker.spreadsheet_id,
            key_path=profile.tracker.credentials_path,
            tab_name=profile.tracker.sheet_tab,
        )
        applications = tracker.read_applications()
        reg = CompanyRegistry()
        match_store = MatchStore(user_id)
        match_store.reconcile_jobstager(reg, LocalTracker(user_id).read_applications())
        result = match_store.reconcile_sheet(reg, applications)
        total_jobs = reg.count_jobs()
        applied_jobs = len(
            match_store.confirmed_job_ids() & {job["id"] for job in reg.job_records()}
        )

        return {
            "status": "reconciled",
            **result,
            "applied_total": applied_jobs,
            "unapplied_total": total_jobs - applied_jobs,
            "total_jobs": total_jobs,
        }
    except Exception as e:
        logger.error(f"Error reconciling tracker: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/api/stage")
async def stage_application(
    req: StageRequest,
    background_tasks: BackgroundTasks,
    user_id: int = Depends(current_user),
):
    """Trigger the Playwright staging agent to pre-fill the application in the user's browser.

    This opens a window on the machine running the process, which only makes sense when
    that machine is the user's own. A shared deployment refuses it and the browser
    extension does the filling instead.
    """
    if not single_tenant():
        raise HTTPException(
            status_code=501,
            detail="Staging runs in your browser via the JobStager extension, not on the server.",
        )
    url = req.url.strip()
    if not url:
        raise HTTPException(status_code=400, detail="URL is required")

    provider, slug, job_id = resolve_url(url)
    if provider == ATSProvider.UNKNOWN and not "myworkdayjobs.com" in url:
        raise HTTPException(status_code=400, detail="Unsupported ATS URL. We support Greenhouse, Lever, Ashby, and Workday.")

    # Run staging asynchronously in the background so HTTP response returns quickly
    async def _do_stage():
        try:
            await run_stage(
                url=url,
                grad_year=req.grad_year,
                headless=False,  # Headed so user can see it
                auto_log=req.auto_log,
                profile=profile_for(user_id),
                user_id=user_id,
            )
        except Exception as e:
            logger.error(f"Error in background staging: {e}")

    background_tasks.add_task(_do_stage)

    return {
        "status": "staging_initiated",
        "provider": provider.value if provider != ATSProvider.UNKNOWN else "workday",
        "url": url,
        "message": "Browser is launching to stage the form with your profile and resume. Check your desktop window!",
    }


@app.post("/api/sync")
async def sync_jobs(user_id: int = Depends(current_user)):
    """Ingest live tech internships feed from SimplifyJobs."""
    reg = CompanyRegistry()
    companies, jobs = await ingest_simplify_feed(reg, active_only=True)
    result = {
        "status": "synced",
        "companies_registered": companies,
        "jobs_added": jobs,
        "total_jobs": reg.count_jobs(),
    }
    match_store = MatchStore(user_id)
    result["jobstager_tracker"] = match_store.reconcile_jobstager(
        reg, LocalTracker(user_id).read_applications()
    )
    profile = profile_for(user_id)
    if profile.tracker.spreadsheet_id and profile.tracker.auto_sync_sheets:
        try:
            tracker = SheetsTracker(
                user_id,
                spreadsheet_id=profile.tracker.spreadsheet_id,
                key_path=profile.tracker.credentials_path,
                tab_name=profile.tracker.sheet_tab,
            )
            result["tracker"] = match_store.reconcile_sheet(reg, tracker.read_applications())
        except Exception as err:
            logger.warning(f"Jobs synced, but tracker reconciliation failed: {err}")
            result["tracker_error"] = str(err)
    return result


@app.post("/api/probe")
async def probe_endpoint(req: ProbeRequest, user_id: int = Depends(current_user)):
    """Probe where a company hosts its ATS board."""
    board = await probe_company(req.company)
    if not board:
        raise HTTPException(status_code=404, detail=f"No active Greenhouse, Lever, or Ashby board found for {req.company}")

    reg = CompanyRegistry()
    reg.add_company(board)
    return {
        "company": board.company_name,
        "provider": board.provider.value,
        "slug": board.slug,
        "board_url": board.board_url,
        "job_count": board.job_count,
    }


@app.get("/api/resumes")
async def get_resumes_config(user_id: int = Depends(current_user)):
    """Return configured graduation cohorts and resume upload statuses."""
    profile = profile_for(user_id)
    # Years to show a row for: the standard span plus anything already configured.
    listable = profile.resumes.get_available_years()
    # Years actually targeted. Uploading a resume for a year must not silently start
    # applying as that cohort -- only an explicit choice does that.
    active_years = sorted(set(profile.resumes.active_cohorts))
    default_year = profile.education.graduation_year

    cohorts_data = {}
    standard_cohorts = [2026, 2027, 2028, 2029, 2030]
    all_cohorts = sorted(set(standard_cohorts + listable + active_years))

    for yr in all_cohorts:
        resume_path = profile.resumes.resolve_resume(yr)
        # resolve_resume() falls back to the default resume, so a path alone does not
        # mean this year has one of its own.
        own = profile.resumes.variants.get(str(yr)) or (
            profile.resumes.grad_2028 if yr == 2028 else
            profile.resumes.grad_2029 if yr == 2029 else None
        )
        cohorts_data[str(yr)] = {
            "year": yr,
            "active": yr in active_years,
            "configured": bool(own and Path(own).expanduser().exists()),
            "path": str(resume_path) if resume_path else None,
            "filename": Path(own).name if own else None,
        }

    return {
        "active_cohorts": active_years,
        "default_cohort": default_year,
        "cohorts": cohorts_data,
    }


@app.post("/api/resumes/cohorts")
async def update_cohorts(req: CohortsUpdateRequest, user_id: int = Depends(current_user)):
    """Update active graduation cohorts for automatic posting targeting."""
    profile = profile_for(user_id)
    profile.resumes.active_cohorts = sorted(list(set(req.active_cohorts)))
    if req.default_cohort:
        profile.education.graduation_year = req.default_cohort
    profiles.save(user_id, profile)
    return {
        "status": "saved",
        "active_cohorts": profile.resumes.active_cohorts,
        "default_cohort": profile.education.graduation_year,
    }


@app.post("/api/resumes/upload")
async def upload_resume(
    grad_year: int = Form(...),
    file: UploadFile = File(...),
    user_id: int = Depends(current_user),
):
    """Upload and register a tailored resume PDF for a specific graduation year cohort."""
    if not file.filename.lower().endswith((".pdf", ".doc", ".docx")):
        raise HTTPException(status_code=400, detail="Only PDF or Word documents allowed")

    # A resume carries a full name, address and phone number, so one user's uploads
    # never share a directory with another's.
    upload_dir = (Path("resumes") / f"user_{user_id}").resolve()
    upload_dir.mkdir(parents=True, exist_ok=True)
    clean_name = re.sub(r"[^a-zA-Z0-9_.-]", "_", file.filename)
    dest_path = upload_dir / f"{grad_year}_{clean_name}"

    content = await file.read()
    with open(dest_path, "wb") as f:
        f.write(content)

    profile = profile_for(user_id)
    profile.resumes.variants[str(grad_year)] = str(dest_path)
    if grad_year == 2028:
        profile.resumes.grad_2028 = str(dest_path)
    elif grad_year == 2029:
        profile.resumes.grad_2029 = str(dest_path)

    if grad_year not in profile.resumes.active_cohorts:
        profile.resumes.active_cohorts.append(grad_year)
        profile.resumes.active_cohorts.sort()

    profiles.save(user_id, profile)

    return {
        "status": "uploaded",
        "grad_year": grad_year,
        "filename": file.filename,
        "path": str(dest_path),
    }


# Serve index.html
@app.get("/", response_class=HTMLResponse)
async def serve_index():
    index_path = STATIC_DIR / "index.html"
    if index_path.exists():
        return FileResponse(index_path)
    return HTMLResponse("<h1>JobStager UI</h1><p>Static index.html not found.</p>")


# Mount static assets
app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")
