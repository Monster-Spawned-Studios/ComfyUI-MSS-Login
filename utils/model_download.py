"""
Download models from CivitAI and HuggingFace to a local path or S3 mount path.
"""

import asyncio
import os
from pathlib import Path
from typing import Awaitable, Callable, Optional, Union

# Type for progress callback: (bytes_done, total_bytes) -> None or awaitable
ProgressCallback = Optional[Callable[[int, Optional[int]], Union[None, Awaitable[None]]]]

# CivitAI: HTTP stream with token
CIVITAI_DOWNLOAD_BASE = "https://civitai.com/api/download/models"


async def download_civitai_async(
	model_version_id: str,
	token: str,
	dest_path: str | Path,
	type_param: Optional[str] = None,
	format_param: Optional[str] = None,
	size_param: Optional[str] = None,
	fp_param: Optional[str] = None,
	progress_callback: ProgressCallback = None,
) -> tuple[bool, str]:
	"""
	Download a model file from CivitAI to dest_path. Async.
	Returns (success, error_message). Follows redirects; token via query or header.
	"""
	import aiohttp

	url = f"{CIVITAI_DOWNLOAD_BASE}/{model_version_id}"
	params = {}
	if token:
		params["token"] = token
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
		headers = {"Authorization": f"Bearer {token}"} if token else {}
		timeout = aiohttp.ClientTimeout(total=None, sock_read=300)
		async with aiohttp.ClientSession(timeout=timeout) as session:
			async with session.get(
				url, params=params or None, headers=headers or None, allow_redirects=True
			) as resp:
				if resp.status != 200:
					return False, f"CivitAI returned {resp.status}"
				content_disp = resp.headers.get("Content-Disposition")
				filename = None
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
					return False, "Path traversal prevented"
				if os.path.abspath(common) != os.path.abspath(dest_resolved):
					out = dest_path / Path(filename).name
					out_resolved = out.resolve()
					try:
						common = os.path.commonpath([out_resolved, dest_resolved])
					except ValueError:
						return False, "Path traversal prevented"
					if os.path.abspath(common) != os.path.abspath(dest_resolved):
						return False, "Path traversal prevented"
				out.parent.mkdir(parents=True, exist_ok=True)
				raw_len = getattr(resp, "content_length", None) or resp.headers.get(
					"Content-Length"
				)
				total_bytes = None
				if raw_len is not None:
					try:
						total_bytes = int(raw_len)
					except (TypeError, ValueError):
						pass
				bytes_done = 0
				try:
					with open(out, "wb") as f:
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
					# Remove partial file so a failed download doesn't leave corrupt data
					try:
						if out.exists():
							out.unlink()
					except OSError:
						pass
					raise
				return True, ""
	except Exception as e:
		return False, str(e)


def download_huggingface(
	repo_id: str,
	filename: str,
	token: str,
	dest_dir: str | Path,
	subfolder: Optional[str] = None,
	progress_dict: Optional[dict] = None,
) -> tuple[bool, str]:
	"""
	Download a file from HuggingFace Hub to dest_dir. Uses huggingface_hub if available.
	Returns (success, error_message).
	If progress_dict is provided, it is updated with bytes_done and total_bytes during download
	(for streaming progress to the client). Keys: bytes_done (int), total_bytes (int or None).
	"""
	dest_dir = Path(dest_dir)
	dest_dir.mkdir(parents=True, exist_ok=True)
	try:
		from huggingface_hub import hf_hub_download
	except ImportError:
		return False, "huggingface_hub is required; pip install huggingface_hub"

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
			local_dir_use_symlinks=False,
			subfolder=subfolder,
			tqdm_class=tqdm_class,
		)
		return bool(path), ""
	except Exception as e:
		return False, str(e)
