"""
Display flattening for experimental model isolation paths.

On disk and in grants, isolated models stay as "{user_uuid}/file.safetensors".
List APIs and loader combos expose a flat display name; inbound prompts are
rewritten back to the storage path before ComfyUI resolves files.
"""

from __future__ import annotations

from typing import Any

from ..constants import experimental_model_isolation_enabled
from .model_isolation import sanitize_user_segment
from .model_visibility_policy import normalize_model_name
from .prompt_model_validator import NODE_MODEL_INPUTS, _looks_like_model_file, _normalize_folder


def _norm_path(item_name: str) -> str:
	return (item_name or "").replace("\\", "/").strip()


def _strip_first_segment(name: str) -> str:
	parts = _norm_path(name).split("/")
	if len(parts) < 2:
		return name
	return "/".join(parts[1:])


def to_display_name(
	item_name: str,
	viewer_user_id: str | None,
	*,
	view_all: bool = False,
	grants: list[dict] | None = None,
) -> str:
	"""
	Return the UI-facing model name.

	- Isolation off: unchanged.
	- view_all: strip only the viewer's own UUID prefix.
	- otherwise: strip the leading user segment when the item matches a grant
	  (covers own and shared-from-other isolation paths).
	"""
	if not experimental_model_isolation_enabled():
		return item_name
	name = _norm_path(item_name)
	if not name:
		return item_name
	viewer_seg = sanitize_user_segment(viewer_user_id)
	prefix = f"{viewer_seg}/"
	if name.startswith(prefix) or name.lower().startswith(prefix.lower()):
		# Preserve remainder casing from original
		return name[len(viewer_seg) + 1 :]
	if view_all:
		return name
	for g in grants or []:
		storage = _norm_path(g.get("item_name") or "")
		if not storage:
			continue
		if normalize_model_name(storage) != normalize_model_name(name):
			continue
		if "/" in storage:
			return _strip_first_segment(storage)
		return name
	return name


def to_storage_name(
	display_name: str,
	viewer_user_id: str | None,
	grants: list[dict] | None = None,
	*,
	folder: str | None = None,
) -> str:
	"""
	Map a flat (or already-prefixed) display name back to the storage path.

	Dual-read order:
	1. Exact grant match
	2. "{viewer_seg}/{display}" grant match
	3. Grant whose suffix after the first segment equals display
	4. Isolation default: prepend viewer segment when display has no slash
	"""
	if not experimental_model_isolation_enabled():
		return display_name
	name = _norm_path(display_name)
	if not name:
		return display_name
	viewer_seg = sanitize_user_segment(viewer_user_id)
	folder_norm = (folder or "").strip()
	candidates = []
	for g in grants or []:
		g_folder = (g.get("folder") or "").strip()
		if folder_norm and g_folder and g_folder != folder_norm:
			# Allow legacy folder aliases
			if not (
				(folder_norm == "unet" and g_folder == "diffusion_models")
				or (folder_norm == "diffusion_models" and g_folder == "unet")
				or (folder_norm == "clip" and g_folder == "text_encoders")
				or (folder_norm == "text_encoders" and g_folder == "clip")
			):
				continue
		item = g.get("item_name")
		if isinstance(item, str) and item.strip():
			candidates.append(item.strip().replace("\\", "/"))

	norm_name = normalize_model_name(name)
	for storage in candidates:
		if normalize_model_name(storage) == norm_name:
			return storage

	prefixed = f"{viewer_seg}/{name}"
	norm_prefixed = normalize_model_name(prefixed)
	for storage in candidates:
		if normalize_model_name(storage) == norm_prefixed:
			return storage

	for storage in candidates:
		if "/" not in storage:
			continue
		suffix = storage.split("/", 1)[1]
		if normalize_model_name(suffix) == norm_name:
			return storage

	if "/" in name:
		return name
	return prefixed


