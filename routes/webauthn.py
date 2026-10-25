# --- START OF FILE routes/webauthn.py ---
"""WebAuthn / passkey ceremonies (second factor)."""

from __future__ import annotations

import json
import time
import uuid

from aiohttp import web

from ..globals import jwt_auth, logger, routes, timeout, users_db
from ..utils.auth_cookies import set_auth_cookie, truthy_remember_me
from ..utils.ip_filter import get_ip, is_https_request
from ..utils.login_finalize import (
	any_group_allows_passwordless,
	issue_session_token,
	require_step_up,
	user_is_admin_or_owner,
)
from ..utils.mfa_temp_store import consume_mfa_temp_token, peek_mfa_temp_token
from ..utils.ntfy_notifier import send_notification
from ..utils.webauthn_challenge_store import consume_ceremony, create_ceremony
from ..utils.webauthn_config import (
	get_rp_id_for_origin,
	get_webauthn_config,
	is_secure_webauthn_origin,
	origin_allowed,
)
from ..utils.webauthn_credentials import WebAuthnCredential, get_webauthn_repo
from ..utils.webauthn_guard import (
	AttestationConveyancePreference,
	AuthenticatorSelectionCriteria,
	PublicKeyCredentialDescriptor,
	ResidentKeyRequirement,
	UserVerificationRequirement,
	generate_authentication_options,
	generate_registration_options,
	options_to_json,
	passkeys_enabled,
	passkeys_unavailable_reason,
	verify_authentication_response,
	verify_registration_response,
)

CEREMONY_COOKIE = "mss_webauthn_ceremony"


def _require_passkeys() -> web.Response | None:
	if not passkeys_enabled():
		return web.json_response(
			{"error": "Passkeys unavailable", "reason": passkeys_unavailable_reason()}, status=503
		)
	return None


def _set_ceremony_cookie(resp: web.Response, ceremony_id: str, secure: bool) -> None:
	resp.set_cookie(
		CEREMONY_COOKIE,
		ceremony_id,
		httponly=True,
		secure=secure,
		samesite="Strict",
		max_age=300,
		path="/",
	)


def _clear_ceremony_cookie(resp: web.Response, secure: bool) -> None:
	resp.del_cookie(CEREMONY_COOKIE, path="/")


async def _json_body(request: web.Request) -> dict:
	data = request.get("_sanitized_data") or {}
	try:
		if request.can_read_body and "application/json" in (request.content_type or ""):
			body = await request.json()
			if isinstance(body, dict):
				data = {**data, **body}
	except Exception:
		pass
	return data


def _extract_origin(request: web.Request) -> str:
	origin = (request.headers.get("Origin") or "").strip()
	if not origin:
		referer = (request.headers.get("Referer") or "").strip()
		if referer:
			try:
				from urllib.parse import urlsplit

				parts = urlsplit(referer)
				if parts.scheme and parts.netloc:
					origin = f"{parts.scheme}://{parts.netloc}"
			except Exception:
				pass
	if not origin:
		scheme = request.headers.get("X-Forwarded-Proto") or request.scheme or "http"
		host = (
			request.headers.get("X-Forwarded-Host")
			or request.headers.get("Host")
			or getattr(request, "host", "")
		)
		if host:
			origin = f"{scheme}://{host}"
	return origin.rstrip("/")


@routes.get("/mss-login/api/webauthn/status")
async def webauthn_status(request: web.Request) -> web.Response:
	origin = _extract_origin(request)
	cfg = get_webauthn_config()
	rp_id = get_rp_id_for_origin(origin) if origin else cfg["rp_id"]
	return web.json_response(
		{
			"available": passkeys_enabled(),
			"secure_origin": is_secure_webauthn_origin(origin) if origin else False,
			"rp_id": rp_id,
			"origins": cfg.get("origins") or [],
			"reason": None if passkeys_enabled() else passkeys_unavailable_reason(),
		}
	)


