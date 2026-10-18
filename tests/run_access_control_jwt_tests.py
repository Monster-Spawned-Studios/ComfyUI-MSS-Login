r"""
Standalone tests for verified JWT role/permission resolution (AUDIT-004).
No ComfyUI required.

  .venv/bin/python tests/run_access_control_jwt_tests.py
"""

from __future__ import annotations

import importlib.util
import json
import os
import sys
import tempfile
import types

_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_UTILS = os.path.join(_PROJECT_ROOT, "utils")


def _ok(cond: bool, msg: str, failed: list[str]) -> None:
	if cond:
		print(f"  ok: {msg}")
	else:
		print(f"  FAIL: {msg}")
		failed.append(msg)


def _install_stubs(secret: str = "test-secret-audit-004-not-for-prod") -> None:
	root = types.ModuleType("mss_login")
	root.__path__ = [_PROJECT_ROOT]
	utils = types.ModuleType("mss_login.utils")
	utils.__path__ = [_UTILS]
	sys.modules["mss_login"] = root
	sys.modules["mss_login.utils"] = utils

	const = types.ModuleType("mss_login.constants")
	const.SECRET_KEY = secret
	const.TOKEN_ALGORITHM = "HS256"
	sys.modules["mss_login.constants"] = const

	for name in ("folder_paths", "execution", "server"):
		if name not in sys.modules:
			sys.modules[name] = types.ModuleType(name)
	sys.modules["folder_paths"].get_output_directory = lambda: "/tmp"
	sys.modules["folder_paths"].get_temp_directory = lambda: "/tmp"
	sys.modules["folder_paths"].get_input_directory = lambda: "/tmp"
	sys.modules["execution"].MAXIMUM_HISTORY_SIZE = 10
	sys.modules["execution"].PromptQueue = object
	sys.modules["server"].PromptServer = object

	aiohttp = types.ModuleType("aiohttp")
	aiohttp.web = types.SimpleNamespace(
		middleware=lambda fn: fn,
		json_response=lambda *a, **k: None,
		Response=lambda *a, **k: None,
		Request=object,
	)
	sys.modules["aiohttp"] = aiohttp

	api_tok = types.ModuleType("mss_login.utils.api_token_store")
	api_tok.get_api_token_store = lambda *_a, **_k: types.SimpleNamespace(
		get_user_for_token=lambda *_: None
	)
	sys.modules["mss_login.utils.api_token_store"] = api_tok
	sys.modules["utils.api_token_store"] = api_tok

	data_dir = types.ModuleType("mss_login.utils.data_dir")
	data_dir.get_data_subdir = lambda *_a, **_k: "/tmp"
	sys.modules["mss_login.utils.data_dir"] = data_dir
	sys.modules["utils.data_dir"] = data_dir

	debug = types.ModuleType("mss_login.utils.debug_log")
	debug.debug_write = lambda *_a, **_k: None
	sys.modules["mss_login.utils.debug_log"] = debug
	sys.modules["utils.debug_log"] = debug

	iso = types.ModuleType("mss_login.utils.user_isolation")
	iso.safe_user_dir_segment = lambda x: str(x or "x")
	iso.user_id_from_queue_item = lambda e: None
	iso.username_from_queue_item = lambda e: None
	sys.modules["mss_login.utils.user_isolation"] = iso
	sys.modules["utils.user_isolation"] = iso

	users_db_mod = types.ModuleType("mss_login.utils.users_db")
	users_db_mod.UsersDB = object
	sys.modules["mss_login.utils.users_db"] = users_db_mod
	sys.modules["utils.users_db"] = users_db_mod


def _load_access_control(secret: str = "test-secret-audit-004-not-for-prod"):
	_install_stubs(secret)
	sys.modules.pop("mss_login.utils.access_control", None)
	sys.modules.pop("utils.access_control", None)
	path = os.path.join(_UTILS, "access_control.py")
	spec = importlib.util.spec_from_file_location("mss_login.utils.access_control", path)
	mod = importlib.util.module_from_spec(spec)
	sys.modules["mss_login.utils.access_control"] = mod
	sys.modules["utils.access_control"] = mod
	spec.loader.exec_module(mod)
	return mod


