# --- START OF FILE utils/webauthn_config.py ---
"""
WebAuthn RP ID and origin allowlist — config only, never from Host / X-Forwarded-*.

Defaults target the Cloudflare tunnel hostname. Tailscale path options are documented
in docs/experimental/webauthn.md (operator must choose; live config is not changed here).
"""

from __future__ import annotations

import os
from typing import Any
from urllib.parse import urlparse

DEFAULT_RP_ID = "tmd-nas.win"
DEFAULT_RP_NAME = "MSS-Login"
DEFAULT_ORIGINS = ("https://comfyui.tmd-nas.win",)


def _load_cfg() -> dict[str, Any]:
	try:
		from ..constants import CONFIG_FILE_PATH, _load_config

		return _load_config(CONFIG_FILE_PATH) or {}
	except Exception:
		return {}


def _get_host_base_url_parsed() -> tuple[str, str]:
	"""Return (origin, hostname) inferred from HOST_BASE_URL (env or DB store)."""
	url = (os.getenv("HOST_BASE_URL") or "").strip().rstrip("/")
	if not url:
		try:
			from ..constants import get_host_base_url

			url = (get_host_base_url() or "").strip().rstrip("/")
		except Exception:
			try:
				from constants import get_host_base_url

				url = (get_host_base_url() or "").strip().rstrip("/")
			except Exception:
				url = ""
	if url:
		try:
			p = urlparse(url)
			if p.scheme == "https" and p.hostname:
				host = p.hostname.lower()
				is_ip = (
					all(part.isdigit() for part in host.split(".")) and host.count(".") == 3
				) or host.startswith("[")
				if is_ip or host in ("127.0.0.1", "localhost"):
					return "", ""
				origin = f"https://{p.netloc}".rstrip("/")
				return origin, host
		except Exception:
			pass
	return "", ""


def get_webauthn_config() -> dict[str, Any]:
	"""Return {rp_id, rp_name, origins:list[str], require_device_bound_for_admins:bool}."""
	cfg = _load_cfg()
	wa = cfg.get("webauthn") if isinstance(cfg.get("webauthn"), dict) else {}

	env_rp_id = (os.getenv("WEBAUTHN_RP_ID") or "").strip()
	env_rp_name = (os.getenv("WEBAUTHN_RP_NAME") or "").strip()
	env_origins = (os.getenv("WEBAUTHN_ORIGINS") or "").strip()

	inferred_origin, inferred_rp_id = _get_host_base_url_parsed()

	configured_rp_id = wa.get("rp_id") or cfg.get("webauthn_rp_id")
	if env_rp_id:
		rp_id = env_rp_id
	elif configured_rp_id:
		rp_id = str(configured_rp_id).strip()
	elif inferred_rp_id and inferred_rp_id not in ("localhost", "127.0.0.1"):
		rp_id = inferred_rp_id
	else:
		rp_id = DEFAULT_RP_ID

	rp_name = env_rp_name or str(wa.get("rp_name") or DEFAULT_RP_NAME).strip() or DEFAULT_RP_NAME

	origins_raw = env_origins or wa.get("origins") or wa.get("allowed_origins")
	if origins_raw:
		if isinstance(origins_raw, str):
			origins = [o.strip() for o in origins_raw.split(",") if o.strip()]
		else:
			origins = [str(o).strip() for o in origins_raw if str(o).strip()]
	else:
		origins = list(DEFAULT_ORIGINS)

	# Auto-include inferred HTTPS origin from HOST_BASE_URL if not already present
	if (
		inferred_origin
		and inferred_origin.startswith("https://")
		and inferred_origin not in origins
	):
		origins.append(inferred_origin)

	return {
		"rp_id": rp_id,
		"rp_name": rp_name,
		"origins": origins,
		"require_device_bound_for_admins": bool(wa.get("require_device_bound_for_admins", False)),
	}


def get_rp_id_for_origin(origin: str) -> str:
	"""Resolve the RP ID for a specific origin during a ceremony.

	If the configured rp_id is a valid domain suffix of the origin, use it (allows subdomains).
	Otherwise, fall back to the origin's own hostname to prevent WebAuthn DOMException SecurityError.
	"""
	cfg = get_webauthn_config()
	configured_rp_id = cfg["rp_id"]
	o = (origin or "").strip().lower()
	if o.startswith("https://") or o.startswith("http://"):
		p = urlparse(o)
		host = p.hostname or ""
		if host:
			if configured_rp_id == host or host.endswith("." + configured_rp_id):
				return configured_rp_id
			return host
	return configured_rp_id


def origin_allowed(origin: str) -> bool:
	o = (origin or "").strip().rstrip("/")
	if not o:
		return False
	cfg = get_webauthn_config()
	origins = cfg["origins"]
	if "*" in origins:
		return True
	allowed = {x.rstrip("/") for x in origins}
	if o in allowed:
		return True
	inferred_origin, _ = _get_host_base_url_parsed()
	if inferred_origin and o == inferred_origin:
		return True
	if o.startswith("https://") or o.startswith("http://"):
		p = urlparse(o)
		host = (p.hostname or "").lower()
		rp_id = str(cfg.get("rp_id", "") or "").strip().lower()
		if host and rp_id and (host == rp_id or host.endswith("." + rp_id)):
			return True
	return False


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
