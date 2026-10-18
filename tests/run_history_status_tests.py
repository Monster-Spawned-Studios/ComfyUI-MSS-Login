"""
Unit tests: user_queue_task_done forwards ComfyUI core status= / process_item=
into per-user history (no ComfyUI install required).
"""

from __future__ import annotations

import importlib.util
import os
import sys
import threading
import types
from typing import Literal, NamedTuple

_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def ok(cond: bool, msg: str) -> None:
	if not cond:
		raise AssertionError(msg)
	print(f"  OK: {msg}")


def _load(name: str, path: str, package: str | None = None):
	spec = importlib.util.spec_from_file_location(name, path)
	mod = importlib.util.module_from_spec(spec)
	if package:
		mod.__package__ = package
	assert spec and spec.loader
	sys.modules[name] = mod
	spec.loader.exec_module(mod)
	return mod


class ExecutionStatus(NamedTuple):
	"""Mirror ComfyUI v0.37.4 PromptQueue.ExecutionStatus."""

	status_str: Literal["success", "error"]
	completed: bool
	messages: list


def _jobs_style_classify(status: dict) -> str:
	"""
	Minimal copy of comfy_execution/jobs.normalize_history_item status mapping
	(v0.37.4): success→COMPLETED; error+interrupted→CANCELLED; error→FAILED.
	"""
	status_str = (status or {}).get("status_str")
	messages = (status or {}).get("messages") or []
	was_interrupted = False
	for entry in messages:
		if (
			isinstance(entry, (list, tuple))
			and len(entry) >= 1
			and entry[0] == "execution_interrupted"
		):
			was_interrupted = True
			break
	if status_str == "success":
		return "COMPLETED"
	if status_str == "error":
		return "CANCELLED" if was_interrupted else "FAILED"
	return "COMPLETED"


def _install_stubs() -> None:
	root = types.ModuleType("mss_login")
	root.__path__ = [_PROJECT_ROOT]
	utils = types.ModuleType("mss_login.utils")
	utils.__path__ = [os.path.join(_PROJECT_ROOT, "utils")]
	sys.modules["mss_login"] = root
	sys.modules["mss_login.utils"] = utils

	for name in ("folder_paths", "execution", "server", "aiohttp"):
		sys.modules[name] = types.ModuleType(name)
	sys.modules["folder_paths"].get_output_directory = lambda: "/tmp"
	sys.modules["folder_paths"].get_temp_directory = lambda: "/tmp"
	sys.modules["folder_paths"].get_input_directory = lambda: "/tmp"

	pq = types.SimpleNamespace(ExecutionStatus=ExecutionStatus)
	sys.modules["execution"].MAXIMUM_HISTORY_SIZE = 10
	sys.modules["execution"].PromptQueue = pq

	sys.modules["server"].PromptServer = object
	sys.modules["aiohttp"].web = types.SimpleNamespace(
		middleware=lambda f: f, json_response=lambda *a, **k: None, Request=object
	)

	api_tok = types.ModuleType("mss_login.utils.api_token_store")
	api_tok.get_api_token_store = lambda *_a, **_k: types.SimpleNamespace(
		get_user_for_token=lambda *_: None
	)
	sys.modules["mss_login.utils.api_token_store"] = api_tok
	data_dir = types.ModuleType("mss_login.utils.data_dir")
	data_dir.get_data_subdir = lambda *_a, **_k: "/tmp"
	sys.modules["mss_login.utils.data_dir"] = data_dir
	debug = types.ModuleType("mss_login.utils.debug_log")
	debug.debug_write = lambda *_a, **_k: None
	sys.modules["mss_login.utils.debug_log"] = debug
	iso = types.ModuleType("mss_login.utils.user_isolation")
	iso.safe_user_dir_segment = lambda x: str(x or "x")
	iso.user_id_from_queue_item = lambda e: (
		e[-1].get("user_id") if isinstance(e, tuple) and isinstance(e[-1], dict) else None
	)
	iso.username_from_queue_item = lambda e: (
		e[-1].get("username") if isinstance(e, tuple) and isinstance(e[-1], dict) else None
	)
	sys.modules["mss_login.utils.user_isolation"] = iso
	users_db_mod = types.ModuleType("mss_login.utils.users_db")
	users_db_mod.UsersDB = object
	sys.modules["mss_login.utils.users_db"] = users_db_mod


