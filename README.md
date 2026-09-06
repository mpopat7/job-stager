# JobStager

> **Stop filling out repetitive forms. Stop letting blind bots hallucinate on your behalf.**
> 
> An intelligent job application staging agent that parses applicant tracking systems (Greenhouse, Lever, Ashby, Workday), pre-fills every field, generates truthful AI responses to custom screening questions, pops open the completed form for a 5-second human review, and automatically syncs to your application tracker.

---

## 1. Origin & Market Analysis

### What is Jobbie (`jobbie.bot`)?
[Jobbie](https://jobbie.bot) is a commercial cloud SaaS ($19–$79/month) founded by Jordan ([@i7solar](https://github.com/i7solar)) that auto-applies to jobs across Greenhouse, Lever, Ashby, and Workday on behalf of job seekers.

### Why Incumbent Auto-Appliers Fail
Current automated job appliers (Jobbie, Sonara, LazyApply, LoopCV) all suffer from fatal architectural flaws:

1. **Datacenter IP Blocking**: They run headless browsers from AWS/GCP servers. Modern ATS platforms (Workday, Greenhouse with Cloudflare Enterprise, Taleo) instantly flag datacenter ASN IPs, silently shadow-banning submissions or triggering unsolvable captchas.
2. **Workday Account Walls**: Workday (which powers over a third of enterprise internships and jobs) requires unique accounts per employer and multi-page wizards. Cloud bots routinely crash or fail here.
3. **The "Blind Bot" Trust Deficit**: Users have no idea what the bot actually submits. When an LLM hallucinates graduation year, visa requirements, or writes generic AI fluff for a custom technical essay (*"As a passionate and detail-oriented engineer..."*), recruiters instantly discard the application.
4. **Extortionate Subscription Quotas**: Charging $39–$79/mo for metered submission credits while putting user PII, unredacted resumes, and demographic data on a third-party server.

---

## 2. The Core Paradigm: Discovery Scraping & 5-Second Review Staging

Instead of a black-box bot that fires blind submissions into the void, **JobStager** introduces a complete, human-in-the-loop application workflow:

### A. Upstream Job Discovery (The Hybrid "Two-Speed" Engine)
Jobbie and other auto-appliers maintain closed proprietary databases of jobs. JobStager matches and exceeds this with a **high-throughput, local hybrid discovery engine**:

```
┌────────────────────────────────────────────────────────┐
│            1. Master Company Registry                  │
│       (~15,000 pre-seeded company slugs in SQLite)     │
│   • Greenhouse (~5,500 active tech/startup boards)     │
│   • Lever (~3,000 mid-market & enterprise boards)      │
│   • Ashby (~2,500 high-growth tech & YC startups)      │
│   • Workday CXS (~3,500 Fortune 500 & tech tenants)    │
│   • Seeded from YC, SimplifyJobs, and open datasets    │
└───────────────────────────┬────────────────────────────┘
                            │
                            ▼
┌────────────────────────────────────────────────────────┐
│            2. Async Python Micro-Crawler               │
│   • High-concurrency async HTTP (httpx + asyncio)      │
│   • Hits public REST APIs directly (~100ms per board)  │
│   • Scans 1,000 boards in <45 seconds headlessly       │
│   • Filters strictly on your profile (titles & grad)   │
└───────────────────────────┬────────────────────────────┘
                            │
                            ▼
┌────────────────────────────────────────────────────────┐
│            3. Local Matched Jobs DB (SQLite)           │
│   • Ranked opportunities ready for 1-click staging     │
│   • Deduplicated against your Google Sheet tracker     │
│   • Self-expanding: auto-registers any URL you paste   │
└────────────────────────────────────────────────────────┘
```

### B. Pre-Filled Staging & 5-Second Review
```
[ Job URL or Queue Match ] ───► [ AI Staging Engine ] ───► [ Auto-Log to Tracker ]
                                         │
                                         ▼
                             [ Pop Open Pre-Filled Tab ]
                             • All personal info filled
                             • Resume PDF attached
                             • Screening Q&As answered with AI
                                         │
                                         ▼
                             [ 5-Second Human Review ]
                             • Glance at answers
                             • Click "Submit Application"
                                         │
                                         ▼
                             [ Submission Confirmed ] ───► [ Tracker Status: "Applied" ]
```

1. **Deterministic Macro Discovery**: Ingests fresh postings directly from employer ATS APIs (Greenhouse, Lever, Ashby, Workday CXS) across ~15,000 companies without fragile browser scraping or datacenter proxy costs.
2. **Zero Bot Detection**: Submissions happen from your real machine, local residential IP, and genuine browser session.
3. **100% Trust**: You see the exact answers on the employer's official page before they enter the ATS.
4. **Automatic Tracking**: The moment it is staged or submitted, it registers directly into your application tracking sheet without manual logging.
5. **10x Time Savings**: Reduces an 8-minute repetitive form to a 5-second glance and click.

---

## 3. Real-World Pipeline Audit & Supported ATS Matrix

An audit of 171 real tech internship applications reveals the candidate pipeline distribution, aligned with the 9 platforms supported by Jobbie (`jobbie.bot`):

| Platform / Category | Applications | % of Pipeline | Discovery Scraper Strategy | Staging / Autofill Strategy |
| :--- | :---: | :---: | :--- | :--- |
| **Workday** | **58** | **33.9%** | **CXS REST API** (`/wday/cxs/.../jobs`) | Native Multi-Step Browser Stager |
| **Greenhouse** | **22** | **12.9%** | **Boards API** (`boards-api.greenhouse.io`) | 1-Click Stager + Form Schema API |
| **Ashby** | **6** | **3.5%** | **Posting API** (`api.ashbyhq.com`) | 1-Click Stager + Application Form API |
| **Lever** | **4** | **2.3%** | **Postings API** (`api.lever.co`) | 1-Click Semantic DOM Stager |
| **Workable** | *Tier-2* | *Included* | **Widget / Jobs API** (`apply.workable.com`) | 1-Click Stager |
| **BambooHR** | *Tier-2* | *Included* | **Careers API** (`{subdomain}.bamboohr.com`) | Standard Form Stager |
| **JazzHR** | *Tier-2* | *Included* | **RSS/XML Feed** (`{subdomain}.applytojob.com`) | Standard Form Stager |
| **Paylocity** | *Tier-2* | *Included* | **Recruiting API** (`recruiting.paylocity.com`) | Multi-Step Stager |
| **Indeed** | *Aggregator* | *Optional* | **Direct / RSS Feed Scrapers** | Indeed SmartApply Session Stager |
| **iCIMS** | **7** | **4.1%** | Custom Portal Search | Native Stager |
| **Tier-2 Other** *(Taleo, Jobvite)* | **13** | **7.6%** | Portal Search | Standard DOM Adapters |
| **Big Tech Portals** *(Microsoft, Google, Apple)* | **29** | **17.0%** | ATS API / Career Site Search | Heuristic Extension Autofill (80%) |
| **Custom / Fellowship Sites** *(MLT, Speedrun)* | **32** | **18.7%** | Manual / Bespoke | Bespoke Form Fillers |
| **Total** | **171** | **100.0%** | | |

### Key Takeaways:
- **Direct Parity with Jobbie**: Supports all 9 platforms tracked by Jobbie (`greenhouse`, `lever`, `workday`, `ashby`, `paylocity`, `jazzhr`, `workable`, `bamboohr`, `indeed`).
- **52.6%** of candidate applications sit on the **Big Four** (Workday, Greenhouse, Ashby, Lever).
- **Public API Discovery**: Greenhouse, Lever, Ashby, Workable, BambooHR, and Workday CXS can all be scraped headlessly via lightweight JSON REST APIs without running browser automation.
- **Workday alone is 33.9%**: Confirms that cloud bots like Jobbie fail because they cannot submit Workday multi-step forms reliably from datacenter IPs; running inside the candidate's authentic browser is essential.

---

## 4. The 3-Tier Distribution & Business Model

```
                       ┌───────────────────────────────────────┐
                       │     Unified ATS Automation Engine     │
                       │   (Board Scrapers + DOM Stagers + AI) │
                       └───────────────────────────────────────┘
                                           │
        ┌──────────────────────────────────┼──────────────────────────────────┐
        ▼                                  ▼                                  ▼
┌──────────────────┐             ┌───────────────────┐              ┌──────────────────┐
│ 1. Open Core CLI │             │ 2. Chrome Agent   │              │ 3. Hosted API    │
│  (Developer/OSS) │             │  (Consumer SaaS)  │              │  (B2B / Program) │
├──────────────────┤             ├───────────────────┤              ├──────────────────┤
│ • MIT License    │             │ • Chrome SidePanel│              │ • Webhooks & API │
│ • Python CLI     │             │ • Real browser IP │              │ • Job board SDKs │
│ • BYO LLM Key    │             │ • $19/mo sub      │              │ • Headless queue │
│ • Developer trust│             │ • Non-technical UX│              │ • Team accounts  │
└──────────────────┘             └───────────────────┘              └──────────────────┘
```

1. **Tier 1: Open-Source Local CLI (Top-of-Funnel & Trust)**
   - Developer-friendly, runs via Playwright and Python.
   - Bring your own LLM key (Gemini, Claude, OpenAI, or local Ollama).
   - Drives GitHub stars, Hacker News visibility ("Show HN: Open-Source Local Job Stager"), and community adapter contributions.
2. **Tier 2: Chrome Agent Extension (The Consumer Business)**
   - Manifest V3 extension with a modern Chrome Side Panel UI for non-technical users.
   - Detects when the user lands on Greenhouse, Lever, Ashby, or Workday.
   - Provides a 1-click **"Stage Application"** button that auto-fills the active tab using the cloud AI solver.
   - Monetized via a clean subscription model ($15–$25/month).
3. **Tier 3: Developer API**
   - Headless API for university talent networks, job boards, and recruiting aggregators.

---

## 5. System Architecture

```
job-stager/
├── core/
│   ├── config/              # User profile & candidate credentials
│   │   ├── profile.example.yaml
│   │   └── schema.py
│   ├── registry/            # Master company catalog (~15k employers)
│   │   ├── companies.db     # SQLite database mapping companies to ATS & slugs
│   │   ├── seed.py          # Seeder from YC, SimplifyJobs & open-jobs
│   │   └── store.py         # Registry CRUD & auto-registration of new URLs
│   ├── scrapers/            # High-throughput async ATS scrapers (Deterministic API)
│   │   ├── base.py          # Unified JobPosting, CompanyBoard & FormSchema models
│   │   ├── crawler.py       # High-concurrency async multi-board scanner (httpx)
│   │   ├── resolver.py      # Company name -> ATS provider & slug detector (probe)
│   │   ├── greenhouse.py    # boards-api.greenhouse.io (jobs + question schema)
│   │   ├── lever.py         # api.lever.co postings extractor
│   │   ├── ashby.py         # api.ashbyhq.com (jobs + form schema)
│   │   ├── workday.py       # CXS REST extractor for modern Workday portals
│   │   ├── workable.py      # apply.workable.com API
│   │   ├── bamboohr.py      # bamboohr.com careers API
│   │   └── jazzhr.py        # applytojob.com RSS/XML feed parser
│   ├── adapters/            # ATS DOM handlers & Playwright injectors (Staging)
│   │   ├── base.py
│   │   ├── greenhouse.py
│   │   ├── lever.py
│   │   ├── ashby.py
│   │   └── workday.py
│   ├── solver/              # AI Question Answering & Prompting
│   │   ├── llm.py           # Gemini / Claude / OpenAI client
│   │   └── prompts.py       # Grounded, anti-fluff Q&A prompts
│   └── tracker/             # Sync connectors
│       ├── base.py
│       ├── sheets.py        # Google Sheets connector (internship-watcher)
│       └── local_db.py      # SQLite / JSON fallbacks
├── cli/
│   ├── main.py              # CLI staging runner: `job-stager stage https://...`
│   ├── scan.py              # CLI board scanner: `job-stager scan [--company <name> | --all]`
│   └── probe.py             # CLI company resolver: `job-stager probe "Stripe"`
├── extension/               # Chrome Extension (Manifest V3 + Side Panel)
│   ├── manifest.json
│   ├── background/
│   ├── content/
│   └── sidepanel/
└── tests/
```

### Profile & Knowledge Base (`profile.yaml`)
Stores the ground truth to prevent hallucinations:
```yaml
candidate:
  first_name: "Jordan"
  last_name: "Rivera"
  email: "jordan.rivera@example.com"
  phone: "555-0100"
  location: "Columbus, OH"
  links:
    linkedin: "https://linkedin.com/in/example"
    github: "https://github.com/example"
    portfolio: "https://example.com"

disclosures:
  work_authorization: "US Citizen"
  requires_sponsorship: false
  gender: "Male"
  veteran_status: "No"
  disability_status: "No"

education:
  school: "Ohio State University"
  degree: "B.S. Computer Science"
  graduation_year: 2028 # or 2029
  gpa: "3.9"

resumes:
  default: "path/to/resume_2028.pdf"
  alt_grad_year: "path/to/resume_2029.pdf"

experience_highlights:
  - topic: "Distributed Systems & Backend"
    summary: "Built high-throughput indexing and data pipelines in Python and Go..."
  - topic: "AI & Machine Learning"
    summary: "Fine-tuned models and developed multi-modal RAG systems..."
```

---

## 6. Implementation Roadmap

- [ ] **Phase 1: Local CLI Engine & Core Scraper Pipeline (MVP)**
  - Build `profile.yaml` loader and validator.
  - Implement unified data models (`core/scrapers/base.py`: `JobPosting`, `FormSchema`, `CompanyBoard`).
  - Implement deterministic ATS scrapers (`core/scrapers/`):
    - `GreenhouseScraper` (Boards API: postings + question schema extraction).
    - `LeverScraper` (Postings API: postings + requirements extraction).
    - `AshbyScraper` (Posting API: postings + application form schema).
    - `WorkdayScraper` (CXS REST API discovery for fast headless listing).
  - Implement company registry (`core/registry/companies.db`) pre-seeded with target tech employers.
  - Implement high-concurrency async scanner (`core/scrapers/crawler.py` via `httpx` + `asyncio`).
  - Implement company resolver / probe (`probe.py`): company name -> ATS provider + slug.
  - Implement `GreenhouseAdapter` and `LeverAdapter` via Playwright for local browser staging.
  - Implement LLM screening question solver with anti-fluff prompts.
  - Hook submission/staging event to Google Sheets tracker (`internship-watcher`).
- [ ] **Phase 2: Complex ATS Support & Tier-2 Scrapers**
  - Implement `WorkdayAdapter` (Playwright multi-step logged-in staging).
  - Implement `AshbyAdapter` for 1-click staging.
  - Add Tier-2 deterministic scrapers (`WorkableScraper`, `BambooHRScraper`, `JazzHRScraper`).
  - Add interactive CLI review prompt (`--headless` vs `--interactive`).
- [ ] **Phase 3: Chrome Extension Prototype**
  - Create Manifest V3 extension with Side Panel.
  - Connect content script to DOM field extractors.
  - Enable 1-click in-browser auto-fill and review banner.
- [ ] **Phase 4: Cloud Backend & Monetization**
  - Lightweight FastAPI / Cloudflare Worker API for hosted LLM inference and auth.
  - Periodic background scraper workers to feed a hosted job queue.
  - Stripe subscription billing integration.
  - Public GitHub release and Show HN launch.
