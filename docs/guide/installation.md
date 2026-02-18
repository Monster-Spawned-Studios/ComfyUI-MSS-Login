# Installation

## Requirements

- ComfyUI with Python 3.x
- Dependencies are installed automatically by the node (see `utils/install_deps.py`)

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

- For encrypted SQLite (optional), install `argon2-cffi` and `pysqlcipher3` (see [Configuration](configuration.md)).
- For MFA recovery (e.g. after changing `SECRET_KEY`), see the recovery endpoint in the [API Reference](../api-reference/endpoints.md#recovery).
