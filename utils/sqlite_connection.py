# --- START OF FILE utils/sqlite_connection.py ---
"""
Open SQLite connections with optional SQLCipher encryption.
When encryption_level is set, uses SECRET_KEY-derived key via Argon2id; requires sqlcipher3.
Do not log secret_key or the derived key.
"""

import os
import shutil
from pathlib import Path
from typing import Any

_sqlcipher_available: bool | None = None


def _check_sqlcipher() -> bool:
	global _sqlcipher_available
	if _sqlcipher_available is not None:
		return _sqlcipher_available
	try:
		import sqlcipher3

		_sqlcipher_available = True
	except ImportError:
		_sqlcipher_available = False
	return _sqlcipher_available


def is_plain_sqlite(path: str) -> bool:
	"""Check if file starts with the standard SQLite header."""
	if not os.path.isfile(path) or os.path.getsize(path) == 0:
		return False
	try:
		with open(path, "rb") as f:
			header = f.read(16)
		return header.startswith(b"SQLite format 3\x00")
	except OSError:
		return False


def _migrate_to_encrypted(path: str, key_hex: str) -> None:
	"""Migrate a plain SQLite database to an encrypted SQLCipher database."""
	import sqlcipher3

	backup_path = path + ".unencrypted.bak"
	if not os.path.exists(backup_path):
		try:
			shutil.copy2(path, backup_path)
		except OSError:
			pass

	tmp_enc = path + ".encrypting.tmp"
	if os.path.exists(tmp_enc):
		os.remove(tmp_enc)

	try:
		conn = sqlcipher3.connect(tmp_enc)
		conn.execute(f"PRAGMA key = \"x'{key_hex}'\"")
		conn.execute(f"ATTACH DATABASE '{path}' AS plaintext KEY ''")
		conn.execute("SELECT sqlcipher_export('main', 'plaintext')")
		conn.execute("DETACH DATABASE plaintext")
		conn.commit()
		conn.close()
		shutil.move(tmp_enc, path)
	except Exception as e:
		if os.path.exists(tmp_enc):
			os.remove(tmp_enc)
		raise RuntimeError(f"Failed to encrypt existing SQLite database: {e}") from e


def _migrate_to_plain(path: str, secret_key: str) -> bool:
	"""If path is encrypted and we can decrypt it with secret_key, export to plain."""
	if not _check_sqlcipher() or is_plain_sqlite(path):
		return False

	import sqlcipher3
	from .db_key_derivation import derive_db_key

	for level in ("secure", "standard", "low"):
		try:
			key_bytes = derive_db_key(secret_key, level)
			key_hex = key_bytes.hex()
			test_conn = sqlcipher3.connect(path)
			test_conn.execute(f"PRAGMA key = \"x'{key_hex}'\"")
			test_conn.execute("SELECT count(*) FROM sqlite_master")
			test_conn.close()
		except Exception:
			continue

		backup_path = path + ".encrypted.bak"
		if not os.path.exists(backup_path):
			try:
				shutil.copy2(path, backup_path)
			except OSError:
				pass

		tmp_plain = path + ".decrypting.tmp"
		if os.path.exists(tmp_plain):
			os.remove(tmp_plain)

		try:
			conn = sqlcipher3.connect(path)
			conn.execute(f"PRAGMA key = \"x'{key_hex}'\"")
			conn.execute(f"ATTACH DATABASE '{tmp_plain}' AS plaintext KEY ''")
			conn.execute("SELECT sqlcipher_export('plaintext')")
			conn.execute("DETACH DATABASE plaintext")
			conn.commit()
			conn.close()
			shutil.move(tmp_plain, path)
			return True
		except Exception as e:
			if os.path.exists(tmp_plain):
				os.remove(tmp_plain)
			raise RuntimeError(f"Failed to decrypt SQLite database: {e}") from e

	return False


def open_sqlite(
	path: str, secret_key: str, encryption_level: str, check_same_thread: bool = False
) -> Any:
	"""
	Open a SQLite connection. If encryption_level is non-empty, use SQLCipher with
	key derived from secret_key (Argon2id). Otherwise use standard sqlite3.
	Safely migrates between plain and encrypted when needed.
	Raises RuntimeError if encryption is requested but sqlcipher3 is not installed.
	"""
	path = str(Path(path).resolve())
	os.makedirs(os.path.dirname(path) or ".", exist_ok=True)

	enc_clean = (encryption_level or "").strip().lower()

	if not enc_clean:
		if os.path.isfile(path) and os.path.getsize(path) > 0 and not is_plain_sqlite(path):
			_migrate_to_plain(path, secret_key)
		import sqlite3

		return sqlite3.connect(path, check_same_thread=check_same_thread)

	if not _check_sqlcipher():
		raise RuntimeError(
			"SQLite encryption is enabled but sqlcipher3 is not installed. "
			"Install with: pip install sqlcipher3 (requires SQLCipher system library). "
			"Or set encryption_level to empty in users_db config to use unencrypted SQLite."
		)

	from .db_key_derivation import derive_db_key

	key_bytes = derive_db_key(secret_key, enc_clean)
	key_hex = key_bytes.hex()

	if is_plain_sqlite(path):
		_migrate_to_encrypted(path, key_hex)

	import sqlcipher3

	conn = sqlcipher3.connect(path, check_same_thread=check_same_thread)
	conn.execute(f"PRAGMA key = \"x'{key_hex}'\"")
	# Force key validation to ensure decryption succeeds
	conn.execute("SELECT count(*) FROM sqlite_master")
	return conn
