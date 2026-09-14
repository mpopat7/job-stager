"""Database engine and schema.

One schema serves both installs. A personal copy keeps its SQLite file and needs no
configuration; a deployment sets `DATABASE_URL` to Postgres and nothing else changes.
That duality is the point -- the open-source local tool and the hosted service are the
same code, so a fix to one is a fix to both.

Every table that holds anything a person typed is keyed by `user_id`. The company and
job registry is deliberately not: those rows are public postings scraped from public
boards, shared by everyone, and owned by nobody.
"""

from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import Optional
from urllib.parse import urlparse

from sqlalchemy import (
    BigInteger, Boolean, Column, DateTime, ForeignKey, Index, Integer, MetaData,
    String, Table, Text, UniqueConstraint, create_engine, false, func, insert, inspect, true,
)
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.engine import Engine

logger = logging.getLogger(__name__)

DEFAULT_DB_PATH = Path(__file__).parent.parent.parent / "jobstager.db"

metadata = MetaData()

users_table = Table(
    "users", metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("handle", String(120), nullable=False, unique=True),
    Column("email", String(320)),
    Column("password_hash", Text, nullable=False),
    Column("password_salt", Text, nullable=False),
    Column("email_verified", Boolean, nullable=False, server_default="0"),
    Column("created_at", DateTime(timezone=True), server_default=func.now()),
)

profiles_table = Table(
    "profiles", metadata,
    Column("user_id", Integer, ForeignKey("users.id", ondelete="CASCADE"), primary_key=True),
    Column("payload", Text, nullable=False),
    Column("updated_at", DateTime(timezone=True), server_default=func.now()),
)

field_provenance_table = Table(
    "field_provenance", metadata,
    Column("user_id", Integer, ForeignKey("users.id", ondelete="CASCADE"), primary_key=True),
    Column("field_path", String(200), primary_key=True),
    Column("source", String(40), nullable=False),
    Column("confirmed_at", DateTime(timezone=True)),
)

