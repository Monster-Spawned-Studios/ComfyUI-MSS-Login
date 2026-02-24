# --- START OF FILE routes/s3.py ---
"""
S3-compatible cloud storage API endpoints (experimental).

All endpoints are gated behind the EXPERIMENTAL_FEATURES flag and require
admin-level authentication. They provide operations for uploading, downloading,
listing, and deleting objects in an S3-compatible bucket (Amazon S3, Backblaze B2,
MinIO, etc.).

Also provides mount management and per-user workflow sync endpoints.
"""

from aiohttp import web

from .. import constants as constants_module
from ..globals import routes, users_db
from ..utils.s3_storage import get_s3_client


def _require_experimental_and_admin(request: web.Request) -> str | None:
    """Validate that experimental features are enabled and the caller is an admin.

    Returns the username on success, or None after sending an error response
    (caller should check and return early).
    """
    if not constants_module.EXPERIMENTAL_FEATURES:
        return None
    username = request.get("user")
    if not username:
        return None
    user_id, user_rec = users_db.get_user(username)
    if not user_rec:
        return None
    is_admin = (
        user_rec.get("admin")
        or user_rec.get("owner")
        and "admin" in [g.lower() for g in user_rec.get("groups", [])]
        or "owner" in [g.lower() for g in user_rec.get("groups", [])]
    )
    if not is_admin:
        return None
    return username


def _require_experimental_and_auth(request: web.Request) -> tuple[str | None, bool]:
    """Validate experimental features are enabled and the caller is authenticated.

    Returns (username, is_admin). username is None on failure.
    """
    if not constants_module.EXPERIMENTAL_FEATURES:
        return None, False
    username = request.get("user")
    if not username:
        return None, False
    user_id, user_rec = users_db.get_user(username)
    if not user_rec:
        return None, False
    is_admin = user_rec.get("admin") or "admin" in [
        g.lower() for g in user_rec.get("groups", [])
    ]
    return username, is_admin


def _error_json(msg: str, status: int = 400) -> web.Response:
    return web.json_response({"error": msg}, status=status)


@routes.get("/mss-login/api/s3/status")
async def s3_status(request: web.Request) -> web.Response:
    """Test whether S3 storage is configured and reachable."""
    if not constants_module.EXPERIMENTAL_FEATURES:
        return _error_json("Experimental features are not enabled.", 403)
    username = _require_experimental_and_admin(request)
    if not username:
        return _error_json("Admin authentication required.", 403)
    if not constants_module.S3_STORAGE_CONFIG.get("enabled"):
        return web.json_response(
            {
                "configured": False,
                "message": "S3 storage is not enabled in config.json.",
            }
        )
    try:
        client = get_s3_client(constants_module.S3_STORAGE_CONFIG)
        result = client.test_connection()
        return web.json_response({"configured": True, **result})
    except Exception as exc:
        return web.json_response({"configured": False, "error": str(exc)})


@routes.get("/mss-login/api/s3/list")
async def s3_list(request: web.Request) -> web.Response:
    """List objects in the S3 bucket. Query params: prefix (optional), max_keys (optional, default 200)."""
    if not constants_module.EXPERIMENTAL_FEATURES:
        return _error_json("Experimental features are not enabled.", 403)
    username = _require_experimental_and_admin(request)
    if not username:
        return _error_json("Admin authentication required.", 403)
    prefix = request.rel_url.query.get("prefix", "")
    try:
        max_keys = int(request.rel_url.query.get("max_keys", "200"))
    except (TypeError, ValueError):
        max_keys = 200
    max_keys = min(max(1, max_keys), 1000)
    try:
        client = get_s3_client(constants_module.S3_STORAGE_CONFIG)
        objects = client.list_objects(prefix=prefix, max_keys=max_keys)
        return web.json_response({"objects": objects, "count": len(objects)})
    except Exception as exc:
        return _error_json(str(exc), 500)


