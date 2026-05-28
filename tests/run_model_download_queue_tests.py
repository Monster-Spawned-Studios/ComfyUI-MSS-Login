"""
Standalone tests for queued model-download helpers and RBAC gating.
"""

import asyncio
import importlib.util
import json
import os
import sys
import types

from aiohttp import web

_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_ROUTES_DIR = os.path.join(_PROJECT_ROOT, "routes")


def _load_module(name: str, path: str, package: str):
	spec = importlib.util.spec_from_file_location(name, path)
	mod = importlib.util.module_from_spec(spec)
	mod.__package__ = package
	assert spec and spec.loader
	spec.loader.exec_module(mod)
	return mod


def _install_stubs():
	root_pkg = types.ModuleType("mss_login")
	root_pkg.__path__ = [_PROJECT_ROOT]
	routes_pkg = types.ModuleType("mss_login.routes")
	routes_pkg.__path__ = [_ROUTES_DIR]
	utils_pkg = types.ModuleType("mss_login.utils")
	utils_pkg.__path__ = [os.path.join(_PROJECT_ROOT, "utils")]
	sys.modules["mss_login"] = root_pkg
	sys.modules["mss_login.routes"] = routes_pkg
	sys.modules["mss_login.utils"] = utils_pkg

	const_mod = types.ModuleType("mss_login.constants")
	const_mod.USERS_DB_CONFIG = {}
	const_mod.experimental_model_isolation_enabled = lambda: False
	const_mod.experimental_s3_enabled = lambda: True
	sys.modules["mss_login.constants"] = const_mod

	class _Routes:
		def get(self, _path):
			return lambda fn: fn

		def post(self, _path):
			return lambda fn: fn

		def put(self, _path):
			return lambda fn: fn

	class _JWT:
		def get_token_from_request(self, request):
			return getattr(request, "_token", None)

		def decode_access_token(self, _token):
			return {"username": "alice"}

	class _UsersDb:
		def get_user(self, username=None):
			if username == "alice":
				return "uid-alice", {"groups": ["user"], "admin": False}
			return None, None

	class _Logger:
		def error(self, *_args, **_kwargs):
			return None

		def warning(self, *_args, **_kwargs):
			return None

		def info(self, *_args, **_kwargs):
			return None

	globals_mod = types.ModuleType("mss_login.globals")
	globals_mod.jwt_auth = _JWT()
	globals_mod.logger = _Logger()
	globals_mod.routes = _Routes()
	globals_mod.users_db = _UsersDb()
	sys.modules["mss_login.globals"] = globals_mod

	class _Cache:
		def list_folders(self):
			return ["checkpoints", "loras"]

	cache_mod = types.ModuleType("mss_login.utils.model_cache")
	cache_mod.get_model_cache = lambda _cfg: _Cache()
	cache_mod.ASSET_FOLDERS_FALLBACK = frozenset({"checkpoints", "loras"})
	sys.modules["mss_login.utils.model_cache"] = cache_mod

	download_mod = types.ModuleType("mss_login.utils.model_download")

	async def _download_civitai_async(*_args, **_kwargs):
		return True, ""

	def _download_huggingface(*_args, **_kwargs):
		return True, ""

	download_mod.download_civitai_async = _download_civitai_async
	download_mod.download_huggingface = _download_huggingface
	sys.modules["mss_login.utils.model_download"] = download_mod

	isolation_mod = types.ModuleType("mss_login.utils.model_isolation")
	isolation_mod.maybe_isolated_destination = lambda *_args, **_kwargs: None
	isolation_mod.sanitize_user_segment = lambda x: x
	sys.modules["mss_login.utils.model_isolation"] = isolation_mod

	keys_mod = types.ModuleType("mss_login.utils.model_source_api_keys_store")
	keys_mod.SOURCES = ("civitai", "huggingface")

	class _Store:
		def list_sources_with_keys(self, _user_id):
			return []

		def get_key(self, _user_id, _source):
			return "token"

		def set_key(self, _user_id, _source, _key):
			return True

		def delete_key(self, _user_id, _source):
			return True

	keys_mod.get_model_source_api_keys_store = lambda _cfg: _Store()
	sys.modules["mss_login.utils.model_source_api_keys_store"] = keys_mod

	policy_mod = types.ModuleType("mss_login.utils.model_visibility_policy")
	policy_mod.user_can_download_models = lambda role, perms: perms.get(
		"can_download_models", False
	)
	policy_mod.user_can_manage_model_sharing = lambda role, perms: False
	sys.modules["mss_login.utils.model_visibility_policy"] = policy_mod

	shared_mod = types.ModuleType("mss_login.utils.shared_items_store")
	shared_mod.get_shared_items_store = lambda _cfg: None
	sys.modules["mss_login.utils.shared_items_store"] = shared_mod


