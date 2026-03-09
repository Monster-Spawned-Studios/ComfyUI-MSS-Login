# --- START OF FILE __init__.py ---
# Auto-install dependencies before any package imports
import json
import os
import sys
import importlib.util

_root = os.path.dirname(os.path.abspath(__file__))
_install_deps_path = os.path.join(_root, "utils", "install_deps.py")
if os.path.isfile(_install_deps_path):
    _spec = importlib.util.spec_from_file_location("install_deps", _install_deps_path)
    if _spec and _spec.loader:
        _mod = importlib.util.module_from_spec(_spec)
        _spec.loader.exec_module(_mod)
        _mod.install_dependencies()

from aiohttp import web
import folder_paths  # pyright: ignore[reportMissingImports]
from .nodes import *
from .constants import (
    FORCE_HTTPS,
    SEPERATE_USERS,
    MATCH_HEADERS,
    REQUIRE_AUTH_FOR_REMOTE_API,
    LOCAL_NETWORK_CIDRS,
    USERS_DB_CONFIG,
    CONFIG_FILE_PATH,
    EXPERIMENTAL_FEATURES,
    S3_STORAGE_CONFIG,
    S3_MOUNT_CONFIG,
    S3_WORKFLOW_SYNC_CONFIG,
    DATA_DIR,
    clear_host_base_url_cache,
)
from .globals import (
    app,
    ip_filter,
    sanitizer,
    timeout,
    jwt_auth,
    access_control,
    instance,
    current_username_var,
    logger,
)
from .utils import watcher
from .utils.bootstrap import ensure_groups_config
from .routes import static, auth, admin, user, workflow_routes, me, mfa, recovery, debug, model_download, news
if EXPERIMENTAL_FEATURES:
    from .routes import s3 as _s3_routes  # noqa: F401
from .utils.sfw_intercept.reactor_sfw_intercept import _load_reactor_module
from .utils.sfw_intercept.nsfw_guard import (
    should_block_image_for_current_user,
    set_latest_prompt_user,
)
from .utils.sfw_intercept.node_interceptor import install_node_interceptor
from .utils.remote_api_guard import create_remote_api_guard_middleware
from .utils.model_filter_middleware import create_model_filter_middleware
from .utils.csp import create_csp_middleware
from .utils.path_safety import is_safe_filename, resolve_path_under
from .utils.shared_items_store import get_shared_items_store
from .utils.prompt_model_validator import validate_prompt_models
from .utils.model_cache import get_model_cache
from .utils.json_utils import load_json_file
from .utils.updater import run_update_check, get_cached_status

import asyncio
import server  # pyright: ignore[reportMissingImports]
from server import PromptServer  # pyright: ignore[reportMissingImports]

WEB_DIRECTORY = "web"

# Export the public API for other extensions
try:
    from . import api

    __all__ = ["NODE_CLASS_MAPPINGS", "WEB_DIRECTORY", "api"]
except ImportError:
    __all__ = ["NODE_CLASS_MAPPINGS", "WEB_DIRECTORY"]

ensure_groups_config()

# If HOST_BASE_URL is set, persist to app_settings so DB and env stay in sync
_host_env = (os.getenv("HOST_BASE_URL") or "").strip().rstrip("/")
if _host_env and _host_env.startswith(("http://", "https://")):
    try:
        from .utils.app_settings_store import get_app_settings_store

        get_app_settings_store(USERS_DB_CONFIG).set("host_base_url", _host_env)
        clear_host_base_url_cache()
    except Exception:
        pass

# Schedule background update check (notify or auto according to config)
_config_for_updater = load_json_file(CONFIG_FILE_PATH, {})
asyncio.ensure_future(run_update_check(app, logger, _config_for_updater))


