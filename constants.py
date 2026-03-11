"""
Constants for the MSS-Login server.
"""

# External data directory (~/.comfyui-mss-login or MSS_LOGIN_DATA_DIR); untouched by git pull.
from .utils.data_dir import ensure_data_dir, get_data_dir

# --- START OF FILE constants.py ---
import json
import os
import uuid
import warnings

from .utils.install_deps import install_dependencies

# --- Base Directories ---
CURRENT_DIR = os.path.dirname(os.path.abspath(__file__))

ensure_data_dir(CURRENT_DIR)
DATA_DIR = get_data_dir()


def _resolve_data_path(rel_or_abs: str) -> str:
    """If path is absolute return as-is; otherwise resolve relative to external data directory.
    Relative paths are contained under DATA_DIR to prevent path traversal (e.g. from config).
    """
    if not rel_or_abs:
        return rel_or_abs
    if os.path.isabs(rel_or_abs):
        return rel_or_abs
    from .utils.path_safety import resolve_path_under

    resolved = resolve_path_under(DATA_DIR, rel_or_abs)
    return resolved if resolved is not None else DATA_DIR


# --- Load .env: data dir first, then repo (so data dir secrets take precedence) ---
_env_data = os.path.join(DATA_DIR, ".env")
_env_repo = os.path.join(CURRENT_DIR, ".env")
try:
    from dotenv import load_dotenv

    load_dotenv(dotenv_path=_env_repo, override=False)
    load_dotenv(dotenv_path=_env_data, override=True)
except ImportError:
    print("[MSS-Login] dotenv not found, trying dotenvx")
    try:
        from dotenvx import load_dotenvx

        load_dotenvx(dotenv_path=_env_repo, override=False)
        load_dotenvx(dotenv_path=_env_data, override=True)
    except ImportError:
        print("[MSS-Login] dotenvx not found, using os.environ")
    except Exception as e:
        print(f"[mss_login] Failed to load .env with dotenvx: {e}")
except Exception as e:
    print(f"[mss_login] Failed to load .env with dotenv: {e}")

# NTFY API Key
NTFY_API_KEY = (os.getenv("NTFY_API_KEY") or "").strip()

WEB_DIR = os.path.join(CURRENT_DIR, "web")

# NOTE: If your html files are directly in web/, remove the 'html' part below
# But based on standard structure, they should be in web/html/
HTML_DIR = os.path.join(WEB_DIR, "html")
CSS_DIR = os.path.join(WEB_DIR, "css")
JS_DIR = os.path.join(WEB_DIR, "js")
ASSETS_DIR = os.path.join(WEB_DIR, "assets")

# --- Load config (runtime config in data dir; defaults from repo config.defaults.json) ---
CONFIG_FILE_PATH = os.path.join(DATA_DIR, "config.json")
DEFAULTS_CONFIG_PATH = os.path.join(CURRENT_DIR, "config.defaults.json")


def _load_config(path):
    if os.path.exists(path):
        try:
            with open(path, "r", encoding="utf-8") as f:
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


# --- Files & Paths (resolved under external data directory when relative) ---
# Legacy path for one-time migration from JSON to DB (do not use for credential storage)
LEGACY_USERS_JSON_PATH = _resolve_data_path(
    config_data.get("legacy_users_json_path", "data/users.json")
)
GROUPS_CONFIG_FILE = _resolve_data_path("data/mss_login_groups.json")
DEFAULT_GROUP_CONFIG_PATH = os.path.join(
    CURRENT_DIR, "users", "defaults", "default_group_config.json"
)
WHITELIST_FILE = _resolve_data_path(config_data.get("whitelist", "data/whitelist.txt"))
BLACKLIST_FILE = _resolve_data_path(config_data.get("blacklist", "data/blacklist.txt"))
LOG_FILE = _resolve_data_path(config_data.get("log", "mss_login.log"))

# Log rotation: rotate when size >= max bytes or once per interval
_log_rot_cfg = config_data.get("log_rotation") or {}
LOG_ROTATION_MAX_BYTES = int(_log_rot_cfg.get("max_bytes", 2 * 1024 * 1024))  # 2 MB
LOG_ROTATION_INTERVAL_HOURS = float(_log_rot_cfg.get("interval_hours", 24))
_log_archive = (_log_rot_cfg.get("archive_dir") or "").strip()
LOG_ROTATION_ARCHIVE_DIR = (
    _resolve_data_path(_log_archive) if _log_archive else os.path.dirname(LOG_FILE)
)

