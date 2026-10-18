r"""
Standalone tests: login lockout state persists across Timeout instances (AUDIT-013).

  .venv/bin/python tests/run_login_timeout_persist_tests.py
"""

from __future__ import annotations

import importlib.util
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


def _load_timeout():
	if "utils" not in sys.modules:
		pkg = types.ModuleType("utils")
		pkg.__path__ = [_UTILS]
		pkg.__file__ = os.path.join(_UTILS, "__init__.py")
		sys.modules["utils"] = pkg

	# Stub ip_filter (Timeout imports it at module level)
	ip_mod = types.ModuleType("utils.ip_filter")

	class IPFilter:
		pass

	ip_mod.IPFilter = IPFilter
	ip_mod.get_ip = lambda _req: "0.0.0.0"
	sys.modules["utils.ip_filter"] = ip_mod

	aiohttp = types.ModuleType("aiohttp")
	aiohttp.web = types.SimpleNamespace(middleware=lambda f: f, json_response=lambda *a, **k: None)
	sys.modules["aiohttp"] = aiohttp

	path = os.path.join(_UTILS, "timeout.py")
	sys.modules.pop("utils.timeout", None)
	spec = importlib.util.spec_from_file_location("utils.timeout", path)
	mod = importlib.util.module_from_spec(spec)
	sys.modules["utils.timeout"] = mod
	spec.loader.exec_module(mod)
	return mod


class _FakeFilter:
	def load_filter_list(self):
		return None

	def is_whitelisted(self, _ip):
		return False

	def add_to_blacklist(self, _ip):
		return None


def test_timeout_persists_across_restart(failed: list[str]) -> None:
	print("TestTimeoutPersistsAcrossRestart")
	mod = _load_timeout()
	tmpdir = tempfile.mkdtemp()
	state_path = os.path.join(tmpdir, "login_timeout_state.json")

	t1 = mod.Timeout(_FakeFilter(), blacklist_after_attempts=0, state_path=state_path)
	for _ in range(3):
		t1.add_failed_attempt("203.0.113.9")
	_ok(t1.get_failed_attempts("203.0.113.9") == 3, "3 failed attempts recorded", failed)
	timed_out, n, remaining = t1.check_is_timed_out("203.0.113.9")
	_ok(timed_out and n == 3 and remaining > 0, "IP timed out after 3 failures", failed)
	_ok(os.path.isfile(state_path), "state file written", failed)

	# Simulate process restart: new Timeout loads same file
	t2 = mod.Timeout(_FakeFilter(), blacklist_after_attempts=0, state_path=state_path)
	_ok(t2.get_failed_attempts("203.0.113.9") == 3, "failed attempts reloaded", failed)
	timed_out2, n2, rem2 = t2.check_is_timed_out("203.0.113.9")
	_ok(timed_out2 and n2 == 3 and rem2 > 0, "timeout survives restart", failed)

	t2.remove_failed_attempts("203.0.113.9")
	t3 = mod.Timeout(_FakeFilter(), blacklist_after_attempts=0, state_path=state_path)
	_ok(t3.get_failed_attempts("203.0.113.9") == 0, "clear persists across restart", failed)
	timed_out3, _, _ = t3.check_is_timed_out("203.0.113.9")
	_ok(not timed_out3, "cleared IP not timed out after restart", failed)


def main() -> int:
	failed: list[str] = []
	try:
		test_timeout_persists_across_restart(failed)
	except Exception as e:
		print(f"FAIL: {e}")
		import traceback

		traceback.print_exc()
		return 1
	print()
	if failed:
		print(f"Failed ({len(failed)}): {failed}")
		return 1
	print("All login timeout persistence tests passed.")
	return 0


if __name__ == "__main__":
	sys.exit(main())
