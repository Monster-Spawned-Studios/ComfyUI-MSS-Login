r"""
Standalone runner for object_info combo mapping (no ComfyUI deps).

Verifies (node_class, input) ComboSource rebuild, uid-strip passthrough for
unmapped nodes, overlap fallback, and NODE_MODEL_INPUTS / prompt rewrite.

Use the project .venv:
  .venv/bin/python tests/run_object_info_combo_mapping_tests.py
"""

from __future__ import annotations

import importlib.util
import os
import sys
import tempfile
import types
from pathlib import Path

_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_UTILS_DIR = os.path.join(_PROJECT_ROOT, "utils")

USER_A = "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"
USER_B = "bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb"

WRONG_UPSCALE = ["4x_NMKD-Superscale-SP_178000_G.pth", "HAT_SRx4_ImageNet-pretrain.pth"]
WRONG_CLIP = ["anima_baseV10_txt.safetensors", "clip_l.safetensors"]


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


def _combo_spec(names: list[str]):
	return [list(names)]


def _install_pkg(tmp_data: Path, enabled: bool = True):
	for key in list(sys.modules):
		if key == "mss_login" or key.startswith("mss_login.") or key in ("folder_paths", "aiohttp"):
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

	def _load(name: str, filename: str):
		path = os.path.join(_UTILS_DIR, filename)
		spec = importlib.util.spec_from_file_location(name, path)
		mod = importlib.util.module_from_spec(spec)
		mod.__package__ = "mss_login.utils"
		sys.modules[name] = mod
		assert spec and spec.loader
		spec.loader.exec_module(mod)
		return mod

	_load("mss_login.utils.path_safety", "path_safety.py")
	_load("mss_login.utils.model_isolation", "model_isolation.py")
	vis = _load("mss_login.utils.model_visibility_policy", "model_visibility_policy.py")
	pmv = _load("mss_login.utils.prompt_model_validator", "prompt_model_validator.py")
	resolver = _load("mss_login.utils.isolation_resolver", "isolation_resolver.py")
	_load("mss_login.utils.isolation_folder_hooks", "isolation_folder_hooks.py")
	display = _load("mss_login.utils.model_path_display", "model_path_display.py")
	_load("mss_login.utils.object_info_combo_sources", "object_info_combo_sources.py")
	mw = _load("mss_login.utils.model_filter_middleware", "model_filter_middleware.py")

	return types.SimpleNamespace(
		vis=vis,
		pmv=pmv,
		resolver=resolver,
		display=display,
		mw=mw,
		fp=fp,
		shared_roots=shared_roots,
		tmp_data=tmp_data,
	)


def _setup_fixture_tree(tmp: Path, env) -> None:
	folders = {
		"ultralytics_bbox": ["face_yolov8m.pt", "hand_yolov8s.pt"],
		"ultralytics_segm": ["person_yolov8m-seg.pt"],
		"sams": ["sam_vit_b_01ec64.pth"],
		"clip_vision": [
			"clip-vision_vit-h.safetensors",
			"hy3d2mini_dino_fp16.safetensors",
			"hy3d21_dino_fp16.safetensors",
		],
		"text_encoders": ["clip_l.safetensors", "Qwen3-4B-Q4_K_M.gguf"],
		"upscale_models": ["4x_NMKD-Superscale-SP_178000_G.pth", "HAT_SRx4_ImageNet-pretrain.pth"],
		"diffusion_models": ["flux1-dev.safetensors"],
	}
	for folder, files in folders.items():
		root = tmp / "shared" / folder
		root.mkdir(parents=True, exist_ok=True)
		for f in files:
			_touch(root / f)
		env.shared_roots[folder] = [str(root)]
	# Legacy clip dual-read for text_encoders
	clip_root = tmp / "shared" / "clip"
	clip_root.mkdir(parents=True, exist_ok=True)
	_touch(clip_root / "legacy_clip.safetensors")
	env.shared_roots["clip"] = [str(clip_root)]
	env.shared_roots["unet"] = list(env.shared_roots.get("diffusion_models", []))
	# Own isolation file for USER_A under clip_vision
	_touch(tmp / "model_isolation" / "models" / "clip_vision" / USER_A / "own_model.safetensors")


