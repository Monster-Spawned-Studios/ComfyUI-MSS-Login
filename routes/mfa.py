# --- START OF FILE routes/mfa.py ---
"""MFA: setup, verify-setup, verify (login second step), recovery codes, owner reset."""

from datetime import UTC, datetime

from aiohttp import web

from ..constants import (
	MFA_DISABLED,
	SESSION_TOKEN_STORE_CONFIG,
	experimental_loading_screen_enabled,
	experimental_mfa_enabled,
)
from ..globals import access_control, jwt_auth, logger, routes, timeout, users_db
from ..utils.auth_cookies import set_auth_cookie, truthy_remember_me
from ..utils.ip_filter import get_ip, is_https_request
from ..utils.login_finalize import (
	evaluate_mfa_gate,
	issue_session_token,
	log_mfa_disabled_emergency,
	mfa_disabled_applies_to_user,
	require_step_up,
	user_is_admin_or_owner,
)
from ..utils.mfa_temp_store import (
	consume_mfa_temp_token,
	peek_mfa_temp_token,
	record_mfa_temp_failure,
)
from ..utils.ntfy_notifier import send_notification
from ..utils.request_navigation import is_browser_navigation
from ..utils.user_console_log import append as user_console_append


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


def _client_meta(request: web.Request) -> tuple[str, str]:
	ip = get_ip(request)
	ua = (request.headers.get("User-Agent") or "").strip()
	return ip, ua


def _resolve_setup_username(
	request: web.Request, data: dict, *, purpose: str
) -> tuple[str | None, str | None]:
	"""
	Resolve username for setup / verify-setup.

	Requires a valid session JWT (with step-up for factor_change) OR a purpose-bound
	mfa_temp_token (mfa_setup / factor_change). mfa_verify temps cannot call setup.
	"""
	ip, ua = _client_meta(request)
	token = jwt_auth.get_token_from_request(request)
	if token:
		user_id, username = jwt_auth.resolve_token_identity(token, touch_idle=False)
		if username and users_db.get_user(username)[0]:
			return username, None
	mfa_temp = (data.get("mfa_temp_token") or "").strip()
	if mfa_temp:
		entry = peek_mfa_temp_token(mfa_temp, purpose=purpose, ip=ip, user_agent=ua)  # type: ignore[arg-type]
		if entry:
			return entry.username, None
		# Wrong purpose / bindings
		if peek_mfa_temp_token(mfa_temp) is not None:
			return None, "Invalid MFA token purpose for this operation."
		return None, "Invalid or expired MFA token. Please log in again."
	return None, "Authentication required (JWT or mfa_temp_token)."


def _mfa_feature_guard(request: web.Request) -> web.Response | None:
	if not experimental_mfa_enabled():
		return web.json_response(
			{
				"error": "MFA is an experimental feature. Enable experimental_features and experimental.mfa to use it.",
				"code": "mfa_experimental_off",
			},
			status=403,
		)
	return None


@routes.post("/mss-login/api/mfa/setup")
async def api_mfa_setup(request: web.Request) -> web.Response:
	"""Start MFA setup. Blocked if MFA already enabled (use factor-change)."""
	guard = _mfa_feature_guard(request)
	if guard:
		return guard
	data = await _get_request_data(request)
	ip, ua = _client_meta(request)

	# Prefer mfa_setup temp (post-password enrollment) or authenticated session
	username, err = _resolve_setup_username(request, data, purpose="mfa_setup")
	if err or not username:
		# Allow factor_change purpose as well when already enrolled (must use reenrollment API)
		username2, err2 = _resolve_setup_username(request, data, purpose="factor_change")
		if username2:
			return web.json_response(
				{
					"error": "MFA already enabled. Use factor-change with current code; setup is blocked.",
					"code": "mfa_already_enabled",
				},
				status=403,
			)
		return web.json_response({"error": err or err2 or "Authentication required"}, status=401)

	_uid, user_rec = users_db.get_user(username)
	if mfa_disabled_applies_to_user(user_rec):
		log_mfa_disabled_emergency(username, ip)
		return web.json_response({"error": "MFA is disabled."}, status=403)
	if MFA_DISABLED and user_is_admin_or_owner(user_rec) and not mfa_disabled_applies_to_user(user_rec):
		# Emergency switch on but does not apply to this admin — continue
		pass

	if users_db.get_mfa_enabled(username):
		return web.json_response(
			{
				"error": "MFA already enabled. Authenticator change requires re-auth and current code.",
				"code": "mfa_already_enabled",
			},
			status=403,
		)

	result = users_db.mfa_setup_start(username, allow_reenrollment=False)
	if not result:
		return web.json_response({"error": "MFA setup failed or user not found"}, status=400)
	provisioning_uri, recovery_codes = result
	return web.json_response(
		{
			"provisioning_uri": provisioning_uri,
			"recovery_codes": recovery_codes,
			# Compat: first code as backup_code for older UI
			"backup_code": recovery_codes[0] if recovery_codes else "",
			"message": "Scan QR with authenticator app, then call verify-setup with a code.",
		}
	)


