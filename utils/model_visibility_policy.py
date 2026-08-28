"""
Shared model visibility policy helpers.

Centralizes model isolation and sharing logic so /models and /prompt checks stay in sync.
"""

from __future__ import annotations

from collections.abc import Callable

from ..constants import experimental_model_isolation_enabled


def user_can_access_s3(role: str, perms: dict) -> bool:
	"""Return True when role may access S3-backed model entries."""
	val = perms.get("can_access_s3_storage")
	if val is None:
		return role in ("admin", "owner")
	return val is True


def user_can_manage_model_sharing(role: str, perms: dict) -> bool:
	"""Owner always can share; admin requires explicit permission."""
	if role == "owner":
		return True
	if role == "admin":
		return perms.get("can_manage_model_sharing", False) is True
	return False


def user_can_download_models(role: str, perms: dict) -> bool:
	"""Return True when role is allowed to queue/download/manage model downloads."""
	val = perms.get("can_download_models")
	if val is None:
		return role in ("admin", "owner")
	return val is True


def user_can_view_all_models(role: str, perms: dict) -> bool:
	"""
	Return True when user can bypass per-item grants.

	When experimental model isolation is off, every role uses the shared
	ComfyUI model library (S3 entries can still be stripped separately).
	Per-user output/workflow folders (SEPERATE_USERS) do not hide checkpoints,
	so ComfyUI and Comfy Portal generation keep working.

	In isolation mode, owner always bypasses; admin bypass requires both
	can_view_all_comfyui_items and can_manage_model_sharing. Other roles see
	only explicitly granted models.
	"""
	if not experimental_model_isolation_enabled():
		return True
	if role == "owner":
		return True
	if role == "admin":
		return perms.get(
			"can_view_all_comfyui_items", False
		) is True and user_can_manage_model_sharing(role, perms)
	return False


def get_user_id_for_username(users_db, username: str | None) -> str | None:
	if not username:
		return None
	try:
		user_id, _ = users_db.get_user(username=username)
		return user_id
	except Exception:
		return None


def normalize_model_name(item_name: str) -> str:
	"""Normalize model identifiers for backend-agnostic comparisons."""
	return (item_name or "").replace("\\", "/").strip().lower()


def _normalized_record(record: dict) -> dict:
	folder = (record.get("folder") or "").strip()
	item_name = (record.get("item_name") or "").strip()
	return {
		"folder": folder,
		"item_name": item_name,
		"norm_item_name": normalize_model_name(item_name),
		"source_backend": (record.get("source_backend") or "unknown").strip().lower(),
		"granted_by_user_id": (record.get("granted_by_user_id") or "").strip(),
		"granted_by_role": (record.get("granted_by_role") or "").strip().lower(),
		"created_at": record.get("created_at") or 0,
	}


def get_effective_model_grants_for_user(
	role: str,
	perms: dict,
	username: str | None,
	users_db,
	shared_items_store_getter: Callable[[dict], object],
	users_db_config: dict,
) -> list[dict]:
	"""
	Return effective grants for a user after applying model isolation/S3 policy.
	"""
	user_id = get_user_id_for_username(users_db, username)
	if not user_id:
		return []

	store = shared_items_store_getter(users_db_config)
	try:
		rows = store.list_for_user(user_id)
	except Exception:
		rows = []

	can_s3 = user_can_access_s3(role, perms)
	normalized: list[dict] = []
	for row in rows:
		record = _normalized_record(row)
		if not record["folder"] or not record["item_name"]:
			continue
		if not can_s3 and record["source_backend"] == "s3":
			continue
		normalized.append(record)
	return normalized


def allowed_set_from_grants(grants: list[dict]) -> set[tuple[str, str]]:
	"""Convert grants into an exact-match set for prompt validation."""
	return {(g["folder"], g["item_name"]) for g in grants}


def filter_items_by_grants(folder: str, item_names: list[str], grants: list[dict]) -> list[str]:
	"""Return only the items granted for a folder."""
	folder_norm = (folder or "").strip()
	if not folder_norm:
		return []
	allowed = {
		g["norm_item_name"]
		for g in grants
		if g.get("folder") == folder_norm and g.get("norm_item_name")
	}
	if not allowed:
		return []
	return [item for item in item_names if normalize_model_name(item) in allowed]
