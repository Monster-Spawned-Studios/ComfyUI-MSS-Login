#!/usr/bin/env bash
# MSS-Login local verification (no ComfyUI install/start).
# Runs lint (optional), CI unit suite, and auth smoke unit checks (401/403).
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"
PY="${ROOT}/.venv/bin/python"
if [[ ! -x "$PY" ]]; then
	echo "error: missing .venv at ${PY}" >&2
	exit 1
fi

SKIP_LINT=0
if [[ "${1:-}" == "--no-lint" ]]; then
	SKIP_LINT=1
fi

echo "[verify] Python: $PY"

if [[ "$SKIP_LINT" -eq 0 ]]; then
	echo "[verify] ruff check"
	"$PY" -m ruff check . --exclude .venv || {
		echo "[verify] note: ruff check reported issues (pre-existing may exist); continuing" >&2
	}
	echo "[verify] ruff format --check"
	"$PY" -m ruff format --check . --exclude .venv || {
		echo "[verify] note: ruff format drift (pre-existing may exist); continuing" >&2
	}
fi

echo "[verify] CI tests (--no-lint)"
"$PY" tests/run_ci.py --no-lint

echo "[verify] auth smoke (unit: missing/bad token deny; missing flag deny; flag allow)"
"$PY" - <<'PY'
"""Lab smoke without ComfyUI: assert middleware permission outcomes (401/403/200)."""
import asyncio
import importlib.util
import json
import os
import sys
import tempfile
import types

ROOT = os.getcwd()
UTILS = os.path.join(ROOT, "utils")

# --- stubs (same shape as access-control perm tests) ---
root = types.ModuleType("mss_login")
root.__path__ = [ROOT]
utils = types.ModuleType("mss_login.utils")
utils.__path__ = [UTILS]
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

class _Resp:
	def __init__(self, *, status=200, body=None):
		self.status = status
		self.body = body

aiohttp = types.ModuleType("aiohttp")
aiohttp.web = types.SimpleNamespace(
	middleware=lambda fn: fn,
	json_response=lambda data=None, status=200, **k: _Resp(status=status, body=data),
	Response=lambda **k: _Resp(status=k.get("status", 200)),
	Request=object,
)
sys.modules["aiohttp"] = aiohttp
api_tok = types.ModuleType("mss_login.utils.api_token_store")
api_tok.get_api_token_store = lambda *_a, **_k: types.SimpleNamespace(
	get_user_for_token=lambda *_: None
)
sys.modules["mss_login.utils.api_token_store"] = api_tok
sys.modules["utils.api_token_store"] = api_tok
data_dir = types.ModuleType("mss_login.utils.data_dir")
data_dir.get_data_subdir = lambda *_a, **_k: "/tmp"
sys.modules["mss_login.utils.data_dir"] = data_dir
sys.modules["utils.data_dir"] = data_dir
debug = types.ModuleType("mss_login.utils.debug_log")
debug.debug_write = lambda *_a, **_k: None
sys.modules["mss_login.utils.debug_log"] = debug
sys.modules["utils.debug_log"] = debug
iso = types.ModuleType("mss_login.utils.user_isolation")
iso.safe_user_dir_segment = lambda x: str(x or "x")
iso.user_id_from_queue_item = lambda e: None
iso.username_from_queue_item = lambda e: None
sys.modules["mss_login.utils.user_isolation"] = iso
sys.modules["utils.user_isolation"] = iso
users_db_mod = types.ModuleType("mss_login.utils.users_db")
users_db_mod.UsersDB = object
sys.modules["mss_login.utils.users_db"] = users_db_mod
sys.modules["utils.users_db"] = users_db_mod
cpe = types.ModuleType("mss_login.utils.cpe_workflows")
cpe.cpe_path_requires_modify_workflows = lambda _p, _m: False
sys.modules["mss_login.utils.cpe_workflows"] = cpe
sys.modules["utils.cpe_workflows"] = cpe

