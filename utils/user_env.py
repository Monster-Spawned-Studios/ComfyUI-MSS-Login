"""
ComfyUI-MSS-Login per-user environment helpers.

Responsible for:
- Resolving the extension root
- Managing the shared users/ directory under DATA_DIR (not the repo)
- Creating / locating per-user folders (users/<username>/...)
- Loading / saving per-user settings JSON
- Centralizing paths for user_db.json and (renamed) mss_login_settings.js
- Workflow storage under MSS_LOGIN_DATA_DIR (see get_user_workflow_dir)
"""

import json
import os
import shutil
from typing import Any, Dict, List

from .data_dir import get_data_dir, get_data_subdir

_USERS_MIGRATED_MARKER = ".migrated_users_to_data_dir"

# -----------------------
# Path helpers
# -----------------------


def get_extension_root() -> str:
	"""
	Returns the root directory of the ComfyUI-mss-login extension.
	Assumes this file lives in `<root>/utils/user_env.py`.
	"""
	here = os.path.dirname(os.path.abspath(__file__))
	return os.path.abspath(os.path.join(here, ".."))


def _copy_tree_missing(src: str, dst: str) -> bool:
	"""Copy files/dirs from src into dst without overwriting existing entries."""
	if not src or not os.path.isdir(src):
		return False
	if os.path.abspath(src) == os.path.abspath(dst):
		return False
	copied = False
	os.makedirs(dst, exist_ok=True)
	try:
		for name in os.listdir(src):
			src_item = os.path.join(src, name)
			dst_item = os.path.join(dst, name)
			if os.path.exists(dst_item):
				continue
			try:
				if os.path.isfile(src_item):
					shutil.copy2(src_item, dst_item)
					copied = True
				elif os.path.isdir(src_item):
					shutil.copytree(src_item, dst_item, dirs_exist_ok=True)
					copied = True
			except OSError:
				continue
	except OSError:
		return False
	return copied


def _legacy_users_paths() -> list[str]:
	"""Read-only legacy capital Users/ locations (extension root + DATA_DIR)."""
	return [os.path.join(get_extension_root(), "Users"), os.path.join(get_data_dir(), "Users")]


def _tree_has_files(path: str) -> bool:
	"""True if path exists and contains at least one regular file."""
	if not path or not os.path.isdir(path):
		return False
	try:
		for dirpath, _, filenames in os.walk(path):
			if filenames:
				return True
	except OSError:
		return False
	return False


def _legacy_fully_mirrored(src: str, dest: str) -> bool:
	"""True when every file under src also exists at the same relative path in dest."""
	if not src or not os.path.isdir(src):
		return True
	if not dest or not os.path.isdir(dest):
		return False
	try:
		for dirpath, _, filenames in os.walk(src):
			for fn in filenames:
				rel = os.path.relpath(os.path.join(dirpath, fn), src)
				if not os.path.isfile(os.path.join(dest, rel)):
					return False
	except OSError:
		return False
	return True


def _remove_legacy_users_tree(path: str) -> bool:
	"""Remove a leftover capital Users/ directory when safe."""
	if not path or not os.path.isdir(path):
		return False
	try:
		shutil.rmtree(path)
		return not os.path.exists(path)
	except OSError:
		return False


def migrate_users_dir_if_needed() -> bool:
	"""
	Consolidate legacy capital Users/ trees into DATA_DIR/users/.

	Always merges any remaining capital Users/ sources into lowercase users/,
	then removes leftover capital Users/ trees that are empty or fully mirrored
	into users/. Writes a marker on first successful pass.
	"""
	data_dir = get_data_dir()
	marker = os.path.join(data_dir, _USERS_MIGRATED_MARKER)
	dest = get_data_subdir("users")
	os.makedirs(dest, exist_ok=True)

	migrated = False
	for legacy in _legacy_users_paths():
		# Never treat a case-folded same path as a source (e.g. macOS).
		if os.path.abspath(legacy) == os.path.abspath(dest):
			continue
		if not os.path.isdir(legacy):
			continue
		if _copy_tree_missing(legacy, dest):
			migrated = True
		# Drop capital Users/ once content is under users/ (or the tree is empty).
		if (not _tree_has_files(legacy)) or _legacy_fully_mirrored(legacy, dest):
			if _remove_legacy_users_tree(legacy):
				migrated = True

	try:
		if not os.path.isfile(marker):
			with open(marker, "w", encoding="utf-8") as f:
				f.write("migrated")
	except OSError:
		pass
	return migrated


def get_users_root() -> str:
	"""
	Root folder for all MSS-Login user-related files:
	  <DATA_DIR>/users/
	"""
	migrate_users_dir_if_needed()
	root = get_data_subdir("users")
	os.makedirs(root, exist_ok=True)
	return root


