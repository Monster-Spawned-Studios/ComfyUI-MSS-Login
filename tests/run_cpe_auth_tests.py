r"""
Standalone tests for Comfy Portal / CPE auth response policy.

Use the project .venv:
  .venv/bin/python tests/run_cpe_auth_tests.py
"""

import importlib.util
import os
import sys
import types

_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_utils = os.path.join(_PROJECT_ROOT, "utils")


def _load_utils_submodule(mod_name):
	if "utils" not in sys.modules:
		pkg = types.ModuleType("utils")
		pkg.__path__ = [_utils]
		pkg.__package__ = "utils"
		sys.modules["utils"] = pkg
	spec = importlib.util.spec_from_file_location(
		f"utils.{mod_name}", os.path.join(_utils, f"{mod_name}.py")
	)
	mod = importlib.util.module_from_spec(spec)
	mod.__package__ = "utils"
	sys.modules[f"utils.{mod_name}"] = mod
	assert spec and spec.loader
	spec.loader.exec_module(mod)
	return mod


def run_tests():
	_load_utils_submodule("path_safety")
	auth = _load_utils_submodule("auth_response")
	cpe = _load_utils_submodule("cpe_workflows")

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

	print("TestJson401ForCpeAndApi")
	ok(
		auth.should_return_json_401("/cpe/workflow/list", accept="*/*"),
		"/cpe/list → JSON even with Accept */*",
	)
	ok(
		auth.should_return_json_401("/api/cpe/workflow/list", accept="text/html"),
		"/api/cpe/list → JSON even with Accept text/html",
	)
	ok(
		auth.should_return_json_401("/cpe/health", accept="text/html", sec_fetch_mode="navigate"),
		"/cpe/health → JSON even with Sec-Fetch-Mode navigate",
	)
	ok(auth.should_return_json_401("/api/prompt", accept="*/*"), "/api/prompt → JSON")
	ok(auth.should_return_json_401("/ws", accept="*/*"), "/ws → JSON")
	ok(
		not auth.should_return_json_401("/", accept="text/html", sec_fetch_mode="navigate"),
		"/ with navigate → HTML redirect",
	)
	ok(
		not auth.should_return_json_401("/login", accept="text/html"),
		"/login with HTML Accept → redirect",
	)
	ok(
		auth.should_return_json_401("/some/page", accept="application/json"),
		"non-API with JSON Accept → JSON 401",
	)

	print("TestCpeConvertNotMutating")
	ok(
		not cpe.cpe_path_requires_modify_workflows("/cpe/workflow/convert", "POST"),
		"convert POST does not require can_modify_workflows",
	)
	ok(
		not cpe.cpe_path_requires_modify_workflows("/api/cpe/workflow/convert", "POST"),
		"api convert POST does not require can_modify_workflows",
	)
	ok(
		cpe.cpe_path_requires_modify_workflows("/cpe/workflow/save", "POST"),
		"save POST requires can_modify_workflows",
	)
	ok(
		not cpe.cpe_path_requires_modify_workflows("/cpe/workflow/list", "GET"),
		"list GET does not require can_modify_workflows",
	)
	ok(cpe.is_cpe_convert_path("/api/cpe/workflow/convert"), "is_cpe_convert_path")

	print("TestRemoteGuardCpePrefix")
	# remote_api_guard has no ComfyUI deps beyond aiohttp for middleware factory;
	# only assert the path constant includes /cpe.
	guard = _load_utils_submodule("remote_api_guard")
	ok(
		any(p == "/cpe" or p.startswith("/cpe") for p in guard.PROTECTED_API_PREFIXES),
		"PROTECTED_API_PREFIXES includes /cpe",
	)

	# Optional Chromium E2E — skipped unless explicitly enabled
	if os.environ.get("MSS_CPE_BROWSER_TESTS") == "1":
		print("TestBrowserE2E (MSS_CPE_BROWSER_TESTS=1)")
		print("  skip: no automated Chromium E2E harness in this runner")
	else:
		print("TestBrowserE2E skipped (set MSS_CPE_BROWSER_TESTS=1 to enable)")

	print(f"\n{run - failed}/{run} passed")
	return failed == 0


if __name__ == "__main__":
	sys.exit(0 if run_tests() else 1)
