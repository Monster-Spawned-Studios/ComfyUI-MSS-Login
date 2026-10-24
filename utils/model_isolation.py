"""
Helpers for experimental per-user model isolation paths.
"""

from __future__ import annotations

import os
import sys

try:
	from ..constants import DATA_DIR, experimental_model_isolation_enabled

	try:
		from ..constants import get_isolation_folder_naming
	except (ImportError, AttributeError):

		def get_isolation_folder_naming() -> str:
			for mod_name in ("mss_login.constants", "constants"):
				mod = sys.modules.get(mod_name)
				if mod and hasattr(mod, "get_isolation_folder_naming"):
					return mod.get_isolation_folder_naming()
			return "username"
except ImportError:
	try:
		from constants import DATA_DIR, experimental_model_isolation_enabled

		try:
			from constants import get_isolation_folder_naming
		except (ImportError, AttributeError):

			def get_isolation_folder_naming() -> str:
				return "username"
	except ImportError:
		DATA_DIR = os.path.expanduser("~/.comfyui-mss-login")

		def experimental_model_isolation_enabled() -> bool:
			return False

		def get_isolation_folder_naming() -> str:
			return "username"


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


def resolve_user_identity(
	user_id: str | None, username: str | None = None
) -> tuple[str | None, str | None]:
	"""Return (user_id, username) tuple by querying users_db when either value is missing."""
	uid = (user_id or "").strip() or None
	uname = (username or "").strip() or None
	if uid and uname:
		return uid, uname

	try:
		from .users_db import users_db

		if uid and not uname:
			_u, user_record = users_db.get_user(user_id=uid)
			if user_record and user_record.get("username"):
				uname = str(user_record["username"]).strip() or None
		elif uname and not uid:
			resolved_id, _record = users_db.get_user(username=uname)
			if resolved_id:
				uid = str(resolved_id).strip() or None
	except Exception:
		pass
	return uid, uname


def user_isolation_candidate_segments(
	user_id: str | None, username: str | None = None
) -> list[str]:
	"""Return ordered list of folder segments for user (primary active first, alternate second)."""
	uid, uname = resolve_user_identity(user_id, username)
	mode = get_isolation_folder_naming()
	segments: list[str] = []
	if mode == "user_id":
		if uid:
			segments.append(sanitize_user_segment(uid))
		if uname:
			seg = sanitize_user_segment(uname)
			if seg not in segments:
				segments.append(seg)
	else:
		# default: username
		if uname:
			segments.append(sanitize_user_segment(uname))
		if uid:
			seg = sanitize_user_segment(uid)
			if seg not in segments:
				segments.append(seg)
	if not segments:
		fallback = sanitize_user_segment(user_id or username or "public")
		segments.append(fallback)
	return segments


def _migrate_folder_contents(src: str, dst: str) -> None:
	"""Move contents of src into dst without overwriting newer files, then clean up src."""
	if not os.path.isdir(src):
		return
	os.makedirs(dst, exist_ok=True)
	try:
		for item in os.listdir(src):
			if not item or item in (".", ".."):
				continue
			s_item = os.path.join(src, item)
			d_item = os.path.join(dst, item)
			if os.path.isdir(s_item):
				_migrate_folder_contents(s_item, d_item)
				try:
					os.rmdir(s_item)
				except OSError:
					pass
			elif os.path.isfile(s_item):
				if not os.path.exists(d_item):
					try:
						os.replace(s_item, d_item)
					except OSError:
						pass
				else:
					try:
						if os.path.getmtime(d_item) >= os.path.getmtime(s_item):
							os.remove(s_item)
					except OSError:
						pass
		try:
			os.rmdir(src)
		except OSError:
			pass
	except OSError:
		pass


def maybe_migrate_user_isolation(
	folder_type: str, user_id: str | None, username: str | None = None
) -> None:
	"""Safely migrate legacy alternate user folder to the active convention if needed."""
	segments = user_isolation_candidate_segments(user_id, username)
	if len(segments) < 2:
		return
	primary_seg, alt_seg = segments[0], segments[1]
	if primary_seg == alt_seg:
		return
	root = isolation_folder_root(folder_type)
	primary_dir = os.path.join(root, primary_seg)
	alt_dir = os.path.join(root, alt_seg)

	if os.path.isdir(alt_dir) and not os.path.exists(primary_dir):
		try:
			os.rename(alt_dir, primary_dir)
		except OSError:
			_migrate_folder_contents(alt_dir, primary_dir)
	elif os.path.isdir(alt_dir) and os.path.isdir(primary_dir):
		_migrate_folder_contents(alt_dir, primary_dir)


def isolation_user_folder(
	folder_type: str, user_id: str | None, username: str | None = None
) -> str:
	"""Return the primary target isolation folder for this user under folder_type."""
	segments = user_isolation_candidate_segments(user_id, username)
	return os.path.join(isolation_folder_root(folder_type), segments[0])


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


def list_isolation_items_for_user(user_id: str | None, username: str | None = None) -> list[dict]:
	"""
	List storage-shaped model paths already on disk under this user's isolation folders.
	Implements bidirectional dual-read across both username and UUID directories.
	"""
	if not experimental_model_isolation_enabled() or (not user_id and not username):
		return []
	segments = user_isolation_candidate_segments(user_id, username)
	primary_seg = segments[0]
	base = isolation_models_base()
	if not os.path.isdir(base):
		return []
	out: list[dict] = []
	seen: set[tuple[str, str]] = set()

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
		root = isolation_folder_root(folder_type)
		for seg in segments:
			user_dir = os.path.join(root, seg)
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
						dedupe_key = (folder_type, rel.lower())
						if dedupe_key in seen:
							continue
						seen.add(dedupe_key)
						out.append(
							{
								"folder": folder_type,
								"item_name": f"{primary_seg}/{rel}",
								"source_backend": "local",
								"granted_by_user_id": "",
								"granted_by_role": "isolation_disk",
								"created_at": 0,
							}
						)
			except OSError:
				continue
	return out


def maybe_isolated_destination(
	folder_type: str, user_id: str | None, target_username: str | None = None
) -> str | None:
	"""Return destination directory for isolated storage, or None when feature is disabled."""
	if not experimental_model_isolation_enabled():
		return None
	ensure_isolation_folder_registered(folder_type)
	maybe_migrate_user_isolation(folder_type, user_id, target_username)
	dest = isolation_user_folder(folder_type, user_id, target_username)
	os.makedirs(dest, exist_ok=True)
	return dest
