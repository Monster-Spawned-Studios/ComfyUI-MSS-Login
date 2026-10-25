"""
ComfyUI nodes that queue MSS-Login model downloads and emit /ws progress.

Portal and desktop clients see ProgressBar updates while the node waits.
"""

from __future__ import annotations

import asyncio
import json
import time

from ..utils.isolation_resolver import is_uuid_segment
from ..utils.model_download_errors import DOWNLOAD_NOT_ALLOWED, DOWNLOAD_NOT_ALLOWED_MESSAGE
from ..utils.model_download_progress import aggregate_progress
from ..utils.model_visibility_policy import user_can_download_models


def _execution_context() -> tuple[str | None, str | None]:
	"""Return (prompt_id, node_id) so Portal can bind WS progress to the running node."""
	try:
		from comfy_execution.utils import get_executing_context

		ctx = get_executing_context()
		if ctx is not None:
			return (getattr(ctx, "prompt_id", None) or None, getattr(ctx, "node_id", None) or None)
	except Exception:
		pass
	try:
		from server import PromptServer

		ps = PromptServer.instance
		prompt_id = getattr(ps, "last_prompt_id", None)
		node_id = getattr(ps, "last_node_id", None)
		return prompt_id or None, node_id or None
	except Exception:
		return None, None


def _emit_progress(
	value: int, maximum: int, label: str = "", *, pb_holder: list | None = None
) -> None:
	"""Best-effort Comfy progress for Portal / frontend (reuses one ProgressBar)."""
	value = int(value)
	maximum = max(int(maximum), 1)
	prompt_id, node_id = _execution_context()
	try:
		from comfy.utils import ProgressBar

		pb = None
		if pb_holder is not None:
			if pb_holder and getattr(pb_holder[0], "total", None) == maximum:
				pb = pb_holder[0]
			else:
				pb_holder.clear()
				pb = ProgressBar(maximum)
				pb_holder.append(pb)
		else:
			pb = ProgressBar(maximum)
		pb.update_absolute(value)
		return
	except Exception:
		pass
	try:
		from server import PromptServer

		payload: dict = {"value": value, "max": maximum}
		if label:
			payload["text"] = label
		if prompt_id:
			payload["prompt_id"] = str(prompt_id)
		if node_id:
			payload["node"] = str(node_id)
		# Third arg scopes the event to the prompt's client when available (Portal WS).
		PromptServer.instance.send_sync("progress", payload, prompt_id)
	except Exception:
		pass


def _plain_model_name(name: str) -> str:
	"""Strip isolation user/UUID first segment so loaders/Portal see a plain filename."""
	raw = (name or "").replace("\\", "/").strip()
	if not raw:
		return name
	parts = raw.split("/")
	if len(parts) >= 2:
		first = parts[0]
		if is_uuid_segment(first):
			return "/".join(parts[1:])
		try:
			from ..utils.model_isolation import user_isolation_candidate_segments

			uid, uname, _, _ = _resolve_prompt_user()
			if uid or uname:
				candidates = user_isolation_candidate_segments(uid, uname)
				if first in candidates:
					return "/".join(parts[1:])
		except Exception:
			pass
	return raw


def _resolve_prompt_user() -> tuple[str | None, str | None, str, dict]:
	"""Return (user_id, username, role, perms) for the current prompt worker."""
	try:
		from ..globals import access_control, users_db

		user_id = access_control.get_current_user_id()
		rec = access_control._lookup_user_record(user_id) if user_id else None
		username = (rec or {}).get("username")
		if not username and user_id:
			try:
				_uid, rec2 = users_db.get_user(user_id=user_id)
				if rec2:
					username = rec2.get("username")
					rec = rec2
			except Exception:
				pass
		groups = [g.lower() for g in (rec or {}).get("groups", [])] if rec else []
		role = "guest"
		for candidate in ("owner", "admin", "power", "user", "guest"):
			if candidate in groups:
				role = candidate
				break
		cfg = {}
		try:
			import os

			path = getattr(access_control, "groups_config_file", None)
			if path and os.path.exists(path):
				with open(path, "r", encoding="utf-8") as f:
					cfg = json.load(f) or {}
		except Exception:
			cfg = {}
		perms = cfg.get(role, {}) if isinstance(cfg, dict) else {}
		return user_id, username, role, perms if isinstance(perms, dict) else {}
	except Exception:
		return None, None, "guest", {}


def _run_on_server_loop(coro, timeout: float = 60.0):
	"""Schedule coroutine on PromptServer loop from a worker thread."""
	from server import PromptServer

	loop = PromptServer.instance.loop
	fut = asyncio.run_coroutine_threadsafe(coro, loop)
	return fut.result(timeout=timeout)


def _wait_jobs(
	job_ids: list[str], user_id: str, *, progress_mode: str = "per_model", poll_seconds: float = 0.5
) -> list[dict]:
	from ..routes import model_download as md

	finished: dict[str, dict] = {}
	pb_holder: list = []
	while len(finished) < len(job_ids):
		snapshots = []
		for jid in job_ids:
			snap = md.get_job_public_snapshot(jid, user_id=user_id)
			if not snap:
				# Race: job just created
				snap = {"job_id": jid, "status": "queued", "progress_pct": 0}
			snapshots.append(snap)
			status = (snap.get("status") or "").lower()
			if status in ("completed", "failed", "cancelled"):
				finished[jid] = snap
		active = None
		for snap in snapshots:
			if (snap.get("status") or "").lower() == "running":
				active = snap.get("job_id")
				break
		value, maximum, label = aggregate_progress(
			snapshots, mode=progress_mode, active_job_id=active
		)
		_emit_progress(value, maximum, label, pb_holder=pb_holder)
		if len(finished) >= len(job_ids):
			break
		time.sleep(poll_seconds)
	return [finished[jid] for jid in job_ids if jid in finished]


