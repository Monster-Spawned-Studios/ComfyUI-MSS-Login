# --- START OF FILE routes/model_download.py ---
"""Queued model downloads with RBAC and provider/global concurrency caps."""

import asyncio
import os
import time
import uuid
from collections import deque
from datetime import UTC, datetime, timezone

from aiohttp import web

from ..constants import (
	USERS_DB_CONFIG,
	experimental_model_isolation_enabled,
	experimental_s3_enabled,
)
from ..globals import jwt_auth, logger, routes, users_db
from ..utils.folder_paths_compat import resolve_local_model_destination
from ..utils.model_cache import ASSET_FOLDERS_FALLBACK, get_model_cache
from ..utils.model_download import (
	ALLOWED_CIVITAI_HOSTS,
	DEFAULT_CIVITAI_HOST,
	civitai_get_model,
	civitai_get_model_version,
	civitai_search_models,
	download_civitai_async,
	download_huggingface,
	get_civitai_host_preference,
	normalize_civitai_host,
	resolve_model_url,
	search_huggingface_models,
	set_civitai_host_preference,
)
from ..utils.model_isolation import sanitize_user_segment
from ..utils.model_source_api_keys_store import SOURCES, get_model_source_api_keys_store
from ..utils.model_visibility_policy import user_can_download_models, user_can_manage_model_sharing
from ..utils.s3_mounter import get_mount_manager
from ..utils.shared_items_store import get_shared_items_store


def _current_user_id_and_username(request):
	# Prefer identity already attached by jwt middleware (API token or JWT).
	username = None
	user_id = None
	try:
		username = request.get("user") if hasattr(request, "get") else None
		user_id = request.get("user_id") if hasattr(request, "get") else None
	except Exception:
		username = None
		user_id = None
	if username and user_id:
		return user_id, username
	if username:
		try:
			uid, _ = users_db.get_user(username=username)
			if uid:
				return uid, username
		except Exception:
			pass
	token = jwt_auth.get_token_from_request(request)
	if not token:
		return None, None
	try:
		from ..utils.api_token_store import get_api_token_store

		api_cfg = getattr(jwt_auth, "api_token_store_config", None) or USERS_DB_CONFIG
		api_store = get_api_token_store(api_cfg)
		api_user = api_store.get_user_for_token(token)
		if api_user is not None:
			return api_user
	except Exception:
		pass
	try:
		p = jwt_auth.decode_access_token(token)
		username = p.get("username")
		if not username:
			return None, None
		user_id, _ = users_db.get_user(username=username)
		return user_id, username
	except Exception as e:
		logger.error(f"[MSS-Login] _current_user_id_and_username error: {e}")
		return None, None


def _role_and_perms(request):
	try:
		from ..globals import access_control

		return access_control._get_user_role_and_permissions(request)
	except Exception:
		return "guest", {}, None


def _can_download_models(request) -> bool:
	role, perms, _ = _role_and_perms(request)
	return user_can_download_models(role, perms)


def _download_auth_or_response(
	request: web.Request,
) -> tuple[str | None, str | None, web.Response | None]:
	"""Return (user_id, username, None) when authorized, or (None, None, error_response)."""
	user_id, username = _current_user_id_and_username(request)
	if not user_id:
		return None, None, web.json_response({"error": "Authentication required"}, status=401)
	if not _can_download_models(request):
		return (
			None,
			None,
			web.json_response({"error": "Model download permission required"}, status=403),
		)
	return user_id, username, None


def _download_capabilities() -> dict:
	return {
		"sources": list(SOURCES),
		"destination_types": ["local", "s3"],
		"limits": {
			"active_total": MAX_ACTIVE_DOWNLOADS,
			"active_civitai": MAX_CIVITAI_DOWNLOADS,
			"active_huggingface": MAX_HUGGINGFACE_DOWNLOADS,
		},
		"experimental": {
			"s3": experimental_s3_enabled(),
			"model_isolation": experimental_model_isolation_enabled(),
		},
		"civitai_hosts": sorted(ALLOWED_CIVITAI_HOSTS),
		"default_civitai_host": DEFAULT_CIVITAI_HOST,
		"civitai_fields": ["model_version_id", "type", "format", "size", "fp", "civitai_host"],
		"huggingface_fields": ["repo_id", "filename", "subfolder"],
	}


