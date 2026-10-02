# --- START OF FILE utils/cpe_convert.py ---
"""UI→API workflow conversion helpers for Comfy Portal Endpoint compatibility.

Phase 0: detect/delegate to a sibling comfy-portal-endpoint Playwright browser.
Phase 1: prefer embedded JWT/Bearer-injected converter when enabled.
"""

from __future__ import annotations

import importlib.util
import os
import sys
from collections.abc import Awaitable, Callable
from contextvars import ContextVar
from typing import Any

# Per-request auth token for the embedded Playwright session (never logged).
_convert_auth_token: ContextVar[str | None] = ContextVar("mss_cpe_convert_auth_token", default=None)

# Optional embedded converter: async (workflow_dict) -> api_prompt_dict.
_embedded_convert: Callable[[dict[str, Any]], Awaitable[dict[str, Any]]] | None = None
_embedded_registered = False


def set_convert_auth_token(token: str | None):
	"""Bind the auth token used by the embedded converter for this task."""
	return _convert_auth_token.set(token)


def reset_convert_auth_token(token) -> None:
	_convert_auth_token.reset(token)


def get_convert_auth_token() -> str | None:
	return _convert_auth_token.get()


def set_embedded_convert(
	converter: Callable[[dict[str, Any]], Awaitable[dict[str, Any]]] | None,
) -> None:
	"""Register or clear the Phase-1 embedded Playwright converter."""
	global _embedded_convert
	_embedded_convert = converter


def get_embedded_convert() -> Callable[[dict[str, Any]], Awaitable[dict[str, Any]]] | None:
	return _embedded_convert


def ensure_embedded_convert_registered() -> None:
	"""Register embedded convert once when ``cpe_embedded_convert`` is enabled."""
	global _embedded_registered
	if _embedded_registered:
		return
	_embedded_registered = True
	try:
		from ..constants import CPE_EMBEDDED_CONVERT
	except Exception:
		CPE_EMBEDDED_CONVERT = True
	if not CPE_EMBEDDED_CONVERT:
		return

	async def _embedded(workflow_data: dict[str, Any]) -> dict[str, Any]:
		from .cpe_browser import get_embedded_browser_manager

		mgr = get_embedded_browser_manager()
		return await mgr.convert_workflow(workflow_data, auth_token=get_convert_auth_token())

	set_embedded_convert(_embedded)


def _custom_nodes_root() -> str | None:
	"""Return ComfyUI custom_nodes directory that contains this package, if any."""
	pkg_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
	parent = os.path.dirname(pkg_root)
	if os.path.basename(parent).lower() in ("custom_nodes", "custom-nodes"):
		return parent
	return None


def _sibling_cpe_browser_paths() -> list[str]:
	"""Candidate browser.py paths for a sibling comfy-portal-endpoint install."""
	root = _custom_nodes_root()
	if not root:
		return []
	candidates = []
	for name in ("comfy-portal-endpoint", "comfy_portal_endpoint", "ComfyUI-comfy-portal-endpoint"):
		path = os.path.join(root, name, "browser.py")
		if os.path.isfile(path):
			candidates.append(path)
	return candidates


def _browser_manager_from_module(mod: Any) -> Any | None:
	getter = getattr(mod, "get_browser_manager", None)
	if callable(getter):
		try:
			return getter()
		except Exception:
			return None
	return None


def find_sibling_cpe_browser_manager() -> Any | None:
	"""Locate CPE's HeadlessBrowserManager if comfy-portal-endpoint is loadable."""
	for mod_name, mod in list(sys.modules.items()):
		if not mod:
			continue
		lower = mod_name.lower()
		if "comfy-portal-endpoint" in lower or "comfy_portal_endpoint" in lower:
			if lower.endswith("browser") or lower.endswith(".browser"):
				mgr = _browser_manager_from_module(mod)
				if mgr is not None:
					return mgr
			mgr = _browser_manager_from_module(mod)
			if mgr is not None:
				return mgr

	for browser_path in _sibling_cpe_browser_paths():
		try:
			cpe_dir = os.path.dirname(browser_path)
			pkg_name = "mss_login_cpe_sibling"
			if pkg_name not in sys.modules:
				import types

				pkg = types.ModuleType(pkg_name)
				pkg.__path__ = [cpe_dir]
				pkg.__package__ = pkg_name
				sys.modules[pkg_name] = pkg
			logger_path = os.path.join(cpe_dir, "logger.py")
			if os.path.isfile(logger_path) and f"{pkg_name}.logger" not in sys.modules:
				logger_spec = importlib.util.spec_from_file_location(
					f"{pkg_name}.logger", logger_path
				)
				if logger_spec and logger_spec.loader:
					logger_mod = importlib.util.module_from_spec(logger_spec)
					logger_mod.__package__ = pkg_name
					sys.modules[f"{pkg_name}.logger"] = logger_mod
					logger_spec.loader.exec_module(logger_mod)

			spec = importlib.util.spec_from_file_location(f"{pkg_name}.browser", browser_path)
			if not spec or not spec.loader:
				continue
			mod = importlib.util.module_from_spec(spec)
			mod.__package__ = pkg_name
			sys.modules[f"{pkg_name}.browser"] = mod
			spec.loader.exec_module(mod)
			mgr = _browser_manager_from_module(mod)
			if mgr is not None:
				return mgr
		except Exception:
			continue
	return None


