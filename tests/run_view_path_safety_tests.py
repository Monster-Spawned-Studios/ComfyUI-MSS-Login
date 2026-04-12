"""
Standalone tests for /view relative-path safety helpers.
"""

import importlib.util
import os
import sys
import types

_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_ROUTES_DIR = os.path.join(_PROJECT_ROOT, "routes")


def _load_module(name: str, path: str, package: str):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    mod.__package__ = package
    assert spec and spec.loader
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


def _install_stubs():
    root_pkg = types.ModuleType("mss_login")
    root_pkg.__path__ = [_PROJECT_ROOT]
    routes_pkg = types.ModuleType("mss_login.routes")
    routes_pkg.__path__ = [_ROUTES_DIR]
    utils_pkg = types.ModuleType("mss_login.utils")
    utils_pkg.__path__ = [os.path.join(_PROJECT_ROOT, "utils")]

    sys.modules["mss_login"] = root_pkg
    sys.modules["mss_login.routes"] = routes_pkg
    sys.modules["mss_login.utils"] = utils_pkg

    globals_mod = types.ModuleType("mss_login.globals")
    globals_mod.jwt_auth = object()
    globals_mod.current_username_var = object()

    class _UsersDb:
        def get_user(self, _username):
            return None

    globals_mod.users_db = _UsersDb()
    sys.modules["mss_login.globals"] = globals_mod

    user_env_mod = types.ModuleType("mss_login.utils.user_env")
    sys.modules["mss_login.utils.user_env"] = user_env_mod

    ps_mod = types.ModuleType("mss_login.utils.path_safety")

    def _is_safe_filename(name):
        return bool(name) and ".." not in name and "/" not in name and "\\" not in name

    ps_mod.is_safe_filename = _is_safe_filename
    ps_mod.resolve_path_under = lambda *_args, **_kwargs: None
    sys.modules["mss_login.utils.path_safety"] = ps_mod

    sfw_mod = types.ModuleType("mss_login.utils.sfw_intercept.nsfw_guard")
    sfw_mod.should_block_image_for_current_user = lambda _p: False
    sys.modules["mss_login.utils.sfw_intercept.nsfw_guard"] = sfw_mod

    folder_paths_mod = types.ModuleType("folder_paths")
    folder_paths_mod.base_path = _PROJECT_ROOT
    folder_paths_mod.get_output_directory = lambda: _PROJECT_ROOT
    sys.modules["folder_paths"] = folder_paths_mod


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

    _install_stubs()
    mod = _load_module(
        "mss_login.routes.workflow_routes",
        os.path.join(_ROUTES_DIR, "workflow_routes.py"),
        "mss_login.routes",
    )

    print("TestViewRelativePathSafety")
    ok(
        mod.build_safe_view_relative_path("image.png", "") == "image.png",
        "empty subfolder keeps filename",
    )
    ok(
        mod.build_safe_view_relative_path("image.png", "alice") == "alice/image.png",
        "single-segment subfolder is preserved",
    )
    ok(
        mod.build_safe_view_relative_path("image.png", "alice/2026-04-12")
        == "alice/2026-04-12/image.png",
        "nested subfolder is preserved",
    )
    ok(
        mod.build_safe_view_relative_path("image.png", "../alice") is None,
        "traversal subfolder is rejected",
    )
    ok(
        mod.build_safe_view_relative_path("../image.png", "alice") is None,
        "unsafe filename is rejected",
    )
    ok(
        mod.build_safe_view_relative_path("image.png", "/absolute/path") is None,
        "absolute subfolder is rejected",
    )

    print()
    if failed:
        print(f"Result: {failed} failed, {run - failed} passed, {run} total")
        sys.exit(1)
    print(f"Result: all {run} tests passed")
    sys.exit(0)


if __name__ == "__main__":
    run_tests()
