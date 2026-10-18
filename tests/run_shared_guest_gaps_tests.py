"""
Shared-guest gap coverage: web handlers, grants, byte precedence, Manager smoke.
No ComfyUI install required.
"""

from __future__ import annotations

import asyncio
import importlib.util
import json
import os
import sys
import tempfile
import types

_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_ROUTES = os.path.join(_PROJECT_ROOT, "routes")
_UTILS = os.path.join(_PROJECT_ROOT, "utils")


def ok(cond: bool, msg: str) -> None:
	if not cond:
		raise AssertionError(msg)
	print(f"  OK: {msg}")


def _load(name: str, path: str, package: str | None = None):
	spec = importlib.util.spec_from_file_location(name, path)
	mod = importlib.util.module_from_spec(spec)
	if package:
		mod.__package__ = package
	assert spec and spec.loader
	sys.modules[name] = mod
	spec.loader.exec_module(mod)
	return mod


def _ensure_shared_workflows():
	root = types.ModuleType("mss_login")
	root.__path__ = [_PROJECT_ROOT]
	utils = types.ModuleType("mss_login.utils")
	utils.__path__ = [_UTILS]
	sys.modules["mss_login"] = root
	sys.modules["mss_login.utils"] = utils
	_load("mss_login.utils.path_safety", os.path.join(_UTILS, "path_safety.py"), "mss_login.utils")
	_load(
		"mss_login.utils.cpe_workflows", os.path.join(_UTILS, "cpe_workflows.py"), "mss_login.utils"
	)
	sys.modules.pop("mss_login.utils.shared_workflows", None)
	return _load(
		"mss_login.utils.shared_workflows",
		os.path.join(_UTILS, "shared_workflows.py"),
		"mss_login.utils",
	)


def _write_bytes(path: str, data: bytes) -> None:
	os.makedirs(os.path.dirname(path), exist_ok=True)
	with open(path, "wb") as handle:
		handle.write(data)


def _write_json(path: str, content: dict) -> None:
	os.makedirs(os.path.dirname(path), exist_ok=True)
	with open(path, "w", encoding="utf-8") as handle:
		json.dump(content, handle)


def _bind(sw, dirs, *, include_legacy: bool = False):
	sw.reset_test_overrides()
	sw.set_test_overrides(
		settings={
			"shared_user": "guest",
			"shared_enabled": True,
			"guest_can_write_shared": False,
			"include_legacy_globals": include_legacy,
			"web_hide_api_format": True,
			"list_shared_metadata": True,
		},
		owner_username="mss_admin",
		workflow_dir_fn=lambda u: dirs.get(u),
		legacy_dirs=[dirs["legacy"]] if include_legacy else [],
		guest_enabled=True,
	)


# --- Fake aiohttp / request helpers for workflow_routes ---


class _FakeResp:
	def __init__(self, *, status=200, body=None, text=None, path=None):
		self.status = status
		self.body = body
		self.text = text
		self.path = path


class _FakeRequest:
	def __init__(self, *, user="bob", query=None, json_body=None, method="GET"):
		self._user = user
		self.query = query or {}
		self._json_body = json_body if json_body is not None else {}
		self.method = method
		self.path = "/api/userdata"
		self.cookies = {}
		self.headers = {}

	async def json(self):
		return self._json_body


