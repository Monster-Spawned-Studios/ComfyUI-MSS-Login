"""
Standalone tests for ComfyUI models_dir / models_path compatibility in download paths.
"""

import importlib.util
import os
import sys
import types

_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_UTILS_DIR = os.path.join(_PROJECT_ROOT, "utils")


def _load_module(name: str, path: str, package: str):
	sys.modules.pop(name, None)
	spec = importlib.util.spec_from_file_location(name, path)
	mod = importlib.util.module_from_spec(spec)
	mod.__package__ = package
	sys.modules[name] = mod
	assert spec and spec.loader
	spec.loader.exec_module(mod)
	return mod


def _install_stubs(models_dir: str, folder_paths: dict[str, list[str]] | None = None):
	root_pkg = types.ModuleType("mss_login")
	root_pkg.__path__ = [_PROJECT_ROOT]
	utils_pkg = types.ModuleType("mss_login.utils")
	utils_pkg.__path__ = [_UTILS_DIR]
	sys.modules["mss_login"] = root_pkg
	sys.modules["mss_login.utils"] = utils_pkg

	const_mod = types.ModuleType("mss_login.constants")
	const_mod.DATA_DIR = os.path.join(_PROJECT_ROOT, "tests", "_tmp_model_download_dest")
	const_mod.experimental_model_isolation_enabled = lambda: False
	sys.modules["mss_login.constants"] = const_mod

	isolation_mod = types.ModuleType("mss_login.utils.model_isolation")
	isolation_mod.maybe_isolated_destination = lambda *_args, **_kwargs: None
	sys.modules["mss_login.utils.model_isolation"] = isolation_mod

	folder_paths_mod = types.ModuleType("folder_paths")
	folder_paths_mod.models_dir = models_dir
	folder_paths_mod.get_folder_paths = lambda folder: (folder_paths or {}).get(folder, [])
	sys.modules["folder_paths"] = folder_paths_mod


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

	models_root = os.path.join(_PROJECT_ROOT, "tests", "_fixtures", "models")
	checkpoints_dir = os.path.join(models_root, "checkpoints")
	os.makedirs(checkpoints_dir, exist_ok=True)

	print("TestGetModelsRoot")
	_install_stubs(models_root)
	compat = _load_module(
		"mss_login.utils.folder_paths_compat",
		os.path.join(_UTILS_DIR, "folder_paths_compat.py"),
		"mss_login.utils",
	)
	ok(compat.get_models_root() == os.path.abspath(models_root), "get_models_root reads models_dir")

	print("TestResolveWithFolderPaths")
	_install_stubs(models_root, {"checkpoints": [checkpoints_dir]})
	compat = _load_module(
		"mss_login.utils.folder_paths_compat",
		os.path.join(_UTILS_DIR, "folder_paths_compat.py"),
		"mss_login.utils",
	)
	dest, base = compat.resolve_local_model_destination("checkpoints", "alice")
	ok(dest == checkpoints_dir, "resolve uses get_folder_paths when available")
	ok(base == os.path.realpath(models_root), "base_dir is models root")

	print("TestResolveFallbackJoin")
	_install_stubs(models_root, {})
	compat = _load_module(
		"mss_login.utils.folder_paths_compat",
		os.path.join(_UTILS_DIR, "folder_paths_compat.py"),
		"mss_login.utils",
	)
	dest, base = compat.resolve_local_model_destination("loras", "alice")
	expected = os.path.join(models_root, "loras")
	ok(dest == expected, "resolve falls back to join(models_dir, folder_type)")
	ok(base == os.path.realpath(models_root), "base_dir matches models root on fallback")

	print("TestNoModelsPathAttribute")
	_install_stubs(models_root, {"checkpoints": [checkpoints_dir]})
	ok(not hasattr(sys.modules["folder_paths"], "models_path"), "stub has models_dir only")
	compat = _load_module(
		"mss_login.utils.folder_paths_compat",
		os.path.join(_UTILS_DIR, "folder_paths_compat.py"),
		"mss_login.utils",
	)
	try:
		compat.resolve_local_model_destination("checkpoints", "user-1")
		ok(True, "resolve works without models_path on folder_paths")
	except ImportError as e:
		ok(False, f"unexpected ImportError: {e}")

	print("TestB2PathStyleDefault")
	# Minimal stubs so s3_mounter can load _load_runtime_config helpers
	const_mod = types.ModuleType("mss_login.constants")
	tmp_data = os.path.join(_PROJECT_ROOT, "tests", "_tmp_s3_path_style")
	os.makedirs(tmp_data, exist_ok=True)
	cfg_path = os.path.join(tmp_data, "config.json")
	const_mod.DATA_DIR = tmp_data
	const_mod.CONFIG_FILE_PATH = cfg_path
	const_mod.SECRET_KEY = "test-secret-key-not-for-prod"
	sys.modules["mss_login.constants"] = const_mod

	json_mod = types.ModuleType("mss_login.utils.json_utils")

	def _load_json(path, default=None):
		if not os.path.isfile(path):
			return default if default is not None else {}
		import json as _json

		with open(path, encoding="utf-8") as f:
			return _json.load(f)

	def _save_json(path, data):
		import json as _json

		with open(path, "w", encoding="utf-8") as f:
			_json.dump(data, f)

	json_mod.load_json_file = _load_json
	json_mod.save_json_file = _save_json
	sys.modules["mss_login.utils.json_utils"] = json_mod

	# Avoid encrypted settings I/O during config load
	app_store_mod = types.ModuleType("mss_login.utils.app_settings_store")

	class _AppStore:
		def get(self, *_a, **_k):
			return ""

		def set(self, *_a, **_k):
			return None

	app_store_mod.get_app_settings_store = lambda *_a, **_k: _AppStore()
	sys.modules["mss_login.utils.app_settings_store"] = app_store_mod
	enc_mod = types.ModuleType("mss_login.utils.encryption")
	enc_mod.decrypt_value = lambda *_a, **_k: ""
	enc_mod.encrypt_and_verify = lambda *_a, **_k: ""
	sys.modules["mss_login.utils.encryption"] = enc_mod

	import json as _json

	with open(cfg_path, "w", encoding="utf-8") as f:
		_json.dump(
			{
				"s3_storage": {
					"enabled": True,
					"endpoint_url": "https://s3.us-west-004.backblazeb2.com",
					"bucket_name": "demo",
					"mount": {"enabled": True},
				}
			},
			f,
		)
	s3m = _load_module(
		"mss_login.utils.s3_mounter",
		os.path.join(_UTILS_DIR, "s3_mounter.py"),
		"mss_login.utils",
	)
	ok(s3m.get_s3_provider_type("https://s3.us-west-004.backblazeb2.com") == "backblaze", "B2 detected")
	cfg = s3m._load_runtime_config()
	ok(cfg["mount"]["use_path_style"] is True, "B2 defaults use_path_style true when unset")

	with open(cfg_path, "w", encoding="utf-8") as f:
		_json.dump(
			{
				"s3_storage": {
					"enabled": True,
					"endpoint_url": "https://s3.us-west-004.backblazeb2.com",
					"bucket_name": "demo",
					"mount": {"enabled": True, "use_path_style": False},
				}
			},
			f,
		)
	cfg = s3m._load_runtime_config()
	ok(cfg["mount"]["use_path_style"] is False, "explicit false overrides B2 default")

	with open(cfg_path, "w", encoding="utf-8") as f:
		_json.dump(
			{
				"s3_storage": {
					"enabled": True,
					"endpoint_url": "https://s3.amazonaws.com",
					"bucket_name": "demo",
					"mount": {"enabled": True},
				}
			},
			f,
		)
	cfg = s3m._load_runtime_config()
	ok(cfg["mount"]["use_path_style"] is False, "AWS keeps path-style false by default")

	print()
	if failed:
		print(f"Result: {failed} failed, {run - failed} passed, {run} total")
		sys.exit(1)
	print(f"Result: all {run} tests passed")
	sys.exit(0)


if __name__ == "__main__":
	run_tests()