def _source_token(user_id: str, source: str) -> str | None:
	"""Return the caller's encrypted-at-rest API key for source (never log/echo)."""
	store = get_model_source_api_keys_store(USERS_DB_CONFIG)
	return store.get_key(user_id, source)


def _resolve_civitai_host_for_request(
	username: str | None,
	body_or_query_host: str | None = None,
	url_hint_host: str | None = None,
) -> str:
	"""
	Host selection: explicit request/URL host (allowlisted) wins; else per-user preference.
	Pasting a .red URL routes that request through .red without changing the saved preference.
	"""
	if url_hint_host:
		return normalize_civitai_host(url_hint_host)
	if body_or_query_host:
		return normalize_civitai_host(body_or_query_host)
	if username:
		return get_civitai_host_preference(username)
	return DEFAULT_CIVITAI_HOST


def _list_download_folder_types() -> list[str]:
	try:
		cache = get_model_cache(USERS_DB_CONFIG)
		folders = cache.list_folders()
		if folders:
			return folders
	except Exception:
		pass
	try:
		import folder_paths  # pyright: ignore[reportMissingImports]

		return sorted(folder_paths.folder_names_and_paths.keys())
	except Exception:
		return sorted(ASSET_FOLDERS_FALLBACK)


MAX_ACTIVE_DOWNLOADS = 5
MAX_CIVITAI_DOWNLOADS = 3
MAX_HUGGINGFACE_DOWNLOADS = 2

_JOBS_LOCK = asyncio.Lock()
_JOBS_BY_ID: dict[str, dict] = {}
_PENDING_JOB_IDS: deque[str] = deque()
_RUNNING_JOB_IDS: set[str] = set()
_RUNNING_BY_PROVIDER = {"civitai": 0, "huggingface": 0}


def _utc_now() -> str:
	return datetime.now(UTC).isoformat()


def _job_public_view(job: dict) -> dict:
	out = {
		"job_id": job["job_id"],
		"source": job.get("source"),
		"destination_type": job.get("destination_type"),
		"folder_type": job.get("folder_type"),
		"status": job.get("status"),
		"created_at": job.get("created_at"),
		"started_at": job.get("started_at"),
		"finished_at": job.get("finished_at"),
		"bytes_done": int(job.get("bytes_done", 0) or 0),
		"total_bytes": job.get("total_bytes"),
		"elapsed": float(job.get("elapsed", 0) or 0),
		"progress_pct": float(job.get("progress_pct", 0) or 0),
		"speed_bps": float(job.get("speed_bps", 0) or 0),
		"eta_seconds": float(job.get("eta_seconds", 0) or 0),
		"destination": job.get("destination", ""),
		"error": job.get("error", ""),
		"can_cancel": job.get("status") == "queued",
	}
	if job.get("source") == "civitai" and job.get("model_version_id"):
		out["model_version_id"] = job["model_version_id"]
		if job.get("civitai_host"):
			out["civitai_host"] = job["civitai_host"]
	elif job.get("source") == "huggingface":
		if job.get("repo_id"):
			out["repo_id"] = job["repo_id"]
		if job.get("filename"):
			out["filename"] = job["filename"]
	return out


def _queue_stats() -> dict:
	return {
		"active_total": len(_RUNNING_JOB_IDS),
		"active_civitai": _RUNNING_BY_PROVIDER["civitai"],
		"active_huggingface": _RUNNING_BY_PROVIDER["huggingface"],
		"pending_total": len(_PENDING_JOB_IDS),
		"limit_total": MAX_ACTIVE_DOWNLOADS,
		"limit_civitai": MAX_CIVITAI_DOWNLOADS,
		"limit_huggingface": MAX_HUGGINGFACE_DOWNLOADS,
	}


def _provider_limit_for(source: str) -> int:
	if source == "civitai":
		return MAX_CIVITAI_DOWNLOADS
	return MAX_HUGGINGFACE_DOWNLOADS


def _can_start_job_unsafe(source: str) -> bool:
	if len(_RUNNING_JOB_IDS) >= MAX_ACTIVE_DOWNLOADS:
		return False
	return _RUNNING_BY_PROVIDER.get(source, 0) < _provider_limit_for(source)


