# --- START OF FILE utils/webauthn_challenge_store.py ---
"""Server-side WebAuthn ceremony challenges keyed by random ceremony id (cookie)."""

from __future__ import annotations

import secrets
import time
from dataclasses import dataclass
from typing import Literal

CeremonyType = Literal["register", "authenticate", "passwordless"]

TTL_SECONDS = 180  # 3 minutes (within 2–5)


@dataclass
class CeremonyChallenge:
	challenge: bytes
	ceremony_type: CeremonyType
	user_id: str | None
	username: str | None
	rp_id: str
	expected_origin: str
	expiry: float


_store: dict[str, CeremonyChallenge] = {}


def _purge() -> None:
	now = time.time()
	for k, v in list(_store.items()):
		if now > v.expiry:
			_store.pop(k, None)


def create_ceremony(
	*,
	challenge: bytes,
	ceremony_type: CeremonyType,
	rp_id: str,
	expected_origin: str,
	user_id: str | None = None,
	username: str | None = None,
) -> str:
	_purge()
	cid = secrets.token_urlsafe(24)
	_store[cid] = CeremonyChallenge(
		challenge=challenge,
		ceremony_type=ceremony_type,
		user_id=user_id,
		username=username,
		rp_id=rp_id,
		expected_origin=expected_origin,
		expiry=time.time() + TTL_SECONDS,
	)
	return cid


def consume_ceremony(ceremony_id: str, *, ceremony_type: CeremonyType | None = None) -> CeremonyChallenge | None:
	"""Atomic single consume."""
	_purge()
	entry = _store.pop(ceremony_id or "", None)
	if not entry:
		return None
	if time.time() > entry.expiry:
		return None
	if ceremony_type is not None and entry.ceremony_type != ceremony_type:
		return None
	return entry


def clear_ceremonies() -> None:
	_store.clear()
