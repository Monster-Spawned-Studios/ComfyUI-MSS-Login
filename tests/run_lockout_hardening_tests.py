r"""
AUDIT-013 lockout: (username, IP), decay, LRU, persist, trusted proxy never blocked,
credential paths include generate_token.

  .venv/bin/python tests/run_lockout_hardening_tests.py
"""

from __future__ import annotations

import importlib.util
import os
import sys
import tempfile
import time
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
		sys.modules["utils"] = pkg

	ip_mod = types.ModuleType("utils.ip_filter")

	class IPFilter:
		pass

	ip_mod.IPFilter = IPFilter
	ip_mod.get_ip = lambda _req: "0.0.0.0"
	ip_mod.is_trusted_proxy_peer = lambda ip, trusted=None: ip in ("10.0.0.1", "127.0.0.1")
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


def test_pair_keying_and_persist(failed: list[str]) -> None:
	print("TestLockoutPairKeying")
	mod = _load_timeout()
	tmpdir = tempfile.mkdtemp()
	state = os.path.join(tmpdir, "login_timeout_state.json")
	t1 = mod.Timeout(_FakeFilter(), blacklist_after_attempts=0, state_path=state)
	for _ in range(3):
		t1.add_failed_attempt("203.0.113.9", "alice")
	_ok(t1.get_failed_attempts("203.0.113.9", "alice") == 3, "alice pair count 3", failed)
	_ok(t1.get_failed_attempts("203.0.113.9", "bob") == 0, "bob not affected", failed)
	timed, n, rem = t1.check_is_timed_out("203.0.113.9", "alice")
	_ok(timed and rem > 0, "alice timed out", failed)
	# Force save
	t1._save_state()
	t2 = mod.Timeout(_FakeFilter(), blacklist_after_attempts=0, state_path=state)
	_ok(t2.get_failed_attempts("203.0.113.9", "alice") == 3, "persists across restart", failed)


def test_trusted_proxy_never_locked(failed: list[str]) -> None:
	print("TestLockoutSkipsTrustedProxy")
	mod = _load_timeout()
	t = mod.Timeout(_FakeFilter(), blacklist_after_attempts=0, state_path=None)
	t.add_failed_attempt("10.0.0.1", "alice")  # trusted in stub
	_ok(t.get_failed_attempts("10.0.0.1", "alice") == 0, "trusted proxy not counted", failed)
	timed, _, _ = t.check_is_timed_out("10.0.0.1", "alice")
	_ok(not timed, "trusted proxy not timed out", failed)


def test_lru_bound(failed: list[str]) -> None:
	print("TestLockoutLRU")
	mod = _load_timeout()
	t = mod.Timeout(_FakeFilter(), blacklist_after_attempts=0, state_path=None, max_entries=50)
	for i in range(80):
		t.add_failed_attempt(f"198.51.100.{i % 250}", f"user{i}")
	_ok(len(t._failed_attempts) <= 50, f"LRU capped ({len(t._failed_attempts)}<=50)", failed)


def test_credential_paths_include_generate_token(failed: list[str]) -> None:
	print("TestCredentialLimitedPaths")
	mod = _load_timeout()
	paths = mod.CREDENTIAL_LIMITED_PATHS
	_ok("/mss-login/generate_token" in paths, "mss-login generate_token limited", failed)
	_ok("/generate_token" in paths, "legacy generate_token limited", failed)
	_ok("/mss-login/api/auth/local-login" in paths, "local-login limited", failed)
	_ok("/login" in paths, "login limited", failed)


def main() -> int:
	failed: list[str] = []
	test_pair_keying_and_persist(failed)
	test_trusted_proxy_never_locked(failed)
	test_lru_bound(failed)
	test_credential_paths_include_generate_token(failed)
	print()
	if failed:
		print(f"{len(failed)} failure(s)")
		return 1
	print("All lockout hardening tests passed.")
	return 0


if __name__ == "__main__":
	sys.exit(main())
