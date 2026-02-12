# --- START OF FILE utils/api_token_store.py ---
"""
API Token Store: long-lived Bearer tokens per user.
Supports JSON file, SQLite, and PostgreSQL backends.
Stores only SHA-256 hashes of tokens; raw token is returned once on creation.
"""
import hashlib
import json
import os
import secrets
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Optional

try:
    from ..constants import _default_sqlite_path
except ImportError:
    def _default_sqlite_path() -> str:
        import sys
        if sys.platform == "win32":
            base = os.environ.get("LOCALAPPDATA", "") or os.path.expanduser("~\\AppData\\Local")
        elif sys.platform == "darwin":
            base = os.path.expanduser("~/Library/Application Support")
        else:
            base = os.environ.get("XDG_DATA_HOME", "") or os.path.expanduser("~/.local/share")
        return os.path.join(base, "mss_login", "api_tokens.db")

# Default local-network CIDRs (used by remote_api_guard; defined here for reference only)
DEFAULT_LOCAL_NETWORK_CIDRS = [
    "127.0.0.0/8",
    "::1/128",
    "10.0.0.0/8",
    "172.16.0.0/12",
    "192.168.0.0/16",
    "172.17.0.0/16",  # Docker default bridge
]


def _normalize_lookup_token(token: str) -> str:
    """Strip token for lookup so client whitespace doesn't break matching."""
    return (token or "").strip()


def _hash_token(token: str) -> str:
    """Return SHA-256 hex digest of token. Never log the result in user-facing logs."""
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


# Sentinel for "never expires" (when expire_hours is 0 and user has permission)
NEVER_EXPIRES_ISO = "9999-12-31T23:59:59+00:00"


def _iso_expires(expire_hours: float) -> str:
    """Return expires_iso. If expire_hours <= 0, return never-expires sentinel."""
    if expire_hours <= 0:
        return NEVER_EXPIRES_ISO
    t = datetime.now(timezone.utc) + timedelta(hours=expire_hours)
    return t.isoformat()


def _is_expired(expires_iso: str) -> bool:
    try:
        if not expires_iso or expires_iso == NEVER_EXPIRES_ISO:
            return False
        t = datetime.fromisoformat(expires_iso.replace("Z", "+00:00"))
        return datetime.now(timezone.utc) >= t
    except Exception:
        return True


# ---------------------------------------------------------------------------
# JSON backend
# ---------------------------------------------------------------------------


class _JsonTokenStore:
    def __init__(self, file_path: str):
        self._path = Path(file_path)
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._data: dict = {}
        self._load()

    def _load(self) -> None:
        if self._path.exists():
            try:
                with open(self._path, "r", encoding="utf-8") as f:
                    self._data = json.load(f)
            except (json.JSONDecodeError, OSError):
                self._data = {}
        else:
            self._data = {}

    def _save(self) -> None:
        with open(self._path, "w", encoding="utf-8") as f:
            json.dump(self._data, f, indent=2)

    def get_user_for_token(self, token: str):
        h = _hash_token(_normalize_lookup_token(token))
        rec = self._data.get(h)
        if not rec:
            return None
        if _is_expired(rec.get("expires_iso", "")):
            del self._data[h]
            self._save()
            return None
        return (rec.get("user_id"), rec.get("username"))

    def create_token(self, user_id: str, username: str, expire_hours: float) -> str:
        raw = secrets.token_urlsafe(32)
        h = _hash_token(raw)
        self._data[h] = {
            "user_id": user_id,
            "username": username,
            "expires_iso": _iso_expires(expire_hours),
        }
        self._save()
        return raw

    def revoke_token(self, token: str) -> bool:
        h = _hash_token(_normalize_lookup_token(token))
        if h in self._data:
            del self._data[h]
            self._save()
            return True
        return False

    def list_tokens_for_user(self, username: str) -> list:
        out = []
        for h, rec in list(self._data.items()):
            if rec.get("username") == username and not _is_expired(rec.get("expires_iso", "")):
                out.append({"token_hash_prefix": h[:8] + "...", "expires_iso": rec.get("expires_iso")})
        return out


# ---------------------------------------------------------------------------
# SQLite backend
# ---------------------------------------------------------------------------


class _SqliteTokenStore:
    def __init__(self, db_path: str):
        self._path = Path(db_path)
        self._path.parent.mkdir(parents=True, exist_ok=True)
        import sqlite3
        self._conn = sqlite3.connect(str(self._path), check_same_thread=False)
        self._conn.execute(
            "CREATE TABLE IF NOT EXISTS api_tokens "
            "(token_hash TEXT PRIMARY KEY, user_id TEXT NOT NULL, username TEXT NOT NULL, expires_iso TEXT NOT NULL)"
        )
        self._conn.commit()

    def get_user_for_token(self, token: str):
        h = _hash_token(_normalize_lookup_token(token))
        row = self._conn.execute(
            "SELECT user_id, username, expires_iso FROM api_tokens WHERE token_hash = ?", (h,)
        ).fetchone()
        if not row:
            return None
        user_id, username, expires_iso = row
        if _is_expired(expires_iso):
            self._conn.execute("DELETE FROM api_tokens WHERE token_hash = ?", (h,))
            self._conn.commit()
            return None
        return (user_id, username)

    def create_token(self, user_id: str, username: str, expire_hours: float) -> str:
        raw = secrets.token_urlsafe(32)
        h = _hash_token(raw)
        exp = _iso_expires(expire_hours)
        self._conn.execute(
            "INSERT INTO api_tokens (token_hash, user_id, username, expires_iso) VALUES (?, ?, ?, ?)",
            (h, user_id, username, exp),
        )
        self._conn.commit()
        return raw

    def revoke_token(self, token: str) -> bool:
        h = _hash_token(_normalize_lookup_token(token))
        cur = self._conn.execute("DELETE FROM api_tokens WHERE token_hash = ?", (h,))
        self._conn.commit()
        return cur.rowcount > 0

    def list_tokens_for_user(self, username: str) -> list:
        rows = self._conn.execute(
            "SELECT token_hash, expires_iso FROM api_tokens WHERE username = ?", (username,)
        ).fetchall()
        out = []
        for token_hash, expires_iso in rows:
            if not _is_expired(expires_iso):
                out.append({"token_hash_prefix": token_hash[:8] + "...", "expires_iso": expires_iso})
        return out


