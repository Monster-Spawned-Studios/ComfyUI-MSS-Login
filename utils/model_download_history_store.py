"""
Durable model download history (same users DB as shared_items / API keys).

Stores job metadata, progress snapshot, description/trigger words, and paths
so incomplete downloads can be resumed after restart.
"""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any

TABLE = "model_download_history"

_COLUMNS = (
	"job_id",
	"user_id",
	"username",
	"source",
	"status",
	"destination_type",
	"folder_type",
	"model_id",
	"model_version_id",
	"repo_id",
	"filename",
	"subfolder",
	"civitai_host",
	"local_path",
	"saved_relpath",
	"description",
	"trigger_words",
	"bytes_done",
	"total_bytes",
	"error",
	"created_at",
	"updated_at",
	"finished_at",
	"extra_json",
)


def _get_sqlite_store(
	db_path: str, secret_key: str = "", encryption_level: str = ""
) -> "_SqliteHistoryStore":
	from .sqlite_connection import open_sqlite

	path = Path(db_path)
	conn = open_sqlite(
		str(path),
		secret_key=secret_key,
		encryption_level=encryption_level or "",
		check_same_thread=False,
	)
	conn.execute(
		f"""
        CREATE TABLE IF NOT EXISTS {TABLE} (
            job_id TEXT NOT NULL PRIMARY KEY,
            user_id TEXT NOT NULL,
            username TEXT NOT NULL DEFAULT '',
            source TEXT NOT NULL DEFAULT '',
            status TEXT NOT NULL DEFAULT 'queued',
            destination_type TEXT NOT NULL DEFAULT 'local',
            folder_type TEXT NOT NULL DEFAULT 'checkpoints',
            model_id TEXT NOT NULL DEFAULT '',
            model_version_id TEXT NOT NULL DEFAULT '',
            repo_id TEXT NOT NULL DEFAULT '',
            filename TEXT NOT NULL DEFAULT '',
            subfolder TEXT NOT NULL DEFAULT '',
            civitai_host TEXT NOT NULL DEFAULT '',
            local_path TEXT NOT NULL DEFAULT '',
            saved_relpath TEXT NOT NULL DEFAULT '',
            description TEXT NOT NULL DEFAULT '',
            trigger_words TEXT NOT NULL DEFAULT '',
            bytes_done REAL NOT NULL DEFAULT 0,
            total_bytes REAL,
            error TEXT NOT NULL DEFAULT '',
            created_at REAL NOT NULL DEFAULT 0,
            updated_at REAL NOT NULL DEFAULT 0,
            finished_at REAL NOT NULL DEFAULT 0,
            extra_json TEXT NOT NULL DEFAULT ''
        )
        """
	)
	_ensure_columns_sqlite(conn)
	conn.execute(
		f"CREATE INDEX IF NOT EXISTS idx_{TABLE}_user ON {TABLE}(user_id, created_at)"
	)
	conn.execute(
		f"CREATE INDEX IF NOT EXISTS idx_{TABLE}_status ON {TABLE}(status, updated_at)"
	)
	conn.commit()
	return _SqliteHistoryStore(conn)


def _ensure_columns_sqlite(conn) -> None:
	migrations = (
		f"ALTER TABLE {TABLE} ADD COLUMN username TEXT NOT NULL DEFAULT ''",
		f"ALTER TABLE {TABLE} ADD COLUMN source TEXT NOT NULL DEFAULT ''",
		f"ALTER TABLE {TABLE} ADD COLUMN status TEXT NOT NULL DEFAULT 'queued'",
		f"ALTER TABLE {TABLE} ADD COLUMN destination_type TEXT NOT NULL DEFAULT 'local'",
		f"ALTER TABLE {TABLE} ADD COLUMN folder_type TEXT NOT NULL DEFAULT 'checkpoints'",
		f"ALTER TABLE {TABLE} ADD COLUMN model_id TEXT NOT NULL DEFAULT ''",
		f"ALTER TABLE {TABLE} ADD COLUMN model_version_id TEXT NOT NULL DEFAULT ''",
		f"ALTER TABLE {TABLE} ADD COLUMN repo_id TEXT NOT NULL DEFAULT ''",
		f"ALTER TABLE {TABLE} ADD COLUMN filename TEXT NOT NULL DEFAULT ''",
		f"ALTER TABLE {TABLE} ADD COLUMN subfolder TEXT NOT NULL DEFAULT ''",
		f"ALTER TABLE {TABLE} ADD COLUMN civitai_host TEXT NOT NULL DEFAULT ''",
		f"ALTER TABLE {TABLE} ADD COLUMN local_path TEXT NOT NULL DEFAULT ''",
		f"ALTER TABLE {TABLE} ADD COLUMN saved_relpath TEXT NOT NULL DEFAULT ''",
		f"ALTER TABLE {TABLE} ADD COLUMN description TEXT NOT NULL DEFAULT ''",
		f"ALTER TABLE {TABLE} ADD COLUMN trigger_words TEXT NOT NULL DEFAULT ''",
		f"ALTER TABLE {TABLE} ADD COLUMN bytes_done REAL NOT NULL DEFAULT 0",
		f"ALTER TABLE {TABLE} ADD COLUMN total_bytes REAL",
		f"ALTER TABLE {TABLE} ADD COLUMN error TEXT NOT NULL DEFAULT ''",
		f"ALTER TABLE {TABLE} ADD COLUMN created_at REAL NOT NULL DEFAULT 0",
		f"ALTER TABLE {TABLE} ADD COLUMN updated_at REAL NOT NULL DEFAULT 0",
		f"ALTER TABLE {TABLE} ADD COLUMN finished_at REAL NOT NULL DEFAULT 0",
		f"ALTER TABLE {TABLE} ADD COLUMN extra_json TEXT NOT NULL DEFAULT ''",
	)
	for sql in migrations:
		try:
			conn.execute(sql)
		except Exception:
			pass


