r"""
Standalone tests for newly implemented features:
1. Workflow storage directory logic (MSS_LOGIN_DATA_DIR vs default)
2. Reserved usernames validation
3. Debug log redaction
4. SQLite encryption and automatic bidirectional migration
5. Model visibility permission checks
6. Custom node dependency scanner and conflict resolution
"""

from __future__ import annotations

import importlib.util
import json
import os
import shutil
import sqlite3
import sys
import tempfile
import types

_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_UTILS_DIR = os.path.join(_PROJECT_ROOT, "utils")

root_pkg = types.ModuleType("mss_login")
root_pkg.__path__ = [_PROJECT_ROOT]
utils_pkg = types.ModuleType("mss_login.utils")
utils_pkg.__path__ = [_UTILS_DIR]
sys.modules["mss_login"] = root_pkg
sys.modules["mss_login.utils"] = utils_pkg


def _load_module_standalone(name: str, rel_path: str, package: str = "mss_login.utils"):
	abs_path = os.path.join(_PROJECT_ROOT, rel_path)
	full_name = f"{package}.{name}"
	spec = importlib.util.spec_from_file_location(full_name, abs_path)
	assert spec and spec.loader
	mod = importlib.util.module_from_spec(spec)
	mod.__package__ = package
	sys.modules[full_name] = mod
	spec.loader.exec_module(mod)
	return mod


