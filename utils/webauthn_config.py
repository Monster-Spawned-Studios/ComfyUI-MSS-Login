# --- START OF FILE utils/webauthn_config.py ---
"""
WebAuthn RP ID and origin allowlist — config only, never from Host / X-Forwarded-*.

Defaults target the Cloudflare tunnel hostname. Tailscale path options are documented
in docs/experimental/webauthn.md (operator must choose; live config is not changed here).
"""

from __future__ import annotations

from typing import Any


DEFAULT_RP_ID = "tmd-nas.win"
DEFAULT_RP_NAME = "MSS-Login"
DEFAULT_ORIGINS = ("https://comfyui.tmd-nas.win",)


def _load_cfg() -> dict[str, Any]:
	try:
		from ..constants import CONFIG_FILE_PATH, _load_config

		return _load_config(CONFIG_FILE_PATH) or {}
	except Exception:
		return {}


def get_webauthn_config() -> dict[str, Any]:
	"""Return {rp_id, rp_name, origins:list[str], passwordless_enabled_default:bool}."""
	cfg = _load_cfg()
	wa = cfg.get("webauthn") if isinstance(cfg.get("webauthn"), dict) else {}
	rp_id = str(wa.get("rp_id") or cfg.get("webauthn_rp_id") or DEFAULT_RP_ID).strip()
	rp_name = str(wa.get("rp_name") or DEFAULT_RP_NAME).strip() or DEFAULT_RP_NAME
	origins_raw = wa.get("origins") or wa.get("allowed_origins") or list(DEFAULT_ORIGINS)
	if isinstance(origins_raw, str):
		origins = [o.strip() for o in origins_raw.split(",") if o.strip()]
	else:
		origins = [str(o).strip() for o in origins_raw if str(o).strip()]
	if not origins:
		origins = list(DEFAULT_ORIGINS)
	return {
		"rp_id": rp_id,
		"rp_name": rp_name,
		"origins": origins,
		"require_device_bound_for_admins": bool(wa.get("require_device_bound_for_admins", False)),
	}


def origin_allowed(origin: str) -> bool:
	o = (origin or "").strip().rstrip("/")
	allowed = {x.rstrip("/") for x in get_webauthn_config()["origins"]}
	return o in allowed


def is_secure_webauthn_origin(origin: str) -> bool:
	"""Passkey UI only on https origins that are not bare IPs (and in the allowlist)."""
	o = (origin or "").strip().lower()
	if not o.startswith("https://"):
		return False
	host = o[len("https://") :].split("/")[0].split(":")[0]
	# Reject bare IPv4
	if all(p.isdigit() for p in host.split(".")) and host.count(".") == 3:
		return False
	# Reject IPv6 literals
	if host.startswith("[") or (host.count(":") >= 2 and not host.endswith(".ts.net")):
		return False
	return origin_allowed(origin)
