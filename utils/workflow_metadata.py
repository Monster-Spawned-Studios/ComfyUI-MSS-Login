"""
UI workflow metadata for Portal/API outputs (extra_pnginfo.workflow).

CPE convert returns the API prompt in data.workflow (CPE contract). Portal clients
often POST only that API prompt, so PNGs lack a workflow tEXt chunk. This module:

1. Builds additive CPE fields (ui_workflow + extra_data) when a UI graph was converted.
2. Remembers a per-user, fingerprint-keyed TTL cache of UI graphs after convert.
3. On POST /prompt, fills extra_data.extra_pnginfo.workflow from that cache when
   the client omitted it (gated by cpe.embed_ui_workflow).

Caveat: the embedded UI graph reflects *saved* widget values from convert time;
the prompt chunk remains the source of truth for what actually ran. Do not inject
fixed/template graphs.
"""

from __future__ import annotations

import copy
import json
import logging
import os
import re
import time
from collections import OrderedDict
from typing import Any, Callable

_log = logging.getLogger("mss_login.workflow_metadata")

DEFAULT_TTL_SECONDS = 30 * 60
DEFAULT_MAX_PER_USER = 32
DEFAULT_MAX_BYTES = 2 * 1024 * 1024  # 2 MiB

_UUID_RE = re.compile(
	r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$", re.IGNORECASE
)

_settings_override: dict[str, Any] | None = None
_global_cache: UiWorkflowCache | None = None
_oversize_logged = False


def set_cpe_embed_settings_override(settings: dict[str, Any] | None) -> None:
	"""Test helper: override config readers (None clears)."""
	global _settings_override
	_settings_override = dict(settings) if settings is not None else None


def _load_config_dict() -> dict[str, Any]:
	try:
		from .data_dir import get_data_dir

		path = os.path.join(get_data_dir(), "config.json")
		if os.path.isfile(path):
			with open(path, encoding="utf-8") as handle:
				data = json.load(handle)
				if isinstance(data, dict):
					return data
	except Exception:
		pass
	try:
		defaults_path = os.path.join(
			os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "config.defaults.json"
		)
		with open(defaults_path, encoding="utf-8") as handle:
			data = json.load(handle)
			if isinstance(data, dict):
				return data
	except Exception:
		pass
	return {}


def cpe_embed_settings() -> dict[str, Any]:
	"""Return embed_ui_workflow (default True) and ui_workflow_max_bytes (default 2 MiB)."""
	if _settings_override is not None:
		base = dict(_settings_override)
	else:
		cfg = _load_config_dict()
		base = dict(cfg.get("cpe") or {}) if isinstance(cfg.get("cpe"), dict) else {}
	max_bytes = base.get("ui_workflow_max_bytes", DEFAULT_MAX_BYTES)
	try:
		max_bytes_i = int(max_bytes)
	except (TypeError, ValueError):
		max_bytes_i = DEFAULT_MAX_BYTES
	if max_bytes_i <= 0:
		max_bytes_i = DEFAULT_MAX_BYTES
	return {
		"embed_ui_workflow": bool(base.get("embed_ui_workflow", True)),
		"ui_workflow_max_bytes": max_bytes_i,
	}


def structure_fingerprint(api_prompt: dict[str, Any] | None) -> str:
	"""Fingerprint from sorted (node_id, class_type) only — ignore widget values."""
	if not isinstance(api_prompt, dict):
		return ""
	pairs: list[tuple[str, str]] = []
	for node_id, node in api_prompt.items():
		if not isinstance(node, dict):
			continue
		class_type = node.get("class_type")
		if not isinstance(class_type, str) or not class_type:
			continue
		pairs.append((str(node_id), class_type))
	pairs.sort()
	if not pairs:
		return ""
	return json.dumps(pairs, separators=(",", ":"), ensure_ascii=False)


