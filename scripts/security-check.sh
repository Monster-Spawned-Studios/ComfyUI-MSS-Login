#!/usr/bin/env bash
# MSS-Login defensive security check (no ComfyUI).
# Runs dependency audit, secret scan, security unit tests, and JWT negative smoke.
# Exit non-zero on any failure.
#
# Usage:
#   ./scripts/security-check.sh
#   SECURITY_CHECK_ALLOW_VULNS=1 ./scripts/security-check.sh   # warn on vulns, don't fail
#   SECURITY_CHECK_SKIP_GITLEAKS=1 ./scripts/security-check.sh

set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

PYTHON="${ROOT}/.venv/bin/python"
if [[ ! -x "$PYTHON" ]]; then
	echo "[security-check] FAIL: missing ${PYTHON}" >&2
	exit 1
fi

FAILED=0
warn() { echo "[security-check] WARN: $*"; }
fail() { echo "[security-check] FAIL: $*"; FAILED=1; }
ok() { echo "[security-check] OK: $*"; }

# --- 1) pip-audit (local venv) ---
echo ""
echo "[security-check] === pip-audit ==="
AUDIT_DIR="${TMPDIR:-/tmp}"
mkdir -p "$AUDIT_DIR" 2>/dev/null || AUDIT_DIR="$ROOT"
AUDIT_JSON="${AUDIT_DIR}/mss-login-pip-audit-$$.json"
PIP_AUDIT_ERR="${AUDIT_DIR}/mss-login-pip-audit-$$.err"
if ! "$PYTHON" -m pip_audit --format json -o "$AUDIT_JSON" 2>"$PIP_AUDIT_ERR"; then
	# pip-audit exits 1 when vulns found; still parse JSON
	true
fi
COUNTS_FILE="${AUDIT_DIR}/mss-login-pip-audit-$$.counts"
if ! "$PYTHON" - "$AUDIT_JSON" "$ROOT" "$COUNTS_FILE" <<'PY'
import json, re, sys
from pathlib import Path

audit_path, root, counts_path = Path(sys.argv[1]), Path(sys.argv[2]), Path(sys.argv[3])
if not audit_path.is_file():
	counts_path.write_text("-1 -1\n", encoding="utf-8")
	raise SystemExit(0)

declared: set[str] = set()
lock = root / "uv.lock"
if lock.is_file():
	for m in re.finditer(
		r'(?m)^name = "([^"]+)"', lock.read_text(encoding="utf-8", errors="replace")
	):
		declared.add(m.group(1).lower().replace("_", "-"))
pyproject = root / "pyproject.toml"
if pyproject.is_file():
	for m in re.finditer(
		r'(?m)^\s*"?([A-Za-z0-9_.-]+)"?\s*[>=<~!]',
		pyproject.read_text(encoding="utf-8", errors="replace"),
	):
		declared.add(m.group(1).lower().replace("_", "-"))

data = json.loads(audit_path.read_text(encoding="utf-8"))
total = locked = 0
for item in data.get("dependencies", []):
	if not isinstance(item, dict):
		continue
	vulns = item.get("vulns") or []
	if not vulns:
		continue
	name = (item.get("name") or "").lower().replace("_", "-")
	total += len(vulns)
	in_declared = name in declared if declared else True
	if in_declared:
		locked += len(vulns)
	for v in vulns:
		tag = "declared" if in_declared else "orphan-venv"
		print(
			f"  [{tag}] {item.get('name')}@{item.get('version')}: "
			f"{v.get('id')} fix={v.get('fix_versions')}",
			file=sys.stderr,
		)
counts_path.write_text(f"{total} {locked}\n", encoding="utf-8")
PY
then
	fail "pip-audit JSON parse failed"
	VULN_TOTAL=-1
	VULN_LOCKED=-1
else
	read -r VULN_TOTAL VULN_LOCKED <"$COUNTS_FILE" || {
		VULN_TOTAL=-1
		VULN_LOCKED=-1
	}
fi
rm -f "$COUNTS_FILE"

if [[ "$VULN_TOTAL" == "-1" ]]; then
	fail "pip-audit produced no JSON (see ${PIP_AUDIT_ERR})"
elif [[ "$VULN_LOCKED" != "0" ]]; then
	if [[ "${SECURITY_CHECK_ALLOW_VULNS:-0}" == "1" ]]; then
		warn "pip-audit: ${VULN_LOCKED} vuln(s) in declared deps (allowed by SECURITY_CHECK_ALLOW_VULNS=1); total hits=${VULN_TOTAL}"
	else
		fail "pip-audit: ${VULN_LOCKED} vuln(s) in declared deps (total venv hits=${VULN_TOTAL}); set SECURITY_CHECK_ALLOW_VULNS=1 to warn-only"
	fi
elif [[ "$VULN_TOTAL" != "0" ]]; then
	warn "pip-audit: ${VULN_TOTAL} vuln(s) only in undeclared/orphan venv packages (see AUDIT-012); not failing"
	ok "pip-audit: no vulnerabilities in declared project dependencies"
