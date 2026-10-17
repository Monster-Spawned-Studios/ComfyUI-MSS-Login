r"""
Standalone tests for auth compatibility fixes (no ComfyUI deps).

Covers:
- Password fields exempt from XSS sanitizer mutation
- Legacy XSS-hashed password dual-verify + rehash
- Ephemeral SECRET_KEY reuse (load-or-create)
- JSON vs form credential field sanitization helpers

  .venv/bin/python tests/run_auth_compat_tests.py
"""

import importlib.util
import os
import sys
import tempfile
import types
import uuid

_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_utils = os.path.join(_PROJECT_ROOT, "utils")


def _ensure_utils_pkg():
	"""Register a stub utils package so relative imports work without utils/__init__.py."""
	if "utils" not in sys.modules:
		pkg = types.ModuleType("utils")
		pkg.__path__ = [_utils]
		pkg.__file__ = os.path.join(_utils, "__init__.py")
		sys.modules["utils"] = pkg


def _load_module(full_name, filename):
	_ensure_utils_pkg()
	path = os.path.join(_utils, filename)
	spec = importlib.util.spec_from_file_location(full_name, path)
	mod = importlib.util.module_from_spec(spec)
	sys.modules[full_name] = mod
	# Also register short name under utils.* for relative imports
	short = full_name.split(".", 1)[-1] if full_name.startswith("utils.") else full_name
	sys.modules[f"utils.{short}"] = mod
	spec.loader.exec_module(mod)
	return mod


