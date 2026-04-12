# --- START OF FILE __init__.py ---
# Auto-install dependencies before any package imports
from .globals import routes
from server import PromptServer  # pyright: ignore[reportMissingImports]
import server  # pyright: ignore[reportMissingImports]
import asyncio
from .utils.updater import (
    get_cached_status,
    perform_recovery_update,
    run_update_check,
    update_check_loop,
)
from .utils.json_utils import load_json_file
from .utils.model_cache import get_model_cache
from .utils.prompt_model_validator import validate_prompt_models
from .utils.shared_items_store import get_shared_items_store
from .utils.path_safety import is_safe_filename, resolve_path_under
from .utils.csp import create_csp_middleware
from .utils.model_filter_middleware import create_model_filter_middleware
from .utils.model_download_redirect import (
    initialize_redirect_pattern_cache,
    is_civicomfy_present,
    rewrite_download_payload_for_user,
    should_try_model_download_redirect,
)
from .utils.model_isolation import isolation_models_base
from .utils.model_visibility_policy import (
    allowed_set_from_grants,
    get_effective_model_grants_for_user,
    user_can_view_all_models,
)
from .utils.remote_api_guard import create_remote_api_guard_middleware
from .utils.api_browser_redirect import create_api_browser_redirect_middleware
from .utils.sfw_intercept.node_interceptor import install_node_interceptor
from .utils.sfw_intercept.nsfw_guard import (
    should_block_image_for_current_user,
    set_latest_prompt_user,
)
from .utils.sfw_intercept.reactor_sfw_intercept import _load_reactor_module
from .routes import (
    static,
    auth,
    admin,
    user,
    workflow_routes,
    me,
    mfa,
    recovery,
    debug,
    model_download,
    news,
)
from .utils.bootstrap import ensure_groups_config
from .utils import watcher
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
    users_db,
)
from .constants import (
    FORCE_HTTPS,
    CLOUDFLARE_PROXY,
    CLOUDFLARED_LOCAL_BYPASS,
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
    CURRENT_DIR,
    EXPERIMENTAL_FAILSAFE_ENABLED,
    EXPERIMENTAL_FAILSAFE_ESCALATE,
    apply_experimental_safety_reset,
    clear_host_base_url_cache,
    experimental_model_isolation_enabled,
)
from .nodes import NODE_CLASS_MAPPINGS
import folder_paths  # pyright: ignore[reportMissingImports]
from aiohttp import web
import json
import os
import sys
import importlib.util
from datetime import datetime, timezone
from .utils.ntfy_notifier import notify_experimental_recovery
from .utils.quarantine_store import quarantine_cleanup_loop
from .utils.trash_store import trash_cleanup_loop, trash_deleted_history_images

_root = os.path.dirname(os.path.abspath(__file__))
_install_deps_path = os.path.join(_root, "utils", "install_deps.py")
if os.path.isfile(_install_deps_path):
    _spec = importlib.util.spec_from_file_location("install_deps", _install_deps_path)
    if _spec and _spec.loader:
        _mod = importlib.util.module_from_spec(_spec)
        _spec.loader.exec_module(_mod)
        _mod.install_dependencies()


if EXPERIMENTAL_FEATURES:
    from .routes import s3 as _s3_routes  # noqa: F401


WEB_DIRECTORY = "web"

# Export the public API for other extensions
try:
    from . import api

    __all__ = ["NODE_CLASS_MAPPINGS", "WEB_DIRECTORY", "api"]
except ImportError:
    __all__ = ["NODE_CLASS_MAPPINGS", "WEB_DIRECTORY"]

ensure_groups_config()
initialize_redirect_pattern_cache(logger=logger)

