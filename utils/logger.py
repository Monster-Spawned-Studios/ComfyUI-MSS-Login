import logging

from pathlib import Path
from datetime import datetime
from typing import List, Optional, Callable

LEVELS = {"INFO", "WARNING", "ERROR", "DEBUG"}


class Logger:
    def __init__(
        self,
        log_file: str | Path,
        log_levels: List[str],
        callback: Optional[Callable[[str], None]] = None,
    ):
        if not all(level in LEVELS for level in log_levels):
            raise ValueError(f"Invalid log levels provided. Valid levels are: {LEVELS}")

        self.log_levels = log_levels
        self.log_file = log_file
        self.callback = callback

        self.logger = logging.getLogger("mss-login")

    def log_message(self, level: str, message: str) -> None:
        if level not in self.log_levels:
            return

        log_entry = f"{datetime.now().isoformat()} - {level} - {message}\n"

        with open(self.log_file, "a") as log_file:
            log_file.write(log_entry)

        if level == "INFO":
            self.logger.info(message)
        elif level == "WARNING":
            self.logger.warning(message)
        elif level == "ERROR":
            self.logger.error(message)
        elif level == "DEBUG":
            self.logger.debug(message)

        if self.callback:
            self.callback(log_entry)

    def info(self, message: str) -> None:
        self.log_message("INFO", message)

    def warning(self, message: str) -> None:
        self.log_message("WARNING", message)

    def error(self, message: str) -> None:
        self.log_message("ERROR", message)

    def debug(self, message: str) -> None:
        self.log_message("DEBUG", message)

    def login_attempt(self, ip: str, username: str, password: str) -> None:
        self.info(
            f"Warning: Attempted login from IP: {ip} with username: '{username}' and password: '{password}'"
        )

    def login_success(self, ip: str, username: str) -> None:
        self.info(f"User: '{username}' logged in from IP: {ip}")

    def generate_attempt(
        self, ip: str, username: str, password: str, expire_hours: int
    ) -> None:
        self.info(
            f"Warning: Attempted generation from IP: {ip} with username: '{username}', password: '{password}' and expiration hours: {expire_hours}"
        )

    def generate_success(self, ip: str, username: str, expire_hours: int) -> None:
        self.info(
            f"User: '{username}' generated token from IP: {ip} with expiration hours: {expire_hours}"
        )

    def registration_attempt(
        self,
        ip: str,
        username: str,
        password: str,
        new_username: str,
        new_password: str,
    ) -> None:
        self.info(
            f"Warning: Attempted registration from IP: {ip} with username: '{username}' and password: '{password}' for new user username: '{new_username}' and password: '{new_password}'"
        )

    def registration_success(
        self, ip: str, new_user: str, registered_by: str = None
    ) -> None:
        if registered_by:
            self.info(
                f"New user: '{new_user}' Registered by '{registered_by}' from IP: {ip}"
            )
        else:
            self.info(f"Admin user: '{new_user}' Registered from IP: {ip}")

    def memory_free(
        self, ip: str, username: str, free_memory: bool, unload_models: bool
    ) -> None:
        if free_memory:
            self.info(f"User: '{username}' freed memory from IP: {ip}")

        if unload_models:
            self.info(f"User: '{username}' unloaded models from IP: {ip}")

    def logout(self, ip: str, username: str) -> None:
        self.info(f"User: '{username}' logged out from IP: {ip}")

    def log_jwt_if_debug(self, token: str, username: str) -> None:
        """When DEBUG_MODE is on: write JWT token to log file and ComfyUI console. Do not call when DEBUG_MODE is off."""
        try:
            from ..constants import DEBUG_MODE

            if not DEBUG_MODE:
                return
            msg = f"JWT token (DEBUG_MODE): {token}"
            with open(self.log_file, "a") as f:
                f.write(f"{datetime.now().isoformat()} - INFO - {msg}\n")
            self.logger.info(msg)
        except Exception:
            pass

    def log_jwt_created_console_only(self, username: str) -> None:
        """Log that a JWT token was created for the user. ComfyUI console only; no file writes (for when DEBUG_MODE is off)."""
        self.logger.info(f"JWT token created for user: {username}")
