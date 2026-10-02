"""
Download models from CivitAI and HuggingFace to a local path or S3 mount path.

CivitAI hosts are allowlisted (civitai.com / civitai.red). HuggingFace downloads
use huggingface_hub with local_dir only (no deprecated symlink flag).
"""

from __future__ import annotations

import asyncio
import os
import re
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Any, Optional
from urllib.parse import parse_qs, urlparse

import aiohttp

from .user_env import load_user_settings, save_user_settings

# Type for progress callback: (bytes_done, total_bytes) -> None or awaitable
ProgressCallback = Optional[Callable[[int, int | None], None | Awaitable[None]]]

ALLOWED_CIVITAI_HOSTS = frozenset({"civitai.com", "civitai.red"})
DEFAULT_CIVITAI_HOST = "civitai.com"
ALLOWED_HF_HOSTS = frozenset({"huggingface.co", "hf.co"})

_CIVITAI_MODEL_PATH_RE = re.compile(
	r"^/models/(?P<model_id>\d+)(?:/(?P<slug>[^/?#]+))?", re.IGNORECASE
)
_CIVITAI_DOWNLOAD_PATH_RE = re.compile(r"^/api/download/models/(?P<version_id>\d+)", re.IGNORECASE)
_CIVITAI_VERSION_API_PATH_RE = re.compile(
	r"^/api/v1/model-versions/(?P<version_id>\d+)", re.IGNORECASE
)
_HF_REPO_PATH_RE = re.compile(
	r"^/(?P<repo_id>[^/]+/[^/]+)(?:/(?P<kind>blob|resolve|raw)/(?P<revision>[^/]+)/(?P<path>.+))?$",
	re.IGNORECASE,
)


def normalize_civitai_host(host: str | None) -> str:
	"""Return an allowlisted CivitAI hostname; default to civitai.com."""
	raw = (host or "").strip().lower()
	if raw.startswith("https://"):
		raw = raw[len("https://") :]
	elif raw.startswith("http://"):
		raw = raw[len("http://") :]
	raw = raw.split("/")[0].split("?")[0].rstrip(".")
	raw = raw.removeprefix("www.")
	if raw in ALLOWED_CIVITAI_HOSTS:
		return raw
	return DEFAULT_CIVITAI_HOST


def civitai_api_base(host: str | None = None) -> str:
	"""Base URL for CivitAI /api/v1 endpoints on the preferred host."""
	return f"https://{normalize_civitai_host(host)}/api/v1"


def civitai_download_base(host: str | None = None) -> str:
	"""Base URL for CivitAI file downloads on the preferred host."""
	return f"https://{normalize_civitai_host(host)}/api/download/models"


def get_civitai_host_preference(username: str) -> str:
	"""Load per-user civitai_host from settings.json (non-secret). Default .com."""
	settings = load_user_settings(username)
	return normalize_civitai_host(
		settings.get("civitai_host") if isinstance(settings, dict) else None
	)


def set_civitai_host_preference(username: str, host: str) -> str:
	"""Persist per-user civitai_host preference. Returns the normalized host."""
	normalized = normalize_civitai_host(host)
	settings = load_user_settings(username)
	if not isinstance(settings, dict):
		settings = {}
	settings["civitai_host"] = normalized
	save_user_settings(username, settings)
	return normalized


def _civitai_auth_headers(token: str | None) -> dict[str, str]:
	"""Prefer Authorization Bearer for CivitAI requests."""
	if token:
		return {"Authorization": f"Bearer {token}"}
	return {}


def _is_allowlisted_hostname(hostname: str | None, allowed: frozenset[str]) -> bool:
	if not hostname:
		return False
	host = hostname.strip().lower().rstrip(".")
	host = host.removeprefix("www.")
	return host in allowed


