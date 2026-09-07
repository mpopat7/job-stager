"""User accounts, login sessions, and credential-endpoint throttling."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
import hashlib
import logging
import os
from pathlib import Path
import secrets
from typing import Optional

from sqlalchemy import delete, func, insert, select, update
from sqlalchemy.exc import IntegrityError

from core.store.db import (
    get_engine, init_db, login_attempts_table, sessions_table, users_table,
)

logger = logging.getLogger(__name__)

SESSION_TTL = timedelta(days=14)
# PBKDF2 is in the standard library; no dependency, and tunable as hardware improves.
PBKDF2_ROUNDS = 240_000


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _aware(value) -> Optional[datetime]:
    """Read a timestamp back as UTC whichever driver returned it.

    SQLite hands back a naive datetime and Postgres an aware one; comparing the two
    raises, which would make every session look invalid on exactly one of the two.
    """
    if value is None:
        return None
    if isinstance(value, str):
        try:
            value = datetime.fromisoformat(value)
        except ValueError:
            return None
    return value if value.tzinfo else value.replace(tzinfo=timezone.utc)


class UserStore:
    """Accounts, password verification, and session tokens."""

    def __init__(self, db_path: Optional[Path | str] = None):
        self.db_path = db_path
        init_db(db_path)

    @property
    def engine(self):
        return get_engine(self.db_path)

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
        with self.engine.begin() as conn:
            try:
                result = conn.execute(
                    insert(users_table).values(
                        handle=handle,
                        email=email,
                        password_hash=self._hash(password, salt),
                        password_salt=salt,
                    )
                )
            except IntegrityError as err:
                raise ValueError(f"handle '{handle}' is already taken") from err
            return int(result.inserted_primary_key[0])

    def verify(self, handle: str, password: str) -> Optional[int]:
        with self.engine.connect() as conn:
            row = conn.execute(
                select(
                    users_table.c.id, users_table.c.password_hash, users_table.c.password_salt
                ).where(users_table.c.handle == handle.strip().lower())
            ).first()
        if row is None:
            # Spend the same work on a missing handle so timing does not reveal it.
            self._hash(password, os.urandom(16).hex())
            return None
        if not secrets.compare_digest(self._hash(password, row.password_salt), row.password_hash):
            return None
        return int(row.id)

    def start_session(self, user_id: int) -> str:
        token = secrets.token_urlsafe(32)
        with self.engine.begin() as conn:
            conn.execute(insert(sessions_table).values(
                token=token, user_id=user_id, created_at=_now(), expires_at=_now() + SESSION_TTL
            ))
        return token

    def user_for_session(self, token: Optional[str]) -> Optional[int]:
        if not token:
            return None
        with self.engine.connect() as conn:
            row = conn.execute(
                select(sessions_table.c.user_id, sessions_table.c.expires_at)
                .where(sessions_table.c.token == token)
            ).first()
        if row is None:
            return None
        expires = _aware(row.expires_at)
        if expires is None or expires < _now():
            self.end_session(token)
            return None
        return int(row.user_id)

    def end_session(self, token: str) -> None:
        with self.engine.begin() as conn:
            conn.execute(delete(sessions_table).where(sessions_table.c.token == token))

    def end_all_sessions(self, user_id: int) -> None:
        """Sign a user out everywhere, including the extension."""
        with self.engine.begin() as conn:
            conn.execute(delete(sessions_table).where(sessions_table.c.user_id == user_id))

    def purge_expired_sessions(self) -> int:
        with self.engine.begin() as conn:
            result = conn.execute(
                delete(sessions_table).where(sessions_table.c.expires_at < _now())
            )
        return int(result.rowcount or 0)

    def count_users(self) -> int:
        with self.engine.connect() as conn:
            return int(conn.execute(select(func.count()).select_from(users_table)).scalar_one())

    def handle_for(self, user_id: int) -> Optional[str]:
        with self.engine.connect() as conn:
            row = conn.execute(
                select(users_table.c.handle).where(users_table.c.id == user_id)
            ).first()
        return row.handle if row else None

    def ensure_local_user(self) -> int:
        """The account a personal install writes under.

        The command line has no login, but every row a person owns is keyed by a user
        now, so a solo install needs one account to own them. It adopts the existing
        account if there is exactly one -- the dashboard's -- and otherwise creates a
        `local` account with an unguessable password nobody is expected to type.
        """
        with self.engine.connect() as conn:
            rows = conn.execute(select(users_table.c.id).order_by(users_table.c.id)).fetchall()
        if len(rows) == 1:
            return int(rows[0].id)
        for row in rows:
            if self.handle_for(int(row.id)) == "local":
                return int(row.id)
        if rows:
            # Several accounts and none of them local: this is a shared install and the
            # caller has to say who it is acting for.
            raise RuntimeError(
                "More than one account exists; pass a user id rather than guessing one."
            )
        return self.create_user("local", secrets.token_urlsafe(24))

    def set_password(self, user_id: int, password: str) -> None:
        """Change a password and invalidate every session it protected."""
        if len(password) < 8:
            raise ValueError("password must be at least 8 characters")
        salt = os.urandom(16).hex()
        with self.engine.begin() as conn:
            conn.execute(
                update(users_table).where(users_table.c.id == user_id).values(
                    password_hash=self._hash(password, salt), password_salt=salt
                )
            )
        self.end_all_sessions(user_id)

    # -- throttling -------------------------------------------------------

    def too_many_attempts(self, bucket: str, limit: int, window: timedelta) -> bool:
        """Count recent attempts against one bucket, pruning what has aged out.

        Storing this alongside the accounts keeps a deployment to one moving part. It is
        the right size for a single web process; several processes behind a load balancer
        want a shared cache instead.
        """
        cutoff = _now() - window
        with self.engine.begin() as conn:
            conn.execute(
                delete(login_attempts_table).where(login_attempts_table.c.attempted_at < cutoff)
            )
            recent = conn.execute(
                select(func.count()).select_from(login_attempts_table)
                .where(login_attempts_table.c.bucket == bucket)
            ).scalar_one()
        return int(recent) >= limit

    def record_attempt(self, bucket: str) -> None:
        with self.engine.begin() as conn:
            conn.execute(insert(login_attempts_table).values(bucket=bucket, attempted_at=_now()))

    def clear_attempts(self, bucket: str) -> None:
        """Forget a bucket's history after a success, so one typo costs nothing later."""
        with self.engine.begin() as conn:
            conn.execute(
                delete(login_attempts_table).where(login_attempts_table.c.bucket == bucket)
            )
