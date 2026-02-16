# --- START OF FILE utils/session_token_store.py ---
"""
Session JWT store: track issued session JWTs by jti for listing and revocation.
Stores (jti, user_id, username, created_at_iso, last_used_at_iso, exp_at_iso?). Blocklist for revoked jtis.
Unused (idle) session tokens are revoked after a configurable timeout to limit exposure if compromised.
"""

import json
from pathlib import Path
from datetime import datetime, timezone, timedelta
from typing import List, Optional

DEFAULT_SESSION_STORE_PATH = "users/session_tokens.json"
DEFAULT_IDLE_REVOKE_MINUTES = 5


def _iso_now() -> str:
	return datetime.now(timezone.utc).isoformat()


def _is_expired(exp_at_iso: Optional[str]) -> bool:
	if not exp_at_iso:
		return False
	try:
		t = datetime.fromisoformat(exp_at_iso.replace("Z", "+00:00"))
		return datetime.now(timezone.utc) >= t
	except Exception:
		return False


def _parse_iso(iso_str: Optional[str]) -> Optional[datetime]:
	if not iso_str:
		return None
	try:
		return datetime.fromisoformat(iso_str.replace("Z", "+00:00"))
	except Exception:
		return None


class SessionTokenStore:
	"""JSON-backed store for session JWT jtis and blocklist. Revokes idle sessions for security."""

	def __init__(self, file_path: str, idle_revoke_minutes: int = DEFAULT_IDLE_REVOKE_MINUTES):
		self._path = Path(file_path)
		self._path.parent.mkdir(parents=True, exist_ok=True)
		self._sessions: List[dict] = []
		self._blocklist: List[str] = []
		self._idle_revoke_minutes = idle_revoke_minutes
		self._load()

	def _load(self) -> None:
		if self._path.exists():
			try:
				with open(self._path, "r", encoding="utf-8") as f:
					data = json.load(f)
				self._sessions = data.get("sessions", [])
				self._blocklist = list(data.get("blocklist", []))
			except (json.JSONDecodeError, OSError):
				self._sessions = []
				self._blocklist = []
		else:
			self._sessions = []
			self._blocklist = []

	def _save(self) -> None:
		with open(self._path, "w", encoding="utf-8") as f:
			json.dump({"sessions": self._sessions, "blocklist": self._blocklist}, f, indent=2)

	def register_session(
		self,
		jti: str,
		user_id: str,
		username: str,
		exp_at_iso: Optional[str] = None,
	) -> None:
		"""Record an issued session JWT. last_used_at_iso is set to now (token is in use at creation)."""
		now = _iso_now()
		self._sessions.append(
			{
				"jti": jti,
				"user_id": user_id,
				"username": username,
				"created_at_iso": now,
				"last_used_at_iso": now,
				"exp_at_iso": exp_at_iso,
			}
		)
		self._save()

	def update_last_used(self, jti: str) -> bool:
		"""Update last_used_at_iso for the given jti. Returns True if updated."""
		for rec in self._sessions:
			if rec.get("jti") == jti:
				rec["last_used_at_iso"] = _iso_now()
				self._save()
				return True
		return False

	def is_revoked(self, jti: str) -> bool:
		"""Return True if jti is in the blocklist."""
		return jti in self._blocklist

	def revoke_idle_sessions(self, idle_minutes: Optional[int] = None) -> int:
		"""
		Revoke all session tokens that have been idle (unused) for longer than idle_minutes.
		Uses self._idle_revoke_minutes if idle_minutes is None.
		Returns the number of sessions revoked.
		"""
		minutes = idle_minutes if idle_minutes is not None else self._idle_revoke_minutes
		cutoff = datetime.now(timezone.utc) - timedelta(minutes=minutes)
		revoked = 0
		for rec in self._sessions:
			jti = rec.get("jti")
			if not jti or jti in self._blocklist:
				continue
			last_used = _parse_iso(rec.get("last_used_at_iso"))
			if last_used is None:
				last_used = _parse_iso(rec.get("created_at_iso"))
			if last_used is not None and last_used < cutoff:
				if jti not in self._blocklist:
					self._blocklist.append(jti)
					revoked += 1
		if revoked:
			self._save()
		return revoked

	def prune_old_sessions(self) -> int:
		"""
		Remove expired and revoked session records from _sessions to prevent unbounded growth.
		Returns the number of records pruned.
		"""
		before = len(self._sessions)
		self._sessions = [
			rec
			for rec in self._sessions
			if rec.get("jti") not in self._blocklist and not _is_expired(rec.get("exp_at_iso"))
		]
		pruned = before - len(self._sessions)
		if pruned:
			self._save()
		return pruned

	def list_sessions_for_user(self, username: str) -> List[dict]:
		"""Return list of session records for username (not revoked, not expired)."""
		out = []
		for rec in self._sessions:
			if rec.get("username") != username:
				continue
			if rec.get("jti") in self._blocklist:
				continue
			if _is_expired(rec.get("exp_at_iso")):
				continue
			out.append(
				{
					"jti": rec.get("jti"),
					"created_at_iso": rec.get("created_at_iso"),
					"last_used_at_iso": rec.get("last_used_at_iso"),
					"exp_at_iso": rec.get("exp_at_iso"),
				}
			)
		return out

	def revoke_session(self, jti: str) -> bool:
		"""Add jti to blocklist. Returns True if added (or already in blocklist)."""
		if jti not in self._blocklist:
			self._blocklist.append(jti)
			self._save()
		return True


_session_store: Optional[SessionTokenStore] = None


def get_session_token_store(
	store_path: str, idle_revoke_minutes: Optional[int] = None
) -> SessionTokenStore:
	"""Get or create the global session token store."""
	global _session_store
	if _session_store is None:
		if idle_revoke_minutes is not None:
			mins = idle_revoke_minutes
		else:
			try:
				from ..constants import SESSION_IDLE_REVOKE_MINUTES
				mins = SESSION_IDLE_REVOKE_MINUTES
			except ImportError:
				mins = DEFAULT_IDLE_REVOKE_MINUTES
		_session_store = SessionTokenStore(store_path, idle_revoke_minutes=mins)
	return _session_store


def reset_session_token_store() -> None:
	"""Reset global store (e.g. after config change)."""
	global _session_store
	_session_store = None