def run_tests():
	failed = 0
	run = 0

	def ok(cond: bool, msg: str):
		nonlocal failed, run
		run += 1
		if not cond:
			print(f"  FAIL: {msg}")
			failed += 1
		else:
			print(f"  ok: {msg}")
		return cond

	print("=== Test 1: Reserved Usernames ===")
	validate_mod = _load_module_standalone("validate", os.path.join("utils", "validate.py"))
	reserved = ["admin", "owner", "root", "system", "api", "administrator", "default", "defaults", "guest"]
	for name in reserved:
		ok(not validate_mod.validate_username(name)[0], f"blocks reserved username '{name}'")
		ok(not validate_mod.validate_username(name.upper())[0], f"blocks uppercase reserved username '{name.upper()}'")
	ok(validate_mod.validate_username("alice")[0], "allows valid username 'alice'")
	ok(validate_mod.validate_username("user_123")[0], "allows valid username 'user_123'")

	print("=== Test 2: Workflow Storage Paths ===")
	user_env_mod = _load_module_standalone("user_env", os.path.join("utils", "user_env.py"))
	# Test with MSS_LOGIN_DATA_DIR unset
	orig_env = os.environ.get("MSS_LOGIN_DATA_DIR")
	try:
		if "MSS_LOGIN_DATA_DIR" in os.environ:
			del os.environ["MSS_LOGIN_DATA_DIR"]
		wf_dir_default = user_env_mod.get_user_workflow_dir("alice")
		ok(
			wf_dir_default.replace("\\", "/").endswith("Users/alice/workflows/default"),
			f"default workflow dir ends with Users/alice/workflows/default: {wf_dir_default}",
		)

		# Test with MSS_LOGIN_DATA_DIR set
		with tempfile.TemporaryDirectory() as tmp_data_dir:
			os.environ["MSS_LOGIN_DATA_DIR"] = tmp_data_dir
			wf_dir_custom = user_env_mod.get_user_workflow_dir("alice")
			expected_prefix = os.path.abspath(tmp_data_dir).replace("\\", "/")
			actual_norm = os.path.abspath(wf_dir_custom).replace("\\", "/")
			ok(
				actual_norm == f"{expected_prefix}/workflows/alice",
				f"custom MSS_LOGIN_DATA_DIR workflow dir matches <dir>/workflows/alice: {actual_norm}",
			)
	finally:
		if orig_env is not None:
			os.environ["MSS_LOGIN_DATA_DIR"] = orig_env
		elif "MSS_LOGIN_DATA_DIR" in os.environ:
			del os.environ["MSS_LOGIN_DATA_DIR"]

	print("=== Test 3: Log Redaction ===")
	redactor_mod = _load_module_standalone("log_redactor", os.path.join("utils", "log_redactor.py"))
	raw_log = (
		"2026-09-17 12:00:00 [INFO] Client 192.168.1.50 connecting from 10.0.0.12 (localhost 127.0.0.1)\n"
		'{"user": "alice", "password": "supersecretpassword123", "token": "abcde12345"}\n'
		"Bearer eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJzdWIiOiIxMjM0NTY3ODkwIn0.dozjgNryP4J3jVmNHl0w5N_XgL0n3I9PlFUP0THsK88\n"
		"Found S3 credentials: AKIAIOSFODNN7EXAMPLE and secret\n"
		"User hash: $argon2id$v=19$m=65536,t=3,p=4$c29tZXNhbHQ$RdescudvJCsgqlsbDaYhuaqiBncvAhSTWuEr+nezmp8\n"
		"Legacy hash: $2a$12$e86gOz0u8mJ19vVl5vEw1.Mqm3o9V7Z3I5V8S6Z1Q2W3E4R5T6Y7U\n"
		"PRAGMA key = '0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef';\n"
		"Authorization: Bearer mysecrettoken123\n"
		"Cookie: session=secretcookie123\n"
	)
	redacted = redactor_mod.redact_log_text(raw_log)
	ok("127.0.0.1" in redacted, "preserves 127.0.0.1")
	ok("192.168.1.50" not in redacted and "[REDACTED_IP]" in redacted, "redacts IPv4 addresses")
	ok("supersecretpassword123" not in redacted and '"password": "[REDACTED]"' in redacted, "redacts JSON password")
	ok("[REDACTED_JWT]" in redacted, "redacts JWT token")
	ok("AKIAIOSFODNN7EXAMPLE" not in redacted and "[REDACTED_AWS_KEY]" in redacted, "redacts AWS key")
	ok("[REDACTED_ARGON2_HASH]" in redacted, "redacts Argon2 hash")
	ok("[REDACTED_BCRYPT_HASH]" in redacted, "redacts bcrypt hash")
	ok("[REDACTED_SQLCIPHER_KEY]" in redacted, "redacts PRAGMA key")
	ok("secretcookie123" not in redacted, "redacts Cookie header")
	ok("mysecrettoken123" not in redacted, "redacts Authorization header")

	print("=== Test 4: SQLite Encryption & Auto-Migration ===")
	sqlite_conn_mod = _load_module_standalone("sqlite_connection", os.path.join("utils", "sqlite_connection.py"))
	with tempfile.TemporaryDirectory() as tmp_dir:
		db_path = os.path.join(tmp_dir, "test_users.db")
		# 1. Create plain database
		conn_plain = sqlite3.connect(db_path)
		conn_plain.execute("CREATE TABLE users (id TEXT PRIMARY KEY, username TEXT);")
		conn_plain.execute("INSERT INTO users VALUES ('u1', 'alice');")
		conn_plain.commit()
		conn_plain.close()
		ok(sqlite_conn_mod.is_plain_sqlite(db_path), "detects plain SQLite database format")

		# 2. Open with encryption level standard -> triggers auto-migration
		conn_enc = sqlite_conn_mod.open_sqlite(
			db_path,
			secret_key="TestPassword123!",
			encryption_level="standard",
		)
		cur = conn_enc.cursor()
		cur.execute("SELECT username FROM users WHERE id = 'u1'")
		row = cur.fetchone()
		conn_enc.close()
		ok(row and row[0] == "alice", "migrated database preserves existing rows")
		ok(not sqlite_conn_mod.is_plain_sqlite(db_path), "database file is now encrypted (not plain)")

		# 3. Open encrypted database directly again
		conn_enc2 = sqlite_conn_mod.open_sqlite(
			db_path,
			secret_key="TestPassword123!",
			encryption_level="standard",
		)
		cur2 = conn_enc2.cursor()
		cur2.execute("INSERT INTO users VALUES ('u2', 'bob');")
		conn_enc2.commit()
		conn_enc2.close()

		# 4. Migrate back to plain (encryption_level disabled/empty)
		conn_plain2 = sqlite_conn_mod.open_sqlite(
			db_path,
			secret_key="TestPassword123!",
			encryption_level="",
		)
		cur3 = conn_plain2.cursor()
		cur3.execute("SELECT count(*) FROM users")
		count = cur3.fetchone()[0]
		conn_plain2.close()
		ok(count == 2, f"unencrypted database has all 2 rows: count={count}")
		ok(sqlite_conn_mod.is_plain_sqlite(db_path), "database file successfully converted back to plain SQLite")

	print("=== Test 5: Model Visibility Policy ===")
	# Install fake constants for model visibility
	fake_const = types.ModuleType("constants")
	fake_const.experimental_model_isolation_enabled = lambda: False
	sys.modules["..constants"] = fake_const
	policy_mod = _load_module_standalone("model_visibility_policy", os.path.join("utils", "model_visibility_policy.py"))
	ok(
		policy_mod.user_can_view_all_models("user", {"can_view_all_comfyui_items": False}) is False,
		"user with can_view_all_comfyui_items: false cannot view all models (safetensors hidden)",
	)
	ok(
		policy_mod.user_can_view_all_models("user", {"can_view_all_comfyui_items": True}) is True,
		"user with can_view_all_comfyui_items: true can view all models when isolation off",
	)
	ok(
		policy_mod.user_can_view_all_models("owner", {"can_view_all_comfyui_items": False}) is True,
		"owner always can view all models",
	)

	print("=== Test 6: Custom Node Dependency Installer ===")
	deps_installer_mod = _load_module_standalone("node_deps_installer", os.path.join("utils", "node_deps_installer.py"))
	with tempfile.TemporaryDirectory() as tmp_nodes_dir:
		node1_dir = os.path.join(tmp_nodes_dir, "ComfyUI-Test-Node")
		os.makedirs(node1_dir)
		with open(os.path.join(node1_dir, "requirements.txt"), "w", encoding="utf-8") as f:
			f.write("torch==2.0.0\n")
			f.write("pytest==7.0.0\n")
			f.write("safetensors>=0.4.0\n")

		scanned = deps_installer_mod.scan_custom_nodes(tmp_nodes_dir)
		ok(len(scanned) == 1, f"scanned 1 node: {len(scanned)}")
		node_info = scanned[0]
		ok(node_info["node_name"] == "ComfyUI-Test-Node", "detected node name")
		# Check conflict resolution:
		# torch should be skipped
		# pytest==7.0.0 should be relaxed to pytest>=7.0.0 if pytest installed
		sanitized = node_info["sanitized_requirements"]
		conflicts = node_info["conflicts_resolved"]
		ok(not any(r.startswith("torch") for r in sanitized), "torch is excluded from installation")
		ok(any("torch" in c for c in conflicts), "torch conflict recorded in notes")
		ok(any(r.startswith("pytest") for r in sanitized), "pytest requirement is retained")

		# Dry run execution test
		res = deps_installer_mod.scan_and_install_node_dependencies(custom_nodes_dir=tmp_nodes_dir, dry_run=True)
		ok(res["success"] is True, "dry-run completed successfully")
		ok(res["nodes_scanned"] == 1, "dry-run scanned 1 node")
		ok(res["results"][0]["status"] == "dry_run", "node result status is dry_run")

	print()
	if failed:
		print(f"Result: {failed} failed, {run - failed} passed, {run} total")
		sys.exit(1)
	print(f"Result: all {run} tests passed!")
	sys.exit(0)


if __name__ == "__main__":
	run_tests()
