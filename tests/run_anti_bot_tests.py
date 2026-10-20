r"""
Standalone tests for Anti-Bot protection, honeypot defenses, and lobby buffer clamping.

  .venv/bin/python tests/run_anti_bot_tests.py
"""

from __future__ import annotations

import asyncio
import importlib.util
import os
import sys
import types
from unittest.mock import MagicMock, patch

_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _setup_mss_login():
	from aiohttp import web

	class FakePromptServer:
		instance = types.SimpleNamespace(
			routes=web.RouteTableDef(), app=web.Application(), prompt_queue=MagicMock()
		)

	srv = types.ModuleType("server")
	srv.PromptServer = FakePromptServer
	sys.modules["server"] = srv

	for name in ("folder_paths", "execution"):
		if name not in sys.modules:
			sys.modules[name] = types.ModuleType(name)
	sys.modules["folder_paths"].get_output_directory = lambda: "/tmp"
	sys.modules["folder_paths"].get_temp_directory = lambda: "/tmp"
	sys.modules["folder_paths"].get_input_directory = lambda: "/tmp"
	sys.modules["execution"].MAXIMUM_HISTORY_SIZE = 10
	sys.modules["execution"].PromptQueue = object

	root = types.ModuleType("mss_login")
	root.__path__ = [_PROJECT_ROOT]
	sys.modules["mss_login"] = root

	utils_pkg = types.ModuleType("mss_login.utils")
	utils_pkg.__path__ = [os.path.join(_PROJECT_ROOT, "utils")]
	sys.modules["mss_login.utils"] = utils_pkg

	routes_pkg = types.ModuleType("mss_login.routes")
	routes_pkg.__path__ = [os.path.join(_PROJECT_ROOT, "routes")]
	sys.modules["mss_login.routes"] = routes_pkg

	# Load constants under mss_login
	c_path = os.path.join(_PROJECT_ROOT, "constants.py")
	c_spec = importlib.util.spec_from_file_location("mss_login.constants", c_path)
	constants_mod = importlib.util.module_from_spec(c_spec)
	constants_mod.__package__ = "mss_login"
	sys.modules["mss_login.constants"] = constants_mod
	c_spec.loader.exec_module(constants_mod)

	# Load anti_bot under mss_login.utils
	ab_path = os.path.join(_PROJECT_ROOT, "utils", "anti_bot.py")
	ab_spec = importlib.util.spec_from_file_location("mss_login.utils.anti_bot", ab_path)
	anti_bot_mod = importlib.util.module_from_spec(ab_spec)
	anti_bot_mod.__package__ = "mss_login.utils"
	sys.modules["mss_login.utils.anti_bot"] = anti_bot_mod
	ab_spec.loader.exec_module(anti_bot_mod)

	# Load route_policy under mss_login.utils
	rp_path = os.path.join(_PROJECT_ROOT, "utils", "route_policy.py")
	rp_spec = importlib.util.spec_from_file_location("mss_login.utils.route_policy", rp_path)
	route_policy_mod = importlib.util.module_from_spec(rp_spec)
	route_policy_mod.__package__ = "mss_login.utils"
	sys.modules["mss_login.utils.route_policy"] = route_policy_mod
	rp_spec.loader.exec_module(route_policy_mod)

	return constants_mod, anti_bot_mod, route_policy_mod


constants_mod, anti_bot, route_policy = _setup_mss_login()