def _embedded_enabled() -> bool:
	try:
		from ..constants import CPE_EMBEDDED_CONVERT

		return bool(CPE_EMBEDDED_CONVERT)
	except Exception:
		return True


def _status_value(manager: Any) -> str:
	status = getattr(manager, "status", None)
	if status is None:
		return "not_initialized"
	value = getattr(status, "value", status)
	if isinstance(value, str) and value:
		return value
	return "not_initialized"


def browser_status_string(manager: Any | None = None) -> str:
	"""Return CPE health browser.status (prefer embedded when enabled)."""
	ensure_embedded_convert_registered()
	if manager is not None:
		return _status_value(manager)

	if _embedded_enabled() and _embedded_convert is not None:
		try:
			from .cpe_browser import get_embedded_browser_manager

			return _status_value(get_embedded_browser_manager())
		except Exception:
			return "not_installed"

	sibling = find_sibling_cpe_browser_manager()
	if sibling is not None:
		return _status_value(sibling)
	return "not_installed"


def health_browser_payload() -> dict[str, Any]:
	"""Build the ``browser`` object for ``GET /cpe/health``."""
	ensure_embedded_convert_registered()
	mgr = None
	if _embedded_enabled() and _embedded_convert is not None:
		try:
			from .cpe_browser import get_embedded_browser_manager

			mgr = get_embedded_browser_manager()
		except Exception:
			mgr = None
	if mgr is None:
		mgr = find_sibling_cpe_browser_manager()
	status = browser_status_string(mgr)
	payload: dict[str, Any] = {"status": status}
	if mgr is not None:
		err = getattr(mgr, "error_message", None)
		if err and status in ("error", "not_installed"):
			payload["error"] = str(err)
	return payload


async def convert_ui_workflow(
	workflow_data: dict[str, Any], *, auth_token: str | None = None
) -> dict[str, Any]:
	"""Convert a UI-format workflow dict to an API prompt dict.

	Preference order:
	1. Embedded JWT-injected converter (when ``cpe_embedded_convert``)
	2. Sibling comfy-portal-endpoint browser manager
	"""
	if not isinstance(workflow_data, dict):
		raise RuntimeError("Workflow data must be a JSON object")

	ensure_embedded_convert_registered()
	token_token = set_convert_auth_token(auth_token)
	try:
		if _embedded_convert is not None and _embedded_enabled():
			return await _embedded_convert(workflow_data)

		mgr = find_sibling_cpe_browser_manager()
		if mgr is None:
			raise RuntimeError(
				"No workflow converter available. Enable cpe_embedded_convert and install "
				"Playwright (uv sync --group cpe-convert && playwright install chromium), "
				"install comfy-portal-endpoint, or save an API-format workflow."
			)
		convert = getattr(mgr, "convert_workflow", None)
		if not callable(convert):
			raise RuntimeError("Sibling CPE browser manager has no convert_workflow")
		return await convert(workflow_data)
	finally:
		reset_convert_auth_token(token_token)


def ui_convert_unavailable_details() -> str:
	"""Human-readable details for a 503 when UI conversion cannot run."""
	ensure_embedded_convert_registered()
	if _embedded_enabled():
		try:
			from .cpe_browser import EmbeddedBrowserManager, get_embedded_browser_manager

			mgr = get_embedded_browser_manager()
			if not EmbeddedBrowserManager.playwright_available():
				return (
					"UI-format conversion requires Playwright. "
					"Run: uv sync --group cpe-convert && playwright install chromium. "
					"Or save an API-format workflow from the desktop UI."
				)
			err = getattr(mgr, "error_message", None)
			if err:
				return str(err)
		except Exception:
			pass
		return (
			"UI-format conversion failed. Ensure ComfyUI is reachable on loopback, "
			"Playwright Chromium is installed, and the convert request is authenticated. "
			"Prefer saving API-format workflows if conversion remains unavailable."
		)
	if find_sibling_cpe_browser_manager() is not None:
		return (
			"UI-format conversion via comfy-portal-endpoint failed or is not ready. "
			"Under MSS-Login auth the stock CPE browser often cannot load `/` without "
			"a session. Enable embedded convert (cpe_embedded_convert) or save "
			"API-format workflows."
		)
	return (
		"This workflow is in UI format. Enable cpe_embedded_convert and install "
		"Playwright, install comfy-portal-endpoint, or save an API-format workflow."
	)


# --- END OF FILE utils/cpe_convert.py ---
