# --- START OF FILE utils/s3_storage.py ---
"""
S3-compatible cloud storage client (experimental).

Provider-agnostic wrapper around boto3 that works with Amazon S3, Backblaze B2,
MinIO, and any other S3-compatible endpoint. All public methods gate on the
EXPERIMENTAL_FEATURES flag and raise RuntimeError when it is disabled.

Credentials are resolved exclusively from environment variables (never from
config files) to follow the project's security conventions.
"""

import os
from typing import Optional

_FEATURE_GATE_MSG = (
	"S3 cloud storage is an experimental feature. "
	"Enable it by setting EXPERIMENTAL_FEATURES=true and configuring s3_storage in config.json."
)


def _require_experimental() -> None:
	"""Raise if the experimental features flag is off."""
	try:
		from .. import constants as c

		if not c.EXPERIMENTAL_FEATURES:
			raise RuntimeError(_FEATURE_GATE_MSG)
	except ImportError:
		raise RuntimeError(_FEATURE_GATE_MSG)


class S3StorageClient:
	"""Thin wrapper around a boto3 S3 client.

	Supports Amazon S3 (standard endpoint) and S3-compatible providers like
	Backblaze B2 (``endpoint_url = "https://s3.<region>.backblazeb2.com"``) or
	MinIO (``endpoint_url = "http://minio-host:9000"``).
	"""

	def __init__(self, config: dict):
		_require_experimental()

		self._bucket = config.get("bucket_name") or ""
		self._prefix = config.get("prefix") or ""
		self._region = config.get("region") or ""
		self._endpoint_url = config.get("endpoint_url") or ""
		self._access_key = config.get("access_key_id") or ""
		self._secret_key = config.get("secret_access_key") or ""

		if not self._bucket:
			raise ValueError("S3 bucket_name is required but not configured.")
		if not self._access_key or not self._secret_key:
			raise ValueError(
				"S3 access_key_id and secret_access_key must be set via environment variables."
			)

		try:
			import boto3
			from botocore.config import Config as BotoConfig
		except ImportError:
			raise RuntimeError(
				"boto3 is required for S3 storage. Install with: pip install boto3"
			)

		kwargs: dict = {
			"aws_access_key_id": self._access_key,
			"aws_secret_access_key": self._secret_key,
			"config": BotoConfig(
				signature_version="s3v4",
				retries={"max_attempts": 3, "mode": "standard"},
			),
		}
		if self._region:
			kwargs["region_name"] = self._region
		if self._endpoint_url:
			kwargs["endpoint_url"] = self._endpoint_url

		self._client = boto3.client("s3", **kwargs)

	def _full_key(self, key: str) -> str:
		"""Prepend the configured prefix to *key*."""
		if self._prefix:
			return self._prefix.rstrip("/") + "/" + key.lstrip("/")
		return key

	# ------------------------------------------------------------------
	# Public API
	# ------------------------------------------------------------------

	def upload_file(self, local_path: str, s3_key: str) -> dict:
		"""Upload a local file to the bucket.

		Returns a dict with ``bucket``, ``key``, and ``size`` on success.
		"""
		_require_experimental()
		if not os.path.isfile(local_path):
			raise FileNotFoundError(f"Local file not found: {local_path}")
		full_key = self._full_key(s3_key)
		self._client.upload_file(local_path, self._bucket, full_key)
		size = os.path.getsize(local_path)
		return {"bucket": self._bucket, "key": full_key, "size": size}

	def download_file(self, s3_key: str, local_path: str) -> str:
		"""Download an object from the bucket to a local path.

		Creates parent directories as needed. Returns the local path.
		"""
		_require_experimental()
		full_key = self._full_key(s3_key)
		os.makedirs(os.path.dirname(local_path) or ".", exist_ok=True)
		self._client.download_file(self._bucket, full_key, local_path)
		return local_path

	def list_objects(self, prefix: str = "", max_keys: int = 1000) -> list[dict]:
		"""List objects under *prefix* (relative to the configured global prefix).

		Returns a list of dicts with ``key``, ``size``, and ``last_modified``.
		"""
		_require_experimental()
		full_prefix = self._full_key(prefix) if prefix else self._prefix
		paginator = self._client.get_paginator("list_objects_v2")
		results: list[dict] = []
		for page in paginator.paginate(
			Bucket=self._bucket,
			Prefix=full_prefix,
			PaginationConfig={"MaxItems": max_keys},
		):
			for obj in page.get("Contents", []):
				results.append(
					{
						"key": obj["Key"],
						"size": obj.get("Size", 0),
						"last_modified": obj["LastModified"].isoformat()
						if obj.get("LastModified")
						else "",
					}
				)
		return results

	def delete_object(self, s3_key: str) -> bool:
		"""Delete an object from the bucket. Returns True on success."""
		_require_experimental()
		full_key = self._full_key(s3_key)
		self._client.delete_object(Bucket=self._bucket, Key=full_key)
		return True

	def head_object(self, s3_key: str) -> dict | None:
		"""Return metadata for an object, or None if it does not exist."""
		_require_experimental()
		full_key = self._full_key(s3_key)
		try:
			resp = self._client.head_object(Bucket=self._bucket, Key=full_key)
			return {
				"key": full_key,
				"size": resp.get("ContentLength", 0),
				"content_type": resp.get("ContentType", ""),
				"last_modified": resp["LastModified"].isoformat()
				if resp.get("LastModified")
				else "",
			}
		except self._client.exceptions.ClientError as exc:
			if exc.response["Error"]["Code"] == "404":
				return None
			raise

	def generate_presigned_url(self, s3_key: str, expires_in: int = 3600) -> str:
		"""Generate a presigned GET URL for the object.

		``expires_in`` is the URL lifetime in seconds (default 1 hour).
		"""
		_require_experimental()
		full_key = self._full_key(s3_key)
		return self._client.generate_presigned_url(
			"get_object",
			Params={"Bucket": self._bucket, "Key": full_key},
			ExpiresIn=expires_in,
		)

	def test_connection(self) -> dict:
		"""Verify that the bucket is reachable. Returns bucket location info."""
		_require_experimental()
		try:
			self._client.head_bucket(Bucket=self._bucket)
			return {
				"ok": True,
				"bucket": self._bucket,
				"endpoint": self._endpoint_url or "amazonaws.com",
			}
		except Exception as exc:
			return {"ok": False, "error": str(exc)}


# ---------------------------------------------------------------------------
# Singleton factory
# ---------------------------------------------------------------------------

_s3_client: Optional[S3StorageClient] = None


def get_s3_client(config: Optional[dict] = None) -> S3StorageClient:
	"""Return the singleton S3StorageClient, building it from *config* on first call.

	*config* should be the ``S3_STORAGE_CONFIG`` dict from constants.
	"""
	global _s3_client
	if _s3_client is not None:
		return _s3_client

	if config is None:
		try:
			from ..constants import S3_STORAGE_CONFIG

			config = S3_STORAGE_CONFIG
		except ImportError:
			config = {}

	if not config.get("enabled"):
		raise RuntimeError(
			"S3 storage is not enabled. Set s3_storage.enabled = true in config.json."
		)

	_s3_client = S3StorageClient(config)
	return _s3_client


def reset_s3_client() -> None:
	"""Clear the singleton so the next ``get_s3_client()`` rebuilds from current config."""
	global _s3_client
	_s3_client = None
