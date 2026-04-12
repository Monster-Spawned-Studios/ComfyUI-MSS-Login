# --- START OF FILE routes/s3.py ---
"""S3-related API routes backed by the consolidated `utils.s3_mounter` runtime."""

import os

from aiohttp import web

from .. import constants as constants_module
from ..globals import routes, users_db
from ..utils.s3_mounter import get_s3_manager, get_s3_settings_payload, save_s3_settings


def _error_json(msg: str, status: int = 400) -> web.Response:
    return web.json_response({"error": msg}, status=status)


def _user_record_from_request(request: web.Request) -> tuple[str | None, dict | None]:
    username = request.get("user")
    if not username:
        return None, None
    _user_id, user_rec = users_db.get_user(username)
    return username, user_rec


def _is_admin_or_owner(user_rec: dict | None) -> bool:
    if not user_rec:
        return False
    groups = [g.lower() for g in user_rec.get("groups", [])]
    return bool(user_rec.get("admin")) or "admin" in groups or "owner" in groups


def _is_owner(user_rec: dict | None) -> bool:
    if not user_rec:
        return False
    groups = [g.lower() for g in user_rec.get("groups", [])]
    return "owner" in groups


def _require_experimental_and_admin(request: web.Request) -> str | None:
    if not constants_module.experimental_s3_enabled():
        return None
    username, user_rec = _user_record_from_request(request)
    if not username or not _is_admin_or_owner(user_rec):
        return None
    return username


def _require_experimental_and_owner(request: web.Request) -> str | None:
    if not constants_module.experimental_s3_enabled():
        return None
    username, user_rec = _user_record_from_request(request)
    if not username or not _is_owner(user_rec):
        return None
    return username


def _require_experimental_and_auth(request: web.Request) -> tuple[str | None, bool]:
    if not constants_module.experimental_s3_enabled():
        return None, False
    username, user_rec = _user_record_from_request(request)
    if not username or not user_rec:
        return None, False
    return username, _is_admin_or_owner(user_rec)


def _local_path_allowed(local_path: str) -> bool:
    if not local_path or ".." in local_path:
        return False
    try:
        resolved = os.path.abspath(local_path)
    except Exception:
        return False
    bases = [os.path.abspath(constants_module.DATA_DIR)]
    try:
        import folder_paths  # pyright: ignore[reportMissingImports]

        bases.append(os.path.abspath(getattr(folder_paths, "base_path", "")))
    except Exception:
        pass
    return any(base and (resolved == base or resolved.startswith(base + os.sep)) for base in bases)


def _manager_or_error() -> tuple[object | None, web.Response | None]:
    mgr = get_s3_manager()
    if mgr is None:
        return None, _error_json("S3 runtime is not initialized.", 400)
    return mgr, None


@routes.get("/mss-login/api/s3/config")
async def s3_get_config(request: web.Request) -> web.Response:
    if not constants_module.experimental_s3_enabled():
        return _error_json("Experimental features are not enabled.", 403)
    if not _require_experimental_and_owner(request):
        return _error_json("Owner authentication required.", 403)
    mgr = get_s3_manager()
    payload = get_s3_settings_payload()
    if mgr is not None:
        payload["status"] = mgr.status()
        payload["workflow_status"] = mgr.workflow_status()
    return web.json_response(payload)


@routes.put("/mss-login/api/s3/config")
async def s3_put_config(request: web.Request) -> web.Response:
    if not constants_module.experimental_s3_enabled():
        return _error_json("Experimental features are not enabled.", 403)
    if not _require_experimental_and_owner(request):
        return _error_json("Owner authentication required.", 403)
    try:
        body = await request.json()
    except Exception:
        return _error_json("Invalid JSON body.")
    try:
        payload = save_s3_settings(body)
        mgr = get_s3_manager()
        if mgr is not None:
            mgr.refresh_config()
            payload["status"] = mgr.status()
            payload["workflow_status"] = mgr.workflow_status()
        return web.json_response({"status": "ok", **payload})
    except Exception as exc:
        return _error_json(str(exc), 500)


