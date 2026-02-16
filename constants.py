# --- START OF FILE constants.py ---
import os
import json
import warnings
import uuid

from .utils.install_deps import install_dependencies

# --- Base Directories ---
CURRENT_DIR = os.path.dirname(os.path.abspath(__file__))

# --- Load .env from node root (sensitive vars; OS env fallback for Docker/Compose) ---
# SECRET_KEY and other secrets can be in .env (optionally encrypted via dotenvx encrypt).
_env_path = os.path.join(CURRENT_DIR, ".env")
try:
    from dotenv import load_dotenv

    load_dotenv(dotenv_path=_env_path, override=True)
except ImportError:
    print("[mss_login] dotenv not found, trying dotenvx")
    try:
        from dotenvx import load_dotenvx

        load_dotenvx(dotenv_path=_env_path, override=True)
    except ImportError:
        print("[mss_login] dotenvx not found, using os.environ")
        pass
    except Exception as e:
        print(f"[mss_login] Failed to load .env with dotenvx: {e}")
        pass
except Exception as e:
    print(f"[mss_login] Failed to load .env with dotenv: {e}")
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


config_data = _load_config(CONFIG_FILE_PATH)


def _normalize_encryption_level(level: str) -> str:
    """Map medium->standard, high->secure; return low|standard|secure."""
    if not level:
        return ""
    s = str(level).strip().lower()
    if s in ("medium", "standard"):
        return "standard"
    if s in ("high", "secure"):
        return "secure"
    if s == "low":
        return "low"
    return s if s in ("low", "standard", "secure") else ""


# --- Files & Paths ---
# Legacy path for one-time migration from JSON to DB (do not use for credential storage)
_legacy_users_path = config_data.get("legacy_users_json_path", "users/users.json")
if not os.path.isabs(_legacy_users_path):
    _legacy_users_path = os.path.join(CURRENT_DIR, _legacy_users_path)
LEGACY_USERS_JSON_PATH = _legacy_users_path

GROUPS_CONFIG_FILE = os.path.join(CURRENT_DIR, "users", "mss_login_groups.json")
DEFAULT_GROUP_CONFIG_PATH = os.path.join(
    CURRENT_DIR, "users", "defaults", "default_group_config.json"
)
WHITELIST_FILE = os.path.join(CURRENT_DIR, "users", "whitelist.txt")
BLACKLIST_FILE = os.path.join(CURRENT_DIR, "users", "blacklist.txt")
LOG_FILE = os.path.join(CURRENT_DIR, config_data.get("log", "mss_login.log"))

# --- Ephemeral SECRET_KEY (for migration when switching to permanent SECRET_KEY) ---
_users_dir = os.path.join(CURRENT_DIR, "users")
EPHEMERAL_SECRET_KEY_PATH = os.path.join(_users_dir, ".ephemeral_secret_key")


def _load_ephemeral_key() -> str:
    """Load ephemeral secret key from file if present. Return empty string if not found or read fails."""
    if not os.path.isfile(EPHEMERAL_SECRET_KEY_PATH):
        return ""
    try:
        with open(EPHEMERAL_SECRET_KEY_PATH, "r", encoding="utf-8") as f:
            return (f.read() or "").strip()
    except Exception:
        return ""


def _persist_ephemeral_key(key: str) -> None:
    """Write ephemeral secret key to file (mode 0o600). Used when SECRET_KEY is unset so migration can run later."""
    if not key:
        return
    try:
        os.makedirs(os.path.dirname(EPHEMERAL_SECRET_KEY_PATH), exist_ok=True)
        with open(EPHEMERAL_SECRET_KEY_PATH, "w", encoding="utf-8") as f:
            f.write(key)
        os.chmod(EPHEMERAL_SECRET_KEY_PATH, 0o600)
    except Exception:
        pass


# --- Configuration Values ---
LOG_LEVELS = config_data.get("log_levels", ["INFO"])
_secret_key_env = config_data.get("secret_key_env", "SECRET_KEY")
SECRET_KEY = (os.getenv(_secret_key_env) or "").strip()
if not SECRET_KEY:
    warnings.warn(
        "[MSS-Login] SECRET_KEY not set. Using random key (logouts on restart)."
    )
    SECRET_KEY = "".join([str(uuid.uuid4().hex) for _ in range(128)])
    _persist_ephemeral_key(SECRET_KEY)

TOKEN_EXPIRE_MINUTES = 60 * config_data.get("access_token_expiration_hours", 12)
MAX_TOKEN_EXPIRE_MINUTES = 60 * config_data.get(
    "max_access_token_expiration_hours", 8760
)
TOKEN_ALGORITHM = "HS256"

BLACKLIST_AFTER_ATTEMPTS = config_data.get("blacklist_after_attempts", 5)
FREE_MEMORY_ON_LOGOUT = config_data.get("free_memory_on_logout", True)
FORCE_HTTPS = config_data.get("force_https", False)
SEPERATE_USERS = config_data.get("seperate_users", True)
MANAGER_ADMIN_ONLY = config_data.get("manager_admin_only", True)
MATCH_HEADERS = {"X-Forwarded-Proto": "https"}


