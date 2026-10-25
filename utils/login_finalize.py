# --- START OF FILE utils/login_finalize.py ---
"""
Single login-finalization gate for every interactive credential path.

Password, local-login, identity-header, MFA verify, generate_token, and passkey
all flow through here so MFA enforcement cannot be skipped on one path.
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Any, Literal

from aiohttp import web

AmrMethod = Literal["pwd", "otp", "hwk", "user", "mfa", "header", "local"]


@dataclass
class MfaGateResult:
	"""Result of evaluating whether MFA is still required before issuing a session."""

	needs_verify: bool = False
	needs_setup: bool = False
	username: str = ""
	reason: str = ""
	in_grace_period: bool = False
	due_date: str = ""
	days_remaining: int = 0


def _load_group_config() -> dict:
	try:
		from ..globals import access_control

		return access_control._load_group_config() or {}
	except Exception:
		try:
			from globals import access_control  # type: ignore

			return access_control._load_group_config() or {}
		except Exception:
			return {}


def user_groups_lower(user_rec: dict | None) -> list[str]:
	if not user_rec:
		return []
	return [str(g).lower() for g in (user_rec.get("groups") or [])]


def user_is_admin_or_owner(user_rec: dict | None) -> bool:
	if not user_rec:
		return False
	if user_rec.get("admin"):
		return True
	groups = user_groups_lower(user_rec)
	return "admin" in groups or "owner" in groups


def any_group_allows_passwordless(
	username: str, users_db: Any, group_cfg: dict | None = None
) -> bool:
	"""True if any group has passwordless_passkey=True (default False)."""
	_uid, user_rec = users_db.get_user(username)
	if not user_rec:
		return False
	cfg = group_cfg if group_cfg is not None else _load_group_config()
	for role in user_groups_lower(user_rec):
		perms = cfg.get(role) or {}
		if perms.get("passwordless_passkey", False) is True:
			return True
	return False


def any_group_requires_mfa(username: str, users_db: Any, group_cfg: dict | None = None) -> bool:
	"""True if any of the user's groups has mfa_required=True."""
	_uid, user_rec = users_db.get_user(username)
	if not user_rec:
		return False
	cfg = group_cfg if group_cfg is not None else _load_group_config()
	for role in user_groups_lower(user_rec):
		perms = cfg.get(role) or {}
		if perms.get("mfa_required", False) is True:
			return True
	return False


def mfa_disabled_applies_to_user(user_rec: dict | None) -> bool:
	"""
	Return True if MFA_DISABLED may skip MFA for this user.

	Admins/owners are never skipped unless MFA_DISABLED_FORCE_ALL is set.
	"""
	try:
		from .. import constants as constants_module
	except ImportError:
		import constants as constants_module  # type: ignore

	if not getattr(constants_module, "MFA_DISABLED", False):
		return False
	if user_is_admin_or_owner(user_rec):
		force = bool(getattr(constants_module, "MFA_DISABLED_FORCE_ALL", False))
		return force
	return True


def evaluate_mfa_gate(
	username: str,
	users_db: Any,
	group_cfg: dict | None = None,
	*,
	experimental_on: bool | None = None,
) -> MfaGateResult:
	"""
	Decide whether interactive issuance must stop for MFA verify or setup.

	Fail closed for admins/owners and any group with mfa_required.
	"""
	try:
		from ..constants import experimental_mfa_enabled
	except ImportError:
		from constants import experimental_mfa_enabled  # type: ignore

	_uid, user_rec = users_db.get_user(username)
	if not user_rec:
		return MfaGateResult(username=username, reason="user_not_found")

	if experimental_on is None:
		experimental_on = experimental_mfa_enabled()

	if mfa_disabled_applies_to_user(user_rec):
		return MfaGateResult(username=username, reason="mfa_disabled")

	# Feature off: enrolled users still blocked from silent bypass at verify;
	# here we still require verify if enrolled so they are not issued a session
	# without a second factor when operators turned the flag off accidentally.
	mfa_enabled = False
	try:
		mfa_enabled = bool(users_db.get_mfa_enabled(username))
	except Exception:
		mfa_enabled = False

	has_passkeys = False
	try:
		import sys

		wc = sys.modules.get("utils.webauthn_credentials") or sys.modules.get(
			"mss_login.utils.webauthn_credentials"
		)
		if wc is None:
			try:
				from . import webauthn_credentials as wc
			except ImportError:
				from utils import webauthn_credentials as wc  # type: ignore
		repo = wc.get_webauthn_repo()
		user_id = user_rec.get("user_id") or user_rec.get("id") or _uid
		if user_id and repo.count_active_for_user(user_id) > 0:
			has_passkeys = True
	except Exception:
		has_passkeys = False

	if mfa_enabled or has_passkeys:
		return MfaGateResult(needs_verify=True, username=username, reason="enrolled")

	if not experimental_on:
		return MfaGateResult(username=username, reason="experimental_off")

	is_priv = user_is_admin_or_owner(user_rec)
	role_req = any_group_requires_mfa(username, users_db, group_cfg)
	if is_priv or role_req:
		try:
			from datetime import UTC, datetime, timedelta
			from ..constants import (
				get_mfa_enforced_at,
				get_mfa_grace_period_days,
				set_mfa_enforced_at,
			)
		except ImportError:
			from datetime import UTC, datetime, timedelta
			from constants import (  # type: ignore
				get_mfa_enforced_at,
				get_mfa_grace_period_days,
				set_mfa_enforced_at,
			)

		grace_days = get_mfa_grace_period_days()
		if grace_days > 0:
			enforced_at_str = get_mfa_enforced_at()
			if not enforced_at_str:
				enforced_at_str = set_mfa_enforced_at()
			try:
				enforced_dt = datetime.fromisoformat(enforced_at_str.replace("Z", "+00:00"))
			except Exception:
				enforced_dt = datetime.now(UTC)
			due_dt = enforced_dt + timedelta(days=grace_days)
			now = datetime.now(UTC)
			if now < due_dt:
				remaining_days = max(
					1, (due_dt - now).days + (1 if (due_dt - now).seconds > 0 else 0)
				)
				due_date_str = due_dt.strftime("%Y-%m-%d")
				return MfaGateResult(
					needs_setup=False,
					username=username,
					reason="mfa_grace_period",
					in_grace_period=True,
					due_date=due_date_str,
					days_remaining=remaining_days,
				)
		return MfaGateResult(
			needs_setup=True,
			username=username,
			reason="admin" if is_priv else "role_mfa_required",
		)
	return MfaGateResult(username=username, reason="ok")


