#!/usr/bin/env python3
"""
Debug entrypoint for VS Code / Cursor.

Reads the ComfyUI path written by ensure_comfyui.py, then runs that install's
main.py with the remaining CLI args. This lets launch.json avoid re-prompting
for the path (inputs live on the Prepare preLaunchTask only).
"""

from __future__ import annotations

import os
import runpy
import sys
from pathlib import Path


def main() -> None:
	repo = Path(__file__).resolve().parent.parent.parent
	marker = repo / ".vscode" / ".comfyui-test-path"
	if not marker.is_file():
		raise SystemExit(
			f"[launch_comfyui_debug] Missing {marker}. "
			"Run the 'Prepare ComfyUI for MSS-Login' task first "
			"(or use a launch config that has it as preLaunchTask)."
		)
	comfy_path = Path(marker.read_text(encoding="utf-8").strip()).expanduser()
	main_py = comfy_path / "main.py"
	if not main_py.is_file():
		raise SystemExit(f"[launch_comfyui_debug] No main.py under {comfy_path}")

	# Prefer the ComfyUI venv interpreter when this wrapper was started with another Python.
	venv_unix = comfy_path / ".venv" / "bin" / "python"
	venv_win = comfy_path / ".venv" / "Scripts" / "python.exe"
	# We stay in-process so debugpy breakpoints in custom_nodes keep working.
	os.chdir(comfy_path)
	sys.path.insert(0, str(comfy_path))
	# Forward args after this script's name
	sys.argv = [str(main_py), *sys.argv[1:]]
	print(f"[launch_comfyui_debug] Running {main_py}", flush=True)
	print(f"[launch_comfyui_debug] cwd={comfy_path}", flush=True)
	if venv_unix.is_file() or venv_win.is_file():
		print(
			"[launch_comfyui_debug] Tip: select ComfyUI's .venv as the Python interpreter "
			"if imports fail.",
			flush=True,
		)
	runpy.run_path(str(main_py), run_name="__main__")


if __name__ == "__main__":
	main()
