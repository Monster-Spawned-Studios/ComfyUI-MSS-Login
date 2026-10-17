# --- START OF FILE utils/users_db.py ---
"""
Users database: SQLite or PostgreSQL only. No plain-text JSON for credentials.
Supports migration from legacy JSON on first run. MFA fields encrypted with SECRET_KEY.
SQLite may use SQLCipher (encryption_level in config) with SECRET_KEY-derived key.
"""

import json
import os
import secrets
from pathlib import Path
from typing import Any, Optional, Tuple

import bcrypt

from .encryption import decrypt_value, encrypt_value, hash_backup_code, verify_backup_code
from .input_sanitizer import sanitize_username as _sanitize_username
from .sanitizer import apply_legacy_xss_sanitize


def _get_logger():
	"""Lazy accessor for the package-level logger, avoiding a circular import
	(globals -> users_db -> globals) at module-load time."""
	try:
		from ..globals import logger as _logger
	except (ImportError, ValueError):
		import logging

		_logger = logging.getLogger("mss-login")
	return _logger


# Schema: user_id, username, password_hash, admin, groups (JSON), sfw_check,
#         mfa_enabled, totp_secret_encrypted, backup_code_hash, backup_code_used
USERS_TABLE = "users"


def _groups_to_json(groups: list) -> str:
	if not isinstance(groups, list):
		groups = ["user"]
	return json.dumps([str(g) for g in groups])


def _json_to_groups(s: str | None) -> list:
	if not s:
		return ["user"]
	try:
		out = json.loads(s)
		return [str(g) for g in out] if isinstance(out, list) else ["user"]
	except Exception:
		return ["user"]


def _dict_row_factory(cursor: Any, row: tuple) -> dict:
	"""Row factory that returns a dict. Works with both sqlite3 and sqlcipher3 cursors."""
	if cursor.description:
		return dict(zip([col[0] for col in cursor.description], row))
	return {}


def _row_to_user(row: dict, secret_key: str) -> dict:
	"""Build user dict from DB row; decrypt TOTP secret if present."""
	groups = _json_to_groups(row.get("groups"))
	pw = row.get("password_hash", "")
	user = {
		"username": row.get("username", ""),
		"password": pw,
		"password_hash": pw,
		"admin": bool(int(row.get("admin") or 0)),
		"groups": groups,
	}
	if "sfw_check" in row:
		user["sfw_check"] = bool(int(row.get("sfw_check", 1)))
	if "mfa_enabled" in row:
		user["mfa_enabled"] = bool(int(row.get("mfa_enabled") or 0))
	if row.get("totp_secret_encrypted"):
		dec = decrypt_value(secret_key, row["totp_secret_encrypted"])
		if dec:
			user["_totp_secret_plain"] = dec  # internal use only
	if row.get("backup_code_hash"):
		user["backup_code_hash"] = row["backup_code_hash"]
	if "backup_code_used" in row:
		user["backup_code_used"] = bool(int(row.get("backup_code_used") or 0))
	return user


def _user_to_row(user: dict, secret_key: str) -> dict:
	"""Build DB row from user dict; encrypt TOTP secret if present."""
	row = {
		"username": user.get("username", ""),
		"password_hash": user.get("password", user.get("password_hash", "")),
		"admin": 1 if user.get("admin") else 0,
		"groups": _groups_to_json(user.get("groups", ["user"])),
		"sfw_check": 1 if user.get("sfw_check", True) else 0,
		"mfa_enabled": 1 if user.get("mfa_enabled") else 0,
		"totp_secret_encrypted": "",
		"backup_code_hash": user.get("backup_code_hash") or "",
		"backup_code_used": 1 if user.get("backup_code_used") else 0,
	}
	if user.get("_totp_secret_plain"):
		enc = encrypt_value(secret_key, user["_totp_secret_plain"])
		if enc:
			row["totp_secret_encrypted"] = enc
	elif user.get("totp_secret_encrypted"):
		row["totp_secret_encrypted"] = user["totp_secret_encrypted"]
	return row


# ---------------------------------------------------------------------------
# SQLite backend (unified DB path; optional SQLCipher via open_sqlite)
# ---------------------------------------------------------------------------


