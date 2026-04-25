# --- START OF FILE utils/encryption.py ---
"""
Encryption helper for sensitive data at rest.
Uses SECRET_KEY to derive an encryption key via HKDF; Fernet for symmetric encryption.
Do not log or expose SECRET_KEY or derived keys.
"""

import hashlib
import hmac
from typing import Optional

# Fernet is part of cryptography package
try:
	from cryptography.fernet import Fernet
	from cryptography.hazmat.primitives import hashes
	from cryptography.hazmat.primitives.kdf.hkdf import HKDF
	from cryptography.hazmat.backends import default_backend

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
	import base64

	return base64.urlsafe_b64encode(key_bytes)


def encrypt_value(secret_key: str, plaintext: str) -> Optional[str]:
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


def decrypt_value(secret_key: str, ciphertext: str) -> Optional[str]:
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


def hash_backup_code(backup_code: str) -> str:
	"""Hash a backup code with SHA-256 for constant-time comparison. Never store plain backup codes."""
	return hashlib.sha256(backup_code.encode("utf-8")).hexdigest()


def verify_backup_code(backup_code: str, stored_hash: str) -> bool:
	"""Constant-time comparison of backup code against stored hash."""
	if not stored_hash or not backup_code:
		return False
	computed = hash_backup_code(backup_code)
	return hmac.compare_digest(computed, stored_hash)