@routes.post("/mss-login/api/s3/upload")
async def s3_upload(request: web.Request) -> web.Response:
    """Upload a local file to S3. JSON body: { "local_path": "...", "s3_key": "..." }."""
    if not constants_module.EXPERIMENTAL_FEATURES:
        return _error_json("Experimental features are not enabled.", 403)
    username = _require_experimental_and_admin(request)
    if not username:
        return _error_json("Admin authentication required.", 403)
    try:
        body = await request.json()
    except Exception:
        return _error_json("Invalid JSON body.")
    local_path = (body.get("local_path") or "").strip()
    s3_key = (body.get("s3_key") or "").strip()
    if not local_path or not s3_key:
        return _error_json("local_path and s3_key are required.")
    try:
        client = get_s3_client(constants_module.S3_STORAGE_CONFIG)
        result = client.upload_file(local_path, s3_key)
        return web.json_response({"message": "Upload complete.", **result})
    except FileNotFoundError as exc:
        return _error_json(str(exc), 404)
    except Exception as exc:
        return _error_json(str(exc), 500)


@routes.post("/mss-login/api/s3/download")
async def s3_download(request: web.Request) -> web.Response:
    """Download an S3 object to a local path. JSON body: { "s3_key": "...", "local_path": "..." }."""
    if not constants_module.EXPERIMENTAL_FEATURES:
        return _error_json("Experimental features are not enabled.", 403)
    username = _require_experimental_and_admin(request)
    if not username:
        return _error_json("Admin authentication required.", 403)
    try:
        body = await request.json()
    except Exception:
        return _error_json("Invalid JSON body.")
    s3_key = (body.get("s3_key") or "").strip()
    local_path = (body.get("local_path") or "").strip()
    if not s3_key or not local_path:
        return _error_json("s3_key and local_path are required.")
    try:
        client = get_s3_client(constants_module.S3_STORAGE_CONFIG)
        saved_path = client.download_file(s3_key, local_path)
        return web.json_response(
            {"message": "Download complete.", "local_path": saved_path}
        )
    except Exception as exc:
        return _error_json(str(exc), 500)


@routes.delete("/mss-login/api/s3/delete")
async def s3_delete(request: web.Request) -> web.Response:
    """Delete an object from S3. JSON body: { "s3_key": "..." }."""
    if not constants_module.EXPERIMENTAL_FEATURES:
        return _error_json("Experimental features are not enabled.", 403)
    username = _require_experimental_and_admin(request)
    if not username:
        return _error_json("Admin authentication required.", 403)
    try:
        body = await request.json()
    except Exception:
        return _error_json("Invalid JSON body.")
    s3_key = (body.get("s3_key") or "").strip()
    if not s3_key:
        return _error_json("s3_key is required.")
    try:
        client = get_s3_client(constants_module.S3_STORAGE_CONFIG)
        client.delete_object(s3_key)
        return web.json_response({"message": "Object deleted."})
    except Exception as exc:
        return _error_json(str(exc), 500)


# ---------------------------------------------------------------------------
# S3 Mount Management Endpoints (admin only)
# ---------------------------------------------------------------------------


@routes.get("/mss-login/api/s3/mount/status")
async def s3_mount_status(request: web.Request) -> web.Response:
    """Return mount health, mode, last sync time, and platform info."""
    if not constants_module.EXPERIMENTAL_FEATURES:
        return _error_json("Experimental features are not enabled.", 403)
    username = _require_experimental_and_admin(request)
    if not username:
        return _error_json("Admin authentication required.", 403)
    try:
        from ..utils.s3_mount import get_mount_manager

        mgr = get_mount_manager()
        if mgr is None:
            return web.json_response(
                {"enabled": False, "message": "S3 mount is not initialized."}
            )
        return web.json_response({"enabled": True, **mgr.status()})
    except Exception as exc:
        return _error_json(str(exc), 500)