# Verify config file integrity (SHA-256) on startup; log warnings for mismatches
try:
    from .utils.config_integrity import verify_local_hashes

    _integrity_mismatches = verify_local_hashes(CURRENT_DIR, DATA_DIR)
    if _integrity_mismatches:
        for _m in _integrity_mismatches:
            logger.warning(
                "[mss-login] Config integrity mismatch: %s (expected %s, got %s)",
                _m["file"],
                _m["expected"][:12] + "...",
                _m["actual"][:12] + "...",
            )
except Exception:
    pass

# If HOST_BASE_URL is set, persist to app_settings so DB and env stay in sync
_host_env = (os.getenv("HOST_BASE_URL") or "").strip().rstrip("/")
if _host_env and _host_env.startswith(("http://", "https://")):
    try:
        from .utils.app_settings_store import get_app_settings_store

        get_app_settings_store(USERS_DB_CONFIG).set("host_base_url", _host_env)
        clear_host_base_url_cache()
    except Exception:
        pass

# Schedule recurring background update check (notify or auto according to config)
_update_task = asyncio.ensure_future(update_check_loop(app, logger, CONFIG_FILE_PATH))
_quarantine_cleanup_task = asyncio.ensure_future(
    quarantine_cleanup_loop(app, logger, CONFIG_FILE_PATH)
)
_trash_cleanup_task = asyncio.ensure_future(trash_cleanup_loop(app, logger, CONFIG_FILE_PATH))


async def _cancel_update_task(app_ref) -> None:
    """Cancel the recurring update check on shutdown."""
    _update_task.cancel()
    _quarantine_cleanup_task.cancel()
    _trash_cleanup_task.cancel()
    try:
        await _update_task
    except (asyncio.CancelledError, Exception):
        pass
    try:
        await _quarantine_cleanup_task
    except (asyncio.CancelledError, Exception):
        pass
    try:
        await _trash_cleanup_task
    except (asyncio.CancelledError, Exception):
        pass


