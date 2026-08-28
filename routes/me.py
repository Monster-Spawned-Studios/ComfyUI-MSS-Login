# --- START OF FILE routes/me.py ---
"""API endpoints for current user: sessions (JWT list), current-token (reveal), revoke."""

from aiohttp import web

from ..constants import SESSION_TOKEN_STORE_CONFIG
from ..globals import jwt_auth, routes, users_db
from ..utils.avatar import MAX_UPLOAD_BYTES, avatar_path, delete_avatar, has_avatar, save_avatar
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
	"""Return current username from JWT/API token ( /mss-login is a public prefix)."""
	username = request.get("user")
	if username:
		return username
	token = jwt_auth.get_token_from_request(request)
	if not token:
		return None
	try:
		from ..constants import API_TOKEN_STORE_CONFIG
		from ..utils.api_token_store import get_api_token_store

		api_user = get_api_token_store(API_TOKEN_STORE_CONFIG).get_user_for_token(token)
		if api_user is not None:
			return api_user[1]
	except Exception:
		pass
	try:
		payload = jwt_auth.decode_access_token(token)
		return payload.get("username")
	except Exception:
		return None


def _is_guest_username(username: str | None) -> bool:
	return not username or str(username).strip().lower() == "guest"


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


async def _read_avatar_upload(request: web.Request) -> bytes:
	content_type = (request.headers.get("Content-Type") or "").lower()
	if "multipart/" in content_type:
		reader = await request.multipart()
		async for part in reader:
			if part.name in ("avatar", "file", "image") or (
				part.filename and part.filename.strip()
			):
				data = await part.read()
				if data:
					return data
		return b""
	data = await request.read()
	return data or b""


@routes.get("/mss-login/api/me/avatar")
async def get_me_avatar(request: web.Request) -> web.Response:
	"""Return the current user's avatar PNG, or 404 if none."""
	username = _get_username_from_request(request)
	if not username:
		return web.json_response({"error": "Authentication required"}, status=401)
	if not has_avatar(username):
		return web.Response(status=404)
	path = avatar_path(username)
	return web.FileResponse(path, headers={"Cache-Control": "no-store"})


@routes.post("/mss-login/api/me/avatar")
@routes.put("/mss-login/api/me/avatar")
async def put_me_avatar(request: web.Request) -> web.Response:
	"""Upload a profile avatar. Guests cannot set an avatar."""
	username = _get_username_from_request(request)
	if not username:
		return web.json_response({"error": "Authentication required"}, status=401)
	if _is_guest_username(username):
		return web.json_response({"error": "Guest accounts cannot set an avatar"}, status=403)
	data = await _read_avatar_upload(request)
	if not data:
		return web.json_response({"error": "No image uploaded"}, status=400)
	if len(data) > MAX_UPLOAD_BYTES:
		return web.json_response({"error": "Image is too large (max 2 MB)"}, status=400)
	ok, msg = save_avatar(username, data)
	if not ok:
		return web.json_response({"error": msg}, status=400)
	try:
		from ..utils.sfw_intercept.nsfw_guard import _classify_image_path

		classified = _classify_image_path(avatar_path(username), use_cache=False)
		if classified:
			label, score = classified
			if label == "nsfw" and float(score or 0) > 0.5:
				delete_avatar(username)
				return web.json_response({"error": "Avatar rejected by content filter"}, status=400)
	except Exception:
		pass
	return web.json_response({"status": "ok", "message": msg})


@routes.delete("/mss-login/api/me/avatar")
async def delete_me_avatar(request: web.Request) -> web.Response:
	username = _get_username_from_request(request)
	if not username:
		return web.json_response({"error": "Authentication required"}, status=401)
	if _is_guest_username(username):
		return web.json_response({"error": "Guest accounts cannot set an avatar"}, status=403)
	delete_avatar(username)
	return web.json_response({"status": "ok"})


routes.get("/api/mss-login/api/me/avatar")(get_me_avatar)
routes.post("/api/mss-login/api/me/avatar")(put_me_avatar)
routes.put("/api/mss-login/api/me/avatar")(put_me_avatar)
routes.delete("/api/mss-login/api/me/avatar")(delete_me_avatar)
