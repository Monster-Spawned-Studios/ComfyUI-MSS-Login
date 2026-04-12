# --- START OF FILE utils/shared_items_store.py ---
"""
Per-user shared ComfyUI items (models, LoRAs, VAEs, embeddings). Same DB backend as users.
Table: shared_items (user_id, folder, item_name) UNIQUE(user_id, folder, item_name).

Metadata columns are intentionally backend-neutral:
- source_backend: local | s3 | unknown
- granted_by_user_id / granted_by_role
- created_at (unix timestamp)
"""

import os
from pathlib import Path
import time
from typing import Optional

TABLE = "shared_items"


def _get_sqlite_store(
    db_path: str, secret_key: str = "", encryption_level: str = ""
) -> "_SqliteSharedStore":
    """Open unified SQLite DB (same path/key as users_db); supports SQLCipher when encryption_level set."""
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
            folder TEXT NOT NULL,
            item_name TEXT NOT NULL,
            source_backend TEXT NOT NULL DEFAULT 'unknown',
            granted_by_user_id TEXT NOT NULL DEFAULT '',
            granted_by_role TEXT NOT NULL DEFAULT '',
            created_at REAL NOT NULL DEFAULT 0,
            PRIMARY KEY (user_id, folder, item_name)
        )
        """
    )
    _ensure_columns_sqlite(conn)
    conn.commit()
    return _SqliteSharedStore(conn)


def _get_postgres_store(
    host: str, port: int, database: str, user: str, password: str
) -> "_PostgresSharedStore":
    try:
        import psycopg2
    except ImportError:
        raise RuntimeError("PostgreSQL requires psycopg2-binary")
    conn = psycopg2.connect(host=host, port=port, dbname=database, user=user, password=password)
    cur = conn.cursor()
    cur.execute(
        f"""
        CREATE TABLE IF NOT EXISTS {TABLE} (
            user_id TEXT NOT NULL,
            folder TEXT NOT NULL,
            item_name TEXT NOT NULL,
            source_backend TEXT NOT NULL DEFAULT 'unknown',
            granted_by_user_id TEXT NOT NULL DEFAULT '',
            granted_by_role TEXT NOT NULL DEFAULT '',
            created_at DOUBLE PRECISION NOT NULL DEFAULT 0,
            PRIMARY KEY (user_id, folder, item_name)
        )
        """
    )
    conn.commit()
    _ensure_columns_postgres(conn)
    conn.commit()
    cur.close()
    return _PostgresSharedStore(conn)


def _get_mysql_store(
    host: str, port: int, database: str, user: str, password: str
) -> "_MySQLSharedStore":
    try:
        import pymysql
    except ImportError:
        raise RuntimeError("MySQL requires pymysql; pip install pymysql")
    conn = pymysql.connect(
        host=host, port=port, user=user, password=password, database=database, charset="utf8mb4"
    )
    cur = conn.cursor()
    cur.execute(
        f"""
        CREATE TABLE IF NOT EXISTS {TABLE} (
            user_id VARCHAR(255) NOT NULL,
            folder VARCHAR(255) NOT NULL,
            item_name VARCHAR(255) NOT NULL,
            source_backend VARCHAR(32) NOT NULL DEFAULT 'unknown',
            granted_by_user_id VARCHAR(255) NOT NULL DEFAULT '',
            granted_by_role VARCHAR(64) NOT NULL DEFAULT '',
            created_at DOUBLE NOT NULL DEFAULT 0,
            PRIMARY KEY (user_id, folder, item_name)
        )
        """
    )
    conn.commit()
    _ensure_columns_mysql(conn)
    conn.commit()
    cur.close()
    return _MySQLSharedStore(conn)


def _ensure_columns_sqlite(conn) -> None:
    # Lightweight idempotent migrations for existing installs.
    migrations = (
        f"ALTER TABLE {TABLE} ADD COLUMN source_backend TEXT NOT NULL DEFAULT 'unknown'",
        f"ALTER TABLE {TABLE} ADD COLUMN granted_by_user_id TEXT NOT NULL DEFAULT ''",
        f"ALTER TABLE {TABLE} ADD COLUMN granted_by_role TEXT NOT NULL DEFAULT ''",
        f"ALTER TABLE {TABLE} ADD COLUMN created_at REAL NOT NULL DEFAULT 0",
    )
    for stmt in migrations:
        try:
            conn.execute(stmt)
        except Exception:
            continue


def _ensure_columns_postgres(conn) -> None:
    cur = conn.cursor()
    migrations = (
        f"ALTER TABLE {TABLE} ADD COLUMN IF NOT EXISTS source_backend TEXT NOT NULL DEFAULT 'unknown'",
        f"ALTER TABLE {TABLE} ADD COLUMN IF NOT EXISTS granted_by_user_id TEXT NOT NULL DEFAULT ''",
        f"ALTER TABLE {TABLE} ADD COLUMN IF NOT EXISTS granted_by_role TEXT NOT NULL DEFAULT ''",
        f"ALTER TABLE {TABLE} ADD COLUMN IF NOT EXISTS created_at DOUBLE PRECISION NOT NULL DEFAULT 0",
    )
    for stmt in migrations:
        try:
            cur.execute(stmt)
        except Exception:
            pass
    cur.close()


def _ensure_columns_mysql(conn) -> None:
    cur = conn.cursor()
    migrations = (
        f"ALTER TABLE {TABLE} ADD COLUMN source_backend VARCHAR(32) NOT NULL DEFAULT 'unknown'",
        f"ALTER TABLE {TABLE} ADD COLUMN granted_by_user_id VARCHAR(255) NOT NULL DEFAULT ''",
        f"ALTER TABLE {TABLE} ADD COLUMN granted_by_role VARCHAR(64) NOT NULL DEFAULT ''",
        f"ALTER TABLE {TABLE} ADD COLUMN created_at DOUBLE NOT NULL DEFAULT 0",
    )
    for stmt in migrations:
        try:
            cur.execute(stmt)
        except Exception:
            pass
    cur.close()


class _SqliteSharedStore:
    def __init__(self, conn):
        self._conn = conn

    def get_all_for_user(self, user_id: str) -> set[tuple[str, str]]:
        """Return set of (folder, item_name) for user."""
        rows = self._conn.execute(
            f"SELECT folder, item_name FROM {TABLE} WHERE user_id = ?", (user_id,)
        ).fetchall()
        return {(r[0], r[1]) for r in rows}

    def add(
        self,
        user_id: str,
        folder: str,
        item_name: str,
        source_backend: str = "unknown",
        granted_by_user_id: str = "",
        granted_by_role: str = "",
    ) -> bool:
        now = float(time.time())
        try:
            self._conn.execute(
                f"""
				INSERT INTO {TABLE}
				(user_id, folder, item_name, source_backend, granted_by_user_id, granted_by_role, created_at)
				VALUES (?, ?, ?, ?, ?, ?, ?)
				ON CONFLICT (user_id, folder, item_name) DO UPDATE SET
					source_backend = excluded.source_backend,
					granted_by_user_id = excluded.granted_by_user_id,
					granted_by_role = excluded.granted_by_role,
					created_at = excluded.created_at
				""",
                (
                    user_id,
                    folder,
                    item_name.strip(),
                    (source_backend or "unknown").strip().lower(),
                    (granted_by_user_id or "").strip(),
                    (granted_by_role or "").strip().lower(),
                    now,
                ),
            )
            self._conn.commit()
            return True
        except Exception:
            self._conn.rollback()
            return False

    def remove(self, user_id: str, folder: str, item_name: str) -> bool:
        cur = self._conn.execute(
            f"DELETE FROM {TABLE} WHERE user_id = ? AND folder = ? AND item_name = ?",
            (user_id, folder, item_name.strip()),
        )
        self._conn.commit()
        return cur.rowcount > 0

    def list_for_user(self, user_id: str) -> list[dict]:
        rows = self._conn.execute(
            f"""
			SELECT folder, item_name, source_backend, granted_by_user_id, granted_by_role, created_at
			FROM {TABLE}
			WHERE user_id = ?
			ORDER BY folder, item_name
			""",
            (user_id,),
        ).fetchall()
        return [
            {
                "folder": r[0],
                "item_name": r[1],
                "source_backend": r[2] if len(r) > 2 else "unknown",
                "granted_by_user_id": r[3] if len(r) > 3 else "",
                "granted_by_role": r[4] if len(r) > 4 else "",
                "created_at": r[5] if len(r) > 5 else 0,
            }
            for r in rows
        ]


class _PostgresSharedStore:
    def __init__(self, conn):
        self._conn = conn

    def get_all_for_user(self, user_id: str) -> set[tuple[str, str]]:
        cur = self._conn.cursor()
        cur.execute(f"SELECT folder, item_name FROM {TABLE} WHERE user_id = %s", (user_id,))
        rows = cur.fetchall()
        cur.close()
        return {(r[0], r[1]) for r in rows}

    def add(
        self,
        user_id: str,
        folder: str,
        item_name: str,
        source_backend: str = "unknown",
        granted_by_user_id: str = "",
        granted_by_role: str = "",
    ) -> bool:
        now = float(time.time())
        try:
            cur = self._conn.cursor()
            cur.execute(
                f"""
				INSERT INTO {TABLE}
				(user_id, folder, item_name, source_backend, granted_by_user_id, granted_by_role, created_at)
				VALUES (%s, %s, %s, %s, %s, %s, %s)
				ON CONFLICT (user_id, folder, item_name) DO UPDATE SET
					source_backend = EXCLUDED.source_backend,
					granted_by_user_id = EXCLUDED.granted_by_user_id,
					granted_by_role = EXCLUDED.granted_by_role,
					created_at = EXCLUDED.created_at
				""",
                (
                    user_id,
                    folder,
                    item_name.strip(),
                    (source_backend or "unknown").strip().lower(),
                    (granted_by_user_id or "").strip(),
                    (granted_by_role or "").strip().lower(),
                    now,
                ),
            )
            self._conn.commit()
            cur.close()
            return True
        except Exception:
            self._conn.rollback()
            return False

    def remove(self, user_id: str, folder: str, item_name: str) -> bool:
        cur = self._conn.cursor()
        cur.execute(
            f"DELETE FROM {TABLE} WHERE user_id = %s AND folder = %s AND item_name = %s",
            (user_id, folder, item_name.strip()),
        )
        self._conn.commit()
        n = cur.rowcount
        cur.close()
        return n > 0

    def list_for_user(self, user_id: str) -> list[dict]:
        cur = self._conn.cursor()
        cur.execute(
            f"""
			SELECT folder, item_name, source_backend, granted_by_user_id, granted_by_role, created_at
			FROM {TABLE}
			WHERE user_id = %s
			ORDER BY folder, item_name
			""",
            (user_id,),
        )
        rows = cur.fetchall()
        cur.close()
        return [
            {
                "folder": r[0],
                "item_name": r[1],
                "source_backend": r[2] if len(r) > 2 else "unknown",
                "granted_by_user_id": r[3] if len(r) > 3 else "",
                "granted_by_role": r[4] if len(r) > 4 else "",
                "created_at": r[5] if len(r) > 5 else 0,
            }
            for r in rows
        ]


class _MySQLSharedStore:
    def __init__(self, conn):
        self._conn = conn

    def get_all_for_user(self, user_id: str) -> set[tuple[str, str]]:
        cur = self._conn.cursor()
        cur.execute(f"SELECT folder, item_name FROM {TABLE} WHERE user_id = %s", (user_id,))
        rows = cur.fetchall()
        cur.close()
        return {(r[0], r[1]) for r in rows}

    def add(
        self,
        user_id: str,
        folder: str,
        item_name: str,
        source_backend: str = "unknown",
        granted_by_user_id: str = "",
        granted_by_role: str = "",
    ) -> bool:
        now = float(time.time())
        try:
            cur = self._conn.cursor()
            cur.execute(
                f"""
				INSERT INTO {TABLE}
				(user_id, folder, item_name, source_backend, granted_by_user_id, granted_by_role, created_at)
				VALUES (%s, %s, %s, %s, %s, %s, %s)
				ON DUPLICATE KEY UPDATE
					source_backend = VALUES(source_backend),
					granted_by_user_id = VALUES(granted_by_user_id),
					granted_by_role = VALUES(granted_by_role),
					created_at = VALUES(created_at)
				""",
                (
                    user_id,
                    folder,
                    item_name.strip(),
                    (source_backend or "unknown").strip().lower(),
                    (granted_by_user_id or "").strip(),
                    (granted_by_role or "").strip().lower(),
                    now,
                ),
            )
            self._conn.commit()
            cur.close()
            return True
        except Exception:
            self._conn.rollback()
            return False

    def remove(self, user_id: str, folder: str, item_name: str) -> bool:
        cur = self._conn.cursor()
        cur.execute(
            f"DELETE FROM {TABLE} WHERE user_id = %s AND folder = %s AND item_name = %s",
            (user_id, folder, item_name.strip()),
        )
        self._conn.commit()
        n = cur.rowcount
        cur.close()
        return n > 0

    def list_for_user(self, user_id: str) -> list[dict]:
        cur = self._conn.cursor()
        cur.execute(
            f"""
			SELECT folder, item_name, source_backend, granted_by_user_id, granted_by_role, created_at
			FROM {TABLE}
			WHERE user_id = %s
			ORDER BY folder, item_name
			""",
            (user_id,),
        )
        rows = cur.fetchall()
        cur.close()
        return [
            {
                "folder": r[0],
                "item_name": r[1],
                "source_backend": r[2] if len(r) > 2 else "unknown",
                "granted_by_user_id": r[3] if len(r) > 3 else "",
                "granted_by_role": r[4] if len(r) > 4 else "",
                "created_at": r[5] if len(r) > 5 else 0,
            }
            for r in rows
        ]


_store: Optional[_SqliteSharedStore | _PostgresSharedStore | _MySQLSharedStore] = None


def get_shared_items_store(config: dict):
    """Get singleton store using same config as users_db (backend, sqlite_path, postgres_*, mysql_*, encryption_level)."""
    global _store
    if _store is not None:
        return _store
    backend = (config.get("backend") or "sqlite").lower()
    if backend == "postgresql":
        _store = _get_postgres_store(
            config.get("postgres_host", "localhost"),
            int(config.get("postgres_port", 5432)),
            config.get("postgres_database", "mss-login"),
            config.get("postgres_user", "mss-login"),
            config.get("postgres_password", ""),
        )
    elif backend == "mysql":
        _store = _get_mysql_store(
            config.get("mysql_host", "localhost"),
            int(config.get("mysql_port", 3306)),
            config.get("mysql_database", "mss_login"),
            config.get("mysql_user", "mss_login"),
            config.get("mysql_password", ""),
        )
    else:
        try:
            from ..constants import SECRET_KEY
        except ImportError:
            SECRET_KEY = ""
        _store = _get_sqlite_store(
            config.get("sqlite_path", "users/users.db"),
            secret_key=SECRET_KEY,
            encryption_level=config.get("encryption_level", ""),
        )
    return _store


def reset_shared_items_store() -> None:
    global _store
    _store = None
