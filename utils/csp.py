# Content-Security-Policy for mss-login HTML pages that use JavaScript.
# Applied only to auth-related HTML routes to reduce XSS and injection surface
# (e.g. when served behind Cloudflare Tunnel / cloudflared).

import os
from urllib.parse import urlparse

from aiohttp import web

# Paths that serve HTML pages loading scripts; add CSP so script-src can be strict (no 'unsafe-inline').
_CSP_HTML_PATHS = frozenset({"/login", "/register", "/loading", "/mfa"})

# Static connect-src extras for known tunnel hosts. HOST_BASE_URL (and request origin)
# are appended dynamically in _connect_src_directive().
_CONNECT_SRC_STATIC = (
	"'self' https://monsterspawned.studio https://*.monsterspawned.studio "
	"wss://*.monsterspawned.studio"
)


def _origin_from_url(url: str) -> str:
	"""Return scheme://host[:port] for a URL, or empty string if invalid."""
	try:
		parsed = urlparse((url or "").strip())
		if parsed.scheme not in ("http", "https") or not parsed.netloc:
			return ""
		return f"{parsed.scheme}://{parsed.netloc}"
	except Exception:
		return ""


def _ws_origin_from_http_origin(origin: str) -> str:
	"""Map http(s) origin to ws(s) for connect-src WebSocket allowances."""
	if origin.startswith("https://"):
		return "wss://" + origin[len("https://") :]
	if origin.startswith("http://"):
		return "ws://" + origin[len("http://") :]
	return ""


def _connect_src_directive(request: web.Request | None = None) -> str:
	"""Build connect-src including HOST_BASE_URL and the current request origin."""
	origins: list[str] = []
	seen: set[str] = set()

	def _add(origin: str) -> None:
		o = (origin or "").strip().rstrip("/")
		if not o or o in seen:
			return
		seen.add(o)
		origins.append(o)
		ws = _ws_origin_from_http_origin(o)
		if ws and ws not in seen:
			seen.add(ws)
			origins.append(ws)

	# Prefer env directly so CSP stays correct even before constants/DB are ready.
	_add(_origin_from_url((os.getenv("HOST_BASE_URL") or "").strip()))
	try:
		from ..constants import get_host_base_url

		_add(_origin_from_url(get_host_base_url()))
	except Exception:
		pass

	if request is not None:
		# Prefer absolute URL origin; fall back to Origin / Host headers.
		try:
			_add(str(request.url.origin()))
		except Exception:
			pass
		_add(_origin_from_url(request.headers.get("Origin") or ""))
		host = (
			request.headers.get("X-Forwarded-Host") or request.headers.get("Host") or ""
		).strip()
		if host:
			proto = (
				(request.headers.get("X-Forwarded-Proto") or request.scheme or "https")
				.split(",")[0]
				.strip()
			)
			if proto not in ("http", "https"):
				proto = "https"
			_add(f"{proto}://{host.split(',')[0].strip()}")

	extra = (" " + " ".join(origins)) if origins else ""
	return f"connect-src {_CONNECT_SRC_STATIC}{extra}"


def _build_csp_for_path(path: str, request: web.Request | None = None) -> str:
	img_src = "img-src 'self' data:"
	media_src = "media-src 'self'"
	if path == "/login":
		try:
			from .login_background import csp_extra_origins_for_login

			img_origin, media_origin = csp_extra_origins_for_login()
			if img_origin:
				img_src = f"{img_src} {img_origin}"
			if media_origin:
				media_src = f"{media_src} {media_origin}"
		except Exception:
			pass
	# script-src: self + cdnjs (DOMPurify, qrcode). style-src: self + unsafe-inline for existing <style> blocks.
	# connect-src: self + tunnel hosts + HOST_BASE_URL / request origin so reverse-proxy installs work.
	# frame-ancestors: self to mitigate clickjacking.
	return (
		"default-src 'self'; "
		"script-src 'self' https://cdnjs.cloudflare.com; "
		"style-src 'self' 'unsafe-inline'; "
		f"{_connect_src_directive(request)}; "
		"frame-ancestors 'self'; "
		f"{img_src}; {media_src}"
	)


def create_csp_middleware() -> web.middleware:
	"""Create middleware that adds Content-Security-Policy to mss-login HTML page responses."""

	@web.middleware
	async def csp_middleware(request: web.Request, handler) -> web.StreamResponse:
		response = await handler(request)
		if request.path in _CSP_HTML_PATHS and isinstance(response, web.StreamResponse):
			response.headers["Content-Security-Policy"] = _build_csp_for_path(request.path, request)
		return response

	return csp_middleware
