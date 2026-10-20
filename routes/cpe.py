# --- START OF FILE routes/cpe.py ---
"""Comfy Portal Endpoint compatible routes, scoped to the authenticated user.

Registered so Comfy Portal can list/get/save workflows even when
comfy-portal-endpoint is not installed. When that extension is present,
workflow_routes middleware still intercepts these paths so listings come
from the user's MSS-Login workflow directory instead of user/default.

UI→API conversion: prefer embedded JWT/Bearer-injected Playwright converter;
else delegate to a sibling comfy-portal-endpoint browser when importable.
"""

from __future__ import annotations

import json
import logging
import os
import time

from aiohttp import web

_log = logging.getLogger("mss_login.cpe")

from ..constants import (
	CPE_CONVERT_TOKEN_MINUTES,
	DATA_DIR,
	USERS_DB_CONFIG,
	experimental_model_isolation_enabled,
)
from ..globals import jwt_auth, routes
from ..utils import user_env
from ..utils.cpe_convert import (
	convert_ui_workflow,
	ensure_embedded_convert_registered,
	ui_convert_unavailable_details,
)
from ..utils.cpe_workflows import (
	CPE_CONVERT_PATHS,
	CPE_GET_AND_CONVERT_PATHS,
	CPE_GET_PATHS,
	CPE_HEALTH_PATHS,
	CPE_LIST_PATHS,
	CPE_SAVE_PATHS,
	CPE_STORAGE_PATHS,
	health_payload,
	list_workflows_payload,
	load_workflow_for_convert,
	looks_like_api_prompt,
	parse_workflow_to_api_prompt,
	read_workflow_text,
	save_workflow_text,
	storage_payload,
	success_convert_payload,
)
from ..utils.model_path_display import (
	flatten_prompt_model_names_for_display,
	prompt_has_foreign_uuid_model,
	rewrite_prompt_model_names,
)
from ..utils.model_visibility_policy import (
	allowed_set_from_grants,
	get_effective_model_grants_for_user,
	get_user_id_for_username,
	user_can_view_all_models,
)
from ..utils.prompt_model_validator import validate_prompt_models
from ..utils.shared_items_store import get_shared_items_store

# Register embedded convert early so /cpe/health reflects Playwright presence.
ensure_embedded_convert_registered()


def _current_username(request: web.Request) -> str:
	try:
		from .workflow_routes import get_current_user

		return get_current_user(request) or "guest"
	except Exception:
		return str(request.get("user") or "guest")


def _user_workflow_dir(request: web.Request) -> str:
	return user_env.get_user_workflow_dir(_current_username(request))


def _auth_token_for_convert(request: web.Request) -> str | None:
	"""Token for Playwright: prefer request Bearer/cookie/query; else mint short JWT."""
	token = (jwt_auth.get_token_from_request(request) or "").strip()
	if token:
		return token
	user = request.get("user")
	if not isinstance(user, dict):
		return None
	username = user.get("username")
	user_id = user.get("id")
	if not username or user_id is None:
		return None
	try:
		return jwt_auth.create_access_token(
			{"id": user_id, "username": username}, expire_minutes=CPE_CONVERT_TOKEN_MINUTES
		)
	except Exception:
		return None


def _viewer_for_flatten(request: web.Request) -> tuple[str | None, list[dict]]:
	"""Return (user_id, grants) for outbound plain-name flattening."""
	try:
		from ..globals import access_control

		role, perms, username = access_control._get_user_role_and_permissions(request)
		users_db = access_control.users_db
		user_id = get_user_id_for_username(users_db, username)
		grants = get_effective_model_grants_for_user(
			role=role,
			perms=perms,
			username=username,
			users_db=users_db,
			shared_items_store_getter=get_shared_items_store,
			users_db_config=USERS_DB_CONFIG,
		)
		return user_id, grants
	except Exception:
		return None, []


