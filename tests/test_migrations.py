"""Schema migrations: a new database, one older than migrations, and the old registry file.

The Postgres case runs only when JOBSTAGER_TEST_DATABASE_URL names a disposable database --
it drops every table in it first.
"""

import json
import os
import sqlite3

from alembic.autogenerate import compare_metadata
from alembic.runtime.migration import MigrationContext
import pytest
from sqlalchemy import inspect, text

from core.registry.store import CompanyRegistry
from core.scrapers.base import ATSProvider, CompanyBoard, JobPosting
from core.store import db as db_mod
from core.store.db import get_engine, init_db, metadata
from core.store.migrate import import_legacy_registry
from core.store.users import UserStore


def _head_revision() -> str:
    """Read the latest revision from the migration scripts themselves.

    Spelling it out as a constant meant every new migration failed this file until
    someone remembered to bump it, which teaches the wrong reflex about a red test.
    """
    from alembic.config import Config
    from alembic.script import ScriptDirectory

    config = Config()
    config.set_main_option("script_location", str(db_mod.MIGRATIONS_DIR))
    return ScriptDirectory.from_config(config).get_current_head()


HEAD = _head_revision()
PG_URL = os.getenv("JOBSTAGER_TEST_DATABASE_URL")

LEGACY_REGISTRY_SQL = """
CREATE TABLE companies (
    id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT NOT NULL, slug TEXT NOT NULL,
    provider TEXT NOT NULL, board_url TEXT NOT NULL, career_url TEXT,
    verified INTEGER DEFAULT 1, active INTEGER DEFAULT 1, job_count INTEGER DEFAULT 0,
    last_scanned TIMESTAMP, created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(provider, slug)
);
CREATE TABLE jobs (
    id TEXT PRIMARY KEY, company TEXT, company_slug TEXT NOT NULL, provider TEXT NOT NULL,
    title TEXT NOT NULL, location TEXT, url TEXT NOT NULL, apply_url TEXT,
    is_internship INTEGER DEFAULT 0, status TEXT DEFAULT 'discovered', updated_at TEXT,
    discovered_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP, stage TEXT, applied_date TEXT, notes TEXT
);
INSERT INTO companies (name, slug, provider, board_url, active, job_count, last_scanned)
VALUES ('Acme', 'acme', 'ashby', 'https://jobs.ashbyhq.com/acme', 1, 3, '2026-09-06T01:00:00');
INSERT INTO jobs (id, company, company_slug, provider, title, location, url, apply_url,
                  is_internship, status, stage, updated_at, discovered_at)
VALUES ('ashby:acme:1', 'Acme', 'acme', 'ashby', 'Software Engineer Intern', 'Remote',
        'https://jobs.ashbyhq.com/acme/1', 'https://jobs.ashbyhq.com/acme/1', 1, 'applied',
        'Applied', NULL, '2026-09-06 01:12:00');
"""


@pytest.fixture(autouse=True)
def _fresh_engines():
    db_mod.reset_engines()
    yield
    db_mod.reset_engines()


def _version(db) -> str:
    with get_engine(db).connect() as conn:
        return conn.execute(text("SELECT version_num FROM alembic_version")).scalar_one()


def _drift(db) -> list:
    with get_engine(db).connect() as conn:
        return compare_metadata(MigrationContext.configure(conn), metadata)


def test_a_new_database_is_built_by_migrations(tmp_path):
    db = tmp_path / "new.db"
    init_db(db)
    assert _version(db) == HEAD


def test_migrations_build_exactly_the_schema_the_code_declares(tmp_path):
    # A table edited in db.py without a migration passes every other test, because those
    # run on fresh databases. This is the one that notices.
    db = tmp_path / "drift.db"
    init_db(db)
    assert _drift(db) == []


def test_a_stamped_database_gains_the_columns_it_never_got(tmp_path):
    """The case above builds `users` from today's metadata, so it always had every column.

    A real database from before migrations did not: it was created by an older
    `create_all` and then stamped, so a column added to the model afterwards was never
    created on disk and no migration had reason to add it. This reproduces that shape
    with raw SQL and checks the repair lands.
    """
    db = tmp_path / "stamped.db"
    with get_engine(db).begin() as conn:
        conn.execute(text("""
            CREATE TABLE users (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                handle TEXT NOT NULL UNIQUE,
                email TEXT,
                password_hash TEXT NOT NULL,
                password_salt TEXT NOT NULL,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """))
        conn.execute(text(
            "INSERT INTO users (handle, password_hash, password_salt)"
            " VALUES ('kept', 'h', 's')"
        ))

    init_db(db)

    assert _version(db) == HEAD
    with get_engine(db).connect() as conn:
        columns = {r[1] for r in conn.execute(text("PRAGMA table_info(users)"))}
        assert "email_verified" in columns
        assert conn.execute(text("SELECT handle FROM users")).scalar_one() == "kept"
        # The account survives an external sign-in writing the column it was missing.
        conn.execute(text("SELECT email_verified FROM users")).scalar_one()


