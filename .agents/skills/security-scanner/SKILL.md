---
name: security-scanner
description: Run penetration testing, dependency security audits, secret scanning, and vulnerability remediation across projects.
---

# Security Scanner & Penetration Tester Skill

Use this skill when auditing a repository for security vulnerabilities, performing penetration tests on authentication/API layers, checking for hardcoded secrets, or scanning supply chain dependencies.

## When to Trigger
- "Run a security audit"
- "Scan for vulnerabilities"
- "Penetration test this project"
- "Check dependencies for CVEs"
- Pre-release and pre-deployment security reviews.

## Execution Workflow

1. **Environment Setup**:
   Ensure security tools use the repository virtual environment (`.venv/bin/pip-audit`). Never hardcode full system paths.

2. **Supply Chain Audit (SCA)**:
   Scan Python and Node.js dependencies for known vulnerabilities:
   ```bash
   osv-scanner scan --config osv-scanner.toml -L uv.lock -L web/frontend/pnpm-lock.yaml
   .venv/bin/pip-audit -r requirements-ci.txt --extra-index-url https://download.pytorch.org/whl/cu128
   ```

3. **Secret & Key Exposure Scan**:
   Verify no API keys, credentials, or private keys are exposed:
   ```bash
   if command -v gitleaks &> /dev/null; then
       gitleaks detect --source . --no-git -v
   elif [[ -x .ci-bin/gitleaks ]]; then
       .ci-bin/gitleaks detect --source . --no-git -v
   fi
   ```

4. **Dynamic & Logical Penetration Testing**:
   - **Path Traversal**: Run `tests/run_path_traversal_tests.py`.
   - **Input Sanitization**: Run `tests/run_sanitizer_tests.py`.
   - **Auth & Token Handling**: Run `tests/run_auth_compat_tests.py`.
   - **RBAC & Authorization**: Audit route handlers in `routes/` for permission decorators.

5. **Remediation & Proof**:
   - Apply surgical security fixes to eliminate discovered vulnerabilities.
   - Re-run security tools to verify zero vulnerabilities remain.
