"""
Owner-configurable login page background (experimental).

Media applies only when experimental.login_background is on and
login_background.enabled is true. Local files must resolve under DATA_DIR.
"""

from __future__ import annotations

import mimetypes
import os
from urllib.parse import urlparse

from ..constants import CONFIG_FILE_PATH, DATA_DIR, experimental_login_background_enabled
from .json_utils import load_json_file, save_json_file
from .path_safety import path_under, resolve_path_under

LOGIN_BG_SUBDIR = "login-background"
ALLOWED_IMAGE_EXTS = frozenset({".jpg", ".jpeg", ".png", ".webp", ".gif"})
ALLOWED_VIDEO_EXTS = frozenset({".mp4", ".webm", ".ogg"})
ALLOWED_MEDIA_EXTS = ALLOWED_IMAGE_EXTS | ALLOWED_VIDEO_EXTS

DEFAULT_LOGIN_BACKGROUND = {
	"enabled": False,
	"source": "url",
	"url": "",
	"local_path": "",
	"media_kind": "auto",
}

PUBLIC_MEDIA_PATH = "/mss-login/api/login-background/media"


def _load_config() -> dict:
	cfg = load_json_file(CONFIG_FILE_PATH, {})
	return cfg if isinstance(cfg, dict) else {}


def _save_config(cfg: dict) -> None:
	save_json_file(CONFIG_FILE_PATH, cfg)


def get_login_background_settings() -> dict:
	"""Return normalized login_background settings from config (not gated)."""
	cfg = _load_config()
	raw = cfg.get("login_background")
	if not isinstance(raw, dict):
		raw = {}
	source = str(raw.get("source") or "url").strip().lower()
	if source not in ("url", "local"):
		source = "url"
	kind = str(raw.get("media_kind") or "auto").strip().lower()
	if kind not in ("auto", "image", "video"):
		kind = "auto"
	return {
		"enabled": bool(raw.get("enabled", False)),
		"source": source,
		"url": str(raw.get("url") or "").strip(),
		"local_path": str(raw.get("local_path") or "").strip(),
		"media_kind": kind,
	}


def save_login_background_settings(data: dict) -> dict:
	"""Validate and persist login_background settings. Raises ValueError on bad input."""
	if not isinstance(data, dict):
		raise ValueError("Invalid body")
	current = get_login_background_settings()
	enabled = bool(data["enabled"]) if "enabled" in data else current["enabled"]
	source = str(data.get("source", current["source"]) or "url").strip().lower()
	if source not in ("url", "local"):
		raise ValueError("source must be 'url' or 'local'")
	url = str(data.get("url", current["url"]) or "").strip()
	local_path = str(data.get("local_path", current["local_path"]) or "").strip()
	kind = str(data.get("media_kind", current["media_kind"]) or "auto").strip().lower()
	if kind not in ("auto", "image", "video"):
		raise ValueError("media_kind must be 'auto', 'image', or 'video'")

	if url:
		parsed = urlparse(url)
		if parsed.scheme not in ("http", "https") or not parsed.netloc:
			raise ValueError("url must be an absolute http(s) URL")

	if local_path:
		resolved = resolve_login_background_local_path(local_path)
		if resolved is None:
			raise ValueError(
				"local_path must resolve to a file under the MSS-Login data directory "
				f"({LOGIN_BG_SUBDIR}/ recommended)"
			)
		ext = os.path.splitext(resolved)[1].lower()
		if ext not in ALLOWED_MEDIA_EXTS:
			raise ValueError(
				"local_path extension not allowed; use " + ", ".join(sorted(ALLOWED_MEDIA_EXTS))
			)

	out = {
		"enabled": enabled,
		"source": source,
		"url": url,
		"local_path": local_path,
		"media_kind": kind,
	}
	cfg = _load_config()
	cfg["login_background"] = out
	_save_config(cfg)
	return out


