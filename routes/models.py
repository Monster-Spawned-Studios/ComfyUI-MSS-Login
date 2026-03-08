"""
Routes: GET /models, GET /models/{folder}, GET /embeddings.
"""

from aiohttp import web

from utils import jwt_auth, users_db

from ..constants import USERS_DB_CONFIG
from ..globals import logger, routes
from ..utils.model_cache import get_model_cache


def is_admin(request: web.Request) -> bool:
    """Check if the user is an admin."""
    token = jwt_auth.get_token_from_request(request)
    if not token:
        return False
    try:
        p = jwt_auth.decode_access_token(token)
        username = p.get("username")
        if not username:
            return False
        _, u = users_db.get_user(username)
        if not u:
            return False
        groups = [g.lower() for g in u.get("groups", [])]
        return u.get("admin", False) or "admin" in groups or "owner" in groups
    except Exception as e:
        logger.error(f"[MSS-Login] is_admin error: {e}")
        return False


def get_folder_list(users_db_config: dict) -> list[str]:
    """Get the list of ComfyUI model folder names."""
    cache = get_model_cache(users_db_config)
    return cache.list_folders()


@routes.get("/api/mss-login/models")
async def api_models(request: web.Request) -> web.Response:
    """List ComfyUI model folder names (for admin shared-items UI). Admin only."""
    if not is_admin(request):
        return web.json_response({"error": "Admin only"}, status=403)
    folders = get_folder_list(USERS_DB_CONFIG)
    return web.json_response({"folders": folders})
