# --- START OF FILE utils/install_deps.py ---
"""Install extension dependencies from requirements.txt and pyproject.toml on first load.
Uses the same Python as ComfyUI (sys.executable). Failures are logged but do not crash ComfyUI."""
import os
import subprocess
import sys


def install_dependencies() -> None:
    """Install dependencies from requirements.txt then pyproject.toml. Safe to call at module load."""
    # Extension root: parent of the directory containing this file (utils/)
    this_dir = os.path.dirname(os.path.abspath(__file__))
    root = os.path.dirname(this_dir)

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
            print("[mss_login] pip install timed out; dependencies may be incomplete.", file=sys.stderr)
            return False
        except Exception as e:
            print(f"[mss_login] Dependency install failed: {e}", file=sys.stderr)
            return False

    req_txt = os.path.join(root, "requirements.txt")
    if os.path.isfile(req_txt):
        run_pip(["-r", "requirements.txt"])

    pyproject = os.path.join(root, "pyproject.toml")
    if os.path.isfile(pyproject):
        run_pip(["."])

# --- END OF FILE utils/install_deps.py ---
