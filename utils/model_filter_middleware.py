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

from ..constants import experimental_model_isolation_enabled
from .isolation_resolver import list_display_names, resolve_model_name
from .model_path_display import (
	map_experiment_items_to_display,
	map_item_list_to_display,
	map_object_info_combos_to_display,
	to_display_name,
	to_storage_name,
)
from .model_visibility_policy import (
	filter_items_by_grants,
	get_effective_model_grants_for_user,
	get_user_id_for_username,
	grant_matches_item,
	normalize_model_name,
	user_can_access_s3,
	user_can_view_all_models,
)
from .path_safety import is_safe_folder_segment
from .prompt_model_validator import (
	_looks_like_model_file,
	folder_for_node_input,
)

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
					grant_matches_item(normalize_model_name(x), all_allowed)
					or any(
						normalize_model_name(x).endswith(ext)
						for ext in (".safetensors", ".ckpt", ".pt", ".pth", ".bin")
					)
					for x in combo
				)
				if not looks_like_models:
					continue
				filtered = [
					x for x in combo if grant_matches_item(normalize_model_name(x), all_allowed)
				]
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
		user_id = get_user_id_for_username(users_db, username)
		return role, perms, username, grants, user_id

	def _flatten_strings(
		items: list[str], user_id: str | None, role: str, perms: dict, grants: list
	) -> list[str]:
		if not experimental_model_isolation_enabled():
			return items
		view_all = user_can_view_all_models(role, perms)
		return map_item_list_to_display(items, user_id, view_all=view_all, grants=grants)

	def _flatten_experiment_items(
		items: list, user_id: str | None, role: str, perms: dict, grants: list
	) -> list:
		if not experimental_model_isolation_enabled():
			return items
		view_all = user_can_view_all_models(role, perms)
		return map_experiment_items_to_display(items, user_id, view_all=view_all, grants=grants)

	def _filter_string_list(
		folder: str, full_list: list[str], role: str, perms: dict, grants: list, user_id: str | None
	):
		full_list = _strip_s3_items(full_list, folder, role, perms)
		view_all = user_can_view_all_models(role, perms)
		if experimental_model_isolation_enabled():
			# Single list builder: shared ∪ own ∪ granted (plain names, shadowing)
			display = list_display_names(folder, user_id, grants=grants, view_all=view_all)
			return _strip_s3_items(display, folder, role, perms)
		if view_all:
			return _flatten_strings(full_list, user_id, role, perms, grants)
		if not grants:
			return []
		filtered = filter_items_by_grants(folder, full_list, grants)
		return _flatten_strings(filtered, user_id, role, perms, grants)

	def _filter_experiment_items(
		folder: str, items: list, role: str, perms: dict, grants: list, user_id: str | None
	):
		"""Filter experiment/models item objects that have a 'name' field."""
		if user_can_view_all_models(role, perms):
			if user_can_access_s3(role, perms):
				return _flatten_experiment_items(items, user_id, role, perms, grants)
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
			return _flatten_experiment_items(out, user_id, role, perms, grants)
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
				if isinstance(n, str) and grant_matches_item(normalize_model_name(n), allowed):
					out.append(it)
			elif isinstance(it, str) and grant_matches_item(normalize_model_name(it), allowed):
				out.append(it)
		return _flatten_experiment_items(out, user_id, role, perms, grants)

	@web.middleware
	async def model_filter_middleware(request: web.Request, handler):
		if request.method != "GET":
			return await handler(request)
		path = _normalize_request_path(request.path)

		# --- Classic model list endpoints ---
		if path == "/models":
			role, perms, username, grants, _user_id = _grants_for(request)
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
			role, perms, username, grants, user_id = _grants_for(request)
			full_list = _get_item_list(folder)
			filtered = _filter_string_list(folder, full_list, role, perms, grants, user_id)
			return web.json_response(filtered)

		if path == "/embeddings":
			role, perms, username, grants, user_id = _grants_for(request)
			full_list = _get_item_list("embeddings")
			filtered = _filter_string_list("embeddings", full_list, role, perms, grants, user_id)
			return web.json_response(filtered)

		# --- Experiment model library (Models sidebar) ---
		if path == "/experiment/models":
			role, perms, username, grants, _user_id = _grants_for(request)
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
			role, perms, username, grants, user_id = _grants_for(request)
			resp = await handler(request)
			return await _maybe_filter_json_response(
				resp,
				lambda data: (
					_filter_experiment_items(folder, data, role, perms, grants, user_id)
					if isinstance(data, list)
					else data
				),
			)

		# --- object_info combo filtering (full + /object_info/{NodeClass}) ---
		if path == "/object_info" or path.startswith("/object_info/"):
			role, perms, username, grants, user_id = _grants_for(request)
			view_all = user_can_view_all_models(role, perms)
			resp = await handler(request)

			def _filter_and_flatten(data):
				# When isolation is on, rebuild loader combos from list_display_names
				# so view_all never exposes other users' UUID prefixes.
				if experimental_model_isolation_enabled() and isinstance(data, dict):
					for node_name, node_info in data.items():
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
								if not all(isinstance(x, str) for x in combo):
									continue
								looks_like_models = any(
									_looks_like_model_file(x) or "/" in x.replace("\\", "/")
									for x in combo
								)
								if not looks_like_models:
									continue
								# Per (class_type, input_key) — avoids clip_name / model_name collisions
								folder = folder_for_node_input(node_name, key) or ""
								if folder:
									mapped = list_display_names(
										folder, user_id, grants=grants, view_all=view_all
									)
								else:
									mapped = map_item_list_to_display(
										combo, user_id, view_all=view_all, grants=grants
									)
									if not view_all and grants:
										# Grant-filter unknown combos (isolation path skips
										# _filter_object_info_combos for these keys)
										allowed = {
											g["norm_item_name"]
											for g in grants
											if g.get("norm_item_name")
										}
										mapped = [
											x
											for x in mapped
											if grant_matches_item(
												normalize_model_name(x), allowed
											)
										]
									elif not view_all:
										mapped = []
								section_map[key] = [mapped, *list(spec[1:])]
					return data
				if view_all:
					return map_object_info_combos_to_display(
						data, user_id, view_all=True, grants=grants
					)
				filtered = _filter_object_info_combos(data, grants)
				return map_object_info_combos_to_display(
					filtered, user_id, view_all=False, grants=grants
				)

			return await _maybe_filter_json_response(resp, _filter_and_flatten)

		return await handler(request)

	async def _filter_experiment_preview(request, handler, rest: str):
		role, perms, username, grants, user_id = _grants_for(request)
		view_all = user_can_view_all_models(role, perms)
		# rest like preview/<folder>/<filename> or similar — best-effort grant check
		parts = [p for p in rest.split("/") if p]
		if len(parts) >= 3:
			folder = _map_legacy(parts[1])
			filename = "/".join(parts[2:])
			resolved = resolve_model_name(
				folder, filename, user_id, grants=grants, view_all=view_all
			)
			storage = (
				resolved.storage_name
				if resolved is not None
				else to_storage_name(filename, user_id, grants, folder=folder, view_all=view_all)
			)
			if not view_all:
				allowed = {
					g["norm_item_name"]
					for g in grants
					if g.get("folder") == folder and g.get("norm_item_name")
				}
				# Dual-read: flat display or storage path
				display = to_display_name(storage, user_id, view_all=False, grants=grants)
				ok = (
					normalize_model_name(storage) in allowed
					or normalize_model_name(filename) in allowed
					or normalize_model_name(display) in allowed
				)
				if not ok:
					return web.Response(status=403, text="Model not allowed")
			# Redirect flat preview paths to storage path so Comfy can resolve the file
			if (
				experimental_model_isolation_enabled()
				and storage
				and normalize_model_name(storage) != normalize_model_name(filename)
			):
				prefix = "/api" if request.path.startswith("/api/") else ""
				new_path = f"{prefix}/experiment/models/preview/{folder}/{storage}"
				raise web.HTTPFound(new_path)
		elif not view_all and not grants:
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
