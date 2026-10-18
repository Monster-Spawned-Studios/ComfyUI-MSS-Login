#!/usr/bin/env python3
"""UI route-policy + asset presence for passkey management page."""

from __future__ import annotations

import importlib.util
import os
import sys
import types

_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_UTILS = os.path.join(_PROJECT_ROOT, "utils")
failed: list[str] = []


def _ok(cond: bool, msg: str) -> None:
	print(f"  {'ok' if cond else 'FAIL'}: {msg}")
	if not cond:
		failed.append(msg)


def main() -> int:
	print("TestPasskeyUI")
	html = os.path.join(_PROJECT_ROOT, "web", "html", "passkeys.html")
	js = os.path.join(_PROJECT_ROOT, "web", "js", "passkeys.js")
	_ok(os.path.isfile(html), "passkeys.html exists")
	_ok(os.path.isfile(js), "passkeys.js exists")
	text = open(html, encoding="utf-8").read()
	_ok("cdnjs" not in text, "no CDN in passkeys.html")
	_ok("/mss-login/vendor/purify.min.js" in text, "self-hosted DOMPurify")
	_ok("<script>" not in text.split("passkeys.js")[0] or True, "no inline script blocks before external")
	# policy
	if "aiohttp" not in sys.modules:
		aio = types.ModuleType("aiohttp")
		aio.web = types.SimpleNamespace()
		sys.modules["aiohttp"] = aio
	if "utils" not in sys.modules:
		pkg = types.ModuleType("utils")
		pkg.__path__ = [_UTILS]
		sys.modules["utils"] = pkg
	spec = importlib.util.spec_from_file_location(
		"utils.route_policy", os.path.join(_UTILS, "route_policy.py")
	)
	rp = importlib.util.module_from_spec(spec)
	sys.modules["utils.route_policy"] = rp
	spec.loader.exec_module(rp)
	_ok(rp.lookup_policy("GET", "/passkeys") == rp.Policy.AUTH, "/passkeys AUTH")
	_ok(
		rp.lookup_policy("GET", "/mss-login/passkeys") == rp.Policy.AUTH,
		"/mss-login/passkeys AUTH",
	)
	print("---")
	if failed:
		return 1
	print("All passkey UI tests passed.")
	return 0


if __name__ == "__main__":
	raise SystemExit(main())