def test_timeout_clamping():
	print("  Testing loading buffer timeout clamping...")
	assert constants_mod.MIN_LOADING_TIMEOUT_SECONDS == 15, "MIN_LOADING_TIMEOUT_SECONDS must be 15"
	assert constants_mod.MAX_LOADING_TIMEOUT_SECONDS == 3600, (
		"MAX_LOADING_TIMEOUT_SECONDS must be 3600"
	)
	assert constants_mod.DEFAULT_LOADING_TIMEOUT_SECONDS == 30, (
		"DEFAULT_LOADING_TIMEOUT_SECONDS must be 30"
	)

	# clamp_loading_timeout tests
	assert anti_bot.clamp_loading_timeout(None) == 30
	assert anti_bot.clamp_loading_timeout("not-a-number") == 30
	assert anti_bot.clamp_loading_timeout(5) == 15, "Values below 15 must be clamped to 15"
	assert anti_bot.clamp_loading_timeout(14) == 15, "Values below 15 must be clamped to 15"
	assert anti_bot.clamp_loading_timeout(15) == 15
	assert anti_bot.clamp_loading_timeout(30) == 30
	assert anti_bot.clamp_loading_timeout(120) == 120
	assert anti_bot.clamp_loading_timeout("300") == 300
	assert anti_bot.clamp_loading_timeout(3600) == 3600
	assert anti_bot.clamp_loading_timeout(5000) == 3600, "Values above 3600 must be clamped to 3600"

	# _get_loading_timeout_seconds with env var
	with patch.dict(os.environ, {"MSS_LOGIN_LOADING_TIMEOUT_SECONDS": "5"}):
		assert constants_mod._get_loading_timeout_seconds() == 15, (
			"Env below 15 must be clamped to 15"
		)
	with patch.dict(os.environ, {"MSS_LOGIN_LOADING_TIMEOUT_SECONDS": "45"}):
		assert constants_mod._get_loading_timeout_seconds() == 45
	with patch.dict(os.environ, {"MSS_LOGIN_LOADING_TIMEOUT_SECONDS": "9999"}):
		assert constants_mod._get_loading_timeout_seconds() == 3600, (
			"Env above 3600 must be clamped to 3600"
		)
	with patch.dict(os.environ, {"MSS_LOGIN_LOADING_TIMEOUT_SECONDS": ""}):
		val = constants_mod._get_loading_timeout_seconds()
		assert val >= 15, "Fallback timeout must be at least 15"
	print("    [PASS] Timeout clamping verified.")


def test_honeypot_logic():
	print("  Testing honeypot field detection...")
	assert anti_bot.HONEYPOT_FIELD_NAME == "hp_auth_shield"

	# Clean payloads (e.g. Krita AI Diffusion JSON, Comfy Portal)
	assert not anti_bot.is_honeypot_triggered(None)
	assert not anti_bot.is_honeypot_triggered({})
	assert not anti_bot.is_honeypot_triggered({"username": "user", "password": "secret_password"})
	assert not anti_bot.is_honeypot_triggered({"hp_auth_shield": ""})
	assert not anti_bot.is_honeypot_triggered({"hp_auth_shield": "   "})
	assert not anti_bot.is_honeypot_triggered({"hp_auth_shield": None})

	# Bot populated payload
	assert anti_bot.is_honeypot_triggered({"hp_auth_shield": "automated_bot"})
	assert anti_bot.is_honeypot_triggered({"hp_auth_shield": "1"})
	assert anti_bot.is_honeypot_triggered({"hp_auth_shield": True})
	assert anti_bot.is_honeypot_triggered(
		{"username": "admin", "password": "123", "hp_auth_shield": "spam"}
	)
	print("    [PASS] Honeypot detection verified.")


def test_automated_user_agent():
	print("  Testing automated attack user-agent detection...")
	assert anti_bot.is_automated_user_agent("Mozilla/5.0 (Windows NT 10.0; Win64; x64)")[0] is False
	assert anti_bot.is_automated_user_agent("Krita AI Diffusion/1.0")[0] is False
	assert anti_bot.is_automated_user_agent("ComfyPortal/1.2")[0] is False
	assert anti_bot.is_automated_user_agent("python-requests/2.31.0")[0] is False
	assert anti_bot.is_automated_user_agent("")[0] is False
	assert anti_bot.is_automated_user_agent(None)[0] is False

	match, name = anti_bot.is_automated_user_agent("sqlmap/1.6#stable (https://sqlmap.org)")
	assert match is True and name == "sqlmap"

	match, name = anti_bot.is_automated_user_agent("Mozilla/5.0 (compatible; Nikto/2.1.6)")
	assert match is True and name == "nikto"

	match, name = anti_bot.is_automated_user_agent("masscan/1.3")
	assert match is True and name == "masscan"

	match, name = anti_bot.is_automated_user_agent("wpscan v3.8")
	assert match is True and name == "wpscan"

	match, name = anti_bot.is_automated_user_agent("gobuster/3.1.0")
	assert match is True and name == "gobuster"
	print("    [PASS] Automated scanner detection verified.")