@routes.get("/mss-login/api/s3/status")
async def s3_status(request: web.Request) -> web.Response:
    if not constants_module.experimental_s3_enabled():
        return _error_json("Experimental features are not enabled.", 403)
    if not _require_experimental_and_admin(request):
        return _error_json("Admin authentication required.", 403)
    mgr, error = _manager_or_error()
    if error:
        return error
    result = mgr.test_connection()
    return web.json_response({"configured": result.get("configured", False), **result})


@routes.get("/mss-login/api/s3/list")
async def s3_list(request: web.Request) -> web.Response:
    if not constants_module.experimental_s3_enabled():
        return _error_json("Experimental features are not enabled.", 403)
    if not _require_experimental_and_admin(request):
        return _error_json("Admin authentication required.", 403)
    mgr, error = _manager_or_error()
    if error:
        return error
    prefix = request.rel_url.query.get("prefix", "")
    max_keys = min(max(_safe_max_keys(request.rel_url.query.get("max_keys")), 1), 1000)
    try:
        objects = mgr.list_objects(prefix=prefix, max_keys=max_keys)
        return web.json_response({"objects": objects, "count": len(objects)})
    except Exception as exc:
        return _error_json(str(exc), 500)


def _safe_max_keys(raw) -> int:
    try:
        return int(raw or 200)
    except (TypeError, ValueError):
        return 200


@routes.post("/mss-login/api/s3/upload")
async def s3_upload(request: web.Request) -> web.Response:
    if not constants_module.experimental_s3_enabled():
        return _error_json("Experimental features are not enabled.", 403)
    if not _require_experimental_and_admin(request):
        return _error_json("Admin authentication required.", 403)
    mgr, error = _manager_or_error()
    if error:
        return error
    try:
        body = await request.json()
    except Exception:
        return _error_json("Invalid JSON body.")
    local_path = (body.get("local_path") or "").strip()
    s3_key = (body.get("s3_key") or "").strip()
    if not local_path or not s3_key:
        return _error_json("local_path and s3_key are required.")
    if not _local_path_allowed(local_path):
        return _error_json("local_path must be under DATA_DIR or ComfyUI root.", 400)
    try:
        result = mgr.upload_file(local_path, s3_key)
        return web.json_response({"message": "Upload complete.", **result})
    except FileNotFoundError as exc:
        return _error_json(str(exc), 404)
    except Exception as exc:
        return _error_json(str(exc), 500)


@routes.post("/mss-login/api/s3/download")
async def s3_download(request: web.Request) -> web.Response:
    if not constants_module.experimental_s3_enabled():
        return _error_json("Experimental features are not enabled.", 403)
    if not _require_experimental_and_admin(request):
        return _error_json("Admin authentication required.", 403)
    mgr, error = _manager_or_error()
    if error:
        return error
    try:
        body = await request.json()
    except Exception:
        return _error_json("Invalid JSON body.")
    s3_key = (body.get("s3_key") or "").strip()
    local_path = (body.get("local_path") or "").strip()
    if not s3_key or not local_path:
        return _error_json("s3_key and local_path are required.")
    if not _local_path_allowed(local_path):
        return _error_json("local_path must be under DATA_DIR or ComfyUI root.", 400)
    try:
        saved_path = mgr.download_file(s3_key, local_path)
        return web.json_response({"message": "Download complete.", "local_path": saved_path})
    except Exception as exc:
        return _error_json(str(exc), 500)


@routes.delete("/mss-login/api/s3/delete")
async def s3_delete(request: web.Request) -> web.Response:
    if not constants_module.experimental_s3_enabled():
        return _error_json("Experimental features are not enabled.", 403)
    if not _require_experimental_and_admin(request):
        return _error_json("Admin authentication required.", 403)
    mgr, error = _manager_or_error()
    if error:
        return error
    try:
        body = await request.json()
    except Exception:
        return _error_json("Invalid JSON body.")
    s3_key = (body.get("s3_key") or "").strip()
    if not s3_key:
        return _error_json("s3_key is required.")
    try:
        deleted = mgr.delete_object(s3_key)
        return web.json_response(
            {"message": "Object deleted." if deleted else "Object not found.", "deleted": deleted}
        )
    except Exception as exc:
        return _error_json(str(exc), 500)