# security.json: lockout unlock overrides (unlock_ips, unlock_devices) in DATA_DIR
SECURITY_JSON_PATH = os.path.join(DATA_DIR, "security.json")

# --- Ephemeral SECRET_KEY (for migration when switching to permanent SECRET_KEY) ---
EPHEMERAL_SECRET_KEY_PATH = os.path.join(DATA_DIR, "data", ".ephemeral_secret_key")


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
TOKEN_ALGORITHM = config_data.get("token_algorithm", "HS256")
if TOKEN_ALGORITHM not in ("HS256", "RS256", "ES256", "PS256"):
    warnings.warn(
        f"[MSS-Login] Invalid token algorithm: {TOKEN_ALGORITHM}. Using HS256."
    )
    TOKEN_ALGORITHM = "HS256"

BLACKLIST_AFTER_ATTEMPTS = config_data.get("blacklist_after_attempts", 5)
# Hours after which auto-bans (from failed login attempts) expire; manual permabans use expires_at NULL
try:
    BLACKLIST_EXPIRY_HOURS = int(config_data.get("blacklist_expiry_hours", 24))
except (TypeError, ValueError):
    BLACKLIST_EXPIRY_HOURS = 24
FREE_MEMORY_ON_LOGOUT = config_data.get("free_memory_on_logout", True)
FORCE_HTTPS = config_data.get("force_https", False)
SEPERATE_USERS = config_data.get("seperate_users", True)
MANAGER_ADMIN_ONLY = config_data.get("manager_admin_only", True)
MATCH_HEADERS = {"X-Forwarded-Proto": "https"}


# Users DB (credentials): single source of truth for backend, path, and Postgres. Password from env only.
_users_db_cfg = config_data.get("users_db") or {}
if isinstance(_users_db_cfg, str):
    _users_db_cfg = {"backend": "sqlite", "sqlite_path": _users_db_cfg}
_users_sqlite_path = _env_or_config(
    "USERS_DB_SQLITE_PATH", _users_db_cfg.get("sqlite_path", "data/mss_login_data.db")
)
_users_sqlite_path = _resolve_data_path(_users_sqlite_path)
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
    "mysql_host": _env_or_config(
        "USERS_DB_MYSQL_HOST", _users_db_cfg.get("mysql_host", "localhost")
    ),
    "mysql_port": _env_or_config(
        "USERS_DB_MYSQL_PORT", str(_users_db_cfg.get("mysql_port", 3306))
    ),
    "mysql_database": _env_or_config(
        "USERS_DB_MYSQL_DATABASE",
        _users_db_cfg.get("mysql_database", "mss_login"),
    ),
    "mysql_user": _env_or_config(
        "USERS_DB_MYSQL_USER", _users_db_cfg.get("mysql_user", "mss_login")
    ),
    "encryption_level": _normalize_encryption_level(
        _users_db_cfg.get("encryption_level", "")
    ),
}
# DB password never in config; env only (unified for users, api_tokens, shared_items)
USERS_DB_CONFIG["postgres_password"] = (
    os.getenv("USERS_DB_PASSWORD") or os.getenv("POSTGRES_PASSWORD") or ""
).strip()
USERS_DB_CONFIG["mysql_password"] = (
    os.getenv("USERS_DB_PASSWORD") or os.getenv("MYSQL_PASSWORD") or ""
).strip()

