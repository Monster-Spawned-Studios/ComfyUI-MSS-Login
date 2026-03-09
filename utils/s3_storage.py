"""Compatibility wrapper for filesystem-backed S3 operations in `utils.s3_mounter`."""

from __future__ import annotations

import re
from typing import Optional

from .s3_mounter import get_s3_manager, init_s3_manager

_s3_client = None


class S3StorageClient:
    """Adapter that forwards S3 operations to the consolidated S3 runtime."""

    def __init__(self, manager):
        self._manager = manager

    def upload_file(self, local_path: str, s3_key: str) -> dict:
        return self._manager.upload_file(local_path, s3_key)

    def download_file(self, s3_key: str, local_path: str) -> str:
        return self._manager.download_file(s3_key, local_path)

    def list_objects(self, prefix: str = "", max_keys: int = 1000) -> list[dict]:
        return self._manager.list_objects(prefix=prefix, max_keys=max_keys)

    def delete_object(self, s3_key: str) -> bool:
        return self._manager.delete_object(s3_key)

    def test_connection(self) -> dict:
        return self._manager.test_connection()


def get_s3_client(config: Optional[dict] = None) -> S3StorageClient:
    del config
    global _s3_client
    if _s3_client is not None:
        return _s3_client
    mgr = get_s3_manager()
    if mgr is None:
        from ..constants import DATA_DIR

        mgr = init_s3_manager(DATA_DIR)
    _s3_client = S3StorageClient(mgr)
    return _s3_client


def reset_s3_client() -> None:
    global _s3_client
    _s3_client = None


def get_s3_provider_type(endpoint_url: str) -> str:
    if not endpoint_url:
        return "aws"
    url = endpoint_url.lower().strip()
    if re.search(r"backblaze", url) or re.search(r"\.backblazeb2\.com", url):
        return "backblaze"
    if re.search(r"\.amazonaws\.com", url) or not url:
        return "aws"
    return "generic"
