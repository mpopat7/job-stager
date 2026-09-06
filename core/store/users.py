"""User accounts and login sessions."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
import hashlib
import logging
import os
from pathlib import Path
import secrets
import sqlite3
from typing import Optional

from core.store.db import get_connection, init_db

logger = logging.getLogger(__name__)

SESSION_TTL = timedelta(days=14)
# PBKDF2 is in the standard library; no dependency, and tunable as hardware improves.
PBKDF2_ROUNDS = 240_000


class UserStore:
    """Accounts, password verification, and session tokens."""

    def __init__(self, db_path: Optional[Path | str] = None):
        self.db_path = db_path
        init_db(db_path)

    def _conn(self) -> sqlite3.Connection:
        return get_connection(self.db_path)

    @staticmethod
    def _hash(password: str, salt: str) -> str:
        return hashlib.pbkdf2_hmac(
            "sha256", password.encode(), bytes.fromhex(salt), PBKDF2_ROUNDS
        ).hex()

    def create_user(self, handle: str, password: str, email: Optional[str] = None) -> int:
        handle = handle.strip().lower()
        if not handle:
            raise ValueError("handle is required")
        if len(password) < 8:
            raise ValueError("password must be at least 8 characters")
        salt = os.urandom(16).hex()
        with self._conn() as conn:
            try:
                cur = conn.execute(
                    "INSERT INTO users (handle, email, password_hash, password_salt) "
                    "VALUES (?, ?, ?, ?)",
                    (handle, email, self._hash(password, salt), salt),
                )
            except sqlite3.IntegrityError as err:
                raise ValueError(f"handle '{handle}' is already taken") from err
            conn.commit()
            return int(cur.lastrowid)

    def verify(self, handle: str, password: str) -> Optional[int]:
        with self._conn() as conn:
            row = conn.execute(
                "SELECT id, password_hash, password_salt FROM users WHERE handle = ?",
                (handle.strip().lower(),),
            ).fetchone()
        if not row:
            # Spend the same work on a missing handle so timing does not reveal it.
            self._hash(password, os.urandom(16).hex())
            return None
        expected = self._hash(password, row["password_salt"])
        if not secrets.compare_digest(expected, row["password_hash"]):
            return None
        return int(row["id"])

    def start_session(self, user_id: int) -> str:
        token = secrets.token_urlsafe(32)
        expires = datetime.now(timezone.utc) + SESSION_TTL
        with self._conn() as conn:
            conn.execute(
                "INSERT INTO sessions (token, user_id, expires_at) VALUES (?, ?, ?)",
                (token, user_id, expires.isoformat()),
            )
            conn.commit()
        return token

    def user_for_session(self, token: Optional[str]) -> Optional[int]:
        if not token:
            return None
        with self._conn() as conn:
            row = conn.execute(
                "SELECT user_id, expires_at FROM sessions WHERE token = ?", (token,)
            ).fetchone()
        if not row:
            return None
        try:
            if datetime.fromisoformat(row["expires_at"]) < datetime.now(timezone.utc):
                self.end_session(token)
                return None
        except ValueError:
            return None
        return int(row["user_id"])

    def end_session(self, token: str) -> None:
        with self._conn() as conn:
            conn.execute("DELETE FROM sessions WHERE token = ?", (token,))
            conn.commit()

    def count_users(self) -> int:
        with self._conn() as conn:
            return int(conn.execute("SELECT COUNT(*) FROM users").fetchone()[0])

    def handle_for(self, user_id: int) -> Optional[str]:
        with self._conn() as conn:
            row = conn.execute("SELECT handle FROM users WHERE id = ?", (user_id,)).fetchone()
        return row["handle"] if row else None
