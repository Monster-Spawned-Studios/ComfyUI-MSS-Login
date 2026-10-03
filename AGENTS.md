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

When committing and pushing, sign commits with SSH if credentials are available (if not, commit and push normally using the system SSH agent and if that fails, commit and push normally without signing):

- **`MSS_SSH_PRIV_KEY`**: SSH private key for commit signing (full key content).
- **`MSS_SSH_PASS`**: Passphrase to decrypt the key. Never log this value.

### ComfyUI and ecosystem compatibility

When editing this extension, maintain compatibility with:

- **ComfyUI**: Be version-agnostic where possible; prefer stable, documented APIs (`PromptServer` routes, `folder_paths`, node `INPUT_TYPES`/`RETURN_TYPES`). Avoid private attributes or nightly-only behavior. Target latest ComfyUI and the [ComfyUI_frontend](https://github.com/Comfy-Org/ComfyUI_frontend) package.
- **Comfy Portal** ([comfy-portal](https://github.com/ShunL12324/comfy-portal)): iOS/Android app uses standard ComfyUI HTTP and WebSocket APIs (prompt, queue, history, view, ws) plus CPE workflow paths. Authenticate with Bearer / `?token=`; list/get/save/run work without a public login-wall bypass. UI→API convert uses MSS embedded Playwright with JWT/Bearer injection (`uv sync --group cpe-convert` + `playwright install chromium`); see [docs/integrations/comfy-portal.md](docs/integrations/comfy-portal.md).
- **comfy-portal-endpoint** ([comfy-portal-endpoint](https://github.com/ShunL12324/comfy-portal-endpoint)): Optional sibling. Prefer MSS embedded convert; uninstall sibling CPE to avoid dual Playwright pools. Stock CPE’s unauthenticated `page.goto(/)` fails under the login wall — do **not** add a public frontend bypass for it.
- **Krita AI Diffusion and other JWT/API clients**: Preserve Bearer token, cookie, and query-token auth; do not break `POST /login` or `POST /mss-login/generate_token` (including JSON bodies) without a compat layer.
- **Backwards compatibility (required)**: Follow [`.agents/rules/backwards-compatibility.mdc`](.agents/rules/backwards-compatibility.mdc). Changes must work with the previous release or ship an explicit migration / dual-read / dual-verify path so upgrades do not brick existing installs (auth, DB encryption, config defaults, on-disk paths, API contracts).

### Do not auto-commit or auto-push

Never `git commit` or `git push` unless the user explicitly asks to commit, push, or create a PR in that turn. Completing a task is not permission to publish. Leave local changes uncommitted and unpushed; summarize what changed and wait. If the user does ask to commit, follow the Commit signing section above.

For significant change sets (~500+ lines) or multi-topic dumps, invoke **`/commit-multi-changes`** and follow [`.agents/skills/commit-multi-changes/SKILL.md`](.agents/skills/commit-multi-changes/SKILL.md): propose a topic-scoped multi-stage commit plan, wait for approval, then commit sequentially. Do not push unless asked.

### Known caveats

- Always use the `.venv` Python (`.venv/bin/python`) per `.agent/rules/python-venv.mdc`.
- **Do not install or run ComfyUI** in the Cloud VM — it is unnecessary and wastes resources. All tests and lint work without it.

## Learned User Preferences

- Prefer consolidating MSS-Login UI into the ComfyUI status-bar avatar/logout menu; avoid a separate floating button that forces a second click, and do not duplicate menu entries.
- When hiding Models or other sidebar UI by permission, mirror the existing console permission-hide pattern rather than inventing a new approach.
- Nested MSS-Login configuration pages should offer back navigation to the main MSS-Login config menu without closing and reopening the dialog.
- For local ComfyUI debugging, support a manually specified test-instance path plus a toggleable auto-install when that path has no `main.py`.
- Agent-agnostic project rules belong in `.agents/rules/` (cross-agent); keep AGENTS.md in sync when adding always-on rules.
- When asked to commit, split changes into small, digestible, topic-scoped commits rather than one large dump; leave push to the user after review unless they explicitly ask to push.
- Only the owner (last-owner / admin-owner) may edit administrator group permissions; admins must not unlock that matrix for themselves.
- Owner-only user registration belongs in the avatar menu and MSS-Login dialog (with role chosen at create time); do not force owners through a separate `/register` navigation for that flow.
- Keep MSS-Login purple/red branding (`#9660fa` and claw logo) for Login and Security Policy UI; do not switch accents to blue or Civicomfy terracotta; keep login Remember Me compact/inline (not a full-width stretched control).
- Prefer a responsive multi-column card grid for Security Policy home section launchers across desktop widths (not a single stacked column).
- Update notices on login and `/loading` should reuse the MSS-Login theme; tell non-updaters to notify an admin; show session-aware Update now only for users with explicit update permission (`can_update_mss_login`).
- Prefer MSS `/loading` as a post-login interstitial before navigating to ComfyUI `/`; do not replace ComfyUI’s in-app splash (fragile across ComfyUI_frontend / PyPI versions).

## Learned Workspace Facts

- Runtime per-user data belongs under `DATA_DIR/users` (lowercase; `MSS_LOGIN_DATA_DIR` or `~/.comfyui-mss-login`). Never create capital `Users/`; migrate leftover capital trees into `users/` and remove them. Repo `users/` is reserved for shipped defaults.
- Password and credential fields must skip XSS `sanitize_input` mutation; `check_username_password` dual-verifies legacy XSS-mutated hashes and rehashes to the raw password on success.
- When `SECRET_KEY` is unset, reuse the persisted `.ephemeral_secret_key` across restarts so SQLCipher and JWT stay stable; do not rotate a new ephemeral key every process start.
- Sanitizer middleware consumes the body via `request.post()`; multipart handlers (for example avatar upload) must reuse that parsed form instead of calling `request.multipart()` again.
- Local ComfyUI debug settings live in gitignored `.vscode/comfyui-test.local.json` (path, `autoInstall`, flavor); see `.vscode/comfyui-test.settings.example.json`.
- Auth compatibility coverage is in `tests/run_auth_compat_tests.py` (password XSS exemption, legacy dual-verify, ephemeral key reuse, JSON credentials); schema drift detection lives in `utils/db_schema.py` and `tests/run_schema_drift_tests.py`; both are wired into CI.
- Owner counts as admin for last-admin / `_has_admin` checks; never mint a second owner after bootstrap; owner updates must preserve `owner`+`admin` membership. Ownership transfer requires `confirm_transfer: true` and an explicit UI Transfer ownership action; role dropdown must not include `owner`. `_ensure_owner_assigned` prefers `mss_admin` and demotes duplicate owners.
- App-stored secrets (CivitAI, HuggingFace, ntfy, S3) are Fernet ciphertext in the owner-chosen users DB—not plaintext JSON; per-user CivitAI host preference (`civitai.com`/`civitai.red`) is non-secret prefs only; migrate secrets with dual-read, encrypt, round-trip verify, then clear legacy files.
- Model downloads (MSS-Login and Civicomfy/Manager routes) require `can_download_models`; under model isolation, post-download ACL grants are file-narrow by default, not all-models.
- Experimental features include S3 (AWS S3 and Backblaze B2; boto3 when FUSE is absent; B2 path-style; macOS best-effort) and owner-only login background (`experimental.login_background` / `EXPERIMENTAL_LOGIN_BACKGROUND`; local media under `DATA_DIR`); document under `docs/experimental/` / MkDocs Experimental Settings.
- Keep `.github` and `.gitea` workflows mirrored; end Actions with the reusable ntfy notification job (`if: always()`, `secrets: inherit`); install Gitleaks via `scripts/ci/install-gitleaks.sh` pinned release asset URL (avoid unauthenticated GitHub API latest on shared runners).
- Comfy `WEB_DIRECTORY` is `web/comfyui` only (extension entry JS). Never put `web/frontend` / `node_modules` / `web/dist` under it — Comfy recursively preloads those as extensions (`node:path` CORS / vite preload spam). Dist is served at `/mss-login/dist`. Login CSP `connect-src` must include `HOST_BASE_URL` (and derived `wss://`) plus the request origin so reverse-proxy hosts work.
