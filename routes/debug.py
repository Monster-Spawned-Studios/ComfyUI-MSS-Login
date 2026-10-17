# --- START OF FILE routes/debug.py ---
"""Debug-mode API routes. Registered here to avoid circular import (constants <-> globals)."""

import os
from datetime import UTC, datetime, timezone

from aiohttp import web

from ..constants import (
	DEBUG_LOG_PATH,
	DEBUG_MODE,
	DEBUG_MODE_FROM_ENV,
	filter_debug_messages_enabled,
)
from ..globals import jwt_auth, routes, users_db
from ..utils.log_redactor import redact_log_text


def _authenticated_username(request: web.Request) -> str | None:
	"""Return username for a valid session/API token, else None."""
	token = jwt_auth.get_token_from_request(request)
	if not token:
		return None
	try:
		from ..utils.api_token_store import get_api_token_store

		api_cfg = getattr(jwt_auth, "api_token_store_config", None) or {}
		api_store = get_api_token_store(api_cfg)
		api_user = api_store.get_user_for_token(token)
		if api_user is not None:
			_uid, uname = api_user
			return uname
	except Exception:
		pass
	try:
		payload = jwt_auth.decode_access_token(token)
		username = payload.get("username") if payload else None
		return username if username else None
	except Exception:
		return None


def _is_admin_username(username: str | None) -> bool:
	if not username:
		return False
	try:
		_uid, rec = users_db.get_user(username)
	except Exception:
		return False
	if not rec:
		return False
	groups = [str(g).lower() for g in (rec.get("groups") or [])]
	return bool(rec.get("admin") or "admin" in groups or "owner" in groups)


@routes.get("/mss-login/api/debug-mode")
async def get_debug_mode(request: web.Request) -> web.Response:
	"""Return debug mode status. Unauthenticated callers get a safe false response."""
	username = _authenticated_username(request)
	if not username:
		return web.json_response({"debugMode": False, "filterDebugMessages": True})

	active = bool(DEBUG_MODE or DEBUG_MODE_FROM_ENV)
	filtered = filter_debug_messages_enabled()
	try:
		return web.json_response({"debugMode": active, "filterDebugMessages": filtered})
	except Exception:
		return web.json_response({"debugMode": False, "filterDebugMessages": False})
	finally:
		if active and not filtered:
			print(f"DEBUG_MODE: {active}")


@routes.get("/mss-login/api/debug-log/export")
async def export_redacted_debug_log(request: web.Request) -> web.Response:
	"""
	Export sanitized debug log with secrets and credentials masked for GitHub troubleshooting.
	Requires an authenticated admin/owner session or API token.
	"""
	username = _authenticated_username(request)
	if not username:
		return web.json_response({"error": "Authentication required"}, status=401)
	if not _is_admin_username(username):
		return web.json_response({"error": "Admin authentication required"}, status=403)

	content = ""
	if os.path.isfile(DEBUG_LOG_PATH):
		try:
			with open(DEBUG_LOG_PATH, "r", encoding="utf-8", errors="replace") as f:
				content = f.read()
		except Exception as e:
			content = f"# Error reading debug log file: {e}\n"
	else:
		content = (
			"# MSS-Login Sanitized Debug Log\n"
			f"# Generated: {datetime.now(UTC).isoformat()}\n"
			"# No debug log entries found on disk (DEBUG_MODE may be disabled or log is empty).\n"
		)

	# Apply redactions
	sanitized = redact_log_text(content)

	# Prepend metadata header
	header = (
		"# ========================================================\n"
		"# ComfyUI-MSS-Login Sanitized Debug Log Export\n"
		f"# Exported by: {username}\n"
		f"# Timestamp: {datetime.now(UTC).isoformat()}\n"
		"# Secrets, keys, tokens, and sensitive IPs have been masked.\n"
		"# Safe for GitHub issue attachments.\n"
		"# ========================================================\n\n"
	)
	final_output = header + sanitized

	return web.Response(
		body=final_output,
		content_type="text/plain; charset=utf-8",
		headers={"Content-Disposition": 'attachment; filename="mss-login-sanitized-debug.log"'},
	)


routes.get("/api/mss-login/api/debug-mode")(get_debug_mode)
routes.get("/api/mss-login/api/debug-log/export")(export_redacted_debug_log)
# --- END OF FILE routes/debug.py ---
