# --- START OF FILE utils/shared_items_store.py ---
"""
Per-user shared ComfyUI items (models, LoRAs, VAEs, embeddings). Same DB backend as users.
Table: shared_items (user_id, folder, item_name) UNIQUE(user_id, folder, item_name).
"""

import os
from pathlib import Path
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
            PRIMARY KEY (user_id, folder, item_name)
        )
        """
    )
    conn.commit()
    return _SqliteSharedStore(conn)


def _get_postgres_store(
    host: str, port: int, database: str, user: str, password: str
) -> "_PostgresSharedStore":
    try:
        import psycopg2
    except ImportError:
        raise RuntimeError("PostgreSQL requires psycopg2-binary")
    conn = psycopg2.connect(
        host=host, port=port, dbname=database, user=user, password=password
    )
    cur = conn.cursor()
    cur.execute(
        f"""
        CREATE TABLE IF NOT EXISTS {TABLE} (
            user_id TEXT NOT NULL,
            folder TEXT NOT NULL,
            item_name TEXT NOT NULL,
            PRIMARY KEY (user_id, folder, item_name)
        )
        """
    )
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
        host=host,
        port=port,
        user=user,
        password=password,
        database=database,
        charset="utf8mb4",
    )
    cur = conn.cursor()
    cur.execute(
        f"""
        CREATE TABLE IF NOT EXISTS {TABLE} (
            user_id VARCHAR(255) NOT NULL,
            folder VARCHAR(255) NOT NULL,
            item_name VARCHAR(255) NOT NULL,
            PRIMARY KEY (user_id, folder, item_name)
        )
        """
    )
    conn.commit()
    cur.close()
    return _MySQLSharedStore(conn)


class _SqliteSharedStore:
    def __init__(self, conn):
        self._conn = conn

    def get_all_for_user(self, user_id: str) -> set[tuple[str, str]]:
        """Return set of (folder, item_name) for user."""
        rows = self._conn.execute(
            f"SELECT folder, item_name FROM {TABLE} WHERE user_id = ?", (user_id,)
        ).fetchall()
        return {(r[0], r[1]) for r in rows}

    def add(self, user_id: str, folder: str, item_name: str) -> bool:
        try:
            self._conn.execute(
                f"INSERT INTO {TABLE} (user_id, folder, item_name) VALUES (?, ?, ?)",
                (user_id, folder, item_name.strip()),
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
            f"SELECT folder, item_name FROM {TABLE} WHERE user_id = ? ORDER BY folder, item_name",
            (user_id,),
        ).fetchall()
        return [{"folder": r[0], "item_name": r[1]} for r in rows]


class _PostgresSharedStore:
    def __init__(self, conn):
        self._conn = conn

    def get_all_for_user(self, user_id: str) -> set[tuple[str, str]]:
        cur = self._conn.cursor()
        cur.execute(
            f"SELECT folder, item_name FROM {TABLE} WHERE user_id = %s", (user_id,)
        )
        rows = cur.fetchall()
        cur.close()
        return {(r[0], r[1]) for r in rows}

    def add(self, user_id: str, folder: str, item_name: str) -> bool:
        try:
            cur = self._conn.cursor()
            cur.execute(
                f"INSERT INTO {TABLE} (user_id, folder, item_name) VALUES (%s, %s, %s)",
                (user_id, folder, item_name.strip()),
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
            f"SELECT folder, item_name FROM {TABLE} WHERE user_id = %s ORDER BY folder, item_name",
            (user_id,),
        )
        rows = cur.fetchall()
        cur.close()
        return [{"folder": r[0], "item_name": r[1]} for r in rows]


class _MySQLSharedStore:
    def __init__(self, conn):
        self._conn = conn

    def get_all_for_user(self, user_id: str) -> set[tuple[str, str]]:
        cur = self._conn.cursor()
        cur.execute(
            f"SELECT folder, item_name FROM {TABLE} WHERE user_id = %s", (user_id,)
        )
        rows = cur.fetchall()
        cur.close()
        return {(r[0], r[1]) for r in rows}

    def add(self, user_id: str, folder: str, item_name: str) -> bool:
        try:
            cur = self._conn.cursor()
            cur.execute(
                f"INSERT INTO {TABLE} (user_id, folder, item_name) VALUES (%s, %s, %s)",
                (user_id, folder, item_name.strip()),
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
            f"SELECT folder, item_name FROM {TABLE} WHERE user_id = %s ORDER BY folder, item_name",
            (user_id,),
        )
        rows = cur.fetchall()
        cur.close()
        return [{"folder": r[0], "item_name": r[1]} for r in rows]


_store: Optional[
    _SqliteSharedStore | _PostgresSharedStore | _MySQLSharedStore
] = None


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
