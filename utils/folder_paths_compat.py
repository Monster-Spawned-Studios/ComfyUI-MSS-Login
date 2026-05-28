"""
Version-agnostic helpers for ComfyUI folder_paths model directory resolution.

ComfyUI exposes models_dir; older forks may use models_path. Callers should use
get_models_root() instead of importing either name directly.
"""

from __future__ import annotations

import os

from .model_isolation import maybe_isolated_destination


def get_models_root() -> str:
	"""Return absolute ComfyUI models root, or empty string when unavailable."""
	try:
		import folder_paths  # pyright: ignore[reportMissingImports]

		root = getattr(folder_paths, "models_dir", None) or getattr(
			folder_paths, "models_path", None
		)
		if root:
			return os.path.abspath(str(root))
	except Exception:
		pass
	return ""


def resolve_local_model_destination(folder_type: str, target_user_id: str) -> tuple[str, str]:
	"""
	Resolve a local download destination under ComfyUI models.

	Returns (dest_dir, base_dir) where base_dir is used for path containment checks.
	Raises RuntimeError when the models root is unavailable or dest escapes base_dir.
	"""
	isolated_dest = maybe_isolated_destination(folder_type, target_user_id)
	if isolated_dest is not None:
		dest_dir = isolated_dest
		base_dir = os.path.realpath(os.path.join(isolated_dest, "..", "..", ".."))
	else:
		models_root = get_models_root()
		if not models_root:
			raise RuntimeError("ComfyUI models directory unavailable")

		try:
			import folder_paths  # pyright: ignore[reportMissingImports]

			paths = folder_paths.get_folder_paths(folder_type)
		except Exception:
			paths = []

		if paths:
			dest_dir = paths[0]
		else:
			dest_dir = os.path.join(models_root, folder_type)
		base_dir = os.path.realpath(models_root)

	resolved_dest = os.path.realpath(dest_dir)
	if not (resolved_dest == base_dir or resolved_dest.startswith(base_dir + os.sep)):
		raise RuntimeError("Destination path escapes allowed directory")
	return dest_dir, base_dir
