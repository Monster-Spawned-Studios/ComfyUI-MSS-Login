r"""
Standalone runner for release_prepare.py (no ComfyUI).

  .venv/bin/python tests/run_release_prepare_tests.py
"""

from __future__ import annotations

import importlib.util
import os
import shutil
import subprocess
import sys
import tempfile
import zipfile
from pathlib import Path

_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _load_release_prepare():
	path = os.path.join(_PROJECT_ROOT, "scripts", "release_prepare.py")
	spec = importlib.util.spec_from_file_location("release_prepare", path)
	module = importlib.util.module_from_spec(spec)
	assert spec.loader is not None
	spec.loader.exec_module(module)
	return module


def _init_git(repo: Path) -> None:
	subprocess.run(["git", "init"], cwd=repo, check=True, capture_output=True)
	subprocess.run(
		["git", "config", "user.email", "test@example.com"],
		cwd=repo,
		check=True,
		capture_output=True,
	)
	subprocess.run(
		["git", "config", "user.name", "Test User"], cwd=repo, check=True, capture_output=True
	)
	# Host git may have commit.gpgsign=true; temp fixtures must not require a signing key.
	subprocess.run(
		["git", "config", "commit.gpgsign", "false"], cwd=repo, check=True, capture_output=True
	)


def _commit_all(repo: Path, message: str) -> None:
	subprocess.run(["git", "add", "-A"], cwd=repo, check=True, capture_output=True)
	subprocess.run(["git", "commit", "-m", message], cwd=repo, check=True, capture_output=True)