def _load_access_control():
	_install_stubs()
	# Reload fresh so ExecutionStatus stub is visible if re-run in-process
	sys.modules.pop("mss_login.utils.access_control", None)
	return _load(
		"mss_login.utils.access_control",
		os.path.join(_PROJECT_ROOT, "utils", "access_control.py"),
		"mss_login.utils",
	)


class _FakeQueue:
	def __init__(self):
		self.mutex = threading.Lock()
		self.currently_running: dict = {}
		self.history: dict = {}


def _make_ctrl(ac_mod, *, user_id: str = "user-a", admin: bool = False):
	ctrl = ac_mod.AccessControl.__new__(ac_mod.AccessControl)
	queue_updates = {"n": 0}

	def _queue_updated():
		queue_updates["n"] += 1

	ctrl.server = types.SimpleNamespace(queue_updated=_queue_updated)
	ctrl._queue_updates = queue_updates
	ctrl._current_user = types.SimpleNamespace(get=lambda: user_id, set=lambda *_: None)
	ctrl._AccessControl__current_user_id = user_id
	ctrl._AccessControl__prompt_queue = _FakeQueue()

	groups = ["admin", "owner"] if admin else ["user"]

	def get_user(**kw):
		uid = kw.get("user_id") or kw.get("username")
		if uid in ("user-a", "USER_A"):
			return ("user-a", {"username": "alice", "groups": ["user"]})
		if uid in ("user-b", "USER_B"):
			return ("user-b", {"username": "bob", "groups": ["user"]})
		if uid in ("admin-1",) or (admin and uid == user_id):
			return (user_id, {"username": "admin", "groups": groups, "admin": True})
		if uid == user_id:
			return (user_id, {"username": "me", "groups": groups, "admin": admin})
		return (None, None)

	ctrl.users_db = types.SimpleNamespace(get_user=get_user)
	return ctrl


def _core_prompt(prompt_id: str, *, sensitive: str = "SECRET", user_id: str = "user-a"):
	# Core shape before MSS stamp; sensitive lives at index 5 (removed by
	# main.py remove_sensitive = lambda p: p[:5] + p[6:]).
	core = (0, prompt_id, {"1": {}}, {}, {}, {"extra_pnginfo": sensitive})
	return (*core, {"user_id": user_id, "username": "alice"})


def test_success_status_and_process_item():
	print("TestSuccessStatusAndProcessItem")
	ac = _load_access_control()
	ctrl = _make_ctrl(ac)
	pq = ctrl._AccessControl__prompt_queue
	item_id = 1
	msgs = [
		("execution_start", {"prompt_id": "p1", "timestamp": 1}),
		("execution_success", {"prompt_id": "p1", "timestamp": 9}),
	]
	pq.currently_running[item_id] = _core_prompt("p1", sensitive="LEAK")
	status = ExecutionStatus("success", True, msgs)
	remove_sensitive = lambda p: p[:5] + p[6:]  # noqa: E731 — match core main.py
	ctrl.user_queue_task_done(
		item_id,
		{"outputs": {"1": {"images": []}}, "meta": {"1": {}}},
		status=status,
		process_item=remove_sensitive,
	)
	entry = pq.history["p1"]
	ok(entry["status"]["status_str"] == "success", "status_str success")
	ok(entry["status"]["completed"] is True, "completed True")
	ok(entry["status"]["messages"] == msgs, "messages preserved")
	ok(entry["status"]["messages"][0][1]["timestamp"] == 1, "start timestamp")
	ok(entry["status"]["messages"][1][1]["timestamp"] == 9, "success timestamp")
	prompt = entry["prompt"]
	ok(len(prompt) == 5, "stored prompt has 5 elements")
	ok(all("LEAK" not in str(x) for x in prompt), "sensitive element not stored")
	ok(entry["user_id"] == "user-a", "user_id stamped")
	ok(entry["outputs"] == {"1": {"images": []}}, "history_result outputs merged")
	ok(entry.get("meta") == {"1": {}}, "history_result meta merged")
	ok(ctrl._queue_updates["n"] == 1, "queue_updated called")


