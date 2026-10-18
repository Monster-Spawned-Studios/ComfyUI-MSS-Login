# --- START OF FILE utils/encryption.py ---
"""
Encryption helper for sensitive data at rest.
Uses SECRET_KEY to derive an encryption key via HKDF; Fernet for symmetric encryption.
Do not log or expose SECRET_KEY or derived keys.
"""

import base64
import hashlib
import hmac
from typing import Optional

# Fernet is part of cryptography package
try:
	from cryptography.fernet import Fernet
	from cryptography.hazmat.backends import default_backend
	from cryptography.hazmat.primitives import hashes
	from cryptography.hazmat.primitives.kdf.hkdf import HKDF

	_CRYPTO_AVAILABLE = True
except ImportError:
	_CRYPTO_AVAILABLE = False
	Fernet = None

# Fixed info string for key derivation (do not change after deployment or existing ciphertexts break)
HKDF_INFO = b"mss-login-users-totp-v1"


def _derive_key(secret_key: str) -> bytes:
	"""Derive a 32-byte key for Fernet from SECRET_KEY using HKDF-SHA256."""
	if not _CRYPTO_AVAILABLE:
		raise RuntimeError("cryptography package required for encryption; pip install cryptography")
	key_material = secret_key.encode("utf-8") if isinstance(secret_key, str) else secret_key
	hkdf = HKDF(
		algorithm=hashes.SHA256(), length=32, salt=None, info=HKDF_INFO, backend=default_backend()
	)
	key_bytes = hkdf.derive(key_material)
	# Fernet needs base64-encoded 32-byte key
	return base64.urlsafe_b64encode(key_bytes)


def encrypt_value(secret_key: str, plaintext: str) -> str | None:
	"""Encrypt a string; return base64-encoded ciphertext or None on failure."""
	if not plaintext:
		return None
	if not _CRYPTO_AVAILABLE:
		return None
	try:
		key = _derive_key(secret_key)
		f = Fernet(key)
		ct = f.encrypt(plaintext.encode("utf-8"))
		return ct.decode("ascii")
	except Exception:
		return None


def decrypt_value(secret_key: str, ciphertext: str) -> str | None:
	"""Decrypt a base64-encoded ciphertext; return plaintext or None on failure."""
	if not ciphertext:
		return None
	if not _CRYPTO_AVAILABLE:
		return None
	try:
		key = _derive_key(secret_key)
		f = Fernet(key)
		pt = f.decrypt(ciphertext.encode("ascii"))
		return pt.decode("utf-8")
	except Exception:
		return None


def encrypt_and_verify(secret_key: str, plaintext: str) -> str | None:
	"""
	Encrypt plaintext and verify round-trip decrypt before returning ciphertext.

	Returns ciphertext only when decrypt yields the same plaintext (constant-time
	compare). Returns None on empty input, crypto failure, or round-trip mismatch
	so callers never persist undecryptable blobs.
	"""
	plain = (plaintext or "").strip()
	if not plain:
		return None
	ciphertext = encrypt_value(secret_key, plain)
	if not ciphertext:
		return None
	round_trip = decrypt_value(secret_key, ciphertext)
	if round_trip is None or not hmac.compare_digest(round_trip, plain):
		return None
	return ciphertext


def hash_backup_code(backup_code: str) -> str:
	"""
	Legacy SHA-256 hash (unsalted). Kept for dual-read of pre-hardening single backup codes.
	New codes use hash_recovery_code (bcrypt).
	"""
	return hashlib.sha256(backup_code.encode("utf-8")).hexdigest()


def verify_backup_code(backup_code: str, stored_hash: str) -> bool:
	"""Constant-time comparison against legacy SHA-256 hash."""
	if not stored_hash or not backup_code:
		return False
	computed = hash_backup_code(backup_code)
	return hmac.compare_digest(computed, stored_hash)


def hash_recovery_code(code: str) -> str:
	"""Hash a recovery code with bcrypt. Never store plaintext recovery codes."""
	import bcrypt

	normalized = (code or "").strip().replace(" ", "").replace("-", "").upper()
	return bcrypt.hashpw(normalized.encode("utf-8"), bcrypt.gensalt()).decode("utf-8")


def verify_recovery_code(code: str, stored_hash: str) -> bool:
	"""Constant-time bcrypt verify for a recovery code."""
	import bcrypt

	if not stored_hash or not code:
		return False
	normalized = (code or "").strip().replace(" ", "").replace("-", "").upper()
	try:
		return bool(bcrypt.checkpw(normalized.encode("utf-8"), stored_hash.encode("utf-8")))
	except (ValueError, TypeError):
		return False


def generate_recovery_codes(count: int = 10) -> tuple[list[str], list[str]]:
	"""
	Generate `count` recovery codes.

	Returns (plaintext_codes_for_display_once, bcrypt_hashes_to_store).
	"""
	import secrets

	plain: list[str] = []
	hashes: list[str] = []
	for _ in range(max(1, int(count))):
		raw = secrets.token_hex(8).upper()
		display = "-".join(raw[i : i + 4] for i in range(0, 16, 4))
		plain.append(display)
		hashes.append(hash_recovery_code(display))
	return plain, hashes