def get_user_db_path() -> str:
	"""
	Global user database JSON.
	  <DATA_DIR>/users/user_db.json
	"""
	return os.path.join(get_users_root(), "user_db.json")


def get_frontend_settings_js_path() -> str:
	"""
	Location of the frontend settings JS file (renamed from sentinel_settings.js):
	  <DATA_DIR>/users/mss_login_settings.js

	This file is still served as a static asset by the backend, but physically
	lives under DATA_DIR/users/ so runtime data stays out of the repo.
	"""
	return os.path.join(get_users_root(), "mss_login_settings.js")


def get_user_root(username: str) -> str:
	"""
	Per-user root folder:
	  <DATA_DIR>/users/<username>/
	"""
	username = (username or "guest").strip() or "guest"
	# Prevent path traversal: username must be a single path segment
	if ".." in username or "/" in username or "\\" in username:
		username = "guest"
	path = os.path.join(get_users_root(), username)
	os.makedirs(path, exist_ok=True)
	return path


def get_user_css_dir(username: str) -> str:
	"""
	Per-user CSS directory:
	  <DATA_DIR>/users/<username>/css/
	"""
	path = os.path.join(get_user_root(username), "css")
	os.makedirs(path, exist_ok=True)
	return path


def get_user_settings_path(username: str) -> str:
	"""
	Per-user settings JSON file:
	  <DATA_DIR>/users/<username>/settings.json
	"""
	return os.path.join(get_user_root(username), "settings.json")


# -----------------------
# JSON helpers
# -----------------------


def _load_json_file(path: str, default: Any) -> Any:
	if not os.path.exists(path):
		return default
	try:
		with open(path, "r", encoding="utf-8") as f:
			return json.load(f)
	except Exception:
		# Don't explode the whole app if someone corrupts a file.
		return default


def _save_json_file(path: str, data: Any) -> None:
	folder = os.path.dirname(path)
	os.makedirs(folder, exist_ok=True)
	with open(path, "w", encoding="utf-8") as f:
		json.dump(data, f, indent=4)


# -----------------------
# Per-user settings API
# -----------------------


def load_user_settings(username: str) -> dict[str, Any]:
	"""
	Load per-user settings JSON. Returns {} if missing or invalid.
	"""
	path = get_user_settings_path(username)
	data = _load_json_file(path, {})
	return data if isinstance(data, dict) else {}


def save_user_settings(username: str, settings: dict[str, Any]) -> None:
	"""
	Save per-user settings JSON. Non-dicts are ignored.
	"""
	if not isinstance(settings, dict):
		return
	path = get_user_settings_path(username)
	_save_json_file(path, settings)


# -----------------------
# Group / global config path helper (optional)
# -----------------------


def get_groups_config_path(filename: str = "mss_login_groups.json") -> str:
	"""
	Path helper for the role/group config file.

	If you want to rename sentinel_groups.json to mss_login_groups.json and keep
	it in the extension root, this gives you a single place to reference it.
	Filename is restricted to a single segment to prevent path traversal.
	"""
	from .path_safety import is_safe_filename

	if not filename or not is_safe_filename(filename):
		filename = "mss_login_groups.json"
	return os.path.join(get_extension_root(), filename)


def get_gallery_root_config_path() -> str:
	"""
	Global config pointing at which user is used as Gallery root.

	  <DATA_DIR>/users/gallery_root.json

	Stored as: { "user": "<username>" }
	"""
	return os.path.join(get_users_root(), "gallery_root.json")


def get_gallery_root_user() -> str | None:
	"""
	Return the username currently configured as Gallery root, or None.
	"""
	data = _load_json_file(get_gallery_root_config_path(), {})
	user = data.get("user")
	if isinstance(user, str) and user.strip():
		return user.strip()
	return None


def set_gallery_root_user(username: str | None) -> None:
	"""
	Set or clear the Gallery root user.
	If username is None or empty, the gallery root is cleared.
	"""
	path = get_gallery_root_config_path()
	if not username:
		_save_json_file(path, {})
		return

	username = username.strip()
	if not username:
		_save_json_file(path, {})
		return

	_save_json_file(path, {"user": username})


def list_user_files(username: str, max_files: int = 500) -> list[str]:
	"""
	Return a relative list of files under the user's root directory.

	  users/<username>/...

	Limited to max_files entries to avoid insane payloads.
	"""
	root = get_user_root(username)
	collected: list[str] = []

	for dirpath, _, filenames in os.walk(root):
		for fn in filenames:
			full = os.path.join(dirpath, fn)
			rel = os.path.relpath(full, root)
			collected.append(rel)
			if len(collected) >= max_files:
				return collected
	return collected


