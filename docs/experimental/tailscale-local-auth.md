# Tailscale / local auth (experimental)

## What it does

Enables passwordless local/Tailscale login helpers for users with
`can_login_locally_without_auth` (typically owner/admin). Use only on networks you control.

## Trust model (fail closed)

**Client trust** (who may use passwordless login) is **only**:

1. Tailscale CGNAT / ULA: `100.64.0.0/10`, `fd7a:115c:a1e0::/48`
2. Entries you **explicitly** list in `local_network_cidrs` that are **not** public/global

Private RFC1918 / loopback / Docker bridge addresses are **not** trusted unless listed.
Public CIDRs in `local_network_cidrs` are logged as warnings and ignored.
An empty `local_network_cidrs` list means **Tailscale only**.

**Proxy-hop trust** is separate: `trusted_proxies` (empty by default). Forwarding
headers (`X-Forwarded-For`, `CF-Connecting-IP`, …) are read **only** when
`request.remote` matches `trusted_proxies`. The resolved client IP is then checked
against the client-trust list above.

Identity-header login (`Tailscale-User-Login`, `Remote-User`, …) is **off by default**.
It requires a trusted proxy peer **and** `trusted_identity_header` naming that exact
header. It never satisfies MFA.

## Docker / port mapping

When ComfyUI runs in a container with published ports, `request.remote` is often the
Docker bridge gateway (for example `172.17.0.1`), not the real client:

1. Add that gateway (or the reverse-proxy container IP) to `trusted_proxies`.
2. Ensure the proxy sets `X-Forwarded-For` (or enable `cloudflare_proxy` for
   `CF-Connecting-IP` when using Cloudflare Tunnel).
3. Prefer host networking for Tailscale if you cannot place a trusted hop in front.

If the real client IP cannot be determined, passwordless login **fails closed**.

## MFA and Comfy Portal

MFA gates **interactive** issuance (password login, local-login, identity-header,
`generate_token`). It does **not** invalidate already-issued session JWTs or
long-lived API tokens. Comfy Portal should keep using `Authorization: Bearer`
(and `?token=` on WebSocket) after MFA is enrolled.

## How to enable

- Master + `experimental.tailscale_local_auth: true` or `EXPERIMENTAL_TAILSCALE_LOCAL_AUTH=1`
- Optionally set `local_network_cidrs` and `trusted_proxies` in config / Security Policy

## When it is required

Optional convenience for trusted networks. Do **not** enable on publicly exposed hosts
without understanding the trust model.

## See also

- [Experimental Settings hub](index.md)
