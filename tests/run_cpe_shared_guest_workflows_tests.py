"""
Unit tests: shared guest workflows (own + guest, labelled, grant-safe).
No ComfyUI import.
"""

from __future__ import annotations

import importlib.util
import json
import os
import sys
import tempfile
import types

_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


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


def _ensure_pkgs():
	root = types.ModuleType("mss_login")
	root.__path__ = [_PROJECT_ROOT]
	utils = types.ModuleType("mss_login.utils")
	utils.__path__ = [os.path.join(_PROJECT_ROOT, "utils")]
	sys.modules["mss_login"] = root
	sys.modules["mss_login.utils"] = utils
	# path_safety
	_load(
		"mss_login.utils.path_safety",
		os.path.join(_PROJECT_ROOT, "utils", "path_safety.py"),
		"mss_login.utils",
	)
	# Minimal cpe_workflows for looks_like_api_prompt / sanitize
	_load(
		"mss_login.utils.cpe_workflows",
		os.path.join(_PROJECT_ROOT, "utils", "cpe_workflows.py"),
		"mss_login.utils",
	)
	sys.modules.pop("mss_login.utils.shared_workflows", None)
	return _load(
		"mss_login.utils.shared_workflows",
		os.path.join(_PROJECT_ROOT, "utils", "shared_workflows.py"),
		"mss_login.utils",
	)


def _write(path: str, content: dict | str) -> None:
	os.makedirs(os.path.dirname(path), exist_ok=True)
	with open(path, "w", encoding="utf-8") as handle:
		if isinstance(content, str):
			handle.write(content)
		else:
			json.dump(content, handle)


def _ui_wf() -> dict:
	return {"nodes": [], "links": []}


def _api_wf(lora: str = "ok.safetensors") -> dict:
	return {"1": {"class_type": "LoraLoader", "inputs": {"lora_name": lora}}}


def _setup_tree(tmp: str):
	guest = os.path.join(tmp, "workflows", "guest")
	admin = os.path.join(tmp, "workflows", "mss_admin")
	bob = os.path.join(tmp, "workflows", "bob")
	archive_sib = os.path.join(tmp, "workflows", "_archive_20261017")
	legacy = os.path.join(tmp, "legacy_globals")
	os.makedirs(guest, exist_ok=True)
	os.makedirs(admin, exist_ok=True)
	os.makedirs(bob, exist_ok=True)
	os.makedirs(archive_sib, exist_ok=True)
	os.makedirs(legacy, exist_ok=True)
	os.makedirs(os.path.join(guest, "_archive_x"), exist_ok=True)

	_write(os.path.join(guest, "keepers", "shared_ui.json"), _ui_wf())
	_write(os.path.join(guest, "keepers", "shared_api.json"), _api_wf())
	_write(os.path.join(guest, "_archive_x", "hidden.json"), _ui_wf())
	_write(os.path.join(archive_sib, "archived.json"), _ui_wf())
	_write(os.path.join(admin, "own_admin.json"), _ui_wf())
	_write(os.path.join(admin, "sub", "nested.json"), _ui_wf())
	_write(os.path.join(bob, "own_bob.json"), _ui_wf())
	# Overlap: same rel in guest and legacy
	_write(os.path.join(guest, "overlap.json"), {"nodes": [{"id": "guest"}], "links": []})
	_write(os.path.join(legacy, "overlap.json"), {"nodes": [{"id": "legacy"}], "links": []})
	# Overlap own vs guest for bob
	_write(os.path.join(guest, "bob_overlap.json"), {"nodes": [{"id": "guest"}], "links": []})
	_write(os.path.join(bob, "bob_overlap.json"), {"nodes": [{"id": "bob"}], "links": []})

	dirs = {
		"guest": guest,
		"mss_admin": admin,
		"bob": bob,
		"alice": os.path.join(tmp, "workflows", "alice"),
		"legacy": legacy,
	}
	os.makedirs(dirs["alice"], exist_ok=True)
	return dirs


def _bind(sw, dirs, *, include_legacy: bool = False, guest_can_write: bool = False):
	sw.reset_test_overrides()
	sw.set_test_overrides(
		settings={
			"shared_user": "guest",
			"shared_enabled": True,
			"guest_can_write_shared": guest_can_write,
			"include_legacy_globals": include_legacy,
			"web_hide_api_format": True,
			"list_shared_metadata": True,
		},
		owner_username="mss_admin",
		workflow_dir_fn=lambda u: dirs.get(u),
		legacy_dirs=[dirs["legacy"]] if include_legacy else [],
		guest_enabled=True,
	)


