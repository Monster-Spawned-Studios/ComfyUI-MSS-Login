r"""
Standalone tests: CivitAI JSON GET must re-check allowlist on redirects (AUDIT-008).

  .venv/bin/python tests/run_civitai_json_redirect_tests.py
"""

from __future__ import annotations

import asyncio
import importlib.util
import os
import sys
import types
from unittest.mock import patch

_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_UTILS = os.path.join(_PROJECT_ROOT, "utils")


def _ok(cond: bool, msg: str, failed: list[str]) -> None:
	if cond:
		print(f"  ok: {msg}")
	else:
		print(f"  FAIL: {msg}")
		failed.append(msg)


def _load_model_download():
	if "utils" not in sys.modules:
		pkg = types.ModuleType("utils")
		pkg.__path__ = [_UTILS]
		pkg.__file__ = os.path.join(_UTILS, "__init__.py")
		sys.modules["utils"] = pkg

	user_env = types.ModuleType("utils.user_env")
	user_env.load_user_settings = lambda *_a, **_k: {}
	user_env.save_user_settings = lambda *_a, **_k: None
	sys.modules["utils.user_env"] = user_env

	path = os.path.join(_UTILS, "model_download.py")
	spec = importlib.util.spec_from_file_location("utils.model_download", path)
	mod = importlib.util.module_from_spec(spec)
	sys.modules["utils.model_download"] = mod
	spec.loader.exec_module(mod)
	return mod


def test_civitai_json_uses_fetch_allowlisted(failed: list[str]) -> None:
	print("TestCivitaiJsonUsesFetchAllowlisted")
	md = _load_model_download()

	called = {"n": 0, "url": None}

	async def fake_fetch(session, url, *, headers=None, params=None, max_redirects=8):
		called["n"] += 1
		called["url"] = url

		class _Resp:
			status = 200

			async def json(self, content_type=None):
				return {"items": []}

			async def text(self):
				return ""

			async def release(self):
				return None

		return _Resp()

	async def run():
		with patch.object(md, "fetch_allowlisted", fake_fetch):
			data, err = await md._civitai_get_json("models", token=None, host="civitai.com")
		return data, err

	data, err = asyncio.run(run())
	_ok(called["n"] == 1, "fetch_allowlisted called once", failed)
	_ok(err == "", f"no error ({err})", failed)
	_ok(isinstance(data, dict), "JSON payload returned", failed)
	_ok("civitai.com" in (called["url"] or ""), "request targets civitai host", failed)


def test_redirect_off_allowlist_rejected(failed: list[str]) -> None:
	print("TestRedirectOffAllowlistRejected")
	md = _load_model_download()

	class _RedirectResp:
		status = 302
		headers = {"Location": "http://127.0.0.1/steal"}

		async def release(self):
			return None

	class _Session:
		async def __aenter__(self):
			return self

		async def __aexit__(self, *a):
			return False

		async def get(self, url, params=None, headers=None, allow_redirects=False):
			# First hop: allowlisted civitai; redirect to localhost (SSRF)
			return _RedirectResp()

	async def run():
		with patch.object(md.aiohttp, "ClientSession", lambda **k: _Session()):
			return await md._civitai_get_json("models", token=None, host="civitai.com")

	data, err = asyncio.run(run())
	_ok(data is None, "no payload when redirect leaves allowlist", failed)
	_ok(bool(err), "error message present", failed)
	err_l = (err or "").lower()
	_ok(
		any(
			s in err_l
			for s in (
				"allowlist",
				"localhost",
				"127.0.0.1",
				"ssrf",
				"https",
				"not allowed",
				"only https",
			)
		),
		f"error indicates blocked redirect ({err})",
		failed,
	)


