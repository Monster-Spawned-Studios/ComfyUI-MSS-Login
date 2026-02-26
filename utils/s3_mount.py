# --- START OF FILE utils/s3_mount.py ---
"""
S3-compatible storage mount manager (experimental).

Uses rclone to either FUSE-mount an S3-compatible bucket as a local directory
or fall back to ``rclone sync`` when FUSE is unavailable (e.g. unprivileged
Docker containers). Mounted/synced paths are registered with ComfyUI's
``folder_paths`` so that S3-hosted models appear natively.

Supports Amazon S3, Backblaze B2, MinIO, and any other S3-compatible endpoint.

Docker Compose Integration
--------------------------

**Option A -- Sidecar rclone container (privileged, true FUSE mount)**::

    services:
      rclone:
        image: rclone/rclone:latest
        cap_add: [SYS_ADMIN]
        devices: ["/dev/fuse"]
        security_opt: ["apparmor:unconfined"]
        command: >
          mount remote:bucket/prefix /mnt/s3
          --vfs-cache-mode full --allow-other --daemon
        volumes:
          - s3_mount:/mnt/s3:shared
        environment:
          - RCLONE_CONFIG_REMOTE_TYPE=s3
          - RCLONE_CONFIG_REMOTE_PROVIDER=AWS
          - RCLONE_CONFIG_REMOTE_ACCESS_KEY_ID=$${S3_ACCESS_KEY_ID}
          - RCLONE_CONFIG_REMOTE_SECRET_ACCESS_KEY=$${S3_SECRET_ACCESS_KEY}
          - RCLONE_CONFIG_REMOTE_REGION=$${S3_REGION}
          - RCLONE_CONFIG_REMOTE_ENDPOINT=$${S3_ENDPOINT_URL}
      comfyui:
        volumes:
          - s3_mount:/comfyui/s3_models:ro

**Option B -- Built-in sync mode (no privileges needed)**:

    Set ``s3_storage.mount.mode`` to ``"sync"`` in ``config.json``.  The
    ``S3MountManager`` runs ``rclone sync`` inside the ComfyUI container
    (just needs the rclone binary on ``$PATH``).  No ``--privileged``,
    ``/dev/fuse``, or ``SYS_ADMIN`` capability required.
"""

import os
import signal
import shutil
import platform
import subprocess
import threading
import time
from typing import Optional

from .s3_storage import get_s3_provider_type

_LOG_PREFIX = "[MSS-Login::S3Mount]"


def _log(msg: str) -> None:
    print(f"{_LOG_PREFIX} {msg}")


