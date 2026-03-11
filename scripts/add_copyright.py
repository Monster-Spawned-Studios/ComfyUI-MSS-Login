#!/usr/bin/env python3
"""
Add or update copyright headers in source files from a COPYRIGHT file.

Reads copyright text from docs/COPYRIGHT (or --copyright-file), ensures the
year matches the current year (from network Date header or system time),
then inserts/updates a comment block at the top of each supported file type.
If the year in the COPYRIGHT file does not match, it is updated in place
(and in all inserted headers).

Intended for CI/CD (e.g. GitHub Actions).
  --dry-run   Report what would be changed without writing.
  --check     Like --dry-run but exit 1 if any file would be modified (fail CI).

Usage:
    python scripts/add_copyright.py [--dry-run | --check] [--copyright-file PATH] [ROOT]
"""

from __future__ import annotations

import argparse
import re
import sys
import urllib.request
from datetime import datetime
from email.utils import parsedate_to_datetime
from pathlib import Path

# Default root: parent of directory containing this script
SCRIPT_DIR = Path(__file__).resolve().parent
DEFAULT_ROOT = SCRIPT_DIR.parent
DEFAULT_COPYRIGHT_FILE = DEFAULT_ROOT / "docs" / "COPYRIGHT"

# Extensions and how to wrap the copyright text.
# "line" = each line prefixed (e.g. # or //); "block" = multi-line block (/* */ or <!-- -->).
COMMENT_STYLES: dict[str, tuple[str, str, str]] = {
	"block_py": ("# ", "\n", "line"),  # Python, shell, YAML: # per line
	"block_js": (
		"/*\n * ",
		"\n */",
		"block",
	),  # JS/TS/CSS: /* \n * line \n * line \n */
	"block_html": ("<!--\n", "\n-->", "block"),  # HTML: <!-- \n line \n line \n -->
}

# Map file extension -> style key
EXTENSION_STYLE: dict[str, str] = {
	".py": "block_py",
	".js": "block_js",
	".ts": "block_js",
	".mjs": "block_js",
	".cjs": "block_js",
	".css": "block_js",
	".html": "block_html",
	".sh": "block_py",
	".yml": "block_py",
	".yaml": "block_py",
}

# Directories to skip when walking
SKIP_DIRS = {".git", "__pycache__", "node_modules", ".venv", "site", "venv"}

# Regex to detect an existing copyright year (e.g. "© 2026" or "Copyright 2026")
YEAR_PATTERN = re.compile(r"(Copyright\s+©?\s*)(\d{4})(\s|$)", re.IGNORECASE)


def get_current_year() -> int:
	"""Return current year from a remote Date header, or system time as fallback."""
	try:
		req = urllib.request.Request(
			"https://www.python.org",
			method="HEAD",
			headers={"User-Agent": "ComfyUI-MSS-Login-add-copyright/1.0"},
		)
		with urllib.request.urlopen(req, timeout=5) as resp:
			raw = resp.headers.get("Date")
			if raw:
				dt = parsedate_to_datetime(raw)
				return dt.year
	except Exception as e:
		print(
			f"[MSS-Login] Failed to get current year from remote Date header, using system time as fallback: {e}",
			file=sys.stderr,
		)
		return datetime.now().year
	return datetime.now().year


def normalize_year_in_text(text: str, current_year: int) -> str:
	"""Replace the first copyright year in text with current_year if different."""

	def repl(match: re.Match[str]) -> str:
		prefix, year, suffix = match.group(1), match.group(2), match.group(3)
		if year == str(current_year):
			return match.group(0)
		return f"{prefix}{current_year}{suffix}"

	return YEAR_PATTERN.sub(repl, text, count=1)


def wrap_comment(copyright_text: str, style_key: str) -> str:
	"""Wrap copyright lines in the comment style for the given key."""
	prefix, suffix, kind = COMMENT_STYLES[style_key]
	lines = [line.strip() for line in copyright_text.splitlines() if line.strip()]
	if kind == "line":
		return "\n".join(prefix + line for line in lines) + "\n"
	# block: opening, then each line with block line prefix, then closing
	if style_key == "block_js":
		open_ = "/*"
		line_prefix = " * "
		close_ = " */"
	elif style_key == "block_html":
		open_ = "<!--"
		line_prefix = " "  # one space so content is inside the comment
		close_ = "-->"
	else:
		open_ = prefix.strip()
		line_prefix = prefix.rstrip()
		close_ = suffix
	body = "\n".join(line_prefix + line for line in lines)
	return f"{open_}\n{body}\n{close_}\n"


def has_shebang(content: str) -> bool:
	"""Return True if content starts with a shebang line."""
	return content.lstrip().startswith("#!")


