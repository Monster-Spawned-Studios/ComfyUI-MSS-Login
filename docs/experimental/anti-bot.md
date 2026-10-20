# Anti-bot defenses (experimental)

## What it does

The MSS-Login anti-bot protection module provides defensive measures designed to protect ComfyUI instances from automated vulnerability scanners, credential stuffing bots, web scrapers, and headless browser automation.

Key protection mechanisms include:
- **Interactive form honeypots**: Traps automated crawlers and penetration testing bots that parse and autofill DOM inputs on authentication forms (`/login` and `/register`).
- **Aggressive scanner detection**: Automatically identifies and rejects requests originating from recognized penetration testing tools and automated exploit frameworks.
- **Behavioral lobby verification**: On the post-login `/loading` screen, tracks authentic human interaction (pointer movements, touch/keyboard interactions, reaction timings, and browser automation flags). Automated bots attempting synthetic `.click()` invocations or sub-human reaction speeds are trapped and held in the lobby queue.
- **Progressive IP lockout**: Malicious bot activity triggers lockout penalties with configurable lockout thresholds.

## How to enable

1. **Master switch**: `EXPERIMENTAL_FEATURES=true` (or `1`) or `config.json` `"experimental_features": true`.
2. **Feature toggle**: `EXPERIMENTAL_ANTI_BOT=1` or `config.json` `"experimental": { "anti_bot": true }`.
3. In the ComfyUI web UI: Avatar menu → **MSS-Login Settings** → **Experimental Settings** → toggle **anti_bot**.

No server restart is required when toggling this feature. In future releases, these defenses will silently protect the server by default.

## Deployment environments

### Cloudflare Tunnel (`cloudflared`)
When operating behind Cloudflare Tunnel:
- Enable `"cloudflare_proxy": true` in `config.json`.
- Add your container network or tunnel gateway to `trusted_proxies` (e.g. `["172.16.0.0/12", "127.0.0.1"]`).
- MSS-Login accurately attributes requests using `CF-Connecting-IP`, ensuring that rate limits and penalties apply exclusively to the true external client IP without affecting other users sharing the tunnel.

### Docker & Portainer container stacks
- Internal bridge networks (such as `172.18.0.0/16`) and container gateways are recognized as proxy hops rather than end clients when configured in `trusted_proxies`.
- Zero external daemon dependencies (no external Redis or Redis-like daemons required); operates natively within Python using SQLCipher/SQLite backend state.

## Compatibility with API clients

- **Krita AI Diffusion**: Krita connects directly via JSON `POST /login` and Bearer JWT tokens. It does not submit browser form honeypots and is never redirected through the `/loading` interstitial.
- **Comfy Portal**: Connects via Bearer / `?token=` headers and direct WebSocket endpoints. Portal workflows and prompt queue submissions bypass all browser interstitial logic.
- **Embedded converter**: MSS-Login's embedded headless browser pool running on `127.0.0.1` is exempted by loopback rules, ensuring internal workflow conversion operates without impediment.

## See also

- [Loading screen](loading-screen.md)
- [Experimental Settings hub](index.md)
