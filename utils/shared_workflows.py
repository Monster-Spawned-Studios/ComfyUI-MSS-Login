"""Shared guest workflow library: own + guest (+ optional legacy) with one precedence.

Precedence for both list and read: own > shared guest > legacy globals.
Shared dir is ``workflows/<shared_user>/`` (default guest), never ``workflows/default``.
"""

from __future__ import annotations

import json
import os
import time
from typing import Any, Callable

from .input_sanitizer import sanitize_username_for_path
from .path_safety import is_safe_relative_path, path_under, resolve_path_under

ORIGIN_OWN = "own"
ORIGIN_SHARED = "shared"
ORIGIN_LEGACY = "legacy"

SHARED_READONLY_BODY = {
	"status": "error",
	"code": "SHARED_READONLY",
	"message": "Shared workflows are read-only",
}

DOWNLOADER_WORKFLOW_FILENAMES = {
	"civitai_model_downloader.json",
	"huggingface_model_downloader.json",
	"universal_model_downloader.json",
}


def is_model_downloader_workflow(name: str | None) -> bool:
	"""Return True if filename matches a shipped model downloader workflow."""
	if not name:
		return False
	base = os.path.basename(str(name)).strip().lower()
	if base in DOWNLOADER_WORKFLOW_FILENAMES:
		return True
	return base.endswith("_model_downloader.json") or base == "model_downloader.json"


# Test overrides (None = use live config / env)
_settings_override: dict[str, Any] | None = None
_owner_username_override: str | None = None
_workflow_dir_override: Callable[[str], str | None] | None = None
_legacy_dirs_override: list[str] | None = None
_guest_enabled_override: bool | None = None
_user_can_download_override: Callable[[str], bool] | dict[str, bool] | bool | None = None
_shipped_dir_override: str | None = None


def reset_test_overrides() -> None:
	global _settings_override, _owner_username_override
	global _workflow_dir_override, _legacy_dirs_override, _guest_enabled_override
	global _user_can_download_override, _shipped_dir_override
	_settings_override = None
	_owner_username_override = None
	_workflow_dir_override = None
	_legacy_dirs_override = None
	_guest_enabled_override = None
	_user_can_download_override = None
	_shipped_dir_override = None


def set_test_overrides(
	*,
	settings: dict[str, Any] | None = None,
	owner_username: str | None = None,
	workflow_dir_fn: Callable[[str], str | None] | None = None,
	legacy_dirs: list[str] | None = None,
	guest_enabled: bool | None = None,
	can_download: Callable[[str], bool] | dict[str, bool] | bool | None = None,
	shipped_dir: str | None = None,
) -> None:
	global _settings_override, _owner_username_override
	global _workflow_dir_override, _legacy_dirs_override, _guest_enabled_override
	global _user_can_download_override, _shipped_dir_override
	if settings is not None:
		_settings_override = dict(settings)
	if owner_username is not None:
		_owner_username_override = owner_username
	if workflow_dir_fn is not None:
		_workflow_dir_override = workflow_dir_fn
	if legacy_dirs is not None:
		_legacy_dirs_override = list(legacy_dirs)
	if guest_enabled is not None:
		_guest_enabled_override = guest_enabled
	if can_download is not None:
		_user_can_download_override = can_download
	if shipped_dir is not None:
		_shipped_dir_override = shipped_dir


def _load_config_dict() -> dict[str, Any]:
	try:
		from .data_dir import get_data_dir

		path = os.path.join(get_data_dir(), "config.json")
		if os.path.isfile(path):
			with open(path, encoding="utf-8") as handle:
				data = json.load(handle)
				if isinstance(data, dict):
					return data
	except Exception:
		pass
	try:
		defaults_path = os.path.join(
			os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "config.defaults.json"
		)
		with open(defaults_path, encoding="utf-8") as handle:
			data = json.load(handle)
			if isinstance(data, dict):
				return data
	except Exception:
		pass
	return {}


def workflows_settings() -> dict[str, Any]:
	if _settings_override is not None:
		base = dict(_settings_override)
	else:
		cfg = _load_config_dict()
		base = dict(cfg.get("workflows") or {})
		cpe = cfg.get("cpe") if isinstance(cfg.get("cpe"), dict) else {}
		if "list_shared_metadata" not in base and "list_shared_metadata" in cpe:
			base["list_shared_metadata"] = cpe["list_shared_metadata"]
	return {
		"shared_user": str(base.get("shared_user") or "guest"),
		"shared_enabled": bool(base.get("shared_enabled", True)),
		"guest_can_write_shared": bool(base.get("guest_can_write_shared", False)),
		"include_legacy_globals": bool(base.get("include_legacy_globals", False)),
		"web_hide_api_format": bool(base.get("web_hide_api_format", True)),
		"list_shared_metadata": bool(base.get("list_shared_metadata", True)),
	}