@routes.post("/mss-login/webauthn/register/options")
async def register_options(request: web.Request) -> web.Response:
	guard = _require_passkeys()
	if guard:
		return guard
	step = require_step_up(request)
	if step:
		return step
	_uid, username = jwt_auth.resolve_request_identity(request, touch_idle=False)
	if not username:
		return web.json_response({"error": "Authentication required"}, status=401)
	user_id, user_rec = users_db.get_user(username)
	if not user_id:
		return web.json_response({"error": "User not found"}, status=404)
	origin = _extract_origin(request)
	if not origin_allowed(origin):
		return web.json_response({"error": "Origin not allowed for passkeys"}, status=403)
	cfg = get_webauthn_config()
	rp_id = get_rp_id_for_origin(origin)
	repo = get_webauthn_repo()
	handle = repo.ensure_user_handle(user_id)
	existing = [
		PublicKeyCredentialDescriptor(id=c.credential_id)
		for c in repo.list_for_user(user_id)
		if c.rp_id == rp_id
	]
	opts = generate_registration_options(
		rp_id=rp_id,
		rp_name=cfg["rp_name"],
		user_id=handle,
		user_name=username,
		user_display_name=username,
		attestation=AttestationConveyancePreference.NONE,
		authenticator_selection=AuthenticatorSelectionCriteria(
			resident_key=ResidentKeyRequirement.PREFERRED,
			user_verification=UserVerificationRequirement.REQUIRED,
		),
		exclude_credentials=existing or None,
	)
	cid = create_ceremony(
		challenge=opts.challenge,
		ceremony_type="register",
		user_id=user_id,
		username=username,
		rp_id=rp_id,
		expected_origin=origin,
	)
	resp = web.json_response(json.loads(options_to_json(opts)))
	_set_ceremony_cookie(resp, cid, secure=is_https_request(request))
	return resp


@routes.post("/mss-login/webauthn/register/verify")
async def register_verify(request: web.Request) -> web.Response:
	guard = _require_passkeys()
	if guard:
		return guard
	step = require_step_up(request)
	if step:
		return step
	_uid, username = jwt_auth.resolve_request_identity(request, touch_idle=False)
	if not username:
		return web.json_response({"error": "Authentication required"}, status=401)
	user_id, _ = users_db.get_user(username)
	ceremony_id = request.cookies.get(CEREMONY_COOKIE) or ""
	ceremony = consume_ceremony(ceremony_id, ceremony_type="register")
	if not ceremony or ceremony.user_id != user_id:
		return web.json_response({"error": "Invalid or expired ceremony"}, status=400)
	body = await _json_body(request)
	credential = body.get("credential") or body
	name = (body.get("name") or "Passkey").strip()[:64] or "Passkey"
	try:
		verification = verify_registration_response(
			credential=credential,
			expected_challenge=ceremony.challenge,
			expected_rp_id=ceremony.rp_id,
			expected_origin=ceremony.expected_origin,
			require_user_verification=True,
		)
	except Exception as e:
		logger.warning(f"[webauthn] register verify failed: {type(e).__name__}")
		return web.json_response({"error": "Registration verification failed"}, status=400)
	repo = get_webauthn_repo()
	cred = WebAuthnCredential(
		id=uuid.uuid4().hex,
		user_id=user_id,
		credential_id=verification.credential_id,
		public_key=verification.credential_public_key,
		sign_count=int(verification.sign_count or 0),
		transports=list(getattr(verification, "transports", None) or []),
		aaguid=str(getattr(verification, "aaguid", "") or ""),
		backup_eligible=bool(
			getattr(verification, "credential_device_type", None) == "multi_device"
		)
		or bool(getattr(verification, "credential_backed_up", False)),
		backup_state=bool(getattr(verification, "credential_backed_up", False)),
		rp_id=ceremony.rp_id,
		name=name,
		created_at=time.time(),
		last_used_at=None,
		revoked_at=None,
	)
	# Prefer flags from verification if present
	if hasattr(verification, "credential_device_type"):
		cred.backup_eligible = str(verification.credential_device_type) == "multi_device"
	repo.add(cred)
	try:
		send_notification(
			"settings_changed",
			"mss-login: passkey registered",
			f"User {username} registered a passkey from IP: {get_ip(request)}",
		)
	except Exception:
		pass
	resp = web.json_response({"message": "Passkey registered", "id": cred.id, "name": cred.name})
	_clear_ceremony_cookie(resp, is_https_request(request))
	return resp


@routes.post("/mss-login/webauthn/passwordless/options")
async def passwordless_options(request: web.Request) -> web.Response:
	"""Discoverable-credential options (empty allowCredentials). Policy off by default."""
	guard = _require_passkeys()
	if guard:
		return guard
	origin = _extract_origin(request)
	cfg = get_webauthn_config()
	if not origin_allowed(origin):
		return web.json_response({"error": "Origin not allowed"}, status=403)
	rp_id = get_rp_id_for_origin(origin)
	opts = generate_authentication_options(
		rp_id=rp_id,
		allow_credentials=[],  # discoverable / empty allow list
		user_verification=UserVerificationRequirement.REQUIRED,
	)
	cid = create_ceremony(
		challenge=opts.challenge,
		ceremony_type="passwordless",
		user_id=None,
		username=None,
		rp_id=rp_id,
		expected_origin=origin,
	)
	resp = web.json_response(json.loads(options_to_json(opts)))
	_set_ceremony_cookie(resp, cid, secure=is_https_request(request))
	return resp


