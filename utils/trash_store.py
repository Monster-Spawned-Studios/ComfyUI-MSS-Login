"""
Per-user trash bin storage and lifecycle management for deleted images.
"""

from __future__ import annotations

import asyncio
import os
import secrets
from datetime import datetime, timezone
from typing import Any

from .json_utils import load_json_file, save_json_file
from .path_safety import resolve_path_under

_ALLOWED_IMAGE_EXTS = (".png", ".jpg", ".jpeg", ".webp", ".gif", ".bmp")


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _sanitize_user_segment(user: str | None) -> str:
    raw = (user or "").strip()
    if not raw:
        return "unknown"
    return "".join(ch if ch.isalnum() or ch in ("_", "-") else "_" for ch in raw) or "unknown"


def _get_paths() -> dict[str, str]:
    from ..constants import DATA_DIR

    root = os.path.join(DATA_DIR, "trash_bin")
    records_path = os.path.join(root, "records.json")
    return {"root": root, "records_path": records_path}


def _load_records() -> list[dict[str, Any]]:
    data = load_json_file(_get_paths()["records_path"], {"items": []})
    if isinstance(data, dict):
        items = data.get("items", [])
        if isinstance(items, list):
            return [it for it in items if isinstance(it, dict)]
    return []


def _save_records(items: list[dict[str, Any]]) -> None:
    paths = _get_paths()
    os.makedirs(paths["root"], exist_ok=True)
    save_json_file(paths["records_path"], {"items": items})


def get_trash_settings() -> dict[str, int]:
    from ..constants import CONFIG_FILE_PATH

    cfg = load_json_file(CONFIG_FILE_PATH, {})
    section = cfg.get("trash") if isinstance(cfg, dict) else {}
    if not isinstance(section, dict):
        section = {}
    retention_days = int(section.get("retention_days", 30) or 30)
    cleanup_interval_hours = int(section.get("cleanup_interval_hours", 24) or 24)
    return {
        "retention_days": max(1, retention_days),
        "cleanup_interval_hours": max(1, cleanup_interval_hours),
    }


def _build_safe_relative_path(filename: str | None, subfolder: str | None) -> str | None:
    name = str(filename or "").strip()
    if not name or "/" in name or "\\" in name or ".." in name:
        return None
    raw_subfolder = str(subfolder or "").replace("\\", "/").strip()
    if raw_subfolder:
        if raw_subfolder.startswith("/") or ".." in raw_subfolder:
            return None
        parts = [seg for seg in raw_subfolder.split("/") if seg]
        if any(seg in (".", "..") for seg in parts):
            return None
        clean_subfolder = "/".join(parts)
        return f"{clean_subfolder}/{name}"
    return name


def _resolve_image_path(
    *, image_type: str, filename: str | None, subfolder: str | None, allow_global_lookup: bool
) -> tuple[str | None, str | None]:
    import folder_paths

    rel = _build_safe_relative_path(filename, subfolder)
    if not rel:
        return None, None
    img_type = (image_type or "output").strip().lower()
    target_dir = (
        folder_paths.get_temp_directory()
        if img_type == "temp"
        else folder_paths.get_output_directory()
    )
    resolved = resolve_path_under(target_dir, rel)
    if resolved and os.path.isfile(resolved):
        return resolved, rel
    if allow_global_lookup:
        from .data_dir import get_data_subdir

        global_base = get_data_subdir("temp" if img_type == "temp" else "output")
        resolved = resolve_path_under(global_base, rel)
        if resolved and os.path.isfile(resolved):
            return resolved, rel
    return None, rel


def _guess_owner_from_path(file_path: str) -> str | None:
    from .data_dir import get_data_subdir

    for kind in ("output", "temp"):
        base = os.path.realpath(get_data_subdir(kind))
        real_path = os.path.realpath(file_path)
        if real_path.startswith(base + os.sep):
            rel = os.path.relpath(real_path, base).replace("\\", "/")
            parts = [p for p in rel.split("/") if p]
            if parts:
                return _sanitize_user_segment(parts[0])
    return None