# ---------------------------------------------------------------------------
# PostgreSQL backend (optional)
# ---------------------------------------------------------------------------

def _get_postgres_store(host: str, port: int, database: str, user: str, password: str):
    try:
        import psycopg2
        from psycopg2.extras import RealDictCursor
    except ImportError:
        raise RuntimeError("PostgreSQL backend requires psycopg2; install with: pip install psycopg2-binary")

    conn = psycopg2.connect(
        host=host,
        port=port,
        dbname=database,
        user=user,
        password=password,
    )
    cur = conn.cursor()
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS api_tokens (
            token_hash TEXT PRIMARY KEY,
            user_id TEXT NOT NULL,
            username TEXT NOT NULL,
            expires_iso TEXT NOT NULL
        )
        """
    )
    conn.commit()
    cur.close()

    class _PostgresTokenStore:
        def __init__(self, conn):
            self._conn = conn

        def _cursor(self):
            return self._conn.cursor(cursor_factory=RealDictCursor)

        def get_user_for_token(self, token: str):
            h = _hash_token(_normalize_lookup_token(token))
            with self._cursor() as cur:
                cur.execute(
                    "SELECT user_id, username, expires_iso FROM api_tokens WHERE token_hash = %s", (h,)
                )
                row = cur.fetchone()
            if not row:
                return None
            if _is_expired(row["expires_iso"]):
                with self._conn.cursor() as cur:
                    cur.execute("DELETE FROM api_tokens WHERE token_hash = %s", (h,))
                    self._conn.commit()
                return None
            return (row["user_id"], row["username"])

        def create_token(self, user_id: str, username: str, expire_hours: float) -> str:
            raw = secrets.token_urlsafe(32)
            h = _hash_token(raw)
            exp = _iso_expires(expire_hours)
            with self._conn.cursor() as cur:
                cur.execute(
                    "INSERT INTO api_tokens (token_hash, user_id, username, expires_iso) VALUES (%s, %s, %s, %s)",
                    (h, user_id, username, exp),
                )
                self._conn.commit()
            return raw

        def revoke_token(self, token: str) -> bool:
            h = _hash_token(_normalize_lookup_token(token))
            with self._conn.cursor() as cur:
                cur.execute("DELETE FROM api_tokens WHERE token_hash = %s", (h,))
                self._conn.commit()
                return cur.rowcount > 0

        def list_tokens_for_user(self, username: str) -> list:
            with self._cursor() as cur:
                cur.execute(
                    "SELECT token_hash, expires_iso FROM api_tokens WHERE username = %s", (username,)
                )
                rows = cur.fetchall()
            out = []
            for r in rows:
                if not _is_expired(r["expires_iso"]):
                    out.append({"token_hash_prefix": r["token_hash"][:8] + "...", "expires_iso": r["expires_iso"]})
            return out

    return _PostgresTokenStore(conn)


# ---------------------------------------------------------------------------
# Factory and singleton
# ---------------------------------------------------------------------------

_api_token_store_instance = None


def get_api_token_store(config: Optional[dict] = None):
    """Build or return the singleton API token store from config.
    config can be either { "api_token_store": { ... } } or the inner { "backend", "json_path", ... }.
    """
    global _api_token_store_instance
    if config is None:
        config = {}
    store_cfg = config.get("api_token_store") if "api_token_store" in config else config
    if not store_cfg:
        store_cfg = {}
    backend = (store_cfg.get("backend") or "sqlite").lower()
    if _api_token_store_instance is not None:
        return _api_token_store_instance

    if backend == "json":
        path = store_cfg.get("json_path") or os.path.join(
            os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "users", "api_tokens.json"
        )
        _api_token_store_instance = _JsonTokenStore(path)
    elif backend == "sqlite":
        path = store_cfg.get("sqlite_path") or _default_sqlite_path()
        _api_token_store_instance = _SqliteTokenStore(path)
    elif backend == "postgresql":
        host = store_cfg.get("postgres_host", "localhost")
        port = int(store_cfg.get("postgres_port", 5432))
        database = store_cfg.get("postgres_database", "mss_login")
        user = store_cfg.get("postgres_user", "mss_login")
        password = (os.getenv("API_TOKEN_DB_PASSWORD") or os.getenv("POSTGRES_PASSWORD") or "").strip()
        _api_token_store_instance = _get_postgres_store(host, port, database, user, password)
    else:
        path = store_cfg.get("sqlite_path") or _default_sqlite_path()
        _api_token_store_instance = _SqliteTokenStore(path)
    return _api_token_store_instance


def reset_api_token_store() -> None:
    """Clear the singleton so next get_api_token_store() builds from current config."""
    global _api_token_store_instance
    _api_token_store_instance = None
