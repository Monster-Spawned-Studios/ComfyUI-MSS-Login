# --- START OF FILE utils/node_deps_installer.py ---
"""
Experimental Custom Node Dependency Scanner & Installer.

Scans custom nodes in ComfyUI's custom_nodes directory, extracts dependencies
from requirements.txt and pyproject.toml, checks for and resolves conflicts
(especially protecting ComfyUI core, PyTorch, and host wheels), and installs
safely using uv pip with pip fallback.
"""

from __future__ import annotations

import importlib.metadata
import json
import os
import re
import subprocess
import sys
import tempfile
from typing import Optional

# Extension root: parent of utils/
_THIS_DIR = os.path.dirname(os.path.abspath(__file__))
_EXTENSION_ROOT = os.path.dirname(_THIS_DIR)

PROTECTED_PACKAGES = {
	"torch",
	"torchvision",
	"torchaudio",
	"comfyui",
	"comfy-org",
	"comfyui-frontend",
}

_REQ_REGEX = re.compile(
	r"^\s*([A-Za-z0-9][A-Za-z0-9._-]*)\s*([><=~^!].*?)?(\s*;.*)?$"
)


def canonicalize_pkg_name(name: str) -> str:
	"""Normalize package name according to PEP 503."""
	return re.sub(r"[-_.]+", "-", name).lower().strip()


def get_installed_version(pkg_name: str) -> str | None:
	"""Return the installed version of a package or None."""
	norm = canonicalize_pkg_name(pkg_name)
	try:
		return importlib.metadata.version(norm)
	except Exception:
		# Also try raw name
		try:
			return importlib.metadata.version(pkg_name)
		except Exception:
			return None


def find_custom_nodes_dir(explicit_dir: str | None = None) -> str | None:
	"""Locate the ComfyUI custom_nodes directory."""
	if explicit_dir and os.path.isdir(explicit_dir):
		return os.path.abspath(explicit_dir)

	env_dir = os.environ.get("COMFYUI_CUSTOM_NODES_DIR")
	if env_dir and os.path.isdir(env_dir):
		return os.path.abspath(env_dir)

	try:
		import folder_paths  # pyright: ignore[reportMissingImports]

		if hasattr(folder_paths, "base_path"):
			cand = os.path.join(folder_paths.base_path, "custom_nodes")
			if os.path.isdir(cand):
				return os.path.abspath(cand)
	except Exception:
		pass

	# Sibling check: if ComfyUI-MSS-Login is in custom_nodes/
	parent = os.path.dirname(_EXTENSION_ROOT)
	if os.path.isdir(parent) and os.path.basename(parent).lower() == "custom_nodes":
		return os.path.abspath(parent)

	return None


def parse_requirement_line(line: str) -> tuple[str, str, str, str] | None:
	"""
	Parse a requirement line.
	Returns (raw_clean, canonical_name, specifier, markers) or None.
	"""
	clean = line.strip()
	if not clean or clean.startswith("#") or clean.startswith("-") or clean.startswith("--"):
		return None

	match = _REQ_REGEX.match(clean)
	if not match:
		return None

	raw_name = match.group(1)
	canon_name = canonicalize_pkg_name(raw_name)
	specifier = (match.group(2) or "").strip()
	markers = (match.group(3) or "").strip()
	return (clean, canon_name, specifier, markers)


def extract_requirements_from_file(file_path: str) -> list[str]:
	"""Extract requirement lines from a requirements.txt or pyproject.toml file."""
	if not os.path.isfile(file_path):
		return []

	basename = os.path.basename(file_path).lower()
	reqs: list[str] = []

	if basename == "requirements.txt" or basename.endswith(".txt"):
		try:
			with open(file_path, "r", encoding="utf-8", errors="replace") as f:
				for line in f:
					parsed = parse_requirement_line(line)
					if parsed:
						reqs.append(parsed[0])
		except Exception:
			pass
	elif basename == "pyproject.toml":
		try:
			import tomllib

			with open(file_path, "rb") as f:
				data = tomllib.load(f)
			deps = data.get("project", {}).get("dependencies", [])
			if isinstance(deps, list):
				for d in deps:
					if isinstance(d, str):
						parsed = parse_requirement_line(d)
						if parsed:
							reqs.append(parsed[0])
		except Exception:
			pass

	return reqs


def sanitize_and_resolve_conflicts(raw_reqs: list[str]) -> tuple[list[str], list[str]]:
	"""
	Check requirements against installed packages and protected core packages.
	Relaxes strict pins (==) to prevent resolution conflicts, and shields protected
	packages (PyTorch, ComfyUI core) from being replaced or downgraded.
	"""
	sanitized: list[str] = []
	conflict_notes: list[str] = []

	for line in raw_reqs:
		parsed = parse_requirement_line(line)
		if not parsed:
			continue

		raw_clean, canon_name, specifier, markers = parsed
		installed_ver = get_installed_version(canon_name)

		# 1. Protect core packages
		if canon_name in PROTECTED_PACKAGES:
			if installed_ver:
				conflict_notes.append(
					f"Skipped protected package '{canon_name}' (specified: '{raw_clean}', "
					f"host has version {installed_ver}) to protect host stability."
				)
				continue

		# 2. Check for pinned version conflict with installed package
		if installed_ver and specifier.startswith("=="):
			target_ver = specifier[2:].strip()
			if target_ver != installed_ver:
				# Relax == to >= so installer does not forcefully downgrade or conflict
				relaxed = f"{canon_name}>={target_ver}"
				if markers:
					relaxed = f"{relaxed} {markers}"
				sanitized.append(relaxed)
				conflict_notes.append(
					f"Relaxed '{raw_clean}' to '{relaxed}' to avoid conflict with "
					f"installed {canon_name}=={installed_ver}."
				)
				continue

		# Otherwise keep requirement as-is
		sanitized.append(raw_clean)

	return sanitized, conflict_notes