def resolve_login_background_local_path(local_path: str) -> str | None:
	"""Resolve a local_path to a real file under DATA_DIR, or None."""
	if not local_path or not isinstance(local_path, str):
		return None
	raw = local_path.strip()
	if not raw:
		return None
	# Absolute path: must stay under DATA_DIR.
	if os.path.isabs(raw) or (len(raw) >= 2 and raw[1] == ":"):
		try:
			resolved = os.path.realpath(raw)
		except OSError:
			return None
		if not path_under(resolved, DATA_DIR):
			return None
		if not os.path.isfile(resolved):
			return None
		return resolved
	# Relative to DATA_DIR (and reject .. via resolve_path_under).
	resolved = resolve_path_under(DATA_DIR, raw)
	if resolved is None or not os.path.isfile(resolved):
		return None
	return resolved


def detect_media_kind(path_or_url: str, explicit: str = "auto") -> str:
	"""Return 'image' or 'video'."""
	if explicit in ("image", "video"):
		return explicit
	ext = os.path.splitext(urlparse(path_or_url).path)[1].lower()
	if ext in ALLOWED_VIDEO_EXTS:
		return "video"
	return "image"


def is_login_background_active() -> bool:
	"""True when experimental flag and appearance toggle are both on."""
	if not experimental_login_background_enabled():
		return False
	return bool(get_login_background_settings().get("enabled"))


def get_public_login_background() -> dict:
	"""
	Public payload for the login page.
	{ enabled, media_kind, src } — src empty when inactive.
	"""
	inactive = {"enabled": False, "media_kind": "image", "src": ""}
	if not experimental_login_background_enabled():
		return inactive
	settings = get_login_background_settings()
	if not settings.get("enabled"):
		return inactive

	source = settings.get("source") or "url"
	if source == "local":
		resolved = resolve_login_background_local_path(settings.get("local_path") or "")
		if not resolved:
			return inactive
		kind = detect_media_kind(resolved, settings.get("media_kind") or "auto")
		return {"enabled": True, "media_kind": kind, "src": PUBLIC_MEDIA_PATH}

	url = (settings.get("url") or "").strip()
	if not url:
		return inactive
	parsed = urlparse(url)
	if parsed.scheme not in ("http", "https") or not parsed.netloc:
		return inactive
	kind = detect_media_kind(url, settings.get("media_kind") or "auto")
	return {"enabled": True, "media_kind": kind, "src": url}


def get_local_media_file() -> tuple[str | None, str | None]:
	"""
	Return (path, content_type) for the configured local media file when active.
	"""
	if not is_login_background_active():
		return None, None
	settings = get_login_background_settings()
	if (settings.get("source") or "url") != "local":
		return None, None
	resolved = resolve_login_background_local_path(settings.get("local_path") or "")
	if not resolved:
		return None, None
	ext = os.path.splitext(resolved)[1].lower()
	if ext not in ALLOWED_MEDIA_EXTS:
		return None, None
	ctype, _ = mimetypes.guess_type(resolved)
	if not ctype:
		ctype = "application/octet-stream"
	return resolved, ctype


def csp_extra_origins_for_login() -> tuple[str | None, str | None]:
	"""
	Return (img_origin, media_origin) to append to CSP when an external URL is active.
	Either may be None.
	"""
	public = get_public_login_background()
	if not public.get("enabled"):
		return None, None
	src = public.get("src") or ""
	if not src.startswith("http://") and not src.startswith("https://"):
		return None, None
	parsed = urlparse(src)
	origin = f"{parsed.scheme}://{parsed.netloc}"
	kind = public.get("media_kind") or "image"
	if kind == "video":
		return None, origin
	return origin, None


def ensure_login_background_dir() -> str:
	"""Ensure DATA_DIR/login-background exists; return its path."""
	path = os.path.join(DATA_DIR, LOGIN_BG_SUBDIR)
	os.makedirs(path, exist_ok=True)
	return path
