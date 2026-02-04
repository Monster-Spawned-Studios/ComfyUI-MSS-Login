# --- START OF FILE constants.py ---
import os
import sys
import json
import warnings
import uuid

# --- Base Directories ---
CURRENT_DIR = os.path.dirname(os.path.abspath(__file__))

# --- Load .env from node root (sensitive vars; OS env fallback for Docker/Compose) ---
# SECRET_KEY and other secrets can be in .env (optionally encrypted via dotenvx encrypt).
_env_path = os.path.join(CURRENT_DIR, ".env")
try:
    from dotenvx import load_dotenvx
    load_dotenvx(dotenv_path=_env_path, override=True)
except ImportError:
    try:
        from dotenv import load_dotenv
        load_dotenv(dotenv_path=_env_path, override=True)
    except ImportError:
        pass

WEB_DIR = os.path.join(CURRENT_DIR, "web")

# NOTE: If your html files are directly in web/, remove the 'html' part below
# But based on standard structure, they should be in web/html/
HTML_DIR = os.path.join(WEB_DIR, "html") 
CSS_DIR = os.path.join(WEB_DIR, "css")
JS_DIR = os.path.join(WEB_DIR, "js")
ASSETS_DIR = os.path.join(WEB_DIR, "assets")

# --- Load config.json ---
CONFIG_FILE_PATH = os.path.join(CURRENT_DIR, "config.json")


def _load_config(path):
    if os.path.exists(path):
        try:
            with open(path, "r") as f:
                return json.load(f)
        except Exception:
            pass
    return {}


def _env_or_config(env_key: str, config_value: str):
    """Resolve sensitive value: env (including from .env) wins, then config. For Docker/Compose compatibility."""
    return (os.getenv(env_key) or "").strip() or config_value or ""


def _default_sqlite_path() -> str:
    """Cross-platform default SQLite path: user-writable on Windows, macOS, Linux. Not written to config."""
    if sys.platform == "win32":
        base = os.environ.get("LOCALAPPDATA", "") or os.path.expanduser("~\\AppData\\Local")
    elif sys.platform == "darwin":
        base = os.path.expanduser("~/Library/Application Support")
    else:
        base = os.environ.get("XDG_DATA_HOME", "") or os.path.expanduser("~/.local/share")
    return os.path.join(base, "Usgromana", "api_tokens.db")


config_data = _load_config(CONFIG_FILE_PATH)

# --- Files & Paths ---
# Legacy path for one-time migration from JSON to DB (do not use for credential storage)
_legacy_users_path = config_data.get("legacy_users_json_path", "users/users.json")
if not os.path.isabs(_legacy_users_path):
    _legacy_users_path = os.path.join(CURRENT_DIR, _legacy_users_path)
LEGACY_USERS_JSON_PATH = _legacy_users_path

GROUPS_CONFIG_FILE = os.path.join(CURRENT_DIR, "users", "usgromana_groups.json")
DEFAULT_GROUP_CONFIG_PATH = os.path.join(CURRENT_DIR, "users", "defaults", "default_group_config.json")
WHITELIST_FILE = os.path.join(CURRENT_DIR, "users", "whitelist.txt")
BLACKLIST_FILE = os.path.join(CURRENT_DIR, "users", "blacklist.txt")
LOG_FILE = os.path.join(CURRENT_DIR, config_data.get("log", "usgromana.log"))

# --- Configuration Values ---
LOG_LEVELS = config_data.get("log_levels", ["INFO"])
SECRET_KEY = os.getenv(config_data.get("secret_key_env", "SECRET_KEY"))
if not SECRET_KEY:
    warnings.warn("[Usgromana] SECRET_KEY not set. Using random key (logouts on restart).")
    SECRET_KEY = "".join([str(uuid.uuid4().hex) for _ in range(128)])

TOKEN_EXPIRE_MINUTES = 60 * config_data.get("access_token_expiration_hours", 12)
MAX_TOKEN_EXPIRE_MINUTES = 60 * config_data.get("max_access_token_expiration_hours", 8760)
TOKEN_ALGORITHM = "HS256"

BLACKLIST_AFTER_ATTEMPTS = config_data.get("blacklist_after_attempts", 5)
FREE_MEMORY_ON_LOGOUT = config_data.get("free_memory_on_logout", True)
FORCE_HTTPS = config_data.get("force_https", False)
SEPERATE_USERS = config_data.get("seperate_users", True)
MANAGER_ADMIN_ONLY = config_data.get("manager_admin_only", True)
MATCH_HEADERS = {"X-Forwarded-Proto": "https"}

