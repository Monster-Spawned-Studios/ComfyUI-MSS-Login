# Login background (experimental)

## What it does

When enabled, the `/login` page can show a full-viewport **image or video** background behind the centered login card. Each MSS-Login instance can use its own media for a unique look.

Configuration is **owner-only** (Security Policy → **Login Appearance**). The background is applied only when:

1. The experimental **master switch** is on,
2. `experimental.login_background` is on, and
3. Login Appearance **Enable custom login background** is on.

## How to enable

- Master + `experimental.login_background: true` or `EXPERIMENTAL_LOGIN_BACKGROUND=1`
- Owner opens **MSS-Login Security Policy → Login Appearance** and configures:
  - **Enable custom login background**
  - **Source:** External URL (`http`/`https`) or **Local path** under the MSS-Login data directory
  - **Media kind:** Auto-detect, Image, or Video
- Recommended local folder: `<DATA_DIR>/login-background/` (for example `~/.comfyui-mss-login/login-background/wallpaper.mp4`)

Allowed local extensions: `.jpg`, `.jpeg`, `.png`, `.webp`, `.gif`, `.mp4`, `.webm`, `.ogg`.

## Behavior notes

- Local media is served from `/mss-login/api/login-background/media` (same-origin; works with the login CSP).
- External URLs are allowlisted in the `/login` Content-Security-Policy for that configured origin only.
- Video backgrounds use muted autoplay + loop so browsers allow playback without a gesture.
- A dark scrim keeps the login card readable over busy media.

## When it is required

Optional branding only. Auth, API clients, and downloads do not depend on it.

## See also

- [Experimental Settings hub](index.md)
- [Authentication](../guide/authentication.md)
- [Configuration](../guide/configuration.md)