# --- WORKFLOW + GLOBAL SFW INTERCEPTION MIDDLEWARE ---
@web.middleware
async def workflow_interceptor_middleware(request, handler):
    path = request.path
    method = request.method

    # 1. Dispatcher
    response = await workflow_routes.middleware_dispatch(request)
    if isinstance(response, web.StreamResponse):
        return response

    # 2. User Resolution (JWT middleware sets request["user"] = username string for API token and session JWT)
    username = None
    try:
        username = request.get("user")
        if not username and hasattr(request, "user") and request.user:
            u = request.user
            username = u.get("username") if isinstance(u, dict) else u
        if not username:
            username = workflow_routes.get_current_user(request)
    except Exception:
        username = None

    # Store for *HTTP* context: fall back to 'guest' only for HTTP-only checks
    current_username_var.set(username or "guest")

    # --- USER CAPTURE FOR WORKER THREAD ---
    if "prompt" in path and method in ("POST", "PUT"):
        # Let nsfw_guard handle defaulting/guest logic.
        set_latest_prompt_user(username)
        print(f"[MSS-Login::Middleware] PROMPT CAPTURE path={path} user={username!r}")

    # --- Prompt model validation (POST/PUT /prompt and /api/prompt) ---
    if path in ("/prompt", "/api/prompt") and method in ("POST", "PUT"):
        body_bytes = await request.read()
        prompt_to_validate = None
        try:
            data = json.loads(body_bytes) if body_bytes else {}
            # ComfyUI accepts either the graph directly or { "prompt": graph, "client_id": ... }
            if (
                isinstance(data, dict)
                and "prompt" in data
                and isinstance(data["prompt"], dict)
            ):
                prompt_to_validate = data["prompt"]
            elif isinstance(data, dict) and data:
                prompt_to_validate = data
        except (json.JSONDecodeError, TypeError):
            pass
        if prompt_to_validate is not None:
            role, perms, username_for_perm = (
                access_control._get_user_role_and_permissions(request)
            )
            can_view_all = (
                perms.get("can_view_all_comfyui_items", False) is True
                or role in ("admin", "owner")
            )
            if not can_view_all:
                user_id, _ = (
                    access_control.users_db.get_user(username=username_for_perm)
                    if username_for_perm
                    else (None, {})
                )
                allowed_set = set()
                if user_id:
                    store = get_shared_items_store(USERS_DB_CONFIG)
                    allowed_set = store.get_all_for_user(user_id)
                known_models = set()
                try:
                    cache = get_model_cache(USERS_DB_CONFIG)
                    for folder in cache.list_folders():
                        for item in cache.list_items(folder):
                            known_models.add((folder, item))
                except Exception:
                    pass
                valid, err_msg = validate_prompt_models(
                    allowed_set,
                    allow_all=False,
                    prompt=prompt_to_validate,
                    known_models=known_models if known_models else None,
                )
                if not valid:
                    return web.json_response(
                        {
                            "error": err_msg or "Model not allowed",
                            "code": "MODEL_NOT_ALLOWED",
                        },
                        status=403,
                    )

        # Re-inject body so ComfyUI handler can read it
        async def _read():
            return body_bytes

        async def _json():
            return json.loads(body_bytes) if body_bytes else {}

        request.read = _read
        request.json = _json

    # --- Case A: /view ---
    if path == "/view" and method == "GET":
        q = request.rel_url.query
        filename = q.get("filename") or q.get("file") or q.get("name")
        img_type = q.get("type", "output")

        if filename and (img_type == "output" or img_type == "temp"):
            if not is_safe_filename(filename):
                filename = None
            if filename:
                if img_type == "temp":
                    target_dir = folder_paths.get_temp_directory()
                else:
                    target_dir = folder_paths.get_output_directory()
                img_path = resolve_path_under(target_dir, filename)
                if img_path and os.path.isfile(img_path):
                    if should_block_image_for_current_user(img_path):
                        return web.Response(status=403, text="NSFW Blocked")

    # --- Case B: /static_gallery ---
    if path.startswith("/static_gallery/") and method == "GET":
        rel = path[len("/static_gallery/") :].lstrip("/\\")
        if rel:
            out_dir = folder_paths.get_output_directory()
            img_path = resolve_path_under(out_dir, rel)
            if img_path and os.path.isfile(img_path):
                if should_block_image_for_current_user(img_path):
                    return web.Response(status=403, text="NSFW Blocked")

    return await handler(request)


# ---------------- Core middlewares ----------------
if FORCE_HTTPS:
    from .utils.force_https import create_https_middleware

    app.middlewares.append(create_https_middleware(MATCH_HEADERS))

app.middlewares.append(ip_filter.create_ip_filter_middleware())
app.middlewares.append(sanitizer.create_sanitizer_middleware())
app.middlewares.append(
    timeout.create_time_out_middleware(limited=("/login", "/register", "/mfa"))
)


# Require auth for remote API access (local network allowed without auth).
# token_validator: only treat request as authenticated when token is valid (not expired/revoked).
def _remote_guard_token_valid(request):
    token = jwt_auth.get_token_from_request(request)
    return bool(token and jwt_auth.is_token_valid(token))


