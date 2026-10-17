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
- List APIs filtered by grants: `GET /models`, `/models/{folder}`, `/embeddings`, `/experiment/models`, `/experiment/models/{folder}` (and `/api/...` aliases), plus loader combo lists in `GET /object_info` and `GET /object_info/{NodeClass}` (and `/api/...` aliases). Combo folders are resolved per `(node class, input key)` so shared keys like `clip_name` / `model_name` do not collide across loaders.
- **Flat UI names only:** On disk and in `shared_items` grants, isolated models remain `{user_id}/file.safetensors`. Every client-facing list (object_info combos, `/models*`, Portal/CPE) exposes **plain relative filenames** only — never another user’s UUID prefix. Loader combos for view-all (owner/admin) use the same plain-name shape (shared library ∪ own isolation ∪ grants); cross-user browse is not shown in loader combos. CPE `convert` / `get-and-convert` responses also flatten API-prompt model inputs so Comfy Portal never receives UUID isolation paths (which would otherwise fail ComfyUI “value not in list”).
- **Shadowing:** If the user’s isolation folder and the shared library both contain the same relative name (e.g. `loras/anima-base-1-masterpiece-v51.safetensors`), the list shows the name once and it resolves to the **user’s copy**.
- **Resolution:** A single resolver maps plain names → storage paths at load time (`folder_paths.get_full_path` / `get_filename_list` hooks). Order: own isolation file → explicit grant → shared library. It never fabricates a UUID prefix for a name that is not on disk under the user or a grant.
- **Legacy prefixes:** Inbound `{own_user_id}/file.safetensors` is accepted and normalized to the plain name for the current user. A UUID prefix for **another** user (or unknown) is rejected with a generic `MODEL_NOT_ALLOWED` (no existence leak). Non-UUID subfolders in the shared library (e.g. `SDXL/foo.safetensors`) keep working.
- **Prompt agreement:** `POST /prompt` keeps user-facing plain names so ComfyUI `validate_inputs`, object_info combos, and Portal lists are the same set. Existing workflows that already store `{own_user_id}/file` still run after normalization.
- **Existing UUID trees:** At startup, isolation folder roots are registered with ComfyUI before the model cache refresh, so files already under `model_isolation/models/{folder}/{user_id}/` appear without re-download. Effective grants also union on-disk files in the current user’s isolation folder with `shared_items` (dual-read), so older downloads remain visible in lists and loaders for users who own or were shared those models. The model cache stores **raw storage names** only; per-user display lists are built at request time.
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