# Users DB (credentials): single source of truth for backend, path, and Postgres. Password from env only.
def _default_users_sqlite_path() -> str:
    return os.path.join(CURRENT_DIR, "users", "users.db")


_users_db_cfg = config_data.get("users_db") or {}
if isinstance(_users_db_cfg, str):
    _users_db_cfg = {"backend": "sqlite", "sqlite_path": _users_db_cfg}
_users_sqlite_path = _env_or_config(
    "USERS_DB_SQLITE_PATH", _users_db_cfg.get("sqlite_path", "users/users.db")
)
if not os.path.isabs(_users_sqlite_path):
    _users_sqlite_path = os.path.join(CURRENT_DIR, _users_sqlite_path)
USERS_DB_CONFIG = {
    "backend": (_users_db_cfg.get("backend") or "sqlite").lower(),
    "sqlite_path": _users_sqlite_path,
    "postgres_host": _env_or_config(
        "USERS_DB_POSTGRES_HOST", _users_db_cfg.get("postgres_host", "localhost")
    ),
    "postgres_port": _env_or_config(
        "USERS_DB_POSTGRES_PORT", str(_users_db_cfg.get("postgres_port", 5432))
    ),
    "postgres_database": _env_or_config(
        "USERS_DB_POSTGRES_DATABASE",
        _users_db_cfg.get("postgres_database", "mss_login"),
    ),
    "postgres_user": _env_or_config(
        "USERS_DB_POSTGRES_USER", _users_db_cfg.get("postgres_user", "mss_login")
    ),
    "encryption_level": _normalize_encryption_level(
        _users_db_cfg.get("encryption_level", "")
    ),
}
# DB password never in config; env only (unified for users, api_tokens, shared_items)
USERS_DB_CONFIG["postgres_password"] = (
    os.getenv("USERS_DB_PASSWORD") or os.getenv("POSTGRES_PASSWORD") or ""
).strip()

# API token store: "json" = legacy file; otherwise use same DB as users (backend, sqlite_path, postgres from USERS_DB_CONFIG).
_api_token_cfg = config_data.get("api_token_store") or {}
_token_backend = (_api_token_cfg.get("backend") or "").strip().lower()
if _token_backend == "json":
    _api_backend = "json"
else:
    _api_backend = USERS_DB_CONFIG["backend"]
_json_path = _env_or_config(
    "API_TOKEN_JSON_PATH", _api_token_cfg.get("json_path", "users/api_tokens.json")
)
if not os.path.isabs(_json_path):
    _json_path = os.path.join(CURRENT_DIR, _json_path)
API_TOKEN_STORE_CONFIG = {
    "backend": _api_backend,
    "json_path": _json_path,
    "sqlite_path": USERS_DB_CONFIG["sqlite_path"],
    "postgres_host": USERS_DB_CONFIG["postgres_host"],
    "postgres_port": USERS_DB_CONFIG["postgres_port"],
    "postgres_database": USERS_DB_CONFIG["postgres_database"],
    "postgres_user": USERS_DB_CONFIG["postgres_user"],
    "postgres_password": USERS_DB_CONFIG["postgres_password"],
    "encryption_level": USERS_DB_CONFIG.get("encryption_level", ""),
}


def reload_users_db_config() -> dict:
    """Re-read config.json and refresh USERS_DB_CONFIG (used after admin saves users DB config). Restart required to use new backend."""
    global config_data, USERS_DB_CONFIG, API_TOKEN_STORE_CONFIG
    config_data = _load_config(CONFIG_FILE_PATH)
    _users_db_cfg = config_data.get("users_db") or {}
    if isinstance(_users_db_cfg, str):
        _users_db_cfg = {"backend": "sqlite", "sqlite_path": _users_db_cfg}
    _users_sqlite_path = _env_or_config(
        "USERS_DB_SQLITE_PATH", _users_db_cfg.get("sqlite_path", "users/users.db")
    )
    if not os.path.isabs(_users_sqlite_path):
        _users_sqlite_path = os.path.join(CURRENT_DIR, _users_sqlite_path)
    USERS_DB_CONFIG = {
        "backend": (_users_db_cfg.get("backend") or "sqlite").lower(),
        "sqlite_path": _users_sqlite_path,
        "postgres_host": _env_or_config(
            "USERS_DB_POSTGRES_HOST", _users_db_cfg.get("postgres_host", "localhost")
        ),
        "postgres_port": _env_or_config(
            "USERS_DB_POSTGRES_PORT", str(_users_db_cfg.get("postgres_port", 5432))
        ),
        "postgres_database": _env_or_config(
            "USERS_DB_POSTGRES_DATABASE",
            _users_db_cfg.get("postgres_database", "mss_login"),
        ),
        "postgres_user": _env_or_config(
            "USERS_DB_POSTGRES_USER", _users_db_cfg.get("postgres_user", "mss_login")
        ),
        "encryption_level": _normalize_encryption_level(
            _users_db_cfg.get("encryption_level", "")
        ),
    }
    USERS_DB_CONFIG["postgres_password"] = (
        os.getenv("USERS_DB_PASSWORD") or os.getenv("POSTGRES_PASSWORD") or ""
    ).strip()
    # Keep API token store in sync (same DB unless backend is "json")
    _api_cfg = config_data.get("api_token_store") or {}
    _tb = (_api_cfg.get("backend") or "").strip().lower()
    _api_backend = "json" if _tb == "json" else USERS_DB_CONFIG["backend"]
    _jp = _env_or_config(
        "API_TOKEN_JSON_PATH", _api_cfg.get("json_path", "users/api_tokens.json")
    )
    if not os.path.isabs(_jp):
        _jp = os.path.join(CURRENT_DIR, _jp)
    API_TOKEN_STORE_CONFIG = {
        "backend": _api_backend,
        "json_path": _jp,
        "sqlite_path": USERS_DB_CONFIG["sqlite_path"],
        "postgres_host": USERS_DB_CONFIG["postgres_host"],
        "postgres_port": USERS_DB_CONFIG["postgres_port"],
        "postgres_database": USERS_DB_CONFIG["postgres_database"],
        "postgres_user": USERS_DB_CONFIG["postgres_user"],
        "postgres_password": USERS_DB_CONFIG["postgres_password"],
        "encryption_level": USERS_DB_CONFIG.get("encryption_level", ""),
    }
    return USERS_DB_CONFIG


