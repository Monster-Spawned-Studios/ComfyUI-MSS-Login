"""
MSS-Login logger: file (no IP/API key/password) and console (may include IP for operators).
Supports log rotation by size or interval; rotated logs are compressed to .tar.gz with timestamp.
"""

import logging
import os
import tarfile
import threading
import time

from pathlib import Path
from datetime import datetime
from typing import List, Optional, Callable

LEVELS = {"INFO", "WARNING", "ERROR", "DEBUG"}


def _timestamp_for_rotation() -> str:
    """Return timestamp string for rotated log filename: YYYY-MM-DDTHH-MM-SS.fff"""
    n = datetime.now()
    return n.strftime("%Y-%m-%dT%H-%M-%S") + f".{n.microsecond // 1000:03d}"


class Logger:
    def __init__(
        self,
        log_file: str | Path,
        log_levels: List[str],
        callback: Optional[Callable[[str], None]] = None,
        rotation_max_bytes: Optional[int] = None,
        rotation_interval_hours: Optional[float] = None,
        rotation_archive_dir: Optional[str] = None,
    ):
        if not all(level in LEVELS for level in log_levels):
            raise ValueError(f"Invalid log levels provided. Valid levels are: {LEVELS}")

        self.log_levels = log_levels
        self.log_file = Path(log_file) if isinstance(log_file, str) else log_file
        self.callback = callback
        self.logger = logging.getLogger("mss-login")
        self._lock = threading.Lock()
        self._rotation_max_bytes = rotation_max_bytes
        self._rotation_interval_hours = rotation_interval_hours
        self._rotation_archive_dir = (
            Path(rotation_archive_dir) if rotation_archive_dir else self.log_file.parent
        )
        self._last_rotate_time: float = time.time()

    def _maybe_rotate(self) -> None:
        """Rotate log if size or interval threshold exceeded. Caller must hold _lock."""
        if self._rotation_max_bytes is None and self._rotation_interval_hours is None:
            return
        if not self.log_file.exists():
            return
        try:
            size = self.log_file.stat().st_size
        except OSError:
            return
        now = time.time()
        interval_sec = (self._rotation_interval_hours or 0) * 3600
        size_ok = self._rotation_max_bytes is None or size < self._rotation_max_bytes
        time_ok = interval_sec <= 0 or (now - self._last_rotate_time) < interval_sec
        if size_ok and time_ok:
            return
        self._do_rotate()

    def _do_rotate(self) -> None:
        """Rotate current log file and compress to .tar.gz. Caller must hold _lock."""
        ts = _timestamp_for_rotation()
        base = f"MSS-Login-{ts}"
        archive_name = base + ".tar.gz"
        log_member_name = base + ".log"
        try:
            self._rotation_archive_dir.mkdir(parents=True, exist_ok=True)
        except OSError:
            return
        rotated_path = self.log_file.parent / (base + ".log")
        try:
            self.log_file.rename(rotated_path)
        except OSError:
            return
        try:
            self.log_file.touch()
        except OSError:
            try:
                rotated_path.rename(self.log_file)
            except OSError:
                pass
            return
        self._last_rotate_time = time.time()
        archive_path = self._rotation_archive_dir / archive_name
        try:
            with tarfile.open(archive_path, "w:gz") as tar:
                tar.add(rotated_path, arcname=log_member_name)
        except Exception:
            pass
        try:
            rotated_path.unlink()
        except OSError:
            pass

    def _log_file(self, level: str, message: str) -> None:
        """Write to log file only (sanitized: no IP, API key, or password)."""
        if level not in self.log_levels:
            return
        log_entry = f"{datetime.now().isoformat()} - {level} - {message}\n"
        with self._lock:
            self._maybe_rotate()
            try:
                with open(self.log_file, "a") as f:
                    f.write(log_entry)
            except Exception:
                pass
        if self.callback:
            self.callback(log_entry)

    def _log_console(self, level: str, message: str) -> None:
        """Write to ComfyUI console only (may include IP for operator visibility)."""
        if level not in self.log_levels:
            return
        if level == "INFO":
            self.logger.info(message)
        elif level == "WARNING":
            self.logger.warning(message)
        elif level == "ERROR":
            self.logger.error(message)
        elif level == "DEBUG":
            self.logger.debug(message)

    def log_message(self, level: str, message: str) -> None:
        """Write same message to file and console (use only for non-sensitive messages)."""
        self._log_file(level, message)
        self._log_console(level, message)

    def info(self, message: str) -> None:
        self.log_message("INFO", message)

    def warning(self, message: str) -> None:
        self.log_message("WARNING", message)

    def error(self, message: str) -> None:
        self.log_message("ERROR", message)

    def debug(self, message: str) -> None:
        self.log_message("DEBUG", message)

    def login_attempt(self, ip: str, username: str) -> None:
        """Log attempted login (no password). File: sanitized; console: may include IP."""
        self._log_file("WARNING", f"Attempted login for username: '{username}'")
        self._log_console("WARNING", f"Attempted login for username: '{username}' from IP: {ip}")

    def login_success(self, ip: str, username: str) -> None:
        """File: no IP; console: may include IP."""
        self._log_file("INFO", f"User: '{username}' logged in")
        self._log_console("INFO", f"User: '{username}' logged in from IP: {ip}")

    def login_failed(self, ip: str, username: str) -> None:
        """Log failed login (no password). File: sanitized; console: may include IP."""
        self._log_file("WARNING", f"Failed login for username: '{username}'")
        self._log_console("WARNING", f"Failed login for username: '{username}' from IP: {ip}")

    def generate_attempt(self, ip: str, username: str, expire_hours: int) -> None:
        """Log token generation attempt (no password)."""
        self._log_file(
            "WARNING",
            f"Attempted token generation for username: '{username}' with expiration hours: {expire_hours}",
        )
        self._log_console(
            "WARNING", f"Attempted token generation for username: '{username}' from IP: {ip}"
        )

    def generate_success(self, ip: str, username: str, expire_hours: int) -> None:
        self._log_file(
            "INFO", f"User: '{username}' generated token with expiration hours: {expire_hours}"
        )
        self._log_console(
            "INFO",
            f"User: '{username}' generated token from IP: {ip} with expiration hours: {expire_hours}",
        )

    def registration_attempt(self, ip: str, username: str, new_username: str) -> None:
        """Log registration attempt (no passwords)."""
        self._log_file(
            "WARNING",
            f"Attempted registration for new user: '{new_username}' by username: '{username}'",
        )
        self._log_console(
            "WARNING",
            f"Attempted registration for new user: '{new_username}' by '{username}' from IP: {ip}",
        )

    def registration_success(self, ip: str, new_user: str, registered_by: str = None) -> None:
        if registered_by:
            self._log_file("INFO", f"New user: '{new_user}' registered by '{registered_by}'")
            self._log_console(
                "INFO", f"New user: '{new_user}' registered by '{registered_by}' from IP: {ip}"
            )
        else:
            self._log_file("INFO", f"Admin user: '{new_user}' registered")
            self._log_console("INFO", f"Admin user: '{new_user}' registered from IP: {ip}")

    def memory_free(self, ip: str, username: str, free_memory: bool, unload_models: bool) -> None:
        if free_memory:
            self._log_file("INFO", f"User: '{username}' freed memory")
            self._log_console("INFO", f"User: '{username}' freed memory from IP: {ip}")
        if unload_models:
            self._log_file("INFO", f"User: '{username}' unloaded models")
            self._log_console("INFO", f"User: '{username}' unloaded models from IP: {ip}")

    def logout(self, ip: str, username: str) -> None:
        self._log_file("INFO", f"User: '{username}' logged out")
        self._log_console("INFO", f"User: '{username}' logged out from IP: {ip}")

    def log_jwt_if_debug(self, token: str, username: str) -> None:
        """When DEBUG_MODE is on: write JWT token to log file and ComfyUI console. Do not call when DEBUG_MODE is off."""
        try:
            from ..constants import DEBUG_MODE

            if not DEBUG_MODE:
                return
            msg = f"JWT token (DEBUG_MODE): {token}"
            with self._lock:
                try:
                    with open(self.log_file, "a") as f:
                        f.write(f"{datetime.now().isoformat()} - INFO - {msg}\n")
                except Exception:
                    pass
            self.logger.info(msg)
        except Exception:
            pass

    def log_jwt_created_console_only(self, username: str) -> None:
        """Log that a JWT token was created for the user. ComfyUI console only; no file writes (for when DEBUG_MODE is off)."""
        self.logger.info(f"JWT token created for user: {username}")
