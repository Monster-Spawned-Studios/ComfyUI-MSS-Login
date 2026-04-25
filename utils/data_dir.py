# --- START OF FILE utils/data_dir.py ---
"""
External data directory for mss-login: ~/.comfyui-mss-login/ (or MSS_LOGIN_DATA_DIR).
All mutable user data (config, DB, session store, whitelist/blacklist) lives here
so it is untouched by git pull and ComfyUI Manager updates.
"""

import json
import os
import shutil
from pathlib import Path
from typing import Any

# Repo root (package directory, one level up from utils/)
_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

_MIGRATED_MARKER = ".migrated_to_data_dir"
_DATA_DIR: str | None = None
# Set to True when migrate_from_repo_if_needed actually copies files (for logging in globals)
MIGRATION_PERFORMED = False


def get_data_dir() -> str:
	"""Return the external data directory path. Uses MSS_LOGIN_DATA_DIR env or ~/.comfyui-mss-login."""
	global _DATA_DIR
	if _DATA_DIR is not None:
		return _DATA_DIR
	raw = os.environ.get("MSS_LOGIN_DATA_DIR", "").strip()
	if raw:
		_DATA_DIR = os.path.abspath(raw)
	else:
		_DATA_DIR = os.path.join(Path.home(), ".comfyui-mss-login")
	return _DATA_DIR


def get_data_subdir(*parts: str) -> str:
	"""Return path inside the data directory (e.g. get_data_subdir('data', 'users.db'))."""
	return os.path.join(get_data_dir(), *parts)


def _deep_merge_new_keys(default: Any, existing: Any) -> Any:
	"""Merge default into existing: only add keys that are missing in existing. Never overwrite."""
	if not isinstance(default, dict) or not isinstance(existing, dict):
		return existing
	out = dict(existing)
	for k, v_default in default.items():
		if k not in out:
			out[k] = v_default
		else:
			out[k] = _deep_merge_new_keys(v_default, out[k])
	return out


def _load_json(path: str) -> dict:
	if os.path.isfile(path):
		try:
			with open(path, "r", encoding="utf-8") as f:
				return json.load(f)
		except Exception:
			pass
	return {}


def _save_json(path: str, data: dict) -> None:
	os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
	with open(path, "w", encoding="utf-8") as f:
		json.dump(data, f, indent=4)


def ensure_data_dir(repo_root: str | None = None) -> str:
	"""
	Create external data directory structure and ensure config exists.
	Runs one-time migration from repo-local files if present, then ensures config.json
	(copy from config.defaults.json if missing, or deep-merge new keys if present).
	Returns the data directory path.
	"""
	root = repo_root or _REPO_ROOT
	data_dir = get_data_dir()
	data_sub = os.path.join(data_dir, "data")
	backups_sub = os.path.join(data_dir, "backups")
	os.makedirs(data_sub, exist_ok=True)
	os.makedirs(backups_sub, exist_ok=True)

	# One-time migration from repo to data dir before touching config
	migrate_from_repo_if_needed(root)

	# One-time migration: users.db -> mss_login_data.db and update config
	_migrate_users_db_to_mss_login_data(data_dir)

	defaults_path = os.path.join(root, "config.defaults.json")
	if not os.path.isfile(defaults_path):
		defaults_path = os.path.join(root, "config.json")
	user_config_path = os.path.join(data_dir, "config.json")

	if not os.path.isfile(user_config_path):
		if os.path.isfile(defaults_path):
			shutil.copy2(defaults_path, user_config_path)
	else:
		defaults = _load_json(defaults_path)
		if defaults:
			existing = _load_json(user_config_path)
			merged = _deep_merge_new_keys(defaults, existing)
			_save_json(user_config_path, merged)
	return data_dir


