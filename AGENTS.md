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

### Commit signing

When committing and pushing, sign commits with SSH if credentials are available:

- **`MSS_SSH_PRIV_KEY`**: SSH private key for commit signing (full key content).
- **`MSS_SSH_PASS`**: Passphrase to decrypt the key. Never log this value.

**If both variables are set and non-empty:**

1. Write the key to a temporary file (e.g. `~/.ssh/mss_signing_key` or `$TMPDIR/mss_signing_key`).
2. Set `GIT_SSH_COMMAND` or configure `git config gpg.format ssh` and `git config user.signingKey` to the key path.
3. Use `git commit -S` (or `git config commit.gpgsign true`) when committing.
4. Remove the temporary key file after pushing.

**If either variable is unset, null, or invalid:** Commit and push normally without signing.

### ComfyUI and ecosystem compatibility

When editing this extension, maintain compatibility with:

- **ComfyUI**: Be version-agnostic where possible; prefer stable, documented APIs (`PromptServer` routes, `folder_paths`, node `INPUT_TYPES`/`RETURN_TYPES`). Avoid private attributes or nightly-only behavior. Target latest ComfyUI and the [ComfyUI_frontend](https://github.com/Comfy-Org/ComfyUI_frontend) package.
- **Comfy Portal** ([comfy-portal](https://github.com/ShunL12324/comfy-portal)): iOS/Android app uses standard ComfyUI HTTP and WebSocket APIs (prompt, queue, history). Use standard APIs so workflows can be executed and synced from the app.
- **comfy-portal-endpoint** ([comfy-portal-endpoint](https://github.com/ShunL12324/comfy-portal-endpoint)): Provides workflow list/get/save/convert; uses a headless browser that must load the real ComfyUI frontend. Avoid blocking the main ComfyUI page from loading; if auth is added, consider allowing unauthenticated access to minimal frontend assets required for conversion, or document that Portal workflow sync will not work when auth is enabled.

### Commit and push when task is complete

Once a task is completed, automatically commit and push all changes to the current branch (typically `development` for PR #15). Follow the Commit signing section above. Do not leave uncommitted changes when the work is done.

### Known caveats

- Always use the `.venv` Python (`.venv/bin/python`) per `.agent/rules/python-venv.mdc`.
- **Do not install or run ComfyUI** in the Cloud VM — it is unnecessary and wastes resources. All tests and lint work without it.
