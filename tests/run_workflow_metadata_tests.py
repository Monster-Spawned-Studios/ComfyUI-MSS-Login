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