def test_cloudflare_r2_redirect_allowed(failed: list[str]) -> None:
	print("TestCloudflareR2RedirectAllowed")
	md = _load_model_download()

	r2_host = (
		"civitai-delivery-worker-prod.5ac0637cfd0766c97916cefa3764fbdf.r2.cloudflarestorage.com"
	)
	_ok(
		md.is_download_redirect_host_allowed(r2_host),
		"civitai-delivery-worker-prod R2 host is allowed",
		failed,
	)
	_ok(
		md.is_download_redirect_host_allowed("civitai.r2.cloudflarestorage.com"),
		"civitai R2 host is allowed",
		failed,
	)
	_ok(
		md.is_download_redirect_host_allowed("civitai-models.s3.amazonaws.com"),
		"civitai S3 host is allowed",
		failed,
	)
	_ok(
		not md.is_download_redirect_host_allowed("malicious.r2.cloudflarestorage.com"),
		"non-civitai R2 host is rejected",
		failed,
	)
	_ok(
		not md.is_download_redirect_host_allowed("attacker-s3.amazonaws.com"),
		"non-civitai S3 host is rejected",
		failed,
	)


def test_authorization_header_stripped_on_r2_redirect(failed: list[str]) -> None:
	print("TestAuthorizationHeaderStrippedOnR2Redirect")
	md = _load_model_download()

	captured_headers = []

	class _RedirectResp:
		def __init__(self, location):
			self.status = 307
			self.headers = {"Location": location}

		async def release(self):
			return None

	class _FinalResp:
		status = 200
		headers = {}

		async def release(self):
			return None

	class _Session:
		def __init__(self):
			self.call_count = 0

		async def get(self, url, params=None, headers=None, allow_redirects=False):
			self.call_count += 1
			captured_headers.append((url, dict(headers or {})))
			if self.call_count == 1:
				return _RedirectResp(
					"https://civitai-delivery-worker-prod.5ac0637cfd0766c97916cefa3764fbdf.r2.cloudflarestorage.com/model.safetensors?X-Amz-Signature=abc"
				)
			return _FinalResp()

	async def run():
		sess = _Session()
		headers = {
			"Authorization": "Bearer secret_civitai_token",
			"User-Agent": "MSS-Login/Test",
		}
		resp = await md.fetch_allowlisted(
			sess, "https://civitai.com/api/download/models/12345", headers=headers
		)
		await resp.release()

	asyncio.run(run())

	_ok(len(captured_headers) == 2, "followed 1 redirect (2 total requests)", failed)
	# Hop 1: civitai.com has Authorization
	hop1_url, hop1_hdrs = captured_headers[0]
	_ok("civitai.com" in hop1_url, "hop 1 requested civitai.com", failed)
	_ok(
		"Authorization" in hop1_hdrs,
		"hop 1 contained Authorization header",
		failed,
	)
	# Hop 2: R2 storage has Authorization STRIPPED, User-Agent kept
	hop2_url, hop2_hdrs = captured_headers[1]
	_ok("r2.cloudflarestorage.com" in hop2_url, "hop 2 requested R2 storage", failed)
	_ok(
		"Authorization" not in hop2_hdrs and "authorization" not in hop2_hdrs,
		"hop 2 stripped Authorization header on R2 redirect",
		failed,
	)
	_ok(
		hop2_hdrs.get("User-Agent") == "MSS-Login/Test",
		"hop 2 preserved non-auth User-Agent header",
		failed,
	)


def main() -> int:
	failed: list[str] = []
	try:
		test_civitai_json_uses_fetch_allowlisted(failed)
		test_redirect_off_allowlist_rejected(failed)
		test_cloudflare_r2_redirect_allowed(failed)
		test_authorization_header_stripped_on_r2_redirect(failed)
	except Exception as e:
		print(f"FAIL: {e}")
		import traceback

		traceback.print_exc()
		return 1
	print()
	if failed:
		print(f"Failed ({len(failed)}): {failed}")
		return 1
	print("All CivitAI JSON redirect tests passed.")
	return 0


if __name__ == "__main__":
	sys.exit(main())
