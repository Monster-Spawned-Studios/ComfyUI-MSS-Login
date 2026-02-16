# --- START OF FILE utils/install_deps.py ---
"""Install extension dependencies from requirements.txt and pyproject.toml on first load.
Uses the same Python as ComfyUI (sys.executable). Failures are logged but do not crash ComfyUI.
"""

import json
import os
from os import getcwd
from os.path import join
import subprocess
import sys

# Extension root: parent of the directory containing this file (utils/)
_install_deps_this_dir = os.path.dirname(os.path.abspath(__file__))
_install_deps_root = os.path.dirname(_install_deps_this_dir)


def _read_config_json(path: str) -> dict:
    """Read config.json at path; return {} if missing or invalid. Avoids importing utils.config (import path issues when loaded via importlib)."""
    if os.path.isfile(path):
        try:
            with open(path, "r") as f:
                return json.load(f)
        except (json.JSONDecodeError, OSError):
            pass
    return {}


# DEBUG_MODE: load from environment (Docker/Compose) then config.json for diagnosis
DEBUG_MODE_FROM_ENV = str(os.environ.get("DEBUG_MODE", "")).strip().lower() in (
    "1",
    "true",
    "yes",
)
DEBUG_MODE = DEBUG_MODE_FROM_ENV or bool(
    _read_config_json(join(_install_deps_root, "config.json")).get("debug_mode", False)
)


# Return True if successful, False otherwise
def install_dependencies() -> bool:
    """Install dependencies from requirements.txt then pyproject.toml. Safe to call at module load. Returns True if successful, False otherwise."""
    root = _install_deps_root

    # Run a command inside the user's shell and return True if successful, False otherwise
    def run_command(command: str) -> bool:
        try:
            result = subprocess.run(
                command,
                cwd=root,
                capture_output=True,
                text=True,
                timeout=300,
                check=False,
            )
            if result.returncode != 0 and result.stderr:
                print(
                    f"[mss_login] {command} warning: {result.stderr.strip()}",
                    file=sys.stderr,
                )
            return result.returncode == 0
        except Exception as e:
            print(f"[mss_login] {command} failed: {e}", file=sys.stderr)
            return False

    # Run a uv command and return True if successful, False otherwise
    def run_uv(args: list[str], timeout: int = 600, cwd: str = root) -> bool:
        try:
            result = subprocess.run(
                [sys.executable, "-m", "uv"] + args,
                cwd=cwd,
                capture_output=True,
                text=True,
                timeout=timeout,
                check=False,
            )
            if result.returncode != 0 and result.stderr:
                print(
                    f"[mss_login] uv {args} warning: {result.stderr.strip()}",
                    file=sys.stderr,
                )
            return result.returncode == 0
        except FileNotFoundError:
            print(
                "[mss_login] uv not available; skipping dependency install.",
                file=sys.stderr,
            )
            return False
        except subprocess.TimeoutExpired:
            print(
                "[mss_login] uv install timed out; dependencies may be incomplete.",
                file=sys.stderr,
            )
            return False
        except Exception as e:
            print(f"[mss_login] uv install failed: {e}", file=sys.stderr)
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
                print(
                    f"[mss_login] pip install warning: {result.stderr.strip()}",
                    file=sys.stderr,
                )
            return result.returncode == 0
        except FileNotFoundError:
            print(
                "[mss_login] pip not available; skipping dependency install.",
                file=sys.stderr,
            )
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
        import platform

        req_txt_metal = os.path.join(root, "requirements_metal.txt")
        req_txt_cuda = os.path.join(root, "requirements_cuda.txt")
        # Check to see if the OS distribution is Windows or Linux, and if the USE_CUDA environment variable is set to 1
        if (
            os.path.isfile(req_txt_cuda)
            and os.environ.get("USE_CUDA") == "1"
            or os.path.isfile(req_txt_cuda)
            and platform.system() in ["Windows", "Linux"]
        ):
            if DEBUG_MODE:
                print(
                    "[mss_login] Installing CUDA dependencies from requirements_cuda.txt..."
                )
            if not run_pip(
                [
                    "-r",
                    "requirements_cuda.txt",
                    "--extra-index-url",
                    "https://download.pytorch.org/whl/cu128",
                ]
            ):
                return False
        elif os.path.isfile(req_txt_metal) and platform.system() in ["Darwin"]:
            if DEBUG_MODE:
                print(
                    "[mss_login] Installing non-CUDA dependencies from requirements_metal.txt..."
                )
            if not run_pip(["-r", "requirements_metal.txt"]):
                return False
        else:
            print(
                "[mss_login] No requirements_metal.txt or requirements_cuda.txt file found and/or operating system is not supported, skipping requirements_metal.txt or requirements_cuda.txt dependency install.",
                file=sys.stderr,
            )
            return False
        # Install the dependencies from the pyproject.toml file
        pyproject = os.path.join(root, "pyproject.toml")
        if os.path.isfile(pyproject) and platform.system() in ["Windows", "Linux"]:
            if not run_uv(
                ["install", "-r", f"{root}/pyproject.toml"], timeout=1200, cwd=root
            ):
                return False
        elif os.path.isfile(pyproject) and platform.system() in ["Darwin"]:
            if DEBUG_MODE:
                print(
                    "[mss_login] Skipping pyproject.toml dependency install on macOS, as it is not supported.",
                    file=sys.stderr,
                )
        else:
            print(
                "[mss_login] No pyproject.toml file found or operating system is not supported, skipping pyproject.toml dependency install.",
                file=sys.stderr,
            )
            return False
    except Exception as e:
        print(
            f"[mss_login] Python dependencies installation failed: {e}", file=sys.stderr
        )
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
