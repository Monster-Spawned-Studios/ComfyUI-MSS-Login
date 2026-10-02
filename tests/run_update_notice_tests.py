#!/usr/bin/env python3
"""Tests for public update notice redaction, persistence, and can_update_mss_login."""

from __future__ import annotations

import importlib.util
import os
import sys
import tempfile
import types

_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_UTILS_DIR = os.path.join(_PROJECT_ROOT, "utils")


def _load_module(name: str, path: str, package: str):
	spec = importlib.util.spec_from_file_location(name, path)
	mod = importlib.util.module_from_spec(spec)
	mod.__package__ = package
	assert spec and spec.loader
	sys.modules[name] = mod
	spec.loader.exec_module(mod)
	return mod


def _install_pkg(data_dir: str):
	root_pkg = types.ModuleType("mss_login")
	root_pkg.__path__ = [_PROJECT_ROOT]
	utils_pkg = types.ModuleType("mss_login.utils")
	utils_pkg.__path__ = [_UTILS_DIR]
	sys.modules["mss_login"] = root_pkg
	sys.modules["mss_login.utils"] = utils_pkg
	const = types.ModuleType("mss_login.constants")
	const.experimental_model_isolation_enabled = lambda: False
	sys.modules["mss_login.constants"] = const
	os.environ["MSS_LOGIN_DATA_DIR"] = data_dir
	for key in list(sys.modules):
		if key in (
			"mss_login.utils.data_dir",
			"mss_login.utils.updater",
			"mss_login.utils.model_visibility_policy",
		):
			del sys.modules[key]


def ok(cond: bool, msg: str) -> None:
	if not cond:
		raise AssertionError(msg)


def main() -> int:
	failures = 0
	with tempfile.TemporaryDirectory() as tmp:
		_install_pkg(tmp)
		data_dir = _load_module(
			"mss_login.utils.data_dir", os.path.join(_UTILS_DIR, "data_dir.py"), "mss_login.utils"
		)
		# Force DATA_DIR cache to temp
		data_dir._DATA_DIR = tmp
		updater = _load_module(
			"mss_login.utils.updater", os.path.join(_UTILS_DIR, "updater.py"), "mss_login.utils"
		)
		policy = _load_module(
			"mss_login.utils.model_visibility_policy",
			os.path.join(_UTILS_DIR, "model_visibility_policy.py"),
			"mss_login.utils",
		)

		try:
			updater._CACHE.clear()
			updater._CACHE.update(
				{
					"current_version": "0.0.7",
					"latest_version": "0.0.8",
					"update_available": True,
					"release_url": "https://github.com/example/repo/releases/tag/v0.0.8",
					"changelog_body": "SECRET CHANGELOG",
					"mode": "notify",
				}
			)
			notice = updater.get_public_update_notice()
			ok(notice["update_available"] is True, "notice reports update available")
			ok(notice["latest_version"] == "0.0.8", "notice has latest version")
			ok(
				notice["release_url"] == "https://github.com/example/repo/releases/tag/v0.0.8",
				"notice keeps https release_url",
			)
			ok("changelog_body" not in notice, "public notice omits changelog_body")
			print("[PASS] public notice redaction")
		except AssertionError as e:
			print(f"[FAIL] public notice redaction: {e}")
			failures += 1

		try:
			updater._CACHE["release_url"] = "http://evil.example/x"
			notice = updater.get_public_update_notice()
			ok(notice["release_url"] == "", "non-https release_url stripped")
			print("[PASS] non-https release_url stripped")
		except AssertionError as e:
			print(f"[FAIL] non-https release_url: {e}")
			failures += 1

		try:
			updater._CACHE.clear()
			updater._CACHE.update(
				{
					"current_version": "0.0.7",
					"latest_version": "0.0.9",
					"update_available": True,
					"release_url": "https://example.com/r",
					"mode": "notify",
					"changelog_body": "body",
				}
			)
			updater._persist_cached_status()
			updater._CACHE.clear()
			status = updater.get_cached_status()
			ok(status["update_available"] is True, "hydrated update_available")
			ok(status["latest_version"] == "0.0.9", "hydrated latest_version")
			print("[PASS] persist and hydrate update status")
		except AssertionError as e:
			print(f"[FAIL] persist/hydrate: {e}")
			failures += 1

		try:
			ok(policy.user_can_update_mss_login("owner", {}) is True, "owner default")
			ok(policy.user_can_update_mss_login("admin", {}) is True, "admin default")
			ok(policy.user_can_update_mss_login("user", {}) is False, "user default")
			ok(
				policy.user_can_update_mss_login("admin", {"can_update_mss_login": False}) is False,
				"admin explicit deny",
			)
			ok(
				policy.user_can_update_mss_login("user", {"can_update_mss_login": True}) is True,
				"user explicit allow",
			)
			print("[PASS] can_update_mss_login permission")
		except AssertionError as e:
			print(f"[FAIL] permission: {e}")
			failures += 1

	os.environ.pop("MSS_LOGIN_DATA_DIR", None)
	if failures:
		print(f"\n{failures} failure(s)")
		return 1
	print("\nAll update-notice tests passed.")
	return 0


if __name__ == "__main__":
	sys.exit(main())