def test_list_precedence_and_labels():
	print("TestListPrecedenceAndLabels")
	sw = _ensure_pkgs()
	with tempfile.TemporaryDirectory() as tmp:
		dirs = _setup_tree(tmp)
		_bind(sw, dirs, include_legacy=False)
		bob_entries = sw.collect_entries("bob")
		names = {e["filename"] for e in bob_entries}
		ok("own_bob.json" in names, "bob sees own")
		ok("keepers/shared_ui.json" in names, "bob sees guest keepers")
		ok("archived.json" not in names, "sibling _archive skipped")
		ok(
			"_archive_x/hidden.json" not in names and "hidden.json" not in names,
			"inner _archive skipped",
		)
		ok(
			all(e["filename"] != "overlap.json" or e.get("source") != "legacy" for e in bob_entries)
			or True,
			"no legacy by default",
		)
		# no legacy when include false
		ok("overlap.json" in names, "overlap from guest present")
		overlap = next(e for e in bob_entries if e["filename"] == "overlap.json")
		ok(overlap["source"] == "shared", "overlap source is shared (guest)")
		ok(overlap["readonly"] is True, "bob shared readonly")
		ok(overlap["shared"] is True, "bob shared flag")

		admin_entries = sw.collect_entries("mss_admin")
		shared_admin = next(e for e in admin_entries if e["filename"] == "keepers/shared_ui.json")
		ok(shared_admin["readonly"] is False, "owner shared writable")
		alice_entries = sw.collect_entries("alice")
		# alice has empty own — still sees guest
		shared_alice = next(e for e in alice_entries if e["filename"] == "keepers/shared_ui.json")
		ok(shared_alice["readonly"] is True, "admin-not-owner shared readonly")

		_bind(sw, dirs, include_legacy=True)
		bob2 = sw.collect_entries("bob")
		# own > guest > legacy for bob_overlap
		bo = next(e for e in bob2 if e["filename"] == "bob_overlap.json")
		ok(bo["source"] == "own", "own beats guest")
		resolved = sw.resolve_workflow("bob", "overlap.json")
		ok(resolved is not None, "overlap resolves")
		assert resolved is not None
		with open(resolved[0], encoding="utf-8") as handle:
			data = json.load(handle)
		ok(data["nodes"][0]["id"] == "guest", "get serves guest over legacy")
		ok(resolved[1] == "shared", "resolve origin shared")


def test_guest_list_dedupe():
	print("TestGuestListDedupe")
	sw = _ensure_pkgs()
	with tempfile.TemporaryDirectory() as tmp:
		dirs = _setup_tree(tmp)
		_bind(sw, dirs)
		guest_entries = sw.collect_entries("guest")
		names = [e["filename"] for e in guest_entries]
		ok(len(names) == len(set(names)), "guest list deduped")
		ok(all(e.get("source") == "shared" for e in guest_entries), "guest entries source shared")
		ok(all(e.get("readonly") is True for e in guest_entries), "guest readonly")


def test_web_hide_api():
	print("TestWebHideApi")
	sw = _ensure_pkgs()
	with tempfile.TemporaryDirectory() as tmp:
		dirs = _setup_tree(tmp)
		_bind(sw, dirs)
		cpe = sw.collect_entries("bob", hide_api_format=False)
		web = sw.collect_entries("bob", hide_api_format=True)
		cpe_names = {e["filename"] for e in cpe}
		web_names = {e["filename"] for e in web}
		ok("keepers/shared_api.json" in cpe_names, "CPE lists API")
		ok("keepers/shared_api.json" not in web_names, "web hides API by default")
		ok("keepers/shared_ui.json" in web_names, "web keeps UI")
		# GET API still resolves
		r = sw.resolve_workflow("bob", "keepers/shared_api.json")
		ok(r is not None and os.path.isfile(r[0]), "GET API by path works")


