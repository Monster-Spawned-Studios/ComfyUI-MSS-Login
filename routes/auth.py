# --- START OF FILE routes/auth.py ---
import json
import os
import sqlite3
import uuid
from datetime import UTC, datetime, timezone

import jwt
from aiohttp import web

from .. import constants as constants_module
from ..constants import (
	API_TOKEN_STORE_CONFIG,
	BLACKLIST_AFTER_ATTEMPTS,
	BLACKLIST_EXPIRY_HOURS,
	DATA_DIR,
	DEBUG_MODE,
	HTML_DIR,
	LOADING_TIMEOUT_SECONDS,
	MAX_TOKEN_EXPIRE_MINUTES,
	SESSION_TOKEN_STORE_CONFIG,
	USERS_DB_CONFIG,
	WEB_DIR,
	experimental_loading_screen_enabled,
	experimental_mfa_enabled,
)
from ..globals import access_control, jwt_auth, logger, routes, timeout, users_db
from ..utils import user_env
from ..utils.api_token_store import get_api_token_store
from ..utils.bootstrap import ensure_groups_config, ensure_guest_user
from ..utils.input_sanitizer import (
	sanitize_backup_code_input,
	sanitize_label,
	sanitize_password_input,
	sanitize_token_hash_prefix,
	sanitize_totp_code,
	sanitize_username,
)
from ..utils.ip_filter import get_device_id, get_ip, is_https_request
from ..utils.lockout_store import get_lockout_store
from ..utils.mfa_temp_store import create_mfa_temp_token
from ..utils.ntfy_notifier import send_notification
from ..utils.request_navigation import is_browser_navigation
from ..utils.session_token_store import get_session_token_store
from ..utils.updater import get_local_version
from ..utils.user_console_log import append as user_console_append
from ..utils.validate import validate_password, validate_username


def _authenticated_admin_username(request: web.Request) -> str | None:
	"""Return the username if the request has a valid admin/owner session or API token."""
	token = jwt_auth.get_token_from_request(request)
	if not token:
		return None
	username = None
	try:
		api_store = get_api_token_store(API_TOKEN_STORE_CONFIG)
		api_user = api_store.get_user_for_token(token)
		if api_user is not None:
			_user_id, username = api_user
		else:
			payload = jwt_auth.decode_access_token(token)
			username = payload.get("username")
	except Exception:
		return None
	if not username:
		return None
	try:
		_uid, rec = users_db.get_user(username)
	except Exception:
		return None
	if not rec:
		return None
	groups = [str(g).lower() for g in rec.get("groups", [])]
	if rec.get("admin") or "admin" in groups or "owner" in groups:
		return username
	return None


@routes.get("/register")
async def get_register(request: web.Request) -> web.Response:
	"""Serve the register page.

	Public only for the first admin (empty user database). After that, an
	authenticated admin session is required so the form is not a brute-force
	target from the login page.
	"""
	has_users = bool(users_db.load_users())
	session_admin = _authenticated_admin_username(request)
	if has_users and not session_admin:
		if is_browser_navigation(request):
			return web.HTTPFound("/login")
		return web.json_response(
			{"error": "Admin authentication required to register users"}, status=403
		)
	path = os.path.join(HTML_DIR, "register.html")
	if not os.path.exists(path):
		return web.Response(text="register.html not found", status=404)
	with open(path, "r", encoding="utf-8") as f:
		html_content = f.read()
	# Hide admin credential fields for first-run bootstrap and for already-authed admins.
	if not has_users or session_admin:
		html_content = html_content.replace("{{ X-Admin-User }}", "true")
	else:
		html_content = html_content.replace("{{ X-Admin-User }}", "false")
	return web.Response(body=html_content, content_type="text/html")


