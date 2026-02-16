import os
import uuid
import jwt
from aiohttp import web
from datetime import datetime, timedelta, timezone

from .users_db import UsersDB
from .access_control import AccessControl
from .logger import Logger
from .api_token_store import get_api_token_store
from .session_token_store import get_session_token_store
from .debug_log import debug_write


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
				if key in query and query[key]:
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
			expire = datetime.now(timezone.utc) + timedelta(minutes=expire_minutes)
			to_encode["exp"] = expire
		return jwt.encode(to_encode, self.__secret_key, algorithm=self.algorithm)

	def decode_access_token(self, token: str) -> dict:
		"""Decode a JWT access token."""
		return jwt.decode(token, self.__secret_key, algorithms=[self.algorithm])

	def is_token_valid(self, token: str) -> bool:
		"""
		Return True if the token is valid (API token in store or valid non-revoked JWT).
		Used by the remote API guard to treat only valid tokens as auth.
		"""
		token = (token or "").strip()
		if not token:
			return False
		try:
			api_store = get_api_token_store(self.api_token_store_config)
			if api_store.get_user_for_token(token) is not None:
				return True
		except Exception:
			pass
		if token.count(".") < 2:
			return False
		try:
			user = self.decode_access_token(token)
			user_id = user.get("id")
			username = user.get("username")
			if not username or user_id is None:
				return False
			if user_id != self.users_db.get_user(username)[0]:
				return False
			jti = user.get("jti")
			if jti:
				try:
					from ..constants import SESSION_TOKEN_STORE_PATH, SESSION_IDLE_REVOKE_MINUTES

					store = get_session_token_store(
						SESSION_TOKEN_STORE_PATH,
						idle_revoke_minutes=SESSION_IDLE_REVOKE_MINUTES,
					)
					if store.is_revoked(jti):
						return False
				except Exception:
					pass
			return True
		except (jwt.ExpiredSignatureError, jwt.DecodeError, ValueError, KeyError, TypeError):
			return False

	def create_jwt_middleware(
		self,
		public: tuple = (),
		public_prefixes: tuple = (),
		public_suffixes: tuple = (),
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
					set_fallback = request.path in ["/api/prompt"]
					self.access_control.set_current_user_id(user_id, set_fallback)
					return await handler(request)

				# API token not found: if token doesn't look like a JWT (no dots), return clear message
				if token.count(".") < 2:
					return await handle_unauthorized_access(
						request,
						"/login",
						message="API token not found or expired. Generate a new token on this server (Settings → mss_login → Generate Token).",
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
							SESSION_TOKEN_STORE_PATH,
							SESSION_IDLE_REVOKE_MINUTES,
						)

						store = get_session_token_store(
							SESSION_TOKEN_STORE_PATH,
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

				set_fallback = request.path in ["/api/prompt"]
				self.access_control.set_current_user_id(user_id, set_fallback)

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
					message="API token not found or expired. Generate a new token on this server (Settings → mss_login → Generate Token).",
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
			request: web.Request,
			redirect_path: str,
			message: str = "Authentication required",
		) -> web.Response:
			"""Handle unauthorized access cases."""
			accept_header = request.headers.get("Accept", "")
			if "text/html" in accept_header:
				return web.HTTPFound(redirect_path)
			body = {"error": message}
			try:
				from ..constants import DEBUG_MODE

				if DEBUG_MODE:
					body["debug"] = "DEBUG_MODE=1: see .cursor/debug.log or server logs."
			except Exception:
				pass
			return web.json_response(body, status=401)

		return jwt_middleware