class _Req:
	def __init__(self, token="token", job_id=""):
		self._token = token
		self.match_info = {"job_id": job_id} if job_id else {}


def run_tests():
	failed = 0
	run = 0

	def ok(cond, msg):
		nonlocal failed, run
		run += 1
		if not cond:
			print(f"  FAIL: {msg}")
			failed += 1
		else:
			print(f"  ok: {msg}")
		return cond

	_install_stubs()
	mod = _load_module(
		"mss_login.routes.model_download",
		os.path.join(_ROUTES_DIR, "model_download.py"),
		"mss_login.routes",
	)

	print("TestQueueCaps")
	mod._RUNNING_JOB_IDS.clear()
	mod._RUNNING_BY_PROVIDER["civitai"] = 0
	mod._RUNNING_BY_PROVIDER["huggingface"] = 0
	ok(mod._can_start_job_unsafe("civitai") is True, "civitai starts when capacity is available")
	mod._RUNNING_BY_PROVIDER["civitai"] = mod.MAX_CIVITAI_DOWNLOADS
	ok(mod._can_start_job_unsafe("civitai") is False, "civitai provider cap is enforced")
	mod._RUNNING_BY_PROVIDER["civitai"] = 0
	mod._RUNNING_JOB_IDS.update({"a", "b", "c", "d", "e"})
	ok(mod._can_start_job_unsafe("huggingface") is False, "global cap is enforced")
	mod._RUNNING_JOB_IDS.clear()

	print("TestPermissionGating")
	mod._role_and_perms = lambda _request: ("user", {"can_download_models": False}, "alice")
	resp = asyncio.run(mod.api_model_download_jobs(_Req()))
	ok(isinstance(resp, web.Response), "jobs endpoint returns response object")
	ok(resp.status == 403, "jobs endpoint denies users without can_download_models")
	mod._role_and_perms = lambda _request: ("user", {"can_download_models": True}, "alice")
	resp = asyncio.run(mod.api_model_download_jobs(_Req()))
	ok(resp.status == 200, "jobs endpoint allows users with can_download_models")

	print("TestCancelRunningJob")
	job_id = "job-running"
	mod._JOBS_BY_ID[job_id] = {"job_id": job_id, "user_id": "uid-alice", "status": "running"}
	resp = asyncio.run(mod.api_model_download_cancel(_Req(job_id=job_id)))
	ok(resp.status == 400, "running job cancel is rejected safely")
	mod._JOBS_BY_ID.clear()

	print("TestMobileClientEndpoints")
	mod._role_and_perms = lambda _request: ("user", {"can_download_models": True}, "alice")
	resp = asyncio.run(mod.api_model_download_sources(_Req()))
	ok(resp.status == 200, "sources endpoint ok for permitted user")
	body = json.loads(resp.text)
	ok("capabilities" in body, "sources includes capabilities for mobile bootstrap")
	resp = asyncio.run(mod.api_model_download_folders(_Req()))
	ok(resp.status == 200, "folders endpoint ok")
	folders_body = json.loads(resp.text)
	ok("checkpoints" in folders_body.get("folders", []), "folders lists checkpoint type")
	job_id = "job-poll"
	mod._JOBS_BY_ID[job_id] = {
		"job_id": job_id,
		"user_id": "uid-alice",
		"source": "civitai",
		"status": "queued",
		"model_version_id": "99",
		"bytes_done": 0,
		"progress_pct": 0.0,
		"elapsed": 0.0,
		"speed_bps": 0.0,
		"eta_seconds": 0.0,
		"destination": "",
		"error": "",
	}
	resp = asyncio.run(mod.api_model_download_job_get(_Req(job_id=job_id)))
	ok(resp.status == 200, "single job GET ok for owner")
	job_body = json.loads(resp.text)
	ok(job_body.get("job", {}).get("job_id") == job_id, "job payload returned")
	resp = asyncio.run(mod.api_model_download_job_get(_Req(job_id="missing")))
	ok(resp.status == 404, "unknown job returns 404")

	print()
	if failed:
		print(f"Result: {failed} failed, {run - failed} passed, {run} total")
		sys.exit(1)
	print(f"Result: all {run} tests passed")
	sys.exit(0)


if __name__ == "__main__":
	run_tests()
