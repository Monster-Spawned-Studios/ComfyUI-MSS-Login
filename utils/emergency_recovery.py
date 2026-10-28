"""
Emergency recovery for account 2FA/MFA lockout (AUDIT-013 / MFA emergency override).

Supports two recovery paths:
1. Automated tool-driven recovery (`scripts/emergency_unlock.py`):
   - Enabled by an administrator (`emergency_recovery.enabled = true` or `MSS_LOGIN_EMERGENCY_RECOVERY_ENABLED=1`)
     when the secret variable (`SECRET_KEY` or `MSS_LOGIN_SECRET_KEY`) is present in system env vars.
   - Strictly gated by mandatory Owner approval (`status = "pending_owner_approval"` -> Owner approves)
     so that the secret encryption environment variable is NEVER read or written unless and until
     the Owner explicitly approves deploying the emergency recovery.
2. Host-only trigger file (`DATA_DIR/.emergency_unlock_owner`):
   - Triggers when a host administrator creates `DATA_DIR/.emergency_unlock_owner` containing the valid secret.
"""

from __future__ import annotations

import json
import logging
import os
import subprocess
import sys
import uuid
from datetime import UTC, datetime
from typing import Any

try:
	from .data_dir import get_data_dir
except ImportError:
	from utils.data_dir import get_data_dir

logger = logging.getLogger(__name__)

EMERGENCY_UNLOCK_TRIGGER_FILENAME = ".emergency_unlock_owner"
EMERGENCY_RECOVERY_REQUESTS_FILENAME = "emergency_recovery_requests.json"
_SECRET_ENV_KEYS = ("MSS_LOGIN_SECRET_KEY", "SECRET_KEY")


def _extension_root() -> str:
	return os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def get_emergency_unlock_path() -> str:
	"""Return the absolute path to the emergency owner unlock trigger file."""
	return os.path.join(get_data_dir(), EMERGENCY_UNLOCK_TRIGGER_FILENAME)


def get_emergency_requests_path() -> str:
	"""Return the absolute path to the pending emergency recovery requests store."""
	data_subdir = os.path.join(get_data_dir(), "data")
	os.makedirs(data_subdir, exist_ok=True)
	return os.path.join(data_subdir, EMERGENCY_RECOVERY_REQUESTS_FILENAME)


def has_secret_env_var_configured(env: Any = None) -> bool:
	"""Check whether a secret variable name exists in the environment WITHOUT reading its value."""
	target_env = env if env is not None else os.environ
	env_keys = set(target_env.keys())
	return bool(env_keys.intersection(_SECRET_ENV_KEYS))


def is_emergency_recovery_enabled(env: Any = None) -> bool:
	"""Return True if the administrator has enabled emergency recovery setup."""
	target_env = env if env is not None else os.environ
	if "MSS_LOGIN_EMERGENCY_RECOVERY_ENABLED" in target_env.keys():
		raw = str(target_env.get("MSS_LOGIN_EMERGENCY_RECOVERY_ENABLED", "")).strip().lower()
		if raw in ("1", "true", "yes", "on"):
			return True
		if raw in ("0", "false", "no", "off"):
			return False

	root = _extension_root()
	for cfg_name in ("config.json", "config.defaults.json"):
		cfg_path = os.path.join(root, cfg_name)
		if os.path.isfile(cfg_path):
			try:
				with open(cfg_path, "r", encoding="utf-8") as f:
					cfg = json.load(f)
				er = cfg.get("emergency_recovery")
				if isinstance(er, dict) and "enabled" in er:
					return bool(er.get("enabled"))
			except Exception:
				continue
	return False


