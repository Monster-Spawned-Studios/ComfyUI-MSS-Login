r"""
Comprehensive tests for MFA grace period, standardized TOTP URI, WebAuthn passkey origin resolution,
emergency owner unlock, and owner-only model isolation folder naming convention.

Run with:
    .venv/bin/python tests/run_mfa_passkeys_isolation_tests.py
"""

import os
import shutil
import sys
import tempfile
import time
import types
import uuid
from datetime import datetime, timezone, timedelta

# Ensure repo root is on sys.path
_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)

# Stub ComfyUI modules if not present
for _mod in ("folder_paths", "execution", "server"):
    if _mod not in sys.modules:
        sys.modules[_mod] = types.ModuleType(_mod)
sys.modules["execution"].MAXIMUM_HISTORY_SIZE = 1000
sys.modules["execution"].PromptQueue = type("PromptQueue", (), {})
sys.modules["server"].PromptServer = type("PromptServer", (), {})


def run_tests():
    failed = 0
    total = 0

    def ok(cond, msg):
        nonlocal failed, total
        total += 1
        if not cond:
            print(f"  FAIL: {msg}")
            failed += 1
        else:
            print(f"  OK: {msg}")

    # ---------------------------------------------------------
    # 1. TOTP URI RFC 6238 Standardization
    # ---------------------------------------------------------
    print("\n--- 1. Standardized TOTP URI Tests ---")
    from utils.users_db import UsersDB
    tmp_dir = tempfile.mkdtemp()
    try:
        secret_key = "test_secret_key_for_unit_tests"
        db_cfg = {"backend": "sqlite", "sqlite_path": os.path.join(tmp_dir, "test.db")}
        users_db = UsersDB(db_cfg, secret_key, None)
        users_db.load_users()
        users_db.add_user(uuid.uuid4().hex, "testuser", "TestPassword123!", False, groups=["user"])

        start_res = users_db.mfa_setup_start("testuser")
        ok(start_res is not None, "mfa_setup_start succeeded")
        uri, recovery_codes = start_res

        ok("otpauth://totp/" in uri, "URI starts with otpauth://totp/")
        ok("MSS-Login:testuser" in uri or "MSS-Login%3Atestuser" in uri, "Label contains issuer and username")
        ok("secret=" in uri, "URI contains secret parameter")
        ok("issuer=MSS-Login" in uri, "URI contains issuer parameter")
        ok("digits=6" in uri, "URI specifies digits=6")
        ok("period=30" in uri, "URI specifies period=30")
        ok("algorithm=SHA1" in uri, "URI specifies algorithm=SHA1")
        ok(len(recovery_codes) == 10, "Generated 10 recovery codes")
    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)

    # ---------------------------------------------------------
    # 2. MFA Grace Period & Due Date Evaluation
    # ---------------------------------------------------------
    print("\n--- 2. MFA Grace Period & Gating Tests ---")
    from utils.login_finalize import evaluate_mfa_gate
    from constants import set_mfa_enforced_at

    tmp_dir = tempfile.mkdtemp()
    try:
        db_cfg = {"backend": "sqlite", "sqlite_path": os.path.join(tmp_dir, "test_grace.db")}
        users_db = UsersDB(db_cfg, "test_secret", None)
        users_db.load_users()
        users_db.add_user(uuid.uuid4().hex, "alice", "Pass123!", True, groups=["admin"])

        groups_cfg = {
            "groups": {
                "admin": {"require_mfa": True},
                "user": {"require_mfa": False}
            }
        }

        # Case A: MFA enforced recently (2 days ago), 7 days grace period -> in grace period
        now_dt = datetime.now(timezone.utc)
        enforced_two_days_ago = (now_dt - timedelta(days=2)).isoformat()
        set_mfa_enforced_at(enforced_two_days_ago)

        gate_in_grace = evaluate_mfa_gate("alice", users_db, groups_cfg, experimental_on=True)
        ok(gate_in_grace.needs_setup is False, "Alice does not block login while in grace period")
        ok(gate_in_grace.in_grace_period is True, "Alice is within grace period")
        ok(gate_in_grace.days_remaining is not None and gate_in_grace.days_remaining > 0, "Days remaining is positive")
        ok(bool(gate_in_grace.due_date), "Due date is populated")

        # Case B: MFA enforced 10 days ago (grace period 7 days expired) -> locked out / must enroll
        enforced_ten_days_ago = (now_dt - timedelta(days=10)).isoformat()
        set_mfa_enforced_at(enforced_ten_days_ago)

        gate_expired = evaluate_mfa_gate("alice", users_db, groups_cfg, experimental_on=True)
        ok(gate_expired.needs_setup is True, "MFA setup is required now that grace period expired")
        ok(gate_expired.in_grace_period is False, "Grace period has expired")
        ok(gate_expired.days_remaining == 0, "Days remaining is 0")
    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)

    # ---------------------------------------------------------
    # 3. WebAuthn Subdomain & Origin Resolution
    # ---------------------------------------------------------
    print("\n--- 3. WebAuthn Origin & Subdomain Tests ---")
    from utils.webauthn_config import (
        origin_allowed,
        is_secure_webauthn_origin,
        get_webauthn_config,
    )

    orig_env_rp = os.environ.get("WEBAUTHN_RP_ID")
    orig_env_origins = os.environ.get("WEBAUTHN_ORIGINS")
    try:
        os.environ["WEBAUTHN_RP_ID"] = "example.com"
        os.environ["WEBAUTHN_ORIGINS"] = "https://example.com"
        ok(origin_allowed("https://example.com"), "Base origin matching rp_id is allowed")
        ok(origin_allowed("https://sub.example.com"), "Subdomain of rp_id is allowed")
        ok(origin_allowed("https://deep.nested.example.com"), "Nested subdomain of rp_id is allowed")
        ok(not origin_allowed("https://evil-example.com"), "Non-subdomain suffix match is rejected")
        ok(not origin_allowed("https://example.org"), "Different domain is rejected")

        ok(is_secure_webauthn_origin("https://comfy.example.com"), "HTTPS URL is secure")
        ok(is_secure_webauthn_origin("https://sub.example.com"), "HTTPS subdomain is secure")
        ok(not is_secure_webauthn_origin("http://localhost:8188"), "HTTP localhost rejected (requires HTTPS)")
        ok(not is_secure_webauthn_origin("http://comfy.example.com"), "HTTP remote is insecure")
        ok(not is_secure_webauthn_origin("https://192.168.1.100"), "Bare IP is rejected")
    finally:
        if orig_env_rp is not None:
            os.environ["WEBAUTHN_RP_ID"] = orig_env_rp
        else:
            os.environ.pop("WEBAUTHN_RP_ID", None)
        if orig_env_origins is not None:
            os.environ["WEBAUTHN_ORIGINS"] = orig_env_origins
        else:
            os.environ.pop("WEBAUTHN_ORIGINS", None)

    # ---------------------------------------------------------
    # 4. Model Isolation Toggle & Dual-Read Migration
    # ---------------------------------------------------------
    print("\n--- 4. Model Isolation Naming & Dual-Read Tests ---")
    from constants import save_isolation_folder_naming, get_isolation_folder_naming
    from utils.model_isolation import (
        user_isolation_candidate_segments,
        maybe_migrate_user_isolation,
    )

    # Naming setting getter/setter
    save_isolation_folder_naming("user_id")
    ok(get_isolation_folder_naming() == "user_id", "Setting saved as user_id")
    save_isolation_folder_naming("username")
    ok(get_isolation_folder_naming() == "username", "Setting saved as username")

    # Candidate segments
    uid = "019488a0-1234-7000-8000-000000000001"
    uname = "bob"
    save_isolation_folder_naming("username")
    segments_user = user_isolation_candidate_segments(uid, uname)
    ok(segments_user == ["bob", uid], "Username convention orders username first")

    save_isolation_folder_naming("user_id")
    segments_id = user_isolation_candidate_segments(uid, uname)
    ok(segments_id == [uid, "bob"], "user_id convention orders user_id first")

    # Auto-migration test
    tmp_models = tempfile.mkdtemp()
    try:
        # Create an existing UUID folder with a model
        uuid_folder = os.path.join(tmp_models, "checkpoints", uid)
        os.makedirs(uuid_folder, exist_ok=True)
        test_model_file = os.path.join(uuid_folder, "test_checkpoint.safetensors")
        with open(test_model_file, "w") as f:
            f.write("checkpoint_data")

        # Set convention to username and run maybe_migrate_user_isolation
        save_isolation_folder_naming("username")
        primary_dir = maybe_migrate_user_isolation(
            os.path.join(tmp_models, "checkpoints"),
            uid,
            uname
        )
        expected_uname_dir = os.path.join(tmp_models, "checkpoints", uname)
        ok(os.path.isdir(expected_uname_dir), "Username directory created by migration")
        migrated_file = os.path.join(expected_uname_dir, "test_checkpoint.safetensors")
        ok(os.path.isfile(migrated_file), "Model file migrated to username directory")
    finally:
        shutil.rmtree(tmp_models, ignore_errors=True)
        save_isolation_folder_naming("username")

    # ---------------------------------------------------------
    # 5. Subfolder Isolation Resolution
    # ---------------------------------------------------------
    print("\n--- 5. Subfolder Isolation Resolution Tests ---")
    import constants
    import utils.model_isolation as mi
    import utils.isolation_resolver as ir
    from utils.isolation_resolver import resolve_model_name

    orig_data_dir = getattr(constants, "DATA_DIR", None)
    orig_iso_fn = getattr(constants, "experimental_model_isolation_enabled", None)
    orig_ir_iso_fn = getattr(ir, "experimental_model_isolation_enabled", None)
    tmp_iso_root = tempfile.mkdtemp()
    try:
        constants.DATA_DIR = tmp_iso_root
        mi.DATA_DIR = tmp_iso_root
        constants.experimental_model_isolation_enabled = lambda: True
        ir.experimental_model_isolation_enabled = lambda: True
        mi.experimental_model_isolation_enabled = lambda: True

        # Carol's isolated checkpoints folder under tmp_iso_root/model_isolation/models/checkpoints/carol
        user_folder = os.path.join(tmp_iso_root, "model_isolation", "models", "checkpoints", "carol")
        sub_folder = os.path.join(user_folder, "SDXL")
        os.makedirs(sub_folder, exist_ok=True)
        sub_model = os.path.join(sub_folder, "juggernaut.safetensors")
        with open(sub_model, "w") as f:
            f.write("dummy model content")

        # Resolve subfolder name "SDXL/juggernaut.safetensors"
        res = resolve_model_name(
            folder="checkpoints",
            name="SDXL/juggernaut.safetensors",
            user_id="user_carol_uuid",
            username="carol",
        )
        ok(res is not None, "Subfolder model resolved")
        ok(res is not None and res.storage_name == "carol/SDXL/juggernaut.safetensors", f"Storage name matches: {getattr(res, 'storage_name', None)}")
        ok(res is not None and res.origin == "user", "Origin is user")

        # Resolve prefixed name "carol/SDXL/juggernaut.safetensors"
        res_prefixed = resolve_model_name(
            folder="checkpoints",
            name="carol/SDXL/juggernaut.safetensors",
            user_id="user_carol_uuid",
            username="carol",
        )
        ok(res_prefixed is not None and res_prefixed.storage_name == "carol/SDXL/juggernaut.safetensors", "Prefixed subfolder model resolved")

        # Foreign UUID prefix is denied
        res_foreign = resolve_model_name(
            folder="checkpoints",
            name="00000000-0000-0000-0000-000000000000/model.safetensors",
            user_id="user_carol_uuid",
            username="carol",
        )
        ok(res_foreign is None, "Foreign UUID prefix is denied")

        # Test manual folder conversion from ComfyUI shared models root
        import utils.folder_paths_compat as fpc

        orig_models_root_fn = getattr(fpc, "get_models_root", None)
        tmp_shared_models = tempfile.mkdtemp()
        orig_get_udb = getattr(mi, "_get_users_db", None)
        try:
            fpc.get_models_root = lambda: tmp_shared_models
            manual_user_dir = os.path.join(tmp_shared_models, "loras", "carol")
            os.makedirs(manual_user_dir, exist_ok=True)
            with open(os.path.join(manual_user_dir, "my_manual.safetensors"), "w") as f:
                f.write("weights")

            fake_udb = type(
                "FakeUDB",
                (),
                {
                    "users": {"user_carol_uuid": {"username": "carol"}},
                    "load_users": lambda self: None,
                },
            )()
            mi._get_users_db = lambda: fake_udb

            count = mi.convert_manual_user_folders_to_isolation()
            ok(count >= 0, "convert_manual_user_folders_to_isolation ran")
            target_converted = os.path.join(
                tmp_iso_root, "model_isolation", "models", "loras", "carol", "my_manual.safetensors"
            )
            ok(os.path.isfile(target_converted), "Manual host folder model converted to isolation")
            ok(not os.path.exists(manual_user_dir), "Original manual folder removed")
        finally:
            if orig_models_root_fn is not None:
                fpc.get_models_root = orig_models_root_fn
            if orig_get_udb is not None:
                mi._get_users_db = orig_get_udb
            shutil.rmtree(tmp_shared_models, ignore_errors=True)
    finally:
        if orig_data_dir is not None:
            constants.DATA_DIR = orig_data_dir
            mi.DATA_DIR = orig_data_dir
        if orig_iso_fn is not None:
            constants.experimental_model_isolation_enabled = orig_iso_fn
        if orig_ir_iso_fn is not None:
            ir.experimental_model_isolation_enabled = orig_ir_iso_fn
        shutil.rmtree(tmp_iso_root, ignore_errors=True)

    # ---------------------------------------------------------
    # 6. Emergency Recovery System
    # ---------------------------------------------------------
    print("\n--- 6. Emergency Recovery System Tests ---")
    import utils.data_dir as dd
    from utils.emergency_recovery import (
        get_emergency_unlock_path,
        check_and_execute_emergency_recovery,
    )
    from utils.ip_filter import IPFilter
    from utils.timeout import Timeout

    orig_env_data_dir = os.environ.get("MSS_LOGIN_DATA_DIR")
    orig_cached_data_dir = dd._DATA_DIR
    tmp_data_dir = tempfile.mkdtemp()
    try:
        os.environ["MSS_LOGIN_DATA_DIR"] = tmp_data_dir
        dd._DATA_DIR = tmp_data_dir

        db_cfg = {"backend": "sqlite", "sqlite_path": os.path.join(tmp_data_dir, "recovery.db")}
        users_db = UsersDB(db_cfg, "test_secret", None)
        users_db.load_users()
        users_db.add_user(uuid.uuid4().hex, "super_owner", "Secret123!", True, groups=["owner", "admin"])

        # Set up MFA and timeout for super_owner
        users_db.mfa_setup_start("super_owner")
        _uid, u = users_db.get_user("super_owner")
        u["mfa_enabled"] = True
        users_db._backend.update(_uid, u, users_db._secret_key)
        ok(users_db.get_mfa_enabled("super_owner") is True, "super_owner has MFA enabled")

        mock_ip_filter = type(
            "MockIPFilter",
            (),
            {
                "load_filter_list": lambda self: None,
                "is_whitelisted": lambda self, ip: False,
                "add_to_blacklist": lambda self, ip: None,
            },
        )()
        timeout = Timeout(mock_ip_filter, state_path=os.path.join(tmp_data_dir, "timeout.json"))
        timeout.add_failed_attempt("1.2.3.4", "super_owner")
        timeout.add_failed_attempt("1.2.3.4", "super_owner")
        timeout.add_failed_attempt("1.2.3.4", "super_owner")

        # Place trigger file
        trigger_path = get_emergency_unlock_path()
        ok(trigger_path.startswith(tmp_data_dir), "Trigger path is inside temp data dir")
        with open(trigger_path, "w") as f:
            f.write("emergency_unlock\n")
        ok(os.path.isfile(trigger_path), "Trigger file placed")

        # Execute recovery
        recovered = check_and_execute_emergency_recovery(
            users_db_instance=users_db,
            timeout_instance=timeout
        )
        ok(recovered is True, "Recovery reported True")
        ok(not os.path.isfile(trigger_path), "Trigger file was removed")
        ok(users_db.get_mfa_enabled("super_owner") is False, "Owner MFA was disabled")
    finally:
        if orig_env_data_dir is not None:
            os.environ["MSS_LOGIN_DATA_DIR"] = orig_env_data_dir
        else:
            os.environ.pop("MSS_LOGIN_DATA_DIR", None)
        dd._DATA_DIR = orig_cached_data_dir
        shutil.rmtree(tmp_data_dir, ignore_errors=True)

    print("\n==========================================")
    print(f"Results: {total - failed}/{total} passed")
    if failed > 0:
        print(f"FAILED: {failed} test(s)")
        return 1
    print("ALL TESTS PASSED SUCCESSFULLY!")
    return 0


if __name__ == "__main__":
    sys.exit(run_tests())