@routes.post("/mss-login/webauthn/passwordless/verify")
async def passwordless_verify(request: web.Request) -> web.Response:
	"""Primary passwordless login: UV passkey counts as both factors when group policy allows."""
	guard = _require_passkeys()
	if guard:
		return guard
	ip = get_ip(request)
	body = await _json_body(request)
	ceremony_id = request.cookies.get(CEREMONY_COOKIE) or ""
	ceremony = consume_ceremony(ceremony_id, ceremony_type="passwordless")
	if not ceremony:
		timeout.add_failed_attempt(ip, None)
		return web.json_response({"error": "Authentication failed"}, status=401)
	credential = body.get("credential") or body
	repo = get_webauthn_repo()
	try:
		from webauthn.helpers import base64url_to_bytes

		stored = repo.get_by_credential_id(base64url_to_bytes(credential["id"]))
	except Exception:
		stored = None
	if not stored or stored.revoked_at:
		timeout.add_failed_attempt(ip, None)
		return web.json_response({"error": "Authentication failed"}, status=401)
	# Identify user via user_handle if present
	user_handle = None
	try:
		uh = (credential.get("response") or {}).get("userHandle")
		if uh:
			from ..utils.webauthn_credentials import b64url_decode

			user_handle = b64url_decode(uh) if isinstance(uh, str) else uh
	except Exception:
		user_handle = None
	if user_handle:
		owner = repo.get_user_id_for_handle(user_handle)
		if owner and owner != stored.user_id:
			timeout.add_failed_attempt(ip, None)
			return web.json_response({"error": "Authentication failed"}, status=401)
	try:
		verification = verify_authentication_response(
			credential=credential,
			expected_challenge=ceremony.challenge,
			expected_rp_id=ceremony.rp_id,
			expected_origin=ceremony.expected_origin,
			credential_public_key=stored.public_key,
			credential_current_sign_count=stored.sign_count,
			require_user_verification=True,
		)
	except Exception:
		timeout.add_failed_attempt(ip, None)
		return web.json_response({"error": "Authentication failed"}, status=401)
	new_count = int(verification.new_sign_count or 0)
	if stored.sign_count > 0 and new_count > 0 and new_count <= stored.sign_count:
		try:
			send_notification(
				"login_failure",
				"mss-login: passkey clone suspected",
				f"passwordless clone user_id={stored.user_id} ip={ip}",
			)
		except Exception:
			pass
		return web.json_response({"error": "Authentication failed"}, status=401)
	repo.update_sign_count(
		stored.credential_id,
		new_count,
		backup_state=bool(getattr(verification, "credential_backed_up", stored.backup_state)),
	)
	users_db.load_users()
	username = None
	user_rec = None
	for uid, u in users_db.users.items():
		if uid == stored.user_id:
			username = u.get("username")
			user_rec = u
			break
	if not username:
		return web.json_response({"error": "Authentication failed"}, status=401)
	if not any_group_allows_passwordless(username, users_db):
		timeout.add_failed_attempt(ip, username)
		return web.json_response({"error": "Authentication failed"}, status=401)
	cfg = get_webauthn_config()
	if cfg.get("require_device_bound_for_admins") and user_is_admin_or_owner(user_rec):
		if stored.synced or stored.backup_eligible:
			return web.json_response({"error": "Authentication failed"}, status=401)
	token, _ = issue_session_token(
		username=username, users_db=users_db, jwt_auth=jwt_auth, amr=["hwk", "user"]
	)
	timeout.remove_failed_attempts(ip, username)
	from ..constants import TOKEN_EXPIRE_MINUTES, experimental_loading_screen_enabled

	remember_me = truthy_remember_me(body.get("remember_me"))
	redirect_url = "/loading" if experimental_loading_screen_enabled() else "/"
	resp = web.json_response(
		{"message": "Login successful", "jwt_token": token, "redirect_url": redirect_url}
	)
	max_age = int(TOKEN_EXPIRE_MINUTES) * 60 if remember_me else None
	set_auth_cookie(
		resp,
		token,
		secure=is_https_request(request),
		remember_me=remember_me,
		max_age_seconds=max_age,
	)
	_clear_ceremony_cookie(resp, is_https_request(request))
	return resp


