"""
Lockout store: IP blacklist and locked device IDs in same DB as users.
Allows unlock by editing users.db (delete rows from ip_blacklist / locked_devices).
Tables: ip_blacklist (ip TEXT PRIMARY KEY), locked_devices (device_id TEXT PRIMARY KEY).
"""

from pathlib import Path
from typing import Optional

TABLE_IP = "ip_blacklist"
TABLE_DEVICES = "locked_devices"


def _get_sqlite_store(
    db_path: str, secret_key: str = "", encryption_level: str = ""
) -> "_SqliteLockoutStore":
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
        CREATE TABLE IF NOT EXISTS {TABLE_IP} (
            ip TEXT PRIMARY KEY
        )
        """
    )
    conn.execute(
        f"""
        CREATE TABLE IF NOT EXISTS {TABLE_DEVICES} (
            device_id TEXT PRIMARY KEY
        )
        """
    )
    conn.commit()
    return _SqliteLockoutStore(conn)


def _get_postgres_store(
    host: str, port: int, database: str, user: str, password: str
) -> "_PostgresLockoutStore":
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
        CREATE TABLE IF NOT EXISTS {TABLE_IP} (
            ip TEXT PRIMARY KEY
        )
        """
    )
    cur.execute(
        f"""
        CREATE TABLE IF NOT EXISTS {TABLE_DEVICES} (
            device_id TEXT PRIMARY KEY
        )
        """
    )
    conn.commit()
    cur.close()
    return _PostgresLockoutStore(conn)


class _SqliteLockoutStore:
    def __init__(self, conn):
        self._conn = conn

    def get_blacklisted_ips(self) -> set[str]:
        rows = self._conn.execute(f"SELECT ip FROM {TABLE_IP}").fetchall()
        return {r[0] for r in rows}

    def get_locked_devices(self) -> set[str]:
        rows = self._conn.execute(f"SELECT device_id FROM {TABLE_DEVICES}").fetchall()
        return {r[0] for r in rows}

    def add_lockout(self, ip: str, device_id: Optional[str] = None) -> None:
        try:
            self._conn.execute(
                f"INSERT OR IGNORE INTO {TABLE_IP} (ip) VALUES (?)", (ip,)
            )
            if device_id:
                self._conn.execute(
                    f"INSERT OR IGNORE INTO {TABLE_DEVICES} (device_id) VALUES (?)",
                    (device_id,),
                )
            self._conn.commit()
        except Exception:
            self._conn.rollback()

    def remove_ip(self, ip: str) -> bool:
        cur = self._conn.execute(f"DELETE FROM {TABLE_IP} WHERE ip = ?", (ip,))
        self._conn.commit()
        return cur.rowcount > 0

    def remove_device(self, device_id: str) -> bool:
        cur = self._conn.execute(
            f"DELETE FROM {TABLE_DEVICES} WHERE device_id = ?", (device_id,)
        )
        self._conn.commit()
        return cur.rowcount > 0


class _PostgresLockoutStore:
    def __init__(self, conn):
        self._conn = conn

    def get_blacklisted_ips(self) -> set[str]:
        cur = self._conn.cursor()
        cur.execute(f"SELECT ip FROM {TABLE_IP}")
        out = {r[0] for r in cur.fetchall()}
        cur.close()
        return out

    def get_locked_devices(self) -> set[str]:
        cur = self._conn.cursor()
        cur.execute(f"SELECT device_id FROM {TABLE_DEVICES}")
        out = {r[0] for r in cur.fetchall()}
        cur.close()
        return out

    def add_lockout(self, ip: str, device_id: Optional[str] = None) -> None:
        try:
            cur = self._conn.cursor()
            cur.execute(
                f"INSERT INTO {TABLE_IP} (ip) VALUES (%s) ON CONFLICT (ip) DO NOTHING",
                (ip,),
            )
            if device_id:
                cur.execute(
                    f"INSERT INTO {TABLE_DEVICES} (device_id) VALUES (%s) ON CONFLICT (device_id) DO NOTHING",
                    (device_id,),
                )
            self._conn.commit()
            cur.close()
        except Exception:
            self._conn.rollback()

    def remove_ip(self, ip: str) -> bool:
        cur = self._conn.cursor()
        cur.execute(f"DELETE FROM {TABLE_IP} WHERE ip = %s", (ip,))
        n = cur.rowcount
        self._conn.commit()
        cur.close()
        return n > 0

    def remove_device(self, device_id: str) -> bool:
        cur = self._conn.cursor()
        cur.execute(f"DELETE FROM {TABLE_DEVICES} WHERE device_id = %s", (device_id,))
        n = cur.rowcount
        self._conn.commit()
        cur.close()
        return n > 0


_store: Optional[_SqliteLockoutStore | _PostgresLockoutStore] = None


def get_lockout_store(config: dict):
    """Get singleton lockout store using same config as users_db."""
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
