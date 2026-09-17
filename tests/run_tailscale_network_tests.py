"""
Standalone tests for Tailscale and local network detection, security guards, and permission verification.
"""

import importlib.util
import os
import sys
import types
import unittest

_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _PROJECT_ROOT not in sys.path:
	sys.path.insert(0, _PROJECT_ROOT)


def _install_stubs():
	root_pkg = types.ModuleType("mss_login")
	root_pkg.__path__ = [_PROJECT_ROOT]
	utils_pkg = types.ModuleType("mss_login.utils")
	utils_pkg.__path__ = [os.path.join(_PROJECT_ROOT, "utils")]
	sys.modules["mss_login"] = root_pkg
	sys.modules["mss_login.utils"] = utils_pkg

	import tempfile

	_tmp_dir = tempfile.gettempdir()
	data_dir_mod = types.ModuleType("mss_login.utils.data_dir")
	data_dir_mod.ensure_data_dir = lambda _root=None: _tmp_dir
	data_dir_mod.get_data_dir = lambda: _tmp_dir
	sys.modules["mss_login.utils.data_dir"] = data_dir_mod

	install_mod = types.ModuleType("mss_login.utils.install_deps")
	install_mod.install_dependencies = lambda: True
	sys.modules["mss_login.utils.install_deps"] = install_mod

	path_safety_mod = types.ModuleType("mss_login.utils.path_safety")
	path_safety_mod.resolve_path_under = lambda base, rel: os.path.join(base, rel)
	sys.modules["mss_login.utils.path_safety"] = path_safety_mod

	access_ctrl_mod = types.ModuleType("mss_login.utils.access_control")
	access_ctrl_mod.AccessControl = type("AccessControl", (), {})
	sys.modules["mss_login.utils.access_control"] = access_ctrl_mod

	if "folder_paths" not in sys.modules:
		folder_paths_mod = types.ModuleType("folder_paths")
		folder_paths_mod.base_path = _PROJECT_ROOT
		folder_paths_mod.get_output_directory = lambda: _PROJECT_ROOT
		folder_paths_mod.get_temp_directory = lambda: _PROJECT_ROOT
		sys.modules["folder_paths"] = folder_paths_mod


def _load_module(name: str, path: str, package: str = ""):
	spec = importlib.util.spec_from_file_location(name, path)
	mod = importlib.util.module_from_spec(spec)
	if package:
		mod.__package__ = package
	sys.modules[name] = mod
	assert spec and spec.loader
	spec.loader.exec_module(mod)
	return mod


_install_stubs()
_constants = _load_module(
	"mss_login.constants", os.path.join(_PROJECT_ROOT, "constants.py"), "mss_login"
)
_ip_filter = _load_module(
	"mss_login.utils.ip_filter",
	os.path.join(_PROJECT_ROOT, "utils", "ip_filter.py"),
	"mss_login.utils",
)
_jwt_mod = _load_module(
	"mss_login.utils.jwt_auth",
	os.path.join(_PROJECT_ROOT, "utils", "jwt_auth.py"),
	"mss_login.utils",
)
_ts_net = _load_module(
	"mss_login.utils.tailscale_network",
	os.path.join(_PROJECT_ROOT, "utils", "tailscale_network.py"),
	"mss_login.utils",
)

classify_ip = _ts_net.classify_ip
detect_network_info = _ts_net.detect_network_info
get_all_trusted_cidrs = _ts_net.get_all_trusted_cidrs
is_ip_in_cidrs = _ts_net.is_ip_in_cidrs
is_trusted_tailscale_or_local = _ts_net.is_trusted_tailscale_or_local
user_can_login_locally_without_auth = _ts_net.user_can_login_locally_without_auth


class _DummyRequest:
	"""Simple request stub for network inspection tests."""

	def __init__(self, remote: str = None, headers: dict = None, cookies: dict = None):
		self.remote = remote
		self.headers = headers or {}
		self.cookies = cookies or {}


class _DummyUsersDb:
	"""Mock users DB for role/permission checking."""

	def __init__(self, users=None, owner="superowner"):
		self._users = users or {
			"superowner": ("id-1", {"groups": ["owner"], "admin": True}),
			"sysadmin": ("id-2", {"groups": ["admin"], "admin": True}),
			"poweruser": ("id-3", {"groups": ["power"], "admin": False}),
			"regularuser": ("id-4", {"groups": ["user"], "admin": False}),
			"guest": ("id-5", {"groups": ["guest"], "admin": False}),
		}
		self._owner = owner

	def get_user(self, username):
		return self._users.get(username, (None, None))

	def get_owner_username(self):
		return self._owner