def _reject_ssrf_url(parsed) -> str | None:
	"""Return an error string if URL is unsafe for server-side fetch; else None."""
	if parsed.scheme not in ("https",):
		return "Only https URLs are allowed"
	hostname = (parsed.hostname or "").strip().lower()
	if not hostname:
		return "Missing hostname"
	# Block literal IPs / localhost / link-local style hosts (SSRF).
	if hostname in ("localhost", "127.0.0.1", "::1", "0.0.0.0"):
		return "Hostname not allowed"
	if re.fullmatch(r"\d{1,3}(?:\.\d{1,3}){3}", hostname):
		return "IP addresses are not allowed"
	if ":" in hostname and hostname.count(":") >= 2:
		return "IP addresses are not allowed"
	return None


def resolve_civitai_url(url: str) -> dict[str, Any] | None:
	"""
	Parse a pasted CivitAI (.com/.red) URL into identifiers.

	Returns dict with keys: source, host, model_id?, model_version_id?
	or None if not a recognized allowlisted CivitAI URL.
	"""
	raw = (url or "").strip()
	if not raw:
		return None
	parsed = urlparse(raw if "://" in raw else f"https://{raw}")
	err = _reject_ssrf_url(parsed)
	if err:
		return None
	host = (parsed.hostname or "").lower()
	host = host.removeprefix("www.")
	if host not in ALLOWED_CIVITAI_HOSTS:
		return None
	path = parsed.path or ""
	qs = parse_qs(parsed.query or "")
	out: dict[str, Any] = {"source": "civitai", "host": host}

	m_dl = _CIVITAI_DOWNLOAD_PATH_RE.match(path)
	if m_dl:
		out["model_version_id"] = m_dl.group("version_id")
		return out

	m_ver = _CIVITAI_VERSION_API_PATH_RE.match(path)
	if m_ver:
		out["model_version_id"] = m_ver.group("version_id")
		return out

	m_model = _CIVITAI_MODEL_PATH_RE.match(path)
	if m_model:
		out["model_id"] = m_model.group("model_id")
		version_from_qs = (qs.get("modelVersionId") or qs.get("model_version_id") or [None])[0]
		if version_from_qs and str(version_from_qs).isdigit():
			out["model_version_id"] = str(version_from_qs)
		return out

	return None


def resolve_huggingface_url(url: str) -> dict[str, Any] | None:
	"""
	Parse a pasted Hugging Face URL into repo_id / filename / revision / subfolder.

	Returns dict with source=huggingface keys, or None if not allowlisted HF URL.
	"""
	raw = (url or "").strip()
	if not raw:
		return None
	parsed = urlparse(raw if "://" in raw else f"https://{raw}")
	err = _reject_ssrf_url(parsed)
	if err:
		return None
	host = (parsed.hostname or "").lower()
	host = host.removeprefix("www.")
	if host not in ALLOWED_HF_HOSTS:
		return None
	path = parsed.path or ""
	m = _HF_REPO_PATH_RE.match(path)
	if not m:
		return None
	repo_id = m.group("repo_id")
	out: dict[str, Any] = {"source": "huggingface", "repo_id": repo_id, "host": host}
	file_path = m.group("path")
	if file_path:
		# Strip trailing query fragments already handled by urlparse; keep nested path.
		file_path = file_path.rstrip("/")
		if "/" in file_path:
			subfolder, filename = file_path.rsplit("/", 1)
			out["subfolder"] = subfolder
			out["filename"] = filename
		else:
			out["filename"] = file_path
		revision = m.group("revision")
		if revision:
			out["revision"] = revision
	return out


def resolve_model_url(url: str) -> dict[str, Any] | None:
	"""SSRF-safe resolve of pasted CivitAI or HuggingFace URLs into download identifiers."""
	return resolve_civitai_url(url) or resolve_huggingface_url(url)


