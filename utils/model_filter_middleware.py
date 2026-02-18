# --- START OF FILE utils/model_filter_middleware.py ---
"""
Middleware: intercept GET /models, GET /models/{folder}, GET /embeddings.
Granular model visibility: only show models the user has explicit permission to access.

Permission logic:
- can_view_all_comfyui_items (group-based): If the user's group has this permission, show all.
- Otherwise: Show only items explicitly shared with the user (shared_items store).
- Admins always see all models.
- Guests and users without shared items see nothing (empty list).

The Models menu in the ComfyUI sidebar fetches from these endpoints; filtered responses
ensure users never see models they are not permitted to use.
"""

from aiohttp import web
import folder_paths  # pyright: ignore[reportMissingImports]


# Folder names we treat as "asset" lists (models, loras, vae, embeddings, etc.)
# Includes ComfyUI Model Library folders: ultralytics, mmdets, sams, classifiers, configs.
ASSET_FOLDERS = frozenset(
    {
        "checkpoints",
        "loras",
        "vae",
        "text_encoders",
        "clip",
        "embeddings",
        "diffusion_models",
        "unet",
        "clip_vision",
        "style_models",
        "controlnet",
        "gligen",
        "upscale_models",
        "latent_upscale_models",
        "hypernetworks",
        "vae_approx",
        "diffusers",
        "photomaker",
        "model_patches",
        "audio_encoders",
        "classifiers",
        "configs",
        "ultralytics_bbox",
        "ultralytics_segm",
        "ultralytics",
        "mmdets_bbox",
        "mmdets_segm",
        "mmdets",
        "sams",
    }
)


def _map_legacy(folder_name: str) -> str:
    try:
        return folder_paths.map_legacy(folder_name)
    except AttributeError:
        return {"unet": "diffusion_models", "clip": "text_encoders"}.get(
            folder_name, folder_name
        )


def create_model_filter_middleware(
    get_user_role_and_permissions, get_shared_items_store, users_db, users_db_config
):
    """Build middleware that filters /models and /embeddings by permission and shared items."""
    from .model_cache import get_model_cache

    def _user_can_view_all(role: str, perms: dict) -> bool:
        """True if user has group-based override to see all models (or is admin)."""
        return perms.get("can_view_all_comfyui_items", False) is True or role == "admin"

    def _get_folder_list():
        """Folder names: from cache if populated, else folder_paths."""
        try:
            cache = get_model_cache(users_db_config)
            if not cache.is_empty():
                return cache.list_folders()
        except Exception:
            pass
        try:
            return list(folder_paths.folder_names_and_paths.keys())
        except AttributeError:
            return list(ASSET_FOLDERS)

    def _get_item_list(folder: str):
        """Item names in folder: from cache if populated, else folder_paths."""
        try:
            cache = get_model_cache(users_db_config)
            if not cache.is_empty():
                return cache.list_items(folder)
        except Exception:
            pass
        try:
            return folder_paths.get_filename_list(folder)
        except Exception:
            return []

    @web.middleware
    async def model_filter_middleware(request: web.Request, handler):
        if request.method != "GET":
            return await handler(request)
        path = request.path.rstrip("/")
        if path == "/models":
            role, perms, username = get_user_role_and_permissions(request)
            folder_names = _get_folder_list()
            if _user_can_view_all(role, perms):
                return web.json_response(folder_names)
            user_id, _ = (
                users_db.get_user(username=username) if username else (None, {})
            )
            if not user_id:
                return web.json_response([])
            allowed = get_shared_items_store(users_db_config).get_all_for_user(user_id)
            allowed_folders = {f for f, _ in allowed}
            filtered = [f for f in folder_names if f in allowed_folders]
            return web.json_response(filtered)

        if path.startswith("/models/"):
            folder = path[len("/models/") :].strip("/")
            folder = _map_legacy(folder)
            role, perms, username = get_user_role_and_permissions(request)
            full_list = _get_item_list(folder)
            if _user_can_view_all(role, perms):
                return web.json_response(full_list)
            user_id, _ = (
                users_db.get_user(username=username) if username else (None, {})
            )
            if not user_id:
                return web.json_response([])
            allowed = get_shared_items_store(users_db_config).get_all_for_user(user_id)
            allowed_in_folder = {name for f, name in allowed if f == folder}
            filtered = [x for x in full_list if x in allowed_in_folder]
            return web.json_response(filtered)

        if path == "/embeddings":
            role, perms, username = get_user_role_and_permissions(request)
            full_list = _get_item_list("embeddings")
            if _user_can_view_all(role, perms):
                return web.json_response(full_list)
            user_id, _ = (
                users_db.get_user(username=username) if username else (None, {})
            )
            if not user_id:
                return web.json_response([])
            allowed = get_shared_items_store(users_db_config).get_all_for_user(user_id)
            allowed_emb = {name for f, name in allowed if f == "embeddings"}
            filtered = [x for x in full_list if x in allowed_emb]
            return web.json_response(filtered)

        return await handler(request)

    return model_filter_middleware
