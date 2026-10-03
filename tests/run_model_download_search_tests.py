r"""
Standalone tests for CivitAI/HF search helpers and filename preference.

  .venv/bin/python tests/run_model_download_search_tests.py
"""

from __future__ import annotations

import asyncio
import importlib.util
import os
import sys
import types
from pathlib import Path
from unittest.mock import MagicMock, patch

_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_utils = os.path.join(_PROJECT_ROOT, "utils")


def _ensure_utils_pkg():
	if "utils" not in sys.modules:
		pkg = types.ModuleType("utils")
		pkg.__path__ = [_utils]
		pkg.__file__ = os.path.join(_utils, "__init__.py")
		sys.modules["utils"] = pkg


def _load_module(full_name, filename):
	_ensure_utils_pkg()
	path = os.path.join(_utils, filename)
	spec = importlib.util.spec_from_file_location(full_name, path)
	mod = importlib.util.module_from_spec(spec)
	sys.modules[full_name] = mod
	short = full_name.split(".", 1)[-1] if full_name.startswith("utils.") else full_name
	sys.modules[f"utils.{short}"] = mod
	spec.loader.exec_module(mod)
	return mod


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
		return cond

	# Stub user_env before loading model_download
	user_env = types.ModuleType("utils.user_env")
	user_env.load_user_settings = lambda *_a, **_k: {}
	user_env.save_user_settings = lambda *_a, **_k: None
	sys.modules["utils.user_env"] = user_env

	md = _load_module("utils.model_download", "model_download.py")

	print("TestCivitaiSearchParams")
	captured = {}

	async def fake_get_json(path, token, host=None, params=None):
		captured["params"] = dict(params or {})
		return {"items": [], "metadata": {}}, ""

	async def run_search_query():
		with patch.object(md, "_civitai_get_json", fake_get_json):
			await md.civitai_search_models("anime", page=3, limit=20, types="LORA")

	asyncio.run(run_search_query())
	ok("query" in captured["params"], "text search includes query")
	ok("page" not in captured["params"], "text search omits page")
	ok(captured["params"].get("types") == "LORA", "types forwarded")

	async def run_search_cursor():
		with patch.object(md, "_civitai_get_json", fake_get_json):
			await md.civitai_search_models("anime", cursor="abc123", sort="Most Downloaded")

	asyncio.run(run_search_cursor())
	ok(captured["params"].get("cursor") == "abc123", "cursor forwarded for text search")
	ok(captured["params"].get("sort") == "Most Downloaded", "sort forwarded")
	ok("page" not in captured["params"], "cursor search still omits page")

	async def run_browse():
		with patch.object(md, "_civitai_get_json", fake_get_json):
			await md.civitai_search_models("", page=2, limit=10, primary_file_only=True)

	asyncio.run(run_browse())
	ok(captured["params"].get("page") == 2, "browse keeps page")
	ok("query" not in captured["params"], "browse has no query")
	ok(captured["params"].get("primaryFileOnly") == "true", "primaryFileOnly forwarded")

	print("TestCivitaiErrorStatusMapping")
	ok(md.map_civitai_error_http_status("CivitAI returned 400: bad") == 400, "400 maps to 400")
	ok(md.map_civitai_error_http_status("CivitAI returned 401") == 401, "401 maps to 401")
	ok(md.map_civitai_error_http_status("CivitAI returned 429: slow") == 429, "429 maps to 429")
	ok(md.map_civitai_error_http_status("CivitAI returned 503") == 502, "503 maps to 502")
	ok(md.map_civitai_error_http_status("Connection timeout") == 502, "network maps to 502")

	print("TestPreferredFilenameWins")

	# Exercise filename selection logic via a thin unit of download_civitai_async internals
	# by mocking aiohttp response headers.
	async def run_preferred_filename():
		import tempfile

		with tempfile.TemporaryDirectory() as td:
			dest_dir = Path(td)
			chunk = b"fake-model-bytes"

			class FakeContent:
				async def read(self, _n):
					nonlocal chunk
					if chunk is None:
						return b""
					data = chunk
					chunk = None
					return data

			class FakeResp:
				status = 200
				headers = {
					"Content-Disposition": 'attachment; filename="server-name.safetensors"',
					"Content-Length": str(len(b"fake-model-bytes")),
				}
				content = FakeContent()
				content_length = len(b"fake-model-bytes")

				async def release(self):
					return None

				async def __aenter__(self):
					return self

				async def __aexit__(self, *a):
					return False

			class FakeSession:
				def __init__(self, *a, **k):
					pass

				async def __aenter__(self):
					return self

				async def __aexit__(self, *a):
					return False

				def get(self, *a, **k):
					return FakeResp()

			with patch.object(md.aiohttp, "ClientSession", FakeSession):
				ok_dl, err, saved = await md.download_civitai_async(
					"138296",
					token="tok",
					dest_path=dest_dir,
					preferred_filename="my-save.safetensors",
					resume=False,
				)
			exists = (dest_dir / "my-save.safetensors").is_file()
			return ok_dl, err, saved, exists

	ok_dl, err, saved, exists = asyncio.run(run_preferred_filename())
	ok(ok_dl is True and not err, f"download succeeded ({err})")
	ok(saved == "my-save.safetensors", f"preferred filename saved (got {saved!r})")
	ok(exists, "preferred file exists on disk")
	print("TestHfListRepoFilesFilter")

	class FakeApi:
		def list_repo_files(self, *_a, **_k):
			return [
				"README.md",
				"model.safetensors",
				"extra/vae.ckpt",
				"config.json",
				"weights.bin",
			]

		def get_paths_info(self, *_a, **_k):
			return []

	with patch.dict(
		"sys.modules", {"huggingface_hub": MagicMock(HfApi=lambda token=None: FakeApi())}
	):
		# Force re-import path inside function — patch huggingface_hub import inside function
		pass

	# Directly patch the import used inside list_huggingface_repo_files
	fake_hub = types.ModuleType("huggingface_hub")
	fake_hub.HfApi = lambda token=None: FakeApi()
	with patch.dict(sys.modules, {"huggingface_hub": fake_hub}):
		files, error = md.list_huggingface_repo_files("org/model")
	ok(not error, f"list files no error ({error})")
	names = [f["path"] for f in (files or [])]
	ok("model.safetensors" in names, "includes safetensors")
	ok("extra/vae.ckpt" in names, "includes nested ckpt")
	ok("weights.bin" in names, "includes bin")
	ok("README.md" not in names and "config.json" not in names, "filters non-model files")
	ok(names[0].endswith(".safetensors"), "safetensors sorted first")

	print()
	if failed:
		print(f"Result: {failed} failed, {run - failed} passed, {run} total")
		sys.exit(1)
	print(f"Result: all {run} tests passed")
	sys.exit(0)


if __name__ == "__main__":
	run_tests()
