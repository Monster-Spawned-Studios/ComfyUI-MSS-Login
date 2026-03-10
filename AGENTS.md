# AGENTS.md

## Cursor Cloud specific instructions

### Overview

This is a **ComfyUI custom-node extension** (MSS-Login) that adds RBAC, JWT auth, NSFW detection, and admin UI to ComfyUI. It is not a standalone app — it requires ComfyUI as its host.

### Do NOT install or run ComfyUI

**Do not install ComfyUI, `comfy-cli`, or attempt to launch a ComfyUI server.** The Cloud VM has no GPU, and downloading/running ComfyUI (~1 GB+ of dependencies) is a waste of time and resources. All tests and lint checks run without ComfyUI. Focus only on linting, testing, and editing the extension code itself.

### Python version

The project requires **Python 3.13+** (`pyproject.toml` → `requires-python = ">=3.13"`). The venv is managed by `uv`.

### Dependency management

- Package manager: **uv** (with `pyproject.toml` / `uv.lock`).
- Dev dependencies: `uv sync --group dev` (includes `pytest`, `pip-audit`). `ruff` is not in the dev group; install it separately with `.venv/bin/pip install ruff` after sync.
- **Do not** use `uv sync --group comfyui` or install `comfy-cli` — ComfyUI is not needed (see above).
- PyTorch: `pyproject.toml` directs Linux/Windows to CUDA wheels (`cu128`). The Cloud VM has no GPU, so after `uv sync` you must replace them with CPU-only wheels: `.venv/bin/pip install torch torchvision torchaudio --index-url https://download.pytorch.org/whl/cpu --force-reinstall`.
- System dependency: `libsqlcipher-dev` is needed for the `sqlcipher3` Python package.

### Running tests

Tests are documented in `tests/README.md`. Quick reference:

```bash
# All CI tests + lint (path traversal, sanitizer, ruff check, ruff format):
.venv/bin/python tests/run_ci.py

# Tests only (skip lint):
.venv/bin/python tests/run_ci.py --no-lint

# Individual test runners (no ComfyUI needed):
.venv/bin/python tests/run_path_traversal_tests.py
.venv/bin/python tests/run_sanitizer_tests.py
```

### Linting

```bash
.venv/bin/python -m ruff check . --exclude .venv
.venv/bin/python -m ruff format --check . --exclude .venv
```

Note: the codebase has pre-existing ruff check errors (12 errors: F403, F405, F811, E722, F541) and format drift (64 files). These are not introduced by setup.

### Known caveats

- Always use the `.venv` Python (`.venv/bin/python`) per `.agent/rules/python-venv.mdc`.
- **Do not install or run ComfyUI** in the Cloud VM — it is unnecessary and wastes resources. All tests and lint work without it.
