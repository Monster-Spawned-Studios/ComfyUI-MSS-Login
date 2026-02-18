# --- START OF FILE utils/session_token_store.py ---
"""
Session JWT store: track issued session JWTs by jti for listing and revocation.
Stores (jti, user_id, username, created_at_iso, last_used_at_iso, exp_at_iso).
Blocklist for revoked jtis. Idle sessions are revoked after a configurable timeout.

Backends: SQLite (with optional SQLCipher encryption) and PostgreSQL.
The legacy JSON backend is retained only for automatic one-time migration.
"""

import json
import os
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import List, Optional


DEFAULT_IDLE_REVOKE_MINUTES = 5

_SESSION_SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS session_tokens (
    jti              TEXT PRIMARY KEY,
    user_id          TEXT NOT NULL,
    username         TEXT NOT NULL,
    created_at_iso   TEXT NOT NULL,
    last_used_at_iso TEXT NOT NULL,
    exp_at_iso       TEXT,
    revoked          INTEGER NOT NULL DEFAULT 0
)
"""


def _iso_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _is_expired(exp_at_iso: Optional[str]) -> bool:
    if not exp_at_iso:
        return False
    try:
        t = datetime.fromisoformat(exp_at_iso.replace("Z", "+00:00"))
        return datetime.now(timezone.utc) >= t
    except Exception:
        return False


def _parse_iso(iso_str: Optional[str]) -> Optional[datetime]:
    if not iso_str:
        return None
    try:
        return datetime.fromisoformat(iso_str.replace("Z", "+00:00"))
    except Exception:
        return None


# ---------------------------------------------------------------------------
# SQLite backend (uses same encryption infra as users_db / api_token_store)
# ---------------------------------------------------------------------------


class _SqliteSessionStore:
    """Encrypted SQLite-backed session token store."""

    def __init__(
        self,
        db_path: str,
        secret_key: str = "",
        encryption_level: str = "",
        idle_revoke_minutes: int = DEFAULT_IDLE_REVOKE_MINUTES,
    ):
        from .sqlite_connection import open_sqlite

        self._conn = open_sqlite(
            db_path,
            secret_key=secret_key,
            encryption_level=encryption_level or "",
            check_same_thread=False,
        )
        self._conn.execute(_SESSION_SCHEMA_SQL)
        self._conn.commit()
        self._idle_revoke_minutes = idle_revoke_minutes

    def register_session(
        self,
        jti: str,
        user_id: str,
        username: str,
        exp_at_iso: Optional[str] = None,
    ) -> None:
        now = _iso_now()
        self._conn.execute(
            "INSERT OR REPLACE INTO session_tokens "
            "(jti, user_id, username, created_at_iso, last_used_at_iso, exp_at_iso, revoked) "
            "VALUES (?, ?, ?, ?, ?, ?, 0)",
            (jti, user_id, username, now, now, exp_at_iso),
        )
        self._conn.commit()

    def update_last_used(self, jti: str) -> bool:
        now = _iso_now()
        cur = self._conn.execute(
            "UPDATE session_tokens SET last_used_at_iso = ? WHERE jti = ? AND revoked = 0",
            (now, jti),
        )
        self._conn.commit()
        return cur.rowcount > 0

    def is_revoked(self, jti: str) -> bool:
        row = self._conn.execute(
            "SELECT revoked FROM session_tokens WHERE jti = ?", (jti,)
        ).fetchone()
        if row is None:
            return False
        return bool(row[0])

    def revoke_idle_sessions(self, idle_minutes: Optional[int] = None) -> int:
        minutes = idle_minutes if idle_minutes is not None else self._idle_revoke_minutes
        cutoff = (datetime.now(timezone.utc) - timedelta(minutes=minutes)).isoformat()
        cur = self._conn.execute(
            "UPDATE session_tokens SET revoked = 1 "
            "WHERE revoked = 0 AND last_used_at_iso < ?",
            (cutoff,),
        )
        self._conn.commit()
        return cur.rowcount

    def prune_old_sessions(self) -> int:
        now_iso = _iso_now()
        cur = self._conn.execute(
            "DELETE FROM session_tokens WHERE revoked = 1 "
            "OR (exp_at_iso IS NOT NULL AND exp_at_iso != '' AND exp_at_iso < ?)",
            (now_iso,),
        )
        self._conn.commit()
        return cur.rowcount

    def list_sessions_for_user(self, username: str) -> List[dict]:
        rows = self._conn.execute(
            "SELECT jti, created_at_iso, last_used_at_iso, exp_at_iso "
            "FROM session_tokens WHERE username = ? AND revoked = 0",
            (username,),
        ).fetchall()
        out = []
        for jti, created, last_used, exp in rows:
            if _is_expired(exp):
                continue
            out.append(
                {
                    "jti": jti,
                    "created_at_iso": created,
                    "last_used_at_iso": last_used,
                    "exp_at_iso": exp,
                }
            )
        return out

    def revoke_session(self, jti: str) -> bool:
        cur = self._conn.execute(
            "UPDATE session_tokens SET revoked = 1 WHERE jti = ?", (jti,)
        )
        self._conn.commit()
        return cur.rowcount > 0


# ---------------------------------------------------------------------------
# PostgreSQL backend
# ---------------------------------------------------------------------------


def _get_postgres_session_store(
    host: str,
    port: int,
    database: str,
    user: str,
    password: str,
    idle_revoke_minutes: int = DEFAULT_IDLE_REVOKE_MINUTES,
):
    try:
        import psycopg2
        from psycopg2.extras import RealDictCursor
    except ImportError:
        raise RuntimeError(
            "PostgreSQL session store requires psycopg2; install with: pip install psycopg2-binary"
        )

    conn = psycopg2.connect(
        host=host, port=port, dbname=database, user=user, password=password
    )
    cur = conn.cursor()
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS session_tokens (
            jti              TEXT PRIMARY KEY,
            user_id          TEXT NOT NULL,
            username         TEXT NOT NULL,
            created_at_iso   TEXT NOT NULL,
            last_used_at_iso TEXT NOT NULL,
            exp_at_iso       TEXT,
            revoked          INTEGER NOT NULL DEFAULT 0
        )
        """
    )
    conn.commit()
    cur.close()

    class _PostgresSessionStore:
        def __init__(self, conn, idle_revoke_minutes: int):
            self._conn = conn
            self._idle_revoke_minutes = idle_revoke_minutes

        def _cursor(self):
            return self._conn.cursor(cursor_factory=RealDictCursor)

        def register_session(
            self,
            jti: str,
            user_id: str,
            username: str,
            exp_at_iso: Optional[str] = None,
        ) -> None:
            now = _iso_now()
            with self._conn.cursor() as cur:
                cur.execute(
                    "INSERT INTO session_tokens "
                    "(jti, user_id, username, created_at_iso, last_used_at_iso, exp_at_iso, revoked) "
                    "VALUES (%s, %s, %s, %s, %s, %s, 0) "
                    "ON CONFLICT (jti) DO UPDATE SET "
                    "user_id = EXCLUDED.user_id, username = EXCLUDED.username, "
                    "created_at_iso = EXCLUDED.created_at_iso, last_used_at_iso = EXCLUDED.last_used_at_iso, "
                    "exp_at_iso = EXCLUDED.exp_at_iso, revoked = 0",
                    (jti, user_id, username, now, now, exp_at_iso),
                )
                self._conn.commit()

        def update_last_used(self, jti: str) -> bool:
            now = _iso_now()
            with self._conn.cursor() as cur:
                cur.execute(
                    "UPDATE session_tokens SET last_used_at_iso = %s WHERE jti = %s AND revoked = 0",
                    (now, jti),
                )
                self._conn.commit()
                return cur.rowcount > 0

        def is_revoked(self, jti: str) -> bool:
            with self._cursor() as cur:
                cur.execute(
                    "SELECT revoked FROM session_tokens WHERE jti = %s", (jti,)
                )
                row = cur.fetchone()
            if row is None:
                return False
            return bool(row["revoked"])

        def revoke_idle_sessions(self, idle_minutes: Optional[int] = None) -> int:
            minutes = (
                idle_minutes if idle_minutes is not None else self._idle_revoke_minutes
            )
            cutoff = (
                datetime.now(timezone.utc) - timedelta(minutes=minutes)
            ).isoformat()
            with self._conn.cursor() as cur:
                cur.execute(
                    "UPDATE session_tokens SET revoked = 1 "
                    "WHERE revoked = 0 AND last_used_at_iso < %s",
                    (cutoff,),
                )
                self._conn.commit()
                return cur.rowcount

        def prune_old_sessions(self) -> int:
            now_iso = _iso_now()
            with self._conn.cursor() as cur:
                cur.execute(
                    "DELETE FROM session_tokens WHERE revoked = 1 "
                    "OR (exp_at_iso IS NOT NULL AND exp_at_iso != '' AND exp_at_iso < %s)",
                    (now_iso,),
                )
                self._conn.commit()
                return cur.rowcount

        def list_sessions_for_user(self, username: str) -> List[dict]:
            with self._cursor() as cur:
                cur.execute(
                    "SELECT jti, created_at_iso, last_used_at_iso, exp_at_iso "
                    "FROM session_tokens WHERE username = %s AND revoked = 0",
                    (username,),
                )
                rows = cur.fetchall()
            out = []
            for r in rows:
                if _is_expired(r["exp_at_iso"]):
                    continue
                out.append(
                    {
                        "jti": r["jti"],
                        "created_at_iso": r["created_at_iso"],
                        "last_used_at_iso": r["last_used_at_iso"],
                        "exp_at_iso": r["exp_at_iso"],
                    }
                )
            return out

        def revoke_session(self, jti: str) -> bool:
            with self._conn.cursor() as cur:
                cur.execute(
                    "UPDATE session_tokens SET revoked = 1 WHERE jti = %s", (jti,)
                )
                self._conn.commit()
                return cur.rowcount > 0

    return _PostgresSessionStore(conn, idle_revoke_minutes)