def test_a_database_from_before_migrations_is_stamped_not_rebuilt(tmp_path):
    db = tmp_path / "old.db"
    users = metadata.tables["users"]
    with get_engine(db).begin() as conn:
        metadata.create_all(conn, tables=[
            metadata.tables[name] for name in ("users", "profiles", "sessions")
        ])
        conn.execute(users.insert().values(handle="kept", password_hash="h", password_salt="s"))

    init_db(db)

    assert _version(db) == HEAD
    assert _drift(db) == []
    with get_engine(db).connect() as conn:
        assert conn.execute(text("SELECT handle FROM users")).scalar_one() == "kept"


def test_the_old_registry_file_imports_and_imports_again_without_duplicates(tmp_path):
    legacy = tmp_path / "companies.db"
    with sqlite3.connect(legacy) as conn:
        conn.executescript(LEGACY_REGISTRY_SQL)
    db = tmp_path / "app.db"

    assert import_legacy_registry(legacy, db) == (1, 1)
    assert import_legacy_registry(legacy, db) == (1, 1)

    registry = CompanyRegistry(db)
    assert registry.count_companies() == 1
    assert registry.count_jobs() == 1
    assert registry.get_company("ACME").provider is ATSProvider.ASHBY
    job = registry.get_jobs()[0]
    assert job.status == "discovered"  # the old per-file "applied" flag is not carried over
    assert job.discovered_at == "2026-09-06 01:12:00"


def _exercise_registry(registry: CompanyRegistry) -> None:
    board = CompanyBoard(company_name="Acme", slug="acme", provider=ATSProvider.ASHBY,
                         board_url="https://jobs.ashbyhq.com/acme", job_count=1)
    assert registry.add_company(board)
    assert registry.add_company(board.model_copy(update={"job_count": 2}))
    assert registry.count_companies() == 1
    assert registry.get_company("acme").job_count == 2

    def job(job_id, updated_at):
        url = f"https://jobs.ashbyhq.com/acme/{job_id}"
        return JobPosting(id=job_id, title="Data Intern", company="Acme", company_slug="acme",
                          url=url, apply_url=url, provider=ATSProvider.ASHBY,
                          is_internship=True, updated_at=updated_at)

    assert registry.upsert_jobs([job("1", None), job("2", "2026-09-10")]) == 2
    assert registry.upsert_jobs([job("1", "2026-09-01"), job("3", None)]) == 1
    assert registry.count_jobs() == 3
    # Dated postings first, undated last, on either database.
    assert [j.id for j in registry.get_jobs()][:2] == ["ashby:acme:2", "ashby:acme:1"]
    assert registry.get_jobs(exclude_ids={"ashby:acme:2"}, limit=None)[0].id == "ashby:acme:1"
    assert registry.find_job(url="https://jobs.ashbyhq.com/acme/3")["id"] == "ashby:acme:3"


def test_the_registry_on_sqlite(tmp_path):
    _exercise_registry(CompanyRegistry(tmp_path / "registry.db"))


@pytest.mark.skipif(not PG_URL, reason="set JOBSTAGER_TEST_DATABASE_URL to a disposable Postgres")
def test_the_whole_schema_on_postgres():
    engine = get_engine(PG_URL)
    with engine.begin() as conn:
        for table in inspect(conn).get_table_names():
            conn.execute(text(f'DROP TABLE IF EXISTS "{table}" CASCADE'))

    init_db(PG_URL)
    assert _version(PG_URL) == HEAD
    assert _drift(PG_URL) == []

    _exercise_registry(CompanyRegistry(PG_URL))
    users = UserStore(PG_URL)
    users.create_user("pguser", "pg-password-long-enough")
    assert users.count_users() == 1


