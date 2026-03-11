"""Redirect browser navigation to /api to a friendly warning page.

When a user types the ComfyUI URL + /api in the address bar (browser navigation),
redirect them to a joke/warning page. Programmatic API calls (fetch, axios, etc.)
are not redirected. Certain API paths used by the frontend as data resources are
excluded so they load normally.
"""

from aiohttp import web

# URL to redirect browser navigators who visit /api
REDIRECT_URL = "https://monsterspawned.studio/secrets/nice-try"

# API paths that must never be redirected (frontend data resources, etc.)
_API_REDIRECT_EXCLUDED_PATHS = frozenset(
	{
		"/api/userdata/comfy.templates.json",
	}
)


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
		path = request.path
		if path == "/api" or path.startswith("/api/"):
			if path not in _API_REDIRECT_EXCLUDED_PATHS and request.method == "GET" and _is_browser_navigation(request):
				return web.HTTPFound(location=REDIRECT_URL)
		return await handler(request)

	return middleware
