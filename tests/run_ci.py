r"""
CI/CD test entrypoint: runs all standalone tests and optional lint.

Use the project .venv (see .agent/rules/python-venv.mdc):

  .\.venv\Scripts\python tests/run_ci.py
  .\.venv\Scripts\python tests/run_ci.py --no-lint   # skip ruff
  .\.venv\Scripts\python tests/run_ci.py --path-only # only path traversal tests

Exit code: 0 if all steps pass, 1 otherwise (suitable for CI).
"""

import argparse
import os
import subprocess
import sys


_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TESTS_DIR = os.path.dirname(os.path.abspath(__file__))


def run_script(name: str, path: str) -> bool:
	"""Run a Python script; return True if exit code is 0."""
	venv_exe = os.path.join(_PROJECT_ROOT, ".venv", "Scripts", "python.exe")
	if not os.path.isfile(venv_exe):
		venv_exe = os.path.join(_PROJECT_ROOT, ".venv", "bin", "python")
	if not os.path.isfile(venv_exe):
		venv_exe = sys.executable
	python = venv_exe
	result = subprocess.run([python, path], cwd=_PROJECT_ROOT, capture_output=False)
	return result.returncode == 0


def run_ruff_check() -> bool:
	"""Run ruff check; return True if exit code is 0."""
	python = sys.executable
	result = subprocess.run(
		[python, "-m", "ruff", "check", _PROJECT_ROOT, "--exclude", ".venv"],
		cwd=_PROJECT_ROOT,
		capture_output=True,
		text=True,
	)
	if result.returncode != 0:
		print("[CI] ruff check failed:")
		print(result.stdout or result.stderr or "")
	return result.returncode == 0


def run_ruff_format_check() -> bool:
	"""Run ruff format --check; return True if exit code is 0."""
	result = subprocess.run(
		[sys.executable, "-m", "ruff", "format", "--check", _PROJECT_ROOT, "--exclude", ".venv"],
		cwd=_PROJECT_ROOT,
		capture_output=True,
		text=True,
	)
	if result.returncode != 0:
		print("[CI] ruff format --check failed (run 'ruff format' to fix):")
		print(result.stdout or result.stderr or "")
	return result.returncode == 0


def main() -> int:
	ap = argparse.ArgumentParser(
		description="Run CI tests (path traversal, sanitizer, optional lint)."
	)
	ap.add_argument("--no-lint", action="store_true", help="Skip ruff check and format")
	ap.add_argument("--path-only", action="store_true", help="Run only path traversal tests")
	args = ap.parse_args()

	steps = []
	# 1. Path traversal tests (required for security)
	steps.append(("Path traversal", os.path.join(TESTS_DIR, "run_path_traversal_tests.py")))
	if not args.path_only:
		steps.append(("Sanitizer", os.path.join(TESTS_DIR, "run_sanitizer_tests.py")))
		steps.append(
			("Navigation detection", os.path.join(TESTS_DIR, "run_navigation_detection_tests.py"))
		)
		steps.append(("Model isolation", os.path.join(TESTS_DIR, "run_model_isolation_tests.py")))
		steps.append(
			("Experimental failsafe", os.path.join(TESTS_DIR, "run_experimental_failsafe_tests.py"))
		)
		steps.append(
			("Model download queue", os.path.join(TESTS_DIR, "run_model_download_queue_tests.py"))
		)
		steps.append(("View path safety", os.path.join(TESTS_DIR, "run_view_path_safety_tests.py")))
		steps.append(("Trash bin", os.path.join(TESTS_DIR, "run_trash_bin_tests.py")))
		steps.append(("NTFY + quarantine", os.path.join(TESTS_DIR, "run_ntfy_quarantine_tests.py")))
	if not args.no_lint:
		steps.append(("Ruff check", None))  # special
		steps.append(("Ruff format", None))

	failed = []
	for label, path in steps:
		if label == "Ruff check":
			if not run_ruff_check():
				failed.append(label)
			continue
		if label == "Ruff format":
			if not run_ruff_format_check():
				failed.append(label)
			continue
		print(f"\n[CI] --- {label} ---")
		if not run_script(label, path):
			failed.append(label)

	print("\n[CI] --- Summary ---")
	if failed:
		print(f"[CI] Failed: {', '.join(failed)}")
		return 1
	print("[CI] All steps passed.")
	return 0


if __name__ == "__main__":
	sys.exit(main())
