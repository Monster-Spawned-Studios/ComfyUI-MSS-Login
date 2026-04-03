# --- START OF FILE routes/model_download.py ---
"""Queued model downloads with RBAC and provider/global concurrency caps."""

import asyncio
import os
import time
import uuid
from collections import deque
from datetime import datetime, timezone

from aiohttp import web

from ..constants import USERS_DB_CONFIG, experimental_model_isolation_enabled, experimental_s3_enabled
from ..globals import jwt_auth, logger, routes, users_db
from ..utils.model_cache import get_model_cache
from ..utils.model_download import download_civitai_async, download_huggingface
from ..utils.model_isolation import maybe_isolated_destination, sanitize_user_segment
from ..utils.model_source_api_keys_store import SOURCES, get_model_source_api_keys_store
from ..utils.model_visibility_policy import user_can_download_models, user_can_manage_model_sharing
from ..utils.shared_items_store import get_shared_items_store


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


MAX_ACTIVE_DOWNLOADS = 5
MAX_CIVITAI_DOWNLOADS = 3
MAX_HUGGINGFACE_DOWNLOADS = 2

_JOBS_LOCK = asyncio.Lock()
_JOBS_BY_ID: dict[str, dict] = {}
_PENDING_JOB_IDS: deque[str] = deque()
_RUNNING_JOB_IDS: set[str] = set()
_RUNNING_BY_PROVIDER = {"civitai": 0, "huggingface": 0}


def _utc_now() -> str:
	return datetime.now(timezone.utc).isoformat()


