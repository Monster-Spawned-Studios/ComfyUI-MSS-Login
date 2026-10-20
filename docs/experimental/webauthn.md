# WebAuthn / Passkeys (experimental)

## What it does

Passkeys (WebAuthn) as a second factor alongside TOTP. Registration requires a fresh session (step-up). Authentication ceremonies use config-only RP ID and origin allowlist — never `Host` or `X-Forwarded-*`.

## Configuration (Environment & Config File)

Passkeys require an **HTTPS origin with a valid domain name** (bare IP addresses are rejected by browser WebAuthn security specifications).

### Automatic Zero-Config Setup with `HOST_BASE_URL`
If your server runs behind an HTTPS reverse proxy or Cloudflare tunnel, setting `HOST_BASE_URL` automatically configures both the allowed origin and the RP ID:
```env
HOST_BASE_URL=https://ai.example.com
```
- Allowed origin: `https://ai.example.com`
- RP ID: `ai.example.com` (automatically resolved for ceremonies)

### Environment Variables
You can also explicitly set or extend WebAuthn settings via environment variables:
```env
# Primary Relying Party ID (domain name or registrable suffix)
WEBAUTHN_RP_ID=example.com

# Human-readable relying party name shown in browser prompts
WEBAUTHN_RP_NAME="My ComfyUI Studio"

# Comma-separated list of allowed origins (or * for dynamic tunnel setups)
WEBAUTHN_ORIGINS=https://ai.example.com,https://tunnel-2.example.com
```

### Config File (`config.json`)
```json
"webauthn": {
  "rp_id": "example.com",
  "rp_name": "MSS-Login",
  "origins": ["https://ai.example.com", "https://comfyui.tmd-nas.win"],
  "require_device_bound_for_admins": false
}
```

### Cloudflare Tunnels & Reverse Proxies
When deploying behind Cloudflare Tunnels (`cloudflared`) or Nginx/Caddy/Traefik:
1. Ensure your public URL is HTTPS (e.g. `https://comfy.yourdomain.com`).
2. Passkey ceremonies automatically detect HTTPS from `CF-Visitor: {"scheme":"https"}` and `X-Forwarded-Proto: https` to enforce `Secure; SameSite=Strict` ceremony cookies.
3. If `rp_id` is set to a parent domain (e.g. `example.com`), subdomains like `ai.example.com` will use it. If the server is accessed from an unrelated allowed origin, RP ID automatically adapts to that origin's domain to prevent browser WebAuthn `DOMException` SecurityErrors.

## Dependency

`webauthn>=3.0.1,<4` (py_webauthn). If missing, passkeys turn off; password + TOTP continue to work.

live config changes (MFA_DISABLED, rp_id/origins, force_https, cloudflare_proxy, group policies) are the user's call.
