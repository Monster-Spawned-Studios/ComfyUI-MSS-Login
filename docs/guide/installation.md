# Installation

## Requirements

- ComfyUI with **Python 3.13+** (see `pyproject.toml`)
- Dependencies are installed automatically on node load when `auto_install_deps` is enabled (default: `true` in `config.json`)

## Automatic dependency installation

On startup, MSS-Login installs dependencies into **ComfyUI's Python environment** (`sys.executable`):

1. **Detect the PyTorch backend** (before downloading wheels):
   - **macOS:** Metal / MPS via PyPI (`requirements_metal.txt`). CUDA indexes are not used on darwin.
   - **Linux / Windows:** read the NVIDIA driver (`libcuda` / `nvidia-smi`). Prefer **cu130** (`https://download.pytorch.org/whl/cu130`) when the driver reports CUDA 13+, otherwise **cu128**, otherwise **CPU** (`https://download.pytorch.org/whl/cpu`).
   - Overrides: `USE_CPU=1` forces CPU wheels; `USE_CUDA=1` prefers CUDA even if auto-detect is inconclusive.
2. **UV first** — `uv pip install` using the matching requirements file (`requirements_metal.txt`, `requirements_cuda.txt`, or `requirements_cpu.txt`), then `pyproject.toml` if needed.
3. **pip fallback** — if UV is missing or fails, the same requirements file is installed with `pip install -r`.

Optional **dotenvx** CLI is installed best-effort via `python-dotenvx` postinstall; failure does not block the node.

### Disable auto-install

- **Config:** set `"auto_install_deps": false` in your data-dir `config.json`
- **Environment:** set `AUTO_INSTALL_DEPS=0` (or `false` / `no`) to disable even when config is `true`

### Manual install

From the extension directory, using ComfyUI's Python:

```bash
# Preferred (UV)
/path/to/comfy/python -m uv pip install --python /path/to/comfy/python .

# macOS (Metal / MPS from PyPI)
/path/to/comfy/python -m pip install -r requirements_metal.txt

# Linux/Windows CUDA 13+
/path/to/comfy/python -m pip install -r requirements_cuda.txt \
  --extra-index-url https://download.pytorch.org/whl/cu130

# Linux/Windows CUDA 12.x
/path/to/comfy/python -m pip install -r requirements_cuda.txt \
  --extra-index-url https://download.pytorch.org/whl/cu128

# Linux/Windows CPU only
/path/to/comfy/python -m pip install -r requirements_cpu.txt \
  --extra-index-url https://download.pytorch.org/whl/cpu
```

See [`utils/install_deps.py`](../../utils/install_deps.py) for the full install logic.

## Steps

1. **Install the node**
   Place the extension in ComfyUI's custom nodes directory:

   ```
   ComfyUI/custom_nodes/ComfyUI-MSS-Login/
   ```

   Or clone:

   ```bash
   cd ComfyUI/custom_nodes
   git clone <repository-url> ComfyUI-MSS-Login
   ```

2. **Restart ComfyUI** so the node loads.

3. **First launch**
   On first run, register the initial admin user when prompted.

4. **Configure**
   Open **Settings → mss-login** to configure groups, IP rules, user environment, and NSFW settings.

## Optional: Encrypted SQLite and recovery

- For encrypted SQLite (optional), install `argon2-cffi` and `sqlcipher3` (see [Configuration](configuration.md)).
- For MFA recovery (e.g. after changing `SECRET_KEY`), see the recovery endpoint in the [API Reference](../api-reference/endpoints.md).
