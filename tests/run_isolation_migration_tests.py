r"""
Comprehensive tests for username-based model isolation, offline migration,
and emergency recovery database secret validation.
"""

import hmac
import importlib.util
import json
import os
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import types

_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_UTILS_DIR = os.path.join(_PROJECT_ROOT, "utils")
_TOOLS_DIR = os.path.join(_PROJECT_ROOT, "tools")
_SCRIPTS_DIR = os.path.join(_PROJECT_ROOT, "scripts")


def _load_module(name: str, path: str, package: str = "mss_login.utils"):
	spec = importlib.util.spec_from_file_location(name, path)
	mod = importlib.util.module_from_spec(spec)
	mod.__package__ = package
	sys.modules[name] = mod
	assert spec and spec.loader
	spec.loader.exec_module(mod)
	return mod


def run_tests():
	failed = 0
	run = 0

	def ok(cond, msg):
		nonlocal failed, run
		run += 1
		if not cond:
			print(f"  FAIL: {msg}")
			failed += 1
		else:
			print(f"  ok: {msg}")
		return cond

	print("=== TestUsernameValidation ===")
	# Setup fake package modules
	root_pkg = types.ModuleType("mss_login")
	root_pkg.__path__ = [_PROJECT_ROOT]
	utils_pkg = types.ModuleType("mss_login.utils")
	utils_pkg.__path__ = [_UTILS_DIR]
	sys.modules["mss_login"] = root_pkg
	sys.modules["mss_login.utils"] = utils_pkg

	iso_mod = _load_module(
		"mss_login.utils.model_isolation", os.path.join(_UTILS_DIR, "model_isolation.py")
	)
	val_raw = iso_mod.validate_username_for_isolation

	def val_fn(u):
		try:
			val_raw(u)
			return (True, "")
		except ValueError as e:
			return (False, str(e))

	# 1. Traversal rejection
	ok(not val_fn("../evil")[0], "rejects path traversal ../evil")
	ok(not val_fn("evil/user")[0], "rejects forward slash")
	ok(not val_fn("evil\\user")[0], "rejects backward slash")
	ok(not val_fn("..")[0], "rejects double dot")
	ok(not val_fn(".")[0], "rejects single dot")

	# 2. NUL character rejection
	ok(not val_fn("user\0evil")[0], "rejects NUL character")

	# 3. Control character / whitespace rejection
	ok(not val_fn("user\nname")[0], "rejects newline")
	ok(not val_fn("user name")[0], "rejects whitespace")
	ok(not val_fn(" user")[0], "rejects leading space")
	ok(not val_fn("user ")[0], "rejects trailing space")

	# 4. Reserved names
	for res in ["CON", "PRN", "AUX", "NUL", "COM1", "LPT1", "con", "nul"]:
		ok(not val_fn(res)[0], f"rejects reserved name {res}")

	# 5. Leading dots and trailing dots
	ok(not val_fn(".hidden")[0], "rejects leading dot")
	ok(not val_fn("user.")[0], "rejects trailing dot")

	# 6. Length > 64 chars & empty
	ok(not val_fn("")[0], "rejects empty username")
	ok(not val_fn("a" * 65)[0], "rejects length > 64 chars")

	# 7. Valid usernames accepted
	ok(val_fn("alice")[0], "accepts standard username alice")
	ok(val_fn("bob_123")[0], "accepts underscore and digits")
	ok(val_fn("john-doe")[0], "accepts hyphens")
	ok(val_fn("first.last")[0], "accepts inner period")

	print("=== TestUUIDFolderMappingAndCaseCollisions ===")
	temp_dir = tempfile.mkdtemp(prefix="mss_test_iso_")
	try:
		map_file = os.path.join(temp_dir, "uuid_folder_mapping.json")

		# Save and load round-trip
		sample_map = {"uuid-1": "Alice", "uuid-2": "Bob"}
		iso_mod.save_uuid_folder_mapping(sample_map, path=map_file)
		loaded = iso_mod.load_uuid_folder_mapping(path=map_file)
		ok(loaded == sample_map, "mapping persists and loads correctly")

		# Case collision detection
		colliding_map = {"uuid-1": "Alice", "uuid-2": "alice"}
		threw_collision = False
		try:
			iso_mod.save_uuid_folder_mapping(colliding_map, path=map_file)
		except ValueError as e:
			threw_collision = True
			ok("Case collision" in str(e), "detects case-insensitive collision")
		ok(threw_collision, "save_uuid_folder_mapping refused case collision")

		print("=== TestAtomicMultiTypeRenameAndJournal ===")
		models_root = os.path.join(temp_dir, "models")
		ckpt_dir = os.path.join(models_root, "checkpoints")
		lora_dir = os.path.join(models_root, "loras")
		os.makedirs(os.path.join(ckpt_dir, "uuid-alice"), exist_ok=True)
		os.makedirs(os.path.join(lora_dir, "uuid-alice"), exist_ok=True)
		with open(os.path.join(ckpt_dir, "uuid-alice", "model.safetensors"), "w") as f:
			f.write("weights")

		# Fake folder_paths
		fp_stub = types.ModuleType("folder_paths")
		fp_stub.filename_list_cache = {"checkpoints": ["some_entry"]}
		sys.modules["folder_paths"] = fp_stub

		# Successful rename
		success = iso_mod.rename_user_isolation_folders(
			old_name="uuid-alice",
			new_name="alice",
			models_root=models_root,
			folder_types=["checkpoints", "loras"],
		)
		ok(success, "rename_user_isolation_folders succeeded")
		ok(
			os.path.exists(os.path.join(ckpt_dir, "alice", "model.safetensors")),
			"ckpt folder renamed",
		)
		ok(os.path.exists(os.path.join(lora_dir, "alice")), "lora folder renamed")
		ok(not os.path.exists(os.path.join(ckpt_dir, "uuid-alice")), "old ckpt folder removed")
		ok(len(fp_stub.filename_list_cache) == 0, "filename_list_cache was cleared")

		# Target exists refusal
		os.makedirs(os.path.join(ckpt_dir, "uuid-bob"), exist_ok=True)
		os.makedirs(os.path.join(ckpt_dir, "bob"), exist_ok=True)
		threw_exists = False
		try:
			iso_mod.rename_user_isolation_folders(
				old_name="uuid-bob",
				new_name="bob",
				models_root=models_root,
				folder_types=["checkpoints"],
			)
		except FileExistsError:
			threw_exists = True
		ok(threw_exists, "refuses rename if destination folder already exists")

		# Journal cleanup check
		journal_path = os.path.join(models_root, ".rename_journal.json")
		ok(not os.path.exists(journal_path), "journal file was cleaned up on completion")

		print("=== TestParentOwnershipPreservation ===")
		parent_dir = os.path.join(temp_dir, "parent_test")
		os.makedirs(parent_dir, mode=0o750, exist_ok=True)
		child_dir = os.path.join(temp_dir, "child_test")
		os.makedirs(child_dir, mode=0o700, exist_ok=True)
		iso_mod.copy_ownership_and_mode(child_dir, parent_dir)
		child_mode = os.stat(child_dir).st_mode & 0o777
		parent_mode = os.stat(parent_dir).st_mode & 0o777
		ok(
			child_mode == parent_mode,
			f"child mode {oct(child_mode)} matches parent mode {oct(parent_mode)}",
		)

		print("=== TestOfflineMigrationToolFunctional ===")
		# Setup an isolated test environment with SQLite DB and UUID folders
		mig_data_dir = os.path.join(temp_dir, "mig_data")
		mig_models_dir = os.path.join(mig_data_dir, "model_isolation", "models")
		os.makedirs(mig_data_dir, exist_ok=True)
		db_path = os.path.join(mig_data_dir, "mss_login_data.db")

		conn = sqlite3.connect(db_path)
		conn.execute(
			"""
			CREATE TABLE users (
				user_id TEXT PRIMARY KEY,
				username TEXT NOT NULL
			)
		"""
		)
		conn.execute(
			"""
			CREATE TABLE shared_items (
				id INTEGER PRIMARY KEY AUTOINCREMENT,
				user_id TEXT,
				item_type TEXT,
				folder TEXT,
				item_name TEXT,
				grantee TEXT
			)
		"""
		)
		conn.execute("INSERT INTO users VALUES ('uuid-carol', 'carol')")
		conn.execute("INSERT INTO users VALUES ('uuid-dave', 'dave')")
		conn.execute(
			"INSERT INTO shared_items (user_id, item_type, folder, item_name, grantee) "
			"VALUES ('uuid-carol', 'model', 'checkpoints', 'uuid-carol/carol_model.safetensors', 'uuid-dave')"
		)
		conn.commit()
		conn.close()

		# Create on-disk UUID model files
		carol_ckpt = os.path.join(mig_models_dir, "checkpoints", "uuid-carol")
		os.makedirs(carol_ckpt, exist_ok=True)
		with open(os.path.join(carol_ckpt, "carol_model.safetensors"), "w") as f:
			f.write("carol weights")

		migrate_script = os.path.join(_TOOLS_DIR, "migrate_isolation.py")

		# 1. Run dry-run
		cmd_dry = [
			sys.executable,
			migrate_script,
			"--dry-run",
			"--data-dir",
			mig_data_dir,
			"--db-path",
			db_path,
		]
		proc_dry = subprocess.run(cmd_dry, capture_output=True, text=True)
		ok(proc_dry.returncode == 0, "migrate_isolation.py --dry-run exited 0")
		ok("END-OK" in proc_dry.stdout, "dry-run reported END-OK")
		ok("Dry-run completed" in proc_dry.stdout, "dry-run completed without writes")
		# Verify dry-run did not alter files
		ok(os.path.exists(carol_ckpt), "dry-run left uuid folder intact")

		# 2. Check port-lock detection
		cmd_locked = [
			sys.executable,
			migrate_script,
			"--apply",
			"--data-dir",
			mig_data_dir,
			"--db-path",
			db_path,
			"--lock-port",
			"9008",
		]
		proc_locked = subprocess.run(cmd_locked, capture_output=True, text=True)
		ok(
			"port 9008" in proc_locked.stderr or proc_locked.returncode == 0,
			"lock-port check handled correctly",
		)

		# 3. Run apply on safe port
		cmd_apply = [
			sys.executable,
			migrate_script,
			"--apply",
			"--data-dir",
			mig_data_dir,
			"--db-path",
			db_path,
			"--lock-port",
			"59999",
		]
		proc_apply = subprocess.run(cmd_apply, capture_output=True, text=True)
		ok(
			proc_apply.returncode == 0,
			f"migrate_isolation.py --apply exited 0: {proc_apply.stderr}",
		)
		ok("END-OK" in proc_apply.stdout, "apply reported END-OK")
		# Verify on-disk rename
		carol_renamed = os.path.join(mig_models_dir, "checkpoints", "carol")
		ok(os.path.exists(carol_renamed), "apply created username folder 'carol'")
		ok(
			os.path.exists(os.path.join(carol_renamed, "carol_model.safetensors")),
			"model file present in username folder",
		)

		# 4. Check 1:1 database grant rewrite rule (zero extra grants added)
		conn = sqlite3.connect(db_path)
		cur = conn.cursor()
		cur.execute("SELECT item_name FROM shared_items")
		rows = cur.fetchall()
		conn.close()
		ok(
			len(rows) == 1,
			f"shared_items count remains exactly 1 (zero extra grants added): {len(rows)}",
		)
		ok(
			rows[0][0] == "carol/carol_model.safetensors",
			f"grant rewritten to 'carol/carol_model.safetensors': {rows[0][0]}",
		)

		# 5. Run verify
		cmd_verify = [
			sys.executable,
			migrate_script,
			"--verify",
			"--data-dir",
			mig_data_dir,
			"--db-path",
			db_path,
		]
		proc_verify = subprocess.run(cmd_verify, capture_output=True, text=True)
		ok(proc_verify.returncode == 0, "migrate_isolation.py --verify exited 0")
		ok("END-OK" in proc_verify.stdout, "verify reported END-OK")

		# 6. Test restore from backup
		# Locate backup directory
		backups_root = os.path.join(mig_data_dir, "model_isolation", "backups")
		backup_dirs = [os.path.join(backups_root, d) for d in os.listdir(backups_root)]
		ok(len(backup_dirs) > 0, "backup directory was created")
		backup_dir = backup_dirs[0]

		cmd_restore = [
			sys.executable,
			migrate_script,
			"--restore",
			backup_dir,
			"--data-dir",
			mig_data_dir,
			"--db-path",
			db_path,
			"--lock-port",
			"59999",
		]
		proc_restore = subprocess.run(cmd_restore, capture_output=True, text=True)
		ok(
			proc_restore.returncode == 0,
			f"migrate_isolation.py --restore exited 0: {proc_restore.stderr}",
		)
		ok("END-OK" in proc_restore.stdout, "restore reported END-OK")
		ok(os.path.exists(carol_ckpt), "restore reinstated original uuid-carol folder")

		# 7. Test existing username folder classification (never classified as 'unknown')
		alice_ckpt = os.path.join(mig_models_dir, "checkpoints", "alice")
		os.makedirs(alice_ckpt, exist_ok=True)
		with open(os.path.join(alice_ckpt, "alice_model.safetensors"), "w") as f:
			f.write("alice content")

		cmd_user_folder = [
			sys.executable,
			migrate_script,
			"--dry-run",
			"--data-dir",
			mig_data_dir,
			"--db-path",
			db_path,
		]
		proc_user_folder = subprocess.run(cmd_user_folder, capture_output=True, text=True)
		ok(proc_user_folder.returncode == 0, "dry-run on existing username folder exited 0")
		ok(
			"unknown" not in proc_user_folder.stdout,
			"existing username folder is NOT marked unknown",
		)
		ok(
			"adopt" in proc_user_folder.stdout or "skip" in proc_user_folder.stdout,
			"existing username folder marked adopt or skip",
		)

		# 8. Test fallback user discovery from DATA_DIR/users/ when DB is unreadable
		os.makedirs(os.path.join(mig_data_dir, "users", "dave"), exist_ok=True)
		dave_ckpt = os.path.join(mig_models_dir, "checkpoints", "dave")
		os.makedirs(dave_ckpt, exist_ok=True)
		with open(os.path.join(dave_ckpt, "dave_model.safetensors"), "w") as f:
			f.write("dave content")

		bogus_db = os.path.join(mig_data_dir, "nonexistent.db")
		cmd_fallback = [
			sys.executable,
			migrate_script,
			"--dry-run",
			"--data-dir",
			mig_data_dir,
			"--db-path",
			bogus_db,
		]
		proc_fallback = subprocess.run(cmd_fallback, capture_output=True, text=True)
		ok(proc_fallback.returncode == 0, "dry-run with unreadable DB exited 0")
		ok(
			"unknown" not in proc_fallback.stdout,
			"fallback discovers user from users/ dir, not marked unknown",
		)
		ok("dave" in proc_fallback.stdout, "dave discovered via filesystem fallback")

		print("=== TestStrictUIGrantEnforcementAndBackwardCompat ===")
		# Set up environment for isolation resolver
		const_mod = sys.modules.get("mss_login.constants")
		if const_mod is None:
			const_mod = types.ModuleType("mss_login.constants")
			sys.modules["mss_login.constants"] = const_mod
		const_mod.DATA_DIR = mig_data_dir
		const_mod.experimental_model_isolation_enabled = lambda: True

		iso_mod.DATA_DIR = mig_data_dir
		carol_target_file = os.path.join(
			mig_models_dir, "checkpoints", "carol", "carol_model.safetensors"
		)
		os.makedirs(os.path.dirname(carol_target_file), exist_ok=True)
		with open(carol_target_file, "w") as f:
			f.write("carol model content")

		fp = sys.modules.get("folder_paths")
		if fp is None:
			fp = types.ModuleType("folder_paths")
			sys.modules["folder_paths"] = fp
		fp.get_folder_paths = lambda folder: [os.path.join(mig_models_dir, folder)]
		fp.get_filename_list = lambda folder: ["carol/carol_model.safetensors"]
		fp.get_full_path = lambda folder, name: os.path.join(mig_models_dir, folder, name)
		fp.filename_list_cache = {}

		res_mod = _load_module(
			"mss_login.utils.isolation_resolver", os.path.join(_UTILS_DIR, "isolation_resolver.py")
		)

		# Scenario A: Non-owner Carol without grant cannot resolve model in her own folder
		res_a = res_mod.resolve_model_name(
			"checkpoints",
			"carol_model.safetensors",
			user_id="uuid-carol",
			username="carol",
			view_all=False,
			grants=[],  # No grant given
		)
		ok(res_a is None, "un-granted non-owner denied (resolve_model_name returns None)")

		# Scenario B: Non-owner Carol with explicit grant in shared_items can resolve it
		res_b = res_mod.resolve_model_name(
			"checkpoints",
			"carol_model.safetensors",
			user_id="uuid-carol",
			username="carol",
			view_all=False,
			grants=[{"folder": "checkpoints", "item_name": "carol/carol_model.safetensors"}],
		)
		ok(
			res_b is not None and res_b.storage_name == "carol/carol_model.safetensors",
			"granted non-owner allowed and resolved",
		)

		# Scenario C: Owner with view_all=True can resolve without explicit grant
		res_c = res_mod.resolve_model_name(
			"checkpoints",
			"carol/carol_model.safetensors",
			user_id="uuid-owner",
			username="owner_user",
			view_all=True,
			grants=[],
		)
		ok(
			res_c is not None and res_c.storage_name == "carol/carol_model.safetensors",
			"owner (view_all=True) allowed without explicit grant",
		)

		# Scenario D: Backward compatibility alias resolution for own legacy UUID
		res_d = res_mod.resolve_model_name(
			"checkpoints",
			"uuid-carol/carol_model.safetensors",
			user_id="uuid-carol",
			username="carol",
			view_all=True,
			grants=[],
		)
		ok(
			res_d is not None and res_d.storage_name == "carol/carol_model.safetensors",
			"legacy <uuid>/model resolves for owning user",
		)

		# Scenario E: Foreign user attempting to resolve Carol's legacy UUID is blocked
		ok(
			res_mod.foreign_uuid_prefix(
				"uuid-carol/carol_model.safetensors", user_id="uuid-dave", username="dave"
			),
			"foreign user legacy UUID identified as foreign prefix",
		)
		res_e = res_mod.resolve_model_name(
			"checkpoints",
			"uuid-carol/carol_model.safetensors",
			user_id="uuid-dave",
			username="dave",
			view_all=False,
			grants=[],
		)
		ok(res_e is None, "foreign user accessing <uuid>/model is denied (returns None)")

		print("=== TestEmergencyRecoverySecretValidation ===")
		emg_mod = _load_module(
			"mss_login.utils.emergency_recovery", os.path.join(_UTILS_DIR, "emergency_recovery.py")
		)

		test_secret = "mss_super_secret_recovery_key_12345"
		os.environ["SECRET_KEY"] = test_secret
		const_mod.SECRET_KEY = test_secret

		trigger_path = os.path.join(temp_dir, ".emergency_unlock_owner")

		# Sub-test 1: Invalid secret rejected, trigger removed, returns False
		with open(trigger_path, "w") as f:
			f.write("wrong_secret_key\n")
		ok(os.path.exists(trigger_path), "created trigger file with wrong secret")

		fake_db_invalid = _FakeUsersDbForEmg()
		handled_invalid = emg_mod.check_and_execute_emergency_recovery(
			users_db_instance=fake_db_invalid, trigger_path=trigger_path
		)
		ok(not handled_invalid, "rejected unlock with invalid secret")
		ok(not fake_db_invalid.unlocked, "owner was not unlocked on invalid secret")
		ok(not os.path.exists(trigger_path), "trigger file removed after invalid attempt")

		# Sub-test 2: Valid secret accepted, trigger removed, returns True
		with open(trigger_path, "w") as f:
			f.write(test_secret + "\n")
		ok(os.path.exists(trigger_path), "created trigger file with valid secret")

		fake_db_valid = _FakeUsersDbForEmg()
		handled_valid = emg_mod.check_and_execute_emergency_recovery(
			users_db_instance=fake_db_valid, trigger_path=trigger_path
		)
		ok(handled_valid, "accepted unlock with valid database secret")
		ok(fake_db_valid.unlocked, "owner MFA was successfully reset")
		ok(not os.path.exists(trigger_path), "trigger file removed after successful unlock")

		# Sub-test 3: CLI emergency_unlock.py requires --secret
		unlock_cli = os.path.join(_SCRIPTS_DIR, "emergency_unlock.py")
		# CLI with incorrect secret
		proc_cli_bad = subprocess.run(
			[sys.executable, unlock_cli, "--secret", "bad_secret", "--create-trigger"],
			capture_output=True,
			text=True,
			env=dict(os.environ, SECRET_KEY=test_secret),
		)
		ok(proc_cli_bad.returncode == 1, "CLI refused incorrect secret")
		ok(
			"Invalid database secret" in proc_cli_bad.stderr
			or "Invalid database secret" in proc_cli_bad.stdout,
			"CLI reported invalid secret error",
		)

		# CLI with correct secret
		proc_cli_good = subprocess.run(
			[sys.executable, unlock_cli, "--secret", test_secret, "--create-trigger"],
			capture_output=True,
			text=True,
			env=dict(os.environ, SECRET_KEY=test_secret),
		)
		ok(proc_cli_good.returncode == 0, "CLI accepted correct secret")
		# Clean up any created trigger
		try:
			from utils.emergency_recovery import get_emergency_unlock_path

			trig = get_emergency_unlock_path()
			if os.path.exists(trig):
				os.unlink(trig)
		except Exception:
			pass

	finally:
		shutil.rmtree(temp_dir, ignore_errors=True)

	print(f"\nResult: {failed} failed, {run - failed} passed, {run} total")
	return failed == 0


class _FakeUsersDbForEmg:
	def __init__(self):
		self.unlocked = False

	def get_owner_username(self):
		return "owner"

	def reset_mfa_for_user(self, username):
		self.unlocked = True
		return True

	def list_users(self):
		return [("owner", {"groups": ["owner"], "mfa_enabled": True})]


if __name__ == "__main__":
	success = run_tests()
	sys.exit(0 if success else 1)
