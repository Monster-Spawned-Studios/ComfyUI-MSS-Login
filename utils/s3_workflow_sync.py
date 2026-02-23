# --- START OF FILE utils/s3_workflow_sync.py ---
"""
Bidirectional per-user workflow sync between local disk and S3 (experimental).

Each authenticated user gets their own ``<prefix>/users/<username>/workflows/``
prefix in S3. Workflows sync bidirectionally between the local
``Users/<username>/workflows/`` directory (managed by ``user_env.py``) and the
corresponding S3 prefix.

Uses the existing ``S3StorageClient`` (boto3) -- not rclone -- since workflows
are small JSON files that do not benefit from FUSE mounting.
"""

import os
import re
import time
import threading
from datetime import datetime, timezone
from typing import Optional

from . import user_env
from .s3_storage import S3StorageClient

_LOG_PREFIX = "[MSS-Login::S3WorkflowSync]"
_SAFE_USERNAME_RE = re.compile(r"[^a-zA-Z0-9_\-]")

# Cap to prevent syncing absurdly large files
_DEFAULT_MAX_SIZE_BYTES = 50 * 1024 * 1024  # 50 MB


def _log(msg: str) -> None:
    print(f"{_LOG_PREFIX} {msg}")


def _sanitize_username_for_s3(username: str) -> str:
    """Strip characters that could cause path traversal or S3 key issues."""
    clean = _SAFE_USERNAME_RE.sub("_", (username or "").strip())
    if not clean or clean in (".", ".."):
        return "_invalid_"
    return clean


def _parse_iso_mtime(iso_str: str) -> float:
    """Convert an ISO-8601 string (from S3 head_object) to a Unix timestamp."""
    if not iso_str:
        return 0.0
    try:
        dt = datetime.fromisoformat(iso_str.replace("Z", "+00:00"))
        return dt.timestamp()
    except (ValueError, TypeError):
        return 0.0


