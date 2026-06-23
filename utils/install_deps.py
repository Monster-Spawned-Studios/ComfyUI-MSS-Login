# --- START OF FILE utils/install_deps.py ---
"""Install extension dependencies on first load (UV-first, pip fallback).

Uses the same Python interpreter as ComfyUI (sys.executable). Failures are logged but do
not crash ComfyUI startup.
"""

from __future__ import annotations

import json
import os
import platform
import subprocess
import sys
from os.path import join
from typing import Optional

# Extension root: parent of the directory containing this file (utils/)
_install_deps_this_dir = os.path.dirname(os.path.abspath(__file__))
_install_deps_root = os.path.dirname(_install_deps_this_dir)

_install_attempted = False


def _detect_pytorch_cuda_index() -> str:
	"""Detect if CUDA 13 support is possible using nvidia-smi or CUDA driver library,
	and return the appropriate PyTorch extra index URL.
	"""
	cu130_index = "https://download.pytorch.org/whl/cu130"
	cu128_index = "https://download.pytorch.org/whl/cu128"

	# Method 1: Check via ctypes
	import ctypes
	import platform

	system = platform.system()
	try:
		cuda_lib = None
		if system == "Windows":
			cuda_lib = ctypes.WinDLL("nvcuda.dll")
		elif system == "Linux":
			cuda_lib = ctypes.CDLL("libcuda.so")
		if cuda_lib is not None:
			version = ctypes.c_int()
			if cuda_lib.cuDriverGetVersion(ctypes.byref(version)) == 0:
				major = version.value // 1000
				if major >= 13:
					return cu130_index
	except Exception:
		pass

	# Method 2: Check via nvidia-smi command line
	try:
		res = subprocess.run(
			["nvidia-smi"], capture_output=True, text=True, timeout=10, check=False
		)
		if res.returncode == 0 and res.stdout:
			import re

			match = re.search(r"CUDA Version:\s*(\d+)", res.stdout)
			if match:
				major = int(match.group(1))
				if major >= 13:
					return cu130_index
	except Exception:
		pass

	return cu128_index


_UV_TIMEOUT_SECONDS = 1200
_PIP_TIMEOUT_SECONDS = 600
_DOTENVX_TIMEOUT_SECONDS = 300


def _read_config_json(path: str) -> dict:
	"""Read config.json at path; return {} if missing or invalid."""
	if os.path.isfile(path):
		try:
			with open(path, encoding="utf-8") as f:
				return json.load(f)
		except (json.JSONDecodeError, OSError):
			pass
	return {}


# DEBUG_MODE: load from environment (Docker/Compose) then config.json for diagnosis
DEBUG_MODE_FROM_ENV = str(os.environ.get("DEBUG_MODE", "")).strip().lower() in ("1", "true", "yes")
_config_for_deps = _read_config_json(join(_install_deps_root, "config.json")) or _read_config_json(
	join(_install_deps_root, "config.defaults.json")
)
DEBUG_MODE = DEBUG_MODE_FROM_ENV or bool(_config_for_deps.get("debug_mode", False))


def _log(message: str, *, error: bool = False) -> None:
	if DEBUG_MODE or error:
		stream = sys.stderr if error else sys.stdout
		print(message, file=stream)


def _platform_requirements_rel() -> Optional[tuple[str, bool]]:
	"""Return (requirements file relative to extension root, needs_cuda_index) or None."""
	root = _install_deps_root
	req_cuda = join(root, "requirements_cuda.txt")
	req_metal = join(root, "requirements_metal.txt")
	req_default = join(root, "requirements.txt")
	system = platform.system()

	use_cuda_env = str(os.environ.get("USE_CUDA", "")).strip() == "1"
	if os.path.isfile(req_cuda) and (use_cuda_env or system in ("Windows", "Linux")):
		return ("requirements_cuda.txt", True)
	if system == "Darwin" and os.path.isfile(req_metal):
		return ("requirements_metal.txt", False)
	if os.path.isfile(req_default):
		return ("requirements.txt", False)
	return None


def _run_subprocess(
	argv: list[str], *, cwd: str, env: Optional[dict[str, str]] = None, timeout: int, label: str
) -> bool:
	try:
		merged_env = os.environ.copy()
		if env:
			merged_env.update(env)
		result = subprocess.run(
			argv,
			cwd=cwd,
			env=merged_env,
			capture_output=True,
			text=True,
			timeout=timeout,
			check=False,
		)
		if result.returncode != 0:
			detail = (result.stderr or result.stdout or "").strip()
			if detail:
				_log(f"[mss-login] {label} warning: {detail}", error=True)
			return False
		if DEBUG_MODE and (result.stdout or "").strip():
			_log(f"[mss-login] {label}: {(result.stdout or '').strip()}")
		return True
	except FileNotFoundError:
		_log(f"[mss-login] {label}: command not found ({argv[0]})", error=True)
		return False
	except subprocess.TimeoutExpired:
		_log(f"[mss-login] {label}: timed out after {timeout}s", error=True)
		return False
	except Exception as exc:
		_log(f"[mss-login] {label} failed: {exc}", error=True)
		return False