def test_loopback_ip():
	print("  Testing loopback IP exemptions...")
	assert anti_bot.is_loopback_ip("127.0.0.1") is True
	assert anti_bot.is_loopback_ip("127.0.0.50") is True
	assert anti_bot.is_loopback_ip("::1") is True
	assert anti_bot.is_loopback_ip("localhost") is True
	assert anti_bot.is_loopback_ip("testclient") is True
	assert anti_bot.is_loopback_ip(None) is False
	assert anti_bot.is_loopback_ip("") is False
	assert anti_bot.is_loopback_ip("198.51.100.2") is False
	assert anti_bot.is_loopback_ip("8.8.8.8") is False
	print("    [PASS] Loopback exemptions verified.")


def test_route_policy():
	print("  Testing route policy registrations...")
	pol = route_policy.lookup_policy("POST", "/mss-login/api/anti-bot/telemetry")
	assert pol == "PUBLIC", f"Telemetry policy was {pol}, expected PUBLIC"

	pol = route_policy.lookup_policy("POST", "/api/mss-login/api/anti-bot/telemetry")
	assert pol == "PUBLIC", f"Mirrored telemetry policy was {pol}, expected PUBLIC"

	pol = route_policy.lookup_policy("GET", "/mss-login/api/settings/loading-screen")
	assert pol == "OWNER", f"Loading settings policy was {pol}, expected OWNER"

	pol = route_policy.lookup_policy("PUT", "/mss-login/api/settings/loading-screen")
	assert pol == "OWNER", f"Loading settings policy was {pol}, expected OWNER"
	print("    [PASS] Route policy rules verified.")


def test_admin_settings_loading_screen():
	print("  Testing admin loading screen API handler...")
	# Load globals first
	g_path = os.path.join(_PROJECT_ROOT, "globals.py")
	g_spec = importlib.util.spec_from_file_location("mss_login.globals", g_path)
	g_mod = importlib.util.module_from_spec(g_spec)
	g_mod.__package__ = "mss_login"
	sys.modules["mss_login.globals"] = g_mod
	g_spec.loader.exec_module(g_mod)

	# Load admin routes
	admin_path = os.path.join(_PROJECT_ROOT, "routes", "admin.py")
	admin_spec = importlib.util.spec_from_file_location("mss_login.routes.admin", admin_path)
	admin_mod = importlib.util.module_from_spec(admin_spec)
	admin_mod.__package__ = "mss_login.routes"
	sys.modules["mss_login.routes.admin"] = admin_mod
	admin_spec.loader.exec_module(admin_mod)

	assert "anti_bot" in admin_mod._EXPERIMENTAL_FEATURE_KEYS, (
		"anti_bot must be in _EXPERIMENTAL_FEATURE_KEYS"
	)

	loop = asyncio.new_event_loop()
	try:
		# Owner GET
		req_owner = MagicMock()
		with patch.object(admin_mod, "is_owner", return_value=True):
			resp = loop.run_until_complete(admin_mod.api_get_loading_screen_settings(req_owner))
			assert resp.status == 200

		# Non-owner GET
		with patch.object(admin_mod, "is_owner", return_value=False):
			resp = loop.run_until_complete(admin_mod.api_get_loading_screen_settings(req_owner))
			assert resp.status == 403

		# Owner PUT validation: too low (< 15)
		with patch.object(admin_mod, "is_owner", return_value=True):
			req_put = MagicMock()

			async def json_put_low():
				return {"timeout_seconds": 10}

			req_put.json = json_put_low
			resp = loop.run_until_complete(admin_mod.api_put_loading_screen_settings(req_put))
			assert resp.status == 400, "Values under 15 must be rejected with 400"

		# Owner PUT validation: too high (> 3600)
		with patch.object(admin_mod, "is_owner", return_value=True):
			req_put_high = MagicMock()

			async def json_put_high():
				return {"timeout_seconds": 4000}

			req_put_high.json = json_put_high
			resp = loop.run_until_complete(admin_mod.api_put_loading_screen_settings(req_put_high))
			assert resp.status == 400, "Values over 3600 must be rejected with 400"
	finally:
		loop.close()
	print("    [PASS] Admin loading screen API verified.")


