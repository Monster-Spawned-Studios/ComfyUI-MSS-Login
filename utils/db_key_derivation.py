# --- START OF FILE utils/db_key_derivation.py ---
"""
Derive a 32-byte database encryption key from SECRET_KEY using Argon2id with
configurable hardening levels (LOW, STANDARD/MEDIUM, HIGH/SECURE).
Used for SQLCipher when encryption_level is set. Do not log SECRET_KEY or derived key.
"""

from typing import Optional

# Fixed salt/info for key derivation (change only when rotating DB encryption)
DB_KEY_SALT_PREFIX = b"mss-login-db-key-v1"

# Argon2id presets: (time_cost, memory_cost_kib, parallelism)
# https://argon2-cffi.readthedocs.io/en/stable/parameters.html
ARGON2_PRESETS = {
	"low": (2, 65536, 1),  # Faster startup, lower security
	"standard": (3, 65536, 4),  # Default
	"secure": (5, 262144, 2),  # Stronger, slower DB open
}
# Aliases
ARGON2_PRESETS["medium"] = ARGON2_PRESETS["standard"]
ARGON2_PRESETS["high"] = ARGON2_PRESETS["secure"]

_argon2_available: bool | None = None


def _argon2_available_check() -> bool:
	global _argon2_available
	if _argon2_available is not None:
		return _argon2_available
	try:
		from argon2 import PasswordHasher

		_argon2_available = True
	except ImportError:
		_argon2_available = False
	return _argon2_available


def derive_db_key(secret_key: str, level: str) -> bytes:
	"""
	Derive a 32-byte key for SQLCipher from SECRET_KEY using Argon2id.
	level: "low" | "standard" | "medium" | "high" | "secure" (medium->standard, high->secure).
	Returns 32 bytes. Raises RuntimeError if argon2-cffi is not installed or level is invalid.
	"""
	if not secret_key:
		raise ValueError("secret_key must be non-empty")
	level = (level or "standard").strip().lower()
	if level in ("medium", "standard"):
		level = "standard"
	elif level in ("high", "secure"):
		level = "secure"
	if level not in ARGON2_PRESETS:
		level = "standard"
	if not _argon2_available_check():
		raise RuntimeError(
			"argon2-cffi is required for database key derivation; pip install argon2-cffi"
		)
	from argon2.low_level import Type, hash_secret_raw

	time_cost, memory_cost_kib, parallelism = ARGON2_PRESETS[level]
	salt = DB_KEY_SALT_PREFIX + level.encode("utf-8")
	key_material = secret_key.encode("utf-8") if isinstance(secret_key, str) else secret_key
	# hash_secret_raw(secret, salt, time_cost, memory_cost, parallelism, hash_len, type)
	key_bytes = hash_secret_raw(
		secret=key_material,
		salt=salt,
		time_cost=time_cost,
		memory_cost=memory_cost_kib,
		parallelism=parallelism,
		hash_len=32,
		type=Type.ID,
	)
	return key_bytes
