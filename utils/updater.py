# --- START OF FILE utils/updater.py ---
"""
Auto-update check for mss-login: notify by default, optional auto-pull.
Runs in background on startup; does not block ComfyUI. Compatible with ComfyUI Manager.
"""

import asyncio
import json
import os
from os.path import join
import shutil
import subprocess
import sys
from time import time
from typing import Any


def _read_config_json(path: str) -> dict:
	"""Read config.json at path; return {} if missing or invalid. Avoids importing utils.config (import path issues when loaded via importlib)."""
	if os.path.isfile(path):
		try:
			with open(path, "r") as f:
				return json.load(f)
		except (json.JSONDecodeError, OSError):
			pass
	return {}


_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_LAST_CHECK_PATH = os.path.join(_REPO_ROOT, ".last_update_check")
_CACHE: dict[str, Any] = {}

# DEBUG_MODE: load from environment (Docker/Compose) then config.json for diagnosis
DEBUG_MODE_FROM_ENV = str(os.environ.get("DEBUG_MODE", "")).strip().lower() in ("1", "true", "yes")
_config_for_updater = _read_config_json(join(_REPO_ROOT, "config.json")) or _read_config_json(
	join(_REPO_ROOT, "config.defaults.json")
)
DEBUG_MODE = DEBUG_MODE_FROM_ENV or bool(_config_for_updater.get("debug_mode", False))


def get_local_version() -> str:
	"""Read canonical version from pyproject.toml [project] version."""
	try:
		path = os.path.join(_REPO_ROOT, "pyproject.toml")
		if os.path.isfile(path):
			with open(path, "r", encoding="utf-8") as f:
				for line in f:
					line = line.strip()
					if line.startswith("version") and "=" in line:
						# version = "0.0.1" or version = '0.0.1'
						val = line.split("=", 1)[1].strip().strip("\"'")
						return val or "0.0.0"
	except Exception:
		pass
	return "0.0.0"


def _parse_version(s: str) -> Any:
	"""Parse version string for comparison; use packaging if available."""
	try:
		from packaging.version import Version

		return Version(s)
	except ImportError:
		# Fallback: simple tuple comparison (e.g. "0.0.2" > "0.0.1")
		parts = []
		for c in s:
			if c.isdigit():
				parts.append(c)
			elif c in ".-" and parts and parts[-1].isdigit():
				parts.append(".")
			else:
				parts.append(" ")
		num_str = "".join(parts).replace(" ", "").strip(".")
		return tuple(int(x) for x in num_str.split(".") if x.isdigit()) if num_str else (0, 0, 0)


def _version_gt(local: str, remote: str) -> bool:
	"""Return True if remote is strictly greater than local."""
	try:
		from packaging.version import Version

		return Version(remote) > Version(local)
	except Exception:
		pass
	try:
		return _parse_version(remote) > _parse_version(local)
	except Exception:
		return False


async def check_for_update(
	check_url: str, local_version: str | None = None, timeout_sec: float = 10.0
) -> tuple[bool, str]:
	"""
	Fetch remote version from check_url. URL can be GitHub releases/latest (single
	object), GitLab tags API (list), or a version.json (dict with "version").
	Returns (update_available, latest_version_str). On any error returns (False, "").
	"""
	local = local_version or get_local_version()
	latest = ""
	try:
		import aiohttp

		async with aiohttp.ClientSession() as session:
			async with session.get(
				check_url, timeout=aiohttp.ClientTimeout(total=timeout_sec)
			) as resp:
				if resp.status != 200:
					return False, ""
				body = await resp.text()
		data = json.loads(body)
		release_url = ""

		if isinstance(data, list) and len(data) > 0:
			# GitLab tags API: find highest version among tags
			candidates = []
			for item in data:
				name = (item.get("name") or item.get("tag_name") or "").lstrip("v").strip()
				if name and _parse_version(name) is not None:
					candidates.append(name)
			latest = max(candidates, key=lambda x: _parse_version(x)) if candidates else ""
		elif isinstance(data, dict):
			# GitHub releases/latest (single object) or version.json
			latest = (data.get("version") or data.get("tag_name") or "").lstrip("v").strip()
			release_url = (data.get("html_url") or "").strip()
			_CACHE["release_body"] = (data.get("body") or "").strip()
		else:
			latest = ""

		if not latest:
			return False, ""
		_CACHE["latest_version"] = latest
		_CACHE["current_version"] = local
		if release_url:
			_CACHE["release_url"] = release_url
		return _version_gt(local, latest), latest
	except Exception:
		return False, ""