@routes.post("/mss-login/api/mfa/verify-setup")
async def api_mfa_verify_setup(request: web.Request) -> web.Response:
	"""Verify first TOTP code and enable MFA. Optionally issues session when mfa_setup temp present."""
	guard = _mfa_feature_guard(request)
	if guard:
		return guard
	data = await _get_request_data(request)
	ip, ua = _client_meta(request)
	username, err = _resolve_setup_username(request, data, purpose="mfa_setup")
	if err or not username:
		return web.json_response({"error": err or "Authentication required"}, status=401)
	code = (data.get("code") or "").strip().replace(" ", "")
	if not code or len(code) != 6:
		return web.json_response({"error": "Invalid code (must be 6 digits)"}, status=400)
	mfa_temp = (data.get("mfa_temp_token") or "").strip()
	if not users_db.mfa_verify_setup(username, code):
		timeout.add_failed_attempt(ip, username)
		if mfa_temp:
			record_mfa_temp_failure(mfa_temp)
		return web.json_response({"error": "Invalid code or setup failed"}, status=400)
	# Consume setup temp only on success
	if mfa_temp:
		consume_mfa_temp_token(mfa_temp, purpose="mfa_setup", ip=ip, user_agent=ua)
	try:
		send_notification(
			"mfa_enabled",
			"mss-login: MFA enabled",
			f"User {username} enabled MFA from IP: {ip}",
		)
	except Exception:
		pass

	# Issue session after successful enrollment when coming from login setup flow
	# (avoids reusing the same TOTP code on /verify — UI must not double-submit).
	remember_me = truthy_remember_me(data.get("remember_me") or data.get("remember"))
	no_exp = _user_can_have_non_expiring_jwt(username)
	token, _jti = issue_session_token(
		username=username,
		users_db=users_db,
		jwt_auth=jwt_auth,
		amr=["pwd", "otp", "mfa"],
		no_expiration=no_exp,
	)
	timeout.remove_failed_attempts(ip, username)
	user_console_append(username, f"JWT token created for user: {username} (after MFA setup)")
	logger.login_success(ip, username)
	redirect_url = "/loading" if experimental_loading_screen_enabled() else "/"
	_secure = is_https_request(request)
	from ..constants import TOKEN_EXPIRE_MINUTES

	max_age = int(TOKEN_EXPIRE_MINUTES) * 60 if remember_me else None
	if is_browser_navigation(request):
		resp = web.HTTPFound(redirect_url)
		set_auth_cookie(
			resp, token, secure=_secure, remember_me=remember_me, max_age_seconds=max_age
		)
		return resp
	resp = web.json_response(
		{
			"message": "MFA enabled successfully",
			"jwt_token": token,
			"redirect_url": redirect_url,
		}
	)
	set_auth_cookie(resp, token, secure=_secure, remember_me=remember_me, max_age_seconds=max_age)
	return resp


