# --- START OF FILE routes/model_download.py ---
"""Model download from CivitAI / HuggingFace. Admin only. API keys per user, encrypted."""

import asyncio
import json
import os
import time

from aiohttp import web

from ..constants import EXPERIMENTAL_FEATURES, USERS_DB_CONFIG
from ..globals import jwt_auth, routes, users_db
from ..utils.model_cache import get_model_cache
from ..utils.model_download import download_civitai_async, download_huggingface
from ..utils.model_source_api_keys_store import SOURCES, get_model_source_api_keys_store


def _current_user_id_and_username(request):
    token = jwt_auth.get_token_from_request(request)
    if not token:
        return None, None
    try:
        p = jwt_auth.decode_access_token(token)
        username = p.get("username")
        if not username:
            return None, None
        user_id, _ = users_db.get_user(username=username)
        return user_id, username
    except Exception:
        return None, None


def _is_admin_or_owner(request):
    user_id, username = _current_user_id_and_username(request)
    if not user_id:
        return False
    _, u = users_db.get_user(username=username)
    groups = [g.lower() for g in (u.get("groups") or [])]
    return u.get("admin", False) or "admin" in groups or "owner" in groups


@routes.get("/mss-login/api/model-download/sources")
async def api_model_download_sources(request: web.Request) -> web.Response:
    """List sources and which have API keys set for current user (masked). Auth required."""
    user_id, _ = _current_user_id_and_username(request)
    if not user_id:
        return web.json_response({"error": "Authentication required"}, status=401)
    store = get_model_source_api_keys_store(USERS_DB_CONFIG)
    with_keys = store.list_sources_with_keys(user_id)
    return web.json_response(
        {
            "sources": list(SOURCES),
            "sources_with_keys": with_keys,
        }
    )


@routes.get("/mss-login/api/model-download/api-keys")
async def api_model_download_api_keys_get(request: web.Request) -> web.Response:
    """Return which sources have keys (masked). Current user only."""
    user_id, _ = _current_user_id_and_username(request)
    if not user_id:
        return web.json_response({"error": "Authentication required"}, status=401)
    store = get_model_source_api_keys_store(USERS_DB_CONFIG)
    with_keys = store.list_sources_with_keys(user_id)
    return web.json_response({"sources_with_keys": with_keys})


@routes.put("/mss-login/api/model-download/api-keys")
async def api_model_download_api_keys_put(request: web.Request) -> web.Response:
    """Set or clear API key for a source. Body: { source, api_key }. Current user only."""
    user_id, _ = _current_user_id_and_username(request)
    if not user_id:
        return web.json_response({"error": "Authentication required"}, status=401)
    try:
        body = await request.json()
    except Exception:
        return web.json_response({"error": "Invalid JSON"}, status=400)
    source = (body.get("source") or "").strip().lower()
    if source not in SOURCES:
        return web.json_response({"error": "Invalid source"}, status=400)
    api_key = (body.get("api_key") or "").strip()
    store = get_model_source_api_keys_store(USERS_DB_CONFIG)
    if api_key:
        ok = store.set_key(user_id, source, api_key)
        if not ok:
            return web.json_response({"error": "Failed to store key"}, status=500)
        return web.json_response({"status": "ok", "source": source})
    else:
        store.delete_key(user_id, source)
        return web.json_response({"status": "ok", "source": source, "cleared": True})


async def _write_progress_line(response: web.StreamResponse, obj: dict) -> None:
    """Append a newline-delimited JSON line to the stream."""
    await response.write((json.dumps(obj) + "\n").encode("utf-8"))


