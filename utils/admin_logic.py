# --- START OF FILE utils/admin_logic.py ---
"""Admin user operations: update groups/admin/sfw_check and delete user. Uses UsersDB only (no JSON)."""

from ..globals import users_db


def patch_user_group(username, group_list, is_admin_bool, sfw_check=None):
	return users_db.update_user(username, group_list, is_admin_bool, sfw_check)


def delete_user_record(username):
	return users_db.delete_user(username)
