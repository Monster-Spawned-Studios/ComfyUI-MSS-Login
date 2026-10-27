#!/usr/bin/env python3
"""
Emergency Unlock CLI for ComfyUI-MSS-Login (Host / Docker Exec).

Usage:
    python scripts/emergency_unlock.py --unlock-owner
    python scripts/emergency_unlock.py --create-trigger
    python scripts/emergency_unlock.py --reset-mfa <username>
    python scripts/emergency_unlock.py --list-users
"""

import argparse
import os
import sys

# Ensure repo root is on sys.path
repo_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if repo_root not in sys.path:
	sys.path.insert(0, repo_root)

# Stub ComfyUI modules if running standalone
for _mod in ("folder_paths", "execution", "server"):
	if _mod not in sys.modules:
		import types

		sys.modules[_mod] = types.ModuleType(_mod)
sys.modules["execution"].MAXIMUM_HISTORY_SIZE = 1000
sys.modules["execution"].PromptQueue = type("PromptQueue", (), {})
sys.modules["server"].PromptServer = type("PromptServer", (), {})

from constants import DATA_DIR, LEGACY_USERS_JSON_PATH, SECRET_KEY, USERS_DB_CONFIG
from utils.emergency_recovery import get_emergency_unlock_path
from utils.users_db import UsersDB


def main():
	parser = argparse.ArgumentParser(
		description="Emergency unlock and MFA recovery tool for ComfyUI-MSS-Login."
	)
	parser.add_argument(
		"--secret",
		type=str,
		default="",
		help="Database secret key (SECRET_KEY). If omitted, read from SECRET_KEY env or prompted.",
	)
	group = parser.add_mutually_exclusive_group(required=True)
	group.add_argument(
		"--unlock-owner",
		action="store_true",
		help="Directly reset MFA and lockout counters for the owner account.",
	)
	group.add_argument(
		"--create-trigger",
		action="store_true",
		help="Create the .emergency_unlock_owner trigger file in DATA_DIR for automatic daemon recovery.",
	)
	group.add_argument(
		"--reset-mfa", metavar="USERNAME", type=str, help="Reset MFA for a specific username."
	)
	group.add_argument(
		"--list-users", action="store_true", help="List all users, roles, and MFA statuses."
	)

	args = parser.parse_args()

	import getpass
	import hmac

	provided_secret = (
		args.secret or os.environ.get("MSS_LOGIN_SECRET_KEY") or os.environ.get("SECRET_KEY") or ""
	).strip()
	if not provided_secret:
		if sys.stdin.isatty():
			try:
				provided_secret = getpass.getpass("Enter MSS-Login database SECRET_KEY: ").strip()
			except (KeyboardInterrupt, EOFError):
				print("\nAborted.", file=sys.stderr)
				return 1
		else:
			print(
				"[ERROR] Database secret is required. Provide via --secret or SECRET_KEY env var.",
				file=sys.stderr,
			)
			return 1

	if not SECRET_KEY or not hmac.compare_digest(provided_secret, str(SECRET_KEY).strip()):
		print("[ERROR] Invalid database secret provided.", file=sys.stderr)
		return 1

	if args.create_trigger:
		trigger_path = get_emergency_unlock_path()
		try:
			with open(trigger_path, "w", encoding="utf-8") as f:
				f.write(f"{provided_secret}\n")
			try:
				os.chmod(trigger_path, 0o600)
			except OSError:
				pass
			print(f"[OK] Emergency trigger created at: {trigger_path}")
			print("MSS-Login will automatically reset the owner MFA on next request or restart.")
			return 0
		except Exception as e:
			print(f"[ERROR] Could not create trigger file {trigger_path}: {e}", file=sys.stderr)
			return 1

	try:
		users_db = UsersDB(USERS_DB_CONFIG, SECRET_KEY, LEGACY_USERS_JSON_PATH)
		users_db.load_users()
	except Exception as e:
		print(f"[ERROR] Failed to load users database: {e}", file=sys.stderr)
		return 1

	if args.list_users:
		users_db.load_users()
		users = list(users_db.users.values())
		print(f"Users found in {DATA_DIR}: ({len(users)})")
		print(f"{'Username':<20} {'Groups':<25} {'Admin':<8} {'MFA Active'}")
		print("-" * 65)
		for u in users:
			uname = u.get("username", "")
			groups_str = ",".join(u.get("groups", []))
			adm = "Yes" if u.get("is_admin") else "No"
			mfa = "Yes" if users_db.get_mfa_enabled(uname) else "No"
			print(f"{uname:<20} {groups_str:<25} {adm:<8} {mfa}")
		return 0

	if args.unlock_owner:
		owner = users_db.get_owner_username()
		if not owner:
			print("[ERROR] No owner account found in database!", file=sys.stderr)
			return 1
		print(f"[*] Found owner account: {owner}")
		ok = users_db.reset_mfa_for_user(owner)
		if not ok:
			print(f"[ERROR] Failed to reset MFA for owner {owner}!", file=sys.stderr)
			return 1
		# Clear lockout state file if present
		try:
			timeout_file = os.path.join(DATA_DIR, "data", "login_timeout_state.json")
			if os.path.isfile(timeout_file):
				import json

				with open(timeout_file, "r") as f:
					state = json.load(f)
				prefix = f"{owner.lower()}:"
				state["failed_attempts"] = {
					k: v
					for k, v in state.get("failed_attempts", {}).items()
					if not k.startswith(prefix)
				}
				state["timeout_end_time"] = {
					k: v
					for k, v in state.get("timeout_end_time", {}).items()
					if not k.startswith(prefix)
				}
				with open(timeout_file, "w") as f:
					json.dump(state, f, indent=2)
				print(f"[*] Cleared persistent timeout lockouts for {owner}")
		except Exception as e:
			print(f"[!] Warning: Could not clear timeout state file: {e}")

		print(f"[SUCCESS] Owner account '{owner}' has been unlocked and MFA reset!")
		return 0

	if args.reset_mfa:
		uname = args.reset_mfa.strip()
		uid, u = users_db.get_user(uname)
		if not uid:
			print(f"[ERROR] User '{uname}' not found!", file=sys.stderr)
			return 1
		ok = users_db.reset_mfa_for_user(uname)
		if ok:
			print(f"[SUCCESS] MFA reset successfully for user '{uname}'.")
			return 0
		else:
			print(f"[ERROR] Failed to reset MFA for user '{uname}'.", file=sys.stderr)
			return 1

	return 0


if __name__ == "__main__":
	sys.exit(main())
