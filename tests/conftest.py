"""
Pytest bootstrap for ComfyUI-free unit tests.

Stub ComfyUI host modules before any project package import can require them.
"""

from __future__ import annotations

import sys
import types


def pytest_configure(config):
	if "folder_paths" not in sys.modules:
		fp = types.ModuleType("folder_paths")
		fp.folder_names_and_paths = {}
		fp.get_folder_paths = lambda folder: []
		fp.get_filename_list = lambda folder: []
		fp.get_full_path = lambda folder, name: None
		fp.map_legacy = lambda n: n
		sys.modules["folder_paths"] = fp
	if "server" not in sys.modules:
		srv = types.ModuleType("server")

		class PromptServer:
			instance = None

		srv.PromptServer = PromptServer
		sys.modules["server"] = srv
