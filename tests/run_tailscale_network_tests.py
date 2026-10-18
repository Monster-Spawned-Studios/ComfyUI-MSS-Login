"""
Standalone tests for Tailscale / local network trust (NEW-A) and JWT integrity.

NEW-A: DEFAULT_LOCAL_PRIVATE_CIDRS never grants client trust. Only Tailscale +
explicit non-public local_network_cidrs. Proxy-hop trust is separate (trusted_proxies).
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
# Ensure empty trusted_proxies / local_network for tests
_constants.TRUSTED_PROXIES = []
_constants.LOCAL_NETWORK_CIDRS = []
_constants.CLOUDFLARE_PROXY = False

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
get_ip = _ip_filter.get_ip
is_trusted_proxy_peer = _ip_filter.is_trusted_proxy_peer


class _DummyRequest:
	def __init__(self, remote: str = None, headers: dict = None, cookies: dict = None):
		self.remote = remote
		self.headers = headers or {}
		self.cookies = cookies or {}

	def get(self, key, default=None):
		return default


class _DummyUsersDb:
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
	def test_tailscale_ipv4_cgnat(self):
		self.assertEqual(classify_ip("100.64.0.1"), "tailscale")
		self.assertEqual(classify_ip("100.80.25.10"), "tailscale")

	def test_tailscale_ipv6_ula(self):
		self.assertEqual(classify_ip("fd7a:115c:a1e0::1"), "tailscale")

	def test_unlisted_private_is_private_untrusted(self):
		"""NEW-A: RFC1918 / loopback without explicit list → private-untrusted, not local."""
		self.assertEqual(classify_ip("127.0.0.1"), "private-untrusted")
		self.assertEqual(classify_ip("10.0.0.5"), "private-untrusted")
		self.assertEqual(classify_ip("172.16.0.1"), "private-untrusted")
		self.assertEqual(classify_ip("192.168.1.100"), "private-untrusted")
		self.assertEqual(classify_ip("::1"), "private-untrusted")

	def test_explicit_lan_is_local(self):
		custom = ["192.168.1.0/24"]
		self.assertEqual(classify_ip("192.168.1.50", custom_cidrs=custom), "local")
		self.assertEqual(classify_ip("10.0.0.5", custom_cidrs=custom), "private-untrusted")

	def test_public_cidr_ignored(self):
		custom = ["8.8.8.0/24"]
		self.assertEqual(classify_ip("8.8.8.8", custom_cidrs=custom), "remote")
		trusted = get_all_trusted_cidrs(custom)
		self.assertFalse(is_ip_in_cidrs("8.8.8.8", trusted))

	def test_remote_public_ips(self):
		# Documentation TEST-NET may be non-global on some Python builds → private-untrusted OK
		self.assertIn(classify_ip("203.0.113.1"), ("remote", "private-untrusted"))
		self.assertEqual(classify_ip("8.8.8.8"), "remote")

	def test_empty_list_is_tailscale_only(self):
		trusted = get_all_trusted_cidrs([])
		self.assertTrue(is_ip_in_cidrs("100.64.0.1", trusted))
		self.assertFalse(is_ip_in_cidrs("192.168.1.1", trusted))


class TestNewATrustMatrix(unittest.TestCase):
	def test_tailscale_client_trusted(self):
		req = _DummyRequest(remote="100.64.0.15")
		self.assertTrue(is_trusted_tailscale_or_local(req))

	def test_explicit_lan_trusted(self):
		req = _DummyRequest(remote="192.168.1.50")
		self.assertTrue(is_trusted_tailscale_or_local(req, custom_cidrs=["192.168.1.0/24"]))

	def test_unlisted_private_rejected(self):
		for ip in ("10.1.2.3", "172.16.0.9", "192.168.0.5", "127.0.0.1"):
			req = _DummyRequest(remote=ip)
			self.assertFalse(is_trusted_tailscale_or_local(req), f"should reject {ip}")

	def test_public_in_list_ignored_and_rejected(self):
		req = _DummyRequest(remote="8.8.8.8")
		self.assertFalse(is_trusted_tailscale_or_local(req, custom_cidrs=["8.8.8.8/32"]))

	def test_tunnel_public_cf_rejected(self):
		"""Private cloudflared peer + public CF-Connecting-IP → rejected (no trusted_proxies)."""
		req = _DummyRequest(remote="127.0.0.1", headers={"CF-Connecting-IP": "203.0.113.88"})
		self.assertFalse(is_trusted_tailscale_or_local(req))

	def test_xff_spoof_from_untrusted_peer_rejected(self):
		req = _DummyRequest(remote="203.0.113.88", headers={"X-Forwarded-For": "100.64.0.1"})
		self.assertFalse(is_trusted_tailscale_or_local(req))
		self.assertEqual(get_ip(req), "203.0.113.88")

	def test_xff_through_trusted_proxy_allowed(self):
		req = _DummyRequest(remote="10.0.0.2", headers={"X-Forwarded-For": "100.64.15.2"})
		# Peer is trusted proxy; client is Tailscale
		self.assertEqual(get_ip(req, trusted_proxies=["10.0.0.2/32"]), "100.64.15.2")
		# Monkeypatch: temporarily trust proxy for is_trusted check via get_ip path
		_constants.TRUSTED_PROXIES = ["10.0.0.2/32"]
		try:
			self.assertTrue(is_trusted_tailscale_or_local(req))
		finally:
			_constants.TRUSTED_PROXIES = []

	def test_empty_cidrs_tailscale_only(self):
		self.assertTrue(is_trusted_tailscale_or_local(_DummyRequest(remote="100.64.1.1")))
		self.assertFalse(is_trusted_tailscale_or_local(_DummyRequest(remote="192.168.1.1")))

	def test_undeterminable_client_fails_closed(self):
		req = _DummyRequest(remote="not-an-ip")
		self.assertFalse(is_trusted_tailscale_or_local(req))
		self.assertEqual(get_ip(req), "")


class TestAntiSpoofingNetworkGuards(unittest.TestCase):
	def test_direct_remote_client_rejected(self):
		req = _DummyRequest(remote="203.0.113.195")
		self.assertFalse(is_trusted_tailscale_or_local(req))

	def test_remote_attacker_spoofing_x_forwarded_for(self):
		req = _DummyRequest(
			remote="203.0.113.88", headers={"X-Forwarded-For": "100.64.0.1, 192.168.1.1"}
		)
		self.assertFalse(is_trusted_tailscale_or_local(req))

	def test_remote_attacker_spoofing_cf_connecting_ip(self):
		req = _DummyRequest(remote="203.0.113.88", headers={"CF-Connecting-IP": "100.64.0.1"})
		self.assertFalse(is_trusted_tailscale_or_local(req))


class TestLocalLoginPermissions(unittest.TestCase):
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
		self.assertFalse(
			user_can_login_locally_without_auth("regularuser", self.db, self.groups_cfg)
		)


class _DummyLogger:
	def error(self, *args, **kwargs):
		pass

	def info(self, *args, **kwargs):
		pass

	def warning(self, *args, **kwargs):
		pass


class TestJwtTokenIntegrity(unittest.TestCase):
	"""JWT / Portal tokens are not broken by local-login or MFA enrollment checks."""

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
		self.assertEqual(token.count("."), 2)
		payload = self.jwt_auth.decode_access_token(token)
		self.assertEqual(payload["username"], "superowner")
		self.assertTrue(self.jwt_auth.is_token_valid(token))

	def test_resolve_identity_does_not_check_mfa(self):
		"""Comfy Portal: enrolled MFA must not invalidate an already-issued JWT."""
		token = self.jwt_auth.create_access_token({"id": "id-1", "username": "superowner"})
		req = _DummyRequest(headers={"Authorization": f"Bearer {token}"})
		# Fake get_token via cookie path
		req.headers = {"Authorization": f"Bearer {token}"}
		uid, uname = self.jwt_auth.resolve_token_identity(token, touch_idle=False)
		self.assertEqual(uname, "superowner")
		self.assertEqual(uid, "id-1")
		self.assertTrue(self.jwt_auth.is_token_valid(token))

	def test_tampered_signature_rejected(self):
		token = self.jwt_auth.create_access_token({"id": "id-1", "username": "superowner"})
		parts = token.split(".")
		self.assertFalse(self.jwt_auth.is_token_valid(f"{parts[0]}.{parts[1]}.invalidsignature"))

	def test_token_user_mismatch_rejected(self):
		token = self.jwt_auth.create_access_token({"id": "wrong-id", "username": "superowner"})
		self.assertFalse(self.jwt_auth.is_token_valid(token))


def run_tests():
	suite = unittest.defaultTestLoader.loadTestsFromTestCase(TestTailscaleIpClassification)
	suite.addTests(unittest.defaultTestLoader.loadTestsFromTestCase(TestNewATrustMatrix))
	suite.addTests(unittest.defaultTestLoader.loadTestsFromTestCase(TestAntiSpoofingNetworkGuards))
	suite.addTests(unittest.defaultTestLoader.loadTestsFromTestCase(TestLocalLoginPermissions))
	suite.addTests(unittest.defaultTestLoader.loadTestsFromTestCase(TestJwtTokenIntegrity))
	runner = unittest.TextTestRunner(verbosity=2)
	result = runner.run(suite)
	return result.wasSuccessful()


if __name__ == "__main__":
	sys.exit(0 if run_tests() else 1)
