# CLAUDE.md

## Project Overview

**ComfyUI-MSS-Login** is a ComfyUI custom-node extension that adds RBAC, JWT authentication, NSFW detection, MFA, and an admin UI to ComfyUI. It is **not a standalone app** — it requires ComfyUI as its host at runtime, but all tests and lint checks run without it.

---

## Critical Constraints

### Do NOT install or run ComfyUI

The CI/cloud environment has no GPU. Installing ComfyUI (~1 GB+ deps) wastes resources. All tests and lint run without it. Never run `comfy-cli` or `uv sync --group comfyui`.

### Always use the project `.venv`

```bash
.venv/bin/python          # Unix/macOS
.venv\Scripts\python.exe  # Windows
```

Never use bare `python`, `pytest`, or `ruff` — always prefix with `.venv/bin/` (Unix) or `.venv\Scripts\` (Windows).

### Python version: 3.13+

Enforced in `pyproject.toml` (`requires-python = ">=3.13"`). Target `py313` for Ruff.

---

## Dependency Management

Package manager: **uv** (`pyproject.toml` / `uv.lock`).

```bash
# Dev setup (includes pytest, pip-audit, ruff)
uv sync --group dev

# NOTE on Linux/Windows: uv installs CUDA PyTorch by default (cu128).
# On GPU-less VMs, replace with CPU-only after every uv sync:
.venv/bin/pip install torch torchvision torchaudio \
  --index-url https://download.pytorch.org/whl/cpu --force-reinstall
```

**System dependency required:** `libsqlcipher-dev` (for `sqlcipher3`).

**dotenvx warning:** The extension tries to run `dotenvx` at startup — this is non-fatal if the standalone binary is missing.

---

## Running Tests

```bash
# All CI steps (path traversal + sanitizer tests + ruff check + ruff format)
.venv/bin/python tests/run_ci.py

# Skip lint (faster)
.venv/bin/python tests/run_ci.py --no-lint

# Individual runners (no ComfyUI needed)
.venv/bin/python tests/run_path_traversal_tests.py
.venv/bin/python tests/run_sanitizer_tests.py
.venv/bin/python tests/run_navigation_detection_tests.py
```

Exit code: `0` = pass, `1` = fail. See `tests/README.md` for full details.

---

## Linting

```bash
.venv/bin/python -m ruff check . --exclude .venv
.venv/bin/python -m ruff format --check . --exclude .venv
```

Config in `pyproject.toml` (`[tool.ruff]`). Key settings:

- `target-version = "py313"`, `line-length = 100`
- `indent-style = "tab"`, `line-ending = "lf"`
- Ignored rules: `ARG001`, `PLR0913`, `F401`, `F841`, `E402`

**Note:** The codebase has ~12 pre-existing ruff errors (F403, F405, F811, E722, F541) and format drift in ~64 files. Do not treat these as newly introduced.

---

## Codebase Structure

```plaintext
__init__.py          # Extension entry point; registers middlewares, routes, node interceptor
nodes.py             # ComfyUI node definitions (NODE_CLASS_MAPPINGS)
api.py               # Public API exported to other extensions
globals.py           # Global singletons: app, routes, logger, jwt_auth, access_control, etc.
constants.py         # All config constants loaded from config.json / env vars
mss_login.py         # Core login logic

routes/              # aiohttp route handlers
  auth.py            # Login, logout, register, token generation
  admin.py           # Admin endpoints
  user.py            # User management
  me.py              # Current-user endpoints
  mfa.py             # MFA setup and verification
  recovery.py        # Account recovery
  models.py          # Model listing/filtering
  model_download.py  # Model download endpoints
  workflow_routes.py # Workflow storage interception
  s3.py              # S3 routes (experimental)
  static.py          # Static file serving
  news.py            # News feed
  debug.py           # Debug endpoints

utils/               # All business logic and security utilities
  access_control.py  # RBAC, folder access, prompt queue patching
  jwt_auth.py        # JWT creation, validation, middleware
  users_db.py        # User database (SQLite/PostgreSQL/MySQL)
  ip_filter.py       # IP whitelist/blacklist middleware
  path_safety.py     # Path traversal prevention
  input_sanitizer.py # Input sanitization
  validate.py        # Username/password validation
  sanitizer.py       # Request sanitizer middleware
  sfw_intercept/     # NSFW detection and image blocking
    nsfw_guard.py
    node_interceptor.py
    reactor_sfw_intercept.py
  csp.py             # Content-Security-Policy middleware
  remote_api_guard.py # Remote API auth guard
  encryption.py      # Encryption utilities
  install_deps.py    # Auto-installs PyTorch on Linux at startup
  updater.py         # Background update checker
  s3_mounter.py      # S3 mount (experimental)
  watcher.py         # File watcher

web/                 # Frontend assets (ComfyUI WEB_DIRECTORY = "web")
  html/              # Login, register, MFA, token generation pages
  js/                # Auth, MFA, logout, loading, common scripts
  css/styles.css     # Styles
  mss_login_settings.js  # ComfyUI settings extension
  legacy_admin.js    # Legacy admin UI

