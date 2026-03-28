"""Redirect browser navigation to /api to a friendly warning page.

When enabled, users who type the ComfyUI URL + /api in the address bar are
redirected to a warning page. Programmatic API calls (fetch, axios, etc.)
are not redirected. Set API_BROWSER_REDIRECT_ENABLED to True to turn this on.
"""

from aiohttp import web

# Set to True to redirect browser navigators who visit /api to REDIRECT_URL
API_BROWSER_REDIRECT_ENABLED = False

# URL to redirect browser navigators who visit /api (when enabled)
REDIRECT_URL = "https://monsterspawned.studio/secrets/nice-try"


def _is_browser_navigation(request: web.Request) -> bool:
	"""Return True if request appears to be from browser address-bar navigation."""
	# Sec-Fetch-Dest: document = loading a document (page)
	# Sec-Fetch-Mode: navigate = top-level navigation
	# Programmatic fetch typically sends Sec-Fetch-Dest: empty, Sec-Fetch-Mode: cors
	dest = request.headers.get("Sec-Fetch-Dest", "").lower()
	mode = request.headers.get("Sec-Fetch-Mode", "").lower()
	if dest == "document" or mode == "navigate":
		return True
	# Fallback: Accept header prefers text/html (browser navigation)
	accept = request.headers.get("Accept", "").lower()
	if "text/html" in accept and accept.split(",")[0].strip().startswith("text/html"):
		return True
	return False


def create_api_browser_redirect_middleware() -> web.middleware:
	"""Create middleware that redirects browser navigation to /api."""

	@web.middleware
	async def middleware(request: web.Request, handler):
		if not API_BROWSER_REDIRECT_ENABLED:
			return await handler(request)
		path = request.path
		if path == "/api" or path.startswith("/api/"):
			if request.method == "GET" and _is_browser_navigation(request):
				return web.HTTPFound(location=REDIRECT_URL)
		return await handler(request)

	return middleware