def set_emergency_recovery_enabled(enabled: bool) -> bool:
	"""Persist `emergency_recovery.enabled` in config.json without touching secret env vars."""
	root = _extension_root()
	cfg_path = os.path.join(root, "config.json")
	defaults_path = os.path.join(root, "config.defaults.json")
	data: dict[str, Any] = {}
	for path in (defaults_path, cfg_path):
		if os.path.isfile(path):
			try:
				with open(path, "r", encoding="utf-8") as f:
					loaded = json.load(f)
				if isinstance(loaded, dict):
					data.update(loaded)
			except Exception:
				pass
	er = data.get("emergency_recovery")
	if not isinstance(er, dict):
		er = {}
	er["enabled"] = bool(enabled)
	er["require_owner_approval"] = True
	data["emergency_recovery"] = er
	try:
		with open(cfg_path, "w", encoding="utf-8") as f:
			json.dump(data, f, indent="\t")
		return True
	except Exception as exc:
		logger.error("[emergency_recovery] Failed to save config.json: %s", exc)
		return False


def _load_requests() -> list[dict[str, Any]]:
	path = get_emergency_requests_path()
	if not os.path.isfile(path):
		return []
	try:
		with open(path, "r", encoding="utf-8") as f:
			data = json.load(f)
		return data if isinstance(data, list) else []
	except Exception:
		return []


def _save_requests(requests_list: list[dict[str, Any]]) -> None:
	path = get_emergency_requests_path()
	with open(path, "w", encoding="utf-8") as f:
		json.dump(requests_list, f, indent=2)
	try:
		os.chmod(path, 0o600)
	except OSError:
		pass


def list_emergency_recovery_requests(status: str | None = None) -> list[dict[str, Any]]:
	"""Return stored emergency recovery requests (never reads or writes secret env vars)."""
	items = _load_requests()
	if status:
		return [r for r in items if r.get("status") == status]
	return items


def create_emergency_recovery_request(
	target_username: str, requested_by: str, reason: str = "", *, env: Any = None
) -> dict[str, Any]:
	"""Queue an emergency 2FA/MFA recovery request awaiting Owner approval.

	Does NOT read or write the secret encryption environment variable.
	"""
	target_username = (target_username or "").strip()
	requested_by = (requested_by or "").strip() or "admin"
	if not target_username:
		raise ValueError("target_username is required")

	if not is_emergency_recovery_enabled(env=env):
		raise PermissionError("Emergency recovery setup is not enabled by the administrator.")
	if not has_secret_env_var_configured(env=env):
		raise RuntimeError(
			"Emergency recovery requires SECRET_KEY (or MSS_LOGIN_SECRET_KEY) to be present in system environment variables."
		)

	requests_list = _load_requests()
	for existing in requests_list:
		if (
			existing.get("target_username", "").lower() == target_username.lower()
			and existing.get("status") == "pending_owner_approval"
		):
			return existing

	req: dict[str, Any] = {
		"request_id": str(uuid.uuid4()),
		"target_username": target_username,
		"requested_by": requested_by,
		"reason": (reason or "").strip(),
		"status": "pending_owner_approval",
		"owner_approved": False,
		"created_at": datetime.now(UTC).isoformat(),
		"approved_by": None,
		"approved_at": None,
	}
	requests_list.append(req)
	_save_requests(requests_list)

	logger.warning(
		"[emergency_recovery] Emergency MFA recovery requested for user '%s' by '%s' (request_id=%s). Awaiting Owner approval.",
		target_username,
		requested_by,
		req["request_id"],
	)
	return req


def reject_emergency_recovery_request(
	request_id: str, owner_username: str, *, is_owner_verified: bool = False
) -> dict[str, Any]:
	"""Owner-only: reject a pending emergency recovery request without touching secret env vars."""
	if not is_owner_verified:
		raise PermissionError("Only the owner can reject emergency recovery requests.")
	requests_list = _load_requests()
	for req in requests_list:
		if req.get("request_id") == request_id:
			if req.get("status") != "pending_owner_approval":
				raise ValueError(f"Request {request_id} is not pending approval.")
			req["status"] = "rejected"
			req["owner_approved"] = False
			req["rejected_by"] = owner_username
			req["rejected_at"] = datetime.now(UTC).isoformat()
			_save_requests(requests_list)
			return req
	raise KeyError(f"Emergency recovery request not found: {request_id}")


