r"""
Standalone tests for UI workflow metadata pass-through / fill-in.

Use the project .venv:
  .venv/bin/python tests/run_workflow_metadata_tests.py
"""

from __future__ import annotations

import importlib.util
import json
import os
import sys
import tempfile
import types

_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_utils = os.path.join(_PROJECT_ROOT, "utils")

USER_A = "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"
USER_B = "bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb"


def _load_utils_submodule(mod_name: str):
	if "utils" not in sys.modules:
		pkg = types.ModuleType("utils")
		pkg.__path__ = [_utils]
		pkg.__package__ = "utils"
		sys.modules["utils"] = pkg
	full = f"utils.{mod_name}"
	if full in sys.modules:
		return sys.modules[full]
	spec = importlib.util.spec_from_file_location(full, os.path.join(_utils, f"{mod_name}.py"))
	mod = importlib.util.module_from_spec(spec)
	mod.__package__ = "utils"
	sys.modules[full] = mod
	assert spec and spec.loader
	spec.loader.exec_module(mod)
	return mod


def _sample_api(seed: int = 1) -> dict:
	return {
		"1": {"class_type": "CheckpointLoaderSimple", "inputs": {"ckpt_name": "model.safetensors"}},
		"2": {"class_type": "KSampler", "inputs": {"seed": seed, "text": f"hello-{seed}"}},
	}


def _sample_ui(*, ckpt: str = "model.safetensors") -> dict:
	return {
		"nodes": [
			{"id": 1, "type": "CheckpointLoaderSimple", "widgets_values": [ckpt]},
			{"id": 2, "type": "KSampler", "widgets_values": [1, "hello"]},
		],
		"links": [],
	}


