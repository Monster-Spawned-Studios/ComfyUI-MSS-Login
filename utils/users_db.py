# --- START OF FILE utils/users_db.py ---
"""
Users database: SQLite or PostgreSQL only. No plain-text JSON for credentials.
Supports migration from legacy JSON on first run. MFA fields encrypted with SECRET_KEY.
SQLite may use SQLCipher (encryption_level in config) with SECRET_KEY-derived key.
"""

import bcrypt
import json
import os
import secrets
from pathlib import Path
from typing import Any, Optional, Tuple

from .encryption import encrypt_value, decrypt_value, hash_backup_code, verify_backup_code

# Schema: user_id, username, password_hash, admin, groups (JSON), sfw_check,
#         mfa_enabled, totp_secret_encrypted, backup_code_hash, backup_code_used
USERS_TABLE = "users"


def _groups_to_json(groups: list) -> str:
	if not isinstance(groups, list):
		groups = ["user"]
	return json.dumps([str(g) for g in groups])


def _json_to_groups(s: Optional[str]) -> list:
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
		self._conn.execute(f"""
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
        """)
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
		self._conn.execute(f"DELETE FROM {USERS_TABLE} WHERE user_id = ?", (user_id,))
		self._conn.commit()


# ---------------------------------------------------------------------------
# PostgreSQL backend
# ---------------------------------------------------------------------------


class _PostgresUsersBackend:
	def __init__(self, host: str, port: int, database: str, user: str, password: str):
		try:
			import psycopg2
			from psycopg2.extras import RealDictCursor
		except ImportError:
			raise RuntimeError("PostgreSQL backend requires psycopg2; pip install psycopg2-binary")
		self._conn = psycopg2.connect(
			host=host,
			port=port,
			dbname=database,
			user=user,
			password=password,
		)
		self._cursor_factory = RealDictCursor
		self._ensure_schema()

	def _ensure_schema(self) -> None:
		cur = self._conn.cursor()
		cur.execute(f"""
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
        """)
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
			config.get("postgres_database", "mss_login"),
			config.get("postgres_user", "mss_login"),
			config.get("postgres_password", ""),
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
	def __init__(self, config: dict, secret_key: str, legacy_json_path: Optional[str] = None):
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
				config.get("postgres_database", "mss_login"),
				config.get("postgres_user", "mss_login"),
				config.get("postgres_password", ""),
			)
		else:
			self._backend = _SqliteUsersBackend(
				config.get("sqlite_path", "users/users.db"),
				secret_key=secret_key,
				encryption_level=config.get("encryption_level", ""),
			)
		self.users: dict = {}
		self.admin_user: tuple[Optional[str], dict] = (None, {})
		if legacy_json_path:
			self._backend.migrate_from_json(legacy_json_path)
		self.load_users()

	@staticmethod
	def hash_password(password: str) -> str:
		return bcrypt.hashpw(password.encode("utf-8"), bcrypt.gensalt()).decode("utf-8")

	def load_users(self) -> dict:
		self.users = self._backend.get_all(self._secret_key)
		self._ensure_groups_schema()
		return self.users

	def _ensure_groups_schema(self) -> None:
		changed = False
		for uid, user in list(self.users.items()):
			if "groups" not in user or not isinstance(user["groups"], list) or not user["groups"]:
				user["groups"] = ["admin"] if user.get("admin") else ["user"]
				changed = True
		if changed:
			for uid, user in self.users.items():
				self._backend.update(uid, user, self._secret_key)

	def save_users(self, users: dict) -> None:
		"""Persist entire users dict (used by code that expects legacy behavior). Prefer update_user/add_user/delete_user."""
		for uid, user in users.items():
			self._backend.update(uid, user, self._secret_key)
		self.users = users

	def _has_admin(self) -> bool:
		self.load_users()
		for _uid, user in self.users.items():
			if user.get("admin"):
				return True
			groups = [g.lower() for g in user.get("groups", [])]
			if "admin" in groups:
				return True
		return False

	def add_user(self, id: str, username: str, password: str, admin: bool) -> None:
		self.load_users()
		has_admin = self._has_admin()
		if not has_admin and len(self.users) == 0:
			admin = True
		groups = ["admin"] if admin else ["user"]
		user = {
			"username": username,
			"password": self.hash_password(password),
			"admin": bool(admin),
			"groups": groups,
		}
		self._backend.insert(id, user, self._secret_key)
		self.users[id] = user

	def get_user(self, username: str = "", user_id: str = "") -> tuple[Optional[str], dict]:
		self.load_users()
		if user_id:
			user = self.users.get(user_id)
			if user is not None:
				return user_id, dict(user)
			return None, {}
		for uid, user_data in self.users.items():
			if user_data.get("username") == username:
				return uid, dict(user_data)
		return None, {}

	def check_username_password(self, username: str, password: str) -> bool:
		user_id, user_data = self.get_user(username=username)
		if not user_id or not user_data:
			return False
		pw = user_data.get("password", "")
		return bcrypt.checkpw(password.encode("utf-8"), pw.encode("utf-8"))

	def get_admin_user(self) -> tuple[Optional[str], dict] | None:
		self.load_users()
		self.admin_user = (None, {})
		for uid, user_data in self.users.items():
			groups = [g.lower() for g in user_data.get("groups", [])]
			if user_data.get("admin") or "admin" in groups:
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

	def update_user(
		self, username: str, groups: list, is_admin: bool, sfw_check: Optional[bool] = None
	) -> bool:
		"""Update groups, admin, and optionally sfw_check for a user. Returns True if found and updated."""
		user_id, user = self.get_user(username=username)
		if not user_id:
			return False
		user["groups"] = [g.lower() for g in groups]
		user["admin"] = is_admin
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
		admins = sum(
			1
			for u in self.users.values()
			if u.get("admin") or "admin" in [g.lower() for g in u.get("groups", [])]
		)
		if (
			user.get("admin") or "admin" in [g.lower() for g in user.get("groups", [])]
		) and admins <= 1:
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

	def mfa_setup_start(self, username: str) -> Optional[Tuple[str, str]]:
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
		backup_code = "-".join(secrets.token_hex(2).upper() for _ in range(2))  # e.g. ABCD-1234
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
		provisioning_uri = totp.provisioning_uri(name=username, issuer_name="mss_login")
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
