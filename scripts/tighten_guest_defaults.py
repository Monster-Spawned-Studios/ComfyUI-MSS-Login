#!/usr/bin/env python3
"""
Opt-in tool: tighten guest can_run / can_access_api from True to False.

Dry-run by default. Never widens access (never sets False → True).
Backs up the groups config before applying.

Usage:
  .venv/bin/python scripts/tighten_guest_defaults.py
  .venv/bin/python scripts/tighten_guest_defaults.py --apply
  .venv/bin/python scripts/tighten_guest_defaults.py --config /path/to/mss_login_groups.json --apply
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import sys
from datetime import datetime
from typing import Any

_KEYS = ("can_run", "can_access_api")


def _load(path: str) -> dict[str, Any]:
	with open(path, encoding="utf-8") as f:
		data = json.load(f)
	if not isinstance(data, dict):
		raise ValueError("groups config must be a JSON object")
	return data


def _proposed_changes(cfg: dict[str, Any]) -> list[tuple[str, str, bool, bool]]:
	"""Return list of (role, key, old, new) where old is True and new would be False."""
	changes: list[tuple[str, str, bool, bool]] = []
	guest = cfg.get("guest")
	if not isinstance(guest, dict):
		return changes
	for key in _KEYS:
		val = guest.get(key)
		if val is True:
			changes.append(("guest", key, True, False))
	return changes


def apply_tighten(cfg: dict[str, Any]) -> dict[str, Any]:
	"""Return a copy with guest can_run/can_access_api True→False only. Never widens."""
	out = json.loads(json.dumps(cfg))
	guest = out.get("guest")
	if not isinstance(guest, dict):
		return out
	for key in _KEYS:
		if guest.get(key) is True:
			guest[key] = False
	return out


def never_widens(before: dict[str, Any], after: dict[str, Any]) -> bool:
	"""Property: no permission key may change from False/absent to True."""
	for role, perms in after.items():
		if not isinstance(perms, dict):
			continue
		before_perms = before.get(role) if isinstance(before.get(role), dict) else {}
		for key, new_val in perms.items():
			old_val = before_perms.get(key)
			if new_val is True and old_val is not True:
				return False
	return True


def main(argv: list[str] | None = None) -> int:
	parser = argparse.ArgumentParser(description="Tighten guest can_run/can_access_api (opt-in).")
	parser.add_argument(
		"--config",
		default=None,
		help="Path to mss_login_groups.json (default: DATA_DIR or users path via constants)",
	)
	parser.add_argument("--apply", action="store_true", help="Write changes (default is dry-run)")
	args = parser.parse_args(argv)

	config_path = args.config
	if not config_path:
		# Prefer live data-dir groups file when available
		try:
			sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
			from constants import GROUPS_CONFIG_FILE  # noqa: E402

			config_path = GROUPS_CONFIG_FILE
		except Exception:
			config_path = os.path.join(
				os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
				"users",
				"mss_login_groups.json",
			)

	if not os.path.isfile(config_path):
		print(f"Config not found: {config_path}", file=sys.stderr)
		return 1

	before = _load(config_path)
	changes = _proposed_changes(before)
	after = apply_tighten(before)

	if not never_widens(before, after):
		print("Refusing: proposed change would widen access", file=sys.stderr)
		return 2

	if not changes:
		print(
			f"No changes needed (guest can_run/can_access_api already false or unset): {config_path}"
		)
		return 0

	print(f"{'APPLY' if args.apply else 'DRY-RUN'}: {config_path}")
	for role, key, old, new in changes:
		print(f"  {role}.{key}: {old} -> {new}")

	if not args.apply:
		print("Re-run with --apply to write (after backup).")
		return 0

	stamp = datetime.now().strftime("%Y%m%d-%H%M")
	backup = f"{config_path}.bak-{stamp}"
	shutil.copy2(config_path, backup)
	print(f"Backup: {backup}")

	tmp = f"{config_path}.tmp-{stamp}"
	with open(tmp, "w", encoding="utf-8") as f:
		json.dump(after, f, indent=4, sort_keys=False)
		f.write("\n")
	os.replace(tmp, config_path)
	print(f"Updated: {config_path}")
	return 0


if __name__ == "__main__":
	sys.exit(main())
