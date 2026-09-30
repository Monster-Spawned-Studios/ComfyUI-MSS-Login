# Experimental Settings

Experimental features use a **master switch** plus **per-feature toggles**. When the master is off, every experimental feature is disabled regardless of per-feature flags.

## How to enable

1. **Master switch**
   - Environment: `EXPERIMENTAL_FEATURES=true` (or `1`)
   - Or `config.json`: `"experimental_features": true`
2. **Per feature** (master must already be on)
   - `config.json` → `"experimental": { "<key>": true }`
   - Environment: `EXPERIMENTAL_<KEY>=true` (for example `EXPERIMENTAL_S3=1`)
   - Admin UI: **Settings → MSS-Login** experimental toggles (when master is on)

Restart ComfyUI after changing env vars or `config.json` if the UI does not hot-reload the flag you changed.

## Feature index

| Flag | Purpose | Docs |
|------|---------|------|
| `mfa` | TOTP multi-factor authentication | [MFA](mfa.md) |
| `s3` | S3-compatible model/workflow storage | [S3](s3.md) |
| `loading_screen` | Post-login `/loading` interstitial | [Loading screen](loading-screen.md) |
| `news` | Login-page RSS from `news.md` | [News](news.md) |
| `model_isolation` | Per-user model folders + grant ACL | [Model isolation](model-isolation.md) |
| `tailscale_local_auth` | Trusted Tailscale/local auth helpers | [Tailscale local auth](tailscale-local-auth.md) |
| `install_other_nodes_deps` | Auto-install other custom-node deps | [Install other node deps](install-other-nodes-deps.md) |

Related (not a feature flag): [Experimental failsafe](failsafe.md) (`experimental_failsafe` / `MSS_LOGIN_EXPERIMENTAL_FAILSAFE`).

## MFA legacy note

When the master switch is on and `experimental.mfa` is **unset** in config, MFA is treated as **enabled** for upgrade compatibility (so MFA-enrolled users are not bypassed). Explicit `"mfa": false` disables it. `MFA_DISABLED` still forces MFA off globally.
