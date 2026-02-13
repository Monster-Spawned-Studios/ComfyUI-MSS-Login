# --- START OF FILE utils/session_token_store.py ---
"""
Session JWT store: track issued session JWTs by jti for listing and revocation.
Stores (jti, user_id, username, created_at_iso, exp_at_iso?). Blocklist for revoked jtis.
"""

import json
from pathlib import Path
from datetime import datetime, timezone
from typing import List, Optional

DEFAULT_SESSION_STORE_PATH = "users/session_tokens.json"


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


class SessionTokenStore:
	"""JSON-backed store for session JWT jtis and blocklist."""

	def __init__(self, file_path: str):
		self._path = Path(file_path)
		self._path.parent.mkdir(parents=True, exist_ok=True)
		self._sessions: List[dict] = []
		self._blocklist: List[str] = []
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
		"""Record an issued session JWT."""
		self._sessions.append(
			{
				"jti": jti,
				"user_id": user_id,
				"username": username,
				"created_at_iso": _iso_now(),
				"exp_at_iso": exp_at_iso,
			}
		)
		self._save()

	def is_revoked(self, jti: str) -> bool:
		"""Return True if jti is in the blocklist."""
		return jti in self._blocklist

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


def get_session_token_store(store_path: str) -> SessionTokenStore:
	"""Get or create the global session token store."""
	global _session_store
	if _session_store is None:
		_session_store = SessionTokenStore(store_path)
	return _session_store


def reset_session_token_store() -> None:
	"""Reset global store (e.g. after config change)."""
	global _session_store
	_session_store = None
