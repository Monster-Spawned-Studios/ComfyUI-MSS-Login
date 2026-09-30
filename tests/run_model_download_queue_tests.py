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
		return True, "", "model.safetensors"

	def _download_huggingface(*_args, **_kwargs):
		return True, "", "weights.safetensors"

	async def _civitai_search(*_a, **_k):
		return {"items": []}, ""

	async def _civitai_get(*_a, **_k):
		return {}, ""

	async def _civitai_version(*_a, **_k):
		return {}, ""

	download_mod.download_civitai_async = _download_civitai_async
	download_mod.download_huggingface = _download_huggingface
	download_mod.ALLOWED_CIVITAI_HOSTS = frozenset({"civitai.com", "civitai.red"})
	download_mod.DEFAULT_CIVITAI_HOST = "civitai.com"
	download_mod.civitai_get_model = _civitai_get
	download_mod.civitai_get_model_version = _civitai_version
	download_mod.civitai_search_models = _civitai_search
	download_mod.get_civitai_host_preference = lambda *_a, **_k: "civitai.com"
	download_mod.normalize_civitai_host = (
		lambda h: (h or "civitai.com").strip().lower()
		if (h or "").strip().lower() in ("civitai.com", "civitai.red")
		else "civitai.com"
	)
	download_mod.resolve_model_url = lambda *_a, **_k: None
	download_mod.search_huggingface_models = lambda *_a, **_k: ([], "")
	download_mod.set_civitai_host_preference = lambda *_a, **_k: "civitai.com"
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

	class _SharedStore:
		def __init__(self):
			self.grants = []

		def add(self, user_id, folder, item_name, **kwargs):
			self.grants.append(
				{
					"user_id": user_id,
					"folder": folder,
					"item_name": item_name,
					**kwargs,
				}
			)

	_shared = _SharedStore()
	shared_mod = types.ModuleType("mss_login.utils.shared_items_store")
	shared_mod.get_shared_items_store = lambda _cfg: _shared
	sys.modules["mss_login.utils.shared_items_store"] = shared_mod

	s3_mod = types.ModuleType("mss_login.utils.s3_mounter")
	s3_mod.get_mount_manager = lambda: None
	sys.modules["mss_login.utils.s3_mounter"] = s3_mod

	compat_mod = types.ModuleType("mss_login.utils.folder_paths_compat")
	compat_mod.resolve_local_model_destination = lambda folder, uid: (
		os.path.join(_PROJECT_ROOT, "tests", "_tmp_dl", folder),
		os.path.join(_PROJECT_ROOT, "tests", "_tmp_dl"),
	)
	sys.modules["mss_login.utils.folder_paths_compat"] = compat_mod

	return _shared


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

	shared_store = _install_stubs()
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

	print("TestNarrowAutoGrant")
	shared_store.grants.clear()
	# Isolation off: no auto-grant
	mod.experimental_model_isolation_enabled = lambda: False
	mod._auto_grant_downloaded_item(
		target_user_id="uid-alice",
		folder_type="checkpoints",
		saved_rel="only.safetensors",
		destination_type="local",
		granted_by_user_id="uid-alice",
		granted_by_role="user",
	)
	ok(len(shared_store.grants) == 0, "isolation off does not auto-grant")
	# Isolation on: grant only exact item
	mod.experimental_model_isolation_enabled = lambda: True
	mod._auto_grant_downloaded_item(
		target_user_id="uid-alice",
		folder_type="checkpoints",
		saved_rel="only.safetensors",
		destination_type="local",
		granted_by_user_id="uid-alice",
		granted_by_role="user",
	)
	ok(len(shared_store.grants) == 1, "isolation on grants exactly one item")
	ok(
		shared_store.grants[0]["item_name"] == "uid-alice/only.safetensors",
		"grant uses user prefix + exact filename",
	)
	mod.experimental_model_isolation_enabled = lambda: False

	print("TestS3Boto3DestinationResolve")
	mount_root = os.path.join(_PROJECT_ROOT, "tests", "_tmp_s3_mount")
	models_root = os.path.join(mount_root, "models")

	class _Mgr:
		def __init__(self):
			self.models_root = models_root
			self._boto3 = True
			self.uploads = []

		def is_mounted(self):
			return False

		def _in_boto3_mode(self):
			return self._boto3

		def get_models_folder_path(self, folder_type):
			return os.path.join(self.models_root, folder_type)

		def upload_file(self, local_path, s3_key):
			self.uploads.append((local_path, s3_key))
			return {"key": s3_key}

	mgr = _Mgr()
	mod.get_mount_manager = lambda: mgr
	dest, base = mod._resolve_destination_path(
		{"destination_type": "s3", "folder_type": "checkpoints"}, "uid-alice"
	)
	ok(dest == os.path.join(models_root, "checkpoints"), "boto3 mode stages under models root")
	ok(base == os.path.realpath(models_root), "boto3 base_dir is models root")
	os.makedirs(dest, exist_ok=True)
	staged = os.path.join(dest, "new.safetensors")
	with open(staged, "wb") as f:
		f.write(b"x")
	mod._upload_s3_staged_download(dest, "new.safetensors")
	ok(len(mgr.uploads) == 1, "boto3 upload called after staged download")
	ok(
		mgr.uploads[0][1] == "models/checkpoints/new.safetensors",
		"upload key is under models/{folder}/...",
	)
	mod.get_mount_manager = lambda: None

	print()
	if failed:
		print(f"Result: {failed} failed, {run - failed} passed, {run} total")
		sys.exit(1)
	print(f"Result: all {run} tests passed")
	sys.exit(0)


if __name__ == "__main__":
	run_tests()