def _migrate_users_db_to_mss_login_data(data_dir: str) -> None:
	"""
	One-time migration: if config uses data/users.db and that file exists but
	data/mss_login_data.db does not, copy the DB and update config to use mss_login_data.db.
	"""
	config_path = os.path.join(data_dir, "config.json")
	config = _load_json(config_path)
	users_cfg = config.get("users_db") or {}
	if isinstance(users_cfg, str):
		users_path = users_cfg
	else:
		users_path = (users_cfg.get("sqlite_path") or "data/users.db").replace("\\", "/")
	if not users_path.rstrip("/").endswith("users.db"):
		return
	abs_users = (
		os.path.normpath(os.path.join(data_dir, users_path))
		if not os.path.isabs(users_path)
		else users_path
	)
	if not os.path.isfile(abs_users):
		return
	same_dir = os.path.dirname(abs_users)
	mss_target = os.path.join(same_dir, "mss_login_data.db")
	if os.path.isfile(mss_target):
		return
	try:
		shutil.copy2(abs_users, mss_target)
		for suffix in ("-wal", "-shm"):
			src = abs_users + suffix
			if os.path.isfile(src):
				shutil.copy2(src, mss_target + suffix)
	except Exception:
		return
	new_rel = os.path.relpath(mss_target, data_dir).replace("\\", "/")
	if "users_db" not in config:
		config["users_db"] = {}
	if isinstance(config["users_db"], dict):
		config["users_db"]["sqlite_path"] = new_rel
	api_cfg = config.get("api_token_store")
	if isinstance(api_cfg, dict):
		config["api_token_store"]["sqlite_path"] = new_rel
	try:
		_save_json(config_path, config)
	except Exception:
		pass


def migrate_from_repo_if_needed(repo_root: str | None = None) -> bool:
	"""
	One-time migration: if repo-local users/users.db or config.json (or groups) exist
	and we have not migrated yet, copy them to the external data directory.
	Returns True if migration was performed.
	"""
	root = repo_root or _REPO_ROOT
	data_dir = get_data_dir()
	marker = os.path.join(data_dir, _MIGRATED_MARKER)
	if os.path.isfile(marker):
		return False

	repo_users = os.path.join(root, "users")
	repo_config = os.path.join(root, "config.json")
	data_sub = os.path.join(data_dir, "data")
	migrated = False

	# Migrate config.json
	if os.path.isfile(repo_config):
		dst = os.path.join(data_dir, "config.json")
		if not os.path.isfile(dst):
			shutil.copy2(repo_config, dst)
			migrated = True

	# Migrate users.db
	src_db = os.path.join(repo_users, "users.db")
	dst_db = os.path.join(data_sub, "users.db")
	if os.path.isfile(src_db) and not os.path.isfile(dst_db):
		os.makedirs(data_sub, exist_ok=True)
		shutil.copy2(src_db, dst_db)
		for suffix in ("-wal", "-shm"):
			if os.path.isfile(src_db + suffix):
				shutil.copy2(src_db + suffix, dst_db + suffix)
		migrated = True

	# Migrate mss_login_groups.json
	src_groups = os.path.join(repo_users, "mss_login_groups.json")
	dst_groups = os.path.join(data_sub, "mss_login_groups.json")
	if os.path.isfile(src_groups) and not os.path.isfile(dst_groups):
		os.makedirs(data_sub, exist_ok=True)
		shutil.copy2(src_groups, dst_groups)
		migrated = True

	# Migrate session_tokens.json
	src_session = os.path.join(repo_users, "session_tokens.json")
	dst_session = os.path.join(data_sub, "session_tokens.json")
	if os.path.isfile(src_session) and not os.path.isfile(dst_session):
		os.makedirs(data_sub, exist_ok=True)
		shutil.copy2(src_session, dst_session)
		migrated = True

	# Migrate whitelist/blacklist (config may reference users/ or security/)
	for name in ("whitelist.txt", "blacklist.txt"):
		for sub in ("users", "security"):
			src = os.path.join(root, sub, name)
			if os.path.isfile(src):
				dst = os.path.join(data_sub, name)
				if not os.path.isfile(dst):
					os.makedirs(data_sub, exist_ok=True)
					shutil.copy2(src, dst)
					migrated = True
				break

	# Migrate .env from repo root to data dir (so secrets stay with data)
	repo_env = os.path.join(root, ".env")
	data_env = os.path.join(data_dir, ".env")
	if os.path.isfile(repo_env) and not os.path.isfile(data_env):
		shutil.copy2(repo_env, data_env)
		try:
			os.chmod(data_env, 0o600)
		except Exception:
			pass
		migrated = True

	if migrated:
		global MIGRATION_PERFORMED
		MIGRATION_PERFORMED = True
		try:
			with open(marker, "w", encoding="utf-8") as f:
				f.write("migrated")
		except Exception:
			pass
	return migrated