def trash_image_reference(
    *,
    filename: str | None,
    subfolder: str | None,
    image_type: str,
    request_username: str,
    is_owner: bool,
    prompt_id: str | None = None,
) -> dict[str, Any]:
    path, rel = _resolve_image_path(
        image_type=image_type,
        filename=filename,
        subfolder=subfolder,
        allow_global_lookup=bool(is_owner),
    )
    if not path:
        return {"status": "not_found", "filename": filename, "subfolder": subfolder}
    ext = os.path.splitext(path)[1].lower()
    if ext not in _ALLOWED_IMAGE_EXTS:
        return {"status": "skipped", "reason": "unsupported_extension", "path": path}
    return trash_image_file(
        source_path=path,
        request_username=request_username,
        is_owner=is_owner,
        relative_path=rel,
        image_type=image_type,
        prompt_id=prompt_id,
    )


def trash_image_file(
    *,
    source_path: str,
    request_username: str,
    is_owner: bool,
    relative_path: str | None,
    image_type: str,
    prompt_id: str | None = None,
) -> dict[str, Any]:
    if not source_path or not os.path.isfile(source_path):
        return {"status": "not_found", "source_path": source_path}

    paths = _get_paths()
    os.makedirs(paths["root"], exist_ok=True)

    real_source = os.path.realpath(source_path)
    owner_guess = _guess_owner_from_path(real_source)
    request_user_safe = _sanitize_user_segment(request_username)
    owner_user = owner_guess or request_user_safe
    if owner_user != request_user_safe and not is_owner:
        return {"status": "forbidden", "source_path": source_path}

    user_dir = os.path.join(paths["root"], owner_user)
    os.makedirs(user_dir, exist_ok=True)

    base_name = os.path.basename(real_source)
    unique = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    dest_name = f"{unique}_{secrets.token_hex(4)}_{base_name}"
    dest_path = os.path.join(user_dir, dest_name)
    while os.path.exists(dest_path):
        dest_name = f"{unique}_{secrets.token_hex(6)}_{base_name}"
        dest_path = os.path.join(user_dir, dest_name)

    try:
        os.replace(real_source, dest_path)
    except FileNotFoundError:
        return {"status": "not_found", "source_path": real_source}
    except Exception as exc:
        return {"status": "error", "error": str(exc)}

    settings = get_trash_settings()
    record_id = secrets.token_hex(16)
    now_ts = int(datetime.now(timezone.utc).timestamp())
    record = {
        "id": record_id,
        "owner_user": owner_user,
        "deleted_by": request_user_safe,
        "deleted_at": _utc_now_iso(),
        "deleted_at_ts": now_ts,
        "delete_after_ts": now_ts + int(settings["retention_days"]) * 86400,
        "source_path": real_source,
        "source_rel": (relative_path or "").replace("\\", "/"),
        "image_type": (image_type or "output").strip().lower() or "output",
        "filename": base_name,
        "trash_path": os.path.realpath(dest_path),
        "prompt_id": str(prompt_id or ""),
    }
    items = _load_records()
    items.append(record)
    _save_records(items)
    return {"status": "ok", "record": record}


def list_trash_items(
    *, request_username: str, is_owner: bool, target_user: str | None = None
) -> list[dict]:
    items = _load_records()
    user = _sanitize_user_segment(target_user or request_username)
    if not is_owner:
        user = _sanitize_user_segment(request_username)
    filtered = [it for it in items if _sanitize_user_segment(it.get("owner_user")) == user]
    filtered.sort(key=lambda it: int(it.get("deleted_at_ts", 0) or 0), reverse=True)
    return filtered


def restore_trash_item(*, item_id: str, request_username: str, is_owner: bool) -> dict[str, Any]:
    items = _load_records()
    request_user = _sanitize_user_segment(request_username)
    for idx, item in enumerate(items):
        if item.get("id") != item_id:
            continue
        owner_user = _sanitize_user_segment(item.get("owner_user"))
        if owner_user != request_user and not is_owner:
            return {"status": "forbidden"}
        trash_path = str(item.get("trash_path") or "")
        if not trash_path or not os.path.isfile(trash_path):
            items.pop(idx)
            _save_records(items)
            return {"status": "missing", "removed_record": True}

        img_type = str(item.get("image_type") or "output").strip().lower()
        source_rel = str(item.get("source_rel") or "").replace("\\", "/")
        if not source_rel:
            source_rel = os.path.basename(str(item.get("filename") or "restored.bin"))
        from .data_dir import get_data_subdir

        base = get_data_subdir("temp" if img_type == "temp" else "output")
        if is_owner and owner_user:
            base = os.path.join(base, owner_user)
        restore_target = resolve_path_under(base, source_rel)
        if not restore_target:
            return {"status": "error", "error": "invalid_restore_path"}

        os.makedirs(os.path.dirname(restore_target), exist_ok=True)
        final_target = restore_target
        if os.path.exists(final_target):
            stem, ext = os.path.splitext(final_target)
            final_target = f"{stem}_restored_{secrets.token_hex(3)}{ext}"

        try:
            os.replace(trash_path, final_target)
        except Exception as exc:
            return {"status": "error", "error": str(exc)}

        items.pop(idx)
        _save_records(items)
        return {"status": "ok", "restored_path": final_target}
    return {"status": "not_found"}


