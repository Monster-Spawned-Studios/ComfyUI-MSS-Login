"""
Consolidated S3 runtime for MSS-Login.

This module owns the s3fs/FUSE mount lifecycle, filesystem-backed S3 CRUD,
per-user workflow sync, and encrypted settings helpers. Other S3 modules should
delegate here rather than maintain their own transport-specific logic.
"""

from __future__ import annotations

import logging
import os
import shutil
import subprocess
import threading
import time
from datetime import datetime, timezone
from typing import Optional

logger = logging.getLogger("S3Mounter")

_DEFAULT_MODEL_FOLDERS = [
    "checkpoints",
    "loras",
    "vae",
    "embeddings",
    "controlnet",
    "upscale_models",
    "clip",
    "clip_vision",
    "diffusion_models",
    "text_encoders",
    "hypernetworks",
    "vae_approx",
]
_ACCESS_KEY_SETTING = "s3_storage.access_key_encrypted"
_SECRET_KEY_SETTING = "s3_storage.secret_key_encrypted"


def _safe_bool(value, default: bool = False) -> bool:
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.strip().lower() in ("1", "true", "yes", "on")
    return bool(value)


def _safe_int(value, default: int) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _safe_relpath(value: str) -> str | None:
    clean = (value or "").replace("\\", "/").strip().lstrip("/")
    if not clean:
        return ""
    normalized = os.path.normpath(clean).replace("\\", "/")
    if normalized in (".", ""):
        return ""
    if normalized.startswith("../") or normalized == "..":
        return None
    return normalized.lstrip("/")


def _resolve_under(base_dir: str, rel_path: str) -> str | None:
    normalized = _safe_relpath(rel_path)
    if normalized is None:
        return None
    candidate = os.path.normpath(os.path.join(base_dir, normalized.replace("/", os.sep)))
    base = os.path.abspath(base_dir)
    resolved = os.path.abspath(candidate)
    if resolved == base or resolved.startswith(base + os.sep):
        return resolved
    return None


def _format_iso(ts: float) -> str:
    return datetime.fromtimestamp(ts, tz=timezone.utc).isoformat()


def _sanitize_username(username: str) -> str:
    safe = "".join(
        c if c.isalnum() or c in ("_", "-") else "_" for c in (username or "").strip()
    )
    return safe or "_invalid_"


def _get_app_store():
    from ..constants import USERS_DB_CONFIG
    from .app_settings_store import get_app_settings_store

    return get_app_settings_store(USERS_DB_CONFIG)


def _load_encrypted_setting(setting_key: str) -> str:
    from ..constants import SECRET_KEY
    from .encryption import decrypt_value

    try:
        encrypted = _get_app_store().get(setting_key) or ""
        if not encrypted:
            return ""
        return decrypt_value(SECRET_KEY, encrypted) or ""
    except Exception:
        return ""


def _save_encrypted_setting(setting_key: str, plaintext: str) -> bool:
    from ..constants import SECRET_KEY
    from .encryption import encrypt_value

    try:
        ciphertext = encrypt_value(SECRET_KEY, plaintext.strip()) if plaintext else ""
        if plaintext and not ciphertext:
            return False
        _get_app_store().set(setting_key, ciphertext or "")
        return True
    except Exception:
        return False


def _resolve_data_path(rel_or_abs: str) -> str:
    from ..constants import DATA_DIR

    raw = (rel_or_abs or "").strip()
    if not raw:
        return os.path.join(DATA_DIR, "s3_mount")
    if os.path.isabs(raw):
        return raw
    return os.path.abspath(os.path.join(DATA_DIR, raw))


