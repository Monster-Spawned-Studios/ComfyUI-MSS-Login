import os
import uuid
from datetime import UTC, datetime, timedelta, timezone

import jwt
from aiohttp import web

from .access_control import AccessControl
from .api_token_store import get_api_token_store
from .auth_response import should_return_json_401
from .debug_log import debug_write
from .logger import Logger
from .session_token_store import get_session_token_store
from .user_isolation import is_prompt_execution_path
from .users_db import UsersDB


class JWTAuth:
	def __init__(
		self,
		users_db: UsersDB,
		access_control: AccessControl,
		logger: Logger,
		secret_key: str,
		expire_minutes: int = 12 * 60,
		algorithm: str = "HS256",
		api_token_store_config: dict = None,
	):
		self.users_db = users_db
		self.access_control = access_control
		self.logger = logger
		self.api_token_store_config = api_token_store_config or {}

		self.expire_minutes = expire_minutes
		self.algorithm = algorithm

		self.__secret_key = secret_key

	@staticmethod
	def get_token_from_request(request: web.Request) -> str:
		"""Extract token from request headers, cookies, or query (e.g. WebSocket ?token=). Strips whitespace."""
		auth_header = request.headers.get("Authorization", "")
		if auth_header.startswith("Bearer "):
			return (auth_header[len("Bearer ") :] or "").strip()
		cookie = request.cookies.get("jwt_token")
		if cookie:
			return (cookie or "").strip()
		# WebSocket and some clients send token in URL (e.g. ws://host/ws?token=...)
		try:
			query = request.rel_url.query
			for key in ("token", "access_token"):
				if query.get(key):
					return (query[key] or "").strip()
		except Exception:
			pass
		return ""

	def create_access_token(
		self, data: dict, expire_minutes=None, no_expiration: bool = False
	) -> str:
		"""Create a JWT access token. When no_expiration=True, omit exp (Admin permission only)."""
		to_encode = data.copy()
		to_encode["jti"] = uuid.uuid4().hex
		if no_expiration:
			pass  # do not add exp
		else:
			if not expire_minutes:
				expire_minutes = self.expire_minutes
			expire = datetime.now(UTC) + timedelta(minutes=expire_minutes)
			to_encode["exp"] = expire
		return jwt.encode(to_encode, self.__secret_key, algorithm=self.algorithm)

	def decode_access_token(self, token: str) -> dict:
		"""Decode a JWT access token."""
		return jwt.decode(token, self.__secret_key, algorithms=[self.algorithm])

	def resolve_token_identity(
		self, token: str, *, touch_idle: bool = False
	) -> tuple[str | None, str | None]:
		"""
		Resolve a Bearer/cookie/query token to (user_id, username).

		Checks API-token store first; otherwise decodes JWT and verifies user-id match,
		jti revocation, and optionally updates idle last-used. Returns (None, None) on failure.
		"""
		token = (token or "").strip()
		if not token:
			return None, None
		try:
			api_store = get_api_token_store(self.api_token_store_config)
			api_user = api_store.get_user_for_token(token)
			if api_user is not None:
				user_id, username = api_user
				return user_id, username
		except Exception:
			pass
		if token.count(".") < 2:
			return None, None
		try:
			user = self.decode_access_token(token)
			user_id = user.get("id")
			username = user.get("username")
			if not username or user_id is None:
				return None, None
			db_uid, db_user = self.users_db.get_user(username)
			if user_id != db_uid:
				return None, None
			# Session epoch: missing claim treated as 0 (legacy dual-read)
			claim_tv = int(user.get("token_version") or 0)
			db_tv = int((db_user or {}).get("token_version") or 0)
			if claim_tv != db_tv:
				return None, None
			jti = user.get("jti")
			if jti:
				try:
					from ..constants import SESSION_IDLE_REVOKE_MINUTES, SESSION_TOKEN_STORE_CONFIG

					store = get_session_token_store(
						SESSION_TOKEN_STORE_CONFIG, idle_revoke_minutes=SESSION_IDLE_REVOKE_MINUTES
					)
					if store.is_revoked(jti):
						return None, None
					if touch_idle:
						store.update_last_used(jti)
						if store.revoke_idle_sessions() > 0:
							store.prune_old_sessions()
				except Exception:
					pass
			return user_id, username
		except (jwt.ExpiredSignatureError, jwt.DecodeError, ValueError, KeyError, TypeError):
			return None, None

	def resolve_request_identity(
		self, request: web.Request, *, touch_idle: bool = True
	) -> tuple[str | None, str | None]:
		"""
		Resolve request auth to (user_id, username). Sets request['user'], request['user_id'],
		and request['auth_verified'] on success. Shared by middleware and handler helpers.
		"""
		if request.get("auth_verified") and request.get("user") and request.get("user_id"):
			return request.get("user_id"), request.get("user")

		token = self.get_token_from_request(request)
		user_id, username = self.resolve_token_identity(token, touch_idle=touch_idle)
		if user_id is not None and username:
			request["user_id"] = user_id
			request["user"] = username
			request["auth_verified"] = True
			return user_id, username
		request["auth_verified"] = False
		return None, None

	def is_token_valid(self, token: str) -> bool:
		"""
		Return True if the token is valid (API token in store or valid non-revoked JWT).
		Used by the remote API guard to treat only valid tokens as auth.
		"""
		user_id, username = self.resolve_token_identity(token, touch_idle=False)
		return user_id is not None and bool(username)

	def create_jwt_middleware(
		self, public: tuple = (), public_prefixes: tuple = (), public_suffixes: tuple = ()
	) -> web.middleware:
		"""Create middleware for JWT authentication."""

		@web.middleware
		async def jwt_middleware(request: web.Request, handler) -> web.Response:
			"""Middleware to handle JWT authentication."""
			if (
				request.path in public
				or request.path.startswith(public_prefixes)
				or request.path.endswith(public_suffixes)
			):
				return await handler(request)

			token = self.get_token_from_request(request)

			if not token:
				debug_write(
					{
						"location": "jwt_auth",
						"message": "no_token",
						"data": {"path": request.path},
						"hypothesisId": "B",
					}
				)
				try:
					import json
					import time

					from ..constants import DEBUG_LOG_PATH

					os.makedirs(os.path.dirname(DEBUG_LOG_PATH), exist_ok=True)
					with open(DEBUG_LOG_PATH, "a", encoding="utf-8") as f:
						f.write(
							json.dumps(
								{
									"sessionId": "debug-session",
									"runId": "run1",
									"hypothesisId": "JWT-B",
									"location": "jwt_auth.py",
									"message": "no_token",
									"data": {"path": request.path},
									"timestamp": int(time.time() * 1000),
								}
							)
							+ "\n"
						)
				except Exception:
					pass
				return await handle_unauthorized_access(request, "/login")

			try:
				# Resolve Bearer: try long-lived API token store first, then JWT
				api_store = get_api_token_store(self.api_token_store_config)
				api_user = api_store.get_user_for_token(token)
				debug_write(
					{
						"location": "jwt_auth",
						"message": "api_store_lookup",
						"data": {"path": request.path, "api_user_found": api_user is not None},
						"hypothesisId": "B",
					}
				)
				try:
					import json
					import time

					from ..constants import DEBUG_LOG_PATH

					os.makedirs(os.path.dirname(DEBUG_LOG_PATH), exist_ok=True)
					with open(DEBUG_LOG_PATH, "a", encoding="utf-8") as f:
						f.write(
							json.dumps(
								{
									"sessionId": "debug-session",
									"runId": "run1",
									"hypothesisId": "JWT-B",
									"location": "jwt_auth.py",
									"message": "api_store_lookup",
									"data": {
										"path": request.path,
										"api_user_found": api_user is not None,
									},
									"timestamp": int(time.time() * 1000),
								}
							)
							+ "\n"
						)
				except Exception:
					pass
				if api_user is not None:
					user_id, username = api_user
					request["user_id"] = user_id
					request["user"] = username
					set_fallback = is_prompt_execution_path(request.path)
					self.access_control.set_current_user_id(
						user_id, set_fallback, username=username
					)
				else:
					# API token not found: if token doesn't look like a JWT (no dots), return clear message
					if token.count(".") < 2:
						return await handle_unauthorized_access(
							request,
							"/login",
							message="API token not found or expired. Generate a new token on this server (Settings → mss-login → Generate Token).",
						)

					user = self.decode_access_token(token)
					user_id = user.get("id")
					username = user.get("username")
					jti = user.get("jti")
					if not user_id == self.users_db.get_user(username)[0]:
						raise ValueError(f"User with username: {username} is not in the database")
					# Session JWT blocklist check (revoked tokens) and idle revocation
					if jti:
						try:
							from ..constants import (
								SESSION_IDLE_REVOKE_MINUTES,
								SESSION_TOKEN_STORE_CONFIG,
							)

							store = get_session_token_store(
								SESSION_TOKEN_STORE_CONFIG,
								idle_revoke_minutes=SESSION_IDLE_REVOKE_MINUTES,
							)
							if store.is_revoked(jti):
								return await handle_unauthorized_access(
									request, "/login", message="Token has been revoked"
								)
							store.update_last_used(jti)
							if store.revoke_idle_sessions() > 0:
								store.prune_old_sessions()
						except Exception:
							pass

					request["user_id"] = user_id
					request["user"] = username

					set_fallback = is_prompt_execution_path(request.path)
					self.access_control.set_current_user_id(
						user_id, set_fallback, username=username
					)

			except jwt.ExpiredSignatureError:
				debug_write(
					{
						"location": "jwt_auth",
						"message": "reject",
						"data": {"path": request.path, "reason": "ExpiredSignatureError"},
						"hypothesisId": "B",
					}
				)
				try:
					import json
					import time

					from ..constants import DEBUG_LOG_PATH

					with open(DEBUG_LOG_PATH, "a", encoding="utf-8") as f:
						f.write(
							json.dumps(
								{
									"sessionId": "debug-session",
									"runId": "run1",
									"hypothesisId": "JWT-B",
									"location": "jwt_auth.py",
									"message": "reject",
									"data": {
										"path": request.path,
										"reason": "ExpiredSignatureError",
									},
									"timestamp": int(time.time() * 1000),
								}
							)
							+ "\n"
						)
				except Exception:
					pass
				return await handle_unauthorized_access(
					request, "/logout", message="Token has expired"
				)
			except jwt.DecodeError:
				debug_write(
					{
						"location": "jwt_auth",
						"message": "reject",
						"data": {"path": request.path, "reason": "DecodeError"},
						"hypothesisId": "B",
					}
				)
				try:
					import json
					import time

					from ..constants import DEBUG_LOG_PATH

					with open(DEBUG_LOG_PATH, "a", encoding="utf-8") as f:
						f.write(
							json.dumps(
								{
									"sessionId": "debug-session",
									"runId": "run1",
									"hypothesisId": "JWT-B",
									"location": "jwt_auth.py",
									"message": "reject",
									"data": {"path": request.path, "reason": "DecodeError"},
									"timestamp": int(time.time() * 1000),
								}
							)
							+ "\n"
						)
				except Exception:
					pass
				# Likely an API token that wasn't in the store (wrong server or expired)
				return await handle_unauthorized_access(
					request,
					"/login",
					message="API token not found or expired. Generate a new token on this server (Settings → mss-login → Generate Token).",
				)
			except Exception as e:
				debug_write(
					{
						"location": "jwt_auth",
						"message": "reject",
						"data": {"path": request.path, "reason": type(e).__name__},
						"hypothesisId": "B",
					}
				)
				try:
					import json
					import time

					from ..constants import DEBUG_LOG_PATH

					with open(DEBUG_LOG_PATH, "a", encoding="utf-8") as f:
						f.write(
							json.dumps(
								{
									"sessionId": "debug-session",
									"runId": "run1",
									"hypothesisId": "JWT-B",
									"location": "jwt_auth.py",
									"message": "reject",
									"data": {"path": request.path, "reason": type(e).__name__},
									"timestamp": int(time.time() * 1000),
								}
							)
							+ "\n"
						)
				except Exception:
					pass
				self.logger.error(f"Unexpected error during token decoding: {e}")
				return await handle_unauthorized_access(
					request, "/logout", message="Unexpected error"
				)

			return await handler(request)

		async def handle_unauthorized_access(
			request: web.Request, redirect_path: str, message: str = "Authentication required"
		) -> web.Response:
			"""Handle unauthorized access: redirect browsers to login/logout, return JSON for API/native clients.

			Rules (in priority order):
			1. API paths (/api/*, /cpe/*, /ws) → always JSON 401 (Comfy Portal, programmatic clients).
			2. Sec-Fetch-Mode: navigate → always HTML redirect (unambiguous browser navigation).
			3. Accept contains text/html but not application/json → HTML redirect.
			4. Everything else (Accept: */*, empty, application/json, etc.) → JSON 401.
			   This ensures native apps (Comfy Portal, curl, etc.) get machine-readable errors.
			"""
			path = request.path
			accept_header = (request.headers.get("Accept") or "").strip().lower()
			sec_mode = (request.headers.get("Sec-Fetch-Mode") or "").lower()
			if should_return_json_401(path, accept=accept_header, sec_fetch_mode=sec_mode):
				body = {"error": message}
				try:
					from ..constants import DEBUG_MODE

					if DEBUG_MODE:
						body["debug"] = "DEBUG_MODE=1: see logs/debug.log or server logs."
				except Exception:
					pass
				return web.json_response(body, status=401)
			return web.HTTPFound(redirect_path)

		return jwt_middleware
