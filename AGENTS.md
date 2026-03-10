# AGENTS.md

## Cursor Cloud specific instructions

### Overview

This is a **ComfyUI custom-node extension** (MSS-Login) that adds RBAC, JWT auth, NSFW detection, and admin UI to ComfyUI. It is not a standalone app — it requires ComfyUI as its host.

### Python version

The project requires **Python 3.13+** (`pyproject.toml` → `requires-python = ">=3.13"`). The venv is managed by `uv`.

### Dependency management

- Package manager: **uv** (with `pyproject.toml`; `uv.lock` is not committed — uv generates it on first sync).
- Dev dependencies: `uv sync --group dev` (includes `pytest`, `pip-audit`). **Note**: `ruff` is NOT in the dev group — install separately with `pip install ruff` after `uv sync`.
- ComfyUI CLI: `uv sync --group comfyui` (installs `comfy-cli`). Note: syncing one group removes packages from the other. For a full dev setup, install dev group first, then use `pip install` for ComfyUI's own requirements on top.
- PyTorch: `pyproject.toml` directs Linux/Windows to CUDA wheels (`cu128`). On GPU-less VMs, replace with CPU-only wheels: `pip install torch torchvision torchaudio --index-url https://download.pytorch.org/whl/cpu --force-reinstall`.
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

### Running ComfyUI with the extension

1. ComfyUI is installed at `ComfyUI/` via `comfy-cli`.
2. The extension is symlinked: `ComfyUI/custom_nodes/mss-login -> /workspace`.
3. A `.env` file is needed (copy from `.env.example`, set `SECRET_KEY`).
4. Launch: `cd ComfyUI && ../.venv/bin/python main.py --cpu --listen 0.0.0.0 --port 8188`

### Known caveats

- **`dotenvx` warning**: The extension tries to run `dotenvx` and `dotenvx-postinstall` at startup. These are optional; the warning is non-fatal. The `python-dotenvx` pip package is installed but the standalone binary install may fail in some environments.
- **GPU-less VMs**: CUDA PyTorch wheels will fail to import `comfy.model_management` with "Torch not compiled with CUDA enabled" or "Found no NVIDIA driver". Install CPU-only PyTorch (see above). **Important**: `uv sync` will always reinstall CUDA torch on Linux (per `pyproject.toml` sources). You must re-run the CPU pip install after every `uv sync`.
- **`install_deps.py` auto-install**: On Linux, the extension auto-installs CUDA PyTorch from `requirements_cuda.txt` on every startup (with captured output). This can be slow on first boot or when CPU-only torch is installed. Deps are already satisfied in Docker images with CUDA torch.
- Always use the `.venv` Python (`.venv/bin/python`) per `.agent/rules/python-venv.mdc`.

### Docker storage (`sombi/comfyui:base-torch2.8.0-cu128`)

The default data directory is `~/.comfyui-mss-login/` (`/root/.comfyui-mss-login` in Docker). In the `sombi/comfyui` image, only `/workspace` is volume-mounted and persistent. **Set `MSS_LOGIN_DATA_DIR=/workspace/.comfyui-mss-login`** so data survives container recreation. See `utils/data_dir.py` for the full path logic.
