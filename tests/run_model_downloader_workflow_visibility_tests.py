r"""
Unit tests for model downloader workflow permission and visibility gating.

Ensures that shipped model downloader workflows (CivitAI, HuggingFace, Universal)
are only visible and accessible to accounts with model downloading permissions
(or admin/owner), across Comfy Portal and the web UI, and never exposed to
regular users or guests without permission.

Run with:
    .venv/bin/python tests/run_model_downloader_workflow_visibility_tests.py
"""

import json
import os
import shutil
import sys
import tempfile
import types

_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _PROJECT_ROOT not in sys.path:
	sys.path.insert(0, _PROJECT_ROOT)

# Stub ComfyUI modules if not present
for _mod in ("folder_paths", "execution", "server"):
	if _mod not in sys.modules:
		sys.modules[_mod] = types.ModuleType(_mod)
sys.modules["execution"].MAXIMUM_HISTORY_SIZE = 1000
sys.modules["execution"].PromptQueue = type("PromptQueue", (), {})
sys.modules["server"].PromptServer = type("PromptServer", (), {})


def run_tests() -> int:
	failed = 0
	total = 0

	def ok(cond: bool, msg: str) -> None:
		nonlocal failed, total
		total += 1
		if not cond:
			print(f"  FAIL: {msg}")
			failed += 1
		else:
			print(f"  OK: {msg}")

	from utils.shared_workflows import (
		collect_entries,
		is_model_downloader_workflow,
		list_payload_for_user,
		reset_test_overrides,
		resolve_workflow,
		set_test_overrides,
		user_has_download_permission,
	)
	from utils.user_env import seed_default_workflows

	# ---------------------------------------------------------
	# 1. Filename pattern matching
	# ---------------------------------------------------------
	print("\n--- 1. Model Downloader Filename Recognition ---")
	ok(
		is_model_downloader_workflow("CivitAI_Model_Downloader.json"),
		"CivitAI downloader recognized",
	)
	ok(
		is_model_downloader_workflow("HuggingFace_Model_Downloader.json"),
		"HuggingFace downloader recognized",
	)
	ok(
		is_model_downloader_workflow("Universal_Model_Downloader.json"),
		"Universal downloader recognized",
	)
	ok(
		is_model_downloader_workflow("subfolder/CivitAI_Model_Downloader.json"),
		"Subfolder CivitAI recognized",
	)
	ok(
		is_model_downloader_workflow("custom_model_downloader.json"),
		"Custom ending in _model_downloader recognized",
	)
	ok(is_model_downloader_workflow("model_downloader.json"), "model_downloader.json recognized")
	ok(not is_model_downloader_workflow("regular_workflow.json"), "Regular workflow not recognized")
	ok(not is_model_downloader_workflow("text2img.json"), "text2img not recognized")
	ok(not is_model_downloader_workflow(""), "Empty filename returns False")
	ok(not is_model_downloader_workflow(None), "None returns False")

	# ---------------------------------------------------------
	# 2. Permission Evaluation
	# ---------------------------------------------------------
	print("\n--- 2. Download Permission Evaluation ---")
	reset_test_overrides()
	ok(not user_has_download_permission(None), "None username has no download permission")
	ok(not user_has_download_permission(""), "Empty username has no download permission")
	ok(not user_has_download_permission("guest"), "Guest has no download permission")

	# Test overrides
	set_test_overrides(
		owner_username="the_owner", can_download={"allowed_user": True, "forbidden_user": False}
	)
	ok(user_has_download_permission("the_owner"), "Owner has download permission")
	ok(user_has_download_permission("allowed_user"), "Permitted user has download permission")
	ok(
		not user_has_download_permission("forbidden_user"),
		"Forbidden user has no download permission",
	)
	ok(not user_has_download_permission("guest"), "Guest still denied with overrides")

	# ---------------------------------------------------------
	# 3. Visibility and Resolution Gating
	# ---------------------------------------------------------
	print("\n--- 3. Visibility and Resolution Gating ---")
	tmp_dir = tempfile.mkdtemp()
	try:
		shipped_dir = os.path.join(tmp_dir, "shipped_workflows")
		os.makedirs(shipped_dir, exist_ok=True)
		user_allowed_dir = os.path.join(tmp_dir, "users", "allowed_user")
		user_forbidden_dir = os.path.join(tmp_dir, "users", "forbidden_user")
		guest_dir = os.path.join(tmp_dir, "users", "guest")
		os.makedirs(user_allowed_dir, exist_ok=True)
		os.makedirs(user_forbidden_dir, exist_ok=True)
		os.makedirs(guest_dir, exist_ok=True)

		# Shipped workflows
		with open(os.path.join(shipped_dir, "CivitAI_Model_Downloader.json"), "w") as f:
			json.dump({"nodes": [{"type": "CivitAI"}], "links": []}, f)
		with open(os.path.join(shipped_dir, "HuggingFace_Model_Downloader.json"), "w") as f:
			json.dump({"nodes": [{"type": "HuggingFace"}], "links": []}, f)
		with open(os.path.join(shipped_dir, "Universal_Model_Downloader.json"), "w") as f:
			json.dump({"nodes": [{"type": "Universal"}], "links": []}, f)
		with open(os.path.join(shipped_dir, "General_Template.json"), "w") as f:
			json.dump({"nodes": [{"type": "KSampler"}], "links": []}, f)

		# Even if unpermitted user manually has a downloader file placed in their own folder:
		with open(os.path.join(user_forbidden_dir, "CivitAI_Model_Downloader.json"), "w") as f:
			json.dump({"nodes": [{"type": "InOwnDir"}], "links": []}, f)
		with open(os.path.join(user_forbidden_dir, "personal.json"), "w") as f:
			json.dump({"nodes": [{"type": "Personal"}], "links": []}, f)

		# Setup shared workflow override
		def mock_wf_dir(u: str) -> str | None:
			if u == "allowed_user":
				return user_allowed_dir
			if u == "forbidden_user":
				return user_forbidden_dir
			if u == "the_owner":
				return os.path.join(tmp_dir, "users", "the_owner")
			if u == "guest":
				return guest_dir
			return None

		set_test_overrides(
			owner_username="the_owner",
			can_download={"allowed_user": True, "forbidden_user": False, "the_owner": True},
			shipped_dir=shipped_dir,
			workflow_dir_fn=mock_wf_dir,
		)

		# Permitted User listing & resolve
		allowed_entries = collect_entries("allowed_user")
		allowed_filenames = [e.get("filename") for e in allowed_entries]
		ok(
			"CivitAI_Model_Downloader.json" in allowed_filenames,
			"Permitted user sees CivitAI downloader in list",
		)
		ok(
			"HuggingFace_Model_Downloader.json" in allowed_filenames,
			"Permitted user sees HuggingFace downloader in list",
		)
		ok(
			"Universal_Model_Downloader.json" in allowed_filenames,
			"Permitted user sees Universal downloader in list",
		)
		ok("General_Template.json" in allowed_filenames, "Permitted user sees general template")

		res_allowed = resolve_workflow("allowed_user", "CivitAI_Model_Downloader.json")
		ok(res_allowed is not None, "Permitted user resolves CivitAI downloader")
		if res_allowed:
			ok(os.path.isfile(res_allowed[0]), "Resolved path is an existing file")

		# Owner listing & resolve
		owner_entries = collect_entries("the_owner")
		owner_filenames = [e.get("filename") for e in owner_entries]
		ok("CivitAI_Model_Downloader.json" in owner_filenames, "Owner sees CivitAI downloader")
		ok("Universal_Model_Downloader.json" in owner_filenames, "Owner sees Universal downloader")
		res_owner = resolve_workflow("the_owner", "Universal_Model_Downloader.json")
		ok(res_owner is not None, "Owner resolves Universal downloader")

		# Unpermitted User listing & resolve
		forbidden_entries = collect_entries("forbidden_user")
		forbidden_filenames = [e.get("filename") for e in forbidden_entries]
		ok(
			"CivitAI_Model_Downloader.json" not in forbidden_filenames,
			"Forbidden user DOES NOT see CivitAI downloader in list (even if in own dir)",
		)
		ok(
			"HuggingFace_Model_Downloader.json" not in forbidden_filenames,
			"Forbidden user DOES NOT see HuggingFace downloader in list",
		)
		ok(
			"Universal_Model_Downloader.json" not in forbidden_filenames,
			"Forbidden user DOES NOT see Universal downloader in list",
		)
		ok(
			"personal.json" in forbidden_filenames,
			"Forbidden user still sees their own personal workflow",
		)
		ok(
			"General_Template.json" in forbidden_filenames,
			"Forbidden user can see non-downloader templates",
		)

		res_forbidden = resolve_workflow("forbidden_user", "CivitAI_Model_Downloader.json")
		ok(res_forbidden is None, "Forbidden user resolution returns None for CivitAI downloader")
		res_forbidden_personal = resolve_workflow("forbidden_user", "personal.json")
		ok(
			res_forbidden_personal is not None,
			"Forbidden user can resolve regular personal workflow",
		)

		# Guest listing & resolve
		guest_entries = collect_entries("guest")
		guest_filenames = [e.get("filename") for e in guest_entries]
		ok(
			"CivitAI_Model_Downloader.json" not in guest_filenames,
			"Guest DOES NOT see CivitAI downloader in list",
		)
		ok(
			"Universal_Model_Downloader.json" not in guest_filenames,
			"Guest DOES NOT see Universal downloader in list",
		)
		ok(
			resolve_workflow("guest", "Universal_Model_Downloader.json") is None,
			"Guest resolution returns None for Universal downloader",
		)

		# CPE payload check
		cpe_allowed = list_payload_for_user("allowed_user")
		cpe_allowed_names = [w["filename"] for w in cpe_allowed.get("workflows", [])]
		ok(
			"CivitAI_Model_Downloader.json" in cpe_allowed_names,
			"CPE payload includes downloaders for permitted user",
		)

		cpe_forbidden = list_payload_for_user("forbidden_user")
		cpe_forbidden_names = [w["filename"] for w in cpe_forbidden.get("workflows", [])]
		ok(
			"CivitAI_Model_Downloader.json" not in cpe_forbidden_names,
			"CPE payload omits downloaders for unpermitted user",
		)

		from utils.cpe_workflows import read_workflow_text

		st_allowed, _ = read_workflow_text(
			"", "CivitAI_Model_Downloader.json", username="allowed_user"
		)
		ok(st_allowed == 200, "CPE read_workflow_text returns 200 for permitted user")

		st_forbidden, _ = read_workflow_text(
			"", "CivitAI_Model_Downloader.json", username="forbidden_user"
		)
		ok(st_forbidden == 404, "CPE read_workflow_text returns 404 for unpermitted user")

		# ---------------------------------------------------------
		# 4. Seeding Default Workflows Gating
		# ---------------------------------------------------------
		print("\n--- 4. Seeding Default Workflows Gating ---")
		target_permitted = os.path.join(tmp_dir, "seed_permitted")
		target_unpermitted = os.path.join(tmp_dir, "seed_unpermitted")
		os.makedirs(target_permitted, exist_ok=True)
		os.makedirs(target_unpermitted, exist_ok=True)

		seed_default_workflows(target_permitted, username="allowed_user")
		seeded_permitted_files = os.listdir(target_permitted)
		ok(
			"CivitAI_Model_Downloader.json" in seeded_permitted_files,
			"Seeding copies CivitAI downloader for permitted user",
		)
		ok(
			"Universal_Model_Downloader.json" in seeded_permitted_files,
			"Seeding copies Universal downloader for permitted user",
		)

		seed_default_workflows(target_unpermitted, username="forbidden_user")
		seeded_unpermitted_files = os.listdir(target_unpermitted)
		ok(
			"CivitAI_Model_Downloader.json" not in seeded_unpermitted_files,
			"Seeding skips CivitAI downloader for unpermitted user",
		)
		ok(
			"Universal_Model_Downloader.json" not in seeded_unpermitted_files,
			"Seeding skips Universal downloader for unpermitted user",
		)
		ok(
			"HuggingFace_Model_Downloader.json" not in seeded_unpermitted_files,
			"Seeding skips HuggingFace downloader for unpermitted user",
		)

	finally:
		reset_test_overrides()
		shutil.rmtree(tmp_dir, ignore_errors=True)

	print("\n==========================================")
	print(f"Results: {total - failed}/{total} passed")
	if failed > 0:
		print(f"FAILED: {failed} test(s)")
		return 1
	print("ALL MODEL DOWNLOADER WORKFLOW VISIBILITY TESTS PASSED!")
	return 0


if __name__ == "__main__":
	sys.exit(run_tests())
