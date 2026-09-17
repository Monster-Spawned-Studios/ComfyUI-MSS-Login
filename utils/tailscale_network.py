# --- START OF FILE utils/tailscale_network.py ---
"""
Network detection and security verification for Tailscale and local private networks.

Enforces strict multi-layer IP matching:
1. TCP socket peer (request.remote) MUST be in trusted Tailscale or local CIDRs.
2. If connecting through a local reverse proxy or Cloudflare tunnel, the originating
   client IP (X-Forwarded-For / CF-Connecting-IP) must ALSO be in trusted CIDRs.
3. External / public internet IPs are ALWAYS classified as remote and CANNOT bypass auth.
"""

import ipaddress
from typing import Any, Optional

from aiohttp import web

try:
	from ..constants import (
		DEFAULT_LOCAL_PRIVATE_CIDRS,
		LOCAL_NETWORK_CIDRS,
		TAILSCALE_CIDRS,
	)
except (ImportError, ValueError):
	try:
		from constants import (
			DEFAULT_LOCAL_PRIVATE_CIDRS,
			LOCAL_NETWORK_CIDRS,
			TAILSCALE_CIDRS,
		)
	except (ImportError, ValueError):
		from mss_login.constants import (
			DEFAULT_LOCAL_PRIVATE_CIDRS,
			LOCAL_NETWORK_CIDRS,
			TAILSCALE_CIDRS,
		)

try:
	from .ip_filter import get_ip
except (ImportError, ValueError):
	try:
		from utils.ip_filter import get_ip
	except (ImportError, ValueError):
		from mss_login.utils.ip_filter import get_ip


def _parse_ip(ip_str: str) -> Optional[ipaddress.IPv4Address | ipaddress.IPv6Address]:
	"""Safely parse an IP address string. Return None if invalid."""
	if not ip_str or not isinstance(ip_str, str):
		return None
	ip_str = ip_str.strip()
	try:
		return ipaddress.ip_address(ip_str)
	except ValueError:
		return None


def is_ip_in_cidrs(ip_str: str, cidr_list: list[str]) -> bool:
	"""Return True if ip_str matches any of the CIDRs or IP entries in cidr_list."""
	addr = _parse_ip(ip_str)
	if addr is None:
		return False
	for cidr in cidr_list:
		if not cidr or not isinstance(cidr, str):
			continue
		cidr = cidr.strip()
		try:
			net = ipaddress.ip_network(cidr, strict=False)
			if addr in net:
				return True
		except ValueError:
			continue
	return False


def get_all_trusted_cidrs(custom_cidrs: Optional[list[str]] = None) -> list[str]:
	"""Return the combined list of default Tailscale, local private, and custom CIDRs."""
	combined = list(TAILSCALE_CIDRS) + list(DEFAULT_LOCAL_PRIVATE_CIDRS)
	if custom_cidrs:
		combined.extend(custom_cidrs)
	elif LOCAL_NETWORK_CIDRS:
		combined.extend(LOCAL_NETWORK_CIDRS)
	return combined


def classify_ip(ip_str: str, custom_cidrs: Optional[list[str]] = None) -> str:
	"""
	Classify an IP address as 'tailscale', 'local', or 'remote'.
	"""
	addr = _parse_ip(ip_str)
	if addr is None:
		return "remote"

	# Check Tailscale first (100.64.0.0/10 or fd7a:115c:a1e0::/48)
	if is_ip_in_cidrs(ip_str, TAILSCALE_CIDRS):
		return "tailscale"

	# Check local private ranges & custom CIDRs
	local_cidrs = list(DEFAULT_LOCAL_PRIVATE_CIDRS)
	if custom_cidrs:
		local_cidrs.extend(custom_cidrs)
	elif LOCAL_NETWORK_CIDRS:
		local_cidrs.extend(LOCAL_NETWORK_CIDRS)

	if is_ip_in_cidrs(ip_str, local_cidrs):
		return "local"

	return "remote"


def is_trusted_tailscale_or_local(
	request: web.Request, custom_cidrs: Optional[list[str]] = None
) -> bool:
	"""
	Strictly verify that an incoming request originates from Tailscale or a local network.

	Security validation rules:
	1. Direct socket peer (request.remote) MUST be in trusted CIDRs.
	   If an external attacker connects directly from a public IP, request.remote
	   is public; all proxy headers are ignored and the request is REJECTED as remote.
	2. If request.remote is local (e.g. localhost reverse proxy or Cloudflare tunnel):
	   - If CF-Connecting-IP is present: it MUST be in trusted CIDRs.
	   - If X-Forwarded-For is present: the first client IP in the chain MUST be in trusted CIDRs.
	"""
	peer_ip = getattr(request, "remote", None)
	if not peer_ip:
		return False

	all_trusted = get_all_trusted_cidrs(custom_cidrs)

	# Rule 1: Peer socket must be in trusted CIDRs
	if not is_ip_in_cidrs(peer_ip, all_trusted):
		return False

	# Rule 2: Inspect proxy headers if peer is local/proxy
	# Check Cloudflare real client IP if present
	cf_ip = request.headers.get("CF-Connecting-IP")
	if cf_ip and not is_ip_in_cidrs(cf_ip, all_trusted):
		return False

	# Check X-Forwarded-For first hop if present
	forwarded = request.headers.get("X-Forwarded-For")
	if forwarded:
		first_ip = forwarded.split(",")[0].strip()
		if first_ip and not is_ip_in_cidrs(first_ip, all_trusted):
			return False

	# Check X-Real-IP if present
	real_ip = request.headers.get("X-Real-IP")
	if real_ip and not is_ip_in_cidrs(real_ip, all_trusted):
		return False

	return True


def detect_network_info(
	request: web.Request, custom_cidrs: Optional[list[str]] = None
) -> dict[str, Any]:
	"""Return diagnostic network info for the current request."""
	trusted = is_trusted_tailscale_or_local(request, custom_cidrs)
	effective_ip = get_ip(request) or getattr(request, "remote", "")
	net_type = classify_ip(effective_ip, custom_cidrs) if trusted else "remote"

	return {
		"peer_ip": getattr(request, "remote", ""),
		"client_ip": effective_ip,
		"is_trusted": trusted,
		"network_type": net_type,
	}


def user_can_login_locally_without_auth(
	username: str, users_db: Any, groups_config: Optional[dict] = None
) -> bool:
	"""
	Return True if username exists and possesses the 'can_login_locally_without_auth' permission.
	Default allowed roles: owner and admin.
	"""
	if not username or not users_db:
		return False

	try:
		user_id, rec = users_db.get_user(username)
	except Exception:
		return False

	if not user_id or not rec:
		return False

	# Owner always has this permission
	owner_username = getattr(users_db, "get_owner_username", lambda: None)()
	if owner_username and username == owner_username:
		return True

	user_groups = [str(g).lower() for g in rec.get("groups", [])]
	if "owner" in user_groups:
		return True

	if groups_config and isinstance(groups_config, dict):
		for g in user_groups:
			perms = groups_config.get(g, {})
			if isinstance(perms, dict) and perms.get("can_login_locally_without_auth"):
				return True

	# Fallback check if user is marked admin or has admin group
	if rec.get("admin") or "admin" in user_groups:
		return True

	return False
