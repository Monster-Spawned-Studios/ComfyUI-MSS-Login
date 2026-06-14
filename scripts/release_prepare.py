"""
Prepare a release: sync pyproject.toml version and create changelog from git history.

Used locally and in CI (.github/workflows/create-release.yml).
"""

from __future__ import annotations

import argparse
import re
import subprocess
import sys
from pathlib import Path

_VERSION_RE = re.compile(r"^[0-9]+\.[0-9]+\.[0-9]+(-[a-zA-Z0-9.]+)?(\+[a-zA-Z0-9.]+)?$")
_SEMVER_TAG_RE = re.compile(r"^v?([0-9]+\.[0-9]+\.[0-9]+)(?:[-+].*)?$")
_VERSION_LINE_RE = re.compile(r'^(\s*version\s*=\s*["\'])([^"\']+)(["\']\s*)$', re.MULTILINE)


def _repo_root(explicit: str | None) -> Path:
	if explicit:
		return Path(explicit).resolve()
	return Path(__file__).resolve().parent.parent


def validate_version(version: str) -> str:
	v = (version or "").strip().lstrip("v")
	if not v or not _VERSION_RE.match(v):
		raise ValueError(f"Invalid version format (expected X.Y.Z): {version!r}")
	if "/" in v or "\\" in v or ".." in v:
		raise ValueError(f"Unsafe version string: {version!r}")
	return v


def read_pyproject_version(pyproject_path: Path) -> str:
	text = pyproject_path.read_text(encoding="utf-8")
	match = _VERSION_LINE_RE.search(text)
	if not match:
		raise ValueError(f"No version line found in {pyproject_path}")
	return match.group(2).strip()


def write_pyproject_version(pyproject_path: Path, version: str) -> bool:
	text = pyproject_path.read_text(encoding="utf-8")
	match = _VERSION_LINE_RE.search(text)
	if not match:
		raise ValueError(f"No version line found in {pyproject_path}")
	current = match.group(2).strip()
	if current == version:
		return False
	new_text = _VERSION_LINE_RE.sub(lambda m: f"{m.group(1)}{version}{m.group(3)}", text, count=1)
	pyproject_path.write_text(new_text, encoding="utf-8")
	return True


def _run_git(args: list[str], cwd: Path) -> subprocess.CompletedProcess[str]:
	return subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True, check=False)


def latest_semver_tag(repo_root: Path) -> str | None:
	result = _run_git(["tag", "-l", "--sort=-v:refname"], repo_root)
	if result.returncode != 0:
		return None
	for line in result.stdout.splitlines():
		line = line.strip()
		if not line:
			continue
		match = _SEMVER_TAG_RE.match(line)
		if match:
			return match.group(1)
	return None


def git_log_bullets(repo_root: Path, version: str) -> list[str]:
	prev = latest_semver_tag(repo_root)
	if prev:
		if prev == version:
			result = _run_git(["log", "-1", "--no-merges", "--pretty=format:%s (%h)"], repo_root)
		else:
			result = _run_git(
				["log", f"{prev}..HEAD", "--no-merges", "--pretty=format:%s (%h)"], repo_root
			)
	else:
		result = _run_git(["log", "--no-merges", "--pretty=format:%s (%h)"], repo_root)
	if result.returncode != 0:
		return ["- Release preparation (no git history available)"]
	lines = [line.strip() for line in result.stdout.splitlines() if line.strip()]
	if not lines:
		return [f"- Release v{version}"]
	return [f"- {line}" if not line.startswith("- ") else line for line in lines]


def default_changelog_title(repo_root: Path, version: str) -> str:
	bullets = git_log_bullets(repo_root, version)
	if bullets:
		first = bullets[0].lstrip("- ").strip()
		if " (" in first:
			first = first.rsplit(" (", 1)[0].strip()
		if first and first.lower() != f"release v{version}".lower():
			return first[:120]
	return f"Release v{version}"


def changelog_path(repo_root: Path, version: str) -> Path:
	return repo_root / "readme" / "changelogs" / f"{version}.md"


def build_changelog_body(repo_root: Path, version: str, title: str, notes: str | None) -> str:
	bullets = git_log_bullets(repo_root, version)
	sections = [f"# {version} - {title}", "", "## Changelog", "", *bullets]
	if notes and notes.strip():
		sections.extend(["", *notes.strip().splitlines()])
	sections.append("")
	return "\n".join(sections)


def prepare_release(
	repo_root: Path,
	version: str,
	changelog_title: str | None = None,
	changelog_notes: str | None = None,
) -> bool:
	"""Update release files. Returns True if any file was modified."""
	version = validate_version(version)
	pyproject = repo_root / "pyproject.toml"
	if not pyproject.is_file():
		raise FileNotFoundError(f"pyproject.toml not found under {repo_root}")

	changed = False
	version_changed = write_pyproject_version(pyproject, version)
	changed = changed or version_changed

	cl_path = changelog_path(repo_root, version)
	cl_path.parent.mkdir(parents=True, exist_ok=True)

	if cl_path.is_file():
		return changed

	title = (changelog_title or "").strip() or default_changelog_title(repo_root, version)
	body = build_changelog_body(repo_root, version, title, changelog_notes)
	cl_path.write_text(body, encoding="utf-8")
	return True


def main(argv: list[str] | None = None) -> int:
	parser = argparse.ArgumentParser(
		description="Prepare release: sync pyproject version and create changelog from git log."
	)
	parser.add_argument("--version", required=True, help="Target release version (X.Y.Z)")
	parser.add_argument(
		"--repo-root", default=None, help="Repository root (default: parent of scripts/)"
	)
	parser.add_argument(
		"--changelog-title",
		default="",
		help="Changelog H1 suffix (default: first commit subject or Release vX.Y.Z)",
	)
	parser.add_argument(
		"--changelog-notes",
		default="",
		help="Additional markdown lines appended after auto-generated bullets",
	)
	parser.add_argument(
		"--print-version", action="store_true", help="Print current pyproject version and exit"
	)
	args = parser.parse_args(argv)

	root = _repo_root(args.repo_root)
	pyproject = root / "pyproject.toml"

	if args.print_version:
		try:
			print(read_pyproject_version(pyproject))
		except (OSError, ValueError) as exc:
			print(f"Error: {exc}", file=sys.stderr)
			return 1
		return 0

	try:
		changed = prepare_release(
			root,
			args.version,
			changelog_title=args.changelog_title or None,
			changelog_notes=args.changelog_notes or None,
		)
	except (ValueError, FileNotFoundError, OSError) as exc:
		print(f"Error: {exc}", file=sys.stderr)
		return 1

	if changed:
		print(f"Release prepared for v{validate_version(args.version)}")
	else:
		print(f"Release v{validate_version(args.version)} already prepared (no changes)")
	return 0


if __name__ == "__main__":
	sys.exit(main())
