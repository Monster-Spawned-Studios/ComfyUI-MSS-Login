"""
Helpers for experimental per-user model isolation paths.
"""

from __future__ import annotations

import errno
import json
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


_RESERVED_NAMES = frozenset(
	{
		"con",
		"prn",
		"aux",
		"nul",
		*(f"com{i}" for i in range(1, 10)),
		*(f"lpt{i}" for i in range(1, 10)),
	}
)


def validate_username_for_isolation(username: str | None) -> str:
	"""
	Strictly validate a username for model isolation folders.

	Rejects unsafe names instead of normalizing to avoid silent collisions:
	- empty or whitespace
	- path separators ('/', '\\', '..')
	- NUL and control characters
	- leading dot ('.')
	- Windows/POSIX reserved names ('CON', 'NUL', 'PRN', etc.)
	- over-long names (> 64 chars)
	- disallowed special characters
	"""
	raw = username or ""
	if any(c.isspace() for c in raw):
		raise ValueError("Username cannot contain whitespace")
	if not raw:
		raise ValueError("Username cannot be empty")
	if len(raw) > 64:
		raise ValueError(f"Username is too long ({len(raw)} > 64 chars)")
	if any(ord(c) < 32 or ord(c) == 127 for c in raw):
		raise ValueError("Username cannot contain control or NUL characters")
	if "/" in raw or "\\" in raw or ".." in raw:
		raise ValueError("Username cannot contain path characters ('/', '\\', '..')")
	if raw.startswith("."):
		raise ValueError("Username cannot start with a dot")
	if raw.endswith("."):
		raise ValueError("Username cannot end with a dot")
	if raw.lower() in _RESERVED_NAMES:
		raise ValueError(f"Username '{raw}' is a reserved system name")
	for c in raw:
		if not (c.isalnum() or c in ("_", "-", ".")):
			raise ValueError(f"Username contains disallowed character: {c!r}")
	return raw


def sanitize_user_segment(user_id: str | None) -> str:
	raw = (user_id or "").strip()
	if not raw:
		return "public"
	try:
		return validate_username_for_isolation(raw)
	except ValueError:
		safe = "".join(c if c.isalnum() or c in ("_", "-") else "_" for c in raw)
		return safe.strip("._-") or "public"


def isolation_models_base() -> str:
	return os.path.join(DATA_DIR, "model_isolation", "models")


def isolation_folder_root(folder_type: str) -> str:
	return os.path.join(isolation_models_base(), folder_type)


def uuid_folder_mapping_path() -> str:
	return os.path.join(DATA_DIR, "model_isolation", "uuid_folder_map.json")


def load_uuid_folder_mapping(path: str | None = None) -> dict[str, str]:
	target_path = path or uuid_folder_mapping_path()
	if not os.path.isfile(target_path):
		return {}
	try:
		with open(target_path, "r", encoding="utf-8") as f:
			data = json.load(f)
			if isinstance(data, dict):
				return {str(k): str(v) for k, v in data.items()}
	except Exception:
		pass
	return {}


def save_uuid_folder_mapping(mapping: dict[str, str], path: str | None = None) -> None:
	target_path = path or uuid_folder_mapping_path()
	os.makedirs(os.path.dirname(target_path), exist_ok=True)
	# Check for case collisions across mapped names
	lower_map: dict[str, str] = {}
	for uid, name in mapping.items():
		norm = name.lower()
		if norm in lower_map and lower_map[norm] != name:
			raise ValueError(
				f"Case collision detected in mapping: '{name}' collides with '{lower_map[norm]}'"
			)
		lower_map[norm] = name
	tmp = f"{target_path}.tmp.{os.getpid()}"
	with open(tmp, "w", encoding="utf-8") as f:
		json.dump(mapping, f, indent=2)
	os.replace(tmp, target_path)


def copy_ownership_and_mode(target: str, reference_parent: str) -> None:
	"""Apply reference parent directory's uid:gid and mode to target (prevent root-owned leftovers)."""
	try:
		st = os.stat(reference_parent)
		try:
			os.chmod(target, st.st_mode & 0o777)
		except OSError:
			pass
		if hasattr(os, "chown") and hasattr(os, "geteuid") and os.geteuid() == 0:
			try:
				os.chown(target, st.st_uid, st.st_gid)
			except (OSError, PermissionError):
				pass
	except OSError:
		pass


def is_safe_contained_path(base_root: str, candidate_path: str) -> bool:
	"""Return True if candidate_path is strictly within base_root after realpath resolution."""
	if not base_root or not candidate_path:
		return False
	try:
		real_root = os.path.realpath(base_root)
		real_cand = os.path.realpath(candidate_path)
		return real_cand == real_root or real_cand.startswith(real_root + os.sep)
	except OSError:
		return False


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


def get_folder_for_user(user_id: str | None, username: str | None = None) -> str:
	"""Return the authoritative folder name for this user from persisted mapping or username."""
	uid, uname = resolve_user_identity(user_id, username)
	mapping = load_uuid_folder_mapping()
	if uid and uid in mapping:
		return mapping[uid]
	target_uname = uname or uid or "public"
	try:
		validated = validate_username_for_isolation(target_uname)
	except ValueError:
		validated = sanitize_user_segment(target_uname)
	if uid and validated:
		mapping[uid] = validated
		try:
			save_uuid_folder_mapping(mapping)
		except Exception:
			pass
	return validated