def shared_user_name() -> str:
	return workflows_settings()["shared_user"]


def _shared_library_enabled() -> bool:
	"""Shared guest folder is independent of guest-JWT login."""
	if _guest_enabled_override is not None:
		return _guest_enabled_override
	return bool(workflows_settings().get("shared_enabled", True))


def _sanitize_username(username: str) -> str:
	return sanitize_username_for_path(username)


def workflow_dir_path(username: str, *, must_exist: bool = True) -> str | None:
	"""Return the on-disk workflow dir for *username* without creating it."""
	if _workflow_dir_override is not None:
		path = _workflow_dir_override(username)
		if not path:
			return None
		if must_exist and not os.path.isdir(path):
			return None
		return os.path.realpath(path) if os.path.isdir(path) else path

	safe = _sanitize_username(username)
	raw_env = os.environ.get("MSS_LOGIN_DATA_DIR", "").strip()
	if raw_env:
		target = os.path.join(os.path.abspath(raw_env), "workflows", safe)
	else:
		try:
			from .data_dir import get_data_subdir

			target = get_data_subdir("users", safe, "workflows", "default")
		except Exception:
			return None
	if must_exist and not os.path.isdir(target):
		return None
	try:
		if os.path.isdir(target):
			try:
				from .user_env import seed_default_workflows

				seed_default_workflows(target, username=username)
			except Exception:
				pass
		return os.path.realpath(target) if os.path.isdir(target) else target
	except OSError:
		return target if not must_exist else None


def shared_workflow_dir() -> str | None:
	"""Realpath of the shared guest workflow dir, or None if missing / disabled."""
	if not _shared_library_enabled():
		return None
	shared_dir = workflow_dir_path(shared_user_name(), must_exist=True)
	if not shared_dir and _workflow_dir_override is None:
		try:
			from .user_env import get_user_workflow_dir

			shared_dir = get_user_workflow_dir(shared_user_name())
			if shared_dir and os.path.isdir(shared_dir):
				return os.path.realpath(shared_dir)
		except Exception:
			pass
	return shared_dir


def potential_legacy_global_dirs() -> list[str]:
	"""ComfyUI / MSS legacy global workflow roots (may not exist)."""
	if _legacy_dirs_override is not None:
		return list(_legacy_dirs_override)
	dirs: list[str] = []
	try:
		import folder_paths

		comfy_root = getattr(folder_paths, "base_path", None) or ""
		if comfy_root:
			dirs.append(os.path.join(comfy_root, "user", "default", "workflows"))
			dirs.append(os.path.join(comfy_root, "user_data", "workflows"))
	except Exception:
		pass
	raw_env = os.environ.get("MSS_LOGIN_DATA_DIR", "").strip()
	if raw_env:
		dirs.append(os.path.join(os.path.abspath(raw_env), "workflows", "default"))
	return dirs


def legacy_global_dirs() -> list[str]:
	if not workflows_settings()["include_legacy_globals"]:
		return []
	out: list[str] = []
	for path in potential_legacy_global_dirs():
		if path and os.path.isdir(path):
			try:
				out.append(os.path.realpath(path))
			except OSError:
				continue
	return out


def get_owner_username() -> str | None:
	if _owner_username_override is not None:
		return _owner_username_override
	try:
		from ..globals import users_db

		name = users_db.get_owner_username()
		return str(name) if name else None
	except Exception:
		return None


def caller_is_owner(username: str) -> bool:
	owner = get_owner_username()
	if not owner or not username:
		return False
	return str(username).strip().lower() == str(owner).strip().lower()


def shipped_workflows_dir() -> str | None:
	"""Realpath of shipped workflows directory (e.g. extension root/workflows)."""
	if _shipped_dir_override is not None:
		return _shipped_dir_override if os.path.isdir(_shipped_dir_override) else None
	try:
		from .user_env import get_extension_root

		root = get_extension_root()
		wf_dir = os.path.join(root, "workflows")
		if os.path.isdir(wf_dir):
			return os.path.realpath(wf_dir)
	except Exception:
		pass
	return None


