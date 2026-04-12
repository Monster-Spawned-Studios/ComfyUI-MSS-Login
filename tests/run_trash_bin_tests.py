"""
Standalone tests for trash-bin storage helpers.
"""

import importlib.util
import json
import os
import shutil
import sys
import tempfile
import types

_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_UTILS_DIR = os.path.join(_PROJECT_ROOT, "utils")
_CONFIG_PATH = os.path.join(_PROJECT_ROOT, ".tmp_trash_test_config.json")


def _load_module(name: str, path: str, package: str):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    mod.__package__ = package
    assert spec and spec.loader
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


def _install_stubs(tmp_data_dir: str):
    root_pkg = types.ModuleType("mss_login")
    root_pkg.__path__ = [_PROJECT_ROOT]
    utils_pkg = types.ModuleType("mss_login.utils")
    utils_pkg.__path__ = [_UTILS_DIR]
    sys.modules["mss_login"] = root_pkg
    sys.modules["mss_login.utils"] = utils_pkg

    const_mod = types.ModuleType("mss_login.constants")
    const_mod.DATA_DIR = tmp_data_dir
    const_mod.CONFIG_FILE_PATH = _CONFIG_PATH
    sys.modules["mss_login.constants"] = const_mod

    json_utils = types.ModuleType("mss_login.utils.json_utils")

    def _load_json_file(path, fallback=None):
        try:
            with open(path, "r", encoding="utf-8") as fh:
                return json.load(fh)
        except Exception:
            return fallback if fallback is not None else {}

    def _save_json_file(path, payload):
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as fh:
            json.dump(payload, fh, indent=2)

    json_utils.load_json_file = _load_json_file
    json_utils.save_json_file = _save_json_file
    sys.modules["mss_login.utils.json_utils"] = json_utils

    path_safety = types.ModuleType("mss_login.utils.path_safety")

    def _resolve_path_under(base_dir, rel_path):
        if not base_dir or rel_path is None:
            return None
        rel = str(rel_path).replace("\\", "/").strip()
        if rel.startswith("/") or ".." in rel:
            return None
        base_real = os.path.realpath(base_dir)
        if not os.path.isdir(base_real):
            return None
        resolved = os.path.realpath(os.path.join(base_real, rel))
        if resolved.startswith(base_real + os.sep) or resolved == base_real:
            return resolved
        return None

    path_safety.resolve_path_under = _resolve_path_under
    sys.modules["mss_login.utils.path_safety"] = path_safety

    data_dir_mod = types.ModuleType("mss_login.utils.data_dir")
    output_base = os.path.join(tmp_data_dir, "output")
    temp_base = os.path.join(tmp_data_dir, "temp")
    os.makedirs(output_base, exist_ok=True)
    os.makedirs(temp_base, exist_ok=True)

    def _get_data_subdir(name: str):
        if name == "output":
            return output_base
        if name == "temp":
            return temp_base
        path = os.path.join(tmp_data_dir, name)
        os.makedirs(path, exist_ok=True)
        return path

    data_dir_mod.get_data_subdir = _get_data_subdir
    sys.modules["mss_login.utils.data_dir"] = data_dir_mod

    folder_paths_mod = types.ModuleType("folder_paths")
    folder_paths_mod.get_output_directory = lambda: os.path.join(output_base, "alice")
    folder_paths_mod.get_temp_directory = lambda: os.path.join(temp_base, "alice")
    sys.modules["folder_paths"] = folder_paths_mod

    os.makedirs(folder_paths_mod.get_output_directory(), exist_ok=True)
    os.makedirs(folder_paths_mod.get_temp_directory(), exist_ok=True)


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

    tmp_data_dir = tempfile.mkdtemp(prefix="mss_trash_test_")
    try:
        with open(_CONFIG_PATH, "w", encoding="utf-8") as fh:
            json.dump({"trash": {"retention_days": 30, "cleanup_interval_hours": 24}}, fh)

        _install_stubs(tmp_data_dir)
        mod = _load_module(
            "mss_login.utils.trash_store",
            os.path.join(_UTILS_DIR, "trash_store.py"),
            "mss_login.utils",
        )

        print("TestTrashMoveAndRestore")
        src = os.path.join(tmp_data_dir, "output", "alice", "image.png")
        with open(src, "wb") as fh:
            fh.write(b"img")
        res = mod.trash_image_reference(
            filename="image.png",
            subfolder="",
            image_type="output",
            request_username="alice",
            is_owner=False,
            prompt_id="p1",
        )
        ok(res.get("status") == "ok", "user delete moves file to trash")
        record = dict(res.get("record") or {})
        ok(not os.path.exists(src), "source file no longer exists after trash move")
        ok(os.path.isfile(record.get("trash_path", "")), "trash file exists")

        restore = mod.restore_trash_item(
            item_id=str(record.get("id")),
            request_username="alice",
            is_owner=False,
        )
        ok(restore.get("status") == "ok", "user can restore own trashed item")
        ok(os.path.isfile(restore.get("restored_path", "")), "restored file exists")

        print("TestOwnerOverrideAndEmpty")
        bob_dir = os.path.join(tmp_data_dir, "output", "bob")
        os.makedirs(bob_dir, exist_ok=True)
        bob_src = os.path.join(bob_dir, "bob.png")
        with open(bob_src, "wb") as fh:
            fh.write(b"img")
        res_owner = mod.trash_image_file(
            source_path=bob_src,
            request_username="owner",
            is_owner=True,
            relative_path="bob.png",
            image_type="output",
            prompt_id="p2",
        )
        ok(res_owner.get("status") == "ok", "owner can trash another user's file")
        bob_items_owner = mod.list_trash_items(
            request_username="owner",
            is_owner=True,
            target_user="bob",
        )
        ok(len(bob_items_owner) >= 1, "owner can list another user's trash")
        bob_items_non_owner = mod.list_trash_items(
            request_username="alice",
            is_owner=False,
            target_user="bob",
        )
        ok(len(bob_items_non_owner) == 0, "non-owner cannot list another user's trash")

        empty_res = mod.empty_trash(request_username="owner", is_owner=True, target_user="bob")
        ok(int(empty_res.get("deleted", 0)) >= 1, "owner can empty target user's trash")

        print("TestTrashCleanup")
        expired_src = os.path.join(tmp_data_dir, "output", "alice", "expired.png")
        with open(expired_src, "wb") as fh:
            fh.write(b"img")
        expired_res = mod.trash_image_file(
            source_path=expired_src,
            request_username="alice",
            is_owner=False,
            relative_path="expired.png",
            image_type="output",
            prompt_id="p3",
        )
        ok(expired_res.get("status") == "ok", "expired fixture moved to trash")
        records = mod._load_records()
        for item in records:
            if item.get("id") == expired_res.get("record", {}).get("id"):
                item["delete_after_ts"] = 1
        mod._save_records(records)
        cleanup = mod.cleanup_expired_trash(now_ts=10)
        ok(int(cleanup.get("deleted", 0)) >= 1, "expired trash items are auto-deleted")

        print()
    finally:
        try:
            if os.path.isfile(_CONFIG_PATH):
                os.remove(_CONFIG_PATH)
        except Exception:
            pass
        shutil.rmtree(tmp_data_dir, ignore_errors=True)

    if failed:
        print(f"Result: {failed} failed, {run - failed} passed, {run} total")
        sys.exit(1)
    print(f"Result: all {run} tests passed")
    sys.exit(0)


if __name__ == "__main__":
    run_tests()