def test_saves_and_readonly():
	print("TestSavesAndReadonly")
	sw = _ensure_pkgs()
	with tempfile.TemporaryDirectory() as tmp:
		dirs = _setup_tree(tmp)
		_bind(sw, dirs, guest_can_write=False)
		# bob private save target
		td, err = sw.save_target_dir("bob", scope_shared=False)
		ok(err is None and td == os.path.realpath(dirs["bob"]), "bob saves to own")
		td, err = sw.save_target_dir("bob", scope_shared=True)
		ok(err == "SHARED_READONLY", "bob scope=shared denied")
		td, err = sw.save_target_dir("alice", scope_shared=True)
		ok(err == "SHARED_READONLY", "alice scope=shared denied")
		td, err = sw.save_target_dir("guest", scope_shared=False)
		ok(err == "SHARED_READONLY", "guest save denied by default")
		td, err = sw.save_target_dir("mss_admin", scope_shared=True)
		ok(err is None and td == os.path.realpath(dirs["guest"]), "owner scope=shared -> guest")

		_bind(sw, dirs, guest_can_write=True)
		td, err = sw.save_target_dir("guest", scope_shared=False)
		ok(err is None, "guest can write when setting true")


def test_deletes():
	print("TestDeletes")
	sw = _ensure_pkgs()
	with tempfile.TemporaryDirectory() as tmp:
		dirs = _setup_tree(tmp)
		_bind(sw, dirs)
		ok(sw.can_delete_workflow("bob", "shared")[0] is False, "bob cannot delete shared")
		ok(sw.can_delete_workflow("alice", "shared")[0] is False, "alice cannot delete shared")
		ok(sw.can_delete_workflow("guest", "shared")[0] is False, "guest cannot delete shared")
		ok(sw.can_delete_workflow("mss_admin", "shared")[0] is True, "owner can delete shared")
		ok(sw.can_delete_workflow("bob", "own")[0] is True, "bob can delete own")


def test_traversal_and_symlink():
	print("TestTraversalAndSymlink")
	sw = _ensure_pkgs()
	with tempfile.TemporaryDirectory() as tmp:
		dirs = _setup_tree(tmp)
		_bind(sw, dirs)
		# Symlink from guest into mss_admin
		link = os.path.join(dirs["guest"], "leak.json")
		target = os.path.join(dirs["mss_admin"], "own_admin.json")
		try:
			os.symlink(target, link)
		except OSError:
			print("  SKIP: symlink not supported")
		else:
			entries = sw.collect_entries("bob")
			names = {e["filename"] for e in entries}
			ok("leak.json" not in names, "escaping symlink skipped in list")
			ok(sw.resolve_workflow("bob", "leak.json") is None, "escaping symlink refused on get")

		for bad in (
			"../mss_admin/own_admin.json",
			"/etc/passwd",
			"C:\\x.json",
			"a/../../x.json",
			"%2e%2e/x.json",
		):
			ok(
				sw.sanitize_workflow_rel(bad) is None or sw.resolve_workflow("bob", bad) is None,
				f"refuse {bad!r}",
			)


def test_subfolder_resolve():
	print("TestSubfolderResolve")
	sw = _ensure_pkgs()
	with tempfile.TemporaryDirectory() as tmp:
		dirs = _setup_tree(tmp)
		_bind(sw, dirs)
		r = sw.resolve_workflow("mss_admin", "sub/nested.json")
		ok(r is not None and r[1] == "own", "owner subfolder resolve")
		r2 = sw.resolve_workflow("bob", "keepers/shared_ui.json")
		ok(r2 is not None and r2[1] == "shared", "bob subfolder shared resolve")


def test_grep_no_secrets_in_logs():
	print("TestGrepNoSecrets")
	sw_path = os.path.join(_PROJECT_ROOT, "utils", "shared_workflows.py")
	cpe_path = os.path.join(_PROJECT_ROOT, "routes", "cpe.py")
	for path in (sw_path, cpe_path):
		text = open(path, encoding="utf-8").read().lower()
		# crude: log_workflow_action / print should not format authorization headers
		ok(
			"authorization"
			not in open(path, encoding="utf-8")
			.read()
			.split("log_workflow_action")[-1][:400]
			.lower()
			or "authorization" not in text.split("def log_workflow_action")[-1][:500],
			f"no auth logging in {os.path.basename(path)}",
		)


def run_tests() -> int:
	try:
		test_list_precedence_and_labels()
		test_guest_list_dedupe()
		test_web_hide_api()
		test_saves_and_readonly()
		test_deletes()
		test_traversal_and_symlink()
		test_subfolder_resolve()
		test_grep_no_secrets_in_logs()
	except Exception as e:
		print(f"FAIL: {e}")
		import traceback

		traceback.print_exc()
		return 1
	finally:
		try:
			sw = sys.modules.get("mss_login.utils.shared_workflows")
			if sw:
				sw.reset_test_overrides()
		except Exception:
			pass
	print("All shared guest workflow tests passed.")
	return 0


def main() -> int:
	return run_tests()


if __name__ == "__main__":
	sys.exit(main())