async def _schedule_jobs_unsafe() -> None:
	while _PENDING_JOB_IDS:
		job_id = _PENDING_JOB_IDS[0]
		job = _JOBS_BY_ID.get(job_id)
		if not job:
			_PENDING_JOB_IDS.popleft()
			continue
		source = job.get("source", "")
		if not _can_start_job_unsafe(source):
			break
		_PENDING_JOB_IDS.popleft()
		job["status"] = "running"
		job["started_at"] = _utc_now()
		_RUNNING_JOB_IDS.add(job_id)
		_RUNNING_BY_PROVIDER[source] = _RUNNING_BY_PROVIDER.get(source, 0) + 1
		asyncio.create_task(_run_job(job_id))


def _safe_folder(folder_type: str) -> bool:
	return bool(folder_type) and all(c.isalnum() or c in "_-" for c in folder_type)


def _validate_folder(folder_type: str) -> str | None:
	if (
		not folder_type
		or ".." in folder_type
		or "/" in folder_type
		or "\\" in folder_type
		or os.path.isabs(folder_type)
	):
		return "Invalid folder_type: must be a single path segment"
	if not _safe_folder(folder_type):
		return "Invalid folder_type: only letters, digits, underscore, hyphen allowed"
	return None


def _resolve_destination_path(job: dict, target_user_id: str) -> tuple[str, str]:
	destination_type = job.get("destination_type", "local")
	folder_type = job.get("folder_type", "checkpoints")
	if destination_type == "local":
		dest_dir, base_dir = resolve_local_model_destination(folder_type, target_user_id)
	else:
		mgr = get_mount_manager()
		if mgr is None:
			raise RuntimeError("S3 manager is not available")
		# FUSE mount or boto3 staging under the local mount models path
		if not mgr.is_mounted() and not mgr._in_boto3_mode():
			raise RuntimeError("S3 mount is not active")
		if experimental_model_isolation_enabled():
			dest_dir = mgr.get_models_folder_path(
				f"{folder_type}/{sanitize_user_segment(target_user_id)}"
			)
		else:
			dest_dir = mgr.get_models_folder_path(folder_type)
		os.makedirs(dest_dir, exist_ok=True)
		base_dir = os.path.realpath(mgr.models_root)

	resolved_dest = os.path.realpath(dest_dir)
	if not (resolved_dest == base_dir or resolved_dest.startswith(base_dir + os.sep)):
		raise RuntimeError("Destination path escapes allowed directory")
	return dest_dir, base_dir


def _upload_s3_staged_download(dest_dir: str, saved_rel: str) -> None:
	"""When S3 is in boto3 mode, push a staged local download to the bucket."""
	mgr = get_mount_manager()
	if mgr is None or not mgr._in_boto3_mode():
		return
	local_path = os.path.realpath(os.path.join(dest_dir, saved_rel))
	models_root = os.path.realpath(mgr.models_root)
	if not (local_path == models_root or local_path.startswith(models_root + os.sep)):
		raise RuntimeError("Staged download path escapes S3 models root")
	if not os.path.isfile(local_path):
		raise RuntimeError(f"Staged download file missing: {saved_rel}")
	rel_from_models = os.path.relpath(local_path, models_root).replace("\\", "/")
	s3_key = f"models/{rel_from_models}"
	mgr.upload_file(local_path, s3_key)


def _auto_grant_downloaded_item(
	*,
	target_user_id: str,
	folder_type: str,
	saved_rel: str,
	destination_type: str,
	granted_by_user_id: str,
	granted_by_role: str,
) -> None:
	"""Grant only the single downloaded item when model isolation is enabled."""
	if not experimental_model_isolation_enabled() or not target_user_id or not saved_rel:
		return
	rel = (saved_rel or "").replace("\\", "/").lstrip("/")
	if not rel or ".." in rel.split("/"):
		return
	user_seg = sanitize_user_segment(target_user_id)
	item_name = f"{user_seg}/{rel}"
	shared_store = get_shared_items_store(USERS_DB_CONFIG)
	shared_store.add(
		target_user_id,
		folder_type,
		item_name,
		source_backend=("s3" if destination_type == "s3" else "local"),
		granted_by_user_id=granted_by_user_id or "",
		granted_by_role=granted_by_role or "",
	)


