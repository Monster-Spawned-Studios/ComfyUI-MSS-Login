# ComfyUI-MSS-Login

**The next-generation security, governance, permissions, and multi-user control system for ComfyUI.**

ComfyUI-MSS-Login adds role-based access control (RBAC), UI enforcement, workflow protection, IP filtering, user environment tools, and a public **NSFW Guard API** so other extensions can integrate safe-for-work enforcement.

## Quick links

- [Installation](guide/installation.md) — Install the node and configure ComfyUI
- [Configuration](guide/configuration.md) — config.json, environment variables, roles
- [Extending the node](guide/extending.md) — Use the HTTP API and Python extension API in your projects
- [API Reference](api-reference/endpoints.md) — All custom HTTP endpoints (auth, admin, user, MFA, recovery)
- [Workflow & intercepted endpoints](api-reference/workflow-endpoints.md) — Userdata workflows, `/view`, `/prompt`
- [NSFW Guard API](extension-api/nsfw-guard-api.md) — Python API for other ComfyUI extensions

## Key features

| Feature | Description |
|--------|-------------|
| **RBAC** | Four roles (Admin, Power, User, Guest) with configurable permissions in `mss_login_groups.json` |
| **UI enforcement** | Dynamic hiding/blocking of menu items, extensions, and workflow save/load for restricted roles |
| **Workflow protection** | Per-user workflow storage; save/delete blocked for non-privileged users |
| **IP filtering** | Whitelist/blacklist with live editing in the settings panel |
| **NSFW Guard API** | Public Python API and HTTP endpoints for NSFW detection and manual tagging |
| **Extension Tabs API** | JavaScript API for other extensions to add tabs to the mss-login admin panel |

## Building this documentation locally

From the project root:

```bash
pip install mkdocs mkdocs-material
python scripts/build_docs.py
mkdocs build
mkdocs serve
```

Then open `http://127.0.0.1:8000`. The `build_docs.py` script regenerates the API reference and extension API pages from the source code.
