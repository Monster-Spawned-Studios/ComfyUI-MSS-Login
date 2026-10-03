r"""
Standalone tests for CSP connect-src including HOST_BASE_URL.

  .venv/bin/python tests/run_csp_host_tests.py
"""

import importlib.util
import os
import sys
import types

_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_utils = os.path.join(_PROJECT_ROOT, "utils")


def _ensure_utils_pkg():
	if "utils" not in sys.modules:
		pkg = types.ModuleType("utils")
		pkg.__path__ = [_utils]
		pkg.__file__ = os.path.join(_utils, "__init__.py")
		sys.modules["utils"] = pkg


def _load_csp():
	_ensure_utils_pkg()
	# Minimal stub package so relative imports in csp resolve.
	if "mss_login" not in sys.modules:
		pkg = types.ModuleType("mss_login")
		pkg.__path__ = [_PROJECT_ROOT]
		sys.modules["mss_login"] = pkg
	if "mss_login.utils" not in sys.modules:
		up = types.ModuleType("mss_login.utils")
		up.__path__ = [_utils]
		sys.modules["mss_login.utils"] = up
	path = os.path.join(_utils, "csp.py")
	spec = importlib.util.spec_from_file_location("mss_login.utils.csp", path)
	mod = importlib.util.module_from_spec(spec)
	sys.modules["mss_login.utils.csp"] = mod
	sys.modules["utils.csp"] = mod
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

	csp = _load_csp()
	print("TestCspConnectSrcIncludesHostBaseUrl")
	prev = os.environ.get("HOST_BASE_URL")
	os.environ["HOST_BASE_URL"] = "https://comfy.tmd-nas.win"
	try:
		# Clear constants cache if already imported
		try:
			import constants as constants_mod

			if hasattr(constants_mod, "clear_host_base_url_cache"):
				constants_mod.clear_host_base_url_cache()
		except Exception:
			pass
		directive = csp._connect_src_directive(None)
		ok("https://comfy.tmd-nas.win" in directive, "connect-src includes HOST_BASE_URL origin")
		ok("wss://comfy.tmd-nas.win" in directive, "connect-src includes wss for HOST_BASE_URL")
		ok("'self'" in directive, "connect-src still includes 'self'")
		built = csp._build_csp_for_path("/login", None)
		ok(
			"connect-src" in built and "https://comfy.tmd-nas.win" in built,
			"full CSP includes host",
		)
	finally:
		if prev is None:
			os.environ.pop("HOST_BASE_URL", None)
		else:
			os.environ["HOST_BASE_URL"] = prev
		try:
			import constants as constants_mod

			if hasattr(constants_mod, "clear_host_base_url_cache"):
				constants_mod.clear_host_base_url_cache()
		except Exception:
			pass

	print(f"\n{run} tests, {failed} failed")
	return 0 if failed == 0 else 1


if __name__ == "__main__":
	sys.exit(run_tests())