def _install_workflow_route_stubs(sw_mod):
	root = sys.modules["mss_login"]
	routes_pkg = types.ModuleType("mss_login.routes")
	routes_pkg.__path__ = [_ROUTES]
	sys.modules["mss_login.routes"] = routes_pkg

	globals_mod = types.ModuleType("mss_login.globals")
	globals_mod.jwt_auth = types.SimpleNamespace(get_token_from_request=lambda _r: None)
	globals_mod.current_username_var = types.SimpleNamespace(set=lambda *_: None, get=lambda: None)
	globals_mod.users_db = types.SimpleNamespace(get_user=lambda *_a, **_k: (None, None))
	sys.modules["mss_login.globals"] = globals_mod

	user_env_mod = types.ModuleType("mss_login.utils.user_env")
	sys.modules["mss_login.utils.user_env"] = user_env_mod

	sfw_pkg = types.ModuleType("mss_login.utils.sfw_intercept")
	sfw_pkg.__path__ = [os.path.join(_UTILS, "sfw_intercept")]
	sys.modules["mss_login.utils.sfw_intercept"] = sfw_pkg
	sfw_mod = types.ModuleType("mss_login.utils.sfw_intercept.nsfw_guard")
	sfw_mod.should_block_image_for_current_user = lambda _p: False
	sys.modules["mss_login.utils.sfw_intercept.nsfw_guard"] = sfw_mod

	# Prefer the real shared_workflows already loaded
	sys.modules["mss_login.utils.shared_workflows"] = sw_mod

	folder_paths_mod = types.ModuleType("folder_paths")
	folder_paths_mod.base_path = _PROJECT_ROOT
	folder_paths_mod.get_output_directory = lambda: _PROJECT_ROOT
	sys.modules["folder_paths"] = folder_paths_mod

	def json_response(data=None, status=200, **_k):
		return _FakeResp(status=status, body=data)

	def Response(*, status=200, text=""):
		return _FakeResp(status=status, text=text)

	class FileResponse:
		def __init__(self, path):
			self.status = 200
			self.path = path
			self.body = None
			self.text = None

	aiohttp = types.ModuleType("aiohttp")
	aiohttp.web = types.SimpleNamespace(
		middleware=lambda f: f,
		json_response=json_response,
		Response=Response,
		FileResponse=FileResponse,
		Request=object,
		StreamResponse=object,
	)
	sys.modules["aiohttp"] = aiohttp

	sys.modules.pop("mss_login.routes.workflow_routes", None)
	mod = _load(
		"mss_login.routes.workflow_routes",
		os.path.join(_ROUTES, "workflow_routes.py"),
		"mss_login.routes",
	)
	return mod


def test_byte_level_precedence():
	print("TestByteLevelPrecedence")
	sw = _ensure_shared_workflows()
	with tempfile.TemporaryDirectory() as tmp:
		dirs = {
			"guest": os.path.join(tmp, "workflows", "guest"),
			"bob": os.path.join(tmp, "workflows", "bob"),
			"mss_admin": os.path.join(tmp, "workflows", "mss_admin"),
			"legacy": os.path.join(tmp, "legacy_globals"),
		}
		for d in dirs.values():
			os.makedirs(d, exist_ok=True)

		rel = "3D/local/40_hy3d2mini_i2m_stl_ui.json"
		own_bytes = b'{"tag":"OWN-BYTES-UNIQUE"}'
		guest_bytes = b'{"tag":"GUEST-BYTES-UNIQUE"}'
		legacy_bytes = b'{"tag":"LEGACY-BYTES-UNIQUE"}'
		_write_bytes(os.path.join(dirs["bob"], rel.replace("/", os.sep)), own_bytes)
		_write_bytes(os.path.join(dirs["guest"], rel.replace("/", os.sep)), guest_bytes)
		_write_bytes(os.path.join(dirs["legacy"], rel.replace("/", os.sep)), legacy_bytes)

		_bind(sw, dirs, include_legacy=True)
		resolved = sw.resolve_workflow("bob", rel)
		ok(resolved is not None, "triple-overlap resolves")
		assert resolved is not None
		with open(resolved[0], "rb") as handle:
			got = handle.read()
		ok(got == own_bytes, "own exact bytes beat guest and legacy")
		ok(resolved[1] == "own", "origin own")

		# Remove own — guest must beat legacy
		os.remove(os.path.join(dirs["bob"], rel.replace("/", os.sep)))
		resolved2 = sw.resolve_workflow("bob", rel)
		ok(resolved2 is not None, "guest+legacy resolves")
		assert resolved2 is not None
		with open(resolved2[0], "rb") as handle:
			got2 = handle.read()
		ok(got2 == guest_bytes, "guest exact bytes beat legacy")
		ok(resolved2[1] == "shared", "origin shared")

		_bind(sw, dirs, include_legacy=False)
		# Without legacy flag, guest still wins; legacy alone must not appear when guest gone
		os.remove(os.path.join(dirs["guest"], rel.replace("/", os.sep)))
		resolved3 = sw.resolve_workflow("bob", rel)
		ok(resolved3 is None, "legacy hidden when include_legacy_globals false")

		_bind(sw, dirs, include_legacy=True)
		resolved4 = sw.resolve_workflow("bob", rel)
		ok(resolved4 is not None, "legacy resolves when enabled")
		assert resolved4 is not None
		with open(resolved4[0], "rb") as handle:
			got4 = handle.read()
		ok(got4 == legacy_bytes, "legacy exact bytes when only legacy remains")
		ok(resolved4[1] == "legacy", "origin legacy")


