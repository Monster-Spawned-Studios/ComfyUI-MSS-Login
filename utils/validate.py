import re

# Usernames that cannot be registered: they conflict with paths or special logic.
# - "guest": used as fallback for unauthenticated users (e.g. in get_user_root).
# - "default": ComfyUI uses user/default/workflows; reserving avoids confusion.
# - "defaults": on case-insensitive filesystems, Users/defaults collides with
#   repo path users/defaults/ (DEFAULT_GROUP_CONFIG_PATH); would break group config.
RESERVED_USERNAMES = frozenset({"guest", "default", "defaults"})


def validate_username(username: str) -> tuple[bool, str]:
	"""
	Validate username based on:
	- Only letters, numbers, and underscores.
	- No spaces.
	- At least 3 characters long.
	- Not a reserved name (guest, default, defaults).
	"""
	if not username or not isinstance(username, str):
		return False, "Username is required."
	username = username.strip()
	if username.lower() in RESERVED_USERNAMES:
		return False, "That username is reserved."
	if re.match(r"^[a-zA-Z0-9_]{3,}$", username):
		return True, ""
	return (
		False,
		"Username must be at least 3 characters, contain only letters, numbers, and underscores, and cannot contain spaces.",
	)


def validate_password(password: str) -> tuple[bool, str]:
	"""
	Validate password based on:
	- At least 8 characters long.
	- Must contain at least one digit.
	- Must contain at least one special character (e.g., !@#$%^&*).
	- Cannot contain spaces.
	"""
	if re.match(
		r"^(?=.*\d)(?=.*[!@#$%^&*()_+\-=\[\]{};':\"\\|,.<>?/`~])[A-Za-z\d!@#$%^&*()_+\-=\[\]{};':\"\\|,.<>?/`~]{8,}$",
		password,
	):
		return True, ""
	return (
		False,
		"Password must be at least 8 characters long, contain at least one digit, one special character, and no spaces.",
	)
