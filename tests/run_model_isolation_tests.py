r"""
Standalone tests for model isolation policy helpers (no ComfyUI deps).
"""

import importlib.util
import json
import os
import sys
import types

_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_UTILS_DIR = os.path.join(_PROJECT_ROOT, "utils")
_TMP_CONFIG = os.path.join(_PROJECT_ROOT, ".tmp_model_isolation_test_config.json")


def _install_fake_constants(enabled: bool):
	const_mod = types.ModuleType("mss_login.constants")
	const_mod.DATA_DIR = os.path.join(_PROJECT_ROOT, ".tmp_test_data")
	const_mod.CONFIG_FILE_PATH = _TMP_CONFIG

	def _exp():
		return enabled

	const_mod.experimental_model_isolation_enabled = _exp
	sys.modules["mss_login.constants"] = const_mod


def _load_module(name: str, path: str, package: str):
	spec = importlib.util.spec_from_file_location(name, path)
	mod = importlib.util.module_from_spec(spec)
	mod.__package__ = package
	assert spec and spec.loader
	spec.loader.exec_module(mod)
	return mod


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

	# Package stubs for relative imports.
	root_pkg = types.ModuleType("mss_login")
	root_pkg.__path__ = [_PROJECT_ROOT]
	utils_pkg = types.ModuleType("mss_login.utils")
	utils_pkg.__path__ = [_UTILS_DIR]
	sys.modules["mss_login"] = root_pkg
	sys.modules["mss_login.utils"] = utils_pkg

	json_utils_mod = types.ModuleType("mss_login.utils.json_utils")

	def _load_json_file(path, fallback=None):
		try:
			with open(path, "r", encoding="utf-8") as f:
				return json.load(f)
		except Exception:
			return fallback if fallback is not None else {}

	def _save_json_file(path, payload):
		with open(path, "w", encoding="utf-8") as f:
			json.dump(payload, f)

	json_utils_mod.load_json_file = _load_json_file
	json_utils_mod.save_json_file = _save_json_file
	sys.modules["mss_login.utils.json_utils"] = json_utils_mod

	with open(_TMP_CONFIG, "w", encoding="utf-8") as f:
		json.dump({"model_isolation": {"download_redirect_patterns": ["/owner-custom"]}}, f)

	print("TestModelVisibilityPolicy")
	_install_fake_constants(enabled=True)
	policy = _load_module(
		"mss_login.utils.model_visibility_policy",
		os.path.join(_UTILS_DIR, "model_visibility_policy.py"),
		"mss_login.utils",
	)

	ok(policy.user_can_manage_model_sharing("owner", {}) is True, "owner can share by default")
	ok(
		policy.user_can_manage_model_sharing("admin", {"can_manage_model_sharing": True}) is True,
		"admin can share with explicit permission",
	)
	ok(
		policy.user_can_manage_model_sharing("admin", {"can_manage_model_sharing": False}) is False,
		"admin cannot share without permission",
	)
	ok(
		policy.normalize_model_name("UserA\\Model.SAFETENSORS") == "usera/model.safetensors",
		"model names are normalized backend-agnostically",
	)

	grants = [
		{"folder": "checkpoints", "item_name": "alice/model-a.safetensors"},
		{"folder": "checkpoints", "item_name": "bob/model-b.safetensors"},
	]
	filtered = policy.filter_items_by_grants(
		"checkpoints",
		["alice/model-a.safetensors", "bob/model-b.safetensors", "other.safetensors"],
		[policy._normalized_record(g) for g in grants],
	)
	ok(
		filtered == ["alice/model-a.safetensors", "bob/model-b.safetensors"],
		"grant filtering works",
	)

	print("TestViewAllWhenIsolationOff")
	_install_fake_constants(enabled=False)
	policy_off = _load_module(
		"mss_login.utils.model_visibility_policy_off",
		os.path.join(_UTILS_DIR, "model_visibility_policy.py"),
		"mss_login.utils",
	)
	ok(
		policy_off.user_can_view_all_models("user", {"can_view_all_comfyui_items": False}) is False,
		"isolation off: user without permission has models hidden",
	)
	ok(
		policy_off.user_can_view_all_models("user", {"can_view_all_comfyui_items": True}) is True,
		"isolation off: standard users with permission see the shared model library",
	)
	ok(
		policy_off.user_can_view_all_models("guest", {}) is True,
		"isolation off: guests without explicit denial are not locked out of local models",
	)
	ok(
		policy_off.user_can_view_all_models("admin", {}) is True,
		"isolation off: admin sees all models",
	)

	print("TestViewAllWhenIsolationOn")
	_install_fake_constants(enabled=True)
	policy_on = _load_module(
		"mss_login.utils.model_visibility_policy_on",
		os.path.join(_UTILS_DIR, "model_visibility_policy.py"),
		"mss_login.utils",
	)
	ok(
		policy_on.user_can_view_all_models("owner", {}) is True,
		"isolation on: owner bypasses grants",
	)
	ok(
		policy_on.user_can_view_all_models("user", {"can_view_all_comfyui_items": True}) is False,
		"isolation on: standard users require explicit grants",
	)
	ok(
		policy_on.user_can_view_all_models(
			"admin", {"can_view_all_comfyui_items": True, "can_manage_model_sharing": True}
		)
		is True,
		"isolation on: admin with both flags bypasses grants",
	)

	print("TestModelIsolationPaths")
	_install_fake_constants(enabled=True)
	paths = _load_module(
		"mss_login.utils.model_isolation",
		os.path.join(_UTILS_DIR, "model_isolation.py"),
		"mss_login.utils",
	)
	ok(paths.sanitize_user_segment("bob@example.com") == "bob_example_com", "user id is sanitized")
	isolation_path = paths.isolation_user_folder("checkpoints", "alice").replace("\\", "/")
	ok(isolation_path.endswith("checkpoints/alice"), "isolation folder path is per-user")

	# Existing on-disk UUID tree: register + list without re-download
	user_ckpt = paths.isolation_user_folder("checkpoints", "alice")
	os.makedirs(user_ckpt, exist_ok=True)
	existing_model = os.path.join(user_ckpt, "legacy_existing.safetensors")
	with open(existing_model, "w", encoding="utf-8") as f:
		f.write("x")
	fp_reg = types.ModuleType("folder_paths")
	fp_reg._added = []

	def _add_model_folder_path(folder_type, root):
		fp_reg._added.append((folder_type, root))

	fp_reg.add_model_folder_path = _add_model_folder_path
	sys.modules["folder_paths"] = fp_reg
	registered = paths.register_all_isolation_folders()
	ok("checkpoints" in registered, "startup registration includes checkpoints")
	ok(
		any(t == "checkpoints" for t, _r in fp_reg._added),
		"checkpoints root registered with folder_paths",
	)
	disk_items = paths.list_isolation_items_for_user("alice")
	ok(
		any(
			it.get("folder") == "checkpoints"
			and it.get("item_name") == "alice/legacy_existing.safetensors"
			for it in disk_items
		),
		"on-disk UUID file listed as storage-shaped grant",
	)

	# Union DB grants with disk via get_effective_model_grants_for_user
	sys.modules["mss_login.utils.model_isolation"] = paths
	policy_disk = _load_module(
		"mss_login.utils.model_visibility_policy_disk",
		os.path.join(_UTILS_DIR, "model_visibility_policy.py"),
		"mss_login.utils",
	)

	class _FakeUsersDb:
		def get_user(self, username=None, user_id=None):
			if username == "alice":
				return "alice", {"username": "alice"}
			raise KeyError(username)

	class _FakeStore:
		def list_for_user(self, user_id):
			return []  # no shared_items rows — disk-only legacy file

	grants_disk_user = policy_disk.get_effective_model_grants_for_user(
		role="user",
		perms={},
		username="alice",
		users_db=_FakeUsersDb(),
		shared_items_store_getter=lambda _cfg: _FakeStore(),
		users_db_config={},
	)
	ok(
		not any(g.get("item_name") == "alice/legacy_existing.safetensors" for g in grants_disk_user),
		"non-owner without shared_items row is not auto-granted on-disk isolation files",
	)
	grants_disk_owner = policy_disk.get_effective_model_grants_for_user(
		role="owner",
		perms={},
		username="alice",
		users_db=_FakeUsersDb(),
		shared_items_store_getter=lambda _cfg: _FakeStore(),
		users_db_config={},
	)
	ok(
		any(g.get("item_name") == "alice/legacy_existing.safetensors" for g in grants_disk_owner),
		"universal model access (owner) includes on-disk isolation files without shared_items row",
	)
	ok(
		policy_disk.grant_matches_item(
			"alice/legacy_existing.safetensors", {"legacy_existing.safetensors"}
		)
		is True,
		"grant dual-read matches storage item against flat grant",
	)
	ok(
		policy_disk.filter_items_by_grants(
			"checkpoints",
			["alice/legacy_existing.safetensors", "other/x.safetensors"],
			[
				policy_disk._normalized_record(
					{"folder": "checkpoints", "item_name": "alice/legacy_existing.safetensors"}
				)
			],
		)
		== ["alice/legacy_existing.safetensors"],
		"filter_items_by_grants keeps storage-named isolation file",
	)
	try:
		os.remove(existing_model)
	except OSError:
		pass

	print("TestModelDownloadRedirect")
	folder_paths_mod = types.ModuleType("folder_paths")
	folder_paths_mod.models_dir = os.path.join(_PROJECT_ROOT, "ComfyUI", "models")
	sys.modules["folder_paths"] = folder_paths_mod
	redirect = _load_module(
		"mss_login.utils.model_download_redirect",
		os.path.join(_UTILS_DIR, "model_download_redirect.py"),
		"mss_login.utils",
	)
	payload = {
		"destination_path": os.path.join(
			_PROJECT_ROOT, "ComfyUI", "models", "checkpoints", "foo.safetensors"
		),
		"other": "keep",
	}
	rewritten, changed = redirect.rewrite_download_payload_for_user(payload, "alice")
	ok(changed is True, "download payload was rewritten")
	ok(
		"/model_isolation/models/checkpoints/alice/foo.safetensors"
		in rewritten["destination_path"].replace("\\", "/"),
		"global models path remaps to user isolated folder",
	)
	effective = redirect.get_effective_route_patterns()
	ok("/owner-custom" in effective, "owner-configured patterns included")
	ok(
		redirect.should_try_model_download_redirect("/my/owner-custom/route") is True,
		"configured route pattern triggers redirect matching",
	)

	print("TestModelPathDisplay")
	_install_fake_constants(enabled=True)
	sys.modules["mss_login.utils.model_isolation"] = paths
	sys.modules["mss_login.utils.model_visibility_policy"] = policy_on
	_load_module(
		"mss_login.utils.prompt_model_validator",
		os.path.join(_UTILS_DIR, "prompt_model_validator.py"),
		"mss_login.utils",
	)
	display = _load_module(
		"mss_login.utils.model_path_display",
		os.path.join(_UTILS_DIR, "model_path_display.py"),
		"mss_login.utils",
	)
	uid = "f350e460-a651-4b51-bbcd-c573fa5feed4"
	storage = f"{uid}/dream.safetensors"
	ok(
		display.to_display_name(storage, uid, view_all=True) == "dream.safetensors",
		"view_all strips own UUID prefix",
	)
	ok(
		display.to_display_name(
			"bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb/other.safetensors", uid, view_all=True
		)
		== "other.safetensors",
		"view_all strips other users' UUID prefixes in loader-facing display",
	)
	grants_disp = [
		policy_on._normalized_record({"folder": "checkpoints", "item_name": storage}),
		policy_on._normalized_record(
			{"folder": "checkpoints", "item_name": "other-user/shared.safetensors"}
		),
	]
	ok(
		display.to_display_name(storage, uid, view_all=False, grants=grants_disp)
		== "dream.safetensors",
		"non-view-all strips own grant prefix",
	)
	ok(
		display.to_display_name(
			"other-user/shared.safetensors", uid, view_all=False, grants=grants_disp
		)
		== "shared.safetensors",
		"non-view-all strips shared grant prefix",
	)
	ok(
		display.to_storage_name("dream.safetensors", uid, grants_disp, folder="checkpoints")
		== storage,
		"flat name maps back to storage via grant",
	)
	ok(
		display.to_storage_name(storage, uid, grants_disp, folder="checkpoints") == storage,
		"legacy prefixed prompt name dual-reads as storage",
	)
	ok(
		display.to_storage_name("shared.safetensors", uid, grants_disp, folder="checkpoints")
		== "other-user/shared.safetensors",
		"shared flat name maps to other user's storage path",
	)
	prompt = {
		"1": {"class_type": "CheckpointLoaderSimple", "inputs": {"ckpt_name": "dream.safetensors"}}
	}
	display.rewrite_prompt_model_names(prompt, uid, grants_disp, normalize_only=True)
	ok(
		prompt["1"]["inputs"]["ckpt_name"] == "dream.safetensors",
		"prompt normalize_only keeps flat loader name plain",
	)
	legacy_prompt = {
		"1": {"class_type": "CheckpointLoaderSimple", "inputs": {"ckpt_name": storage}}
	}
	display.rewrite_prompt_model_names(legacy_prompt, uid, grants_disp, normalize_only=True)
	ok(
		legacy_prompt["1"]["inputs"]["ckpt_name"] == "dream.safetensors",
		"prompt normalize_only strips own legacy UUID prefix to plain",
	)

	_install_fake_constants(enabled=False)
	display_off = _load_module(
		"mss_login.utils.model_path_display_off",
		os.path.join(_UTILS_DIR, "model_path_display.py"),
		"mss_login.utils",
	)
	ok(
		display_off.to_display_name(storage, uid, view_all=False, grants=grants_disp) == storage,
		"isolation off: display names unchanged",
	)

	print("TestModelFilterPathNormalizeAndObjectInfo")
	_install_fake_constants(enabled=True)
	# Stub folder_paths so middleware module can import
	fp = types.ModuleType("folder_paths")
	fp.folder_names_and_paths = {"checkpoints": ([], {".safetensors"})}
	fp.get_filename_list = lambda folder: []
	fp.map_legacy = lambda n: n
	sys.modules["folder_paths"] = fp
	aiohttp_mod = types.ModuleType("aiohttp")

	class _Web:
		@staticmethod
		def middleware(fn):
			return fn

		@staticmethod
		def json_response(data, status=200):
			return ("json", data, status)

		class Request:
			pass

		class Response:
			pass

		class StreamResponse:
			pass

		class HTTPFound(Exception):
			def __init__(self, location):
				self.location = location

	aiohttp_mod.web = _Web
	sys.modules["aiohttp"] = aiohttp_mod
	# path_safety is required by middleware
	_load_module(
		"mss_login.utils.path_safety", os.path.join(_UTILS_DIR, "path_safety.py"), "mss_login.utils"
	)
	sys.modules["mss_login.utils.model_visibility_policy"] = policy_on
	sys.modules["mss_login.utils.model_path_display"] = display
	mw = _load_module(
		"mss_login.utils.model_filter_middleware",
		os.path.join(_UTILS_DIR, "model_filter_middleware.py"),
		"mss_login.utils",
	)
	ok(mw._normalize_request_path("/api/models/") == "/models", "strips /api and trailing slash")
	ok(
		mw._normalize_request_path("/api/experiment/models") == "/experiment/models",
		"normalizes experiment path under /api",
	)
	ok(mw._normalize_request_path("/models") == "/models", "bare /models unchanged")
	grants_oi = [
		policy_on._normalized_record({"folder": "checkpoints", "item_name": "allowed.safetensors"})
	]
	oi = {
		"CheckpointLoaderSimple": {
			"input": {
				"required": {"ckpt_name": [["allowed.safetensors", "secret.safetensors"], {}]}
			}
		}
	}
	filtered_oi = mw._filter_object_info_combos(oi, grants_oi)
	combo = filtered_oi["CheckpointLoaderSimple"]["input"]["required"]["ckpt_name"][0]
	ok(combo == ["allowed.safetensors"], "object_info combo filtered to grants")

	print()
	try:
		if os.path.isfile(_TMP_CONFIG):
			os.remove(_TMP_CONFIG)
	except Exception:
		pass
	if failed:
		print(f"Result: {failed} failed, {run - failed} passed, {run} total")
		sys.exit(1)
	print(f"Result: all {run} tests passed")
	sys.exit(0)


if __name__ == "__main__":
	run_tests()
