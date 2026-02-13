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

| Variable | Purpose |
|----------|---------|
| `SECRET_KEY` | JWT signing and session stability; also used for SQLite encryption key when encryption is enabled |
| `USERS_DB_SQLITE_PATH` | Optional; default SQLite path for users/API tokens/shared items |
| `POSTGRES_*` | Optional; PostgreSQL host, port, database, user, password |
| `RECOVERY_MODE` | Enable recovery endpoint for MFA reset (e.g. `true` or `1`) |
| `RECOVERY_MODE_HOST` | Comma-separated IPs allowed to call recovery (default: 127.0.0.1, ::1) |

## Roles and permissions

Roles are defined in `users/mss_login_groups.json` (or the path set in config). Default roles:

| Role | Typical permissions |
|------|---------------------|
| **admin** | Full access; can_run, can_upload, can_access_manager, can_access_api, can_see_restricted_settings, can_have_api_tokens, etc. |
| **power** | Elevated; no restricted settings, API tokens allowed |
| **user** | Standard; run and upload, no manager, no API tokens |
| **guest** | Restricted; can_access_api only (e.g. prompt), no run/upload/save |

Permissions control workflow save/delete, extension access, and whether a user can have API tokens. Edit via **Settings → mss_login** or by modifying the groups JSON (with ComfyUI stopped or after a reload).

## Users database

- **Unified database**: One SQLite file or one PostgreSQL database holds users, API tokens, and shared items.
- **Encrypted SQLite**: Set `encryption_level` in `config.json` under `users_db` to `low`, `standard`, or `secure`. Requires `argon2-cffi` and, for encryption at rest, `pysqlcipher3` with a system SQLCipher build.

See the README in the project root for detailed troubleshooting (SECRET_KEY, recovery mode, API tokens).