def map_item_list_to_display(
	items: list[str],
	viewer_user_id: str | None,
	*,
	view_all: bool = False,
	grants: list[dict] | None = None,
) -> list[str]:
	"""Map a list of storage item names to display names (stable order, de-duped)."""
	seen: set[str] = set()
	out: list[str] = []
	for item in items:
		if not isinstance(item, str):
			continue
		display = to_display_name(item, viewer_user_id, view_all=view_all, grants=grants)
		key = normalize_model_name(display)
		if key in seen:
			continue
		seen.add(key)
		out.append(display)
	return out


def map_experiment_items_to_display(
	items: list,
	viewer_user_id: str | None,
	*,
	view_all: bool = False,
	grants: list[dict] | None = None,
) -> list:
	"""Map experiment/models item objects (or strings) to display names."""
	out = []
	seen: set[str] = set()
	for it in items:
		if isinstance(it, dict):
			n = it.get("name")
			if not isinstance(n, str):
				out.append(it)
				continue
			display = to_display_name(n, viewer_user_id, view_all=view_all, grants=grants)
			key = normalize_model_name(display)
			if key in seen:
				continue
			seen.add(key)
			cloned = dict(it)
			cloned["name"] = display
			out.append(cloned)
		elif isinstance(it, str):
			display = to_display_name(it, viewer_user_id, view_all=view_all, grants=grants)
			key = normalize_model_name(display)
			if key in seen:
				continue
			seen.add(key)
			out.append(display)
		else:
			out.append(it)
	return out


def map_object_info_combos_to_display(
	payload: Any,
	viewer_user_id: str | None,
	*,
	view_all: bool = False,
	grants: list[dict] | None = None,
) -> Any:
	"""Flatten isolation prefixes inside object_info combo lists."""
	if not experimental_model_isolation_enabled() or not isinstance(payload, dict):
		return payload
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
				if not all(isinstance(x, str) for x in combo):
					continue
				looks_like_models = any(
					_looks_like_model_file(x) or "/" in x.replace("\\", "/") for x in combo
				)
				if not looks_like_models:
					continue
				mapped = map_item_list_to_display(
					combo, viewer_user_id, view_all=view_all, grants=grants
				)
				section_map[key] = [mapped, *list(spec[1:])]
	return payload


def rewrite_prompt_model_names(
	prompt: dict[str, Any],
	viewer_user_id: str | None,
	grants: list[dict] | None = None,
	*,
	known_models: set[tuple[str, str]] | None = None,
) -> dict[str, Any]:
	"""
	Rewrite model filename inputs in an API-format prompt to storage paths.

	Mutates and returns the same prompt dict. No-op when isolation is off.
	"""
	if not experimental_model_isolation_enabled() or not isinstance(prompt, dict):
		return prompt

	for _node_id, node in prompt.items():
		if not isinstance(node, dict):
			continue
		class_type = node.get("class_type")
		inputs = node.get("inputs")
		if not isinstance(inputs, dict):
			continue
		rewritten_keys: set[str] = set()
		if isinstance(class_type, str):
			mappings = NODE_MODEL_INPUTS.get(class_type) or []
			for folder, input_key in mappings:
				value = inputs.get(input_key)
				if not isinstance(value, str) or not value.strip():
					continue
				folder_norm = _normalize_folder(folder)
				inputs[input_key] = to_storage_name(
					value.strip(), viewer_user_id, grants, folder=folder_norm
				)
				rewritten_keys.add(input_key)

		# Catch-all for unknown nodes / extra model-like strings
		for key, value in list(inputs.items()):
			if key in rewritten_keys:
				continue
			if not isinstance(value, str) or not value.strip():
				continue
			if not _looks_like_model_file(value):
				continue
			raw = value.strip()
			folder_hint: str | None = None
			if known_models:
				# Prefer folder from known storage or display match
				for folder, name in known_models:
					if normalize_model_name(name) == normalize_model_name(raw):
						folder_hint = folder
						break
					if normalize_model_name(_strip_first_segment(name)) == normalize_model_name(
						raw
					):
						folder_hint = folder
						break
			inputs[key] = to_storage_name(raw, viewer_user_id, grants, folder=folder_hint)

	return prompt