def user_has_download_permission(username: str | None) -> bool:
	"""Return True if username is allowed to view/load/run model download workflows."""
	if not username:
		return False
	clean_user = str(username).strip().lower()
	if clean_user in ("guest", ""):
		return False

	if _user_can_download_override is not None:
		if callable(_user_can_download_override):
			return bool(_user_can_download_override(clean_user))
		if isinstance(_user_can_download_override, dict):
			if clean_user in _user_can_download_override:
				return bool(_user_can_download_override[clean_user])
			if username in _user_can_download_override:
				return bool(_user_can_download_override[username])
		if isinstance(_user_can_download_override, bool):
			return _user_can_download_override

	if caller_is_owner(username):
		return True

	try:
		try:
			from ..globals import users_db
		except Exception:
			try:
				from globals import users_db
			except Exception:
				users_db = None

		rec = None
		if users_db and hasattr(users_db, "get_user"):
			_uid, u = users_db.get_user(username)
			rec = u

		if not rec:
			return False

		if rec.get("can_download_models") is True:
			return True

		groups = rec.get("groups") or rec.get("group") or []
		if isinstance(groups, str):
			groups = [groups]
		groups_clean = [str(g).strip().lower() for g in groups if g]
		is_admin = bool(rec.get("admin")) or "admin" in groups_clean or "owner" in groups_clean

		role = groups_clean[0] if groups_clean else ("admin" if is_admin else "user")
		if is_admin and role not in ("owner", "admin"):
			role = "admin"

		cfg = None
		try:
			from ..globals import access_control

			if access_control and hasattr(access_control, "_load_group_config"):
				cfg = access_control._load_group_config()
		except Exception:
			pass

		if not cfg:
			try:
				from ..constants import DEFAULT_GROUP_CONFIG_PATH, GROUPS_CONFIG_FILE
				from .json_utils import load_json_file

				if GROUPS_CONFIG_FILE and os.path.exists(GROUPS_CONFIG_FILE):
					cfg = load_json_file(GROUPS_CONFIG_FILE, None)
				if (
					not cfg
					and DEFAULT_GROUP_CONFIG_PATH
					and os.path.exists(DEFAULT_GROUP_CONFIG_PATH)
				):
					cfg = load_json_file(DEFAULT_GROUP_CONFIG_PATH, None)
			except Exception:
				pass

		perms = cfg.get(role, {}) if isinstance(cfg, dict) else {}
		try:
			from .model_visibility_policy import user_can_download_models
		except Exception:
			try:
				from utils.model_visibility_policy import user_can_download_models
			except Exception:

				def user_can_download_models(r: str, p: dict) -> bool:
					val = p.get("can_download_models")
					if val is None:
						return r in ("admin", "owner")
					return val is True

		return user_can_download_models(role, perms)
	except Exception:
		return False


def _is_archive_dir_name(name: str) -> bool:
	return bool(name) and name.startswith("_archive")


def _path_contained(abs_path: str, root: str) -> bool:
	try:
		return path_under(abs_path, root)
	except Exception:
		return False


def _safe_file_under_root(root: str, rel: str) -> str | None:
	if not root or not is_safe_relative_path(rel):
		return None
	resolved = resolve_path_under(root, rel)
	if not resolved or not os.path.isfile(resolved):
		return None
	if not _path_contained(resolved, root):
		return None
	# Symlink escape: realpath already applied in resolve_path_under
	return resolved


def detect_workflow_format(path: str) -> str:
	"""Return ``ui`` or ``api`` for a workflow JSON file."""
	try:
		with open(path, encoding="utf-8") as handle:
			data = json.load(handle)
	except Exception:
		return "ui"
	try:
		from .cpe_workflows import looks_like_api_prompt

		return "api" if looks_like_api_prompt(data) else "ui"
	except Exception:
		return "ui"


def sanitize_workflow_rel(name: str | None, *, default: str | None = None) -> str | None:
	"""Safe relative ``.json`` path (allows subfolders)."""
	from .cpe_workflows import sanitize_cpe_filename

	return sanitize_cpe_filename(name, default=default)


