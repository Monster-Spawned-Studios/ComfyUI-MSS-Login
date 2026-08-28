"""
Quarantine storage and lifecycle management for NSFW notification actions.
"""

from __future__ import annotations

import asyncio
import hashlib
import os
from datetime import UTC, datetime, timezone
from typing import Any, Dict, List, Optional

from .json_utils import load_json_file, save_json_file


def _utc_now_iso() -> str:
	return datetime.now(UTC).isoformat()


def _get_paths() -> dict:
	from ..constants import DATA_DIR

	quarantine_root = os.path.join(DATA_DIR, "quarantine")
	images_dir = os.path.join(quarantine_root, "images")
	records_path = os.path.join(quarantine_root, "records.json")
	return {"root": quarantine_root, "images_dir": images_dir, "records_path": records_path}


def get_quarantine_settings() -> dict:
	"""Return effective quarantine settings from config with secure defaults."""
	from ..constants import CONFIG_FILE_PATH

	cfg = load_json_file(CONFIG_FILE_PATH, {})
	section = cfg.get("quarantine") if isinstance(cfg, dict) else {}
	if not isinstance(section, dict):
		section = {}
	retention_days = int(section.get("retention_days", 30) or 30)
	cleanup_interval_hours = int(section.get("cleanup_interval_hours", 24) or 24)
	return {
		"retention_days": max(1, retention_days),
		"cleanup_interval_hours": max(1, cleanup_interval_hours),
	}


def _load_records() -> list[dict]:
	paths = _get_paths()
	data = load_json_file(paths["records_path"], {"items": []})
	if isinstance(data, dict):
		items = data.get("items", [])
		if isinstance(items, list):
			return [x for x in items if isinstance(x, dict)]
	return []


def _save_records(items: list[dict]) -> None:
	paths = _get_paths()
	os.makedirs(paths["root"], exist_ok=True)
	save_json_file(paths["records_path"], {"items": items})


def list_quarantine_items() -> list[dict]:
	"""List quarantine records newest-first."""
	items = _load_records()
	items.sort(key=lambda x: str(x.get("quarantined_at", "")), reverse=True)
	return items


def mark_quarantine_item_reviewed(record_id: str) -> dict | None:
	"""Mark a quarantined item reviewed and persist the update."""
	items = _load_records()
	for item in items:
		if item.get("id") == record_id:
			item["reviewed_at"] = _utc_now_iso()
			_save_records(items)
			return item
	return None


def quarantine_image_file(
	*,
	source_path: str,
	username: str,
	workflow_name: str,
	generated_at: str,
	score: Any,
	severity: Any,
	retention_days: int,
) -> dict:
	"""Move source file to quarantine and create a metadata record."""
	paths = _get_paths()
	if not source_path or not os.path.isfile(source_path):
		return {"status": "not_found", "source_path": source_path}

	os.makedirs(paths["images_dir"], exist_ok=True)
	real_source = os.path.realpath(source_path)
	image_root = os.path.realpath(paths["images_dir"])
	if real_source.startswith(image_root + os.sep):
		return {"status": "already_quarantined", "source_path": real_source}

	base_name = os.path.basename(real_source)
	quarantined_at = _utc_now_iso()
	unique_prefix = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
	dest_name = f"{unique_prefix}_{base_name}"
	dest_path = os.path.join(paths["images_dir"], dest_name)
	while os.path.exists(dest_path):
		dest_name = (
			f"{unique_prefix}_{hashlib.sha1(dest_name.encode('utf-8')).hexdigest()[:8]}_{base_name}"
		)
		dest_path = os.path.join(paths["images_dir"], dest_name)

	try:
		os.replace(real_source, dest_path)
	except FileNotFoundError:
		return {"status": "not_found", "source_path": real_source}
	except Exception as exc:
		return {"status": "error", "error": str(exc)}

	record_id = hashlib.sha1(
		f"{dest_path}|{quarantined_at}|{username}|{workflow_name}".encode()
	).hexdigest()
	delete_after_ts = int(datetime.now(UTC).timestamp()) + (
		max(1, int(retention_days)) * 86400
	)
	record = {
		"id": record_id,
		"file_name": base_name,
		"quarantined_name": dest_name,
		"source_path": real_source,
		"quarantined_path": os.path.realpath(dest_path),
		"username": username or "unknown",
		"workflow_name": workflow_name or "unknown",
		"generated_at": generated_at or "unknown",
		"quarantined_at": quarantined_at,
		"reviewed_at": None,
		"score": score,
		"severity": severity,
		"delete_after_ts": delete_after_ts,
	}
	items = _load_records()
	items.append(record)
	_save_records(items)
	return {"status": "ok", "record": record}


def cleanup_expired_quarantine(*, now_ts: int | None = None) -> dict:
	"""Delete unreviewed quarantined files past retention and prune records."""
	current_ts = int(now_ts or datetime.now(UTC).timestamp())
	items = _load_records()
	kept: list[dict] = []
	deleted = 0
	missing = 0
	for item in items:
		reviewed = bool(item.get("reviewed_at"))
		delete_after_ts = int(item.get("delete_after_ts", 0) or 0)
		file_path = str(item.get("quarantined_path") or "")
		if reviewed or delete_after_ts <= 0 or delete_after_ts > current_ts:
			kept.append(item)
			continue
		try:
			if file_path and os.path.isfile(file_path):
				os.remove(file_path)
				deleted += 1
			else:
				missing += 1
		except Exception:
			kept.append(item)
			continue
	_save_records(kept)
	return {"deleted": deleted, "missing": missing, "remaining": len(kept)}


async def quarantine_cleanup_loop(app, logger, _config_path: str = "") -> None:
	"""Background task to periodically clean expired unreviewed quarantine items."""
	while True:
		try:
			settings = get_quarantine_settings()
			result = cleanup_expired_quarantine()
			if result.get("deleted") or result.get("missing"):
				logger.info(
					"[mss-login] Quarantine cleanup: deleted=%s missing=%s remaining=%s",
					result.get("deleted"),
					result.get("missing"),
					result.get("remaining"),
				)
			sleep_seconds = max(300, int(settings["cleanup_interval_hours"]) * 3600)
		except Exception as exc:
			logger.warning("[mss-login] Quarantine cleanup error: %s", exc)
			sleep_seconds = 3600
		await asyncio.sleep(sleep_seconds)
