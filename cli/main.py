"""Main CLI entrypoint for JobStager."""

from __future__ import annotations

import argparse
import asyncio
from datetime import datetime
import sys
from typing import Optional
from playwright.async_api import async_playwright

from core.adapters import get_adapter
from core.config.loader import load_profile
from core.config.schema import CandidateProfile
from core.registry.seed import seed_registry
from core.registry.store import CompanyRegistry
from core.scrapers.base import ATSProvider, JobPosting
from core.scrapers.greenhouse import GreenhouseScraper
from core.scrapers.lever import LeverScraper
from core.scrapers.ashby import AshbyScraper
from core.scrapers.resolver import resolve_url
from core.solver.llm import QuestionSolver
from core.store.users import UserStore
from core.tracker.sheets import SheetsTracker


async def run_stage(
    url: str,
    grad_year: Optional[int] = None,
    headless: bool = False,
    auto_log: bool = False,
    profile: Optional[CandidateProfile] = None,
    user_id: Optional[int] = None,
) -> int:
    """Stage a job application in the browser, generate AI answers, and prepare for human review."""
    print(f"\n⚡ JobStager: Initializing staging for application...")
    print(f"   Target URL: {url}")

    # 1. Resolve ATS Provider
    provider, slug, job_id = resolve_url(url)
    if provider == ATSProvider.UNKNOWN:
        print(f"❌ Could not detect a supported ATS provider for URL: {url}")
        print("   Supported: Greenhouse, Lever, Ashby, Workday")
        return 1

    print(f"   Detected ATS: {provider.value.upper()} (Slug: {slug or 'N/A'}, Job ID: {job_id or 'N/A'})")

    # 2. Load Profile. A caller that knows who is asking passes it in; only the command
    # line falls back to the file on this machine.
    try:
        if profile is None:
            profile = load_profile()
        print(f"   Candidate:  {profile.candidate.full_name} ({profile.education.school})")
    except Exception as e:
        print(f"❌ Failed to load profile: {e}")
        return 1

    # Grad year resolution (auto-detected from posting unless manually specified)
    from core.config.grad_detector import detect_grad_year

    available_cohorts = profile.resumes.get_available_years()
    if grad_year:
        chosen_grad_year = grad_year
        print(f"   Grad Year:  {chosen_grad_year} (manual override)")
    else:
        chosen_grad_year, detect_reason = detect_grad_year(
            f"{url} {slug or ''}",
            available_years=available_cohorts,
            default_year=profile.education.graduation_year,
        )
        print(f"   Grad Year:  {chosen_grad_year} (cohort: {detect_reason})")

    profile.education.graduation_year = chosen_grad_year

    # 3. Auto-register in Company Registry
    registry = CompanyRegistry()
    registry.auto_register_from_url(url)

    # 4. Ingest Form Schema & Generate AI Answers
    answers = {}
    solver = QuestionSolver(profile)
    schema = None

    if job_id and slug:
        print("   Ingesting form questions and generating truthful answers...")
        if provider == ATSProvider.GREENHOUSE:
            scraper = GreenhouseScraper()
            schema = await scraper.fetch_form_schema(slug, job_id)
        elif provider == ATSProvider.LEVER:
            scraper = LeverScraper()
            schema = await scraper.fetch_form_schema(slug, job_id)
        elif provider == ATSProvider.ASHBY:
            scraper = AshbyScraper()
            schema = await scraper.fetch_form_schema(slug, job_id)

        if schema and schema.custom_questions:
            print(f"   Found {len(schema.custom_questions)} custom questions:")
            for q in schema.custom_questions:
                ans = await solver.solve(q)
                answers[q.name] = ans
                preview = ans[:60] + "..." if len(ans) > 60 else ans
                print(f"   • Q: {q.label[:45]}...")
                print(f"     A: {preview}")

    # 5. Look up job info from registry or construct fallback
    with registry._get_connection() as conn:
        row = conn.execute(
            "SELECT id, company, company_slug, title, url FROM jobs WHERE url = ? OR (id = ? AND id != '')",
            (url, job_id or ""),
        ).fetchone()

    initial_company = ""
    initial_title = ""
    if row:
        initial_company = row["company"] or (row["company_slug"].replace("-", " ").title() if row["company_slug"] else "")
        initial_title = row["title"]

    if not initial_company:
        initial_company = slug.replace("-", " ").title() if slug else "Company"
    if not initial_title:
        initial_title = "Software Engineer Intern"

    job = JobPosting(
        id=job_id or (row["id"] if row else "job"),
        title=initial_title,
        company=initial_company,
        company_slug=slug or (row["company_slug"] if row else "slug"),
        url=url,
        apply_url=url,
        provider=provider,
    )

    tracker = SheetsTracker(
        user_id if user_id is not None else UserStore().ensure_local_user(),
        spreadsheet_id=profile.tracker.spreadsheet_id,
        key_path=profile.tracker.credentials_path,
        tab_name=profile.tracker.sheet_tab,
    )

    logged = False

    # 6. Launch Browser & Run Staging Adapter
    adapter = get_adapter(provider)
    if not adapter:
        print(f"❌ No staging adapter available for provider {provider.value}")
        return 1

    print("\n🌐 Launching browser to pre-fill application...")
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=headless)
        context = await browser.new_context(viewport={"width": 1280, "height": 900})
        page = await context.new_page()

        async def on_mark_applied():
            nonlocal logged, job
            if logged:
                return {"success": True, "company": job.company, "title": job.title, "grad_year": chosen_grad_year}

            # Refine title from page if generic
            if job.title in ("Software Engineer Intern", "Internship", ""):
                try:
                    h1_el = await page.query_selector("h1")
                    if h1_el:
                        ext = (await h1_el.inner_text()).strip()
                        if ext:
                            job.title = ext
                    if job.title in ("Software Engineer Intern", "Internship", ""):
                        p_title = await page.title()
                        if p_title:
                            ext = p_title.split("|")[0].split("-")[0].strip()
                            if ext:
                                job.title = ext
                except Exception:
                    pass

            try:
                tracker.log_application(job=job, grad_year=chosen_grad_year, stage="Applied")
                today_str = datetime.now().strftime("%-d %b %Y")
                with registry._get_connection() as conn:
                    conn.execute(
                        "UPDATE jobs SET status = 'applied', stage = 'Applied', applied_date = ? WHERE url = ? OR id = ?",
                        (today_str, url, job.id),
                    )
                    conn.commit()
                logged = True
                print(f"\n📝 Application logged to your Google Sheets tracker:")
                print(f"   Spreadsheet ID: {profile.tracker.spreadsheet_id}")
                print(f"   Tab:            {profile.tracker.sheet_tab}")
                print(f"   Row added:      {job.company} | {job.title} | {chosen_grad_year} grad | Applied")
                return {"success": True, "company": job.company, "title": job.title, "grad_year": chosen_grad_year}
            except Exception as err:
                logger.error(f"Failed to log application to tracker: {err}")
                return {"success": False, "error": str(err), "company": job.company, "title": job.title}

        async def on_close_window():
            try:
                await page.close()
            except Exception:
                pass

        # Expose RPC functions to browser page for on-site banner buttons
        await page.expose_function("jobStagerMarkApplied", on_mark_applied)
        await page.expose_function("jobStagerCloseWindow", on_close_window)

        result = await adapter.stage(
            page=page,
            url=url,
            profile=profile,
            answers=answers,
        )

        if result.grad_year:
            chosen_grad_year = result.grad_year
            profile.education.graduation_year = result.grad_year

        if not result.success:
            print(f"❌ Staging encountered an issue: {result.error}")
        else:
            print(f"✅ Application successfully staged!")
            print(f"   Target cohort:     Class of {chosen_grad_year}")
            print(f"   Fields pre-filled: {result.fields_filled}")
            print(f"   Resume attached:   {'Yes' if result.resume_attached else 'No'}")

        if auto_log:
            await on_mark_applied()

        if not headless:
            print("\n👀 Application staged in browser window.")
            print("   • JobStager toolbar is live at the top of the page.")
            print("   • Review and submit directly on the site.")
            print("   • Click [✓ Mark as Applied] on the banner to record to your Sheets tracker.")
            print("   • Close window when done (no terminal input needed).\n")
            try:
                await page.wait_for_event("close", timeout=0)
            except Exception:
                pass

        try:
            await browser.close()
        except Exception:
            pass

    return 0


