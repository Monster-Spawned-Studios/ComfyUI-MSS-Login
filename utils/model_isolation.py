"""
Helpers for experimental per-user model isolation paths.
"""

from __future__ import annotations

import os
import sys

try:
	from ..constants import (
		DATA_DIR,
		experimental_model_isolation_enabled,
	)
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
		from constants import (
			DATA_DIR,
			experimental_model_isolation_enabled,
		)
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


def _get_users_db():
	for mod_name in ("..globals", "globals"):
		try:
			mod = __import__(mod_name, fromlist=["users_db"])
			if hasattr(mod, "users_db"):
				return mod.users_db
		except Exception:
			pass
	for mod_name in ("..utils.access_control", "utils.access_control"):
		try:
			mod = __import__(mod_name, fromlist=["access_control"])
			if hasattr(mod, "access_control") and hasattr(mod.access_control, "users_db"):
				return mod.access_control.users_db
		except Exception:
			pass
	for mod_name in ("mss_login.globals", "globals"):
		mod = sys.modules.get(mod_name)
		if mod and hasattr(mod, "users_db"):
			return mod.users_db
	return None


def resolve_user_identity(
	user_id: str | None, username: str | None = None
) -> tuple[str | None, str | None]:
	"""Return (user_id, username) tuple by querying users_db when either value is missing."""
	uid = (user_id or "").strip() or None
	uname = (username or "").strip() or None
	if uid and uname:
		return uid, uname

	udb = _get_users_db()
	if udb is not None:
		try:
			if uid and not uname:
				# Check if uid is actually a username
				try:
					resolved_id, user_record = udb.get_user(username=uid)
					if resolved_id and user_record:
						return str(resolved_id).strip(), uid
				except Exception:
					pass
				# Look up by user_id
				try:
					_u, user_record = udb.get_user(user_id=uid)
					if user_record and user_record.get("username"):
						uname = str(user_record["username"]).strip() or None
				except Exception:
					try:
						res = udb.get_user(uid)
						if isinstance(res, tuple) and len(res) >= 2 and isinstance(res[1], dict):
							uname = str(res[1].get("username") or "").strip() or None
					except Exception:
						pass
			elif uname and not uid:
				try:
					resolved_id, _record = udb.get_user(username=uname)
					if resolved_id:
						uid = str(resolved_id).strip() or None
				except Exception:
					try:
						res = udb.get_user(uname)
						if isinstance(res, tuple) and len(res) >= 1 and res[0]:
							uid = str(res[0]).strip() or None
					except Exception:
						pass
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


def _is_isolation_enabled() -> bool:
	for mod_name in ("mss_login.constants", "constants"):
		mod = sys.modules.get(mod_name)
		if mod and hasattr(mod, "experimental_model_isolation_enabled"):
			try:
				return bool(mod.experimental_model_isolation_enabled())
			except Exception:
				pass
	try:
		return bool(experimental_model_isolation_enabled())
	except Exception:
		return False