def _walk_json_files(root: str) -> list[tuple[str, str]]:
	"""Yield (rel, abs_path) for JSON files under root, skipping ``_archive*`` dirs."""
	results: list[tuple[str, str]] = []
	if not root or not os.path.isdir(root):
		return results
	try:
		root_real = os.path.realpath(root)
	except OSError:
		return results
	for dirpath, dirnames, filenames in os.walk(root_real):
		dirnames[:] = [d for d in dirnames if not _is_archive_dir_name(d)]
		# Also skip if current dir itself is archive (shouldn't happen after prune)
		base = os.path.basename(dirpath)
		if _is_archive_dir_name(base) and dirpath != root_real:
			dirnames[:] = []
			continue
		for filename in filenames:
			if not filename.lower().endswith(".json"):
				continue
			full = os.path.join(dirpath, filename)
			try:
				full_real = os.path.realpath(full)
			except OSError:
				continue
			if not _path_contained(full_real, root_real):
				continue
			if not os.path.isfile(full_real):
				continue
			rel = os.path.relpath(full_real, root_real).replace("\\", "/")
			if not is_safe_relative_path(rel):
				continue
			results.append((rel, full_real))
	return results


def _entry_for(
	rel: str, abs_path: str, origin: str, *, username: str, include_metadata: bool
) -> dict[str, Any]:
	try:
		st = os.stat(abs_path)
		size = int(st.st_size)
		modified = float(st.st_mtime)
	except OSError:
		size = 0
		modified = time.time()
	fmt = detect_workflow_format(abs_path)
	entry: dict[str, Any] = {"filename": rel, "size": size, "modified": modified}
	if include_metadata:
		is_shared_origin = origin in (ORIGIN_SHARED, ORIGIN_LEGACY)
		owner = caller_is_owner(username)
		entry["shared"] = is_shared_origin and origin == ORIGIN_SHARED
		entry["source"] = origin
		entry["readonly"] = bool(is_shared_origin and not owner)
		entry["format"] = fmt
	return entry


def search_roots(username: str) -> list[tuple[str, str]]:
	"""Ordered roots for resolve (own first): list of (origin, abs_dir)."""
	roots: list[tuple[str, str]] = []
	own = workflow_dir_path(username, must_exist=True)
	shared = shared_workflow_dir()
	own_real = own
	shared_real = shared
	if own_real:
		roots.append((ORIGIN_OWN, own_real))
	if shared_real and (not own_real or shared_real != own_real):
		roots.append((ORIGIN_SHARED, shared_real))
	elif shared_real and own_real and shared_real == own_real:
		# guest caller: own is shared — tagged as shared at collect time
		pass
	for legacy in legacy_global_dirs():
		if own_real and legacy == own_real:
			continue
		if shared_real and legacy == shared_real:
			continue
		roots.append((ORIGIN_LEGACY, legacy))
	shipped = shipped_workflows_dir()
	if shipped and shipped != own_real and shipped != shared_real:
		if shipped not in [r[1] for r in roots]:
			roots.append((ORIGIN_LEGACY, shipped))
	return roots


def resolve_workflow(username: str, rel: str) -> tuple[str, str] | None:
	"""Return ``(abs_path, origin)`` using own > shared > legacy precedence."""
	clean = sanitize_workflow_rel(rel)
	if not clean:
		return None
	if is_model_downloader_workflow(clean) and not user_has_download_permission(username):
		return None
	own = workflow_dir_path(username, must_exist=True)
	shared = shared_workflow_dir()
	# Guest: own == shared
	if own:
		path = _safe_file_under_root(own, clean)
		if path:
			origin = ORIGIN_SHARED if shared and own == shared else ORIGIN_OWN
			return path, origin
	if shared and (not own or shared != own):
		path = _safe_file_under_root(shared, clean)
		if path:
			return path, ORIGIN_SHARED
	for legacy in legacy_global_dirs():
		path = _safe_file_under_root(legacy, clean)
		if path:
			return path, ORIGIN_LEGACY
	shipped = shipped_workflows_dir()
	if shipped:
		path = _safe_file_under_root(shipped, clean)
		if path:
			return path, ORIGIN_LEGACY
	return None


