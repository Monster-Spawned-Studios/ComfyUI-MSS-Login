# Installation

## Requirements

- ComfyUI with **Python 3.13+** (see `pyproject.toml`)
- Dependencies are installed automatically on node load when `auto_install_deps` is enabled (default: `true` in `config.json`)

## Automatic dependency installation

On startup, MSS-Login installs dependencies into **ComfyUI's Python environment** (`sys.executable`):

1. **UV first** — `uv pip install` using `pyproject.toml` (respects CUDA vs Metal PyTorch indexes), or the platform requirements file:
   - Linux / Windows: `requirements_cuda.txt` (CUDA wheels via PyTorch index)
   - macOS: `requirements_metal.txt`
2. **pip fallback** — if UV is missing or fails, the same requirements file is installed with `pip install -r`.

Optional **dotenvx** CLI is installed best-effort via `python-dotenvx` postinstall; failure does not block the node.

### Disable auto-install

- **Config:** set `"auto_install_deps": false` in your data-dir `config.json`
- **Environment:** set `AUTO_INSTALL_DEPS=0` (or `false` / `no`) to disable even when config is `true`

### Manual install

From the extension directory, using ComfyUI's Python:

```bash
# Preferred (UV)
/path/to/comfy/python -m uv pip install --python /path/to/comfy/python .

# Fallback (pip)
/path/to/comfy/python -m pip install -r requirements_cuda.txt \
  --extra-index-url https://download.pytorch.org/whl/cu128
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
