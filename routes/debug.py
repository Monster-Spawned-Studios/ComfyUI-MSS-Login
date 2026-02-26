# --- START OF FILE routes/debug.py ---
"""Debug-mode API route. Registered here to avoid circular import (constants <-> globals)."""
from aiohttp import web

from ..constants import DEBUG_MODE, DEBUG_MODE_FROM_ENV
from ..globals import routes


@routes.get("/mss-login/api/debug-mode")
async def get_debug_mode(
    request: web.Request = None,  # pyright: ignore[reportUnusedParameter, reportMissingParameter]
) -> web.json_response:
    """Return the debug mode from the environment/config."""
    try:
        if not DEBUG_MODE:
            return web.json_response({"debugMode": DEBUG_MODE_FROM_ENV})
        else:
            return web.json_response({"debugMode": DEBUG_MODE})
    except Exception:
        return web.json_response({"debugMode": False})
    finally:
        if DEBUG_MODE or DEBUG_MODE_FROM_ENV:
            print(f"DEBUG_MODE: {DEBUG_MODE or DEBUG_MODE_FROM_ENV}")
