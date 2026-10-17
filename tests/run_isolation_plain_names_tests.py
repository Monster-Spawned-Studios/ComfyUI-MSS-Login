r"""
Standalone runner for isolation plain-name tests (no ComfyUI deps).

Mirrors tests/test_isolation_plain_names.py using an ok() harness so CI does not
need pytest package collection of the project root.

Use the project .venv:
  .venv/bin/python tests/run_isolation_plain_names_tests.py
"""

from __future__ import annotations

import importlib.util
import json
import os
import sys
import tempfile
import types
from pathlib import Path

_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_UTILS_DIR = os.path.join(_PROJECT_ROOT, "utils")

USER_A = "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"
USER_B = "bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb"
OWNER = "f350e460-a651-4b51-bbcd-c573fa5feed4"


def _touch(path: Path):
	path.parent.mkdir(parents=True, exist_ok=True)
	path.write_bytes(b"model")


def _grant(folder: str, item_name: str) -> dict:
	return {
		"folder": folder,
		"item_name": item_name,
		"norm_item_name": item_name.replace("\\", "/").strip().lower(),
		"source_backend": "local",
		"granted_by_user_id": "",
		"granted_by_role": "test",
		"created_at": 0,
	}


def _install_pkg(tmp_data: Path, enabled: bool = True):
	for key in list(sys.modules):
		if key == "mss_login" or key.startswith("mss_login.") or key == "folder_paths":
			del sys.modules[key]

	root_pkg = types.ModuleType("mss_login")
	root_pkg.__path__ = [_PROJECT_ROOT]
	utils_pkg = types.ModuleType("mss_login.utils")
	utils_pkg.__path__ = [_UTILS_DIR]
	sys.modules["mss_login"] = root_pkg
	sys.modules["mss_login.utils"] = utils_pkg

	const_mod = types.ModuleType("mss_login.constants")
	const_mod.DATA_DIR = str(tmp_data)
	const_mod.CONFIG_FILE_PATH = str(tmp_data / "config.json")
	const_mod.CURRENT_DIR = _PROJECT_ROOT
	const_mod.USERS_DB_CONFIG = {}
	const_mod.experimental_model_isolation_enabled = lambda: enabled
	sys.modules["mss_login.constants"] = const_mod

	shared_roots: dict[str, list[str]] = {}

	def _full_path(folder: str, name: str):
		name = (name or "").replace("\\", "/")
		iso_base = tmp_data / "model_isolation" / "models"
		for f in (folder, {"unet": "diffusion_models", "diffusion_models": "unet"}.get(folder)):
			if not f:
				continue
			cand = iso_base / f / name
			if cand.is_file():
				return str(cand)
		for root in shared_roots.get(folder, []):
			cand = Path(root) / name
			if cand.is_file():
				return str(cand)
		return None

	fp = types.ModuleType("folder_paths")
	fp.folder_names_and_paths = {}
	fp.get_folder_paths = lambda folder: list(shared_roots.get(folder, []))
	fp.get_filename_list = lambda folder: []
	fp.get_full_path = _full_path
	fp.get_full_path_or_raise = _full_path
	fp.map_legacy = lambda n: {"unet": "diffusion_models", "clip": "text_encoders"}.get(n, n)
	sys.modules["folder_paths"] = fp

	def _load(name: str, filename: str):
		path = os.path.join(_UTILS_DIR, filename)
		spec = importlib.util.spec_from_file_location(name, path)
		mod = importlib.util.module_from_spec(spec)
		mod.__package__ = "mss_login.utils"
		sys.modules[name] = mod
		assert spec and spec.loader
		spec.loader.exec_module(mod)
		return mod

	_load("mss_login.utils.model_isolation", "model_isolation.py")
	vis = _load("mss_login.utils.model_visibility_policy", "model_visibility_policy.py")
	pmv = _load("mss_login.utils.prompt_model_validator", "prompt_model_validator.py")
	resolver = _load("mss_login.utils.isolation_resolver", "isolation_resolver.py")
	_load("mss_login.utils.isolation_folder_hooks", "isolation_folder_hooks.py")
	display = _load("mss_login.utils.model_path_display", "model_path_display.py")

	return types.SimpleNamespace(
		vis=vis,
		pmv=pmv,
		resolver=resolver,
		display=display,
		fp=fp,
		shared_roots=shared_roots,
		tmp_data=tmp_data,
	)


