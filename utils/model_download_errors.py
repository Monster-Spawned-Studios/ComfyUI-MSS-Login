"""Structured error codes for model-download HTTP API (Portal / headless clients)."""

from __future__ import annotations

from aiohttp import web

# Machine-readable codes (also echoed in the ``error`` field for BC).
DOWNLOAD_NOT_ALLOWED = "DOWNLOAD_NOT_ALLOWED"
INVALID_SOURCE = "INVALID_SOURCE"
NOT_FOUND = "NOT_FOUND"
AUTH_REQUIRED = "AUTH_REQUIRED"
AUTH_REQUIRED_UPSTREAM = "AUTH_REQUIRED_UPSTREAM"
INSUFFICIENT_DISK = "INSUFFICIENT_DISK"
FILE_TOO_LARGE = "FILE_TOO_LARGE"
CHECKSUM_MISMATCH = "CHECKSUM_MISMATCH"
ALREADY_EXISTS = "ALREADY_EXISTS"
INVALID_REQUEST = "INVALID_REQUEST"
RATE_LIMITED = "RATE_LIMITED"

DOWNLOAD_NOT_ALLOWED_MESSAGE = "Your account does not have permission to download models."


def error_payload(code: str, message: str) -> dict[str, str]:
	"""
	Dual-shape error body for backwards compatibility.

	Clients that key on ``error`` as a string still work; Portal can use
	``error`` as the code and ``message`` as the human text.
	"""
	return {"error": code, "message": message, "code": code}


def json_error(code: str, message: str, status: int) -> web.Response:
	return web.json_response(error_payload(code, message), status=status)