@routes.post("/register")
async def post_register(request: web.Request) -> web.Response:
	"""Register a new user."""
	sanitized_data = request.get("_sanitized_data", {})
	ip = get_ip(request)
	new_username = sanitize_username(sanitized_data.get("new_user_username"))
	new_password = sanitize_password_input(sanitized_data.get("new_user_password"))
	username = sanitize_username(sanitized_data.get("username"))

	ok, msg = validate_username(new_username)
	if not ok:
		return web.json_response({"error": msg}, status=400)
	ok, msg = validate_password(new_password)
	if not ok:
		return web.json_response({"error": msg}, status=400)

	admin_user = users_db.get_admin_user()
	is_first_admin = admin_user[0] is None
	session_admin = _authenticated_admin_username(request)

	if not is_first_admin:
		if session_admin:
			username = session_admin
		else:
			timeout.add_failed_attempt(ip)
			return web.json_response(
				{"error": "Admin authentication required to register users"}, status=403
			)

	if None not in users_db.get_user(new_username):
		return web.json_response({"error": "Username exists"}, status=400)

	users_db.add_user(str(uuid.uuid4()), new_username, new_password, is_first_admin)

	# Create directory immediately
	user_env.get_user_workflow_dir(new_username)

	if is_first_admin:
		ensure_groups_config()
		ensure_guest_user()

	logger.registration_success(ip, new_username, username if not is_first_admin else None)
	try:
		send_notification(
			"user_created",
			"MSS-Login: User created",
			f"New user: {new_username} (registered by {username if not is_first_admin else 'first admin'}) from IP: {ip}",
		)
	except Exception as e:
		logger.error(f"[auth.py] post_register: send_notification: {e}")
	timeout.remove_failed_attempts(ip)
	if is_browser_navigation(request):
		return web.HTTPFound("/login?registered=1")
	return web.json_response({"message": "User registered"})


# Content-Security-Policy for the loading page. connect-src includes tunnel subdomains
# so Cloudflare Tunnel (cloudflared) at e.g. comfyui-server.monsterspawned.studio works.
LOADING_CSP = (
	"default-src 'self'; "
	"script-src 'self'; "
	"style-src 'self' 'unsafe-inline'; "
	"connect-src 'self' https://*.monsterspawned.studio wss://*.monsterspawned.studio; "
	"img-src 'self'; "
	"base-uri 'self'; "
	"form-action 'self'"
)


def _parse_tips_file(path: str) -> list[str]:
	"""Read a loading-tips JSON file and return a list of non-empty tip strings.

	Accepts either a plain JSON array of strings or an object with a
	``"messages"`` key containing such an array.  Returns ``[]`` on any
	I/O or parse error so the caller can fall through to the next source.
	"""
	if not os.path.isfile(path):
		return []
	try:
		with open(path, "r", encoding="utf-8") as f:
			data = json.load(f)
			if isinstance(data, list):
				return [s for s in data if isinstance(s, str) and s.strip()]
			if isinstance(data, dict) and isinstance(data.get("messages"), list):
				return [s for s in data["messages"] if isinstance(s, str) and s.strip()]
	except (json.JSONDecodeError, OSError):
		pass
	return []


@routes.get("/mss-login/loading-tips.json")
async def get_loading_tips(request: web.Request) -> web.Response:
	"""Serve loading tips JSON from same origin when loading screen is enabled (experimental).
	Data dir override: DATA_DIR/loading-tips.json; else bundled web/data/loading-tips.json.
	When loading screen is disabled, returns 200 with minimal payload so consumers (e.g.
	ComfyUI_frontend or extension scripts that reference this URL) do not see a failed
	request and get stuck in a loading state.
	"""
	if not experimental_loading_screen_enabled():
		return web.json_response(["Preparing ComfyUI…"])
	tips_data = _parse_tips_file(os.path.join(DATA_DIR, "loading-tips.json"))
	if not tips_data:
		tips_data = _parse_tips_file(os.path.join(WEB_DIR, "data", "loading-tips.json"))
	payload = tips_data if tips_data else ["Preparing ComfyUI…"]
	return web.json_response(payload)


@routes.get("/loading")
async def get_loading(request: web.Request) -> web.Response:
	"""Serve the loading page (between login and ComfyUI) when experimental loading_screen is enabled.
	Otherwise redirect to /. Requires valid JWT; unauthenticated users are redirected by JWT middleware.
	This is an MSS-Login intermediate page and does not conflict with ComfyUI's in-app loading at /.
	"""
	if not experimental_loading_screen_enabled():
		return web.HTTPFound("/")
	path = os.path.join(HTML_DIR, "loading.html")
	if not os.path.exists(path):
		return web.Response(text="loading.html not found", status=404)
	timeout_ms = LOADING_TIMEOUT_SECONDS * 1000
	try:
		with open(path, "r", encoding="utf-8") as f:
			html = f.read()
	except OSError:
		return web.Response(text="loading.html not found", status=404)
	html = html.replace("{{LOADING_TIMEOUT_MS}}", str(timeout_ms))
	resp = web.Response(text=html, content_type="text/html")
	resp.headers["Content-Security-Policy"] = LOADING_CSP
	return resp


