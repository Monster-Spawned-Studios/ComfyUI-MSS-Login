"""Auth cookie helpers: browser session cookies only.

Long-lived API / JWT tokens created via Generate Token (api_token_store) are
never cleared or revoked here — only the interactive browser login cookie
(`jwt_token`) and related session aliases used by the ComfyUI UI login flow.
"""

from __future__ import annotations

from typing import Any

from aiohttp import web

# Browser-login session cookies only. Do NOT add names used by third-party apps
# or opaque API tokens. Logout must never touch api_token_store.
BROWSER_SESSION_COOKIE_NAMES = (
	"jwt_token",  # HttpOnly session cookie set on /login, /mfa, guest, local-login
	"mss-login-jwt",  # legacy client alias (non-HttpOnly, if ever set)
	"mss_login_jwt",
)

# Paths to clear against (browsers match path prefix).
_CLEAR_PATHS = ("/", "/mss-login", "/login", "/logout", "/mfa")


def truthy_remember_me(value: Any) -> bool:
	"""Parse remember_me from form/JSON; default False (session cookie only)."""
	if value is True:
		return True
	if value is False or value is None:
		return False
	s = str(value).strip().lower()
	return s in ("1", "true", "yes", "on")


def set_auth_cookie(
	resp: web.Response,
	token: str,
	*,
	secure: bool,
	remember_me: bool = False,
	max_age_seconds: int | None = None,
) -> None:
	"""
	Set jwt_token HttpOnly cookie for the browser UI session.

	Default (remember_me=False): browser session cookie (no Max-Age / Expires).
	remember_me=True: persistent cookie with max_age_seconds (JWT lifetime).

	Does not create or modify long-lived API tokens (api_token_store).
	"""
	kwargs: dict[str, Any] = {
		"httponly": True,
		"samesite": "Strict",
		"secure": bool(secure),
		"path": "/",
	}
	if remember_me and max_age_seconds is not None and int(max_age_seconds) > 0:
		kwargs["max_age"] = int(max_age_seconds)
	resp.set_cookie("jwt_token", token, **kwargs)


def clear_auth_cookies(resp: web.Response, *, secure: bool) -> None:
	"""
	Expire browser-session auth cookies only.

	Preserves long-standing API/JWT tokens that users generated for external
	apps (Krita, Comfy Portal, etc.) — those live in api_token_store and are
	not cookies.
	"""
	for name in BROWSER_SESSION_COOKIE_NAMES:
		for path in _CLEAR_PATHS:
			try:
				resp.del_cookie(name, path=path)
			except Exception:
				pass
			try:
				resp.set_cookie(
					name,
					"",
					max_age=0,
					path=path,
					httponly=True,
					samesite="Strict",
					secure=bool(secure),
				)
			except Exception:
				pass