def rename_user_isolation_folders(
	old_name: str,
	new_name: str,
	user_id: str | None = None,
	models_root: str | None = None,
	folder_types: list[str] | None = None,
	mapping_path: str | None = None,
) -> bool:
	"""
	Atomically rename a user's isolation folders across all folder_types using a journal.
	Rolls back completed moves on partial failure. Refuses if any target exists.
	Aborts on EXDEV (never falls back to copy+delete).
	Preserves parent directory ownership and mode.
	"""
	validated_new = validate_username_for_isolation(new_name)
	if old_name == validated_new:
		return True
	base = models_root or isolation_models_base()
	if not os.path.isdir(base):
		return True

	if folder_types is None:
		folder_types = []
		for name in os.listdir(base):
			p = os.path.join(base, name)
			if os.path.isdir(p) and not name.startswith("."):
				folder_types.append(name)

	# 1. Refuse if ANY target folder already exists
	for ft in folder_types:
		target_dir = os.path.join(base, ft, validated_new)
		if os.path.exists(target_dir):
			raise FileExistsError(f"Target isolation folder already exists for {ft}: {target_dir}")

	# 2. Plan journal
	journal_dir = base if models_root else os.path.join(DATA_DIR, "model_isolation")
	journal_path = os.path.join(journal_dir, ".rename_journal.json")
	os.makedirs(os.path.dirname(journal_path), exist_ok=True)
	steps: list[dict] = []
	for ft in folder_types:
		src = os.path.join(base, ft, old_name)
		dst = os.path.join(base, ft, validated_new)
		if os.path.exists(src):
			steps.append({"folder_type": ft, "src": src, "dst": dst, "status": "pending"})

	if not steps:
		return True

	journal = {"old_name": old_name, "new_name": validated_new, "user_id": user_id, "steps": steps}
	with open(journal_path, "w", encoding="utf-8") as f:
		json.dump(journal, f, indent=2)

	# 3. Apply moves with rollback on error
	completed: list[dict] = []
	try:
		for step in steps:
			src = step["src"]
			dst = step["dst"]
			parent = os.path.dirname(dst)
			os.replace(src, dst)
			copy_ownership_and_mode(dst, parent)
			step["status"] = "done"
			completed.append(step)
			with open(journal_path, "w", encoding="utf-8") as f:
				json.dump(journal, f, indent=2)
	except Exception as exc:
		# Roll back completed steps in reverse order
		for step in reversed(completed):
			try:
				os.replace(step["dst"], step["src"])
			except OSError:
				pass
		try:
			os.remove(journal_path)
		except OSError:
			pass
		raise exc

	# 4. Clean up journal and update mapping
	try:
		os.remove(journal_path)
	except OSError:
		pass

	if user_id:
		mapping = load_uuid_folder_mapping(mapping_path)
		mapping[user_id] = validated_new
		save_uuid_folder_mapping(mapping, mapping_path)

	# 5. Invalidate ComfyUI caches
	try:
		import folder_paths  # pyright: ignore[reportMissingImports]

		if hasattr(folder_paths, "filename_list_cache") and isinstance(
			folder_paths.filename_list_cache, dict
		):
			folder_paths.filename_list_cache.clear()
	except Exception:
		pass
	return True


def user_isolation_candidate_segments(
	user_id: str | None, username: str | None = None
) -> list[str]:
	"""Return ordered list of folder segments for user (primary active first, alternate second)."""
	uid, uname = resolve_user_identity(user_id, username)
	segments: list[str] = []
	# Primary is always the authoritative folder (username)
	primary = get_folder_for_user(uid, uname)
	if primary:
		segments.append(primary)
	if uname:
		seg = sanitize_user_segment(uname)
		if seg not in segments:
			segments.append(seg)
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
		for mod_name in ("mss_login.utils.folder_paths_compat", "utils.folder_paths_compat"):
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

	# 4. Count discovered files without adding grants (Hard Rule: folder placement is never a grant)
	if os.path.isdir(base):
		for ft in os.listdir(base):
			ft_dir = os.path.join(base, ft)
			if not os.path.isdir(ft_dir):
				continue
			for uname in user_map:
				u_dir = os.path.join(ft_dir, uname)
				if not os.path.isdir(u_dir):
					continue
				for dirpath, _dirnames, filenames in os.walk(u_dir):
					for fn in filenames:
						if not fn or fn.startswith("."):
							continue
						if os.path.isfile(os.path.join(dirpath, fn)):
							converted_count += 1

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
	parent = ensure_isolation_folder_registered(folder_type)
	maybe_migrate_user_isolation(folder_type, user_id, target_username)
	dest = isolation_user_folder(folder_type, user_id, target_username)
	os.makedirs(dest, exist_ok=True)
	if parent and os.path.isdir(parent):
		copy_ownership_and_mode(dest, parent)
	return dest
