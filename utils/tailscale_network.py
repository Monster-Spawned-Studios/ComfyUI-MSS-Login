# --- START OF FILE utils/tailscale_network.py ---
"""
Network detection and security verification for Tailscale and local private networks.

NEW-A / AUDIT-013 trust model:
- Client trust (passwordless local login): TAILSCALE_CIDRS + explicit non-public
  local_network_cidrs only. DEFAULT_LOCAL_PRIVATE_CIDRS never grants client trust.
- Proxy-hop trust: trusted_proxies only (see ip_filter.get_ip / is_trusted_proxy_peer).
- Resolved client IP comes from get_ip(); undeterminable client → fail closed.
"""

import ipaddress
import logging
from typing import Any, Optional

from aiohttp import web

try:
	from ..constants import LOCAL_NETWORK_CIDRS, TAILSCALE_CIDRS
except (ImportError, ValueError):
	try:
		from constants import LOCAL_NETWORK_CIDRS, TAILSCALE_CIDRS
	except (ImportError, ValueError):
		from mss_login.constants import LOCAL_NETWORK_CIDRS, TAILSCALE_CIDRS

try:
	from .ip_filter import get_ip, is_trusted_proxy_peer
except (ImportError, ValueError):
	try:
		from utils.ip_filter import get_ip, is_trusted_proxy_peer
	except (ImportError, ValueError):
		from mss_login.utils.ip_filter import get_ip, is_trusted_proxy_peer

_log = logging.getLogger("mss_login.tailscale_network")


def _parse_ip(ip_str: str) -> ipaddress.IPv4Address | ipaddress.IPv6Address | None:
	"""Safely parse an IP address string. Return None if invalid."""
	if not ip_str or not isinstance(ip_str, str):
		return None
	ip_str = ip_str.strip()
	try:
		return ipaddress.ip_address(ip_str)
	except ValueError:
		return None


def _is_public_network(cidr: str) -> bool:
	"""Return True if the CIDR/IP is globally routable (should not be client-trusted)."""
	try:
		net = ipaddress.ip_network(cidr.strip(), strict=False)
	except ValueError:
		return True  # unparseable → treat as public / ignore
	# ip_network.is_global is False for private, loopback, link-local, ULA, CGNAT, etc.
	return bool(net.is_global)


def filter_client_trust_cidrs(
	custom_cidrs: list[str] | None = None, *, warn: bool = True
) -> list[str]:
	"""
	Return explicit local_network_cidrs entries that are safe for client trust.
	Public / global CIDRs are logged and ignored. Does NOT include DEFAULT_LOCAL_PRIVATE_CIDRS.
	"""
	raw: list[str] = []
	if custom_cidrs is not None:
		raw = list(custom_cidrs)
	elif LOCAL_NETWORK_CIDRS:
		raw = list(LOCAL_NETWORK_CIDRS)

	out: list[str] = []
	for entry in raw:
		if not entry or not isinstance(entry, str):
			continue
		entry = entry.strip()
		if not entry:
			continue
		if _is_public_network(entry):
			if warn:
				_log.warning(
					"[MSS-Login] Ignoring public CIDR in local_network_cidrs (not trusted for local login): %s",
					entry,
				)
			continue
		out.append(entry)
	return out


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


def get_all_trusted_cidrs(custom_cidrs: list[str] | None = None) -> list[str]:
	"""
	Client-trust CIDRs: Tailscale + explicit non-public local_network_cidrs only.
	DEFAULT_LOCAL_PRIVATE_CIDRS is intentionally excluded (NEW-A).
	Empty local_network_cidrs → Tailscale-only.
	"""
	return list(TAILSCALE_CIDRS) + filter_client_trust_cidrs(custom_cidrs)


def classify_ip(ip_str: str, custom_cidrs: list[str] | None = None) -> str:
	"""
	Classify an IP for auth purposes.

	Returns:
	  - 'tailscale' for TAILSCALE_CIDRS
	  - 'local' only for an explicit local_network_cidrs match
	  - 'private-untrusted' for RFC1918/loopback/etc. that are NOT explicitly listed
	  - 'remote' for public / unknown
	"""
	addr = _parse_ip(ip_str)
	if addr is None:
		return "remote"

	if is_ip_in_cidrs(ip_str, TAILSCALE_CIDRS):
		return "tailscale"

	explicit = filter_client_trust_cidrs(custom_cidrs, warn=False)
	if is_ip_in_cidrs(ip_str, explicit):
		return "local"

	# Globally routable → remote. Everything else unlisted (RFC1918, loopback,
	# link-local, ULA, CGNAT outside Tailscale, docs ranges) → private-untrusted.
	if getattr(addr, "is_global", False):
		return "remote"
	return "private-untrusted"


def is_trusted_tailscale_or_local(
	request: web.Request, custom_cidrs: list[str] | None = None
) -> bool:
	"""
	Strictly verify that an incoming request's *client* IP is Tailscale or explicitly listed LAN.

	- Resolve client via get_ip() (trusted_proxies gate on forwarding headers).
	- If client IP is empty/undeterminable → fail closed (False).
	- Client must be in TAILSCALE_CIDRS or explicit non-public local_network_cidrs.
	- Unlisted private / loopback / Docker bridge clients are NOT trusted.
	- Cloudflare tunnel with public CF-Connecting-IP never passes (public client).
	"""
	client_ip = get_ip(request)
	if not client_ip:
		return False

	trusted = get_all_trusted_cidrs(custom_cidrs)
	return is_ip_in_cidrs(client_ip, trusted)


def detect_network_info(
	request: web.Request, custom_cidrs: list[str] | None = None
) -> dict[str, Any]:
	"""Return diagnostic network info for the current request."""
	trusted = is_trusted_tailscale_or_local(request, custom_cidrs)
	effective_ip = get_ip(request) or ""
	net_type = classify_ip(effective_ip, custom_cidrs) if effective_ip else "remote"
	# Auth view: private-untrusted counts as remote for is_local
	is_local_for_auth = net_type in ("tailscale", "local") and trusted

	return {
		"peer_ip": getattr(request, "remote", "") or "",
		"client_ip": effective_ip,
		"is_trusted": trusted,
		"is_local": is_local_for_auth,
		"network_type": net_type
		if trusted
		else ("private-untrusted" if net_type == "private-untrusted" else "remote"),
		"peer_is_trusted_proxy": is_trusted_proxy_peer(getattr(request, "remote", "") or ""),
	}


def user_can_login_locally_without_auth(
	username: str, users_db: Any, groups_config: dict | None = None
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
