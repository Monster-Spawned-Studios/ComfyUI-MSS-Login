#!/usr/bin/env python3
"""
Prepare a local ComfyUI workspace for debugging MSS-Login.

Configuration priority (highest first):
  1. CLI flags (--path / --auto-install / --flavor)
  2. Environment (COMFYUI_PATH, COMFYUI_AUTO_INSTALL, COMFYUI_INSTALL_FLAVOR)
  3. .vscode/comfyui-test.local.json (manual path + auto-install toggle)
  4. Defaults: <repo>/ComfyUI , auto-install=yes , flavor=cpu

If the resolved path has no main.py:
  - autoInstall true  -> install ComfyUI via comfy-cli (uv)
  - autoInstall false -> exit with an error

Also symlinks this repo into <ComfyUI>/custom_nodes/ComfyUI-MSS-Login.
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any


FLAVOR_FLAGS = {
	"cpu": ["--cpu"],
	"nvidia": ["--nvidia"],
	"amd": ["--amd"],
	"m-series": ["--m-series"],
}

LOCAL_SETTINGS_NAME = "comfyui-test.local.json"
EXAMPLE_SETTINGS_NAME = "comfyui-test.settings.example.json"


def _truthy(value: Any) -> bool:
	if isinstance(value, bool):
		return value
	if value is None:
		return False
	return str(value).strip().lower() in {"1", "true", "yes", "y", "on"}


def _run(cmd: list[str], *, cwd: Path | None = None, env: dict | None = None) -> None:
	print(f"[ensure_comfyui] $ {' '.join(cmd)}", flush=True)
	subprocess.run(cmd, cwd=str(cwd) if cwd else None, env=env, check=True)


def _resolve_uv(workspace: Path) -> list[str]:
	candidates = [
		workspace / ".venv" / "bin" / "uv",
		workspace / ".venv" / "Scripts" / "uv.exe",
		shutil.which("uv"),
	]
	for c in candidates:
		if not c:
			continue
		p = Path(c)
		if p.is_file() or (isinstance(c, str) and shutil.which(c)):
			return [str(c)]
	return [sys.executable, "-m", "uv"]


def _is_comfyui_install(path: Path) -> bool:
	return (path / "main.py").is_file()


def _expand_path(raw: str, workspace: Path) -> Path:
	text = (raw or "").strip()
	text = text.replace("${workspaceFolder}", str(workspace))
	text = text.replace("$workspaceFolder", str(workspace))
	text = os.path.expandvars(text)
	return Path(text).expanduser().resolve()


def _load_local_settings(vscode_dir: Path) -> dict[str, Any]:
	path = vscode_dir / LOCAL_SETTINGS_NAME
	if not path.is_file():
		return {}
	try:
		data = json.loads(path.read_text(encoding="utf-8"))
	except (OSError, json.JSONDecodeError) as exc:
		raise SystemExit(f"[ensure_comfyui] Invalid {path}: {exc}") from exc
	if not isinstance(data, dict):
		raise SystemExit(f"[ensure_comfyui] {path} must contain a JSON object.")
	print(f"[ensure_comfyui] Loaded settings from {path}", flush=True)
	return data


def _write_local_settings(vscode_dir: Path, settings: dict[str, Any]) -> Path:
	vscode_dir.mkdir(parents=True, exist_ok=True)
	path = vscode_dir / LOCAL_SETTINGS_NAME
	payload = {
		"comfyuiPath": settings["comfyuiPath"],
		"autoInstall": bool(settings["autoInstall"]),
		"installFlavor": settings["installFlavor"],
	}
	path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
	print(f"[ensure_comfyui] Wrote {path}", flush=True)
	return path


def _ensure_comfy_cli(uv_cmd: list[str], workspace: Path) -> None:
	_run([*uv_cmd, "sync", "--group", "comfyui"], cwd=workspace)


def _install_comfyui(
	uv_cmd: list[str],
	workspace: Path,
	comfy_path: Path,
	flavor: str,
) -> None:
	flags = FLAVOR_FLAGS.get(flavor)
	if flags is None:
		raise SystemExit(
			f"[ensure_comfyui] Unknown install flavor '{flavor}'. "
			f"Use one of: {', '.join(sorted(FLAVOR_FLAGS))}."
		)
	comfy_path.parent.mkdir(parents=True, exist_ok=True)
	_ensure_comfy_cli(uv_cmd, workspace)
	env = os.environ.copy()
	env["UV_PROJECT"] = str(workspace)
	_run(
		[
			*uv_cmd,
			"run",
			"--group",
			"comfyui",
			"comfy",
			"--workspace",
			str(comfy_path),
			"install",
			*flags,
			"--fast-deps",
		],
		cwd=workspace,
		env=env,
	)
	if not _is_comfyui_install(comfy_path):
		raise SystemExit(
			f"[ensure_comfyui] Install finished but {comfy_path / 'main.py'} is still missing."
		)


def _link_mss_login(comfy_path: Path, mss_src: Path) -> Path:
	custom_nodes = comfy_path / "custom_nodes"
	custom_nodes.mkdir(parents=True, exist_ok=True)
	target = custom_nodes / "ComfyUI-MSS-Login"
	alt = custom_nodes / "MSS-Login"

	try:
		if mss_src.resolve() in {target.resolve(), alt.resolve()}:
			print(f"[ensure_comfyui] Workspace is already the custom node at {mss_src}", flush=True)
			return mss_src
	except OSError:
		pass

	for existing in (target, alt):
		if existing.is_symlink() or existing.is_dir():
			try:
				if existing.resolve() == mss_src.resolve():
					print(f"[ensure_comfyui] MSS-Login already linked at {existing}", flush=True)
					return existing
			except OSError:
				pass

	if target.exists() or target.is_symlink():
		if target.is_symlink() or target.is_file():
			target.unlink()
		else:
			print(
				f"[ensure_comfyui] WARNING: {target} exists and is not this repo; "
				"linking as MSS-Login instead.",
				flush=True,
			)
			target = alt
			if target.exists() or target.is_symlink():
				if target.is_symlink() or target.is_file():
					target.unlink()
				else:
					raise SystemExit(
						f"[ensure_comfyui] Refusing to overwrite existing directory {target}."
					)

	try:
		target.symlink_to(mss_src, target_is_directory=True)
		print(f"[ensure_comfyui] Symlinked {target} -> {mss_src}", flush=True)
	except OSError as exc:
		print(
			f"[ensure_comfyui] Symlink failed ({exc}); copying instead (dev only).",
			flush=True,
		)
		if target.exists():
			shutil.rmtree(target)
		shutil.copytree(
			mss_src,
			target,
			ignore=shutil.ignore_patterns(
				".git",
				".venv",
				"__pycache__",
				".env",
				".env.keys",
				"ComfyUI",
				".local-mss-data",
				"*.log",
				"logs",
			),
			dirs_exist_ok=False,
		)
		print(f"[ensure_comfyui] Copied MSS-Login to {target}", flush=True)
	return target


def _write_resolved_path(workspace: Path, comfy_path: Path) -> None:
	out = workspace / ".vscode" / ".comfyui-test-path"
	out.parent.mkdir(parents=True, exist_ok=True)
	out.write_text(str(comfy_path.resolve()) + "\n", encoding="utf-8")
	print(f"[ensure_comfyui] Wrote resolved path to {out}", flush=True)


def _resolve_config(args: argparse.Namespace, workspace: Path, local: dict[str, Any]) -> tuple[Path, bool, str]:
	"""Merge CLI / env / local settings / defaults into (path, auto_install, flavor)."""
	# Path
	path_raw = (args.path or "").strip() or (os.environ.get("COMFYUI_PATH") or "").strip()
	if not path_raw:
		path_raw = str(local.get("comfyuiPath") or "").strip()
	if not path_raw:
		path_raw = str(workspace / "ComfyUI")
		print(f"[ensure_comfyui] No path configured; defaulting to {path_raw}", flush=True)

	# Auto-install toggle
	if args.auto_install is not None:
		auto_install = _truthy(args.auto_install)
	elif (os.environ.get("COMFYUI_AUTO_INSTALL") or "").strip():
		auto_install = _truthy(os.environ.get("COMFYUI_AUTO_INSTALL"))
	elif "autoInstall" in local:
		auto_install = _truthy(local.get("autoInstall"))
	else:
		auto_install = True

	# Flavor
	flavor = (args.flavor or "").strip() or (os.environ.get("COMFYUI_INSTALL_FLAVOR") or "").strip()
	if not flavor:
		flavor = str(local.get("installFlavor") or "cpu").strip()
	if flavor not in FLAVOR_FLAGS:
		raise SystemExit(
			f"[ensure_comfyui] Unknown flavor '{flavor}'. "
			f"Use one of: {', '.join(sorted(FLAVOR_FLAGS))}."
		)

	return _expand_path(path_raw, workspace), auto_install, flavor


def main() -> int:
	parser = argparse.ArgumentParser(description=__doc__)
	parser.add_argument(
		"--path",
		default=None,
		help="ComfyUI install directory (overrides local settings / env).",
	)
	parser.add_argument(
		"--auto-install",
		default=None,
		help="yes/no: generate ComfyUI if missing (overrides local settings / env).",
	)
	parser.add_argument(
		"--flavor",
		default=None,
		choices=sorted(FLAVOR_FLAGS),
		help="comfy-cli install flavor when auto-installing.",
	)
	parser.add_argument(
		"--mss-src",
		default=os.environ.get("MSS_LOGIN_SRC", "").strip(),
		help="Path to the MSS-Login repo (default: repo root).",
	)
	parser.add_argument(
		"--save-settings",
		action="store_true",
		help="Write resolved path/autoInstall/flavor to .vscode/comfyui-test.local.json and exit after prepare.",
	)
	parser.add_argument(
		"--write-settings-only",
		action="store_true",
		help="Only write .vscode/comfyui-test.local.json from --path/--auto-install/--flavor (no install/link).",
	)
	args = parser.parse_args()

	script_dir = Path(__file__).resolve().parent
	workspace = script_dir.parent.parent
	vscode_dir = workspace / ".vscode"
	mss_src = Path(args.mss_src).expanduser().resolve() if args.mss_src else workspace
	local = _load_local_settings(vscode_dir)

	# For --write-settings-only, require explicit values (from VS Code inputs)
	if args.write_settings_only:
		path_raw = (args.path or "").strip() or str(workspace / "ComfyUI")
		auto = True if args.auto_install is None else _truthy(args.auto_install)
		flavor = (args.flavor or "cpu").strip()
		_write_local_settings(
			vscode_dir,
			{
				"comfyuiPath": path_raw,
				"autoInstall": auto,
				"installFlavor": flavor,
			},
		)
		print(
			"[ensure_comfyui] Settings saved. Launch 'ComfyUI + MSS-Login (debug)' to start.\n"
			f"  path={path_raw}\n  autoInstall={auto}\n  installFlavor={flavor}",
			flush=True,
		)
		return 0

	comfy_path, auto_install, flavor = _resolve_config(args, workspace, local)
	uv_cmd = _resolve_uv(workspace)

	print(f"[ensure_comfyui] COMFYUI_PATH={comfy_path}", flush=True)
	print(f"[ensure_comfyui] AUTO_INSTALL={auto_install}", flush=True)
	print(f"[ensure_comfyui] FLAVOR={flavor}", flush=True)
	print(f"[ensure_comfyui] MSS_LOGIN_SRC={mss_src}", flush=True)

	if _is_comfyui_install(comfy_path):
		print("[ensure_comfyui] Existing ComfyUI install detected.", flush=True)
	elif auto_install:
		print(
			f"[ensure_comfyui] No ComfyUI at {comfy_path}; auto-install ON — installing ({flavor})...",
			flush=True,
		)
		_install_comfyui(uv_cmd, workspace, comfy_path, flavor)
	else:
		example = vscode_dir / EXAMPLE_SETTINGS_NAME
		local_path = vscode_dir / LOCAL_SETTINGS_NAME
		raise SystemExit(
			f"[ensure_comfyui] ComfyUI not found at {comfy_path} (missing main.py).\n"
			"  Auto-install is OFF. Either:\n"
			f"    - Point comfyuiPath at an existing install in {local_path}, or\n"
			"    - Set autoInstall to true (toggle), or\n"
			"    - Run task 'Configure ComfyUI test instance'.\n"
			f"  Example settings: {example}"
		)

	_link_mss_login(comfy_path, mss_src)
	_write_resolved_path(workspace, comfy_path)

	if args.save_settings:
		# Persist the path string as given when possible (keep ${workspaceFolder} if used)
		path_for_file = (args.path or "").strip() or str(
			local.get("comfyuiPath") or comfy_path
		)
		_write_local_settings(
			vscode_dir,
			{
				"comfyuiPath": path_for_file,
				"autoInstall": auto_install,
				"installFlavor": flavor,
			},
		)

	for rel in (".venv/bin/python", ".venv/Scripts/python.exe"):
		py = comfy_path / rel
		if py.is_file():
			print(f"[ensure_comfyui] ComfyUI Python: {py}", flush=True)
			break
	else:
		print(
			"[ensure_comfyui] WARNING: No .venv under ComfyUI; "
			"pick ComfyUI's interpreter in Cursor if imports fail.",
			flush=True,
		)

	print("[ensure_comfyui] Ready.", flush=True)
	return 0


if __name__ == "__main__":
	try:
		raise SystemExit(main())
	except subprocess.CalledProcessError as exc:
		print(f"[ensure_comfyui] Command failed with exit {exc.returncode}", flush=True)
		raise SystemExit(exc.returncode) from exc