class TestTailscaleIpClassification(unittest.TestCase):
	"""Test IP classification against Tailscale and local private subnets."""

	def test_tailscale_ipv4_cgnat(self):
		self.assertEqual(classify_ip("100.64.0.1"), "tailscale")
		self.assertEqual(classify_ip("100.80.25.10"), "tailscale")
		self.assertEqual(classify_ip("100.127.255.255"), "tailscale")

	def test_tailscale_ipv6_ula(self):
		self.assertEqual(classify_ip("fd7a:115c:a1e0::1"), "tailscale")
		self.assertEqual(classify_ip("fd7a:115c:a1e0:ab12::34cd"), "tailscale")

	def test_local_private_ipv4(self):
		self.assertEqual(classify_ip("127.0.0.1"), "local")
		self.assertEqual(classify_ip("10.0.0.5"), "local")
		self.assertEqual(classify_ip("172.16.0.1"), "local")
		self.assertEqual(classify_ip("172.17.0.2"), "local")  # Docker bridge
		self.assertEqual(classify_ip("192.168.1.100"), "local")
		self.assertEqual(classify_ip("169.254.1.1"), "local")

	def test_local_private_ipv6(self):
		self.assertEqual(classify_ip("::1"), "local")
		self.assertEqual(classify_ip("fe80::1"), "local")
		self.assertEqual(classify_ip("fc00::1"), "local")

	def test_remote_public_ips(self):
		self.assertEqual(classify_ip("203.0.113.1"), "remote")
		self.assertEqual(classify_ip("8.8.8.8"), "remote")
		self.assertEqual(classify_ip("1.1.1.1"), "remote")
		self.assertEqual(classify_ip("2607:f8b0:4005:805::200e"), "remote")

	def test_custom_cidrs(self):
		custom = ["198.51.100.0/24"]
		self.assertEqual(classify_ip("198.51.100.42", custom_cidrs=custom), "local")
		self.assertEqual(classify_ip("198.51.100.42", custom_cidrs=[]), "remote")


class TestAntiSpoofingNetworkGuards(unittest.TestCase):
	"""Test multi-layer security ensuring remote IPs cannot spoof local/Tailscale access."""

	def test_direct_tailscale_client(self):
		req = _DummyRequest(remote="100.64.0.15")
		self.assertTrue(is_trusted_tailscale_or_local(req))
		info = detect_network_info(req)
		self.assertTrue(info["is_trusted"])
		self.assertEqual(info["network_type"], "tailscale")

	def test_direct_local_client(self):
		req = _DummyRequest(remote="192.168.1.50")
		self.assertTrue(is_trusted_tailscale_or_local(req))
		info = detect_network_info(req)
		self.assertTrue(info["is_trusted"])
		self.assertEqual(info["network_type"], "local")

	def test_direct_remote_client_rejected(self):
		req = _DummyRequest(remote="203.0.113.195")
		self.assertFalse(is_trusted_tailscale_or_local(req))
		info = detect_network_info(req)
		self.assertFalse(info["is_trusted"])
		self.assertEqual(info["network_type"], "remote")

	def test_remote_attacker_spoofing_x_forwarded_for(self):
		"""Attacker connecting directly from public IP with fake X-Forwarded-For MUST BE REJECTED."""
		req = _DummyRequest(
			remote="203.0.113.88",
			headers={"X-Forwarded-For": "100.64.0.1, 192.168.1.1"},
		)
		self.assertFalse(is_trusted_tailscale_or_local(req))

	def test_remote_attacker_spoofing_cf_connecting_ip(self):
		"""Attacker connecting directly from public IP with fake CF-Connecting-IP MUST BE REJECTED."""
		req = _DummyRequest(
			remote="203.0.113.88",
			headers={"CF-Connecting-IP": "100.64.0.1"},
		)
		self.assertFalse(is_trusted_tailscale_or_local(req))

	def test_cloudflare_tunnel_proxying_remote_client(self):
		"""Local cloudflared tunnel on 127.0.0.1 with public CF-Connecting-IP MUST BE REJECTED."""
		req = _DummyRequest(
			remote="127.0.0.1",
			headers={"CF-Connecting-IP": "203.0.113.88"},
		)
		self.assertFalse(is_trusted_tailscale_or_local(req))

	def test_local_proxy_with_remote_client_in_forwarded_for(self):
		"""Local reverse proxy forwarding an external client MUST BE REJECTED."""
		req = _DummyRequest(
			remote="127.0.0.1",
			headers={"X-Forwarded-For": "203.0.113.88, 127.0.0.1"},
		)
		self.assertFalse(is_trusted_tailscale_or_local(req))

	def test_tailscale_serve_local_proxy_with_tailscale_client(self):
		"""Tailscale Serve running on 127.0.0.1 forwarding a Tailscale client MUST BE ACCEPTED."""
		req = _DummyRequest(
			remote="127.0.0.1",
			headers={"X-Forwarded-For": "100.64.15.2, 127.0.0.1"},
		)
		self.assertTrue(is_trusted_tailscale_or_local(req))
		info = detect_network_info(req)
		self.assertTrue(info["is_trusted"])
		self.assertEqual(info["network_type"], "tailscale")