def _load_runtime_config() -> dict:
    from ..constants import CONFIG_FILE_PATH
    from .json_utils import load_json_file

    cfg = load_json_file(CONFIG_FILE_PATH, {}) or {}
    s3 = cfg.get("s3_storage") or {}
    mount = s3.get("mount") or {}
    workflow = s3.get("workflow_sync") or {}

    access_env = (s3.get("access_key_id_env") or "S3_ACCESS_KEY_ID").strip()
    secret_env = (s3.get("secret_access_key_env") or "S3_SECRET_ACCESS_KEY").strip()
    model_folders = mount.get("model_folders") or list(_DEFAULT_MODEL_FOLDERS)

    return {
        "enabled": _safe_bool(s3.get("enabled"), False),
        "endpoint_url": (s3.get("endpoint_url") or "").strip(),
        "bucket_name": (s3.get("bucket_name") or "").strip(),
        "region": (s3.get("region") or "").strip(),
        "prefix": (s3.get("prefix") or "comfyui").strip().strip("/"),
        "access_key_id": (os.getenv(access_env) or _load_encrypted_setting(_ACCESS_KEY_SETTING)).strip(),
        "secret_access_key": (os.getenv(secret_env) or _load_encrypted_setting(_SECRET_KEY_SETTING)).strip(),
        "access_key_id_env": access_env,
        "secret_access_key_env": secret_env,
        "mount": {
            "enabled": _safe_bool(mount.get("enabled"), False),
            "local_mount_path": _resolve_data_path(mount.get("local_mount_path") or "s3_mount"),
            "mode": ((mount.get("mode") or "mount").strip().lower() or "mount"),
            "model_folders": [str(folder).strip() for folder in model_folders if str(folder).strip()],
            "mount_output": _safe_bool(mount.get("mount_output"), False),
            "mount_input": _safe_bool(mount.get("mount_input"), False),
            "read_only": _safe_bool(mount.get("read_only"), False),
            "use_path_style": _safe_bool(mount.get("use_path_style"), _safe_bool(s3.get("use_path_style"), False)),
            "allow_other": _safe_bool(mount.get("allow_other"), True),
            "auto_install": _safe_bool(mount.get("auto_install"), True),
        },
        "workflow_sync": {
            "enabled": _safe_bool(workflow.get("enabled"), False),
            "sync_interval_seconds": _safe_int(workflow.get("sync_interval_seconds"), 60),
            "conflict_strategy": (workflow.get("conflict_strategy") or "newer_wins").strip().lower(),
            "sync_on_save": _safe_bool(workflow.get("sync_on_save"), True),
            "sync_on_delete": _safe_bool(workflow.get("sync_on_delete"), True),
            "max_workflow_size_mb": _safe_int(workflow.get("max_workflow_size_mb"), 50),
        },
    }


def get_s3_settings_payload() -> dict:
    cfg = _load_runtime_config()
    mount_cfg = cfg["mount"]
    workflow_cfg = cfg["workflow_sync"]
    return {
        "enabled": cfg["enabled"],
        "endpoint_url": cfg["endpoint_url"],
        "bucket_name": cfg["bucket_name"],
        "region": cfg["region"],
        "prefix": cfg["prefix"],
        "access_key_id_env": cfg["access_key_id_env"],
        "secret_access_key_env": cfg["secret_access_key_env"],
        "has_access_key": bool(cfg["access_key_id"]),
        "has_secret_key": bool(cfg["secret_access_key"]),
        "mount_enabled": mount_cfg["enabled"],
        "mount_local_path": mount_cfg["local_mount_path"],
        "mount_mode": mount_cfg["mode"],
        "model_folders": list(mount_cfg["model_folders"]),
        "mount_output": mount_cfg["mount_output"],
        "mount_input": mount_cfg["mount_input"],
        "read_only": mount_cfg["read_only"],
        "use_path_style": mount_cfg["use_path_style"],
        "allow_other": mount_cfg["allow_other"],
        "auto_install": mount_cfg["auto_install"],
        "workflow_sync_enabled": workflow_cfg["enabled"],
        "workflow_sync_interval_seconds": workflow_cfg["sync_interval_seconds"],
        "workflow_conflict_strategy": workflow_cfg["conflict_strategy"],
        "workflow_sync_on_save": workflow_cfg["sync_on_save"],
        "workflow_sync_on_delete": workflow_cfg["sync_on_delete"],
        "workflow_max_size_mb": workflow_cfg["max_workflow_size_mb"],
    }