@routes.post("/mss-login/api/s3/mount/sync")
async def s3_mount_sync(request: web.Request) -> web.Response:
    """Trigger a manual rclone sync (sync mode only)."""
    if not constants_module.EXPERIMENTAL_FEATURES:
        return _error_json("Experimental features are not enabled.", 403)
    username = _require_experimental_and_admin(request)
    if not username:
        return _error_json("Admin authentication required.", 403)
    try:
        from ..utils.s3_mount import get_mount_manager

        mgr = get_mount_manager()
        if mgr is None:
            return _error_json("S3 mount is not initialized.", 400)
        ok = mgr.trigger_sync()
        return web.json_response({"synced": ok})
    except Exception as exc:
        return _error_json(str(exc), 500)


@routes.post("/mss-login/api/s3/mount/remount")
async def s3_mount_remount(request: web.Request) -> web.Response:
    """Unmount and remount the S3 storage."""
    if not constants_module.EXPERIMENTAL_FEATURES:
        return _error_json("Experimental features are not enabled.", 403)
    username = _require_experimental_and_admin(request)
    if not username:
        return _error_json("Admin authentication required.", 403)
    try:
        from ..utils.s3_mount import get_mount_manager

        mgr = get_mount_manager()
        if mgr is None:
            return _error_json("S3 mount is not initialized.", 400)
        mgr.unmount()
        ok = mgr.mount_or_sync()
        if ok:
            registered = mgr.register_folder_paths()
            return web.json_response(
                {"remounted": True, "registered_folders": registered}
            )
        return web.json_response({"remounted": False, "message": "Remount failed."})
    except Exception as exc:
        return _error_json(str(exc), 500)


# ---------------------------------------------------------------------------
# S3 Workflow Sync Endpoints
# ---------------------------------------------------------------------------


@routes.get("/mss-login/api/s3/workflows/status")
async def s3_workflow_status(request: web.Request) -> web.Response:
    """Return workflow sync health and last sync times (admin only)."""
    if not constants_module.EXPERIMENTAL_FEATURES:
        return _error_json("Experimental features are not enabled.", 403)
    username = _require_experimental_and_admin(request)
    if not username:
        return _error_json("Admin authentication required.", 403)
    try:
        from ..utils.s3_workflow_sync import get_workflow_sync

        sync = get_workflow_sync()
        if sync is None:
            return web.json_response(
                {"enabled": False, "message": "Workflow sync is not initialized."}
            )
        return web.json_response({"enabled": True, **sync.status()})
    except Exception as exc:
        return _error_json(str(exc), 500)


@routes.post("/mss-login/api/s3/workflows/sync")
async def s3_workflow_sync_user(request: web.Request) -> web.Response:
    """Trigger workflow sync for the current user (or a specific user if admin provides ?username=)."""
    if not constants_module.EXPERIMENTAL_FEATURES:
        return _error_json("Experimental features are not enabled.", 403)
    username, is_admin = _require_experimental_and_auth(request)
    if not username:
        return _error_json("Authentication required.", 403)

    target = request.rel_url.query.get("username", "").strip()
    if target and target != username and not is_admin:
        return _error_json("Admin privileges required to sync another user.", 403)
    sync_target = target or username

    try:
        from ..utils.s3_workflow_sync import get_workflow_sync

        sync = get_workflow_sync()
        if sync is None:
            return _error_json("Workflow sync is not initialized.", 400)
        stats = sync.sync_user(sync_target)
        return web.json_response({"user": sync_target, **stats})
    except Exception as exc:
        return _error_json(str(exc), 500)


@routes.post("/mss-login/api/s3/workflows/sync-all")
async def s3_workflow_sync_all(request: web.Request) -> web.Response:
    """Trigger workflow sync for all users (admin only)."""
    if not constants_module.EXPERIMENTAL_FEATURES:
        return _error_json("Experimental features are not enabled.", 403)
    username = _require_experimental_and_admin(request)
    if not username:
        return _error_json("Admin authentication required.", 403)
    try:
        from ..utils.s3_workflow_sync import get_workflow_sync

        sync = get_workflow_sync()
        if sync is None:
            return _error_json("Workflow sync is not initialized.", 400)
        results = sync.sync_all_users()
        return web.json_response({"results": results})
    except Exception as exc:
        return _error_json(str(exc), 500)
