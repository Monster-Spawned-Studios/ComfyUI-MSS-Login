# Tests for ComfyUI-MSS-Login

Use the project **.venv** for all test commands (see [.agent/rules/python-venv.mdc](../.agent/rules/python-venv.mdc)).

## Quick start (CI and local)

Run all CI steps (path traversal, sanitizer, ruff check, ruff format):

```powershell
# Windows
.\.venv\Scripts\python tests/run_ci.py
```

```bash
# Unix / macOS
.venv/bin/python tests/run_ci.py
```

- `--no-lint` — skip ruff check and format (faster; use when lint runs separately).
- `--path-only` — run only path traversal tests.

Exit code: **0** if all steps pass, **1** otherwise (suitable for CI).

## Individual test runners

These run without loading ComfyUI or `folder_paths`, so they work in CI and on machines where ComfyUI is not installed.

| Script | What it tests |
|--------|----------------|
| `run_path_traversal_tests.py` | `utils.path_safety`: path traversal prevention, safe filenames/folders, `resolve_path_under`, attack vectors. |
| `run_sanitizer_tests.py` | `utils.input_sanitizer` and `utils.validate`: username/password sanitization and validation. |
| `run_cpe_workflow_tests.py` | Per-user Comfy Portal Endpoint workflow list/get/save helpers (no ComfyUI). |
| `run_install_deps_plan_tests.py` | PyTorch backend detection (Metal / cu130 / cu128 / CPU). |
| `run_user_isolation_tests.py` | Per-user output-dir segments, `/prompt` vs `/api/prompt`, queue user stamps. |
| `run_avatar_tests.py` | Avatar upload sanitization (PNG re-encode, SVG/HTML reject, guest block). |

Examples:

```powershell
.\.venv\Scripts\python tests/run_path_traversal_tests.py
.\.venv\Scripts\python tests/run_sanitizer_tests.py
```

## CI/CD usage

1. **Install deps** (including dev): `uv sync --group dev` (or your normal install that includes pytest, ruff).
2. **Run tests**:  
   `.\.venv\Scripts\python tests/run_ci.py`  
   Or without lint:  
   `.\.venv\Scripts\python tests/run_ci.py --no-lint`
3. Use exit code for pass/fail (0 = pass, 1 = fail).

Example (GitHub Actions style):

```yaml
- name: Run tests
  run: ./.venv/Scripts/python tests/run_ci.py
  # Windows: .\.venv\Scripts\python tests/run_ci.py
```

For a **path-traversal-only** gate:

```powershell
.\.venv\Scripts\python tests/run_ci.py --path-only --no-lint
```

## Pytest (when ComfyUI env is available)

The project has a `test_path_traversal.py` module written for pytest. Pytest collection may pull in the root package (and thus ComfyUI deps like `folder_paths`). For reliable CI without ComfyUI, use the standalone runners above. With a full ComfyUI environment you can run:

```powershell
.\.venv\Scripts\python -m pytest tests/ -v
```

## Test coverage

- **Path safety**: safe filename/folder segment, basename extraction, path resolution under a base dir, path-under check, and explicit path traversal attack vectors (e.g. `..`, encoded slashes, long paths, unicode).
- **Sanitizer / validate**: username and password sanitization (null bytes, length, allowed chars), and `validate_username` / `validate_password` rules.
