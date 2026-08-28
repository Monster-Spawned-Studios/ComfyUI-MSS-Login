# --- START OF FILE utils/cpe_workflows.py ---
"""Comfy Portal Endpoint compatible workflow list/get/save, scoped per user.

Response shapes match https://github.com/ShunL12324/comfy-portal-endpoint
(`GET /cpe/workflow/list`, `GET /cpe/workflow/get`, `POST /cpe/workflow/save`,
`GET /cpe/workflow/get-and-convert`). File I/O is confined to the caller's
workflow directory (and optional extra shared dirs for reads).
"""

from __future__ import annotations

import json
import os
import time
from collections.abc import Iterable
from typing import Any

from .path_safety import is_safe_relative_path, path_under, resolve_path_under

CPE_LIST_PATHS = ("/api/cpe/workflow/list", "/cpe/workflow/list")
CPE_GET_PATHS = ("/api/cpe/workflow/get", "/cpe/workflow/get")
CPE_SAVE_PATHS = ("/api/cpe/workflow/save", "/cpe/workflow/save")
CPE_CONVERT_PATHS = ("/api/cpe/workflow/convert", "/cpe/workflow/convert")
CPE_GET_AND_CONVERT_PATHS = ("/api/cpe/workflow/get-and-convert", "/cpe/workflow/get-and-convert")
CPE_HEALTH_PATHS = ("/api/cpe/health", "/cpe/health")


def is_cpe_path(path: str) -> bool:
	"""True if path is a Comfy Portal Endpoint route (with or without /api prefix)."""
	if not path:
		return False
	if path in CPE_HEALTH_PATHS:
		return True
	for prefix in ("/api/cpe/", "/cpe/"):
		if path == prefix.rstrip("/") or path.startswith(prefix):
			return True
	return False


def is_cpe_workflow_mutating_path(path: str) -> bool:
	"""True for CPE workflow write endpoints (save)."""
	return path in CPE_SAVE_PATHS


def sanitize_cpe_filename(name: str | None, *, default: str | None = None) -> str | None:
	"""Normalize a CPE filename to a safe relative ``.json`` path."""
	raw = (name or "").strip() or (default or "")
	if not raw:
		return None
	normalized = raw.replace("\\", "/").strip()
	if (
		os.path.isabs(raw)
		or os.path.isabs(normalized)
		or (len(normalized) >= 2 and normalized[1] == ":")
	):
		return None
	clean = normalized.lstrip("/")
	if not clean:
		return None
	if not clean.lower().endswith(".json"):
		clean += ".json"
	if not is_safe_relative_path(clean):
		return None
	return clean


def collect_workflow_entries(
	user_workflow_dir: str, extra_dirs: Iterable[str] | None = None
) -> list[dict[str, Any]]:
	"""Return CPE list entries from extra (shared) dirs then the user's dir.

	User files override shared files with the same relative filename.
	"""
	files_map: dict[str, dict[str, Any]] = {}
	for root_dir in list(extra_dirs or []) + [user_workflow_dir]:
		if not root_dir or not os.path.isdir(root_dir):
			continue
		for dirpath, _, filenames in os.walk(root_dir):
			for filename in filenames:
				if not filename.lower().endswith(".json"):
					continue
				full_path = os.path.join(dirpath, filename)
				rel = os.path.relpath(full_path, root_dir).replace("\\", "/")
				if not is_safe_relative_path(rel):
					continue
				try:
					st = os.stat(full_path)
					size = int(st.st_size)
					modified = float(st.st_mtime)
				except OSError:
					size = 0
					modified = time.time()
				files_map[rel] = {"filename": rel, "size": size, "modified": modified}
	return [files_map[key] for key in sorted(files_map)]


def list_workflows_payload(
	user_workflow_dir: str, extra_dirs: Iterable[str] | None = None
) -> dict[str, Any]:
	"""CPE ``GET /cpe/workflow/list`` body."""
	return {
		"status": "success",
		"workflows": collect_workflow_entries(user_workflow_dir, extra_dirs),
	}


def _resolve_readable_workflow(
	user_workflow_dir: str, filename: str, extra_dirs: Iterable[str] | None = None
) -> str | None:
	clean = sanitize_cpe_filename(filename)
	if not clean:
		return None
	search_dirs = [user_workflow_dir] + list(extra_dirs or [])
	for root_dir in search_dirs:
		if not root_dir or not os.path.isdir(root_dir):
			continue
		resolved = resolve_path_under(root_dir, clean)
		if resolved and os.path.isfile(resolved):
			return resolved
	return None