# API token store: always use the same DB as users (one local database).
# Legacy "json" backend is no longer used; tokens live in the same SQLite/Postgres/MySQL as user accounts.
_api_token_cfg = config_data.get("api_token_store") or {}
_json_path = _resolve_data_path(
    _env_or_config(
        "API_TOKEN_JSON_PATH",
        _api_token_cfg.get("json_path", "data/api_tokens.json"),
    )
)
API_TOKEN_STORE_CONFIG = {
    "backend": USERS_DB_CONFIG["backend"],
    "json_path": _json_path,
    "sqlite_path": USERS_DB_CONFIG["sqlite_path"],
    "postgres_host": USERS_DB_CONFIG["postgres_host"],
    "postgres_port": USERS_DB_CONFIG["postgres_port"],
    "postgres_database": USERS_DB_CONFIG["postgres_database"],
    "postgres_user": USERS_DB_CONFIG["postgres_user"],
    "postgres_password": USERS_DB_CONFIG["postgres_password"],
    "mysql_host": USERS_DB_CONFIG["mysql_host"],
    "mysql_port": USERS_DB_CONFIG["mysql_port"],
    "mysql_database": USERS_DB_CONFIG["mysql_database"],
    "mysql_user": USERS_DB_CONFIG["mysql_user"],
    "mysql_password": USERS_DB_CONFIG["mysql_password"],
    "encryption_level": USERS_DB_CONFIG.get("encryption_level", ""),
}


def _localhost_fallback(use_https: bool = True, use_port: bool = False, port: int = 8188) -> str:
    """Build localhost base URL when HOST_BASE_URL and DB have no value."""
    scheme = "https" if use_https else "http"
    if use_port and port:
        return f"{scheme}://localhost:{port}"
    return f"{scheme}://localhost"


_host_base_url_cache = None


def clear_host_base_url_cache() -> None:
    """Invalidate in-memory host base URL cache so next get_host_base_url() re-reads from DB."""
    global _host_base_url_cache
    _host_base_url_cache = None


def _is_safe_base_url(url: str) -> bool:
    """Allow only http/https to avoid open redirects."""
    u = (url or "").strip().lower()
    return u.startswith("https://") or u.startswith("http://")


def get_host_base_url(
    use_https: bool = True, use_port: bool = False, port: int = 8188
) -> str:
    """
    Get the base URL for this host. Single source of truth. Resolution order:
    1. HOST_BASE_URL environment variable (if set, this always wins over auto-detected)
    2. Value stored in app_settings (from first admin connection or startup)
    3. Localhost fallback (scheme and port from arguments)
    The returned URL's scheme (HTTP/HTTPS) is taken from the configured or detected URL;
    use_https only affects the localhost fallback.
    """
    global _host_base_url_cache
    # HOST_BASE_URL takes priority over any auto-detected or stored URL when set
    env_url = (os.getenv("HOST_BASE_URL") or "").strip().rstrip("/")
    if env_url and _is_safe_base_url(env_url):
        return env_url
    if _host_base_url_cache is not None:
        return (
            _host_base_url_cache
            if _host_base_url_cache
            else _localhost_fallback(use_https=use_https, use_port=use_port, port=port)
        )
    from .utils.app_settings_store import get_app_settings_store

    store = get_app_settings_store(USERS_DB_CONFIG)
    val = (store.get("host_base_url") or "").strip().rstrip("/")
    if val and _is_safe_base_url(val):
        _host_base_url_cache = val
    else:
        _host_base_url_cache = ""
    return (
        _host_base_url_cache
        if _host_base_url_cache
        else _localhost_fallback(use_https=use_https, use_port=use_port, port=port)
    )


def get_domain(use_https: bool = True, use_port: bool = False, port: int = 8188) -> str:
    """
    Get the domain/base URL for the server. Derived from get_host_base_url() (HOST_BASE_URL
    or stored/detected URL). When the node needs a secure URL (use_https=True), the scheme
    is enforced to https if the resolved URL was http.
    """
    from urllib.parse import urlparse, urlunparse

    base = get_host_base_url(use_https=use_https, use_port=use_port, port=port)
    try:
        parsed = urlparse(base)
        if not parsed.scheme or not parsed.netloc:
            return base
        if use_https and parsed.scheme == "http":
            parsed = parsed._replace(scheme="https")
        if use_port and port:
            host = parsed.netloc.split(":")[0] if ":" in parsed.netloc else parsed.netloc
            parsed = parsed._replace(netloc=f"{host}:{port}")
        return urlunparse(parsed).rstrip("/") or base
    except Exception:
        return base


