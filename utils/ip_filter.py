"""
IP filter: whitelist and blacklist from the same DB store as lockout (no file-based lists).
Whitelist/blacklist are stored in ip_whitelist and ip_blacklist; blacklist supports expiry (temporary) and permaban (NULL).

Client IP resolution (AUDIT-013 / NEW-A): forwarding headers are trusted ONLY when
request.remote is in trusted_proxies (empty by default). Proxy-hop trust is separate
from local_network_cidrs client trust.
"""

import ipaddress
import time
from typing import Optional

from aiohttp import web

# Cookie name for device ID (set by client or server on first visit)
DEVICE_ID_COOKIE = "mss_login_device_id"
DEVICE_ID_HEADER = "X-Device-ID"


def _is_cloudflare_proxy_enabled() -> bool:
	"""Check if cloudflare_proxy is enabled in config (lazy import to avoid circular deps)."""
	try:
		from ..constants import CLOUDFLARE_PROXY

		return bool(CLOUDFLARE_PROXY)
	except Exception:
		return False


def _get_trusted_proxies() -> list[str]:
	"""Return configured trusted proxy CIDRs/IPs (empty default = fail closed on headers)."""
	try:
		from ..constants import TRUSTED_PROXIES

		return list(TRUSTED_PROXIES or [])
	except Exception:
		return []


def is_trusted_proxy_peer(peer_ip: str, trusted_proxies: list[str] | None = None) -> bool:
	"""Return True if peer_ip matches an entry in trusted_proxies (IP or CIDR)."""
	if not peer_ip:
		return False
	cidrs = trusted_proxies if trusted_proxies is not None else _get_trusted_proxies()
	if not cidrs:
		return False
	try:
		addr = ipaddress.ip_address(peer_ip.strip())
	except ValueError:
		return False
	for entry in cidrs:
		if not entry or not isinstance(entry, str):
			continue
		entry = entry.strip()
		try:
			net = ipaddress.ip_network(entry, strict=False)
			if addr in net:
				return True
		except ValueError:
			try:
				if addr == ipaddress.ip_address(entry):
					return True
			except ValueError:
				continue
	return False


def _normalize_ip(ip: str | None) -> str:
	if not ip:
		return ""
	try:
		return str(ipaddress.ip_address(ip.strip()))
	except ValueError:
		return ""


def get_ip(request: web.Request, trusted_proxies: list[str] | None = None) -> str:
	"""Extract the real client IP. Fail closed on spoofable headers.

	Rules:
	- If request.remote is NOT in trusted_proxies: return peer only (ignore all
	  forwarding headers). Empty trusted_proxies (default) means headers are never trusted.
	- If peer IS trusted and cloudflare_proxy: prefer CF-Connecting-IP.
	- Else if peer is trusted: walk X-Forwarded-For right-to-left, skipping trusted hops;
	  take the first untrusted hop (true client).
	- Else fall back to request.remote.
	- Invalid / undeterminable → "" (callers that gate auth must fail closed).
	"""
	peer = _normalize_ip(getattr(request, "remote", None) or "")
	cidrs = trusted_proxies if trusted_proxies is not None else _get_trusted_proxies()
	peer_trusted = is_trusted_proxy_peer(peer, cidrs) if peer else False

	if not peer_trusted:
		return peer

	# Peer is a trusted proxy — may read forwarding headers.
	cf_enabled = _is_cloudflare_proxy_enabled()
	if cf_enabled:
		for hdr in ("CF-Connecting-IP", "True-Client-IP", "Cf-Pseudo-IPv4"):
			candidate = _normalize_ip(request.headers.get(hdr))
			if candidate:
				return candidate

	forwarded = request.headers.get("X-Forwarded-For")
	if forwarded:
		# Right-to-left: skip trusted hops; first untrusted is the client.
		hops = [h.strip() for h in forwarded.split(",") if h.strip()]
		for hop in reversed(hops):
			norm = _normalize_ip(hop)
			if not norm:
				continue
			if is_trusted_proxy_peer(norm, cidrs):
				continue
			return norm
		# All hops trusted — use leftmost (original client) if valid, else peer
		if hops:
			left = _normalize_ip(hops[0])
			if left:
				return left

	real_ip = _normalize_ip(request.headers.get("X-Real-IP"))
	if real_ip:
		return real_ip

	return peer


def is_https_request(request: web.Request) -> bool:
	"""Return True if the client is connecting via HTTPS, inferred from proxy headers or request scheme.

	Checks (in priority order):
	1. request.scheme == "https" (set by force_https middleware when X-Forwarded-Proto matches).
	2. X-Forwarded-Proto: https (nginx, Apache, most reverse proxies).
	3. CF-Visitor: {"scheme":"https"} (Cloudflare Tunnel / cloudflared).
	"""
	if getattr(request, "scheme", "") == "https":
		return True
	if (request.headers.get("X-Forwarded-Proto") or "").lower() == "https":
		return True
	cf_visitor = request.headers.get("CF-Visitor") or ""
	if '"scheme":"https"' in cf_visitor or '"scheme": "https"' in cf_visitor:
		return True
	return False


def get_device_id(request: web.Request) -> str | None:
	"""Extract device identifier from request (X-Device-ID header or cookie). Used for lockout across IP changes."""
	did = request.headers.get(DEVICE_ID_HEADER)
	if did:
		return (did or "").strip() or None
	did = request.cookies.get(DEVICE_ID_COOKIE)
	if did:
		return (did or "").strip() or None
	return None