async def _set_progress(
	job_id: str, bytes_done: int, total_bytes: int | None, start_time: float
) -> None:
	async with _JOBS_LOCK:
		job = _JOBS_BY_ID.get(job_id)
		if not job:
			return
		elapsed = max(0.001, time.perf_counter() - start_time)
		speed_bps = float(bytes_done) / elapsed
		eta_seconds = 0.0
		progress_pct = 0.0
		if total_bytes and total_bytes > 0:
			progress_pct = min(100.0, (float(bytes_done) / float(total_bytes)) * 100.0)
			remaining = max(0.0, float(total_bytes) - float(bytes_done))
			if speed_bps > 0:
				eta_seconds = remaining / speed_bps
		job["bytes_done"] = int(bytes_done)
		job["total_bytes"] = int(total_bytes) if total_bytes is not None else None
		job["elapsed"] = round(elapsed, 2)
		job["speed_bps"] = round(speed_bps, 2)
		job["eta_seconds"] = round(eta_seconds, 2)
		job["progress_pct"] = round(progress_pct, 2)


async def _run_job(job_id: str) -> None:
	success = False
	error = ""
	dest_dir = ""
	try:
		async with _JOBS_LOCK:
			job = _JOBS_BY_ID.get(job_id)
			if not job:
				return
			job["status"] = "running"
			start_time = time.perf_counter()
		source = job["source"]
		token = job["token"]
		target_user_id = job["target_user_id"]
		target_username = job.get("target_username", "")
		user_id = job["user_id"]
		role = job.get("role", "guest")
		destination_type = job.get("destination_type", "local")
		folder_type = job.get("folder_type", "checkpoints")

		dest_dir, _base_dir = _resolve_destination_path(job, target_user_id)
		saved_rel: str | None = None

		async def progress_callback(bytes_done: int, total_bytes: int | None):
			async with _JOBS_LOCK:
				cur = _JOBS_BY_ID.get(job_id, {})
				if cur.get("cancel_requested"):
					raise RuntimeError("Cancelled by user")
			await _set_progress(job_id, bytes_done, total_bytes, start_time)

		if source == "civitai":
			success, error, saved_rel = await download_civitai_async(
				job["model_version_id"],
				token,
				dest_dir,
				type_param=job.get("type"),
				format_param=job.get("format"),
				size_param=job.get("size"),
				fp_param=job.get("fp"),
				progress_callback=progress_callback,
				host=job.get("civitai_host"),
			)
		else:
			progress_dict = {"bytes_done": 0, "total_bytes": None}
			loop = asyncio.get_event_loop()

			def run_hf():
				return download_huggingface(
					job["repo_id"],
					job["filename"],
					token,
					dest_dir,
					subfolder=job.get("subfolder"),
					progress_dict=progress_dict,
				)

			task = loop.run_in_executor(None, run_hf)
			while not task.done():
				await asyncio.sleep(0.25)
				async with _JOBS_LOCK:
					cur = _JOBS_BY_ID.get(job_id, {})
					if cur.get("cancel_requested"):
						error = "Cancelled by user"
						break
				await _set_progress(
					job_id,
					int(progress_dict.get("bytes_done", 0) or 0),
					progress_dict.get("total_bytes"),
					start_time,
				)
			if error:
				success = False
			else:
				success, error, saved_rel = await task

		if success and destination_type == "s3" and saved_rel:
			_upload_s3_staged_download(dest_dir, saved_rel)

		if success:
			# Always refresh cache so shared-library destinations pick up the new file.
			# Do not auto-grant other users when isolation is off.
			try:
				cache = get_model_cache(USERS_DB_CONFIG)
				cache.refresh_from_folder_paths()
			except Exception:
				pass
			try:
				_auto_grant_downloaded_item(
					target_user_id=target_user_id,
					folder_type=folder_type,
					saved_rel=saved_rel or "",
					destination_type=destination_type,
					granted_by_user_id=user_id or "",
					granted_by_role=role or "",
				)
			except Exception as e:
				logger.warning(f"[MSS-Login] model isolation auto-grant failed: {e}")
			if target_username:
				logger.info(f"[MSS-Login] Download completed for target user '{target_username}'")
	except Exception as e:
		error = str(e)
		success = False
	finally:
		async with _JOBS_LOCK:
			job = _JOBS_BY_ID.get(job_id)
			if job:
				job["status"] = (
					"completed"
					if success
					else ("cancelled" if error == "Cancelled by user" else "failed")
				)
				job["finished_at"] = _utc_now()
				job["destination"] = dest_dir
				job["error"] = error or ""
				if success:
					job["progress_pct"] = 100.0
			source = job.get("source") if job else None
			if source:
				_RUNNING_BY_PROVIDER[source] = max(0, _RUNNING_BY_PROVIDER.get(source, 0) - 1)
			_RUNNING_JOB_IDS.discard(job_id)
			await _schedule_jobs_unsafe()


