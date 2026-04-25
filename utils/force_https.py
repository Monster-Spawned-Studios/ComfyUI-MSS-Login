"""HTTPS enforcement and security headers middleware.

When force_https is enabled:
  - Requests detected as HTTPS (via proxy headers, CF-Visitor, or scheme) get
    request.scheme set to 'https' so downstream code (cookies, URL generation) works.
  - Browser requests detected as plain HTTP receive a 301 redirect to the HTTPS URL.
    API requests (Bearer auth, non-HTML Accept) and WebSocket upgrades are not redirected
    to preserve Comfy Portal and programmatic client compatibility.

Security headers (HSTS, X-Content-Type-Options, etc.) are added to all responses when
the connection is detected as HTTPS, regardless of the force_https setting.
"""

from aiohttp import web

from .ip_filter import is_https_request


_BROWSER_REDIRECT_PATHS = frozenset(
	{"/", "/login", "/register", "/loading", "/mfa", "/mss-login/generate_token"}
)


def _is_browser_request(request: web.Request) -> bool:
	"""Heuristic: treat as browser if Accept includes text/html and no Bearer token."""
	if request.headers.get("Authorization", "").startswith("Bearer "):
		return False
	accept = (request.headers.get("Accept") or "").lower()
	return "text/html" in accept or accept in ("", "*/*")


def _is_websocket_upgrade(request: web.Request) -> bool:
	return (request.headers.get("Upgrade") or "").lower() == "websocket"


def create_https_middleware(match_headers: dict | None) -> web.middleware:
	"""Create middleware that enforces HTTPS via redirect and sets request.scheme."""

	@web.middleware
	async def https_middleware(request: web.Request, handler) -> web.StreamResponse:
		if is_https_request(request):
			request = request.clone(scheme="https")
			return await handler(request)

		if match_headers:
			matched = any(request.headers.get(key) == value for key, value in match_headers.items())
			if matched:
				request = request.clone(scheme="https")
				return await handler(request)

		if (
			not _is_websocket_upgrade(request)
			and _is_browser_request(request)
			and (request.path in _BROWSER_REDIRECT_PATHS or request.path.startswith("/mss-login"))
		):
			# Relative Location only: str(request.url) embeds Host / forwarded host and enables open redirects.
			raise web.HTTPMovedPermanently(str(request.rel_url))

		return await handler(request)

	return https_middleware


def create_security_headers_middleware(cloudflare_proxy: bool = False) -> web.middleware:
	"""Add standard security headers to all responses.

	HSTS is only set when the request is detected as HTTPS (or cloudflare_proxy
	is enabled, since Cloudflare terminates TLS at the edge).
	"""

	@web.middleware
	async def security_headers_middleware(request: web.Request, handler) -> web.StreamResponse:
		response = await handler(request)

		if not isinstance(response, web.StreamResponse):
			return response

		response.headers["X-Content-Type-Options"] = "nosniff"
		response.headers["X-Frame-Options"] = "SAMEORIGIN"
		response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
		response.headers["Permissions-Policy"] = "camera=(), microphone=(), geolocation=()"

		if cloudflare_proxy or is_https_request(request):
			response.headers["Strict-Transport-Security"] = "max-age=31536000; includeSubDomains"

		return response

	return security_headers_middleware
