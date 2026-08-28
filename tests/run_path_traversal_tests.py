r"""
Standalone runner for path traversal tests (no pytest collection of project package).

Use the project .venv (see .agent/rules/python-venv.mdc):
  .\.venv\Scripts\python tests/run_path_traversal_tests.py
"""

import importlib.util
import os
import sys
import tempfile

# Load path_safety without importing the rest of utils (avoids ComfyUI/server deps)
_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_path_safety_path = os.path.join(_PROJECT_ROOT, "utils", "path_safety.py")
_spec = importlib.util.spec_from_file_location("path_safety", _path_safety_path)
_path_safety = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_path_safety)

is_safe_filename = _path_safety.is_safe_filename
is_safe_relative_path = _path_safety.is_safe_relative_path
is_safe_folder_segment = _path_safety.is_safe_folder_segment
safe_basename = _path_safety.safe_basename
resolve_path_under = _path_safety.resolve_path_under
path_under = _path_safety.path_under


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

	print("TestIsSafeFilename")
	ok(is_safe_filename("image.png"), "allows simple name")
	ok(is_safe_filename("workflow.json"), "allows workflow.json")
	ok(not is_safe_filename(".."), "rejects ..")
	ok(not is_safe_filename("a..b"), "rejects a..b")
	ok(not is_safe_filename("a/b"), "rejects a/b")
	ok(not is_safe_filename("/etc/passwd"), "rejects absolute")
	ok(not is_safe_filename("a\\b"), "rejects backslash")
	ok(not is_safe_filename(""), "rejects empty")
	ok(not is_safe_filename(None), "rejects None")
	ok(not is_safe_filename(123), "rejects non-string")

	print("TestIsSafeRelativePath")
	ok(is_safe_relative_path("workflow.json"), "allows simple name")
	ok(is_safe_relative_path("folder/workflow.json"), "allows nested relative path")
	ok(not is_safe_relative_path("../secret.json"), "rejects parent traversal")
	ok(not is_safe_relative_path("a/../b.json"), "rejects embedded ..")
	ok(not is_safe_relative_path("/etc/passwd"), "rejects absolute")
	ok(not is_safe_relative_path("C:/windows/x.json"), "rejects drive-letter path")
	ok(not is_safe_relative_path(""), "rejects empty")
	ok(not is_safe_relative_path(None), "rejects None")

	print("TestIsSafeFolderSegment")
	ok(is_safe_folder_segment("checkpoints"), "allows checkpoints")
	ok(not is_safe_folder_segment(".."), "rejects ..")
	ok(not is_safe_folder_segment("a/b"), "rejects a/b")
	ok(not is_safe_folder_segment("/etc"), "rejects absolute")

	print("TestSafeBasename")
	ok(safe_basename("file.json") == "file.json", "returns basename")
	ok(safe_basename("a/b/file.json") == "file.json", "strips path")
	ok(safe_basename("..") is None, "returns None for ..")
	ok(safe_basename("") is None, "returns None for empty")

	print("TestResolvePathUnder")
	with tempfile.TemporaryDirectory() as base:
		sub = os.path.join(base, "sub")
		os.makedirs(sub, exist_ok=True)
		f = os.path.join(sub, "file.txt")
		open(f, "w").close()
		resolved = resolve_path_under(base, "sub/file.txt")
		ok(resolved is not None, "returns path when under base")
		ok(
			resolved == os.path.normpath(f) or os.path.samefile(resolved, f),
			"resolved equals expected",
		)
	with tempfile.TemporaryDirectory() as base:
		ok(resolve_path_under(base, "..") is None, "rejects ..")
		ok(resolve_path_under(base, "sub/../etc") is None, "rejects sub/../etc")
		ok(resolve_path_under(base, "/etc/passwd") is None, "rejects leading slash")
	ok(
		resolve_path_under("/nonexistent/base/dir", "file.txt") is None,
		"nonexistent base returns None",
	)
	with tempfile.TemporaryDirectory() as base:
		ok(resolve_path_under("", "file.txt") is None, "empty base returns None")
		# None rel_path is normalized to "" so result is base_dir (contained)
		ok(
			resolve_path_under(base, None) is not None
			and resolve_path_under(base, None) == os.path.realpath(base),
			"None rel_path returns base",
		)

	print("TestPathUnder")
	with tempfile.TemporaryDirectory() as base:
		sub = os.path.join(base, "sub")
		os.makedirs(sub, exist_ok=True)
		ok(path_under(sub, base), "sub is under base")
		ok(path_under(base, base), "base equals base")
	with tempfile.TemporaryDirectory() as base:
		other = tempfile.mkdtemp()
		try:
			ok(not path_under(other, base), "other not under base")
		finally:
			os.rmdir(other)

	# --- CI-oriented: path traversal attack vectors ---
	print("TestPathTraversalAttackVectors")
	ok(not is_safe_filename("....//....//....//etc/passwd"), "rejects encoded-style traversal")
	ok(not is_safe_filename("..%2F..%2Fetc%2Fpasswd"), "rejects percent-encoded slash in name")
	ok(not is_safe_folder_segment("checkpoints/../loras"), "rejects folder with traversal")
	ok(not is_safe_folder_segment("."), "rejects current dir (.)")
	ok(
		safe_basename("C:\\Windows\\System32\\file.txt") == "file.txt",
		"safe_basename strips Windows path",
	)
	ok(safe_basename("file.txt") == "file.txt", "safe_basename keeps safe name")
	with tempfile.TemporaryDirectory() as base:
		ok(resolve_path_under(base, "sub/..") is None, "rejects sub/..")
		ok(resolve_path_under(base, "....//....//....") is None, "rejects dot-dot-slash variant")

	# --- Edge cases: unicode, whitespace, length ---
	print("TestEdgeCases")
	ok(is_safe_filename("image_é.png"), "allows unicode in filename")
	ok(not is_safe_filename("  "), "rejects whitespace-only")
	ok(not is_safe_filename("\t"), "rejects tab-only")
	ok(is_safe_filename("a" * 200), "allows long single segment")
	ok(not is_safe_filename("a" * 200 + "/x"), "rejects long path with slash")
	ok(is_safe_folder_segment("model_v2"), "allows alphanumeric and underscore")

	print()
	if failed:
		print(f"Result: {failed} failed, {run - failed} passed, {run} total")
		sys.exit(1)
	print(f"Result: all {run} tests passed")
	sys.exit(0)


if __name__ == "__main__":
	run_tests()
