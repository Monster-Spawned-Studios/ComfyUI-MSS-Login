---
name: comfyui-custom-nodes
description: ComfyUI custom node development — version-agnostic, frontend/Portal compatible, secure, and Git-safe
---

# ComfyUI Custom Node Development

Use when developing or modifying ComfyUI custom nodes. Keep nodes compatible with a wide range of ComfyUI versions, the official frontend, Comfy Portal (mobile), and follow secure coding and Git-safe data practices.

## Version and API compatibility

- Prefer **stable, documented ComfyUI APIs** (e.g. `PromptServer` routes, `folder_paths`, node `INPUT_TYPES` / `RETURN_TYPES`). Avoid relying on private attributes or nightly-only behavior.
- Prefer **feature detection or optional imports** over hard version checks so nodes work across ComfyUI versions when possible.
- Document minimum ComfyUI (or frontend) version only when you use a feature that requires it.

## ComfyUI_frontend compatibility

- **Reference**: [ComfyUI_frontend](https://github.com/Comfy-Org/ComfyUI_frontend) — official front-end; release schedule: development → feature freeze → publication; nightlies at `Comfy-Org/ComfyUI_frontend@latest`.
- Use the **extension/settings API** for UI: `app.registerExtension({ name, settings, commands, menuCommands, … })`, `app.extensionManager.setting.get/set`, `app.extensionManager.toast.add`, `app.extensionManager.dialog.prompt/confirm` (not `window.prompt`/`confirm`/`alert` — they are not available in ComfyUI Desktop).
- Sidebar tabs: `app.extensionManager.registerSidebarTab`. Bottom panel: `bottomPanelTabs`. Keybindings/commands: `commands` + `keybindings` in `registerExtension`.
- Prefer these APIs over legacy or undocumented globals so the node works with current and future frontend versions.

## Comfy Portal compatibility

- **Comfy Portal**: [comfy-portal](https://github.com/ShunL12324/comfy-portal) (iOS/Android) uses standard ComfyUI HTTP and WebSocket APIs (prompt, queue, history, etc.).
- **Server extension**: [comfy-portal-endpoint](https://github.com/ShunL12324/comfy-portal-endpoint) provides workflow list/get/save/convert; it uses a headless browser that **must load the real ComfyUI frontend**. It is **not compatible** with extensions that block or intercept the ComfyUI frontend (e.g. login walls that prevent the UI from loading).
- For Portal compatibility: avoid blocking the main ComfyUI page from loading; use standard prompt/queue/history APIs so workflows can be executed and synced from the app. If the node adds auth, consider allowing unauthenticated access to the minimal frontend assets required for conversion, or document that Portal workflow sync will not work when auth is enabled.

## Python 3 and Comfy registry standards

- Follow **Python 3** best practices: type hints where helpful, `pathlib` for paths, explicit encoding for file I/O, and clear error messages.
- **Comfy registry security** ([Comfy docs](https://docs.comfy.org/registry/standards)): **No `eval`/`exec`** on user or workflow input (RCE risk). **No runtime `subprocess` for `pip install`**; depend on ComfyUI Manager or declared dependencies. **No code obfuscation.**

## Secure coding and data

- **Secrets and PII**: Use **environment variables** or **dotenv** (prefer **python-dotenvx** when available). Never commit secrets or PII; use placeholders in repo (e.g. `https://example.com`, `user@example.com`). Do not log or store passwords, tokens, or PII in plaintext.
- **Injection**: Use parameterized queries and safe APIs for DB/files; validate and sanitize user input; avoid building commands or paths from unsanitized input.
- **Brute force / abuse**: Consider rate limiting, backoff, or caps on sensitive operations (login, password reset, API). Patch and document any finding before publishing.

## Git-safe and environment-agnostic storage

- Store **user data, caches, and config** in a **data directory** (e.g. under `DATA_DIR` or path from env like `MSS_LOGIN_DATA_DIR`), not next to source. Keep repo free of user-specific paths and secrets.
- **.gitignore**: Ignore `.env`, `.env.*`, and any local data/cache directories. Do not commit API keys, tokens, or credentials.

## Web / settings UI (when the node adds HTML/JS/CSS)

- **No inline JavaScript** in HTML; use external scripts and event binding (e.g. from `WEB_DIRECTORY`).
- **No inline CSS** in HTML; use external stylesheets for security and maintainability.
- **Sensitive data**: Never embed secrets or PII in HTML/JS/CSS; use env/config and pass only non-sensitive data to the client if needed.
- Prefer **Content-Security-Policy**-friendly patterns (no `eval`, no inline scripts/styles) so the node is easier to harden.

## Before publishing

- Check for **eval/exec**, **runtime pip**, **obfuscation**, **inline scripts/styles**, **plaintext secrets/PII**, and **path/query injection**. Fix and notify the user of any issues found.

## Custom Web Data for Nodes

When editing HTML, CSS, or JavaScript for a ComfyUI custom node (e.g. settings or admin UI), follow the same security and compatibility goals as the main [ComfyUI custom node rule](comfyui-custom-nodes.mdc).

- **No inline JavaScript** in HTML: use external `.js` files and attach behavior via event listeners or script tags that reference `WEB_DIRECTORY` assets.
- **No inline CSS** in HTML: use external `.css` files; keeps markup clean and supports CSP.
- **No secrets or PII** in HTML/CSS/JS or in committed examples; use env/config (e.g. python-dotenvx) and placeholders like `https://example.com` or `user@example.com` in docs/samples only.
- Prefer patterns that work with **ComfyUI_frontend** and **Comfy Portal** (standard APIs, no blocking of the main frontend).
