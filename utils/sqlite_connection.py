# --- START OF FILE utils/sqlite_connection.py ---
"""
Open SQLite connections with optional SQLCipher encryption.
When encryption_level is set, uses SECRET_KEY-derived key via Argon2id; requires pysqlcipher3.
Do not log secret_key or the derived key.
"""

import os
from pathlib import Path
from typing import Any, Optional

_sqlcipher_available: Optional[bool] = None


def _check_sqlcipher() -> bool:
	global _sqlcipher_available
	if _sqlcipher_available is not None:
		return _sqlcipher_available
	try:
		import pysqlcipher3

		_sqlcipher_available = True
	except ImportError:
		_sqlcipher_available = False
	return _sqlcipher_available


def open_sqlite(
	path: str,
	secret_key: str,
	encryption_level: str,
	check_same_thread: bool = False,
) -> Any:
	"""
	Open a SQLite connection. If encryption_level is non-empty, use SQLCipher with
	key derived from secret_key (Argon2id). Otherwise use standard sqlite3.
	Raises RuntimeError if encryption is requested but pysqlcipher3 is not installed.
	"""
	path = str(Path(path).resolve())
	os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
	if not (encryption_level or "").strip():
		import sqlite3

		return sqlite3.connect(path, check_same_thread=check_same_thread)
	if not _check_sqlcipher():
		raise RuntimeError(
			"SQLite encryption is enabled but pysqlcipher3 is not installed. "
			"Install with: pip install pysqlcipher3 (requires SQLCipher system library). "
			"Or set encryption_level to empty in users_db config to use unencrypted SQLite."
		)
	from .db_key_derivation import derive_db_key

	key_bytes = derive_db_key(secret_key, encryption_level)
	key_hex = key_bytes.hex()
	import pysqlcipher3

	conn = pysqlcipher3.connect(path, check_same_thread=check_same_thread)
	# Pass raw key as hex so SQLCipher uses it directly (no PBKDF2)
	conn.execute(f"PRAGMA key = x'{key_hex}'")
	return conn