app.middlewares.append(
    create_remote_api_guard_middleware(
        require_auth_for_remote_api=REQUIRE_AUTH_FOR_REMOTE_API,
        local_network_cidrs=LOCAL_NETWORK_CIDRS if LOCAL_NETWORK_CIDRS else None,
        token_validator=_remote_guard_token_valid,
    )
)

# IMPORTANT: run JWT auth BEFORE we try to read request.user in workflow_interceptor
# Headless JWT sessions use only WebSocket (/ws?token=...) and REST (Bearer/cookie/query);
# /ws, /history, /prompt, /queue, /view, /api/userdata are NOT in public, so they require a valid token.
app.middlewares.append(
    jwt_auth.create_jwt_middleware(
        public=("/login", "/logout", "/register", "/generate_token", "/mfa"),
        public_prefixes=(
            "/mss-login",
            "/mss-login-gallery",
            "/mss-login/api/mfa",
            "/assets",
            "/static",
        ),
    )
)

# Now that jwt_auth can populate request.user, we can safely
# resolve usernames inside workflow_interceptor_middleware.
app.middlewares.append(workflow_interceptor_middleware)

# Filter /models and /embeddings by can_view_all_comfyui_items and per-user shared items
app.middlewares.append(
    create_model_filter_middleware(
        access_control._get_user_role_and_permissions,
        get_shared_items_store,
        access_control.users_db,
        USERS_DB_CONFIG,
    )
)

if SEPERATE_USERS:
    app.middlewares.append(access_control.create_folder_access_control_middleware())
    access_control.patch_folder_paths()
    access_control.patch_prompt_queue()

app.middlewares.append(access_control.create_mss_login_middleware())
app.middlewares.append(create_csp_middleware())
watcher.register(app)

install_node_interceptor()

# Populate model cache on startup so GET /models can use it (best-effort)
try:
    from .utils.model_cache import get_model_cache

    cache = get_model_cache(USERS_DB_CONFIG)
    cache.refresh_from_folder_paths()
except Exception:
    pass  # Cache stays empty until admin uses "Refresh folders" in settings

# ---------------------------------------------------------------------------
# Consolidated S3 runtime (s3fs mount + workflow sync) -- experimental
# ---------------------------------------------------------------------------
_s3_runtime = None
if EXPERIMENTAL_FEATURES:
    try:
        from .utils.s3_mounter import init_s3_manager
        from .globals import users_db as _users_db_for_sync

        _s3_runtime = init_s3_manager(DATA_DIR, users_db=_users_db_for_sync)
        if _s3_runtime.mount_or_sync():
            _registered = _s3_runtime.register_folder_paths()
            if _registered:
                try:
                    cache = get_model_cache(USERS_DB_CONFIG)
                    cache.refresh_from_folder_paths()
                except Exception:
                    pass
                print(
                    f"[MSS-Login] S3 mount active. "
                    f"Registered folders: {', '.join(_registered)}"
                )
            if _s3_runtime.workflow_status().get("running"):
                print("[MSS-Login] S3 workflow sync active.")
        elif _s3_runtime.status().get("enabled"):
            print(
                "[MSS-Login] S3 runtime started in degraded mode: "
                f"{_s3_runtime.status().get('last_error')}"
            )
    except Exception as _s3_exc:
        print(f"[MSS-Login] S3 runtime init error: {_s3_exc}")


async def _shutdown_s3(app_ref) -> None:
    """Clean up the consolidated S3 runtime on app shutdown."""
    if _s3_runtime is not None:
        try:
            _s3_runtime.stop()
        except Exception:
            pass


app.on_shutdown.append(_shutdown_s3)

# Ensure routes are added to the app
# In ComfyUI, instance.routes should be automatically added by PromptServer,
# but we'll explicitly add them to ensure they're registered
from .globals import routes

try:
    # Check if routes are already in the app
    routes_in_app = any(
        r._resource is routes for r in app.router.routes() if hasattr(r, "_resource")
    )
    if not routes_in_app:
        app.add_routes(routes)
except Exception:
    # Try to add anyway - might work even if check fails
    try:
        app.add_routes(routes)
    except Exception:
        pass  # ComfyUI may handle route registration automatically

print("------------------------------------------")
print("[MSS-Login] Security System Initialized.")
print("[MSS-Login] Workflow Storage Interceptor Active.")
print("------------------------------------------")
# --- END OF FILE __init__.py ---