@routes.get("/mss-login/api/s3/mount/status")
async def s3_mount_status(request: web.Request) -> web.Response:
    if not constants_module.experimental_s3_enabled():
        return _error_json("Experimental features are not enabled.", 403)
    if not _require_experimental_and_owner(request):
        return _error_json("Owner authentication required.", 403)
    mgr, error = _manager_or_error()
    if error:
        return error
    return web.json_response({"enabled": True, **mgr.status()})


@routes.post("/mss-login/api/s3/mount/sync")
async def s3_mount_sync(request: web.Request) -> web.Response:
    if not constants_module.experimental_s3_enabled():
        return _error_json("Experimental features are not enabled.", 403)
    if not _require_experimental_and_owner(request):
        return _error_json("Owner authentication required.", 403)
    mgr, error = _manager_or_error()
    if error:
        return error
    try:
        ok = mgr.trigger_sync()
        return web.json_response({"synced": ok, "workflow_status": mgr.workflow_status()})
    except Exception as exc:
        return _error_json(str(exc), 500)


@routes.post("/mss-login/api/s3/mount/remount")
async def s3_mount_remount(request: web.Request) -> web.Response:
    if not constants_module.experimental_s3_enabled():
        return _error_json("Experimental features are not enabled.", 403)
    if not _require_experimental_and_owner(request):
        return _error_json("Owner authentication required.", 403)
    mgr, error = _manager_or_error()
    if error:
        return error
    try:
        return web.json_response(mgr.remount())
    except Exception as exc:
        return _error_json(str(exc), 500)


@routes.post("/mss-login/api/s3/mount/unmount")
@routes.post("/s3_mounter/unmount")
async def s3_mount_unmount(request: web.Request) -> web.Response:
    if not constants_module.experimental_s3_enabled():
        return _error_json("Experimental features are not enabled.", 403)
    if not _require_experimental_and_owner(request):
        return _error_json("Owner authentication required.", 403)
    mgr, error = _manager_or_error()
    if error:
        return error
    return web.json_response(
        {"status": "ok", "message": mgr.unmount(), "mount_status": mgr.status()}
    )


@routes.get("/mss-login/api/s3/workflows/status")
async def s3_workflow_status(request: web.Request) -> web.Response:
    if not constants_module.experimental_s3_enabled():
        return _error_json("Experimental features are not enabled.", 403)
    if not _require_experimental_and_admin(request):
        return _error_json("Admin authentication required.", 403)
    mgr, error = _manager_or_error()
    if error:
        return error
    return web.json_response({"enabled": True, **mgr.workflow_status()})


@routes.post("/mss-login/api/s3/workflows/sync")
async def s3_workflow_sync_user(request: web.Request) -> web.Response:
    if not constants_module.experimental_s3_enabled():
        return _error_json("Experimental features are not enabled.", 403)
    username, is_admin = _require_experimental_and_auth(request)
    if not username:
        return _error_json("Authentication required.", 403)
    mgr, error = _manager_or_error()
    if error:
        return error

    target = request.rel_url.query.get("username", "").strip()
    if target and target != username and not is_admin:
        return _error_json("Admin privileges required to sync another user.", 403)
    sync_target = target or username
    try:
        stats = mgr.sync_user(sync_target)
        return web.json_response({"user": sync_target, **stats})
    except Exception as exc:
        return _error_json(str(exc), 500)


@routes.post("/mss-login/api/s3/workflows/sync-all")
async def s3_workflow_sync_all(request: web.Request) -> web.Response:
    if not constants_module.experimental_s3_enabled():
        return _error_json("Experimental features are not enabled.", 403)
    if not _require_experimental_and_admin(request):
        return _error_json("Admin authentication required.", 403)
    mgr, error = _manager_or_error()
    if error:
        return error
    try:
        results = mgr.sync_all_users()
        return web.json_response({"results": results})
    except Exception as exc:
        return _error_json(str(exc), 500)
