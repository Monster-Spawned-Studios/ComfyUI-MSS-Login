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

- New downloads are stored in per-user model subfolders (`<folder>/<user_id>/...`) when isolation is enabled. On success, only the **exact downloaded item** is auto-granted to that user (not the whole prefix). Shared/global destinations do not auto-grant other users; roles without `can_view_all_comfyui_items` need an explicit Shared Models grant.
- Requests that attempt to download directly into global `models` paths are rewritten into per-user isolated paths when possible (including Civicomfy/core model download-style routes). Matching download routes return **403** if the caller lacks `can_download_models` (checked before rewrite).
- Redirect route matching supports:
	- built-in defaults
	- launch-time auto-detected patterns (e.g. Civicomfy when installed)
	- owner-defined custom patterns persisted in config

Owner API:

- `GET /mss-login/api/settings/model-isolation-download-patterns`
- `PUT /mss-login/api/settings/model-isolation-download-patterns` with `{ "patterns": ["..."] }`
- Non-privileged users only see models explicitly granted to them. This restriction applies **only while `experimental.model_isolation` is on**. When it is off, `SEPERATE_USERS` still isolates output/input/workflow folders, but the shared model library remains visible so ComfyUI and Comfy Portal can generate images.
- List APIs filtered by grants: `GET /models`, `/models/{folder}`, `/embeddings`, `/experiment/models`, `/experiment/models/{folder}` (and `/api/...` aliases), plus loader combo lists in `GET /object_info`.
- Execution: `POST /prompt` and `/api/prompt` return **403 `MODEL_NOT_ALLOWED`** when a workflow references an ungranted model. Comfy Portal runs through the same path. WebSocket `/ws` is auth-only (no prompt submission over WS).
- Comfy Portal Endpoint: `POST /api/cpe/workflow/save` and `GET .../get-and-convert` reject API-format workflows that reference ungranted models (`MODEL_NOT_ALLOWED`). UI-format graphs cannot be validated without conversion.
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

The web UI frontend workspace is under `web/frontend` (build-only; not part of ComfyUI's `WEB_DIRECTORY`):

```bash
cd web/frontend
npm install
npm run build
```

Build outputs are emitted to `web/dist` (served at `/mss-login/dist`). Comfy extension entry scripts live in `web/comfyui`.