def convert_manual_user_folders_to_isolation() -> int:
	"""
	Automatically detect manually created user folders on the host OS
	(under <models_root>/<folder_type>/<username>, DATA_DIR/users/<username>/models,
	or legacy DATA_DIR/model_isolation/models/<folder_type>/<uuid>)
	and convert/migrate them into proper model isolation folders
	(DATA_DIR/model_isolation/models/<folder_type>/<username>) with shared item grants.
	Returns count of converted/granted files.
	"""
	if not _is_isolation_enabled():
		return 0

	udb = _get_users_db()
	if udb is None:
		return 0

	try:
		if hasattr(udb, "load_users"):
			udb.load_users()
	except Exception:
		pass

	users = getattr(udb, "users", {})
	if not users or not isinstance(users, dict):
		return 0

	user_map: dict[str, str] = {}  # username -> user_id
	uid_map: dict[str, str] = {}  # user_id -> username
	for uid, data in users.items():
		if isinstance(data, dict):
			uname = (data.get("username") or "").strip()
			if uname:
				user_map[uname] = str(uid)
				uid_map[str(uid)] = uname

	if not user_map:
		return 0

	converted_count = 0

	# 1. Check ComfyUI shared models root for <models_root>/<folder_type>/<username>
	try:
		models_root = None
		for mod_name in (
			"mss_login.utils.folder_paths_compat",
			"utils.folder_paths_compat",
		):
			mod = sys.modules.get(mod_name)
			if mod and hasattr(mod, "get_models_root"):
				models_root = mod.get_models_root()
				break
		if not models_root:
			from .folder_paths_compat import get_models_root

			models_root = get_models_root()
		if models_root and os.path.isdir(models_root):
			for ft in os.listdir(models_root):
				ft_path = os.path.join(models_root, ft)
				if not os.path.isdir(ft_path):
					continue
				for uname, uid in user_map.items():
					user_cand_dir = os.path.join(ft_path, uname)
					if os.path.isdir(user_cand_dir):
						target_iso = isolation_user_folder(ft, uid, uname)
						_migrate_folder_contents(user_cand_dir, target_iso)
						try:
							os.rmdir(user_cand_dir)
						except OSError:
							pass
						ensure_isolation_folder_registered(ft)
	except Exception:
		pass

	# 2. Check DATA_DIR/users/<username>/models/<folder_type>
	users_root = os.path.join(DATA_DIR, "users")
	if os.path.isdir(users_root):
		for uname, uid in user_map.items():
			user_models = os.path.join(users_root, uname, "models")
			if os.path.isdir(user_models):
				try:
					for ft in os.listdir(user_models):
						ft_dir = os.path.join(user_models, ft)
						if os.path.isdir(ft_dir):
							target_iso = isolation_user_folder(ft, uid, uname)
							_migrate_folder_contents(ft_dir, target_iso)
							try:
								os.rmdir(ft_dir)
							except OSError:
								pass
							ensure_isolation_folder_registered(ft)
					try:
						os.rmdir(user_models)
					except OSError:
						pass
				except OSError:
					pass

	# 3. Check legacy UUID folders in DATA_DIR/model_isolation/models/<folder_type>/<uuid>
	base = isolation_models_base()
	if os.path.isdir(base):
		try:
			for ft in os.listdir(base):
				ft_dir = os.path.join(base, ft)
				if not os.path.isdir(ft_dir):
					continue
				for uid, uname in uid_map.items():
					legacy_uid_dir = os.path.join(ft_dir, uid)
					if os.path.isdir(legacy_uid_dir):
						target_iso = isolation_user_folder(ft, uid, uname)
						if os.path.abspath(legacy_uid_dir) != os.path.abspath(target_iso):
							_migrate_folder_contents(legacy_uid_dir, target_iso)
							try:
								os.rmdir(legacy_uid_dir)
							except OSError:
								pass
		except OSError:
			pass

	# 4. Auto-grant all files found in isolation folders for each user
	try:
		from .shared_items_store import get_shared_items_store

		try:
			from ..constants import USERS_DB_CONFIG
		except ImportError:
			from constants import USERS_DB_CONFIG

		shared_store = get_shared_items_store(USERS_DB_CONFIG)
		if shared_store is not None and os.path.isdir(base):
			for ft in os.listdir(base):
				ft_dir = os.path.join(base, ft)
				if not os.path.isdir(ft_dir):
					continue
				for uname, uid in user_map.items():
					u_dir = os.path.join(ft_dir, uname)
					if not os.path.isdir(u_dir):
						continue
					for dirpath, _dirnames, filenames in os.walk(u_dir):
						for fn in filenames:
							if not fn or fn.startswith("."):
								continue
							full_path = os.path.join(dirpath, fn)
							if not os.path.isfile(full_path):
								continue
							rel = os.path.relpath(full_path, u_dir).replace("\\", "/")
							item_name = f"{uname}/{rel}"
							try:
								shared_store.add(
									uid,
									ft,
									item_name,
									source_backend="local",
									granted_by_user_id=uid,
									granted_by_role="isolation_manual",
								)
								converted_count += 1
							except Exception:
								pass
	except Exception:
		pass

	return converted_count


def register_all_isolation_folders() -> list[str]:
	"""
	Register isolation roots for existing on-disk folder types plus common asset folders.

	Must run at startup (before model cache refresh) so existing UUID trees appear in
	folder_paths / object_info / Model Library without requiring a new download.
	"""
	if not experimental_model_isolation_enabled():
		return []
	try:
		convert_manual_user_folders_to_isolation()
	except Exception:
		pass
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


def list_isolation_items_for_user(
	user_id: str | None, username: str | None = None
) -> list[dict]:
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
