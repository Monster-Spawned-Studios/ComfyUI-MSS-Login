# --- START OF FILE utils/model_filter_middleware.py ---
"""
Middleware: intercept GET /models, GET /models/{folder}, GET /embeddings.
Return full list if user has can_view_all_comfyui_items; else filter by shared items per user.
"""

from aiohttp import web
import folder_paths


# Folder names we treat as "asset" lists (models, loras, vae, embeddings, etc.)
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
	}
)


def _map_legacy(folder_name: str) -> str:
	try:
		return folder_paths.map_legacy(folder_name)
	except AttributeError:
		return {"unet": "diffusion_models", "clip": "text_encoders"}.get(folder_name, folder_name)


def create_model_filter_middleware(
	get_user_role_and_permissions, get_shared_items_store, users_db, users_db_config
):
	"""Build middleware that filters /models and /embeddings by permission and shared items."""

	@web.middleware
	async def model_filter_middleware(request: web.Request, handler):
		if request.method != "GET":
			return await handler(request)
		path = request.path.rstrip("/")
		if path == "/models":
			role, perms, username = get_user_role_and_permissions(request)
			can_view_all = perms.get("can_view_all_comfyui_items", False) is True or role == "admin"
			try:
				folder_names = list(folder_paths.folder_names_and_paths.keys())
			except AttributeError:
				folder_names = list(ASSET_FOLDERS)
			if can_view_all:
				return web.json_response(folder_names)
			store = get_shared_items_store(users_db_config)
			user_id, _ = users_db.get_user(username=username) if username else (None, {})
			if not user_id:
				return web.json_response([])
			allowed = store.get_all_for_user(user_id)
			allowed_folders = {f for f, _ in allowed}
			filtered = [f for f in folder_names if f in allowed_folders]
			return web.json_response(filtered)

		if path.startswith("/models/"):
			folder = path[len("/models/") :].strip("/")
			folder = _map_legacy(folder)
			role, perms, username = get_user_role_and_permissions(request)
			can_view_all = perms.get("can_view_all_comfyui_items", False) is True or role == "admin"
			try:
				full_list = folder_paths.get_filename_list(folder)
			except Exception:
				full_list = []
			if can_view_all:
				return web.json_response(full_list)
			store = get_shared_items_store(users_db_config)
			user_id, _ = users_db.get_user(username=username) if username else (None, {})
			if not user_id:
				return web.json_response([])
			allowed = store.get_all_for_user(user_id)
			allowed_in_folder = {name for f, name in allowed if f == folder}
			filtered = [x for x in full_list if x in allowed_in_folder]
			return web.json_response(filtered)

		if path == "/embeddings":
			role, perms, username = get_user_role_and_permissions(request)
			can_view_all = perms.get("can_view_all_comfyui_items", False) is True or role == "admin"
			try:
				full_list = folder_paths.get_filename_list("embeddings")
			except Exception:
				full_list = []
			if can_view_all:
				return web.json_response(full_list)
			store = get_shared_items_store(users_db_config)
			user_id, _ = users_db.get_user(username=username) if username else (None, {})
			if not user_id:
				return web.json_response([])
			allowed = store.get_all_for_user(user_id)
			allowed_emb = {name for f, name in allowed if f == "embeddings"}
			filtered = [x for x in full_list if x in allowed_emb]
			return web.json_response(filtered)

		return await handler(request)

	return model_filter_middleware
