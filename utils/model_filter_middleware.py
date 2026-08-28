# --- START OF FILE utils/model_filter_middleware.py ---
"""
Middleware: intercept GET /models, GET /models/{folder}, GET /embeddings.
Granular model visibility: only show models the user has explicit permission to access.

Permission logic:
- can_view_all_comfyui_items (group-based): If the user's group has this permission, show all.
- can_access_s3_storage: If false, items originating from the S3 mount are hidden.
- Otherwise: Show only items explicitly shared with the user (shared_items store).
- Admins always see all models.
- Guests and users without shared items see nothing (empty list).
"""

import os

import folder_paths  # pyright: ignore[reportMissingImports]
from aiohttp import web

from .model_visibility_policy import (
	filter_items_by_grants,
	get_effective_model_grants_for_user,
	normalize_model_name,
	user_can_access_s3,
	user_can_view_all_models,
)
from .path_safety import is_safe_folder_segment

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
		return {"unet": "diffusion_models", "clip": "text_encoders"}.get(folder_name, folder_name)


def create_model_filter_middleware(
	get_user_role_and_permissions, get_shared_items_store, users_db, users_db_config
):
	"""Build middleware that filters /models and /embeddings by permission and shared items."""
	from .model_cache import get_model_cache

	def _get_s3_mount_root() -> str | None:
		"""Return the S3 mount local_root, or None if not mounted."""
		try:
			from .s3_mounter import get_mount_manager

			mgr = get_mount_manager()
			if mgr is not None and mgr.is_mounted():
				return mgr.models_root
		except Exception:
			pass
		return None

	def _s3_item_names(mount_root: str, folder: str) -> frozenset[str]:
		"""Return the set of filenames that live under the S3 mount for a folder."""
		if not folder or ".." in folder or "/" in folder or "\\" in folder:
			return frozenset()
		s3_dir = os.path.join(mount_root, folder)
		if not os.path.isdir(s3_dir):
			return frozenset()
		names: set[str] = set()
		for dirpath, _, filenames in os.walk(s3_dir):
			rel = os.path.relpath(dirpath, s3_dir)
			for fn in filenames:
				if rel == ".":
					names.add(fn)
				else:
					names.add(os.path.join(rel, fn))
		return frozenset(normalize_model_name(name) for name in names)

	def _strip_s3_items(items: list[str], folder: str, role: str, perms: dict) -> list[str]:
		"""Remove S3-mounted items from the list when the user lacks S3 access."""
		if user_can_access_s3(role, perms):
			return items
		mount_root = _get_s3_mount_root()
		if mount_root is None:
			return items
		s3_names = _s3_item_names(mount_root, folder)
		if not s3_names:
			return items
		return [item for item in items if normalize_model_name(item) not in s3_names]

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
			if user_can_view_all_models(role, perms):
				return web.json_response(folder_names)
			grants = get_effective_model_grants_for_user(
				role=role,
				perms=perms,
				username=username,
				users_db=users_db,
				shared_items_store_getter=get_shared_items_store,
				users_db_config=users_db_config,
			)
			if not grants:
				return web.json_response([])
			allowed_folders = {g["folder"] for g in grants}
			filtered = [f for f in folder_names if f in allowed_folders]
			return web.json_response(filtered)

		if path.startswith("/models/"):
			folder = path[len("/models/") :].strip("/")
			if not is_safe_folder_segment(folder):
				return web.json_response([])
			folder = _map_legacy(folder)
			role, perms, username = get_user_role_and_permissions(request)
			full_list = _get_item_list(folder)
			full_list = _strip_s3_items(full_list, folder, role, perms)
			if user_can_view_all_models(role, perms):
				return web.json_response(full_list)
			grants = get_effective_model_grants_for_user(
				role=role,
				perms=perms,
				username=username,
				users_db=users_db,
				shared_items_store_getter=get_shared_items_store,
				users_db_config=users_db_config,
			)
			if not grants:
				return web.json_response([])
			filtered = filter_items_by_grants(folder, full_list, grants)
			return web.json_response(filtered)

		if path == "/embeddings":
			role, perms, username = get_user_role_and_permissions(request)
			full_list = _get_item_list("embeddings")
			full_list = _strip_s3_items(full_list, "embeddings", role, perms)
			if user_can_view_all_models(role, perms):
				return web.json_response(full_list)
			grants = get_effective_model_grants_for_user(
				role=role,
				perms=perms,
				username=username,
				users_db=users_db,
				shared_items_store_getter=get_shared_items_store,
				users_db_config=users_db_config,
			)
			if not grants:
				return web.json_response([])
			filtered = filter_items_by_grants("embeddings", full_list, grants)
			return web.json_response(filtered)

		return await handler(request)

	return model_filter_middleware