@routes.get("/mss-login/api/model-download/sources")
async def api_model_download_sources(request: web.Request) -> web.Response:
	"""List sources, key-presence, and client capabilities. Requires model-download permission."""
	user_id, _username, err = _download_auth_or_response(request)
	if err:
		return err
	store = get_model_source_api_keys_store(USERS_DB_CONFIG)
	with_keys = store.list_sources_with_keys(user_id)
	return web.json_response(
		{
			"sources": list(SOURCES),
			"sources_with_keys": with_keys,
			"capabilities": _download_capabilities(),
		}
	)


@routes.get("/mss-login/api/model-download/folders")
async def api_model_download_folders(request: web.Request) -> web.Response:
	"""List valid folder_type values for download destinations (mobile/client API)."""
	_user_id, _username, err = _download_auth_or_response(request)
	if err:
		return err
	folders = _list_download_folder_types()
	return web.json_response({"folders": folders, "default_folder": "checkpoints"})


@routes.get("/mss-login/api/model-download/api-keys")
async def api_model_download_api_keys_get(request: web.Request) -> web.Response:
	"""Return which sources have keys for current user only."""
	user_id, _username, err = _download_auth_or_response(request)
	if err:
		return err
	store = get_model_source_api_keys_store(USERS_DB_CONFIG)
	with_keys = store.list_sources_with_keys(user_id)
	return web.json_response({"sources_with_keys": with_keys})


@routes.put("/mss-login/api/model-download/api-keys")
async def api_model_download_api_keys_put(request: web.Request) -> web.Response:
	"""Set or clear API key for a source. Body: { source, api_key }. Current user only."""
	user_id, _username, err = _download_auth_or_response(request)
	if err:
		return err
	try:
		body = await request.json()
	except Exception as e:
		return web.json_response({"error": f"Invalid JSON: {e}"}, status=400)
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


