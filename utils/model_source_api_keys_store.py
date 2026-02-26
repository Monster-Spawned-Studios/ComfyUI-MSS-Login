"""
Per-user API keys for model download sources (CivitAI, HuggingFace). Same DB as users.
Keys stored encrypted at rest. Table: user_model_source_api_keys (user_id, source, api_key_encrypted, created_at).
"""

import time
from pathlib import Path
from typing import Optional

TABLE = "user_model_source_api_keys"
SOURCES = ("civitai", "huggingface")


def _get_sqlite_store(
    db_path: str, secret_key: str = "", encryption_level: str = ""
) -> "_SqliteApiKeysStore":
    from .sqlite_connection import open_sqlite

    path = Path(db_path)
    conn = open_sqlite(
        str(path),
        secret_key=secret_key,
        encryption_level=encryption_level or "",
        check_same_thread=False,
    )
    conn.execute(
        f"""
        CREATE TABLE IF NOT EXISTS {TABLE} (
            user_id TEXT NOT NULL,
            source TEXT NOT NULL,
            api_key_encrypted TEXT,
            created_at REAL NOT NULL,
            PRIMARY KEY (user_id, source)
        )
        """
    )
    conn.commit()
    return _SqliteApiKeysStore(conn, secret_key)


def _get_postgres_store(
    host: str, port: int, database: str, user: str, password: str, secret_key: str
) -> "_PostgresApiKeysStore":
    try:
        import psycopg2
    except ImportError as exc:
        raise RuntimeError("PostgreSQL requires psycopg2-binary") from exc

    conn = psycopg2.connect(
        host=host, port=port, dbname=database, user=user, password=password
    )
    cur = conn.cursor()
    cur.execute(
        f"""
        CREATE TABLE IF NOT EXISTS {TABLE} (
            user_id TEXT NOT NULL,
            source TEXT NOT NULL,
            api_key_encrypted TEXT,
            created_at REAL NOT NULL,
            PRIMARY KEY (user_id, source)
        )
        """
    )
    conn.commit()
    cur.close()
    return _PostgresApiKeysStore(conn, secret_key)


class _SqliteApiKeysStore:
    def __init__(self, conn, secret_key: str):
        self._conn = conn
        self._secret_key = secret_key

    def get_key(self, user_id: str, source: str) -> Optional[str]:
        if source not in SOURCES:
            return None
        row = self._conn.execute(
            f"SELECT api_key_encrypted FROM {TABLE} WHERE user_id = ? AND source = ?",
            (user_id, source),
        ).fetchone()
        if not row or not row[0]:
            return None
        from .encryption import decrypt_value
        return decrypt_value(self._secret_key, row[0])

    def set_key(self, user_id: str, source: str, api_key: str) -> bool:
        if source not in SOURCES:
            return False
        from .encryption import encrypt_value
        encrypted = encrypt_value(self._secret_key, (api_key or "").strip())
        if not encrypted and (api_key or "").strip():
            return False
        now = time.time()
        try:
            self._conn.execute(
                f"""
                INSERT INTO {TABLE} (user_id, source, api_key_encrypted, created_at)
                VALUES (?, ?, ?, ?)
                ON CONFLICT (user_id, source) DO UPDATE SET
                    api_key_encrypted = excluded.api_key_encrypted,
                    created_at = excluded.created_at
                """,
                (user_id, source, encrypted or "", now),
            )
            self._conn.commit()
            return True
        except Exception:
            self._conn.rollback()
            return False

    def has_key(self, user_id: str, source: str) -> bool:
        if source not in SOURCES:
            return False
        row = self._conn.execute(
            f"SELECT 1 FROM {TABLE} WHERE user_id = ? AND source = ? AND api_key_encrypted != ''",
            (user_id, source),
        ).fetchone()
        return row is not None

    def delete_key(self, user_id: str, source: str) -> bool:
        if source not in SOURCES:
            return False
        cur = self._conn.execute(
            f"DELETE FROM {TABLE} WHERE user_id = ? AND source = ?",
            (user_id, source),
        )
        self._conn.commit()
        return cur.rowcount > 0

    def list_sources_with_keys(self, user_id: str) -> list[str]:
        rows = self._conn.execute(
            f"SELECT source FROM {TABLE} WHERE user_id = ? AND api_key_encrypted != ''",
            (user_id,),
        ).fetchall()
        return [r[0] for r in rows]


