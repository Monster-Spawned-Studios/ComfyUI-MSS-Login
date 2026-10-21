# Loading screen (experimental)

## What it does

After login, redirects interactive browser sessions to `/loading` before entering the main ComfyUI interface.

MSS `/loading` is a **pre-ComfyUI interstitial** owned by MSS-Login. It acts as both an informational lobby and an intelligent traffic buffer/queue system:
- Displays rotating tips: sequentially starts with *"Preparing ComfyUI…"* and *"Loading your workspace…"*, followed by randomized tips.
- Displays an active countdown timer informing the user when their session will enter the workspace.
- Provides a **Continue** action allowing verified human users to proceed directly into ComfyUI without waiting out the full buffer duration.
- Evaluates client behavioral authenticity: programmatic synthetic clicks and automated bots are trapped, reported to the server, and forced to remain in the queue buffer.
- Shows pending MSS-Login update notices with an optional inline updater for authorized administrators.

It does **not** replace or inject into ComfyUI’s default in-app splash at `/`. After Continue or the timeout completes, the browser navigates to `/`; ComfyUI may still show its own internal loader.

## How to enable and configure

1. **Enable the feature**:
   - Master switch: `EXPERIMENTAL_FEATURES=true` or `config.json` `"experimental_features": true`
   - Feature flag: `EXPERIMENTAL_LOADING_SCREEN=1` or `config.json` `"experimental": { "loading_screen": true }`

2. **Configure lobby buffer timeout**:
   The server owner can configure the duration the loading screen stays active:
   - **Environment variable**: `MSS_LOGIN_LOADING_TIMEOUT_SECONDS=30`
   - **Configuration file**: `config.json` → `"loading_screen": { "timeout_seconds": 30 }`
   - **Owner REST API**:
     - `GET /mss-login/api/settings/loading-screen`: Inspect current buffer settings (returns `timeout_seconds`, `min_timeout_seconds`, `max_timeout_seconds`).
     - `PUT /mss-login/api/settings/loading-screen`: Update buffer duration (`{"timeout_seconds": 45}`).
   - **Duration bounds**: Enforces a strict minimum of **15 seconds** (default: 30 seconds; scalable up to 3600 seconds / 1 hour) to ensure pacing and queue buffering.

## Network & deployment compatibility

- **Cloudflare Tunnel (`cloudflared`)**: Fully compatible. Set `"cloudflare_proxy": true` in `config.json` and configure `trusted_proxies` to allow true client IP extraction via `CF-Connecting-IP`.
- **Docker & Portainer stacks**: Compatible with internal container bridge networks (`172.16.0.0/12`). Trusted proxy settings ensure internal container gateways are never misidentified as malicious actors.
- **External API & JWT Clients**: Non-browser API clients such as **Krita AI Diffusion** and **Comfy Portal** use direct Bearer / query-token authentication and WebSocket endpoints (`/ws`, `/prompt`, `/queue`). They are never redirected to `/loading` and experience zero delay or interference.
- **Local automation / Loopback**: Internal workflow converters (e.g., embedded Playwright on `127.0.0.1`) are exempted from queue delays and bot checks.

## See also

- [Anti-bot defense](anti-bot.md)
- [Experimental Settings hub](index.md)