def _upgrade_to(db, revision: str) -> None:
    from alembic import command
    from alembic.config import Config

    config = Config()
    config.set_main_option("script_location", str(db_mod.MIGRATIONS_DIR))
    with get_engine(db).begin() as conn:
        config.attributes["connection"] = conn
        command.upgrade(config, revision)


def test_workday_boards_filed_under_job_are_split_into_their_real_portals(tmp_path):
    """The old resolver took the literal `job` as the portal for any locale-less URL.

    One fake board collected jobs from two real portals; the repair files each job under
    the portal its URL names, keeps a user's match pointing at the same posting, and
    drops the fake board.
    """
    db = tmp_path / "workday.db"
    _upgrade_to(db, "0004_stamped_users_columns")
    host = "tencent.wd1.myworkdayjobs.com"
    bad = f"{host}/tencent/job"
    with get_engine(db).begin() as conn:
        conn.execute(text(
            "INSERT INTO users (id, handle, password_hash, password_salt, email_verified)"
            " VALUES (1, 'u', 'h', 's', 0)"
        ))
        conn.execute(text(
            "INSERT INTO companies (name, slug, provider, board_url, verified, active, job_count)"
            " VALUES ('Tencent', :slug, 'workday', :url, 1, 1, 0)"
        ), {"slug": bad, "url": f"https://{bad}"})
        for job_id, portal in (("a", "Tencent_Careers"), ("b", "OA_Huoshui_Platform")):
            conn.execute(text(
                "INSERT INTO jobs (id, company, company_slug, provider, title, url, is_internship)"
                " VALUES (:id, 'Tencent', :slug, 'workday', 'Intern', :url, 1)"
            ), {"id": f"workday:{bad}:{job_id}", "slug": bad,
                "url": f"https://{host}/{portal}/job/UK-London/NLP-Research-Intern_R1"})
        conn.execute(text(
            "INSERT INTO job_matches (user_id, job_id, candidate_job_ids, origin, status,"
            " matched_by, source_key, company, role)"
            " VALUES (1, :job, :candidates, 'sheet', 'confirmed', 'url', 'k', 'Tencent', 'Intern')"
        ), {"job": f"workday:{bad}:a", "candidates": json.dumps([f"workday:{bad}:b"])})

    init_db(db)

    careers = f"{host}/tencent/Tencent_Careers"
    platform = f"{host}/tencent/OA_Huoshui_Platform"
    with get_engine(db).connect() as conn:
        slugs = set(conn.execute(text(
            "SELECT slug FROM companies WHERE provider = 'workday'")).scalars())
        assert slugs == {careers, platform}
        assert set(conn.execute(text("SELECT id FROM jobs")).scalars()) == {
            f"workday:{careers}:a", f"workday:{platform}:b"}
        match = conn.execute(text("SELECT job_id, candidate_job_ids FROM job_matches")).one()
        assert match.job_id == f"workday:{careers}:a"
        assert json.loads(match.candidate_job_ids) == [f"workday:{platform}:b"]
        board_url = conn.execute(text(
            "SELECT board_url FROM companies WHERE slug = :s"), {"s": careers}).scalar_one()
        assert board_url == f"https://{host}/Tencent_Careers"


def test_stored_dates_become_sortable_and_jobs_gain_a_role_family(tmp_path):
    db = tmp_path / "dates.db"
    _upgrade_to(db, "0005_workday_board_slugs")
    rows = [
        ("workday:a", "Software Engineer Intern", "Posted 3 Days Ago"),
        ("lever:b", "Finance Intern", "1788371280643"),
        ("workday:c", "Marketing Intern", "Tempe, AZ"),
        ("ashby:d", "Data Science Intern", "2026-09-01"),
    ]
    with get_engine(db).begin() as conn:
        for job_id, title, posted in rows:
            conn.execute(text(
                "INSERT INTO jobs (id, company_slug, provider, title, url, is_internship,"
                " updated_at, discovered_at) VALUES (:id, 's', 'x', :title, :id, 1, :posted,"
                " '2026-09-24 06:00:00')"
            ), {"id": job_id, "title": title, "posted": posted})

    init_db(db)

    with get_engine(db).connect() as conn:
        got = dict((r[0], (r[1], r[2])) for r in conn.execute(
            text("SELECT id, updated_at, role_family FROM jobs")))
    assert got == {
        "workday:a": ("2026-09-21", "Software Engineering"),
        "lever:b": ("2026-09-02", "Finance"),
        "workday:c": (None, "Other"),
        "ashby:d": ("2026-09-01", "Data Science"),
    }