def save_s3_settings(payload: dict) -> dict:
    from ..constants import CONFIG_FILE_PATH, reload_s3_storage_config
    from .json_utils import load_json_file, save_json_file

    cfg = load_json_file(CONFIG_FILE_PATH, {}) or {}
    s3 = cfg.get("s3_storage") or {}
    mount = s3.get("mount") or {}
    workflow = s3.get("workflow_sync") or {}

    def _folder_list(value) -> list[str]:
        if isinstance(value, list):
            raw = value
        else:
            raw = str(value or "").split(",")
        cleaned: list[str] = []
        for item in raw:
            folder = str(item).strip().strip("/").strip("\\")
            if folder and folder not in cleaned:
                cleaned.append(folder)
        return cleaned or list(_DEFAULT_MODEL_FOLDERS)

    s3["enabled"] = _safe_bool(payload.get("enabled"), False)
    s3["endpoint_url"] = (payload.get("endpoint_url") or "").strip()
    s3["bucket_name"] = (payload.get("bucket_name") or "").strip()
    s3["region"] = (payload.get("region") or "").strip()
    s3["prefix"] = (payload.get("prefix") or "comfyui").strip().strip("/")
    s3["access_key_id_env"] = (payload.get("access_key_id_env") or "S3_ACCESS_KEY_ID").strip()
    s3["secret_access_key_env"] = (
        payload.get("secret_access_key_env") or "S3_SECRET_ACCESS_KEY"
    ).strip()

    mount["enabled"] = _safe_bool(payload.get("mount_enabled"), False)
    mount["local_mount_path"] = (payload.get("mount_local_path") or "s3_mount").strip()
    mount["mode"] = ((payload.get("mount_mode") or "mount").strip().lower() or "mount")
    mount["model_folders"] = _folder_list(payload.get("model_folders"))
    mount["mount_output"] = _safe_bool(payload.get("mount_output"), False)
    mount["mount_input"] = _safe_bool(payload.get("mount_input"), False)
    mount["read_only"] = _safe_bool(payload.get("read_only"), False)
    mount["use_path_style"] = _safe_bool(payload.get("use_path_style"), False)
    mount["allow_other"] = _safe_bool(payload.get("allow_other"), True)
    mount["auto_install"] = _safe_bool(payload.get("auto_install"), True)
    s3["mount"] = mount

    workflow["enabled"] = _safe_bool(payload.get("workflow_sync_enabled"), False)
    workflow["sync_interval_seconds"] = _safe_int(
        payload.get("workflow_sync_interval_seconds"), 60
    )
    workflow["conflict_strategy"] = (
        payload.get("workflow_conflict_strategy") or "newer_wins"
    ).strip().lower()
    workflow["sync_on_save"] = _safe_bool(payload.get("workflow_sync_on_save"), True)
    workflow["sync_on_delete"] = _safe_bool(payload.get("workflow_sync_on_delete"), True)
    workflow["max_workflow_size_mb"] = _safe_int(payload.get("workflow_max_size_mb"), 50)
    s3["workflow_sync"] = workflow

    cfg["s3_storage"] = s3
    save_json_file(CONFIG_FILE_PATH, cfg)
    reload_s3_storage_config()

    access_key = (payload.get("access_key_id") or "").strip()
    secret_key = (payload.get("secret_access_key") or "").strip()
    if payload.get("clear_access_key"):
        _save_encrypted_setting(_ACCESS_KEY_SETTING, "")
    elif access_key:
        if not _save_encrypted_setting(_ACCESS_KEY_SETTING, access_key):
            raise RuntimeError("Failed to encrypt and store the S3 access key.")

    if payload.get("clear_secret_key"):
        _save_encrypted_setting(_SECRET_KEY_SETTING, "")
    elif secret_key:
        if not _save_encrypted_setting(_SECRET_KEY_SETTING, secret_key):
            raise RuntimeError("Failed to encrypt and store the S3 secret key.")

    return get_s3_settings_payload()