@routes.get("/login")
async def get_login(request: web.Request) -> web.Response:
	"""Serve the login page with version injected from pyproject.toml."""
	if not users_db.load_users():
		return web.HTTPFound("/register")
	if jwt_auth.get_token_from_request(request):
		return web.HTTPFound("/logout")
	path = os.path.join(HTML_DIR, "login.html")
	if not os.path.exists(path):
		return web.Response(text="login.html not found", status=404)
	try:
		with open(path, "r", encoding="utf-8") as f:
			html = f.read()
	except OSError:
		return web.Response(text="login.html not found", status=404)
	version = get_local_version()
	html = html.replace("{{VERSION}}", version)
	return web.Response(text=html, content_type="text/html")


@routes.get("/mfa")
async def get_mfa(request: web.Request) -> web.Response:
	"""Serve the MFA page (verify or setup). Token and mode are in sessionStorage set by login."""
	if not experimental_mfa_enabled():
		return web.json_response(
			{
				"error": "MFA is an experimental feature. Enable experimental_features and experimental.mfa to use it."
			},
			status=403,
		)
	path = os.path.join(HTML_DIR, "mfa.html")
	if not os.path.exists(path):
		return web.Response(text="mfa.html not found", status=404)
	return web.FileResponse(path)


@routes.post("/login")
async def post_login(request: web.Request) -> web.Response:
	sanitized_data = request.get("_sanitized_data", {})
	ip = get_ip(request)

	if str(sanitized_data.get("guest_login", "false")).lower() == "true":
		if not constants_module.ALLOW_GUEST_JWT:
			return web.json_response(
				{"error": "Guest login is disabled. Guests cannot obtain JWT tokens."}, status=403
			)
		ensure_guest_user()
		guest_id, _ = users_db.get_user("guest")
		if not guest_id:
			return web.json_response({"error": "Guest disabled"}, status=500)

		user_env.get_user_workflow_dir("guest")

		token = jwt_auth.create_access_token({"id": guest_id, "username": "guest"})
		try:
			payload = jwt_auth.decode_access_token(token)
			jti = payload.get("jti")
			exp = payload.get("exp")
			exp_at_iso = datetime.fromtimestamp(exp, tz=UTC).isoformat() if exp else None
			if jti:
				get_session_token_store(SESSION_TOKEN_STORE_CONFIG).register_session(
					jti, guest_id, "guest", exp_at_iso
				)
		except (jwt.DecodeError, jwt.ExpiredSignatureError, OSError, sqlite3.Error):
			pass
		if DEBUG_MODE:
			logger.log_jwt_if_debug(token, "guest")
		else:
			logger.log_jwt_created_console_only("guest")
		user_console_append("guest", "JWT token created for user: guest")
		logger.login_success(ip, "guest")
		timeout.remove_failed_attempts(ip)
		redirect_url = "/loading" if experimental_loading_screen_enabled() else "/"
		_secure = is_https_request(request)
		if is_browser_navigation(request):
			resp = web.HTTPFound(redirect_url)
			resp.set_cookie("jwt_token", token, httponly=True, samesite="Strict", secure=_secure)
			return resp
		resp = web.json_response(
			{"message": "Guest login", "jwt_token": token, "redirect_url": redirect_url}
		)
		resp.set_cookie("jwt_token", token, httponly=True, samesite="Strict", secure=_secure)
		return resp

	username = sanitize_username(sanitized_data.get("username"))
	password = sanitize_password_input(sanitized_data.get("password"))

	if users_db.check_username_password(username, password):
		user_id, user_rec = users_db.get_user(username)
		user_env.get_user_workflow_dir(username)

		mfa_enabled = users_db.get_mfa_enabled(username)

		# CRITICAL: Fail closed. If user has MFA enrolled, ALWAYS require second factor.
		if mfa_enabled and not constants_module.MFA_DISABLED:
			mfa_temp = create_mfa_temp_token(username)
			timeout.remove_failed_attempts(ip)
			return web.json_response(
				{
					"message": "MFA verification required",
					"mfa_required": True,
					"mfa_temp_token": mfa_temp,
				},
				status=200,
			)

		# When MFA feature or global MFA is disabled, skip MFA setup/role branches
		if experimental_mfa_enabled() and not constants_module.MFA_DISABLED:
			is_admin = user_rec.get("admin") or "admin" in [
				g.lower() for g in user_rec.get("groups", [])
			]
			role_requires_mfa = _role_requires_mfa(username)

			if is_admin and not mfa_enabled:
				# Force admin to set up MFA before issuing JWT
				mfa_temp = create_mfa_temp_token(username)
				timeout.remove_failed_attempts(ip)
				return web.json_response(
					{
						"message": "MFA setup required for admin accounts",
						"mfa_setup_required": True,
						"mfa_temp_token": mfa_temp,
					},
					status=200,
				)
			if role_requires_mfa and not mfa_enabled:
				# Role requires MFA but user has not set it up
				mfa_temp = create_mfa_temp_token(username)
				timeout.remove_failed_attempts(ip)
				return web.json_response(
					{
						"message": "MFA setup required for your role",
						"mfa_setup_required": True,
						"mfa_temp_token": mfa_temp,
					},
					status=200,
				)

		no_exp = _user_can_have_non_expiring_jwt(username)
		token = jwt_auth.create_access_token(
			{"id": user_id, "username": username}, no_expiration=no_exp
		)
		try:
			payload = jwt_auth.decode_access_token(token)
			jti = payload.get("jti")
			exp = payload.get("exp")
			exp_at_iso = datetime.fromtimestamp(exp, tz=UTC).isoformat() if exp else None
			if jti:
				get_session_token_store(SESSION_TOKEN_STORE_CONFIG).register_session(
					jti, user_id, username, exp_at_iso
				)
		except Exception as e:
			logger.error(f"[auth.py] post_login: register_session: {e}")
		if DEBUG_MODE:
			logger.log_jwt_if_debug(token, username)
		else:
			logger.log_jwt_created_console_only(username)
		user_console_append(username, f"JWT token created for user: {username}")
		try:
			send_notification(
				"user_login", "mss-login: User login", f"User {username} logged in from IP: {ip}"
			)
		except Exception as e:
			logger.error(f"[auth.py] post_login: send_notification: {e}")
		logger.login_success(ip, username)
		timeout.remove_failed_attempts(ip)
		redirect_url = "/loading" if experimental_loading_screen_enabled() else "/"
		_secure = is_https_request(request)
		if is_browser_navigation(request):
			resp = web.HTTPFound(redirect_url)
			resp.set_cookie("jwt_token", token, httponly=True, samesite="Strict", secure=_secure)
			return resp
		resp = web.json_response(
			{"message": "Login successful", "jwt_token": token, "redirect_url": redirect_url}
		)
		resp.set_cookie("jwt_token", token, httponly=True, samesite="Strict", secure=_secure)
		return resp

	timeout.add_failed_attempt(ip)
	if timeout.get_failed_attempts(ip) >= BLACKLIST_AFTER_ATTEMPTS:
		get_lockout_store(USERS_DB_CONFIG).add_lockout(
			ip, get_device_id(request), expiry_hours=BLACKLIST_EXPIRY_HOURS
		)
	logger.login_failed(ip, username or "")
	try:
		send_notification(
			"login_failure",
			"mss-login: Login failure",
			f"Failed login attempt for username '{username}' from IP: {ip}",
		)
	except Exception as e:
		logger.error(f"[auth.py] post_login: send_notification: {e!s}")
	return web.json_response({"error": "Invalid credentials"}, status=401)


