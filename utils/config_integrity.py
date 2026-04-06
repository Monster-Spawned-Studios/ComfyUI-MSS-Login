"""Config file integrity verification using SHA-256 hashes.

Computes and stores hashes for repo-shipped config templates
(config.defaults.json, default_group_config.json) so local
tampering can be detected on startup. Optionally verifies
against the GitHub repository during the update check cycle.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
from typing import Any

logger = logging.getLogger("mss-login.config_integrity")

_TRACKED_FILES = [
    "config.defaults.json",
    os.path.join("users", "defaults", "default_group_config.json"),
]

_HASH_STORE_FILENAME = ".config_hashes.json"


def compute_file_hash(path: str) -> str:
    """Return the hex SHA-256 digest of a file's contents, or empty string on error."""
    try:
        sha = hashlib.sha256()
        with open(path, "rb") as f:
            for chunk in iter(lambda: f.read(8192), b""):
                sha.update(chunk)
        return sha.hexdigest()
    except (OSError, IOError):
        return ""


def _hash_store_path(data_dir: str) -> str:
    return os.path.join(data_dir, "data", _HASH_STORE_FILENAME)


def store_hashes(repo_root: str, data_dir: str) -> dict[str, str]:
    """Compute and persist SHA-256 hashes for tracked config files."""
    hashes: dict[str, str] = {}
    for rel in _TRACKED_FILES:
        full = os.path.join(repo_root, rel)
        h = compute_file_hash(full)
        if h:
            hashes[rel] = h

    store_path = _hash_store_path(data_dir)
    os.makedirs(os.path.dirname(store_path), exist_ok=True)
    try:
        with open(store_path, "w", encoding="utf-8") as f:
            json.dump(hashes, f, indent="\t")
    except OSError as exc:
        logger.warning("[mss-login] Failed to write config hashes: %s", exc)

    return hashes


def _load_stored_hashes(data_dir: str) -> dict[str, str]:
    store_path = _hash_store_path(data_dir)
    if not os.path.isfile(store_path):
        return {}
    try:
        with open(store_path, "r", encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except (OSError, json.JSONDecodeError):
        return {}


def verify_local_hashes(repo_root: str, data_dir: str) -> list[dict[str, Any]]:
    """Compare current file hashes against stored hashes.

    Returns a list of dicts for each mismatch:
      {"file": <relative path>, "expected": <stored hash>, "actual": <current hash>}

    Returns an empty list when everything matches or no stored hashes exist.
    """
    stored = _load_stored_hashes(data_dir)
    if not stored:
        store_hashes(repo_root, data_dir)
        return []

    mismatches: list[dict[str, Any]] = []
    for rel, expected in stored.items():
        full = os.path.join(repo_root, rel)
        actual = compute_file_hash(full)
        if actual and actual != expected:
            mismatches.append({"file": rel, "expected": expected, "actual": actual})

    return mismatches


async def verify_remote_hashes(
    repo_root: str,
    data_dir: str,
    check_url: str,
    branch: str = "main",
    timeout_sec: float = 10.0,
) -> list[dict[str, Any]]:
    """Fetch config file contents from GitHub and compare against local files.

    Uses the GitHub raw URL derived from the auto_update check_url config.
    Returns a list of dicts for mismatches (same format as verify_local_hashes),
    or empty list when everything matches or the fetch fails.
    """
    owner, repo = _extract_github_owner_repo(check_url)
    if not owner or not repo:
        return []

    mismatches: list[dict[str, Any]] = []
    try:
        import aiohttp

        async with aiohttp.ClientSession() as session:
            for rel in _TRACKED_FILES:
                url_path = rel.replace(os.sep, "/")
                raw_url = f"https://raw.githubusercontent.com/{owner}/{repo}/{branch}/{url_path}"
                try:
                    async with session.get(
                        raw_url, timeout=aiohttp.ClientTimeout(total=timeout_sec)
                    ) as resp:
                        if resp.status != 200:
                            continue
                        remote_bytes = await resp.read()
                except Exception:
                    continue

                remote_hash = hashlib.sha256(remote_bytes).hexdigest()
                local_hash = compute_file_hash(os.path.join(repo_root, rel))
                if local_hash and local_hash != remote_hash:
                    mismatches.append(
                        {
                            "file": rel,
                            "expected": remote_hash,
                            "actual": local_hash,
                            "source": "github",
                        }
                    )
    except ImportError:
        pass

    return mismatches


def _extract_github_owner_repo(check_url: str) -> tuple[str | None, str | None]:
    """Extract (owner, repo) from a GitHub API or raw URL."""
    if not check_url or "github.com" not in check_url:
        return None, None
    try:
        for prefix in ("api.github.com/repos/", "raw.githubusercontent.com/"):
            if prefix in check_url:
                parts = check_url.split(prefix, 1)[-1].strip("/").split("/")
                if len(parts) >= 2:
                    return parts[0], parts[1]
    except Exception:
        pass
    return None, None
