# Content-Security-Policy for mss-login HTML pages that use JavaScript.
# Applied only to auth-related HTML routes to reduce XSS and injection surface
# (e.g. when served behind Cloudflare Tunnel / cloudflared).

from aiohttp import web

# Paths that serve HTML pages loading scripts; add CSP so script-src can be strict (no 'unsafe-inline').
_CSP_HTML_PATHS = frozenset(
    {
        "/login",
        "/register",
        "/loading",
        "/mfa",
        "/mss-login/generate_token",
    }
)

# script-src: self + cdnjs (DOMPurify, qrcode). style-src: self + unsafe-inline for existing <style> blocks.
# connect-src: self + tunnel/subdomain URLs so Cloudflare Tunnel (cloudflared) and same-site requests work
# regardless of how the browser normalizes the origin (e.g. comfyui-server.monsterspawned.studio).
# img-src: self + data:. frame-ancestors: self to mitigate clickjacking.
_CSP_VALUE = (
    "default-src 'self'; "
    "script-src 'self' https://cdnjs.cloudflare.com; "
    "style-src 'self' 'unsafe-inline'; "
    "img-src 'self' data:; "
    "connect-src 'self' https://monsterspawned.studio https://*.monsterspawned.studio wss://*.monsterspawned.studio; "
    "frame-ancestors 'self'"
)


def create_csp_middleware() -> web.middleware:
    """Create middleware that adds Content-Security-Policy to mss-login HTML page responses."""

    @web.middleware
    async def csp_middleware(request: web.Request, handler) -> web.StreamResponse:
        response = await handler(request)
        if request.path in _CSP_HTML_PATHS and isinstance(response, web.StreamResponse):
            response.headers["Content-Security-Policy"] = _CSP_VALUE
        return response

    return csp_middleware