def resolve_canonical_user_id(
	request: Any = None, *, user_id: str | None = None, username: str | None = None
) -> str | None:
	"""
	Resolve canonical user UUID from request, user_id, or username.
	Returns normalized user_id string or None.
	"""
	if user_id:
		uid = str(user_id).strip()
		if uid:
			return uid

	# Direct request attribute / dict checks
	if request is not None:
		try:
			req_uid = (
				request.get("user_id")
				if hasattr(request, "get")
				else getattr(request, "user_id", None)
			)
			if req_uid:
				uid = str(req_uid).strip()
				if uid:
					return uid
		except Exception:
			pass

		# Try jwt_auth request identity resolution
		try:
			from ..globals import jwt_auth

			r_uid, r_uname = jwt_auth.resolve_request_identity(request, touch_idle=False)
			if r_uid:
				return str(r_uid).strip()
			if r_uname and not username:
				username = r_uname
		except Exception:
			pass

		# Try access_control get user role and permissions
		try:
			from ..globals import access_control

			_, _, perm_user = access_control._get_user_role_and_permissions(request)
			if perm_user:
				from .model_visibility_policy import get_user_id_for_username

				db_uid = get_user_id_for_username(access_control.users_db, perm_user)
				if db_uid:
					return str(db_uid).strip()
		except Exception:
			pass

		# Try request.get("user")
		try:
			user_val = request.get("user") if hasattr(request, "get") else None
			if isinstance(user_val, dict):
				if user_val.get("id"):
					return str(user_val["id"]).strip()
				if user_val.get("username") and not username:
					username = user_val.get("username")
			elif isinstance(user_val, str) and not username:
				username = user_val
		except Exception:
			pass

		# Try access_control contextvar
		try:
			from ..globals import access_control

			cur_uid = access_control.get_current_user_id()
			if cur_uid:
				return str(cur_uid).strip()
		except Exception:
			pass

	if username:
		try:
			from ..globals import access_control
			from .model_visibility_policy import get_user_id_for_username

			db_uid = get_user_id_for_username(access_control.users_db, username)
			if db_uid:
				return str(db_uid).strip()
		except Exception:
			pass

	return None


def build_workflow_cache_key(
	user_id_or_request: Any, api_prompt: dict[str, Any] | None
) -> str | None:
	"""
	One shared function to build the workflow cache key from canonical user ID
	and prompt structure fingerprint. Both convert route and /prompt hook call this.
	Format: '{canonical_user_id}:{fingerprint}'
	"""
	if isinstance(user_id_or_request, str):
		uid = user_id_or_request.strip()
	else:
		uid = resolve_canonical_user_id(user_id_or_request)
	if not uid:
		return None
	fp = structure_fingerprint(api_prompt)
	if not fp:
		return None
	return f"{uid}:{fp}"


class UiWorkflowCache:
	"""Per-user LRU cache of UI workflows keyed by API structure fingerprint."""

	def __init__(
		self,
		*,
		ttl_seconds: float = DEFAULT_TTL_SECONDS,
		max_per_user: int = DEFAULT_MAX_PER_USER,
		clock: Callable[[], float] | None = None,
	) -> None:
		self.ttl_seconds = float(ttl_seconds)
		self.max_per_user = int(max_per_user)
		self._clock = clock or time.monotonic
		# user_id -> OrderedDict[fingerprint -> (expires_at, workflow)]
		self._store: dict[str, OrderedDict[str, tuple[float, dict[str, Any]]]] = {}

	def clear(self) -> None:
		self._store.clear()

	def put(
		self,
		user_id: str | None = None,
		fingerprint: str | None = None,
		workflow: dict[str, Any] | None = None,
		*,
		key: str | None = None,
	) -> None:
		if key is not None:
			if ":" in key:
				uid, fp = key.split(":", 1)
			else:
				uid, fp = key, fingerprint or ""
		elif user_id and ":" in user_id and not fingerprint:
			uid, fp = user_id.split(":", 1)
		else:
			uid = (user_id or "").strip()
			fp = (fingerprint or "").strip()
		if not uid or not fp or not isinstance(workflow, dict):
			return
		bucket = self._store.setdefault(uid, OrderedDict())
		now = self._clock()
		bucket[fp] = (now + self.ttl_seconds, copy.deepcopy(workflow))
		bucket.move_to_end(fp)
		while len(bucket) > self.max_per_user:
			bucket.popitem(last=False)

	def get(
		self, user_id: str | None = None, fingerprint: str | None = None, *, key: str | None = None
	) -> dict[str, Any] | None:
		if key is not None:
			if ":" in key:
				uid, fp = key.split(":", 1)
			else:
				uid, fp = key, fingerprint or ""
		elif user_id and ":" in user_id and not fingerprint:
			uid, fp = user_id.split(":", 1)
		else:
			uid = (user_id or "").strip()
			fp = (fingerprint or "").strip()
		if not uid or not fp:
			return None
		bucket = self._store.get(uid)
		if not bucket or fp not in bucket:
			return None
		expires_at, workflow = bucket[fp]
		if self._clock() >= expires_at:
			del bucket[fp]
			if not bucket:
				self._store.pop(uid, None)
			return None
		bucket.move_to_end(fp)
		return copy.deepcopy(workflow)


