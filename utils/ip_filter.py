import os
import hashlib
import ipaddress
import time
from pathlib import Path
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
        # Validate and normalize the IP address
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


class IPFilter:
    def __init__(
        self,
        whitelist_file: str | Path,
        blacklist_file: str | Path,
        security_json_path: Optional[str | Path] = None,
        lockout_store=None,
    ):
        self.whitelist_file = whitelist_file
        self.blacklist_file = blacklist_file
        self.security_json_path = security_json_path
        self.lockout_store = lockout_store

        self._whitelist_hash = None
        self._blacklist_hash = None

        self.whitelist = []
        self.blacklist = []

        self.load_filter_list()

    @staticmethod
    def calculate_file_hash(filter_file) -> str:
        """Calculate the SHA256 hash of the filter IP list file."""
        if os.path.exists(filter_file):
            with open(filter_file, "rb") as f:
                file_data = f.read()
                return hashlib.sha256(file_data).hexdigest()
        return ""

    def load_filter_list(self) -> tuple[list, list]:
        """Load whitelist and blacklist IP lists from files. Supports both single IPs and CIDR ranges."""

        def load_ip_list(
            file_path: str | Path,
            current_hash: str,
            hash_attribute: str,
            list_attribute: str,
        ) -> list:
            new_hash = self.calculate_file_hash(file_path)
            if new_hash != current_hash:
                ip_list = []
                if os.path.exists(file_path):
                    with open(file_path, "r") as f:
                        for line in f:
                            ip = line.strip()
                            if ip and not ip.startswith("#"):  # Skip comments
                                try:
                                    # Try as single IP first
                                    ip_list.append(ipaddress.ip_address(ip))
                                except ValueError:
                                    try:
                                        # Try as CIDR network
                                        ip_list.append(
                                            ipaddress.ip_network(ip, strict=False)
                                        )
                                    except ValueError:
                                        # Invalid IP format, skip
                                        continue
                setattr(self, hash_attribute, new_hash)
                setattr(self, list_attribute, ip_list)
                return ip_list
            else:
                # Hash unchanged, return cached list
                return getattr(self, list_attribute)

        self.whitelist = load_ip_list(
            self.whitelist_file, self._whitelist_hash, "_whitelist_hash", "whitelist"
        )
        self.blacklist = load_ip_list(
            self.blacklist_file, self._blacklist_hash, "_blacklist_hash", "blacklist"
        )

        return self.whitelist, self.blacklist

    def is_allowed(self, ip: str, request: Optional[web.Request] = None) -> bool:
        """
        Checks if the given IP (and optional request for device ID) is allowed.
        Order: 1) security.json unlock_ips / unlock_devices -> allow
               2) IP in blacklist (file + DB) or device in locked_devices -> deny
               3) Existing whitelist/blacklist file logic.
        """
        self.load_filter_list()

        try:
            ip_addr = ipaddress.ip_address(ip)
        except ValueError:
            return False

        # 1) security.json unlock overrides
        if self.security_json_path:
            from .security_config import (
                get_unlock_ips,
                get_unlock_devices,
                is_lockout_disabled_until,
            )
            unlock_ips = get_unlock_ips(self.security_json_path)
            unlock_devices = get_unlock_devices(self.security_json_path)
            disable_until = is_lockout_disabled_until(self.security_json_path)
            if disable_until is not None and time.time() < disable_until:
                pass  # Skip lockout checks below
            elif ip in unlock_ips:
                return True
            elif request and get_device_id(request) and get_device_id(request) in unlock_devices:
                return True

        # 2) Lockout: DB blacklist and locked devices (unless disabled above)
        if self.security_json_path:
            disable_until = is_lockout_disabled_until(self.security_json_path)
            if disable_until is None or time.time() >= disable_until:
                if self.lockout_store:
                    db_blacklist = self.lockout_store.get_blacklisted_ips()
                    if ip in db_blacklist:
                        return False
                    if request:
                        did = get_device_id(request)
                        if did and did in self.lockout_store.get_locked_devices():
                            return False

        # 3) File blacklist (merge with DB for backward compatibility)
        file_blacklist = self.blacklist

        # Check whitelist (if not empty, IP must be whitelisted)
        if self.whitelist:
            for entry in self.whitelist:
                if isinstance(entry, (ipaddress.IPv4Network, ipaddress.IPv6Network)):
                    # CIDR range check
                    if ip_addr in entry:
                        return True
                else:
                    # Single IP check
                    if ip_addr == entry:
                        return True
            return False

        # Check blacklist (file + DB; if whitelist is empty)
        for entry in file_blacklist:
            if isinstance(entry, (ipaddress.IPv4Network, ipaddress.IPv6Network)):
                # CIDR range check
                if ip_addr in entry:
                    return False
            else:
                # Single IP check
                if ip_addr == entry:
                    return False

        return True

    def add_to_blacklist(self, ip: str) -> None:
        """Add a given IP to the blacklist file."""
        try:
            ip_obj = ipaddress.ip_address(ip)
        except ValueError:
            return

        # Check if already in blacklist
        ip_str = str(ip_obj)
        for entry in self.blacklist:
            if str(entry) == ip_str:
                return  # Already in blacklist

        # Add to in-memory list
        self.blacklist.append(ip_obj)

        # Append to file
        try:
            # Check if file exists and has content
            file_exists = os.path.exists(self.blacklist_file)
            needs_newline = False

            if file_exists:
                with open(self.blacklist_file, "r") as f:
                    content = f.read()
                    if content and not content.endswith("\n"):
                        needs_newline = True

            with open(self.blacklist_file, "a") as file:
                if needs_newline:
                    file.write("\n")
                file.write(ip_str + "\n")

            # Update hash after writing
            self._blacklist_hash = self.calculate_file_hash(self.blacklist_file)
        except Exception as e:
            # Log error but don't fail - in-memory list is updated
            print(f"[mss-login] Warning: Failed to write IP to blacklist file: {e}")

    def create_ip_filter_middleware(self) -> web.middleware:
        """Create the middleware for managing blacklisted and whitelisted ip."""

        @web.middleware
        async def ip_filter_middleware(request: web.Request, handler) -> web.Response:
            ip = get_ip(request)

            if not self.is_allowed(ip, request):
                return await handle_access_denied(
                    request,
                    "Access denied: IP is either not whitelisted or is blacklisted.",
                )

            return await handler(request)

        async def handle_access_denied(
            request: web.Request, message: str
        ) -> web.Response:
            """Handle denied access cases."""
            accept_header = request.headers.get("Accept", "")
            if "text/html" in accept_header:
                return web.HTTPForbidden(reason=message)
            else:
                return web.json_response({"error": message}, status=403)

        return ip_filter_middleware