def approve_and_execute_emergency_recovery(
	request_id: str,
	owner_username: str,
	*,
	is_owner_verified: bool = False,
	users_db_instance: Any = None,
	timeout_instance: Any = None,
	env: Any = None,
) -> dict[str, Any]:
	"""Owner-only: approve a pending emergency recovery request and automatically run `scripts/emergency_unlock.py`.

	The secret encryption variable is read ONLY here after `is_owner_verified` is confirmed,
	and is never written or modified in the environment.
	"""
	if not is_owner_verified:
		raise PermissionError("Owner approval is required before deploying emergency recovery.")

	if users_db_instance is not None:
		_uid, urec = users_db_instance.get_user(owner_username)
		groups = [str(g).lower() for g in (urec or {}).get("groups", [])]
		if "owner" not in groups:
			raise PermissionError("Caller is not in the owner group.")

	if not is_emergency_recovery_enabled(env=env):
		raise PermissionError("Emergency recovery setup is not enabled.")

	requests_list = _load_requests()
	target_req: dict[str, Any] | None = None
	for req in requests_list:
		if req.get("request_id") == request_id:
			target_req = req
			break

	if target_req is None:
		raise KeyError(f"Emergency recovery request not found: {request_id}")
	if target_req.get("status") != "pending_owner_approval":
		raise ValueError(f"Request {request_id} is already in status '{target_req.get('status')}'.")

	target_env = env if env is not None else os.environ
	if not has_secret_env_var_configured(env=target_env):
		raise RuntimeError("System environment secret variable is not configured.")

	# Owner approval verified: now (and only now) read the secret from environment variables
	secret_val = ""
	for key in _SECRET_ENV_KEYS:
		if key in target_env.keys():
			val = str(target_env[key] or "").strip()
			if val:
				secret_val = val
				break

	if not secret_val:
		raise RuntimeError("System environment secret variable is empty.")

	target_username = str(target_req.get("target_username") or "").strip()
	script_path = os.path.join(_extension_root(), "scripts", "emergency_unlock.py")
	if not os.path.isfile(script_path):
		raise FileNotFoundError(f"Emergency recovery script not found: {script_path}")

	cmd = [sys.executable, script_path, "--secret", secret_val, "--reset-mfa", target_username]
	child_env = os.environ.copy()
	child_env["MSS_LOGIN_DATA_DIR"] = get_data_dir()

	proc = subprocess.run(
		cmd,
		cwd=_extension_root(),
		env=child_env,
		capture_output=True,
		text=True,
		timeout=30,
		check=False,
	)

	# Also sync in-memory instances if provided (or if DB uses an ephemeral/instance secret in tests)
	in_memory_ok = False
	if users_db_instance is not None:
		try:
			users_db_instance.load_users()
			in_memory_ok = bool(users_db_instance.reset_mfa_for_user(target_username))
		except Exception as exc:
			logger.debug("[emergency_recovery] In-memory users_db sync note: %s", exc)

	if proc.returncode != 0 and not in_memory_ok:
		detail = (proc.stderr or proc.stdout or "").strip() or f"exit {proc.returncode}"
		raise RuntimeError(f"emergency_unlock.py failed: {detail}")

	if timeout_instance is not None and hasattr(timeout_instance, "clear_all_for_user"):
		try:
			timeout_instance.clear_all_for_user(target_username)
		except Exception:
			pass

	target_req["owner_approved"] = True
	target_req["status"] = "approved_and_executed"
	target_req["approved_by"] = owner_username
	target_req["approved_at"] = datetime.now(UTC).isoformat()
	_save_requests(requests_list)

	logger.warning(
		"[emergency_recovery] Owner '%s' approved and executed emergency MFA unlock for user '%s' (request_id=%s).",
		owner_username,
		target_username,
		request_id,
	)
	try:
		try:
			from .notifications import send_notification
		except ImportError:
			from utils.notifications import send_notification

		send_notification(
			"security_alert",
			"MSS-Login: Emergency MFA Recovery Executed",
			f"Owner '{owner_username}' approved and deployed emergency MFA unlock for '{target_username}'.",
		)
	except Exception:
		pass

	return target_req