def reload_users_db_config() -> dict:
    """Re-read config and refresh USERS_DB_CONFIG (used after admin saves users DB config). Restart required to use new backend."""
    global config_data, USERS_DB_CONFIG, API_TOKEN_STORE_CONFIG, SESSION_TOKEN_STORE_CONFIG
    config_data = _load_config(CONFIG_FILE_PATH)
    _users_db_cfg = config_data.get("users_db") or {}
    if isinstance(_users_db_cfg, str):
        _users_db_cfg = {"backend": "sqlite", "sqlite_path": _users_db_cfg}
    _users_sqlite_path = _resolve_data_path(
        _env_or_config(
            "USERS_DB_SQLITE_PATH",
            _users_db_cfg.get("sqlite_path", "data/mss_login_data.db"),
        )
    )
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
        "mysql_host": _env_or_config(
            "USERS_DB_MYSQL_HOST", _users_db_cfg.get("mysql_host", "localhost")
        ),
        "mysql_port": _env_or_config(
            "USERS_DB_MYSQL_PORT", str(_users_db_cfg.get("mysql_port", 3306))
        ),
        "mysql_database": _env_or_config(
            "USERS_DB_MYSQL_DATABASE",
            _users_db_cfg.get("mysql_database", "mss_login"),
        ),
        "mysql_user": _env_or_config(
            "USERS_DB_MYSQL_USER", _users_db_cfg.get("mysql_user", "mss_login")
        ),
        "encryption_level": _normalize_encryption_level(
            _users_db_cfg.get("encryption_level", "")
        ),
    }
    USERS_DB_CONFIG["postgres_password"] = (
        os.getenv("USERS_DB_PASSWORD") or os.getenv("POSTGRES_PASSWORD") or ""
    ).strip()
    USERS_DB_CONFIG["mysql_password"] = (
        os.getenv("USERS_DB_PASSWORD") or os.getenv("MYSQL_PASSWORD") or ""
    ).strip()
    # Keep API token store in sync (same DB unless backend is "json")
    _api_cfg = config_data.get("api_token_store") or {}
    _jp = _resolve_data_path(
        _env_or_config(
            "API_TOKEN_JSON_PATH",
            _api_cfg.get("json_path", "data/api_tokens.json"),
        )
    )
    API_TOKEN_STORE_CONFIG = {
        "backend": USERS_DB_CONFIG["backend"],
        "json_path": _jp,
        "sqlite_path": USERS_DB_CONFIG["sqlite_path"],
        "postgres_host": USERS_DB_CONFIG["postgres_host"],
        "postgres_port": USERS_DB_CONFIG["postgres_port"],
        "postgres_database": USERS_DB_CONFIG["postgres_database"],
        "postgres_user": USERS_DB_CONFIG["postgres_user"],
        "postgres_password": USERS_DB_CONFIG["postgres_password"],
        "mysql_host": USERS_DB_CONFIG["mysql_host"],
        "mysql_port": USERS_DB_CONFIG["mysql_port"],
        "mysql_database": USERS_DB_CONFIG["mysql_database"],
        "mysql_user": USERS_DB_CONFIG["mysql_user"],
        "mysql_password": USERS_DB_CONFIG["mysql_password"],
        "encryption_level": USERS_DB_CONFIG.get("encryption_level", ""),
    }
    # Keep session token store in sync with users_db backend
    SESSION_TOKEN_STORE_CONFIG = {
        "backend": USERS_DB_CONFIG["backend"],
        "sqlite_path": USERS_DB_CONFIG["sqlite_path"],
        "encryption_level": USERS_DB_CONFIG.get("encryption_level", "standard"),
        "secret_key": SECRET_KEY,
        "postgres_host": USERS_DB_CONFIG["postgres_host"],
        "postgres_port": USERS_DB_CONFIG["postgres_port"],
        "postgres_database": USERS_DB_CONFIG["postgres_database"],
        "postgres_user": USERS_DB_CONFIG["postgres_user"],
        "postgres_password": USERS_DB_CONFIG["postgres_password"],
        "mysql_host": USERS_DB_CONFIG["mysql_host"],
        "mysql_port": USERS_DB_CONFIG["mysql_port"],
        "mysql_database": USERS_DB_CONFIG["mysql_database"],
        "mysql_user": USERS_DB_CONFIG["mysql_user"],
        "mysql_password": USERS_DB_CONFIG["mysql_password"],
        "legacy_json_path": "",
    }
    return USERS_DB_CONFIG


