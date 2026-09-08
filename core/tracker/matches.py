"""Per-user links between applications and public job postings."""

from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import json
import re
from pathlib import Path
from typing import Iterable, Optional
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from sqlalchemy import delete, func, insert, select

from core.registry.store import CompanyRegistry
from core.scrapers.base import ATSProvider, JobPosting
from core.scrapers.resolver import resolve_url
from core.store.db import get_engine, init_db, job_matches_table, upsert


_COMPANY_SUFFIXES = {
    "co", "company", "corp", "corporation", "inc", "incorporated", "llc", "ltd",
    "technologies", "technology", "the",
}
_TITLE_IGNORED = {
    "a", "an", "and", "for", "or", "position", "the",
}
_TRACKING_QUERY_KEYS = {"gh_src", "ref", "referrer", "referral", "source"}


def normalize_url(url: str) -> str:
    """Canonicalize a posting URL without dropping parameters that identify the job."""
    raw = (url or "").strip()
    if not raw:
        return ""
    try:
        parsed = urlsplit(raw)
    except ValueError:
        return raw.lower().rstrip("/")

    host = (parsed.hostname or "").lower()
    if host.startswith("www."):
        host = host[4:]
    if parsed.port:
        host = f"{host}:{parsed.port}"
    path = re.sub(r"/+", "/", parsed.path).rstrip("/") or "/"
    if path.endswith("/application") and "ashbyhq.com" in host:
        path = path.removesuffix("/application") or "/"
    query = []
    for key, value in parse_qsl(parsed.query, keep_blank_values=True):
        lowered = key.lower()
        if lowered.startswith("utm_") or lowered in _TRACKING_QUERY_KEYS:
            continue
        query.append((lowered, value))
    query.sort()
    return urlunsplit(((parsed.scheme or "https").lower(), host, path, urlencode(query), ""))


def ats_identity(url: str) -> Optional[tuple[str, str, str]]:
    """Return a provider/job-id identity when the repository's resolver can find one."""
    provider, slug, job_id = resolve_url(url or "")
    if provider == ATSProvider.UNKNOWN or not job_id:
        return None
    return provider.value, slug.strip().lower(), str(job_id).strip().lower()


def normalize_company(company: str) -> str:
    words = re.findall(r"[a-z0-9]+", (company or "").lower())
    return "".join(word for word in words if word not in _COMPANY_SUFFIXES)


def normalize_title(title: str) -> str:
    text = (title or "").lower().replace("co-op", "coop")
    text = re.sub(r"\bswe\b", "software engineer", text)
    text = re.sub(r"\bsoftware engineering\b", "software engineer", text)
    text = re.sub(r"\binternship\b", "intern", text)
    words = re.findall(r"[a-z0-9]+", text)
    kept = [word for word in words if word not in _TITLE_IGNORED]
    return " ".join(kept)


def _title_overlap(left: str, right: str) -> float:
    left_words = set(left.split())
    right_words = set(right.split())
    if not left_words or not right_words:
        return 0.0
    return len(left_words & right_words) / len(left_words | right_words)


def _source_key(application: dict, ordinal: int) -> str:
    row = application.get("row")
    if row not in (None, ""):
        return f"row:{row}"
    identity = "\x1f".join(
        str(application.get(key, "")).strip().lower()
        for key in ("Company", "Role", "Link", "Date Applied")
    )
    digest = hashlib.sha256(identity.encode("utf-8")).hexdigest()[:32]
    return f"content:{digest}:{ordinal}"


def _job_identity(job: dict) -> Optional[tuple[str, str, str]]:
    resolved = ats_identity(job.get("url", ""))
    if resolved:
        return resolved
    job_id = str(job.get("id", ""))
    parts = job_id.split(":", 2)
    if len(parts) == 3 and parts[0] in {provider.value for provider in ATSProvider}:
        return parts[0], parts[1].lower(), parts[2].lower()
    return None