async def _civitai_get_json(
	path: str, token: str | None, host: str | None = None, params: dict[str, Any] | None = None
) -> tuple[dict[str, Any] | list | None, str]:
	"""
	GET JSON from CivitAI /api/v1/... on the allowlisted host.
	Returns (payload, error_message).
	"""
	base = civitai_api_base(host)
	# path must be relative under /api/v1
	rel = (path or "").lstrip("/")
	url = f"{base}/{rel}"
	headers = _civitai_auth_headers(token)
	try:
		timeout = aiohttp.ClientTimeout(total=60, sock_read=60)
		async with (
			aiohttp.ClientSession(timeout=timeout) as session,
			session.get(
				url, params=params or None, headers=headers or None, allow_redirects=True
			) as resp,
		):
			if resp.status != 200:
				text = ""
				try:
					text = (await resp.text())[:200]
				except Exception:
					pass
				return None, f"CivitAI returned {resp.status}" + (f": {text}" if text else "")
			try:
				data = await resp.json(content_type=None)
			except Exception as e:
				return None, f"Invalid JSON from CivitAI: {e}"
			return data, ""
	except Exception as e:
		return None, str(e)


async def civitai_search_models(
	query: str,
	token: str | None = None,
	host: str | None = None,
	types: str | None = None,
	page: int = 1,
	limit: int = 20,
) -> tuple[dict[str, Any] | None, str]:
	"""Search CivitAI models via GET /api/v1/models."""
	params: dict[str, Any] = {
		"limit": max(1, min(int(limit or 20), 100)),
		"page": max(1, int(page or 1)),
	}
	q = (query or "").strip()
	if q:
		params["query"] = q
	if types:
		params["types"] = types
	return await _civitai_get_json("models", token, host=host, params=params)


async def civitai_get_model(
	model_id: str | int, token: str | None = None, host: str | None = None
) -> tuple[dict[str, Any] | None, str]:
	"""Fetch a CivitAI model by id via GET /api/v1/models/{id}."""
	mid = str(model_id).strip()
	if not mid.isdigit():
		return None, "Invalid model id"
	return await _civitai_get_json(f"models/{mid}", token, host=host)


async def civitai_get_model_version(
	version_id: str | int, token: str | None = None, host: str | None = None
) -> tuple[dict[str, Any] | None, str]:
	"""Fetch a CivitAI model version by id via GET /api/v1/model-versions/{id}."""
	vid = str(version_id).strip()
	if not vid.isdigit():
		return None, "Invalid model version id"
	return await _civitai_get_json(f"model-versions/{vid}", token, host=host)


def search_huggingface_models(
	query: str, token: str | None = None, limit: int = 20
) -> tuple[list[dict[str, Any]] | None, str]:
	"""
	Search Hugging Face Hub models. Returns a list of public-safe dicts (no secrets).
	"""
	try:
		from huggingface_hub import HfApi
	except ImportError:
		return None, "huggingface_hub is required; pip install huggingface_hub"

	q = (query or "").strip()
	lim = max(1, min(int(limit or 20), 100))
	try:
		api = HfApi(token=token or None)
		results = list(api.list_models(search=q or None, limit=lim, full=False))
		out: list[dict[str, Any]] = []
		for item in results:
			repo_id = getattr(item, "id", None) or getattr(item, "modelId", None) or ""
			out.append(
				{
					"id": repo_id,
					"repo_id": repo_id,
					"downloads": getattr(item, "downloads", None),
					"likes": getattr(item, "likes", None),
					"tags": list(getattr(item, "tags", None) or [])[:20],
					"pipeline_tag": getattr(item, "pipeline_tag", None),
					"private": bool(getattr(item, "private", False)),
				}
			)
		return out, ""
	except Exception as e:
		return None, str(e)


