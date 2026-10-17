"""
Isolation plain-name resolution tests (pytest-shaped module).

The project root `__init__.py` is a ComfyUI extension entrypoint; pytest collection
of the package tree fails without ComfyUI. Prefer the standalone runner (wired in
`tests/run_ci.py`):

  .venv/bin/python tests/run_isolation_plain_names_tests.py

This file documents the suite and can be executed directly:

  .venv/bin/python tests/test_isolation_plain_names.py
"""

from __future__ import annotations

import importlib.util
import os
import sys

_TESTS_DIR = os.path.dirname(os.path.abspath(__file__))
_RUNNER = os.path.join(_TESTS_DIR, "run_isolation_plain_names_tests.py")


def _load_runner():
	spec = importlib.util.spec_from_file_location("run_isolation_plain_names_tests", _RUNNER)
	mod = importlib.util.module_from_spec(spec)
	assert spec and spec.loader
	sys.modules["run_isolation_plain_names_tests"] = mod
	spec.loader.exec_module(mod)
	return mod


def test_isolation_plain_names_suite():
	"""Pytest-discoverable name; run via standalone runner in CI."""
	runner = _load_runner()
	assert runner.run_tests() == 0


if __name__ == "__main__":
	sys.exit(_load_runner().run_tests())
