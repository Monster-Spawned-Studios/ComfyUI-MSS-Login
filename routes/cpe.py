# --- START OF FILE routes/cpe.py ---
"""Comfy Portal Endpoint compatible routes, scoped to the authenticated user.

Registered so Comfy Portal can list/get/save workflows even when
comfy-portal-endpoint is not installed. When that extension is present,
workflow_routes middleware still intercepts these paths so listings come
from the user's MSS-Login workflow directory instead of user/default.

UI→API conversion: prefer embedded converter (Phase 1+); else delegate to a
sibling comfy-portal-endpoint Playwright browser when importable.
"""

from __future__ import annotations

import json
import os

from aiohttp import web

from ..constants import USERS_DB_CONFIG
from ..globals import routes
from ..utils import user_env
from ..utils.cpe_convert import convert_ui_workflow, ui_convert_unavailable_details
from ..utils.cpe_workflows import (
	CPE_CONVERT_PATHS,
	CPE_GET_AND_CONVERT_PATHS,
	CPE_GET_PATHS,
	CPE_HEALTH_PATHS,
	CPE_LIST_PATHS,
	CPE_SAVE_PATHS,
	health_payload,
	list_workflows_payload,
	load_workflow_for_convert,
	looks_like_api_prompt,
	parse_workflow_to_api_prompt,
	read_workflow_text,
	save_workflow_text,
	success_convert_payload,
)
from ..utils.model_visibility_policy import (
	allowed_set_from_grants,
	get_effective_model_grants_for_user,
	user_can_view_all_models,
)
from ..utils.prompt_model_validator import validate_prompt_models
from ..utils.shared_items_store import get_shared_items_store


def _extra_global_dirs() -> list[str]:
	try:
		from .workflow_routes import POTENTIAL_GLOBALS

		return [path for path in POTENTIAL_GLOBALS if path and os.path.isdir(path)]
	except Exception:
		return []


def _current_username(request: web.Request) -> str:
	try:
		from .workflow_routes import get_current_user

		return get_current_user(request) or "guest"
	except Exception:
		return str(request.get("user") or "guest")


def _user_workflow_dir(request: web.Request) -> str:
	return user_env.get_user_workflow_dir(_current_username(request))


def _model_grant_check(request: web.Request, workflow_payload: object) -> web.Response | None:
	"""Return a 403 response if API-format workflow references forbidden models."""
	prompt = parse_workflow_to_api_prompt(workflow_payload)
	if prompt is None:
		# UI-format: cannot validate without conversion; allow storage sync.
		return None
	try:
		from ..globals import access_control

		role, perms, username = access_control._get_user_role_and_permissions(request)
	except Exception:
		return None
	if user_can_view_all_models(role, perms):
		return None
	try:
		users_db = access_control.users_db
		grants = get_effective_model_grants_for_user(
			role=role,
			perms=perms,
			username=username,
			users_db=users_db,
			shared_items_store_getter=get_shared_items_store,
			users_db_config=USERS_DB_CONFIG,
		)
		allowed_set = allowed_set_from_grants(grants)
		valid, err_msg = validate_prompt_models(allowed_set, allow_all=False, prompt=prompt)
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
		return None
	return None


async def _convert_ui_or_error(workflow_data: dict) -> tuple[int, dict]:
	"""Run UI→API conversion; return (status, payload)."""
	try:
		api_prompt = await convert_ui_workflow(workflow_data)
		if not isinstance(api_prompt, dict) or not looks_like_api_prompt(api_prompt):
			return 503, {
				"status": "error",
				"message": "Workflow conversion failed",
				"details": "Converter returned a non-API workflow payload",
			}
		return 200, success_convert_payload(api_prompt)
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


async def handle_cpe_list(request: web.Request) -> web.Response:
	payload = list_workflows_payload(_user_workflow_dir(request), _extra_global_dirs())
	return web.json_response(payload)


async def handle_cpe_get(request: web.Request) -> web.Response:
	filename = request.query.get("filename")
	status, payload = read_workflow_text(
		_user_workflow_dir(request), filename, _extra_global_dirs()
	)
	if status == 200:
		denied = _model_grant_check(request, payload.get("workflow"))
		if denied is not None:
			return denied
	return web.json_response(payload, status=status)


async def handle_cpe_save(request: web.Request) -> web.Response:
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
	workflow = data.get("workflow")
	denied = _model_grant_check(request, workflow)
	if denied is not None:
		return denied
	if isinstance(workflow, (dict, list)):
		workflow_str = json.dumps(workflow)
	else:
		workflow_str = workflow
	name = data.get("name")
	status, payload = save_workflow_text(_user_workflow_dir(request), workflow_str, name)
	if status == 200:
		try:
			from .workflow_routes import _fire_and_forget, _get_workflow_sync, sanitize_name

			wf_sync = _get_workflow_sync()
			if wf_sync is not None:
				clean = sanitize_name(payload.get("filename") or "")
				if clean:
					user = _current_username(request)
					file_path = os.path.join(_user_workflow_dir(request), clean)
					_fire_and_forget(wf_sync.upload_user_workflow, user, clean, file_path)
		except Exception:
			pass
	return web.json_response(payload, status=status)


async def handle_cpe_get_and_convert(request: web.Request) -> web.Response:
	filename = request.query.get("filename")
	status, meta, workflow_data = load_workflow_for_convert(
		_user_workflow_dir(request), filename, _extra_global_dirs()
	)
	if status != 200 or workflow_data is None:
		return web.json_response(meta, status=status)

	filename_out = meta.get("filename") or filename
	if looks_like_api_prompt(workflow_data):
		payload = success_convert_payload(workflow_data, filename=filename_out)
		denied = _model_grant_check(request, workflow_data)
		if denied is not None:
			return denied
		return web.json_response(payload, status=200)

	conv_status, payload = await _convert_ui_or_error(workflow_data)
	if conv_status == 200:
		payload["filename"] = filename_out
		wf = None
		if isinstance(payload.get("data"), dict):
			wf = payload["data"].get("workflow")
		denied = _model_grant_check(request, wf)
		if denied is not None:
			return denied
	return web.json_response(payload, status=conv_status)


async def handle_cpe_convert(request: web.Request) -> web.Response:
	"""POST body = UI-format workflow JSON object (CPE contract)."""
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
		return web.json_response(success_convert_payload(workflow_data), status=200)

	conv_status, payload = await _convert_ui_or_error(workflow_data)
	if conv_status == 200:
		wf = None
		if isinstance(payload.get("data"), dict):
			wf = payload["data"].get("workflow")
		denied = _model_grant_check(request, wf)
		if denied is not None:
			return denied
	return web.json_response(payload, status=conv_status)


async def handle_cpe_health(request: web.Request) -> web.Response:
	return web.json_response(health_payload())


async def dispatch_cpe_request(request: web.Request) -> web.StreamResponse | None:
	"""Intercept CPE paths so per-user workflow storage and convert delegation apply."""
	path = request.path
	method = request.method.upper()
	if path in CPE_HEALTH_PATHS and method == "GET":
		return await handle_cpe_health(request)
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


# --- END OF FILE routes/cpe.py ---