def empty_trash(
    *, request_username: str, is_owner: bool, target_user: str | None = None
) -> dict[str, int]:
    items = _load_records()
    request_user = _sanitize_user_segment(request_username)
    target = _sanitize_user_segment(target_user or request_user)
    if not is_owner:
        target = request_user

    kept: list[dict[str, Any]] = []
    deleted = 0
    missing = 0
    for item in items:
        owner = _sanitize_user_segment(item.get("owner_user"))
        if owner != target:
            kept.append(item)
            continue
        path = str(item.get("trash_path") or "")
        try:
            if path and os.path.isfile(path):
                os.remove(path)
                deleted += 1
            else:
                missing += 1
        except Exception:
            kept.append(item)
    _save_records(kept)
    return {"deleted": deleted, "missing": missing, "remaining": len(kept)}


def cleanup_expired_trash(*, now_ts: int | None = None) -> dict[str, int]:
    current_ts = int(now_ts or datetime.now(timezone.utc).timestamp())
    items = _load_records()
    kept: list[dict[str, Any]] = []
    deleted = 0
    missing = 0
    for item in items:
        delete_after_ts = int(item.get("delete_after_ts", 0) or 0)
        if delete_after_ts <= 0 or delete_after_ts > current_ts:
            kept.append(item)
            continue
        path = str(item.get("trash_path") or "")
        try:
            if path and os.path.isfile(path):
                os.remove(path)
                deleted += 1
            else:
                missing += 1
        except Exception:
            kept.append(item)
    _save_records(kept)
    return {"deleted": deleted, "missing": missing, "remaining": len(kept)}


def trash_deleted_history_images(
    *,
    history_entry: dict[str, Any] | None,
    request_username: str,
    is_owner: bool,
    prompt_id: str | None = None,
) -> dict[str, int]:
    if not isinstance(history_entry, dict):
        return {"moved": 0, "skipped": 0, "forbidden": 0, "missing": 0}
    outputs = history_entry.get("outputs")
    if not isinstance(outputs, dict):
        return {"moved": 0, "skipped": 0, "forbidden": 0, "missing": 0}
    moved = 0
    skipped = 0
    forbidden = 0
    missing = 0
    for node_out in outputs.values():
        if not isinstance(node_out, dict):
            continue
        images = node_out.get("images")
        if not isinstance(images, list):
            continue
        for image in images:
            if not isinstance(image, dict):
                continue
            res = trash_image_reference(
                filename=image.get("filename"),
                subfolder=image.get("subfolder"),
                image_type=str(image.get("type") or "output"),
                request_username=request_username,
                is_owner=is_owner,
                prompt_id=prompt_id,
            )
            status = res.get("status")
            if status == "ok":
                moved += 1
            elif status == "forbidden":
                forbidden += 1
            elif status == "not_found":
                missing += 1
            else:
                skipped += 1
    return {"moved": moved, "skipped": skipped, "forbidden": forbidden, "missing": missing}


async def trash_cleanup_loop(app, logger, _config_path: str = "") -> None:
    while True:
        try:
            settings = get_trash_settings()
            result = cleanup_expired_trash()
            if result.get("deleted") or result.get("missing"):
                logger.info(
                    "[mss-login] Trash cleanup: deleted=%s missing=%s remaining=%s",
                    result.get("deleted"),
                    result.get("missing"),
                    result.get("remaining"),
                )
            sleep_seconds = max(300, int(settings["cleanup_interval_hours"]) * 3600)
        except Exception as exc:
            logger.warning("[mss-login] Trash cleanup error: %s", exc)
            sleep_seconds = 3600
        await asyncio.sleep(sleep_seconds)