def _run_uv_pip(args: list[str], *, cwd: str, timeout: int = _UV_TIMEOUT_SECONDS) -> bool:
	uv_env = {"UV_TORCH_BACKEND": "auto"}
	python_target = sys.executable
	argv = [sys.executable, "-m", "uv", "pip", "install", "--python", python_target] + args
	return _run_subprocess(argv, cwd=cwd, env=uv_env, timeout=timeout, label="uv pip install")


def _run_pip(args: list[str], *, cwd: str, timeout: int = _PIP_TIMEOUT_SECONDS) -> bool:
	argv = [sys.executable, "-m", "pip", "install"] + args
	return _run_subprocess(argv, cwd=cwd, timeout=timeout, label="pip install")


def _install_with_uv(root: str, req_rel: Optional[tuple[str, bool]]) -> bool:
	# Platform requirements (torch, etc.) first — avoids building this repo as a wheel.
	if req_rel is not None:
		req_file, needs_cuda_index = req_rel
		_log(f"[mss-login] Installing dependencies with uv ({req_file})...")
		uv_args = ["-r", req_file]
		if needs_cuda_index:
			uv_args.extend(["--extra-index-url", _detect_pytorch_cuda_index()])
		if _run_uv_pip(uv_args, cwd=root):
			return True
		_log("[mss-login] uv requirements install failed; trying pyproject.toml.", error=True)

	pyproject = join(root, "pyproject.toml")
	if os.path.isfile(pyproject):
		_log("[mss-login] Installing dependencies with uv (pyproject.toml)...")
		# Install [project.dependencies] without packaging the custom-node tree as a wheel.
		uv_args = ["-r", "pyproject.toml"]
		system = platform.system()
		if system in ("Windows", "Linux"):
			uv_args.extend(["--extra-index-url", _detect_pytorch_cuda_index()])
		return _run_uv_pip(uv_args, cwd=root)

	return False


def _install_with_pip(root: str, req_rel: Optional[tuple[str, bool]]) -> bool:
	pyproject = join(root, "pyproject.toml")
	if os.path.isfile(pyproject):
		_log("[mss-login] Installing dependencies with pip (pyproject.toml)...")
		try:
			import tomllib

			with open(pyproject, "rb") as f:
				data = tomllib.load(f)
			deps = data.get("project", {}).get("dependencies", [])
			if deps:
				system = platform.system()
				pip_args = list(deps)
				if system in ("Windows", "Linux"):
					pip_args.extend(["--extra-index-url", _detect_pytorch_cuda_index()])
				if _run_pip(pip_args, cwd=root):
					return True
		except Exception as exc:
			_log(f"[mss-login] Parsing pyproject.toml failed for pip: {exc}", error=True)

	if req_rel is None:
		_log("[mss-login] No platform requirements file found; skipping pip fallback.", error=True)
		return False

	req_file, needs_cuda_index = req_rel
	_log(f"[mss-login] Falling back to pip ({req_file})...")
	pip_args = ["-r", req_file]
	if needs_cuda_index:
		pip_args.extend(["--extra-index-url", _detect_pytorch_cuda_index()])
	return _run_pip(pip_args, cwd=root)


def _ensure_dotenvx_binary() -> bool:
	"""Best-effort install of dotenvx CLI via python-dotenvx postinstall."""
	if _run_subprocess(
		["dotenvx", "--version"], cwd=_install_deps_root, timeout=30, label="dotenvx --version"
	):
		return True

	_log("[mss-login] Dotenvx binary not found; running dotenvx-postinstall...", error=True)
	if _run_subprocess(
		[sys.executable, "-m", "dotenvx_postinstall"],
		cwd=_install_deps_root,
		timeout=_DOTENVX_TIMEOUT_SECONDS,
		label="dotenvx-postinstall",
	):
		_log("[mss-login] Dotenvx binary installed successfully.")
		return True

	# Some installs expose a console script instead of -m
	return _run_subprocess(
		["dotenvx-postinstall"],
		cwd=_install_deps_root,
		timeout=_DOTENVX_TIMEOUT_SECONDS,
		label="dotenvx-postinstall",
	)


def install_dependencies() -> bool:
	"""UV-first dependency install into ComfyUI's Python. Safe at module load."""
	global _install_attempted

	if _install_attempted:
		if DEBUG_MODE:
			_log("[mss-login] Dependency install already attempted this process; skipping.")
		return True

	_install_attempted = True
	root = _install_deps_root
	req_rel = _platform_requirements_rel()

	try:
		if _install_with_uv(root, req_rel):
			deps_ok = True
		elif _install_with_pip(root, req_rel):
			_log("[mss-login] Dependencies installed via pip fallback.")
			deps_ok = True
		else:
			_log(
				"[mss-login] Dependency install failed (uv and pip). "
				"Install manually: uv pip install . or pip install -r requirements.txt",
				error=True,
			)
			deps_ok = False
	except Exception as exc:
		_log(f"[mss-login] Python dependency installation failed: {exc}", error=True)
		deps_ok = False

	# dotenvx binary is optional; do not fail the whole install if postinstall fails
	try:
		if not _ensure_dotenvx_binary():
			_log(
				"[mss-login] Dotenvx binary unavailable. "
				"Install python-dotenvx and run dotenvx-postinstall if needed.",
				error=True,
			)
	except Exception as exc:
		_log(f"[mss-login] Dotenvx setup failed: {exc}", error=True)

	return deps_ok


# --- END OF FILE utils/install_deps.py ---