def _flatten_api_workflow(request: web.Request, workflow: object) -> object:
	"""Deep-copy API prompt with UUID isolation prefixes stripped for Portal clients."""
	if not experimental_model_isolation_enabled():
		return workflow
	if not isinstance(workflow, dict) or not looks_like_api_prompt(workflow):
		return workflow
	user_id, grants = _viewer_for_flatten(request)
	return flatten_prompt_model_names_for_display(workflow, user_id, grants)


def _flatten_convert_payload(request: web.Request, payload: dict) -> dict:
	"""Mutate convert success payload so data.workflow uses plain model filenames."""
	if not isinstance(payload, dict):
		return payload
	data = payload.get("data")
	if not isinstance(data, dict):
		return payload
	wf = data.get("workflow")
	flattened = _flatten_api_workflow(request, wf)
	if flattened is not wf:
		data["workflow"] = flattened
	return payload


def _model_grant_check(
	request: web.Request, workflow_payload: object, *, origin: str | None = None
) -> web.Response | None:
	"""Return a 403 response if API-format workflow references forbidden models.

	For shared/legacy origins, exceptions fail closed. Own files keep fail-open
	on unexpected errors (legacy behaviour).
	"""
	from ..utils.shared_workflows import ORIGIN_LEGACY, ORIGIN_SHARED

	fail_closed = origin in (ORIGIN_SHARED, ORIGIN_LEGACY)
	deny = web.json_response(
		{
			"status": "error",
			"error": "Model not allowed",
			"code": "MODEL_NOT_ALLOWED",
			"message": "Model not allowed",
		},
		status=403,
	)
	prompt = parse_workflow_to_api_prompt(workflow_payload)
	if prompt is None:
		return None
	try:
		from ..globals import access_control

		role, perms, username = access_control._get_user_role_and_permissions(request)
	except Exception:
		return deny if fail_closed else None
	try:
		users_db = access_control.users_db
		user_id = get_user_id_for_username(users_db, username)
		if prompt_has_foreign_uuid_model(prompt, user_id):
			return deny
		if user_can_view_all_models(role, perms):
			return None
		grants = get_effective_model_grants_for_user(
			role=role,
			perms=perms,
			username=username,
			users_db=users_db,
			shared_items_store_getter=get_shared_items_store,
			users_db_config=USERS_DB_CONFIG,
		)
		# Dual-read: normalize own UUID prefix to plain; validate via grant dual-read
		prompt_for_check = json.loads(json.dumps(prompt))
		rewrite_prompt_model_names(prompt_for_check, user_id, grants, normalize_only=True)
		allowed_set = allowed_set_from_grants(grants)
		valid, err_msg = validate_prompt_models(
			allowed_set, allow_all=False, prompt=prompt_for_check
		)
		if not valid:
			return web.json_response(
				{
					"status": "error",
					"error": err_msg or "Model not allowed",
					"code": "MODEL_NOT_ALLOWED",
					"message": err_msg or "Model not allowed",
				},
				status=403,
			)
	except Exception:
		return deny if fail_closed else None
	return None


async def _convert_ui_or_error(
	workflow_data: dict, *, auth_token: str | None, user_id: str | None = None
) -> tuple[int, dict]:
	"""Run UI→API conversion; return (status, payload).

	On success, include additive sanitized ``ui_workflow`` / ``extra_data``.
	Cache fill-in is deferred until after ``_model_grant_check`` succeeds.
	"""
	t_start = time.perf_counter()
	try:
		t_conv0 = time.perf_counter()
		api_prompt = await convert_ui_workflow(workflow_data, auth_token=auth_token)
		t_conv = time.perf_counter() - t_conv0
		if not isinstance(api_prompt, dict) or not looks_like_api_prompt(api_prompt):
			return 503, {
				"status": "error",
				"message": "Workflow conversion failed",
				"details": "Converter returned a non-API workflow payload",
			}
		from ..utils.workflow_metadata import cpe_embed_settings, sanitize_ui_workflow

		cleaned = None
		embed = cpe_embed_settings()
		t_san0 = time.perf_counter()
		if embed["embed_ui_workflow"]:
			cleaned = sanitize_ui_workflow(
				workflow_data, user_id, max_bytes=embed["ui_workflow_max_bytes"]
			)
		t_san = time.perf_counter() - t_san0
		t_total = time.perf_counter() - t_start
		_log.debug(
			"[cpe _convert_ui_or_error] browser_convert=%.3fs sanitize=%.3fs total=%.3fs",
			t_conv,
			t_san,
			t_total,
		)
		return 200, success_convert_payload(api_prompt, ui_workflow=cleaned)
	except RuntimeError as exc:
		return 503, {
			"status": "error",
			"message": "Workflow conversion failed",
			"details": str(exc) or ui_convert_unavailable_details(),
		}
	except Exception as exc:
		return 503, {
			"status": "error",
			"message": "Workflow conversion failed",
			"details": str(exc) or ui_convert_unavailable_details(),
		}


