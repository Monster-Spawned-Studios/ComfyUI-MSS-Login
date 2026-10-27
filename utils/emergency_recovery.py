"""
Emergency recovery for owner account lockout (AUDIT-013 / MFA emergency override).

Triggers when a host administrator creates:
    DATA_DIR/.emergency_unlock_owner

Upon detection (startup or request preflight):
1. Identifies the primary owner username.
2. Disables MFA and resets TOTP secrets for that owner.
3. Clears any rate limit / lockout backoff in timeout.py.
4. Removes the trigger file.
5. Emits an audit log and notification.
"""

from __future__ import annotations

import logging
import os
from typing import Any

try:
	from .data_dir import get_data_dir
except ImportError:
	from utils.data_dir import get_data_dir

logger = logging.getLogger(__name__)

EMERGENCY_UNLOCK_TRIGGER_FILENAME = ".emergency_unlock_owner"


def get_emergency_unlock_path() -> str:
	"""Return the absolute path to the emergency owner unlock trigger file."""
	return os.path.join(get_data_dir(), EMERGENCY_UNLOCK_TRIGGER_FILENAME)


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