@routes.post("/mss-login/api/model-download/download")
async def api_model_download_start(request: web.Request) -> web.Response:
	"""Queue a model download job and return job id."""
	user_id, username, err = _download_auth_or_response(request)
	if err:
		return err
	role, perms, _role_username = _role_and_perms(request)
	try:
		body = await request.json()
	except Exception as e:
		return web.json_response({"error": f"Invalid JSON: {e}"}, status=400)

	# Optional pasted URL: SSRF-safe resolve into source identifiers (host allowlisted).
	url_hint = None
	pasted_url = (body.get("url") or body.get("model_url") or "").strip()
	if pasted_url:
		resolved = resolve_model_url(pasted_url)
		if not resolved:
			return web.json_response({"error": "Unrecognized or disallowed model URL"}, status=400)
		body = {**body, **{k: v for k, v in resolved.items() if k != "host"}}
		if resolved.get("source"):
			body["source"] = resolved["source"]
		if resolved.get("host") and resolved.get("source") == "civitai":
			url_hint = resolved["host"]
			body.setdefault("civitai_host", resolved["host"])

	source = (body.get("source") or "").strip().lower()
	if source not in SOURCES:
		return web.json_response({"error": "Invalid source"}, status=400)
	destination_type = (body.get("destination_type") or "local").strip().lower()
	if destination_type not in ("local", "s3"):
		return web.json_response({"error": "Invalid destination_type"}, status=400)
	if destination_type == "s3" and not experimental_s3_enabled():
		return web.json_response(
			{
				"error": "S3 is an experimental feature. Enable experimental_features and experimental.s3 to use it."
			},
			status=403,
		)
	folder_type = (body.get("folder_type") or "checkpoints").strip()
	folder_error = _validate_folder(folder_type)
	if folder_error:
		return web.json_response({"error": folder_error}, status=400)

	token = _source_token(user_id, source)
	if not token:
		return web.json_response({"error": "No API key set for this source"}, status=400)

	target_user_id = user_id
	target_username = (body.get("target_username") or "").strip()
	if target_username:
		if not user_can_manage_model_sharing(role, perms):
			return web.json_response(
				{"error": "Only owner/admin with sharing permission can set target_username"},
				status=403,
			)
		target_user_id, _ = users_db.get_user(username=target_username)
		if not target_user_id:
			return web.json_response({"error": "target_username not found"}, status=404)
	job_id = uuid.uuid4().hex
	job = {
		"job_id": job_id,
		"user_id": user_id,
		"source": source,
		"destination_type": destination_type,
		"folder_type": folder_type,
		"target_user_id": target_user_id,
		"target_username": target_username,
		"role": role,
		"token": token,
		"status": "queued",
		"created_at": _utc_now(),
		"started_at": "",
		"finished_at": "",
		"bytes_done": 0,
		"total_bytes": None,
		"elapsed": 0.0,
		"progress_pct": 0.0,
		"speed_bps": 0.0,
		"eta_seconds": 0.0,
		"destination": "",
		"error": "",
		"cancel_requested": False,
	}
	if source == "civitai":
		model_version_id = (
			body.get("model_version_id") or body.get("modelVersionId") or ""
		).strip()
		if not model_version_id:
			return web.json_response({"error": "model_version_id required for CivitAI"}, status=400)
		job["model_version_id"] = model_version_id
		job["type"] = body.get("type")
		job["format"] = body.get("format")
		job["size"] = body.get("size")
		job["fp"] = body.get("fp")
		job["civitai_host"] = _resolve_civitai_host_for_request(
			username,
			body_or_query_host=body.get("civitai_host") or body.get("host"),
			url_hint_host=url_hint,
		)
	else:
		repo_id = (body.get("repo_id") or "").strip()
		filename = (body.get("filename") or "").strip()
		if filename and (".." in filename or "/" in filename or "\\" in filename):
			filename = os.path.basename(filename)
		subfolder = body.get("subfolder")
		if isinstance(subfolder, str):
			subfolder = subfolder.strip()
			if ".." in subfolder or subfolder.startswith("/"):
				subfolder = None
		if not repo_id or not filename:
			return web.json_response(
				{"error": "repo_id and filename required for HuggingFace"}, status=400
			)
		job["repo_id"] = repo_id
		job["filename"] = filename
		job["subfolder"] = subfolder

	async with _JOBS_LOCK:
		_JOBS_BY_ID[job_id] = job
		_PENDING_JOB_IDS.append(job_id)
		await _schedule_jobs_unsafe()
	return web.json_response({"status": "queued", "job_id": job_id, "stats": _queue_stats()})


@routes.get("/mss-login/api/model-download/preferences")
async def api_model_download_preferences_get(request: web.Request) -> web.Response:
	"""Return caller's non-secret model-download preferences (civitai_host)."""
	_user_id, username, err = _download_auth_or_response(request)
	if err:
		return err
	host = get_civitai_host_preference(username or "")
	return web.json_response(
		{
			"civitai_host": host,
			"allowed_civitai_hosts": sorted(ALLOWED_CIVITAI_HOSTS),
			"default_civitai_host": DEFAULT_CIVITAI_HOST,
		}
	)


