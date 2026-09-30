r"""
Standalone tests for owner/admin role integrity (no ComfyUI deps).

Covers regression for: saving owner as groups=["owner"] stripped admin, so the
next registered user was treated as first-admin and received owner (and became
the sole last-admin account).

  .venv/bin/python tests/run_owner_role_tests.py
"""

import importlib.util
import os
import sys
import tempfile
import types
import uuid

_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_utils = os.path.join(_PROJECT_ROOT, "utils")


def _ensure_utils_pkg():
	"""Register a stub utils package so relative imports work without utils/__init__.py."""
	if "utils" not in sys.modules:
		pkg = types.ModuleType("utils")
		pkg.__path__ = [_utils]
		pkg.__file__ = os.path.join(_utils, "__init__.py")
		sys.modules["utils"] = pkg


def _load_module(full_name, filename):
	_ensure_utils_pkg()
	path = os.path.join(_utils, filename)
	spec = importlib.util.spec_from_file_location(full_name, path)
	mod = importlib.util.module_from_spec(spec)
	sys.modules[full_name] = mod
	short = full_name.split(".", 1)[-1] if full_name.startswith("utils.") else full_name
	sys.modules[f"utils.{short}"] = mod
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

	_load_module("utils.path_safety", "path_safety.py")
	_load_module("utils.encryption", "encryption.py")
	_load_module("utils.sanitizer", "sanitizer.py")
	_load_module("utils.db_key_derivation", "db_key_derivation.py")
	_load_module("utils.sqlite_connection", "sqlite_connection.py")
	users_db_mod = _load_module("utils.users_db", "users_db.py")
	UsersDB = users_db_mod.UsersDB

	with tempfile.TemporaryDirectory(prefix="mss_owner_role_") as tmp:
		db_path = os.path.join(tmp, "users.db")
		cfg = {"backend": "sqlite", "sqlite_path": db_path, "encryption_level": ""}
		db = UsersDB(cfg, secret_key="test-secret-key-for-owner-role")

		# --- helper: owner counts as admin ---
		print("TestOwnerCountsAsAdmin")
		owner_uid = str(uuid.uuid4())
		db.add_user(owner_uid, "realowner", "OwnerPass1!", admin=True)
		_uid, owner_rec = db.get_user(username="realowner")
		ok("owner" in [g.lower() for g in owner_rec.get("groups", [])], "first user gets owner")
		ok("admin" in [g.lower() for g in owner_rec.get("groups", [])], "first user gets admin")
		ok(db._has_admin() is True, "owner+admin counted by _has_admin")
		admin_uid, _admin_rec = db.get_admin_user()
		ok(admin_uid is not None, "get_admin_user finds owner")
		ok(db.get_owner_username() == "realowner", "get_owner_username returns real owner")

		# Simulate legacy broken save: owner-only groups, admin=False (bypass update_user)
		owner_rec["groups"] = ["owner"]
		owner_rec["admin"] = False
		db._backend.update(owner_uid, owner_rec, db._secret_key)
		db.users[owner_uid] = owner_rec
		ok(UsersDB._user_is_admin(owner_rec) is True, "_user_is_admin true for owner-only")
		ok(db._has_admin() is True, "owner-only still counted by _has_admin")
		admin_uid2, _ = db.get_admin_user()
		ok(admin_uid2 is not None, "get_admin_user finds owner-only user")
		ok(db.delete_user("realowner") == "last_admin", "owner-only is last_admin protected")

		# --- add_user must not mint a second owner ---
		print("TestAddUserDoesNotMintSecondOwner")
		new_uid = str(uuid.uuid4())
		db.add_user(new_uid, "newuser", "NewUser1!ab", admin=True)
		_nuid, new_rec = db.get_user(username="newuser")
		new_groups = [g.lower() for g in new_rec.get("groups", [])]
		ok("owner" not in new_groups, "second admin user does not get owner")
		ok("admin" in new_groups, "second admin user gets admin group")
		ok(db.get_owner_username() == "realowner", "original owner unchanged")

		# Regular registration path (admin=False)
		user_uid = str(uuid.uuid4())
		db.add_user(user_uid, "regularuser", "Regular1!ab", admin=False)
		_ruid, reg_rec = db.get_user(username="regularuser")
		reg_groups = [g.lower() for g in reg_rec.get("groups", [])]
		ok(reg_groups == ["user"], "non-admin registration gets user role")
		ok("owner" not in reg_groups, "non-admin registration does not get owner")

		# New user is not last_admin (owner + another admin exist)
		ok(db.delete_user("newuser") is True, "non-sole admin can be deleted")

		# --- update_user normalizes owner to keep admin ---
		print("TestUpdateUserNormalizesOwner")
		ok(
			db.update_user("realowner", ["owner"], False, sfw_check=True) is True,
			"update_user accepts owner-only payload",
		)
		_uid3, healed = db.get_user(username="realowner")
		healed_groups = [g.lower() for g in healed.get("groups", [])]
		ok("owner" in healed_groups, "normalized groups keep owner")
		ok("admin" in healed_groups, "normalized groups restore admin")
		ok(healed.get("admin") is True, "normalized admin flag is True")

		# --- empty DB bootstrap still grants owner ---
		print("TestBootstrapFirstUserGetsOwner")
		db2_path = os.path.join(tmp, "users2.db")
		db2 = UsersDB(
			{"backend": "sqlite", "sqlite_path": db2_path, "encryption_level": ""},
			secret_key="test-secret-key-for-owner-role-2",
		)
		boot_uid = str(uuid.uuid4())
		db2.add_user(boot_uid, "bootstrap", "BootPass1!", admin=False)
		# Empty DB forces admin=True and owner grant inside add_user
		_buid, boot_rec = db2.get_user(username="bootstrap")
		boot_groups = [g.lower() for g in boot_rec.get("groups", [])]
		ok(
			"owner" in boot_groups and "admin" in boot_groups,
			"empty-DB first user gets owner+admin",
		)
		ok(boot_rec.get("admin") is True, "empty-DB first user admin flag True")

		# Normalize helper unit check
		print("TestNormalizeOwnerGroupsHelper")
		g, a = UsersDB._normalize_owner_groups(["owner"], False)
		ok(g == ["owner", "admin"] and a is True, "normalize owner-only -> owner+admin")
		g2, a2 = UsersDB._normalize_owner_groups(["user"], False)
		ok(g2 == ["user"] and a2 is False, "normalize user unchanged")
		g3, a3 = UsersDB._normalize_owner_groups(["owner", "admin"], True)
		ok(g3 == ["owner", "admin"] and a3 is True, "normalize owner+admin unchanged")

		# Create-with-role (owner API path via add_user groups=)
		print("TestAddUserWithExplicitRole")
		power_uid = str(uuid.uuid4())
		db.add_user(power_uid, "poweruser", "PowerUser1!ab", admin=False, groups=["power"])
		_puid, power_rec = db.get_user(username="poweruser")
		ok(power_rec.get("groups") == ["power"], "explicit power role assigned")
		ok(power_rec.get("admin") is False, "power role is not admin")
		admin2_uid = str(uuid.uuid4())
		db.add_user(admin2_uid, "admin2", "AdminTwo1!ab", admin=True, groups=["admin"])
		_a2, admin2_rec = db.get_user(username="admin2")
		ok(admin2_rec.get("groups") == ["admin"], "explicit admin role assigned")
		# Refuse minting second owner via groups=
		fake_owner_uid = str(uuid.uuid4())
		db.add_user(
			fake_owner_uid, "fakeowner", "FakeOwner1!ab", admin=True, groups=["owner", "admin"]
		)
		_fuid, fake_rec = db.get_user(username="fakeowner")
		fake_groups = [g.lower() for g in fake_rec.get("groups", [])]
		ok("owner" not in fake_groups, "second owner refused when groups=owner")
		ok("admin" in fake_groups, "stripped owner still keeps admin when requested")

	print(f"\n{run} tests, {failed} failed")
	return 0 if failed == 0 else 1


if __name__ == "__main__":
	sys.exit(run_tests())