def get_ui_workflow_cache() -> UiWorkflowCache:
	global _global_cache
	if _global_cache is None:
		_global_cache = UiWorkflowCache()
	return _global_cache


def set_ui_workflow_cache_for_tests(cache: UiWorkflowCache | None) -> None:
	"""Replace or clear the process-global cache (tests only)."""
	global _global_cache
	_global_cache = cache


def looks_like_ui_workflow(obj: Any) -> bool:
	"""True for classic UI graphs (nodes+links) or a non-empty nodes list schema."""
	if not isinstance(obj, dict):
		return False
	if "nodes" in obj and "links" in obj:
		return True
	nodes = obj.get("nodes")
	return isinstance(nodes, list) and len(nodes) > 0 and "class_type" not in obj


def _serialized_size(obj: Any) -> int:
	try:
		return len(json.dumps(obj, ensure_ascii=False, separators=(",", ":")).encode("utf-8"))
	except (TypeError, ValueError):
		return DEFAULT_MAX_BYTES + 1


def _absolute_path_roots() -> list[str]:
	roots: list[str] = ["/root/", "/root"]
	try:
		from ..constants import DATA_DIR

		if DATA_DIR:
			roots.append(os.path.normpath(str(DATA_DIR)))
	except Exception:
		pass
	try:
		from .model_isolation import isolation_models_base

		base = isolation_models_base()
		if base:
			roots.append(os.path.normpath(str(base)))
	except Exception:
		pass
	return roots


def _is_absolute_server_path(value: str, roots: list[str]) -> bool:
	v = (value or "").strip()
	if not v:
		return False
	norm = v.replace("\\", "/")
	if norm.startswith("/root/") or norm == "/root":
		return True
	# Unix absolute or Windows drive path that looks like a filesystem path
	if norm.startswith("/") and (
		"/" in norm[1:] or norm.startswith("/mnt/") or "mss" in norm.lower()
	):
		for root in roots:
			r = root.replace("\\", "/").rstrip("/")
			if r and (norm == r or norm.startswith(r + "/")):
				return True
		# Generic absolute path with model-like suffix or isolation segment
		lower = norm.lower()
		if "model_isolation" in lower or lower.endswith(
			(".safetensors", ".ckpt", ".pt", ".pth", ".bin", ".onnx")
		):
			return True
	if len(norm) >= 3 and norm[1] == ":" and norm[2] in "/\\":
		return True
	return False


def _sanitize_user_segment(user_id: str | None) -> str:
	raw = (user_id or "").strip()
	if not raw:
		return "public"
	safe = "".join(c if c.isalnum() or c in ("_", "-") else "_" for c in raw)
	return safe or "public"


def _is_uuid_segment(segment: str) -> bool:
	return bool(segment and _UUID_RE.match(segment.strip()))