# API token store (long-lived Bearer tokens). Sensitive values: env (including .env) then config.
_api_token_cfg = config_data.get("api_token_store") or {}
_sqlite_from_env = _env_or_config("SQLITE_PATH", "") or _env_or_config("API_TOKEN_SQLITE_PATH", "")
_sqlite_from_config = _api_token_cfg.get("sqlite_path") or ""
_sqlite_path = _sqlite_from_env or _sqlite_from_config or _default_sqlite_path()
API_TOKEN_STORE_CONFIG = {
    "backend": (_api_token_cfg.get("backend") or "sqlite").lower(),
    "json_path": _env_or_config("API_TOKEN_JSON_PATH", _api_token_cfg.get("json_path", "users/api_tokens.json")),
    "sqlite_path": _sqlite_path,
    "postgres_host": _env_or_config("POSTGRES_HOST", _api_token_cfg.get("postgres_host", "localhost")),
    "postgres_port": _env_or_config("POSTGRES_PORT", str(_api_token_cfg.get("postgres_port", 5432))),
    "postgres_database": _env_or_config("POSTGRES_DATABASE", _api_token_cfg.get("postgres_database", "usgromana")),
    "postgres_user": _env_or_config("POSTGRES_USER", _api_token_cfg.get("postgres_user", "usgromana")),
}
if not os.path.isabs(API_TOKEN_STORE_CONFIG["json_path"]):
    API_TOKEN_STORE_CONFIG["json_path"] = os.path.join(CURRENT_DIR, API_TOKEN_STORE_CONFIG["json_path"])
if not os.path.isabs(API_TOKEN_STORE_CONFIG["sqlite_path"]):
    API_TOKEN_STORE_CONFIG["sqlite_path"] = os.path.join(CURRENT_DIR, API_TOKEN_STORE_CONFIG["sqlite_path"])

# Users DB (credentials): SQLite or PostgreSQL only; no plain-text JSON. Password from env only.
def _default_users_sqlite_path() -> str:
    return os.path.join(CURRENT_DIR, "users", "users.db")

_users_db_cfg = config_data.get("users_db") or {}
if isinstance(_users_db_cfg, str):
    _users_db_cfg = {"backend": "sqlite", "sqlite_path": _users_db_cfg}
_users_sqlite_path = _env_or_config("USERS_DB_SQLITE_PATH", _users_db_cfg.get("sqlite_path", "users/users.db"))
if not os.path.isabs(_users_sqlite_path):
    _users_sqlite_path = os.path.join(CURRENT_DIR, _users_sqlite_path)
USERS_DB_CONFIG = {
    "backend": (_users_db_cfg.get("backend") or "sqlite").lower(),
    "sqlite_path": _users_sqlite_path,
    "postgres_host": _env_or_config("USERS_DB_POSTGRES_HOST", _users_db_cfg.get("postgres_host", "localhost")),
    "postgres_port": _env_or_config("USERS_DB_POSTGRES_PORT", str(_users_db_cfg.get("postgres_port", 5432))),
    "postgres_database": _env_or_config("USERS_DB_POSTGRES_DATABASE", _users_db_cfg.get("postgres_database", "usgromana")),
    "postgres_user": _env_or_config("USERS_DB_POSTGRES_USER", _users_db_cfg.get("postgres_user", "usgromana")),
}
# DB password never in config; env only
USERS_DB_CONFIG["postgres_password"] = (os.getenv("USERS_DB_PASSWORD") or os.getenv("POSTGRES_PASSWORD") or "").strip()


def reload_users_db_config() -> dict:
    """Re-read config.json and refresh USERS_DB_CONFIG (used after admin saves users DB config). Restart required to use new backend."""
    global config_data, USERS_DB_CONFIG
    config_data = _load_config(CONFIG_FILE_PATH)
    _users_db_cfg = config_data.get("users_db") or {}
    if isinstance(_users_db_cfg, str):
        _users_db_cfg = {"backend": "sqlite", "sqlite_path": _users_db_cfg}
    _users_sqlite_path = _env_or_config("USERS_DB_SQLITE_PATH", _users_db_cfg.get("sqlite_path", "users/users.db"))
    if not os.path.isabs(_users_sqlite_path):
        _users_sqlite_path = os.path.join(CURRENT_DIR, _users_sqlite_path)
    USERS_DB_CONFIG = {
        "backend": (_users_db_cfg.get("backend") or "sqlite").lower(),
        "sqlite_path": _users_sqlite_path,
        "postgres_host": _env_or_config("USERS_DB_POSTGRES_HOST", _users_db_cfg.get("postgres_host", "localhost")),
        "postgres_port": _env_or_config("USERS_DB_POSTGRES_PORT", str(_users_db_cfg.get("postgres_port", 5432))),
        "postgres_database": _env_or_config("USERS_DB_POSTGRES_DATABASE", _users_db_cfg.get("postgres_database", "usgromana")),
        "postgres_user": _env_or_config("USERS_DB_POSTGRES_USER", _users_db_cfg.get("postgres_user", "usgromana")),
    }
    USERS_DB_CONFIG["postgres_password"] = (os.getenv("USERS_DB_PASSWORD") or os.getenv("POSTGRES_PASSWORD") or "").strip()
    return USERS_DB_CONFIG