def match_applications(applications: Iterable[dict], jobs: list[dict]) -> list[dict]:
    """Match each application to at most one job, with exact identities taking priority."""
    by_url: dict[str, list[dict]] = {}
    by_identity: dict[tuple[str, str, str], list[dict]] = {}
    by_company_title: dict[tuple[str, str], list[dict]] = {}
    for job in jobs:
        for raw_url in {job.get("url", ""), job.get("apply_url", "")}:
            canonical = normalize_url(raw_url)
            if canonical:
                bucket = by_url.setdefault(canonical, [])
                if not any(existing["id"] == job["id"] for existing in bucket):
                    bucket.append(job)
            identity = ats_identity(raw_url)
            if identity:
                bucket = by_identity.setdefault(identity, [])
                if not any(existing["id"] == job["id"] for existing in bucket):
                    bucket.append(job)
        identity = _job_identity(job)
        if identity and identity not in by_identity:
            by_identity[identity] = [job]
        company_title = (
            normalize_company(job.get("company") or job.get("company_slug", "")),
            normalize_title(job.get("title", "")),
        )
        if all(company_title):
            by_company_title.setdefault(company_title, []).append(job)

    used_jobs: set[str] = set()
    results: list[dict] = []
    pending: list[tuple[int, dict]] = []

    def available(candidates: Iterable[dict]) -> list[dict]:
        return [job for job in candidates if job["id"] not in used_jobs]

    # URL and ATS ids are stronger than names. Resolve those across the whole Sheet first
    # so a generic title row cannot claim the posting needed by a later exact-link row.
    for ordinal, application in enumerate(applications):
        link = str(application.get("Link", "")).strip()
        candidates = available(by_url.get(normalize_url(link), [])) if link else []
        matched_by = "normalized_url"
        if not candidates and link:
            identity = ats_identity(link)
            candidates = available(by_identity.get(identity, [])) if identity else []
            matched_by = "ats_identity"
        if len(candidates) == 1:
            job_id = candidates[0]["id"]
            used_jobs.add(job_id)
            results.append(_match_record(application, ordinal, job_id, "confirmed", matched_by))
        elif len(candidates) > 1:
            results.append(_match_record(
                application, ordinal, None, "possible", matched_by,
                [job["id"] for job in candidates],
            ))
        else:
            pending.append((ordinal, application))

    for ordinal, application in pending:
        company = normalize_company(str(application.get("Company", "")))
        title = normalize_title(str(application.get("Role", "")))
        key = (company, title)
        if not all(key):
            continue
        candidates = available(by_company_title.get(key, []))
        if len(candidates) == 1:
            job_id = candidates[0]["id"]
            used_jobs.add(job_id)
            results.append(_match_record(
                application, ordinal, job_id, "confirmed", "company_title"
            ))
            continue
        elif len(candidates) > 1:
            results.append(_match_record(
                application, ordinal, None, "possible", "company_title",
                [job["id"] for job in candidates],
            ))
            continue

        related = [
            job for job in available(job for job in jobs if (
                normalize_company(job.get("company") or job.get("company_slug", "")) == company
            ))
            if _title_overlap(title, normalize_title(job.get("title", ""))) >= 0.5
        ]
        if related:
            results.append(_match_record(
                application, ordinal, None, "possible", "company_title_tokens",
                [job["id"] for job in related],
            ))
    return results


def _match_record(
    application: dict,
    ordinal: int,
    job_id: Optional[str],
    status: str,
    matched_by: str,
    candidate_job_ids: Optional[list[str]] = None,
) -> dict:
    return {
        "job_id": job_id,
        "candidate_job_ids": candidate_job_ids or [],
        "status": status,
        "matched_by": matched_by,
        "source_key": _source_key(application, ordinal),
        "external_row": application.get("row"),
        "company": str(application.get("Company", "")).strip(),
        "role": str(application.get("Role", "")).strip(),
        "link": str(application.get("Link", "")).strip(),
        "stage": str(application.get("Stage", "Applied")).strip() or "Applied",
        "date_applied": str(application.get("Date Applied", "")).strip(),
        "notes": str(application.get("Comp/Notes", "")).strip(),
    }