def test_web_handlers_subfolder_and_readonly():
	print("TestWebHandlersSubfolderAndReadonly")
	sw = _ensure_shared_workflows()
	with tempfile.TemporaryDirectory() as tmp:
		dirs = {
			"guest": os.path.join(tmp, "workflows", "guest"),
			"bob": os.path.join(tmp, "workflows", "bob"),
			"mss_admin": os.path.join(tmp, "workflows", "mss_admin"),
			"legacy": os.path.join(tmp, "legacy_globals"),
		}
		for d in dirs.values():
			os.makedirs(d, exist_ok=True)
		rel = "3D/local/40_hy3d2mini_i2m_stl_ui.json"
		shared_body = {"nodes": [{"id": "shared-3d"}], "links": []}
		_write_json(os.path.join(dirs["guest"], rel.replace("/", os.sep)), shared_body)
		_bind(sw, dirs)

		wr = _install_workflow_route_stubs(sw)
		wr.get_current_user = lambda _req: "bob"

		async def _run():
			# GET shared subfolder
			req = _FakeRequest(user="bob")
			resp = await wr.get_workflow_content(req, rel)
			ok(resp.status == 200, "GET shared subfolder 200")
			ok(os.path.isfile(resp.path), "GET returns file path")
			with open(resp.path, encoding="utf-8") as handle:
				data = json.load(handle)
			ok(data["nodes"][0]["id"] == "shared-3d", "GET serves shared content")

			# Guest saving shared → 403 SHARED_READONLY
			wr.get_current_user = lambda _req: "guest"
			save_req = _FakeRequest(
				user="guest",
				query={"file": "hack.json"},
				json_body={"nodes": [], "links": []},
				method="POST",
			)
			save_resp = await wr.save_workflow(save_req)
			ok(save_resp.status == 403, "guest save 403")
			ok(
				isinstance(save_resp.body, dict)
				and save_resp.body.get("code") == "SHARED_READONLY",
				"guest save SHARED_READONLY",
			)

			# Non-owner deleting shared → 403
			wr.get_current_user = lambda _req: "bob"
			del_resp = await wr.delete_workflow(_FakeRequest(user="bob"), rel)
			ok(del_resp.status == 403, "bob delete shared 403")
			ok(
				isinstance(del_resp.body, dict) and del_resp.body.get("code") == "SHARED_READONLY",
				"bob delete SHARED_READONLY",
			)
			ok(
				os.path.isfile(os.path.join(dirs["guest"], rel.replace("/", os.sep))),
				"shared file still on disk",
			)

			# Bob can save to own dir with subfolder
			own_rel = "3D/local/my_own.json"
			wr.get_current_user = lambda _req: "bob"
			own_save = _FakeRequest(
				user="bob",
				query={"file": own_rel},
				json_body={"nodes": [{"id": "own"}], "links": []},
				method="POST",
			)
			own_resp = await wr.save_workflow(own_save)
			ok(own_resp.status == 200, "bob own subfolder save 200")
			ok(
				os.path.isfile(os.path.join(dirs["bob"], own_rel.replace("/", os.sep))),
				"own subfolder file written",
			)

			# Traversal rejected on GET
			for bad in ("../mss_admin/x.json", "/etc/passwd", "%2e%2e/x.json"):
				bad_resp = await wr.get_workflow_content(_FakeRequest(user="bob"), bad)
				ok(bad_resp.status in (400, 404), f"GET rejects {bad!r} status={bad_resp.status}")

			# Symlink escape on GET
			link = os.path.join(dirs["guest"], "leak.json")
			target = os.path.join(dirs["mss_admin"], "secret.json")
			_write_json(target, {"secret": True})
			try:
				os.symlink(target, link)
			except OSError:
				print("  SKIP: symlink not supported")
			else:
				leak_resp = await wr.get_workflow_content(_FakeRequest(user="bob"), "leak.json")
				ok(
					leak_resp.status == 404
					or (
						getattr(leak_resp, "path", None)
						and not os.path.samefile(leak_resp.path, target)
					),
					"GET refuses escaping symlink",
				)

		asyncio.run(_run())


