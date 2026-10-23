---
name: bug-hunter
description: Walk through ComfyUI-MSS-Login, identify bugs across auth, storage, routes, and UI, and apply targeted fixes.
---

# Bug Hunter Skill

Use this skill when auditing, diagnosing, or repairing bugs across the ComfyUI-MSS-Login codebase.

## When to Trigger
- "Audit the project for bugs"
- "Find and fix bugs in this codebase"
- "Run bug hunter"
- When preparing a release or investigating mysterious test failures.

## Execution Workflow

1. **Verify Environment**:
   Ensure commands are run using the repository virtual environment (`.venv/bin/python`). Never hardcode full system paths.

2. **Run Static Code Checks**:
   Check for syntax errors and linter violations:
   ```bash
   .venv/bin/python -m ruff check . --exclude .venv
   ```

3. **Run CI and Unit Test Suites**:
   Execute the full automated test suite:
   ```bash
   .venv/bin/python tests/run_ci.py --no-lint
   ```

4. **Audit Critical Functional Areas**:
   - **Path Traversal & Sanitization**: Verify `utils/path_safety.py` and `tests/run_path_traversal_tests.py`.
   - **RBAC & Authorization**: Inspect route decorators in `routes/`.
   - **Database & Schema**: Verify SQLite migrations with `tests/run_schema_drift_tests.py`.
   - **Authentication**: Run `tests/run_auth_compat_tests.py` to ensure backwards compatibility.

5. **Apply Targeted Fixes**:
   - Make minimal, surgical edits to resolve discovered bugs.
   - Re-run the corresponding test suite immediately to confirm the fix.
   - Do not make broad refactorings outside the scope of the bug.
