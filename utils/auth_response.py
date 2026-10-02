# --- START OF FILE utils/auth_response.py ---
"""Unauthorized-response policy for JWT middleware (no ComfyUI imports)."""

from __future__ import annotations


def should_return_json_401(path: str, accept: str = "", sec_fetch_mode: str = "") -> bool:
	"""True when unauthorized clients should get JSON 401 instead of HTML redirect.

	Rules (priority order), shared with ``JWTAuth`` middleware:
	1. ``/api/*``, ``/cpe/*``, ``/ws`` → always JSON (Comfy Portal / programmatic clients).
	2. ``Sec-Fetch-Mode: navigate`` → HTML redirect.
	3. Accept wants HTML and not JSON → HTML redirect.
	4. Otherwise → JSON 401.
	"""
	path = path or ""
	if (
		path.startswith("/api/")
		or path.startswith("/cpe/")
		or path in ("/ws",)
		or path.startswith("/ws/")
	):
		return True
	if (sec_fetch_mode or "").lower() == "navigate":
		return False
	accept_header = (accept or "").strip().lower()
	if "text/html" in accept_header and "application/json" not in accept_header:
		return False
	return True


# --- END OF FILE utils/auth_response.py ---