app.on_cleanup.append(_cancel_update_task)


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

    # --- Model download destination rewrite for Civicomfy / core model routes ---
    if (
        experimental_model_isolation_enabled()
        and method in ("POST", "PUT", "PATCH")
        and should_try_model_download_redirect(path)
    ):
        current_user_id = access_control.get_current_user_id()
        if current_user_id and (
            is_civicomfy_present() or "/manager" in path.lower() or "model" in path.lower()
        ):
            original_body = await request.read()
            rewritten_body = original_body
            content_type = (request.headers.get("Content-Type") or "").lower()
            if "application/json" in content_type and original_body:
                try:
                    payload = json.loads(original_body)
                    rewritten_payload, changed = rewrite_download_payload_for_user(
                        payload, current_user_id
                    )
                    if changed:
                        rewritten_body = json.dumps(rewritten_payload).encode("utf-8")
                        logger.info(
                            "[mss-login] Model isolation redirected model download path for user=%s path=%s target_base=%s",
                            current_user_id,
                            path,
                            isolation_models_base(),
                        )
                except (json.JSONDecodeError, TypeError):
                    pass

            async def _read_redirect():
                return rewritten_body

            async def _json_redirect():
                return json.loads(rewritten_body) if rewritten_body else {}

            request.read = _read_redirect
            request.json = _json_redirect

    # --- Prompt model validation (POST/PUT /prompt and /api/prompt) ---
    if path in ("/prompt", "/api/prompt") and method in ("POST", "PUT"):
        body_bytes = await request.read()
        prompt_to_validate = None
        try:
            data = json.loads(body_bytes) if body_bytes else {}
            # ComfyUI accepts either the graph directly or { "prompt": graph, "client_id": ... }
            if isinstance(data, dict) and "prompt" in data and isinstance(data["prompt"], dict):
                prompt_to_validate = data["prompt"]
            elif isinstance(data, dict) and data:
                prompt_to_validate = data
        except (json.JSONDecodeError, TypeError):
            pass
        if prompt_to_validate is not None:
            role, perms, username_for_perm = access_control._get_user_role_and_permissions(request)
            can_view_all = user_can_view_all_models(role, perms)
            if not can_view_all:
                grants = get_effective_model_grants_for_user(
                    role=role,
                    perms=perms,
                    username=username_for_perm,
                    users_db=access_control.users_db,
                    shared_items_store_getter=get_shared_items_store,
                    users_db_config=USERS_DB_CONFIG,
                )
                allowed_set = allowed_set_from_grants(grants)
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

    # --- Move deleted history images to per-user trash bin before core delete ---
    if path in ("/history", "/api/history") and method in ("POST", "DELETE"):
        body_bytes = await request.read()
        payload = {}
        try:
            payload = json.loads(body_bytes) if body_bytes else {}
        except (json.JSONDecodeError, TypeError):
            payload = {}

        delete_ids = []
        clear_all = False
        if isinstance(payload, dict):
            clear_all = bool(payload.get("clear"))
            raw_delete = payload.get("delete")
            if isinstance(raw_delete, list):
                delete_ids = [str(x) for x in raw_delete if str(x).strip()]

        if method == "DELETE":
            tail = path.rsplit("/", 1)[-1]
            if tail and tail not in ("history", "api"):
                delete_ids.append(tail)
        if delete_ids or clear_all:
            try:
                username_for_delete = request.get("user") or workflow_routes.get_current_user(
                    request
                )
                owner_username = users_db.get_owner_username()
                is_owner_delete = bool(
                    username_for_delete
                    and owner_username
                    and str(username_for_delete) == str(owner_username)
                )

                moved = 0
                skipped = 0
                forbidden = 0
                missing = 0

                if clear_all:
                    visible = access_control.user_queue_get_history()
                    for prompt_id, entry in (visible or {}).items():
                        result = trash_deleted_history_images(
                            history_entry=entry,
                            request_username=str(username_for_delete or "guest"),
                            is_owner=is_owner_delete,
                            prompt_id=str(prompt_id),
                        )
                        moved += int(result.get("moved", 0) or 0)
                        skipped += int(result.get("skipped", 0) or 0)
                        forbidden += int(result.get("forbidden", 0) or 0)
                        missing += int(result.get("missing", 0) or 0)
                else:
                    for prompt_id in delete_ids:
                        visible = access_control.user_queue_get_history(prompt_id=prompt_id)
                        entry = visible.get(prompt_id) if isinstance(visible, dict) else None
                        result = trash_deleted_history_images(
                            history_entry=entry,
                            request_username=str(username_for_delete or "guest"),
                            is_owner=is_owner_delete,
                            prompt_id=str(prompt_id),
                        )
                        moved += int(result.get("moved", 0) or 0)
                        skipped += int(result.get("skipped", 0) or 0)
                        forbidden += int(result.get("forbidden", 0) or 0)
                        missing += int(result.get("missing", 0) or 0)

                if moved or skipped or forbidden or missing:
                    logger.info(
                        "[mss-login] Trash pre-delete: user=%s moved=%s skipped=%s forbidden=%s missing=%s",
                        username_for_delete,
                        moved,
                        skipped,
                        forbidden,
                        missing,
                    )
            except Exception as trash_exc:
                logger.warning("[mss-login] Trash pre-delete hook error: %s", trash_exc)

        async def _read_history():
            return body_bytes

        async def _json_history():
            return json.loads(body_bytes) if body_bytes else {}

        request.read = _read_history
        request.json = _json_history

    # --- Case A: /view ---
    if path == "/view" and method == "GET":
        q = request.rel_url.query
        filename = q.get("filename") or q.get("file") or q.get("name")
        subfolder = q.get("subfolder")
        img_type = q.get("type", "output")
        rel_path = workflow_routes.build_safe_view_relative_path(filename, subfolder)

        if rel_path and (img_type == "output" or img_type == "temp"):
            if img_type == "temp":
                target_dir = folder_paths.get_temp_directory()
            else:
                target_dir = folder_paths.get_output_directory()
            img_path = resolve_path_under(target_dir, rel_path)

            # Admin/owner fallback: if the file isn't in the current
            # user's directory, search the shared output/temp base.
            if not img_path or not os.path.isfile(img_path):
                request_user = request.get("user") or workflow_routes.get_current_user(request)
                owner_username = users_db.get_owner_username()
                is_owner = bool(request_user and owner_username and request_user == owner_username)
                if is_owner:
                    from .utils.data_dir import get_data_subdir

                    base = get_data_subdir("temp" if img_type == "temp" else "output")
                    img_path = resolve_path_under(base, rel_path)

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
from .utils.force_https import create_security_headers_middleware