@routes.post("/mss-login/api/mfa/verify")
async def api_mfa_verify(request: web.Request) -> web.Response:
	"""Complete login after password: verify TOTP or recovery code, issue JWT."""
	if not experimental_mfa_enabled():
		return web.json_response(
			{
				"error": "MFA is an experimental feature. Enable experimental_features and experimental.mfa to use it.",
				"code": "mfa_experimental_off",
			},
			status=403,
		)
	from ..constants import DEBUG_MODE, TOKEN_EXPIRE_MINUTES

	data = await _get_request_data(request)
	ip, ua = _client_meta(request)
	mfa_temp = (data.get("mfa_temp_token") or "").strip()
	code = (data.get("code") or "").strip().replace(" ", "")
	backup_code_raw = (
		(data.get("backup_code") or data.get("recovery_code") or "")
		.strip()
		.replace(" ", "")
		.replace("-", "")
		.upper()
	)
	remember_me = truthy_remember_me(data.get("remember_me") or data.get("remember"))
	if not mfa_temp:
		return web.json_response({"error": "mfa_temp_token required"}, status=400)

	entry = peek_mfa_temp_token(mfa_temp, purpose="mfa_verify", ip=ip, user_agent=ua)
	if not entry:
		return web.json_response(
			{"error": "Invalid or expired token. Please log in again."}, status=401
		)
	username = entry.username

	ok = False
	if backup_code_raw:
		ok = users_db.verify_backup_code_and_consume(username, backup_code_raw)
		amr = ["pwd", "otp", "mfa"]
	elif code:
		ok = users_db.verify_totp(username, code)
		amr = ["pwd", "otp", "mfa"]
	else:
		return web.json_response({"error": "Provide code or backup_code"}, status=400)

	if not ok:
		timeout.add_failed_attempt(ip, username)
		record_mfa_temp_failure(mfa_temp)
		return web.json_response({"error": "Invalid code"}, status=400)

	# Consume only on success
	consume_mfa_temp_token(mfa_temp, purpose="mfa_verify", ip=ip, user_agent=ua)

	no_exp = _user_can_have_non_expiring_jwt(username)
	token, _jti = issue_session_token(
		username=username,
		users_db=users_db,
		jwt_auth=jwt_auth,
		amr=amr,
		no_expiration=no_exp,
	)
	timeout.remove_failed_attempts(ip, username)
	if DEBUG_MODE:
		logger.log_jwt_if_debug(token, username)
	else:
		logger.log_jwt_created_console_only(username)
	user_console_append(username, f"JWT token created for user: {username} (after MFA)")
	logger.login_success(ip, username)
	redirect_url = "/loading" if experimental_loading_screen_enabled() else "/"
	_secure = is_https_request(request)
	max_age = int(TOKEN_EXPIRE_MINUTES) * 60 if remember_me else None
	if is_browser_navigation(request):
		resp = web.HTTPFound(redirect_url)
		set_auth_cookie(
			resp, token, secure=_secure, remember_me=remember_me, max_age_seconds=max_age
		)
		return resp
	resp = web.json_response(
		{"message": "Login successful", "jwt_token": token, "redirect_url": redirect_url}
	)
	set_auth_cookie(resp, token, secure=_secure, remember_me=remember_me, max_age_seconds=max_age)
	return resp


@routes.post("/mss-login/api/mfa/recovery-codes/regenerate")
async def api_mfa_regenerate_recovery_codes(request: web.Request) -> web.Response:
	"""Regenerate recovery codes (step-up required). Old set invalidated."""
	guard = _mfa_feature_guard(request)
	if guard:
		return guard
	step = require_step_up(request)
	if step:
		return step
	_uid, username = jwt_auth.resolve_request_identity(request, touch_idle=False)
	if not username:
		return web.json_response({"error": "Authentication required"}, status=401)
	codes = users_db.regenerate_recovery_codes(username)
	if codes is None:
		return web.json_response({"error": "MFA not enabled or user not found"}, status=400)
	try:
		send_notification(
			"mfa_enabled",
			"mss-login: recovery codes regenerated",
			f"User {username} regenerated recovery codes from IP: {get_ip(request)}",
		)
	except Exception:
		pass
	return web.json_response({"recovery_codes": codes, "message": "Save these codes; shown once."})


@routes.post("/mss-login/api/mfa/reset-user")
async def api_mfa_reset_user(request: web.Request) -> web.Response:
	"""Owner-only: reset MFA for a single user; bumps token_version and notifies."""
	guard = _mfa_feature_guard(request)
	if guard:
		return guard
	step = require_step_up(request)
	if step:
		return step
	_uid, actor = jwt_auth.resolve_request_identity(request, touch_idle=False)
	if not actor:
		return web.json_response({"error": "Authentication required"}, status=401)
	_aid, actor_rec = users_db.get_user(actor)
	groups = [g.lower() for g in (actor_rec or {}).get("groups", [])]
	if "owner" not in groups:
		return web.json_response({"error": "Owner only"}, status=403)
	data = await _get_request_data(request)
	target = (data.get("username") or "").strip()
	if not target:
		return web.json_response({"error": "username required"}, status=400)
	if not users_db.reset_mfa_for_user(target):
		return web.json_response({"error": "User not found"}, status=404)
	try:
		send_notification(
			"mfa_disabled",
			"mss-login: MFA reset by owner",
			f"Owner {actor} reset MFA for user {target} from IP: {get_ip(request)}",
		)
	except Exception:
		pass
	return web.json_response({"message": f"MFA reset for {target}; sessions revoked."})


def _user_can_have_non_expiring_jwt(username: str) -> bool:
	_user_id, user_rec = users_db.get_user(username)
	if not user_rec:
		return False
	cfg = access_control._load_group_config()
	for role in [g.lower() for g in user_rec.get("groups", [])]:
		perms = cfg.get(role, {})
		if perms.get("can_have_non_expiring_jwt", False) is True:
			return True
	return False
