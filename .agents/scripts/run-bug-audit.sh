#!/usr/bin/env bash
# run-bug-audit.sh - Automated bug detection and audit runner for ComfyUI-MSS-Login
# Resolves all paths relative to the repository root (strictly NO hardcoded system paths).

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "$REPO_ROOT"

PYTHON=".venv/bin/python"

if [[ ! -x "$PYTHON" ]]; then
    echo "[!] Virtual environment Python not found at $PYTHON."
    echo "    Please run 'uv sync --group dev' first."
    exit 1
fi

echo "========================================================"
echo "  ComfyUI-MSS-Login: Autonomous Bug Hunter Audit"
echo "  Repository Root: $REPO_ROOT"
echo "========================================================"

FAILED_SUITES=()

run_check() {
    local name="$1"
    shift
    echo ""
    echo ">>> Running: $name"
    if "$@"; then
        echo "  [PASS] $name"
    else
        echo "  [FAIL] $name"
        FAILED_SUITES+=("$name")
    fi
}

# 1. Static Analysis & Lints
run_check "Ruff Syntax & Lint Check" "$PYTHON" -m ruff check . --exclude .venv

# 2. Path Traversal & File Safety
run_check "Path Traversal Safety Suite" "$PYTHON" tests/run_path_traversal_tests.py

# 3. Input Sanitizer & XSS Protections
run_check "Sanitizer & XSS Exemption Suite" "$PYTHON" tests/run_sanitizer_tests.py

# 4. Authentication Compatibility & Key Stability
run_check "Authentication Compatibility Suite" "$PYTHON" tests/run_auth_compat_tests.py

# 5. Database Schema Drift & Migrations
run_check "Database Schema Drift Suite" "$PYTHON" tests/run_schema_drift_tests.py

# 6. Full Automated CI Test Suite
run_check "Full CI Test Suite" "$PYTHON" tests/run_ci.py --no-lint

echo ""
echo "========================================================"
echo "  Bug Hunter Audit Summary"
echo "========================================================"
if [ ${#FAILED_SUITES[@]} -eq 0 ]; then
    echo "  All audit suites PASSED cleanly with zero regressions."
    exit 0
else
    echo "  The following suites reported issues:"
    for suite in "${FAILED_SUITES[@]}"; do
        echo "    - $suite"
    done
    exit 1
fi