# Session JWT store (jti tracking and blocklist for list/revoke)
_session_store_path = config_data.get(
    "session_token_store_path", "users/session_tokens.json"
)
if not os.path.isabs(_session_store_path):
    _session_store_path = os.path.join(CURRENT_DIR, _session_store_path)
SESSION_TOKEN_STORE_PATH = _session_store_path

# Remote API guard: require auth for non-local clients
REQUIRE_AUTH_FOR_REMOTE_API = config_data.get("require_auth_for_remote_api", True)
LOCAL_NETWORK_CIDRS = config_data.get("local_network_cidrs") or []

# DEBUG_MODE: load from environment (Docker/Compose) then config.json for diagnosis
DEBUG_MODE_FROM_ENV = str(os.environ.get("DEBUG_MODE", "")).strip().lower() in (
    "1",
    "true",
    "yes",
)
DEBUG_MODE = DEBUG_MODE_FROM_ENV or bool(config_data.get("debug_mode", False))
DEBUG_LOG_PATH = os.path.join(CURRENT_DIR, "logs", "debug.log")

AUTO_INSTALL_DEPS = config_data.get("auto_install_deps", True)
AUTO_INSTALL_DEPS_FROM_ENV = os.environ.get(
    "AUTO_INSTALL_DEPS", "1"
).strip().lower() in ("1", "true", "yes")
if AUTO_INSTALL_DEPS and AUTO_INSTALL_DEPS_FROM_ENV in ("1", "true", "yes"):
    if DEBUG_MODE:
        print("[mss_login::DEBUG] Auto-installing dependencies...")
        try:
            if not install_dependencies():
                print(
                    "[mss_login::DEBUG] Auto-installing dependencies failed.\n\nPlease install the dependencies manually using your package manager of choice and by following the instructions available in the README.md file."
                )
            else:
                print(
                    "[mss_login::DEBUG] Auto-installing dependencies succeeded. You can now start ComfyUI and use the extension."
                )
        except Exception as e:
            print(
                f"[mss_login::DEBUG] Auto-installing dependencies failed: '{e}'.\n\nPlease install the dependencies manually using your package manager of choice and by following the instructions available in the README.md file."
            )


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


# Recovery mode: locally-only endpoint to reset MFA when SECRET_KEY changed without migration
def _get_recovery_mode() -> bool:
    env_val = (
        str(
            os.environ.get("RECOVERY_MODE", "")
            or os.environ.get("RECOVERY_MODE_ENABLED", "")
        )
        .strip()
        .lower()
    )
    return env_val in ("1", "true", "yes")


def _get_recovery_mode_hosts() -> list:
    """Allowed client IPs for recovery mode. Default 127.0.0.1, ::1. Override via RECOVERY_MODE_HOST or RECOVRY_MODE_HOST (comma-separated)."""
    raw = (
        os.environ.get("RECOVERY_MODE_HOST")
        or os.environ.get("RECOVRY_MODE_HOST")
        or ""
    ).strip()
    if not raw:
        return ["127.0.0.1", "::1"]
    return [x.strip() for x in raw.split(",") if x.strip()]


RECOVERY_MODE = _get_recovery_mode()
RECOVERY_MODE_HOSTS = _get_recovery_mode_hosts()


def reload_allow_guest_jwt() -> bool:
    """Re-read config and refresh ALLOW_GUEST_JWT (used after Admin saves guest-JWT setting)."""
    global ALLOW_GUEST_JWT
    ALLOW_GUEST_JWT = _get_allow_guest_jwt()
    return ALLOW_GUEST_JWT


def reload_api_token_store_config() -> dict:
    """Re-read config and refresh API_TOKEN_STORE_CONFIG. Token store uses same DB as users; only json_path is token-specific."""
    reload_users_db_config()
    return API_TOKEN_STORE_CONFIG