sessions_table = Table(
    "sessions", metadata,
    Column("token", String(64), primary_key=True),
    Column("user_id", Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
    Column("created_at", DateTime(timezone=True), server_default=func.now()),
    Column("expires_at", DateTime(timezone=True), nullable=False),
)

# Applications used to live in their own file with no owner column and a global
# UNIQUE(company, role, link). Two people applying to the same job collided on that
# constraint and the second write overwrote the first one's row.
applications_table = Table(
    "applications", metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("user_id", Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False,
           index=True),
    Column("date_applied", String(20), nullable=False),
    Column("company", String(300), nullable=False),
    Column("role", String(400), nullable=False),
    Column("source", String(60), nullable=False),
    Column("link", Text, nullable=False),
    Column("stage", String(60), nullable=False, server_default="Applied"),
    Column("grad_year", Integer, nullable=False),
    Column("notes", Text),
    Column("created_at", DateTime(timezone=True), server_default=func.now()),
    UniqueConstraint("user_id", "company", "role", "link", name="uq_application_per_user"),
)

# A match is private application state layered over the shared jobs registry. The job
# itself stays in `jobs`; only the user's relationship to its public id lives here.
# `candidate_job_ids` is used for an ambiguous Sheet row. Those ids are suggestions, not
# matches, so a single application is never recorded as belonging to several jobs.
job_matches_table = Table(
    "job_matches", metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("user_id", Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False,
           index=True),
    Column("job_id", String(500), index=True),
    Column("candidate_job_ids", Text),
    Column("origin", String(20), nullable=False),
    Column("status", String(20), nullable=False),
    Column("matched_by", String(40), nullable=False),
    Column("source_key", String(100), nullable=False),
    Column("external_row", Integer),
    Column("company", String(300), nullable=False),
    Column("role", String(400), nullable=False),
    Column("link", Text),
    Column("stage", String(60)),
    Column("date_applied", String(40)),
    Column("notes", Text),
    Column("created_at", DateTime(timezone=True), server_default=func.now()),
    Column("updated_at", DateTime(timezone=True), server_default=func.now()),
    UniqueConstraint("user_id", "origin", "source_key", name="uq_match_source_per_user"),
)

# Attempts against a credential endpoint, so a deployment can throttle without pulling in
# a cache server. Rows are pruned as they are counted.
login_attempts_table = Table(
    "login_attempts", metadata,
    Column("id", BigInteger().with_variant(Integer, "sqlite"), primary_key=True,
           autoincrement=True),
    Column("bucket", String(200), nullable=False, index=True),
    Column("attempted_at", DateTime(timezone=True), nullable=False),
)

# The public registry: boards and the postings scraped from them. It used to be its own
# companies.db file, which a host with no persistent disk loses on every restart.
companies_table = Table(
    "companies", metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("name", String(300), nullable=False),
    Column("slug", String(200), nullable=False),
    Column("provider", String(40), nullable=False),
    Column("board_url", Text, nullable=False),
    Column("career_url", Text),
    Column("verified", Boolean, nullable=False, server_default=true()),
    Column("active", Boolean, nullable=False, server_default=true()),
    Column("job_count", Integer, nullable=False, server_default="0"),
    Column("last_scanned", String(40)),
    Column("created_at", DateTime(timezone=True), server_default=func.now()),
    UniqueConstraint("provider", "slug", name="uq_company_board"),
)

jobs_table = Table(
    "jobs", metadata,
    Column("id", String(500), primary_key=True),
    Column("company", String(300)),
    Column("company_slug", String(200), nullable=False),
    Column("provider", String(40), nullable=False),
    Column("title", Text, nullable=False),
    Column("location", Text),
    Column("url", Text, nullable=False),
    Column("apply_url", Text),
    Column("is_internship", Boolean, nullable=False, server_default=false()),
    Column("updated_at", String(40)),
    Column("discovered_at", DateTime(timezone=True), server_default=func.now()),
    Index("ix_jobs_url", "url"),
)

MIGRATIONS_DIR = Path(__file__).parent / "migrations"
# The revision that matches what `create_all` built before migrations existed.
BASELINE_REVISION = "0001_baseline"
_BASELINE_TABLES = (
    "users", "profiles", "field_provenance", "sessions", "applications", "job_matches",
    "login_attempts",
)

_engines: dict[str, Engine] = {}
_migrated: set[str] = set()


def database_url(db_path: Optional[Path | str] = None) -> str:
    """Where the data lives.

    An explicit path wins so tests can point at a scratch file; otherwise `DATABASE_URL`
    if a deployment set one, else the local SQLite file.
    """
    if db_path is not None:
        text = str(db_path)
        if "://" in text:
            return _with_driver(text)
        return f"sqlite:///{Path(text).resolve()}"
    configured = os.getenv("DATABASE_URL", "").strip()
    if configured:
        return _with_driver(configured)
    return f"sqlite:///{Path(DEFAULT_DB_PATH).resolve()}"


def _with_driver(url: str) -> str:
    """Name the Postgres driver that is actually installed.

    Hosts hand out bare `postgres://` (Heroku-style) or `postgresql://` (Neon) URLs, and
    SQLAlchemy reads the bare form as psycopg2, which this project does not ship.
    """
    for bare in ("postgres://", "postgresql://"):
        if url.startswith(bare):
            return "postgresql+psycopg://" + url[len(bare):]
    return url


def is_sqlite(url: Optional[str] = None) -> bool:
    return urlparse(url or database_url()).scheme.startswith("sqlite")


def get_engine(db_path: Optional[Path | str] = None) -> Engine:
    """A pooled engine per database URL."""
    url = database_url(db_path)
    engine = _engines.get(url)
    if engine is not None:
        return engine

    kwargs: dict = {"future": True, "pool_pre_ping": True}
    if url.startswith("sqlite"):
        path = url.replace("sqlite:///", "", 1)
        if path and path != ":memory:":
            Path(path).parent.mkdir(parents=True, exist_ok=True)
        # FastAPI serves requests from a thread pool, and the default SQLite check
        # rejects a connection reused across threads.
        kwargs["connect_args"] = {"check_same_thread": False}

    engine = create_engine(url, **kwargs)
    if url.startswith("sqlite"):
        from sqlalchemy import event

        @event.listens_for(engine, "connect")
        def _enforce_foreign_keys(dbapi_conn, _record):
            dbapi_conn.execute("PRAGMA foreign_keys = ON")

    _engines[url] = engine
    return engine


def init_db(db_path: Optional[Path | str] = None) -> None:
    """Bring the schema up to the newest migration, once per database per process.

    Every store calls this from its constructor, so after the first call it has to cost
    nothing. A schema change is a new file in `migrations/versions/`, never an edit to
    the tables above alone -- `tests/test_migrations.py` fails when the two disagree.

    A database built by `create_all` before migrations existed has the baseline tables
    and no version row. It is stamped at the baseline rather than rebuilt, then upgraded
    like any other.
    """
    url = database_url(db_path)
    if url in _migrated:
        return

    from alembic import command
    from alembic.config import Config

    config = Config()
    config.set_main_option("script_location", str(MIGRATIONS_DIR))
    with get_engine(db_path).begin() as conn:
        config.attributes["connection"] = conn
        tables = set(inspect(conn).get_table_names())
        if "alembic_version" not in tables and "users" in tables:
            # A copy older than the newest baseline table is missing some of them too.
            metadata.create_all(conn, tables=[metadata.tables[n] for n in _BASELINE_TABLES])
            command.stamp(config, BASELINE_REVISION)
        command.upgrade(config, "head")
    _migrated.add(url)


def reset_engines() -> None:
    """Drop cached engines. Tests point the URL somewhere new between cases."""
    for engine in _engines.values():
        engine.dispose()
    _engines.clear()
    _migrated.clear()


def upsert(engine: Engine, table: Table, values: dict, index_elements, update_cols):
    """One upsert statement that both supported databases understand.

    SQLAlchemy Core has no dialect-neutral ON CONFLICT, and the two dialects spell it
    differently enough that every caller would otherwise carry this branch.

    Pass `values=None` for a statement to run many rows through at once,
    `conn.execute(stmt, rows)` -- one round trip instead of one per row, which matters
    against a database across a network.
    """
    dialect = engine.dialect.name
    if dialect == "postgresql":
        stmt = pg_insert(table)
    elif dialect == "sqlite":
        stmt = sqlite_insert(table)
    else:
        return insert(table) if values is None else insert(table).values(**values)
    if values is not None:
        stmt = stmt.values(**values)
    return stmt.on_conflict_do_update(
        index_elements=list(index_elements),
        set_={c: getattr(stmt.excluded, c) for c in update_cols},
    )
