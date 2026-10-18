r"""
Standalone tests for access-control sensitive permission default-deny (AUDIT-003).
No ComfyUI required.

  .venv/bin/python tests/run_access_control_perm_tests.py
"""

from __future__ import annotations

import asyncio
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


def _install_stubs() -> None:
	root = types.ModuleType("mss_login")
	root.__path__ = [_PROJECT_ROOT]
	utils = types.ModuleType("mss_login.utils")
	utils.__path__ = [_UTILS]
	sys.modules["mss_login"] = root
	sys.modules["mss_login.utils"] = utils

	for name in ("folder_paths", "execution", "server"):
		if name not in sys.modules:
			sys.modules[name] = types.ModuleType(name)
	sys.modules["folder_paths"].get_output_directory = lambda: "/tmp"
	sys.modules["folder_paths"].get_temp_directory = lambda: "/tmp"
	sys.modules["folder_paths"].get_input_directory = lambda: "/tmp"
	sys.modules["execution"].MAXIMUM_HISTORY_SIZE = 10
	sys.modules["execution"].PromptQueue = object
	sys.modules["server"].PromptServer = object

	class _Resp:
		def __init__(self, *, status=200, body=None, text=None):
			self.status = status
			self.body = body
			self.text = text

	def json_response(data=None, status=200, **_k):
		return _Resp(status=status, body=data)

	def Response(*, status=200, text=""):
		return _Resp(status=status, text=text)

	aiohttp = types.ModuleType("aiohttp")
	aiohttp.web = types.SimpleNamespace(
		middleware=lambda fn: fn,
		json_response=json_response,
		Response=Response,
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

	cpe = types.ModuleType("mss_login.utils.cpe_workflows")
	cpe.cpe_path_requires_modify_workflows = lambda _p, _m: False
	sys.modules["mss_login.utils.cpe_workflows"] = cpe
	sys.modules["utils.cpe_workflows"] = cpe


def _load_access_control():
	_install_stubs()
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
	def __init__(self, path: str, method: str = "GET"):
		self.path = path
		self.method = method
		self.cookies = {}
		self.headers = {}
		self.rel_url = types.SimpleNamespace(query={})


def _make_ctrl(ac_mod, groups: dict, role: str = "user"):
	tmpdir = tempfile.mkdtemp()
	groups_path = os.path.join(tmpdir, "groups.json")
	with open(groups_path, "w", encoding="utf-8") as f:
		json.dump(groups, f)

	ctrl = ac_mod.AccessControl.__new__(ac_mod.AccessControl)
	ctrl.users_db = types.SimpleNamespace(
		get_user=lambda username=None, user_id=None, **_k: (
			("uid", {"username": username or "alice", "groups": [role]})
			if (username or user_id)
			else (None, None)
		)
	)
	ctrl.groups_config_file = groups_path
	ctrl.api_token_store_config = {}
	ctrl.server = types.SimpleNamespace()
	return ctrl


async def _call_mw(mw, path: str, method: str = "GET"):
	handler_called = {"n": 0}

	async def handler(_req):
		handler_called["n"] += 1
		return types.SimpleNamespace(status=200, body={"ok": True})

	resp = await mw(_FakeRequest(path, method=method), handler)
	return resp, handler_called["n"]


def test_sensitive_perm_helper(failed: list[str]) -> None:
	print("TestSensitivePermHelper")
	ac = _load_access_control()
	fn = ac._sensitive_perm_allowed
	_ok(fn({}, "can_run") is False, "missing can_run denies", failed)
	_ok(fn({"can_run": None}, "can_run") is False, "None can_run denies", failed)
	_ok(fn({"can_run": False}, "can_run") is False, "False can_run denies", failed)
	_ok(fn({"can_run": True}, "can_run") is True, "True can_run allows", failed)
	_ok(fn({"can_run": "false"}, "can_run") is False, 'string "false" denies', failed)
	_ok(fn({"can_run": "true"}, "can_run") is True, 'string "true" allows', failed)
	_ok(fn({"can_upload": 0}, "can_upload") is False, "0 can_upload denies", failed)
	_ok(fn({"can_access_api": 1}, "can_access_api") is True, "1 can_access_api allows", failed)


def test_middleware_default_deny_missing_flags(failed: list[str]) -> None:
	print("TestMiddlewareDefaultDenyMissingFlags")
	ac = _load_access_control()
	ctrl = _make_ctrl(ac, {"user": {"ui_clear": True}}, role="user")

	async def _run():
		ctrl._get_user_role_and_permissions = lambda _req: ("user", {"ui_clear": True}, "alice")
		mw = ctrl.create_mss_login_middleware()

		cases = [
			("/prompt", "POST", 403, "missing can_run denies queue"),
			("/upload/image", "POST", 403, "missing can_upload denies upload"),
			("/api/object_info", "GET", 403, "missing can_access_api denies API"),
		]
		for path, method, expect, label in cases:
			resp, n = await _call_mw(mw, path, method)
			_ok(resp.status == expect, f"{label} (status={getattr(resp, 'status', None)})", failed)
			_ok(n == 0, f"{label}: handler not called", failed)

		ctrl._get_user_role_and_permissions = lambda _req: (
			"user",
			{"can_run": True, "can_upload": True, "can_access_api": True},
			"alice",
		)
		mw2 = ctrl.create_mss_login_middleware()
		resp, n = await _call_mw(mw2, "/prompt", "POST")
		_ok(resp.status == 200 and n == 1, "explicit can_run True allows queue", failed)
		resp, n = await _call_mw(mw2, "/api/history", "GET")
		_ok(resp.status == 200 and n == 1, "explicit can_access_api True allows API", failed)

		ctrl._get_user_role_and_permissions = lambda _req: (
			"user",
			{"can_run": False, "can_upload": False, "can_access_api": False},
			"alice",
		)
		mw3 = ctrl.create_mss_login_middleware()
		resp, _ = await _call_mw(mw3, "/prompt", "POST")
		_ok(resp.status == 403, "explicit can_run False denies", failed)

		# Intentional admin shortcut for can_modify_workflows (unchanged by AUDIT-003)
		ctrl._get_user_role_and_permissions = lambda _req: (
			"admin",
			{"can_run": True, "can_upload": True, "can_access_api": True},
			"admin",
		)
		mw4 = ctrl.create_mss_login_middleware()
		resp, n = await _call_mw(mw4, "/api/userdata/workflows/save", "POST")
		_ok(
			resp.status == 200 and n == 1,
			"admin shortcut allows modify workflows when flag missing",
			failed,
		)

	asyncio.run(_run())


def test_queue_put_default_deny(failed: list[str]) -> None:
	print("TestQueuePutDefaultDeny")
	ac = _load_access_control()
	ctrl = _make_ctrl(ac, {"user": {}}, role="user")
	ctrl._current_user = types.SimpleNamespace(get=lambda: "uid", set=lambda *_: None)
	ctrl._AccessControl__current_user_id = "uid"

	class _PQ:
		def put(self, item):
			self.last = item

	pq = _PQ()
	ctrl._AccessControl__prompt_queue_put = pq.put
	ctrl._AccessControl__prompt_queue = pq
	raised = False
	try:
		ctrl.user_queue_put(("prompt",))
	except PermissionError:
		raised = True
	_ok(raised, "user_queue_put raises when can_run missing", failed)

	ctrl2 = _make_ctrl(ac, {"user": {"can_run": True}}, role="user")
	ctrl2._current_user = types.SimpleNamespace(get=lambda: "uid", set=lambda *_: None)
	ctrl2._AccessControl__current_user_id = "uid"
	pq2 = _PQ()
	ctrl2._AccessControl__prompt_queue_put = pq2.put
	ctrl2._AccessControl__prompt_queue = pq2
	ctrl2.user_queue_put(("prompt",))
	_ok(hasattr(pq2, "last"), "user_queue_put allows when can_run True", failed)


def main() -> int:
	failed: list[str] = []
	try:
		test_sensitive_perm_helper(failed)
		test_middleware_default_deny_missing_flags(failed)
		test_queue_put_default_deny(failed)
	except Exception as e:
		print(f"FAIL: {e}")
		import traceback

		traceback.print_exc()
		return 1
	print()
	if failed:
		print(f"Failed ({len(failed)}): {failed}")
		return 1
	print("All access-control permission tests passed.")
	return 0


if __name__ == "__main__":
	sys.exit(main())