app.middlewares.append(create_security_headers_middleware(cloudflare_proxy=CLOUDFLARE_PROXY))

if FORCE_HTTPS:
    from .utils.force_https import create_https_middleware

    app.middlewares.append(create_https_middleware(MATCH_HEADERS))

app.middlewares.append(ip_filter.create_ip_filter_middleware())
app.middlewares.append(sanitizer.create_sanitizer_middleware())
app.middlewares.append(create_api_browser_redirect_middleware())
app.middlewares.append(timeout.create_time_out_middleware(limited=("/login", "/register", "/mfa")))


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
        cloudflare_proxy=CLOUDFLARE_PROXY,
        cloudflared_local_bypass=CLOUDFLARED_LOCAL_BYPASS,
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
def _handle_experimental_critical_failure(reason: str) -> None:
    """Trip failsafe and optionally escalate recovery on repeated failures."""

    def _notify_recovery(action: str, failure_count: int, details: str = "") -> None:
        try:
            notify_experimental_recovery(
                reason=reason,
                recovery_action=action,
                failure_count=failure_count,
                occurred_at=datetime.now(timezone.utc).isoformat(),
                details=details,
            )
        except Exception as _ntfy_exc:
            print(f"[MSS-Login] Recovery ntfy notify error: {_ntfy_exc}")

    if not EXPERIMENTAL_FAILSAFE_ENABLED:
        print(f"[MSS-Login] Experimental critical failure (failsafe disabled): {reason}")
        return
    try:
        state = apply_experimental_safety_reset(reason, recovery_action="config_reset")
        print(
            "[MSS-Login] Experimental failsafe reset applied. "
            f"Reason='{reason}', failure_count={state.get('failure_count', 0)}"
        )
        failure_count = int(state.get("failure_count", 0) or 0)
        _notify_recovery("config_reset", failure_count)
        if EXPERIMENTAL_FAILSAFE_ESCALATE and failure_count >= 2:
            ok, msg = perform_recovery_update(
                repo_root=CURRENT_DIR,
                data_dir=DATA_DIR,
                logger=logger,
                branch="development",
            )
            action = "recovery_update" if ok else "recovery_update_failed"
            apply_experimental_safety_reset(reason, recovery_action=action)
            _notify_recovery(action, failure_count, details=msg)
            print(f"[MSS-Login] Experimental failsafe escalation result: {msg}")
    except Exception as _failsafe_exc:
        print(f"[MSS-Login] Experimental failsafe handler error: {_failsafe_exc}")


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
                print(f"[MSS-Login] S3 mount active. Registered folders: {', '.join(_registered)}")
            if _s3_runtime.workflow_status().get("running"):
                print("[MSS-Login] S3 workflow sync active.")
        elif _s3_runtime.status().get("enabled"):
            print(
                "[MSS-Login] S3 runtime started in degraded mode: "
                f"{_s3_runtime.status().get('last_error')}"
            )
            _handle_experimental_critical_failure(
                f"s3_runtime_degraded:{_s3_runtime.status().get('last_error')}"
            )
    except Exception as _s3_exc:
        print(f"[MSS-Login] S3 runtime init error: {_s3_exc}")
        _handle_experimental_critical_failure(f"s3_runtime_init_error:{_s3_exc}")


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