def _user_id_for_request(request: web.Request) -> str | None:
	from ..utils.workflow_metadata import resolve_canonical_user_id

	return resolve_canonical_user_id(request)


def _cache_ui_after_grant(request: web.Request, payload: dict) -> None:
	"""Remember sanitized UI graph for fill-in only after grant check passed."""
	from ..utils.workflow_metadata import (
		build_workflow_cache_key,
		remember_converted_ui_workflow,
		resolve_canonical_user_id,
	)

	data = payload.get("data") if isinstance(payload, dict) else None
	if not isinstance(data, dict):
		return
	api = data.get("workflow")
	ui = data.get("ui_workflow")
	if not isinstance(api, dict) or not isinstance(ui, dict):
		return
	user_id = resolve_canonical_user_id(request)
	cache_key = build_workflow_cache_key(user_id, api)
	if not cache_key:
		return
	remember_converted_ui_workflow(user_id, api, ui)


async def handle_cpe_list(request: web.Request) -> web.Response:
	from ..utils.shared_workflows import log_workflow_action

	user = _current_username(request)
	payload = list_workflows_payload("", username=user)
	log_workflow_action(user, "list", "merged", "*")
	return web.json_response(payload)


async def handle_cpe_get(request: web.Request) -> web.Response:
	from ..utils.shared_workflows import log_workflow_action

	user = _current_username(request)
	filename = request.query.get("filename")
	status, payload = read_workflow_text("", filename, username=user)
	if status == 200:
		denied = _model_grant_check(request, payload.get("workflow"), origin=payload.get("source"))
		if denied is not None:
			return denied
		log_workflow_action(user, "get", str(payload.get("source") or "own"), str(filename or ""))
	return web.json_response(payload, status=status)


async def handle_cpe_save(request: web.Request) -> web.Response:
	from ..utils.shared_workflows import (
		SHARED_READONLY_BODY,
		log_workflow_action,
		save_target_dir,
		sanitize_workflow_rel,
	)

	try:
		data = await request.json()
	except Exception:
		return web.json_response(
			{"status": "error", "message": "workflow field is required"}, status=400
		)
	if not isinstance(data, dict) or "workflow" not in data:
		return web.json_response(
			{"status": "error", "message": "workflow field is required"}, status=400
		)
	user = _current_username(request)
	scope_shared = str(data.get("scope") or "").strip().lower() == "shared"
	target_dir, err = save_target_dir(user, scope_shared=scope_shared)
	if err or not target_dir:
		return web.json_response(SHARED_READONLY_BODY, status=403)
	workflow = data.get("workflow")
	denied = _model_grant_check(request, workflow, origin="own")
	if denied is not None:
		return denied
	if isinstance(workflow, (dict, list)):
		workflow_str = json.dumps(workflow)
	else:
		workflow_str = workflow
	name = data.get("name")
	status, payload = save_workflow_text(target_dir, workflow_str, name)
	if status == 200:
		source = "shared" if scope_shared else "own"
		log_workflow_action(user, "save", source, str(payload.get("filename") or name or ""))
		# S3 sync only for private own-dir saves (never shared for non-owner path)
		if not scope_shared:
			try:
				from .workflow_routes import _fire_and_forget, _get_workflow_sync

				wf_sync = _get_workflow_sync()
				if wf_sync is not None:
					clean = sanitize_workflow_rel(payload.get("filename") or "")
					if clean:
						file_path = os.path.join(target_dir, clean.replace("/", os.sep))
						_fire_and_forget(wf_sync.upload_user_workflow, user, clean, file_path)
			except Exception:
				pass
	return web.json_response(payload, status=status)


