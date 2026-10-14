"""
Unit tests: DOWNLOAD_NOT_ALLOWED shape, SSRF/allowlist, filename sanitize,
progress aggregation, fail-closed queue put, rate limit.
"""

from __future__ import annotations

import importlib.util
import json
import os
import sys
import types

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


def test_error_payload():
	print("TestErrorPayload")
	mod = _load(
		"mss_login.utils.model_download_errors",
		os.path.join(_PROJECT_ROOT, "utils", "model_download_errors.py"),
		"mss_login.utils",
	)
	payload = mod.error_payload(mod.DOWNLOAD_NOT_ALLOWED, mod.DOWNLOAD_NOT_ALLOWED_MESSAGE)
	ok(payload["error"] == "DOWNLOAD_NOT_ALLOWED", "error field is code")
	ok(payload["code"] == "DOWNLOAD_NOT_ALLOWED", "code field present")
	ok(
		"does not have permission" in payload["message"],
		"message is human-readable",
	)


def test_ssrf_and_sanitize():
	print("TestSSRFAndSanitize")
	# Minimal package stubs for relative imports
	root = types.ModuleType("mss_login")
	root.__path__ = [_PROJECT_ROOT]
	utils = types.ModuleType("mss_login.utils")
	utils.__path__ = [os.path.join(_PROJECT_ROOT, "utils")]
	sys.modules.setdefault("mss_login", root)
	sys.modules.setdefault("mss_login.utils", utils)
	user_env = types.ModuleType("mss_login.utils.user_env")
	user_env.load_user_settings = lambda *_a, **_k: {}
	user_env.save_user_settings = lambda *_a, **_k: None
	sys.modules["mss_login.utils.user_env"] = user_env

	mod = _load(
		"mss_login.utils.model_download",
		os.path.join(_PROJECT_ROOT, "utils", "model_download.py"),
		"mss_login.utils",
	)
	ok(mod.is_download_redirect_host_allowed("huggingface.co"), "hf host allowed")
	ok(mod.is_download_redirect_host_allowed("cdn-lfs.huggingface.co"), "hf CDN allowed")
	ok(mod.is_download_redirect_host_allowed("civitai.com"), "civitai allowed")
	ok(not mod.is_download_redirect_host_allowed("evil.example"), "unknown host blocked")
	ok(not mod.is_download_redirect_host_allowed("127.0.0.1"), "localhost blocked")
	ok(mod.sanitize_model_filename("..") == "", "dotdot name rejected")
	ok(
		mod.sanitize_model_filename("/tmp/../x.safetensors") == "x.safetensors",
		"path stripped to basename",
	)
	ok(mod.sanitize_model_filename("model.safetensors") == "model.safetensors", "safe name ok")
	ok(mod.sanitize_model_filename("evil.exe") == "", "bad extension rejected")
	from urllib.parse import urlparse

	err = mod._reject_ssrf_url(urlparse("http://huggingface.co/x"))
	ok(err is not None, "http rejected")
	err = mod._reject_ssrf_url(urlparse("https://169.254.169.254/latest"))
	ok(err is not None, "metadata IP rejected")
	resolved = mod.resolve_model_url("https://civitai.com/models/123?modelVersionId=456")
	ok(resolved and resolved.get("source") == "civitai", "civitai URL resolves")
	resolved_bad = mod.resolve_model_url("https://evil.example/models/1")
	ok(resolved_bad is None, "disallowed host URL rejected")


def test_progress_aggregation():
	print("TestProgressAggregation")
	mod = _load(
		"mss_login.utils.model_download_progress",
		os.path.join(_PROJECT_ROOT, "utils", "model_download_progress.py"),
	)
	jobs = [
		{
			"job_id": "a",
			"status": "running",
			"progress_pct": 50,
			"bytes_done": 50,
			"total_bytes": 100,
			"filename": "a.safetensors",
		},
		{
			"job_id": "b",
			"status": "queued",
			"progress_pct": 0,
			"bytes_done": 0,
			"total_bytes": 100,
			"filename": "b.safetensors",
		},
	]
	val, mx, label = mod.aggregate_progress(jobs, mode="per_model", active_job_id="a")
	ok(val == 50 and mx == 100, "per_model uses active job pct")
	ok("a.safetensors" in label, "per_model label includes filename")
	val2, mx2, _ = mod.aggregate_progress(jobs, mode="overall")
	ok(mx2 == 100 and 0 <= val2 <= 100, "overall returns percent")
	ok(val2 == 25, "overall is 50/200 bytes => 25%")


