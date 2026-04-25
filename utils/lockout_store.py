"""
Lockout store: IP blacklist (with optional expiry), IP whitelist, and locked device IDs in same DB as users.
Allows unlock by editing users.db (delete rows from ip_blacklist / locked_devices).
Tables: ip_blacklist (ip TEXT PRIMARY KEY, expires_at INTEGER NULL), ip_whitelist (entry TEXT PRIMARY KEY), locked_devices (device_id TEXT PRIMARY KEY).
expires_at is Unix timestamp; NULL = permanent ban (permaban).
"""

from pathlib import Path
from typing import Optional
import time

TABLE_IP = "ip_blacklist"
TABLE_WHITELIST = "ip_whitelist"
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
	# ip_blacklist: add expires_at for new installs; migrate existing table
	conn.execute(
		f"""
        CREATE TABLE IF NOT EXISTS {TABLE_IP} (
            ip TEXT PRIMARY KEY,
            expires_at INTEGER NULL
        )
        """
	)
	# SQLite CREATE TABLE IF NOT EXISTS with expires_at; if table already existed with old schema, add column
	try:
		conn.execute(f"ALTER TABLE {TABLE_IP} ADD COLUMN expires_at INTEGER NULL")
		conn.commit()
	except Exception:
		conn.rollback()
	conn.execute(
		f"""
        CREATE TABLE IF NOT EXISTS {TABLE_WHITELIST} (
            entry TEXT PRIMARY KEY
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
	conn = psycopg2.connect(host=host, port=port, dbname=database, user=user, password=password)
	cur = conn.cursor()
	cur.execute(
		f"""
        CREATE TABLE IF NOT EXISTS {TABLE_IP} (
            ip TEXT PRIMARY KEY,
            expires_at TIMESTAMP NULL
        )
        """
	)
	try:
		cur.execute(f"ALTER TABLE {TABLE_IP} ADD COLUMN expires_at TIMESTAMP NULL")
		conn.commit()
	except Exception:
		conn.rollback()
	cur.execute(
		f"""
        CREATE TABLE IF NOT EXISTS {TABLE_WHITELIST} (
            entry TEXT PRIMARY KEY
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


def _get_mysql_store(
	host: str, port: int, database: str, user: str, password: str
) -> "_MySQLLockoutStore":
	try:
		import pymysql
	except ImportError:
		raise RuntimeError("MySQL backend requires pymysql; pip install pymysql")
	conn = pymysql.connect(
		host=host, port=port, user=user, password=password, database=database, charset="utf8mb4"
	)
	cur = conn.cursor()
	cur.execute(
		f"""
        CREATE TABLE IF NOT EXISTS {TABLE_IP} (
            ip VARCHAR(45) PRIMARY KEY,
            expires_at INT NULL
        )
        """
	)
	try:
		cur.execute(f"ALTER TABLE {TABLE_IP} ADD COLUMN expires_at INT NULL")
		conn.commit()
	except Exception:
		conn.rollback()
	cur.execute(
		f"""
        CREATE TABLE IF NOT EXISTS {TABLE_WHITELIST} (
            `entry` VARCHAR(64) PRIMARY KEY
        )
        """
	)
	cur.execute(
		f"""
        CREATE TABLE IF NOT EXISTS {TABLE_DEVICES} (
            device_id VARCHAR(255) PRIMARY KEY
        )
        """
	)
	conn.commit()
	cur.close()
	return _MySQLLockoutStore(conn)


def _now_ts() -> int:
	return int(time.time())


class _SqliteLockoutStore:
	def __init__(self, conn):
		self._conn = conn

	def get_whitelist(self) -> list[str]:
		rows = self._conn.execute(f"SELECT entry FROM {TABLE_WHITELIST}").fetchall()
		return [r[0] for r in rows]

	def get_blacklisted_ips(self) -> set[str]:
		now = _now_ts()
		rows = self._conn.execute(
			f"SELECT ip FROM {TABLE_IP} WHERE expires_at IS NULL OR expires_at > ?", (now,)
		).fetchall()
		return {r[0] for r in rows}

	def get_blacklist_with_expiry(self) -> list[tuple[str, Optional[int]]]:
		"""Return list of (ip, expires_at_ts or None for permanent)."""
		rows = self._conn.execute(f"SELECT ip, expires_at FROM {TABLE_IP}").fetchall()
		return [(r[0], r[1]) for r in rows]

	def get_locked_devices(self) -> set[str]:
		rows = self._conn.execute(f"SELECT device_id FROM {TABLE_DEVICES}").fetchall()
		return {r[0] for r in rows}

	def add_lockout(
		self, ip: str, device_id: Optional[str] = None, expiry_hours: Optional[float] = None
	) -> None:
		expires_at = None
		if expiry_hours is not None and expiry_hours >= 0:
			expires_at = _now_ts() + int(expiry_hours * 3600)
		try:
			self._conn.execute(
				f"INSERT OR REPLACE INTO {TABLE_IP} (ip, expires_at) VALUES (?, ?)",
				(ip, expires_at),
			)
			if device_id:
				self._conn.execute(
					f"INSERT OR IGNORE INTO {TABLE_DEVICES} (device_id) VALUES (?)", (device_id,)
				)
			self._conn.commit()
		except Exception:
			self._conn.rollback()

	def add_whitelist_entry(self, entry: str) -> None:
		try:
			self._conn.execute(
				f"INSERT OR IGNORE INTO {TABLE_WHITELIST} (entry) VALUES (?)", (entry,)
			)
			self._conn.commit()
		except Exception:
			self._conn.rollback()

	def remove_whitelist_entry(self, entry: str) -> bool:
		cur = self._conn.execute(f"DELETE FROM {TABLE_WHITELIST} WHERE entry = ?", (entry,))
		self._conn.commit()
		return cur.rowcount > 0

	def add_blacklist_entry(self, ip: str, expires_at: Optional[int] = None) -> None:
		try:
			self._conn.execute(
				f"INSERT OR REPLACE INTO {TABLE_IP} (ip, expires_at) VALUES (?, ?)",
				(ip, expires_at),
			)
			self._conn.commit()
		except Exception:
			self._conn.rollback()

	def remove_blacklist_entry(self, ip: str) -> bool:
		return self.remove_ip(ip)

	def remove_ip(self, ip: str) -> bool:
		cur = self._conn.execute(f"DELETE FROM {TABLE_IP} WHERE ip = ?", (ip,))
		self._conn.commit()
		return cur.rowcount > 0

	def remove_device(self, device_id: str) -> bool:
		cur = self._conn.execute(f"DELETE FROM {TABLE_DEVICES} WHERE device_id = ?", (device_id,))
		self._conn.commit()
		return cur.rowcount > 0

	def set_whitelist(self, entries: list[str]) -> None:
		try:
			self._conn.execute(f"DELETE FROM {TABLE_WHITELIST}")
			for entry in entries:
				entry = (entry or "").strip()
				if entry:
					self._conn.execute(
						f"INSERT OR IGNORE INTO {TABLE_WHITELIST} (entry) VALUES (?)", (entry,)
					)
			self._conn.commit()
		except Exception:
			self._conn.rollback()

	def set_blacklist(self, entries: list[tuple[str, Optional[int]]]) -> None:
		"""Replace blacklist with list of (ip, expires_at_ts or None)."""
		try:
			self._conn.execute(f"DELETE FROM {TABLE_IP}")
			for ip, expires_at in entries:
				ip = (ip or "").strip()
				if ip:
					self._conn.execute(
						f"INSERT INTO {TABLE_IP} (ip, expires_at) VALUES (?, ?)", (ip, expires_at)
					)
			self._conn.commit()
		except Exception:
			self._conn.rollback()


class _PostgresLockoutStore:
	def __init__(self, conn):
		self._conn = conn

	def get_whitelist(self) -> list[str]:
		cur = self._conn.cursor()
		cur.execute(f"SELECT entry FROM {TABLE_WHITELIST}")
		out = [r[0] for r in cur.fetchall()]
		cur.close()
		return out

	def get_blacklisted_ips(self) -> set[str]:
		now = _now_ts()
		cur = self._conn.cursor()
		cur.execute(
			f"SELECT ip FROM {TABLE_IP} WHERE expires_at IS NULL OR expires_at > to_timestamp(%s)",
			(now,),
		)
		out = {r[0] for r in cur.fetchall()}
		cur.close()
		return out

	def get_blacklist_with_expiry(self) -> list[tuple[str, Optional[int]]]:
		cur = self._conn.cursor()
		cur.execute(f"SELECT ip, expires_at FROM {TABLE_IP}")
		rows = cur.fetchall()
		cur.close()
		out = []
		for ip, exp in rows:
			ts = int(exp.timestamp()) if exp is not None else None
			out.append((ip, ts))
		return out

	def get_locked_devices(self) -> set[str]:
		cur = self._conn.cursor()
		cur.execute(f"SELECT device_id FROM {TABLE_DEVICES}")
		out = {r[0] for r in cur.fetchall()}
		cur.close()
		return out

	def add_lockout(
		self, ip: str, device_id: Optional[str] = None, expiry_hours: Optional[float] = None
	) -> None:
		try:
			cur = self._conn.cursor()
			if expiry_hours is not None and expiry_hours >= 0:
				exp_ts = _now_ts() + int(expiry_hours * 3600)
				cur.execute(
					f"INSERT INTO {TABLE_IP} (ip, expires_at) VALUES (%s, to_timestamp(%s)) ON CONFLICT (ip) DO UPDATE SET expires_at = EXCLUDED.expires_at",
					(ip, exp_ts),
				)
			else:
				cur.execute(
					f"INSERT INTO {TABLE_IP} (ip, expires_at) VALUES (%s, NULL) ON CONFLICT (ip) DO UPDATE SET expires_at = NULL",
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

	def add_whitelist_entry(self, entry: str) -> None:
		try:
			cur = self._conn.cursor()
			cur.execute(
				f"INSERT INTO {TABLE_WHITELIST} (entry) VALUES (%s) ON CONFLICT (entry) DO NOTHING",
				(entry,),
			)
			self._conn.commit()
			cur.close()
		except Exception:
			self._conn.rollback()

	def remove_whitelist_entry(self, entry: str) -> bool:
		cur = self._conn.cursor()
		cur.execute(f"DELETE FROM {TABLE_WHITELIST} WHERE entry = %s", (entry,))
		n = cur.rowcount
		self._conn.commit()
		cur.close()
		return n > 0

	def add_blacklist_entry(self, ip: str, expires_at: Optional[int] = None) -> None:
		try:
			cur = self._conn.cursor()
			if expires_at is not None:
				cur.execute(
					f"INSERT INTO {TABLE_IP} (ip, expires_at) VALUES (%s, to_timestamp(%s)) ON CONFLICT (ip) DO UPDATE SET expires_at = EXCLUDED.expires_at",
					(ip, expires_at),
				)
			else:
				cur.execute(
					f"INSERT INTO {TABLE_IP} (ip, expires_at) VALUES (%s, NULL) ON CONFLICT (ip) DO UPDATE SET expires_at = NULL",
					(ip,),
				)
			self._conn.commit()
			cur.close()
		except Exception:
			self._conn.rollback()

	def remove_blacklist_entry(self, ip: str) -> bool:
		return self.remove_ip(ip)

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

	def set_whitelist(self, entries: list[str]) -> None:
		try:
			cur = self._conn.cursor()
			cur.execute(f"DELETE FROM {TABLE_WHITELIST}")
			for entry in entries:
				entry = (entry or "").strip()
				if entry:
					cur.execute(
						f"INSERT INTO {TABLE_WHITELIST} (entry) VALUES (%s) ON CONFLICT (entry) DO NOTHING",
						(entry,),
					)
			self._conn.commit()
			cur.close()
		except Exception:
			self._conn.rollback()

	def set_blacklist(self, entries: list[tuple[str, Optional[int]]]) -> None:
		try:
			cur = self._conn.cursor()
			cur.execute(f"DELETE FROM {TABLE_IP}")
			for ip, expires_at in entries:
				ip = (ip or "").strip()
				if ip:
					if expires_at is not None:
						cur.execute(
							f"INSERT INTO {TABLE_IP} (ip, expires_at) VALUES (%s, to_timestamp(%s))",
							(ip, expires_at),
						)
					else:
						cur.execute(
							f"INSERT INTO {TABLE_IP} (ip, expires_at) VALUES (%s, NULL)", (ip,)
						)
			self._conn.commit()
			cur.close()
		except Exception:
			self._conn.rollback()


class _MySQLLockoutStore:
	def __init__(self, conn):
		self._conn = conn

	def get_whitelist(self) -> list[str]:
		cur = self._conn.cursor()
		cur.execute(f"SELECT `entry` FROM {TABLE_WHITELIST}")
		out = [r[0] for r in cur.fetchall()]
		cur.close()
		return out

	def get_blacklisted_ips(self) -> set[str]:
		now = _now_ts()
		cur = self._conn.cursor()
		cur.execute(
			f"SELECT ip FROM {TABLE_IP} WHERE expires_at IS NULL OR expires_at > %s", (now,)
		)
		out = {r[0] for r in cur.fetchall()}
		cur.close()
		return out

	def get_blacklist_with_expiry(self) -> list[tuple[str, Optional[int]]]:
		cur = self._conn.cursor()
		cur.execute(f"SELECT ip, expires_at FROM {TABLE_IP}")
		rows = cur.fetchall()
		cur.close()
		return [(r[0], r[1]) for r in rows]

	def get_locked_devices(self) -> set[str]:
		cur = self._conn.cursor()
		cur.execute(f"SELECT device_id FROM {TABLE_DEVICES}")
		out = {r[0] for r in cur.fetchall()}
		cur.close()
		return out

	def add_lockout(
		self, ip: str, device_id: Optional[str] = None, expiry_hours: Optional[float] = None
	) -> None:
		try:
			cur = self._conn.cursor()
			exp_val = (
				_now_ts() + int(expiry_hours * 3600)
				if expiry_hours is not None and expiry_hours >= 0
				else None
			)
			cur.execute(
				"INSERT INTO {} (ip, expires_at) VALUES (%s, %s) ON DUPLICATE KEY UPDATE expires_at = VALUES(expires_at)".format(
					TABLE_IP
				),
				(ip, exp_val),
			)
			if device_id:
				cur.execute(
					"INSERT IGNORE INTO {} (device_id) VALUES (%s)".format(TABLE_DEVICES),
					(device_id,),
				)
			self._conn.commit()
			cur.close()
		except Exception:
			self._conn.rollback()

	def add_whitelist_entry(self, entry: str) -> None:
		try:
			cur = self._conn.cursor()
			cur.execute(
				"INSERT IGNORE INTO {} (`entry`) VALUES (%s)".format(TABLE_WHITELIST), (entry,)
			)
			self._conn.commit()
			cur.close()
		except Exception:
			self._conn.rollback()

	def remove_whitelist_entry(self, entry: str) -> bool:
		cur = self._conn.cursor()
		cur.execute("DELETE FROM {} WHERE `entry` = %s".format(TABLE_WHITELIST), (entry,))
		n = cur.rowcount
		self._conn.commit()
		cur.close()
		return n > 0

	def add_blacklist_entry(self, ip: str, expires_at: Optional[int] = None) -> None:
		try:
			cur = self._conn.cursor()
			cur.execute(
				"INSERT INTO {} (ip, expires_at) VALUES (%s, %s) ON DUPLICATE KEY UPDATE expires_at = VALUES(expires_at)".format(
					TABLE_IP
				),
				(ip, expires_at),
			)
			self._conn.commit()
			cur.close()
		except Exception:
			self._conn.rollback()

	def remove_blacklist_entry(self, ip: str) -> bool:
		return self.remove_ip(ip)

	def remove_ip(self, ip: str) -> bool:
		cur = self._conn.cursor()
		cur.execute("DELETE FROM {} WHERE ip = %s".format(TABLE_IP), (ip,))
		n = cur.rowcount
		self._conn.commit()
		cur.close()
		return n > 0

	def remove_device(self, device_id: str) -> bool:
		cur = self._conn.cursor()
		cur.execute("DELETE FROM {} WHERE device_id = %s".format(TABLE_DEVICES), (device_id,))
		n = cur.rowcount
		self._conn.commit()
		cur.close()
		return n > 0

	def set_whitelist(self, entries: list[str]) -> None:
		try:
			cur = self._conn.cursor()
			cur.execute("DELETE FROM {}".format(TABLE_WHITELIST))
			for entry in entries:
				entry = (entry or "").strip()
				if entry:
					cur.execute(
						"INSERT IGNORE INTO {} (`entry`) VALUES (%s)".format(TABLE_WHITELIST),
						(entry,),
					)
			self._conn.commit()
			cur.close()
		except Exception:
			self._conn.rollback()

	def set_blacklist(self, entries: list[tuple[str, Optional[int]]]) -> None:
		try:
			cur = self._conn.cursor()
			cur.execute("DELETE FROM {}".format(TABLE_IP))
			for ip, expires_at in entries:
				ip = (ip or "").strip()
				if ip:
					cur.execute(
						"INSERT INTO {} (ip, expires_at) VALUES (%s, %s)".format(TABLE_IP),
						(ip, expires_at),
					)
			self._conn.commit()
			cur.close()
		except Exception:
			self._conn.rollback()


_store: Optional[_SqliteLockoutStore | _PostgresLockoutStore | _MySQLLockoutStore] = None


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
