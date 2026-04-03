"""
Standalone tests for experimental failsafe config reset behavior.
"""

import importlib.util
import json
import os
import shutil
import sys
import tempfile
import types

_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_CONSTANTS_PATH = os.path.join(_PROJECT_ROOT, "constants.py")


def _load_module(name: str, path: str, package: str):
	spec = importlib.util.spec_from_file_location(name, path)
	mod = importlib.util.module_from_spec(spec)
	mod.__package__ = package
	assert spec and spec.loader
	spec.loader.exec_module(mod)
	return mod


def _install_stubs(tmp_data_dir: str):
	root_pkg = types.ModuleType("mss_login")
	root_pkg.__path__ = [_PROJECT_ROOT]
	utils_pkg = types.ModuleType("mss_login.utils")
	utils_pkg.__path__ = [os.path.join(_PROJECT_ROOT, "utils")]
	sys.modules["mss_login"] = root_pkg
	sys.modules["mss_login.utils"] = utils_pkg

	data_dir_mod = types.ModuleType("mss_login.utils.data_dir")
	data_dir_mod.ensure_data_dir = lambda _root=None: tmp_data_dir
	data_dir_mod.get_data_dir = lambda: tmp_data_dir
	sys.modules["mss_login.utils.data_dir"] = data_dir_mod

	install_mod = types.ModuleType("mss_login.utils.install_deps")
	install_mod.install_dependencies = lambda: True
	sys.modules["mss_login.utils.install_deps"] = install_mod

	path_safety_mod = types.ModuleType("mss_login.utils.path_safety")
	path_safety_mod.resolve_path_under = lambda base, rel: os.path.join(base, rel)
	sys.modules["mss_login.utils.path_safety"] = path_safety_mod


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

	tmp_data_dir = tempfile.mkdtemp(prefix="mss_login_failsafe_")
	try:
		_install_stubs(tmp_data_dir)
		constants = _load_module("mss_login.constants", _CONSTANTS_PATH, "mss_login")
		constants.CONFIG_FILE_PATH = os.path.join(tmp_data_dir, "config.json")
		initial = {
			"experimental_features": True,
			"experimental": {
				"mfa": True,
				"s3": True,
				"loading_screen": True,
				"news": True,
				"model_isolation": True,
			},
			"users_db": {"sqlite_path": "data/mss_login_data.db"},
			"secret_key_env": "SECRET_KEY",
		}
		with open(constants.CONFIG_FILE_PATH, "w", encoding="utf-8") as f:
			json.dump(initial, f, indent=2)

		print("TestFailsafeResetPreservesCredentials")
		state = constants.apply_experimental_safety_reset("unit-test-failure")
		with open(constants.CONFIG_FILE_PATH, "r", encoding="utf-8") as f:
			updated = json.load(f)
		ok(updated.get("experimental_features") is False, "failsafe disables experimental master switch")
		ok(all(updated.get("experimental", {}).get(k) is False for k in ("mfa", "s3", "loading_screen", "news", "model_isolation")), "failsafe disables all experimental sub-flags")
		ok(updated.get("users_db", {}).get("sqlite_path") == "data/mss_login_data.db", "users DB config remains unchanged")
		ok(updated.get("secret_key_env") == "SECRET_KEY", "secret-key config remains unchanged")
		ok(int(state.get("failure_count", 0)) >= 1, "failsafe increments failure counter")

	finally:
		try:
			shutil.rmtree(tmp_data_dir, ignore_errors=True)
		except Exception:
			pass

	print()
	if failed:
		print(f"Result: {failed} failed, {run - failed} passed, {run} total")
		sys.exit(1)
	print(f"Result: all {run} tests passed")
	sys.exit(0)


if __name__ == "__main__":
	run_tests()