def test_model_not_allowed_grants():
	print("TestModelNotAllowedGrants")
	with tempfile.TemporaryDirectory() as tmp_data:
		# Clear prior mss_login imports so constants stub wins
		for key in list(sys.modules):
			if key == "mss_login" or key.startswith("mss_login."):
				del sys.modules[key]

		root = types.ModuleType("mss_login")
		root.__path__ = [_PROJECT_ROOT]
		utils = types.ModuleType("mss_login.utils")
		utils.__path__ = [_UTILS]
		sys.modules["mss_login"] = root
		sys.modules["mss_login.utils"] = utils

		const_mod = types.ModuleType("mss_login.constants")
		const_mod.DATA_DIR = tmp_data
		const_mod.CONFIG_FILE_PATH = os.path.join(tmp_data, "config.json")
		const_mod.CURRENT_DIR = _PROJECT_ROOT
		const_mod.USERS_DB_CONFIG = {}
		const_mod.experimental_model_isolation_enabled = lambda: True
		sys.modules["mss_login.constants"] = const_mod

		if "folder_paths" not in sys.modules:
			sys.modules["folder_paths"] = types.ModuleType("folder_paths")

		_load(
			"mss_login.utils.path_safety", os.path.join(_UTILS, "path_safety.py"), "mss_login.utils"
		)
		_load(
			"mss_login.utils.model_visibility_policy",
			os.path.join(_UTILS, "model_visibility_policy.py"),
			"mss_login.utils",
		)
		pmv = _load(
			"mss_login.utils.prompt_model_validator",
			os.path.join(_UTILS, "prompt_model_validator.py"),
			"mss_login.utils",
		)

		_run_model_grant_cases(pmv)