def scan_custom_nodes(custom_nodes_dir: str) -> list[dict]:
	"""Scan custom nodes directory and discover dependencies and conflicts."""
	if not os.path.isdir(custom_nodes_dir):
		return []

	discovered: list[dict] = []
	this_norm = os.path.abspath(_EXTENSION_ROOT).lower()

	for entry in sorted(os.listdir(custom_nodes_dir)):
		if entry.startswith(".") or entry.startswith("__"):
			continue
		node_path = os.path.join(custom_nodes_dir, entry)
		if not os.path.isdir(node_path):
			continue
		if os.path.abspath(node_path).lower() == this_norm:
			continue

		raw_reqs: list[str] = []
		req_txt = os.path.join(node_path, "requirements.txt")
		pyproj = os.path.join(node_path, "pyproject.toml")

		if os.path.isfile(req_txt):
			raw_reqs.extend(extract_requirements_from_file(req_txt))
		if os.path.isfile(pyproj):
			raw_reqs.extend(extract_requirements_from_file(pyproj))

		# Deduplicate raw requirements preserving order
		seen = set()
		unique_reqs: list[str] = []
		for r in raw_reqs:
			p = parse_requirement_line(r)
			if p and p[1] not in seen:
				seen.add(p[1])
				unique_reqs.append(r)

		if not unique_reqs:
			continue

		sanitized, conflicts = sanitize_and_resolve_conflicts(unique_reqs)
		discovered.append({
			"node_name": entry,
			"node_path": node_path,
			"raw_requirements": unique_reqs,
			"sanitized_requirements": sanitized,
			"conflicts_resolved": conflicts,
		})

	return discovered


def _run_cmd(cmd: list[str], timeout: int = 180) -> tuple[bool, str]:
	"""Run command with timeout and return (success, output)."""
	try:
		proc = subprocess.run(
			cmd,
			capture_output=True,
			text=True,
			timeout=timeout,
			check=False,
		)
		out = (proc.stdout or "").strip()
		err = (proc.stderr or "").strip()
		combined = "\n".join(filter(None, [out, err]))
		return proc.returncode == 0, combined
	except subprocess.TimeoutExpired:
		return False, f"Command timed out after {timeout} seconds."
	except Exception as e:
		return False, f"Execution failed: {e}"


def install_node_dependencies(
	node_info: dict, dry_run: bool = False, timeout: int = 180
) -> dict:
	"""Install sanitized requirements for a single custom node."""
	node_name = node_info["node_name"]
	reqs = node_info.get("sanitized_requirements", [])
	conflicts = node_info.get("conflicts_resolved", [])

	if not reqs:
		return {
			"node": node_name,
			"status": "skipped",
			"message": "No dependencies to install.",
			"conflicts_resolved": conflicts,
		}

	if dry_run:
		return {
			"node": node_name,
			"status": "dry_run",
			"message": f"Would install {len(reqs)} package(s).",
			"requirements": reqs,
			"conflicts_resolved": conflicts,
		}

	# Write temporary requirements file
	with tempfile.NamedTemporaryFile(
		mode="w", suffix="_reqs.txt", delete=False, encoding="utf-8"
	) as tf:
		temp_path = tf.name
		for r in reqs:
			tf.write(f"{r}\n")

	try:
		# 1. Try uv pip install
		uv_cmd = [
			sys.executable,
			"-m",
			"uv",
			"pip",
			"install",
			"--python",
			sys.executable,
			"-r",
			temp_path,
		]
		ok, out = _run_cmd(uv_cmd, timeout=timeout)
		if ok:
			return {
				"node": node_name,
				"status": "success",
				"installer": "uv",
				"installed": reqs,
				"conflicts_resolved": conflicts,
				"output": out,
			}

		# 2. Fallback to pip install
		pip_cmd = [sys.executable, "-m", "pip", "install", "-r", temp_path]
		ok, out = _run_cmd(pip_cmd, timeout=timeout)
		if ok:
			return {
				"node": node_name,
				"status": "success",
				"installer": "pip",
				"installed": reqs,
				"conflicts_resolved": conflicts,
				"output": out,
			}

		return {
			"node": node_name,
			"status": "failed",
			"error": out,
			"conflicts_resolved": conflicts,
		}
	finally:
		try:
			if os.path.isfile(temp_path):
				os.remove(temp_path)
		except Exception:
			pass


def scan_and_install_node_dependencies(
	custom_nodes_dir: str | None = None,
	dry_run: bool = False,
	timeout_per_node: int = 180,
) -> dict:
	"""
	Main entrypoint: scan custom nodes directory, resolve conflicts, and install dependencies.
	"""
	target_dir = find_custom_nodes_dir(custom_nodes_dir)
	if not target_dir or not os.path.isdir(target_dir):
		return {
			"success": False,
			"error": f"Custom nodes directory not found (target: {custom_nodes_dir}).",
			"custom_nodes_dir": target_dir,
			"nodes_scanned": 0,
			"results": [],
		}

	nodes = scan_custom_nodes(target_dir)
	results = []
	total_conflicts = 0

	for node_info in nodes:
		res = install_node_dependencies(
			node_info, dry_run=dry_run, timeout=timeout_per_node
		)
		total_conflicts += len(node_info.get("conflicts_resolved", []))
		results.append(res)

	return {
		"success": True,
		"custom_nodes_dir": target_dir,
		"nodes_scanned": len(nodes),
		"conflicts_resolved_count": total_conflicts,
		"results": results,
	}
# --- END OF FILE utils/node_deps_installer.py ---