class S3MountManager:
    """Single runtime for mounted S3 access and workflow mirroring."""

    def __init__(self, data_dir: str, users_db=None):
        self._data_dir = data_dir
        self._users_db = users_db
        self._lock = threading.RLock()
        self._stop_event = threading.Event()
        self._workflow_thread: Optional[threading.Thread] = None
        self._mount_proc: Optional[subprocess.Popen] = None
        self._last_error = ""
        self._active_mode = "idle"
        self._registered_folders: list[str] = []
        self._last_sync_times: dict[str, float] = {}
        self._cfg: dict = {}
        self._mount_root = ""
        self._models_root = ""
        self._workflow_root = ""
        self._passwd_path = os.path.join(self._data_dir, "data", ".passwd-s3fs")
        self.refresh_config()

    def refresh_config(self) -> dict:
        with self._lock:
            self._cfg = _load_runtime_config()
            self._mount_root = self._cfg["mount"]["local_mount_path"]
            self._models_root = os.path.join(self._mount_root, "models")
            self._workflow_root = os.path.join(self._mount_root, "users")
            return self._cfg

    @property
    def local_root(self) -> str:
        return self._mount_root

    @property
    def models_root(self) -> str:
        return self._models_root

    def get_models_folder_path(self, folder_type: str) -> str:
        return os.path.join(self._models_root, folder_type)

    def _is_enabled(self) -> bool:
        return self._cfg.get("enabled") and self._cfg["mount"].get("enabled")

    def _is_configured(self) -> bool:
        return all(
            [
                self._cfg.get("bucket_name"),
                self._cfg.get("endpoint_url"),
                self._cfg.get("access_key_id"),
                self._cfg.get("secret_access_key"),
            ]
        )

    def _fuse_available(self) -> bool:
        return os.name == "posix" and os.path.exists("/dev/fuse")

    def _s3fs_installed(self) -> bool:
        return shutil.which("s3fs") is not None

    def _mountpoint_active(self) -> bool:
        if not self._mount_root or not os.path.isdir(self._mount_root):
            return False
        if not shutil.which("mountpoint"):
            return False
        result = subprocess.run(
            ["mountpoint", "-q", self._mount_root],
            capture_output=True,
            check=False,
        )
        return result.returncode == 0

    def is_mounted(self) -> bool:
        return self._mountpoint_active()

    def _ensure_s3fs(self) -> bool:
        if self._s3fs_installed():
            return True
        if not self._cfg["mount"].get("auto_install", True):
            self._last_error = "s3fs is not installed."
            return False
        if os.name != "posix" or os.getuid() != 0 or shutil.which("apt") is None:
            self._last_error = "s3fs is not installed and automatic installation is unavailable."
            return False
        try:
            subprocess.check_call(["apt", "update", "-qq"])
            subprocess.check_call(["apt", "install", "-yqq", "s3fs", "fuse"])
            return True
        except subprocess.CalledProcessError as exc:
            self._last_error = f"Automatic s3fs install failed: {exc}"
            return False

    def _write_passwd_file(self) -> None:
        os.makedirs(os.path.dirname(self._passwd_path), exist_ok=True)
        with open(self._passwd_path, "w", encoding="utf-8") as handle:
            handle.write(
                f"{self._cfg['access_key_id']}:{self._cfg['secret_access_key']}"
            )
        os.chmod(self._passwd_path, 0o600)

    def _mount_spec(self) -> str:
        bucket = self._cfg["bucket_name"]
        prefix = (self._cfg.get("prefix") or "").strip("/")
        return f"{bucket}:/{prefix}" if prefix else bucket

    def _build_mount_cmd(self) -> list[str]:
        mount_cfg = self._cfg["mount"]
        cmd = [
            "s3fs",
            self._mount_spec(),
            self._mount_root,
            "-f",
            "-o",
            f"passwd_file={self._passwd_path}",
            "-o",
            f"url={self._cfg['endpoint_url']}",
            "-o",
            "use_cache=/tmp",
            "-o",
            f"uid={os.getuid()}",
            "-o",
            f"gid={os.getgid()}",
            "-o",
            "dbglevel=info",
        ]
        if mount_cfg.get("allow_other"):
            cmd.extend(["-o", "allow_other"])
        if mount_cfg.get("use_path_style"):
            cmd.extend(["-o", "use_path_request_style"])
        if mount_cfg.get("read_only"):
            cmd.extend(["-o", "ro"])
        return cmd

    def mount_or_sync(self) -> bool:
        with self._lock:
            self.refresh_config()
            self._last_error = ""
            if not self._is_enabled():
                self._active_mode = "disabled"
                return False
            if not self._is_configured():
                self._active_mode = "degraded"
                self._last_error = "S3 is enabled but required settings are missing."
                return False
            if not self._ensure_s3fs():
                self._active_mode = "degraded"
                return False
            if not self._fuse_available():
                self._active_mode = "degraded"
                self._last_error = "FUSE is not available in this container."
                return False
            os.makedirs(self._mount_root, exist_ok=True)
            if self._mountpoint_active():
                self._active_mode = "mount"
                self.start_background_sync()
                return True

            self._write_passwd_file()
            cmd = self._build_mount_cmd()
            try:
                self._mount_proc = subprocess.Popen(
                    cmd,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                )
                time.sleep(3)
                if not self._mountpoint_active():
                    stderr = ""
                    if self._mount_proc and self._mount_proc.poll() is not None:
                        stderr = (
                            (self._mount_proc.stderr.read() or b"").decode(
                                errors="replace"
                            )
                        )
                    self._last_error = stderr or "s3fs did not establish a mount."
                    self._active_mode = "degraded"
                    return False
                self._active_mode = "mount"
                self.start_background_sync()
                return True
            except Exception as exc:
                self._last_error = str(exc)
                self._active_mode = "degraded"
                return False

    def register_folder_paths(self) -> list[str]:
        if not self.is_mounted():
            return []
        try:
            import folder_paths  # pyright: ignore[reportMissingImports]
        except ImportError:
            return []

        mount_cfg = self._cfg["mount"]
        registered: list[str] = []
        for folder_type in mount_cfg.get("model_folders") or []:
            folder_dir = self.get_models_folder_path(folder_type)
            os.makedirs(folder_dir, exist_ok=True)
            try:
                folder_paths.add_model_folder_path(folder_type, folder_dir)
                registered.append(folder_type)
            except Exception:
                continue

        if mount_cfg.get("mount_output"):
            out_dir = os.path.join(self._mount_root, "output")
            os.makedirs(out_dir, exist_ok=True)
            try:
                folder_paths.add_model_folder_path("output", out_dir)
                registered.append("output")
            except Exception:
                pass

        if mount_cfg.get("mount_input"):
            in_dir = os.path.join(self._mount_root, "input")
            os.makedirs(in_dir, exist_ok=True)
            try:
                folder_paths.add_model_folder_path("input", in_dir)
                registered.append("input")
            except Exception:
                pass

        self._registered_folders = registered
        return registered

    def unmount(self) -> str:
        with self._lock:
            self.stop_background_sync()
            if not os.path.isdir(self._mount_root):
                self._active_mode = "idle"
                return f"Mount path does not exist: {self._mount_root}"
            if not self._mountpoint_active():
                self._active_mode = "idle"
                return f"No filesystem mounted at this path: {self._mount_root}"
            try:
                if shutil.which("fusermount"):
                    subprocess.check_call(["fusermount", "-u", self._mount_root])
                else:
                    subprocess.check_call(["umount", self._mount_root])
                if self._mount_proc and self._mount_proc.poll() is None:
                    self._mount_proc.terminate()
                    self._mount_proc.wait(timeout=5)
                self._active_mode = "idle"
                self._mount_proc = None
                return "Unmount successful."
            except Exception as exc:
                self._last_error = str(exc)
                return f"Unmount failed: {exc}"

    def remount(self) -> dict:
        self.unmount()
        mounted = self.mount_or_sync()
        registered = self.register_folder_paths() if mounted else []
        return {
            "remounted": mounted,
            "registered_folders": registered,
            "status": self.status(),
        }

    def _path_for_key(self, s3_key: str) -> str:
        path = _resolve_under(self._mount_root, s3_key)
        if path is None:
            raise ValueError("Invalid S3 key path.")
        return path

    def list_objects(self, prefix: str = "", max_keys: int = 1000) -> list[dict]:
        if not self.is_mounted():
            raise RuntimeError("S3 mount is not active.")
        base = self._mount_root if not prefix else self._path_for_key(prefix)
        if not os.path.exists(base):
            return []
        results: list[dict] = []
        if os.path.isfile(base):
            st = os.stat(base)
            rel = os.path.relpath(base, self._mount_root).replace("\\", "/")
            return [{"key": rel, "size": st.st_size, "last_modified": _format_iso(st.st_mtime)}]
        for root, _, files in os.walk(base):
            for filename in files:
                full = os.path.join(root, filename)
                st = os.stat(full)
                rel = os.path.relpath(full, self._mount_root).replace("\\", "/")
                results.append(
                    {
                        "key": rel,
                        "size": st.st_size,
                        "last_modified": _format_iso(st.st_mtime),
                    }
                )
                if len(results) >= max_keys:
                    return results
        return results

    def upload_file(self, local_path: str, s3_key: str) -> dict:
        if not self.is_mounted():
            raise RuntimeError("S3 mount is not active.")
        if not os.path.isfile(local_path):
            raise FileNotFoundError(f"Local file not found: {local_path}")
        dest = self._path_for_key(s3_key)
        os.makedirs(os.path.dirname(dest), exist_ok=True)
        shutil.copy2(local_path, dest)
        size = os.path.getsize(dest)
        return {"bucket": self._cfg["bucket_name"], "key": _safe_relpath(s3_key) or "", "size": size}

    def download_file(self, s3_key: str, local_path: str) -> str:
        if not self.is_mounted():
            raise RuntimeError("S3 mount is not active.")
        source = self._path_for_key(s3_key)
        if not os.path.isfile(source):
            raise FileNotFoundError(f"S3 object not found: {s3_key}")
        os.makedirs(os.path.dirname(local_path) or ".", exist_ok=True)
        shutil.copy2(source, local_path)
        return local_path

    def delete_object(self, s3_key: str) -> bool:
        if not self.is_mounted():
            raise RuntimeError("S3 mount is not active.")
        target = self._path_for_key(s3_key)
        if not os.path.exists(target):
            return False
        if os.path.isdir(target):
            shutil.rmtree(target)
        else:
            os.remove(target)
        return True

    def test_connection(self) -> dict:
        return {
            "ok": self.is_mounted(),
            "configured": self._is_configured(),
            "bucket": self._cfg.get("bucket_name") or "",
            "endpoint": self._cfg.get("endpoint_url") or "",
            "mode": self._active_mode,
            "error": self._last_error,
        }

    def _workflow_dir_for_user(self, username: str) -> str:
        safe = _sanitize_username(username)
        return os.path.join(self._workflow_root, safe, "workflows")

    def _scan_local_json_files(self, base_dir: str, max_size_bytes: int) -> dict[str, float]:
        result: dict[str, float] = {}
        if not os.path.isdir(base_dir):
            return result
        for root, _, files in os.walk(base_dir):
            for filename in files:
                if not filename.lower().endswith(".json"):
                    continue
                full = os.path.join(root, filename)
                try:
                    st = os.stat(full)
                except OSError:
                    continue
                if st.st_size > max_size_bytes:
                    continue
                rel = os.path.relpath(full, base_dir).replace("\\", "/")
                result[rel] = st.st_mtime
        return result

    def _copy_local_to_remote(self, local_path: str, remote_dir: str, rel_name: str) -> None:
        remote_path = _resolve_under(remote_dir, rel_name)
        if remote_path is None:
            return
        os.makedirs(os.path.dirname(remote_path), exist_ok=True)
        shutil.copy2(local_path, remote_path)

    def _copy_remote_to_local(self, remote_dir: str, rel_name: str, local_dir: str) -> None:
        remote_path = _resolve_under(remote_dir, rel_name)
        local_path = _resolve_under(local_dir, rel_name)
        if remote_path is None or local_path is None or not os.path.isfile(remote_path):
            return
        os.makedirs(os.path.dirname(local_path), exist_ok=True)
        shutil.copy2(remote_path, local_path)

    def _resolve_conflict(self, local_mtime: float, remote_mtime: float) -> str:
        strategy = self._cfg["workflow_sync"].get("conflict_strategy") or "newer_wins"
        if strategy == "local_wins":
            return "upload"
        if strategy == "s3_wins":
            return "download"
        if abs(local_mtime - remote_mtime) < 2.0:
            return "skip"
        return "upload" if local_mtime > remote_mtime else "download"

    def sync_user(self, username: str) -> dict:
        from . import user_env

        if not self.is_mounted():
            return {"skipped": True, "reason": "s3 mount unavailable"}
        if not username or username == "guest":
            return {"skipped": True, "reason": "guest user"}

        max_size = int(self._cfg["workflow_sync"].get("max_workflow_size_mb", 50)) * 1024 * 1024
        local_dir = user_env.get_user_workflow_dir(username)
        remote_dir = self._workflow_dir_for_user(username)
        os.makedirs(local_dir, exist_ok=True)
        os.makedirs(remote_dir, exist_ok=True)

        local_files = self._scan_local_json_files(local_dir, max_size)
        remote_files = self._scan_local_json_files(remote_dir, max_size)
        stats = {"uploaded": 0, "downloaded": 0, "skipped": 0, "errors": 0}

        for rel_name in sorted(set(local_files) | set(remote_files)):
            try:
                if rel_name in local_files and rel_name not in remote_files:
                    self._copy_local_to_remote(
                        _resolve_under(local_dir, rel_name) or "",
                        remote_dir,
                        rel_name,
                    )
                    stats["uploaded"] += 1
                elif rel_name not in local_files and rel_name in remote_files:
                    self._copy_remote_to_local(remote_dir, rel_name, local_dir)
                    stats["downloaded"] += 1
                else:
                    action = self._resolve_conflict(local_files[rel_name], remote_files[rel_name])
                    if action == "upload":
                        self._copy_local_to_remote(
                            _resolve_under(local_dir, rel_name) or "",
                            remote_dir,
                            rel_name,
                        )
                        stats["uploaded"] += 1
                    elif action == "download":
                        self._copy_remote_to_local(remote_dir, rel_name, local_dir)
                        stats["downloaded"] += 1
                    else:
                        stats["skipped"] += 1
            except Exception:
                stats["errors"] += 1

        self._last_sync_times[username] = time.time()
        return stats

    def sync_all_users(self) -> dict[str, dict]:
        usernames: list[str] = []
        if self._users_db is not None:
            try:
                usernames = [
                    (u.get("username") or u.get("name") or "")
                    for u in self._users_db.list_users()
                    if (u.get("username") or u.get("name") or "") not in ("", "guest")
                ]
            except Exception:
                usernames = []
        return {username: self.sync_user(username) for username in usernames}

    def upload_user_workflow(self, username: str, rel_name: str, local_path: str) -> None:
        if not self._cfg["workflow_sync"].get("sync_on_save", True) or not self.is_mounted():
            return
        remote_dir = self._workflow_dir_for_user(username)
        os.makedirs(remote_dir, exist_ok=True)
        self._copy_local_to_remote(local_path, remote_dir, rel_name)
        self._last_sync_times[username] = time.time()

    def delete_user_workflow(self, username: str, rel_name: str) -> None:
        if not self._cfg["workflow_sync"].get("sync_on_delete", True) or not self.is_mounted():
            return
        remote_dir = self._workflow_dir_for_user(username)
        target = _resolve_under(remote_dir, rel_name)
        if target and os.path.exists(target):
            os.remove(target)
            self._last_sync_times[username] = time.time()

    def get_s3_only_workflows(self, username: str) -> list[dict]:
        from . import user_env
        from ..routes.workflow_routes import get_file_info

        if not self.is_mounted() or not username or username == "guest":
            return []

        max_size = int(self._cfg["workflow_sync"].get("max_workflow_size_mb", 50)) * 1024 * 1024
        local_dir = user_env.get_user_workflow_dir(username)
        remote_dir = self._workflow_dir_for_user(username)
        local_files = set(self._scan_local_json_files(local_dir, max_size).keys())
        remote_files = self._scan_local_json_files(remote_dir, max_size)

        downloaded: list[dict] = []
        for rel_name in sorted(remote_files.keys()):
            if rel_name in local_files:
                continue
            self._copy_remote_to_local(remote_dir, rel_name, local_dir)
            try:
                downloaded.append(get_file_info(local_dir, rel_name))
            except Exception:
                continue
        return downloaded

    def _workflow_loop(self) -> None:
        while not self._stop_event.is_set():
            try:
                self.sync_all_users()
            except Exception:
                pass
            self._stop_event.wait(self._cfg["workflow_sync"].get("sync_interval_seconds", 60))

    def start_background_sync(self) -> None:
        if not self._cfg["workflow_sync"].get("enabled"):
            return
        if not self.is_mounted():
            return
        if self._workflow_thread and self._workflow_thread.is_alive():
            return
        self._stop_event.clear()
        self._workflow_thread = threading.Thread(
            target=self._workflow_loop,
            daemon=True,
            name="s3-workflow-sync",
        )
        self._workflow_thread.start()

    def stop_background_sync(self) -> None:
        self._stop_event.set()
        if self._workflow_thread and self._workflow_thread.is_alive():
            self._workflow_thread.join(timeout=5)
        self._workflow_thread = None

    def trigger_sync(self) -> bool:
        if not self.is_mounted():
            return False
        self.sync_all_users()
        return True

    def workflow_status(self) -> dict:
        return {
            "running": self._workflow_thread is not None and self._workflow_thread.is_alive(),
            "interval_seconds": self._cfg["workflow_sync"].get("sync_interval_seconds", 60),
            "conflict_strategy": self._cfg["workflow_sync"].get("conflict_strategy", "newer_wins"),
            "sync_on_save": self._cfg["workflow_sync"].get("sync_on_save", True),
            "sync_on_delete": self._cfg["workflow_sync"].get("sync_on_delete", True),
            "last_sync_times": dict(self._last_sync_times),
        }

    def status(self) -> dict:
        return {
            "enabled": self._is_enabled(),
            "configured": self._is_configured(),
            "mounted": self.is_mounted(),
            "mode": self._active_mode,
            "mount_root": self._mount_root,
            "models_root": self._models_root,
            "workflow_root": self._workflow_root,
            "bucket": self._cfg.get("bucket_name") or "",
            "endpoint": self._cfg.get("endpoint_url") or "",
            "prefix": self._cfg.get("prefix") or "",
            "fuse_available": self._fuse_available(),
            "s3fs_installed": self._s3fs_installed(),
            "read_only": self._cfg["mount"].get("read_only", False),
            "model_folders": list(self._cfg["mount"].get("model_folders") or []),
            "registered_folders": list(self._registered_folders),
            "last_error": self._last_error,
        }

    def stop(self) -> None:
        self.unmount()


_s3_manager: Optional[S3MountManager] = None


def init_s3_manager(data_dir: str, users_db=None) -> S3MountManager:
    global _s3_manager
    _s3_manager = S3MountManager(data_dir, users_db=users_db)
    return _s3_manager


def get_s3_manager() -> Optional[S3MountManager]:
    return _s3_manager


def init_mount_manager(s3_config: dict | None = None, mount_config: dict | None = None, data_dir: str | None = None) -> S3MountManager:
    del s3_config, mount_config
    if data_dir is None:
        from ..constants import DATA_DIR

        data_dir = DATA_DIR
    return init_s3_manager(data_dir)


def get_mount_manager() -> Optional[S3MountManager]:
    return get_s3_manager()


def get_workflow_sync() -> Optional[S3MountManager]:
    return get_s3_manager()


def init_workflow_sync(*args, **kwargs) -> S3MountManager:
    del args, kwargs
    mgr = get_s3_manager()
    if mgr is None:
        from ..constants import DATA_DIR

        mgr = init_s3_manager(DATA_DIR)
    return mgr