def _job_public_view(job: dict) -> dict:
	return {
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


def _resolve_destination_path(
	job: dict,
	target_user_id: str,
) -> tuple[str, str]:
	destination_type = job.get("destination_type", "local")
	folder_type = job.get("folder_type", "checkpoints")
	if destination_type == "local":
		from folder_paths import get_folder_paths, models_path

		isolated_dest = maybe_isolated_destination(folder_type, target_user_id)
		if isolated_dest is not None:
			dest_dir = isolated_dest
			base_dir = os.path.realpath(os.path.join(isolated_dest, "..", "..", ".."))
		else:
			paths = get_folder_paths(folder_type)
			if paths:
				dest_dir = paths[0]
				base_dir = os.path.realpath(models_path)
			else:
				dest_dir = os.path.join(models_path, folder_type)
				base_dir = os.path.realpath(models_path)
	else:
		from ..utils.s3_mounter import get_mount_manager

		mgr = get_mount_manager()
		if mgr is None or not mgr.is_mounted():
			raise RuntimeError("S3 mount is not active")
		if experimental_model_isolation_enabled():
			dest_dir = mgr.get_models_folder_path(f"{folder_type}/{sanitize_user_segment(target_user_id)}")
		else:
			dest_dir = mgr.get_models_folder_path(folder_type)
		base_dir = os.path.realpath(mgr.models_root)

	resolved_dest = os.path.realpath(dest_dir)
	if not (resolved_dest == base_dir or resolved_dest.startswith(base_dir + os.sep)):
		raise RuntimeError("Destination path escapes allowed directory")
	return dest_dir, base_dir


async def _set_progress(job_id: str, bytes_done: int, total_bytes: int | None, start_time: float) -> None:
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

		async def progress_callback(bytes_done: int, total_bytes: int | None):
			async with _JOBS_LOCK:
				cur = _JOBS_BY_ID.get(job_id, {})
				if cur.get("cancel_requested"):
					raise RuntimeError("Cancelled by user")
			await _set_progress(job_id, bytes_done, total_bytes, start_time)

		if source == "civitai":
			success, error = await download_civitai_async(
				job["model_version_id"],
				token,
				dest_dir,
				type_param=job.get("type"),
				format_param=job.get("format"),
				progress_callback=progress_callback,
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
				success, error = await task

		if success:
			try:
				cache = get_model_cache(USERS_DB_CONFIG)
				cache.refresh_from_folder_paths()
			except Exception:
				pass
			if experimental_model_isolation_enabled() and target_user_id:
				try:
					cache = get_model_cache(USERS_DB_CONFIG)
					items = cache.list_items(folder_type)
					prefix = f"{sanitize_user_segment(target_user_id)}/"
					shared_store = get_shared_items_store(USERS_DB_CONFIG)
					for item_name in items:
						if item_name.startswith(prefix):
							shared_store.add(
								target_user_id,
								folder_type,
								item_name,
								source_backend=("s3" if destination_type == "s3" else "local"),
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
				job["status"] = "completed" if success else ("cancelled" if error == "Cancelled by user" else "failed")
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
	"""List sources and key-presence for current user. Requires model-download permission."""
	user_id, _ = _current_user_id_and_username(request)
	if not user_id:
		return web.json_response({"error": "Authentication required"}, status=401)
	if not _can_download_models(request):
		return web.json_response({"error": "Model download permission required"}, status=403)
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
	"""Return which sources have keys for current user only."""
	user_id, _ = _current_user_id_and_username(request)
	if not user_id:
		return web.json_response({"error": "Authentication required"}, status=401)
	if not _can_download_models(request):
		return web.json_response({"error": "Model download permission required"}, status=403)
	store = get_model_source_api_keys_store(USERS_DB_CONFIG)
	with_keys = store.list_sources_with_keys(user_id)
	return web.json_response({"sources_with_keys": with_keys})


@routes.put("/mss-login/api/model-download/api-keys")
async def api_model_download_api_keys_put(request: web.Request) -> web.Response:
	"""Set or clear API key for a source. Body: { source, api_key }. Current user only."""
	user_id, _ = _current_user_id_and_username(request)
	if not user_id:
		return web.json_response({"error": "Authentication required"}, status=401)
	if not _can_download_models(request):
		return web.json_response({"error": "Model download permission required"}, status=403)
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
	user_id, _ = _current_user_id_and_username(request)
	if not user_id:
		return web.json_response({"error": "Authentication required"}, status=401)
	if not _can_download_models(request):
		return web.json_response({"error": "Model download permission required"}, status=403)
	role, perms, _username = _role_and_perms(request)
	try:
		body = await request.json()
	except Exception as e:
		return web.json_response({"error": f"Invalid JSON: {e}"}, status=400)

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

	store = get_model_source_api_keys_store(USERS_DB_CONFIG)
	token = store.get_key(user_id, source)
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
		model_version_id = (body.get("model_version_id") or body.get("modelVersionId") or "").strip()
		if not model_version_id:
			return web.json_response({"error": "model_version_id required for CivitAI"}, status=400)
		job["model_version_id"] = model_version_id
		job["type"] = body.get("type")
		job["format"] = body.get("format")
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
			return web.json_response({"error": "repo_id and filename required for HuggingFace"}, status=400)
		job["repo_id"] = repo_id
		job["filename"] = filename
		job["subfolder"] = subfolder

	async with _JOBS_LOCK:
		_JOBS_BY_ID[job_id] = job
		_PENDING_JOB_IDS.append(job_id)
		await _schedule_jobs_unsafe()
	return web.json_response({"status": "queued", "job_id": job_id, "stats": _queue_stats()})


@routes.get("/mss-login/api/model-download/jobs")
async def api_model_download_jobs(request: web.Request) -> web.Response:
	"""Return caller-visible jobs and queue stats (privacy-preserving, per-user)."""
	user_id, _ = _current_user_id_and_username(request)
	if not user_id:
		return web.json_response({"error": "Authentication required"}, status=401)
	if not _can_download_models(request):
		return web.json_response({"error": "Model download permission required"}, status=403)
	async with _JOBS_LOCK:
		jobs = [
			_job_public_view(job)
			for job in _JOBS_BY_ID.values()
			if job.get("user_id") == user_id
		]
		jobs.sort(key=lambda item: item.get("created_at", ""), reverse=True)
		return web.json_response({"jobs": jobs, "stats": _queue_stats()})


@routes.post("/mss-login/api/model-download/jobs/{job_id}/cancel")
async def api_model_download_cancel(request: web.Request) -> web.Response:
	"""Cancel a queued/running download job owned by the caller."""
	user_id, _ = _current_user_id_and_username(request)
	if not user_id:
		return web.json_response({"error": "Authentication required"}, status=401)
	if not _can_download_models(request):
		return web.json_response({"error": "Model download permission required"}, status=403)
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
routes.get("/api/mss-login/api/model-download/api-keys")(api_model_download_api_keys_get)
routes.put("/api/mss-login/api/model-download/api-keys")(api_model_download_api_keys_put)
routes.post("/api/mss-login/api/model-download/download")(api_model_download_start)
routes.get("/api/mss-login/api/model-download/jobs")(api_model_download_jobs)
routes.post("/api/mss-login/api/model-download/jobs/{job_id}/cancel")(api_model_download_cancel)