def _row_to_dict(row: tuple | dict | None) -> dict[str, Any] | None:
	if row is None:
		return None
	if isinstance(row, dict):
		data = dict(row)
	else:
		data = dict(zip(_COLUMNS, row, strict=False))
	tw = data.get("trigger_words") or ""
	if isinstance(tw, str) and tw.startswith("["):
		try:
			data["trigger_words_list"] = json.loads(tw)
		except Exception:
			data["trigger_words_list"] = [t.strip() for t in tw.split(",") if t.strip()]
	elif isinstance(tw, str) and tw:
		data["trigger_words_list"] = [t.strip() for t in tw.split(",") if t.strip()]
	else:
		data["trigger_words_list"] = []
	extra = data.get("extra_json") or ""
	if isinstance(extra, str) and extra.startswith("{"):
		try:
			data["extra"] = json.loads(extra)
		except Exception:
			data["extra"] = {}
	else:
		data["extra"] = {}
	return data


class _SqliteHistoryStore:
	def __init__(self, conn):
		self._conn = conn

	def upsert(self, record: dict[str, Any]) -> bool:
		now = time.time()
		job_id = (record.get("job_id") or "").strip()
		if not job_id:
			return False
		user_id = (record.get("user_id") or "").strip()
		trigger = record.get("trigger_words")
		if isinstance(trigger, (list, tuple)):
			trigger_s = json.dumps(list(trigger))
		else:
			trigger_s = str(trigger or "")
		extra = record.get("extra_json")
		if isinstance(extra, dict):
			extra_s = json.dumps(extra)
		else:
			extra_s = str(extra or "")
		vals = {
			"job_id": job_id,
			"user_id": user_id,
			"username": str(record.get("username") or ""),
			"source": str(record.get("source") or ""),
			"status": str(record.get("status") or "queued"),
			"destination_type": str(record.get("destination_type") or "local"),
			"folder_type": str(record.get("folder_type") or "checkpoints"),
			"model_id": str(record.get("model_id") or ""),
			"model_version_id": str(record.get("model_version_id") or ""),
			"repo_id": str(record.get("repo_id") or ""),
			"filename": str(record.get("filename") or ""),
			"subfolder": str(record.get("subfolder") or ""),
			"civitai_host": str(record.get("civitai_host") or ""),
			"local_path": str(record.get("local_path") or ""),
			"saved_relpath": str(record.get("saved_relpath") or ""),
			"description": str(record.get("description") or ""),
			"trigger_words": trigger_s,
			"bytes_done": float(record.get("bytes_done") or 0),
			"total_bytes": record.get("total_bytes"),
			"error": str(record.get("error") or ""),
			"created_at": float(record.get("created_at") or now),
			"updated_at": float(record.get("updated_at") or now),
			"finished_at": float(record.get("finished_at") or 0),
			"extra_json": extra_s,
		}
		cols = ", ".join(_COLUMNS)
		placeholders = ", ".join(["?"] * len(_COLUMNS))
		updates = ", ".join(f"{c}=excluded.{c}" for c in _COLUMNS if c != "job_id")
		self._conn.execute(
			f"""
            INSERT INTO {TABLE} ({cols}) VALUES ({placeholders})
            ON CONFLICT(job_id) DO UPDATE SET {updates}
            """,
			tuple(vals[c] for c in _COLUMNS),
		)
		self._conn.commit()
		return True

	def get(self, job_id: str) -> dict[str, Any] | None:
		cur = self._conn.execute(
			f"SELECT {', '.join(_COLUMNS)} FROM {TABLE} WHERE job_id = ?",
			((job_id or "").strip(),),
		)
		row = cur.fetchone()
		return _row_to_dict(row)

	def list_for_user(
		self,
		user_id: str,
		*,
		status: str | None = None,
		limit: int = 100,
		include_all_users: bool = False,
	) -> list[dict[str, Any]]:
		lim = max(1, min(int(limit or 100), 500))
		if include_all_users:
			if status:
				cur = self._conn.execute(
					f"""
                    SELECT {', '.join(_COLUMNS)} FROM {TABLE}
                    WHERE status = ?
                    ORDER BY created_at DESC LIMIT ?
                    """,
					(status, lim),
				)
			else:
				cur = self._conn.execute(
					f"""
                    SELECT {', '.join(_COLUMNS)} FROM {TABLE}
                    ORDER BY created_at DESC LIMIT ?
                    """,
					(lim,),
				)
		else:
			uid = (user_id or "").strip()
			if status:
				cur = self._conn.execute(
					f"""
                    SELECT {', '.join(_COLUMNS)} FROM {TABLE}
                    WHERE user_id = ? AND status = ?
                    ORDER BY created_at DESC LIMIT ?
                    """,
					(uid, status, lim),
				)
			else:
				cur = self._conn.execute(
					f"""
                    SELECT {', '.join(_COLUMNS)} FROM {TABLE}
                    WHERE user_id = ?
                    ORDER BY created_at DESC LIMIT ?
                    """,
					(uid, lim),
				)
		return [_row_to_dict(r) for r in cur.fetchall() if r]

	def list_resumable(self, user_id: str | None = None) -> list[dict[str, Any]]:
		statuses = ("failed", "interrupted", "queued", "running")
		placeholders = ", ".join("?" * len(statuses))
		if user_id:
			cur = self._conn.execute(
				f"""
                SELECT {', '.join(_COLUMNS)} FROM {TABLE}
                WHERE user_id = ? AND status IN ({placeholders})
                ORDER BY updated_at DESC
                """,
				(user_id, *statuses),
			)
		else:
			cur = self._conn.execute(
				f"""
                SELECT {', '.join(_COLUMNS)} FROM {TABLE}
                WHERE status IN ({placeholders})
                ORDER BY updated_at DESC
                """,
				statuses,
			)
		return [_row_to_dict(r) for r in cur.fetchall() if r]

	def update_fields(self, job_id: str, **fields: Any) -> bool:
		rec = self.get(job_id)
		if not rec:
			return False
		rec.update(fields)
		rec["updated_at"] = time.time()
		return self.upsert(rec)


