# MSS-Login

<p align="center">
  <img src="./web/assets/mss_logo.png" width="220" />
</p>

<p align="center">
  <strong>The next-generation security, governance, permissions, and multi‑user control system for ComfyUI.</strong>
</p>

<p align="center">
  <strong>Version 0.0.1</strong> — Latest release includes Extension Tabs API, IP filtering improvements, and performance optimizations
</p>

---

## Table of Contents

1. [Overview](#overview)
2. [Key Features](#key-features)
3. [Architecture](#architecture)
4. [Installation](#installation)
5. [Documentation](#documentation)
6. [Folder Structure](#folder-structure)
7. [RBAC Roles](#rbac-roles)
8. [UI Enforcement Layer](#ui-enforcement-layer)
9. [Workflow Protection](#workflow-protection)
10. [IP Rules System](#ip-rules-system)
11. [User Environment Tools](#user-environment-tools)
12. [Settings Panel](#settings-panel)
13. [API Endpoints](#api-endpoints)
14. [Backend Components](#backend-components)
15. [Troubleshooting](#troubleshooting)
16. [License](#license)

---

## Overview

**ComfyUI mss_login** is a comprehensive security layer that adds:

- Role‑Based Access Control (RBAC)
- UI element gating
- Workflow save/delete blocking
- Transparent user folder isolation
- IP whitelist and blacklist enforcement
- User environment management utilities
- A modern administrative panel with multiple tabs
- Dynamic theme integration with the ComfyUI dark mode
- Live UI popups, toast notifications, and visual enforcement
- **NSFW Guard API** - Public API for NSFW detection and enforcement
- **Gallery integration** - Manual image flagging and metadata-based tagging
- **Extension Tabs API** - Allow other extensions to add custom tabs to the admin panel

It replaces the older Sentinel system with a faster, cleaner, more modular architecture—fully rewritten for reliability and future expansion.

---

## Key Features

### 🔐 **RBAC Security**

Four roles: **Admin, Power, User, Guest**
Each with configurable permissions stored in `mss_login_groups.json`.

### 🚫 **Save & Delete Workflow Blocking**

Non‑privileged roles cannot:

- Save workflows
- Export workflows
- Overwrite existing workflows
- Delete workflow files

All blocked actions trigger:

- A server‑side 403
- A UI toast popup explaining the denial

### 👁️ **Dynamic UI Enforcement**

mss_login hides or disables:

- Top‑menu items
- Sidebar tabs
- Settings categories
- Extension panels
- File menu operations

Enforcement occurs every 1 second to catch late‑loading UI elements.

### 🌐 **IP Filtering System**

Complete backend implementation:

- Whitelist mode
- Blacklist mode
- Live editing in mss_login settings tab
- Persistent storage via `ip_filter.py`

### 🗂️ **User Environment Tools**

From `user_env.py`:

- Purge a user’s folders
- List user-owned files
- Promote user workflow to default (all user view)
- Delete single user workflow
- Toggle gallery‑folder mode

### 🖥️ **Transparent Themed Admin UI**

The administrative modal features:

- Transparent blurred glass background
- Neon accent tabs
- Integrated logo watermark
- Scrollable permission tables
- Responsive layout

### 🔧 **Watcher Middleware**

A new middleware that detects:

- Forbidden workflow saves
- Forbidden deletes
  And triggers UI-side toast popups through a custom fetch wrapper.

### 🛡️ **NSFW Guard API**

A comprehensive public API that allows other ComfyUI extensions to:

- Check user NSFW viewing permissions
- Validate image tensors, PIL Images, or file paths for NSFW content
- Integrate NSFW protection into custom nodes and extensions
- **Metadata-based tagging system** - Images are tagged with NSFW metadata stored alongside files
- **Gallery integration endpoint** - `/mss-login-gallery/mark-nsfw` for manual image flagging
- **Automatic scanning** - Background scanning of output directory with caching
- **Per-user enforcement** - SFW restrictions apply per-user based on role permissions

See [API_USAGE.md](./readme/API_USAGE.md) for complete documentation and examples.

**Quick Example:**

```python
from api import check_tensor_nsfw, is_sfw_enforced_for_user

# In your custom node
if is_sfw_enforced_for_user():
    if check_tensor_nsfw(image_tensor):
        # Block or replace NSFW content
        image_tensor = torch.zeros_like(image_tensor)
```

**Gallery Integration:**

```javascript
// Mark an image as NSFW from gallery UI
fetch("/mss-login-gallery/mark-nsfw", {
 method: "POST",
 headers: { "Content-Type": "application/json" },
 body: JSON.stringify({
  filename: "image.png",
  is_nsfw: true,
  score: 1.0,
  label: "manual",
 }),
});
```

---

## Architecture

```text
ComfyUI
│
├── mss_login Core
│   ├── access_control.py    → RBAC, path blocking, folder isolation
│   ├── __init__.py          → Route registration, middleware setup
│   ├── api.py               → NSFW Guard API (public interface)
│   ├── globals.py           → Shared server instances, route table
│   ├── constants.py         → Configuration paths
│   ├── routes/
│   │   ├── auth.py          → Login/Register/Token endpoints
│   │   ├── admin.py         → User & Group management, NSFW admin tools
│   │   ├── user.py          → User environment, mark-nsfw endpoint
│   │   ├── static.py        → Asset serving
│   │   └── workflow_routes.py → Workflow protection, NSFW enforcement
│   ├── utils/
│   │   ├── ip_filter.py     → Whitelist/blacklist system
│   │   ├── user_env.py      → User folder management
│   │   ├── sanitizer.py     → Input scrubbing
│   │   ├── logger.py        → Logging hooks
│   │   ├── timeout.py       → Rate limiting
│   │   ├── sfw_intercept/
│   │   │   ├── nsfw_guard.py → NSFW detection, metadata tagging
│   │   │   └── node_interceptor.py → Node-level image interception
│   │   └── reactor_sfw_intercept.py → ReActor SFW patch
│   └── web/
│       ├── js/mss_login_setting.js → UI enforcement + settings panel
│       ├── css/mss-login.css        → Themed UI
│       └── assets/mss_logo.png
│
└── ComfyUI (upstream)
```

---

## Installation

1. Extract mss_login into:

```text
ComfyUI/custom_nodes/mss-login/
```

1. Restart ComfyUI.

2. On first launch, register the initial admin.

3. Open settings → **mss_login** to configure.

---

## Documentation

Full documentation (installation, configuration, **API reference**, and **Extension API** for integrating the node in your projects) is built with [MkDocs](https://www.mkdocs.org/) and the [Material](https://squidfunk.github.io/mkdocs-material/) theme. It is available in the **`docs/`** folder and can be published to GitHub Pages or any static host.

### Building the docs locally

From the project root:

```bash
pip install mkdocs "mkdocs-material"
python scripts/build_docs.py
mkdocs build
mkdocs serve
```

Then open `http://127.0.0.1:8000`. The script `scripts/build_docs.py` regenerates the API reference (HTTP endpoints) and Extension API (NSFW Guard Python API) from the source code.

### Extending the node

- **HTTP API** — See the generated [API Reference](docs/api-reference/endpoints.md) for all custom endpoints (auth, admin, user, MFA, recovery). For workflow and intercepted paths, see [Workflow & intercepted](docs/api-reference/workflow-endpoints.md).
- **Headless JWT session** — Use [WebSocket and REST only](docs/guide/headless-jwt-session.md) (no HTML) with a valid JWT to run ComfyUI headless; no loading page or full UI required.
- **Python Extension API** — Use the [NSFW Guard API](docs/extension-api/nsfw-guard-api.md) from other ComfyUI extensions to check or tag NSFW content.
- **Overview** — [Extending the node](docs/guide/extending.md) summarizes how to use both the HTTP and Python APIs in your projects.

When the repository has GitHub Pages enabled and the Docs workflow runs on `main` or `production`, the site is deployed automatically.

---

## Folder Structure

```text
mss_login/
│
├── __init__.py              → Main entry point, route registration
├── api.py                   → NSFW Guard API (public interface)
├── globals.py               → Shared server instances, route table
├── constants.py             → Configuration paths
├── access_control.py        → RBAC, path blocking, folder isolation
│
├── routes/
│   ├── auth.py              → Login/Register/Token endpoints
│   ├── admin.py             → User & Group management, NSFW admin tools
│   ├── user.py              → User environment, mark-nsfw endpoint
│   ├── static.py           → Asset serving
│   └── workflow_routes.py   → Workflow protection, NSFW enforcement
│
├── utils/
│   ├── ip_filter.py         → Whitelist/blacklist system
│   ├── user_env.py          → User folder management
│   ├── sanitizer.py         → Input scrubbing
│   ├── logger.py            → Logging hooks
│   ├── timeout.py           → Rate limiting
│   ├── sfw_intercept/
│   │   ├── nsfw_guard.py    → NSFW detection, metadata tagging
│   │   └── node_interceptor.py → Node-level image interception
│   └── reactor_sfw_intercept.py → ReActor SFW patch
│
├── web/
│   ├── js/mss_login_setting.js → UI enforcement + settings panel
│   ├── css/mss-login.css        → Themed UI
│   └── assets/mss_logo.png
│
└── users/
    ├── users.json
    └── mss_login_groups.json
```

---

## RBAC Roles

| Role      | Description                                                            |
| --------- | ---------------------------------------------------------------------- |
| **Admin** | Full access to all ComfyUI and mss_login features.                     |
| **Power** | Elevated user with additional permissions but no admin panel access.   |
| **User**  | Standard user who can run workflows but cannot modify system behavior. |
| **Guest** | Fully restricted by default—cannot run, upload, save, or manage.       |

Permissions are stored in:

```text
users/mss_login_groups.json
```

and editable through the settings panel.

---

## UI Enforcement Layer

mss_login dynamically modifies the UI by:

- Injecting CSS rules to hide elements
- Removing menu entries (Save, Load, Manage Extensions)
- Blocking iTools, Crystools, rgthree, ImpactPack for restricted roles
- Guarding PrimeVue dialogs (Save workflow warnings)
- Intercepting hotkeys (Ctrl+S, Ctrl+O)

All logic is contained in:

```text
web/js/mss_login_setting.js
```

---

## Workflow Protection

If a user lacking permission tries to save:

1. Backend blocks the operation (`can_modify_workflows`)
2. watcher.py detects the 403 with code `"WORKFLOW_SAVE_DENIED"`
3. UI shows a centered toast popup:
   > “You do not have permission to save workflows.”

Same for delete operations.

---

## IP Rules System

Located in:

```text
utils/ip_filter.py
```

### Features

- Whitelist mode: Only listed IPs allowed
- Blacklist mode: Block specific IPs (permanent or temporary)
- Configurable through new “IP Rules” tab in settings
- **Temporary bans:** Auto-bans from failed logins expire after a configurable period (default 24 hours; `blacklist_expiry_hours` in config). Manual temporary bans can be set in the admin IP Rules tab.
- **Permanent bans:** Database-only (no JSON file); add or remove via the ComfyUI admin IP Rules tab.
- IP lists are stored in the same database as users (SQLite, PostgreSQL, or MySQL).
- Changes applied instantly to middleware

---

## User Environment Tools

From:

```text
utils/user_env.py
```

Features:

- Purge a user’s input/output/temp folders
- List all user-bound files
- Toggle whether their folder functions as a gallery

Exposed through the “User Env” tab in the mss_login settings modal.

---

## Settings Panel

Access via:
**Settings → mss_login**

Tabs:

1. **Users & Roles**
2. **Permissions & UI**
3. **IP Rules**
4. **User Environment**
5. **NSFW Management**

### Extension Tabs API

Other ComfyUI extensions can register custom tabs in the mss_login admin panel to manage their own permissions and settings. See [EXTENSION_TABS_API.md](./EXTENSION_TABS_API.md) for complete documentation.

**Quick Example:**

```javascript
window.mss_loginAdminTabs.register({
 id: "myextension",
 label: "My Extension",
 order: 50,
 render: async (container, context) => {
  const { usersList, groupsConfig, currentUser } = context;
  container.innerHTML = `<h3>My Extension Settings</h3>`;
  // Render your content here
 },
});
```

### Additional UI Features

- Integrated logout button in the settings entry
- Transparent blurred panel
- Neon-accented tab bar
- Logo watermark in top-right

---

## API Endpoints

### NSFW Guard API (Public)

The NSFW Guard API provides programmatic access to NSFW detection and enforcement. See [API_USAGE.md](./readme/API_USAGE.md) for complete documentation.

**Key Functions:**

- `check_tensor_nsfw(images_tensor, threshold=0.5)` - Check image tensors
- `check_image_path_nsfw(image_path, username=None)` - Check image files
- `check_pil_image_nsfw(pil_image, threshold=0.5)` - Check PIL Images
- `is_sfw_enforced_for_user(username=None)` - Check user restrictions
- `set_image_nsfw_tag(image_path, is_nsfw, score=1.0, label="manual")` - Tag images
- `get_image_nsfw_tag(image_path)` - Get existing tags

### Gallery Integration Endpoint

**POST `/mss-login-gallery/mark-nsfw`**
Manually mark an image as NSFW or SFW. Designed for integration with gallery extensions.

**Request Body:**

```json
{
 "filename": "image.png",
 "is_nsfw": true,
 "score": 1.0, // optional, default 1.0
 "label": "manual" // optional, default "manual"
}
```

**Response:**

```json
{
 "status": "ok",
 "message": "Image marked as NSFW",
 "filename": "image.png",
 "is_nsfw": true
}
```

**Features:**

- Recursively searches output directory subdirectories
- Security checks prevent path traversal
- Integrates with metadata tagging system
- Returns 404 if file not found, 403 for invalid paths

### Authentication Endpoints

**POST `/mss-login/api/login`** - User login
**POST `/mss-login/api/register`** - User registration
**POST `/mss-login/api/guest-login`** - Guest login
**POST `/mss-login/api/refresh-token`** - Token refresh

### Admin Endpoints

**GET/PUT `/mss-login/api/users`** - User management
**GET/PUT `/mss-login/api/groups`** - Group/permission management
**PUT `/mss-login/api/ip-lists`** - IP whitelist/blacklist
**POST `/mss-login/api/nsfw-management`** - NSFW admin tools (scan, fix, clear)

### User Environment Endpoints

**POST `/mss-login/api/user-env`** - User folder operations (purge, list, promote)

### Extension Integration

**Extension Tabs API** - JavaScript API for extensions to add custom tabs to the admin panel. See [EXTENSION_TABS_API.md](./readme/EXTENSION_TABS_API.md) for complete documentation.

---

## Backend Components

### `__init__.py`

- Main entry point for ComfyUI extension
- Route registration and middleware setup
- Server instance initialization

### `api.py`

- **NSFW Guard API** - Public interface for other extensions
- Functions: `check_tensor_nsfw()`, `check_image_path_nsfw()`, `is_sfw_enforced_for_user()`
- Metadata tagging: `set_image_nsfw_tag()`, `get_image_nsfw_tag()`
- User context management for worker threads

### `access_control.py`

- Folder isolation
- RBAC
- Middleware for blocking paths
- Workflow protection
- Extension gating

### `routes/auth.py`

- JWT authentication endpoints
- Login, registration, token refresh
- Guest login support

### `routes/admin.py`

- User & group management
- Permission editing
- NSFW management tools (scan, fix, clear)
- IP rules management

### `routes/user.py`

- User environment operations
- **Gallery integration**: `/mss-login-gallery/mark-nsfw` endpoint
- File management (purge, list, promote workflows)

### `routes/workflow_routes.py`

- Workflow save/delete protection
- Global NSFW enforcement on `/view` endpoint
- Workflow listing and loading

### `routes/static.py`

- Asset serving (CSS, JS, images)
- Logo and UI resources

### `utils/sfw_intercept/nsfw_guard.py`

- NSFW detection using AI models
- Metadata-based tagging system
- Background scanning and caching
- Per-user enforcement logic

### `utils/sfw_intercept/node_interceptor.py`

- Node-level image interception
- Real-time NSFW blocking in custom nodes

### `utils/reactor_sfw_intercept.py`

- ReActor extension SFW patch
- Per-user SFW enforcement for face swap operations

### `utils/ip_filter.py`

- Whitelist & blacklist logic
- Persistent storage

### `utils/user_env.py`

- Folder operations
- Metadata tools
- User file management

---

## Troubleshooting

### Missing Logo

Ensure the file exists:

```text
mss-login/web/assets/mss_logo.png
```

### UI Not Updating

Clear browser cache or disable caching dev tools.

### Guest cannot run workflows

Check:

```json
can_run = true
```

in `mss_login_groups.json`.

### mark-nsfw endpoint returns 404

- Ensure the image file exists in the output directory or subdirectories
- Check that the filename doesn't contain path traversal characters (`..`, `/`, `\`)
- Verify the file is within the output directory (security check)

### NSFW Guard API not working

- Ensure `ComfyUI-mss_login` is loaded before your extension
- Check that the API is available: `from api import is_available; print(is_available())`
- Verify user context is set in worker threads using `set_user_context()`

### NSFW tags not persisting

- Check that metadata files (`.nsfw_metadata.json`) are being created alongside images
- Verify write permissions in the output directory
- Ensure metadata files aren't being deleted by cleanup scripts

### DEBUG_MODE for token / "Unable to connect to server" issues

When using API tokens (e.g. Comfy Portal iOS) and seeing "Unable to connect to server", enable debug logging to see where the request is blocked (remote API guard, JWT/token validation, or access control).

- **Environment (Docker/Compose):** set `DEBUG_MODE=1` or `DEBUG_MODE=true` in your env or Compose file.
- **Config:** in `config.json` set `"debug_mode": true`.
- Logs are written to `.cursor/debug.log` (NDJSON). Check for `location` values: `remote_api_guard` (blocked before auth), `jwt_auth` (token not found or invalid), `access_control` (403 after auth).
- 401 responses include a `debug` hint when DEBUG_MODE is on. Do not leave DEBUG_MODE enabled in production.

### API token "not found or expired"

If the client sends a Bearer token but the server returns "API token not found or expired", the token is not in this server's token store. **Generate the token on the same ComfyUI instance (and same container/host) that the client connects to.** In Docker, ensure the database (unified SQLite file, PostgreSQL, or MySQL) is on a **persisted volume** so tokens survive restarts and are the same instance the client hits.

### Unified database and encrypted SQLite

- **Single database:** Users, API tokens, sessions, lockout, IP whitelist/blacklist, and shared items use one SQLite file, one PostgreSQL database, or one MySQL database (config: `users_db` in `config.json`). The default SQLite path is `data/mss_login_data.db` under the data directory. Token storage uses the same DB; set token storage backend to SQLite, PostgreSQL, or MySQL in Settings. Passwords for PostgreSQL and MySQL are read from environment only (`USERS_DB_PASSWORD`, `POSTGRES_PASSWORD`, or `MYSQL_PASSWORD`). If you had an existing `data/users.db`, the first run migrates it to `data/mss_login_data.db` and updates config automatically.
- **Encrypted SQLite:** Encryption at rest (SQLCipher) applies **only to SQLite**. To encrypt the SQLite file with a key derived from `SECRET_KEY`, set `encryption_level` in `users_db` to `low`, `standard`, or `secure` (Settings → Users DB). Requires **argon2-cffi** (`pip install argon2-cffi`) and, for encryption at rest, **sqlcipher3** with a system SQLCipher build (`pip install sqlcipher3`; see [SQLCipher](https://www.zetetic.net/sqlcipher/) for your OS). If `encryption_level` is set but sqlcipher3 is not installed, startup fails with a clear message.

- **Base URL:** Set `HOST_BASE_URL` (e.g. `https://comfy.example.com`) when behind a reverse proxy so RSS and links use the correct host. If unset, the URL is detected from the first admin or owner connection and stored in the database.

### SECRET_KEY and recovery

- **Unset SECRET_KEY:** If the `SECRET_KEY` environment variable is not set, a random key is used and persisted to `users/.ephemeral_secret_key` (do not commit this file). Sessions and MFA data use this key until restart.
- **Setting a permanent SECRET_KEY:** When you later set `SECRET_KEY` in the environment and restart, the extension will automatically migrate TOTP secrets from the ephemeral key to the new key and then remove the ephemeral file. No manual action needed if the file is still present.
- **Recovery mode:** If the ephemeral file was deleted or migration failed, users with MFA may be unable to log in. Enable recovery mode: set `RECOVERY_MODE=1`, then from an allowed host (default: localhost only), send `POST /api/mss-login/recovery/reset-mfa`. This clears MFA for all users so they can log in with password and re-enroll MFA. Override allowed hosts with `RECOVERY_MODE_HOST` or `RECOVERY_MODE_HOST` (comma-separated IPs). Recovery is only accessible when `RECOVERY_MODE` is enabled and the client IP is in the allowed list.

---

## License

Please refer to the license file found under the [readme](./readme/) folder, here: [LICENSE.md](./readme/LICENSE.md)

---

## Changelog — MSS-Login

All notable changes to the **MSS-Login** project are documented here.
This project follows a semantic-style versioning flow adapted for active development.

## 0.0.2 - **Security updates/enhancements**

- Changelog can be viewed here: [v0.0.2 Changelog](./readme/changelogs/0.0.2.md)

## 0.0.1 - **Initial release**

- Changelog can be viewed here: [v0.0.1 Changelog](./readme/changelogs/0.0.1.md)

---