def _strip_display_name(value: str, viewer_user_id: str | None) -> str:
	"""
	Strip owner UUID prefix from save filename prefixes and model paths for embedded copy.
	Preserves actual save location while keeping embedded copy clean.
	"""
	name = (value or "").replace("\\", "/").strip()
	if not name:
		return value

	# Strip viewer's own UUID prefix first
	viewer = _sanitize_user_segment(viewer_user_id)
	if viewer and viewer != "public":
		prefix = f"{viewer}/"
		if name.startswith(prefix) or name.lower().startswith(prefix.lower()):
			name = name[len(prefix) :]

	# If leading segment is any UUID segment, strip it
	parts = name.split("/")
	if len(parts) >= 2 and _is_uuid_segment(parts[0]):
		name = "/".join(parts[1:])

	try:
		from .model_path_display import to_display_name

		return to_display_name(name, viewer_user_id)
	except Exception:
		pass
	return name


def _string_has_foreign_uuid(value: str, viewer_user_id: str | None) -> bool:
	try:
		from .isolation_resolver import foreign_uuid_prefix

		return foreign_uuid_prefix(value, viewer_user_id)
	except Exception:
		pass
	path = (value or "").replace("\\", "/").strip()
	if "/" not in path:
		return False
	seg, _rest = path.split("/", 1)
	if not _is_uuid_segment(seg):
		return False
	viewer = _sanitize_user_segment(viewer_user_id)
	return seg.lower() != viewer.lower()


def sanitize_ui_workflow(
	workflow: Any, viewer_user_id: str | None, *, max_bytes: int | None = None
) -> dict[str, Any] | None:
	"""
	Return a sanitized deep copy of a UI workflow, or None to drop embedding.

	- Rejects oversize graphs (log once at INFO; caller still queues the prompt).
	- Rejects non-UI shapes.
	- Drops on foreign UUID prefixes or absolute server paths.
	- Strips the viewer's own uid/ prefix from string values via to_display_name.
	Never logs workflow contents, tokens, or JWTs.
	"""
	global _oversize_logged
	if not looks_like_ui_workflow(workflow):
		return None
	cap = DEFAULT_MAX_BYTES if max_bytes is None else int(max_bytes)
	if _serialized_size(workflow) > cap:
		if not _oversize_logged:
			_log.info("UI workflow exceeds size cap (%s bytes); omitting workflow embed", cap)
			_oversize_logged = True
		return None

	cloned = copy.deepcopy(workflow)
	roots = _absolute_path_roots()
	drop = False

	def walk(obj: Any) -> Any:
		nonlocal drop
		if drop:
			return obj
		if isinstance(obj, str):
			if _is_absolute_server_path(obj, roots):
				drop = True
				return obj
			if _string_has_foreign_uuid(obj, viewer_user_id):
				drop = True
				return obj
			return _strip_display_name(obj, viewer_user_id)
		if isinstance(obj, list):
			return [walk(x) for x in obj]
		if isinstance(obj, dict):
			return {k: walk(v) for k, v in obj.items()}
		return obj

	result = walk(cloned)
	if drop:
		_log.info("UI workflow embed dropped (foreign uid prefix or absolute path)")
		return None
	if not isinstance(result, dict):
		return None
	return result


def client_supplied_workflow(extra_data: Any) -> Any | None:
	if not isinstance(extra_data, dict):
		return None
	png = extra_data.get("extra_pnginfo")
	if not isinstance(png, dict):
		return None
	wf = png.get("workflow")
	return wf if wf is not None else None


def ensure_extra_pnginfo_workflow(
	extra_data: dict[str, Any] | None, workflow: dict[str, Any]
) -> dict[str, Any]:
	"""Set extra_pnginfo.workflow without overwriting an existing client workflow."""
	base: dict[str, Any] = dict(extra_data) if isinstance(extra_data, dict) else {}
	png = base.get("extra_pnginfo")
	if not isinstance(png, dict):
		png = {}
	else:
		png = dict(png)
	if "workflow" not in png or png.get("workflow") is None:
		png["workflow"] = workflow
	base["extra_pnginfo"] = png
	return base


