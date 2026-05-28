# Configuration

## config.json

The main configuration file at the node root is `config.json`. It controls:

- Paths (log file, legacy users JSON, group config)
- Log levels
- Secret key environment variable name
- Users database (SQLite path or PostgreSQL settings)
- API token store backend (database or JSON file)
- Optional: `require_auth_for_remote_api`, `force_https`, IP list paths, recovery mode

Sensitive values (e.g. database passwords, `SECRET_KEY`) should be set via **environment variables** or **.env**; see `.env.example` in the project root. Never commit `.env` or `.env.keys`.

## Environment variables

Copy `.env.example` to `.env` and set:

| Variable                 | Purpose                                                                                           |
| ------------------------ | ------------------------------------------------------------------------------------------------- |
| `SECRET_KEY`             | JWT signing and session stability; also used for SQLite encryption key when encryption is enabled |
| `USERS_DB_SQLITE_PATH`   | Optional; default SQLite path for users/API tokens/shared items (default: `data/mss_login_data.db`) |
| `HOST_BASE_URL`          | Optional; **primary** base URL of this instance (e.g. `https://comfy.example.com`). If unset, detected from first admin/owner connection and stored in the database. HTTPS vs HTTP is taken from this URL (or the detected one); the node enforces HTTPS when it needs a secure URL. Used for RSS, links, and domain resolution. |
| `POSTGRES_*`             | Optional; PostgreSQL host, port, database, user, password                                         |
| `MYSQL_*` / `USERS_DB_PASSWORD` | Optional; when backend is MySQL, set `MYSQL_PASSWORD` or `USERS_DB_PASSWORD` in environment (password never stored in config). |
| `EXPERIMENTAL_FEATURES`  | Enable experimental features (MFA, S3 storage/mount/workflow sync). Set to `true` or `1` to enable. Default: false. |
| `RECOVERY_MODE`          | Enable recovery endpoint for MFA reset (e.g. `true` or `1`)                                       |
| `RECOVERY_MODE_HOST`     | Comma-separated IPs allowed to call recovery (default: 127.0.0.1, ::1)                            |
| `MSS_LOGIN_LOADING_TIMEOUT_SECONDS` | Loading page fail-safe redirect delay in seconds (default: 15; range 1–300). Only applies when experimental loading screen is enabled. |
| `EXPERIMENTAL_MFA`, `EXPERIMENTAL_S3`, `EXPERIMENTAL_LOADING_SCREEN`, `EXPERIMENTAL_NEWS` | Per-feature experimental toggles when master `EXPERIMENTAL_FEATURES` is on. Set to `true` or `1` to enable. |

## Experimental features

Experimental features use a **master switch** plus **per-feature toggles**. Enable the master, then enable each feature you want:

- **Master:** set `EXPERIMENTAL_FEATURES=true` (env) or `"experimental_features": true` in `config.json`.
- **Per-feature:** in `config.json` set `"experimental": { "mfa": true, "s3": true, "loading_screen": true, "news": true }` for the features you want. Or use env: `EXPERIMENTAL_MFA`, `EXPERIMENTAL_S3`, `EXPERIMENTAL_LOADING_SCREEN`, `EXPERIMENTAL_NEWS` (each `true`/`1` to enable). Admins can toggle these in **Settings → mss-login** when the master is on.

**MFA**, **S3**, **Loading screen** (post-login page at `/loading`), and **News** (RSS) are experimental. When the master is off, all are disabled. When the master is on, each is off until enabled in config or the settings UI. `MFA_DISABLED` (env or config) still disables MFA globally if you want S3 but not MFA.

**Server news feed (experimental):** With `experimental.news` enabled, admins can add a `news.md` file in the node’s data directory (`~/.comfyui-mss-login/` or `MSS_LOGIN_DATA_DIR`). The file is converted to an RSS feed and shown on the login page. See `readme/news_feed_template.md` for the format.

### Loading page and ComfyUI frontend

The **loading page** (`/loading`) is an MSS-Login intermediate page shown after login when `experimental.loading_screen` is enabled. It does **not** conflict with ComfyUI's in-app loading at `/`: ComfyUI's loading state runs at the root URL after the user leaves the loading page. Flow: Login then (optional) `/loading` then user clicks Continue or fail-safe fires then `/` (ComfyUI app). A **fail-safe** automatically redirects to `/` after a configurable delay (default 15 seconds). Set `MSS_LOGIN_LOADING_TIMEOUT_SECONDS` (env) or `loading_screen.timeout_seconds` in config (1 to 300). Tips from `web/data/loading-tips.json` rotate every 5 seconds.

## Roles and permissions

Roles are defined in `users/mss_login_groups.json` (or the path set in config). Default roles:

| Role      | Typical permissions                                                                                                          |
| --------- | ---------------------------------------------------------------------------------------------------------------------------- |
| **admin** | Full access; can_run, can_upload, can_access_manager, can_access_api, can_see_restricted_settings, can_have_api_tokens, etc. |
| **power** | Elevated; no restricted settings, API tokens allowed                                                                         |
| **user**  | Standard; run and upload, no manager, no API tokens                                                                          |
| **guest** | Restricted; can_access_api only (e.g. prompt), no run/upload/save                                                            |

Permissions control workflow save/delete, extension access, and whether a user can have API tokens. Edit via **Settings → mss-login** or by modifying the groups JSON (with ComfyUI stopped or after a reload).

## Lockout and security.json

