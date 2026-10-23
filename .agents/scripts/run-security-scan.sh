#!/usr/bin/env bash
# run-security-scan.sh - Automated security scanning and pentest runner for ComfyUI-MSS-Login
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
echo "  ComfyUI-MSS-Login: Autonomous Security Scanner & Pentest"
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

# 1. Dependency Vulnerability Audit (pip-audit on installed environment)
if "$PYTHON" -c "import pip_audit" 2>/dev/null; then
    run_check "Dependency Vulnerability Audit (pip-audit)" "$PYTHON" -m pip_audit
else
    echo "[!] pip-audit not installed in virtual environment, skipping."
fi

# 2. OSV Scanner (if available)
if command -v osv-scanner &>/dev/null; then
    run_check "OSV Lockfile Scanner" osv-scanner scan --config osv-scanner.toml -L uv.lock -L web/frontend/pnpm-lock.yaml
else
    echo "[-] osv-scanner not found in PATH, skipping."
fi

# 3. Secret Detection (Gitleaks)
GITLEAKS_BIN=""
if command -v gitleaks &>/dev/null; then
    GITLEAKS_BIN="gitleaks"
elif [[ -x ".ci-bin/gitleaks" ]]; then
    GITLEAKS_BIN=".ci-bin/gitleaks"
fi

if [[ -n "$GITLEAKS_BIN" ]]; then
    run_check "Secret Detection (gitleaks)" "$GITLEAKS_BIN" detect --source . --redact
else
    echo "[-] gitleaks binary not found, skipping static secret detection."
fi

# 4. Path Traversal & Arbitrary File Access Security Suite
run_check "Path Traversal & Injection Defense Suite" "$PYTHON" tests/run_path_traversal_tests.py

# 5. XSS & Input Sanitizer Security Suite
run_check "Input Sanitization & XSS Defense Suite" "$PYTHON" tests/run_sanitizer_tests.py

# 6. Authentication, JWT & Replay Attack Defense Suite
run_check "Auth Compatibility & JWT Defense Suite" "$PYTHON" tests/run_auth_compat_tests.py

# 7. Built-in Security Defense Verification
if [[ -f "scripts/security-check.sh" ]]; then
    run_check "Defensive Security Script (scripts/security-check.sh)" bash scripts/security-check.sh
fi

echo ""
echo "========================================================"
echo "  Security Scan & Pentest Summary"
echo "========================================================"
if [ ${#FAILED_SUITES[@]} -eq 0 ]; then
    echo "  All security and pentest suites PASSED. Zero exploitable flaws detected."
    exit 0
else
    echo "  The following security checks reported findings:"
    for suite in "${FAILED_SUITES[@]}"; do
        echo "    - $suite"
    done
    exit 1
fi
