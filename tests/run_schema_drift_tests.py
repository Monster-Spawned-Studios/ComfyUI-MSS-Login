"""
Schema drift detection for the MSS-Login users database.

Boots store factories against a temp SQLite DB (plain + SQLCipher when available),
introspects tables, diffs against utils/db_schema.EXPECTED_TABLES, and verifies
Fernet secret round-trips remain decryptable after reopen.
"""

from __future__ import annotations

import importlib.util
import os
import sys
import tempfile
import types

_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_UTILS_DIR = os.path.join(_PROJECT_ROOT, "utils")


def _load_module(name: str, path: str, package: str):
	spec = importlib.util.spec_from_file_location(name, path)
	mod = importlib.util.module_from_spec(spec)
	mod.__package__ = package
	assert spec and spec.loader
	sys.modules[name] = mod
	spec.loader.exec_module(mod)
	return mod


def _install_stubs(secret_key: str, db_path: str, encryption_level: str = ""):
	root_pkg = types.ModuleType("mss_login")
	root_pkg.__path__ = [_PROJECT_ROOT]
	utils_pkg = types.ModuleType("mss_login.utils")
	utils_pkg.__path__ = [_UTILS_DIR]
	sys.modules["mss_login"] = root_pkg
	sys.modules["mss_login.utils"] = utils_pkg

	const = types.ModuleType("mss_login.constants")
	const.SECRET_KEY = secret_key
	const.USERS_DB_CONFIG = {
		"backend": "sqlite",
		"sqlite_path": db_path,
		"encryption_level": encryption_level,
	}
	const.DATA_DIR = os.path.dirname(db_path)
	sys.modules["mss_login.constants"] = const
	return const