class MatchStore:
    """Private match state for one user."""

    def __init__(self, user_id: int, db_path: Optional[Path | str] = None):
        self.user_id = user_id
        self.db_path = db_path
        init_db(db_path)

    @property
    def engine(self):
        return get_engine(self.db_path)

    def reconcile_jobstager(
        self,
        registry: CompanyRegistry,
        applications: Iterable[dict],
    ) -> dict:
        """Rematch locally recorded applications after the discovery feed changes."""
        rows = list(applications)
        candidates = [
            {
                "Company": row.get("company", ""),
                "Role": row.get("role", ""),
                "Link": row.get("link", ""),
                "Stage": row.get("stage", "Applied"),
                "Date Applied": row.get("date_applied", ""),
                "Comp/Notes": row.get("notes", ""),
                "row": row.get("id"),
            }
            for row in rows
        ]
        matches = [
            match for match in match_applications(candidates, registry.job_records())
            if match["status"] == "confirmed"
        ]

        now = datetime.now(timezone.utc)
        with self.engine.begin() as conn:
            conn.execute(delete(job_matches_table).where(
                job_matches_table.c.user_id == self.user_id,
                job_matches_table.c.origin == "jobstager",
            ))
            for match in matches:
                values = dict(match)
                values["candidate_job_ids"] = json.dumps(values["candidate_job_ids"])
                values["external_row"] = None
                conn.execute(insert(job_matches_table).values(
                    user_id=self.user_id,
                    origin="jobstager",
                    created_at=now,
                    updated_at=now,
                    **values,
                ))

        return {
            "application_count": len(rows),
            "matched_count": len(matches),
        }

    def reconcile_sheet(self, registry: CompanyRegistry, applications: Iterable[dict]) -> dict:
        rows = list(applications)
        matches = match_applications(rows, registry.job_records())

        # A JobStager application is copied to the Sheet when connected. Keep its original
        # provenance instead of presenting that mirror row as a separate external match.
        jobstager_ids = self.confirmed_job_ids(origin="jobstager")
        filtered = []
        for match in matches:
            if match["job_id"] in jobstager_ids:
                continue
            candidates = [
                job_id for job_id in match["candidate_job_ids"] if job_id not in jobstager_ids
            ]
            if match["status"] == "possible":
                if not candidates:
                    continue
                match["candidate_job_ids"] = candidates
            filtered.append(match)

        now = datetime.now(timezone.utc)
        with self.engine.begin() as conn:
            conn.execute(delete(job_matches_table).where(
                job_matches_table.c.user_id == self.user_id,
                job_matches_table.c.origin == "sheet",
            ))
            for match in filtered:
                values = dict(match)
                values["candidate_job_ids"] = json.dumps(values["candidate_job_ids"])
                conn.execute(insert(job_matches_table).values(
                    user_id=self.user_id,
                    origin="sheet",
                    created_at=now,
                    updated_at=now,
                    **values,
                ))

        confirmed = sum(match["status"] == "confirmed" for match in filtered)
        possible = len(filtered) - confirmed
        return {
            "sheet_count": len(rows),
            "matched_count": len(filtered),
            "confirmed_count": confirmed,
            "possible_count": possible,
        }

    def mark_jobstager(
        self,
        job: JobPosting,
        registry: Optional[CompanyRegistry] = None,
    ) -> Optional[dict]:
        registry = registry or CompanyRegistry()
        application = {
            "Company": job.company,
            "Role": job.title,
            "Link": job.url,
            "Stage": job.stage or "Applied",
            "Date Applied": job.applied_date or datetime.now().strftime("%Y-%m-%d"),
            "Comp/Notes": job.notes or "",
        }
        matches = match_applications([application], registry.job_records())
        if not matches or matches[0]["status"] != "confirmed":
            return None
        match = matches[0]
        match["source_key"] = f"job:{match['job_id']}"
        now = datetime.now(timezone.utc)
        values = {
            "user_id": self.user_id,
            "origin": "jobstager",
            "created_at": now,
            "updated_at": now,
            **match,
        }
        values["candidate_job_ids"] = json.dumps(values["candidate_job_ids"])
        with self.engine.begin() as conn:
            conn.execute(upsert(
                self.engine,
                job_matches_table,
                values,
                index_elements=["user_id", "origin", "source_key"],
                update_cols=[
                    "job_id", "candidate_job_ids", "status", "matched_by", "company",
                    "role", "link", "stage", "date_applied", "notes", "updated_at",
                ],
            ))
        return match

    def confirmed_job_ids(self, origin: Optional[str] = None) -> set[str]:
        query = select(job_matches_table.c.job_id).where(
            job_matches_table.c.user_id == self.user_id,
            job_matches_table.c.status == "confirmed",
            job_matches_table.c.job_id.is_not(None),
        )
        if origin:
            query = query.where(job_matches_table.c.origin == origin)
        with self.engine.connect() as conn:
            return {row.job_id for row in conn.execute(query)}

    def job_states(self) -> dict[str, dict]:
        states: dict[str, dict] = {}
        for match in self.list_matches(origin=None, limit=None):
            ids = [match["job_id"]] if match["job_id"] else match["candidate_job_ids"]
            for job_id in ids:
                current = states.get(job_id)
                if current and current["status"] == "confirmed":
                    continue
                states[job_id] = {
                    "status": match["status"],
                    "origin": match["origin"],
                    "matched_by": match["matched_by"],
                    "stage": match["stage"],
                    "date_applied": match["date_applied"],
                    "notes": match["notes"],
                }
        return states

    def list_matches(
        self,
        origin: Optional[str] = "sheet",
        status: Optional[str] = None,
        limit: Optional[int] = 500,
    ) -> list[dict]:
        query = select(job_matches_table).where(job_matches_table.c.user_id == self.user_id)
        if origin:
            query = query.where(job_matches_table.c.origin == origin)
        if status:
            query = query.where(job_matches_table.c.status == status)
        query = query.order_by(job_matches_table.c.updated_at.desc(), job_matches_table.c.id.desc())
        if limit is not None:
            query = query.limit(limit)
        with self.engine.connect() as conn:
            rows = conn.execute(query).fetchall()
        out = []
        for row in rows:
            record = dict(row._mapping)
            try:
                record["candidate_job_ids"] = json.loads(record["candidate_job_ids"] or "[]")
            except (TypeError, json.JSONDecodeError):
                record["candidate_job_ids"] = []
            out.append(record)
        return out

    def counts(self, origin: Optional[str] = None) -> dict[str, int]:
        query = (
            select(job_matches_table.c.status, func.count().label("n"))
            .where(job_matches_table.c.user_id == self.user_id)
            .group_by(job_matches_table.c.status)
        )
        if origin:
            query = query.where(job_matches_table.c.origin == origin)
        with self.engine.connect() as conn:
            rows = conn.execute(query).fetchall()
        counts = {"confirmed": 0, "possible": 0}
        counts.update({row.status: row.n for row in rows})
        return counts
