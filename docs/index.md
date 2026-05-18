# ComfyUI-MSS-Login

**The next-generation security, governance, permissions, and multi-user control system for ComfyUI.**

ComfyUI-MSS-Login adds role-based access control (RBAC), UI enforcement, workflow protection, IP filtering, user environment tools, and a public **NSFW Guard API** so other extensions can integrate safe-for-work enforcement.

## Building a mobile client?

Start with these guides for a smooth API-token flow from login through image download:

- [Authentication](guide/authentication.md) — API tokens, Bearer headers, permissions
- [Image generation pipeline](guide/image-generation.md) — `/prompt` → queue/history → `/view`
- [Model download API](guide/model-download-api.md) — CivitAI / Hugging Face downloads from mobile or scripts
- [Mobile and Comfy Portal](integrations/comfy-portal.md) — Comfy Portal and troubleshooting

## Quick links

- [Installation](guide/installation.md) — Install the node and configure ComfyUI
- [Configuration](guide/configuration.md) — config.json, environment variables, roles, ntfy
- [Authentication](guide/authentication.md) — JWT, API tokens, MFA, remote API guard
- [Image generation pipeline](guide/image-generation.md) — End-to-end run flow for API clients
- [Model download API](guide/model-download-api.md) — Queue CivitAI/Hugging Face downloads via Bearer token
- [Headless JWT session](guide/headless-jwt-session.md) — WebSocket and REST only (no HTML)
- [NSFW and outputs](guide/nsfw-and-outputs.md) — Save-time and `/view` enforcement
- [Extending the node](guide/extending.md) — Use the HTTP API and Python extension API in your projects
- [API Reference](api-reference/endpoints.md) — All custom HTTP endpoints (auth, admin, user, MFA, recovery)
- [ComfyUI client APIs](api-reference/comfyui-client-endpoints.md) — `/prompt`, `/queue`, `/history`, `/view`, `/ws`
- [Workflow & intercepted endpoints](api-reference/workflow-endpoints.md) — Userdata workflows, `/view`, `/prompt`
- [Mobile and Comfy Portal](integrations/comfy-portal.md) — iOS/Android integration notes
- [NSFW Guard API](extension-api/nsfw-guard-api.md) — Python API for other ComfyUI extensions

## Key features

| Feature                 | Description                                                                                            |
| ----------------------- | ------------------------------------------------------------------------------------------------------ |
| **RBAC**                | Five roles (Owner, Admin, Power, User, Guest) with configurable permissions in `mss_login_groups.json` |
| **UI enforcement**      | Dynamic hiding/blocking of menu items, extensions, and workflow save/load for restricted roles         |
| **Workflow protection** | Per-user workflow storage; save/delete blocked for non-privileged users                                |
| **IP filtering**        | Whitelist/blacklist with live editing in the settings panel                                            |
| **NSFW Guard API**      | Public Python API and HTTP endpoints for NSFW detection and manual tagging                             |
| **Extension Tabs API**  | JavaScript API for other extensions to add tabs to the mss-login admin panel                           |