def check_and_execute_emergency_recovery(
	users_db_instance: Any = None, timeout_instance: Any = None, trigger_path: str | None = None
) -> bool:
	"""
	Check for the presence of the emergency trigger file and execute recovery if found.
	Returns True if an emergency unlock was executed, False otherwise.
	"""
	trigger_path = trigger_path or get_emergency_unlock_path()
	if not os.path.isfile(trigger_path):
		return False

	trigger_secret = ""
	try:
		with open(trigger_path, "r", encoding="utf-8") as f:
			trigger_secret = f.read().strip()
	except Exception:
		trigger_secret = ""

	try:
		from ..constants import LEGACY_USERS_JSON_PATH, SECRET_KEY, USERS_DB_CONFIG
		from .users_db import UsersDB
	except (ImportError, ValueError):
		from constants import LEGACY_USERS_JSON_PATH, SECRET_KEY, USERS_DB_CONFIG
		from utils.users_db import UsersDB

	import hmac

	expected_secret = None
	if users_db_instance is not None and getattr(users_db_instance, "_secret_key", None):
		expected_secret = str(users_db_instance._secret_key).strip()
	elif SECRET_KEY:
		expected_secret = str(SECRET_KEY).strip()

	if (
		not trigger_secret
		or not expected_secret
		or not hmac.compare_digest(trigger_secret, expected_secret)
	):
		logger.error(
			"[emergency_recovery] Security alert: Emergency trigger file %s present with invalid database secret! Ignoring unlock.",
			trigger_path,
		)
		try:
			os.unlink(trigger_path)
		except Exception:
			pass
		return False

	logger.warning(
		"[emergency_recovery] Emergency unlock trigger detected at %s with valid secret! Initiating owner unlock.",
		trigger_path,
	)

	try:
		if users_db_instance is None:
			users_db_instance = UsersDB(USERS_DB_CONFIG, SECRET_KEY, LEGACY_USERS_JSON_PATH)

		owner_username = users_db_instance.get_owner_username()
		if not owner_username:
			logger.error(
				"[emergency_recovery] Emergency unlock failed: No owner account found in database!"
			)
		else:
			reset_ok = users_db_instance.reset_mfa_for_user(owner_username)
			if reset_ok:
				logger.warning(
					"[emergency_recovery] MFA successfully reset for owner %s.", owner_username
				)
			else:
				logger.error(
					"[emergency_recovery] Failed to reset MFA for owner %s.", owner_username
				)

			if timeout_instance is not None and hasattr(timeout_instance, "clear_all_for_user"):
				timeout_instance.clear_all_for_user(owner_username)
				logger.info(
					"[emergency_recovery] Cleared lockout counters for owner %s.", owner_username
				)

			try:
				try:
					from .notifications import send_notification
				except ImportError:
					from utils.notifications import send_notification

				send_notification(
					"security_alert",
					"MSS-Login: Owner Emergency Unlock Triggered",
					f"Host emergency unlock file detected. MFA was reset and lockouts cleared for owner '{owner_username}'.",
				)
			except Exception as notify_err:
				logger.debug("[emergency_recovery] Notification dispatch error: %s", notify_err)

	finally:
		try:
			os.unlink(trigger_path)
			logger.info("[emergency_recovery] Removed emergency trigger file: %s", trigger_path)
		except Exception as unlink_err:
			logger.error(
				"[emergency_recovery] Could not remove trigger file %s: %s",
				trigger_path,
				unlink_err,
			)

	return True
