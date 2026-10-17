"""
Helpers for experimental per-user model isolation paths.
"""

from __future__ import annotations

import os

from ..constants import DATA_DIR, experimental_model_isolation_enabled

# Common ComfyUI asset folder types registered at startup when isolation is on.
_COMMON_ISOLATION_FOLDERS = frozenset(
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


def sanitize_user_segment(user_id: str | None) -> str:
	raw = (user_id or "").strip()
	if not raw:
		return "public"
	safe = "".join(c if c.isalnum() or c in ("_", "-") else "_" for c in raw)
	return safe or "public"


def isolation_models_base() -> str:
	return os.path.join(DATA_DIR, "model_isolation", "models")


def isolation_folder_root(folder_type: str) -> str:
	return os.path.join(isolation_models_base(), folder_type)


def isolation_user_folder(folder_type: str, user_id: str | None) -> str:
	return os.path.join(isolation_folder_root(folder_type), sanitize_user_segment(user_id))


def ensure_isolation_folder_registered(folder_type: str) -> str | None:
	"""
	Register a shared folder root so ComfyUI resolves names as "<user_id>/<file>".
	Returns the folder root when enabled.
	"""
	if not experimental_model_isolation_enabled():
		return None
	folder = (folder_type or "").strip()
	if not folder or "/" in folder or "\\" in folder or folder in (".", ".."):
		return None
	root = isolation_folder_root(folder)
	os.makedirs(root, exist_ok=True)
	try:
		import folder_paths  # pyright: ignore[reportMissingImports]

		folder_paths.add_model_folder_path(folder, root)
	except Exception:
		pass
	return root


def register_all_isolation_folders() -> list[str]:
	"""
	Register isolation roots for existing on-disk folder types plus common asset folders.

	Must run at startup (before model cache refresh) so existing UUID trees appear in
	folder_paths / object_info / Model Library without requiring a new download.
	"""
	if not experimental_model_isolation_enabled():
		return []
	folder_types: set[str] = set(_COMMON_ISOLATION_FOLDERS)
	base = isolation_models_base()
	if os.path.isdir(base):
		try:
			for name in os.listdir(base):
				path = os.path.join(base, name)
				if not os.path.isdir(path):
					continue
				if not name or name in (".", "..") or "/" in name or "\\" in name:
					continue
				folder_types.add(name)
		except OSError:
			pass
	registered: list[str] = []
	for folder in sorted(folder_types):
		if ensure_isolation_folder_registered(folder) is not None:
			registered.append(folder)
	return registered


def list_isolation_items_for_user(user_id: str | None) -> list[dict]:
	"""
	List storage-shaped model paths already on disk under this user's isolation folders.

	Returns dicts compatible with shared_items rows:
	{folder, item_name, source_backend, granted_by_user_id, granted_by_role, created_at}
	where item_name is "{user_seg}/relative/path".
	"""
	if not experimental_model_isolation_enabled() or not user_id:
		return []
	user_seg = sanitize_user_segment(user_id)
	base = isolation_models_base()
	if not os.path.isdir(base):
		return []
	out: list[dict] = []
	try:
		folder_types = [
			name
			for name in os.listdir(base)
			if os.path.isdir(os.path.join(base, name))
			and name
			and name not in (".", "..")
			and "/" not in name
			and "\\" not in name
		]
	except OSError:
		return []
	for folder_type in folder_types:
		user_dir = isolation_user_folder(folder_type, user_id)
		if not os.path.isdir(user_dir):
			continue
		try:
			for dirpath, _dirnames, filenames in os.walk(user_dir):
				for fn in filenames:
					if not fn or fn.startswith("."):
						continue
					full = os.path.join(dirpath, fn)
					if not os.path.isfile(full):
						continue
					rel = os.path.relpath(full, user_dir).replace("\\", "/")
					if not rel or rel.startswith(".."):
						continue
					out.append(
						{
							"folder": folder_type,
							"item_name": f"{user_seg}/{rel}",
							"source_backend": "local",
							"granted_by_user_id": "",
							"granted_by_role": "isolation_disk",
							"created_at": 0,
						}
					)
		except OSError:
			continue
	return out


def maybe_isolated_destination(folder_type: str, user_id: str | None) -> str | None:
	"""Return destination directory for isolated storage, or None when feature is disabled."""
	if not experimental_model_isolation_enabled():
		return None
	ensure_isolation_folder_registered(folder_type)
	dest = isolation_user_folder(folder_type, user_id)
	os.makedirs(dest, exist_ok=True)
	return dest
