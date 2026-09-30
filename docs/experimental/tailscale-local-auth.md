# Tailscale / local auth (experimental)

## What it does

Enables trusted Tailscale and local-network authentication helpers (see admin Tailscale settings). Use only on networks you control.

## How to enable

- Master + `experimental.tailscale_local_auth: true` or `EXPERIMENTAL_TAILSCALE_LOCAL_AUTH=1`
- Configure local/Tailscale CIDRs as documented in configuration

## When it is required

Optional convenience for trusted networks. Do **not** enable on publicly exposed hosts without understanding the trust model.

## See also

- [Experimental Settings hub](index.md)
