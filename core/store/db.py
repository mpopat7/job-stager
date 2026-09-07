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
    BigInteger, Boolean, Column, DateTime, ForeignKey, Integer, MetaData,
    String, Table, Text, UniqueConstraint, create_engine, func, insert,
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
# itself stays in companies.db; only the user's relationship to its public id lives here.
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

_engines: dict[str, Engine] = {}


def database_url(db_path: Optional[Path | str] = None) -> str:
    """Where the data lives.

    An explicit path wins so tests can point at a scratch file; otherwise `DATABASE_URL`
    if a deployment set one, else the local SQLite file.
    """
    if db_path is not None:
        text = str(db_path)
        if "://" in text:
            return text
        return f"sqlite:///{Path(text).resolve()}"
    configured = os.getenv("DATABASE_URL", "").strip()
    if configured:
        # Heroku-style URLs name a driver SQLAlchemy 2 no longer ships.
        if configured.startswith("postgres://"):
            configured = configured.replace("postgres://", "postgresql+psycopg://", 1)
        return configured
    return f"sqlite:///{Path(DEFAULT_DB_PATH).resolve()}"


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
    """Create anything missing.

    Fine while the schema only grows. A deployment that has to *change* a column under
    live data needs migrations; that is the next thing to add here, not something
    `create_all` can be stretched to cover.
    """
    metadata.create_all(get_engine(db_path))


def reset_engines() -> None:
    """Drop cached engines. Tests point the URL somewhere new between cases."""
    for engine in _engines.values():
        engine.dispose()
    _engines.clear()


def upsert(engine: Engine, table: Table, values: dict, index_elements, update_cols):
    """One upsert statement that both supported databases understand.

    SQLAlchemy Core has no dialect-neutral ON CONFLICT, and the two dialects spell it
    differently enough that every caller would otherwise carry this branch.
    """
    dialect = engine.dialect.name
    if dialect == "postgresql":
        stmt = pg_insert(table).values(**values)
    elif dialect == "sqlite":
        stmt = sqlite_insert(table).values(**values)
    else:
        return insert(table).values(**values)
    return stmt.on_conflict_do_update(
        index_elements=list(index_elements),
        set_={c: getattr(stmt.excluded, c) for c in update_cols},
    )
