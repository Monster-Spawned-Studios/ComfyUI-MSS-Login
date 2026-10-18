#!/usr/bin/env python3
"""
MFA hardening regression tests (PR1).

  .venv/bin/python tests/run_mfa_tests.py
"""

from __future__ import annotations

import importlib.util
import os
import sys
import tempfile
import time
import types
from pathlib import Path

_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_UTILS = os.path.join(_PROJECT_ROOT, "utils")

failed: list[str] = []


def _ok(cond: bool, msg: str) -> None:
	if cond:
		print(f"  ok: {msg}")
	else:
		print(f"  FAIL: {msg}")
		failed.append(msg)


def _ensure_utils_pkg() -> None:
	if "utils" not in sys.modules:
		pkg = types.ModuleType("utils")
		pkg.__path__ = [_UTILS]
		sys.modules["utils"] = pkg


def _load(name: str, filename: str):
	_ensure_utils_pkg()
	path = os.path.join(_UTILS, filename)
	mod_name = f"utils.{name}"
	sys.modules.pop(mod_name, None)
	spec = importlib.util.spec_from_file_location(mod_name, path)
	mod = importlib.util.module_from_spec(spec)
	sys.modules[mod_name] = mod
	spec.loader.exec_module(mod)
	return mod


def _make_users_db(tmpdir: str):
	# Stub heavy deps before loading users_db
	if "folder_paths" not in sys.modules:
		sys.modules["folder_paths"] = types.ModuleType("folder_paths")
	enc = _load("encryption", "encryption.py")
	# input_sanitizer / sanitizer may pull more — load users_db carefully
	for dep, fn in (
		("input_sanitizer", "input_sanitizer.py"),
		("sanitizer", "sanitizer.py"),
		("sqlite_connection", "sqlite_connection.py"),
	):
		try:
			_load(dep, fn)
		except Exception as e:
			print(f"  note: optional load {dep}: {e}")
	udb_mod = _load("users_db", "users_db.py")
	cfg = {"backend": "sqlite", "sqlite_path": os.path.join(tmpdir, "users.db")}
	db = udb_mod.UsersDB(cfg, "test-secret-key-for-mfa-hardening-32b", None)
	return db, udb_mod, enc


def test_temp_store() -> None:
	print("TestMfaTempStore")
	mod = _load("mfa_temp_store", "mfa_temp_store.py")
	mod.clear_mfa_temp_store()
	tok = mod.create_mfa_temp_token("alice", purpose="mfa_verify", ip="1.2.3.4", user_agent="UA")
	_ok(
		mod.peek_mfa_temp_token(tok, purpose="mfa_setup", ip="1.2.3.4", user_agent="UA") is None,
		"wrong purpose rejected",
	)
	_ok(
		mod.peek_mfa_temp_token(tok, purpose="mfa_verify", ip="1.2.3.4", user_agent="UA") is not None,
		"correct purpose ok",
	)
	_ok(
		mod.consume_mfa_temp_token(tok, purpose="mfa_verify", ip="1.2.3.4", user_agent="UA")
		== "alice",
		"consume on success",
	)
	tok2 = mod.create_mfa_temp_token("bob", purpose="mfa_verify", ip="10.0.0.1", user_agent="Chrome")
	_ok(mod.record_mfa_temp_failure(tok2), "fail 1")
	_ok(mod.record_mfa_temp_failure(tok2), "fail 2")
	_ok(not mod.record_mfa_temp_failure(tok2), "fail 3 invalidates")
	_ok(mod.peek_mfa_temp_token(tok2) is None, "token gone after cap")


def test_setup_takeover() -> None:
	print("TestMfaSetupTakeover")
	import pyotp

	with tempfile.TemporaryDirectory() as td:
		db, _udb, enc = _make_users_db(td)
		db.add_user("victim-id", "victim", "Password123!", False, groups=["user"])
		r1 = db.mfa_setup_start("victim")
		_ok(r1 is not None, "initial setup ok")
		_uid, user = db.get_user("victim")
		secret = enc.decrypt_value(db._secret_key, user["totp_secret_encrypted"])
		code = pyotp.TOTP(secret).now()
		_ok(db.mfa_verify_setup("victim", code), "verify-setup enables")
		_ok(db.get_mfa_enabled("victim"), "mfa enabled")
		_ok(db.mfa_setup_start("victim") is None, "setup blocked when enabled (takeover)")


