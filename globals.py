"""
Global variables for the MSS-Login server.
"""

import contextvars

# --- START OF FILE globals.py ---
import ipaddress
import os

from server import PromptServer  # pyright: ignore[reportMissingImports]

from .constants import (
    API_TOKEN_STORE_CONFIG,
    BLACKLIST_AFTER_ATTEMPTS,
    BLACKLIST_EXPIRY_HOURS,
    BLACKLIST_FILE,
    CONFIG_FILE_PATH,
    EPHEMERAL_SECRET_KEY_PATH,
    EXPERIMENTAL_FEATURES,
    GROUPS_CONFIG_FILE,
    LEGACY_USERS_JSON_PATH,
    LOG_FILE,
    LOG_LEVELS,
    LOG_ROTATION_ARCHIVE_DIR,
    LOG_ROTATION_INTERVAL_HOURS,
    LOG_ROTATION_MAX_BYTES,
    SECRET_KEY,
    SECURITY_JSON_PATH,
    TOKEN_ALGORITHM,
    TOKEN_EXPIRE_MINUTES,
    USERS_DB_CONFIG,
    WHITELIST_FILE,
    _load_config,
    _load_ephemeral_key,
)

# Import Utils
from .utils.access_control import AccessControl
from .utils.data_dir import MIGRATION_PERFORMED
from .utils.ip_filter import IPFilter
from .utils.jwt_auth import JWTAuth
from .utils.lockout_store import get_lockout_store
from .utils.logger import Logger
from .utils.sanitizer import Sanitizer
from .utils.timeout import Timeout
from .utils.users_db import UsersDB, migrate_totp_to_new_key

current_username_var = contextvars.ContextVar("mss_login_current_user", default=None)

instance = PromptServer.instance
app = instance.app
routes = instance.routes

# 1. Logger & DB (credentials in SQLite/PostgreSQL only; no plain-text JSON)
logger = Logger(
    LOG_FILE,
    LOG_LEVELS,
    rotation_max_bytes=LOG_ROTATION_MAX_BYTES,
    rotation_interval_hours=LOG_ROTATION_INTERVAL_HOURS,
    rotation_archive_dir=LOG_ROTATION_ARCHIVE_DIR,
)

# Log if one-time migration from repo to external data dir was performed (done during constants load)

if MIGRATION_PERFORMED:
    logger.info(
        "[mss_login] Migrated repo-local config and data to external data directory. "
        "Future updates (git pull / ComfyUI Manager) will not overwrite your data."
    )

# If SECRET_KEY is now set from env and ephemeral file exists, migrate TOTP to new key then remove file
_old_key = _load_ephemeral_key()
if _old_key and _old_key != SECRET_KEY:
    if migrate_totp_to_new_key(USERS_DB_CONFIG, _old_key, SECRET_KEY):
        try:
            if os.path.isfile(EPHEMERAL_SECRET_KEY_PATH):
                os.remove(EPHEMERAL_SECRET_KEY_PATH)
        except Exception:
            pass
        logger.info(
            "[mss_login] Migrated TOTP secrets to new SECRET_KEY; ephemeral key file removed."
        )

users_db = UsersDB(USERS_DB_CONFIG, SECRET_KEY, LEGACY_USERS_JSON_PATH)

# One-time migration: copy api_tokens from separate DB into unified DB (when encryption off)
if USERS_DB_CONFIG.get("backend") == "sqlite" and not USERS_DB_CONFIG.get("encryption_level"):
    _cfg = _load_config(CONFIG_FILE_PATH)
    _old_token_path = (_cfg.get("api_token_store") or {}).get("sqlite_path")
    if _old_token_path and not os.path.isabs(_old_token_path):
        _old_token_path = os.path.join(os.path.dirname(CONFIG_FILE_PATH), _old_token_path)
    from .utils.migrate_api_tokens_to_unified import migrate_api_tokens_if_needed

    if migrate_api_tokens_if_needed(
        USERS_DB_CONFIG.get("sqlite_path", ""),
        _old_token_path,
        CONFIG_FILE_PATH,
    ):
        logger.info("[mss_login] Migrated API tokens from separate DB into unified DB.")

# 2. Access Control (Depends on DB + Server + Config Path; API token store for Bearer resolution)
access_control = AccessControl(
    users_db=users_db,
    server=instance,
    groups_config_file=GROUPS_CONFIG_FILE,
    api_token_store_config=API_TOKEN_STORE_CONFIG,
)

# 3. Auth (Depends on DB + Access Control; API token store for long-lived Bearer tokens)
jwt_auth = JWTAuth(
    users_db=users_db,
    access_control=access_control,
    logger=logger,
    secret_key=SECRET_KEY,
    expire_minutes=TOKEN_EXPIRE_MINUTES,
    algorithm=TOKEN_ALGORITHM,
    api_token_store_config=API_TOKEN_STORE_CONFIG,
)

# 4. Network Security

_lockout_store = get_lockout_store(USERS_DB_CONFIG)
ip_filter = IPFilter(
    _lockout_store,
    blacklist_expiry_hours=BLACKLIST_EXPIRY_HOURS,
    security_json_path=SECURITY_JSON_PATH,
)
# One-time migration: move whitelist/blacklist from files to DB if files exist

if os.path.isfile(WHITELIST_FILE) or os.path.isfile(BLACKLIST_FILE):
    from .utils.ip_filter import _parse_entry

    _migrated = False
    if os.path.isfile(WHITELIST_FILE):
        try:
            _wl_entries = []
            with open(WHITELIST_FILE, "r", encoding="utf-8") as f:
                for line in f:
                    entry = line.strip()
                    if entry and not entry.startswith("#") and _parse_entry(entry):
                        _wl_entries.append(entry)
            if _wl_entries:
                _lockout_store.set_whitelist(_wl_entries)
                _migrated = True
        except Exception:
            pass
    if os.path.isfile(BLACKLIST_FILE):
        try:
            _bl_entries = []
            with open(BLACKLIST_FILE, "r", encoding="utf-8") as f:
                for line in f:
                    ip = line.strip()
                    if ip and not ip.startswith("#"):
                        try:
                            ipaddress.ip_address(ip)
                            _bl_entries.append((ip, None))
                        except ValueError:
                            pass
            if _bl_entries:
                _lockout_store.set_blacklist(_bl_entries)
                _migrated = True
        except Exception:
            pass
    if _migrated:
        for _path in (WHITELIST_FILE, BLACKLIST_FILE):
            try:
                if os.path.isfile(_path):
                    os.rename(_path, _path + ".migrated")
            except Exception:
                pass
        logger.info("[mss_login] Migrated IP whitelist/blacklist from files to database.")
timeout = Timeout(ip_filter, BLACKLIST_AFTER_ATTEMPTS)
sanitizer = Sanitizer()