class MSSModelDownload:
	"""Queue a CivitAI/HF download and wait until complete; emit Comfy progress."""

	@classmethod
	def INPUT_TYPES(cls):
		return {
			"required": {
				"source": (["civitai", "huggingface"], {"default": "civitai"}),
				"folder_type": (
					[
						"checkpoints",
						"loras",
						"vae",
						"unet",
						"diffusion_models",
						"text_encoders",
						"clip_vision",
						"controlnet",
						"upscale_models",
						"embeddings",
					],
					{"default": "checkpoints"},
				),
				"progress_mode": (["per_model", "overall"], {"default": "per_model"}),
			},
			"optional": {
				"url": ("STRING", {"default": "", "multiline": False}),
				"model_version_id": ("STRING", {"default": ""}),
				"repo_id": ("STRING", {"default": ""}),
				"filename": ("STRING", {"default": ""}),
				"subfolder": ("STRING", {"default": ""}),
				"save_as": ("STRING", {"default": ""}),
				"api_key": ("STRING", {"default": "", "multiline": False}),
				"isolate": (
					"BOOLEAN",
					{
						"default": True,
						"label_on": "Isolated (Private)",
						"label_off": "Shared (Global)",
					},
				),
			},
		}

	RETURN_TYPES = ("STRING",)
	RETURN_NAMES = ("model_name",)
	FUNCTION = "download"
	CATEGORY = "MSS-Login/Models"
	OUTPUT_NODE = True

	def download(
		self,
		source,
		folder_type,
		progress_mode="per_model",
		url="",
		model_version_id="",
		repo_id="",
		filename="",
		subfolder="",
		save_as="",
		api_key="",
		isolate=True,
	):
		user_id, username, role, perms = _resolve_prompt_user()
		if not user_id:
			raise RuntimeError("DOWNLOAD_NOT_ALLOWED: Not authenticated for model download")
		if not user_can_download_models(role, perms):
			raise RuntimeError(f"{DOWNLOAD_NOT_ALLOWED}: {DOWNLOAD_NOT_ALLOWED_MESSAGE}")

		isolate_val = (
			isolate
			if isinstance(isolate, bool)
			else (str(isolate).strip().lower() not in ("false", "0", "off", "no"))
		)
		body = {
			"source": source,
			"folder_type": folder_type,
			"destination_type": "local",
			"isolate": isolate_val,
		}
		if (url or "").strip():
			body["url"] = url.strip()
		if (model_version_id or "").strip():
			body["model_version_id"] = model_version_id.strip()
		if (repo_id or "").strip():
			body["repo_id"] = repo_id.strip()
		if (filename or "").strip():
			body["filename"] = filename.strip()
		if (subfolder or "").strip():
			body["subfolder"] = subfolder.strip()
		if (save_as or "").strip():
			body["save_as"] = save_as.strip()
		if (api_key or "").strip():
			body["api_key"] = api_key.strip()

		from ..routes.model_download import enqueue_download_job

		async def _enqueue():
			return await enqueue_download_job(user_id, username, role, perms, body)

		job_id, err = _run_on_server_loop(_enqueue())
		if err:
			code = err.get("error") or "DOWNLOAD_FAILED"
			msg = err.get("message") or json.dumps(err)
			raise RuntimeError(f"{code}: {msg}")

		results = _wait_jobs([job_id], user_id, progress_mode=progress_mode)
		if not results:
			raise RuntimeError("Download job disappeared before completion")
		job = results[0]
		status = (job.get("status") or "").lower()
		if status != "completed":
			code = job.get("error_code") or "DOWNLOAD_FAILED"
			msg = job.get("error") or status
			raise RuntimeError(f"{code}: {msg}")
		name = job.get("filename") or job.get("saved_relpath") or job_id
		name = _plain_model_name(str(name))
		_emit_progress(100, 100, f"Completed {name}")
		return (name,)


class MSSDownloadStatus:
	"""Wait on an existing download job_id and emit progress."""

	@classmethod
	def INPUT_TYPES(cls):
		return {
			"required": {
				"job_id": ("STRING", {"default": ""}),
				"progress_mode": (["per_model", "overall"], {"default": "per_model"}),
			}
		}

	RETURN_TYPES = ("STRING", "STRING")
	RETURN_NAMES = ("status", "detail_json")
	FUNCTION = "status"
	CATEGORY = "MSS-Login/Models"

	def status(self, job_id, progress_mode="per_model"):
		user_id, _username, role, perms = _resolve_prompt_user()
		if not user_id or not user_can_download_models(role, perms):
			raise RuntimeError(f"{DOWNLOAD_NOT_ALLOWED}: {DOWNLOAD_NOT_ALLOWED_MESSAGE}")
		jid = (job_id or "").strip()
		if not jid:
			raise RuntimeError("INVALID_REQUEST: job_id required")
		results = _wait_jobs([jid], user_id, progress_mode=progress_mode)
		job = results[0] if results else {"status": "not_found", "job_id": jid}
		return (str(job.get("status") or ""), json.dumps(job))


NODE_CLASS_MAPPINGS = {"MSSModelDownload": MSSModelDownload, "MSSDownloadStatus": MSSDownloadStatus}

NODE_DISPLAY_NAME_MAPPINGS = {
	"MSSModelDownload": "MSS Model Download",
	"MSSDownloadStatus": "MSS Download Status",
}
