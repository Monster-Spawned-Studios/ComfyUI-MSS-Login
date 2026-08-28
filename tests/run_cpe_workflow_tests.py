r"""
Standalone tests for Comfy Portal Endpoint compatible per-user workflow helpers.

Use the project .venv (see .agent/rules/python-venv.mdc):
  .venv/bin/python tests/run_cpe_workflow_tests.py
"""

import importlib.util
import json
import os
import sys
import tempfile

_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_utils = os.path.join(_PROJECT_ROOT, "utils")


def _load_utils_submodule(mod_name):
	import types

	if "utils" not in sys.modules:
		pkg = types.ModuleType("utils")
		pkg.__path__ = [_utils]
		pkg.__package__ = "utils"
		sys.modules["utils"] = pkg
	spec = importlib.util.spec_from_file_location(
		f"utils.{mod_name}", os.path.join(_utils, f"{mod_name}.py")
	)
	mod = importlib.util.module_from_spec(spec)
	mod.__package__ = "utils"
	sys.modules[f"utils.{mod_name}"] = mod
	assert spec and spec.loader
	spec.loader.exec_module(mod)
	return mod


def run_tests():
	_load_utils_submodule("path_safety")
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

	print("TestCpePathHelpers")
	ok(cpe.is_cpe_path("/api/cpe/workflow/list"), "matches /api/cpe/workflow/list")
	ok(cpe.is_cpe_path("/cpe/workflow/list"), "matches /cpe/workflow/list")
	ok(cpe.is_cpe_path("/api/cpe/health"), "matches /api/cpe/health")
	ok(not cpe.is_cpe_path("/api/userdata"), "does not match userdata")
	ok(cpe.is_cpe_workflow_mutating_path("/api/cpe/workflow/save"), "save is mutating")
	ok(not cpe.is_cpe_workflow_mutating_path("/api/cpe/workflow/list"), "list is not mutating")

	print("TestSanitizeCpeFilename")
	ok(cpe.sanitize_cpe_filename("demo") == "demo.json", "appends .json")
	ok(cpe.sanitize_cpe_filename("folder/demo.json") == "folder/demo.json", "allows nested")
	ok(cpe.sanitize_cpe_filename("../etc/passwd") is None, "rejects traversal")
	ok(cpe.sanitize_cpe_filename("/abs.json") is None, "rejects absolute")

	print("TestListGetSavePerUser")
	with tempfile.TemporaryDirectory() as tmp:
		alice = os.path.join(tmp, "alice")
		bob = os.path.join(tmp, "bob")
		shared = os.path.join(tmp, "shared")
		os.makedirs(alice)
		os.makedirs(bob)
		os.makedirs(shared)
		with open(os.path.join(alice, "alice.json"), "w", encoding="utf-8") as handle:
			json.dump({"1": {"class_type": "Note", "inputs": {}}}, handle)
		with open(os.path.join(shared, "shared.json"), "w", encoding="utf-8") as handle:
			json.dump({"nodes": [], "links": []}, handle)
		listed = cpe.list_workflows_payload(alice, extra_dirs=[shared])
		ok(listed["status"] == "success", "list status success")
		names = {item["filename"] for item in listed["workflows"]}
		ok("alice.json" in names, "includes user workflow")
		ok("shared.json" in names, "includes shared workflow")
		bob_list = cpe.list_workflows_payload(bob, extra_dirs=[shared])
		bob_names = {item["filename"] for item in bob_list["workflows"]}
		ok("alice.json" not in bob_names, "does not leak another user's workflows")
		ok("shared.json" in bob_names, "bob still sees shared")

		status, body = cpe.read_workflow_text(alice, "alice.json")
		ok(status == 200, "get user file 200")
		ok(body.get("filename") == "alice.json", "get returns filename")
		ok('"class_type"' in body.get("workflow", ""), "get returns raw JSON string")

		status, body = cpe.read_workflow_text(bob, "alice.json")
		ok(status == 404, "bob cannot get alice's private file")

		status, body = cpe.read_workflow_text(alice, "../bob/secret.json")
		ok(status == 400, "get rejects traversal filename")

		status, saved = cpe.save_workflow_text(
			alice,
			json.dumps({"1": {"class_type": "CheckpointLoaderSimple", "inputs": {}}}),
			"new_wf",
		)
		ok(status == 200, "save 200")
		ok(saved.get("filename") == "new_wf.json", "save returns filename")
		ok(os.path.isfile(os.path.join(alice, "new_wf.json")), "save wrote user dir")
		ok(not os.path.isfile(os.path.join(bob, "new_wf.json")), "save did not write other user")

		status, _denied = cpe.save_workflow_text(alice, "{}", "../escape.json")
		ok(status == 400, "save rejects traversal name")

		status, converted = cpe.get_and_convert_payload(alice, "new_wf.json")
		ok(status == 200, "API-format get-and-convert succeeds")
		ok("workflow" in converted.get("data", {}), "convert payload has data.workflow")

		status, ui_convert = cpe.get_and_convert_payload(alice, "shared.json", extra_dirs=[shared])
		ok(status == 503, "UI-format get-and-convert returns 503 without browser")

	print("TestLooksLikeApiPrompt")
	ok(
		cpe.looks_like_api_prompt({"1": {"class_type": "Note", "inputs": {}}}),
		"API prompt detected",
	)
	ok(not cpe.looks_like_api_prompt({"nodes": [], "links": []}), "UI graph is not API prompt")

	print("TestHealthPayload")
	health = cpe.health_payload()
	ok(health.get("status") == "success", "health status success")
	ok(health.get("browser", {}).get("status") == "ready", "health browser ready")

	print(f"\n{run - failed}/{run} passed")
	return failed == 0


if __name__ == "__main__":
	sys.exit(0 if run_tests() else 1)
