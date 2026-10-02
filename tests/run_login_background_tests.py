"""
Tests for experimental login background helpers (path safety, gating, public payload).

  .venv/bin/python tests/run_login_background_tests.py
"""

from __future__ import annotations

import importlib.util
import json
import os
import sys
import tempfile
import types

_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_UTILS_DIR = os.path.join(_PROJECT_ROOT, "utils")

_PASS = 0
_FAIL = 0


def ok(cond: bool, msg: str) -> None:
	global _PASS, _FAIL
	if cond:
		_PASS += 1
		print(f"  OK  {msg}")
	else:
		_FAIL += 1
		print(f" FAIL {msg}")


def _load_module(name: str, path: str, package: str | None = None):
	spec = importlib.util.spec_from_file_location(name, path)
	mod = importlib.util.module_from_spec(spec)
	if package:
		mod.__package__ = package
	assert spec and spec.loader
	sys.modules[name] = mod
	spec.loader.exec_module(mod)
	return mod


def main() -> int:
	print("=== login background ===")
	data_dir = tempfile.mkdtemp(prefix="mss-login-bg-")
	config_path = os.path.join(data_dir, "config.json")
	bg_dir = os.path.join(data_dir, "login-background")
	os.makedirs(bg_dir, exist_ok=True)
	img_path = os.path.join(bg_dir, "test.png")
	with open(img_path, "wb") as f:
		f.write(b"\x89PNG\r\n\x1a\n")

	# Minimal package stubs so login_background can import ..constants / .path_safety / .json_utils
	pkg = types.ModuleType("mss_login_pkg")
	pkg.__path__ = [_PROJECT_ROOT]
	sys.modules["mss_login_pkg"] = pkg

	utils_pkg = types.ModuleType("mss_login_pkg.utils")
	utils_pkg.__path__ = [_UTILS_DIR]
	sys.modules["mss_login_pkg.utils"] = utils_pkg

	constants = types.ModuleType("mss_login_pkg.constants")
	constants.CONFIG_FILE_PATH = config_path
	constants.DATA_DIR = data_dir
	constants.experimental_login_background_enabled = lambda: False
	sys.modules["mss_login_pkg.constants"] = constants

	_load_module(
		"mss_login_pkg.utils.path_safety",
		os.path.join(_UTILS_DIR, "path_safety.py"),
		"mss_login_pkg.utils",
	)
	_load_module(
		"mss_login_pkg.utils.json_utils",
		os.path.join(_UTILS_DIR, "json_utils.py"),
		"mss_login_pkg.utils",
	)
	lb = _load_module(
		"mss_login_pkg.utils.login_background",
		os.path.join(_UTILS_DIR, "login_background.py"),
		"mss_login_pkg.utils",
	)

	public = lb.get_public_login_background()
	ok(public.get("enabled") is False, "public disabled when experimental off")

	constants.experimental_login_background_enabled = lambda: True
	# Rebind name used inside module
	lb.experimental_login_background_enabled = lambda: True

	saved = lb.save_login_background_settings(
		{
			"enabled": True,
			"source": "local",
			"local_path": "login-background/test.png",
			"media_kind": "auto",
			"url": "",
		}
	)
	ok(saved["enabled"] is True, "settings save enabled")
	ok(os.path.isfile(config_path), "config.json written")
	with open(config_path, encoding="utf-8") as f:
		disk = json.load(f)
	ok(disk.get("login_background", {}).get("enabled") is True, "persisted enabled flag")

	public = lb.get_public_login_background()
	ok(public.get("enabled") is True, "public enabled when experimental+toggle on")
	ok(public.get("src") == lb.PUBLIC_MEDIA_PATH, "local src uses media route")
	ok(public.get("media_kind") == "image", "png detected as image")

	path, ctype = lb.get_local_media_file()
	ok(path == os.path.realpath(img_path), "local media resolves under DATA_DIR")
	ok(ctype is not None, f"content-type set ({ctype})")

	try:
		lb.save_login_background_settings(
			{
				"enabled": True,
				"source": "local",
				"local_path": "../outside.png",
				"media_kind": "auto",
			}
		)
		ok(False, "traversal path should raise")
	except ValueError:
		ok(True, "traversal path rejected")

	try:
		lb.save_login_background_settings(
			{"enabled": True, "source": "url", "url": "javascript:alert(1)", "media_kind": "image"}
		)
		ok(False, "javascript URL should raise")
	except ValueError:
		ok(True, "javascript URL rejected")

	lb.save_login_background_settings(
		{
			"enabled": True,
			"source": "url",
			"url": "https://cdn.example.com/wall.mp4",
			"media_kind": "auto",
			"local_path": "",
		}
	)
	public = lb.get_public_login_background()
	ok(public.get("src") == "https://cdn.example.com/wall.mp4", "external URL passed through")
	ok(public.get("media_kind") == "video", "mp4 detected as video")
	img_o, media_o = lb.csp_extra_origins_for_login()
	ok(img_o is None and media_o == "https://cdn.example.com", "CSP media origin for video URL")

	print(f"\n{_PASS} passed, {_FAIL} failed")
	return 1 if _FAIL else 0


if __name__ == "__main__":
	raise SystemExit(main())
