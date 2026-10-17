"""
Display flattening for experimental model isolation paths.

On disk and in grants, isolated models stay as "{user_uuid}/file.safetensors".
List APIs and loader combos expose a flat display name. Prompt values stay as
plain (user-facing) names; folder_paths hooks resolve them at load time.
"""

from __future__ import annotations

import copy
from typing import Any

from ..constants import experimental_model_isolation_enabled
from .isolation_resolver import foreign_uuid_prefix, is_uuid_segment, resolve_model_name
from .model_isolation import sanitize_user_segment
from .model_visibility_policy import normalize_model_name
from .prompt_model_validator import (
	NODE_MODEL_INPUTS,
	_looks_like_model_file,
	_normalize_folder,
	folder_for_node_input,
)


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
	- Leading UUID segment (own or other): strip to plain relative name so loader
	  combos never expose another user's UUID prefix.
	- Grant match: strip first segment when storage-shaped.
	"""
	_ = view_all
	if not experimental_model_isolation_enabled():
		return item_name
	name = _norm_path(item_name)
	if not name:
		return item_name
	viewer_seg = sanitize_user_segment(viewer_user_id)
	prefix = f"{viewer_seg}/"
	if name.startswith(prefix) or name.lower().startswith(prefix.lower()):
		return name[len(viewer_seg) + 1 :]
	parts = name.split("/")
	if len(parts) >= 2 and is_uuid_segment(parts[0]):
		# Never show other users' UUID prefixes in loader-facing lists
		return "/".join(parts[1:])
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
	view_all: bool = False,
) -> str:
	"""
	Map a flat (or already-prefixed) display name back to the storage path.

	Uses isolation_resolver: prefixes only when the own isolation file exists or
	a grant matches. Never fabricates a UUID prefix for shared library names.
	"""
	if not experimental_model_isolation_enabled():
		return display_name
	name = _norm_path(display_name)
	if not name:
		return display_name
	if foreign_uuid_prefix(name, viewer_user_id):
		# Leave unchanged; callers reject with a generic error
		return name
	resolved = resolve_model_name(
		folder or "", name, viewer_user_id, grants=grants, view_all=view_all
	)
	if resolved is not None:
		return resolved.storage_name
	return name


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
		# Drop entries that would still expose a foreign UUID prefix
		if "/" in display and is_uuid_segment(display.split("/", 1)[0]):
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
			if "/" in display and is_uuid_segment(display.split("/", 1)[0]):
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
			if "/" in display and is_uuid_segment(display.split("/", 1)[0]):
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


def _normalize_own_prefix(raw: str, viewer_seg: str) -> str:
	own_prefix = f"{viewer_seg}/"
	if raw.startswith(own_prefix) or raw.lower().startswith(own_prefix.lower()):
		return raw[len(viewer_seg) + 1 :]
	# Any UUID first segment → strip to plain for display/outbound (own handled above;
	# foreign cases are rejected separately before this runs on /prompt).
	parts = raw.split("/", 1)
	if len(parts) == 2 and is_uuid_segment(parts[0]):
		return parts[1]
	return raw


def rewrite_prompt_model_names(
	prompt: dict[str, Any],
	viewer_user_id: str | None,
	grants: list[dict] | None = None,
	*,
	known_models: set[tuple[str, str]] | None = None,
	normalize_only: bool = True,
) -> dict[str, Any]:
	"""
	Normalize model filename inputs in an API-format prompt.

	Default (normalize_only=True): strip UUID prefixes to plain names so ComfyUI
	validate_inputs agrees with patched get_filename_list. Does not fabricate
	storage prefixes. Touches NODE_MODEL_INPUTS keys and any model-looking strings.

	Mutates and returns the same prompt dict. No-op when isolation is off.
	"""
	_ = known_models
	if not experimental_model_isolation_enabled() or not isinstance(prompt, dict):
		return prompt

	viewer_seg = sanitize_user_segment(viewer_user_id)

	for _node_id, node in prompt.items():
		if not isinstance(node, dict):
			continue
		class_type = node.get("class_type")
		inputs = node.get("inputs")
		if not isinstance(inputs, dict):
			continue
		rewritten: set[str] = set()
		if isinstance(class_type, str):
			mappings = NODE_MODEL_INPUTS.get(class_type) or []
			for folder, input_key in mappings:
				value = inputs.get(input_key)
				if not isinstance(value, str) or not value.strip():
					continue
				raw = value.strip().replace("\\", "/")
				folder_norm = _normalize_folder(folder)
				if normalize_only:
					inputs[input_key] = _normalize_own_prefix(raw, viewer_seg)
				else:
					inputs[input_key] = to_storage_name(
						raw, viewer_user_id, grants, folder=folder_norm
					)
				rewritten.add(input_key)
		# Catch-all for unmapped loaders / extra model-like strings
		if normalize_only:
			for key, value in list(inputs.items()):
				if key in rewritten:
					continue
				if not isinstance(value, str) or not value.strip():
					continue
				if not _looks_like_model_file(value):
					continue
				raw = value.strip().replace("\\", "/")
				inputs[key] = _normalize_own_prefix(raw, viewer_seg)

	return prompt


def flatten_prompt_model_names_for_display(
	prompt: dict[str, Any], viewer_user_id: str | None, grants: list[dict] | None = None
) -> dict[str, Any]:
	"""
	Return a deep-copied prompt with model filenames flattened to plain names.

	Used for CPE convert/get responses so Portal never sees UUID isolation prefixes.
	"""
	if not isinstance(prompt, dict):
		return prompt

	cloned = copy.deepcopy(prompt)
	rewrite_prompt_model_names(cloned, viewer_user_id, grants, normalize_only=True)
	# Also strip any remaining UUID first segments via to_display_name
	for _node_id, node in cloned.items():
		if not isinstance(node, dict):
			continue
		inputs = node.get("inputs")
		if not isinstance(inputs, dict):
			continue
		class_type = node.get("class_type")
		for key, value in list(inputs.items()):
			if not isinstance(value, str) or not value.strip():
				continue
			if not _looks_like_model_file(value) and "/" not in value.replace("\\", "/"):
				continue
			folder = folder_for_node_input(class_type if isinstance(class_type, str) else None, key)
			_ = folder
			inputs[key] = to_display_name(
				value.strip(), viewer_user_id, view_all=False, grants=grants
			)
	return cloned


def prompt_has_foreign_uuid_model(prompt: dict[str, Any], viewer_user_id: str | None) -> bool:
	"""True if any model-looking input uses another user's UUID prefix."""
	if not isinstance(prompt, dict):
		return False
	for _node_id, node in prompt.items():
		if not isinstance(node, dict):
			continue
		class_type = node.get("class_type")
		inputs = node.get("inputs")
		if not isinstance(inputs, dict):
			continue
		checked: set[str] = set()
		if isinstance(class_type, str):
			for _folder, input_key in NODE_MODEL_INPUTS.get(class_type) or []:
				value = inputs.get(input_key)
				checked.add(input_key)
				if isinstance(value, str) and foreign_uuid_prefix(value.strip(), viewer_user_id):
					return True
		for key, value in inputs.items():
			if key in checked:
				continue
			if not isinstance(value, str) or not value.strip():
				continue
			if not _looks_like_model_file(value):
				continue
			if foreign_uuid_prefix(value.strip(), viewer_user_id):
				return True
	return False
