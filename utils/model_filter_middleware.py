# --- START OF FILE utils/model_filter_middleware.py ---
"""
Middleware: intercept GET model-list and object_info routes.
Granular model visibility: only show models the user has explicit permission to access.

Covers:
- /models, /models/{folder}, /embeddings (+ /api prefixes)
- /experiment/models, /experiment/models/{folder} (+ /api prefixes)
- /object_info (+ /api prefix) — strip ungranted combo list entries

Permission logic:
- When experimental model isolation is off: show the shared library (S3 items
  still hidden without can_access_s3_storage). Output/workflow isolation does
  not empty this list.
- When isolation is on: owner sees all; admin needs can_view_all_comfyui_items
  plus can_manage_model_sharing; everyone else sees only shared_items grants.
"""

from __future__ import annotations

import json
import os
from typing import Any

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


def _normalize_request_path(path: str) -> str:
	"""Strip trailing slash and optional /api prefix for matching."""
	p = (path or "").rstrip("/")
	if p.startswith("/api/"):
		p = p[4:]  # keep leading slash: /api/models -> /models
	elif p == "/api":
		p = "/"
	return p


def _map_legacy(folder_name: str) -> str:
	try:
		return folder_paths.map_legacy(folder_name)
	except AttributeError:
		return {"unet": "diffusion_models", "clip": "text_encoders"}.get(folder_name, folder_name)


def _filter_object_info_combos(payload: Any, grants: list[dict]) -> Any:
	"""Strip ungranted model filenames from object_info combo inputs."""
	if not isinstance(payload, dict):
		return payload
	allowed_by_folder: dict[str, set[str]] = {}
	for g in grants:
		folder = (g.get("folder") or "").strip()
		norm = g.get("norm_item_name") or normalize_model_name(g.get("item_name") or "")
		if not folder or not norm:
			continue
		allowed_by_folder.setdefault(folder, set()).add(norm)
		# Also allow legacy aliases
		if folder == "diffusion_models":
			allowed_by_folder.setdefault("unet", set()).add(norm)
		if folder == "text_encoders":
			allowed_by_folder.setdefault("clip", set()).add(norm)

	all_allowed = set()
	for names in allowed_by_folder.values():
		all_allowed |= names

	for _node_name, node_info in payload.items():
		if not isinstance(node_info, dict):
			continue
		inp = node_info.get("input")
		if not isinstance(inp, dict):
			continue
		for section in ("required", "optional"):
			section_map = inp.get(section)
			if not isinstance(section_map, dict):
				continue
			for key, spec in list(section_map.items()):
				if not isinstance(spec, (list, tuple)) or not spec:
					continue
				combo = spec[0]
				if not isinstance(combo, list) or not combo:
					continue
				# Heuristic: combo of strings that look like filenames
				if not all(isinstance(x, str) for x in combo):
					continue
				looks_like_models = any(
					normalize_model_name(x) in all_allowed
					or any(
						normalize_model_name(x).endswith(ext)
						for ext in (".safetensors", ".ckpt", ".pt", ".pth", ".bin")
					)
					for x in combo
				)
				if not looks_like_models:
					continue
				filtered = [x for x in combo if normalize_model_name(x) in all_allowed]
				# Rebuild spec with filtered combo
				new_spec = [filtered, *list(spec[1:])]
				section_map[key] = new_spec
	return payload