def run_tests() -> int:
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

	with tempfile.TemporaryDirectory(prefix="mss_iso_plain_") as td:
		tmp = Path(td)
		env = _install_pkg(tmp, enabled=True)
		shared_dm = tmp / "shared" / "diffusion_models"
		shared_loras = tmp / "shared" / "loras"
		shared_dm.mkdir(parents=True)
		shared_loras.mkdir(parents=True)
		_touch(shared_dm / "anima-base-v1.0.safetensors")
		_touch(shared_loras / "lora.safetensors")
		_touch(shared_loras / "anima-base-1-masterpiece-v51.safetensors")
		env.shared_roots["diffusion_models"] = [str(shared_dm)]
		env.shared_roots["unet"] = [str(shared_dm)]
		env.shared_roots["loras"] = [str(shared_loras)]

		_touch(tmp / "model_isolation" / "models" / "diffusion_models" / USER_A / "x.safetensors")
		_touch(tmp / "model_isolation" / "models" / "loras" / USER_A / "lora.safetensors")
		_touch(tmp / "model_isolation" / "models" / "diffusion_models" / USER_B / "y.safetensors")

		print("TestListing")
		grants_a = [_grant("diffusion_models", f"{USER_A}/x.safetensors")]
		names = env.resolver.list_display_names(
			"diffusion_models", USER_A, grants=grants_a, view_all=False
		)
		ok("x.safetensors" in names, "A's file shows as plain name")
		ok("y.safetensors" not in names, "B's file absent from A's list")
		ok(not any(USER_A in n or USER_B in n for n in names), "no UUID prefixes in A's list")

		owner_names = env.resolver.list_display_names(
			"diffusion_models", OWNER, grants=[], view_all=True
		)
		ok("anima-base-v1.0.safetensors" in owner_names, "shared file present for owner")
		ok(
			all(not env.resolver.is_uuid_segment(n.split("/", 1)[0]) for n in owner_names),
			"owner list has no UUID-prefixed entries",
		)

		print("TestOwnerSharedRegression")
		for folder in ("unet", "diffusion_models"):
			resolved = env.resolver.resolve_model_name(
				folder, "anima-base-v1.0.safetensors", OWNER, grants=[], view_all=True
			)
			ok(
				resolved is not None
				and resolved.storage_name == "anima-base-v1.0.safetensors"
				and resolved.origin == "shared",
				f"owner shared resolves plain via {folder}",
			)
		ok(
			env.display.to_storage_name(
				"anima-base-v1.0.safetensors", OWNER, [], folder="diffusion_models", view_all=True
			)
			== "anima-base-v1.0.safetensors",
			"to_storage_name does not fabricate owner UUID prefix",
		)

		print("TestValidationAndFullPath")
		grants = [_grant("diffusion_models", f"{USER_A}/x.safetensors")]
		prompt = {"1": {"class_type": "UNETLoader", "inputs": {"unet_name": "x.safetensors"}}}
		valid, err = env.pmv.validate_prompt_models(
			env.vis.allowed_set_from_grants(grants), False, prompt
		)
		ok(valid, f"plain name validates for A ({err})")
		resolved = env.resolver.resolve_model_name(
			"diffusion_models", "x.safetensors", USER_A, grants=grants
		)
		ok(
			resolved is not None and resolved.storage_name == f"{USER_A}/x.safetensors",
			"resolve maps to A's storage path",
		)
		full = env.fp.get_full_path("diffusion_models", resolved.storage_name if resolved else "")
		ok(bool(full) and Path(full).is_file(), "get_full_path returns A's file")

		print("TestLegacyAndForeignPrefix")
		legacy = {
			"1": {"class_type": "UNETLoader", "inputs": {"unet_name": f"{USER_A}/x.safetensors"}}
		}
		env.display.rewrite_prompt_model_names(legacy, USER_A, grants, normalize_only=True)
		ok(legacy["1"]["inputs"]["unet_name"] == "x.safetensors", "own legacy prefix normalizes")
		ok(
			env.display.prompt_has_foreign_uuid_model(
				{
					"1": {
						"class_type": "UNETLoader",
						"inputs": {"unet_name": f"{USER_B}/y.safetensors"},
					}
				},
				USER_A,
			),
			"foreign prefix detected for y",
		)
		ok(
			env.display.prompt_has_foreign_uuid_model(
				{
					"1": {
						"class_type": "UNETLoader",
						"inputs": {"unet_name": f"{USER_B}/nope.safetensors"},
					}
				},
				USER_A,
			),
			"foreign prefix detected for nope",
		)
		body_y = json.dumps({"error": "Model not allowed", "code": "MODEL_NOT_ALLOWED"})
		body_nope = json.dumps({"error": "Model not allowed", "code": "MODEL_NOT_ALLOWED"})
		ok(body_y == body_nope, "generic deny bodies are byte-identical")
		ok(
			env.resolver.resolve_model_name(
				"diffusion_models", f"{USER_B}/y.safetensors", USER_A, grants=[]
			)
			is None
			and env.resolver.resolve_model_name(
				"diffusion_models", f"{USER_B}/nope.safetensors", USER_A, grants=[]
			)
			is None,
			"foreign prefixes resolve to None without existence leak",
		)

		print("TestCollisionAndGrant")
		grants_lora = [_grant("loras", f"{USER_A}/lora.safetensors")]
		lora_names = env.resolver.list_display_names(
			"loras", USER_A, grants=grants_lora, view_all=False
		)
		ok(lora_names.count("lora.safetensors") == 1, "collision listed once")
		ra = env.resolver.resolve_model_name(
			"loras", "lora.safetensors", USER_A, grants=grants_lora
		)
		ok(ra is not None and ra.origin == "user", "A resolves collision to own copy")
		rb = env.resolver.resolve_model_name(
			"loras",
			"lora.safetensors",
			USER_B,
			grants=[_grant("loras", "lora.safetensors")],
			view_all=False,
		)
		ok(
			rb is not None
			and rb.storage_name == "lora.safetensors"
			and rb.origin in ("shared", "granted"),
			"B without own copy resolves shared (or flat grant)",
		)
		rb_shared = env.resolver.resolve_model_name(
			"loras", "lora.safetensors", USER_B, grants=[], view_all=True
		)
		ok(
			rb_shared is not None and rb_shared.origin == "shared",
			"B view_all without own copy resolves shared origin",
		)

		grants_share = [
			_grant("diffusion_models", f"{USER_A}/x.safetensors"),
			_grant("diffusion_models", f"{USER_B}/y.safetensors"),
		]
		names_g = env.resolver.list_display_names(
			"diffusion_models", USER_A, grants=grants_share, view_all=False
		)
		ok(
			"y.safetensors" in names_g and f"{USER_B}/y.safetensors" not in names_g,
			"grant as plain",
		)
		rg = env.resolver.resolve_model_name(
			"diffusion_models", "y.safetensors", USER_A, grants=grants_share
		)
		ok(rg is not None and rg.origin == "granted", "grant loads B's storage path")

		print("TestAgreementAndSymlink")
		grants_agree = [
			_grant("diffusion_models", f"{USER_A}/x.safetensors"),
			_grant("diffusion_models", "anima-base-v1.0.safetensors"),
		]
		listed = set(
			env.resolver.list_display_names(
				"diffusion_models", USER_A, grants=grants_agree, view_all=False
			)
		)
		accepted = set()
		for name in listed:
			p = {"1": {"class_type": "UNETLoader", "inputs": {"unet_name": name}}}
			v, _ = env.pmv.validate_prompt_models(
				env.vis.allowed_set_from_grants(grants_agree), False, p
			)
			if v:
				accepted.add(name)
		cpe = set(
			env.resolver.list_display_names(
				"diffusion_models", USER_A, grants=grants_agree, view_all=False
			)
		)
		ok(listed == accepted == cpe, "object_info/validate/CPE name sets agree")

		outside = tmp / "outside" / "evil.safetensors"
		_touch(outside)
		link = tmp / "model_isolation" / "models" / "diffusion_models" / USER_A / "evil.safetensors"
		try:
			link.symlink_to(outside)
			sym_ok = True
		except OSError:
			sym_ok = False
		if sym_ok:
			sym_names = env.resolver.list_display_names(
				"diffusion_models", USER_A, grants=[], view_all=False
			)
			ok("evil.safetensors" not in sym_names, "escaping symlink ignored in list")
			ok(
				env.resolver.resolve_model_name(
					"diffusion_models", "evil.safetensors", USER_A, grants=[]
				)
				is None,
				"escaping symlink ignored in resolve",
			)
		else:
			ok(True, "symlink skip (unsupported)")

		print("TestOwnerMiddlewareUnchanged")
		prompt_o = {
			"1": {
				"class_type": "UNETLoader",
				"inputs": {"unet_name": "anima-base-v1.0.safetensors"},
			}
		}
		before = json.dumps(prompt_o, sort_keys=True)
		env.display.rewrite_prompt_model_names(prompt_o, OWNER, [], normalize_only=True)
		ok(json.dumps(prompt_o, sort_keys=True) == before, "owner shared name unchanged")

		print("TestRewriteNoCatchAll")
		prompt_c = {
			"1": {
				"class_type": "SomeCustomNode",
				"inputs": {"text": "note.safetensors", "other": "x.safetensors"},
			}
		}
		env.display.rewrite_prompt_model_names(
			prompt_c,
			USER_A,
			[_grant("diffusion_models", f"{USER_A}/x.safetensors")],
			normalize_only=True,
		)
		ok(
			prompt_c["1"]["inputs"]["text"] == "note.safetensors"
			and prompt_c["1"]["inputs"]["other"] == "x.safetensors",
			"free-text .safetensors inputs are not rewritten",
		)

	print(f"\n{run - failed}/{run} passed")
	return 1 if failed else 0


if __name__ == "__main__":
	sys.exit(run_tests())
