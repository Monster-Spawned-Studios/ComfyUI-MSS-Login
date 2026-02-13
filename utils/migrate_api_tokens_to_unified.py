# --- START OF FILE utils/migrate_api_tokens_to_unified.py ---
"""
One-time migration: copy api_tokens table from a separate SQLite file into the unified DB.
Runs when backend is sqlite, encryption is off, and config still has an old api_token_store path.
"""
import os
from pathlib import Path
from typing import Optional


def migrate_api_tokens_if_needed(
    unified_path: str,
    old_api_tokens_path: Optional[str],
    config_path: str,
) -> bool:
    """
    If old_api_tokens_path is set and differs from unified_path, and both files exist,
    copy api_tokens rows from old DB into unified DB (which must already have api_tokens table).
    Returns True if migration was performed. Uses standard sqlite3 (no encryption).
    """
    if not old_api_tokens_path or not unified_path or old_api_tokens_path == unified_path:
        return False
    unified_path = str(Path(unified_path).resolve())
    old_path = str(Path(old_api_tokens_path).resolve())
    if not os.path.isfile(old_path) or not os.path.isfile(unified_path):
        return False
    try:
        import sqlite3
    except ImportError:
        return False
    try:
        conn_old = sqlite3.connect(old_path)
        rows = conn_old.execute(
            "SELECT token_hash, user_id, username, expires_iso FROM api_tokens"
        ).fetchall()
        conn_old.close()
        if not rows:
            return False
        conn_new = sqlite3.connect(unified_path)
        conn_new.execute(
            "CREATE TABLE IF NOT EXISTS api_tokens "
            "(token_hash TEXT PRIMARY KEY, user_id TEXT NOT NULL, username TEXT NOT NULL, expires_iso TEXT NOT NULL)"
        )
        for row in rows:
            try:
                conn_new.execute(
                    "INSERT OR IGNORE INTO api_tokens (token_hash, user_id, username, expires_iso) VALUES (?, ?, ?, ?)",
                    row,
                )
            except Exception:
                pass
        conn_new.commit()
        conn_new.close()
        return True
    except Exception:
        return False