def read_workflow_text(
	user_workflow_dir: str, filename: str, extra_dirs: Iterable[str] | None = None
) -> tuple[int, dict[str, Any]]:
	"""CPE ``GET /cpe/workflow/get`` status and body."""
	clean = sanitize_cpe_filename(filename)
	if not clean:
		return 400, {"status": "error", "message": "filename query parameter is required"}
	path = _resolve_readable_workflow(user_workflow_dir, clean, extra_dirs)
	if path is None:
		return 404, {"status": "error", "message": f"Workflow file not found: {clean}"}
	try:
		with open(path, encoding="utf-8") as handle:
			content = handle.read()
	except OSError as exc:
		return 500, {"status": "error", "message": "Internal server error", "details": str(exc)}
	return 200, {"status": "success", "filename": clean, "workflow": content}


def save_workflow_text(
	user_workflow_dir: str, workflow_str: str, name: str | None = None
) -> tuple[int, dict[str, Any]]:
	"""CPE ``POST /cpe/workflow/save`` status and body. Writes only into the user dir."""
	if workflow_str is None or not isinstance(workflow_str, str) or not workflow_str.strip():
		return 400, {"status": "error", "message": "workflow field is required"}
	try:
		json.loads(workflow_str)
	except json.JSONDecodeError:
		return 400, {"status": "error", "message": "Invalid JSON in workflow field"}
	clean = sanitize_cpe_filename(name, default=f"workflow_{int(time.time())}.json")
	if not clean:
		return 400, {"status": "error", "message": "Invalid filename"}
	if not user_workflow_dir:
		return 500, {
			"status": "error",
			"message": "Internal server error",
			"details": "missing user dir",
		}
	os.makedirs(user_workflow_dir, exist_ok=True)
	target = os.path.normpath(os.path.join(user_workflow_dir, clean.replace("/", os.sep)))
	if not path_under(target, user_workflow_dir):
		return 400, {"status": "error", "message": "Invalid filename path"}
	parent = os.path.dirname(target)
	os.makedirs(parent, exist_ok=True)
	try:
		with open(target, "w", encoding="utf-8") as handle:
			handle.write(workflow_str)
	except OSError as exc:
		return 500, {"status": "error", "message": "Internal server error", "details": str(exc)}
	return 200, {"status": "success", "message": "Workflow saved successfully", "filename": clean}


def looks_like_api_prompt(data: Any) -> bool:
	"""True when *data* looks like a ComfyUI API prompt (nodes keyed by id with class_type)."""
	if not isinstance(data, dict):
		return False
	if "nodes" in data and "links" in data:
		return False
	node_like = [value for value in data.values() if isinstance(value, dict)]
	if not node_like:
		return False
	return any("class_type" in node for node in node_like)


def get_and_convert_payload(
	user_workflow_dir: str, filename: str, extra_dirs: Iterable[str] | None = None
) -> tuple[int, dict[str, Any]]:
	"""Read a user workflow and return it as API format when already converted.

	UI-format graphs need comfy-portal-endpoint's headless browser; those return 503.
	"""
	status, body = read_workflow_text(user_workflow_dir, filename, extra_dirs)
	if status != 200:
		return status, body
	filename_out = body.get("filename") or filename
	try:
		workflow_data = json.loads(body.get("workflow") or "")
	except json.JSONDecodeError:
		return 400, {"status": "error", "message": "Invalid JSON in workflow file"}
	if not workflow_data:
		return 400, {
			"status": "error",
			"message": "Workflow file contains no data or is an empty JSON object",
		}
	if not looks_like_api_prompt(workflow_data):
		return 503, {
			"status": "error",
			"message": "Workflow conversion failed",
			"details": (
				"This workflow is in UI format. Install comfy-portal-endpoint for "
				"headless conversion, or save an API-format workflow."
			),
		}
	return 200, {
		"status": "success",
		"message": "Workflow converted successfully",
		"filename": filename_out,
		"data": {"workflow": workflow_data},
	}


def health_payload() -> dict[str, Any]:
	"""CPE ``GET /cpe/health`` body. List/get/save are served by MSS-Login."""
	return {"status": "success", "browser": {"status": "ready"}}


# --- END OF FILE utils/cpe_workflows.py ---
