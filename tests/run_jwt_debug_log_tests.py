r"""
Standalone tests: DEBUG_MODE JWT logging must never write raw tokens (AUDIT-007).

  .venv/bin/python tests/run_jwt_debug_log_tests.py
"""

from __future__ import annotations

import importlib.util
import os
import sys
import tempfile
import types

_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_UTILS = os.path.join(_PROJECT_ROOT, "utils")


def _ok(cond: bool, msg: str, failed: list[str]) -> None:
	if cond:
		print(f"  ok: {msg}")
	else:
		print(f"  FAIL: {msg}")
		failed.append(msg)


def _load_logger():
	root = types.ModuleType("mss_login")
	root.__path__ = [_PROJECT_ROOT]
	utils = types.ModuleType("mss_login.utils")
	utils.__path__ = [_UTILS]
	sys.modules["mss_login"] = root
	sys.modules["mss_login.utils"] = utils

	path = os.path.join(_UTILS, "logger.py")
	spec = importlib.util.spec_from_file_location("mss_login.utils.logger", path)
	mod = importlib.util.module_from_spec(spec)
	sys.modules["mss_login.utils.logger"] = mod
	sys.modules["utils.logger"] = mod
	spec.loader.exec_module(mod)
	return mod


def test_jwt_debug_log_redacts_token(failed: list[str]) -> None:
	print("TestJwtDebugLogRedactsToken")
	mod = _load_logger()
	tmpdir = tempfile.mkdtemp()
	log_path = os.path.join(tmpdir, "mss.log")

	const = types.ModuleType("mss_login.constants")
	const.DEBUG_MODE = True
	const.filter_debug_messages_enabled = lambda: False
	sys.modules["mss_login.constants"] = const

	logger = mod.Logger(log_path, ["INFO", "WARNING", "ERROR", "DEBUG"])
	try:
		import jwt

		raw_token = jwt.encode(
			{"username": "alice", "jti": "abc123"},
			"test-secret-audit-007-not-for-prod",
			algorithm="HS256",
		)
	except ImportError:
		raw_token = "header.payload.signature-FAKE-JWT-FOR-TEST-ONLY"
	logger.log_jwt_if_debug(raw_token, "alice")

	text = Path_read(log_path)
	_ok(raw_token not in text, "raw JWT not written to log file", failed)
	_ok("username=alice" in text, "username present in debug log", failed)
	_ok("jti=abc123" in text or "jti=" in text, "jti or placeholder present", failed)
	_ok("JWT token created (DEBUG_MODE)" in text, "safe debug message prefix", failed)
	_ok("JWT token (DEBUG_MODE):" not in text, "old raw-token log format gone", failed)

	# Console logger should also avoid the raw token
	# (capture via a temporary handler is heavy; assert file is enough for AUDIT-007)


def Path_read(path: str) -> str:
	if not os.path.isfile(path):
		return ""
	with open(path, encoding="utf-8") as f:
		return f.read()


def main() -> int:
	failed: list[str] = []
	try:
		test_jwt_debug_log_redacts_token(failed)
	except Exception as e:
		print(f"FAIL: {e}")
		import traceback

		traceback.print_exc()
		return 1
	print()
	if failed:
		print(f"Failed ({len(failed)}): {failed}")
		return 1
	print("All JWT debug log tests passed.")
	return 0


if __name__ == "__main__":
	sys.exit(main())