async def download_civitai_async(
	model_version_id: str,
	token: str,
	dest_path: str | Path,
	type_param: str | None = None,
	format_param: str | None = None,
	size_param: str | None = None,
	fp_param: str | None = None,
	progress_callback: ProgressCallback = None,
	host: str | None = None,
	resume: bool = True,
	preferred_filename: str | None = None,
) -> tuple[bool, str, str | None]:
	"""
	Download a model file from CivitAI to dest_path. Async.
	Returns (success, error_message, saved_relpath). Prefer Authorization Bearer; follow redirects.
	Requires a token for file downloads (current CivitAI policy).

	When resume=True and a partial file exists, sends Range and appends (keeps
	partials on failure so the job can be resumed later).
	"""
	if not (token or "").strip():
		return False, "CivitAI API token required for downloads", None

	url = f"{civitai_download_base(host)}/{model_version_id}"
	params: dict[str, str] = {}
	# Prefer Bearer; do not put the token in the query string.
	if type_param:
		params["type"] = type_param
	if format_param:
		params["format"] = format_param
	if size_param:
		params["size"] = size_param
	if fp_param:
		params["fp"] = fp_param

	dest_path = Path(dest_path)
	dest_path.parent.mkdir(parents=True, exist_ok=True)

	try:
		headers = _civitai_auth_headers(token)
		timeout = aiohttp.ClientTimeout(total=None, sock_read=300)
		async with (
			aiohttp.ClientSession(timeout=timeout) as session,
			session.get(
				url, params=params or None, headers=headers or None, allow_redirects=True
			) as resp,
		):
			# First request discovers filename / Content-Length; may re-request with Range.
			if resp.status not in (200, 206):
				# If we intended resume, try without Range path below after HEAD-like get fails
				return False, f"CivitAI returned {resp.status}", None
			content_disp = resp.headers.get("Content-Disposition")
			filename = None
			if preferred_filename:
				_basename = Path(preferred_filename).name
				if _basename:
					filename = _basename
			if content_disp and "filename=" in content_disp:
				part = content_disp.split("filename=")[-1].strip().strip("\"'")
				if part:
					# Use basename only; if Path.name is empty (e.g. input was "/" or ".."),
					# discard the server-provided name entirely rather than falling back
					# to the raw value, which could contain path traversal sequences.
					_basename = Path(part).name
					if _basename:
						filename = _basename
			if not filename and dest_path.suffix:
				filename = dest_path.name
			if not filename:
				filename = f"model_{model_version_id}.safetensors"
			out = dest_path if dest_path.suffix else dest_path / filename
			if out.is_dir():
				out = dest_path / filename
			# Ensure final path stays under dest_path (defense in depth)
			dest_resolved = dest_path.resolve()
			out_resolved = out.resolve()
			try:
				common = os.path.commonpath([out_resolved, dest_resolved])
			except ValueError:
				return False, "Path traversal prevented", None
			if os.path.abspath(common) != os.path.abspath(dest_resolved):
				out = dest_path / Path(filename).name
				out_resolved = out.resolve()
				try:
					common = os.path.commonpath([out_resolved, dest_resolved])
				except ValueError:
					return False, "Path traversal prevented", None
				if os.path.abspath(common) != os.path.abspath(dest_resolved):
					return False, "Path traversal prevented", None
			out.parent.mkdir(parents=True, exist_ok=True)

			existing = 0
			if resume and out.is_file():
				try:
					existing = out.stat().st_size
				except OSError:
					existing = 0

			# If partial exists, re-request with Range (current response may be full body).
			if existing > 0:
				await resp.release()
				range_headers = {**headers, "Range": f"bytes={existing}-"}
				async with session.get(
					url, params=params or None, headers=range_headers or None, allow_redirects=True
				) as range_resp:
					if range_resp.status == 206:
						return await _stream_civitai_body(
							range_resp,
							out,
							dest_resolved,
							bytes_done_start=existing,
							progress_callback=progress_callback,
							append=True,
							keep_partial_on_error=True,
						)
					if range_resp.status == 200:
						# Server ignored Range — restart from scratch.
						existing = 0
						return await _stream_civitai_body(
							range_resp,
							out,
							dest_resolved,
							bytes_done_start=0,
							progress_callback=progress_callback,
							append=False,
							keep_partial_on_error=True,
						)
					return False, f"CivitAI returned {range_resp.status}", None

			return await _stream_civitai_body(
				resp,
				out,
				dest_resolved,
				bytes_done_start=0,
				progress_callback=progress_callback,
				append=False,
				keep_partial_on_error=True,
			)
	except Exception as e:
		return False, str(e), None


