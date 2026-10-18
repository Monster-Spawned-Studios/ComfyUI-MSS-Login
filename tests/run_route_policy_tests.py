r"""
AUDIT-002: route policy coverage, path normalize, default-deny semantics.

  .venv/bin/python tests/run_route_policy_tests.py
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


def _load_route_policy():
	root = types.ModuleType("mss_login")
	root.__path__ = [_PROJECT_ROOT]
	utils = types.ModuleType("mss_login.utils")
	utils.__path__ = [_UTILS]
	sys.modules["mss_login"] = root
	sys.modules["mss_login.utils"] = utils

	aiohttp = types.ModuleType("aiohttp")

	class _Resp:
		def __init__(self, *, status=200, body=None):
			self.status = status
			self.body = body

	def json_response(data=None, status=200, **_k):
		return _Resp(status=status, body=data)

	aiohttp.web = types.SimpleNamespace(
		middleware=lambda f: f, json_response=json_response, Request=object
	)
	sys.modules["aiohttp"] = aiohttp

	path = os.path.join(_UTILS, "route_policy.py")
	sys.modules.pop("mss_login.utils.route_policy", None)
	spec = importlib.util.spec_from_file_location("mss_login.utils.route_policy", path)
	mod = importlib.util.module_from_spec(spec)
	mod.__package__ = "mss_login.utils"
	sys.modules["mss_login.utils.route_policy"] = mod
	spec.loader.exec_module(mod)
	return mod


def test_path_normalize(failed: list[str]) -> None:
	print("TestPathNormalize")
	mod = _load_route_policy()
	n = mod.normalize_request_path
	_ok(n("/mss-login/api/groups") == "/mss-login/api/groups", "normal path ok", failed)
	_ok(n("/mss-login//api/groups") is None, "duplicate slash rejected", failed)
	_ok(n("/mss-login/api/%2e%2e/groups") is None, "encoded dot rejected", failed)
	_ok(n("/mss-login/api/%2fgroups") is None, "encoded slash rejected", failed)
	_ok(n("/mss-login/api\\groups") is None, "backslash rejected", failed)
	_ok(n("/mss-login/api/../groups") is None, "dot segment rejected", failed)


def test_policy_lookup(failed: list[str]) -> None:
	print("TestPolicyLookup")
	mod = _load_route_policy()
	_ok(mod.lookup_policy("GET", "/mss-login/api/groups") == "ADMIN", "groups is ADMIN", failed)
	_ok(
		mod.lookup_policy("GET", "/mss-login/api/settings/guest-jwt") == "AUTH",
		"guest-jwt GET is AUTH",
		failed,
	)
	_ok(
		mod.lookup_policy("POST", "/mss-login-gallery/mark-nsfw") == "AUTH",
		"mark-nsfw is AUTH",
		failed,
	)
	_ok(
		mod.lookup_policy("POST", "/mss-login/generate_token") == "PUBLIC",
		"generate_token PUBLIC (Portal issuance)",
		failed,
	)
	_ok(
		mod.lookup_policy("POST", "/mss-login/api/mfa/verify") == "PUBLIC",
		"mfa/verify PUBLIC (completes login; does not break issued tokens)",
		failed,
	)
	_ok(mod.lookup_policy("GET", "/mss-login/api/me") == "OPTIONAL_AUTH", "api/me optional", failed)
	_ok(
		mod.lookup_policy("GET", "/mss-login/api/totally-unknown") is None,
		"unmapped returns None (default deny)",
		failed,
	)


def test_policies_nonempty(failed: list[str]) -> None:
	print("TestPoliciesRegistered")
	mod = _load_route_policy()
	listed = mod.listed_policy_routes()
	_ok(len(listed) > 40, f"policy table has entries ({len(listed)})", failed)
	# Every listed path should be MSS-scoped
	for meth, path, pol in listed:
		base = path.split("{")[0]
		_ok(
			mod.is_mss_scoped_path(base)
			or path in ("/generate_token", "/s3_mounter/unmount")
			or path.startswith("/mss/"),
			f"scoped {meth} {path}",
			failed,
		)


def main() -> int:
	failed: list[str] = []
	test_path_normalize(failed)
	test_policy_lookup(failed)
	test_policies_nonempty(failed)
	print()
	if failed:
		print(f"{len(failed)} failure(s)")
		return 1
	print("All route-policy tests passed.")
	return 0


if __name__ == "__main__":
	sys.exit(main())