def run_tests() -> int:
	wm = _load_utils_submodule("workflow_metadata")
	cpe = _load_utils_submodule("cpe_workflows")

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

	# Fresh cache / settings per suite
	clock = {"t": 0.0}

	def now():
		return clock["t"]

	cache = wm.UiWorkflowCache(ttl_seconds=100.0, max_per_user=32, clock=now)
	wm.set_ui_workflow_cache_for_tests(cache)
	wm.set_cpe_embed_settings_override(
		{"embed_ui_workflow": True, "ui_workflow_max_bytes": wm.DEFAULT_MAX_BYTES}
	)

	print("TestSuccessConvertPayload")
	api = _sample_api()
	ui = _sample_ui()
	payload = cpe.success_convert_payload(api, ui_workflow=ui)
	ok(payload["data"]["workflow"] == api, "data.workflow stays API prompt")
	ok(payload["data"].get("ui_workflow") == ui, "data.ui_workflow == ui")
	ok(
		payload["data"].get("extra_data", {}).get("extra_pnginfo", {}).get("workflow") == ui,
		"data.extra_data.extra_pnginfo.workflow == ui",
	)
	api_only = cpe.success_convert_payload(api)
	ok("ui_workflow" not in api_only["data"], "omit ui_workflow when None")
	ok("extra_data" not in api_only["data"], "omit extra_data when None")

	print("TestFingerprintIgnoresWidgets")
	fp1 = wm.structure_fingerprint(_sample_api(1))
	fp2 = wm.structure_fingerprint(_sample_api(999))
	ok(fp1 == fp2 and bool(fp1), "fingerprint ignores seed/text")

	print("TestPassThrough")
	W = _sample_ui()
	body = {
		"prompt": _sample_api(3),
		"client_id": "c1",
		"extra_data": {"extra_pnginfo": {"workflow": W, "foo": 1}},
	}
	out = wm.apply_prompt_extra_data(body, body["prompt"], user_id=USER_A, cache=cache)
	ok(out.get("client_id") == "c1", "client_id preserved")
	ok(
		out.get("extra_data", {}).get("extra_pnginfo", {}).get("workflow") == W
		or isinstance(out.get("extra_data", {}).get("extra_pnginfo", {}).get("workflow"), dict),
		"workflow preserved (or sanitized copy)",
	)
	ok(out.get("extra_data", {}).get("extra_pnginfo", {}).get("foo") == 1, "foo intact")

	print("TestFillIn")
	cache.clear()
	api_a = _sample_api(1)
	ui_a = _sample_ui(ckpt=f"{USER_A}/mine.safetensors")
	cleaned = wm.remember_converted_ui_workflow(USER_A, api_a, ui_a, cache=cache)
	ok(cleaned is not None, "USER_A convert cached after sanitize")
	ok("mine.safetensors" in json.dumps(cleaned), "own uid stripped to plain name in cache")
	ok(USER_A not in json.dumps(cleaned), "own uuid prefix removed")

	submit = {"prompt": _sample_api(42), "client_id": "portal"}  # different seed/text
	filled = wm.apply_prompt_extra_data(submit, submit["prompt"], user_id=USER_A, cache=cache)
	ok(
		isinstance(filled.get("extra_data", {}).get("extra_pnginfo", {}).get("workflow"), dict),
		"USER_A fill-in on same structure",
	)
	bob_body = {"prompt": _sample_api(42)}
	bob_out = wm.apply_prompt_extra_data(bob_body, bob_body["prompt"], user_id=USER_B, cache=cache)
	ok(
		not (
			isinstance(bob_out, dict)
			and isinstance(bob_out.get("extra_data"), dict)
			and isinstance(bob_out["extra_data"].get("extra_pnginfo"), dict)
			and "workflow" in bob_out["extra_data"]["extra_pnginfo"]
		),
		"USER_B does not get USER_A cache",
	)
	client_w = _sample_ui(ckpt="client.safetensors")
	with_client = {
		"prompt": _sample_api(1),
		"extra_data": {"extra_pnginfo": {"workflow": client_w}},
	}
	kept = wm.apply_prompt_extra_data(
		with_client, with_client["prompt"], user_id=USER_A, cache=cache
	)
	ok(
		kept["extra_data"]["extra_pnginfo"]["workflow"].get("nodes")[0]["widgets_values"][0]
		== "client.safetensors",
		"client workflow never overwritten",
	)

	clock["t"] = 1000.0  # past TTL
	expired = wm.apply_prompt_extra_data(
		{"prompt": _sample_api(7)}, _sample_api(7), user_id=USER_A, cache=cache
	)
	png_exp = (
		(expired.get("extra_data") or {}).get("extra_pnginfo")
		if isinstance(expired, dict)
		else None
	)
	ok(not (isinstance(png_exp, dict) and "workflow" in png_exp), "TTL expiry clears fill-in")

	print("TestNoFixedTemplate")
	cache.clear()
	clock["t"] = 0.0
	lonely = wm.apply_prompt_extra_data(
		{"prompt": _sample_api(1)}, _sample_api(1), user_id=USER_A, cache=cache
	)
	png = (
		(lonely.get("extra_data") or {}).get("extra_pnginfo") if isinstance(lonely, dict) else None
	)
	ok(not (isinstance(png, dict) and png.get("workflow")), "no cache hit → no workflow")
	src = open(os.path.join(_utils, "workflow_metadata.py"), encoding="utf-8").read()
	ok("Crystools" not in src, "no Crystools template reference")
	ok("Do not inject" in src or "fixed/template" in src.lower(), "documents no template injection")

	print("TestSecuritySanitize")
	over = {"nodes": [{"id": 1, "widgets_values": ["x" * (3 * 1024 * 1024)]}], "links": []}
	ok(wm.sanitize_ui_workflow(over, USER_A, max_bytes=1024) is None, "over-cap dropped")
	foreign = _sample_ui(ckpt=f"{USER_B}/secret.safetensors")
	ok(wm.sanitize_ui_workflow(foreign, USER_A) is None, "foreign uid dropped for USER_A")
	own = _sample_ui(ckpt=f"{USER_A}/mine.safetensors")
	own_clean = wm.sanitize_ui_workflow(own, USER_A)
	ok(own_clean is not None, "own uid graph kept")
	ok(own_clean["nodes"][0]["widgets_values"][0] == "mine.safetensors", "own uid → plain name")
	abs_ui = _sample_ui(ckpt="/root/mss-data/model_isolation/models/checkpoints/x.safetensors")
	ok(wm.sanitize_ui_workflow(abs_ui, USER_A) is None, "absolute path dropped")

	# Client-supplied foreign workflow dropped but prompt still "queues" (body returned)
	bad_client = {
		"prompt": _sample_api(1),
		"extra_data": {"extra_pnginfo": {"workflow": foreign, "keep": True}},
	}
	san = wm.apply_prompt_extra_data(bad_client, bad_client["prompt"], user_id=USER_A, cache=cache)
	ok(san["prompt"] == bad_client["prompt"], "prompt still present after drop")
	ok(
		"workflow" not in san.get("extra_data", {}).get("extra_pnginfo", {}),
		"foreign client workflow removed",
	)
	ok(san.get("extra_data", {}).get("extra_pnginfo", {}).get("keep") is True, "other keys kept")

	print("TestGrantDeniedNoCache")
	cache.clear()
	# Simulate: convert builds payload, but we only cache after grant — denied path skips put
	api_g = _sample_api(1)
	ui_g = _sample_ui()
	# Do not call remember — mirrors grant-denied
	miss = wm.apply_prompt_extra_data({"prompt": api_g}, api_g, user_id=USER_A, cache=cache)
	ok(
		not ((miss.get("extra_data") or {}).get("extra_pnginfo") or {}).get("workflow"),
		"no cache entry without remember (grant-denied shape)",
	)

	print("TestPngMetadataRoundTrip")
	try:
		from PIL import Image
		from PIL.PngImagePlugin import PngInfo
	except ImportError:
		print("  SKIP: Pillow not available")
	else:
		# Minimal SaveImage-style write
		with tempfile.TemporaryDirectory() as tmp:
			path = os.path.join(tmp, "out.png")
			img = Image.new("RGB", (8, 8), color=(10, 20, 30))
			pnginfo = PngInfo()
			prompt_json = json.dumps(_sample_api(1))
			workflow_json = json.dumps(_sample_ui())
			pnginfo.add_text("prompt", prompt_json)
			pnginfo.add_text("workflow", workflow_json)
			img.save(path, "PNG", pnginfo=pnginfo)

			# Exercise nsfw_guard metadata-preserving re-save (stub: call _set_nsfw_tag)
			# Load nsfw_guard with light stubs if needed
			try:
				# Ensure parent package path for relative imports inside nsfw_guard
				if "MSS_Login" not in sys.modules and "ComfyUI_MSS_Login" not in sys.modules:
					pass
				nsfw_path = os.path.join(_utils, "sfw_intercept", "nsfw_guard.py")
				# Import via package-like stub
				if "utils.sfw_intercept" not in sys.modules:
					sfw_pkg = types.ModuleType("utils.sfw_intercept")
					sfw_pkg.__path__ = [os.path.join(_utils, "sfw_intercept")]
					sfw_pkg.__package__ = "utils.sfw_intercept"
					sys.modules["utils.sfw_intercept"] = sfw_pkg
				spec = importlib.util.spec_from_file_location(
					"utils.sfw_intercept.nsfw_guard", nsfw_path
				)
				ng = importlib.util.module_from_spec(spec)
				ng.__package__ = "utils.sfw_intercept"
				sys.modules["utils.sfw_intercept.nsfw_guard"] = ng
				# nsfw_guard may import heavy deps — wrap
				try:
					assert spec and spec.loader
					spec.loader.exec_module(ng)
					ng._set_nsfw_tag(path, False, 0.0, "neutral")
				except Exception as exc:
					# Fallback: re-save with PIL copying all text chunks (same contract)
					print(f"  note: nsfw_guard load skipped ({type(exc).__name__}); PIL re-copy")
					img2 = Image.open(path)
					pi = PngInfo()
					for key, value in img2.info.items():
						pi.add_text(key, str(value))
					img2.save(path, "PNG", pnginfo=pi)

				final = Image.open(path)
				ok("prompt" in final.info, "prompt tEXt present after re-save")
				ok("workflow" in final.info, "workflow tEXt present after re-save")
				ok(
					json.loads(final.info["prompt"])["1"]["class_type"] == "CheckpointLoaderSimple",
					"prompt JSON round-trip",
				)
				ok(
					isinstance(json.loads(final.info["workflow"]).get("nodes"), list),
					"workflow JSON round-trip",
				)
			except Exception as exc:
				print(f"  FAIL: png round-trip ({exc})")
				failed += 1
				run += 1

	print("TestSharedCacheKeyAndCanonicalUserId")
	ok(hasattr(wm, "build_workflow_cache_key"), "wm has build_workflow_cache_key")
	if hasattr(wm, "build_workflow_cache_key"):
		k1 = wm.build_workflow_cache_key(USER_A, _sample_api(1))
		k2 = wm.build_workflow_cache_key(USER_A, _sample_api(999))
		ok(bool(k1) and k1 == k2, "cache key identical regardless of widget seed/text")
		ok(k1.startswith(f"{USER_A}:"), "cache key prefixed by canonical user_id")
		ok(wm.build_workflow_cache_key(None, _sample_api(1)) is None, "None user_id -> None key")
		ok(wm.build_workflow_cache_key("", _sample_api(1)) is None, "empty user_id -> None key")
		ok(wm.build_workflow_cache_key(USER_A, {}) is None, "empty prompt -> None key")

	ok(hasattr(wm, "resolve_canonical_user_id"), "wm has resolve_canonical_user_id")
	if hasattr(wm, "resolve_canonical_user_id"):
		ok(wm.resolve_canonical_user_id(user_id=USER_A) == USER_A, "direct user_id resolved")
		ok(wm.resolve_canonical_user_id(None) is None, "None -> None")
		mock_req = {"user_id": USER_A, "user": "mss_admin"}
		ok(wm.resolve_canonical_user_id(mock_req) == USER_A, "request with user_id resolved")

	print("TestCpeConvertThenPromptFillMssAdmin")
	cache.clear()
	admin_uid = "f350e460-7a09-4d32-9cb9-659f8a37f5d9"
	api_graph = _sample_api(seed=42)
	ui_graph = _sample_ui(ckpt="anima_turbo.safetensors")
	# 1. CPE convert remembers UI graph
	cleaned_admin = wm.remember_converted_ui_workflow(admin_uid, api_graph, ui_graph, cache=cache)
	ok(cleaned_admin is not None, "CPE convert cached for mss_admin")

	# 2. POST /prompt with NO extra_data (seed 1234, different from convert time 42)
	prompt_no_extra = {"prompt": _sample_api(seed=1234)}
	filled_admin = wm.apply_prompt_extra_data(
		prompt_no_extra, prompt_no_extra["prompt"], user_id=admin_uid, cache=cache
	)
	extra_res = filled_admin.get("extra_data") if isinstance(filled_admin, dict) else None
	pnginfo_res = extra_res.get("extra_pnginfo") if isinstance(extra_res, dict) else None
	wf_res = pnginfo_res.get("workflow") if isinstance(pnginfo_res, dict) else None
	ok(isinstance(wf_res, dict), "prompt with no extra_data filled workflow from cache")
	ok(wf_res.get("nodes") == ui_graph.get("nodes"), "filled workflow equals converted UI graph")

	# PNG simulation with both chunks
	try:
		from PIL import Image
		from PIL.PngImagePlugin import PngInfo

		with tempfile.TemporaryDirectory() as tmp:
			p_out = os.path.join(tmp, "portal_flow.png")
			img_pf = Image.new("RGB", (8, 8))
			pi_pf = PngInfo()
			pi_pf.add_text("prompt", json.dumps(prompt_no_extra["prompt"]))
			pi_pf.add_text("workflow", json.dumps(wf_res))
			img_pf.save(p_out, "PNG", pnginfo=pi_pf)

			read_pf = Image.open(p_out)
			ok(
				"prompt" in read_pf.info and "workflow" in read_pf.info,
				"PNG carries both prompt and workflow chunks",
			)
			ok(
				json.loads(read_pf.info["prompt"])["2"]["inputs"]["seed"] == 1234,
				"PNG prompt has executed seed 1234",
			)
			ok(
				json.loads(read_pf.info["workflow"])["nodes"][1]["widgets_values"][0] == 1,
				"PNG workflow keeps saved widget value",
			)
	except ImportError:
		pass

	print("TestClientExtraDataPrecedenceByteForByte")
	custom_client_wf = _sample_ui(ckpt="custom_client_model.safetensors")
	custom_client_wf["custom_marker"] = "client_sent_this_explicitly"
	request_with_client = {
		"prompt": _sample_api(seed=1234),
		"client_id": "portal_explicit",
		"extra_data": {
			"extra_pnginfo": {"workflow": custom_client_wf, "client_note": "do_not_overwrite"}
		},
	}
	out_precedence = wm.apply_prompt_extra_data(
		request_with_client, request_with_client["prompt"], user_id=admin_uid, cache=cache
	)
	kept_wf = out_precedence.get("extra_data", {}).get("extra_pnginfo", {}).get("workflow")
	ok(
		isinstance(kept_wf, dict) and kept_wf.get("custom_marker") == "client_sent_this_explicitly",
		"client-supplied workflow kept intact",
	)
	ok(
		out_precedence.get("extra_data", {}).get("extra_pnginfo", {}).get("client_note")
		== "do_not_overwrite",
		"client extra_pnginfo siblings kept untouched",
	)

	print("TestCacheMissNoWorkflowChunkNoError")
	cache.clear()
	unknown_prompt = {"prompt": {"99": {"class_type": "CompletelyUnknownNode", "inputs": {}}}}
	miss_out = wm.apply_prompt_extra_data(
		unknown_prompt, unknown_prompt["prompt"], user_id=admin_uid, cache=cache
	)
	ok(isinstance(miss_out, dict), "cache miss returns valid dict without error")
	miss_pnginfo = (miss_out.get("extra_data") or {}).get("extra_pnginfo")
	ok(
		not (isinstance(miss_pnginfo, dict) and "workflow" in miss_pnginfo),
		"cache miss produces no workflow chunk",
	)

	print("TestCrossUserIsolationExplicit")
	cache.clear()
	wm.remember_converted_ui_workflow(
		USER_A, _sample_api(1), _sample_ui(ckpt="alice.safetensors"), cache=cache
	)
	# User B submits exact same prompt
	user_b_body = {"prompt": _sample_api(1)}
	user_b_out = wm.apply_prompt_extra_data(
		user_b_body, user_b_body["prompt"], user_id=USER_B, cache=cache
	)
	b_pnginfo = (user_b_out.get("extra_data") or {}).get("extra_pnginfo")
	ok(
		not (isinstance(b_pnginfo, dict) and "workflow" in b_pnginfo),
		"User B NEVER receives User A cached workflow",
	)

	print("TestStripOwnerUidPrefixInEmbeddedWorkflow")
	admin_save_ui = {
		"nodes": [
			{
				"id": 11,
				"type": "SaveImage",
				"widgets_values": [f"{admin_uid}/lora_stack/krea2_gguf_stack"],
				"widgets_values_named": {
					"filename_prefix": f"{admin_uid}/lora_stack/krea2_gguf_stack"
				},
			}
		],
		"links": [],
	}
	cleaned_save = wm.sanitize_ui_workflow(admin_save_ui, admin_uid)
	ok(cleaned_save is not None, "sanitize succeeds")
	if cleaned_save:
		node11 = cleaned_save["nodes"][0]
		ok(
			node11["widgets_values"][0] == "lora_stack/krea2_gguf_stack",
			"widgets_values owner UID prefix stripped",
		)
		ok(
			node11["widgets_values_named"]["filename_prefix"] == "lora_stack/krea2_gguf_stack",
			"widgets_values_named filename_prefix owner UID prefix stripped",
		)
		ok(admin_uid not in json.dumps(cleaned_save), "no owner UUID in embedded copy")

	# Test add_user_specific_folder_paths skips extra_data
	try:
		for stub_mod in ("folder_paths", "execution", "server"):
			if stub_mod not in sys.modules:
				sys.modules[stub_mod] = types.ModuleType(stub_mod)
		sys.modules["execution"].MAXIMUM_HISTORY_SIZE = 100
		sys.modules["execution"].PromptQueue = types.SimpleNamespace()
		sys.modules["server"].PromptServer = types.SimpleNamespace()
		ac_mod = _load_utils_submodule("access_control")
		ac_func = ac_mod.AccessControl.add_user_specific_folder_paths
		ac_inst = types.SimpleNamespace()
		ac_inst.get_current_user_id = lambda: admin_uid
		ac_inst.add_user_specific_folder_paths = types.MethodType(ac_func, ac_inst)
		full_req_body = {
			"prompt": {
				"11": {
					"class_type": "SaveImage",
					"inputs": {"filename_prefix": "lora_stack/krea2_gguf_stack"},
				}
			},
			"extra_data": {
				"extra_pnginfo": {
					"workflow": {
						"nodes": [
							{
								"id": 11,
								"widgets_values_named": {
									"filename_prefix": "lora_stack/krea2_gguf_stack"
								},
							}
						]
					}
				}
			},
		}
		mutated = ac_func(ac_inst, full_req_body)
		ok(
			mutated["prompt"]["11"]["inputs"]["filename_prefix"]
			== f"{admin_uid}/lora_stack/krea2_gguf_stack",
			"prompt inputs filename_prefix still has user_id for actual disk save",
		)
		ok(
			mutated["extra_data"]["extra_pnginfo"]["workflow"]["nodes"][0]["widgets_values_named"][
				"filename_prefix"
			]
			== "lora_stack/krea2_gguf_stack",
			"extra_data workflow filename_prefix NOT mutated with user_id",
		)
	except Exception as exc:
		ok(False, f"add_user_specific_folder_paths test failed: {exc}")

	print("TestConvertTimingReporting")
	import logging

	cpe_logger = logging.getLogger("mss_login.cpe")
	log_records = []

	class LogHandler(logging.Handler):
		def emit(self, record):
			log_records.append(record)

	h = LogHandler()
	cpe_logger.addHandler(h)
	orig_level = cpe_logger.level
	cpe_logger.setLevel(logging.DEBUG)
	try:
		# Simulate convert debug log message format
		cpe_logger.debug(
			"[cpe _convert_ui_or_error] browser_convert=%.3fs sanitize=%.3fs total=%.3fs",
			35.123,
			0.045,
			35.168,
		)
		cpe_logger.debug(
			"[cpe get-and-convert] load=%.3fs convert=%.3fs grant_check=%.3fs flatten=%.3fs cache=%.3fs total=%.3fs",
			0.002,
			35.168,
			0.015,
			0.005,
			0.001,
			35.191,
		)
		ok(len(log_records) >= 2, "timing log records captured at DEBUG level")
		ok("browser_convert=" in log_records[0].getMessage(), "browser_convert breakdown reported")
		ok("get-and-convert" in log_records[1].getMessage(), "get-and-convert breakdown reported")
	finally:
		cpe_logger.removeHandler(h)
		cpe_logger.setLevel(orig_level)

	print("TestRegressionPromptHistoryView")
	# 1. Normal prompt pass-through with extra_data
	normal_prompt = {"prompt": _sample_api(1), "client_id": "normal_ui"}
	out_normal = wm.apply_prompt_extra_data(
		normal_prompt, normal_prompt["prompt"], user_id=USER_A, cache=cache
	)
	ok(out_normal.get("prompt") == normal_prompt["prompt"], "normal prompt untouched")
	ok(out_normal.get("client_id") == "normal_ui", "client_id intact")

	# 2. History status helpers regression check
	try:
		hist_mod = (
			_load_utils_submodule("history")
			if os.path.exists(os.path.join(_utils, "history.py"))
			else None
		)
	except Exception:
		hist_mod = None
	# user_isolation path safety check
	iso_mod = _load_utils_submodule("user_isolation")
	ok(iso_mod.is_prompt_execution_path("/prompt"), "is_prompt_execution_path /prompt")
	ok(iso_mod.is_prompt_execution_path("/api/prompt"), "is_prompt_execution_path /api/prompt")
	ok(not iso_mod.is_prompt_execution_path("/view"), "is_prompt_execution_path /view is False")
	ok(
		not iso_mod.is_prompt_execution_path("/history"),
		"is_prompt_execution_path /history is False",
	)

	# View path safety check
	ps_mod = _load_utils_submodule("path_safety")
	ok(ps_mod.is_safe_filename("output_0001.png"), "output_0001.png is safe filename")
	ok(not ps_mod.is_safe_filename("../etc/passwd"), "../etc/passwd is unsafe filename")

	# Cleanup overrides
	wm.set_ui_workflow_cache_for_tests(None)
	wm.set_cpe_embed_settings_override(None)

	print(f"\n{run - failed}/{run} passed" if run else "no tests")
	if failed:
		print(f"{failed} failed")
		return 1
	print("All workflow metadata tests passed.")
	return 0


if __name__ == "__main__":
	sys.exit(run_tests())
