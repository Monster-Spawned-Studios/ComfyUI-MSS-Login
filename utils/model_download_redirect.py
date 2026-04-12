"""
Request-level rewriting helpers for third-party model download endpoints.

When model isolation is enabled, some extensions/core routes may still target the
global ComfyUI models directory. These helpers redirect such destinations into the
isolated per-user model tree.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from ..constants import CONFIG_FILE_PATH
from .model_isolation import isolation_models_base, sanitize_user_segment
from .json_utils import load_json_file, save_json_file


_PATHISH_KEYWORDS = ("path", "dir", "folder", "destination", "dest", "download", "save", "output")

_DEFAULT_ROUTE_PATTERNS = ["/civicomfy", "civicomfy", "/manager", "model/download"]

_cached_route_patterns: list[str] | None = None


def _normalize_route_pattern(value: str) -> str:
    return (value or "").strip().lower()


def _dedupe_patterns(values: list[str]) -> list[str]:
    out: list[str] = []
    for value in values:
        norm = _normalize_route_pattern(value)
        if norm and norm not in out:
            out.append(norm)
    return out


def _config_model_isolation_block() -> dict:
    cfg = load_json_file(CONFIG_FILE_PATH, {}) or {}
    block = cfg.get("model_isolation")
    if isinstance(block, dict):
        return block
    return {}


def _config_route_patterns() -> list[str]:
    block = _config_model_isolation_block()
    patterns = block.get("download_redirect_patterns")
    if isinstance(patterns, list):
        return [str(x) for x in patterns]
    return []


def _env_route_patterns() -> list[str]:
    raw = (os.environ.get("MODEL_ISOLATION_DOWNLOAD_REDIRECT_PATTERNS") or "").strip()
    if not raw:
        return []
    return [x.strip() for x in raw.split(",") if x.strip()]


def _detected_route_patterns() -> list[str]:
    patterns: list[str] = []
    if is_civicomfy_present():
        patterns.extend(["/civicomfy", "civicomfy", "/api/civicomfy"])
    return patterns


def get_effective_route_patterns() -> list[str]:
    """Return merged route patterns for redirect matching."""
    return _dedupe_patterns(
        _DEFAULT_ROUTE_PATTERNS
        + _config_route_patterns()
        + _env_route_patterns()
        + _detected_route_patterns()
    )


def initialize_redirect_pattern_cache(logger=None) -> list[str]:
    """Build route-pattern cache once at startup (safe to call repeatedly)."""
    global _cached_route_patterns
    _cached_route_patterns = get_effective_route_patterns()
    if logger is not None:
        try:
            logger.info(
                "[mss-login] Model isolation redirect patterns initialized: %s",
                ", ".join(_cached_route_patterns),
            )
        except Exception:
            pass
    return list(_cached_route_patterns)


def get_configured_route_patterns() -> list[str]:
    """Owner-facing configured patterns (without defaults/autodetection)."""
    return _dedupe_patterns(_config_route_patterns())


def save_configured_route_patterns(patterns: list[str]) -> list[str]:
    """Persist owner-defined route patterns in config.json."""
    cfg = load_json_file(CONFIG_FILE_PATH, {}) or {}
    if not isinstance(cfg, dict):
        cfg = {}
    block = cfg.get("model_isolation")
    if not isinstance(block, dict):
        block = {}
    block["download_redirect_patterns"] = _dedupe_patterns([str(x) for x in (patterns or [])])
    cfg["model_isolation"] = block
    save_json_file(CONFIG_FILE_PATH, cfg)
    initialize_redirect_pattern_cache()
    return block["download_redirect_patterns"]


def is_civicomfy_present() -> bool:
    """Best-effort detection for Civicomfy custom node presence."""
    candidates = ("Civicomfy", "ComfyUI-Civicomfy", "civicomfy")
    try:
        import folder_paths  # pyright: ignore[reportMissingImports]

        custom_nodes_dir = getattr(folder_paths, "custom_nodes_dir", None)
        if custom_nodes_dir:
            for name in candidates:
                if os.path.isdir(os.path.join(custom_nodes_dir, name)):
                    return True
    except Exception:
        pass

    # Fallback: probe common local custom_nodes layout.
    try:
        base = Path(__file__).resolve().parents[3]
        custom_nodes = base / "custom_nodes"
        for name in candidates:
            if (custom_nodes / name).is_dir():
                return True
    except Exception:
        pass
    return False


def should_try_model_download_redirect(path: str) -> bool:
    global _cached_route_patterns
    p = (path or "").lower()
    if _cached_route_patterns is None:
        _cached_route_patterns = get_effective_route_patterns()
    for pattern in _cached_route_patterns:
        if pattern and pattern in p:
            return True
    return False


def _global_models_root() -> str:
    try:
        import folder_paths  # pyright: ignore[reportMissingImports]

        root = getattr(folder_paths, "models_dir", None) or getattr(
            folder_paths, "models_path", None
        )
        if root:
            return os.path.abspath(str(root))
    except Exception:
        pass
    return ""


def _to_isolated_path(value: str, user_id: str) -> str:
    raw = (value or "").strip().replace("\\", "/")
    if not raw:
        return value

    global_root = _global_models_root().replace("\\", "/").rstrip("/")
    isolation_base = isolation_models_base().replace("\\", "/")
    user_seg = sanitize_user_segment(user_id)

    # Matches direct references to "models" root.
    if raw in ("models", "/models", "./models"):
        return os.path.join(isolation_base, user_seg).replace("\\", "/")

    # Relative models path e.g. models/checkpoints/a.safetensors
    if raw.startswith("models/"):
        rel = raw[len("models/") :].lstrip("/")
        parts = [p for p in rel.split("/") if p]
        if not parts:
            return os.path.join(isolation_base, user_seg).replace("\\", "/")
        folder = parts[0]
        if len(parts) > 1 and parts[1] == user_seg:
            return os.path.join(isolation_base, folder, *parts[1:]).replace("\\", "/")
        return os.path.join(isolation_base, folder, user_seg, *parts[1:]).replace("\\", "/")

    # Absolute path under ComfyUI global models root.
    if global_root:
        norm_abs = os.path.abspath(raw).replace("\\", "/")
        global_norm = global_root.replace("\\", "/")
        if norm_abs == global_norm or norm_abs.startswith(global_norm + "/"):
            rel = norm_abs[len(global_norm) :].lstrip("/")
            parts = [p for p in rel.split("/") if p]
            if not parts:
                return os.path.join(isolation_base, user_seg).replace("\\", "/")
            folder = parts[0]
            if len(parts) > 1 and parts[1] == user_seg:
                return os.path.join(isolation_base, folder, *parts[1:]).replace("\\", "/")
            return os.path.join(isolation_base, folder, user_seg, *parts[1:]).replace("\\", "/")
    return value


def rewrite_download_payload_for_user(payload: Any, user_id: str) -> tuple[Any, bool]:
    """Rewrite path-like fields in payload to isolated per-user model destinations."""
    changed = False

    def _walk(node: Any):
        nonlocal changed
        if isinstance(node, dict):
            out = {}
            for key, value in node.items():
                lkey = str(key).lower()
                if isinstance(value, str) and any(k in lkey for k in _PATHISH_KEYWORDS):
                    new_val = _to_isolated_path(value, user_id)
                    if new_val != value:
                        changed = True
                    out[key] = new_val
                else:
                    out[key] = _walk(value)
            return out
        if isinstance(node, list):
            return [_walk(x) for x in node]
        return node

    return _walk(payload), changed
