#!/usr/bin/env python3
"""Passkey browser compatibility tests (status origins allowlist, strict POSTs, JS api() method/body)."""

from __future__ import annotations

import asyncio
import importlib.util
import json
import os
import subprocess
import sys
import time
import types

_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_ROUTES_DIR = os.path.join(_PROJECT_ROOT, "routes")
_UTILS_DIR = os.path.join(_PROJECT_ROOT, "utils")
failed: list[str] = []


def _ok(cond: bool, msg: str) -> None:
	if cond:
		print(f"  ok: {msg}")
	else:
		print(f"  FAIL: {msg}")
		failed.append(msg)


def _load_webauthn_routes():
	sys.modules["folder_paths"] = types.ModuleType("folder_paths")

	root_pkg = types.ModuleType("mss_login")
	root_pkg.__path__ = [_PROJECT_ROOT]
	routes_pkg = types.ModuleType("mss_login.routes")
	routes_pkg.__path__ = [_ROUTES_DIR]
	utils_pkg = types.ModuleType("mss_login.utils")
	utils_pkg.__path__ = [_UTILS_DIR]
	sys.modules["mss_login"] = root_pkg
	sys.modules["mss_login.routes"] = routes_pkg
	sys.modules["mss_login.utils"] = utils_pkg

	globals_mod = types.ModuleType("mss_login.globals")

	class _Routes:
		def get(self, *a, **k):
			return lambda fn: fn

		def post(self, *a, **k):
			return lambda fn: fn

		def delete(self, *a, **k):
			return lambda fn: fn

	globals_mod.routes = _Routes()
	globals_mod.jwt_auth = types.SimpleNamespace(
		resolve_request_identity=lambda req, touch_idle=False: ("uid1", "alice"),
		get_token_from_request=lambda req: "fake_token",
		resolve_token_identity=lambda tok, touch_idle=False: ("uid1", "alice"),
		decode_access_token=lambda tok: {
			"auth_time": time.time(),
			"user_id": "uid1",
			"username": "alice",
		},
	)
	globals_mod.users_db = types.SimpleNamespace(get_user=lambda u: ("uid1", {"username": "alice"}))
	globals_mod.logger = types.SimpleNamespace(
		info=lambda *a: None, warning=lambda *a: None, error=lambda *a: None
	)
	globals_mod.timeout = types.SimpleNamespace()
	sys.modules["mss_login.globals"] = globals_mod

	spec = importlib.util.spec_from_file_location(
		"mss_login.routes.webauthn", os.path.join(_ROUTES_DIR, "webauthn.py")
	)
	mod = importlib.util.module_from_spec(spec)
	sys.modules["mss_login.routes.webauthn"] = mod
	spec.loader.exec_module(mod)
	return mod


def test_status_endpoint_origins():
	print("TestStatusEndpointOrigins")
	mod = _load_webauthn_routes()
	from aiohttp.test_utils import make_mocked_request

	async def _run():
		# 1. Status without Origin header (Option a: response includes configured origins allowlist)
		req_no_origin = make_mocked_request("GET", "/mss-login/api/webauthn/status", headers={})
		resp = await mod.webauthn_status(req_no_origin)
		data = json.loads(resp.text)
		_ok(resp.status == 200, "status endpoint returns 200")
		_ok("origins" in data, "response includes origins field")
		_ok(
			data["origins"] == ["https://comfyui.tmd-nas.win"],
			"origins matches configured allowlist",
		)
		_ok(data["secure_origin"] is False, "secure_origin is false when Origin header is omitted")
		_ok(data["available"] is True, "passkeys available is true")

		# 2. Status with allowed Origin header
		req_allowed = make_mocked_request(
			"GET",
			"/mss-login/api/webauthn/status",
			headers={"Origin": "https://comfyui.tmd-nas.win"},
		)
		resp_allowed = await mod.webauthn_status(req_allowed)
		data_allowed = json.loads(resp_allowed.text)
		_ok(
			data_allowed["secure_origin"] is True,
			"secure_origin is true with allowed Origin header",
		)
		_ok(
			data_allowed["origins"] == ["https://comfyui.tmd-nas.win"],
			"origins preserved on allowed request",
		)

		# 3. Status with disallowed Origin header
		req_disallowed = make_mocked_request(
			"GET", "/mss-login/api/webauthn/status", headers={"Origin": "https://evil.example"}
		)
		resp_disallowed = await mod.webauthn_status(req_disallowed)
		data_disallowed = json.loads(resp_disallowed.text)
		_ok(
			data_disallowed["secure_origin"] is False,
			"secure_origin is false with disallowed Origin header",
		)

	asyncio.run(_run())