# Session JWT store (jti tracking and blocklist for list/revoke)
_session_store_path = config_data.get("session_token_store_path", "users/session_tokens.json")
if not os.path.isabs(_session_store_path):
    _session_store_path = os.path.join(CURRENT_DIR, _session_store_path)
SESSION_TOKEN_STORE_PATH = _session_store_path

# Remote API guard: require auth for non-local clients
REQUIRE_AUTH_FOR_REMOTE_API = config_data.get("require_auth_for_remote_api", True)
LOCAL_NETWORK_CIDRS = config_data.get("local_network_cidrs") or []

# DEBUG_MODE: load from environment (Docker/Compose) then config.json for diagnosis
DEBUG_MODE = str(os.environ.get("DEBUG_MODE", "")).strip().lower() in ("1", "true", "yes")
if not DEBUG_MODE:
    DEBUG_MODE = bool(config_data.get("debug_mode", False))
DEBUG_LOG_PATH = os.path.join(CURRENT_DIR, "logs", "debug.log")
# Session debug log for instrumentation (e.g. .cursor/debug.log)
CURSOR_DEBUG_LOG = os.path.join(CURRENT_DIR, ".cursor", "debug.log")

# Guest JWT: allow guest login to receive a session JWT (default False for security)
def _get_allow_guest_jwt():
    env_val = str(os.environ.get("ALLOW_GUEST_JWT", "")).strip().lower()
    if env_val in ("1", "true", "yes"):
        return True
    if env_val in ("0", "false", "no"):
        return False
    cfg = _load_config(CONFIG_FILE_PATH)
    return bool(cfg.get("allow_guest_jwt", False))


ALLOW_GUEST_JWT = _get_allow_guest_jwt()


def reload_allow_guest_jwt() -> bool:
    """Re-read config and refresh ALLOW_GUEST_JWT (used after Admin saves guest-JWT setting)."""
    global ALLOW_GUEST_JWT
    ALLOW_GUEST_JWT = _get_allow_guest_jwt()
    return ALLOW_GUEST_JWT


def reload_api_token_store_config() -> dict:
    """Re-read config.json and refresh API_TOKEN_STORE_CONFIG (used after saving token storage config)."""
    global API_TOKEN_STORE_CONFIG
    cfg = _load_config(CONFIG_FILE_PATH)
    _api_token_cfg = cfg.get("api_token_store") or {}
    _sqlite_from_env = _env_or_config("SQLITE_PATH", "") or _env_or_config("API_TOKEN_SQLITE_PATH", "")
    _sqlite_from_config = _api_token_cfg.get("sqlite_path") or ""
    _sqlite_path = _sqlite_from_env or _sqlite_from_config or _default_sqlite_path()
    API_TOKEN_STORE_CONFIG = {
        "backend": (_api_token_cfg.get("backend") or "sqlite").lower(),
        "json_path": _env_or_config("API_TOKEN_JSON_PATH", _api_token_cfg.get("json_path", "users/api_tokens.json")),
        "sqlite_path": _sqlite_path,
        "postgres_host": _env_or_config("POSTGRES_HOST", _api_token_cfg.get("postgres_host", "localhost")),
        "postgres_port": _env_or_config("POSTGRES_PORT", str(_api_token_cfg.get("postgres_port", 5432))),
        "postgres_database": _env_or_config("POSTGRES_DATABASE", _api_token_cfg.get("postgres_database", "usgromana")),
        "postgres_user": _env_or_config("POSTGRES_USER", _api_token_cfg.get("postgres_user", "usgromana")),
    }
    if not os.path.isabs(API_TOKEN_STORE_CONFIG["json_path"]):
        API_TOKEN_STORE_CONFIG["json_path"] = os.path.join(CURRENT_DIR, API_TOKEN_STORE_CONFIG["json_path"])
    if not os.path.isabs(API_TOKEN_STORE_CONFIG["sqlite_path"]):
        API_TOKEN_STORE_CONFIG["sqlite_path"] = os.path.join(CURRENT_DIR, API_TOKEN_STORE_CONFIG["sqlite_path"])
    return API_TOKEN_STORE_CONFIG