def get_shebang_and_rest(content: str) -> tuple[str, str]:
	"""Split content into first shebang line (plus optional blank) and the rest."""
	lines = content.splitlines(keepends=True)
	if not lines:
		return "", ""
	first = lines[0]
	if not first.lstrip().startswith("#!"):
		return "", content
	rest_start = 1
	while rest_start < len(lines) and lines[rest_start].strip() == "":
		rest_start += 1
	return "".join(lines[:rest_start]), "".join(lines[rest_start:])


def already_has_copyright(content: str, copyright_lines: list[str]) -> bool:
	"""Return True if content already contains the copyright block (by key lines)."""
	if not copyright_lines:
		return False
	first_line = copyright_lines[0].strip()
	if not first_line:
		return False
	# Check first ~2KB for copyright-like content
	head = content[:2048]
	return first_line in head and (
		"monsterspawned" in head.lower()
		or "Monster Spawned" in head
		or "All Rights Reserved" in head
	)


def insert_header(content: str, header: str, ext: str) -> str:
	"""Insert header at the top of content. Preserve shebang for .py and .sh."""
	style_key = EXTENSION_STYLE.get(ext, "block_py")
	if style_key == "block_py" and has_shebang(content):
		shebang, rest = get_shebang_and_rest(content)
		if rest and not rest.startswith("\n"):
			sep = "\n"
		else:
			sep = ""
		return shebang + header + sep + rest
	return header + content


def collect_files(root: Path) -> list[tuple[Path, str]]:
	"""Return list of (path, extension) for supported files under root."""
	out: list[tuple[Path, str]] = []
	for path in root.rglob("*"):
		if not path.is_file():
			continue
		if any(part in SKIP_DIRS for part in path.parts):
			continue
		ext = path.suffix.lower()
		if ext in EXTENSION_STYLE:
			out.append((path, ext))
	return sorted(out)


def main() -> int:
	"""Main function to add or update copyright headers from a COPYRIGHT file."""
	parser = argparse.ArgumentParser(
		description="Add or update copyright headers from a COPYRIGHT file.",
	)
	parser.add_argument(
		"root",
		nargs="?",
		type=Path,
		default=DEFAULT_ROOT,
		help=f"Project root to scan (default: {DEFAULT_ROOT})",
	)
	parser.add_argument(
		"--copyright-file",
		type=Path,
		default=DEFAULT_COPYRIGHT_FILE,
		help="Path to COPYRIGHT file (default: docs/COPYRIGHT under root)",
	)
	parser.add_argument(
		"--dry-run",
		action="store_true",
		help="Only report what would be changed; do not write files",
	)
	parser.add_argument(
		"--check",
		action="store_true",
		help="Like --dry-run but exit 1 if any file would be modified (for CI)",
	)
	args = parser.parse_args()
	if args.check:
		args.dry_run = True
	root = args.root.resolve()
	copyright_path = args.copyright_file.resolve()
	if not root.is_dir():
		print(f"Error: root is not a directory: {root}", file=sys.stderr)
		return 1
	if not copyright_path.is_file():
		print(f"Error: COPYRIGHT file not found: {copyright_path}", file=sys.stderr)
		return 1

	current_year = get_current_year()
	raw_copyright = copyright_path.read_text(encoding="utf-8").strip()
	copyright_text = normalize_year_in_text(raw_copyright, current_year)
	if copyright_text != raw_copyright:
		if args.dry_run:
			print(f"Dry run: would update year in {copyright_path.relative_to(root)}")
		else:
			copyright_path.write_text(copyright_text + "\n", encoding="utf-8")
			print(f"Updated year in {copyright_path.relative_to(root)}")
	copyright_lines = [s.strip() for s in copyright_text.splitlines() if s.strip()]

	files = collect_files(root)
	modified: list[Path] = []
	skipped: list[tuple[Path, str]] = []

	for path, ext in files:
		try:
			content = path.read_text(encoding="utf-8", errors="replace")
		except Exception as e:
			print(f"Warning: could not read {path}: {e}", file=sys.stderr)
			skipped.append((path, str(e)))
			continue
		if already_has_copyright(content, copyright_lines):
			skipped.append((path, "already has copyright"))
			continue
		style_key = EXTENSION_STYLE[ext]
		header = wrap_comment(copyright_text, style_key)
		new_content = insert_header(content, header, ext)
		if new_content != content:
			modified.append(path)
			if not args.dry_run:
				path.write_text(new_content, encoding="utf-8", newline="")

	if args.dry_run:
		if modified:
			print("Dry run: would add/update copyright in:")
			for p in modified:
				print(f"  {p.relative_to(root)}")
			return 1 if args.check else 0  # --check: fail CI when updates needed
		print("Dry run: no files need copyright added.")
		return 0

	for p in modified:
		print(f"Updated: {p.relative_to(root)}")
	if not modified:
		print("No files modified.")
	return 0


if __name__ == "__main__":
	sys.exit(main())
