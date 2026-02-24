# --- START OF FILE routes/debug.py ---
"""Debug-mode API route. Registered here to avoid circular import (constants <-> globals)."""
from aiohttp import web

from ..constants import DEBUG_MODE
from ..globals import routes


@routes.get("/mss-login/api/debug-mode")
async def get_debug_mode(request):  # pyright: ignore[reportUnusedParameter]
    """Return the debug mode from the environment/config."""
    return web.json_response({"debugMode": DEBUG_MODE})
