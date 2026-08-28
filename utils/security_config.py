"""
Load security.json (lockout unlock overrides) from MSS-Login data directory.
Structure: {"lockout": {"unlock_ips": [], "unlock_devices": [], "disable_lockout_until": null}}
"""

import json
import os
import time
from pathlib import Path
from typing import Optional


def load_security_config(security_json_path: str | Path) -> dict:
	"""Load security.json; return lockout section or empty dict. Never raises."""
	path = Path(security_json_path) if isinstance(security_json_path, str) else security_json_path
	if not path.exists():
		return {}
	try:
		with open(path, "r", encoding="utf-8") as f:
			data = json.load(f)
	except Exception:
		return {}
	return data.get("lockout") or {}


def get_unlock_ips(security_json_path: str | Path) -> set[str]:
	"""Return set of IPs that are always allowed (override blacklist)."""
	cfg = load_security_config(security_json_path)
	return set(cfg.get("unlock_ips") or [])


def get_unlock_devices(security_json_path: str | Path) -> set[str]:
	"""Return set of device IDs that are always allowed (override locked_devices)."""
	cfg = load_security_config(security_json_path)
	return set(cfg.get("unlock_devices") or [])


def is_lockout_disabled_until(security_json_path: str | Path) -> float | None:
	"""Return Unix timestamp until which lockout checks are disabled, or None."""
	cfg = load_security_config(security_json_path)
	t = cfg.get("disable_lockout_until")
	if t is None:
		return None
	try:
		return float(t)
	except (TypeError, ValueError):
		return None