async def _stream_civitai_body(
	resp,
	out: Path,
	dest_resolved: Path,
	*,
	bytes_done_start: int,
	progress_callback: ProgressCallback,
	append: bool,
	keep_partial_on_error: bool,
) -> tuple[bool, str, str | None]:
	raw_len = getattr(resp, "content_length", None) or resp.headers.get("Content-Length")
	content_range = resp.headers.get("Content-Range") or ""
	total_bytes = None
	if content_range and "/" in content_range:
		try:
			total_bytes = int(content_range.rsplit("/", 1)[-1])
		except (TypeError, ValueError):
			pass
	if total_bytes is None and raw_len is not None:
		try:
			chunk_len = int(raw_len)
			total_bytes = (bytes_done_start + chunk_len) if append else chunk_len
		except (TypeError, ValueError):
			pass
	bytes_done = int(bytes_done_start)
	mode = "ab" if append else "wb"
	try:
		with open(out, mode) as f:
			while True:
				chunk = await resp.content.read(1024 * 1024)
				if not chunk:
					break
				f.write(chunk)
				bytes_done += len(chunk)
				if progress_callback:
					cb = progress_callback(bytes_done, total_bytes)
					if asyncio.iscoroutine(cb):
						await cb
	except Exception:
		if not keep_partial_on_error:
			try:
				if out.exists():
					out.unlink()
			except OSError:
				pass
		raise
	try:
		saved_rel = os.path.relpath(out.resolve(), dest_resolved)
	except ValueError:
		saved_rel = out.name
	return True, "", saved_rel.replace("\\", "/")


def download_huggingface(
	repo_id: str,
	filename: str,
	token: str,
	dest_dir: str | Path,
	subfolder: str | None = None,
	progress_dict: dict | None = None,
) -> tuple[bool, str, str | None]:
	"""
	Download a file from HuggingFace Hub to dest_dir. Uses huggingface_hub if available.
	Returns (success, error_message, saved_relpath).
	If progress_dict is provided, it is updated with bytes_done and total_bytes during download
	(for streaming progress to the client). Keys: bytes_done (int), total_bytes (int or None).
	"""
	dest_dir = Path(dest_dir)
	dest_dir.mkdir(parents=True, exist_ok=True)
	try:
		from huggingface_hub import hf_hub_download
	except ImportError:
		return False, "huggingface_hub is required; pip install huggingface_hub", None

	tqdm_class = None
	if progress_dict is not None:
		# Custom tqdm-like class that writes progress to progress_dict for the route to stream.
		class _ProgressTqdm:
			def __init__(self, total=None, **kwargs):
				self.total = total
				self.n = 0
				progress_dict["total_bytes"] = total
				progress_dict["bytes_done"] = 0

			def update(self, n=1):
				self.n += n
				progress_dict["bytes_done"] = self.n
				progress_dict["total_bytes"] = self.total

			def __enter__(self):
				return self

			def __exit__(self, *args):
				return False

			def close(self):
				pass

		tqdm_class = _ProgressTqdm

	try:
		path = hf_hub_download(
			repo_id=repo_id,
			filename=filename,
			token=token or None,
			local_dir=str(dest_dir),
			subfolder=subfolder,
			tqdm_class=tqdm_class,
		)
		if not path:
			return False, "Download returned empty path", None
		try:
			saved_rel = os.path.relpath(os.path.realpath(path), dest_dir.resolve())
		except ValueError:
			parts = [p for p in (subfolder, filename) if p]
			saved_rel = "/".join(parts) if parts else Path(path).name
		return True, "", saved_rel.replace("\\", "/")
	except Exception as e:
		return False, str(e), None