# Session JWT store: encrypted DB (same backend as users_db); legacy JSON path for one-time migration.
_session_cfg = config_data.get("session_token_store") or {}
_session_legacy_json = _resolve_data_path(
    config_data.get("session_token_store_path", "")
    or _session_cfg.get("legacy_json_path", "data/session_tokens.json")
)
SESSION_TOKEN_STORE_CONFIG = {
    "backend": USERS_DB_CONFIG["backend"],
    "sqlite_path": USERS_DB_CONFIG["sqlite_path"],
    "encryption_level": USERS_DB_CONFIG.get("encryption_level", "standard"),
    "secret_key": SECRET_KEY,
    "postgres_host": USERS_DB_CONFIG["postgres_host"],
    "postgres_port": USERS_DB_CONFIG["postgres_port"],
    "postgres_database": USERS_DB_CONFIG["postgres_database"],
    "postgres_user": USERS_DB_CONFIG["postgres_user"],
    "postgres_password": USERS_DB_CONFIG["postgres_password"],
    "mysql_host": USERS_DB_CONFIG["mysql_host"],
    "mysql_port": USERS_DB_CONFIG["mysql_port"],
    "mysql_database": USERS_DB_CONFIG["mysql_database"],
    "mysql_user": USERS_DB_CONFIG["mysql_user"],
    "mysql_password": USERS_DB_CONFIG["mysql_password"],
    "legacy_json_path": (
        _session_legacy_json if os.path.isfile(_session_legacy_json) else ""
    ),
}

# Idle session revocation: revoke session JWTs unused for this many minutes (security)
try:
    SESSION_IDLE_REVOKE_MINUTES = int(config_data.get("session_idle_revoke_minutes", 5))
except (TypeError, ValueError):
    SESSION_IDLE_REVOKE_MINUTES = 5

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


# Experimental features: master switch + per-feature (one-by-one; env overrides: EXPERIMENTAL_*)
def _get_experimental_features() -> bool:
    env_val = str(os.environ.get("EXPERIMENTAL_FEATURES", "")).strip().lower()
    if env_val in ("1", "true", "yes"):
        return True
    if env_val in ("0", "false", "no"):
        return False
    cfg = _load_config(CONFIG_FILE_PATH)
    return bool(cfg.get("experimental_features", False))


def _get_experimental_sub(key: str) -> bool:
    """Read a single experimental feature: env EXPERIMENTAL_<KEY> (uppercased) or config experimental.<key>."""
    env_key = "EXPERIMENTAL_" + key.upper()
    env_val = str(os.environ.get(env_key, "")).strip().lower()
    if env_val in ("1", "true", "yes"):
        return True
    if env_val in ("0", "false", "no"):
        return False
    cfg = _load_config(CONFIG_FILE_PATH)
    block = cfg.get("experimental")
    if isinstance(block, dict):
        return bool(block.get(key, False))
    return False


EXPERIMENTAL_FEATURES = _get_experimental_features()


def experimental_mfa_enabled() -> bool:
    """True if master experimental is on and MFA feature is enabled.

    Legacy upgrade path: when EXPERIMENTAL_FEATURES is on and experimental.mfa
    is unset in config, treat MFA as enabled (prior behavior) to avoid auth bypass
    for MFA-enrolled users after upgrade.
    """
    if not EXPERIMENTAL_FEATURES:
        return False
    env_val = str(os.environ.get("EXPERIMENTAL_MFA", "")).strip().lower()
    if env_val in ("1", "true", "yes"):
        return True
    if env_val in ("0", "false", "no"):
        return False
    cfg = _load_config(CONFIG_FILE_PATH)
    block = cfg.get("experimental")
    if isinstance(block, dict) and "mfa" in block:
        return bool(block.get("mfa", False))
    return True


def experimental_s3_enabled() -> bool:
    """True if master experimental is on and S3 feature is enabled."""
    return bool(EXPERIMENTAL_FEATURES and _get_experimental_sub("s3"))


def experimental_loading_screen_enabled() -> bool:
    """True if master experimental is on and loading_screen feature is enabled."""
    return bool(EXPERIMENTAL_FEATURES and _get_experimental_sub("loading_screen"))


def experimental_news_enabled() -> bool:
    """True if master experimental is on and news feature is enabled."""
    return bool(EXPERIMENTAL_FEATURES and _get_experimental_sub("news"))


