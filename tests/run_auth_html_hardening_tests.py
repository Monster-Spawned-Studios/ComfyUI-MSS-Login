r"""
Standalone tests for auth HTML hardening (guest omit + debug gate).

  .venv/bin/python tests/run_auth_html_hardening_tests.py
"""

import asyncio
import importlib.util
import os
import re
import sys
import types

_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_ROUTES_DIR = os.path.join(_PROJECT_ROOT, "routes")
_UTILS_DIR = os.path.join(_PROJECT_ROOT, "utils")
_HTML_LOGIN = os.path.join(_PROJECT_ROOT, "web", "html", "login.html")


def _load_module(name: str, path: str, package: str):
	spec = importlib.util.spec_from_file_location(name, path)
	mod = importlib.util.module_from_spec(spec)
	mod.__package__ = package
	assert spec and spec.loader
	sys.modules[name] = mod
	spec.loader.exec_module(mod)
	return mod


def _ensure_pkg(name: str, path: str):
	if name in sys.modules:
		return sys.modules[name]
	pkg = types.ModuleType(name)
	pkg.__path__ = [path]
	sys.modules[name] = pkg
	return pkg


class _FakeResponse:
	def __init__(self, *, status=200, text="", body=b"", content_type="application/json"):
		self.status = status
		self.text = text
		self.body = body if isinstance(body, (bytes, bytearray)) else str(body).encode("utf-8")
		self.content_type = content_type
		self.headers = {}


def _install_debug_stubs(*, username=None, is_admin=False):
	_ensure_pkg("mss_login", _PROJECT_ROOT)
	_ensure_pkg("mss_login.routes", _ROUTES_DIR)
	_ensure_pkg("mss_login.utils", _UTILS_DIR)

	aiohttp = types.ModuleType("aiohttp")

	class web:
		Request = object

		@staticmethod
		def json_response(data, status=200):
			import json

			return _FakeResponse(status=status, text=json.dumps(data))

		@staticmethod
		def Response(*, body=b"", content_type="text/plain", headers=None):
			resp = _FakeResponse(status=200, body=body, content_type=content_type)
			if headers:
				resp.headers.update(headers)
			return resp

	aiohttp.web = web
	sys.modules["aiohttp"] = aiohttp

	constants = types.ModuleType("mss_login.constants")
	constants.DEBUG_LOG_PATH = os.path.join(_PROJECT_ROOT, "tests", "_tmp_no_debug.log")
	constants.DEBUG_MODE = True
	constants.DEBUG_MODE_FROM_ENV = False
	constants.filter_debug_messages_enabled = lambda: False
	sys.modules["mss_login.constants"] = constants

	class _Jwt:
		def get_token_from_request(self, _req):
			return "tok" if username else None

		def decode_access_token(self, _tok):
			if not username:
				raise ValueError("no user")
			return {"username": username}

	class _UsersDb:
		def get_user(self, uname):
			if uname != username:
				return None, None
			groups = ["admin", "owner"] if is_admin else ["user"]
			return "uid", {"groups": groups, "admin": is_admin}

	globals_mod = types.ModuleType("mss_login.globals")
	globals_mod.jwt_auth = _Jwt()
	globals_mod.users_db = _UsersDb()
	globals_mod.routes = types.SimpleNamespace(get=lambda *_a, **_k: lambda fn: fn)
	sys.modules["mss_login.globals"] = globals_mod

	redactor = types.ModuleType("mss_login.utils.log_redactor")
	redactor.redact_log_text = lambda text: text
	sys.modules["mss_login.utils.log_redactor"] = redactor

	api_store_mod = types.ModuleType("mss_login.utils.api_token_store")

	class _ApiStore:
		def get_user_for_token(self, _tok):
			return None

	api_store_mod.get_api_token_store = lambda _cfg: _ApiStore()
	sys.modules["mss_login.utils.api_token_store"] = api_store_mod


