# --- START OF FILE routes/mfa.py ---
"""MFA: setup, verify-setup, verify (login second step)."""

from aiohttp import web
from ..globals import routes, users_db, jwt_auth, access_control
from ..constants import EXPERIMENTAL_FEATURES, SESSION_TOKEN_STORE_CONFIG, MFA_DISABLED
from ..utils.session_token_store import get_session_token_store
from ..utils.user_console_log import append as user_console_append
from ..utils.mfa_temp_store import (
    consume_mfa_temp_token,
    get_username_for_mfa_temp_token,
)
from ..utils.ntfy_notifier import send_notification
from ..utils.ip_filter import get_ip


async def _get_request_data(request: web.Request) -> dict:
    """Get POST body as dict (JSON or form)."""
    data = request.get("_sanitized_data") or {}
    if request.can_read_body:
        try:
            if "application/json" in (request.content_type or ""):
                body = await request.json()
                if isinstance(body, dict):
                    data = {**data, **body}
        except Exception:
            pass
    return data


def _resolve_username_from_token_and_data(
    request: web.Request, data: dict
) -> tuple[str | None, str | None]:
    """Return (username, error_message). username from JWT or from body mfa_temp_token."""
    token = jwt_auth.get_token_from_request(request)
    if token:
        try:
            payload = jwt_auth.decode_access_token(token)
            username = payload.get("username")
            if username and users_db.get_user(username)[0]:
                return username, None
        except Exception:
            pass
    mfa_temp = (data.get("mfa_temp_token") or "").strip()
    if mfa_temp:
        username = get_username_for_mfa_temp_token(mfa_temp)
        if username:
            return username, None
        return None, "Invalid or expired MFA token. Please log in again."
    return None, "Authentication required (JWT or mfa_temp_token)."


@routes.post("/mss-login/api/mfa/setup")
async def api_mfa_setup(request: web.Request) -> web.Response:
    """Start MFA setup. Requires JWT (logged-in user) or mfa_temp_token (from login when mfa_setup_required).
    Returns provisioning_uri (for QR) and backup_code (show once)."""
    if not EXPERIMENTAL_FEATURES:
        return web.json_response(
            {"error": "MFA is an experimental feature. Enable EXPERIMENTAL_FEATURES to use it."},
            status=403,
        )
    if MFA_DISABLED:
        return web.json_response({"error": "MFA is disabled."}, status=403)
    data = await _get_request_data(request)
    username, err = _resolve_username_from_token_and_data(request, data)
    if err or not username:
        return web.json_response(
            {"error": err or "Authentication required"}, status=401
        )
    result = users_db.mfa_setup_start(username)
    if not result:
        return web.json_response(
            {"error": "MFA setup failed or user not found"}, status=400
        )
    provisioning_uri, backup_code = result
    return web.json_response(
        {
            "provisioning_uri": provisioning_uri,
            "backup_code": backup_code,
            "message": "Scan QR with authenticator app, then call verify-setup with a code.",
        }
    )


@routes.post("/mss-login/api/mfa/verify-setup")
async def api_mfa_verify_setup(request: web.Request) -> web.Response:
    """Verify first TOTP code and enable MFA. Body: { code: "123456" } or { mfa_temp_token, code }."""
    if not EXPERIMENTAL_FEATURES:
        return web.json_response(
            {"error": "MFA is an experimental feature. Enable EXPERIMENTAL_FEATURES to use it."},
            status=403,
        )
    if MFA_DISABLED:
        return web.json_response({"error": "MFA is disabled."}, status=403)
    data = await _get_request_data(request)
    username, err = _resolve_username_from_token_and_data(request, data)
    if err or not username:
        return web.json_response(
            {"error": err or "Authentication required"}, status=401
        )
    code = (data.get("code") or "").strip().replace(" ", "")
    if not code or len(code) != 6:
        return web.json_response(
            {"error": "Invalid code (must be 6 digits)"}, status=400
        )
    if not users_db.mfa_verify_setup(username, code):
        return web.json_response({"error": "Invalid code or setup failed"}, status=400)
    try:
        send_notification(
            "mfa_enabled",
            "mss-login: MFA enabled",
            f"User {username} enabled MFA from IP: {get_ip(request)}",
        )
    except Exception:
        pass
    return web.json_response({"message": "MFA enabled successfully"})


@routes.post("/mss-login/api/mfa/verify")
async def api_mfa_verify(request: web.Request) -> web.Response:
    """Complete login after password: verify TOTP or backup code, issue JWT. Body: mfa_temp_token, code OR backup_code."""
    if not EXPERIMENTAL_FEATURES:
        return web.json_response(
            {"error": "MFA is an experimental feature. Enable EXPERIMENTAL_FEATURES to use it."},
            status=403,
        )
    if MFA_DISABLED:
        return web.json_response({"error": "MFA is disabled."}, status=403)
    from ..globals import logger
    from ..constants import DEBUG_MODE
    from datetime import datetime, timezone

    data = await _get_request_data(request)
    mfa_temp = (data.get("mfa_temp_token") or "").strip()
    code = (data.get("code") or "").strip().replace(" ", "")
    backup_code_raw = (
        (data.get("backup_code") or "")
        .strip()
        .replace(" ", "")
        .replace("-", "")
        .upper()
    )
    if not mfa_temp:
        return web.json_response({"error": "mfa_temp_token required"}, status=400)
    username = consume_mfa_temp_token(mfa_temp)
    if not username:
        return web.json_response(
            {"error": "Invalid or expired token. Please log in again."}, status=401
        )
    if backup_code_raw:
        if not users_db.verify_backup_code_and_consume(username, backup_code_raw):
            return web.json_response(
                {"error": "Invalid or already used backup code"}, status=400
            )
    elif code:
        if not users_db.verify_totp(username, code):
            return web.json_response({"error": "Invalid code"}, status=400)
    else:
        return web.json_response({"error": "Provide code or backup_code"}, status=400)

    user_id, _ = users_db.get_user(username=username)
    if not user_id:
        return web.json_response({"error": "User not found"}, status=500)
    no_exp = _user_can_have_non_expiring_jwt(username)
    token = jwt_auth.create_access_token(
        {"id": user_id, "username": username}, no_expiration=no_exp
    )
    try:
        payload = jwt_auth.decode_access_token(token)
        jti = payload.get("jti")
        exp = payload.get("exp")
        exp_at_iso = (
            datetime.fromtimestamp(exp, tz=timezone.utc).isoformat() if exp else None
        )
        if jti:
            get_session_token_store(SESSION_TOKEN_STORE_CONFIG).register_session(
                jti, user_id, username, exp_at_iso
            )
    except Exception:
        pass
    if DEBUG_MODE:
        logger.log_jwt_if_debug(token, username)
    else:
        logger.log_jwt_created_console_only(username)
    user_console_append(username, f"JWT token created for user: {username} (after MFA)")
    resp = web.json_response({"message": "Login successful", "jwt_token": token})
    resp.set_cookie("jwt_token", token, httponly=True, samesite="Strict")
    logger.login_success(get_ip(request), username)
    return resp


def _user_can_have_non_expiring_jwt(username: str) -> bool:
    user_id, user_rec = users_db.get_user(username)
    if not user_rec:
        return False
    groups = [g.lower() for g in user_rec.get("groups", [])]
    role = groups[0] if groups else "user"
    cfg = access_control._load_group_config()
    perms = cfg.get(role, {})
    if role in ("admin", "owner"):
        return True
    return perms.get("can_have_non_expiring_jwt", False) is True