def test_rate_limit():
	print("TestRateLimit")
	mod = _load(
		"mss_login.utils.download_rate_limit",
		os.path.join(_PROJECT_ROOT, "utils", "download_rate_limit.py"),
	)
	mod.reset_rate_limits()
	ok(mod.check_rate_limit("u1", limit=2, window_seconds=60.0), "first allowed")
	ok(mod.check_rate_limit("u1", limit=2, window_seconds=60.0), "second allowed")
	ok(not mod.check_rate_limit("u1", limit=2, window_seconds=60.0), "third limited")
	ok(mod.check_rate_limit("u2", limit=2, window_seconds=60.0), "other user allowed")


def test_queue_put_fail_closed_and_user_id_match():
	print("TestQueuePutFailClosed")
	# Load access_control with heavy stubs
	root = types.ModuleType("mss_login")
	root.__path__ = [_PROJECT_ROOT]
	utils = types.ModuleType("mss_login.utils")
	utils.__path__ = [os.path.join(_PROJECT_ROOT, "utils")]
	sys.modules["mss_login"] = root
	sys.modules["mss_login.utils"] = utils

	for name in (
		"folder_paths",
		"execution",
		"server",
		"aiohttp",
	):
		if name not in sys.modules:
			sys.modules[name] = types.ModuleType(name)
	sys.modules["folder_paths"].get_output_directory = lambda: "/tmp"
	sys.modules["folder_paths"].get_temp_directory = lambda: "/tmp"
	sys.modules["folder_paths"].get_input_directory = lambda: "/tmp"
	sys.modules["execution"].MAXIMUM_HISTORY_SIZE = 10
	sys.modules["execution"].PromptQueue = object
	sys.modules["server"].PromptServer = object
	sys.modules["aiohttp"].web = types.SimpleNamespace(
		middleware=lambda f: f,
		json_response=lambda *a, **k: None,
		Request=object,
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
	iso.user_id_from_queue_item = lambda e: None
	iso.username_from_queue_item = lambda e: None
	sys.modules["mss_login.utils.user_isolation"] = iso
	users_db_mod = types.ModuleType("mss_login.utils.users_db")
	users_db_mod.UsersDB = object
	sys.modules["mss_login.utils.users_db"] = users_db_mod

	ac = _load(
		"mss_login.utils.access_control",
		os.path.join(_PROJECT_ROOT, "utils", "access_control.py"),
		"mss_login.utils",
	)
	ok(ac.AccessControl._normalize_user_id(123) == "123", "normalize int user id")
	ctrl = ac.AccessControl.__new__(ac.AccessControl)
	ok(ctrl._user_id_matches("123", 123), "type-mismatched ids match")
	ok(not ctrl._user_id_matches("a", "b"), "different ids do not match")

	# Fail-closed put
	import tempfile

	groups_path = os.path.join(tempfile.mkdtemp(), "groups.json")
	with open(groups_path, "w", encoding="utf-8") as f:
		json.dump({"user": {"can_run": False}}, f)

	class _PQ:
		def put(self, item):
			self.last = item

	ctrl.users_db = types.SimpleNamespace(
		get_user=lambda **kw: (
			("uid", {"username": "alice", "groups": ["user"]})
			if kw.get("user_id") == "uid" or kw.get("username") == "alice"
			else (None, None)
		)
	)
	ctrl.groups_config_file = groups_path
	ctrl._current_user = types.SimpleNamespace(get=lambda: "uid", set=lambda *_: None)
	ctrl._AccessControl__current_user_id = "uid"
	ctrl._AccessControl__prompt_queue_put = _PQ().put
	ctrl._AccessControl__prompt_queue = _PQ()
	raised = False
	try:
		ctrl.user_queue_put(("prompt",))
	except PermissionError:
		raised = True
	ok(raised, "user_queue_put raises when can_run is false")


def main() -> int:
	try:
		test_error_payload()
		test_ssrf_and_sanitize()
		test_progress_aggregation()
		test_rate_limit()
		test_queue_put_fail_closed_and_user_id_match()
	except Exception as e:
		print(f"FAIL: {e}")
		return 1
	print("All portal download hardening tests passed.")
	return 0


if __name__ == "__main__":
	sys.exit(main())
