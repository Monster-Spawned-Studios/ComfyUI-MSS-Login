# --- START OF FILE routes/auth.py ---
import os
import uuid
from aiohttp import web
from ..globals import routes, users_db, jwt_auth, logger, timeout, access_control
from ..constants import HTML_DIR, MAX_TOKEN_EXPIRE_MINUTES
from ..utils.bootstrap import ensure_guest_user, ensure_groups_config
from ..utils.ip_filter import get_ip
from ..utils import user_env
from ..utils.api_token_store import get_api_token_store
from ..constants import API_TOKEN_STORE_CONFIG

@routes.get("/register")
async def get_register(request: web.Request) -> web.Response:
    path = os.path.join(HTML_DIR, "register.html")
    if not os.path.exists(path): return web.Response(text="register.html not found", status=404)
    with open(path, "r") as f: html_content = f.read()
    if not users_db.load_users():
        html_content = html_content.replace("{{ X-Admin-User }}", "true")
    else:
        html_content = html_content.replace("{{ X-Admin-User }}", "false")
    return web.Response(body=html_content, content_type="text/html")

@routes.post("/register")
async def post_register(request: web.Request) -> web.Response:
    sanitized_data = request.get("_sanitized_data", {})
    ip = get_ip(request)
    new_username = sanitized_data.get("new_user_username")
    new_password = sanitized_data.get("new_user_password")
    username = sanitized_data.get("username")
    password = sanitized_data.get("password")

    admin_user = users_db.get_admin_user()
    is_first_admin = (admin_user[0] is None)

    if not is_first_admin:
        if not users_db.check_username_password(username, password):
            timeout.add_failed_attempt(ip)
            return web.json_response({"error": "Invalid admin credentials"}, status=403)

    if None not in users_db.get_user(new_username):
        return web.json_response({"error": "Username exists"}, status=400)

    users_db.add_user(str(uuid.uuid4()), new_username, new_password, is_first_admin)

    # Create directory immediately
    user_env.get_user_workflow_dir(new_username)

    if is_first_admin:
        ensure_groups_config()
        ensure_guest_user()

    logger.registration_success(ip, new_username, username if not is_first_admin else None)
    timeout.remove_failed_attempts(ip)
    return web.json_response({"message": "User registered"})

@routes.get("/login")
async def get_login(request: web.Request) -> web.Response:
    if not users_db.load_users(): return web.HTTPFound("/register")
    if jwt_auth.get_token_from_request(request): return web.HTTPFound("/logout")
    path = os.path.join(HTML_DIR, "login.html")
    return web.FileResponse(path) if os.path.exists(path) else web.Response(text="login.html not found", status=404)

@routes.post("/login")
async def post_login(request: web.Request) -> web.Response:
    sanitized_data = request.get("_sanitized_data", {})
    ip = get_ip(request)
    
    if str(sanitized_data.get("guest_login", "false")).lower() == "true":
        ensure_guest_user()
        guest_id, _ = users_db.get_user("guest")
        if not guest_id: return web.json_response({"error": "Guest disabled"}, status=500)
        
        user_env.get_user_workflow_dir("guest")
        
        token = jwt_auth.create_access_token({"id": guest_id, "username": "guest"})
        resp = web.json_response({"message": "Guest login", "jwt_token": token})
        resp.set_cookie("jwt_token", token, httponly=True, samesite="Strict")
        logger.login_success(ip, "guest")
        timeout.remove_failed_attempts(ip)
        return resp

    username = sanitized_data.get("username")
    password = sanitized_data.get("password")

    if users_db.check_username_password(username, password):
        user_id, _ = users_db.get_user(username)
        
        user_env.get_user_workflow_dir(username)
        
        token = jwt_auth.create_access_token({"id": user_id, "username": username})
        resp = web.json_response({"message": "Login successful", "jwt_token": token})
        resp.set_cookie("jwt_token", token, httponly=True, samesite="Strict")
        logger.login_success(ip, username)
        timeout.remove_failed_attempts(ip)
        return resp

    timeout.add_failed_attempt(ip)
    return web.json_response({"error": "Invalid credentials"}, status=401)

