"""
Standalone tests for ntfy token precedence, signed actions, and quarantine retention.
"""

import os
import shutil
import json
import hmac
import hashlib
import importlib.util
import sys
import tempfile
import types
import uuid

_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_UTILS_DIR = os.path.join(_PROJECT_ROOT, "utils")


def _load_module(name: str, path: str, package: str):
	spec = importlib.util.spec_from_file_location(name, path)
	mod = importlib.util.module_from_spec(spec)
	mod.__package__ = package
	assert spec and spec.loader
	sys.modules[name] = mod
	spec.loader.exec_module(mod)
	return mod


def _install_stub_packages():
	root_pkg = types.ModuleType("mss_login")
	root_pkg.__path__ = [_PROJECT_ROOT]
	utils_pkg = types.ModuleType("mss_login.utils")
	utils_pkg.__path__ = [_UTILS_DIR]
	sys.modules["mss_login"] = root_pkg
	sys.modules["mss_login.utils"] = utils_pkg


_install_stub_packages()
const_mod = types.ModuleType("mss_login.constants")
const_mod.DEBUG_MODE = False
const_mod.NTFY_API_KEY = "env-fallback-token"
const_mod.SECRET_KEY = "test-secret-key"
const_mod.CONFIG_FILE_PATH = os.path.join(_PROJECT_ROOT, "tests", "tmp-config.json")
const_mod.DATA_DIR = os.path.join(_PROJECT_ROOT, "tests", "tmp-data")
const_mod.get_domain = lambda use_https=True, use_port=False, port=8188: "https://localhost"
sys.modules["mss_login.constants"] = const_mod
json_utils = _load_module(
	"mss_login.utils.json_utils", os.path.join(_UTILS_DIR, "json_utils.py"), "mss_login.utils"
)
sys.modules["mss_login.utils.json_utils"] = json_utils
ntfy_notifier = _load_module(
	"mss_login.utils.ntfy_notifier", os.path.join(_UTILS_DIR, "ntfy_notifier.py"), "mss_login.utils"
)
quarantine_store = _load_module(
	"mss_login.utils.quarantine_store",
	os.path.join(_UTILS_DIR, "quarantine_store.py"),
	"mss_login.utils",
)


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

	print("TestApiKeyPrecedence")
	ok(
		ntfy_notifier._resolve_api_key("explicit-token", "cfg-token") == "explicit-token",
		"explicit api_key wins",
	)
	ok(
		ntfy_notifier._resolve_api_key("", "cfg-token") == "cfg-token",
		"config token used when explicit missing",
	)
	fallback = ntfy_notifier._resolve_api_key("", "")
	ok(
		fallback == (ntfy_notifier.NTFY_API_KEY or "").strip(),
		"env token fallback used when explicit/config missing",
	)

	print("TestNsfwSeverityMapping")
	ok(ntfy_notifier.nsfw_score_to_severity(0.0) == 1, "0.0 maps to 1/10")
	ok(ntfy_notifier.nsfw_score_to_severity(0.01) == 1, "near-zero maps to 1/10")
	ok(ntfy_notifier.nsfw_score_to_severity(0.55) == 6, "0.55 maps to 6/10")
	ok(ntfy_notifier.nsfw_score_to_severity(1.0) == 10, "1.0 maps to 10/10")
	ok(ntfy_notifier.nsfw_score_to_severity(None) is None, "None score stays None")

	print("TestSignedActionToken")
	token = ntfy_notifier.create_signed_action_token(
		{"action": "x", "path": "/tmp/a"}, ttl_seconds=120
	)
	payload = ntfy_notifier.verify_signed_action_token(token)
	ok(isinstance(payload, dict), "signed token verifies")
	ok(payload.get("path") == "/tmp/a", "payload round-trips")
	expired_payload = dict(payload or {})
	expired_payload["exp"] = 1
	expired_bytes = json.dumps(expired_payload, separators=(",", ":"), sort_keys=True).encode(
		"utf-8"
	)
	expired_sig = hmac.new(
		ntfy_notifier.SECRET_KEY.encode("utf-8"), expired_bytes, hashlib.sha256
	).digest()
	expired = (
		f"{ntfy_notifier._b64url_encode(expired_bytes)}.{ntfy_notifier._b64url_encode(expired_sig)}"
	)
	ok(ntfy_notifier.verify_signed_action_token(expired) is None, "expired token rejected")

	print("TestNtfyConfigPersistence")
	config_path = const_mod.CONFIG_FILE_PATH
	os.makedirs(os.path.dirname(config_path), exist_ok=True)
	try:
		with open(config_path, "w", encoding="utf-8") as f:
			json.dump({}, f)
		ntfy_notifier.save_ntfy_config(
			"topic-a",
			["image_generated"],
			base_url="https://ntfy.example.com",
			api_token="cfg-token",
		)
		cfg = ntfy_notifier._load_ntfy_config()
		ok(cfg.get("base_url") == "https://ntfy.example.com", "custom base_url saved")
		ok(cfg.get("api_token") == "cfg-token", "api_token saved")
		ntfy_notifier.save_ntfy_config("topic-a", ["image_generated"], base_url=None, api_token=None)
		cfg2 = ntfy_notifier._load_ntfy_config()
		ok(
			cfg2.get("base_url") == "https://ntfy.example.com",
			"partial save preserves base_url when omitted",
		)
		ok(cfg2.get("api_token") == "cfg-token", "partial save preserves api_token when omitted")
		ntfy_notifier.save_ntfy_config("topic-a", [], base_url="")
		cfg3 = ntfy_notifier._load_ntfy_config()
		ok(cfg3.get("base_url") == "https://ntfy.sh", "explicit empty base_url resets to default")
		req = ntfy_notifier._build_request(
			base_url=cfg.get("base_url", ""),
			topic="my-topic",
			event_key="image_generated",
			title="t",
			message="m",
			priority="default",
			tags=None,
			click="",
			icon="",
			markdown=False,
			attachment_url="",
			attachment_file_path="",
			attachment_filename="",
			actions=None,
			delay="",
			email="",
			api_key="",
			timeout=5,
		)
		ok(
			req.get("url") == "https://ntfy.example.com/my-topic",
			"_build_request uses custom base_url in publish URL",
		)
		ok(
			"shared_items_removed" in ntfy_notifier.EVENT_KEYS,
			"shared_items_removed is a toggleable event key",
		)
	finally:
		if os.path.isfile(config_path):
			os.remove(config_path)

	print("TestQuarantineRetentionCleanup")
	tmp_root = tempfile.mkdtemp(prefix="mss_quarantine_test_")
	try:
		images_dir = os.path.join(tmp_root, "images")
		records_path = os.path.join(tmp_root, "records.json")
		orig_get_paths = quarantine_store._get_paths
		quarantine_store._get_paths = lambda: {
			"root": tmp_root,
			"images_dir": images_dir,
			"records_path": records_path,
		}

		source_dir = os.path.join(tmp_root, "source")
		os.makedirs(source_dir, exist_ok=True)
		source_file = os.path.join(source_dir, f"{uuid.uuid4().hex}.png")
		with open(source_file, "wb") as f:
			f.write(b"test")

		result = quarantine_store.quarantine_image_file(
			source_path=source_file,
			username="owner",
			workflow_name="wf",
			generated_at="2026-04-12T00:00:00Z",
			score=0.93,
			severity=10,
			retention_days=30,
		)
		ok(result.get("status") == "ok", "file quarantined successfully")
		record = result.get("record") or {}
		quarantined_path = record.get("quarantined_path")
		ok(bool(quarantined_path and os.path.isfile(quarantined_path)), "quarantined file exists")
		cleanup = quarantine_store.cleanup_expired_quarantine(
			now_ts=int(record.get("delete_after_ts", 0)) + 5
		)
		ok(cleanup.get("deleted", 0) >= 1, "expired unreviewed quarantined file deleted")
	finally:
		quarantine_store._get_paths = orig_get_paths
		shutil.rmtree(tmp_root, ignore_errors=True)

	print()
	if failed:
		print(f"Result: {failed} failed, {run - failed} passed, {run} total")
		raise SystemExit(1)
	print(f"Result: all {run} tests passed")
	raise SystemExit(0)


if __name__ == "__main__":
	run_tests()
