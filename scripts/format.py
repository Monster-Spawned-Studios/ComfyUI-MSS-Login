"""
Auto-format all project Python files using ruff.

Usage (always use the project .venv):
    .venv/bin/python scripts/format.py            # format + fix lint
    .venv/bin/python scripts/format.py --check     # dry-run (exit 1 if changes needed)
    .venv/bin/python scripts/format.py --no-fix    # format only, skip lint auto-fix

Exit code: 0 if everything is clean, 1 if files were changed (or would be in --check mode).
"""

import argparse
import subprocess
import sys
from os.path import abspath, dirname

_PROJECT_ROOT = dirname(dirname(abspath(__file__)))


def _run(args: list[str], label: str, check_only: bool) -> bool:
    """Run a command and return True on success."""
    result = subprocess.run(
        args,
        cwd=_PROJECT_ROOT,
        capture_output=True,
        text=True,
        check=False,
    )

    if result.returncode == 0:
        if not check_only:
            print(f"  [{label}] OK")
        return True

    print(f"  [{label}] {'would change' if check_only else 'applied changes to'} files:")
    output = (result.stdout or "") + (result.stderr or "")
    for line in output.strip().splitlines():
        stripped = line.strip()
        if stripped and not stripped.startswith("All checks"):
            print(f"    {stripped}")

    return False


def main() -> int:
    ap = argparse.ArgumentParser(description="Format project Python files with ruff.")
    ap.add_argument(
        "--check",
        action="store_true",
        help="Check only; don't modify files (exit 1 if drift exists).",
    )
    ap.add_argument(
        "--no-fix",
        action="store_true",
        help="Skip ruff check --fix (only run ruff format).",
    )
    args = ap.parse_args()

    python = sys.executable
    check_only = args.check
    all_ok = True

    # Step 1: ruff format
    fmt_args = [python, "-m", "ruff", "format", _PROJECT_ROOT, "--exclude", ".venv"]
    if check_only:
        fmt_args.insert(4, "--check")
    print("[format] Ruff format" + (" --check" if check_only else "") + "...")
    if not _run(fmt_args, "ruff format", check_only):
        all_ok = False

    # Step 2: ruff check --fix (auto-fixable lint)
    if not args.no_fix:
        fix_args = [python, "-m", "ruff", "check", _PROJECT_ROOT, "--exclude", ".venv"]
        if not check_only:
            fix_args.append("--fix")
        print("[format] Ruff check" + ("" if check_only else " --fix") + "...")
        if not _run(fix_args, "ruff check", check_only):
            all_ok = False

    if all_ok:
        print("[format] All files are clean.")
    elif not check_only:
        print("[format] Done. Files were reformatted.")
    else:
        print("[format] Drift detected. Run without --check to fix.")

    return 0 if all_ok else 1


if __name__ == "__main__":
    sys.exit(main())
