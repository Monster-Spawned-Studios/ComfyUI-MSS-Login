"""
IP filter: whitelist and blacklist from the same DB store as lockout (no file-based lists).
Whitelist/blacklist are stored in ip_whitelist and ip_blacklist; blacklist supports expiry (temporary) and permaban (NULL).
"""

import ipaddress
import time
from typing import Optional

from aiohttp import web


# Cookie name for device ID (set by client or server on first visit)
DEVICE_ID_COOKIE = "mss_login_device_id"
DEVICE_ID_HEADER = "X-Device-ID"


def get_ip(request: web.Request) -> str:
	"""Extract IP address from request headers or remote address.
	Prefers CF-Connecting-IP (Cloudflare Tunnel / cloudflared), then X-Forwarded-For,
	then X-Real-IP, then request.remote, so the real client IP is used behind proxies.
	"""
	ip = request.headers.get("CF-Connecting-IP")
	if not ip:
		forwarded = request.headers.get("X-Forwarded-For")
		if forwarded:
			ip = forwarded.split(",")[0].strip()
	if not ip:
		ip = request.headers.get("X-Real-IP")
	if not ip:
		ip = request.remote

	try:
		ip = str(ipaddress.ip_address(ip))
	except ValueError:
		ip = ""

	return ip


def get_device_id(request: web.Request) -> Optional[str]:
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
		security_json_path: Optional[str] = None,
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

	def is_allowed(self, ip: str, request: Optional[web.Request] = None) -> bool:
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
			elif ip in unlock_ips:
				return True
			elif request and get_device_id(request) in unlock_devices:
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

			if not self.is_allowed(ip, request):
				return await handle_access_denied(
					request,
					"Access denied: IP is either not whitelisted or is blacklisted.",
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
