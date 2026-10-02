# --- START OF FILE utils/cpe_convert.py ---
"""UI→API workflow conversion helpers for Comfy Portal Endpoint compatibility.

Phase 0: detect/delegate to a sibling comfy-portal-endpoint Playwright browser
when installed. Phase 1 will add an embedded JWT-injected converter; the hook
below is the extension point (returns None until that lands).
"""

from __future__ import annotations

import importlib.util
import os
import sys
from collections.abc import Awaitable, Callable
from typing import Any

# Optional embedded converter: async (workflow_dict) -> api_prompt_dict.
# Phase 1 registers a real implementation; Phase 0 leaves this None.
_embedded_convert: Callable[[dict[str, Any]], Awaitable[dict[str, Any]]] | None = None


def set_embedded_convert(
	converter: Callable[[dict[str, Any]], Awaitable[dict[str, Any]]] | None,
) -> None:
	"""Register or clear the Phase-1 embedded Playwright converter."""
	global _embedded_convert
	_embedded_convert = converter


def get_embedded_convert() -> Callable[[dict[str, Any]], Awaitable[dict[str, Any]]] | None:
	return _embedded_convert


def _custom_nodes_root() -> str | None:
	"""Return ComfyUI custom_nodes directory that contains this package, if any."""
	# utils/ -> package root (ComfyUI-MSS-Login) -> custom_nodes
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
	"""Locate CPE's HeadlessBrowserManager if comfy-portal-endpoint is loadable.

	Tries already-imported sys.modules first, then loads sibling ``browser.py``
	via importlib (hyphenated folder names are not normal Python packages).
	"""
	for mod_name, mod in list(sys.modules.items()):
		if not mod:
			continue
		lower = mod_name.lower()
		if "comfy-portal-endpoint" in lower or "comfy_portal_endpoint" in lower:
			if lower.endswith("browser") or lower.endswith(".browser"):
				mgr = _browser_manager_from_module(mod)
				if mgr is not None:
					return mgr
			# Package root may expose get_browser_manager re-export in future
			mgr = _browser_manager_from_module(mod)
			if mgr is not None:
				return mgr

	for browser_path in _sibling_cpe_browser_paths():
		try:
			# Load package-ish namespace so relative imports inside CPE work if needed
			cpe_dir = os.path.dirname(browser_path)
			pkg_name = "mss_login_cpe_sibling"
			if pkg_name not in sys.modules:
				import types

				pkg = types.ModuleType(pkg_name)
				pkg.__path__ = [cpe_dir]
				pkg.__package__ = pkg_name
				sys.modules[pkg_name] = pkg
			# Load logger first if present (browser imports .logger)
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


def browser_status_string(manager: Any | None = None) -> str:
	"""Return CPE health browser.status value from a manager or discovery."""
	mgr = manager if manager is not None else find_sibling_cpe_browser_manager()
	if mgr is None and _embedded_convert is None:
		return "not_installed"
	if mgr is None:
		# Embedded converter registered but no live status object yet
		return "not_initialized"
	status = getattr(mgr, "status", None)
	if status is None:
		return "not_initialized"
	# Enum with .value, or plain string
	value = getattr(status, "value", status)
	if isinstance(value, str) and value:
		return value
	return "not_initialized"


def health_browser_payload() -> dict[str, Any]:
	"""Build the ``browser`` object for ``GET /cpe/health``."""
	mgr = find_sibling_cpe_browser_manager()
	status = browser_status_string(mgr)
	payload: dict[str, Any] = {"status": status}
	if mgr is not None:
		err = getattr(mgr, "error_message", None)
		if err and status in ("error", "not_installed"):
			payload["error"] = str(err)
	return payload


async def convert_ui_workflow(workflow_data: dict[str, Any]) -> dict[str, Any]:
	"""Convert a UI-format workflow dict to an API prompt dict.

	Preference order:
	1. Embedded JWT-injected converter (Phase 1+)
	2. Sibling comfy-portal-endpoint browser manager

	Raises RuntimeError when no converter is available or conversion fails.
	"""
	if not isinstance(workflow_data, dict):
		raise RuntimeError("Workflow data must be a JSON object")

	if _embedded_convert is not None:
		return await _embedded_convert(workflow_data)

	mgr = find_sibling_cpe_browser_manager()
	if mgr is None:
		raise RuntimeError(
			"No workflow converter available. Install comfy-portal-endpoint "
			"for headless conversion, or save an API-format workflow."
		)
	convert = getattr(mgr, "convert_workflow", None)
	if not callable(convert):
		raise RuntimeError("Sibling CPE browser manager has no convert_workflow")
	return await convert(workflow_data)


def ui_convert_unavailable_details() -> str:
	"""Human-readable details for a 503 when UI conversion cannot run."""
	if find_sibling_cpe_browser_manager() is not None:
		return (
			"UI-format conversion via comfy-portal-endpoint failed or is not ready. "
			"The headless browser must load the ComfyUI frontend; under MSS-Login "
			"auth this often fails until embedded JWT-injected convert (Phase 1) "
			"is enabled. Prefer saving API-format workflows from the desktop UI."
		)
	return (
		"This workflow is in UI format. Install comfy-portal-endpoint for "
		"headless conversion, or save an API-format workflow."
	)


# --- END OF FILE utils/cpe_convert.py ---
