"""Compatibility wrapper for the consolidated `utils.s3_mounter` runtime."""

from . import s3_mounter as _s3_mounter

S3MountManager = _s3_mounter.S3MountManager
get_mount_manager = _s3_mounter.get_mount_manager
init_mount_manager = _s3_mounter.init_mount_manager

__all__ = ["S3MountManager", "get_mount_manager", "init_mount_manager"]