# ---------------------------------------------------------------------------
# Legacy JSON migration helper
# ---------------------------------------------------------------------------


def _migrate_json_to_backend(json_path: str, store) -> int:
    """
    One-time migration: read legacy JSON session store and insert active
    sessions into the new backend. Returns number of records migrated.
    """
    if not os.path.isfile(json_path):
        return 0
    try:
        with open(json_path, "r", encoding="utf-8") as f:
            data = json.load(f)
    except (json.JSONDecodeError, OSError):
        return 0

    sessions = data.get("sessions", [])
    blocklist = set(data.get("blocklist", []))
    migrated = 0
    for rec in sessions:
        jti = rec.get("jti")
        if not jti:
            continue
        if jti in blocklist or _is_expired(rec.get("exp_at_iso")):
            continue
        store.register_session(
            jti,
            rec.get("user_id", ""),
            rec.get("username", ""),
            rec.get("exp_at_iso"),
        )
        migrated += 1

    # Rename the old file so migration doesn't run again
    try:
        os.rename(json_path, json_path + ".migrated")
    except OSError:
        pass
    return migrated


# ---------------------------------------------------------------------------
# Factory and singleton
# ---------------------------------------------------------------------------

_session_store = None


def get_session_token_store(
    config: Optional[dict] = None,
    idle_revoke_minutes: Optional[int] = None,
) -> "_SqliteSessionStore":
    """
    Build or return the singleton session token store from config.
    Uses the same DB backend as users_db (SQLite w/ encryption or PostgreSQL).

    config should be the SESSION_TOKEN_STORE_CONFIG dict from constants
    (keys: backend, sqlite_path, encryption_level, postgres_*, legacy_json_path).
    """
    global _session_store
    if _session_store is not None:
        return _session_store

    if config is None:
        config = {}

    if idle_revoke_minutes is None:
        try:
            from ..constants import SESSION_IDLE_REVOKE_MINUTES

            idle_revoke_minutes = SESSION_IDLE_REVOKE_MINUTES
        except ImportError:
            idle_revoke_minutes = DEFAULT_IDLE_REVOKE_MINUTES

    backend = (config.get("backend") or "sqlite").lower()
    legacy_json = config.get("legacy_json_path") or ""

    if backend == "postgresql":
        _session_store = _get_postgres_session_store(
            host=config.get("postgres_host", "localhost"),
            port=int(config.get("postgres_port", 5432)),
            database=config.get("postgres_database", "mss_login"),
            user=config.get("postgres_user", "mss_login"),
            password=config.get("postgres_password", ""),
            idle_revoke_minutes=idle_revoke_minutes,
        )
    else:
        db_path = config.get("sqlite_path") or ""
        if not db_path:
            try:
                from ..constants import USERS_DB_CONFIG

                db_path = USERS_DB_CONFIG.get("sqlite_path", "data/users.db")
            except ImportError:
                db_path = "data/users.db"

        secret_key = config.get("secret_key") or ""
        if not secret_key:
            try:
                from ..constants import SECRET_KEY as _sk

                secret_key = _sk
            except ImportError:
                secret_key = ""

        encryption_level = config.get("encryption_level") or ""
        _session_store = _SqliteSessionStore(
            db_path=db_path,
            secret_key=secret_key,
            encryption_level=encryption_level,
            idle_revoke_minutes=idle_revoke_minutes,
        )

    # One-time migration from legacy JSON if it exists
    if legacy_json and os.path.isfile(legacy_json):
        _migrate_json_to_backend(legacy_json, _session_store)

    return _session_store


def reset_session_token_store() -> None:
    """Reset global store (e.g. after config change)."""
    global _session_store
    _session_store = None
