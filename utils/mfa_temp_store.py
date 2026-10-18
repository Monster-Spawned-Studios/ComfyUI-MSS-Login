# --- START OF FILE utils/mfa_temp_store.py ---
"""
In-memory store for MFA temp tokens issued after password / local / header success.

Tokens are single-use on success, purpose-bound, and bound to client IP + User-Agent.
Failed attempts are capped; expired entries are purged on access.
"""

from __future__ import annotations

import secrets
import time
from dataclasses import dataclass
from typing import Literal

Purpose = Literal["mfa_verify", "mfa_setup", "factor_change"]

TTL_SECONDS = 300
MAX_ATTEMPTS = 3


@dataclass
class MfaTempEntry:
	username: str
	purpose: Purpose
	ip: str
	user_agent: str
	expiry: float
	attempts: int = 0


# token -> entry
_store: dict[str, MfaTempEntry] = {}


def _purge_expired() -> None:
	now = time.time()
	dead = [t for t, e in _store.items() if now > e.expiry]
	for t in dead:
		_store.pop(t, None)


def create_mfa_temp_token(
	username: str,
	*,
	purpose: Purpose,
	ip: str = "",
	user_agent: str = "",
) -> str:
	"""Create a purpose-bound temp token. Purges expired entries first."""
	_purge_expired()
	token = secrets.token_urlsafe(32)
	_store[token] = MfaTempEntry(
		username=username,
		purpose=purpose,
		ip=(ip or "").strip(),
		user_agent=(user_agent or "").strip(),
		expiry=time.time() + TTL_SECONDS,
		attempts=0,
	)
	return token


def peek_mfa_temp_token(
	token: str,
	*,
	purpose: Purpose | None = None,
	ip: str | None = None,
	user_agent: str | None = None,
) -> MfaTempEntry | None:
	"""
	Return entry if valid (not expired, purpose/IP/UA match when provided).
	Does not consume. Does not increment attempts.
	"""
	_purge_expired()
	if not token:
		return None
	entry = _store.get(token)
	if not entry:
		return None
	if time.time() > entry.expiry:
		_store.pop(token, None)
		return None
	if purpose is not None and entry.purpose != purpose:
		return None
	if ip is not None and entry.ip and entry.ip != (ip or "").strip():
		return None
	if user_agent is not None and entry.user_agent and entry.user_agent != (user_agent or "").strip():
		return None
	return entry


def record_mfa_temp_failure(token: str) -> bool:
	"""
	Increment attempt counter. Invalidate (pop) when attempts reach MAX_ATTEMPTS.
	Returns True if token still exists after recording.
	"""
	_purge_expired()
	entry = _store.get(token)
	if not entry:
		return False
	entry.attempts += 1
	if entry.attempts >= MAX_ATTEMPTS:
		_store.pop(token, None)
		return False
	return True


def consume_mfa_temp_token(
	token: str,
	*,
	purpose: Purpose | None = None,
	ip: str | None = None,
	user_agent: str | None = None,
) -> str | None:
	"""
	Consume token on success path only. Returns username if valid and bindings match.
	Does not increment failure attempts (caller records failures separately).
	"""
	entry = peek_mfa_temp_token(token, purpose=purpose, ip=ip, user_agent=user_agent)
	if not entry:
		return None
	_store.pop(token, None)
	return entry.username


def get_username_for_mfa_temp_token(token: str) -> str | None:
	"""Backward-compatible peek (no purpose/IP check). Prefer peek_mfa_temp_token."""
	entry = peek_mfa_temp_token(token)
	return entry.username if entry else None


def clear_mfa_temp_store() -> None:
	"""Test helper: wipe all temp tokens."""
	_store.clear()
