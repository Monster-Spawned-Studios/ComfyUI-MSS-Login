#!/usr/bin/env python3
"""WebAuthn second-factor tests (PR2)."""

from __future__ import annotations

import importlib.util
import os
import sys
import tempfile
import time
import types

_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_UTILS = os.path.join(_PROJECT_ROOT, "utils")
failed: list[str] = []


def _ok(cond: bool, msg: str) -> None:
	if cond:
		print(f"  ok: {msg}")
	else:
		print(f"  FAIL: {msg}")
		failed.append(msg)


def _ensure_pkg():
	if "utils" not in sys.modules:
		pkg = types.ModuleType("utils")
		pkg.__path__ = [_UTILS]
		sys.modules["utils"] = pkg


def _load(name: str, fn: str):
	_ensure_pkg()
	path = os.path.join(_UTILS, fn)
	mod_name = f"utils.{name}"
	sys.modules.pop(mod_name, None)
	spec = importlib.util.spec_from_file_location(mod_name, path)
	mod = importlib.util.module_from_spec(spec)
	sys.modules[mod_name] = mod
	spec.loader.exec_module(mod)
	return mod


def test_guarded_import():
	print("TestWebAuthnGuard")
	g = _load("webauthn_guard", "webauthn_guard.py")
	_ok(g.passkeys_enabled() is True, "webauthn library available in venv")


def test_file_repo_contract():
	print("TestWebAuthnFileRepo")
	cred_mod = _load("webauthn_credentials", "webauthn_credentials.py")
	with tempfile.TemporaryDirectory() as td:
		repo = cred_mod.FileWebAuthnRepo(os.path.join(td, "creds.json"))
		h = repo.ensure_user_handle("u1")
		_ok(len(h) == 64, "64-byte user handle")
		_ok(repo.get_user_id_for_handle(h) == "u1", "handle reverse lookup")
		c = cred_mod.WebAuthnCredential(
			id="c1",
			user_id="u1",
			credential_id=b"\x01\x02\x03",
			public_key=b"\x04\x05",
			sign_count=0,
			transports=["internal"],
			aaguid="",
			backup_eligible=False,
			backup_state=False,
			rp_id="tmd-nas.win",
			name="Laptop",
			created_at=time.time(),
			last_used_at=None,
			revoked_at=None,
		)
		repo.add(c)
		_ok(repo.count_active_for_user("u1") == 1, "one active")
		got = repo.get_by_credential_id(b"\x01\x02\x03")
		_ok(got is not None and got.name == "Laptop", "lookup by credential id")
		repo.update_sign_count(b"\x01\x02\x03", 2)
		_ok(repo.get_by_credential_id(b"\x01\x02\x03").sign_count == 2, "sign_count updated")
		_ok(repo.revoke(b"\x01\x02\x03", "u1"), "revoke")
		_ok(repo.count_active_for_user("u1") == 0, "none active after revoke")


def test_challenge_consume_once():
	print("TestCeremonyChallenge")
	ch = _load("webauthn_challenge_store", "webauthn_challenge_store.py")
	ch.clear_ceremonies()
	cid = ch.create_ceremony(
		challenge=b"abc",
		ceremony_type="authenticate",
		rp_id="tmd-nas.win",
		expected_origin="https://comfyui.tmd-nas.win",
		username="alice",
	)
	e1 = ch.consume_ceremony(cid, ceremony_type="authenticate")
	_ok(e1 is not None, "first consume ok")
	e2 = ch.consume_ceremony(cid, ceremony_type="authenticate")
	_ok(e2 is None, "replay challenge rejected")


def test_config_origin():
	print("TestWebAuthnConfig")
	cfg_mod = _load("webauthn_config", "webauthn_config.py")
	# Without loading real config, defaults apply via empty _load_cfg override
	orig = cfg_mod._load_cfg
	cfg_mod._load_cfg = lambda: {
		"webauthn": {
			"rp_id": "tmd-nas.win",
			"origins": ["https://comfyui.tmd-nas.win"],
		}
	}
	try:
		_ok(cfg_mod.origin_allowed("https://comfyui.tmd-nas.win"), "tunnel origin allowed")
		_ok(not cfg_mod.origin_allowed("https://evil.example"), "evil origin rejected")
		_ok(
			not cfg_mod.is_secure_webauthn_origin("http://comfyui.tmd-nas.win"),
			"http rejected",
		)
		_ok(
			not cfg_mod.is_secure_webauthn_origin("https://203.0.113.10"),
			"bare IP rejected",
		)
	finally:
		cfg_mod._load_cfg = orig


def test_route_policy_webauthn():
	print("TestWebAuthnRoutePolicy")
	if "aiohttp" not in sys.modules:
		aio = types.ModuleType("aiohttp")
		aio.web = types.SimpleNamespace()
		sys.modules["aiohttp"] = aio
	rp = _load("route_policy", "route_policy.py")
	_ok(
		rp.lookup_policy("POST", "/mss-login/webauthn/auth/options") == rp.Policy.PUBLIC,
		"auth options PUBLIC",
	)
	_ok(
		rp.lookup_policy("POST", "/mss-login/webauthn/register/options") == rp.Policy.AUTH,
		"register AUTH",
	)


def test_missing_dep_message():
	print("TestMissingDepMessage")
	g = _load("webauthn_guard", "webauthn_guard.py")
	# Simulate missing
	old = g.WEBAUTHN_AVAILABLE
	g.WEBAUTHN_AVAILABLE = False
	try:
		_ok(not g.passkeys_enabled(), "disabled when flag false")
	finally:
		g.WEBAUTHN_AVAILABLE = old


def main() -> int:
	test_guarded_import()
	test_file_repo_contract()
	test_challenge_consume_once()
	test_config_origin()
	test_route_policy_webauthn()
	test_missing_dep_message()
	print("---")
	if failed:
		for f in failed:
			print("FAIL", f)
		return 1
	print("All WebAuthn tests passed.")
	return 0


if __name__ == "__main__":
	raise SystemExit(main())