def test_totp_replay() -> None:
	print("TestTotpReplay")
	import pyotp

	with tempfile.TemporaryDirectory() as td:
		db, _udb, enc = _make_users_db(td)
		db.add_user("replay-id", "replay", "Password123!", False, groups=["user"])
		db.mfa_setup_start("replay")
		_uid, user = db.get_user("replay")
		secret = enc.decrypt_value(db._secret_key, user["totp_secret_encrypted"])
		code = pyotp.TOTP(secret).now()
		_ok(db.mfa_verify_setup("replay", code), "setup verify")
		_ok(not db.verify_totp("replay", code), "same code replay rejected")


def test_recovery_codes() -> None:
	print("TestRecoveryCodes")
	import pyotp

	with tempfile.TemporaryDirectory() as td:
		db, _udb, enc = _make_users_db(td)
		db.add_user("recov-id", "recov", "Password123!", False, groups=["user"])
		uri_codes = db.mfa_setup_start("recov")
		_ok(uri_codes is not None, "setup returns codes")
		_uri, codes = uri_codes
		_ok(len(codes) == 10, "ten recovery codes")
		_uid, user = db.get_user("recov")
		secret = enc.decrypt_value(db._secret_key, user["totp_secret_encrypted"])
		# may need fresh code if previous tests burned window — use now again
		code = pyotp.TOTP(secret).now()
		# if verify_setup fails due to timestep from none, ok
		ok_setup = db.mfa_verify_setup("recov", code)
		if not ok_setup:
			time.sleep(1)
			ok_setup = db.mfa_verify_setup("recov", pyotp.TOTP(secret).now())
		_ok(ok_setup, "enable mfa")
		old = codes[0]
		_ok(db.verify_backup_code_and_consume("recov", old), "consume first recovery code")
		new_codes = db.regenerate_recovery_codes("recov")
		_ok(new_codes is not None and len(new_codes) == 10, "regenerate")
		_ok(not db.verify_backup_code_and_consume("recov", old), "old set dead")
		_ok(db.verify_backup_code_and_consume("recov", new_codes[0]), "new set works")


def test_token_version() -> None:
	print("TestTokenVersion")
	with tempfile.TemporaryDirectory() as td:
		db, _udb, _enc = _make_users_db(td)
		db.add_user("tv-id", "tv", "Password123!", False, groups=["user"])
		jwt_mod = _load("jwt_auth", "jwt_auth.py")
		# stub api token + session stores lightly
		sys.modules.setdefault(
			"utils.api_token_store",
			types.SimpleNamespace(
				get_api_token_store=lambda *a, **k: types.SimpleNamespace(
					get_user_for_token=lambda t: None
				)
			),
		)
		sys.modules.setdefault(
			"utils.session_token_store",
			types.SimpleNamespace(
				get_session_token_store=lambda *a, **k: types.SimpleNamespace(
					is_revoked=lambda j: False,
					update_last_used=lambda j: None,
					revoke_idle_sessions=lambda: 0,
					prune_old_sessions=lambda: None,
					revoke_all_for_username=lambda u: 0,
				)
			),
		)
		# Reload jwt_auth after stubs? already loaded — patch module attrs
		auth = jwt_mod.JWTAuth("jwt-secret-key-for-tests-32bytes!!", db, expire_minutes=60)
		uid, _ = db.get_user("tv")
		tok = auth.create_access_token({"id": uid, "username": "tv", "token_version": 0})
		# Patch resolve path stores
		with _patch_jwt_deps(jwt_mod):
			got = auth.resolve_token_identity(tok)
			_ok(got[1] == "tv", "token valid at version 0")
			db.bump_token_version("tv")
			got2 = auth.resolve_token_identity(tok)
			_ok(got2 == (None, None), "token_version bump revokes")


class _Ctx:
	def __enter__(self):
		return self

	def __exit__(self, *a):
		return False


def _patch_jwt_deps(jwt_mod):
	# Ensure get_api_token_store / get_session_token_store resolve
	import utils.jwt_auth as ja  # may fail

	return _Ctx()