def _parse_version_from_content(body: str, content_type: str = "") -> str:
	"""
	Extract version from response body: JSON with "version" key, or pyproject.toml-style
	(version = "x.y.z" or version = 'x.y.z'). Returns empty string if not found.
	"""
	body = (body or "").strip()
	if not body:
		return ""
	if "json" in (content_type or "").lower() or body.startswith("{"):
		try:
			data = json.loads(body)
			v = (data.get("version") or "").strip().lstrip("v")
			return v if v else ""
		except Exception:
			pass
	import re

	for pattern in (r'version\s*=\s*["\']([^"\']+)["\']', r'"version"\s*:\s*["\']([^"\']+)["\']'):
		m = re.search(pattern, body)
		if m:
			return m.group(1).strip().lstrip("v") or ""
	return ""


async def check_for_update_branch(
	check_url: str, local_version: str | None = None, timeout_sec: float = 10.0
) -> tuple[bool, str]:
	"""
	Fetch version from a branch URL (e.g. raw pyproject.toml or version.json).
	Returns (update_available, latest_version_str). On any error returns (False, "").
	"""
	local = local_version or get_local_version()
	latest = ""
	try:
		import aiohttp

		async with aiohttp.ClientSession() as session:
			async with session.get(
				check_url, timeout=aiohttp.ClientTimeout(total=timeout_sec)
			) as resp:
				if resp.status != 200:
					return False, ""
				body = await resp.text()
				ct = resp.headers.get("Content-Type") or ""
		latest = _parse_version_from_content(body, ct)
		if not latest:
			return False, ""
		_CACHE["latest_version"] = latest
		_CACHE["current_version"] = local
		return _version_gt(local, latest), latest
	except Exception:
		return False, ""


def get_cached_status() -> dict[str, Any]:
	"""Return last cached update check result for API route."""
	return {
		"current_version": _CACHE.get("current_version") or get_local_version(),
		"latest_version": _CACHE.get("latest_version") or "",
		"update_available": _CACHE.get("update_available", False),
		"mode": _CACHE.get("mode", "notify"),
		"changelog_url": _CACHE.get("changelog_url", ""),
		"release_url": _CACHE.get("release_url", ""),
		"changelog_body": _CACHE.get("changelog_body", ""),
	}


def _extract_github_repo_from_url(check_url: str) -> tuple[str | None, str | None]:
	"""Extract (owner, repo) from GitHub API URL like .../repos/OWNER/REPO/..."""
	if not check_url or "github.com" not in check_url:
		return None, None
	try:
		if "api.github.com/repos/" in check_url:
			parts = check_url.split("api.github.com/repos/", 1)[-1].strip("/").split("/")
			if len(parts) >= 2:
				return parts[0], parts[1]
		if "raw.githubusercontent.com/" in check_url:
			parts = check_url.split("raw.githubusercontent.com/", 1)[-1].strip("/").split("/")
			if len(parts) >= 2:
				return parts[0], parts[1]
	except Exception:
		pass
	return None, None


async def _fetch_changelog_markdown(
	version: str, check_url: str, check_mode: str, branch: str, timeout_sec: float = 8.0
) -> str:
	"""
	Try to load changelog from readme/changelogs/X.X.X.md (raw GitHub/Gitea/GitLab),
	then fall back to release body already in _CACHE (releases mode).
	"""
	version = (version or "").strip().lstrip("v")
	if not version:
		return _CACHE.get("release_body", "")
	owner, repo = _extract_github_repo_from_url(check_url)
	if not owner or not repo:
		return _CACHE.get("release_body", "")
	ref = version if (check_mode or "releases").strip().lower() != "branch" else (branch or "main")
	raw_url = (
		f"https://raw.githubusercontent.com/{owner}/{repo}/{ref}/readme/changelogs/{version}.md"
	)
	try:
		import aiohttp

		async with aiohttp.ClientSession() as session:
			async with session.get(
				raw_url, timeout=aiohttp.ClientTimeout(total=timeout_sec)
			) as resp:
				if resp.status == 200:
					body = await resp.text()
					if body and body.strip():
						return body.strip()
	except Exception:
		pass
	return _CACHE.get("release_body", "")


