"""Pytest-shaped wrapper for object_info combo mapping tests."""

from __future__ import annotations

import importlib.util
import os
import sys

_TESTS_DIR = os.path.dirname(os.path.abspath(__file__))
_RUNNER = os.path.join(_TESTS_DIR, "run_object_info_combo_mapping_tests.py")


def _load_runner():
	spec = importlib.util.spec_from_file_location("run_object_info_combo_mapping_tests", _RUNNER)
	mod = importlib.util.module_from_spec(spec)
	assert spec and spec.loader
	sys.modules[spec.name] = mod
	spec.loader.exec_module(mod)
	return mod


def test_object_info_combo_mapping_suite():
	mod = _load_runner()
	rc = mod.run_tests()
	assert rc == 0
