# --- START OF FILE routes/me.py ---
"""API endpoints for current user: sessions (JWT list), current-token (reveal), revoke."""

from aiohttp import web

from ..constants import SESSION_TOKEN_STORE_CONFIG
from ..globals import jwt_auth, routes, users_db
from ..utils.session_token_store import get_session_token_store
from ..utils.user_console_log import get_lines as get_user_console_lines


@routes.get("/mss-login/api/is-https")
async def get_is_https(request: web.Request) -> web.Response:
	"""Return whether the request was over HTTPS (for 'eye to reveal' check behind reverse proxy)."""
	proto = request.headers.get("X-Forwarded-Proto", "").strip().lower()
	if proto:
		is_https = proto == "https"
	else:
		is_https = request.url.scheme == "https"
	return web.json_response({"is_https": is_https})


def _get_username_from_request(request: web.Request):
	"""Return current username from request (set by JWT middleware)."""
	return request.get("user")


@routes.get("/mss-login/me/console")
async def get_me_console(request: web.Request) -> web.Response:
	"""Return the current user's console log lines."""
	username = _get_username_from_request(request)
	if not username:
		return web.json_response({"error": "Authentication required"}, status=401)
	lines = get_user_console_lines(username)
	return web.json_response({"lines": lines})


@routes.get("/mss-login/me/sessions")
async def get_me_sessions(request: web.Request) -> web.Response:
	"""Return list of session tokens for the current user (jti, created_at_iso, exp_at_iso, is_current)."""
	username = _get_username_from_request(request)
	if not username:
		return web.json_response({"error": "Authentication required"}, status=401)
	token = jwt_auth.get_token_from_request(request)
	current_jti = None
	if token and token.count(".") >= 2:
		try:
			payload = jwt_auth.decode_access_token(token)
			current_jti = payload.get("jti")
		except Exception:
			pass
	store = get_session_token_store(SESSION_TOKEN_STORE_CONFIG)
	sessions = store.list_sessions_for_user(username)
	out = []
	for s in sessions:
		jti = s.get("jti")
		out.append(
			{
				"jti": jti,
				"created_at_iso": s.get("created_at_iso"),
				"last_used_at_iso": s.get("last_used_at_iso"),
				"exp_at_iso": s.get("exp_at_iso"),
				"is_current": jti == current_jti,
			}
		)
	return web.json_response({"sessions": out})


@routes.get("/mss-login/me/current-token")
async def get_me_current_token(request: web.Request) -> web.Response:
	"""Return the current request's token (for 'eye to reveal'). Auth required."""
	username = _get_username_from_request(request)
	if not username:
		return web.json_response({"error": "Authentication required"}, status=401)
	token = jwt_auth.get_token_from_request(request)
	if not token:
		return web.json_response({"error": "No token in request"}, status=400)
	proto = request.headers.get("X-Forwarded-Proto", "").strip().lower()
	is_https = proto == "https" if proto else request.url.scheme == "https"
	return web.json_response({"token": token, "is_https": is_https})


@routes.post("/mss-login/me/sessions/revoke")
async def post_me_sessions_revoke(request: web.Request) -> web.Response:
	"""Revoke a session by jti. jti must belong to the current user."""
	username = _get_username_from_request(request)
	if not username:
		return web.json_response({"error": "Authentication required"}, status=401)
	try:
		data = await request.json()
		jti = (data.get("jti") or "").strip()
		if not jti:
			return web.json_response({"error": "jti required"}, status=400)
	except Exception:
		return web.json_response({"error": "Invalid JSON"}, status=400)
	store = get_session_token_store(SESSION_TOKEN_STORE_CONFIG)
	sessions = store.list_sessions_for_user(username)
	if not any(s.get("jti") == jti for s in sessions):
		return web.json_response({"error": "Session not found or already revoked"}, status=404)
	store.revoke_session(jti)
	return web.json_response({"status": "ok"})