@routes.put("/mss-login/api/model-download/preferences")
async def api_model_download_preferences_put(request: web.Request) -> web.Response:
	"""Update caller's civitai_host preference (civitai.com | civitai.red)."""
	_user_id, username, err = _download_auth_or_response(request)
	if err:
		return err
	if not username:
		return web.json_response({"error": "Username required"}, status=400)
	try:
		body = await request.json()
	except Exception as e:
		return web.json_response({"error": f"Invalid JSON: {e}"}, status=400)
	raw_host = body.get("civitai_host") or body.get("host") or ""
	normalized = normalize_civitai_host(raw_host)
	# Reject unknown hosts rather than silently rewriting (except empty -> default).
	requested = (raw_host or "").strip().lower()
	if requested.startswith("https://"):
		requested = requested[len("https://") :]
	elif requested.startswith("http://"):
		requested = requested[len("http://") :]
	requested = requested.split("/")[0].split("?")[0]
	if requested.startswith("www."):
		requested = requested[4:]
	if requested and requested not in ALLOWED_CIVITAI_HOSTS:
		return web.json_response(
			{"error": "Invalid civitai_host; allowed: civitai.com, civitai.red"},
			status=400,
		)
	host = set_civitai_host_preference(username, normalized)
	return web.json_response({"status": "ok", "civitai_host": host})


@routes.get("/mss-login/api/model-download/civitai/search")
async def api_model_download_civitai_search(request: web.Request) -> web.Response:
	"""Proxy CivitAI model search. Uses caller's encrypted API key; never echoes it."""
	user_id, username, err = _download_auth_or_response(request)
	if err:
		return err
	query = (request.rel_url.query.get("query") or request.rel_url.query.get("q") or "").strip()
	types = (request.rel_url.query.get("types") or "").strip() or None
	try:
		page = int(request.rel_url.query.get("page") or "1")
	except ValueError:
		page = 1
	try:
		limit = int(request.rel_url.query.get("limit") or "20")
	except ValueError:
		limit = 20
	host = _resolve_civitai_host_for_request(
		username,
		body_or_query_host=request.rel_url.query.get("civitai_host")
		or request.rel_url.query.get("host"),
	)
	token = _source_token(user_id, "civitai")
	data, error = await civitai_search_models(
		query, token=token, host=host, types=types, page=page, limit=limit
	)
	if error:
		return web.json_response({"error": error}, status=502)
	return web.json_response({"host": host, "query": query, "result": data})


@routes.get("/mss-login/api/model-download/civitai/models/{model_id}")
async def api_model_download_civitai_model(request: web.Request) -> web.Response:
	"""Proxy GET /api/v1/models/{id} against the preferred CivitAI host."""
	user_id, username, err = _download_auth_or_response(request)
	if err:
		return err
	model_id = (request.match_info.get("model_id") or "").strip()
	if not model_id.isdigit():
		return web.json_response({"error": "Invalid model_id"}, status=400)
	host = _resolve_civitai_host_for_request(
		username,
		body_or_query_host=request.rel_url.query.get("civitai_host")
		or request.rel_url.query.get("host"),
	)
	token = _source_token(user_id, "civitai")
	data, error = await civitai_get_model(model_id, token=token, host=host)
	if error:
		return web.json_response({"error": error}, status=502)
	return web.json_response({"host": host, "model": data})


@routes.get("/mss-login/api/model-download/civitai/model-versions/{version_id}")
async def api_model_download_civitai_model_version(request: web.Request) -> web.Response:
	"""Proxy GET /api/v1/model-versions/{id} against the preferred CivitAI host."""
	user_id, username, err = _download_auth_or_response(request)
	if err:
		return err
	version_id = (request.match_info.get("version_id") or "").strip()
	if not version_id.isdigit():
		return web.json_response({"error": "Invalid version_id"}, status=400)
	host = _resolve_civitai_host_for_request(
		username,
		body_or_query_host=request.rel_url.query.get("civitai_host")
		or request.rel_url.query.get("host"),
	)
	token = _source_token(user_id, "civitai")
	data, error = await civitai_get_model_version(version_id, token=token, host=host)
	if error:
		return web.json_response({"error": error}, status=502)
	return web.json_response({"host": host, "model_version": data})


@routes.get("/mss-login/api/model-download/huggingface/search")
async def api_model_download_huggingface_search(request: web.Request) -> web.Response:
	"""Search Hugging Face Hub models. Uses caller's encrypted API key; never echoes it."""
	user_id, _username, err = _download_auth_or_response(request)
	if err:
		return err
	query = (request.rel_url.query.get("query") or request.rel_url.query.get("q") or "").strip()
	try:
		limit = int(request.rel_url.query.get("limit") or "20")
	except ValueError:
		limit = 20
	token = _source_token(user_id, "huggingface")
	loop = asyncio.get_event_loop()
	items, error = await loop.run_in_executor(
		None, lambda: search_huggingface_models(query, token=token, limit=limit)
	)
	if error:
		return web.json_response({"error": error}, status=502)
	return web.json_response({"query": query, "items": items or []})