def _run_model_grant_cases(pmv) -> None:

	# Lightweight stand-in for _model_grant_check fail-closed semantics used by CPE
	ORIGIN_SHARED = "shared"
	ORIGIN_LEGACY = "legacy"
	ORIGIN_OWN = "own"

	def grant_check(prompt: dict, *, origin: str, grants: set[tuple[str, str]], boom: bool = False):
		"""Mirror cpe._model_grant_check grant decision without aiohttp/ComfyUI."""
		fail_closed = origin in (ORIGIN_SHARED, ORIGIN_LEGACY)
		deny = {"code": "MODEL_NOT_ALLOWED", "status": 403}
		try:
			if boom:
				raise RuntimeError("simulated")
			valid, err = pmv.validate_prompt_models(grants, allow_all=False, prompt=prompt)
			if not valid:
				return {"code": "MODEL_NOT_ALLOWED", "status": 403, "error": err}
			return None
		except Exception:
			return deny if fail_closed else None

	lora_prompt = {
		"1": {"class_type": "LoraLoader", "inputs": {"lora_name": "secret_lora.safetensors"}}
	}
	# Non-owner / new user: empty grants → denied for shared
	for user_label in ("bob-non-owner", "brand_new_user"):
		denied = grant_check(lora_prompt, origin=ORIGIN_SHARED, grants=set())
		ok(
			denied is not None and denied["code"] == "MODEL_NOT_ALLOWED",
			f"{user_label} shared ungranted LoRA → MODEL_NOT_ALLOWED",
		)

	# After explicit grant through granular permissions → allowed
	granted = {("loras", "secret_lora.safetensors")}
	allowed = grant_check(lora_prompt, origin=ORIGIN_SHARED, grants=granted)
	ok(allowed is None, "shared allowed after explicit LoRA grant")

	# Legacy origin also fail-closed on exception
	boom_shared = grant_check(lora_prompt, origin=ORIGIN_LEGACY, grants=granted, boom=True)
	ok(
		boom_shared is not None and boom_shared["code"] == "MODEL_NOT_ALLOWED",
		"legacy fail-closed on exception",
	)

	# Own origin fail-open on exception (preserve existing behaviour)
	boom_own = grant_check(lora_prompt, origin=ORIGIN_OWN, grants=set(), boom=True)
	ok(boom_own is None, "own fail-open on unexpected exception")

	# New user still denied for ungranted checkpoint on shared
	ckpt_prompt = {
		"1": {
			"class_type": "CheckpointLoaderSimple",
			"inputs": {"ckpt_name": "ungranted_ckpt.safetensors"},
		}
	}
	denied_ckpt = grant_check(ckpt_prompt, origin=ORIGIN_SHARED, grants=set())
	ok(
		denied_ckpt is not None and denied_ckpt["code"] == "MODEL_NOT_ALLOWED",
		"new user shared ungranted checkpoint → MODEL_NOT_ALLOWED",
	)
	ok(
		grant_check(
			ckpt_prompt,
			origin=ORIGIN_SHARED,
			grants={("checkpoints", "ungranted_ckpt.safetensors")},
		)
		is None,
		"shared allowed after explicit checkpoint grant",
	)


