"""Request classification helpers for browser navigation vs API calls."""

from typing import Any


def is_browser_navigation(request: Any) -> bool:
    """Return True only for full-page browser navigations.

    `Sec-Fetch-Mode: navigate` is treated as authoritative. When that header is
    missing (common for non-browser clients), we intentionally require positive
    HTML navigation signals to avoid misclassifying API calls and breaking JSON
    login/token flows.
    """
    sec_mode = (request.headers.get("Sec-Fetch-Mode") or "").strip().lower()
    if sec_mode == "navigate":
        return True
    if sec_mode:
        return False

    content_type = (getattr(request, "content_type", "") or "").strip().lower()
    if content_type == "application/json":
        return False

    accept = (request.headers.get("Accept") or "").strip().lower()
    if not accept or accept == "*/*" or "application/json" in accept:
        return False
    return "text/html" in accept or "application/xhtml+xml" in accept