class _FakeRequest:
	def __init__(self, *, headers=None, cookies=None, query=None):
		self.path = "/api/x"
		self.method = "GET"
		self.headers = headers or {}
		self.cookies = cookies or {}
		self.rel_url = types.SimpleNamespace(query=query or {})


def _make_ctrl(ac_mod):
	tmpdir = tempfile.mkdtemp()
	groups_path = os.path.join(tmpdir, "groups.json")
	with open(groups_path, "w", encoding="utf-8") as f:
		json.dump(
			{"user": {"can_run": True, "can_upload": True, "can_access_api": True}},
			f,
		)
	ctrl = ac_mod.AccessControl.__new__(ac_mod.AccessControl)
	ctrl.users_db = types.SimpleNamespace(
		get_user=lambda username=None, **_k: (
			("uid", {"username": "alice", "groups": ["user"]})
			if username == "alice"
			else (None, None)
		)
	)
	ctrl.groups_config_file = groups_path
	ctrl.api_token_store_config = {}
	ctrl.server = types.SimpleNamespace()
	return ctrl


def test_verified_jwt_role_resolution(failed: list[str]) -> None:
	print("TestVerifiedJwtRoleResolution")
	try:
		import jwt
	except ImportError:
		print("  SKIP: PyJWT not installed")
		return

	secret = "test-secret-audit-004-not-for-prod"
	ac = _load_access_control(secret)
	ctrl = _make_ctrl(ac)

	good = jwt.encode({"username": "alice", "id": "uid"}, secret, algorithm="HS256")
	role, perms, user = ctrl._get_user_role_and_permissions(
		_FakeRequest(headers={"Authorization": f"Bearer {good}"})
	)
	_ok(user == "alice" and role == "user", "verified JWT resolves username/role", failed)
	_ok(perms.get("can_run") is True, "verified JWT loads group perms", failed)

	# Cookie path
	role_c, _, user_c = ctrl._get_user_role_and_permissions(
		_FakeRequest(cookies={"jwt_token": good})
	)
	_ok(user_c == "alice" and role_c == "user", "verified JWT from cookie works", failed)


def test_forged_jwt_rejected(failed: list[str]) -> None:
	print("TestForgedJwtRejected")
	try:
		import jwt
	except ImportError:
		print("  SKIP: PyJWT not installed")
		return

	secret = "test-secret-audit-004-not-for-prod"
	ac = _load_access_control(secret)
	ctrl = _make_ctrl(ac)

	# Wrong signing key — would succeed with verify_signature=False (AUDIT-004 repro)
	forged = jwt.encode({"username": "alice", "id": "uid"}, "attacker-key", algorithm="HS256")
	role, perms, user = ctrl._get_user_role_and_permissions(
		_FakeRequest(headers={"Authorization": f"Bearer {forged}"})
	)
	_ok(user is None and role == "guest", "forged JWT treated as guest", failed)
	_ok(perms == {}, "forged JWT gets empty perms", failed)

	# Unverified decode helper returns None for bad sig
	claims = ac.AccessControl._decode_verified_jwt_claims(forged)
	_ok(claims is None, "_decode_verified_jwt_claims rejects bad signature", failed)

	junk = ctrl._get_user_role_and_permissions(
		_FakeRequest(headers={"Authorization": "Bearer not.a.jwt"})
	)
	_ok(junk[2] is None and junk[0] == "guest", "malformed JWT treated as guest", failed)

	# Empty / missing token
	none_user = ctrl._get_user_role_and_permissions(_FakeRequest())
	_ok(none_user[2] is None and none_user[0] == "guest", "missing token is guest", failed)


def main() -> int:
	failed: list[str] = []
	try:
		test_verified_jwt_role_resolution(failed)
		test_forged_jwt_rejected(failed)
	except Exception as e:
		print(f"FAIL: {e}")
		import traceback

		traceback.print_exc()
		return 1
	print()
	if failed:
		print(f"Failed ({len(failed)}): {failed}")
		return 1
	print("All access-control JWT verification tests passed.")
	return 0


if __name__ == "__main__":
	sys.exit(main())
