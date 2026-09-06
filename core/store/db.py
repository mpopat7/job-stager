"""SQLite connection and schema for user-scoped data."""

from __future__ import annotations

import logging
from pathlib import Path
import sqlite3
from typing import Optional

logger = logging.getLogger(__name__)

DEFAULT_DB_PATH = Path(__file__).parent.parent.parent / "jobstager.db"


def get_connection(db_path: Optional[Path | str] = None) -> sqlite3.Connection:
    path = Path(db_path or DEFAULT_DB_PATH).resolve()
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def init_db(db_path: Optional[Path | str] = None) -> None:
    """Create the account and profile tables.

    A profile holds a home address, phone number and EEO self-identification, so every
    row is scoped to its owner and nothing here is readable without a user id.
    """
    with get_connection(db_path) as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS users (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                handle TEXT NOT NULL UNIQUE,
                email TEXT,
                password_hash TEXT NOT NULL,
                password_salt TEXT NOT NULL,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS profiles (
                user_id INTEGER PRIMARY KEY REFERENCES users(id) ON DELETE CASCADE,
                payload TEXT NOT NULL,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS field_provenance (
                user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                field_path TEXT NOT NULL,
                source TEXT NOT NULL,
                confirmed_at TIMESTAMP,
                PRIMARY KEY (user_id, field_path)
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS sessions (
                token TEXT PRIMARY KEY,
                user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                expires_at TIMESTAMP NOT NULL
            )
        """)
        conn.commit()
