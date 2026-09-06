"""Pre-seeded master list of tech employers across ATS platforms."""

from __future__ import annotations

import logging
from typing import List
from core.registry.store import CompanyRegistry
from core.scrapers.base import ATSProvider, CompanyBoard

logger = logging.getLogger(__name__)

INITIAL_BOARDS: List[dict] = [
    # --- Greenhouse Boards ---
    {"name": "Stripe", "slug": "stripe", "provider": ATSProvider.GREENHOUSE, "url": "https://boards.greenhouse.io/stripe"},
    {"name": "Figma", "slug": "figma", "provider": ATSProvider.GREENHOUSE, "url": "https://boards.greenhouse.io/figma"},
    {"name": "Airbnb", "slug": "airbnb", "provider": ATSProvider.GREENHOUSE, "url": "https://boards.greenhouse.io/airbnb"},
    {"name": "DoorDash", "slug": "doordash", "provider": ATSProvider.GREENHOUSE, "url": "https://boards.greenhouse.io/doordash"},
    {"name": "Databricks", "slug": "databricks", "provider": ATSProvider.GREENHOUSE, "url": "https://boards.greenhouse.io/databricks"},
    {"name": "Snowflake", "slug": "snowflake", "provider": ATSProvider.GREENHOUSE, "url": "https://boards.greenhouse.io/snowflake"},
    {"name": "Cloudflare", "slug": "cloudflare", "provider": ATSProvider.GREENHOUSE, "url": "https://boards.greenhouse.io/cloudflare"},
    {"name": "Reddit", "slug": "reddit", "provider": ATSProvider.GREENHOUSE, "url": "https://boards.greenhouse.io/reddit"},
    {"name": "Robinhood", "slug": "robinhood", "provider": ATSProvider.GREENHOUSE, "url": "https://boards.greenhouse.io/robinhood"},
    {"name": "Discord", "slug": "discord", "provider": ATSProvider.GREENHOUSE, "url": "https://boards.greenhouse.io/discord"},
    {"name": "Pinterest", "slug": "pinterest", "provider": ATSProvider.GREENHOUSE, "url": "https://boards.greenhouse.io/pinterest"},
    {"name": "Instacart", "slug": "instacart", "provider": ATSProvider.GREENHOUSE, "url": "https://boards.greenhouse.io/instacart"},
    {"name": "Coinbase", "slug": "coinbase", "provider": ATSProvider.GREENHOUSE, "url": "https://boards.greenhouse.io/coinbase"},
    {"name": "Twitch", "slug": "twitch", "provider": ATSProvider.GREENHOUSE, "url": "https://boards.greenhouse.io/twitch"},
    {"name": "Chime", "slug": "chime", "provider": ATSProvider.GREENHOUSE, "url": "https://boards.greenhouse.io/chime"},
    {"name": "Brex", "slug": "brex", "provider": ATSProvider.GREENHOUSE, "url": "https://boards.greenhouse.io/brex"},
    {"name": "Gusto", "slug": "gusto", "provider": ATSProvider.GREENHOUSE, "url": "https://boards.greenhouse.io/gusto"},
    {"name": "Samsara", "slug": "samsara", "provider": ATSProvider.GREENHOUSE, "url": "https://boards.greenhouse.io/samsara"},
    {"name": "Plaid", "slug": "plaid", "provider": ATSProvider.GREENHOUSE, "url": "https://boards.greenhouse.io/plaid"},
    {"name": "Scale AI", "slug": "scaleai", "provider": ATSProvider.GREENHOUSE, "url": "https://boards.greenhouse.io/scaleai"},
    {"name": "MongoDB", "slug": "mongodb", "provider": ATSProvider.GREENHOUSE, "url": "https://boards.greenhouse.io/mongodb"},
    {"name": "GitLab", "slug": "gitlab", "provider": ATSProvider.GREENHOUSE, "url": "https://boards.greenhouse.io/gitlab"},
    {"name": "Elastic", "slug": "elastic", "provider": ATSProvider.GREENHOUSE, "url": "https://boards.greenhouse.io/elastic"},
    {"name": "Asana", "slug": "asana", "provider": ATSProvider.GREENHOUSE, "url": "https://boards.greenhouse.io/asana"},
    {"name": "Jane Street", "slug": "janestreet", "provider": ATSProvider.GREENHOUSE, "url": "https://boards.greenhouse.io/janestreet"},
    {"name": "Citadel", "slug": "citadel", "provider": ATSProvider.GREENHOUSE, "url": "https://boards.greenhouse.io/citadel"},
    {"name": "Hudson River Trading", "slug": "hudsonrivertrading", "provider": ATSProvider.GREENHOUSE, "url": "https://boards.greenhouse.io/hudsonrivertrading"},
    {"name": "Two Sigma", "slug": "twosigma", "provider": ATSProvider.GREENHOUSE, "url": "https://boards.greenhouse.io/twosigma"},
    {"name": "Jump Trading", "slug": "jumptrading", "provider": ATSProvider.GREENHOUSE, "url": "https://boards.greenhouse.io/jumptrading"},
    {"name": "Anduril", "slug": "andurilindustries", "provider": ATSProvider.GREENHOUSE, "url": "https://boards.greenhouse.io/andurilindustries"},
    {"name": "Duolingo", "slug": "duolingo", "provider": ATSProvider.GREENHOUSE, "url": "https://boards.greenhouse.io/duolingo"},

    # --- Lever Boards ---
    {"name": "Palantir", "slug": "palantir", "provider": ATSProvider.LEVER, "url": "https://jobs.lever.co/palantir"},
    {"name": "Netflix", "slug": "netflix", "provider": ATSProvider.LEVER, "url": "https://jobs.lever.co/netflix"},
    {"name": "Yelp", "slug": "yelp", "provider": ATSProvider.LEVER, "url": "https://jobs.lever.co/yelp"},
    {"name": "Spotify", "slug": "spotify", "provider": ATSProvider.LEVER, "url": "https://jobs.lever.co/spotify"},
    {"name": "Affirm", "slug": "affirm", "provider": ATSProvider.LEVER, "url": "https://jobs.lever.co/affirm"},
    {"name": "Grammarly", "slug": "grammarly", "provider": ATSProvider.LEVER, "url": "https://jobs.lever.co/grammarly"},
    {"name": "Docker", "slug": "docker", "provider": ATSProvider.LEVER, "url": "https://jobs.lever.co/docker"},
    {"name": "Postman", "slug": "postman", "provider": ATSProvider.LEVER, "url": "https://jobs.lever.co/postman"},
    {"name": "Coursera", "slug": "coursera", "provider": ATSProvider.LEVER, "url": "https://jobs.lever.co/coursera"},
    {"name": "Atlassian", "slug": "atlassian", "provider": ATSProvider.LEVER, "url": "https://jobs.lever.co/atlassian"},
    {"name": "Datadog", "slug": "datadog", "provider": ATSProvider.LEVER, "url": "https://jobs.lever.co/datadog"},
    {"name": "Lyft", "slug": "lyft", "provider": ATSProvider.LEVER, "url": "https://jobs.lever.co/lyft"},

    # --- Ashby Boards ---
    {"name": "OpenAI", "slug": "openai", "provider": ATSProvider.ASHBY, "url": "https://jobs.ashbyhq.com/openai"},
    {"name": "Anthropic", "slug": "anthropic", "provider": ATSProvider.ASHBY, "url": "https://jobs.ashbyhq.com/anthropic"},
    {"name": "Ramp", "slug": "ramp", "provider": ATSProvider.ASHBY, "url": "https://jobs.ashbyhq.com/ramp"},
    {"name": "Retool", "slug": "retool", "provider": ATSProvider.ASHBY, "url": "https://jobs.ashbyhq.com/retool"},
    {"name": "Vercel", "slug": "vercel", "provider": ATSProvider.ASHBY, "url": "https://jobs.ashbyhq.com/vercel"},
    {"name": "Linear", "slug": "linear", "provider": ATSProvider.ASHBY, "url": "https://jobs.ashbyhq.com/linear"},
    {"name": "Notion", "slug": "notion", "provider": ATSProvider.ASHBY, "url": "https://jobs.ashbyhq.com/notion"},
    {"name": "Perplexity", "slug": "perplexity", "provider": ATSProvider.ASHBY, "url": "https://jobs.ashbyhq.com/perplexity"},
    {"name": "Cursor", "slug": "anysphere", "provider": ATSProvider.ASHBY, "url": "https://jobs.ashbyhq.com/anysphere"},
    {"name": "ElevenLabs", "slug": "elevenlabs", "provider": ATSProvider.ASHBY, "url": "https://jobs.ashbyhq.com/elevenlabs"},
    {"name": "Supabase", "slug": "supabase", "provider": ATSProvider.ASHBY, "url": "https://jobs.ashbyhq.com/supabase"},
    {"name": "Modal", "slug": "modal", "provider": ATSProvider.ASHBY, "url": "https://jobs.ashbyhq.com/modal"},
    {"name": "Replit", "slug": "replit", "provider": ATSProvider.ASHBY, "url": "https://jobs.ashbyhq.com/replit"},
    {"name": "Deel", "slug": "deel", "provider": ATSProvider.ASHBY, "url": "https://jobs.ashbyhq.com/deel"},
    {"name": "PostHog", "slug": "posthog", "provider": ATSProvider.ASHBY, "url": "https://jobs.ashbyhq.com/posthog"},
    {"name": "Railway", "slug": "railway", "provider": ATSProvider.ASHBY, "url": "https://jobs.ashbyhq.com/railway"},
    {"name": "Resend", "slug": "resend", "provider": ATSProvider.ASHBY, "url": "https://jobs.ashbyhq.com/resend"},
    {"name": "Cognition", "slug": "cognition", "provider": ATSProvider.ASHBY, "url": "https://jobs.ashbyhq.com/cognition"},

    # --- Workday CXS Tenants ---
    {
        "name": "Nvidia",
        "slug": "nvidia.wd5.myworkdayjobs.com/nvidia/NVIDIAExternalCareerSite",
        "provider": ATSProvider.WORKDAY,
        "url": "https://nvidia.wd5.myworkdayjobs.com/en-US/NVIDIAExternalCareerSite",
    },
    {
        "name": "Salesforce",
        "slug": "salesforce.wd12.myworkdayjobs.com/salesforce/External_Career_Site",
        "provider": ATSProvider.WORKDAY,
        "url": "https://salesforce.wd12.myworkdayjobs.com/en-US/External_Career_Site",
    },
    {
        "name": "Adobe",
        "slug": "adobe.wd5.myworkdayjobs.com/adobe/adobe_careers",
        "provider": ATSProvider.WORKDAY,
        "url": "https://adobe.wd5.myworkdayjobs.com/en-US/adobe_careers",
    },
    {
        "name": "Target",
        "slug": "target.wd5.myworkdayjobs.com/target/targetcareers",
        "provider": ATSProvider.WORKDAY,
        "url": "https://target.wd5.myworkdayjobs.com/en-US/targetcareers",
    },
]


def seed_registry(registry: CompanyRegistry) -> int:
    """Seed the database with initial known company ATS boards."""
    seeded = 0
    for item in INITIAL_BOARDS:
        board = CompanyBoard(
            company_name=item["name"],
            slug=item["slug"],
            provider=item["provider"],
            board_url=item["url"],
            active=True,
        )
        if registry.add_company(board):
            seeded += 1
    logger.info(f"Seeded {seeded} company boards into registry.")
    return seeded