class TestLocalLoginPermissions(unittest.TestCase):
	"""Test permission verification for login without authentication."""

	def setUp(self):
		self.db = _DummyUsersDb()
		self.groups_cfg = {
			"owner": {"can_login_locally_without_auth": True},
			"admin": {"can_login_locally_without_auth": True},
			"power": {"can_login_locally_without_auth": False},
			"user": {"can_login_locally_without_auth": False},
			"guest": {"can_login_locally_without_auth": False},
		}

	def test_owner_always_permitted(self):
		self.assertTrue(user_can_login_locally_without_auth("superowner", self.db, self.groups_cfg))

	def test_admin_default_permitted(self):
		self.assertTrue(user_can_login_locally_without_auth("sysadmin", self.db, self.groups_cfg))

	def test_user_default_denied(self):
		self.assertFalse(user_can_login_locally_without_auth("regularuser", self.db, self.groups_cfg))

	def test_guest_default_denied(self):
		self.assertFalse(user_can_login_locally_without_auth("guest", self.db, self.groups_cfg))

	def test_custom_permitted_role(self):
		custom_cfg = dict(self.groups_cfg)
		custom_cfg["power"] = {"can_login_locally_without_auth": True}
		self.assertTrue(user_can_login_locally_without_auth("poweruser", self.db, custom_cfg))

	def test_unknown_user_denied(self):
		self.assertFalse(user_can_login_locally_without_auth("nonexistent", self.db, self.groups_cfg))


class _DummyLogger:
	def error(self, *args, **kwargs):
		pass

	def info(self, *args, **kwargs):
		pass

	def warning(self, *args, **kwargs):
		pass


class TestJwtTokenIntegrity(unittest.TestCase):
	"""Verify JWT tokens are not damaged, broken, or altered by new local-login features."""

	def setUp(self):
		self.db = _DummyUsersDb()
		self.secret_key = "test_super_secret_signing_key_32bytes"
		self.jwt_auth = _jwt_mod.JWTAuth(
			users_db=self.db,
			access_control=None,
			logger=_DummyLogger(),
			secret_key=self.secret_key,
			expire_minutes=60,
			algorithm="HS256",
		)

	def test_standard_jwt_generation_and_decoding(self):
		token = self.jwt_auth.create_access_token({"id": "id-1", "username": "superowner"})
		self.assertIsInstance(token, str)
		self.assertEqual(token.count("."), 2)  # Header, Payload, Signature
		payload = self.jwt_auth.decode_access_token(token)
		self.assertEqual(payload["id"], "id-1")
		self.assertEqual(payload["username"], "superowner")
		self.assertIn("jti", payload)
		self.assertIn("exp", payload)
		self.assertTrue(self.jwt_auth.is_token_valid(token))

	def test_non_expiring_jwt_token(self):
		token = self.jwt_auth.create_access_token(
			{"id": "id-1", "username": "superowner"}, no_expiration=True
		)
		payload = self.jwt_auth.decode_access_token(token)
		self.assertEqual(payload["username"], "superowner")
		self.assertNotIn("exp", payload)
		self.assertTrue(self.jwt_auth.is_token_valid(token))

	def test_tampered_signature_rejected(self):
		token = self.jwt_auth.create_access_token({"id": "id-1", "username": "superowner"})
		parts = token.split(".")
		tampered = f"{parts[0]}.{parts[1]}.invalidsignature"
		self.assertFalse(self.jwt_auth.is_token_valid(tampered))

	def test_invalid_token_strings_rejected(self):
		self.assertFalse(self.jwt_auth.is_token_valid("invalid.token"))
		self.assertFalse(self.jwt_auth.is_token_valid(""))
		self.assertFalse(self.jwt_auth.is_token_valid(None))

	def test_token_user_mismatch_rejected(self):
		# User id does not match users_db
		token = self.jwt_auth.create_access_token({"id": "wrong-id", "username": "superowner"})
		self.assertFalse(self.jwt_auth.is_token_valid(token))

	def test_token_unknown_user_rejected(self):
		token = self.jwt_auth.create_access_token({"id": "id-999", "username": "ghost"})
		self.assertFalse(self.jwt_auth.is_token_valid(token))


def run_tests():
	suite = unittest.defaultTestLoader.loadTestsFromTestCase(TestTailscaleIpClassification)
	suite.addTests(unittest.defaultTestLoader.loadTestsFromTestCase(TestAntiSpoofingNetworkGuards))
	suite.addTests(unittest.defaultTestLoader.loadTestsFromTestCase(TestLocalLoginPermissions))
	suite.addTests(unittest.defaultTestLoader.loadTestsFromTestCase(TestJwtTokenIntegrity))
	runner = unittest.TextTestRunner(verbosity=2)
	result = runner.run(suite)
	return result.wasSuccessful()


if __name__ == "__main__":
	sys.exit(0 if run_tests() else 1)
