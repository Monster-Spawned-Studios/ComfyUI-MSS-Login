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
    "192.168.1.0/24",
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
        has_token = _has_auth_token(request)
        client_ip = get_ip(request)
        is_local = _is_local_ip(client_ip, cidrs)
        # #region agent log
        try:
            from ..constants import DEBUG_MODE, DEBUG_LOG_PATH, CURSOR_DEBUG_LOG
            if DEBUG_MODE:
                import json, os, time
                os.makedirs(os.path.dirname(DEBUG_LOG_PATH), exist_ok=True)
                with open(DEBUG_LOG_PATH, "a", encoding="utf-8") as f:
                    f.write(json.dumps({"location": "remote_api_guard", "message": "check", "data": {"path": path, "has_token": has_token, "client_ip_last": client_ip.split(".")[-1] if client_ip and "." in client_ip else "n/a", "is_local": is_local}, "timestamp": int(time.time() * 1000), "hypothesisId": "A"}) + "\n")
            os.makedirs(os.path.dirname(CURSOR_DEBUG_LOG), exist_ok=True)
            with open(CURSOR_DEBUG_LOG, "a", encoding="utf-8") as f:
                f.write(json.dumps({"sessionId": "debug-session", "runId": "run1", "hypothesisId": "JWT-A", "location": "remote_api_guard.py", "message": "guard_check", "data": {"path": path, "has_token": has_token, "is_local": is_local}, "timestamp": int(time.time() * 1000)}) + "\n")
        except Exception:
            pass
        # #endregion
        if has_token:
            return await handler(request)
        if is_local:
            return await handler(request)
        # #region agent log
        try:
            from ..constants import DEBUG_MODE, DEBUG_LOG_PATH, CURSOR_DEBUG_LOG
            if DEBUG_MODE:
                import json, os, time
                with open(DEBUG_LOG_PATH, "a", encoding="utf-8") as f:
                    f.write(json.dumps({"location": "remote_api_guard", "message": "blocked_401", "data": {"path": path, "client_ip_last": client_ip.split(".")[-1] if client_ip and "." in client_ip else "n/a"}, "timestamp": int(time.time() * 1000), "hypothesisId": "A"}) + "\n")
            with open(CURSOR_DEBUG_LOG, "a", encoding="utf-8") as f:
                f.write(json.dumps({"sessionId": "debug-session", "runId": "run1", "hypothesisId": "JWT-A", "location": "remote_api_guard.py", "message": "blocked_401", "data": {"path": path}, "timestamp": int(time.time() * 1000)}) + "\n")
        except Exception:
            pass
        # #endregion
        body = {"error": "Authentication required for remote API access."}
        try:
            from ..constants import DEBUG_MODE
            if DEBUG_MODE:
                body["debug"] = "DEBUG_MODE=1: see logs/debug.log or server logs."
        except Exception:
            pass
        return web.json_response(body, status=401)

    return middleware
