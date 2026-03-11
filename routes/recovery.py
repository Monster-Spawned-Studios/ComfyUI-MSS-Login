# --- START OF FILE routes/recovery.py ---
"""
Recovery mode: locally-only endpoint to reset MFA when SECRET_KEY changed
and ephemeral-key migration was not possible. Only accessible when
RECOVERY_MODE=1 and client IP is in RECOVERY_MODE_HOSTS (default 127.0.0.1, ::1).
"""

import ipaddress
from aiohttp import web

from ..globals import routes, users_db, logger
from ..constants import RECOVERY_MODE, RECOVERY_MODE_HOSTS, experimental_mfa_enabled
from ..utils.ip_filter import get_ip


def _client_ip_allowed_for_recovery(client_ip: str) -> bool:
    """Return True if client_ip is in RECOVERY_MODE_HOSTS (exact match or CIDR)."""
    if not client_ip:
        return False
    try:
        addr = ipaddress.ip_address(client_ip)
    except ValueError:
        return False
    for allowed in RECOVERY_MODE_HOSTS:
        allowed = allowed.strip()
        if not allowed:
            continue
        try:
            if "/" in allowed:
                net = ipaddress.ip_network(allowed, strict=False)
                if addr in net:
                    return True
            else:
                if ipaddress.ip_address(allowed) == addr:
                    return True
        except ValueError:
            continue
    return False


@routes.post("/api/mss-login/recovery/reset-mfa")
async def post_recovery_reset_mfa(request: web.Request) -> web.Response:
    """
    Reset MFA for all users. Only allowed when RECOVERY_MODE is enabled
    and request comes from an allowed host (RECOVERY_MODE_HOST / RECOVRY_MODE_HOST).
    """
    if not experimental_mfa_enabled():
        return web.json_response(
            {
                "error": "MFA is an experimental feature. Enable experimental_features and experimental.mfa to use this endpoint."
            },
            status=403,
        )
    if not RECOVERY_MODE:
        return web.json_response(
            {
                "error": "Recovery mode is not enabled. Set RECOVERY_MODE=1 to use this endpoint."
            },
            status=403,
        )
    client_ip = get_ip(request)
    if not _client_ip_allowed_for_recovery(client_ip):
        logger.error(
            f"[mss-login] Recovery reset-mfa rejected: client IP {client_ip!r} not in allowed hosts."
        )
        return web.json_response(
            {
                "error": "Recovery endpoint is only accessible from allowed hosts (e.g. localhost)."
            },
            status=403,
        )
    try:
        count = users_db.reset_mfa_for_all_users()
        logger.info(
            f"[mss-login] Recovery: MFA reset for {count} user(s) from IP {client_ip!r}."
        )
        return web.json_response(
            {
                "message": f"MFA reset for {count} user(s). Users may log in with password and re-enroll MFA."
            }
        )
    except Exception as e:
        logger.error(f"[mss-login] Recovery reset-mfa failed: {e}")
        return web.json_response({"error": "Failed to reset MFA."}, status=500)