_store: _SqliteHistoryStore | None = None


def get_model_download_history_store(config: dict):
	"""Singleton history store on the same DB as users."""
	global _store
	if _store is not None:
		return _store
	backend = (config.get("backend") or "sqlite").lower()
	# History is SQLite-oriented for this release; other backends fall back to sqlite path.
	try:
		from ..constants import SECRET_KEY
	except ImportError:
		SECRET_KEY = ""
	_store = _get_sqlite_store(
		config.get("sqlite_path", "users/users.db"),
		secret_key=SECRET_KEY,
		encryption_level=config.get("encryption_level", ""),
	)
	return _store


def reset_model_download_history_store() -> None:
	global _store
	_store = None


def extract_civitai_metadata(model_payload: dict | None, version_payload: dict | None = None) -> dict[str, Any]:
	"""Normalize description + trainedWords from CivitAI model/version JSON."""
	description = ""
	triggers: list[str] = []
	model_id = ""
	model_name = ""
	version_id = ""
	if isinstance(model_payload, dict):
		description = str(model_payload.get("description") or "")[:4000]
		model_id = str(model_payload.get("id") or "")
		model_name = str(model_payload.get("name") or "")
		# Some responses nest version trainedWords only on versions
		for ver in model_payload.get("modelVersions") or []:
			if not isinstance(ver, dict):
				continue
			words = ver.get("trainedWords") or []
			if isinstance(words, list):
				for w in words:
					s = str(w).strip()
					if s and s not in triggers:
						triggers.append(s)
	if isinstance(version_payload, dict):
		version_id = str(version_payload.get("id") or "")
		words = version_payload.get("trainedWords") or []
		if isinstance(words, list):
			for w in words:
				s = str(w).strip()
				if s and s not in triggers:
					triggers.append(s)
		if not description:
			description = str(version_payload.get("description") or "")[:4000]
		model = version_payload.get("model")
		if isinstance(model, dict):
			if not model_id:
				model_id = str(model.get("id") or "")
			if not model_name:
				model_name = str(model.get("name") or "")
			if not description:
				description = str(model.get("description") or "")[:4000]
	return {
		"description": description,
		"trigger_words": triggers,
		"model_id": model_id,
		"model_name": model_name,
		"model_version_id": version_id,
	}
