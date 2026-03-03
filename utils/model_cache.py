# --- START OF FILE utils/model_cache.py ---
"""
Cache of available ComfyUI model folders and item names in the same DB as users/shared_items.
Table: cached_models (folder, item_name, updated_at). Refresh from folder_paths on startup or via admin API.
"""

import time
from pathlib import Path
from typing import Optional

TABLE = "cached_models"

# Fallback folder set when folder_paths is unavailable or has no folder_names_and_paths
# Includes ComfyUI Model Library folders: ultralytics, mmdets, sams, classifiers, configs.
ASSET_FOLDERS_FALLBACK = frozenset(
    {
        "checkpoints",
        "loras",
        "vae",
        "text_encoders",
        "clip",
        "embeddings",
        "diffusion_models",
        "unet",
        "clip_vision",
        "style_models",
        "controlnet",
        "gligen",
        "upscale_models",
        "latent_upscale_models",
        "hypernetworks",
        "vae_approx",
        "diffusers",
        "photomaker",
        "model_patches",
        "audio_encoders",
        "classifiers",
        "configs",
        "ultralytics_bbox",
        "ultralytics_segm",
        "ultralytics",
        "mmdets_bbox",
        "mmdets_segm",
        "mmdets",
        "sams",
    }
)


def _get_sqlite_store(
    db_path: str, secret_key: str = "", encryption_level: str = ""
) -> "_SqliteModelCache":
    """Open unified SQLite DB (same path/key as users_db); supports SQLCipher when encryption_level set."""
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
            folder TEXT NOT NULL,
            item_name TEXT NOT NULL,
            updated_at REAL NOT NULL,
            PRIMARY KEY (folder, item_name)
        )
        """
    )
    conn.execute(f"CREATE INDEX IF NOT EXISTS idx_{TABLE}_folder ON {TABLE} (folder)")
    conn.commit()
    return _SqliteModelCache(conn)


def _get_postgres_store(
    host: str, port: int, database: str, user: str, password: str
) -> "_PostgresModelCache":
    try:
        import psycopg2
    except ImportError:
        raise RuntimeError("PostgreSQL requires psycopg2-binary")
    conn = psycopg2.connect(
        host=host, port=port, dbname=database, user=user, password=password
    )
    cur = conn.cursor()
    cur.execute(
        f"""
        CREATE TABLE IF NOT EXISTS {TABLE} (
            folder TEXT NOT NULL,
            item_name TEXT NOT NULL,
            updated_at REAL NOT NULL,
            PRIMARY KEY (folder, item_name)
        )
        """
    )
    cur.execute(f"CREATE INDEX IF NOT EXISTS idx_{TABLE}_folder ON {TABLE} (folder)")
    conn.commit()
    cur.close()
    return _PostgresModelCache(conn)


def _get_mysql_store(
    host: str, port: int, database: str, user: str, password: str
) -> "_MySQLModelCache":
    try:
        import pymysql
    except ImportError:
        raise RuntimeError("MySQL requires pymysql; pip install pymysql")
    conn = pymysql.connect(
        host=host,
        port=port,
        user=user,
        password=password,
        database=database,
        charset="utf8mb4",
    )
    cur = conn.cursor()
    cur.execute(
        f"""
        CREATE TABLE IF NOT EXISTS {TABLE} (
            folder VARCHAR(255) NOT NULL,
            item_name VARCHAR(255) NOT NULL,
            updated_at DOUBLE NOT NULL,
            PRIMARY KEY (folder, item_name)
        )
        """
    )
    try:
        cur.execute(f"CREATE INDEX idx_{TABLE}_folder ON {TABLE} (folder)")
    except Exception:
        pass  # Index may already exist; MySQL has no IF NOT EXISTS for indexes
    conn.commit()
    cur.close()
    return _MySQLModelCache(conn)


class _SqliteModelCache:
    def __init__(self, conn):
        self._conn = conn

    def refresh_from_folder_paths(self) -> tuple[list[str], int]:
        """
        Refresh cache from ComfyUI folder_paths. Returns (list of folder names, total item count).
        """
        try:
            import folder_paths

            folder_names = list(folder_paths.folder_names_and_paths.keys())
        except Exception:
            folder_names = list(ASSET_FOLDERS_FALLBACK)

        now = time.time()
        total = 0
        for folder in folder_names:
            try:
                import folder_paths

                names = folder_paths.get_filename_list(folder)
            except Exception:
                names = []
            for item_name in names:
                item_name = (item_name or "").strip()
                if not item_name:
                    continue
                try:
                    self._conn.execute(
                        f"INSERT INTO {TABLE} (folder, item_name, updated_at) VALUES (?, ?, ?) "
                        f"ON CONFLICT (folder, item_name) DO UPDATE SET updated_at = ?",
                        (folder, item_name, now, now),
                    )
                    total += 1
                except Exception:
                    self._conn.rollback()
                    raise
        self._conn.commit()
        # Prune items in folders that no longer exist in folder_paths
        if folder_names:
            self._conn.execute(
                f"DELETE FROM {TABLE} WHERE folder NOT IN ({','.join('?' * len(folder_names))})",
                folder_names,
            )
            self._conn.commit()
        return (folder_names, total)

    def list_folders(self) -> list[str]:
        """Return distinct folder names from cache, ordered."""
        rows = self._conn.execute(
            f"SELECT DISTINCT folder FROM {TABLE} ORDER BY folder"
        ).fetchall()
        return [r[0] for r in rows]

    def list_items(self, folder: str) -> list[str]:
        """Return item names in folder from cache, ordered."""
        rows = self._conn.execute(
            f"SELECT item_name FROM {TABLE} WHERE folder = ? ORDER BY item_name",
            (folder,),
        ).fetchall()
        return [r[0] for r in rows]

    def is_empty(self) -> bool:
        """True if cache has no rows."""
        row = self._conn.execute(f"SELECT 1 FROM {TABLE} LIMIT 1").fetchone()
        return row is None


class _PostgresModelCache:
    def __init__(self, conn):
        self._conn = conn

    def refresh_from_folder_paths(self) -> tuple[list[str], int]:
        """
        Refresh cache from ComfyUI folder_paths. Returns (list of folder names, total item count).
        """
        try:
            import folder_paths

            folder_names = list(folder_paths.folder_names_and_paths.keys())
        except Exception:
            folder_names = list(ASSET_FOLDERS_FALLBACK)

        now = time.time()
        total = 0
        for folder in folder_names:
            try:
                import folder_paths

                names = folder_paths.get_filename_list(folder)
            except Exception:
                names = []
            for item_name in names:
                item_name = (item_name or "").strip()
                if not item_name:
                    continue
                try:
                    cur = self._conn.cursor()
                    cur.execute(
                        f"INSERT INTO {TABLE} (folder, item_name, updated_at) VALUES (%s, %s, %s) "
                        f"ON CONFLICT (folder, item_name) DO UPDATE SET updated_at = EXCLUDED.updated_at",
                        (folder, item_name, now),
                    )
                    total += 1
                    cur.close()
                except Exception:
                    self._conn.rollback()
                    raise
        self._conn.commit()
        if folder_names:
            cur = self._conn.cursor()
            placeholders = ",".join("%s" for _ in folder_names)
            cur.execute(
                f"DELETE FROM {TABLE} WHERE folder NOT IN ({placeholders})",
                tuple(folder_names),
            )
            cur.close()
            self._conn.commit()
        return (folder_names, total)

    def list_folders(self) -> list[str]:
        """Return distinct folder names from cache, ordered."""
        cur = self._conn.cursor()
        cur.execute(f"SELECT DISTINCT folder FROM {TABLE} ORDER BY folder")
        rows = cur.fetchall()
        cur.close()
        return [r[0] for r in rows]

    def list_items(self, folder: str) -> list[str]:
        """Return item names in folder from cache, ordered."""
        cur = self._conn.cursor()
        cur.execute(
            f"SELECT item_name FROM {TABLE} WHERE folder = %s ORDER BY item_name",
            (folder,),
        )
        rows = cur.fetchall()
        cur.close()
        return [r[0] for r in rows]

    def is_empty(self) -> bool:
        """True if cache has no rows."""
        cur = self._conn.cursor()
        cur.execute(f"SELECT 1 FROM {TABLE} LIMIT 1")
        row = cur.fetchone()
        cur.close()
        return row is None


class _MySQLModelCache:
    def __init__(self, conn):
        self._conn = conn

    def refresh_from_folder_paths(self) -> tuple[list[str], int]:
        try:
            import folder_paths
            folder_names = list(folder_paths.folder_names_and_paths.keys())
        except Exception:
            folder_names = list(ASSET_FOLDERS_FALLBACK)
        now = time.time()
        total = 0
        for folder in folder_names:
            try:
                import folder_paths
                names = folder_paths.get_filename_list(folder)
            except Exception:
                names = []
            for item_name in names:
                item_name = (item_name or "").strip()
                if not item_name:
                    continue
                try:
                    cur = self._conn.cursor()
                    cur.execute(
                        "INSERT INTO {} (folder, item_name, updated_at) VALUES (%s, %s, %s) "
                        "ON DUPLICATE KEY UPDATE updated_at = VALUES(updated_at)".format(
                            TABLE
                        ),
                        (folder, item_name, now),
                    )
                    total += 1
                    cur.close()
                except Exception:
                    self._conn.rollback()
                    raise
        self._conn.commit()
        if folder_names:
            cur = self._conn.cursor()
            placeholders = ",".join("%s" for _ in folder_names)
            cur.execute(
                f"DELETE FROM {TABLE} WHERE folder NOT IN ({placeholders})",
                tuple(folder_names),
            )
            cur.close()
            self._conn.commit()
        return (folder_names, total)

    def list_folders(self) -> list[str]:
        cur = self._conn.cursor()
        cur.execute(f"SELECT DISTINCT folder FROM {TABLE} ORDER BY folder")
        rows = cur.fetchall()
        cur.close()
        return [r[0] for r in rows]

    def list_items(self, folder: str) -> list[str]:
        cur = self._conn.cursor()
        cur.execute(
            f"SELECT item_name FROM {TABLE} WHERE folder = %s ORDER BY item_name",
            (folder,),
        )
        rows = cur.fetchall()
        cur.close()
        return [r[0] for r in rows]

    def is_empty(self) -> bool:
        cur = self._conn.cursor()
        cur.execute(f"SELECT 1 FROM {TABLE} LIMIT 1")
        row = cur.fetchone()
        cur.close()
        return row is None


_store: Optional[
    _SqliteModelCache | _PostgresModelCache | _MySQLModelCache
] = None


def get_model_cache(config: dict):
    """Get singleton model cache using same config as users_db (backend, sqlite_path, postgres_*, mysql_*, encryption_level)."""
    global _store
    if _store is not None:
        return _store
    backend = (config.get("backend") or "sqlite").lower()
    if backend == "postgresql":
        _store = _get_postgres_store(
            config.get("postgres_host", "localhost"),
            int(config.get("postgres_port", 5432)),
            config.get("postgres_database", "mss-login"),
            config.get("postgres_user", "mss-login"),
            config.get("postgres_password", ""),
        )
    elif backend == "mysql":
        _store = _get_mysql_store(
            config.get("mysql_host", "localhost"),
            int(config.get("mysql_port", 3306)),
            config.get("mysql_database", "mss_login"),
            config.get("mysql_user", "mss_login"),
            config.get("mysql_password", ""),
        )
    else:
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


def reset_model_cache() -> None:
    global _store
    _store = None
