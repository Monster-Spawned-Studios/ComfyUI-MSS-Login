# --- START OF FILE utils/user_isolation.py ---
"""Helpers for per-user output/input/workflow isolation.

Kept free of ComfyUI imports so CI can cover the path and queue-item logic
used by access_control and jwt_auth.
"""

from __future__ import annotations


def safe_user_dir_segment(user_id: str | None) -> str:
	"""Return a single path segment for a per-user output/input/temp folder."""
	raw = (user_id or "").strip() or "public"
	if ".." in raw or "/" in raw or "\\" in raw:
		return "public"
	return raw


def is_prompt_execution_path(path: str) -> bool:
	"""True for ComfyUI prompt submit paths used by the frontend and Comfy Portal."""
	if not path:
		return False
	normalized = path.rstrip("/") or "/"
	return normalized in ("/prompt", "/api/prompt")


def _queue_item_meta(item) -> dict:
	if isinstance(item, tuple) and item and isinstance(item[-1], dict):
		return item[-1]
	return {}


def user_id_from_queue_item(item) -> str | None:
	"""Read the MSS-Login user_id stamp from a patched PromptQueue entry."""
	uid = _queue_item_meta(item).get("user_id")
	if uid:
		return str(uid)
	return None


def username_from_queue_item(item) -> str | None:
	"""Read the MSS-Login username stamp from a patched PromptQueue entry."""
	name = _queue_item_meta(item).get("username")
	if name:
		return str(name)
	return None


# --- END OF FILE utils/user_isolation.py ---