def test_login_and_register_anti_bot_integration():
	print("  Testing login & register anti-bot defenses...")
	auth_path = os.path.join(_PROJECT_ROOT, "routes", "auth.py")
	auth_spec = importlib.util.spec_from_file_location("mss_login.routes.auth", auth_path)
	auth_mod = importlib.util.module_from_spec(auth_spec)
	auth_mod.__package__ = "mss_login.routes"
	sys.modules["mss_login.routes.auth"] = auth_mod
	auth_spec.loader.exec_module(auth_mod)

	with patch.object(anti_bot, "is_anti_bot_active", return_value=True):
		# 1. Login honeypot trigger
		req_bot = MagicMock()
		req_bot.get.return_value = {
			"username": "attacker",
			"password": "pwd",
			"hp_auth_shield": "bot_filled_value",
		}
		req_bot.headers = {"User-Agent": "Mozilla/5.0"}
		req_bot.remote = "198.51.100.99"

		with (
			patch.object(auth_mod, "get_ip", return_value="198.51.100.99"),
			patch.object(auth_mod.timeout, "add_failed_attempt") as mock_fail,
		):
			loop = asyncio.new_event_loop()
			try:
				resp = loop.run_until_complete(auth_mod.post_login(req_bot))
				assert resp.status == 403, f"Expected 403, got {resp.status}"
				assert mock_fail.called, "Failed attempt must be added on honeypot trigger"
			finally:
				loop.close()

		# 2. Scanner tool detection on register
		req_scanner = MagicMock()
		req_scanner.get.return_value = {
			"new_user_username": "newbot",
			"new_user_password": "pwd",
			"hp_auth_shield": "",
		}
		req_scanner.headers = {"User-Agent": "sqlmap/1.5#stable"}
		req_scanner.remote = "198.51.100.88"

		with (
			patch.object(auth_mod, "get_ip", return_value="198.51.100.88"),
			patch.object(auth_mod.timeout, "add_failed_attempt") as mock_fail,
		):
			loop = asyncio.new_event_loop()
			try:
				resp = loop.run_until_complete(auth_mod.post_register(req_scanner))
				assert resp.status == 403, f"Expected 403 for scanner tool, got {resp.status}"
				assert mock_fail.called, "Failed attempt must be added on scanner tool"
			finally:
				loop.close()

		# 3. Telemetry reporting
		req_telemetry = MagicMock()
		req_telemetry.remote = "198.51.100.77"
		req_telemetry.headers = {"User-Agent": "Mozilla/5.0"}

		async def fake_json():
			return {"reason": "synthetic_click", "elapsed_ms": 150, "entropy": 0, "webdriver": True}

		req_telemetry.json = fake_json

		with (
			patch.object(auth_mod, "get_ip", return_value="198.51.100.77"),
			patch.object(auth_mod.timeout, "add_failed_attempt") as mock_fail,
		):
			loop = asyncio.new_event_loop()
			try:
				resp = loop.run_until_complete(auth_mod.api_anti_bot_telemetry(req_telemetry))
				assert resp.status == 200, f"Expected 200 from telemetry, got {resp.status}"
				assert mock_fail.called, (
					"Failed attempt must be added for high-confidence bot telemetry"
				)
			finally:
				loop.close()

	print("    [PASS] Login and register anti-bot defenses verified.")


def main():
	print("[Anti-Bot Tests] Starting test suite...")
	test_timeout_clamping()
	test_honeypot_logic()
	test_automated_user_agent()
	test_loopback_ip()
	test_route_policy()
	test_admin_settings_loading_screen()
	test_login_and_register_anti_bot_integration()
	print("[Anti-Bot Tests] All 7 test suites passed successfully!")
	return 0


if __name__ == "__main__":
	sys.exit(main())