class _SqliteUsersBackend:
	def __init__(self, db_path: str, secret_key: str = "", encryption_level: str = ""):
		self._path = Path(db_path)
		from .sqlite_connection import open_sqlite

		self._conn = open_sqlite(
			str(self._path),
			secret_key=secret_key,
			encryption_level=encryption_level or "",
			check_same_thread=False,
		)
		# Use dict row factory so rows work with both sqlite3 and sqlcipher3 (sqlite3.Row
		# requires sqlite3.Cursor; sqlcipher3 returns sqlcipher3.dbapi2.Cursor).
		self._conn.row_factory = _dict_row_factory
		self._ensure_schema()

	def _ensure_schema(self) -> None:
		self._conn.execute(
			f"""
            CREATE TABLE IF NOT EXISTS {USERS_TABLE} (
                user_id TEXT PRIMARY KEY,
                username TEXT UNIQUE NOT NULL,
                password_hash TEXT NOT NULL,
                admin INTEGER NOT NULL DEFAULT 0,
                groups TEXT NOT NULL DEFAULT '["user"]',
                sfw_check INTEGER NOT NULL DEFAULT 1,
                mfa_enabled INTEGER NOT NULL DEFAULT 0,
                totp_secret_encrypted TEXT,
                backup_code_hash TEXT,
                backup_code_used INTEGER NOT NULL DEFAULT 0
            )
        """
		)
		self._conn.commit()

	def migrate_from_json(self, legacy_path: str) -> bool:
		"""If legacy_path exists, load JSON and insert users; then rename file. Return True if migration ran."""
		if not os.path.exists(legacy_path):
			return False
		try:
			with open(legacy_path, "r", encoding="utf-8") as f:
				data = json.load(f)
		except Exception:
			return False
		users_data = data.get("users", data) if isinstance(data, dict) else data
		if isinstance(users_data, dict):
			items = list(users_data.items())
		else:
			items = [(i, u) for i, u in enumerate(users_data) if isinstance(u, dict)]
		for uid, u in items:
			uid = str(uid)
			username = u.get("username") or u.get("user", "")
			if not username:
				continue
			password = u.get("password", "")
			admin = bool(u.get("admin"))
			groups = u.get("groups", ["admin"] if admin else ["user"])
			sfw = u.get("sfw_check", True)
			self._conn.execute(
				f"""
                INSERT OR REPLACE INTO {USERS_TABLE}
                (user_id, username, password_hash, admin, groups, sfw_check, mfa_enabled, totp_secret_encrypted, backup_code_hash, backup_code_used)
                VALUES (?, ?, ?, ?, ?, ?, 0, NULL, NULL, 0)
                """,
				(
					uid,
					username,
					password,
					1 if admin else 0,
					_groups_to_json(groups),
					1 if sfw else 0,
				),
			)
		self._conn.commit()
		try:
			os.rename(legacy_path, legacy_path + ".migrated")
		except Exception:
			pass
		return True

	def get_all(self, secret_key: str) -> dict:
		"""Return { user_id: user_dict }."""
		rows = self._conn.execute(
			f"SELECT user_id, username, password_hash, admin, groups, sfw_check, mfa_enabled, totp_secret_encrypted, backup_code_hash, backup_code_used FROM {USERS_TABLE}"
		).fetchall()
		out = {}
		for r in rows:
			row = dict(r)
			uid = row.pop("user_id")
			out[uid] = _row_to_user(row, secret_key)
		return out

	def insert(self, user_id: str, user: dict, secret_key: str) -> None:
		"""Insert a user into the database."""
		row = _user_to_row(user, secret_key)
		self._conn.execute(
			f"""
            INSERT INTO {USERS_TABLE}
            (user_id, username, password_hash, admin, groups, sfw_check, mfa_enabled, totp_secret_encrypted, backup_code_hash, backup_code_used)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
			(
				user_id,
				row["username"],
				row["password_hash"],
				row["admin"],
				row["groups"],
				row["sfw_check"],
				row["mfa_enabled"],
				row["totp_secret_encrypted"] or None,
				row["backup_code_hash"] or None,
				row["backup_code_used"],
			),
		)
		self._conn.commit()

	def update(self, user_id: str, user: dict, secret_key: str) -> None:
		"""Update a user in the database."""
		row = _user_to_row(user, secret_key)
		self._conn.execute(
			f"""
            UPDATE {USERS_TABLE} SET
                username = ?, password_hash = ?, admin = ?, groups = ?, sfw_check = ?,
                mfa_enabled = ?, totp_secret_encrypted = ?, backup_code_hash = ?, backup_code_used = ?
            WHERE user_id = ?
            """,
			(
				row["username"],
				row["password_hash"],
				row["admin"],
				row["groups"],
				row["sfw_check"],
				row["mfa_enabled"],
				row["totp_secret_encrypted"] or None,
				row["backup_code_hash"] or None,
				row["backup_code_used"],
				user_id,
			),
		)
		self._conn.commit()

	def delete(self, user_id: str) -> None:
		"""Delete a user from the database."""
		self._conn.execute(f"DELETE FROM {USERS_TABLE} WHERE user_id = ?", (user_id,))
		self._conn.commit()


# ---------------------------------------------------------------------------
# PostgreSQL backend
# ---------------------------------------------------------------------------


class _PostgresUsersBackend:
	"""PostgreSQL backend for the users database."""

	def __init__(self, host: str, port: int, database: str, user: str, password: str):
		"""Initialize the PostgreSQL backend for the users database."""
		try:
			import psycopg2
			from psycopg2.extras import RealDictCursor
		except ImportError as e:
			_get_logger().error(
				f"PostgreSQL backend requires psycopg2; pip install psycopg2-binary: {e}"
			)
			raise RuntimeError(
				f"PostgreSQL backend requires psycopg2; pip install psycopg2-binary: {e}"
			) from e
		self._conn = psycopg2.connect(
			host=host, port=port, dbname=database, user=user, password=password
		)
		self._cursor_factory = RealDictCursor
		self._ensure_schema()

	def _ensure_schema(self) -> None:
		"""Ensure the schema is correct."""
		cur = self._conn.cursor()
		cur.execute(
			f"""
            CREATE TABLE IF NOT EXISTS {USERS_TABLE} (
                user_id TEXT PRIMARY KEY,
                username TEXT UNIQUE NOT NULL,
                password_hash TEXT NOT NULL,
                admin INTEGER NOT NULL DEFAULT 0,
                groups TEXT NOT NULL DEFAULT '["user"]',
                sfw_check INTEGER NOT NULL DEFAULT 1,
                mfa_enabled INTEGER NOT NULL DEFAULT 0,
                totp_secret_encrypted TEXT,
                backup_code_hash TEXT,
                backup_code_used INTEGER NOT NULL DEFAULT 0
            )
        """
		)
		self._conn.commit()
		cur.close()

	def migrate_from_json(self, legacy_path: str) -> bool:
		"""
		If legacy_path exists, load JSON and insert users; then rename file. Return True if migration ran.
		"""
		if not os.path.exists(legacy_path):
			return False
		try:
			with open(legacy_path, "r", encoding="utf-8") as f:
				data = json.load(f)
		except Exception:
			return False
		users_data = data.get("users", data) if isinstance(data, dict) else data
		if isinstance(users_data, dict):
			items = list(users_data.items())
		else:
			items = [(str(i), u) for i, u in enumerate(users_data) if isinstance(u, dict)]
		cur = self._conn.cursor()
		for uid, u in items:
			uid = str(uid)
			username = u.get("username") or u.get("user", "")
			if not username:
				continue
			password = u.get("password", "")
			admin = 1 if u.get("admin") else 0
			groups = _groups_to_json(u.get("groups", ["admin"] if admin else ["user"]))
			sfw = 1 if u.get("sfw_check", True) else 0
			cur.execute(
				f"""
                INSERT INTO {USERS_TABLE}
                (user_id, username, password_hash, admin, groups, sfw_check, mfa_enabled, totp_secret_encrypted, backup_code_hash, backup_code_used)
                VALUES (%s, %s, %s, %s, %s, %s, 0, NULL, NULL, 0)
                ON CONFLICT (user_id) DO UPDATE SET
                    username = EXCLUDED.username, password_hash = EXCLUDED.password_hash,
                    admin = EXCLUDED.admin, groups = EXCLUDED.groups, sfw_check = EXCLUDED.sfw_check
                """,
				(uid, username, password, admin, groups, sfw),
			)
		self._conn.commit()
		cur.close()
		try:
			os.rename(legacy_path, legacy_path + ".migrated")
		except Exception:
			pass
		return True

	def get_all(self, secret_key: str) -> dict:
		"""Return { user_id: user_dict }."""
		cur = self._conn.cursor(cursor_factory=self._cursor_factory)
		cur.execute(
			f"SELECT user_id, username, password_hash, admin, groups, sfw_check, mfa_enabled, totp_secret_encrypted, backup_code_hash, backup_code_used FROM {USERS_TABLE}"
		)
		rows = cur.fetchall()
		cur.close()
		out = {}
		for r in rows:
			uid = r.pop("user_id")
			out[uid] = _row_to_user(dict(r), secret_key)
		return out

	def insert(self, user_id: str, user: dict, secret_key: str) -> None:
		"""Insert a user into the database."""
		row = _user_to_row(user, secret_key)
		cur = self._conn.cursor()
		cur.execute(
			f"""
            INSERT INTO {USERS_TABLE}
            (user_id, username, password_hash, admin, groups, sfw_check, mfa_enabled, totp_secret_encrypted, backup_code_hash, backup_code_used)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            """,
			(
				user_id,
				row["username"],
				row["password_hash"],
				row["admin"],
				row["groups"],
				row["sfw_check"],
				row["mfa_enabled"],
				row["totp_secret_encrypted"] or None,
				row["backup_code_hash"] or None,
				row["backup_code_used"],
			),
		)
		self._conn.commit()
		cur.close()

	def update(self, user_id: str, user: dict, secret_key: str) -> None:
		"""Update a user in the database."""
		row = _user_to_row(user, secret_key)
		cur = self._conn.cursor()
		cur.execute(
			f"""
            UPDATE {USERS_TABLE} SET
                username = %s, password_hash = %s, admin = %s, groups = %s, sfw_check = %s,
                mfa_enabled = %s, totp_secret_encrypted = %s, backup_code_hash = %s, backup_code_used = %s
            WHERE user_id = %s
            """,
			(
				row["username"],
				row["password_hash"],
				row["admin"],
				row["groups"],
				row["sfw_check"],
				row["mfa_enabled"],
				row["totp_secret_encrypted"] or None,
				row["backup_code_hash"] or None,
				row["backup_code_used"],
				user_id,
			),
		)
		self._conn.commit()
		cur.close()

	def delete(self, user_id: str) -> None:
		"""Delete a user from the database."""
		cur = self._conn.cursor()
		cur.execute(f"DELETE FROM {USERS_TABLE} WHERE user_id = %s", (user_id,))
		self._conn.commit()
		cur.close()


# ---------------------------------------------------------------------------
# MySQL backend
# ---------------------------------------------------------------------------


class _MySQLUsersBackend:
	"""MySQL backend for the users database."""

	def __init__(self, host: str, port: int, database: str, user: str, password: str):
		try:
			import pymysql
			from pymysql.cursors import DictCursor
		except ImportError as e:
			_get_logger().error(f"MySQL backend requires pymysql; pip install pymysql: {e}")
			raise RuntimeError("MySQL backend requires pymysql; pip install pymysql") from e
		self._conn = pymysql.connect(
			host=host, port=port, user=user, password=password, database=database, charset="utf8mb4"
		)
		self._dict_cursor = DictCursor
		self._ensure_schema()

	def _ensure_schema(self) -> None:
		cur = self._conn.cursor()
		cur.execute(
			f"""
            CREATE TABLE IF NOT EXISTS {USERS_TABLE} (
                user_id VARCHAR(255) PRIMARY KEY,
                username VARCHAR(255) UNIQUE NOT NULL,
                password_hash TEXT NOT NULL,
                admin INT NOT NULL DEFAULT 0,
                groups TEXT NOT NULL,
                sfw_check INT NOT NULL DEFAULT 1,
                mfa_enabled INT NOT NULL DEFAULT 0,
                totp_secret_encrypted TEXT,
                backup_code_hash TEXT,
                backup_code_used INT NOT NULL DEFAULT 0
            )
            """
		)
		self._conn.commit()
		cur.close()

	def migrate_from_json(self, legacy_path: str) -> bool:
		if not os.path.exists(legacy_path):
			return False
		try:
			with open(legacy_path, "r", encoding="utf-8") as f:
				data = json.load(f)
		except Exception:
			return False
		users_data = data.get("users", data) if isinstance(data, dict) else data
		if isinstance(users_data, dict):
			items = list(users_data.items())
		else:
			items = [(str(i), u) for i, u in enumerate(users_data) if isinstance(u, dict)]
		cur = self._conn.cursor()
		for uid, u in items:
			uid = str(uid)
			username = u.get("username") or u.get("user", "")
			if not username:
				continue
			password = u.get("password", "")
			admin = 1 if u.get("admin") else 0
			groups = _groups_to_json(u.get("groups", ["admin"] if admin else ["user"]))
			sfw = 1 if u.get("sfw_check", True) else 0
			cur.execute(
				f"""
                INSERT INTO {USERS_TABLE}
                (user_id, username, password_hash, admin, groups, sfw_check, mfa_enabled, totp_secret_encrypted, backup_code_hash, backup_code_used)
                VALUES (%s, %s, %s, %s, %s, %s, 0, NULL, NULL, 0)
                ON DUPLICATE KEY UPDATE
                    username = VALUES(username), password_hash = VALUES(password_hash),
                    admin = VALUES(admin), groups = VALUES(groups), sfw_check = VALUES(sfw_check)
                """,
				(uid, username, password, admin, groups, sfw),
			)
		self._conn.commit()
		cur.close()
		try:
			os.rename(legacy_path, legacy_path + ".migrated")
		except Exception:
			pass
		return True

	def get_all(self, secret_key: str) -> dict:
		cur = self._conn.cursor(self._dict_cursor)
		cur.execute(
			f"SELECT user_id, username, password_hash, admin, groups, sfw_check, mfa_enabled, totp_secret_encrypted, backup_code_hash, backup_code_used FROM {USERS_TABLE}"
		)
		rows = cur.fetchall()
		cur.close()
		out = {}
		for r in rows:
			uid = r.pop("user_id")
			out[uid] = _row_to_user(dict(r), secret_key)
		return out

	def insert(self, user_id: str, user: dict, secret_key: str) -> None:
		row = _user_to_row(user, secret_key)
		cur = self._conn.cursor()
		cur.execute(
			f"""
            INSERT INTO {USERS_TABLE}
            (user_id, username, password_hash, admin, groups, sfw_check, mfa_enabled, totp_secret_encrypted, backup_code_hash, backup_code_used)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            """,
			(
				user_id,
				row["username"],
				row["password_hash"],
				row["admin"],
				row["groups"],
				row["sfw_check"],
				row["mfa_enabled"],
				row["totp_secret_encrypted"] or None,
				row["backup_code_hash"] or None,
				row["backup_code_used"],
			),
		)
		self._conn.commit()
		cur.close()

	def update(self, user_id: str, user: dict, secret_key: str) -> None:
		row = _user_to_row(user, secret_key)
		cur = self._conn.cursor()
		cur.execute(
			f"""
            UPDATE {USERS_TABLE} SET
                username = %s, password_hash = %s, admin = %s, groups = %s, sfw_check = %s,
                mfa_enabled = %s, totp_secret_encrypted = %s, backup_code_hash = %s, backup_code_used = %s
            WHERE user_id = %s
            """,
			(
				row["username"],
				row["password_hash"],
				row["admin"],
				row["groups"],
				row["sfw_check"],
				row["mfa_enabled"],
				row["totp_secret_encrypted"] or None,
				row["backup_code_hash"] or None,
				row["backup_code_used"],
				user_id,
			),
		)
		self._conn.commit()
		cur.close()

	def delete(self, user_id: str) -> None:
		cur = self._conn.cursor()
		cur.execute(f"DELETE FROM {USERS_TABLE} WHERE user_id = %s", (user_id,))
		self._conn.commit()
		cur.close()


# ---------------------------------------------------------------------------
# SECRET_KEY migration: re-encrypt TOTP secrets from old key to new key
# ---------------------------------------------------------------------------


def migrate_totp_to_new_key(config: dict, old_key: str, new_key: str) -> bool:
	"""
	Re-encrypt all TOTP secrets in the users DB from old_key to new_key.
	Used when switching from ephemeral to permanent SECRET_KEY.
	Returns True if migration completed successfully.
	"""
	if not old_key or not new_key:
		return False
	backend_type = (config.get("backend") or "sqlite").lower()
	if backend_type == "sqlite":
		backend = _SqliteUsersBackend(
			config.get("sqlite_path", "users/users.db"),
			secret_key=old_key,
			encryption_level=config.get("encryption_level", ""),
		)
	elif backend_type == "postgresql":
		backend = _PostgresUsersBackend(
			config.get("postgres_host", "localhost"),
			int(config.get("postgres_port", 5432)),
			config.get("postgres_database", "mss-login"),
			config.get("postgres_user", "mss-login"),
			config.get("postgres_password", ""),
		)
	elif backend_type == "mysql":
		backend = _MySQLUsersBackend(
			config.get("mysql_host", "localhost"),
			int(config.get("mysql_port", 3306)),
			config.get("mysql_database", "mss_login"),
			config.get("mysql_user", "mss_login"),
			config.get("mysql_password", ""),
		)
	else:
		backend = _SqliteUsersBackend(
			config.get("sqlite_path", "users/users.db"),
			secret_key=old_key,
			encryption_level=config.get("encryption_level", ""),
		)
	try:
		users = backend.get_all(old_key)
		for uid, user in users.items():
			backend.update(uid, user, new_key)
		return True
	except Exception:
		return False


# ---------------------------------------------------------------------------
# UsersDB (unified API)
# ---------------------------------------------------------------------------


class UsersDB:
	"""Users database (unified API)."""

	def __init__(self, config: dict, secret_key: str, legacy_json_path: str | None = None):
		"""Initialize the users database."""
		self._config = config
		self._secret_key = secret_key
		self._legacy_path = legacy_json_path
		backend = (config.get("backend") or "sqlite").lower()
		if backend == "sqlite":
			self._backend = _SqliteUsersBackend(
				config.get("sqlite_path", "users/users.db"),
				secret_key=secret_key,
				encryption_level=config.get("encryption_level", ""),
			)
		elif backend == "postgresql":
			self._backend = _PostgresUsersBackend(
				config.get("postgres_host", "localhost"),
				int(config.get("postgres_port", 5432)),
				config.get("postgres_database", "mss-login"),
				config.get("postgres_user", "mss-login"),
				config.get("postgres_password", ""),
			)
		elif backend == "mysql":
			self._backend = _MySQLUsersBackend(
				config.get("mysql_host", "localhost"),
				int(config.get("mysql_port", 3306)),
				config.get("mysql_database", "mss_login"),
				config.get("mysql_user", "mss_login"),
				config.get("mysql_password", ""),
			)
		else:
			self._backend = _SqliteUsersBackend(
				config.get("sqlite_path", "users/users.db"),
				secret_key=secret_key,
				encryption_level=config.get("encryption_level", ""),
			)
		self.users: dict = {}
		self.admin_user: tuple[str | None, dict] = (None, {})
		if legacy_json_path:
			self._backend.migrate_from_json(legacy_json_path)
		self.load_users()

	@staticmethod
	def hash_password(password: str) -> str:
		"""Hash a password."""
		try:
			return bcrypt.hashpw(password.encode("utf-8"), bcrypt.gensalt()).decode("utf-8")  # type: ignore
		except Exception as e:
			_get_logger().error(f"[MSS-Login] Failed to hash password: [REDACTED]: {e}")
			return ""

	def load_users(self) -> dict:
		"""Load all users from the database."""
		self.users = self._backend.get_all(self._secret_key)
		self._ensure_groups_schema()
		return self.users

	def _ensure_groups_schema(self) -> None:
		"""Ensure the groups schema is correct."""
		changed = False
		for uid, user in list(self.users.items()):
			if "groups" not in user or not isinstance(user["groups"], list) or not user["groups"]:
				user["groups"] = ["admin"] if user.get("admin") else ["user"]
				changed = True
		self._ensure_owner_assigned()
		if changed:
			for uid, user in self.users.items():
				self._backend.update(uid, user, self._secret_key)

	@staticmethod
	def _user_is_admin(user: dict) -> bool:
		"""True if user has admin privilege (admin flag, admin group, or owner group)."""
		if not user:
			return False
		if user.get("admin"):
			return True
		groups = [str(g).lower() for g in user.get("groups", [])]
		return "admin" in groups or "owner" in groups

	@staticmethod
	def _normalize_owner_groups(groups: list, is_admin: bool) -> tuple[list[str], bool]:
		"""Ensure owner always retains admin group and admin flag."""
		normalized = [str(g).lower() for g in (groups or [])]
		if "owner" in normalized:
			if "admin" not in normalized:
				normalized.append("admin")
			return normalized, True
		return normalized, bool(is_admin)

	def _owner_exists_in_loaded(self) -> bool:
		"""True if any loaded user has owner in groups (no reload)."""
		for user in self.users.values():
			groups = [str(g).lower() for g in user.get("groups", [])]
			if "owner" in groups:
				return True
		return False

	def _ensure_owner_assigned(self) -> None:
		"""Ensure exactly one owner exists (migration / repair).

		- If no owner: promote a deterministic admin (prefer username ``mss_admin``
		  when that account is admin; otherwise the lexicographically first admin
		  username) so restarts do not flip ownership based on dict order.
		- If multiple owners: keep the preferred one, demote the rest to admin.
		"""
		owners: list[tuple[str, dict, str]] = []
		for uid, user in self.users.items():
			groups = [g.lower() for g in user.get("groups", [])]
			if "owner" in groups:
				owners.append((uid, user, str(user.get("username") or "")))

		if len(owners) > 1:
			owners_sorted = sorted(owners, key=lambda t: t[2].lower())
			preferred = next(
				(t for t in owners_sorted if t[2].lower() == "mss_admin"), owners_sorted[0]
			)
			keep_uid = preferred[0]
			for uid, user, _uname in owners:
				if uid == keep_uid:
					normalized, admin_flag = self._normalize_owner_groups(
						user.get("groups", []), True
					)
					if user.get("groups") != normalized or user.get("admin") is not admin_flag:
						user["groups"] = normalized
						user["admin"] = admin_flag
						self._backend.update(uid, user, self._secret_key)
						self.users[uid] = user
					continue
				# Demote extra owners to admin (never leave zero admins).
				new_groups = [
					g for g in [str(x).lower() for x in user.get("groups", [])] if g != "owner"
				]
				if "admin" not in new_groups:
					new_groups.insert(0, "admin")
				user["groups"] = new_groups
				user["admin"] = True
				self._backend.update(uid, user, self._secret_key)
				self.users[uid] = user
			return

		if owners:
			# Heal single owner to always retain admin group/flag
			uid, user, _uname = owners[0]
			normalized, admin_flag = self._normalize_owner_groups(user.get("groups", []), True)
			if user.get("groups") != normalized or user.get("admin") is not admin_flag:
				user["groups"] = normalized
				user["admin"] = admin_flag
				self._backend.update(uid, user, self._secret_key)
				self.users[uid] = user
			return

		admin_uid, admin_user = self._find_admin_in_loaded_users()
		if not admin_uid or not admin_user:
			return
		groups = list(admin_user.get("groups", ["admin"]))
		if "owner" not in [g.lower() for g in groups]:
			admin_user["groups"] = ["owner"] + [g for g in groups if g.lower() != "owner"]
			if "admin" not in [g.lower() for g in admin_user["groups"]]:
				admin_user["groups"].append("admin")
			admin_user["admin"] = True
			self._backend.update(admin_uid, admin_user, self._secret_key)
			self.users[admin_uid] = admin_user

	def _find_admin_in_loaded_users(self) -> tuple[str | None, dict]:
		"""Find a preferred admin in the already-loaded self.users without reloading.

		Prefers username ``mss_admin`` when that account is admin; otherwise the
		lexicographically first admin username for stable migration behavior.
		"""
		admins: list[tuple[str, dict, str]] = []
		for uid, user_data in self.users.items():
			if self._user_is_admin(user_data):
				admins.append((uid, user_data, str(user_data.get("username") or "")))
		if not admins:
			return (None, {})
		for uid, user_data, uname in admins:
			if uname.lower() == "mss_admin":
				return (uid, user_data)
		admins.sort(key=lambda t: t[2].lower())
		return (admins[0][0], admins[0][1])

	def save_users(self, users: dict) -> None:
		"""Persist entire users dict (used by code that expects legacy behavior). Prefer update_user/add_user/delete_user."""
		for uid, user in users.items():
			self._backend.update(uid, user, self._secret_key)
		self.users = users

	def _has_admin(self) -> bool:
		self.load_users()
		for _uid, user in self.users.items():
			if self._user_is_admin(user):
				return True
		return False

	def add_user(
		self,
		user_id: str,
		username: str,
		password: str,
		admin: bool,
		groups: list[str] | None = None,
	) -> None:
		"""Add a user to the database.

		When ``groups`` is provided (owner create-user API), those groups are used
		as-is (caller must not pass ``owner`` for a second owner). Otherwise the
		legacy bootstrap/admin/user assignment applies.
		"""
		try:
			self.load_users()
			username = _sanitize_username(username)
			if not username:
				return
			has_admin = self._has_admin()
			has_owner = self._owner_exists_in_loaded()
			if not has_admin and len(self.users) == 0:
				admin = True
			# First admin gets owner only when no owner/admin exists yet
			grant_owner = bool(admin) and not has_admin and not has_owner
			if groups is not None:
				normalized = [str(g).strip().lower() for g in groups if str(g).strip()]
				if not normalized:
					normalized = ["user"]
				# Never mint a second owner through this path
				if "owner" in normalized and (has_owner or has_admin or len(self.users) > 0):
					normalized = [g for g in normalized if g != "owner"]
					if not normalized:
						normalized = ["admin"] if admin else ["user"]
				if "owner" in normalized and "admin" not in normalized:
					normalized = list(normalized) + ["admin"]
				is_admin_flag = "admin" in normalized or "owner" in normalized
				assigned = normalized
			else:
				assigned = ["owner", "admin"] if grant_owner else (["admin"] if admin else ["user"])
				is_admin_flag = bool(admin) or grant_owner
			user = {
				"username": username,
				"password": self.hash_password(password),
				"admin": bool(is_admin_flag),
				"groups": assigned,
			}
			self._backend.insert(user_id, user, self._secret_key)
			self.users[user_id] = user
		except Exception as e:
			_get_logger().error(f"[MSS-Login] Failed to add user: {user_id}: {e}")

	def get_user(self, username: str = "", user_id: str = "") -> tuple[str | None, dict]:
		"""Get a user by username or user_id."""
		self.load_users()
		if user_id:
			user = self.users.get(user_id)
			if user is not None:
				return user_id, dict(user)
			return None, {}
		# Normalize username for lookup (defense-in-depth; prevents injection-style values)
		username = _sanitize_username(username)
		for uid, user_data in self.users.items():
			if user_data.get("username") == username:
				return uid, dict(user_data)
		return None, {}

	def check_username_password(self, username: str, password: str) -> bool:
		"""
		Check if a username and password match.

		Also accepts passwords that were hashed after the legacy XSS sanitizer
		mutated them (pre-fix installs). On a legacy match, re-hashes with the
		raw password so future logins use the correct form.
		"""
		user_id, user_data = self.get_user(username=username)
		if not user_id or not user_data:
			return False
		stored = user_data.get("password", "") or ""
		if not stored:
			return False
		try:
			stored_bytes = stored.encode("utf-8")
			password_bytes = password.encode("utf-8")
			if bcrypt.checkpw(password_bytes, stored_bytes):
				return True
			# Legacy: middleware XSS-sanitized the password before hashing/verify.
			legacy = apply_legacy_xss_sanitize(password)
			if (
				legacy
				and legacy != password
				and bcrypt.checkpw(legacy.encode("utf-8"), stored_bytes)
			):
				# Upgrade stored hash to raw password for future logins.
				new_hash = self.hash_password(password)
				if new_hash:
					user_data["password"] = new_hash
					user_data["password_hash"] = new_hash
					self._backend.update(user_id, user_data, self._secret_key)
					self.users[user_id] = user_data
				return True
			return False
		except (ValueError, TypeError):
			return False

	def get_admin_user(self) -> tuple[str | None, dict] | None:
		"""Get the admin user."""
		self.load_users()
		self.admin_user = (None, {})
		for uid, user_data in self.users.items():
			if self._user_is_admin(user_data):
				self.admin_user = (uid, user_data)
				break
		return self.admin_user

	def list_users_for_admin(self) -> list[dict]:
		"""Return list of { username, groups, is_admin, sfw_check } for admin API."""
		self.load_users()
		out = []
		for _uid, u in self.users.items():
			out.append(
				{
					"username": u.get("username", "unknown"),
					"groups": [g.lower() for g in u.get("groups", ["user"])],
					"is_admin": u.get("admin", False),
					"sfw_check": u.get("sfw_check", True),
				}
			)
		return out

	def get_owner_username(self) -> str | None:
		"""Return the username of the user who has 'owner' in groups, or None if no owner."""
		self.load_users()
		for _uid, u in self.users.items():
			groups = [g.lower() for g in u.get("groups", [])]
			if "owner" in groups:
				return u.get("username")
		return None

	def update_user(
		self, username: str, groups: list, is_admin: bool, sfw_check: bool | None = None
	) -> bool:
		"""Update groups, admin, and optionally sfw_check for a user. Returns True if found and updated."""
		user_id, user = self.get_user(username=username)
		if not user_id:
			return False
		normalized_groups, normalized_admin = self._normalize_owner_groups(groups, is_admin)
		user["groups"] = normalized_groups
		user["admin"] = normalized_admin
		if sfw_check is not None:
			user["sfw_check"] = bool(sfw_check)
		self._backend.update(user_id, user, self._secret_key)
		self.users[user_id] = user
		return True

	def delete_user(self, username: str) -> bool | str:
		"""Delete user. Returns True on success, False if not found, 'last_admin' if would remove last admin."""
		self.load_users()
		user_id, user = self.get_user(username=username)
		if not user_id:
			return False
		admins = sum(1 for u in self.users.values() if self._user_is_admin(u))
		if self._user_is_admin(user) and admins <= 1:
			return "last_admin"
		self._backend.delete(user_id)
		self.users.pop(user_id, None)
		return True

	# ----------------------------
	# MFA
	# ----------------------------

	def get_mfa_enabled(self, username: str) -> bool:
		"""Return True if user has MFA enabled."""
		_uid, user = self.get_user(username=username)
		if not user:
			return False
		return bool(user.get("mfa_enabled", False))

	def mfa_setup_start(self, username: str) -> tuple[str, str] | None:
		"""
		Start MFA setup: generate TOTP secret and backup code, store encrypted/hashed, return (provisioning_uri, backup_code).
		Returns None if user not found. Call mfa_verify_setup after user scans QR and enters first code.
		"""
		try:
			import pyotp
		except ImportError:
			return None
		user_id, user = self.get_user(username=username)
		if not user_id:
			return None
		secret = pyotp.random_base32()
		# 64-bit backup code: 16 hex chars in 4 groups of 4, e.g. ABCD-1234-EFGH-5678
		_raw = secrets.token_hex(8).upper()
		backup_code = "-".join(_raw[i : i + 4] for i in range(0, 16, 4))
		backup_hash = hash_backup_code(backup_code.replace("-", "").upper())
		enc = encrypt_value(self._secret_key, secret)
		if not enc:
			return None
		user["totp_secret_encrypted"] = enc
		user["backup_code_hash"] = backup_hash
		user["backup_code_used"] = False
		user["mfa_enabled"] = False
		self._backend.update(user_id, user, self._secret_key)
		self.users[user_id] = user
		totp = pyotp.TOTP(secret)
		provisioning_uri = totp.provisioning_uri(name=username, issuer_name="mss-login")
		return (provisioning_uri, backup_code)

	def mfa_verify_setup(self, username: str, code: str) -> bool:
		"""Verify TOTP code and enable MFA for user. Returns True if code valid and MFA enabled."""
		try:
			import pyotp
		except ImportError:
			return False
		user_id, user = self.get_user(username=username)
		if not user_id:
			return False
		enc = user.get("totp_secret_encrypted")
		if not enc:
			return False
		secret = decrypt_value(self._secret_key, enc)
		if not secret:
			return False
		totp = pyotp.TOTP(secret)
		if not totp.verify(code, valid_window=1):
			return False
		user["mfa_enabled"] = True
		self._backend.update(user_id, user, self._secret_key)
		self.users[user_id] = user
		return True

	def verify_totp(self, username: str, code: str) -> bool:
		"""Verify TOTP code for user. Returns True if valid."""
		try:
			import pyotp
		except ImportError:
			return False
		user_id, user = self.get_user(username=username)
		if not user_id:
			return False
		enc = user.get("totp_secret_encrypted")
		if not enc:
			return False
		secret = decrypt_value(self._secret_key, enc)
		if not secret:
			return False
		totp = pyotp.TOTP(secret)
		return bool(totp.verify(code, valid_window=1))

	def verify_backup_code_and_consume(self, username: str, backup_code: str) -> bool:
		"""Verify backup code (constant-time) and mark as used. Returns True if valid and not already used."""
		user_id, user = self.get_user(username=username)
		if not user_id:
			return False
		if user.get("backup_code_used"):
			return False
		stored_hash = user.get("backup_code_hash")
		if not stored_hash:
			return False
		if not verify_backup_code(backup_code, stored_hash):
			return False
		user["backup_code_used"] = True
		self._backend.update(user_id, user, self._secret_key)
		self.users[user_id] = user
		return True

	def reset_mfa_for_all_users(self) -> int:
		"""
		Clear MFA for all users (totp_secret_encrypted, backup_code_hash, mfa_enabled=0).
		Used by recovery mode when SECRET_KEY changed and migration was not possible.
		Returns the number of users updated.
		"""
		self.load_users()
		count = 0
		for uid, user in list(self.users.items()):
			if (
				user.get("mfa_enabled")
				or user.get("totp_secret_encrypted")
				or user.get("backup_code_hash")
			):
				user["mfa_enabled"] = False
				user["totp_secret_encrypted"] = ""
				user["backup_code_hash"] = ""
				user["backup_code_used"] = False
				if "_totp_secret_plain" in user:
					del user["_totp_secret_plain"]
				self._backend.update(uid, user, self._secret_key)
				count += 1
		return count
