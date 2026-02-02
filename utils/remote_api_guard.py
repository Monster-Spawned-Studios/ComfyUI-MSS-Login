# --- START OF FILE utils/remote_api_guard.py ---
"""
Remote API guard: require authentication for API requests from non-local clients.
If require_auth_for_remote_api is True, requests to protected API paths without
a Bearer token or JWT cookie are allowed only when the client IP is in the
local network (loopback, private ranges, Docker bridge). Otherwise return 401.
"""
import ipaddress
from aiohttp import web

from .ip_filter import get_ip


# Default CIDRs considered "local" (Docker-aware: 172.17.0.0/16 is default bridge)
DEFAULT_LOCAL_CIDRS = [
    "127.0.0.0/8",
    "::1/128",
    "10.0.0.0/8",
    "172.16.0.0/12",
    "192.168.0.0/16",
    "172.17.0.0/16",
]

# Path prefixes that are considered "protected API" (require auth when remote)
PROTECTED_API_PREFIXES = (
    "/prompt",
    "/api/prompt",
    "/api/queue",
    "/queue",
    "/api/",
)

# Paths or prefixes that are always allowed without auth (even if under /api/)
PUBLIC_API_PATHS = (
    "/login",
    "/register",
    "/logout",
    "/usgromana",
    "/usgromana-gallery",
    "/generate_token",
    "/static",
    "/assets",
    "/favicon",
    "/ws",
    "/",
)


def _is_protected_path(path: str) -> bool:
    if not path:
        return False
    for public in PUBLIC_API_PATHS:
        if path == public or path.startswith(public + "/"):
            return False
    for prefix in PROTECTED_API_PREFIXES:
        if path.startswith(prefix):
            return True
    return False


def _has_auth_token(request: web.Request) -> bool:
    """Return True if request has a Bearer token or jwt_token cookie."""
    auth = request.headers.get("Authorization", "")
    if auth.startswith("Bearer ") and len(auth) > 7:
        return True
    if request.cookies.get("jwt_token"):
        return True
    return False


def _is_local_ip(ip: str, local_cidrs: list) -> bool:
    if not ip:
        return False
    try:
        addr = ipaddress.ip_address(ip)
    except ValueError:
        return False
    for cidr in local_cidrs:
        try:
            net = ipaddress.ip_network(cidr, strict=False)
            if addr in net:
                return True
        except ValueError:
            continue
    return False


def create_remote_api_guard_middleware(
    require_auth_for_remote_api: bool = True,
    local_network_cidrs: list = None,
):
    """Create middleware that denies remote API access without auth when enabled."""
    cidrs = list(local_network_cidrs) if local_network_cidrs else DEFAULT_LOCAL_CIDRS

    @web.middleware
    async def middleware(request: web.Request, handler):
        if not require_auth_for_remote_api:
            return await handler(request)
        path = request.path
        if not _is_protected_path(path):
            return await handler(request)
        if _has_auth_token(request):
            return await handler(request)
        client_ip = get_ip(request)
        if _is_local_ip(client_ip, cidrs):
            return await handler(request)
        return web.json_response(
            {"error": "Authentication required for remote API access."},
            status=401,
        )

    return middleware