def remember_converted_ui_workflow(
	user_id: str | None,
	api_prompt: dict[str, Any],
	ui_workflow: dict[str, Any] | None,
	*,
	cache: UiWorkflowCache | None = None,
	max_bytes: int | None = None,
) -> dict[str, Any] | None:
	"""
	Sanitize and cache a UI workflow for fill-in. Returns sanitized graph or None.
	"""
	settings = cpe_embed_settings()
	if not settings["embed_ui_workflow"]:
		return None
	cap = settings["ui_workflow_max_bytes"] if max_bytes is None else max_bytes
	cleaned = sanitize_ui_workflow(ui_workflow, user_id, max_bytes=cap)
	if cleaned is None:
		return None
	fp = structure_fingerprint(api_prompt)
	if not fp:
		return cleaned
	key = build_workflow_cache_key(user_id, api_prompt)
	if not key:
		return cleaned
	uid, fp = key.split(":", 1)
	(cache or get_ui_workflow_cache()).put(uid, fp, cleaned)
	return cleaned


def apply_prompt_extra_data(
	data: Any,
	prompt: dict[str, Any],
	*,
	user_id: str | None,
	cache: UiWorkflowCache | None = None,
	embed_enabled: bool | None = None,
	max_bytes: int | None = None,
) -> Any:
	"""
	Preserve sibling keys on a wrapped prompt body; sanitize or fill-in workflow.

	- Wrapped ``{prompt, client_id, extra_data, ...}``: only ``prompt`` should already
	  be normalized by the caller; this mutates ``extra_data`` in place on a copy.
	- Bare graph (body is the prompt): wrap into ``{prompt, extra_data}`` when a
	  workflow is filled in; otherwise return the prompt dict unchanged.
	Never overwrites a client-supplied workflow. Never injects a template.
	"""
	settings = cpe_embed_settings()
	enabled = settings["embed_ui_workflow"] if embed_enabled is None else bool(embed_enabled)
	cap = settings["ui_workflow_max_bytes"] if max_bytes is None else int(max_bytes)
	store = cache if cache is not None else get_ui_workflow_cache()

	wrapped = isinstance(data, dict) and "prompt" in data and isinstance(data.get("prompt"), dict)
	if wrapped:
		out = dict(data)
		out["prompt"] = prompt
		extra = out.get("extra_data")
		extra_dict = dict(extra) if isinstance(extra, dict) else {}
	elif isinstance(data, dict) and data is prompt:
		# Bare graph body
		out = None  # may wrap later
		extra_dict = {}
	elif isinstance(data, dict):
		out = dict(data)
		out["prompt"] = prompt
		extra = out.get("extra_data")
		extra_dict = dict(extra) if isinstance(extra, dict) else {}
		wrapped = True
	else:
		return data

	existing = client_supplied_workflow(extra_dict)
	if existing is not None:
		cleaned = sanitize_ui_workflow(existing, user_id, max_bytes=cap)
		png = extra_dict.get("extra_pnginfo")
		png_dict = dict(png) if isinstance(png, dict) else {}
		if cleaned is None:
			png_dict.pop("workflow", None)
		else:
			png_dict["workflow"] = cleaned
		if png_dict:
			extra_dict["extra_pnginfo"] = png_dict
		else:
			extra_dict.pop("extra_pnginfo", None)
	elif enabled:
		key = build_workflow_cache_key(user_id, prompt)
		cached = None
		if key:
			uid, fp = key.split(":", 1)
			cached = store.get(uid, fp)
		if cached is not None:
			cleaned = sanitize_ui_workflow(cached, user_id, max_bytes=cap)
			if cleaned is not None:
				extra_dict = ensure_extra_pnginfo_workflow(extra_dict, cleaned)

	if not wrapped and out is None:
		# Bare graph
		if extra_dict:
			return {"prompt": prompt, "extra_data": extra_dict}
		return prompt

	assert isinstance(out, dict)
	if extra_dict:
		out["extra_data"] = extra_dict
	elif "extra_data" in out:
		out.pop("extra_data", None)
	return out