If lockout is enabled (`blacklist_after_attempts` in config), too many failed logins blacklist the IP and can lock the device. To **unlock** (e.g. if the owner is locked out):

- **security.json** in the MSS-Login data directory: create or edit `security.json` with a `lockout` section. Add your IP to `unlock_ips` or your device ID to `unlock_devices` to allow access again. Optional: `disable_lockout_until` (Unix timestamp) to temporarily disable lockout checks.
- **Database (SQLite/PostgreSQL/MySQL):** IP whitelist and blacklist are stored in the same database as users (`ip_whitelist`, `ip_blacklist` tables). Auto-bans from failed logins expire after a configurable period (default 24 hours; set `blacklist_expiry_hours` in config). Permanent bans are database-only (no JSON file); add them via the ComfyUI admin IP Rules tab. To clear a lock, remove rows from `ip_blacklist` or `locked_devices` in that database.

Example `security.json`:

```json
{
 "lockout": {
  "unlock_ips": ["192.168.1.100"],
  "unlock_devices": [],
  "disable_lockout_until": null
 }
}
```

## Users database

- **Unified database**: One SQLite file, one PostgreSQL database, or one MySQL database holds users, API tokens, sessions, lockout, IP whitelist/blacklist, and shared items. Choose the backend in Settings → Users Database (or Token Storage when using the same DB). Passwords for PostgreSQL and MySQL are read from environment only (`USERS_DB_PASSWORD`, `POSTGRES_PASSWORD`, or `MYSQL_PASSWORD`); never stored in config.
- **Encrypted SQLite**: Encryption at rest (SQLCipher) applies **only to SQLite**. Set `encryption_level` in `config.json` under `users_db` to `low`, `standard`, or `secure`. Requires `argon2-cffi` and, for encryption at rest, `sqlcipher3` with a system SQLCipher build.

See the README in the project root for detailed troubleshooting (SECRET_KEY, recovery mode, API tokens).

## Auto-update

Under `config.json` → `auto_update` you can set:

- **check_mode**: `"releases"` (default) uses the release-tag API (e.g. GitHub `releases/latest`); `"branch"` uses an interval-based check against a branch. For branch mode, set `check_url` to a URL that returns a version (e.g. raw `pyproject.toml` or `version.json` from that branch).
- **check_url**, **check_interval_hours**, **branch**, **changelog_url**: See `config.defaults.json`. Changelog text is loaded first from `readme/changelogs/X.X.X.md` (by version); if missing, the release body from the provider API is used.

## Push notifications (ntfy)

MSS-Login can send push notifications via [ntfy](https://ntfy.sh) or a **self-hosted** ntfy server. Configuration lives in `config.json` under the MSS-Login data directory (`MSS_LOGIN_DATA_DIR`, default `~/.comfyui-mss-login/`):

```json
{
	"ntfy": {
		"topic": "my-secret-topic",
		"base_url": "https://ntfy.example.com",
		"api_token": "",
		"enabled_events": ["image_generated", "nsfw_block", "user_login"]
	}
}
```

| Key | Purpose |
|-----|---------|
| `topic` | ntfy topic (channel name); required to send |
| `base_url` | Server root URL without trailing slash (default `https://ntfy.sh`) |
| `api_token` | Bearer token for authenticated ntfy servers (optional) |
| `enabled_events` | List of event keys to notify (see admin UI or `EVENT_KEYS` in code) |

### Self-hosted ntfy

Set `base_url` to your instance, for example `https://ntfy.example.com`. Remote hosts must use **HTTPS** (HTTP is allowed only for `localhost` / `127.0.0.1`).

### Environment fallback

If `api_token` is empty in config, the server uses the `NTFY_API_KEY` environment variable (see `.env.example`).

### Admin UI vs manual edit

- **Settings → mss-login** (admin): topic, server URL, API token, and event checkboxes. Saving preserves an existing token unless you enter a new one or check **Clear stored API token**.
- **Manual `config.json`**: safe for automation; partial API updates that omit `base_url` or `api_token` preserve existing values.

### Notable events

| Event | When fired |
|-------|------------|
| `image_generated` | Owner: image saved after generation (may attach file via PUT) |
| `nsfw_block` | NSFW content blocked; may include quarantine action button |
| `user_login` / `user_logout` | Session events |
| `api_token_created` | New API token issued |
| `login_failure` | Failed login attempt |

Quarantine actions in notifications call back to your ComfyUI server (`/mss-login/api/ntfy/quarantine`), not to the ntfy host.

## S3 model storage

S3 is an experimental feature; enable `EXPERIMENTAL_FEATURES` (see above) to use it.

When S3 mount is enabled, the bucket is exposed as a local path (FUSE or sync). That path is registered with ComfyUI’s `folder_paths` so models (e.g. `.safetensors`, `.pt`, `.ckpt`) are indexed and loadable like local files.

- **Bucket layout:** Mirror ComfyUI folder names under your prefix, e.g. `prefix/checkpoints/`, `prefix/loras/`, `prefix/vae/`. Default `model_folders` include checkpoints, loras, vae, embeddings, controlnet, upscale_models, clip, clip_vision, diffusion_models, text_encoders, hypernetworks, vae_approx.
- **Relative path:** The effective local path is `s3_mount` under the data directory (or the path set in `s3_storage.mount.local_mount_path`). Models there are discovered at startup and after mount/sync; assign them to users via the admin shared-items UI (same permissions as local models).
