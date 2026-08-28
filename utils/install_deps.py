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


PYTORCH_INDEX_CU130 = "https://download.pytorch.org/whl/cu130"
PYTORCH_INDEX_CU128 = "https://download.pytorch.org/whl/cu128"
PYTORCH_INDEX_CPU = "https://download.pytorch.org/whl/cpu"


def detect_cuda_major() -> int | None:
	"""Return the host CUDA driver major version, or None if CUDA is unavailable.

	Checks the NVIDIA driver library first, then ``nvidia-smi``. Used to decide
	whether cu130 wheels are supported before installing them.
	"""
	import ctypes
	import re

	system = platform.system()
	if system == "Darwin":
		return None

	try:
		cuda_lib = None
		if system == "Windows":
			cuda_lib = ctypes.WinDLL("nvcuda.dll")
		elif system == "Linux":
			for soname in ("libcuda.so.1", "libcuda.so"):
				try:
					cuda_lib = ctypes.CDLL(soname)
					break
				except OSError:
					continue
		if cuda_lib is not None:
			version = ctypes.c_int()
			if cuda_lib.cuDriverGetVersion(ctypes.byref(version)) == 0:
				major = int(version.value) // 1000
				if major > 0:
					return major
	except Exception:
		pass

	try:
		res = subprocess.run(
			["nvidia-smi"], capture_output=True, text=True, timeout=10, check=False
		)
		if res.returncode == 0 and res.stdout:
			match = re.search(r"CUDA Version:\s*(\d+)", res.stdout)
			if match:
				major = int(match.group(1))
				if major > 0:
					return major
	except Exception:
		pass

	return None


def detect_torch_install_plan(
	*, system: str | None = None, cuda_major: int | None = None, env: dict[str, str] | None = None
) -> dict[str, str | None]:
	"""Choose the PyTorch backend for this host.

	Preference order:
	- macOS: Metal (PyPI wheels; CUDA indexes are not published for darwin)
	- Linux/Windows: cu130 if the NVIDIA driver reports CUDA 13+, else cu128
	  if any CUDA driver is present, else CPU
	Env overrides: ``USE_CPU=1`` forces CPU; ``USE_CUDA=1`` prefers CUDA when a
	driver is found (still requires a CUDA major >= 13 for cu130).
	"""
	system = system or platform.system()
	environ = env if env is not None else os.environ
	force_cpu = str(environ.get("USE_CPU", "")).strip() == "1"
	force_cuda = str(environ.get("USE_CUDA", "")).strip() == "1"

	if system == "Darwin":
		return {
			"backend": "metal",
			"requirements_file": "requirements_metal.txt",
			"extra_index_url": None,
		}

	if force_cpu:
		return {
			"backend": "cpu",
			"requirements_file": "requirements_cpu.txt",
			"extra_index_url": PYTORCH_INDEX_CPU,
		}

	if cuda_major is None:
		cuda_major = detect_cuda_major()

	if cuda_major is not None and cuda_major >= 13:
		return {
			"backend": "cuda130",
			"requirements_file": "requirements_cuda.txt",
			"extra_index_url": PYTORCH_INDEX_CU130,
		}
	if cuda_major is not None and cuda_major >= 11:
		return {
			"backend": "cuda128",
			"requirements_file": "requirements_cuda.txt",
			"extra_index_url": PYTORCH_INDEX_CU128,
		}
	if force_cuda:
		# Caller asked for CUDA but no driver was detected; stay on cu128 rather
		# than silently installing CPU wheels (matches previous auto-install).
		return {
			"backend": "cuda128",
			"requirements_file": "requirements_cuda.txt",
			"extra_index_url": PYTORCH_INDEX_CU128,
		}
	return {
		"backend": "cpu",
		"requirements_file": "requirements_cpu.txt",
		"extra_index_url": PYTORCH_INDEX_CPU,
	}


def _detect_pytorch_cuda_index() -> str:
	"""Return the CUDA wheel index after verifying driver support.

	Prefers cu130 when the host CUDA driver is 13+. Falls back to cu128.
	"""
	plan = detect_torch_install_plan()
	return plan.get("extra_index_url") or PYTORCH_INDEX_CU128


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


def _platform_requirements_rel() -> tuple[str, str | None] | None:
	"""Return (requirements file relative to extension root, extra_index_url) or None.

	extra_index_url is the PyTorch wheel index for CUDA/CPU hosts, or None for
	macOS Metal (PyPI). CUDA 13 is selected only after detect_cuda_major()
	confirms driver support.
	"""
	root = _install_deps_root
	plan = detect_torch_install_plan()
	req_file = plan["requirements_file"]
	req_path = join(root, req_file)
	if os.path.isfile(req_path):
		return (req_file, plan["extra_index_url"])
	req_default = join(root, "requirements.txt")
	if os.path.isfile(req_default):
		return ("requirements.txt", plan["extra_index_url"])
	return None


def _run_subprocess(
	argv: list[str], *, cwd: str, env: dict[str, str] | None = None, timeout: int, label: str
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


def _install_with_uv(root: str, req_rel: tuple[str, str | None] | None) -> bool:
	# Platform requirements (torch, etc.) first — avoids building this repo as a wheel.
	if req_rel is not None:
		req_file, extra_index_url = req_rel
		_log(f"[mss-login] Installing dependencies with uv ({req_file})...")
		uv_args = ["-r", req_file]
		if extra_index_url:
			uv_args.extend(["--extra-index-url", extra_index_url])
		if _run_uv_pip(uv_args, cwd=root):
			return True
		_log("[mss-login] uv requirements install failed; trying pyproject.toml.", error=True)

	pyproject = join(root, "pyproject.toml")
	if os.path.isfile(pyproject):
		_log("[mss-login] Installing dependencies with uv (pyproject.toml)...")
		# Install [project.dependencies] without packaging the custom-node tree as a wheel.
		uv_args = ["-r", "pyproject.toml"]
		plan = detect_torch_install_plan()
		if plan.get("extra_index_url"):
			uv_args.extend(["--extra-index-url", plan["extra_index_url"]])
		return _run_uv_pip(uv_args, cwd=root)

	return False


def _install_with_pip(root: str, req_rel: tuple[str, str | None] | None) -> bool:
	pyproject = join(root, "pyproject.toml")
	plan = detect_torch_install_plan()
	if os.path.isfile(pyproject):
		_log("[mss-login] Installing dependencies with pip (pyproject.toml)...")
		try:
			import tomllib

			with open(pyproject, "rb") as f:
				data = tomllib.load(f)
			deps = data.get("project", {}).get("dependencies", [])
			if deps:
				pip_args = list(deps)
				if plan.get("extra_index_url"):
					pip_args.extend(["--extra-index-url", plan["extra_index_url"]])
				if _run_pip(pip_args, cwd=root):
					return True
		except Exception as exc:
			_log(f"[mss-login] Parsing pyproject.toml failed for pip: {exc}", error=True)

	if req_rel is None:
		_log("[mss-login] No platform requirements file found; skipping pip fallback.", error=True)
		return False

	req_file, extra_index_url = req_rel
	_log(f"[mss-login] Falling back to pip ({req_file})...")
	pip_args = ["-r", req_file]
	if extra_index_url:
		pip_args.extend(["--extra-index-url", extra_index_url])
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
	plan = detect_torch_install_plan()
	_log(
		f"[mss-login] PyTorch install plan: backend={plan.get('backend')} "
		f"index={plan.get('extra_index_url') or 'PyPI (Metal/default)'}"
	)
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