def test_strict_post_origin_checks():
	print("TestStrictPostOriginChecks")
	mod = _load_webauthn_routes()
	from aiohttp.test_utils import make_mocked_request

	async def _run():
		# 1. Passwordless options POST with no Origin
		req_pw_none = make_mocked_request(
			"POST", "/mss-login/webauthn/passwordless/options", headers={}
		)
		resp_pw_none = await mod.passwordless_options(req_pw_none)
		_ok(resp_pw_none.status == 403, "passwordless options without Origin returns 403")

		# 2. Passwordless options POST with wrong Origin
		req_pw_wrong = make_mocked_request(
			"POST",
			"/mss-login/webauthn/passwordless/options",
			headers={"Origin": "https://evil.example"},
		)
		resp_pw_wrong = await mod.passwordless_options(req_pw_wrong)
		_ok(resp_pw_wrong.status == 403, "passwordless options with wrong Origin returns 403")

		# 3. Register options POST with no Origin
		req_reg_none = make_mocked_request(
			"POST", "/mss-login/webauthn/register/options", headers={}
		)
		resp_reg_none = await mod.register_options(req_reg_none)
		_ok(resp_reg_none.status == 403, "register options without Origin returns 403")

		# 4. Register options POST with wrong Origin
		req_reg_wrong = make_mocked_request(
			"POST",
			"/mss-login/webauthn/register/options",
			headers={"Origin": "https://evil.example"},
		)
		resp_reg_wrong = await mod.register_options(req_reg_wrong)
		_ok(resp_reg_wrong.status == 403, "register options with wrong Origin returns 403")

	asyncio.run(_run())


