"""
Prepare a release: sync pyproject.toml version and create changelog from git history.

Used locally and in CI (.github/workflows/create-release.yml).
"""

from __future__ import annotations

import argparse
import os
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


def update_readme_version(repo_root: Path, version: str) -> bool:
	readme_path = repo_root / "README.md"
	if not readme_path.is_file():
		return False
	content = readme_path.read_text(encoding="utf-8")

	pattern = re.compile(
		r"(<strong>Version\s+)([0-9]+\.[0-9]+\.[0-9]+(?:-[a-zA-Z0-9.]+)?)(</strong>)", re.IGNORECASE
	)
	match = pattern.search(content)
	if not match:
		return False

	current_version = match.group(2)
	if current_version == version:
		return False

	new_content = pattern.sub(rf"\g<1>{version}\g<3>", content, count=1)
	readme_path.write_text(new_content, encoding="utf-8")
	return True


def update_changes_markdown(repo_root: Path, version: str, title: str) -> bool:
	changes_path = repo_root / "readme" / "CHANGES.md"
	if not changes_path.is_file():
		return False
	content = changes_path.read_text(encoding="utf-8")

	version_header = f"## {version}"
	if version_header in content:
		return False

	new_entry = f"## {version} - **{title}**\n\n- Changelog can be viewed here: [v{version} Changelog](./readme/changelogs/{version}.md)\n\n"

	header_pattern = re.compile(r"^(#\s+Changes\s*\n+)", re.IGNORECASE)
	match = header_pattern.search(content)
	if not match:
		new_content = f"# Changes\n\n{new_entry}" + content
	else:
		new_content = header_pattern.sub(rf"\g<1>{new_entry}", content, count=1)

	changes_path.write_text(new_content, encoding="utf-8")
	return True


def auto_increment_version(current_version: str) -> str:
	match = _SEMVER_TAG_RE.match(current_version)
	if not match:
		raise ValueError(f"Cannot auto-increment version: {current_version!r}")
	version_str = match.group(1)
	parts = list(map(int, version_str.split(".")))
	parts[2] += 1  # Bump patch
	return ".".join(map(str, parts))


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

	title = (changelog_title or "").strip() or default_changelog_title(repo_root, version)

	if not cl_path.is_file():
		body = build_changelog_body(repo_root, version, title, changelog_notes)
		cl_path.write_text(body, encoding="utf-8")
		changed = True

	# Update README.md version
	readme_changed = update_readme_version(repo_root, version)
	changed = changed or readme_changed

	# Update readme/CHANGES.md
	changes_changed = update_changes_markdown(repo_root, version, title)
	changed = changed or changes_changed

	return changed


def main(argv: list[str] | None = None) -> int:
	parser = argparse.ArgumentParser(
		description="Prepare release: sync pyproject version and create changelog from git log."
	)
	parser.add_argument(
		"--version",
		default="",
		help="Target release version (X.Y.Z); if omitted, auto-resolves/increments",
	)
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
		version = (args.version or "").strip()
		if not version:
			# Auto-resolve version
			current = read_pyproject_version(pyproject)
			# Check if tag exists
			tag_exists = False
			result = _run_git(["tag", "-l"], root)
			if result.returncode == 0:
				tags = [t.strip() for t in result.stdout.splitlines() if t.strip()]
				if current in tags or f"v{current}" in tags:
					tag_exists = True

			if tag_exists:
				version = auto_increment_version(current)
				print(
					f"Current version v{current} is already tagged. Auto-incrementing to v{version}."
				)
			else:
				version = current
				print(f"Current version v{version} is not tagged. Using it.")
		else:
			version = validate_version(version)

		changed = prepare_release(
			root,
			version,
			changelog_title=args.changelog_title or None,
			changelog_notes=args.changelog_notes or None,
		)

		# Write version to GITHUB_OUTPUT if present
		if "GITHUB_OUTPUT" in os.environ:
			with open(os.environ["GITHUB_OUTPUT"], "a") as f:
				f.write(f"version={version}\n")

	except (ValueError, FileNotFoundError, OSError) as exc:
		print(f"Error: {exc}", file=sys.stderr)
		return 1

	if changed:
		print(f"Release prepared for v{version}")
	else:
		print(f"Release v{version} already prepared (no changes)")
	return 0


if __name__ == "__main__":
	sys.exit(main())
