# --- START OF FILE utils/bootstrap.py ---
import os
import uuid
from ..constants import GROUPS_CONFIG_FILE, DEFAULT_GROUP_CONFIG_PATH
from .json_utils import load_json_file, save_json_file
from .admin_logic import patch_user_group
from ..globals import logger, users_db


def load_default_groups():
	cfg = load_json_file(DEFAULT_GROUP_CONFIG_PATH, None)
	if cfg is None:
		logger.error("[MSS-Login] Missing default_group_config.json; using built-in fallback!")
		return {
			"owner": {
				"can_run": True,
				"can_upload": True,
				"can_access_manager": True,
				"can_access_api": True,
				"can_see_restricted_settings": True,
				"can_have_api_tokens": True,
				"can_have_non_expiring_jwt": True,
				"can_view_console": True,
			},
			"admin": {
				"can_run": True,
				"can_upload": True,
				"can_access_manager": True,
				"can_access_api": True,
				"can_see_restricted_settings": True,
				"can_have_api_tokens": True,
				"can_have_non_expiring_jwt": False,
				"can_view_console": False,
			},
			"power": {
				"can_run": True,
				"can_upload": True,
				"can_access_manager": True,
				"can_access_api": True,
				"can_see_restricted_settings": False,
				"can_have_api_tokens": True,
				"can_have_non_expiring_jwt": False,
			},
			"user": {
				"can_run": True,
				"can_upload": True,
				"can_access_manager": False,
				"can_access_api": True,
				"can_see_restricted_settings": False,
				"can_have_api_tokens": False,
				"can_have_non_expiring_jwt": False,
			},
			"guest": {
				"can_run": False,
				"can_upload": False,
				"can_access_manager": False,
				"can_access_api": True,
				"can_see_restricted_settings": False,
				"can_have_api_tokens": False,
				"can_have_non_expiring_jwt": False,
			},
		}
	return cfg


def _apply_owner_max_merge(current: dict) -> None:
	"""Set owner's permissions to the max of all roles (true if any role has true). Immutable owner."""
	if "owner" not in current:
		return
	all_keys = set()
	for perms in current.values():
		if isinstance(perms, dict):
			all_keys.update(perms.keys())
	for key in all_keys:
		owner_has = any(
			bool(perms.get(key)) for perms in current.values() if isinstance(perms, dict)
		)
		current.setdefault("owner", {})[key] = owner_has


def ensure_groups_config():
	default_cfg = load_default_groups()
	current = load_json_file(GROUPS_CONFIG_FILE, {})
	changed = False

	# Ensure owner exists in default fallback (default_group_config.json already has it)
	if "owner" not in default_cfg and "admin" in default_cfg:
		default_cfg = dict(default_cfg)
		default_cfg["owner"] = dict(default_cfg["admin"])

	# Add missing groups
	for role, perms in default_cfg.items():
		if role not in current:
			current[role] = dict(perms) if isinstance(perms, dict) else {}
			changed = True

	# Add missing permission keys
	for role, perms in default_cfg.items():
		if not isinstance(perms, dict):
			continue
		for key, value in perms.items():
			if role not in current:
				current[role] = {}
			if key not in current[role]:
				current[role][key] = value
				changed = True

	# Owner always gets max of all roles (immutable additive permissions)
	owner_before = dict(current.get("owner", {})) if current.get("owner") else {}
	_apply_owner_max_merge(current)
	owner_after = current.get("owner", {})
	if owner_before != owner_after:
		changed = True

	if changed:
		save_json_file(GROUPS_CONFIG_FILE, current)


def ensure_guest_user():
	try:
		guest_id, guest_rec = users_db.get_user("guest")
	except Exception as e:
		logger.error(f"[mss_login] Error checking guest user: {e}")
		return

	if guest_id is not None:
		patch_user_group("guest", ["guest"], False)
		return

	try:
		random_password = str(uuid.uuid4())
		new_guest_id = str(uuid.uuid4())
		users_db.add_user(new_guest_id, "guest", random_password, False)
		patch_user_group("guest", ["guest"], False)
		logger.info("[mss_login] Created default 'guest' user")
	except Exception as e:
		logger.error(f"[mss_login] Error creating guest user: {e}")