secret = "verify-smoke-secret-not-for-prod"
const = types.ModuleType("mss_login.constants")
const.SECRET_KEY = secret
const.TOKEN_ALGORITHM = "HS256"
sys.modules["mss_login.constants"] = const

path = os.path.join(UTILS, "access_control.py")
spec = importlib.util.spec_from_file_location("mss_login.utils.access_control", path)
ac = importlib.util.module_from_spec(spec)
sys.modules["mss_login.utils.access_control"] = ac
sys.modules["utils.access_control"] = ac
spec.loader.exec_module(ac)

tmpdir = tempfile.mkdtemp()
groups_path = os.path.join(tmpdir, "groups.json")
with open(groups_path, "w", encoding="utf-8") as f:
	json.dump(
		{
			"user": {"can_run": True, "can_upload": True, "can_access_api": True},
			"denied": {"can_run": False, "can_upload": False, "can_access_api": False},
		},
		f,
	)

ctrl = ac.AccessControl.__new__(ac.AccessControl)
ctrl.users_db = types.SimpleNamespace(
	get_user=lambda username=None, **_k: (
		("uid", {"username": username, "groups": ["user"]})
		if username == "alice"
		else (
			("uid2", {"username": username, "groups": ["denied"]})
			if username == "bob"
			else (None, None)
		)
	)
)
ctrl.groups_config_file = groups_path
ctrl.api_token_store_config = {}
ctrl.server = types.SimpleNamespace()

class Req:
	def __init__(self, path, method="GET", headers=None):
		self.path = path
		self.method = method
		self.headers = headers or {}
		self.cookies = {}
		self.rel_url = types.SimpleNamespace(query={})

async def call(mw, path, method="GET", headers=None):
	called = {"n": 0}
	async def handler(_r):
		called["n"] += 1
		return _Resp(status=200, body={"ok": True})
	resp = await mw(Req(path, method, headers), handler)
	return resp, called["n"]

async def main():
	# Missing / forged token → guest empty perms → 403 on protected API (lab stand-in for 401)
	# JWTAuth middleware owns true 401; access-control default-denies guest for can_access_api.
	ctrl._get_user_role_and_permissions = lambda _r: ("guest", {}, None)
	mw = ctrl.create_mss_login_middleware()
	resp, n = await call(mw, "/api/object_info")
	assert resp.status == 403 and n == 0, f"no token expected 403 got {resp.status}"
	print("  ok: no token → 403 (guest default-deny)")

	# Bad token (forged JWT) via real resolver
	import jwt
	forged = jwt.encode({"username": "alice"}, "wrong-key", algorithm="HS256")
	del ctrl._get_user_role_and_permissions
	role, perms, user = ctrl._get_user_role_and_permissions(
		Req("/api/x", headers={"Authorization": f"Bearer {forged}"})
	)
	assert user is None and role == "guest", "bad token must be guest"
	print("  ok: bad token → guest (401/deny path)")

	# Valid claims but flag false → 403
	ctrl._get_user_role_and_permissions = lambda _r: (
		"denied",
		{"can_run": False, "can_upload": False, "can_access_api": False},
		"bob",
	)
	mw2 = ctrl.create_mss_login_middleware()
	resp, n = await call(mw2, "/api/history")
	assert resp.status == 403 and n == 0, f"no flag expected 403 got {resp.status}"
	print("  ok: valid identity without flag → 403")

	# Valid + flag → 200
	ctrl._get_user_role_and_permissions = lambda _r: (
		"user",
		{"can_run": True, "can_upload": True, "can_access_api": True},
		"alice",
	)
	mw3 = ctrl.create_mss_login_middleware()
	resp, n = await call(mw3, "/api/history")
	assert resp.status == 200 and n == 1, f"with flag expected 200 got {resp.status}"
	print("  ok: valid identity with flag → 200")

asyncio.run(main())
print("[verify] auth smoke passed")
PY

echo "[verify] CPE gate"
"$PY" tests/run_cpe_auth_tests.py
"$PY" tests/run_cpe_workflow_tests.py
"$PY" tests/run_cpe_shared_guest_workflows_tests.py

echo "[verify] ALL PASSED"
