r"""
AUDIT-001: guest defaults deny can_run/can_access_api; merge preserves existing values;
opt-in tighten script never widens access.

  .venv/bin/python tests/run_guest_defaults_tests.py
"""

from __future__ import annotations

import importlib.util
import json
import os
import sys
import tempfile
import types

_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_UTILS = os.path.join(_PROJECT_ROOT, "utils")
_DEFAULTS = os.path.join(_PROJECT_ROOT, "users", "defaults", "default_group_config.json")
_SCRIPT = os.path.join(_PROJECT_ROOT, "scripts", "tighten_guest_defaults.py")


def _ok(cond: bool, msg: str, failed: list[str]) -> None:
	if cond:
		print(f"  ok: {msg}")
	else:
		print(f"  FAIL: {msg}")
		failed.append(msg)


def _load_bootstrap(groups_path: str, defaults_path: str):
	"""Load mss_login.utils.bootstrap with stubbed constants pointing at temp paths."""
	root = types.ModuleType("mss_login")
	root.__path__ = [_PROJECT_ROOT]
	utils = types.ModuleType("mss_login.utils")
	utils.__path__ = [_UTILS]
	sys.modules["mss_login"] = root
	sys.modules["mss_login.utils"] = utils

	const = types.ModuleType("mss_login.constants")
	const.DEFAULT_GROUP_CONFIG_PATH = defaults_path
	const.GROUPS_CONFIG_FILE = groups_path
	sys.modules["mss_login.constants"] = const

	globals_mod = types.ModuleType("mss_login.globals")
	globals_mod.logger = types.SimpleNamespace(
		error=lambda *a, **k: None, info=lambda *a, **k: None
	)
	globals_mod.users_db = None
	sys.modules["mss_login.globals"] = globals_mod

	json_utils = types.ModuleType("mss_login.utils.json_utils")

	def load_json_file(path, default=None):
		if not os.path.isfile(path):
			return default if default is not None else {}
		with open(path, encoding="utf-8") as f:
			return json.load(f)

	def save_json_file(path, data):
		parent = os.path.dirname(path)
		if parent:
			os.makedirs(parent, exist_ok=True)
		with open(path, "w", encoding="utf-8") as f:
			json.dump(data, f, indent=2)

	json_utils.load_json_file = load_json_file
	json_utils.save_json_file = save_json_file
	sys.modules["mss_login.utils.json_utils"] = json_utils

	admin_logic = types.ModuleType("mss_login.utils.admin_logic")
	admin_logic.patch_user_group = lambda *a, **k: None
	sys.modules["mss_login.utils.admin_logic"] = admin_logic

	sys.modules.pop("mss_login.utils.bootstrap", None)
	path = os.path.join(_UTILS, "bootstrap.py")
	spec = importlib.util.spec_from_file_location("mss_login.utils.bootstrap", path)
	mod = importlib.util.module_from_spec(spec)
	mod.__package__ = "mss_login.utils"
	sys.modules["mss_login.utils.bootstrap"] = mod
	assert spec and spec.loader
	spec.loader.exec_module(mod)
	return mod


def test_shipped_guest_defaults(failed: list[str]) -> None:
	print("TestShippedGuestDefaults")
	with open(_DEFAULTS, encoding="utf-8") as f:
		cfg = json.load(f)
	guest = cfg.get("guest") or {}
	_ok(guest.get("can_run") is False, "shipped guest.can_run is false", failed)
	_ok(guest.get("can_access_api") is False, "shipped guest.can_access_api is false", failed)


def test_merge_preserves_existing(failed: list[str]) -> None:
	print("TestMergePreservesExistingGuest")
	tmpdir = tempfile.mkdtemp()
	groups_path = os.path.join(tmpdir, "mss_login_groups.json")
	existing = {
		"guest": {"can_run": True, "can_access_api": True, "can_upload": False},
		"user": {"can_run": True},
	}
	with open(groups_path, "w", encoding="utf-8") as f:
		json.dump(existing, f)

	boot = _load_bootstrap(groups_path, _DEFAULTS)
	boot.ensure_groups_config()
	with open(groups_path, encoding="utf-8") as f:
		after = json.load(f)
	_ok(after["guest"]["can_run"] is True, "merge keeps existing guest.can_run=true", failed)
	_ok(
		after["guest"]["can_access_api"] is True,
		"merge keeps existing guest.can_access_api=true",
		failed,
	)


def test_merge_adds_missing_keys_from_deny_defaults(failed: list[str]) -> None:
	print("TestMergeAddsMissingKeys")
	tmpdir = tempfile.mkdtemp()
	groups_path = os.path.join(tmpdir, "mss_login_groups.json")
	existing = {"guest": {"can_run": False, "can_upload": False}}
	with open(groups_path, "w", encoding="utf-8") as f:
		json.dump(existing, f)

	boot = _load_bootstrap(groups_path, _DEFAULTS)
	boot.ensure_groups_config()
	with open(groups_path, encoding="utf-8") as f:
		after = json.load(f)
	_ok(
		after["guest"].get("can_access_api") is False,
		"missing can_access_api filled as false",
		failed,
	)
	_ok(after["guest"]["can_run"] is False, "existing can_run=false preserved", failed)


def test_tighten_script_never_widens(failed: list[str]) -> None:
	print("TestTightenScriptNeverWidens")
	spec = importlib.util.spec_from_file_location("tighten_guest_defaults", _SCRIPT)
	mod = importlib.util.module_from_spec(spec)
	spec.loader.exec_module(mod)

	cases = [
		{"guest": {"can_run": True, "can_access_api": True}},
		{"guest": {"can_run": False, "can_access_api": False}},
		{"guest": {"can_run": True, "can_access_api": False}},
		{"guest": {}, "user": {"can_run": False}},
		{"admin": {"can_run": True}},
	]
	for i, before in enumerate(cases):
		after = mod.apply_tighten(before)
		_ok(mod.never_widens(before, after), f"case {i} never widens", failed)
		guest_b = before.get("guest") if isinstance(before.get("guest"), dict) else {}
		guest_a = after.get("guest") if isinstance(after.get("guest"), dict) else {}
		for key in ("can_run", "can_access_api"):
			if guest_b.get(key) is True:
				_ok(guest_a.get(key) is False, f"case {i} {key} True→False", failed)
			elif guest_b.get(key) is False:
				_ok(guest_a.get(key) is False, f"case {i} {key} stays False", failed)


def main() -> int:
	failed: list[str] = []
	test_shipped_guest_defaults(failed)
	test_merge_preserves_existing(failed)
	test_merge_adds_missing_keys_from_deny_defaults(failed)
	test_tighten_script_never_widens(failed)
	print()
	if failed:
		print(f"{len(failed)} failure(s)")
		return 1
	print("All guest-defaults tests passed.")
	return 0


if __name__ == "__main__":
	sys.exit(main())
