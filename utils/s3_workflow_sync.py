"""Compatibility wrapper for workflow sync now implemented in `utils.s3_mounter`."""

from . import s3_mounter as _s3_mounter

S3WorkflowSync = _s3_mounter.S3MountManager
get_workflow_sync = _s3_mounter.get_workflow_sync
init_workflow_sync = _s3_mounter.init_workflow_sync

__all__ = ["S3WorkflowSync", "get_workflow_sync", "init_workflow_sync"]
