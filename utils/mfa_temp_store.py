# --- START OF FILE utils/mfa_temp_store.py ---
"""
In-memory store for MFA temp tokens issued after password success.
Token -> (username, expiry_ts). Used to complete MFA verify or MFA setup without full JWT.
"""

import secrets
import time
from typing import Optional

# token -> (username, expiry_timestamp)
_store: dict[str, tuple[str, float]] = {}
# 5 minutes
TTL_SECONDS = 300


def create_mfa_temp_token(username: str) -> str:
	token = secrets.token_urlsafe(32)
	_store[token] = (username, time.time() + TTL_SECONDS)
	return token


def consume_mfa_temp_token(token: str) -> Optional[str]:
	"""Return username if token valid and not expired; remove token. Return None otherwise."""
	if not token:
		return None
	entry = _store.pop(token, None)
	if not entry:
		return None
	username, expiry = entry
	if time.time() > expiry:
		return None
	return username


def get_username_for_mfa_temp_token(token: str) -> Optional[str]:
	"""Return username if token valid and not expired; do not remove token (for setup flow)."""
	if not token:
		return None
	entry = _store.get(token)
	if not entry:
		return None
	username, expiry = entry
	if time.time() > expiry:
		_store.pop(token, None)
		return None
	return username