class S3WorkflowSync:
    """Bidirectional per-user workflow sync with S3."""

    def __init__(
        self,
        s3_client: S3StorageClient,
        s3_prefix: str,
        sync_config: dict,
        users_db=None,
    ):
        self._s3 = s3_client
        self._prefix = (s3_prefix or "").rstrip("/")
        self._users_db = users_db

        self._interval = int(sync_config.get("sync_interval_seconds") or 60)
        self._conflict_strategy = (
            sync_config.get("conflict_strategy") or "newer_wins"
        ).lower()
        self._sync_on_save = sync_config.get("sync_on_save", True)
        self._sync_on_delete = sync_config.get("sync_on_delete", True)
        max_mb = sync_config.get("max_workflow_size_mb") or 50
        self._max_size = int(max_mb) * 1024 * 1024

        self._user_locks: dict[str, threading.Lock] = {}
        self._global_lock = threading.Lock()
        self._stop_event = threading.Event()
        self._bg_thread: Optional[threading.Thread] = None
        self._last_sync_times: dict[str, float] = {}

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _user_lock(self, username: str) -> threading.Lock:
        with self._global_lock:
            if username not in self._user_locks:
                self._user_locks[username] = threading.Lock()
            return self._user_locks[username]

    def _s3_workflow_prefix(self, username: str) -> str:
        safe = _sanitize_username_for_s3(username)
        if self._prefix:
            return f"{self._prefix}/users/{safe}/workflows/"
        return f"users/{safe}/workflows/"

    def _s3_key_for_workflow(self, username: str, rel_name: str) -> str:
        prefix = self._s3_workflow_prefix(username).rstrip("/")
        return f"{prefix}/{rel_name.lstrip('/')}"

    # ------------------------------------------------------------------
    # Core sync logic
    # ------------------------------------------------------------------

    def sync_user(self, username: str) -> dict:
        """Sync workflows for a single user. Returns stats dict."""
        if not username or username == "guest":
            return {"skipped": True, "reason": "guest user"}

        lock = self._user_lock(username)
        if not lock.acquire(blocking=False):
            return {"skipped": True, "reason": "sync already in progress"}

        try:
            return self._do_sync_user(username)
        finally:
            lock.release()
            self._last_sync_times[username] = time.time()

    def _do_sync_user(self, username: str) -> dict:
        stats = {"uploaded": 0, "downloaded": 0, "skipped": 0, "errors": 0}
        local_dir = user_env.get_user_workflow_dir(username)
        s3_prefix = self._s3_workflow_prefix(username)

        # Build maps of local and remote files
        local_files = self._scan_local(local_dir)
        remote_files = self._scan_remote(username)

        all_keys = set(local_files.keys()) | set(remote_files.keys())

        for rel_name in all_keys:
            local_mtime = local_files.get(rel_name, 0.0)
            remote_mtime = remote_files.get(rel_name, 0.0)

            try:
                if rel_name in local_files and rel_name not in remote_files:
                    # Local only -- upload to S3
                    self._upload_workflow(username, local_dir, rel_name)
                    stats["uploaded"] += 1

                elif rel_name not in local_files and rel_name in remote_files:
                    # Remote only -- download to local
                    self._download_workflow(username, local_dir, rel_name)
                    stats["downloaded"] += 1

                else:
                    # Both exist -- resolve conflict
                    action = self._resolve_conflict(local_mtime, remote_mtime)
                    if action == "upload":
                        self._upload_workflow(username, local_dir, rel_name)
                        stats["uploaded"] += 1
                    elif action == "download":
                        self._download_workflow(username, local_dir, rel_name)
                        stats["downloaded"] += 1
                    else:
                        stats["skipped"] += 1
            except Exception as exc:
                _log(f"Error syncing {rel_name} for {username}: {exc}")
                stats["errors"] += 1

        return stats

    def _resolve_conflict(self, local_mtime: float, remote_mtime: float) -> str:
        if self._conflict_strategy == "local_wins":
            return "upload"
        if self._conflict_strategy == "s3_wins":
            return "download"
        # newer_wins (default)
        if abs(local_mtime - remote_mtime) < 2.0:
            return "skip"
        return "upload" if local_mtime > remote_mtime else "download"

    def _scan_local(self, local_dir: str) -> dict[str, float]:
        """Return {relative_name: mtime} for all .json files in local_dir."""
        result: dict[str, float] = {}
        if not os.path.isdir(local_dir):
            return result
        for root, _, files in os.walk(local_dir):
            for f in files:
                if not f.lower().endswith(".json"):
                    continue
                full = os.path.join(root, f)
                rel = os.path.relpath(full, local_dir).replace("\\", "/")
                try:
                    st = os.stat(full)
                    if st.st_size > self._max_size:
                        continue
                    result[rel] = st.st_mtime
                except OSError:
                    pass
        return result

    def _scan_remote(self, username: str) -> dict[str, float]:
        """Return {relative_name: mtime_timestamp} for workflows in S3."""
        result: dict[str, float] = {}
        s3_prefix = self._s3_workflow_prefix(username)
        # The S3 client prepends its own global prefix via _full_key,
        # so we pass the path relative to the global prefix.
        relative_prefix = s3_prefix
        if self._prefix and relative_prefix.startswith(self._prefix):
            relative_prefix = relative_prefix[len(self._prefix) :].lstrip("/")

        try:
            objects = self._s3.list_objects(prefix=relative_prefix, max_keys=1000)
        except Exception as exc:
            _log(f"Failed to list S3 objects for {username}: {exc}")
            return result

        for obj in objects:
            key = obj.get("key", "")
            full_prefix = self._s3._full_key(relative_prefix)
            if key.startswith(full_prefix):
                rel = key[len(full_prefix) :]
            else:
                rel = key.rsplit("/workflows/", 1)[-1] if "/workflows/" in key else ""
            if not rel or not rel.lower().endswith(".json"):
                continue
            mtime = _parse_iso_mtime(obj.get("last_modified", ""))
            size = obj.get("size", 0)
            if size > self._max_size:
                continue
            result[rel] = mtime

        return result

    def _upload_workflow(
        self, username: str, local_dir: str, rel_name: str
    ) -> None:
        local_path = os.path.join(local_dir, rel_name.replace("/", os.sep))
        s3_key = self._s3_key_for_workflow(username, rel_name)
        # Strip the global prefix since upload_file applies it
        if self._prefix and s3_key.startswith(self._prefix):
            s3_key = s3_key[len(self._prefix) :].lstrip("/")
        self._s3.upload_file(local_path, s3_key)

    def _download_workflow(
        self, username: str, local_dir: str, rel_name: str
    ) -> None:
        s3_key = self._s3_key_for_workflow(username, rel_name)
        if self._prefix and s3_key.startswith(self._prefix):
            s3_key = s3_key[len(self._prefix) :].lstrip("/")
        local_path = os.path.join(local_dir, rel_name.replace("/", os.sep))
        self._s3.download_file(s3_key, local_path)

    # ------------------------------------------------------------------
    # Hooks for workflow_routes (fire-and-forget)
    # ------------------------------------------------------------------

    def upload_user_workflow(
        self, username: str, rel_name: str, local_path: str
    ) -> None:
        """Upload a single workflow to S3 (called after local save)."""
        if not self._sync_on_save:
            return
        if not username or username == "guest":
            return
        try:
            if os.path.getsize(local_path) > self._max_size:
                return
            s3_key = self._s3_key_for_workflow(username, rel_name)
            if self._prefix and s3_key.startswith(self._prefix):
                s3_key = s3_key[len(self._prefix) :].lstrip("/")
            self._s3.upload_file(local_path, s3_key)
            _log(f"Uploaded workflow {rel_name} for {username}")
        except Exception as exc:
            _log(f"Failed to upload workflow {rel_name} for {username}: {exc}")

    def delete_user_workflow(self, username: str, rel_name: str) -> None:
        """Delete a single workflow from S3 (called after local delete)."""
        if not self._sync_on_delete:
            return
        if not username or username == "guest":
            return
        try:
            s3_key = self._s3_key_for_workflow(username, rel_name)
            if self._prefix and s3_key.startswith(self._prefix):
                s3_key = s3_key[len(self._prefix) :].lstrip("/")
            self._s3.delete_object(s3_key)
            _log(f"Deleted workflow {rel_name} for {username} from S3")
        except Exception as exc:
            _log(f"Failed to delete workflow {rel_name} for {username}: {exc}")

    def get_s3_only_workflows(self, username: str) -> list[dict]:
        """Return file-info dicts for workflows that exist in S3 but not locally.

        These are pulled down on demand so the user sees them in the list.
        """
        if not username or username == "guest":
            return []

        local_dir = user_env.get_user_workflow_dir(username)
        local_files = set(self._scan_local(local_dir).keys())
        remote_files = self._scan_remote(username)

        downloaded: list[dict] = []
        for rel_name, mtime in remote_files.items():
            if rel_name in local_files:
                continue
            # Pull down to local so ComfyUI can serve it
            try:
                self._download_workflow(username, local_dir, rel_name)
                full_path = os.path.join(local_dir, rel_name.replace("/", os.sep))
                from ..routes.workflow_routes import get_file_info

                info = get_file_info(local_dir, rel_name)
                downloaded.append(info)
            except Exception as exc:
                _log(f"Failed to pull S3-only workflow {rel_name}: {exc}")

        return downloaded

    # ------------------------------------------------------------------
    # Background sync
    # ------------------------------------------------------------------

    def sync_all_users(self) -> dict[str, dict]:
        """Sync workflows for every known user. Returns {username: stats}."""
        results: dict[str, dict] = {}
        usernames = self._get_all_usernames()
        for uname in usernames:
            results[uname] = self.sync_user(uname)
        return results

    def _get_all_usernames(self) -> list[str]:
        if self._users_db is None:
            return []
        try:
            all_users = self._users_db.list_users()
            return [
                u.get("username") or u.get("name") or ""
                for u in all_users
                if (u.get("username") or u.get("name") or "") not in ("", "guest")
            ]
        except Exception:
            return []

    def _bg_loop(self) -> None:
        while not self._stop_event.is_set():
            try:
                self.sync_all_users()
            except Exception as exc:
                _log(f"Background sync error: {exc}")
            self._stop_event.wait(self._interval)

    def start_background_sync(self) -> None:
        if self._bg_thread and self._bg_thread.is_alive():
            return
        self._stop_event.clear()
        self._bg_thread = threading.Thread(
            target=self._bg_loop, daemon=True, name="s3-workflow-sync"
        )
        self._bg_thread.start()
        _log(f"Background workflow sync started (interval={self._interval}s)")

    def stop_background_sync(self) -> None:
        self._stop_event.set()
        if self._bg_thread and self._bg_thread.is_alive():
            self._bg_thread.join(timeout=5)
        _log("Background workflow sync stopped.")

    # ------------------------------------------------------------------
    # Status
    # ------------------------------------------------------------------

    def status(self) -> dict:
        return {
            "running": self._bg_thread is not None and self._bg_thread.is_alive(),
            "interval_seconds": self._interval,
            "conflict_strategy": self._conflict_strategy,
            "sync_on_save": self._sync_on_save,
            "sync_on_delete": self._sync_on_delete,
            "last_sync_times": dict(self._last_sync_times),
        }


# ---------------------------------------------------------------------------
# Singleton
# ---------------------------------------------------------------------------

_workflow_sync: Optional[S3WorkflowSync] = None


def get_workflow_sync() -> Optional[S3WorkflowSync]:
    """Return the singleton S3WorkflowSync, or None if not initialized."""
    return _workflow_sync


def init_workflow_sync(
    s3_client: S3StorageClient,
    s3_prefix: str,
    sync_config: dict,
    users_db=None,
) -> S3WorkflowSync:
    """Create and store the singleton S3WorkflowSync."""
    global _workflow_sync
    _workflow_sync = S3WorkflowSync(s3_client, s3_prefix, sync_config, users_db)
    return _workflow_sync
# --- END OF FILE utils/s3_workflow_sync.py ---
