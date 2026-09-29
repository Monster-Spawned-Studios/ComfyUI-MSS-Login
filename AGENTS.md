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

- **`dotenvx` warning**: The extension tries to run `dotenvx` and `dotenvx-postinstall` at startup. These are optional; the warning is non-fatal. The `python-dotenvx` pip package is installed but the standalone binary install may fail in some environments.
- **GPU-less VMs**: CUDA PyTorch wheels will fail to import `comfy.model_management` with "Torch not compiled with CUDA enabled" or "Found no NVIDIA driver". Install CPU-only PyTorch (see above). **Important**: `uv sync` will always reinstall CUDA torch on Linux (per `pyproject.toml` sources). You must re-run the CPU pip install after every `uv sync`.
- **`install_deps.py` auto-install**: On Linux, the extension auto-installs CUDA PyTorch from `requirements_cuda.txt` on every startup (with captured output). This can be slow on first boot or when CPU-only torch is installed. Deps are already satisfied in Docker images with CUDA torch.
- Always use the `.venv` Python (`.venv/bin/python`) per `.agent/rules/python-venv.mdc`.

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
- **Krita AI Diffusion and other JWT/API clients**: Preserve Bearer token, cookie, and query-token auth; do not break `POST /login` or `POST /mss-login/generate_token` (including JSON bodies) without a compat layer.
- **Backwards compatibility (required)**: Follow [`.agents/rules/backwards-compatibility.mdc`](.agents/rules/backwards-compatibility.mdc). Changes must work with the previous release or ship an explicit migration / dual-read / dual-verify path so upgrades do not brick existing installs (auth, DB encryption, config defaults, on-disk paths, API contracts).

### Do not auto-commit or auto-push

Never `git commit` or `git push` unless the user explicitly asks to commit, push, or create a PR in that turn. Completing a task is not permission to publish. Leave local changes uncommitted and unpushed; summarize what changed and wait. If the user does ask to commit, follow the Commit signing section above.

### Known caveats

- Always use the `.venv` Python (`.venv/bin/python`) per `.agent/rules/python-venv.mdc`.
- **Do not install or run ComfyUI** in the Cloud VM — it is unnecessary and wastes resources. All tests and lint work without it.

## Learned User Preferences

- Prefer consolidating MSS-Login UI into the ComfyUI status-bar avatar/logout menu; avoid a separate floating button that forces a second click, and do not duplicate menu entries.
- When hiding Models or other sidebar UI by permission, mirror the existing console permission-hide pattern rather than inventing a new approach.
- Nested MSS-Login configuration pages should offer back navigation to the main MSS-Login config menu without closing and reopening the dialog.
- For local ComfyUI debugging, support a manually specified test-instance path plus a toggleable auto-install when that path has no `main.py`.
- Agent-agnostic project rules belong in `.agents/rules/` (cross-agent); keep AGENTS.md in sync when adding always-on rules.

## Learned Workspace Facts

- Runtime per-user data belongs under `DATA_DIR/users` (`MSS_LOGIN_DATA_DIR` or `~/.comfyui-mss-login`); do not write capital `Users/` under the extension root—repo `users/` is reserved for shipped defaults.
- Password and credential fields must skip XSS `sanitize_input` mutation; `check_username_password` dual-verifies legacy XSS-mutated hashes and rehashes to the raw password on success.
- When `SECRET_KEY` is unset, reuse the persisted `.ephemeral_secret_key` across restarts so SQLCipher and JWT stay stable; do not rotate a new ephemeral key every process start.
- Sanitizer middleware consumes the body via `request.post()`; multipart handlers (for example avatar upload) must reuse that parsed form instead of calling `request.multipart()` again.
- Local ComfyUI debug settings live in gitignored `.vscode/comfyui-test.local.json` (path, `autoInstall`, flavor); see `.vscode/comfyui-test.settings.example.json`.
- Auth compatibility coverage is in `tests/run_auth_compat_tests.py` (password XSS exemption, legacy dual-verify, ephemeral key reuse, JSON credentials) and is wired into CI.