@routes.get("/logout")
async def get_logout(request: web.Request) -> web.Response:
	username = None
	try:
		token = jwt_auth.get_token_from_request(request)
		if token and token.count(".") >= 2:
			payload = jwt_auth.decode_access_token(token)
			username = payload.get("username")
			if username:
				ip = get_ip(request)
				send_notification(
					"user_logout",
					"mss-login: User logout",
					f"User {username} logged out from IP: {ip}",
				)
				logger.logout(ip, username)
	except Exception as e:
		logger.error(f"[auth.py] get_logout: send_notification: {e!s}")
	resp = web.HTTPFound("/login")
	resp.del_cookie("jwt_token", path="/")
	return resp


def _role_requires_mfa(username: str) -> bool:
	"""Return True if the user's role has mfa_required (admins always treated as requiring MFA in login flow)."""
	user_id, user_rec = users_db.get_user(username)
	if not user_rec:
		return False
	groups = [g.lower() for g in user_rec.get("groups", [])]
	role = groups[0] if groups else "user"
	cfg = access_control._load_group_config()
	perms = cfg.get(role, {})
	return perms.get("mfa_required", False) is True


def _user_can_have_api_tokens(username: str) -> bool:
	"""Return True if the user's role has can_have_api_tokens (admin always allowed)."""
	user_id, user_rec = users_db.get_user(username)
	if not user_rec:
		return False
	groups = [g.lower() for g in user_rec.get("groups", [])]
	role = groups[0] if groups else "user"
	cfg = access_control._load_group_config()
	perms = cfg.get(role, {})
	if role in ("admin", "owner"):
		return True
	return perms.get("can_have_api_tokens", False) is True