def issue_session_token(
	*,
	username: str,
	users_db: Any,
	jwt_auth: Any,
	amr: list[str],
	auth_time: int | None = None,
	no_expiration: bool | None = None,
) -> tuple[str, str | None]:
	"""
	Create a session JWT with token_version, amr, auth_time; register jti.

	Returns (raw_token, jti).
	"""
	try:
		from ..constants import SESSION_TOKEN_STORE_CONFIG
	except ImportError:
		from constants import SESSION_TOKEN_STORE_CONFIG  # type: ignore
	from .session_token_store import get_session_token_store

	user_id, user_rec = users_db.get_user(username)
	if not user_id:
		raise ValueError("user_not_found")
	tv = int(user_rec.get("token_version") or 0) if user_rec else 0
	if no_expiration is None:
		no_expiration = False
	now = int(auth_time if auth_time is not None else time.time())
	token = jwt_auth.create_access_token(
		{
			"id": user_id,
			"username": username,
			"token_version": tv,
			"amr": list(amr),
			"auth_time": now,
		},
		no_expiration=bool(no_expiration),
	)
	jti = None
	try:
		from datetime import UTC, datetime

		payload = jwt_auth.decode_access_token(token)
		jti = payload.get("jti")
		exp = payload.get("exp")
		exp_at_iso = datetime.fromtimestamp(exp, tz=UTC).isoformat() if exp else None
		if jti:
			get_session_token_store(SESSION_TOKEN_STORE_CONFIG).register_session(
				jti, user_id, username, exp_at_iso
			)
	except Exception:
		pass
	return token, jti


def require_step_up(request: web.Request, *, max_age_seconds: int = 300) -> web.Response | None:
	"""
	Return a 401/403 JSON response if the session is missing or auth_time is stale.
	Returns None when step-up is satisfied.
	"""
	try:
		from ..globals import jwt_auth
	except ImportError:
		from globals import jwt_auth  # type: ignore

	token = jwt_auth.get_token_from_request(request)
	if not token:
		return web.json_response(
			{"error": "step_up_required", "message": "Re-authenticate"}, status=401
		)
	try:
		# Prefer identity resolution (revocation + token_version)
		user_id, username = jwt_auth.resolve_token_identity(token, touch_idle=False)
		if not user_id or not username:
			return web.json_response({"error": "step_up_required"}, status=401)
		payload = jwt_auth.decode_access_token(token)
	except Exception:
		return web.json_response({"error": "step_up_required"}, status=401)
	auth_time = payload.get("auth_time")
	if auth_time is None:
		return web.json_response(
			{"error": "step_up_required", "message": "Fresh authentication required"}, status=403
		)
	try:
		age = int(time.time()) - int(auth_time)
	except (TypeError, ValueError):
		return web.json_response({"error": "step_up_required"}, status=403)
	if age < 0 or age > max_age_seconds:
		return web.json_response(
			{"error": "step_up_required", "message": "Fresh authentication required"}, status=403
		)
	return None


def log_mfa_disabled_emergency(username: str, ip: str) -> None:
	"""Log + ntfy when MFA_DISABLED skips MFA for a non-privileged user."""
	try:
		try:
			from ..globals import logger
		except ImportError:
			from globals import logger  # type: ignore
		from .ntfy_notifier import send_notification

		logger.warning(f"[MFA_DISABLED] Skipping MFA enforcement for user={username!r} ip={ip!r}")
		send_notification(
			"mfa_disabled",
			"mss-login: MFA_DISABLED active",
			f"MFA bypassed for user '{username}' from IP {ip} (emergency switch).",
		)
	except Exception:
		pass