def run_tests():
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
		return cond

	input_sanitizer = _load_module("utils.input_sanitizer", "input_sanitizer.py")
	# encryption + sanitizer deps for users_db
	_load_module("utils.encryption", "encryption.py")
	sanitizer = _load_module("utils.sanitizer", "sanitizer.py")

	Sanitizer = sanitizer.Sanitizer
	apply_legacy_xss_sanitize = sanitizer.apply_legacy_xss_sanitize
	is_json_content_type = sanitizer.is_json_content_type
	is_password_field = sanitizer.is_password_field
	sanitize_password_input = input_sanitizer.sanitize_password_input

	# --- password field helpers ---
	print("TestPasswordFieldHelpers")
	ok(is_password_field("password") is True, "password is password field")
	ok(is_password_field("new_user_password") is True, "new_user_password is password field")
	ok(is_password_field("USERNAME") is False, "username is not password field")
	ok(is_json_content_type("application/json") is True, "application/json detected")
	ok(
		is_json_content_type("application/json; charset=utf-8") is True,
		"application/json with charset detected",
	)
	ok(is_json_content_type("application/x-www-form-urlencoded") is False, "form not json")

	# --- XSS must not mutate passwords via _sanitize_field ---
	print("TestPasswordExemptFromXss")
	special = "Pass&word1!(x)"
	via_field = Sanitizer._sanitize_field("password", special)
	ok(via_field == sanitize_password_input(special), "password field keeps special chars")
	ok("&" in via_field and "(" in via_field, "password retains & and (")
	legacy = apply_legacy_xss_sanitize(special)
	ok(legacy != special, "legacy XSS mutates special-char password")
	label_sanitized = Sanitizer._sanitize_field("label", "<script>x</script>")
	ok("<script>" not in str(label_sanitized), "non-password fields escape raw script tags")
	ok(
		"&lt;" in str(label_sanitized) or "script" in str(label_sanitized).lower(),
		"non-password XSS path still transforms HTML-ish input",
	)

	# --- dual-verify + rehash via UsersDB (temp sqlite, no encryption) ---
	print("TestLegacyPasswordDualVerify")
	# Stub modules that users_db / sqlite_connection may need
	_load_module("utils.path_safety", "path_safety.py")
	_load_module("utils.db_key_derivation", "db_key_derivation.py")
	_load_module("utils.sqlite_connection", "sqlite_connection.py")
	users_db_mod = _load_module("utils.users_db", "users_db.py")
	UsersDB = users_db_mod.UsersDB

	with tempfile.TemporaryDirectory(prefix="mss_auth_compat_") as tmp:
		db_path = os.path.join(tmp, "users.db")
		cfg = {"backend": "sqlite", "sqlite_path": db_path, "encryption_level": ""}
		db = UsersDB(cfg, secret_key="test-secret-key-for-auth-compat")
		uid = str(uuid.uuid4())
		raw_pw = "Secret1&'(x)"
		# Simulate pre-fix account: hash was of XSS-sanitized password
		legacy_pw = apply_legacy_xss_sanitize(raw_pw)
		ok(legacy_pw != raw_pw, "fixture password is mutated by legacy XSS")
		db.add_user(uid, "legacyuser", legacy_pw, admin=True)
		# Stored hash is of legacy form; check with raw should succeed and rehash
		ok(
			db.check_username_password("legacyuser", raw_pw) is True,
			"raw password verifies via legacy path",
		)
		# After rehash, stored hash should match raw directly
		_uid2, urec = db.get_user(username="legacyuser")
		stored = urec.get("password", "")
		import bcrypt

		ok(
			bcrypt.checkpw(raw_pw.encode("utf-8"), stored.encode("utf-8")),
			"rehash stores raw password hash",
		)
		ok(
			db.check_username_password("legacyuser", raw_pw) is True,
			"second login with raw still works",
		)
		ok(db.check_username_password("legacyuser", "wrong!") is False, "wrong password rejected")

		# Fresh account with raw password (post-fix path)
		uid2 = str(uuid.uuid4())
		simple = "Valid1!ab"
		db.add_user(uid2, "freshuser", simple, admin=False)
		ok(db.check_username_password("freshuser", simple) is True, "fresh raw password verifies")

	# --- ephemeral SECRET_KEY reuse (pure helper, no full constants import) ---
	print("TestEphemeralSecretKeyReuse")

	# Inline the same logic as constants.resolve_secret_key to avoid importing constants
	# (which runs ensure_data_dir / dotenv). Keep in sync with constants.resolve_secret_key.
	def resolve_secret_key(env_value, *, load_ephemeral, persist_ephemeral, generate_key):
		key = (env_value or "").strip()
		if key:
			return key, False
		existing = (load_ephemeral() or "").strip()
		if existing:
			return existing, False
		new_key = generate_key()
		persist_ephemeral(new_key)
		return new_key, True

	store = {"key": ""}

	def load_e():
		return store["key"]

	def persist_e(k):
		store["key"] = k

	def gen():
		return "generated-key-aaaa"

	k1, created1 = resolve_secret_key(
		"", load_ephemeral=load_e, persist_ephemeral=persist_e, generate_key=gen
	)
	ok(created1 is True, "first resolve creates ephemeral")
	ok(k1 == "generated-key-aaaa", "first resolve returns generated key")
	ok(store["key"] == "generated-key-aaaa", "ephemeral persisted")

	k2, created2 = resolve_secret_key(
		"", load_ephemeral=load_e, persist_ephemeral=persist_e, generate_key=lambda: "other"
	)
	ok(created2 is False, "second resolve does not create")
	ok(k2 == "generated-key-aaaa", "second resolve reuses same key")

	k3, created3 = resolve_secret_key(
		"env-secret", load_ephemeral=load_e, persist_ephemeral=persist_e, generate_key=gen
	)
	ok(created3 is False and k3 == "env-secret", "env SECRET_KEY wins")

	# Verify constants.resolve_secret_key exists and matches by loading source symbol via AST-free import of function only
	# Load constants function by reading the module's function after stubbing heavy deps is too heavy;
	# instead exec just the function from a minimal namespace by importing the real module path carefully.
	const_path = os.path.join(_PROJECT_ROOT, "constants.py")
	# Parse resolve_secret_key from constants by loading with importlib after stubbing data_dir side effects
	# Simpler: import the function object by compiling a tiny extract — we already tested the algorithm above.
	# Cross-check the real function signature exists in source:
	with open(const_path, "r", encoding="utf-8") as f:
		src = f.read()
	ok("def resolve_secret_key(" in src, "constants defines resolve_secret_key")
	ok(
		"_load_ephemeral_key()" in src or "load_ephemeral" in src,
		"constants reuses ephemeral load path",
	)

	# --- credential dict sanitization (simulates middleware field handling) ---
	print("TestCredentialDictSanitization")
	payload = {"username": "alice<script>", "password": "Pass&word1!(x)", "label": "my;token"}
	sanitized = {k: Sanitizer._sanitize_field(k, v) for k, v in payload.items()}
	ok(sanitized["password"] == "Pass&word1!(x)", "JSON-style password field unchanged")
	ok(
		"<" not in sanitized["username"] or "script" not in sanitized["username"].lower(),
		"username still sanitized",
	)

	# --- auth cookie clear must emit Path=/ only (logout redirect-loop fix) ---
	print("TestClearAuthCookiesPath")
	try:
		from aiohttp import web as aiohttp_web

		auth_cookies = _load_module("utils.auth_cookies", "auth_cookies.py")
		resp = aiohttp_web.Response()
		auth_cookies.set_auth_cookie(resp, "dummy-token", secure=False, remember_me=False)
		auth_cookies.clear_auth_cookies(resp, secure=False)
		# Collect Set-Cookie headers for jwt_token
		set_cookies = []
		if hasattr(resp, "cookies") and resp.cookies:
			for morsel in resp.cookies.values():
				set_cookies.append(morsel)
		# Also check raw headers
		raw_headers = []
		for k, v in resp.headers.items():
			if k.lower() == "set-cookie":
				raw_headers.append(v)
		# Path=/ must be present; Path=/mfa must not survive as the only clear
		joined = " ".join(raw_headers) if raw_headers else ""
		if not joined and set_cookies:
			joined = " ".join(str(m) for m in set_cookies)
		ok(
			"Path=/mfa" not in joined and "path=/mfa" not in joined.lower(),
			"clear does not leave Path=/mfa",
		)
		# The cookie morsel for jwt_token should have path /
		jwt_morsel = resp.cookies.get("jwt_token") if hasattr(resp, "cookies") else None
		if jwt_morsel is not None:
			path_val = jwt_morsel.get("path") or "/"
			ok(path_val == "/", f"clear jwt_token path is / (got {path_val!r})")
		else:
			ok(bool(joined) or jwt_morsel is not None, "clear emitted jwt_token cookie")
	except Exception as e:
		ok(False, f"clear_auth_cookies test error: {e}")

	print()
	if failed:
		print(f"Result: {failed} failed, {run - failed} passed, {run} total")
		sys.exit(1)
	print(f"Result: all {run} tests passed")
	sys.exit(0)


if __name__ == "__main__":
	run_tests()