def _user_can_have_non_expiring_jwt(username: str) -> bool:
	"""Return True if the user's role has can_have_non_expiring_jwt."""
	user_id, user_rec = users_db.get_user(username)
	if not user_rec:
		return False
	groups = [g.lower() for g in user_rec.get("groups", [])]
	role = groups[0] if groups else "user"
	cfg = access_control._load_group_config()
	perms = cfg.get(role, {})
	return perms.get("can_have_non_expiring_jwt", False) is True


@routes.get("/mss-login/generate_token")
async def get_generate_token(request: web.Request) -> web.Response:
	"""Redirect standalone token page requests to the login page."""
	return web.HTTPFound("/login")


@routes.post("/mss-login/generate_token")
async def post_generate_token(request: web.Request) -> web.Response:
	"""Create a long-lived API token. Requires credentials (or JWT) and can_have_api_tokens."""
	sanitized_data = dict(request.get("_sanitized_data") or {})  # type: ignore[assignment]
	if request.can_read_body and "application/json" in (request.content_type or ""):
		try:
			body = await request.json()  # type: ignore[assignment]
			if isinstance(body, dict):
				sanitized_data.update(body)
		except Exception:
			pass
	ip = get_ip(request)
	username = sanitize_username(sanitized_data.get("username"))
	password = sanitize_password_input(sanitized_data.get("password"))
	label = sanitize_label(sanitized_data.get("label"))
	require_password_reauth_raw = sanitized_data.get("require_password_reauth")
	require_password_reauth = str(require_password_reauth_raw).strip().lower() in (
		"1",
		"true",
		"yes",
		"on",
	)
	expire_hours_raw = sanitized_data.get("expire_hours")
	try:
		expire_hours = float(expire_hours_raw) if expire_hours_raw not in (None, "") else 720.0
	except (TypeError, ValueError):
		expire_hours = 720.0

	max_hours = MAX_TOKEN_EXPIRE_MINUTES / 60.0
	# 0 = never expire (only if user has can_have_non_expiring_jwt); otherwise clamp to 1..max
	if expire_hours <= 0:
		# Will be validated per-user below (must have non-expiring permission)
		pass
	elif expire_hours > max_hours:
		expire_hours = max_hours
	else:
		expire_hours = max(1.0, expire_hours)

	# Optionally resolve user from JWT (no password needed)
	# For sensitive in-app flows, callers can force password re-auth.
	token_from_request = jwt_auth.get_token_from_request(request)
	if token_from_request and not require_password_reauth:
		try:
			payload = jwt_auth.decode_access_token(token_from_request)
			jwt_username = payload.get("username")
			if jwt_username and users_db.get_user(jwt_username)[0]:
				if not _user_can_have_api_tokens(jwt_username):
					return web.json_response(
						{"error": "MSS-Login: You do not have permission to create API tokens."},
						status=403,
					)
				if expire_hours <= 0 and not _user_can_have_non_expiring_jwt(jwt_username):
					return web.json_response(
						{
							"error": "MSS-Login: Never-expiring tokens (0 hours) require the Non-expiring JWT permission. Ask an admin to enable it for your role."
						},
						status=403,
					)
				user_id, _ = users_db.get_user(jwt_username)
				store = get_api_token_store(API_TOKEN_STORE_CONFIG)
				raw_token = store.create_token(user_id, jwt_username, expire_hours, label=label)
				if DEBUG_MODE:
					logger.log_jwt_if_debug(raw_token, jwt_username)
				else:
					logger.log_jwt_created_console_only(jwt_username)
				user_console_append(jwt_username, f"API token created for user: {jwt_username}")
				try:
					send_notification(
						"api_token_created",
						"mss-login: API token created",
						f"User {jwt_username} created API token from IP: {ip}",
					)
				except Exception:
					pass
				logger.generate_success(
					ip, jwt_username, int(expire_hours) if expire_hours > 0 else 0
				)
				return web.json_response(
					{
						"message": "API token created.",
						"jwt_token": raw_token,
						"expires_in_hours": expire_hours if expire_hours > 0 else None,
					}
				)
		except Exception:
			pass

	# MFA second step: mfa_temp_token + code (from first step when user has MFA)
	mfa_temp = (sanitized_data.get("mfa_temp_token") or "").strip()
	mfa_code = sanitize_totp_code(sanitized_data.get("code"))
	mfa_backup = sanitize_backup_code_input(sanitized_data.get("backup_code"))
	if mfa_temp and (mfa_code or mfa_backup):
		from ..utils.mfa_temp_store import consume_mfa_temp_token

		mfa_username = consume_mfa_temp_token(mfa_temp)
		if not mfa_username:
			return web.json_response(
				{"error": "Invalid or expired MFA token. Please log in again."}, status=401
			)
		if mfa_backup:
			if not users_db.verify_backup_code_and_consume(mfa_username, mfa_backup):
				return web.json_response(
					{"error": "Invalid or already used backup code"}, status=400
				)
		elif mfa_code:
			if not users_db.verify_totp(mfa_username, mfa_code):
				return web.json_response({"error": "Invalid code"}, status=400)
		else:
			return web.json_response({"error": "Provide code or backup_code"}, status=400)
		username = mfa_username
		user_id, _ = users_db.get_user(username)
		if not user_id:
			return web.json_response({"error": "User not found"}, status=500)
		expire_hours = float(sanitized_data.get("expire_hours") or 720)
		if expire_hours <= 0:
			pass
		elif expire_hours > MAX_TOKEN_EXPIRE_MINUTES / 60.0:
			expire_hours = MAX_TOKEN_EXPIRE_MINUTES / 60.0
		else:
			expire_hours = max(1.0, expire_hours)
		if not _user_can_have_api_tokens(username):
			return web.json_response(
				{"error": "mss-login: You do not have permission to create API tokens."}, status=403
			)
		if expire_hours <= 0 and not _user_can_have_non_expiring_jwt(username):
			return web.json_response(
				{
					"error": "mss-login: Never-expiring tokens (0 hours) require the Non-expiring JWT permission."
				},
				status=403,
			)
		store = get_api_token_store(API_TOKEN_STORE_CONFIG)
		raw_token = store.create_token(user_id, username, expire_hours, label=label)
		if DEBUG_MODE:
			logger.log_jwt_if_debug(raw_token, username)
		else:
			logger.log_jwt_created_console_only(username)
		user_console_append(username, f"API token created for user: {username} (after MFA)")
		try:
			send_notification(
				"api_token_created",
				"mss-login: API token created",
				f"User {username} created API token (MFA) from IP: {ip}",
			)
		except Exception:
			pass
		logger.generate_success(ip, username, int(expire_hours) if expire_hours > 0 else 0)
		return web.json_response(
			{
				"message": "API token created.",
				"jwt_token": raw_token,
				"expires_in_hours": expire_hours if expire_hours > 0 else None,
			}
		)

	if not username or not password:
		logger.generate_attempt(ip, username or "", int(expire_hours))
		return web.json_response({"error": "Username and password required."}, status=401)

	if not users_db.check_username_password(username, password):
		logger.generate_attempt(ip, username, int(expire_hours))
		timeout.add_failed_attempt(ip)
		return web.json_response({"error": "Invalid credentials."}, status=401)

	if not _user_can_have_api_tokens(username):
		return web.json_response(
			{"error": "MSS-Login: You do not have permission to create API tokens."}, status=403
		)
	if expire_hours <= 0 and not _user_can_have_non_expiring_jwt(username):
		return web.json_response(
			{
				"error": "MSS-Login: Never-expiring tokens (0 hours) require the Non-expiring JWT permission. Ask an admin to enable it for your role."
			},
			status=403,
		)

	# CRITICAL: Fail closed. If user has MFA enrolled, ALWAYS require second factor.
	if users_db.get_mfa_enabled(username) and not constants_module.MFA_DISABLED:
		mfa_temp = create_mfa_temp_token(username)
		return web.json_response(
			{
				"message": "MFA verification required",
				"mfa_required": True,
				"mfa_temp_token": mfa_temp,
				"expire_hours": expire_hours,
			},
			status=200,
		)

	user_id, _ = users_db.get_user(username)
	store = get_api_token_store(API_TOKEN_STORE_CONFIG)
	raw_token = store.create_token(user_id, username, expire_hours, label=label)
	if DEBUG_MODE:
		logger.log_jwt_if_debug(raw_token, username)
	else:
		logger.log_jwt_created_console_only(username)
	user_console_append(username, f"API token created for user: {username}")
	try:
		send_notification(
			"api_token_created",
			"mss-login: API token created",
			f"User {username} created API token from IP: {ip}",
		)
	except Exception:
		pass
	logger.generate_success(ip, username, int(expire_hours) if expire_hours > 0 else 0)
	timeout.remove_failed_attempts(ip)
	return web.json_response(
		{
			"message": "API token created.",
			"jwt_token": raw_token,
			"expires_in_hours": expire_hours if expire_hours > 0 else None,
		}
	)