def main():
    parser = argparse.ArgumentParser(
        prog="job-stager",
        description="JobStager: Intelligent Job Application Staging Agent",
    )
    subparsers = parser.add_subparsers(dest="command", help="Subcommand to run")

    # stage
    stage_parser = subparsers.add_parser("stage", help="Stage a job application in the browser")
    stage_parser.add_argument("url", help="Job application URL (Greenhouse, Lever, Ashby, Workday)")
    stage_parser.add_argument("--grad-year", type=int, help="Optional manual grad year override (auto-detected from posting if omitted)")
    stage_parser.add_argument("--headless", action="store_true", help="Run browser headlessly (e.g. for testing)")
    stage_parser.add_argument("--auto-log", action="store_true", help="Automatically log to tracker upon staging")

    # scan
    scan_parser = subparsers.add_parser("scan", help="Scan ATS boards for job openings")
    scan_parser.add_argument("--company", "-c", help="Specific company name or slug")
    scan_parser.add_argument("--provider", "-p", choices=["greenhouse", "lever", "ashby", "workday"])
    scan_parser.add_argument("--keywords", "-k", nargs="+", default=["intern", "engineer", "software", "data"])
    scan_parser.add_argument("--all-roles", action="store_true", help="Scan all roles, not just internships")
    scan_parser.add_argument("--concurrency", type=int, default=25)

    # sync
    sync_parser = subparsers.add_parser("sync", help="Ingest thousands of live tech internships into SQLite from open feeds")
    sync_parser.add_argument("--include-inactive", action="store_true", help="Include inactive/closed listings")

    # jobs
    jobs_parser = subparsers.add_parser("jobs", help="List and search discovered internship opportunities")
    jobs_parser.add_argument("--keywords", "-k", nargs="+", default=["intern", "software", "engineer", "data"], help="Keywords to search")
    jobs_parser.add_argument("--provider", "-p", choices=["greenhouse", "lever", "ashby", "workday"])
    jobs_parser.add_argument("--show-applied", action="store_true", help="Include roles already applied for (hidden by default)")
    jobs_parser.add_argument("--limit", "-n", type=int, default=25, help="Number of jobs to display")

    # reconcile
    reconcile_parser = subparsers.add_parser("reconcile", help="Cross-reference SQLite jobs with Google Sheets tracker to flag applied roles")

    # probe
    probe_parser = subparsers.add_parser("probe", help="Probe and register a company's ATS board")
    probe_parser.add_argument("company", help="Company name to probe (e.g. 'Stripe', 'Ramp')")

    # ui
    ui_parser = subparsers.add_parser("ui", help="Launch the local interactive web dashboard")
    ui_parser.add_argument("--port", "-P", type=int, default=8000, help="Port to run web server on")
    ui_parser.add_argument("--no-browser", action="store_true", help="Do not automatically open browser")

    # seed
    subparsers.add_parser("seed", help="Seed known company boards into local SQLite database")

    args = parser.parse_args()

    if args.command == "ui":
        import webbrowser
        import uvicorn
        from web.server import app

        url = f"http://localhost:{args.port}"
        print(f"\n⚡ JobStager Web Dashboard starting at: {url}")
        print("   Real-time ATS browsing, 1-click browser staging, and Google Sheets tracker sync.")
        if not args.no_browser:
            webbrowser.open(url)
        uvicorn.run(app, host="127.0.0.1", port=args.port, log_level="warning")
        sys.exit(0)

    elif args.command == "sync":
        from core.registry.ingest import ingest_simplify_feed
        reg = CompanyRegistry()
        print("⚡ Ingesting live tech internship feed from SimplifyJobs...")
        companies, jobs = asyncio.run(ingest_simplify_feed(reg, active_only=not args.include_inactive))
        print(f"\n✅ Ingestion complete!")
        print(f"   Registered companies: {reg.count_companies()} total")
        print(f"   Discovered jobs:      {reg.count_jobs()} total")
        print("   Run `job-stager jobs` to view and stage opportunities.")
        sys.exit(0)

    elif args.command == "jobs":
        reg = CompanyRegistry()
        total_jobs = reg.count_jobs()
        total_applied = reg.count_applied_jobs()
        if total_jobs == 0:
            print("⚡ No jobs in local database yet. Run `job-stager sync` to ingest thousands of active postings.")
            sys.exit(0)

        ats_enum = ATSProvider(args.provider.lower()) if args.provider else None
        matching = reg.get_jobs(
            keywords=args.keywords,
            provider=ats_enum,
            limit=args.limit,
            hide_applied=not args.show_applied,
        )
        status_filter_text = "All roles" if args.show_applied else f"Unapplied roles only (hiding {total_applied} applied)"
        print(f"\n🎯 Found {len(matching)} opportunities ({total_jobs} total in DB, {status_filter_text}):")
        print(f"   Filters: {', '.join(args.keywords)}\n")

        for i, j in enumerate(matching, 1):
            status_tag = f" \033[93m[APPLIED: {j.stage or 'Applied'}]\033[0m" if j.status == "applied" else ""
            print(f" {i:2d}. {j.company:<20} | {j.title}{status_tag}")
            print(f"     Location: {j.location}")
            print(f"     ATS:      {j.provider.value.upper()}")
            print(f"     Stage:    job-stager stage \"{j.url}\"")
            print()

        sys.exit(0)

    elif args.command == "reconcile":
        profile = load_profile()
        tracker = SheetsTracker(
            UserStore().ensure_local_user(),
            spreadsheet_id=profile.tracker.spreadsheet_id,
            key_path=profile.tracker.credentials_path,
            tab_name=profile.tracker.sheet_tab,
        )
        print("\n⚡ Reading applications from Google Sheets tracker...")
        apps = tracker.read_applications()
        print(f"   Fetched {len(apps)} rows from sheet tab '{profile.tracker.sheet_tab}'.")

        reg = CompanyRegistry()
        updated_count, matched = reg.reconcile_with_tracker(apps)
        total_applied = reg.count_applied_jobs()
        total_jobs = reg.count_jobs()
        unapplied = total_jobs - total_applied

        print(f"\n✅ Tracker Reconciliation Complete:")
        print(f"   Sheet applications:   {len(apps)}")
        print(f"   Matched DB postings:  {updated_count}")
        print(f"   Total marked applied: {total_applied}")
        print(f"   Fresh / unapplied:    {unapplied} (out of {total_jobs} total in DB)")

        # Stage breakdown
        with reg._get_connection() as conn:
            breakdown = conn.execute(
                "SELECT COALESCE(stage, 'Applied'), count(*) FROM jobs WHERE status = 'applied' GROUP BY stage ORDER BY count(*) DESC"
            ).fetchall()
        print("\n📊 Applied Breakdown by Stage:")
        for stage_name, cnt in breakdown:
            print(f"   • {stage_name:<16} : {cnt} listings")

        print("\n💡 Run `job-stager jobs` to view only fresh unapplied roles, or `job-stager jobs --show-applied` to include all.")
        sys.exit(0)

    elif args.command == "stage":
        target_url = args.url
        # If user passed a job key instead of URL, resolve from DB
        if not target_url.startswith("http"):
            reg = CompanyRegistry()
            with reg._get_connection() as conn:
                row = conn.execute("SELECT url FROM jobs WHERE id = ? LIMIT 1", (target_url,)).fetchone()
                if row:
                    target_url = row["url"]

        exit_code = asyncio.run(
            run_stage(
                url=target_url,
                grad_year=args.grad_year,
                headless=args.headless,
                auto_log=args.auto_log,
            )
        )
        sys.exit(exit_code)

    elif args.command == "scan":
        from cli.scan import run_scan
        exit_code = asyncio.run(
            run_scan(
                company=args.company,
                provider=args.provider,
                keywords=None if args.all_roles else args.keywords,
                internships_only=not args.all_roles,
                concurrency=args.concurrency,
            )
        )
        sys.exit(exit_code)

    elif args.command == "probe":
        from cli.probe import run_probe
        exit_code = asyncio.run(run_probe(args.company))
        sys.exit(exit_code)

    elif args.command == "seed":
        reg = CompanyRegistry()
        count = seed_registry(reg)
        print(f"✅ Successfully seeded {count} companies into registry ({reg.count_companies()} total).")
        sys.exit(0)

    else:
        parser.print_help()
        sys.exit(1)


if __name__ == "__main__":
    main()
