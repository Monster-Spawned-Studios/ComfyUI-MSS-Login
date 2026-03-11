"""
Sanitize user text inputs for database storage and safe handling.

Use these helpers to prevent:
- SQL injection (in conjunction with parameterized queries)
- Database corruption from null bytes or control characters
- Path traversal when username is used in paths
- Excessive length / DoS from oversized inputs

All DB access in this project uses parameterized queries; this module adds
defense-in-depth by normalizing and constraining values before they are bound.
"""

import re
from typing import Optional

# Maximum lengths for stored/processed values (align with typical DB column sizes)
USERNAME_MAX_LEN = 128
PASSWORD_MAX_LEN = 4096
LABEL_MAX_LEN = 128
TOKEN_HASH_PREFIX_MAX_LEN = 64
TOKEN_HASH_PREFIX_MIN_LEN = 8

# Control characters and null byte (strip from all user text)
_CONTROL_AND_NULL = re.compile(r"[\x00-\x1f\x7f]")


def _strip_control_and_null(value: str) -> str:
	"""Remove null bytes and ASCII control characters."""
	if not isinstance(value, str):
		return ""
	return _CONTROL_AND_NULL.sub("", value)


def sanitize_username(value: Optional[str], max_len: int = USERNAME_MAX_LEN) -> str:
	"""
	Sanitize a username for safe storage and use in paths/DB.

	- Strips leading/trailing whitespace and null/control characters.
	- Restricts to letters, digits, underscore, and hyphen (safe for paths and identifiers).
	- Truncates to max_len.

	Use with validate_username() for registration (which enforces length and pattern).
	"""
	if value is None:
		return ""
	s = _strip_control_and_null(str(value).strip())
	# Allow only safe identifier characters (alphanumeric, underscore, hyphen)
	s = re.sub(r"[^a-zA-Z0-9_\-]", "", s)
	return s[:max_len]


def sanitize_password_input(value: Optional[str], max_len: int = PASSWORD_MAX_LEN) -> str:
	"""
	Sanitize password input before hashing/verification.

	- Removes null bytes and control characters (can cause issues in hashing/logging).
	- Does not alter allowed character set (passwords may contain special chars).
	- Truncates to max_len to avoid DoS.

	Never log or store the return value in plain text.
	"""
	if value is None:
		return ""
	s = _strip_control_and_null(str(value))
	return s[:max_len]


def sanitize_label(value: Optional[str], max_len: int = LABEL_MAX_LEN) -> str:
	"""
	Sanitize a label (e.g. API token label) for safe storage.

	- Strips control characters and null bytes.
	- Truncates to max_len.
	"""
	if value is None:
		return ""
	s = _strip_control_and_null(str(value).strip())
	return s[:max_len]


def sanitize_token_hash_prefix(
	value: Optional[str],
	min_len: int = TOKEN_HASH_PREFIX_MIN_LEN,
	max_len: int = TOKEN_HASH_PREFIX_MAX_LEN,
) -> str:
	"""
	Sanitize a token hash prefix (e.g. for revoke-by-prefix).

	- Strips control characters and null bytes.
	- Restricts to alphanumeric (hex-style prefix).
	- Returns normalized prefix; caller should check len >= min_len.
	"""
	if value is None:
		return ""
	s = _strip_control_and_null(str(value).strip().rstrip("."))
	s = re.sub(r"[^a-zA-Z0-9]", "", s)
	return s[:max_len]


def sanitize_totp_code(value: Optional[str], max_len: int = 8) -> str:
	"""
	Sanitize TOTP/MFA code input (digits only, typical length 6).
	"""
	if value is None:
		return ""
	s = _strip_control_and_null(str(value).strip().replace(" ", ""))
	s = re.sub(r"[^0-9]", "", s)
	return s[:max_len]


def sanitize_backup_code_input(value: Optional[str], max_len: int = 16) -> str:
	"""
	Sanitize backup code input (alphanumeric, no spaces/dashes).
	"""
	if value is None:
		return ""
	s = _strip_control_and_null(str(value).strip().replace(" ", "").replace("-", "").upper())
	s = re.sub(r"[^A-Z0-9]", "", s)
	return s[:max_len]


def sanitize_prompt_text(value: Optional[str], max_len: int = 1024) -> str:
	"""
	Sanitize freeform prompt-like text for display or storage.

	- Strips null bytes and control characters.
	- Truncates to max_len.

	Note: ComfyUI prompt in this codebase is a workflow graph (dict), not freeform text;
	this is for any future or ancillary text fields that might be stored.
	"""
	if value is None:
		return ""
	s = _strip_control_and_null(str(value).strip())
	return s[:max_len]
