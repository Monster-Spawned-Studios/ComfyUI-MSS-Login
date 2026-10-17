"""
Idempotent wrappers for folder_paths.get_filename_list / get_full_path.

When isolation is on and a request/execution user is set, lists return per-user
plain display names and get_full_path resolves through isolation_resolver.
Cache refresh must call get_raw_filename_list to store storage-shaped names.
"""

from __future__ import annotations

import json
import os
from typing import Any, Callable

from ..constants import experimental_model_isolation_enabled

_installed = False
_raw_get_filename_list: Callable[[str], list[str]] | None = None
_raw_get_full_path: Callable[..., Any] | None = None
_raw_get_full_path_or_raise: Callable[..., Any] | None = None


def get_raw_filename_list(folder: str) -> list[str]:
	"""Return ComfyUI's unpatched filename list (storage-shaped names)."""
	if _raw_get_filename_list is not None:
		try:
			return list(_raw_get_filename_list(folder) or [])
		except Exception:
			return []
	try:
		import folder_paths  # pyright: ignore[reportMissingImports]

		return list(folder_paths.get_filename_list(folder) or [])
	except Exception:
		return []


def _current_user_id() -> str | None:
	try:
		from ..globals import access_control

		uid = access_control.get_current_user_id()
		if uid and str(uid).strip() and str(uid).strip() != "public":
			return str(uid).strip()
	except Exception:
		pass
	return None


def _grants_and_view_all(user_id: str | None) -> tuple[list[dict], bool]:
	"""Best-effort grants + view_all for the current user during execution."""
	grants: list[dict] = []
	view_all = False
	if not user_id:
		return grants, view_all
	try:
		from ..constants import USERS_DB_CONFIG
		from ..globals import access_control
		from .model_visibility_policy import (
			get_effective_model_grants_for_user,
			get_user_id_for_username,
			user_can_view_all_models,
		)
		from .shared_items_store import get_shared_items_store

		user_rec = access_control._lookup_user_record(user_id)
		if not user_rec:
			return grants, view_all
		username = user_rec.get("username")
		groups = [g.lower() for g in user_rec.get("groups", [])]
		role = "owner" if "owner" in groups else ("admin" if "admin" in groups else "user")
		perms: dict = {}
		try:
			from ..constants import CURRENT_DIR

			cfg_path = os.path.join(CURRENT_DIR, "groups_config.json")
			# Prefer runtime path used by access_control when available
			cfg_file = getattr(access_control, "groups_config_file", None) or cfg_path
			if os.path.isfile(cfg_file):
				with open(cfg_file, "r", encoding="utf-8") as f:
					cfg = json.load(f)
				perms = cfg.get(role, {}) if isinstance(cfg, dict) else {}
		except Exception:
			perms = {}
		view_all = user_can_view_all_models(role, perms)
		grants = get_effective_model_grants_for_user(
			role=role,
			perms=perms,
			username=username,
			users_db=access_control.users_db,
			shared_items_store_getter=get_shared_items_store,
			users_db_config=USERS_DB_CONFIG,
		)
		_ = get_user_id_for_username  # imported for API symmetry / future use
	except Exception:
		pass
	return grants, view_all


def _wrapped_get_filename_list(folder: str) -> list[str]:
	if not experimental_model_isolation_enabled():
		return get_raw_filename_list(folder)
	user_id = _current_user_id()
	if not user_id:
		return get_raw_filename_list(folder)
	from .isolation_resolver import list_display_names

	grants, view_all = _grants_and_view_all(user_id)
	return list_display_names(folder, user_id, grants=grants, view_all=view_all)


def _resolve_for_path(folder: str, filename: str) -> str:
	"""Return storage name for get_full_path, or original filename on miss."""
	if not experimental_model_isolation_enabled():
		return filename
	user_id = _current_user_id()
	if not user_id:
		return filename
	from .isolation_resolver import foreign_uuid_prefix, resolve_model_name

	if foreign_uuid_prefix(filename, user_id):
		# Do not resolve into another user's tree
		return filename
	grants, view_all = _grants_and_view_all(user_id)
	resolved = resolve_model_name(folder, filename, user_id, grants=grants, view_all=view_all)
	if resolved is None:
		return filename
	return resolved.storage_name


def _wrapped_get_full_path(folder: str, filename: str, *args, **kwargs):
	storage = _resolve_for_path(folder, filename)
	assert _raw_get_full_path is not None
	return _raw_get_full_path(folder, storage, *args, **kwargs)


def _wrapped_get_full_path_or_raise(folder: str, filename: str, *args, **kwargs):
	storage = _resolve_for_path(folder, filename)
	assert _raw_get_full_path_or_raise is not None
	return _raw_get_full_path_or_raise(folder, storage, *args, **kwargs)


def install_isolation_folder_path_hooks() -> bool:
	"""
	Patch folder_paths list/path APIs once. Returns True when newly installed.
	Safe to call multiple times (idempotent).
	"""
	global _installed, _raw_get_filename_list, _raw_get_full_path, _raw_get_full_path_or_raise
	if _installed:
		return False
	try:
		import folder_paths  # pyright: ignore[reportMissingImports]
	except Exception:
		return False

	_raw_get_filename_list = folder_paths.get_filename_list
	folder_paths.get_filename_list = _wrapped_get_filename_list

	if hasattr(folder_paths, "get_full_path") and callable(folder_paths.get_full_path):
		_raw_get_full_path = folder_paths.get_full_path
		folder_paths.get_full_path = _wrapped_get_full_path

	if hasattr(folder_paths, "get_full_path_or_raise") and callable(
		folder_paths.get_full_path_or_raise
	):
		_raw_get_full_path_or_raise = folder_paths.get_full_path_or_raise
		folder_paths.get_full_path_or_raise = _wrapped_get_full_path_or_raise

	_installed = True
	return True