def get_experimental_flags() -> dict:
    """Return dict of per-feature flags for /me and settings. Keys: mfa, s3, loading_screen, news."""
    return {
        "mfa": experimental_mfa_enabled(),
        "s3": experimental_s3_enabled(),
        "loading_screen": experimental_loading_screen_enabled(),
        "news": experimental_news_enabled(),
    }


# Loading screen fail-safe timeout (seconds). Env: MSS_LOGIN_LOADING_TIMEOUT_SECONDS (default 15).
def _get_loading_timeout_seconds() -> int:
    env_val = os.environ.get("MSS_LOGIN_LOADING_TIMEOUT_SECONDS", "").strip()
    if env_val.isdigit():
        return max(1, min(300, int(env_val)))
    cfg = _load_config(CONFIG_FILE_PATH)
    screen = cfg.get("loading_screen")
    if isinstance(screen, dict) and "timeout_seconds" in screen:
        val = screen["timeout_seconds"]
        if isinstance(val, int) and 1 <= val <= 300:
            return val
    return 15


LOADING_TIMEOUT_SECONDS = _get_loading_timeout_seconds()


def reload_experimental_features() -> bool:
    """Re-read config and refresh EXPERIMENTAL_FEATURES."""
    global EXPERIMENTAL_FEATURES
    EXPERIMENTAL_FEATURES = _get_experimental_features()
    return EXPERIMENTAL_FEATURES


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


# MFA disabled: when True, skip all MFA (setup, verify, role requirements)
def _get_mfa_disabled():
    env_val = str(os.environ.get("MFA_DISABLED", "")).strip().lower()
    if env_val in ("1", "true", "yes"):
        return True
    if env_val in ("0", "false", "no"):
        return False
    cfg = _load_config(CONFIG_FILE_PATH)
    return bool(cfg.get("mfa_disabled", False))


MFA_DISABLED = _get_mfa_disabled()