@routes.post("/mss-login/webauthn/auth/options")
async def auth_options(request: web.Request) -> web.Response:
	"""Public auth options. Does not reveal whether the username exists."""
	guard = _require_passkeys()
	if guard:
		return guard
	body = await _json_body(request)
	username = (body.get("username") or "").strip()
	mfa_temp = (body.get("mfa_temp_token") or "").strip()
	ip = get_ip(request)
	ua = (request.headers.get("User-Agent") or "").strip()
	if not username and mfa_temp:
		entry = peek_mfa_temp_token(mfa_temp, purpose="mfa_verify", ip=ip, user_agent=ua)
		if entry:
			username = entry.username
	origin = _extract_origin(request)
	cfg = get_webauthn_config()
	rp_id = get_rp_id_for_origin(origin) if origin else cfg["rp_id"]
	# Always return options-shaped payload; empty allowCredentials for unknown users
	allow = []
	user_id = None
	if username:
		uid, _ = users_db.get_user(username)
		if uid:
			user_id = uid
			repo = get_webauthn_repo()
			for c in repo.list_for_user(uid):
				if (c.rp_id == rp_id or c.rp_id == cfg["rp_id"]) and not c.revoked_at:
					allow.append(PublicKeyCredentialDescriptor(id=c.credential_id))
	# Enumeration-safe: if no credentials, still generate options with empty allow list
	opts = generate_authentication_options(
		rp_id=rp_id,
		allow_credentials=allow or None,
		user_verification=UserVerificationRequirement.REQUIRED,
	)
	expected_origin = (
		origin if origin_allowed(origin) else (cfg["origins"][0] if cfg["origins"] else "")
	)
	cid = create_ceremony(
		challenge=opts.challenge,
		ceremony_type="authenticate",
		user_id=user_id,
		username=username or None,
		rp_id=rp_id,
		expected_origin=expected_origin,
	)
	resp = web.json_response(json.loads(options_to_json(opts)))
	_set_ceremony_cookie(resp, cid, secure=is_https_request(request))
	return resp


@routes.post("/mss-login/webauthn/auth/verify")
async def auth_verify(request: web.Request) -> web.Response:
	"""Complete second-factor (or passwordless in PR3) with a passkey."""
	guard = _require_passkeys()
	if guard:
		return guard
	ip = get_ip(request)
	ua = (request.headers.get("User-Agent") or "").strip()
	body = await _json_body(request)
	ceremony_id = request.cookies.get(CEREMONY_COOKIE) or ""
	ceremony = consume_ceremony(ceremony_id, ceremony_type="authenticate")
	if not ceremony:
		timeout.add_failed_attempt(ip, (body.get("username") or None))
		return web.json_response({"error": "Invalid or expired ceremony"}, status=400)
	credential = body.get("credential") or body
	mfa_temp = (body.get("mfa_temp_token") or "").strip()
	repo = get_webauthn_repo()
	# Resolve credential from assertion id
	cred_id_b64 = None
	try:
		cred_id_b64 = credential.get("id") or credential.get("rawId")
	except Exception:
		pass
	stored = None
	if isinstance(credential, dict) and credential.get("rawId"):
		from ..utils.webauthn_credentials import b64url_decode

	try:
		from webauthn.helpers import base64url_to_bytes

		cred_bytes = base64url_to_bytes(credential["id"])
		stored = repo.get_by_credential_id(cred_bytes)
	except Exception:
		try:
			raw = credential.get("rawId") if isinstance(credential, dict) else None
			if isinstance(raw, str):
				stored = repo.get_by_credential_id(b64url_decode(raw))
		except Exception:
			stored = None
	if not stored or stored.revoked_at:
		timeout.add_failed_attempt(ip, ceremony.username)
		return web.json_response({"error": "Authentication failed"}, status=401)
	if stored.rp_id != ceremony.rp_id:
		return web.json_response({"error": "Authentication failed"}, status=401)
	try:
		verification = verify_authentication_response(
			credential=credential,
			expected_challenge=ceremony.challenge,
			expected_rp_id=ceremony.rp_id,
			expected_origin=ceremony.expected_origin,
			credential_public_key=stored.public_key,
			credential_current_sign_count=stored.sign_count,
			require_user_verification=True,
		)
	except Exception:
		timeout.add_failed_attempt(ip, ceremony.username)
		return web.json_response({"error": "Authentication failed"}, status=401)

	new_count = int(verification.new_sign_count or 0)
	# Clone detection
	if stored.sign_count > 0 and new_count > 0 and new_count <= stored.sign_count:
		try:
			send_notification(
				"login_failure",
				"mss-login: passkey clone suspected",
				f"sign_count did not increase for user_id={stored.user_id} ip={ip}",
			)
		except Exception:
			pass
		timeout.add_failed_attempt(ip, ceremony.username)
		return web.json_response({"error": "Authentication failed"}, status=401)

	backup_state = bool(getattr(verification, "credential_backed_up", stored.backup_state))
	repo.update_sign_count(stored.credential_id, new_count, backup_state=backup_state)

	# Resolve username from stored credential owner
	# users_db is keyed by username — find username for user_id
	username = ceremony.username
	if not username:
		for uid, u in (users_db.load_users() or {}).items():
			if uid == stored.user_id:
				username = u.get("username")
				break
	if not username:
		# scan get by iterating
		users_db.load_users()
		for uid, u in users_db.users.items():
			if uid == stored.user_id:
				username = u.get("username")
				break
	if not username:
		return web.json_response({"error": "Authentication failed"}, status=401)

	# Second-factor path: require mfa_temp from password login
	if mfa_temp:
		entry = peek_mfa_temp_token(mfa_temp, purpose="mfa_verify", ip=ip, user_agent=ua)
		if not entry or entry.username != username:
			return web.json_response({"error": "Invalid MFA session"}, status=401)
		consume_mfa_temp_token(mfa_temp, purpose="mfa_verify", ip=ip, user_agent=ua)

	amr = ["hwk", "user", "mfa"]
	if mfa_temp:
		amr = ["pwd", "hwk", "user", "mfa"]
	token, _ = issue_session_token(username=username, users_db=users_db, jwt_auth=jwt_auth, amr=amr)
	timeout.remove_failed_attempts(ip, username)
	remember_me = truthy_remember_me(body.get("remember_me"))
	from ..constants import TOKEN_EXPIRE_MINUTES, experimental_loading_screen_enabled

	redirect_url = "/loading" if experimental_loading_screen_enabled() else "/"
	resp = web.json_response(
		{"message": "Login successful", "jwt_token": token, "redirect_url": redirect_url}
	)
	max_age = int(TOKEN_EXPIRE_MINUTES) * 60 if remember_me else None
	set_auth_cookie(
		resp,
		token,
		secure=is_https_request(request),
		remember_me=remember_me,
		max_age_seconds=max_age,
	)
	_clear_ceremony_cookie(resp, is_https_request(request))
	return resp