def _fake_object_info() -> dict:
	"""Every mapped combo starts with the *wrong* folder list (simulates the bug)."""
	return {
		"UltralyticsDetectorProvider": {
			"input": {"required": {"model_name": _combo_spec(WRONG_UPSCALE)}}
		},
		"SAMLoader": {"input": {"required": {"model_name": _combo_spec(WRONG_UPSCALE)}}},
		"CLIPVisionLoader": {"input": {"required": {"clip_name": _combo_spec(WRONG_CLIP)}}},
		"CLIPVisionLoaderMultiGPU": {"input": {"required": {"clip_name": _combo_spec(WRONG_CLIP)}}},
		"CLIPVisionLoaderDisTorch2MultiGPU": {
			"input": {"required": {"clip_name": _combo_spec(WRONG_CLIP)}}
		},
		"CLIPLoaderGGUF": {"input": {"required": {"clip_name": _combo_spec(WRONG_UPSCALE)}}},
		"UpscaleModelLoader": {"input": {"required": {"model_name": _combo_spec(WRONG_UPSCALE)}}},
		"easy cascadeLoader": {
			"input": {"required": {"clip_name": _combo_spec(["None", *WRONG_CLIP])}}
		},
		"SomeCustomNode": {
			"input": {
				"required": {
					"model_name": _combo_spec(
						[
							"foo/bar.safetensors",
							"baz.pt",
							f"{USER_A}/mine.safetensors",
							f"{USER_B}/theirs.safetensors",
						]
					)
				}
			}
		},
		"AILab_Florence2": {
			"input": {
				"required": {
					"model_name": _combo_spec(
						["microsoft/Florence-2-base", "microsoft/Florence-2-large"]
					)
				}
			}
		},
	}


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

	with tempfile.TemporaryDirectory(prefix="mss_oi_combo_") as td:
		tmp = Path(td)
		env = _install_pkg(tmp, enabled=True)
		_setup_fixture_tree(tmp, env)

		print("TestObjectInfoComboMapping")
		payload = _fake_object_info()
		out = env.mw.filter_object_info_payload(payload, user_id=USER_A, grants=[], view_all=True)

		ultra = out["UltralyticsDetectorProvider"]["input"]["required"]["model_name"][0]
		ok(
			set(ultra)
			== {"bbox/face_yolov8m.pt", "bbox/hand_yolov8s.pt", "segm/person_yolov8m-seg.pt"},
			"UltralyticsDetectorProvider.model_name has bbox/ + segm/ only",
		)
		ok(not any(x in ultra for x in WRONG_UPSCALE), "Ultralytics has no upscale bleed")

		sam = out["SAMLoader"]["input"]["required"]["model_name"][0]
		ok(sam == ["sam_vit_b_01ec64.pth"], "SAMLoader.model_name is sams only")
		ok(not any(x in sam for x in WRONG_UPSCALE), "SAMLoader has no upscale bleed")

		for cls in (
			"CLIPVisionLoader",
			"CLIPVisionLoaderMultiGPU",
			"CLIPVisionLoaderDisTorch2MultiGPU",
		):
			cv = out[cls]["input"]["required"]["clip_name"][0]
			ok(
				"hy3d2mini_dino_fp16.safetensors" in cv
				and "hy3d21_dino_fp16.safetensors" in cv
				and "clip-vision_vit-h.safetensors" in cv,
				f"{cls} includes clip_vision files",
			)
			ok(
				"clip_l.safetensors" not in cv and "Qwen3-4B-Q4_K_M.gguf" not in cv,
				f"{cls} has no text-encoder bleed",
			)

		gguf = out["CLIPLoaderGGUF"]["input"]["required"]["clip_name"][0]
		ok(
			"Qwen3-4B-Q4_K_M.gguf" in gguf and "clip_l.safetensors" in gguf,
			"CLIPLoaderGGUF includes text_encoders (+gguf)",
		)
		ok(
			"hy3d2mini_dino_fp16.safetensors" not in gguf
			and not any(x in gguf for x in WRONG_UPSCALE),
			"CLIPLoaderGGUF has no clip_vision/upscale bleed",
		)

		up = out["UpscaleModelLoader"]["input"]["required"]["model_name"][0]
		ok(set(up) == set(WRONG_UPSCALE), "UpscaleModelLoader still lists upscale_models")

		# Unmapped passthrough
		florence = out["AILab_Florence2"]["input"]["required"]["model_name"][0]
		ok(
			florence == ["microsoft/Florence-2-base", "microsoft/Florence-2-large"],
			"AILab_Florence2 passthrough identical",
		)
		custom = out["SomeCustomNode"]["input"]["required"]["model_name"][0]
		ok(
			custom == ["foo/bar.safetensors", "baz.pt", "mine.safetensors"],
			"SomeCustomNode: own UUID stripped, foreign dropped, order kept",
		)

		# None sentinel
		cascade = out["easy cascadeLoader"]["input"]["required"]["clip_name"][0]
		ok(cascade[0] == "None", "easy cascadeLoader keeps None sentinel first")
		ok("clip_l.safetensors" in cascade, "easy cascadeLoader has text encoders")

		print("TestOverlapFallback")
		# Mapped UpscaleModelLoader but original has only unrelated model files
		mismatch = {
			"UpscaleModelLoader": {
				"input": {
					"required": {
						"model_name": _combo_spec(["totally_unrelated_encoder.safetensors"])
					}
				}
			}
		}
		fb = env.mw.filter_object_info_payload(mismatch, user_id=USER_A, grants=[], view_all=True)
		ok(
			fb["UpscaleModelLoader"]["input"]["required"]["model_name"][0]
			== ["totally_unrelated_encoder.safetensors"],
			"overlap miss + view_all falls back to original",
		)
		empty_grants = {
			"UpscaleModelLoader": {
				"input": {
					"required": {
						"model_name": _combo_spec(["totally_unrelated_encoder.safetensors"])
					}
				}
			}
		}
		# Non-view_all, no grants: rebuilt from folders is empty → stay empty (no leak)
		empty_out = env.mw.filter_object_info_payload(
			empty_grants, user_id=USER_B, grants=[], view_all=False
		)
		ok(
			empty_out["UpscaleModelLoader"]["input"]["required"]["model_name"][0] == [],
			"non-view_all no grants keeps empty rebuilt (no original leak)",
		)

		print("TestNodeModelInputsAndRewrite")
		ok(
			env.pmv.folder_for_node_input("CLIPLoader", "clip_name") == "text_encoders",
			"CLIPLoader → text_encoders",
		)
		# Legacy clip dual-read via list_display_names
		te = env.resolver.list_display_names("text_encoders", USER_A, view_all=True)
		ok(
			"legacy_clip.safetensors" in te or "clip_l.safetensors" in te,
			"text_encoders list includes clip dual-read and/or shared encoders",
		)
		# Ensure clip alias surfaces legacy file when querying text_encoders
		ok(
			"legacy_clip.safetensors" in te,
			"list_display_names(text_encoders) includes legacy clip dir file",
		)
		ok(
			env.pmv.folder_for_node_input("SAMLoader", "model_name") == "sams",
			"SAMLoader uses model_name → sams",
		)
		prompt = {
			"1": {
				"class_type": "CLIPVisionLoaderMultiGPU",
				"inputs": {"clip_name": f"{USER_A}/hy3d2mini_dino_fp16.safetensors"},
			},
			"2": {
				"class_type": "UltralyticsDetectorProvider",
				"inputs": {"model_name": "bbox/face_yolov8m.pt"},
			},
		}
		env.display.rewrite_prompt_model_names(prompt, USER_A, grants=[], normalize_only=True)
		ok(
			prompt["1"]["inputs"]["clip_name"] == "hy3d2mini_dino_fp16.safetensors",
			"rewrite strips viewer prefix for CLIPVisionLoaderMultiGPU",
		)
		ok(
			prompt["2"]["inputs"]["model_name"] == "bbox/face_yolov8m.pt",
			"rewrite keeps bbox/ prefix for Ultralytics",
		)

		print("TestIsolationPrefixStrip")
		cv_payload = {
			"CLIPVisionLoader": {
				"input": {
					"required": {
						"clip_name": _combo_spec(
							["clip-vision_vit-h.safetensors", f"{USER_A}/own_model.safetensors"]
						)
					}
				}
			}
		}
		# Force rebuild from folder sources (mapped)
		as_a = env.mw.filter_object_info_payload(
			{"CLIPVisionLoader": {"input": {"required": {"clip_name": _combo_spec(WRONG_CLIP)}}}},
			user_id=USER_A,
			grants=[],
			view_all=True,
		)
		cv_a = as_a["CLIPVisionLoader"]["input"]["required"]["clip_name"][0]
		ok(
			"own_model.safetensors" in cv_a and not any(x.startswith(f"{USER_A}/") for x in cv_a),
			"USER_A sees own_model without UUID prefix",
		)
		as_b = env.mw.filter_object_info_payload(
			{"CLIPVisionLoader": {"input": {"required": {"clip_name": _combo_spec(WRONG_CLIP)}}}},
			user_id=USER_B,
			grants=[],
			view_all=False,
		)
		cv_b = as_b["CLIPVisionLoader"]["input"]["required"]["clip_name"][0]
		ok(
			"own_model.safetensors" not in cv_b and not any(USER_A in x for x in cv_b),
			"USER_B without grants does not see USER_A model or UUID",
		)

		print("TestNoCrossFolderBleed")
		mapped_checks = [
			(
				"UltralyticsDetectorProvider",
				"model_name",
				{"bbox/face_yolov8m.pt", "bbox/hand_yolov8s.pt", "segm/person_yolov8m-seg.pt"},
			),
			("SAMLoader", "model_name", {"sam_vit_b_01ec64.pth"}),
			("UpscaleModelLoader", "model_name", set(WRONG_UPSCALE)),
		]
		full = env.mw.filter_object_info_payload(
			_fake_object_info(), user_id=USER_A, grants=[], view_all=True
		)
		for cls, key, allowed in mapped_checks:
			got = set(full[cls]["input"]["required"][key][0])
			# Drop sentinels for subset check
			got_files = {x for x in got if x not in ("None",)}
			ok(got_files <= allowed, f"{cls}.{key} subset of expected folder display names")

		_ = cv_payload  # silence unused in isolation path above

	print()
	if failed:
		print(f"Result: {failed} failed, {run - failed} passed, {run} total")
		return 1
	print(f"Result: all {run} tests passed")
	return 0


if __name__ == "__main__":
	sys.exit(run_tests())
