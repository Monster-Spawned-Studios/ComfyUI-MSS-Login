"""
Load and parse config.json from external data directory.
Provide global constants for the MSS-Login server.
"""

import json
import os
import uuid
import warnings
from pathlib import Path
from typing import Any, Dict

# Repo root (package directory, one level up from utils/)
EXT_PATH = os.path.join(os.path.dirname(__file__), "..")
# Runtime config lives in external data dir (~/.comfyui-mss-login)
from .data_dir import ensure_data_dir, get_data_dir

ensure_data_dir(EXT_PATH)
_DATA_DIR = get_data_dir()
CONFIG_FILE = os.path.join(_DATA_DIR, "config.json")
COMFY_ROOT = Path(__file__).resolve().parents[2]  # adjust if needed
USER_DATA_ROOT = COMFY_ROOT / "user_data"  # for future use
USER_OUTPUT_ROOT = COMFY_ROOT / "output" / "users"
USER_TEMP_ROOT = COMFY_ROOT / "temp" / "users"


def _resolve_data_path(rel_or_abs: str) -> str:
    """Resolve relative path under _DATA_DIR; contain to prevent path traversal."""
    if not rel_or_abs or os.path.isabs(rel_or_abs):
        return rel_or_abs or ""
    from .path_safety import resolve_path_under

    resolved = resolve_path_under(_DATA_DIR, rel_or_abs)
    return resolved if resolved is not None else _DATA_DIR


def load_config(file_path: str) -> Dict[str, Any]:
    if os.path.exists(file_path):
        with open(file_path, "r", encoding="utf-8") as f:
            try:
                return json.load(f)
            except json.JSONDecodeError:
                return {}
    return {}


config = load_config(CONFIG_FILE)

SECRET_KEY = os.getenv(config.get("secret_key_env", "SECRET_KEY"))

if not SECRET_KEY:
    warnings.warn(
        "The SECRET_KEY environment variable is not set. A random key will be used for this session. "
        "This will cause all users to log out on server restart."
    )
    SECRET_KEY = "".join([str(uuid.uuid4().hex) for _ in range(128)])

MATCH_HEADERS = {"X-Forwarded-Proto": "https"}

TOKEN_ALGORITHM = "HS256"
TOKEN_EXPIRE_MINUTES = 60 * config.get("access_token_expiration_hours", 12)
MAX_TOKEN_EXPIRE_MINUTES = 60 * config.get("max_access_token_expiration_hours", 8760)

_users_db_cfg = config.get("users_db", "users_db.json")
if isinstance(_users_db_cfg, dict):
    USERS_FILE = _resolve_data_path(_users_db_cfg.get("sqlite_path", "data/users.db"))
else:
    USERS_FILE = _resolve_data_path(_users_db_cfg)
LOG_FILE = _resolve_data_path(config.get("log", "mss_login.log"))
LOG_LEVELS = config.get("log_levels", ["INFO"])

WHITELIST = _resolve_data_path(config.get("whitelist", "data/whitelist.txt"))
BLACKLIST = _resolve_data_path(config.get("blacklist", "data/blacklist.txt"))

BLACKLIST_AFTER_ATTEMPTS = config.get("blacklist_after_attempts")

FREE_MEMORY_ON_LOGOUT = config.get("free_memory_on_logout", False)
FORCE_HTTPS = config.get("force_https", False)

SEPERATE_USERS = config.get("seperate_users", False)

MANAGER_ADMIN_ONLY = config.get("manager_admin_only", False)

WEB_DIR = os.path.join(EXT_PATH, "web")
HTML_DIR = os.path.join(WEB_DIR, "html")
CSS_DIR = os.path.join(WEB_DIR, "css")
JS_DIR = os.path.join(WEB_DIR, "js")
ASSETS_DIR = os.path.join(WEB_DIR, "assets")