@routes.post("/generate_token")
async def post_generate_token_legacy(request: web.Request) -> web.Response:
	"""Legacy endpoint: delegate to POST /mss-login/generate_token."""
	return await post_generate_token(request)


def _resolve_username_from_jwt(request: web.Request) -> str | None:
	"""Extract and validate the username from the current request JWT."""
	token = jwt_auth.get_token_from_request(request)
	if not token:
		return None
	try:
		payload = jwt_auth.decode_access_token(token)
		uname = payload.get("username")
		if uname and users_db.get_user(uname)[0]:
			return uname
	except Exception:
		pass
	return None


@routes.get("/mss-login/api/tokens")
async def get_user_tokens(request: web.Request) -> web.Response:
	"""Return the calling user's API tokens (labels, expiry, hash prefix). Session tokens are excluded."""
	username = _resolve_username_from_jwt(request)
	if not username:
		return web.json_response({"error": "Authentication required."}, status=401)
	store = get_api_token_store(API_TOKEN_STORE_CONFIG)
	tokens = store.list_tokens_for_user(username)
	return web.json_response({"tokens": tokens})


@routes.delete("/mss-login/api/tokens")
async def delete_user_token(request: web.Request) -> web.Response:
	"""Revoke one of the calling user's API tokens by its hash prefix."""
	username = _resolve_username_from_jwt(request)
	if not username:
		return web.json_response({"error": "Authentication required."}, status=401)
	try:
		body = await request.json()
	except Exception:
		return web.json_response({"error": "Invalid JSON body."}, status=400)
	prefix = sanitize_token_hash_prefix(body.get("token_hash_prefix"))
	if not prefix or len(prefix) < 8:
		return web.json_response(
			{"error": "token_hash_prefix must be at least 8 alphanumeric characters."}, status=400
		)
	store = get_api_token_store(API_TOKEN_STORE_CONFIG)
	revoked = store.revoke_token_by_hash_prefix(prefix, username)
	if revoked:
		user_console_append(username, f"API token revoked (prefix: {prefix}...)")
		return web.json_response({"message": "Token revoked."})
	return web.json_response({"error": "Token not found."}, status=404)
