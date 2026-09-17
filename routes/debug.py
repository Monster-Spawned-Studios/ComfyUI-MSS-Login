# --- START OF FILE routes/debug.py ---
"""Debug-mode API routes. Registered here to avoid circular import (constants <-> globals)."""

import os
from datetime import datetime, timezone
from aiohttp import web

from ..constants import (
	DEBUG_LOG_PATH,
	DEBUG_MODE,
	DEBUG_MODE_FROM_ENV,
	filter_debug_messages_enabled,
)
from ..globals import routes
from ..utils import jwt_auth
from ..utils.log_redactor import redact_log_text


@routes.get("/mss-login/api/debug-mode")
async def get_debug_mode(
	request: web.Request = None,  # pyright: ignore[reportUnusedParameter, reportMissingParameter]
) -> web.json_response:
	"""Return the debug mode and filter status from the environment/config."""
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
	"""
	# Check authentication if available
	token = jwt_auth.get_token_from_request(request)
	username = "anonymous"
	if token:
		try:
			payload = jwt_auth.decode_access_token(token)
			if payload and payload.get("username"):
				username = payload["username"]
		except Exception:
			pass

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
			f"# Generated: {datetime.now(timezone.utc).isoformat()}\n"
			"# No debug log entries found on disk (DEBUG_MODE may be disabled or log is empty).\n"
		)

	# Apply redactions
	sanitized = redact_log_text(content)

	# Prepend metadata header
	header = (
		"# ========================================================\n"
		"# ComfyUI-MSS-Login Sanitized Debug Log Export\n"
		f"# Exported by: {username}\n"
		f"# Timestamp: {datetime.now(timezone.utc).isoformat()}\n"
		"# Secrets, keys, tokens, and sensitive IPs have been masked.\n"
		"# Safe for GitHub issue attachments.\n"
		"# ========================================================\n\n"
	)
	final_output = header + sanitized

	return web.Response(
		body=final_output,
		content_type="text/plain; charset=utf-8",
		headers={
			"Content-Disposition": 'attachment; filename="mss-login-sanitized-debug.log"'
		},
	)


routes.get("/api/mss-login/api/debug-mode")(get_debug_mode)
routes.get("/api/mss-login/api/debug-log/export")(export_redacted_debug_log)
# --- END OF FILE routes/debug.py ---