tests/
  run_ci.py                      # Master CI runner
  run_path_traversal_tests.py    # Path safety tests
  run_sanitizer_tests.py         # Input sanitizer tests
  run_navigation_detection_tests.py
  test_path_traversal.py         # pytest-compatible path traversal tests
```

---

## Middleware Stack (execution order in `__init__.py`)

1. `force_https` (if `FORCE_HTTPS=true`)
2. `ip_filter` — IP whitelist/blacklist
3. `sanitizer` — request sanitization
4. `api_browser_redirect` — redirects API calls from browsers
5. `timeout` — rate limiting on `/login`, `/register`, `/mfa`
6. `remote_api_guard` — blocks unauthenticated remote API access
7. `jwt_auth` — JWT/Bearer/cookie auth; sets `request["user"]`
8. `workflow_interceptor` — user resolution, prompt model validation, NSFW image blocking
9. `model_filter` — filters `/models` and `/embeddings` per user permissions
10. `folder_access_control` (if `SEPERATE_USERS=true`)
11. `mss_login` — main RBAC enforcement
12. `csp` — Content-Security-Policy headers

---

## Key Conventions

### Security

- **No `eval`/`exec`** on user/workflow input (RCE risk).
- **No runtime `subprocess` pip install** — use declared dependencies only.
- **No inline JS/CSS** in HTML — use external files from `WEB_DIRECTORY`.
- Use parameterized queries for all DB access.
- Validate and sanitize all user input via `utils/input_sanitizer.py` and `utils/validate.py`.
- Use `utils/path_safety.py` (`is_safe_filename`, `resolve_path_under`) for any file path operations.
- Never commit secrets or PII; use env vars or `python-dotenvx`.
- Store user data/config under `DATA_DIR` (env: `MSS_LOGIN_DATA_DIR`), not next to source.

### ComfyUI Compatibility

- Use stable ComfyUI APIs: `PromptServer` routes, `folder_paths`, node `INPUT_TYPES`/`RETURN_TYPES`.
- Prefer feature detection over hard version checks.
- Do not block or intercept the main ComfyUI page from loading (breaks Comfy Portal headless browser).
- Use `app.registerExtension(...)` and `app.extensionManager.*` APIs for frontend UI — not `window.prompt`/`confirm`/`alert` (unavailable in ComfyUI Desktop).

### Comfy Portal Compatibility

- Comfy Portal (iOS/Android) uses standard ComfyUI HTTP/WebSocket APIs.
- `comfy-portal-endpoint` uses a headless browser that loads the real ComfyUI frontend — auth must not prevent this. If auth is added, allow unauthenticated access to minimal frontend assets for workflow conversion, or document the limitation.

### Code Style

- Python 3.13+, type hints where helpful, `pathlib` for paths, explicit encoding for file I/O.
- Tabs for indentation, LF line endings (enforced by Ruff and `.editorconfig`).
- `known-first-party` for isort: `utils`, `utils.sfw_intercept`, `routes`.

---

## Commit Signing (SSH)

If `MSS_SSH_PRIV_KEY` and `MSS_SSH_PASS` are both set and non-empty:

1. Write key to a temp file (e.g. `~/.ssh/mss_signing_key`).
2. Configure git: `gpg.format=ssh`, `user.signingKey=<path>`.
3. Commit with `git commit -S`.
4. Remove the temp key file after pushing.

If either variable is unset or empty: commit and push normally without signing.

**Never log `MSS_SSH_PASS`.**

---

## Environment Variables (key ones)

| Variable                      | Purpose                                                |
| ----------------------------- | ------------------------------------------------------ |
| `MSS_LOGIN_DATA_DIR`          | External data directory (config, DB, logs)             |
| `HOST_BASE_URL`               | Base URL for the server (persisted to app settings DB) |
| `SECRET_KEY`                  | JWT signing key                                        |
| `FORCE_HTTPS`                 | Redirect HTTP → HTTPS                                  |
| `REQUIRE_AUTH_FOR_REMOTE_API` | Block unauthenticated remote API calls                 |
| `EXPERIMENTAL_FEATURES`       | Enable S3 mount/sync                                   |
| `MSS_SSH_PRIV_KEY`            | SSH private key for commit signing                     |
| `MSS_SSH_PASS`                | Passphrase for SSH signing key (never log)             |

---

## Development Workflow

1. Make changes on the designated feature branch (see session context).
2. Run tests: `.venv/bin/python tests/run_ci.py`
3. Fix any lint/test failures before committing.
4. Commit and push when the task is complete. Do not leave uncommitted changes.
5. Sign commits with SSH if `MSS_SSH_PRIV_KEY` and `MSS_SSH_PASS` are available.

---

## CI/CD

Workflows in `.github/workflows/`:

- `code-quality.yml` — Ruff lint and format check (triggers on push/PR to `production`)
- `security.yml` — Security scanning
- `pull-request.yml` — PR checks
- `spell-check.yml` — Spell check (ignore list: `.codespellignore`)
- `publish.yml` — Comfy Registry publish
- `create-release.yml` — Release automation
- `docs.yml` — MkDocs documentation build
