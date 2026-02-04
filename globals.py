# --- START OF FILE globals.py ---
from server import PromptServer
from .constants import *

# Import Utils
from .utils.access_control import AccessControl
from .utils.users_db import UsersDB
from .utils.jwt_auth import JWTAuth
from .utils.ip_filter import IPFilter
from .utils.timeout import Timeout
from .utils.logger import Logger
from .utils.sanitizer import Sanitizer

import contextvars

current_username_var = contextvars.ContextVar("usgromana_current_user", default=None)

instance = PromptServer.instance
app = instance.app
routes = instance.routes

# 1. Logger & DB (credentials in SQLite/PostgreSQL only; no plain-text JSON)
logger = Logger(LOG_FILE, LOG_LEVELS)
users_db = UsersDB(USERS_DB_CONFIG, SECRET_KEY, LEGACY_USERS_JSON_PATH)

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
