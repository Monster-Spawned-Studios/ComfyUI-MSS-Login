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
)
from .utils import watcher
from .utils.bootstrap import ensure_groups_config
from .routes import static, auth, admin, user, workflow_routes, me, mfa, recovery, debug
from .utils.sfw_intercept.reactor_sfw_intercept import _load_reactor_module
from .utils.sfw_intercept.nsfw_guard import (
    should_block_image_for_current_user,
    set_latest_prompt_user,
)
from .utils.sfw_intercept.node_interceptor import install_node_interceptor
from .utils.remote_api_guard import create_remote_api_guard_middleware
from .utils.model_filter_middleware import create_model_filter_middleware
from .utils.shared_items_store import get_shared_items_store
from .utils.prompt_model_validator import validate_prompt_models

import server  # pyright: ignore[reportMissingImports]

WEB_DIRECTORY = "./web"

# Export the public API for other extensions
try:
    from . import api

    __all__ = ["NODE_CLASS_MAPPINGS", "WEB_DIRECTORY", "api"]
except ImportError:
    __all__ = ["NODE_CLASS_MAPPINGS", "WEB_DIRECTORY"]

ensure_groups_config()


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
                or role == "admin"
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
                valid, err_msg = validate_prompt_models(
                    allowed_set, allow_all=False, prompt=prompt_to_validate
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
            if img_type == "temp":
                target_dir = folder_paths.get_temp_directory()
            else:
                target_dir = folder_paths.get_output_directory()

            img_path = os.path.join(target_dir, filename)

            if os.path.isfile(img_path):
                if should_block_image_for_current_user(img_path):
                    return web.Response(status=403, text="NSFW Blocked")

    # --- Case B: /static_gallery ---
    if path.startswith("/static_gallery/") and method == "GET":
        rel = path[len("/static_gallery/") :].lstrip("/\\")
        out_dir = folder_paths.get_output_directory()
        img_path = os.path.join(out_dir, rel)
        if os.path.isfile(img_path) and should_block_image_for_current_user(img_path):
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
app.middlewares.append(
    jwt_auth.create_jwt_middleware(
        public=("/login", "/logout", "/register", "/generate_token", "/mfa"),
        public_prefixes=(
            "/mss-login",
            "/mss-login-gallery",
            "/mss_login/api/mfa",
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
watcher.register(app)

install_node_interceptor()

# Populate model cache on startup so GET /models can use it (best-effort)
try:
    from .utils.model_cache import get_model_cache

    cache = get_model_cache(USERS_DB_CONFIG)
    cache.refresh_from_folder_paths()
except Exception:
    pass  # Cache stays empty until admin uses "Refresh folders" in settings

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