@routes.get("/mss-login/api/model-download/jobs/{job_id}")
async def api_model_download_job_get(request: web.Request) -> web.Response:
	"""Return one download job by id (for mobile polling). Caller must own the job."""
	user_id, _username, err = _download_auth_or_response(request)
	if err:
		return err
	job_id = (request.match_info.get("job_id") or "").strip()
	if not job_id:
		return web.json_response({"error": "Missing job_id"}, status=400)
	async with _JOBS_LOCK:
		job = _JOBS_BY_ID.get(job_id)
		if not job or job.get("user_id") != user_id:
			return web.json_response({"error": "Job not found"}, status=404)
		return web.json_response({"job": _job_public_view(job), "stats": _queue_stats()})


@routes.get("/mss-login/api/model-download/jobs")
async def api_model_download_jobs(request: web.Request) -> web.Response:
	"""Return caller-visible jobs and queue stats (privacy-preserving, per-user)."""
	user_id, _username, err = _download_auth_or_response(request)
	if err:
		return err
	async with _JOBS_LOCK:
		jobs = [
			_job_public_view(job) for job in _JOBS_BY_ID.values() if job.get("user_id") == user_id
		]
		jobs.sort(key=lambda item: item.get("created_at", ""), reverse=True)
		return web.json_response({"jobs": jobs, "stats": _queue_stats()})


@routes.post("/mss-login/api/model-download/jobs/{job_id}/cancel")
async def api_model_download_cancel(request: web.Request) -> web.Response:
	"""Cancel a queued/running download job owned by the caller."""
	user_id, _username, err = _download_auth_or_response(request)
	if err:
		return err
	job_id = (request.match_info.get("job_id") or "").strip()
	if not job_id:
		return web.json_response({"error": "Missing job_id"}, status=400)
	async with _JOBS_LOCK:
		job = _JOBS_BY_ID.get(job_id)
		if not job or job.get("user_id") != user_id:
			return web.json_response({"error": "Job not found"}, status=404)
		if job.get("status") in ("completed", "failed", "cancelled"):
			return web.json_response({"error": "Job already finished"}, status=400)
		if job.get("status") == "running":
			return web.json_response(
				{"error": "Running downloads cannot be cancelled safely; wait for completion"},
				status=400,
			)
		job["cancel_requested"] = True
		if job.get("status") == "queued":
			job["status"] = "cancelled"
			job["finished_at"] = _utc_now()
			try:
				_PENDING_JOB_IDS.remove(job_id)
			except ValueError:
				pass
		return web.json_response({"status": "ok", "job_id": job_id})


routes.get("/api/mss-login/api/model-download/sources")(api_model_download_sources)
routes.get("/api/mss-login/api/model-download/folders")(api_model_download_folders)
routes.get("/api/mss-login/api/model-download/api-keys")(api_model_download_api_keys_get)
routes.put("/api/mss-login/api/model-download/api-keys")(api_model_download_api_keys_put)
routes.get("/api/mss-login/api/model-download/preferences")(api_model_download_preferences_get)
routes.put("/api/mss-login/api/model-download/preferences")(api_model_download_preferences_put)
routes.get("/api/mss-login/api/model-download/civitai/search")(api_model_download_civitai_search)
routes.get("/api/mss-login/api/model-download/civitai/models/{model_id}")(
	api_model_download_civitai_model
)
routes.get("/api/mss-login/api/model-download/civitai/model-versions/{version_id}")(
	api_model_download_civitai_model_version
)
routes.get("/api/mss-login/api/model-download/huggingface/search")(
	api_model_download_huggingface_search
)
routes.post("/api/mss-login/api/model-download/download")(api_model_download_start)
routes.get("/api/mss-login/api/model-download/jobs/{job_id}")(api_model_download_job_get)
routes.get("/api/mss-login/api/model-download/jobs")(api_model_download_jobs)
routes.post("/api/mss-login/api/model-download/jobs/{job_id}/cancel")(api_model_download_cancel)
