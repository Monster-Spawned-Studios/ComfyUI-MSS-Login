r"""
Standalone tests for per-user output/workflow isolation helpers (no ComfyUI).

Use the project .venv:
  .venv/bin/python tests/run_user_isolation_tests.py
"""

import importlib.util
import os
import sys

_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _load_module(name, path):
	spec = importlib.util.spec_from_file_location(name, path)
	mod = importlib.util.module_from_spec(spec)
	spec.loader.exec_module(mod)
	return mod


def run_tests():
	iso = _load_module("user_isolation", os.path.join(_PROJECT_ROOT, "utils", "user_isolation.py"))
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

	print("TestSafeUserDirSegment")
	ok(iso.safe_user_dir_segment(None) == "public", "None -> public")
	ok(iso.safe_user_dir_segment("") == "public", "empty -> public")
	ok(iso.safe_user_dir_segment("abc-123") == "abc-123", "keeps uuid-like id")
	ok(iso.safe_user_dir_segment("../etc") == "public", "rejects traversal")
	ok(iso.safe_user_dir_segment("a/b") == "public", "rejects slash")

	print("TestPromptExecutionPaths")
	ok(iso.is_prompt_execution_path("/prompt"), "Comfy Portal POST /prompt")
	ok(iso.is_prompt_execution_path("/api/prompt"), "frontend POST /api/prompt")
	ok(iso.is_prompt_execution_path("/prompt/"), "trailing slash")
	ok(not iso.is_prompt_execution_path("/api/queue"), "queue is not prompt submit")
	ok(not iso.is_prompt_execution_path("/view"), "view is not prompt submit")

	print("TestUserIdFromQueueItem")
	ok(
		iso.user_id_from_queue_item((0, "pid", {}, {}, [], {"user_id": "u-1"})) == "u-1",
		"reads stamp from patched queue tuple",
	)
	ok(iso.user_id_from_queue_item((0, "pid", {})) is None, "missing stamp is None")
	ok(iso.user_id_from_queue_item("not-a-tuple") is None, "non-tuple is None")
	ok(
		iso.username_from_queue_item(
			(0, "pid", {}, {}, [], {"user_id": "u-1", "username": "alice"})
		)
		== "alice",
		"reads username stamp from patched queue tuple",
	)

	print(f"\n{run - failed}/{run} passed")
	return failed == 0


if __name__ == "__main__":
	sys.exit(0 if run_tests() else 1)
