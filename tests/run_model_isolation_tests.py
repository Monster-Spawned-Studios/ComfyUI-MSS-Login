r"""
Standalone tests for model isolation policy helpers (no ComfyUI deps).
"""

import importlib.util
import json
import os
import sys
import types

_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_UTILS_DIR = os.path.join(_PROJECT_ROOT, "utils")
_TMP_CONFIG = os.path.join(_PROJECT_ROOT, ".tmp_model_isolation_test_config.json")


def _install_fake_constants(enabled: bool):
	const_mod = types.ModuleType("mss_login.constants")
	const_mod.DATA_DIR = os.path.join(_PROJECT_ROOT, ".tmp_test_data")
	const_mod.CONFIG_FILE_PATH = _TMP_CONFIG

	def _exp():
		return enabled

	const_mod.experimental_model_isolation_enabled = _exp
	sys.modules["mss_login.constants"] = const_mod


def _load_module(name: str, path: str, package: str):
	spec = importlib.util.spec_from_file_location(name, path)
	mod = importlib.util.module_from_spec(spec)
	mod.__package__ = package
	assert spec and spec.loader
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

	# Package stubs for relative imports.
	root_pkg = types.ModuleType("mss_login")
	root_pkg.__path__ = [_PROJECT_ROOT]
	utils_pkg = types.ModuleType("mss_login.utils")
	utils_pkg.__path__ = [_UTILS_DIR]
	sys.modules["mss_login"] = root_pkg
	sys.modules["mss_login.utils"] = utils_pkg

	json_utils_mod = types.ModuleType("mss_login.utils.json_utils")

	def _load_json_file(path, fallback=None):
		try:
			with open(path, "r", encoding="utf-8") as f:
				return json.load(f)
		except Exception:
			return fallback if fallback is not None else {}

	def _save_json_file(path, payload):
		with open(path, "w", encoding="utf-8") as f:
			json.dump(payload, f)

	json_utils_mod.load_json_file = _load_json_file
	json_utils_mod.save_json_file = _save_json_file
	sys.modules["mss_login.utils.json_utils"] = json_utils_mod

	with open(_TMP_CONFIG, "w", encoding="utf-8") as f:
		json.dump({"model_isolation": {"download_redirect_patterns": ["/owner-custom"]}}, f)

	print("TestModelVisibilityPolicy")
	_install_fake_constants(enabled=True)
	policy = _load_module(
		"mss_login.utils.model_visibility_policy",
		os.path.join(_UTILS_DIR, "model_visibility_policy.py"),
		"mss_login.utils",
	)

	ok(policy.user_can_manage_model_sharing("owner", {}) is True, "owner can share by default")
	ok(
		policy.user_can_manage_model_sharing("admin", {"can_manage_model_sharing": True}) is True,
		"admin can share with explicit permission",
	)
	ok(
		policy.user_can_manage_model_sharing("admin", {"can_manage_model_sharing": False}) is False,
		"admin cannot share without permission",
	)
	ok(
		policy.normalize_model_name("UserA\\Model.SAFETENSORS") == "usera/model.safetensors",
		"model names are normalized backend-agnostically",
	)

	grants = [
		{"folder": "checkpoints", "item_name": "alice/model-a.safetensors"},
		{"folder": "checkpoints", "item_name": "bob/model-b.safetensors"},
	]
	filtered = policy.filter_items_by_grants(
		"checkpoints",
		["alice/model-a.safetensors", "bob/model-b.safetensors", "other.safetensors"],
		[policy._normalized_record(g) for g in grants],
	)
	ok(
		filtered == ["alice/model-a.safetensors", "bob/model-b.safetensors"],
		"grant filtering works",
	)

	print("TestModelIsolationPaths")
	_install_fake_constants(enabled=True)
	paths = _load_module(
		"mss_login.utils.model_isolation",
		os.path.join(_UTILS_DIR, "model_isolation.py"),
		"mss_login.utils",
	)
	ok(paths.sanitize_user_segment("bob@example.com") == "bob_example_com", "user id is sanitized")
	isolation_path = paths.isolation_user_folder("checkpoints", "alice").replace("\\", "/")
	ok(isolation_path.endswith("checkpoints/alice"), "isolation folder path is per-user")

	print("TestModelDownloadRedirect")
	folder_paths_mod = types.ModuleType("folder_paths")
	folder_paths_mod.models_dir = os.path.join(_PROJECT_ROOT, "ComfyUI", "models")
	sys.modules["folder_paths"] = folder_paths_mod
	redirect = _load_module(
		"mss_login.utils.model_download_redirect",
		os.path.join(_UTILS_DIR, "model_download_redirect.py"),
		"mss_login.utils",
	)
	payload = {
		"destination_path": os.path.join(
			_PROJECT_ROOT, "ComfyUI", "models", "checkpoints", "foo.safetensors"
		),
		"other": "keep",
	}
	rewritten, changed = redirect.rewrite_download_payload_for_user(payload, "alice")
	ok(changed is True, "download payload was rewritten")
	ok(
		"/model_isolation/models/checkpoints/alice/foo.safetensors"
		in rewritten["destination_path"].replace("\\", "/"),
		"global models path remaps to user isolated folder",
	)
	effective = redirect.get_effective_route_patterns()
	ok("/owner-custom" in effective, "owner-configured patterns included")
	ok(
		redirect.should_try_model_download_redirect("/my/owner-custom/route") is True,
		"configured route pattern triggers redirect matching",
	)

	print()
	try:
		if os.path.isfile(_TMP_CONFIG):
			os.remove(_TMP_CONFIG)
	except Exception:
		pass
	if failed:
		print(f"Result: {failed} failed, {run - failed} passed, {run} total")
		sys.exit(1)
	print(f"Result: all {run} tests passed")
	sys.exit(0)


if __name__ == "__main__":
	run_tests()
