r"""
Standalone tests for input sanitization and validation (no ComfyUI deps).

Use the project .venv (see .agent/rules/python-venv.mdc):
  .\.venv\Scripts\python tests/run_sanitizer_tests.py
"""

import importlib.util
import os
import sys

_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_utils = os.path.join(_PROJECT_ROOT, "utils")


def _load_module(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def run_tests():
    input_sanitizer = _load_module("input_sanitizer", os.path.join(_utils, "input_sanitizer.py"))
    validate = _load_module("validate", os.path.join(_utils, "validate.py"))

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

    # --- sanitize_username ---
    print("TestSanitizeUsername")
    ok(input_sanitizer.sanitize_username("alice") == "alice", "keeps valid username")
    ok(input_sanitizer.sanitize_username("  bob  ") == "bob", "strips whitespace")
    ok(input_sanitizer.sanitize_username("user_name") == "user_name", "allows underscore")
    ok(input_sanitizer.sanitize_username("user-name") == "user-name", "allows hyphen")
    ok(".." not in input_sanitizer.sanitize_username("a/b"), "strips path separators")
    ok("/" not in input_sanitizer.sanitize_username("a/b/c"), "strips slashes")
    ok(input_sanitizer.sanitize_username(None) == "", "None returns empty")
    out = input_sanitizer.sanitize_username("x" * 200)
    ok(len(out) <= 128, "truncates to max_len")
    ok("\x00" not in input_sanitizer.sanitize_username("a\x00b"), "strips null byte")

    # --- sanitize_password_input ---
    print("TestSanitizePasswordInput")
    ok(input_sanitizer.sanitize_password_input("secret") == "secret", "keeps password")
    ok(input_sanitizer.sanitize_password_input(None) == "", "None returns empty")
    ok("\x00" not in input_sanitizer.sanitize_password_input("p\x00w"), "strips null byte")

    # --- validate_username ---
    print("TestValidateUsername")
    ok(validate.validate_username("alice")[0] is True, "valid username passes")
    ok(validate.validate_username("ab")[0] is False, "too short fails")
    ok(validate.validate_username("user name")[0] is False, "space fails")
    ok(validate.validate_username("user@")[0] is False, "invalid char fails")
    ok(validate.validate_username("abc")[0] is True, "min length passes")
    # Reserved usernames must not break user account system (path/config conflicts)
    ok(validate.validate_username("guest")[0] is False, "reserved 'guest' rejected")
    ok(validate.validate_username("default")[0] is False, "reserved 'default' rejected")
    ok(validate.validate_username("defaults")[0] is False, "reserved 'defaults' rejected")
    ok(
        validate.validate_username("Default")[0] is False,
        "reserved 'Default' (case-insensitive) rejected",
    )
    ok(
        validate.validate_username("DEFAULTS")[0] is False,
        "reserved 'DEFAULTS' (case-insensitive) rejected",
    )

    # --- validate_password ---
    print("TestValidatePassword")
    ok(validate.validate_password("short")[0] is False, "too short fails")
    ok(validate.validate_password("nodigit!")[0] is False, "no digit fails")
    ok(validate.validate_password("nospecial1")[0] is False, "no special fails")
    ok(validate.validate_password("Valid1!ab")[0] is True, "valid password passes")

    print()
    if failed:
        print(f"Result: {failed} failed, {run - failed} passed, {run} total")
        sys.exit(1)
    print(f"Result: all {run} tests passed")
    sys.exit(0)


if __name__ == "__main__":
    run_tests()