def test_manager_smoke():
	print("TestManagerSmoke")
	# Reuse access_control middleware stubs pattern
	root = types.ModuleType("mss_login")
	root.__path__ = [_PROJECT_ROOT]
	utils = types.ModuleType("mss_login.utils")
	utils.__path__ = [_UTILS]
	sys.modules["mss_login"] = root
	sys.modules["mss_login.utils"] = utils

	for name in ("folder_paths", "execution", "server"):
		sys.modules[name] = types.ModuleType(name)
	sys.modules["folder_paths"].get_output_directory = lambda: "/tmp"
	sys.modules["folder_paths"].get_temp_directory = lambda: "/tmp"
	sys.modules["folder_paths"].get_input_directory = lambda: "/tmp"
	sys.modules["execution"].MAXIMUM_HISTORY_SIZE = 10
	sys.modules["execution"].PromptQueue = object
	sys.modules["server"].PromptServer = object

	def json_response(data=None, status=200, **_k):
		return _FakeResp(status=status, body=data)

	def Response(*, status=200, text=""):
		return _FakeResp(status=status, text=text)

	aiohttp = types.ModuleType("aiohttp")
	aiohttp.web = types.SimpleNamespace(
		middleware=lambda fn: fn, json_response=json_response, Response=Response, Request=object
	)
	sys.modules["aiohttp"] = aiohttp

	api_tok = types.ModuleType("mss_login.utils.api_token_store")
	api_tok.get_api_token_store = lambda *_a, **_k: types.SimpleNamespace(
		get_user_for_token=lambda *_: None
	)
	sys.modules["mss_login.utils.api_token_store"] = api_tok
	data_dir = types.ModuleType("mss_login.utils.data_dir")
	data_dir.get_data_subdir = lambda *_a, **_k: "/tmp"
	sys.modules["mss_login.utils.data_dir"] = data_dir
	debug = types.ModuleType("mss_login.utils.debug_log")
	debug.debug_write = lambda *_a, **_k: None
	sys.modules["mss_login.utils.debug_log"] = debug
	iso = types.ModuleType("mss_login.utils.user_isolation")
	iso.safe_user_dir_segment = lambda x: str(x or "x")
	iso.user_id_from_queue_item = lambda e: None
	iso.username_from_queue_item = lambda e: None
	sys.modules["mss_login.utils.user_isolation"] = iso
	users_db_mod = types.ModuleType("mss_login.utils.users_db")
	users_db_mod.UsersDB = object
	sys.modules["mss_login.utils.users_db"] = users_db_mod
	cpe = types.ModuleType("mss_login.utils.cpe_workflows")
	cpe.cpe_path_requires_modify_workflows = lambda _p, _m: False
	sys.modules["mss_login.utils.cpe_workflows"] = cpe

	sys.modules.pop("mss_login.utils.access_control", None)
	ac = _load(
		"mss_login.utils.access_control",
		os.path.join(_UTILS, "access_control.py"),
		"mss_login.utils",
	)

	ok("/api/manager" in ac.EXTENSION_BLOCK_MAP["can_access_manager"], "map includes /api/manager")
	ok("/manager" in ac.EXTENSION_BLOCK_MAP["can_access_manager"], "map includes /manager")
	ok(
		"/extensions/comfyui-manager" in ac.EXTENSION_BLOCK_MAP["can_access_manager"],
		"map includes extensions path",
	)

	tmpdir = tempfile.mkdtemp()
	groups_path = os.path.join(tmpdir, "groups.json")
	with open(groups_path, "w", encoding="utf-8") as handle:
		json.dump({"user": {}}, handle)

	ctrl = ac.AccessControl.__new__(ac.AccessControl)
	ctrl.users_db = types.SimpleNamespace(
		get_user=lambda username=None, user_id=None, **_k: (
			("uid", {"username": username or "alice", "groups": ["user"]})
			if (username or user_id)
			else (None, None)
		)
	)
	ctrl.groups_config_file = groups_path
	ctrl.api_token_store_config = {}
	ctrl.server = types.SimpleNamespace()

	async def _call(mw, path: str):
		called = {"n": 0}

		async def handler(_req):
			called["n"] += 1
			return _FakeResp(status=200, body={"ok": True})

		req = types.SimpleNamespace(
			path=path, method="GET", cookies={}, headers={}, rel_url=types.SimpleNamespace(query={})
		)
		resp = await mw(req, handler)
		return resp, called["n"]

	async def _run():
		# Denied: can_access_manager False — MSS blocks Manager paths
		ctrl._get_user_role_and_permissions = lambda _req: (
			"user",
			{
				"can_access_api": True,
				"can_access_manager": False,
				"can_run": True,
				"can_upload": True,
			},
			"alice",
		)
		mw = ctrl.create_mss_login_middleware()
		for path in ("/api/manager/queue", "/manager", "/extensions/comfyui-manager/index.js"):
			resp, n = await _call(mw, path)
			ok(resp.status == 403 and n == 0, f"denied blocks {path}")

		# Permitted: can_access_manager True — handler runs (MSS does not alter beyond auth)
		ctrl._get_user_role_and_permissions = lambda _req: (
			"user",
			{
				"can_access_api": True,
				"can_access_manager": True,
				"can_run": True,
				"can_upload": True,
			},
			"alice",
		)
		mw2 = ctrl.create_mss_login_middleware()
		for path in ("/api/manager/queue", "/manager", "/extensions/comfyui-manager/index.js"):
			resp, n = await _call(mw2, path)
			ok(resp.status == 200 and n == 1, f"permitted allows {path}")

	asyncio.run(_run())


def run_tests() -> int:
	try:
		test_byte_level_precedence()
		test_web_handlers_subfolder_and_readonly()
		test_model_not_allowed_grants()
		test_manager_smoke()
	except Exception as e:
		print(f"FAIL: {e}")
		import traceback

		traceback.print_exc()
		return 1
	finally:
		try:
			sw = sys.modules.get("mss_login.utils.shared_workflows")
			if sw and hasattr(sw, "reset_test_overrides"):
				sw.reset_test_overrides()
		except Exception:
			pass
	print("All shared-guest gap tests passed.")
	return 0


def main() -> int:
	return run_tests()


if __name__ == "__main__":
	sys.exit(main())