def test_error_status():
	print("TestErrorStatus")
	ac = _load_access_control()
	ctrl = _make_ctrl(ac)
	pq = ctrl._AccessControl__prompt_queue
	item_id = 2
	msgs = [
		("execution_start", {"prompt_id": "p2", "timestamp": 1}),
		(
			"execution_error",
			{"prompt_id": "p2", "exception_message": "boom", "node_id": "3", "timestamp": 5},
		),
	]
	pq.currently_running[item_id] = _core_prompt("p2")
	ctrl.user_queue_task_done(
		item_id, {"outputs": {}}, status=ExecutionStatus("error", False, msgs)
	)
	st = pq.history["p2"]["status"]
	ok(st["status_str"] == "error", "error status_str")
	ok(st["completed"] is False, "error completed False")
	ok(st["messages"][1][1]["exception_message"] == "boom", "exception_message kept")


def test_interrupted_cancelled():
	print("TestInterruptedCancelled")
	ac = _load_access_control()
	ctrl = _make_ctrl(ac)
	pq = ctrl._AccessControl__prompt_queue
	item_id = 3
	msgs = [
		("execution_start", {"prompt_id": "p3", "timestamp": 1}),
		("execution_interrupted", {"prompt_id": "p3", "timestamp": 4}),
	]
	pq.currently_running[item_id] = _core_prompt("p3")
	ctrl.user_queue_task_done(
		item_id, {"outputs": {}}, status=ExecutionStatus("error", False, msgs)
	)
	st = pq.history["p3"]["status"]
	ok(st["status_str"] == "error", "interrupted status_str error")
	ok(st["completed"] is False, "interrupted completed False")
	ok(_jobs_style_classify(st) == "CANCELLED", "jobs-style classifier CANCELLED")


def test_positional_status():
	print("TestPositionalStatus")
	ac = _load_access_control()
	ctrl = _make_ctrl(ac)
	pq = ctrl._AccessControl__prompt_queue
	item_id = 4
	msgs = [("execution_success", {"prompt_id": "p4", "timestamp": 2})]
	pq.currently_running[item_id] = _core_prompt("p4")
	status = ExecutionStatus("success", True, msgs)
	ctrl.user_queue_task_done(item_id, {"outputs": {}}, status)
	ok(pq.history["p4"]["status"]["status_str"] == "success", "positional status works")


def test_forward_compat_unknown_kwarg():
	print("TestForwardCompatUnknownKwarg")
	ac = _load_access_control()
	ctrl = _make_ctrl(ac)
	pq = ctrl._AccessControl__prompt_queue
	item_id = 5
	msgs = [("execution_success", {"prompt_id": "p5", "timestamp": 1})]
	pq.currently_running[item_id] = _core_prompt("p5")
	ctrl.user_queue_task_done(
		item_id, {"outputs": {}}, status=ExecutionStatus("success", True, msgs), foo=1
	)
	ok("p5" in pq.history, "extra kwarg does not raise")


def test_legacy_fallback():
	print("TestLegacyFallback")
	ac = _load_access_control()
	ctrl = _make_ctrl(ac)
	pq = ctrl._AccessControl__prompt_queue
	item_id = 6
	msgs = [("execution_success", {"prompt_id": "p6", "timestamp": 1})]
	pq.currently_running[item_id] = _core_prompt("p6")
	ctrl.user_queue_task_done(item_id, {"outputs": {}}, completed=True, messages=msgs)
	st = pq.history["p6"]["status"]
	ok(isinstance(st["messages"], list), "legacy messages is list")
	ok(st["completed"] is True, "legacy completed")
	ok(st.get("status_str") == "success", "legacy status_str from bool completed")


def test_regression_core_style_status():
	print("TestRegressionCoreStyleStatus")
	ac = _load_access_control()
	ctrl = _make_ctrl(ac)
	pq = ctrl._AccessControl__prompt_queue
	item_id = 7
	msgs = [
		("execution_start", {"prompt_id": "p7", "timestamp": 1}),
		("execution_success", {"prompt_id": "p7", "timestamp": 2}),
	]
	pq.currently_running[item_id] = _core_prompt("p7")
	ctrl.user_queue_task_done(
		item_id,
		{"outputs": {}},
		status=ExecutionStatus("success", True, msgs),
		process_item=lambda p: p[:5] + p[6:],
	)
	st = pq.history["p7"]["status"]
	ok(st["messages"] and len(st["messages"]) > 0, "regression: messages non-empty")
	ok(st["completed"] is not None, "regression: completed not None")


