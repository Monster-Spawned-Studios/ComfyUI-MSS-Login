"""Pytest-shaped wrapper for workflow metadata tests."""

from __future__ import annotations

import importlib.util
import os
import sys

_TESTS_DIR = os.path.dirname(os.path.abspath(__file__))
_RUNNER = os.path.join(_TESTS_DIR, "run_workflow_metadata_tests.py")


def _load_runner():
	spec = importlib.util.spec_from_file_location("run_workflow_metadata_tests", _RUNNER)
	mod = importlib.util.module_from_spec(spec)
	assert spec and spec.loader
	sys.modules[spec.name] = mod
	spec.loader.exec_module(mod)
	return mod


def test_workflow_metadata_suite():
	mod = _load_runner()
	rc = mod.run_tests()
	assert rc == 0