@routes.get("/logout")
async def get_logout(request: web.Request) -> web.Response:
    resp = web.HTTPFound("/login")
    resp.del_cookie("jwt_token", path="/")
    return resp


def _user_can_have_api_tokens(username: str) -> bool:
    """Return True if the user's role has can_have_api_tokens (admin always allowed)."""
    user_id, user_rec = users_db.get_user(username)
    if not user_rec:
        return False
    groups = [g.lower() for g in user_rec.get("groups", [])]
    role = groups[0] if groups else "user"
    cfg = access_control._load_group_config()
    perms = cfg.get(role, {})
    if role == "admin":
        return True
    return perms.get("can_have_api_tokens", False) is True


@routes.get("/usgromana/generate_token")
async def get_generate_token(request: web.Request) -> web.Response:
    """Serve the generate API token page (public)."""
    path = os.path.join(HTML_DIR, "generate_token.html")
    if not os.path.exists(path):
        return web.Response(text="generate_token.html not found", status=404)
    return web.FileResponse(path)


@routes.post("/usgromana/generate_token")
async def post_generate_token(request: web.Request) -> web.Response:
    """Create a long-lived API token. Requires credentials (or JWT) and can_have_api_tokens."""
    sanitized_data = request.get("_sanitized_data", {})
    ip = get_ip(request)
    username = (sanitized_data.get("username") or "").strip()
    password = sanitized_data.get("password") or ""
    expire_hours_raw = sanitized_data.get("expire_hours")
    try:
        expire_hours = float(expire_hours_raw) if expire_hours_raw not in (None, "") else 720.0
    except (TypeError, ValueError):
        expire_hours = 720.0

    max_hours = MAX_TOKEN_EXPIRE_MINUTES / 60.0
    if expire_hours <= 0 or expire_hours > max_hours:
        expire_hours = min(max(1.0, expire_hours), max_hours)

    # Optionally resolve user from JWT (no password needed)
    token_from_request = jwt_auth.get_token_from_request(request)
    if token_from_request:
        try:
            payload = jwt_auth.decode_access_token(token_from_request)
            jwt_username = payload.get("username")
            if jwt_username and users_db.get_user(jwt_username)[0]:
                if not _user_can_have_api_tokens(jwt_username):
                    return web.json_response({"error": "Usgromana: You do not have permission to create API tokens."}, status=403)
                user_id, _ = users_db.get_user(jwt_username)
                store = get_api_token_store(API_TOKEN_STORE_CONFIG)
                raw_token = store.create_token(user_id, jwt_username, expire_hours)
                logger.generate_success(ip, jwt_username, int(expire_hours))
                return web.json_response({
                    "message": "API token created.",
                    "jwt_token": raw_token,
                    "expires_in_hours": expire_hours,
                })
        except Exception:
            pass

    if not username or not password:
        logger.generate_attempt(ip, username or "", password or "", int(expire_hours))
        return web.json_response({"error": "Username and password required."}, status=401)

    if not users_db.check_username_password(username, password):
        logger.generate_attempt(ip, username, password, int(expire_hours))
        timeout.add_failed_attempt(ip)
        return web.json_response({"error": "Invalid credentials."}, status=401)

    if not _user_can_have_api_tokens(username):
        return web.json_response({"error": "Usgromana: You do not have permission to create API tokens."}, status=403)

    user_id, _ = users_db.get_user(username)
    store = get_api_token_store(API_TOKEN_STORE_CONFIG)
    raw_token = store.create_token(user_id, username, expire_hours)
    logger.generate_success(ip, username, int(expire_hours))
    timeout.remove_failed_attempts(ip)
    return web.json_response({
        "message": "API token created.",
        "jwt_token": raw_token,
        "expires_in_hours": expire_hours,
    })


@routes.post("/generate_token")
async def post_generate_token_legacy(request: web.Request) -> web.Response:
    """Legacy endpoint: delegate to POST /usgromana/generate_token."""
    return await post_generate_token(request)