def _parse_entry(entry: str):
	"""Parse a whitelist/blacklist entry string into ip_address or ip_network. Return None if invalid."""
	entry = (entry or "").strip()
	if not entry or entry.startswith("#"):
		return None
	try:
		return ipaddress.ip_address(entry)
	except ValueError:
		try:
			return ipaddress.ip_network(entry, strict=False)
		except ValueError:
			return None


class IPFilter:
	"""IP allow/deny using whitelist and blacklist from the lockout store (DB)."""

	def __init__(
		self,
		lockout_store,
		blacklist_expiry_hours: float = 24,
		security_json_path: str | None = None,
	):
		self.lockout_store = lockout_store
		self.blacklist_expiry_hours = blacklist_expiry_hours
		self.security_json_path = security_json_path
		self.whitelist: list = []  # list of ip_address or ip_network
		self.blacklist: set = set()  # set of IP strings (from DB)
		self.load_filter_list()

	def load_filter_list(self) -> tuple[list, set]:
		"""Load whitelist and blacklist from the store. Whitelist entries can be IP or CIDR."""
		raw_whitelist = self.lockout_store.get_whitelist()
		self.whitelist = []
		for entry in raw_whitelist:
			parsed = _parse_entry(entry)
			if parsed is not None:
				self.whitelist.append(parsed)
		self.blacklist = self.lockout_store.get_blacklisted_ips()
		return self.whitelist, self.blacklist

	def is_whitelisted(self, ip: str) -> bool:
		"""Return True if the IP is in the whitelist (after loading from store)."""
		try:
			ip_addr = ipaddress.ip_address(ip)
		except ValueError:
			return False
		for entry in self.whitelist:
			if isinstance(entry, (ipaddress.IPv4Network, ipaddress.IPv6Network)):
				if ip_addr in entry:
					return True
			elif ip_addr == entry:
				return True
		return False

	def is_allowed(self, ip: str, request: web.Request | None = None) -> bool:
		"""
		Check if the given IP (and optional request for device ID) is allowed.
		Order: 1) security.json unlock_ips / unlock_devices -> allow
		       2) IP in DB blacklist or device in locked_devices -> deny
		       3) Whitelist/blacklist from DB (whitelist: allow only if matched; else deny if blacklist matched).
		"""
		self.load_filter_list()

		try:
			ip_addr = ipaddress.ip_address(ip)
		except ValueError:
			return False

		# 1) security.json unlock overrides
		if self.security_json_path:
			from .security_config import (
				get_unlock_devices,
				get_unlock_ips,
				is_lockout_disabled_until,
			)

			unlock_ips = get_unlock_ips(self.security_json_path)
			unlock_devices = get_unlock_devices(self.security_json_path)
			disable_until = is_lockout_disabled_until(self.security_json_path)
			if disable_until is not None and time.time() < disable_until:
				pass
			elif ip in unlock_ips or request and get_device_id(request) in unlock_devices:
				return True

		# 2) Lockout: DB blacklist and locked devices (unless disabled above)
		if self.security_json_path:
			disable_until = is_lockout_disabled_until(self.security_json_path)
			if disable_until is None or time.time() >= disable_until:
				if self.lockout_store:
					if ip in self.blacklist:
						return False
					if request:
						did = get_device_id(request)
						if did and did in self.lockout_store.get_locked_devices():
							return False

		# 3) Whitelist (if not empty, IP must be whitelisted)
		if self.whitelist:
			for entry in self.whitelist:
				if isinstance(entry, (ipaddress.IPv4Network, ipaddress.IPv6Network)):
					if ip_addr in entry:
						return True
				else:
					if ip_addr == entry:
						return True
			return False

		# 4) Blacklist (if whitelist empty, deny if in blacklist)
		if ip in self.blacklist:
			return False

		return True

	def add_to_blacklist(self, ip: str) -> None:
		"""Add IP to the blacklist in the store with temporary expiry (blacklist_expiry_hours)."""
		try:
			ipaddress.ip_address(ip)
		except ValueError:
			return
		import time as _time

		expires_at = None
		if self.blacklist_expiry_hours is not None and self.blacklist_expiry_hours >= 0:
			expires_at = int(_time.time()) + int(self.blacklist_expiry_hours * 3600)
		self.lockout_store.add_blacklist_entry(ip, expires_at=expires_at)

	def create_ip_filter_middleware(self) -> web.middleware:
		"""Create the middleware for managing blacklisted and whitelisted IP."""

		@web.middleware
		async def ip_filter_middleware(request: web.Request, handler) -> web.Response:
			ip = get_ip(request)

			# Never deny a trusted proxy peer itself (hop trust ≠ client trust).
			peer = getattr(request, "remote", "") or ""
			if is_trusted_proxy_peer(peer):
				return await handler(request)

			if not self.is_allowed(ip, request):
				return await handle_access_denied(
					request, "Access denied: IP is either not whitelisted or is blacklisted."
				)

			return await handler(request)

		async def handle_access_denied(request: web.Request, message: str) -> web.Response:
			"""Redirect or 403 HTML for browsers; return JSON only for explicit API clients."""
			accept_header = (request.headers.get("Accept") or "").strip().lower()
			wants_html = "text/html" in accept_header or not accept_header or accept_header == "*/*"
			if wants_html:
				return web.HTTPForbidden(reason=message)
			return web.json_response({"error": message}, status=403)

		return ip_filter_middleware