def test_js_api_and_visibility_with_node():
	print("TestJsApiAndVisibilityWithNode")
	node_script = """
const fs = require("fs");
const assert = require("assert");

const passkeysJs = fs.readFileSync("web/js/passkeys.js", "utf8");

async function testMainFlow() {
    let fetchCalls = [];
    let listeners = {};
    const elements = {
        "passkey-insecure": { hidden: false },
        "passkey-section": { hidden: true },
        "passkey-list": {
            innerHTML: "",
            children: [],
            appendChild: function(c) { this.children.push(c); }
        },
        "passkey-register": {
            addEventListener: (evt, fn) => { listeners["register"] = fn; }
        },
        "recovery-regen": {
            addEventListener: (evt, fn) => { listeners["regen"] = fn; }
        },
        "recovery-codes": { hidden: true, textContent: "" },
        "passkey-name": { value: "Browser Passkey" }
    };

    global.window = { isSecureContext: true };
    global.location = { origin: "https://comfyui.tmd-nas.win" };
    global.document = {
        readyState: "complete",
        getElementById: (id) => elements[id],
        createElement: (tag) => ({
            tagName: tag,
            children: [],
            appendChild: function(c) { this.children.push(c); },
            addEventListener: function(evt, fn) { this.listener = fn; }
        }),
        createTextNode: (txt) => ({ text: txt })
    };
    if (typeof navigator !== "undefined") {
        navigator.credentials = {
            create: async () => ({
                id: "cred_test_123",
                rawId: new Uint8Array([1, 2, 3]).buffer,
                type: "public-key",
                response: {
                    clientDataJSON: new Uint8Array([4, 5]).buffer,
                    attestationObject: new Uint8Array([6, 7]).buffer
                }
            })
        };
    }

    global.fetch = async (url, opts) => {
        fetchCalls.push({ url, opts: JSON.parse(JSON.stringify(opts || {})) });
        if (url === "/mss-login/api/webauthn/status") {
            return {
                ok: true,
                status: 200,
                json: async () => ({
                    available: true,
                    secure_origin: false, // browser same-origin GET omits Origin header
                    origins: ["https://comfyui.tmd-nas.win"]
                })
            };
        }
        if (url === "/mss-login/webauthn/credentials" && (!opts || !opts.method || opts.method === "GET")) {
            return {
                ok: true,
                status: 200,
                json: async () => ({
                    credentials: [{ id: "key_1", name: "Security Key", synced: true, last_used_at: 1700000000 }]
                })
            };
        }
        if (url === "/mss-login/webauthn/credentials" && opts && opts.method === "DELETE") {
            return { ok: true, status: 200, json: async () => ({ status: "deleted" }) };
        }
        if (url === "/mss-login/webauthn/register/options") {
            return { ok: true, status: 200, json: async () => ({ challenge: "dGVzdA" }) };
        }
        if (url === "/mss-login/webauthn/register/verify") {
            return { ok: true, status: 200, json: async () => ({ ok: true }) };
        }
        if (url === "/mss-login/api/mfa/recovery-codes/regenerate") {
            return { ok: true, status: 200, json: async () => ({ recovery_codes: ["codeA", "codeB"] }) };
        }
        return { ok: true, status: 200, json: async () => ({}) };
    };

    eval(passkeysJs);
    await new Promise(r => setTimeout(r, 60));

    // Check passkey UI is shown on matching allowed origin in secure context
    assert.strictEqual(elements["passkey-insecure"].hidden, true, "passkey-insecure hidden on allowed origin");
    assert.strictEqual(elements["passkey-section"].hidden, false, "passkey-section shown on allowed origin");

    // Check Delete button sends DELETE method and JSON body
    assert.strictEqual(elements["passkey-list"].children.length, 1, "credentials rendered");
    const li = elements["passkey-list"].children[0];
    const delBtn = li.children.find(c => c.tagName === "button");
    assert(delBtn && delBtn.listener, "delete button has click handler");
    fetchCalls = [];
    await delBtn.listener();
    const delCall = fetchCalls.find(c => c.url === "/mss-login/webauthn/credentials");
    assert(delCall, "credentials endpoint called for delete");
    assert.strictEqual(delCall.opts.method, "DELETE", "DELETE method passed to fetch");
    assert.strictEqual(delCall.opts.credentials, "same-origin", "credentials same-origin preserved");
    assert.strictEqual(delCall.opts.headers["Content-Type"], "application/json", "Content-Type header preserved");
    assert.strictEqual(delCall.opts.body, JSON.stringify({ id: "key_1" }), "DELETE body preserved");

    // Check register flow sends POST method and JSON body
    fetchCalls = [];
    assert(listeners["register"], "register button listener attached");
    await listeners["register"]();
    const optCall = fetchCalls.find(c => c.url === "/mss-login/webauthn/register/options");
    assert(optCall, "register options called");
    assert.strictEqual(optCall.opts.method, "POST", "register options sends POST");
    assert.strictEqual(optCall.opts.credentials, "same-origin", "credentials same-origin preserved on options POST");

    const verCall = fetchCalls.find(c => c.url === "/mss-login/webauthn/register/verify");
    assert(verCall, "register verify called");
    assert.strictEqual(verCall.opts.method, "POST", "register verify sends POST");
    assert.strictEqual(verCall.opts.headers["Content-Type"], "application/json", "verify Content-Type preserved");
    const verBody = JSON.parse(verCall.opts.body);
    assert.strictEqual(verBody.name, "Browser Passkey", "register verify body name preserved");
    assert.strictEqual(verBody.credential.id, "cred_test_123", "register verify body credential preserved");

    // Check recovery codes regenerate button sends POST
    fetchCalls = [];
    assert(listeners["regen"], "recovery regen button listener attached");
    await listeners["regen"]();
    const regenCall = fetchCalls.find(c => c.url === "/mss-login/api/mfa/recovery-codes/regenerate");
    assert(regenCall, "recovery regenerate called");
    assert.strictEqual(regenCall.opts.method, "POST", "recovery regenerate sends POST");
    assert.strictEqual(regenCall.opts.credentials, "same-origin", "recovery regenerate credentials same-origin preserved");
    assert.strictEqual(elements["recovery-codes"].hidden, false, "recovery codes displayed");
    assert.strictEqual(elements["recovery-codes"].textContent, "codeA\\ncodeB", "recovery codes text updated");
}

async function testVisibilityScenario(scenario) {
    const elements = {
        "passkey-insecure": { hidden: false },
        "passkey-section": { hidden: true },
        "passkey-list": { innerHTML: "", appendChild: () => {} },
        "passkey-register": { addEventListener: () => {} },
        "recovery-regen": { addEventListener: () => {} },
        "recovery-codes": { hidden: true, textContent: "" },
        "passkey-name": { value: "" }
    };
    global.window = { isSecureContext: scenario.isSecureContext };
    global.location = { origin: scenario.origin };
    global.document = {
        readyState: "complete",
        getElementById: (id) => elements[id],
        createElement: () => ({ appendChild: () => {}, addEventListener: () => {} }),
        createTextNode: () => ({})
    };
    global.fetch = async (url) => {
        if (url === "/mss-login/api/webauthn/status") {
            return { ok: true, status: 200, json: async () => scenario.statusResponse };
        }
        return { ok: true, status: 200, json: async () => ({ credentials: [] }) };
    };

    eval(passkeysJs);
    await new Promise(r => setTimeout(r, 40));
    return {
        insecureHidden: elements["passkey-insecure"].hidden,
        sectionHidden: elements["passkey-section"].hidden
    };
}

async function testAll() {
    await testMainFlow();

    // Insecure context -> UI hidden
    const s1 = await testVisibilityScenario({
        isSecureContext: false,
        origin: "https://comfyui.tmd-nas.win",
        statusResponse: { available: true, origins: ["https://comfyui.tmd-nas.win"] }
    });
    assert.strictEqual(s1.insecureHidden, false, "insecure visible when !isSecureContext");
    assert.strictEqual(s1.sectionHidden, true, "section hidden when !isSecureContext");

    // Mismatched origin -> UI hidden
    const s2 = await testVisibilityScenario({
        isSecureContext: true,
        origin: "https://evil.example",
        statusResponse: { available: true, origins: ["https://comfyui.tmd-nas.win"] }
    });
    assert.strictEqual(s2.insecureHidden, false, "insecure visible on evil origin");
    assert.strictEqual(s2.sectionHidden, true, "section hidden on evil origin");

    // Fallback when origins missing (older server), secure_origin = true
    const s3 = await testVisibilityScenario({
        isSecureContext: true,
        origin: "https://comfyui.tmd-nas.win",
        statusResponse: { available: true, secure_origin: true }
    });
    assert.strictEqual(s3.insecureHidden, true, "insecure hidden when fallback secure_origin=true");
    assert.strictEqual(s3.sectionHidden, false, "section visible when fallback secure_origin=true");

    // Fallback when origins missing (older server), secure_origin = false
    const s4 = await testVisibilityScenario({
        isSecureContext: true,
        origin: "https://comfyui.tmd-nas.win",
        statusResponse: { available: true, secure_origin: false }
    });
    assert.strictEqual(s4.insecureHidden, false, "insecure visible when fallback secure_origin=false");
    assert.strictEqual(s4.sectionHidden, true, "section hidden when fallback secure_origin=false");

    console.log("NODE_TEST_OK");
}

testAll().catch(e => {
    console.error("NODE_TEST_FAIL:", e);
    process.exit(1);
});
"""
	res = subprocess.run(
		["node", "-e", node_script], cwd=_PROJECT_ROOT, capture_output=True, text=True
	)
	_ok(res.returncode == 0, f"Node tests exited with 0 (output: {res.stdout.strip()})")
	if res.returncode != 0:
		print("Node test stderr:", res.stderr)


def main() -> int:
	print("TestPasskeyBrowserCompatibility")
	test_status_endpoint_origins()
	test_strict_post_origin_checks()
	test_js_api_and_visibility_with_node()
	print("---")
	if failed:
		for f in failed:
			print(f"FAIL: {f}")
		return 1
	print("All passkey browser compatibility tests passed.")
	return 0


if __name__ == "__main__":
	raise SystemExit(main())
