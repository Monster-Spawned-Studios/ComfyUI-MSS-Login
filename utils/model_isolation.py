"""
Helpers for experimental per-user model isolation paths.
"""

from __future__ import annotations

import os

from ..constants import DATA_DIR, experimental_model_isolation_enabled


def sanitize_user_segment(user_id: str | None) -> str:
    raw = (user_id or "").strip()
    if not raw:
        return "public"
    safe = "".join(c if c.isalnum() or c in ("_", "-") else "_" for c in raw)
    return safe or "public"


def isolation_models_base() -> str:
    return os.path.join(DATA_DIR, "model_isolation", "models")


def isolation_folder_root(folder_type: str) -> str:
    return os.path.join(isolation_models_base(), folder_type)


def isolation_user_folder(folder_type: str, user_id: str | None) -> str:
    return os.path.join(isolation_folder_root(folder_type), sanitize_user_segment(user_id))


def ensure_isolation_folder_registered(folder_type: str) -> str | None:
    """
    Register a shared folder root so ComfyUI resolves names as "<user_id>/<file>".
    Returns the folder root when enabled.
    """
    if not experimental_model_isolation_enabled():
        return None
    root = isolation_folder_root(folder_type)
    os.makedirs(root, exist_ok=True)
    try:
        import folder_paths  # pyright: ignore[reportMissingImports]

        folder_paths.add_model_folder_path(folder_type, root)
    except Exception:
        pass
    return root


def maybe_isolated_destination(folder_type: str, user_id: str | None) -> str | None:
    """Return destination directory for isolated storage, or None when feature is disabled."""
    if not experimental_model_isolation_enabled():
        return None
    ensure_isolation_folder_registered(folder_type)
    dest = isolation_user_folder(folder_type, user_id)
    os.makedirs(dest, exist_ok=True)
    return dest
