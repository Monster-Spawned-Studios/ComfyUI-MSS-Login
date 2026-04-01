# Experimental Model Isolation

`model_isolation` is an experimental feature that keeps model visibility scoped per user and allows explicit sharing by owner/admin.

## Enable

In `config.json`:

```json
{
	"experimental_features": true,
	"experimental": {
		"model_isolation": true
	}
}
```

## Behavior

- New downloads are stored in per-user model subfolders (`<folder>/<user_id>/...`) when isolation is enabled.
- Requests that attempt to download directly into global `models` paths are rewritten into per-user isolated paths when possible (including Civicomfy/core model download-style routes).
- Redirect route matching supports:
	- built-in defaults
	- launch-time auto-detected patterns (e.g. Civicomfy when installed)
	- owner-defined custom patterns persisted in config

Owner API:

- `GET /mss-login/api/settings/model-isolation-download-patterns`
- `PUT /mss-login/api/settings/model-isolation-download-patterns` with `{ "patterns": ["..."] }`
- Non-privileged users only see models explicitly granted to them.
- Owner users can grant/revoke model visibility.
- Admin users can grant/revoke only when their role includes `can_manage_model_sharing: true`.
- S3-backed and local-backed grants are tracked separately (`source_backend`) so S3 restrictions remain consistent.

## Sharing API Notes

- `POST /mss-login/api/users/{username}/shared-items` accepts:
	- `folder` (required)
	- `item_name` (required)
	- `source_backend` (`local`, `s3`, or `unknown`; optional)
- List responses include:
	- `source_backend`
	- `granted_by_user_id`
	- `granted_by_role`
	- `created_at`

## Rollback

- Disable `experimental.model_isolation` (or `experimental_features`) and restart.
- Existing model files remain on disk; only enforcement logic is disabled.

## Frontend build (Vue + Tailwind)

The web UI frontend workspace is under `web/frontend`:

```bash
cd web/frontend
npm install
npm run build
```

Build outputs are emitted to `web/dist`.