def test_token_version_simple() -> None:
	"""token_version without full jwt_auth resolve (avoids store wiring)."""
	print("TestTokenVersionBump")
	with tempfile.TemporaryDirectory() as td:
		db, _udb, _enc = _make_users_db(td)
		db.add_user("tv2-id", "tv2", "Password123!", False, groups=["user"])
		_ok(int(db.get_user("tv2")[1].get("token_version") or 0) == 0, "starts at 0")
		v = db.bump_token_version("tv2")
		_ok(v == 1, "bumped to 1")
		_ok(int(db.get_user("tv2")[1].get("token_version") or 0) == 1, "persisted")


def test_login_finalize_mfa_disabled() -> None:
	print("TestMfaDisabledCarveOut")
	if _PROJECT_ROOT not in sys.path:
		sys.path.insert(0, _PROJECT_ROOT)
	# Minimal constants stub for absolute import fallback
	c = types.ModuleType("constants")
	c.MFA_DISABLED = True
	c.MFA_DISABLED_FORCE_ALL = False

	def _exp_mfa():
		return True

	c.experimental_mfa_enabled = _exp_mfa
	sys.modules["constants"] = c
	lf = _load("login_finalize", "login_finalize.py")
	with tempfile.TemporaryDirectory() as td:
		db, _udb, _enc = _make_users_db(td)
		db.add_user("normie-id", "normie", "Password123!", False, groups=["user"])
		db.add_user("boss-id", "boss", "Password123!", True, groups=["admin", "owner"])
		_ok(lf.mfa_disabled_applies_to_user(db.get_user("normie")[1]), "disabled applies to user")
		_ok(
			not lf.mfa_disabled_applies_to_user(db.get_user("boss")[1]),
			"disabled does NOT apply to admin/owner",
		)


def test_multi_group() -> None:
	print("TestMultiGroupMfaRequired")
	lf = _load("login_finalize", "login_finalize.py")
	with tempfile.TemporaryDirectory() as td:
		db, _udb, _enc = _make_users_db(td)
		db.add_user("multi-id", "multi", "Password123!", False, groups=["user", "artist"])
		cfg = {"user": {"mfa_required": False}, "artist": {"mfa_required": True}}
		_ok(lf.any_group_requires_mfa("multi", db, cfg), "any group mfa_required")


def test_route_policy() -> None:
	print("TestRoutePolicyMfa")
	# Stub aiohttp for route_policy
	if "aiohttp" not in sys.modules:
		aio = types.ModuleType("aiohttp")
		aio.web = types.SimpleNamespace()
		sys.modules["aiohttp"] = aio
	rp = _load("route_policy", "route_policy.py")
	_ok(rp.lookup_policy("POST", "/mss-login/api/mfa/setup") == rp.Policy.PUBLIC, "setup PUBLIC")
	_ok(
		rp.lookup_policy("POST", "/mss-login/api/mfa/reset-user") == rp.Policy.OWNER,
		"reset-user OWNER",
	)


def test_no_plaintext_cache() -> None:
	print("TestNoPlaintextTotpCache")
	enc = _load("encryption", "encryption.py")
	# Load only _row_to_user via users_db after stubs
	with tempfile.TemporaryDirectory() as td:
		db, udb_mod, _ = _make_users_db(td)
		ct = enc.encrypt_value("test-secret-key-for-mfa-hardening-32b", "JBSWY3DPEHPK3PXP")
		row = {
			"username": "x",
			"password_hash": "h",
			"admin": 0,
			"groups": '["user"]',
			"mfa_enabled": 1,
			"totp_secret_encrypted": ct,
			"backup_code_hash": "",
			"backup_code_used": 0,
			"token_version": 0,
		}
		user = udb_mod._row_to_user(row, "test-secret-key-for-mfa-hardening-32b")
		_ok("_totp_secret_plain" not in user, "no plaintext TOTP cache on load")
		_ok(user.get("totp_secret_encrypted") == ct, "ciphertext retained")


def main() -> int:
	test_temp_store()
	test_setup_takeover()
	test_totp_replay()
	test_recovery_codes()
	test_token_version_simple()
	test_login_finalize_mfa_disabled()
	test_multi_group()
	test_route_policy()
	test_no_plaintext_cache()
	print("---")
	if failed:
		print(f"FAILED ({len(failed)}):")
		for f in failed:
			print(f"  - {f}")
		return 1
	print("All MFA hardening tests passed.")
	return 0


if __name__ == "__main__":
	raise SystemExit(main())
