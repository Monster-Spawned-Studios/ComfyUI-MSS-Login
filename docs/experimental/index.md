# Experimental Settings

Experimental features use a **master switch** plus **per-feature toggles**. When the master is off, every experimental feature is disabled regardless of per-feature flags.

## How to enable

1. **Master switch**
   - Environment: `EXPERIMENTAL_FEATURES=true` (or `1`)
   - Or `config.json`: `"experimental_features": true`
2. **Per feature** (master must already be on)
   - `config.json` → `"experimental": { "<key>": true }`
   - Environment: `EXPERIMENTAL_<KEY>=true` (for example `EXPERIMENTAL_S3=1`)
   - Admin UI (preferred): avatar menu → **MSS-Login Settings** → **Experimental Settings**
   - Also available: ComfyUI **Settings → MSS-Login** experimental toggles

When you save from the Admin UI, `reload_experimental_features()` updates most flags live. Features that initialize only at process start will show a **restart required** notice (see below).

After changing env vars or editing `config.json` on disk, restart ComfyUI if the UI does not pick up the change.

## Restart required

| Change | Restart needed? |
|--------|-----------------|
| `s3` toggled, or master toggled while `s3` is enabled | **Yes** — S3 mount/sync starts at process boot |
| `install_other_nodes_deps` turned **on** | **Yes** — startup background scan; manual Scan & Install still works live |
| `mfa`, `loading_screen`, `news`, `model_isolation`, `tailscale_local_auth`, `login_background`, failsafe | **No** — apply after save |

The PUT `/mss-login/api/settings/experimental` response includes `restart_required` and `restart_reasons` so the UI can warn you.

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
| `login_background` | Custom `/login` image or video background | [Login background](login-background.md) |

Related (not a feature flag): [Experimental failsafe](failsafe.md) (`experimental_failsafe` / `MSS_LOGIN_EXPERIMENTAL_FAILSAFE`).

Owner-only **Login Appearance** (background media paths/URLs) is under Security Policy → Login Appearance; enable `login_background` here first.

## MFA legacy note

When the master switch is on and `experimental.mfa` is **unset** in config, MFA is treated as **enabled** for upgrade compatibility (so MFA-enrolled users are not bypassed). Explicit `"mfa": false` disables it. `MFA_DISABLED` still forces MFA off globally.