class S3MountManager:
    """Manage an rclone FUSE mount or periodic sync of an S3 bucket."""

    def __init__(self, s3_config: dict, mount_config: dict, data_dir: str):
        self._s3_cfg = s3_config
        self._mount_cfg = mount_config
        self._data_dir = data_dir

        self._rclone_path: str = mount_config.get("rclone_path") or "rclone"
        self._mode: str = (mount_config.get("mode") or "auto").lower()
        self._sync_interval: int = int(mount_config.get("sync_interval_seconds") or 300)
        self._vfs_cache_mode: str = mount_config.get("vfs_cache_mode") or "full"
        self._vfs_cache_max_size: str = mount_config.get("vfs_cache_max_size") or "10G"
        self._model_folders: list[str] = mount_config.get("model_folders") or []
        self._mount_output: bool = bool(mount_config.get("mount_output"))
        self._mount_input: bool = bool(mount_config.get("mount_input"))
        self._read_only: bool = mount_config.get("read_only", True)

        raw_path = (mount_config.get("local_mount_path") or "").strip()
        if raw_path and os.path.isabs(raw_path):
            self._local_root = raw_path
        else:
            self._local_root = os.path.join(data_dir, raw_path or "s3_mount")

        self._rclone_proc: Optional[subprocess.Popen] = None
        self._sync_thread: Optional[threading.Thread] = None
        self._stop_event = threading.Event()
        self._active_mode: Optional[str] = None
        self._last_sync_time: float = 0.0
        self._mounted = False

    # ------------------------------------------------------------------
    # Platform detection
    # ------------------------------------------------------------------

    @staticmethod
    def _detect_platform() -> str:
        if os.path.isfile("/.dockerenv") or os.environ.get("COMFYUI_DOCKER"):
            return "docker"
        return platform.system().lower()

    def _fuse_available(self) -> bool:
        plat = self._detect_platform()
        if plat == "docker":
            return os.path.exists("/dev/fuse")
        if plat == "linux":
            return os.path.exists("/dev/fuse") or shutil.which("fusermount3") is not None
        if plat == "darwin":
            return shutil.which("mount_macfuse") is not None or os.path.isdir(
                "/Library/Filesystems/macfuse.fs"
            )
        if plat == "windows":
            winfsp_dir = os.path.join(
                os.environ.get("ProgramFiles(x86)", r"C:\Program Files (x86)"),
                "WinFsp",
            )
            return os.path.isdir(winfsp_dir)
        return False

    def _rclone_installed(self) -> bool:
        try:
            subprocess.run(
                [self._rclone_path, "version"],
                capture_output=True,
                timeout=10,
            )
            return True
        except (FileNotFoundError, subprocess.TimeoutExpired, OSError):
            return False

    # ------------------------------------------------------------------
    # rclone remote string (no config file -- credentials via CLI flags)
    # ------------------------------------------------------------------

    def _remote_string(self) -> str:
        """Build the ``:s3:bucket/prefix`` remote string for rclone."""
        bucket = self._s3_cfg.get("bucket_name") or ""
        prefix = (self._s3_cfg.get("prefix") or "").strip("/")
        models_prefix = f"{prefix}/models" if prefix else "models"
        return f":s3:{bucket}/{models_prefix}"

    def _rclone_env(self) -> dict[str, str]:
        """Environment variables that configure the rclone S3 backend."""
        env = dict(os.environ)
        endpoint = self._s3_cfg.get("endpoint_url") or ""
        provider = get_s3_provider_type(endpoint)
        rclone_provider = {"aws": "AWS", "backblaze": "B2", "generic": "Other"}.get(
            provider, "Other"
        )

        env["RCLONE_S3_PROVIDER"] = rclone_provider
        env["RCLONE_S3_ACCESS_KEY_ID"] = self._s3_cfg.get("access_key_id") or ""
        env["RCLONE_S3_SECRET_ACCESS_KEY"] = self._s3_cfg.get("secret_access_key") or ""
        if endpoint:
            env["RCLONE_S3_ENDPOINT"] = endpoint
        region = self._s3_cfg.get("region") or ""
        if region:
            env["RCLONE_S3_REGION"] = region
        return env

    # ------------------------------------------------------------------
    # Mount (FUSE)
    # ------------------------------------------------------------------

    def _build_mount_cmd(self) -> list[str]:
        cmd = [
            self._rclone_path,
            "mount",
            self._remote_string(),
            self._local_root,
            "--vfs-cache-mode", self._vfs_cache_mode,
            "--vfs-cache-max-size", self._vfs_cache_max_size,
            "--dir-cache-time", "5m",
            "--poll-interval", "1m",
            "--no-checksum",
        ]
        if self._read_only:
            cmd.append("--read-only")

        plat = self._detect_platform()
        if plat in ("linux", "docker"):
            cmd.append("--allow-other")
        elif plat == "darwin":
            cmd.extend(["--volname", "ComfyUI-S3"])
        elif plat == "windows":
            cmd.append("--network-mode")

        return cmd

    def _try_fuse_mount(self) -> bool:
        if not self._fuse_available():
            _log("FUSE not available on this platform.")
            return False

        os.makedirs(self._local_root, exist_ok=True)
        cmd = self._build_mount_cmd()
        _log(f"Attempting FUSE mount: {' '.join(cmd)}")

        try:
            self._rclone_proc = subprocess.Popen(
                cmd,
                env=self._rclone_env(),
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
            )
            # Give rclone a moment to mount
            time.sleep(3)
            if self._rclone_proc.poll() is not None:
                stderr = (self._rclone_proc.stderr.read() or b"").decode(errors="replace")
                _log(f"FUSE mount process exited early: {stderr}")
                self._rclone_proc = None
                return False

            self._active_mode = "fuse"
            self._mounted = True
            _log(f"FUSE mount active at {self._local_root}")
            return True
        except Exception as exc:
            _log(f"FUSE mount failed: {exc}")
            self._rclone_proc = None
            return False

    # ------------------------------------------------------------------
    # Sync fallback
    # ------------------------------------------------------------------

    def _run_sync_once(self) -> bool:
        os.makedirs(self._local_root, exist_ok=True)
        cmd = [
            self._rclone_path,
            "sync",
            self._remote_string(),
            self._local_root,
            "--no-update-modtime",
            "--transfers", "4",
            "--checkers", "8",
        ]
        _log(f"Running sync: {' '.join(cmd)}")
        try:
            result = subprocess.run(
                cmd,
                env=self._rclone_env(),
                capture_output=True,
                timeout=600,
            )
            self._last_sync_time = time.time()
            if result.returncode != 0:
                stderr = result.stderr.decode(errors="replace")
                _log(f"Sync returned non-zero ({result.returncode}): {stderr}")
                return False
            _log("Sync completed successfully.")
            return True
        except subprocess.TimeoutExpired:
            _log("Sync timed out after 600 seconds.")
            return False
        except Exception as exc:
            _log(f"Sync failed: {exc}")
            return False

    def _sync_loop(self) -> None:
        """Background thread that periodically runs rclone sync."""
        while not self._stop_event.is_set():
            self._run_sync_once()
            self._stop_event.wait(self._sync_interval)

    def _start_sync_mode(self) -> bool:
        ok = self._run_sync_once()
        if not ok:
            _log("Initial sync failed; will retry in background.")

        self._active_mode = "sync"
        self._mounted = True
        self._sync_thread = threading.Thread(
            target=self._sync_loop, daemon=True, name="s3-mount-sync"
        )
        self._sync_thread.start()
        _log(f"Sync mode active. Interval: {self._sync_interval}s")
        return True

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def mount_or_sync(self) -> bool:
        """Start the mount or sync based on configured mode.

        Returns True if the local directory is available.
        """
        if not self._rclone_installed():
            _log(
                "rclone binary not found. Install rclone "
                "(https://rclone.org/install/) or set s3_storage.mount.rclone_path."
            )
            return False

        if self._mode == "mount":
            return self._try_fuse_mount()
        if self._mode == "sync":
            return self._start_sync_mode()

        # auto: try FUSE first, fall back to sync
        if self._try_fuse_mount():
            return True
        _log("Falling back to sync mode.")
        return self._start_sync_mode()

    def register_folder_paths(self) -> list[str]:
        """Register mounted model directories with ComfyUI's folder_paths.

        Returns a list of folder types that were registered.
        """
        try:
            import folder_paths  # pyright: ignore[reportMissingImports]
        except ImportError:
            _log("folder_paths module not available; skipping registration.")
            return []

        registered: list[str] = []
        for folder_type in self._model_folders:
            folder_dir = os.path.join(self._local_root, folder_type)
            if not os.path.isdir(folder_dir):
                os.makedirs(folder_dir, exist_ok=True)

            try:
                folder_paths.add_model_folder_path(folder_type, folder_dir)
                registered.append(folder_type)
                _log(f"Registered extra folder path: {folder_type} -> {folder_dir}")
            except Exception as exc:
                _log(f"Failed to register {folder_type}: {exc}")

        if self._mount_output:
            out_dir = os.path.join(self._local_root, "output")
            os.makedirs(out_dir, exist_ok=True)
            try:
                folder_paths.add_model_folder_path("output", out_dir)
                registered.append("output")
            except Exception:
                pass

        if self._mount_input:
            in_dir = os.path.join(self._local_root, "input")
            os.makedirs(in_dir, exist_ok=True)
            try:
                folder_paths.add_model_folder_path("input", in_dir)
                registered.append("input")
            except Exception:
                pass

        return registered

    def trigger_sync(self) -> bool:
        """Manually trigger a sync (only meaningful in sync mode)."""
        if self._active_mode == "sync":
            return self._run_sync_once()
        _log("Manual sync not applicable in FUSE mode.")
        return False

    def unmount(self) -> None:
        """Stop the mount/sync and clean up."""
        self._stop_event.set()

        if self._rclone_proc and self._rclone_proc.poll() is None:
            _log("Stopping FUSE mount process...")
            try:
                plat = self._detect_platform()
                if plat == "windows":
                    self._rclone_proc.terminate()
                else:
                    self._rclone_proc.send_signal(signal.SIGTERM)
                self._rclone_proc.wait(timeout=10)
            except Exception:
                self._rclone_proc.kill()
            self._rclone_proc = None

            # fusermount cleanup on Linux
            if self._detect_platform() in ("linux", "docker"):
                try:
                    subprocess.run(
                        ["fusermount", "-u", self._local_root],
                        capture_output=True,
                        timeout=10,
                    )
                except Exception:
                    pass

        if self._sync_thread and self._sync_thread.is_alive():
            self._sync_thread.join(timeout=5)

        self._mounted = False
        self._active_mode = None
        _log("Unmounted / stopped.")

    def is_mounted(self) -> bool:
        """Return True if the mount or sync is currently active."""
        if self._active_mode == "fuse":
            return self._rclone_proc is not None and self._rclone_proc.poll() is None
        if self._active_mode == "sync":
            return self._sync_thread is not None and self._sync_thread.is_alive()
        return False

    def status(self) -> dict:
        """Return a status dict for the API."""
        return {
            "mounted": self.is_mounted(),
            "mode": self._active_mode or "none",
            "local_root": self._local_root,
            "last_sync_time": self._last_sync_time,
            "platform": self._detect_platform(),
            "fuse_available": self._fuse_available(),
            "rclone_installed": self._rclone_installed(),
            "read_only": self._read_only,
            "model_folders": self._model_folders,
        }

    @property
    def local_root(self) -> str:
        return self._local_root


# ---------------------------------------------------------------------------
# Singleton
# ---------------------------------------------------------------------------

_mount_manager: Optional[S3MountManager] = None


def get_mount_manager() -> Optional[S3MountManager]:
    """Return the singleton mount manager, or None if not initialized."""
    return _mount_manager


def init_mount_manager(
    s3_config: dict, mount_config: dict, data_dir: str
) -> S3MountManager:
    """Create and store the singleton S3MountManager."""
    global _mount_manager
    _mount_manager = S3MountManager(s3_config, mount_config, data_dir)
    return _mount_manager
# --- END OF FILE utils/s3_mount.py ---