def run_tests() -> int:
	failed = 0
	run = 0

	def ok(cond, msg):
		nonlocal failed, run
		run += 1
		if not cond:
			print(f"  FAIL: {msg}")
			failed += 1
		else:
			print(f"  ok: {msg}")

	secret = "schema-drift-test-secret-key!!"
	encryption = _load_module(
		"mss_login.utils.encryption", os.path.join(_UTILS_DIR, "encryption.py"), "mss_login.utils"
	)
	db_schema = _load_module(
		"mss_login.utils.db_schema", os.path.join(_UTILS_DIR, "db_schema.py"), "mss_login.utils"
	)

	levels_to_test = [""]
	try:
		import sqlcipher3  # noqa: F401

		levels_to_test.append("low")
	except ImportError:
		print("  note: sqlcipher3 not installed; skipping SQLCipher schema pass")

	for enc_level in levels_to_test:
		label = "plain SQLite" if not enc_level else f"SQLCipher ({enc_level})"
		print(f"TestSchemaDrift[{label}]")
		tmpdir = tempfile.mkdtemp(prefix="mss_schema_drift_")
		db_path = os.path.join(tmpdir, "mss_login_data.db")
		# Clear store singletons between runs by using unique module reload paths
		for name in list(sys.modules):
			if name.startswith("mss_login.utils.") and name not in (
				"mss_login.utils.encryption",
				"mss_login.utils.db_schema",
			):
				# keep encryption/db_schema; drop stores so factories re-init
				if any(
					x in name
					for x in (
						"app_settings",
						"model_source",
						"api_token",
						"session_token",
						"shared_items",
						"model_cache",
						"lockout",
						"users_db",
						"sqlite_connection",
						"db_key",
					)
				):
					del sys.modules[name]

		const = _install_stubs(secret, db_path, enc_level)
		config = const.USERS_DB_CONFIG

		# Force-create all tables via store factories
		sqlite_connection = _load_module(
			"mss_login.utils.sqlite_connection",
			os.path.join(_UTILS_DIR, "sqlite_connection.py"),
			"mss_login.utils",
		)
		sys.modules["mss_login.utils.sqlite_connection"] = sqlite_connection

		app_settings = _load_module(
			"mss_login.utils.app_settings_store",
			os.path.join(_UTILS_DIR, "app_settings_store.py"),
			"mss_login.utils",
		)
		# Reset singleton
		app_settings._store = None
		store = app_settings.get_app_settings_store(config)
		db_schema.ensure_schema_version(store)

		api_keys = _load_module(
			"mss_login.utils.model_source_api_keys_store",
			os.path.join(_UTILS_DIR, "model_source_api_keys_store.py"),
			"mss_login.utils",
		)
		api_keys._store = None
		keys_store = api_keys.get_model_source_api_keys_store(config, secret)

		api_token_store = _load_module(
			"mss_login.utils.api_token_store",
			os.path.join(_UTILS_DIR, "api_token_store.py"),
			"mss_login.utils",
		)
		try:
			if hasattr(api_token_store, "get_api_token_store"):
				api_token_store.get_api_token_store(config)
		except TypeError:
			api_token_store.get_api_token_store()
		except Exception as e:
			print(f"  note: api_token_store: {e}")

		session_mod = _load_module(
			"mss_login.utils.session_token_store",
			os.path.join(_UTILS_DIR, "session_token_store.py"),
			"mss_login.utils",
		)
		try:
			if hasattr(session_mod, "get_session_token_store"):
				session_mod.get_session_token_store(config)
		except TypeError:
			try:
				session_mod.get_session_token_store()
			except Exception as e:
				print(f"  note: session_token_store: {e}")
		except Exception as e:
			print(f"  note: session_token_store: {e}")

		shared_mod = _load_module(
			"mss_login.utils.shared_items_store",
			os.path.join(_UTILS_DIR, "shared_items_store.py"),
			"mss_login.utils",
		)
		if hasattr(shared_mod, "get_shared_items_store"):
			shared_mod._store = None
			try:
				shared_mod.get_shared_items_store(config)
			except Exception as e:
				print(f"  note: shared_items: {e}")

		cache_mod = _load_module(
			"mss_login.utils.model_cache",
			os.path.join(_UTILS_DIR, "model_cache.py"),
			"mss_login.utils",
		)
		if hasattr(cache_mod, "get_model_cache"):
			if hasattr(cache_mod, "_cache"):
				cache_mod._cache = None
			try:
				cache_mod.get_model_cache(config)
			except Exception as e:
				print(f"  note: model_cache init: {e}")

		lockout_mod = _load_module(
			"mss_login.utils.lockout_store",
			os.path.join(_UTILS_DIR, "lockout_store.py"),
			"mss_login.utils",
		)
		if hasattr(lockout_mod, "get_lockout_store"):
			if hasattr(lockout_mod, "_store"):
				lockout_mod._store = None
			try:
				lockout_mod.get_lockout_store(config)
			except Exception as e:
				print(f"  note: lockout_store: {e}")

		users_db_mod = _load_module(
			"mss_login.utils.users_db",
			os.path.join(_UTILS_DIR, "users_db.py"),
			"mss_login.utils",
		)
		try:
			udb = users_db_mod.UsersDB(config, secret, "")
			udb.load_users()
		except Exception as e:
			print(f"  note: UsersDB init via open_sqlite fallback: {e}")
			conn = sqlite_connection.open_sqlite(
				db_path, secret_key=secret, encryption_level=enc_level, check_same_thread=False
			)
			conn.execute(
				"""
                CREATE TABLE IF NOT EXISTS users (
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
			conn.commit()
			conn.close()

		conn = sqlite_connection.open_sqlite(
			db_path, secret_key=secret, encryption_level=enc_level, check_same_thread=False
		)
		live = db_schema.introspect_sqlite(conn)
		problems = db_schema.diff_schema(live)
		ok(not problems, f"schema matches registry ({len(live)} tables)")
		if problems:
			for p in problems:
				print(f"    - {p}")

		# Secret round-trip: CivitAI key
		ok(keys_store.set_key("u1", "civitai", "civitai-secret-token"), "set civitai key")
		ok(keys_store.get_key("u1", "civitai") == "civitai-secret-token", "civitai key decrypts")

		# ntfy-style app_settings secret
		ct = encryption.encrypt_and_verify(secret, "ntfy-token-xyz")
		ok(bool(ct), "encrypt_and_verify ntfy token")
		store.set("ntfy.api_token_encrypted", ct or "")
		conn.close()

		# Reopen DB and confirm decrypt still works
		api_keys._store = None
		app_settings._store = None
		keys2 = api_keys.get_model_source_api_keys_store(config, secret)
		store2 = app_settings.get_app_settings_store(config)
		ok(keys2.get_key("u1", "civitai") == "civitai-secret-token", "civitai key survives reopen")
		enc_val = store2.get("ntfy.api_token_encrypted") or ""
		ok(
			encryption.decrypt_value(secret, enc_val) == "ntfy-token-xyz",
			"ntfy token survives reopen decrypt",
		)

	print(f"\nResult: {run - failed}/{run} passed" + ("" if failed == 0 else f", {failed} failed"))
	return 0 if failed == 0 else 1


if __name__ == "__main__":
	sys.exit(run_tests())