def create_model_filter_middleware(
	get_user_role_and_permissions, get_shared_items_store, users_db, users_db_config
):
	"""Build middleware that filters model lists and object_info by permission and shared items."""
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

	def _grants_for(request: web.Request):
		role, perms, username = get_user_role_and_permissions(request)
		grants = get_effective_model_grants_for_user(
			role=role,
			perms=perms,
			username=username,
			users_db=users_db,
			shared_items_store_getter=get_shared_items_store,
			users_db_config=users_db_config,
		)
		return role, perms, username, grants

	def _filter_string_list(
		folder: str, full_list: list[str], role: str, perms: dict, grants: list
	):
		full_list = _strip_s3_items(full_list, folder, role, perms)
		if user_can_view_all_models(role, perms):
			return full_list
		if not grants:
			return []
		return filter_items_by_grants(folder, full_list, grants)

	def _filter_experiment_items(folder: str, items: list, role: str, perms: dict, grants: list):
		"""Filter experiment/models item objects that have a 'name' field."""
		if user_can_view_all_models(role, perms):
			if user_can_access_s3(role, perms):
				return items
			# Still strip S3 by name when no S3 access
			names = [
				it.get("name") if isinstance(it, dict) else (it if isinstance(it, str) else None)
				for it in items
			]
			names = [n for n in names if isinstance(n, str)]
			allowed_names = set(_strip_s3_items(names, folder, role, perms))
			out = []
			for it in items:
				if isinstance(it, dict):
					n = it.get("name")
					if isinstance(n, str) and n in allowed_names:
						out.append(it)
				elif isinstance(it, str) and it in allowed_names:
					out.append(it)
			return out
		if not grants:
			return []
		allowed = {
			g["norm_item_name"]
			for g in grants
			if g.get("folder") == folder and g.get("norm_item_name")
		}
		out = []
		for it in items:
			if isinstance(it, dict):
				n = it.get("name")
				if isinstance(n, str) and normalize_model_name(n) in allowed:
					out.append(it)
			elif isinstance(it, str) and normalize_model_name(it) in allowed:
				out.append(it)
		return out

	@web.middleware
	async def model_filter_middleware(request: web.Request, handler):
		if request.method != "GET":
			return await handler(request)
		path = _normalize_request_path(request.path)

		# --- Classic model list endpoints ---
		if path == "/models":
			role, perms, username, grants = _grants_for(request)
			folder_names = _get_folder_list()
			if user_can_view_all_models(role, perms):
				return web.json_response(folder_names)
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
			role, perms, username, grants = _grants_for(request)
			full_list = _get_item_list(folder)
			filtered = _filter_string_list(folder, full_list, role, perms, grants)
			return web.json_response(filtered)

		if path == "/embeddings":
			role, perms, username, grants = _grants_for(request)
			full_list = _get_item_list("embeddings")
			filtered = _filter_string_list("embeddings", full_list, role, perms, grants)
			return web.json_response(filtered)

		# --- Experiment model library (Models sidebar) ---
		if path == "/experiment/models":
			role, perms, username, grants = _grants_for(request)
			folder_names = _get_folder_list()
			if user_can_view_all_models(role, perms):
				return await handler(request)
			if not grants:
				return web.json_response([])
			# Let core handler run, then filter response if it is a list of folder names/objects
			resp = await handler(request)
			return await _maybe_filter_json_response(
				resp, lambda data: _filter_experiment_folder_list(data, grants, folder_names)
			)

		if path.startswith("/experiment/models/"):
			rest = path[len("/experiment/models/") :].strip("/")
			# Preview routes: /experiment/models/preview/...
			if rest.startswith("preview"):
				return await _filter_experiment_preview(request, handler, rest)
			folder = rest.split("/")[0] if rest else ""
			if not is_safe_folder_segment(folder):
				return web.json_response([])
			folder = _map_legacy(folder)
			role, perms, username, grants = _grants_for(request)
			resp = await handler(request)
			return await _maybe_filter_json_response(
				resp,
				lambda data: (
					_filter_experiment_items(folder, data, role, perms, grants)
					if isinstance(data, list)
					else data
				),
			)

		# --- object_info combo filtering ---
		if path in ("/object_info",):
			role, perms, username, grants = _grants_for(request)
			if user_can_view_all_models(role, perms):
				return await handler(request)
			resp = await handler(request)
			return await _maybe_filter_json_response(
				resp, lambda data: _filter_object_info_combos(data, grants)
			)

		return await handler(request)

	async def _filter_experiment_preview(request, handler, rest: str):
		role, perms, username, grants = _grants_for(request)
		if user_can_view_all_models(role, perms):
			return await handler(request)
		# rest like preview/<folder>/<filename> or similar — best-effort grant check
		parts = [p for p in rest.split("/") if p]
		if len(parts) >= 3:
			folder = _map_legacy(parts[1])
			filename = "/".join(parts[2:])
			allowed = {
				g["norm_item_name"]
				for g in grants
				if g.get("folder") == folder and g.get("norm_item_name")
			}
			if normalize_model_name(filename) not in allowed:
				return web.Response(status=403, text="Model not allowed")
		elif not grants:
			return web.Response(status=403, text="Model not allowed")
		return await handler(request)

	def _filter_experiment_folder_list(data, grants, folder_names):
		allowed_folders = {g["folder"] for g in grants}
		if isinstance(data, list):
			out = []
			for item in data:
				if isinstance(item, str):
					if item in allowed_folders:
						out.append(item)
				elif isinstance(item, dict):
					name = item.get("name") or item.get("folder")
					if isinstance(name, str) and name in allowed_folders:
						out.append(item)
			return out
		return data

	async def _maybe_filter_json_response(resp: web.StreamResponse, transform):
		if not isinstance(resp, web.Response):
			return resp
		ctype = (resp.content_type or "").lower()
		if "json" not in ctype and resp.body is None:
			return resp
		try:
			body = resp.body
			if body is None:
				return resp
			if isinstance(body, (bytes, bytearray)):
				raw = bytes(body)
			elif isinstance(body, str):
				raw = body.encode("utf-8")
			else:
				return resp
			data = json.loads(raw.decode("utf-8"))
			filtered = transform(data)
			return web.json_response(filtered, status=resp.status)
		except Exception:
			return resp

	return model_filter_middleware


# --- END OF FILE utils/model_filter_middleware.py ---