def test_per_user_history_filtering():
	print("TestPerUserHistoryFiltering")
	ac = _load_access_control()
	ctrl = _make_ctrl(ac, user_id="user-a", admin=False)
	pq = ctrl._AccessControl__prompt_queue
	msgs_a = [("execution_success", {"prompt_id": "pa", "timestamp": 1})]
	msgs_b = [("execution_success", {"prompt_id": "pb", "timestamp": 2})]
	pq.currently_running[10] = _core_prompt("pa", user_id="user-a")
	ctrl.user_queue_task_done(10, {"outputs": {}}, status=ExecutionStatus("success", True, msgs_a))
	pq.currently_running[11] = _core_prompt("pb", user_id="user-b")
	ctrl.user_queue_task_done(11, {"outputs": {}}, status=ExecutionStatus("success", True, msgs_b))
	# As USER_A
	ctrl._current_user = types.SimpleNamespace(get=lambda: "user-a", set=lambda *_: None)
	ctrl._AccessControl__current_user_id = "user-a"
	hist_a = ctrl.user_queue_get_history()
	ok(set(hist_a.keys()) == {"pa"}, "user-a sees only own history")
	ok(hist_a["pa"]["status"]["status_str"] == "success", "user-a entry has full status")
	# As admin
	ctrl_admin = _make_ctrl(ac, user_id="admin-1", admin=True)
	ctrl_admin._AccessControl__prompt_queue = pq
	hist_admin = ctrl_admin.user_queue_get_history()
	ok(set(hist_admin.keys()) == {"pa", "pb"}, "admin sees both entries")


def test_status_dict_messages_coercion():
	"""Dict status must not KeyError when messages is missing, None, or non-list."""
	print("TestStatusDictMessagesCoercion")
	ac = _load_access_control()
	ctrl = _make_ctrl(ac)
	pq = ctrl._AccessControl__prompt_queue

	# Missing messages key
	pq.currently_running[20] = _core_prompt("p-missing")
	ctrl.user_queue_task_done(
		20, {"outputs": {}}, status={"status_str": "success", "completed": True}
	)
	st = pq.history["p-missing"]["status"]
	ok(isinstance(st.get("messages"), list), "missing messages -> list")
	ok(st["messages"] == [], "missing messages -> empty list")
	ok(st["status_str"] == "success", "missing messages keeps status_str")

	# messages: None
	pq.currently_running[21] = _core_prompt("p-none")
	ctrl.user_queue_task_done(
		21, {"outputs": {}}, status={"status_str": "error", "completed": False, "messages": None}
	)
	st = pq.history["p-none"]["status"]
	ok(st["messages"] == [], "None messages -> empty list")

	# Normal list preserved
	msgs = [("execution_success", {"prompt_id": "p-ok", "timestamp": 1})]
	pq.currently_running[22] = _core_prompt("p-ok")
	ctrl.user_queue_task_done(
		22, {"outputs": {}}, status={"status_str": "success", "completed": True, "messages": msgs}
	)
	st = pq.history["p-ok"]["status"]
	ok(st["messages"] == msgs, "normal messages list preserved")

	# Non-list (string) coerced to [] — do not character-split
	pq.currently_running[23] = _core_prompt("p-str")
	ctrl.user_queue_task_done(
		23,
		{"outputs": {}},
		status={"status_str": "success", "completed": True, "messages": "not-a-list"},
	)
	st = pq.history["p-str"]["status"]
	ok(st["messages"] == [], "non-list messages -> empty list (no char split)")

	# Non-dict status still builds via kwargs path
	pq.currently_running[24] = _core_prompt("p-nondict")
	ctrl.user_queue_task_done(
		24,
		{"outputs": {}},
		status="legacy-blob",
		completed=True,
		messages=[("execution_success", {"prompt_id": "p-nondict", "timestamp": 1})],
	)
	st = pq.history["p-nondict"]["status"]
	ok(isinstance(st["messages"], list), "non-dict status uses kwargs messages")
	ok(len(st["messages"]) == 1, "non-dict kwargs messages kept")


def run_tests() -> int:
	try:
		test_success_status_and_process_item()
		test_error_status()
		test_interrupted_cancelled()
		test_positional_status()
		test_forward_compat_unknown_kwarg()
		test_legacy_fallback()
		test_regression_core_style_status()
		test_per_user_history_filtering()
		test_status_dict_messages_coercion()
	except Exception as e:
		print(f"FAIL: {e}")
		return 1
	print("All history status tests passed.")
	return 0


def main() -> int:
	return run_tests()


if __name__ == "__main__":
	sys.exit(main())