def _load_auth_guest_helpers():
	"""Load _inject_login_guest_block from routes/auth.py without importing the full module."""
	auth_path = os.path.join(_ROUTES_DIR, "auth.py")
	with open(auth_path, "r", encoding="utf-8") as f:
		src = f.read()
	# Extract the guest-block constant + helper (pure string transforms).
	start = src.find("_GUEST_LOGIN_BLOCK_ENABLED")
	end = src.find("\ndef _apply_login_cookie", start)
	ok_chunk = start >= 0 and end > start
	if not ok_chunk:
		raise RuntimeError("Could not locate guest inject helpers in routes/auth.py")
	chunk = src[start:end]
	ns = {"re": re}
	exec(chunk, ns)  # noqa: S102 — controlled extract from our own source
	return ns["_inject_login_guest_block"]


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

	print("TestLoginHtmlGuestPlaceholder")
	with open(_HTML_LOGIN, "r", encoding="utf-8") as f:
		raw = f.read()
	ok("{{GUEST_LOGIN_BLOCK}}" in raw, "login.html has GUEST_LOGIN_BLOCK placeholder")
	ok("guest-login-btn" not in raw, "guest button not hard-coded in source HTML")
	ok("guest_login_flag" not in raw, "guest_login flag not hard-coded in source HTML")

	inject = _load_auth_guest_helpers()

	print("TestGuestOmitWhenDisabled")
	disabled = inject(raw, allow_guest=False)
	ok("guest-login-btn" not in disabled, "guest button omitted when disabled")
	ok("guest_login_flag" not in disabled, "guest flag omitted when disabled")
	ok("{{GUEST_LOGIN_BLOCK}}" not in disabled, "placeholder replaced when disabled")

	print("TestGuestInjectWhenEnabled")
	enabled = inject(raw, allow_guest=True)
	ok('id="guest-login-btn"' in enabled, "guest button present when enabled")
	ok('id="guest_login_flag"' in enabled, "guest flag present when enabled")
	ok("{{GUEST_LOGIN_BLOCK}}" not in enabled, "placeholder replaced when enabled")

	print("TestLegacyStripWhenDisabled")
	legacy = (
		'<button type="button" id="guest-login-btn">Guest Login</button>'
		'<input type="hidden" id="guest_login_flag" name="guest_login" value="false">'
	)
	stripped = inject(legacy, allow_guest=False)
	ok("guest-login-btn" not in stripped, "legacy guest button stripped")
	ok("guest_login_flag" not in stripped, "legacy guest flag stripped")

	print("TestAuthRouteHelperPresent")
	auth_path = os.path.join(_ROUTES_DIR, "auth.py")
	with open(auth_path, "r", encoding="utf-8") as f:
		auth_src = f.read()
	ok("_inject_login_guest_block" in auth_src, "auth.py defines guest inject helper")
	ok("GUEST_LOGIN_BLOCK" in auth_src, "auth.py references GUEST_LOGIN_BLOCK")
	ok("ALLOW_GUEST_JWT" in auth_src, "get_login uses ALLOW_GUEST_JWT")

	print("TestDebugModeUnauthenticated")
	_install_debug_stubs(username=None)
	# Force reload
	sys.modules.pop("mss_login.routes.debug", None)
	debug_mod = _load_module(
		"mss_login.routes.debug", os.path.join(_ROUTES_DIR, "debug.py"), "mss_login.routes"
	)
	resp = asyncio.run(debug_mod.get_debug_mode(object()))
	ok(resp.status == 200, "debug-mode returns 200 when unauthenticated")
	normalized = resp.text.replace(" ", "").lower()
	ok('"debugmode":false' in normalized, "debug-mode hides real state when unauthenticated")

	print("TestDebugLogExportAuth")
	resp = asyncio.run(debug_mod.export_redacted_debug_log(object()))
	ok(resp.status == 401, "debug-log export requires authentication")

	_install_debug_stubs(username="alice", is_admin=False)
	sys.modules.pop("mss_login.routes.debug", None)
	debug_mod = _load_module(
		"mss_login.routes.debug", os.path.join(_ROUTES_DIR, "debug.py"), "mss_login.routes"
	)
	resp = asyncio.run(debug_mod.export_redacted_debug_log(object()))
	ok(resp.status == 403, "debug-log export denies non-admin")

	_install_debug_stubs(username="admin", is_admin=True)
	sys.modules.pop("mss_login.routes.debug", None)
	debug_mod = _load_module(
		"mss_login.routes.debug", os.path.join(_ROUTES_DIR, "debug.py"), "mss_login.routes"
	)
	resp = asyncio.run(debug_mod.export_redacted_debug_log(object()))
	ok(resp.status == 200, "debug-log export allows admin")

	print()
	if failed:
		print(f"Result: {failed} failed, {run - failed} passed, {run} total")
		sys.exit(1)
	print(f"Result: all {run} tests passed")
	sys.exit(0)


if __name__ == "__main__":
	run_tests()
