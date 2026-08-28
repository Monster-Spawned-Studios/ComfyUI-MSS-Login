r"""
Standalone tests for avatar upload sanitization (no ComfyUI).

  .venv/bin/python tests/run_avatar_tests.py
"""

import importlib.util
import io
import os
import sys
import tempfile
import types

_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_UTILS = os.path.join(_PROJECT_ROOT, "utils")


def _load(name, path):
	spec = importlib.util.spec_from_file_location(name, path)
	mod = importlib.util.module_from_spec(spec)
	sys.modules[name] = mod
	spec.loader.exec_module(mod)
	return mod


def _png_bytes(size=(32, 32), color=(10, 20, 30)):
	from PIL import Image

	buf = io.BytesIO()
	Image.new("RGB", size, color).save(buf, format="PNG")
	return buf.getvalue()


def run_tests():
	# Minimal package stubs so relative imports in avatar.py resolve.
	if "utils" not in sys.modules:
		pkg = types.ModuleType("utils")
		pkg.__path__ = [_UTILS]
		pkg.__package__ = "utils"
		sys.modules["utils"] = pkg
	path_safety = _load("utils.path_safety", os.path.join(_UTILS, "path_safety.py"))
	sys.modules["utils.path_safety"] = path_safety

	data_dir = types.ModuleType("utils.data_dir")
	tmpdir = tempfile.mkdtemp(prefix="mss-avatar-")
	data_dir.get_data_subdir = lambda *parts: os.path.join(tmpdir, *parts)
	sys.modules["utils.data_dir"] = data_dir

	user_env = types.ModuleType("utils.user_env")

	def _sanitize(name):
		raw = (name or "guest").strip() or "guest"
		if ".." in raw or "/" in raw or "\\" in raw:
			return "guest"
		return raw

	user_env._sanitize_username_for_path = _sanitize
	sys.modules["utils.user_env"] = user_env

	avatar = _load("utils.avatar", os.path.join(_UTILS, "avatar.py"))

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

	print("TestProcessAvatarBytes")
	png, err = avatar.process_avatar_bytes(_png_bytes())
	ok(png is not None and err is None, "valid PNG is accepted")
	ok(png[:8] == b"\x89PNG\r\n\x1a\n", "output is PNG")

	svg, err = avatar.process_avatar_bytes(b"<svg xmlns='http://www.w3.org/2000/svg'></svg>")
	ok(svg is None and err, "SVG is rejected")

	html, err = avatar.process_avatar_bytes(b"<!DOCTYPE html><script>alert(1)</script>")
	ok(html is None and err, "HTML is rejected")

	huge, err = avatar.process_avatar_bytes(b"x" * (avatar.MAX_UPLOAD_BYTES + 10))
	ok(huge is None and err, "oversize payload is rejected")

	junk, err = avatar.process_avatar_bytes(b"not-an-image")
	ok(junk is None and err, "garbage bytes are rejected")

	print("TestGuestAndTraversalUsernames")
	try:
		avatar.avatar_dir("guest")
		ok(False, "guest username rejected")
	except ValueError:
		ok(True, "guest username rejected")
	try:
		avatar.avatar_dir("../etc")
		ok(False, "traversal username rejected")
	except ValueError:
		ok(True, "traversal username rejected")

	print("TestSaveAndHasAvatar")
	ok_save, msg = avatar.save_avatar("alice", _png_bytes())
	ok(ok_save, f"save alice avatar ({msg})")
	ok(avatar.has_avatar("alice"), "alice has avatar")
	ok(not avatar.has_avatar("bob"), "bob has no avatar")
	ok(avatar.delete_avatar("alice"), "delete alice avatar")
	ok(not avatar.has_avatar("alice"), "alice avatar removed")

	print(f"\n{run - failed}/{run} passed")
	return failed == 0


if __name__ == "__main__":
	sys.exit(0 if run_tests() else 1)