@routes.post("/mss-login/api/model-download/download")
async def api_model_download_start(request: web.Request) -> web.Response:
    """Start a model download. Admin only. Streams NDJSON progress then final status."""
    if not _is_admin_or_owner(request):
        return web.json_response({"error": "Admin or owner only"}, status=403)
    user_id, _ = _current_user_id_and_username(request)
    if not user_id:
        return web.json_response({"error": "Authentication required"}, status=401)
    try:
        body = await request.json()
    except Exception:
        return web.json_response({"error": "Invalid JSON"}, status=400)

    source = (body.get("source") or "").strip().lower()
    if source not in SOURCES:
        return web.json_response({"error": "Invalid source"}, status=400)
    destination_type = (body.get("destination_type") or "local").strip().lower()
    if destination_type not in ("local", "s3"):
        return web.json_response({"error": "Invalid destination_type"}, status=400)
    if destination_type == "s3" and not EXPERIMENTAL_FEATURES:
        return web.json_response(
            {
                "error": "S3 is an experimental feature. Enable EXPERIMENTAL_FEATURES to use it."
            },
            status=403,
        )
    folder_type = (body.get("folder_type") or "checkpoints").strip()
    # Prevent path traversal: allow only a single path segment (no "..", no separators)
    if (
        not folder_type
        or ".." in folder_type
        or "/" in folder_type
        or "\\" in folder_type
        or os.path.isabs(folder_type)
    ):
        return web.json_response(
            {"error": "Invalid folder_type: must be a single path segment"},
            status=400,
        )
    # Restrict to safe characters used by ComfyUI folder names (e.g. checkpoints, loras)
    if not all(c.isalnum() or c in "_-" for c in folder_type):
        return web.json_response(
            {"error": "Invalid folder_type: only letters, digits, underscore, hyphen allowed"},
            status=400,
        )

    store = get_model_source_api_keys_store(USERS_DB_CONFIG)
    token = store.get_key(user_id, source)
    if not token:
        return web.json_response(
            {"error": "No API key set for this source"}, status=400
        )

    # Resolve destination path
    dest_dir = None
    if destination_type == "local":
        try:
            from folder_paths import (  # pyright: ignore[reportMissingImports]
                get_folder_paths,
                models_path,
            )

            paths = get_folder_paths(folder_type)
            if paths:
                dest_dir = paths[0]
            else:
                dest_dir = os.path.join(models_path, folder_type)
        except Exception:
            return web.json_response(
                {"error": "Could not resolve local model path"}, status=500
            )
    else:
        try:
            from ..utils.s3_mount import get_mount_manager

            mgr = get_mount_manager()
            dest_dir = os.path.join(mgr.local_root, folder_type)
        except Exception:
            return web.json_response({"error": "S3 mount not available"}, status=500)

    response = web.StreamResponse(
        status=200,
        headers={"Content-Type": "application/x-ndjson; charset=utf-8"},
    )
    await response.prepare(request)

    start_time = time.perf_counter()

    async def write_progress(bytes_done: int, total_bytes: int | None):
        elapsed = time.perf_counter() - start_time
        await _write_progress_line(
            response,
            {
                "bytes_done": bytes_done,
                "total_bytes": total_bytes,
                "elapsed": round(elapsed, 2),
            },
        )

    try:
        if source == "civitai":
            model_version_id = (
                body.get("model_version_id") or body.get("modelVersionId") or ""
            ).strip()
            if not model_version_id:
                await _write_progress_line(
                    response,
                    {
                        "status": "error",
                        "error": "model_version_id required for CivitAI",
                    },
                )
                return response
            success, err = await download_civitai_async(
                model_version_id,
                token,
                dest_dir,
                type_param=body.get("type"),
                format_param=body.get("format"),
                progress_callback=write_progress,
            )
        elif source == "huggingface":
            repo_id = (body.get("repo_id") or "").strip()
            filename = (body.get("filename") or "").strip()
            # Prevent path traversal: use basename only; reject path separators
            if filename and (".." in filename or "/" in filename or "\\" in filename):
                filename = os.path.basename(filename)
            subfolder = body.get("subfolder")
            if isinstance(subfolder, str):
                subfolder = subfolder.strip()
                if ".." in subfolder or subfolder.startswith("/"):
                    subfolder = None
            if not repo_id or not filename:
                await _write_progress_line(
                    response,
                    {
                        "status": "error",
                        "error": "repo_id and filename required for HuggingFace",
                    },
                )
                return response
            progress_dict = {"bytes_done": 0, "total_bytes": None}
            loop = asyncio.get_event_loop()

            def run_hf():
                return download_huggingface(
                    repo_id,
                    filename,
                    token,
                    dest_dir,
                    subfolder=subfolder,
                    progress_dict=progress_dict,
                )

            task = loop.run_in_executor(None, run_hf)
            while not task.done():
                await asyncio.sleep(0.25)
                await write_progress(
                    progress_dict.get("bytes_done", 0),
                    progress_dict.get("total_bytes"),
                )
            success, err = await task
        else:
            await _write_progress_line(
                response, {"status": "error", "error": "Unknown source"}
            )
            return response

        if not success:
            await _write_progress_line(
                response, {"status": "error", "error": err or "Download failed"}
            )
            return response

        # Refresh model cache so new file appears
        try:
            cache = get_model_cache(USERS_DB_CONFIG)
            cache.refresh_from_folder_paths()
        except Exception:
            pass

        await _write_progress_line(response, {"status": "ok", "destination": dest_dir})
    except Exception as e:
        await _write_progress_line(response, {"status": "error", "error": str(e)})

    return response