def reload_mfa_disabled() -> bool:
    """Re-read config and refresh MFA_DISABLED."""
    global MFA_DISABLED
    MFA_DISABLED = _get_mfa_disabled()
    return MFA_DISABLED


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
    """Allowed client IPs for recovery mode. Default 127.0.0.1, ::1. Override via RECOVERY_MODE_HOST or RECOVERY_MODE_HOSTS (comma-separated)."""
    raw = (
        os.environ.get("RECOVERY_MODE_HOST")
        or os.environ.get("RECOVERY_MODE_HOSTS")
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


# ---------------------------------------------------------------------------
# S3-compatible cloud storage (experimental)
# Credentials are resolved from env vars only -- never stored in config.json.
# ---------------------------------------------------------------------------
_s3_cfg = config_data.get("s3_storage") or {}
_s3_ak_env = _s3_cfg.get("access_key_id_env", "S3_ACCESS_KEY_ID")
_s3_sk_env = _s3_cfg.get("secret_access_key_env", "S3_SECRET_ACCESS_KEY")

S3_STORAGE_CONFIG: dict = {
    "enabled": bool(_s3_cfg.get("enabled", False)),
    "endpoint_url": (_s3_cfg.get("endpoint_url") or "").strip(),
    "bucket_name": (_s3_cfg.get("bucket_name") or "").strip(),
    "region": (_s3_cfg.get("region") or "").strip(),
    "prefix": (_s3_cfg.get("prefix") or "comfyui/").strip(),
    "access_key_id": (os.getenv(_s3_ak_env) or "").strip(),
    "secret_access_key": (os.getenv(_s3_sk_env) or "").strip(),
}

# S3 mount sub-config (rclone FUSE mount / sync for model folders)
_s3_mount_cfg = _s3_cfg.get("mount") or {}
_s3_mount_local = (_s3_mount_cfg.get("local_mount_path") or "").strip()
S3_MOUNT_CONFIG: dict = {
    "enabled": bool(_s3_mount_cfg.get("enabled", False)),
    "local_mount_path": _resolve_data_path(_s3_mount_local) if _s3_mount_local else "",
    "rclone_path": (_s3_mount_cfg.get("rclone_path") or "rclone").strip(),
    "mode": (_s3_mount_cfg.get("mode") or "auto").strip().lower(),
    "sync_interval_seconds": int(_s3_mount_cfg.get("sync_interval_seconds") or 300),
    "vfs_cache_mode": (_s3_mount_cfg.get("vfs_cache_mode") or "full").strip(),
    "vfs_cache_max_size": (_s3_mount_cfg.get("vfs_cache_max_size") or "10G").strip(),
    "model_folders": _s3_mount_cfg.get("model_folders")
    or [
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
    ],
    "mount_output": bool(_s3_mount_cfg.get("mount_output", False)),
    "mount_input": bool(_s3_mount_cfg.get("mount_input", False)),
    "read_only": _s3_mount_cfg.get("read_only", True),
}

# S3 workflow sync sub-config (bidirectional per-user workflow sync)
_s3_wf_cfg = _s3_cfg.get("workflow_sync") or {}
S3_WORKFLOW_SYNC_CONFIG: dict = {
    "enabled": bool(_s3_wf_cfg.get("enabled", False)),
    "sync_interval_seconds": int(_s3_wf_cfg.get("sync_interval_seconds") or 60),
    "conflict_strategy": (_s3_wf_cfg.get("conflict_strategy") or "newer_wins")
    .strip()
    .lower(),
    "sync_on_save": _s3_wf_cfg.get("sync_on_save", True),
    "sync_on_delete": _s3_wf_cfg.get("sync_on_delete", True),
    "max_workflow_size_mb": int(_s3_wf_cfg.get("max_workflow_size_mb") or 50),
}


def reload_s3_storage_config() -> dict:
    """Re-read config and refresh S3_STORAGE_CONFIG, S3_MOUNT_CONFIG, and S3_WORKFLOW_SYNC_CONFIG."""
    global S3_STORAGE_CONFIG, S3_MOUNT_CONFIG, S3_WORKFLOW_SYNC_CONFIG
    cfg = _load_config(CONFIG_FILE_PATH)
    s3 = cfg.get("s3_storage") or {}
    ak_env = s3.get("access_key_id_env", "S3_ACCESS_KEY_ID")
    sk_env = s3.get("secret_access_key_env", "S3_SECRET_ACCESS_KEY")
    S3_STORAGE_CONFIG = {
        "enabled": bool(s3.get("enabled", False)),
        "endpoint_url": (s3.get("endpoint_url") or "").strip(),
        "bucket_name": (s3.get("bucket_name") or "").strip(),
        "region": (s3.get("region") or "").strip(),
        "prefix": (s3.get("prefix") or "comfyui/").strip(),
        "access_key_id": (os.getenv(ak_env) or "").strip(),
        "secret_access_key": (os.getenv(sk_env) or "").strip(),
    }

    mc = s3.get("mount") or {}
    mc_local = (mc.get("local_mount_path") or "").strip()
    S3_MOUNT_CONFIG = {
        "enabled": bool(mc.get("enabled", False)),
        "local_mount_path": _resolve_data_path(mc_local) if mc_local else "",
        "rclone_path": (mc.get("rclone_path") or "rclone").strip(),
        "mode": (mc.get("mode") or "auto").strip().lower(),
        "sync_interval_seconds": int(mc.get("sync_interval_seconds") or 300),
        "vfs_cache_mode": (mc.get("vfs_cache_mode") or "full").strip(),
        "vfs_cache_max_size": (mc.get("vfs_cache_max_size") or "10G").strip(),
        "model_folders": mc.get("model_folders")
        or [
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
        ],
        "mount_output": bool(mc.get("mount_output", False)),
        "mount_input": bool(mc.get("mount_input", False)),
        "read_only": mc.get("read_only", True),
    }

    wc = s3.get("workflow_sync") or {}
    S3_WORKFLOW_SYNC_CONFIG = {
        "enabled": bool(wc.get("enabled", False)),
        "sync_interval_seconds": int(wc.get("sync_interval_seconds") or 60),
        "conflict_strategy": (wc.get("conflict_strategy") or "newer_wins")
        .strip()
        .lower(),
        "sync_on_save": wc.get("sync_on_save", True),
        "sync_on_delete": wc.get("sync_on_delete", True),
        "max_workflow_size_mb": int(wc.get("max_workflow_size_mb") or 50),
    }

    return S3_STORAGE_CONFIG
