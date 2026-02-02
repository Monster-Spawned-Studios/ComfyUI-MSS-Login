# --- START OF FILE constants.py ---
import os
import json
import warnings
import uuid

# --- Base Directories ---
CURRENT_DIR = os.path.dirname(os.path.abspath(__file__))
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
            with open(path, "r") as f: return json.load(f)
        except: pass
    return {}

config_data = _load_config(CONFIG_FILE_PATH)

# --- Files & Paths ---
USERS_FILE = os.path.join(CURRENT_DIR, "users", "users.json")
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

# API token store (long-lived Bearer tokens)
_api_token_cfg = config_data.get("api_token_store") or {}
API_TOKEN_STORE_CONFIG = {
    "backend": _api_token_cfg.get("backend", "json"),
    "json_path": _api_token_cfg.get("json_path", "users/api_tokens.json"),
    "sqlite_path": _api_token_cfg.get("sqlite_path", "users/api_tokens.db"),
    "postgres_host": _api_token_cfg.get("postgres_host", "localhost"),
    "postgres_port": _api_token_cfg.get("postgres_port", 5432),
    "postgres_database": _api_token_cfg.get("postgres_database", "usgromana"),
    "postgres_user": _api_token_cfg.get("postgres_user", "usgromana"),
}
if not os.path.isabs(API_TOKEN_STORE_CONFIG["json_path"]):
    API_TOKEN_STORE_CONFIG["json_path"] = os.path.join(CURRENT_DIR, API_TOKEN_STORE_CONFIG["json_path"])
if not os.path.isabs(API_TOKEN_STORE_CONFIG["sqlite_path"]):
    API_TOKEN_STORE_CONFIG["sqlite_path"] = os.path.join(CURRENT_DIR, API_TOKEN_STORE_CONFIG["sqlite_path"])

# Remote API guard: require auth for non-local clients
REQUIRE_AUTH_FOR_REMOTE_API = config_data.get("require_auth_for_remote_api", True)
LOCAL_NETWORK_CIDRS = config_data.get("local_network_cidrs") or []

# DEBUG_MODE: load from environment (Docker/Compose) then config.json for diagnosis
DEBUG_MODE = str(os.environ.get("DEBUG_MODE", "")).strip().lower() in ("1", "true", "yes")
if not DEBUG_MODE:
    DEBUG_MODE = bool(config_data.get("debug_mode", False))
DEBUG_LOG_PATH = os.path.join(CURRENT_DIR, "logs", "debug.log")


def reload_api_token_store_config() -> dict:
    """Re-read config.json and refresh API_TOKEN_STORE_CONFIG (used after saving token storage config)."""
    global API_TOKEN_STORE_CONFIG
    cfg = _load_config(CONFIG_FILE_PATH)
    _api_token_cfg = cfg.get("api_token_store") or {}
    API_TOKEN_STORE_CONFIG = {
        "backend": _api_token_cfg.get("backend", "json"),
        "json_path": _api_token_cfg.get("json_path", "users/api_tokens.json"),
        "sqlite_path": _api_token_cfg.get("sqlite_path", "users/api_tokens.db"),
        "postgres_host": _api_token_cfg.get("postgres_host", "localhost"),
        "postgres_port": _api_token_cfg.get("postgres_port", 5432),
        "postgres_database": _api_token_cfg.get("postgres_database", "usgromana"),
        "postgres_user": _api_token_cfg.get("postgres_user", "usgromana"),
    }
    if not os.path.isabs(API_TOKEN_STORE_CONFIG["json_path"]):
        API_TOKEN_STORE_CONFIG["json_path"] = os.path.join(CURRENT_DIR, API_TOKEN_STORE_CONFIG["json_path"])
    if not os.path.isabs(API_TOKEN_STORE_CONFIG["sqlite_path"]):
        API_TOKEN_STORE_CONFIG["sqlite_path"] = os.path.join(CURRENT_DIR, API_TOKEN_STORE_CONFIG["sqlite_path"])
    return API_TOKEN_STORE_CONFIG