def run_tests() -> int:
	rp = _load_release_prepare()
	failed = 0
	run = 0

	def ok(cond: bool, msg: str) -> None:
		nonlocal failed, run
		run += 1
		if not cond:
			print(f"  FAIL: {msg}")
			failed += 1
		else:
			print(f"  ok: {msg}")

	print("[release_prepare] validate_version rejects unsafe input")
	try:
		rp.validate_version("../1.0.0")
		ok(False, "should reject path traversal version")
	except ValueError:
		ok(True, "rejects unsafe version")

	ok(rp.validate_version("1.2.3") == "1.2.3", "accepts valid version")
	ok(rp.validate_version("v1.2.3") == "1.2.3", "strips v prefix")

	with tempfile.TemporaryDirectory() as tmp:
		repo = Path(tmp)
		(repo / "readme" / "changelogs").mkdir(parents=True)
		(repo / "pyproject.toml").write_text(
			'[project]\nname = "test"\nversion = "0.0.1"\n', encoding="utf-8"
		)
		(repo / "README.md").write_text(
			'<p align="center">\n  <strong>Version 0.0.1</strong> — test\n</p>\n', encoding="utf-8"
		)
		(repo / "web" / "html").mkdir(parents=True)
		(repo / "web" / "html" / "login.html").write_text(
			"<p>MSS-Login - v{{VERSION}}</p>\n", encoding="utf-8"
		)
		(repo / "uv.lock").write_text(
			'[[package]]\nname = "mss-login"\nversion = "0.0.1"\nsource = { virtual = "." }\n',
			encoding="utf-8",
		)
		_init_git(repo)
		_commit_all(repo, "initial commit")
		(repo / "feature.txt").write_text("alpha\n", encoding="utf-8")
		_commit_all(repo, "add feature alpha")

		print("[release_prepare] bumps pyproject and creates changelog")
		changed = rp.prepare_release(repo, "0.0.2", changelog_title="Test release")
		ok(changed, "prepare_release reports changes")
		ok(
			rp.read_pyproject_version(repo / "pyproject.toml") == "0.0.2",
			"pyproject version updated",
		)
		cl = repo / "readme" / "changelogs" / "0.0.2.md"
		ok(cl.is_file(), "changelog file created")
		body = cl.read_text(encoding="utf-8")
		ok("# 0.0.2 - Test release" in body, "changelog heading")
		ok("## Changelog" in body, "changelog section")
		ok("add feature alpha" in body, "git log bullet from commit")
		ok("Version 0.0.2" in (repo / "README.md").read_text(encoding="utf-8"), "README version")
		ok(
			"MSS-Login - v0.0.2"
			in (repo / "web" / "html" / "login.html").read_text(encoding="utf-8"),
			"login page version stamped",
		)
		ok(
			'name = "mss-login"\nversion = "0.0.2"'
			in (repo / "uv.lock").read_text(encoding="utf-8"),
			"uv.lock mss-login version",
		)

		print("[release_prepare] inject_login_version handles placeholder and stamped values")
		ok(
			rp.inject_login_version("<p>MSS-Login - v{{VERSION}}</p>", "1.2.3")
			== "<p>MSS-Login - v1.2.3</p>",
			"injects placeholder",
		)
		ok(
			rp.inject_login_version("<p>MSS-Login - v0.0.2</p>", "1.2.3")
			== "<p>MSS-Login - v1.2.3</p>",
			"replaces previously stamped version",
		)

		print("[release_prepare] --print-version-files lists existing version paths")
		listed = {
			p.relative_to(repo).as_posix()
			for p in rp.versioned_release_paths(repo, "0.0.2")
			if p.exists()
		}
		ok("pyproject.toml" in listed, "lists pyproject.toml")
		ok("web/html/login.html" in listed, "lists login.html")
		ok("uv.lock" in listed, "lists uv.lock")

		print("[release_prepare] idempotent when files already match")
		changed_again = rp.prepare_release(repo, "0.0.2")
		ok(not changed_again, "second run makes no changes")
		original = body
		ok(cl.read_text(encoding="utf-8") == original, "changelog not overwritten")

		print("[release_prepare] respects existing changelog file")
		cl.write_text("# 0.0.2 - Manual\n\n## Changelog\n\n- custom\n", encoding="utf-8")
		(repo / "pyproject.toml").write_text(
			'[project]\nname = "test"\nversion = "0.0.1"\n', encoding="utf-8"
		)
		rp.prepare_release(repo, "0.0.2")
		ok("custom" in cl.read_text(encoding="utf-8"), "manual changelog preserved")
		ok(rp.read_pyproject_version(repo / "pyproject.toml") == "0.0.2", "version still updated")

		print("[release_prepare] package zip applies copyright before compression")
		scripts_dir = repo / "scripts"
		scripts_dir.mkdir(parents=True, exist_ok=True)
		shutil.copy2(
			os.path.join(_PROJECT_ROOT, "scripts", "add_copyright.py"),
			scripts_dir / "add_copyright.py",
		)
		docs_dir = repo / "docs"
		docs_dir.mkdir(parents=True, exist_ok=True)
		notice_src = os.path.join(_PROJECT_ROOT, "docs", "COPYRIGHT")
		if os.path.isfile(notice_src):
			shutil.copy2(notice_src, docs_dir / "COPYRIGHT")
		else:
			(docs_dir / "COPYRIGHT").write_text(
				"Copyright (c) 2026 Monster Spawned Studios All Rights Reserved.\n",
				encoding="utf-8",
			)
		utils_dir = repo / "utils"
		utils_dir.mkdir(parents=True, exist_ok=True)
		(utils_dir / "sample.py").write_text("print('hi')\n", encoding="utf-8")
		zip_path = repo / "MSS-Login-v0.0.2.zip"
		rp.package_release_zip(repo, "0.0.2", zip_path)
		ok(zip_path.is_file(), "zip written")
		with zipfile.ZipFile(zip_path) as archive:
			names = set(archive.namelist())
			ok("utils/sample.py" in names, "zip contains staged python file")
			ok("web/html/login.html" in names, "zip contains login page")
			sample = archive.read("utils/sample.py").decode("utf-8")
			ok("Copyright" in sample, "copyright header applied before zip")
			ok("print('hi')" in sample, "original file body preserved")
			login = archive.read("web/html/login.html").decode("utf-8")
			ok("MSS-Login - v0.0.2" in login, "zip login page has stamped version")

	print(f"\n[release_prepare] {run - failed}/{run} passed")
	return 1 if failed else 0


if __name__ == "__main__":
	sys.exit(run_tests())