async def handle_cpe_get_and_convert(request: web.Request) -> web.Response:
	from ..utils.shared_workflows import log_workflow_action

	t_start = time.perf_counter()
	user = _current_username(request)
	filename = request.query.get("filename")
	t_load0 = time.perf_counter()
	status, meta, workflow_data = load_workflow_for_convert("", filename, username=user)
	t_load = time.perf_counter() - t_load0
	if status != 200 or workflow_data is None:
		return web.json_response(meta, status=status)

	filename_out = meta.get("filename") or filename
	origin = meta.get("source")
	log_workflow_action(user, "get-and-convert", str(origin or "own"), str(filename_out or ""))
	if looks_like_api_prompt(workflow_data):
		denied = _model_grant_check(request, workflow_data, origin=origin)
		if denied is not None:
			return denied
		payload = success_convert_payload(
			_flatten_api_workflow(request, workflow_data), filename=filename_out
		)
		return web.json_response(payload, status=200)

	auth_token = _auth_token_for_convert(request)
	t_conv0 = time.perf_counter()
	conv_status, payload = await _convert_ui_or_error(
		workflow_data, auth_token=auth_token, user_id=_user_id_for_request(request)
	)
	t_conv = time.perf_counter() - t_conv0
	t_grant = 0.0
	t_flatten = 0.0
	t_cache = 0.0
	if conv_status == 200:
		payload["filename"] = filename_out
		wf = None
		if isinstance(payload.get("data"), dict):
			wf = payload["data"].get("workflow")
		t_grant0 = time.perf_counter()
		denied = _model_grant_check(request, wf, origin=origin)
		t_grant = time.perf_counter() - t_grant0
		if denied is not None:
			return denied
		t_flat0 = time.perf_counter()
		_flatten_convert_payload(request, payload)
		t_flatten = time.perf_counter() - t_flat0
		t_cache0 = time.perf_counter()
		_cache_ui_after_grant(request, payload)
		t_cache = time.perf_counter() - t_cache0
	t_total = time.perf_counter() - t_start
	_log.debug(
		"[cpe get-and-convert] load=%.3fs convert=%.3fs grant_check=%.3fs flatten=%.3fs cache=%.3fs total=%.3fs",
		t_load,
		t_conv,
		t_grant,
		t_flatten,
		t_cache,
		t_total,
	)
	return web.json_response(payload, status=conv_status)