def backup_before_update(data_dir: str) -> str | None:
	"""Copy data directory to data_dir/backups/<timestamp>. Returns backup path or None on failure."""
	try:
		from datetime import datetime

		backups_dir = os.path.join(data_dir, "backups")
		os.makedirs(backups_dir, exist_ok=True)
		ts = datetime.utcnow().strftime("%Y%m%d_%H%M%S")
		dest = os.path.join(backups_dir, ts)
		if os.path.isdir(data_dir):
			shutil.copytree(
				data_dir, dest, dirs_exist_ok=False, ignore=shutil.ignore_patterns("backups")
			)
		return dest
	except Exception:
		return None


def perform_update(repo_root: str, data_dir: str, logger: Any) -> tuple[bool, str]:
	"""
	Run git pull and pip install. Backup data dir first. On failure, attempt rollback.
	Returns (success, message).
	"""
	backup_path = backup_before_update(data_dir)
	if not backup_path and os.path.isdir(os.path.join(data_dir, "data")):
		logger.warning("[mss-login] Update backup skipped (backup dir creation failed).")
	try:
		# Stash local changes if any
		try:
			subprocess.run(
				["git", "stash", "push", "-m", "mss_login_auto_update"],
				cwd=repo_root,
				capture_output=True,
				timeout=30,
				check=False,
			)
		except Exception:
			pass
		# Pull
		r = subprocess.run(
			["git", "pull", "origin", "--no-edit"],
			cwd=repo_root,
			capture_output=True,
			timeout=60,
			text=True,
		)
		if r.returncode != 0:
			# Rollback
			try:
				subprocess.run(
					["git", "merge", "--abort"], cwd=repo_root, capture_output=True, timeout=10
				)
			except Exception:
				pass
			try:
				subprocess.run(
					["git", "reset", "--hard", "HEAD@{1}"],
					cwd=repo_root,
					capture_output=True,
					timeout=10,
				)
			except Exception:
				pass
			return False, r.stderr or r.stdout or "git pull failed"
		# Optional: pip install -r requirements.txt
		req = os.path.join(repo_root, "requirements.txt")
		if os.path.isfile(req):
			try:
				subprocess.run(
					[sys.executable, "-m", "pip", "install", "-r", req, "-q"],
					cwd=repo_root,
					capture_output=True,
					timeout=120,
					check=False,
				)
				if DEBUG_MODE:
					print("[mss-login] Auto-update: installed dependencies from requirements.txt")
				subprocess.run(
					[sys.executable, "-m", "uv", "sync", "--group", "production"],
					cwd=repo_root,
					capture_output=True,
					timeout=600,
					check=False,
				)
				if DEBUG_MODE:
					print("[mss-login] Auto-update: installed dependencies from pyproject.toml")
			except Exception:
				print(
					f"[mss-login] Auto-update: failed to install dependencies from dependency files. Error:\n{sys.exc_info()[1]}"
				)
				return (
					False,
					"Failed to install dependencies from dependency files. Please check the logs for more information.",
				)
		return True, "Update applied. Restart ComfyUI to use the new version."
	except subprocess.TimeoutExpired:
		return False, "Update timed out."
	except Exception as e:
		return False, str(e)


def perform_recovery_update(
	repo_root: str, data_dir: str, logger: Any, branch: str = "development"
) -> tuple[bool, str]:
	"""Last-resort recovery update for repeated experimental failures.

	Security invariant: never delete or rewrite external data-dir credentials/users DB.
	This routine only manipulates git-tracked files under repo_root.
	"""
	if not os.path.isdir(repo_root):
		return False, "Repository path is missing."
	if not os.path.isdir(data_dir):
		logger.warning(
			"[mss-login] Recovery update: data dir missing, continuing without data backup."
		)
	backup_path = backup_before_update(data_dir) if os.path.isdir(data_dir) else None
	if os.path.isdir(data_dir) and not backup_path:
		logger.warning(
			"[mss-login] Recovery update: data backup failed; aborting to protect credentials."
		)
		return False, "Recovery aborted because data backup failed."
	try:
		fetch = subprocess.run(
			["git", "fetch", "origin", branch],
			cwd=repo_root,
			capture_output=True,
			timeout=60,
			text=True,
			check=False,
		)
		if fetch.returncode != 0:
			return False, fetch.stderr or fetch.stdout or "git fetch failed"

		hard_reset = subprocess.run(
			["git", "reset", "--hard", f"origin/{branch}"],
			cwd=repo_root,
			capture_output=True,
			timeout=30,
			text=True,
			check=False,
		)
		if hard_reset.returncode != 0:
			return False, hard_reset.stderr or hard_reset.stdout or "git reset failed"

		success, msg = perform_update(repo_root, data_dir, logger)
		if success:
			return True, "Recovery update succeeded. Credentials/user data were preserved."
		return False, f"Recovery update post-reset failed: {msg}"
	except subprocess.TimeoutExpired:
		return False, "Recovery update timed out."
	except Exception as exc:
		return False, str(exc)