def collect_entries(
	username: str, *, include_metadata: bool | None = None, hide_api_format: bool | None = None
) -> list[dict[str, Any]]:
	"""Merged workflow list with unified precedence (own wins)."""
	settings = workflows_settings()
	if include_metadata is None:
		include_metadata = bool(settings["list_shared_metadata"])
	if hide_api_format is None:
		hide_api_format = False

	# Build lowest→highest so later overwrites: shipped, legacy, shared, own
	layers: list[tuple[str, str]] = []
	shipped = shipped_workflows_dir()
	if shipped:
		layers.append((ORIGIN_LEGACY, shipped))
	for legacy in legacy_global_dirs():
		layers.append((ORIGIN_LEGACY, legacy))
	shared = shared_workflow_dir()
	own = workflow_dir_path(username, must_exist=True)
	if shared:
		layers.append((ORIGIN_SHARED, shared))
	if own and (not shared or own != shared):
		layers.append((ORIGIN_OWN, own))
	elif own and shared and own == shared:
		# guest: single layer as shared
		layers = [(o, d) for o, d in layers if d != own]
		layers.append((ORIGIN_SHARED, own))

	files_map: dict[str, dict[str, Any]] = {}
	seen_realpaths: dict[str, str] = {}
	for origin, root in layers:
		for rel, abs_path in _walk_json_files(root):
			try:
				rp = os.path.realpath(abs_path)
			except OSError:
				continue
			# Dedupe by realpath; higher layer overwrites key
			prev_rel = seen_realpaths.get(rp)
			if prev_rel and prev_rel != rel and prev_rel in files_map:
				# Same file via different rel — keep one key (prefer current rel)
				del files_map[prev_rel]
			seen_realpaths[rp] = rel
			files_map[rel] = _entry_for(
				rel, abs_path, origin, username=username, include_metadata=include_metadata
			)

	entries = [files_map[key] for key in sorted(files_map)]
	if hide_api_format:
		entries = [e for e in entries if e.get("format") != "api"]
	if not user_has_download_permission(username):
		entries = [e for e in entries if not is_model_downloader_workflow(e.get("filename"))]
	return entries


def list_payload_for_user(username: str) -> dict[str, Any]:
	"""CPE list envelope."""
	return {"status": "success", "workflows": collect_entries(username, hide_api_format=False)}


def can_write_shared(username: str, *, scope_shared: bool) -> tuple[bool, str | None]:
	"""Return (allowed, error_code). error_code is SHARED_READONLY when denied."""
	settings = workflows_settings()
	shared_name = shared_user_name()
	is_shared_user = str(username).strip().lower() == str(shared_name).strip().lower()

	if scope_shared:
		if caller_is_owner(username):
			return True, None
		return False, "SHARED_READONLY"

	# Writing into own dir that *is* the shared dir (guest)
	if is_shared_user:
		if settings["guest_can_write_shared"]:
			return True, None
		return False, "SHARED_READONLY"

	return True, None


def save_target_dir(username: str, *, scope_shared: bool) -> tuple[str | None, str | None]:
	"""Return (abs_dir, error_code). Creates own dir when writing privately."""
	allowed, err = can_write_shared(username, scope_shared=scope_shared)
	if not allowed:
		return None, err

	if scope_shared:
		shared = shared_workflow_dir()
		if not shared:
			# Owner may create shared dir? Plan: don't create for shared_workflow_dir()
			# but owner write with scope=shared needs a dir — create only for owner write.
			path = workflow_dir_path(shared_user_name(), must_exist=False)
			if not path:
				return None, "SHARED_READONLY"
			os.makedirs(path, exist_ok=True)
			return os.path.realpath(path), None
		return shared, None

	# Private own dir (may create). Honour test override first.
	if _workflow_dir_override is not None:
		path = _workflow_dir_override(username)
		if not path:
			return None, "SHARED_READONLY"
		os.makedirs(path, exist_ok=True)
		return os.path.realpath(path), None
	try:
		from . import user_env

		path = user_env.get_user_workflow_dir(username)
		return os.path.realpath(path), None
	except Exception:
		path = workflow_dir_path(username, must_exist=False)
		if not path:
			return None, "SHARED_READONLY"
		os.makedirs(path, exist_ok=True)
		return os.path.realpath(path), None


def can_delete_workflow(username: str, origin: str) -> tuple[bool, str | None]:
	if origin == ORIGIN_OWN:
		# Guest own == shared
		shared = shared_workflow_dir()
		own = workflow_dir_path(username, must_exist=True)
		if shared and own and shared == own:
			if workflows_settings()["guest_can_write_shared"]:
				return True, None
			return False, "SHARED_READONLY"
		return True, None
	if origin in (ORIGIN_SHARED, ORIGIN_LEGACY):
		if caller_is_owner(username):
			return True, None
		return False, "SHARED_READONLY"
	return False, "SHARED_READONLY"


def log_workflow_action(username: str, action: str, source: str, rel: str) -> None:
	"""Safe audit log — never tokens/headers."""
	print(f"[mss-login] user={username!s} action={action} source={source} file={rel!s}", flush=True)
