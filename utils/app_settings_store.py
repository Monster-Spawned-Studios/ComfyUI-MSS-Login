"""
App settings key-value store in the same DB as users (USERS_DB_CONFIG).
Table: app_settings (key TEXT PRIMARY KEY, value TEXT).
Used for host_base_url and other persistent app-level settings.
"""

from pathlib import Path
from typing import Optional

TABLE = "app_settings"


def _get_sqlite_store(
    db_path: str, secret_key: str = "", encryption_level: str = ""
) -> "_SqliteAppSettingsStore":
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
            key TEXT PRIMARY KEY,
            value TEXT
        )
        """
    )
    conn.commit()
    return _SqliteAppSettingsStore(conn)


def _get_postgres_store(
    host: str, port: int, database: str, user: str, password: str
) -> "_PostgresAppSettingsStore":
    try:
        import psycopg2
    except ImportError:
        raise RuntimeError("PostgreSQL requires psycopg2-binary")
    conn = psycopg2.connect(host=host, port=port, dbname=database, user=user, password=password)
    cur = conn.cursor()
    cur.execute(
        f"""
        CREATE TABLE IF NOT EXISTS {TABLE} (
            key TEXT PRIMARY KEY,
            value TEXT
        )
        """
    )
    conn.commit()
    cur.close()
    return _PostgresAppSettingsStore(conn)


def _get_mysql_store(
    host: str, port: int, database: str, user: str, password: str
) -> "_MySQLAppSettingsStore":
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
            `key` VARCHAR(255) PRIMARY KEY,
            value TEXT
        )
        """
    )
    conn.commit()
    cur.close()
    return _MySQLAppSettingsStore(conn)


class _SqliteAppSettingsStore:
    def __init__(self, conn):
        self._conn = conn

    def get(self, key: str) -> Optional[str]:
        row = self._conn.execute(f"SELECT value FROM {TABLE} WHERE key = ?", (key,)).fetchone()
        return row[0] if row else None

    def set(self, key: str, value: str) -> None:
        self._conn.execute(
            f"INSERT OR REPLACE INTO {TABLE} (key, value) VALUES (?, ?)",
            (key, value),
        )
        self._conn.commit()


class _PostgresAppSettingsStore:
    def __init__(self, conn):
        self._conn = conn

    def get(self, key: str) -> Optional[str]:
        cur = self._conn.cursor()
        cur.execute(f"SELECT value FROM {TABLE} WHERE key = %s", (key,))
        row = cur.fetchone()
        cur.close()
        return row[0] if row else None

    def set(self, key: str, value: str) -> None:
        cur = self._conn.cursor()
        cur.execute(
            f"INSERT INTO {TABLE} (key, value) VALUES (%s, %s) ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value",
            (key, value),
        )
        self._conn.commit()
        cur.close()


class _MySQLAppSettingsStore:
    def __init__(self, conn):
        self._conn = conn

    def get(self, key: str) -> Optional[str]:
        cur = self._conn.cursor()
        cur.execute(f"SELECT value FROM {TABLE} WHERE `key` = %s", (key,))
        row = cur.fetchone()
        cur.close()
        return row[0] if row else None

    def set(self, key: str, value: str) -> None:
        cur = self._conn.cursor()
        cur.execute(
            f"INSERT INTO {TABLE} (`key`, value) VALUES (%s, %s) ON DUPLICATE KEY UPDATE value = VALUES(value)",
            (key, value),
        )
        self._conn.commit()
        cur.close()


_store: Optional[_SqliteAppSettingsStore | _PostgresAppSettingsStore | _MySQLAppSettingsStore] = (
    None
)


def get_app_settings_store(config: dict):
    """Get singleton app settings store using same config as users_db."""
    global _store
    if _store is not None:
        return _store
    backend = (config.get("backend") or "sqlite").lower()
    if backend == "postgresql":
        _store = _get_postgres_store(
            config.get("postgres_host", "localhost"),
            int(config.get("postgres_port", 5432)),
            config.get("postgres_database", "mss_login"),
            config.get("postgres_user", "mss_login"),
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
            config.get("sqlite_path", "data/mss_login_data.db"),
            secret_key=SECRET_KEY,
            encryption_level=config.get("encryption_level", ""),
        )
    return _store