async def handle_cpe_convert(request: web.Request) -> web.Response:
	"""POST body = UI-format workflow JSON object (CPE contract)."""
	t_start = time.perf_counter()
	try:
		workflow_data = await request.json()
	except Exception:
		return web.json_response({"status": "error", "message": "Invalid JSON body"}, status=400)
	if not isinstance(workflow_data, dict) or not workflow_data:
		return web.json_response(
			{"status": "error", "message": "Workflow JSON object is required"}, status=400
		)
	if looks_like_api_prompt(workflow_data):
		denied = _model_grant_check(request, workflow_data)
		if denied is not None:
			return denied
		return web.json_response(
			success_convert_payload(_flatten_api_workflow(request, workflow_data)), status=200
		)

	auth_token = _auth_token_for_convert(request)
	t_conv0 = time.perf_counter()
	conv_status, payload = await _convert_ui_or_error(
		workflow_data, auth_token=auth_token, user_id=_user_id_for_request(request)
	)
	t_conv = time.perf_counter() - t_conv0
	t_grant = 0.0
	t_flatten = 0.0
	t_cache = 0.0
	if conv_status == 200:
		wf = None
		if isinstance(payload.get("data"), dict):
			wf = payload["data"].get("workflow")
		t_grant0 = time.perf_counter()
		denied = _model_grant_check(request, wf)
		t_grant = time.perf_counter() - t_grant0
		if denied is not None:
			return denied
		t_flat0 = time.perf_counter()
		_flatten_convert_payload(request, payload)
		t_flatten = time.perf_counter() - t_flat0
		t_cache0 = time.perf_counter()
		_cache_ui_after_grant(request, payload)
		t_cache = time.perf_counter() - t_cache0
	t_total = time.perf_counter() - t_start
	_log.debug(
		"[cpe convert] convert=%.3fs grant_check=%.3fs flatten=%.3fs cache=%.3fs total=%.3fs",
		t_conv,
		t_grant,
		t_flatten,
		t_cache,
		t_total,
	)
	return web.json_response(payload, status=conv_status)


async def handle_cpe_health(request: web.Request) -> web.Response:
	return web.json_response(health_payload())


async def handle_cpe_storage(request: web.Request) -> web.Response:
	"""Disk stats for DATA_DIR (authenticated; same shape as upstream CPE)."""
	payload = storage_payload(DATA_DIR)
	status = 200 if payload.get("status") == "success" else 500
	return web.json_response(payload, status=status)


async def dispatch_cpe_request(request: web.Request) -> web.StreamResponse | None:
	"""Intercept CPE paths so per-user workflow storage and convert delegation apply."""
	path = request.path
	method = request.method.upper()
	if path in CPE_HEALTH_PATHS and method == "GET":
		return await handle_cpe_health(request)
	if path in CPE_STORAGE_PATHS and method == "GET":
		return await handle_cpe_storage(request)
	if path in CPE_LIST_PATHS and method == "GET":
		return await handle_cpe_list(request)
	if path in CPE_GET_PATHS and method == "GET":
		return await handle_cpe_get(request)
	if path in CPE_SAVE_PATHS and method == "POST":
		return await handle_cpe_save(request)
	if path in CPE_GET_AND_CONVERT_PATHS and method == "GET":
		return await handle_cpe_get_and_convert(request)
	if path in CPE_CONVERT_PATHS and method == "POST":
		return await handle_cpe_convert(request)
	return None


@routes.get("/cpe/workflow/list")
@routes.get("/api/cpe/workflow/list")
async def cpe_list_workflows(request: web.Request) -> web.Response:
	return await handle_cpe_list(request)


@routes.get("/cpe/workflow/get")
@routes.get("/api/cpe/workflow/get")
async def cpe_get_workflow(request: web.Request) -> web.Response:
	return await handle_cpe_get(request)


@routes.post("/cpe/workflow/save")
@routes.post("/api/cpe/workflow/save")
async def cpe_save_workflow(request: web.Request) -> web.Response:
	return await handle_cpe_save(request)


@routes.get("/cpe/workflow/get-and-convert")
@routes.get("/api/cpe/workflow/get-and-convert")
async def cpe_get_and_convert(request: web.Request) -> web.Response:
	return await handle_cpe_get_and_convert(request)


@routes.post("/cpe/workflow/convert")
@routes.post("/api/cpe/workflow/convert")
async def cpe_convert_workflow(request: web.Request) -> web.Response:
	return await handle_cpe_convert(request)


@routes.get("/cpe/health")
@routes.get("/api/cpe/health")
async def cpe_health(request: web.Request) -> web.Response:
	return await handle_cpe_health(request)


@routes.get("/cpe/system/storage")
@routes.get("/api/cpe/system/storage")
async def cpe_system_storage(request: web.Request) -> web.Response:
	return await handle_cpe_storage(request)


# --- END OF FILE routes/cpe.py ---
