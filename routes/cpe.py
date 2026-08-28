# --- START OF FILE routes/cpe.py ---
"""Comfy Portal Endpoint compatible routes, scoped to the authenticated user.

Registered so Comfy Portal can list/get/save workflows even when
comfy-portal-endpoint is not installed. When that extension is present,
workflow_routes middleware still intercepts these paths so listings come
from the user's MSS-Login workflow directory instead of user/default.
"""

from __future__ import annotations

import json
import os

from aiohttp import web

from ..globals import routes
from ..utils import user_env
from ..utils.cpe_workflows import (
	CPE_GET_AND_CONVERT_PATHS,
	CPE_GET_PATHS,
	CPE_HEALTH_PATHS,
	CPE_LIST_PATHS,
	CPE_SAVE_PATHS,
	get_and_convert_payload,
	health_payload,
	list_workflows_payload,
	read_workflow_text,
	save_workflow_text,
)


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


async def handle_cpe_list(request: web.Request) -> web.Response:
	payload = list_workflows_payload(_user_workflow_dir(request), _extra_global_dirs())
	return web.json_response(payload)


async def handle_cpe_get(request: web.Request) -> web.Response:
	filename = request.query.get("filename")
	status, payload = read_workflow_text(
		_user_workflow_dir(request), filename, _extra_global_dirs()
	)
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
	status, payload = get_and_convert_payload(
		_user_workflow_dir(request), filename, _extra_global_dirs()
	)
	return web.json_response(payload, status=status)


async def handle_cpe_health(request: web.Request) -> web.Response:
	return web.json_response(health_payload())


async def dispatch_cpe_request(request: web.Request) -> web.StreamResponse | None:
	"""Intercept CPE paths so per-user workflow storage is used.

	``POST /cpe/workflow/convert`` is left to comfy-portal-endpoint (headless
	browser) when that extension is installed. This dispatcher does not claim it.
	"""
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


@routes.get("/cpe/health")
@routes.get("/api/cpe/health")
async def cpe_health(request: web.Request) -> web.Response:
	return await handle_cpe_health(request)


# --- END OF FILE routes/cpe.py ---
