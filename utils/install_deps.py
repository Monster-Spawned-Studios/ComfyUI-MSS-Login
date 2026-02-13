# --- START OF FILE utils/install_deps.py ---
"""Install extension dependencies from requirements.txt and pyproject.toml on first load.
Uses the same Python as ComfyUI (sys.executable). Failures are logged but do not crash ComfyUI."""

import os
import subprocess
import sys


# Return True if successful, False otherwise
def install_dependencies() -> bool:
	"""Install dependencies from requirements.txt then pyproject.toml. Safe to call at module load. Returns True if successful, False otherwise."""
	# Extension root: parent of the directory containing this file (utils/)
	this_dir = os.path.dirname(os.path.abspath(__file__))
	root = os.path.dirname(this_dir)

	# Run a command inside the user's shell and return True if successful, False otherwise
	def run_command(command: str) -> bool:
		try:
			result = subprocess.run(
				command, cwd=root, capture_output=True, text=True, timeout=300, check=False
			)
			if result.returncode != 0 and result.stderr:
				print(f"[mss_login] {command} warning: {result.stderr.strip()}", file=sys.stderr)
			return result.returncode == 0
		except Exception as e:
			print(f"[mss_login] {command} failed: {e}", file=sys.stderr)
			return False

	# Run a pip command and return True if successful, False otherwise
	def run_pip(args: list[str]) -> bool:
		try:
			result = subprocess.run(
				[sys.executable, "-m", "pip", "install"] + args,
				cwd=root,
				capture_output=True,
				text=True,
				timeout=300,
				check=False,
			)
			if result.returncode != 0 and result.stderr:
				print(f"[mss_login] pip install warning: {result.stderr.strip()}", file=sys.stderr)
			return result.returncode == 0
		except FileNotFoundError:
			print("[mss_login] pip not available; skipping dependency install.", file=sys.stderr)
			return False
		except subprocess.TimeoutExpired:
			print(
				"[mss_login] pip install timed out; dependencies may be incomplete.",
				file=sys.stderr,
			)
			return False
		except Exception as e:
			print(f"[mss_login] Dependency install failed: {e}", file=sys.stderr)
			return False

	try:
		req_txt = os.path.join(root, "requirements.txt")
		if os.path.isfile(req_txt):
			if not run_pip(["-r", "requirements.txt"]):
				return False

		pyproject = os.path.join(root, "pyproject.toml")
		if os.path.isfile(pyproject):
			if not run_pip(["."]):
				return False
	except Exception as e:
		print(f"[mss_login] Python dependencies installation failed: {e}", file=sys.stderr)
		return False

	try:
		# Check to see if the dotenvx binary is installed
		if not run_command(["dotenvx", "--version"]):
			print(
				"[mss_login] Dotenvx binary not found. Please install it manually using `pip install python-dotenvx`.",
				file=sys.stderr,
			)
			print(
				"[mss_login] Running `dotenvx-postinstall` to install the dotenvx binary...",
				file=sys.stderr,
			)
			if not run_command(["dotenvx-postinstall"]):
				print(
					"[mss_login] Failed to install dotenvx binary. Please install it manually using `dotenvx-postinstall`.",
					file=sys.stderr,
				)
				return False
			else:
				print("[mss_login] Dotenvx binary installed successfully.")
	except Exception as e:
		print(f"[mss_login] Dotenvx binary installation failed: {e}", file=sys.stderr)
		return False

	return True


# --- END OF FILE utils/install_deps.py ---