else
	ok "pip-audit: no known vulnerabilities in .venv"
fi
rm -f "$AUDIT_JSON" "$PIP_AUDIT_ERR"

# --- 2) gitleaks (git history) ---
echo ""
echo "[security-check] === gitleaks ==="
GITLEAKS=""
if [[ -x "${ROOT}/.ci-bin/gitleaks" ]]; then
	GITLEAKS="${ROOT}/.ci-bin/gitleaks"
elif command -v gitleaks >/dev/null 2>&1; then
	GITLEAKS="$(command -v gitleaks)"
fi
if [[ "${SECURITY_CHECK_SKIP_GITLEAKS:-0}" == "1" ]]; then
	warn "gitleaks skipped (SECURITY_CHECK_SKIP_GITLEAKS=1)"
elif [[ -z "$GITLEAKS" ]]; then
	if [[ -f "${ROOT}/scripts/ci/install-gitleaks.sh" ]]; then
		bash "${ROOT}/scripts/ci/install-gitleaks.sh" >"${AUDIT_DIR}/gitleaks-install.log" 2>&1 || true
		if [[ -x "${ROOT}/.ci-bin/gitleaks" ]]; then
			GITLEAKS="${ROOT}/.ci-bin/gitleaks"
		fi
	fi
fi
if [[ "${SECURITY_CHECK_SKIP_GITLEAKS:-0}" != "1" ]]; then
	if [[ -z "$GITLEAKS" ]]; then
		fail "gitleaks not found (install via scripts/ci/install-gitleaks.sh)"
	elif ! "$GITLEAKS" detect --source "$ROOT" --redact; then
		fail "gitleaks found potential secrets in git history"
	else
		ok "gitleaks: no leaks in git history"
	fi
fi

# --- 3) Security unit runners (no ComfyUI) ---
echo ""
echo "[security-check] === unit / negative tests ==="
run_py() {
	local label="$1"
	local script="$2"
	echo "[security-check] --- ${label} ---"
	if ! "$PYTHON" "$script"; then
		fail "${label}"
	else
		ok "${label}"
	fi
}

run_py "Path traversal" "${ROOT}/tests/run_path_traversal_tests.py"
run_py "Sanitizer" "${ROOT}/tests/run_sanitizer_tests.py"
run_py "Auth compatibility" "${ROOT}/tests/run_auth_compat_tests.py"
run_py "Auth HTML hardening" "${ROOT}/tests/run_auth_html_hardening_tests.py"
run_py "Portal download hardening (SSRF / flags)" "${ROOT}/tests/run_portal_download_hardening_tests.py"
run_py "View path safety" "${ROOT}/tests/run_view_path_safety_tests.py"

# --- 4) JWT negative smoke (invalid / expired / alg=none) ---
echo ""
echo "[security-check] === JWT negative smoke ==="
if ! "$PYTHON" - <<'PY'
from datetime import UTC, datetime, timedelta

import jwt

secret = "a" * 32
alg = "HS256"

# Valid
tok = jwt.encode(
	{"id": 1, "username": "u", "exp": datetime.now(UTC) + timedelta(minutes=5)},
	secret,
	algorithm=alg,
)
jwt.decode(tok, secret, algorithms=[alg])

# Expired -> must raise
expired = jwt.encode(
	{"id": 1, "username": "u", "exp": datetime.now(UTC) - timedelta(minutes=5)},
	secret,
	algorithm=alg,
)
try:
	jwt.decode(expired, secret, algorithms=[alg])
	raise SystemExit("expired token was accepted")
except jwt.ExpiredSignatureError:
	pass

# alg=none -> must raise
# cspell:disable-next-line
none_tok = "eyJhbGciOiJub25lIiwidHlwIjoiSldUIn0.eyJpZCI6MSwidXNlcm5hbWUiOiJ1In0."
try:
	jwt.decode(none_tok, secret, algorithms=[alg])
	raise SystemExit("alg=none token was accepted")
except jwt.exceptions.InvalidAlgorithmError:
	pass

# Wrong secret -> must raise
try:
	jwt.decode(tok, "b" * 32, algorithms=[alg])
	raise SystemExit("wrong-secret token was accepted")
except jwt.InvalidSignatureError:
	pass

# Empty / garbage
for bad in ("", "not-a-jwt", "a.b"):
	try:
		jwt.decode(bad, secret, algorithms=[alg])
		raise SystemExit(f"garbage accepted: {bad!r}")
	except Exception:
		pass

print("JWT negative smoke passed")
PY
then
	fail "JWT negative smoke"
else
	ok "JWT negative smoke (expired / none / wrong secret / garbage)"
fi

# --- Summary ---
echo ""
if [[ "$FAILED" -ne 0 ]]; then
	echo "[security-check] RESULT: FAILED"
	exit 1
fi
echo "[security-check] RESULT: PASSED"
exit 0