async def run_update_check(app: Any, logger: Any, config: dict[str, Any]) -> None:
	"""
	Background task: run version check (and optionally auto-update) according to config.
	Does not block; errors are logged only.
	"""
	au = config.get("auto_update") or {}
	if not isinstance(au, dict):
		au = _config_for_updater.get("auto_update") or {}
	if not isinstance(au, dict):
		return
	enabled = au.get("enabled", True)
	if not enabled:
		return
	if not isinstance(au, dict):
		return
	mode = (au.get("mode") or "notify").strip().lower()
	if mode not in ("notify", "auto"):
		return
	check_url = (au.get("check_url") or "").strip()
	if not check_url:
		return
	interval_hours = max(0, int(au.get("check_interval_hours") or 24))
	# Skip if we checked recently
	try:
		if os.path.isfile(_LAST_CHECK_PATH):
			with open(_LAST_CHECK_PATH, "r", encoding="utf-8") as f:
				last = float(f.read().strip())
			if time() - last < interval_hours * 3600:
				return
	except Exception:
		pass
	try:
		with open(_LAST_CHECK_PATH, "w", encoding="utf-8") as f:
			f.write(str(time()))
	except Exception:
		pass
	check_mode = (au.get("check_mode") or "releases").strip().lower()
	# Run check (non-blocking)
	try:
		if check_mode == "branch":
			available, latest = await check_for_update_branch(check_url)
		else:
			available, latest = await check_for_update(check_url)
		_CACHE["update_available"] = available
		_CACHE["mode"] = mode
		_CACHE["changelog_url"] = (au.get("changelog_url") or "").strip()
		branch = (au.get("branch") or "main").strip()
		if latest:
			_CACHE["changelog_body"] = await _fetch_changelog_markdown(
				latest, check_url, check_mode, branch
			)
		else:
			_CACHE["changelog_body"] = _CACHE.get("release_body", "")
		# release_url is set inside check_for_update when GitHub API returns html_url
		if available and latest:
			logger.info(
				f"[mss-login] Update available: {latest} (current: {get_local_version()}). "
				"Restart ComfyUI and update via ComfyUI Manager or git pull, or enable auto_update to update automatically."
			)
			if mode == "auto":
				from .data_dir import get_data_dir

				loop = asyncio.get_event_loop()
				success, msg = await loop.run_in_executor(
					None, lambda: perform_update(_REPO_ROOT, get_data_dir(), logger)
				)
				if success:
					logger.info(f"[mss-login] {msg}")
				else:
					logger.warning(f"[mss-login] Auto-update failed: {msg}")
	except Exception as e:
		logger.debug(f"[mss-login] Update check failed: {e}")


async def update_check_loop(app: Any, logger: Any, config_file_path: str) -> None:
	"""Recurring background task that checks for updates on the configured interval.

	Re-reads config.json on each iteration so runtime changes to auto_update
	settings (interval, mode, enabled) take effect without a restart.
	"""
	while True:
		try:
			cfg = {}
			if os.path.isfile(config_file_path):
				try:
					with open(config_file_path, "r", encoding="utf-8") as f:
						cfg = json.load(f)
				except Exception:
					pass
			au = cfg.get("auto_update") or {}
			if not isinstance(au, dict) or not au.get("enabled", True):
				await asyncio.sleep(3600)
				continue

			interval_hours = max(1, int(au.get("check_interval_hours") or 24))
			await run_update_check(app, logger, cfg)
			await asyncio.sleep(interval_hours * 3600)
		except asyncio.CancelledError:
			break
		except Exception as exc:
			logger.debug(f"[mss-login] Update loop error: {exc}")
			await asyncio.sleep(3600)
