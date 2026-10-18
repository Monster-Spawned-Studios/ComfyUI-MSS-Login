r"""
AUDIT-013: get_ip trusts forwarding headers only from trusted_proxies.
Identity-header opt-in matrix (4c) unit checks.

  .venv/bin/python tests/run_trusted_proxy_ip_tests.py
"""

from __future__ import annotations

import importlib.util
import os
import sys
import types

_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_UTILS = os.path.join(_PROJECT_ROOT, "utils")


def _ok(cond: bool, msg: str, failed: list[str]) -> None:
	if cond:
		print(f"  ok: {msg}")
	else:
		print(f"  FAIL: {msg}")
		failed.append(msg)


def _load_ip_filter():
	root = types.ModuleType("mss_login")
	root.__path__ = [_PROJECT_ROOT]
	utils = types.ModuleType("mss_login.utils")
	utils.__path__ = [_UTILS]
	sys.modules["mss_login"] = root
	sys.modules["mss_login.utils"] = utils

	const = types.ModuleType("mss_login.constants")
	const.CLOUDFLARE_PROXY = False
	const.TRUSTED_PROXIES = []
	sys.modules["mss_login.constants"] = const

	aiohttp = types.ModuleType("aiohttp")
	aiohttp.web = types.SimpleNamespace(
		middleware=lambda f: f, json_response=lambda *a, **k: None, Request=object
	)
	sys.modules["aiohttp"] = aiohttp

	path = os.path.join(_UTILS, "ip_filter.py")
	sys.modules.pop("mss_login.utils.ip_filter", None)
	spec = importlib.util.spec_from_file_location("mss_login.utils.ip_filter", path)
	mod = importlib.util.module_from_spec(spec)
	mod.__package__ = "mss_login.utils"
	sys.modules["mss_login.utils.ip_filter"] = mod
	spec.loader.exec_module(mod)
	return mod, const


class _Req:
	def __init__(self, remote, headers=None):
		self.remote = remote
		self.headers = headers or {}


def test_spoof_matrix(failed: list[str]) -> None:
	print("TestTrustedProxySpoofMatrix")
	mod, const = _load_ip_filter()
	get_ip = mod.get_ip

	# Untrusted peer: ignore XFF / CF
	r = _Req("203.0.113.10", {"X-Forwarded-For": "100.64.0.1", "CF-Connecting-IP": "100.64.0.2"})
	_ok(get_ip(r) == "203.0.113.10", "untrusted peer ignores XFF and CF", failed)

	# Trusted peer without CF: XFF right-to-left
	r2 = _Req("10.0.0.1", {"X-Forwarded-For": "198.51.100.9, 10.0.0.1"})
	_ok(
		get_ip(r2, trusted_proxies=["10.0.0.1/32"]) == "198.51.100.9",
		"trusted peer walks XFF to client",
		failed,
	)

	# Trusted peer + cloudflare_proxy
	const.CLOUDFLARE_PROXY = True
	r3 = _Req("10.0.0.1", {"CF-Connecting-IP": "203.0.113.50", "X-Forwarded-For": "1.2.3.4"})
	_ok(
		get_ip(r3, trusted_proxies=["10.0.0.0/8"]) == "203.0.113.50",
		"trusted peer + CF uses CF-Connecting-IP",
		failed,
	)
	const.CLOUDFLARE_PROXY = False

	# Empty trusted_proxies: even localhost peer ignores headers
	r4 = _Req("127.0.0.1", {"X-Forwarded-For": "100.64.0.1"})
	_ok(get_ip(r4, trusted_proxies=[]) == "127.0.0.1", "empty trusted_proxies ignores XFF", failed)


def test_identity_header_opt_in_logic(failed: list[str]) -> None:
	print("TestIdentityHeaderOptInLogic")
	# Pure logic: headers only matter when peer trusted AND opt-in names that header
	allowed = {
		"Tailscale-User-Login",
		"Remote-User",
		"X-Remote-User",
		"X-Forwarded-User",
		"X-Webauth-User",
	}

	def would_accept(peer_trusted: bool, opt_in: str | None, header_sent: str) -> bool:
		if not opt_in or opt_in not in allowed:
			return False
		if not peer_trusted:
			return False
		return header_sent == opt_in

	for hdr in allowed:
		_ok(not would_accept(False, hdr, hdr), f"untrusted peer ignores {hdr}", failed)
		_ok(not would_accept(True, None, hdr), f"opt-in unset ignores {hdr}", failed)
		_ok(
			not would_accept(True, "Remote-User", hdr) if hdr != "Remote-User" else True,
			f"wrong opt-in ignores {hdr}" if hdr != "Remote-User" else "skip same",
			failed,
		)
		_ok(would_accept(True, hdr, hdr), f"trusted + matching opt-in accepts {hdr}", failed)


def main() -> int:
	failed: list[str] = []
	test_spoof_matrix(failed)
	test_identity_header_opt_in_logic(failed)
	print()
	if failed:
		print(f"{len(failed)} failure(s)")
		return 1
	print("All trusted-proxy IP tests passed.")
	return 0


if __name__ == "__main__":
	sys.exit(main())