class _PostgresApiKeysStore:
    def __init__(self, conn, secret_key: str):
        self._conn = conn
        self._secret_key = secret_key

    def get_key(self, user_id: str, source: str) -> Optional[str]:
        if source not in SOURCES:
            return None
        cur = self._conn.cursor()
        cur.execute(
            f"SELECT api_key_encrypted FROM {TABLE} WHERE user_id = %s AND source = %s",
            (user_id, source),
        )
        row = cur.fetchone()
        cur.close()
        if not row or not row[0]:
            return None
        from .encryption import decrypt_value
        return decrypt_value(self._secret_key, row[0])

    def set_key(self, user_id: str, source: str, api_key: str) -> bool:
        if source not in SOURCES:
            return False
        from .encryption import encrypt_value
        encrypted = encrypt_value(self._secret_key, (api_key or "").strip())
        if not encrypted and (api_key or "").strip():
            return False
        now = time.time()
        try:
            cur = self._conn.cursor()
            cur.execute(
                f"""
                INSERT INTO {TABLE} (user_id, source, api_key_encrypted, created_at)
                VALUES (%s, %s, %s, %s)
                ON CONFLICT (user_id, source) DO UPDATE SET
                    api_key_encrypted = EXCLUDED.api_key_encrypted,
                    created_at = EXCLUDED.created_at
                """,
                (user_id, source, encrypted or "", now),
            )
            self._conn.commit()
            cur.close()
            return True
        except Exception:
            self._conn.rollback()
            return False

    def has_key(self, user_id: str, source: str) -> bool:
        if source not in SOURCES:
            return False
        cur = self._conn.cursor()
        cur.execute(
            f"SELECT 1 FROM {TABLE} WHERE user_id = %s AND source = %s AND api_key_encrypted != ''",
            (user_id, source),
        )
        row = cur.fetchone()
        cur.close()
        return row is not None

    def delete_key(self, user_id: str, source: str) -> bool:
        if source not in SOURCES:
            return False
        cur = self._conn.cursor()
        cur.execute(f"DELETE FROM {TABLE} WHERE user_id = %s AND source = %s", (user_id, source))
        n = cur.rowcount
        self._conn.commit()
        cur.close()
        return n > 0

    def list_sources_with_keys(self, user_id: str) -> list[str]:
        cur = self._conn.cursor()
        cur.execute(
            f"SELECT source FROM {TABLE} WHERE user_id = %s AND api_key_encrypted != ''",
            (user_id,),
        )
        rows = cur.fetchall()
        cur.close()
        return [r[0] for r in rows]


_store: Optional[_SqliteApiKeysStore | _PostgresApiKeysStore] = None


def get_model_source_api_keys_store(config: dict, secret_key: str = ""):
    """Get singleton store. Uses same DB as users; pass SECRET_KEY for encryption."""
    global _store
    if _store is not None:
        return _store
    backend = (config.get("backend") or "sqlite").lower()
    try:
        from ..constants import SECRET_KEY
    except ImportError:
        SECRET_KEY = ""
    sk = secret_key or SECRET_KEY
    if backend == "postgresql":
        _store = _get_postgres_store(
            config.get("postgres_host", "localhost"),
            int(config.get("postgres_port", 5432)),
            config.get("postgres_database", "mss-login"),
            config.get("postgres_user", "mss-login"),
            config.get("postgres_password", ""),
            sk,
        )
    else:
        _store = _get_sqlite_store(
            config.get("sqlite_path", "users/users.db"),
            secret_key=sk,
            encryption_level=config.get("encryption_level", ""),
        )
    return _store
