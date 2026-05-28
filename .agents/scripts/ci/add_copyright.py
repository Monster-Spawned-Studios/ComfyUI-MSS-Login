"""Add and update file-header copyright notices for supported source files."""

from __future__ import annotations

import json
import os
import re
from argparse import ArgumentParser, Namespace
from datetime import datetime
from pathlib import Path
from typing import Optional
from urllib.error import URLError
from urllib.request import urlopen


class CopyrightInstaller:
	"""Add and update copyright headers for supported file extensions."""

	YEAR_PATTERN = re.compile(r"(19|20)\d{2}")
	COPYRIGHT_LINE_PATTERN = re.compile(
		r"(?im)^(?P<prefix>\s*(?:[#/@*!;\"'<>\-]+\s*)?)Copyright\s+(?P<holder>.+?)\s+"
		r"(?P<start>\d{4})(?:-(?P<end>\d{4}))?(?P<suffix>.*)$"
	)
	SUPPORTED_EXTENSIONS = (
		".py",
		".js",
		".ts",
		".css",
		".html",
		".xml",
		".json5",
		".jsonc",
		".yaml",
		".yml",
		".toml",
		".sh",
		".bat",
		".cmd",
		".ps1",
		".psm1",
	)
	SHEBANG_EXTENSIONS = {".py", ".sh"}
	SKIP_DIRECTORIES = {
		".git",
		".venv",
		"node_modules",
		"__pycache__",
		".mypy_cache",
		".pytest_cache",
		".ruff_cache",
	}

	def __init__(self) -> None:
		self.args = self._parse_args()
		self.notice_path = Path(self.args.copyright_notice).resolve()
		self.notice_loaded_from_file = self.notice_path.exists()
		self.notice_text = self._read_notice_text(self.notice_path)
		self.current_system_year = datetime.now().year
		self.notice_year_hint = (
			self._extract_latest_year(self.notice_text) if self.notice_loaded_from_file else None
		)

	def _parse_args(self) -> Namespace:
		parser = ArgumentParser(
			description="Add or update copyright headers on supported source files.",
			epilog=(
				"Example: add_copyright.py --directory ./src --copyright-notice ./copyright.txt "
				"--recursive --update"
			),
		)
		parser.add_argument(
			"--directory",
			"--dir",
			"-D",
			default=str(Path.cwd() / "src"),
			type=str,
			help="Directory containing files to process.",
		)
		parser.add_argument(
			"--copyright-notice",
			"--copyright",
			"-C",
			default=str(Path.cwd() / "copyright.txt"),
			type=str,
			help="Path to a notice text file. If missing, a default notice is generated.",
		)
		parser.add_argument(
			"--recursive", "-R", action="store_true", help="Process files recursively."
		)
		parser.add_argument(
			"--update",
			"-U",
			action="store_true",
			help="Update stale copyright years to a newer range when detected.",
		)
		parser.add_argument(
			"--verbose", "-V", action="store_true", help="Print per-file processing details."
		)
		parser.add_argument(
			"--silent", "-S", action="store_true", help="Suppress non-error output."
		)
		parser.add_argument(
			"--dry-run", action="store_true", help="Report changes without writing to disk."
		)
		return parser.parse_args()

	def _log(self, message: str) -> None:
		if not self.args.silent:
			print(message)

	def _verbose(self, message: str) -> None:
		if self.args.verbose and not self.args.silent:
			print(message)

	def _read_notice_text(self, notice_path: Path) -> str:
		if not notice_path.exists():
			default_year = datetime.now().year
			return f"Copyright (c) {default_year} All Rights Reserved."
		try:
			text = notice_path.read_text(encoding="utf-8").strip()
		except OSError as error:
			self._log(f"[red]Failed reading notice file '{notice_path}': {error}[/red]")
			return f"Copyright (c) {datetime.now().year} All Rights Reserved."
		return text or f"Copyright (c) {datetime.now().year} All Rights Reserved."

	def _extract_latest_year(self, text: str) -> Optional[int]:
		years = [int(match.group(0)) for match in self.YEAR_PATTERN.finditer(text or "")]
		return max(years) if years else None

	def _fetch_year_from_internet(self) -> Optional[int]:
		# Read-only year lookup with short timeout for CI stability.
		url = "https://worldtimeapi.org/api/timezone/Etc/UTC"
		try:
			with urlopen(url, timeout=3) as response:
				payload = json.loads(response.read().decode("utf-8"))
			date_time = payload.get("datetime", "")
			parsed_year = int(str(date_time)[:4])
			if 1900 <= parsed_year <= 3000:
				return parsed_year
		except (URLError, OSError, ValueError, json.JSONDecodeError, TimeoutError):
			return None
		return None

	def _resolve_target_year(self, saved_year: Optional[int]) -> int:
		# Ordered fallback: notice file -> internet -> local system.
		notice_year = self.notice_year_hint
		if notice_year is not None and (saved_year is None or notice_year > saved_year):
			self._verbose(f"[cyan]Year source: notice file ({notice_year}).[/cyan]")
			return notice_year

		internet_year = self._fetch_year_from_internet()
		if internet_year is not None and (saved_year is None or internet_year > saved_year):
			self._verbose(f"[cyan]Year source: internet ({internet_year}).[/cyan]")
			return internet_year

		self._verbose(f"[cyan]Year source: system clock ({self.current_system_year}).[/cyan]")
		return self.current_system_year

	def get_comment_template(self, file_extension: str) -> list[str]:
		if not file_extension:
			return []
		# Keep this mapping as the source of supported extensions and wrappers.
		match file_extension.lower():
			case ".py":
				return ['"""', "{copyright_notice}", '"""']
			case ".js" | ".ts" | ".css" | ".json5" | ".jsonc":
				return ["/**", "{copyright_notice}", "*/"]
			case ".html" | ".xml":
				return ["<!--", "{copyright_notice}", "-->"]
			case ".yaml" | ".yml":
				return ["#", "{copyright_notice}", "#"]
			case ".toml" | ".sh":
				return ["#", "{copyright_notice}", "#"]
			case ".bat" | ".cmd":
				return ["@REM", "{copyright_notice}", "@REM"]
			case ".ps1" | ".psm1":
				return ["#region", "{copyright_notice}", "#endregion"]
			case _:
				return []

	def _render_comment_block(self, template: list[str], notice_text: str) -> str:
		if not template:
			return ""
		start, _, end = template
		lines = [line.rstrip() for line in notice_text.splitlines()] or [notice_text.strip()]
		lines = [line for line in lines if line != ""]
		if not lines:
			lines = [f"Copyright (c) {self.current_system_year} All Rights Reserved."]

		if start in {"#", "@REM"} and start == end:
			content = "\n".join(f"{start} {line}" for line in lines)
			return f"{content}\n\n"

		if start == "#region" and end == "#endregion":
			content = "\n".join(f"# {line}" for line in lines)
			return f"{start}\n{content}\n{end}\n\n"

		content = "\n".join(lines)
		return f"{start}\n{content}\n{end}\n\n"

	def _find_insert_index(self, file_content: str, file_extension: str) -> int:
		lines = file_content.splitlines(keepends=True)
		index = 0
		if (
			lines
			and lines[0].startswith("#!")
			and file_extension.lower() in self.SHEBANG_EXTENSIONS
		):
			index = 1
		if file_extension.lower() == ".py" and len(lines) > index and "coding:" in lines[index]:
			index += 1
		return sum(len(line) for line in lines[:index])

	def _find_first_copyright_match(self, text: str) -> Optional[re.Match[str]]:
		return self.COPYRIGHT_LINE_PATTERN.search(text)

	def _update_copyright_line_if_needed(self, text: str) -> tuple[str, bool]:
		match = self._find_first_copyright_match(text)
		if match is None:
			return text, False

		start_year = int(match.group("start"))
		current_saved_year = int(match.group("end") or match.group("start"))

		if current_saved_year >= self.current_system_year or not self.args.update:
			return text, False

		target_year = self._resolve_target_year(current_saved_year)
		if target_year <= current_saved_year:
			return text, False

		holder = match.group("holder").strip()
		suffix = (match.group("suffix") or "").rstrip()
		prefix = match.group("prefix") or ""
		updated_line = f"{prefix}Copyright {holder} {current_saved_year}-{target_year}"
		if suffix:
			updated_line = f"{updated_line}{suffix if suffix.startswith(' ') else f' {suffix}'}"

		# Replace the entire matched line in a stable way.
		updated_text = text[: match.start()] + updated_line + text[match.end() :]
		self._verbose(
			f"[yellow]Updated copyright year range {start_year if match.group('end') is None else current_saved_year}-{target_year}.[/yellow]"
		)
		return updated_text, True

	def _build_notice_for_insert(self) -> str:
		notice = self.notice_text.strip()
		match = self._find_first_copyright_match(notice)
		if match is None:
			return notice

		saved_year = int(match.group("end") or match.group("start"))
		if not self.args.update or saved_year >= self.current_system_year:
			return notice

		target_year = self._resolve_target_year(saved_year)
		if target_year <= saved_year:
			return notice

		holder = match.group("holder").strip()
		suffix = (match.group("suffix") or "").rstrip()
		prefix = match.group("prefix") or ""
		updated_line = f"{prefix}Copyright {holder} {saved_year}-{target_year}"
		if suffix:
			updated_line = f"{updated_line}{suffix if suffix.startswith(' ') else f' {suffix}'}"
		return notice[: match.start()] + updated_line + notice[match.end() :]

	def _process_file(self, file_path: Path, notice_for_insert: str) -> bool:
		extension = file_path.suffix.lower()
		if extension not in self.SUPPORTED_EXTENSIONS:
			return False

		template = self.get_comment_template(extension)
		if not template:
			return False

		try:
			original_text = file_path.read_text(encoding="utf-8")
		except (OSError, UnicodeDecodeError):
			self._verbose(f"[magenta]Skipped unreadable file: {file_path}[/magenta]")
			return False

		updated_text = original_text
		changed = False

		updated_text, did_update_year = self._update_copyright_line_if_needed(updated_text)
		changed = changed or did_update_year

		if self._find_first_copyright_match(updated_text) is None:
			insert_index = self._find_insert_index(updated_text, extension)
			comment_block = self._render_comment_block(template, notice_for_insert)
			updated_text = updated_text[:insert_index] + comment_block + updated_text[insert_index:]
			changed = True
			self._verbose(f"[green]Added header: {file_path}[/green]")
		elif did_update_year:
			self._verbose(f"[green]Updated header year: {file_path}[/green]")

		if changed and not self.args.dry_run:
			try:
				file_path.write_text(updated_text, encoding="utf-8")
			except OSError as error:
				self._log(f"[red]Failed writing '{file_path}': {error}[/red]")
				return False
		return changed

	def _iter_target_files(self, root: Path):
		if self.args.recursive:
			for current_root, directory_names, file_names in os.walk(root):
				directory_names[:] = [
					name for name in directory_names if name not in self.SKIP_DIRECTORIES
				]
				for file_name in file_names:
					yield Path(current_root) / file_name
			return
		yield from (path for path in root.iterdir() if path.is_file())

	def run(self) -> None:
		directory = Path(self.args.directory).resolve()
		if not directory.exists():
			self._log(f"[red]Directory does not exist: {directory}[/red]")
			raise SystemExit(5)
		if not directory.is_dir():
			self._log(f"[red]Path is not a directory: {directory}[/red]")
			raise SystemExit(4)

		notice_for_insert = self._build_notice_for_insert()
		files_seen = 0
		files_changed = 0

		for file_path in self._iter_target_files(directory):
			files_seen += 1
			if self._process_file(file_path, notice_for_insert):
				files_changed += 1

		if self.args.dry_run:
			self._log(
				f"[cyan]Dry-run complete. Changed {files_changed} of {files_seen} scanned files.[/cyan]"
			)
			return

		self._log(
			f"[green]Completed. Changed {files_changed} of {files_seen} scanned files.[/green]"
		)


if __name__ == "__main__":
	CopyrightInstaller().run()