@routes.get("/mss-login/webauthn/credentials")
async def list_credentials(request: web.Request) -> web.Response:
	_uid, username = jwt_auth.resolve_request_identity(request, touch_idle=False)
	if not username:
		return web.json_response({"error": "Authentication required"}, status=401)
	user_id, _ = users_db.get_user(username)
	origin = (request.headers.get("Origin") or "").strip()
	cfg = get_webauthn_config()
	rp_id = get_rp_id_for_origin(origin) if origin else cfg["rp_id"]
	repo = get_webauthn_repo()
	items = []
	for c in repo.list_for_user(user_id):
		if c.rp_id != rp_id and c.rp_id != cfg["rp_id"]:
			continue
		items.append(
			{
				"id": c.id,
				"name": c.name,
				"synced": c.synced,
				"device_bound": not c.synced,
				"last_used_at": c.last_used_at,
				"created_at": c.created_at,
			}
		)
	return web.json_response({"credentials": items})


@routes.delete("/mss-login/webauthn/credentials")
async def delete_credential(request: web.Request) -> web.Response:
	guard = _require_passkeys()
	if guard:
		return guard
	step = require_step_up(request)
	if step:
		return step
	_uid, username = jwt_auth.resolve_request_identity(request, touch_idle=False)
	if not username:
		return web.json_response({"error": "Authentication required"}, status=401)
	user_id, user_rec = users_db.get_user(username)
	body = await _json_body(request)
	cred_row_id = (body.get("id") or "").strip()
	repo = get_webauthn_repo()
	active = repo.list_for_user(user_id)
	target = next((c for c in active if c.id == cred_row_id), None)
	if not target:
		return web.json_response({"error": "Not found"}, status=404)
	# Cannot remove last passkey when it is the only strong factor and MFA is enforced
	has_totp = users_db.get_mfa_enabled(username)
	mfa_enforced = user_is_admin_or_owner(user_rec) or has_totp
	if mfa_enforced and not has_totp and len(active) <= 1:
		return web.json_response(
			{"error": "Cannot remove last strong factor while MFA is required"}, status=403
		)
	repo.revoke(target.credential_id, user_id)
	users_db.bump_token_version(username)
	try:
		send_notification(
			"settings_changed",
			"mss-login: passkey revoked",
			f"User {username} revoked a passkey from IP: {get_ip(request)}",
		)
	except Exception:
		pass
	return web.json_response({"message": "Passkey revoked; sessions invalidated."})
