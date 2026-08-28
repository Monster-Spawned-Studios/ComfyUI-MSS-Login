# --- START OF FILE utils/avatar.py ---
"""Per-user profile avatars stored under the MSS-Login data directory.

Uploads are re-encoded with Pillow so the stored file is a sanitized PNG
(no SVG/HTML, no exotic codecs, no path-traversal filenames).
"""

from __future__ import annotations

import io
import os
from typing import Optional

from PIL import Image, ImageFile, UnidentifiedImageError

from .data_dir import get_data_subdir
from .path_safety import is_safe_folder_segment
from .user_env import _sanitize_username_for_path

# Decompression-bomb ceiling (Pillow default is ~178 million pixels).
Image.MAX_IMAGE_PIXELS = 8_000_000
ImageFile.LOAD_TRUNCATED_IMAGES = False

AVATAR_FILENAME = "avatar.png"
MAX_UPLOAD_BYTES = 2 * 1024 * 1024
MAX_EDGE_PX = 512
ALLOWED_MODES = ("RGB", "RGBA", "L", "P", "LA")
ALLOWED_FORMATS = frozenset({"JPEG", "PNG", "WEBP", "GIF"})


def avatar_dir(username: str) -> str:
	safe = _sanitize_username_for_path(username)
	if not is_safe_folder_segment(safe) or safe.lower() == "guest":
		raise ValueError("Invalid username for avatar storage")
	path = get_data_subdir("Users", safe)
	os.makedirs(path, exist_ok=True)
	return path


def avatar_path(username: str) -> str:
	return os.path.join(avatar_dir(username), AVATAR_FILENAME)


def has_avatar(username: str) -> bool:
	try:
		path = avatar_path(username)
	except ValueError:
		return False
	return os.path.isfile(path)


def delete_avatar(username: str) -> bool:
	try:
		path = avatar_path(username)
	except ValueError:
		return False
	if os.path.isfile(path):
		os.remove(path)
		return True
	return False


def _looks_like_svg_or_html(data: bytes) -> bool:
	head = data[:512].lstrip().lower()
	return (
		head.startswith(b"<svg")
		or head.startswith(b"<?xml")
		or b"<svg" in head
		or head.startswith(b"<html")
		or head.startswith(b"<!doctype")
		or b"<script" in head
	)


def process_avatar_bytes(data: bytes) -> tuple[Optional[bytes], Optional[str]]:
	"""Validate and re-encode an upload. Returns (png_bytes, error_message)."""
	if not data or not isinstance(data, bytes):
		return None, "No image data"
	if len(data) > MAX_UPLOAD_BYTES:
		return None, f"Image is too large (max {MAX_UPLOAD_BYTES // (1024 * 1024)} MB)"
	if _looks_like_svg_or_html(data):
		return None, "SVG and HTML images are not allowed"
	try:
		with Image.open(io.BytesIO(data)) as img:
			img.verify()
	except (UnidentifiedImageError, OSError, ValueError, Image.DecompressionBombError):
		return None, "File is not a valid image"
	try:
		with Image.open(io.BytesIO(data)) as img:
			fmt = (img.format or "").upper()
			if fmt not in ALLOWED_FORMATS:
				return None, "Unsupported image type"
			if img.mode not in ALLOWED_MODES:
				img = img.convert("RGBA")
			elif img.mode in ("P", "LA", "L"):
				img = img.convert("RGBA")
			elif img.mode != "RGBA":
				img = img.convert("RGB")
			# Flatten animated GIF/WebP to the first frame
			img.seek(0)
			w, h = img.size
			if w < 1 or h < 1:
				return None, "Invalid image dimensions"
			if max(w, h) > MAX_EDGE_PX:
				img.thumbnail((MAX_EDGE_PX, MAX_EDGE_PX), Image.Resampling.LANCZOS)
			if img.mode == "RGBA":
				background = Image.new("RGB", img.size, (20, 22, 28))
				background.paste(img, mask=img.split()[-1])
				img = background
			out = io.BytesIO()
			img.save(out, format="PNG", optimize=True)
			png = out.getvalue()
	except (UnidentifiedImageError, OSError, ValueError, Image.DecompressionBombError):
		return None, "Could not process image"
	if not png or len(png) > MAX_UPLOAD_BYTES:
		return None, "Processed image is too large"
	return png, None


def save_avatar(username: str, data: bytes) -> tuple[bool, str]:
	png, err = process_avatar_bytes(data)
	if err or not png:
		return False, err or "Invalid image"
	path = avatar_path(username)
	os.makedirs(os.path.dirname(path), exist_ok=True)
	tmp = path + ".tmp"
	with open(tmp, "wb") as handle:
		handle.write(png)
	os.replace(tmp, path)
	return True, "Avatar updated"


# --- END OF FILE utils/avatar.py ---