def purge_user_root(username: str) -> None:
	"""
	Delete the entire per-user folder under users/<username>/ and recreate it.
	"""
	root = get_user_root(username)
	if os.path.exists(root):
		shutil.rmtree(root, ignore_errors=True)
	os.makedirs(root, exist_ok=True)


def _sanitize_username_for_path(username: str) -> str:
	"""Normalize username for use as a path segment; prevent path traversal."""
	name = (username or "guest").strip() or "guest"
	if ".." in name or "/" in name or "\\" in name:
		return "guest"
	return name


def _get_plugin_workflow_dir(username: str) -> str:
	"""Legacy extension-root workflow dir: <ext_root>/Users/<username>/workflows/."""
	safe = _sanitize_username_for_path(username)
	return os.path.join(get_extension_root(), "Users", safe, "workflows")


def _legacy_data_users_workflow_dirs(username: str) -> list[str]:
	"""Read-only legacy DATA_DIR/Users/<user>/workflows paths (no mkdir)."""
	safe = _sanitize_username_for_path(username)
	legacy_root = os.path.join(get_data_dir(), "Users", safe, "workflows")
	return [os.path.join(legacy_root, "default"), legacy_root]


def _copy_workflows_if_empty(sources: list[str], target_dir: str) -> None:
	"""
	One-time helper: copy workflows from the first non-empty source directory
	into target_dir if target_dir is currently empty. Non-destructive.
	"""
	try:
		if os.path.isdir(target_dir):
			existing = [f for f in os.listdir(target_dir) if f != "default"]
			if existing:
				return
	except OSError:
		return

	for src in sources:
		if not src or not os.path.isdir(src) or os.path.abspath(src) == os.path.abspath(target_dir):
			continue
		try:
			items = os.listdir(src)
			# Only consider it a source if it has files other than the target directory itself
			valid_items = [item for item in items if item != "default"]
			if not valid_items:
				continue
			os.makedirs(target_dir, exist_ok=True)
			for name in valid_items:
				src_item = os.path.join(src, name)
				dst_item = os.path.join(target_dir, name)
				if os.path.isfile(src_item) and not os.path.exists(dst_item):
					shutil.copy2(src_item, dst_item)
				elif os.path.isdir(src_item) and not os.path.exists(dst_item):
					shutil.copytree(src_item, dst_item, dirs_exist_ok=True)
			break
		except OSError:
			continue


def get_user_workflow_dir(username: str) -> str:
	"""
	Return the per-user workflow directory.

	- If MSS_LOGIN_DATA_DIR is set:
	  Stores workflows under <MSS_LOGIN_DATA_DIR>/workflows/<username>/
	- If MSS_LOGIN_DATA_DIR is not set:
	  Stores workflows in the configured user directory with a 'default' subfolder:
	  <data_dir>/users/<username>/workflows/default/
	"""
	safe = _sanitize_username_for_path(username)
	raw_env = os.environ.get("MSS_LOGIN_DATA_DIR", "").strip()

	# Ensure any leftover capital Users/ is merged into lowercase users/ first.
	migrate_users_dir_if_needed()

	if raw_env:
		base = os.path.abspath(raw_env)
		target_dir = os.path.join(base, "workflows", safe)
		os.makedirs(target_dir, exist_ok=True)
		sources = [
			get_data_subdir("users", safe, "workflows", "default"),
			get_data_subdir("users", safe, "workflows"),
			*_legacy_data_users_workflow_dirs(safe),
			_get_plugin_workflow_dir(safe),
		]
		_copy_workflows_if_empty(sources, target_dir)
		return target_dir
	else:
		target_dir = get_data_subdir("users", safe, "workflows", "default")
		os.makedirs(target_dir, exist_ok=True)
		sources = [
			get_data_subdir("users", safe, "workflows"),
			*_legacy_data_users_workflow_dirs(safe),
			_get_plugin_workflow_dir(safe),
		]
		_copy_workflows_if_empty(sources, target_dir)
		return target_dir


def list_user_workflows(username: str) -> list[str]:
	"""
	Returns a list of workflow filenames (relative paths) for the user.
	"""
	wf_dir = get_user_workflow_dir(username)
	workflows = []
	if os.path.exists(wf_dir):
		for root, _, files in os.walk(wf_dir):
			for file in files:
				if file.endswith(".json"):
					# We store them relative to the workflow folder
					full_path = os.path.join(root, file)
					rel_path = os.path.relpath(full_path, wf_dir)
					workflows.append(rel_path)
	return workflows
