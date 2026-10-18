# WebAuthn / Passkeys (experimental)

## What it does

Passkeys (WebAuthn) as a second factor alongside TOTP. Registration requires a fresh session (step-up). Authentication ceremonies use config-only RP ID and origin allowlist — never `Host` or `X-Forwarded-*`.

## Config (operator-owned — do not change live blindly)

```json
"webauthn": {
  "rp_id": "tmd-nas.win",
  "rp_name": "MSS-Login",
  "origins": ["https://comfyui.tmd-nas.win"],
  "require_device_bound_for_admins": false
}
```

Also ensure `trusted_proxies` is set so Secure cookies and client IP are correct behind Cloudflare.

## Tailscale vs tunnel

The Tailscale hostname (`*.ts.net`) has no `tailscale serve` / HTTPS by default. Options:

1. **`tailscale serve`** with a separate `ts.net` `rp_id` — credentials are **not** shared with `tmd-nas.win`.
2. **Private subdomain** of `tmd-nas.win` with a real certificate pointing at the Tailscale IP — one shared `rp_id`.

Related Origins support is optional and not required for the tunnel path.

## Dependency

`webauthn>=3.0.1,<4` (py_webauthn). If missing, passkeys turn off; password + TOTP continue to work.

live config changes (MFA_DISABLED, rp_id/origins, force_https, cloudflare_proxy, group policies) are the user's call.
