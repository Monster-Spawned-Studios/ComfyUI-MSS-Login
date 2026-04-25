"""
Path traversal prevention utilities.

Centralized helpers to validate and resolve paths so that user-controlled
input cannot escape intended base directories. Use these whenever building
file paths from request data (query, body, path params).
"""

import os
from pathlib import Path


def is_safe_folder_segment(folder: str) -> bool:
	"""
	Return True if folder is a single path segment safe for use in paths.

	Rejects empty, "..", absolute paths, and any string containing path
	separators (/, \\).
	"""
	if not folder or not isinstance(folder, str):
		return False
	folder = folder.strip()
	if not folder:
		return False
	if ".." in folder or "/" in folder or "\\" in folder:
		return False
	if folder in (".", "..") or os.path.isabs(folder):
		return False
	return True


def is_safe_filename(name: str) -> bool:
	"""
	Return True if name is a safe filename (no path components, no traversal).

	Use for single-file names (e.g. workflow name, image filename).
	Rejects empty, "..", and any string containing path separators.
	"""
	if not name or not isinstance(name, str):
		return False
	name = name.strip()
	if not name:
		return False
	if ".." in name or "/" in name or "\\" in name:
		return False
	return True


def safe_basename(name: str) -> str | None:
	"""
	Return the basename of name if it would be safe; otherwise None.

	Use when you must accept a string that might contain path segments
	but you only want to use the final component (e.g. Content-Disposition
	filename). Strips path and returns the last segment only if it passes
	is_safe_filename.
	"""
	if not name or not isinstance(name, str):
		return None
	base = os.path.basename(name.replace("\\", "/").strip())
	if not base or ".." in base:
		return None
	return base


def resolve_path_under(base_dir: str, rel_path: str) -> str | None:
	"""
	Resolve rel_path against base_dir and return the real path only if
	the result lies under base_dir (or equals it). Otherwise return None.

	Use for any user-supplied relative path before opening files.
	"""
	if not base_dir or not isinstance(base_dir, str):
		return None
	if rel_path is None:
		rel_path = ""
	if not isinstance(rel_path, str):
		return None
	rel_path = rel_path.replace("\\", "/").strip()
	if ".." in rel_path or rel_path.startswith("/"):
		return None
	try:
		base_real = os.path.realpath(base_dir)
		if not os.path.isdir(base_real):
			return None
		combined = os.path.normpath(os.path.join(base_dir, rel_path))
		resolved = os.path.realpath(combined)
		if not resolved.startswith(base_real + os.sep) and resolved != base_real:
			return None
		return resolved
	except (OSError, ValueError):
		return None


def path_under(base_path: str, base_dir: str) -> bool:
	"""
	Return True if base_path (already resolved) is under base_dir or equals it.

	Both arguments should be real/resolved paths. Use after os.path.realpath
	for defense in depth.
	"""
	if not base_path or not base_dir:
		return False
	try:
		base_real = os.path.realpath(base_dir)
		path_real = os.path.realpath(base_path)
		return path_real == base_real or path_real.startswith(base_real + os.sep)
	except (OSError, ValueError):
		return False
