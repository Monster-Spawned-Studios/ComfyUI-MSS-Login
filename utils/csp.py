# Content-Security-Policy for mss-login HTML pages that use JavaScript.
# Applied only to auth-related HTML routes to reduce XSS and injection surface
# (e.g. when served behind Cloudflare Tunnel / cloudflared).

from aiohttp import web

# Paths that serve HTML pages loading scripts; add CSP so script-src can be strict (no 'unsafe-inline').
_CSP_HTML_PATHS = frozenset({"/login", "/register", "/loading", "/mfa"})

# script-src: self + cdnjs (DOMPurify, qrcode). style-src: self + unsafe-inline for existing <style> blocks.
# connect-src: self + tunnel/subdomain URLs so Cloudflare Tunnel (cloudflared) and same-site requests work
# regardless of how the browser normalizes the origin (e.g. comfyui-server.monsterspawned.studio).
# img-src: self + data:. frame-ancestors: self to mitigate clickjacking.
_CSP_BASE = (
	"default-src 'self'; "
	"script-src 'self' https://cdnjs.cloudflare.com; "
	"style-src 'self' 'unsafe-inline'; "
	"connect-src 'self' https://monsterspawned.studio https://*.monsterspawned.studio wss://*.monsterspawned.studio; "
	"frame-ancestors 'self'"
)


def _build_csp_for_path(path: str) -> str:
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
	return f"{_CSP_BASE}; {img_src}; {media_src}"


def create_csp_middleware() -> web.middleware:
	"""Create middleware that adds Content-Security-Policy to mss-login HTML page responses."""

	@web.middleware
	async def csp_middleware(request: web.Request, handler) -> web.StreamResponse:
		response = await handler(request)
		if request.path in _CSP_HTML_PATHS and isinstance(response, web.StreamResponse):
			response.headers["Content-Security-Policy"] = _build_csp_for_path(request.path)
		return response

	return csp_middleware
