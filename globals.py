# --- START OF FILE globals.py ---
import os
from server import PromptServer
from .constants import (
	SECRET_KEY,
	USERS_DB_CONFIG,
	CONFIG_FILE_PATH,
	LEGACY_USERS_JSON_PATH,
	LOG_FILE,
	LOG_LEVELS,
	GROUPS_CONFIG_FILE,
	API_TOKEN_STORE_CONFIG,
	TOKEN_EXPIRE_MINUTES,
	TOKEN_ALGORITHM,
	WHITELIST_FILE,
	BLACKLIST_FILE,
	BLACKLIST_AFTER_ATTEMPTS,
	EPHEMERAL_SECRET_KEY_PATH,
	_load_ephemeral_key,
	_load_config,
)

# Import Utils
from .utils.access_control import AccessControl
from .utils.users_db import UsersDB, migrate_totp_to_new_key
from .utils.jwt_auth import JWTAuth
from .utils.ip_filter import IPFilter
from .utils.timeout import Timeout
from .utils.logger import Logger
from .utils.sanitizer import Sanitizer

import contextvars

current_username_var = contextvars.ContextVar("mss_login_current_user", default=None)

instance = PromptServer.instance
app = instance.app
routes = instance.routes

# 1. Logger & DB (credentials in SQLite/PostgreSQL only; no plain-text JSON)
logger = Logger(LOG_FILE, LOG_LEVELS)

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
ip_filter = IPFilter(WHITELIST_FILE, BLACKLIST_FILE)
timeout = Timeout(ip_filter, BLACKLIST_AFTER_ATTEMPTS)
sanitizer = Sanitizer()
