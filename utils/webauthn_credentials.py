# --- START OF FILE utils/webauthn_credentials.py ---
"""
WebAuthn credential repository (backend-agnostic interface).

SQLite/SQLCipher primary implementation; file backend for contract tests.
"""

from __future__ import annotations

import base64
import json
import os
import secrets
import threading
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Protocol


def b64url_encode(data: bytes) -> str:
	return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def b64url_decode(data: str) -> bytes:
	pad = "=" * (-len(data) % 4)
	return base64.urlsafe_b64decode((data + pad).encode("ascii"))


@dataclass
class WebAuthnCredential:
	id: str
	user_id: str
	credential_id: bytes
	public_key: bytes
	sign_count: int
	transports: list[str]
	aaguid: str
	backup_eligible: bool
	backup_state: bool
	rp_id: str
	name: str
	created_at: float
	last_used_at: float | None
	revoked_at: float | None

	@property
	def synced(self) -> bool:
		return bool(self.backup_eligible)


class WebAuthnCredentialRepo(Protocol):
	def ensure_user_handle(self, user_id: str) -> bytes: ...
	def get_user_handle(self, user_id: str) -> bytes | None: ...
	def get_user_id_for_handle(self, handle: bytes) -> str | None: ...
	def list_for_user(
		self, user_id: str, *, include_revoked: bool = False
	) -> list[WebAuthnCredential]: ...
	def get_by_credential_id(self, credential_id: bytes) -> WebAuthnCredential | None: ...
	def add(self, cred: WebAuthnCredential) -> None: ...
	def update_sign_count(
		self, credential_id: bytes, sign_count: int, *, backup_state: bool | None = None
	) -> None: ...
	def revoke(self, credential_id: bytes, user_id: str) -> bool: ...
	def count_active_for_user(self, user_id: str) -> int: ...


class FileWebAuthnRepo:
	"""Atomic JSON file backend (contract tests / simple deploys)."""

	def __init__(self, path: str):
		self._path = Path(path)
		self._lock = threading.Lock()
		self._path.parent.mkdir(parents=True, exist_ok=True)
		if not self._path.exists():
			self._write({"handles": {}, "credentials": []})

	def _read(self) -> dict:
		with open(self._path, "r", encoding="utf-8") as f:
			return json.load(f)

	def _write(self, data: dict) -> None:
		tmp = self._path.with_suffix(".tmp")
		with open(tmp, "w", encoding="utf-8") as f:
			json.dump(data, f, indent=0)
			f.flush()
			os.fsync(f.fileno())
		os.replace(tmp, self._path)

	def ensure_user_handle(self, user_id: str) -> bytes:
		with self._lock:
			data = self._read()
			h = data["handles"].get(user_id)
			if h:
				return b64url_decode(h)
			raw = secrets.token_bytes(64)
			data["handles"][user_id] = b64url_encode(raw)
			self._write(data)
			return raw

	def get_user_handle(self, user_id: str) -> bytes | None:
		data = self._read()
		h = data["handles"].get(user_id)
		return b64url_decode(h) if h else None

	def get_user_id_for_handle(self, handle: bytes) -> str | None:
		enc = b64url_encode(handle)
		data = self._read()
		for uid, h in data["handles"].items():
			if h == enc:
				return uid
		return None

	def _from_row(self, row: dict) -> WebAuthnCredential:
		return WebAuthnCredential(
			id=row["id"],
			user_id=row["user_id"],
			credential_id=b64url_decode(row["credential_id"]),
			public_key=b64url_decode(row["public_key"]),
			sign_count=int(row.get("sign_count") or 0),
			transports=list(row.get("transports") or []),
			aaguid=row.get("aaguid") or "",
			backup_eligible=bool(row.get("backup_eligible")),
			backup_state=bool(row.get("backup_state")),
			rp_id=row.get("rp_id") or "",
			name=row.get("name") or "",
			created_at=float(row.get("created_at") or 0),
			last_used_at=row.get("last_used_at"),
			revoked_at=row.get("revoked_at"),
		)

	def list_for_user(
		self, user_id: str, *, include_revoked: bool = False
	) -> list[WebAuthnCredential]:
		data = self._read()
		out = []
		for row in data["credentials"]:
			if row["user_id"] != user_id:
				continue
			if not include_revoked and row.get("revoked_at"):
				continue
			out.append(self._from_row(row))
		return out

	def get_by_credential_id(self, credential_id: bytes) -> WebAuthnCredential | None:
		enc = b64url_encode(credential_id)
		data = self._read()
		for row in data["credentials"]:
			if row["credential_id"] == enc:
				return self._from_row(row)
		return None

	def add(self, cred: WebAuthnCredential) -> None:
		with self._lock:
			data = self._read()
			data["credentials"].append(
				{
					"id": cred.id,
					"user_id": cred.user_id,
					"credential_id": b64url_encode(cred.credential_id),
					"public_key": b64url_encode(cred.public_key),
					"sign_count": cred.sign_count,
					"transports": cred.transports,
					"aaguid": cred.aaguid,
					"backup_eligible": cred.backup_eligible,
					"backup_state": cred.backup_state,
					"rp_id": cred.rp_id,
					"name": cred.name,
					"created_at": cred.created_at,
					"last_used_at": cred.last_used_at,
					"revoked_at": cred.revoked_at,
				}
			)
			self._write(data)

	def update_sign_count(
		self, credential_id: bytes, sign_count: int, *, backup_state: bool | None = None
	) -> None:
		enc = b64url_encode(credential_id)
		with self._lock:
			data = self._read()
			for row in data["credentials"]:
				if row["credential_id"] == enc:
					row["sign_count"] = int(sign_count)
					row["last_used_at"] = time.time()
					if backup_state is not None:
						row["backup_state"] = bool(backup_state)
					break
			self._write(data)

	def revoke(self, credential_id: bytes, user_id: str) -> bool:
		enc = b64url_encode(credential_id)
		with self._lock:
			data = self._read()
			for row in data["credentials"]:
				if row["credential_id"] == enc and row["user_id"] == user_id:
					row["revoked_at"] = time.time()
					self._write(data)
					return True
			return False

	def count_active_for_user(self, user_id: str) -> int:
		return len(self.list_for_user(user_id, include_revoked=False))


_repo: WebAuthnCredentialRepo | None = None


def get_webauthn_repo() -> WebAuthnCredentialRepo:
	global _repo
	if _repo is not None:
		return _repo
	try:
		from ..constants import DATA_DIR

		path = os.path.join(DATA_DIR, "webauthn_credentials.json")
	except Exception:
		path = os.path.join(os.path.expanduser("~/.comfyui-mss-login"), "webauthn_credentials.json")
	_repo = FileWebAuthnRepo(path)
	return _repo


def set_webauthn_repo_for_tests(repo: WebAuthnCredentialRepo | None) -> None:
	global _repo
	_repo = repo
