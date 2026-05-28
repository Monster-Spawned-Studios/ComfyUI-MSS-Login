# ComfyUI client APIs

Standard ComfyUI HTTP and WebSocket endpoints used by desktop UI, Comfy Portal, and custom mobile apps. When MSS-Login is enabled, these routes require authentication and may be **intercepted** as noted below.

Unless listed as public in [Authentication](../guide/authentication.md), send:

```http
Authorization: Bearer <jwt-or-api-token>
```

## Execution

| Method | Path | MSS-Login behavior |
|--------|------|-------------------|
| POST | `/prompt` | Requires auth + `can_run`; tags user; validates allowed models; queue may be per-user |
| POST | `/api/prompt` | Same as `/prompt` |
| GET | `/queue` | Auth required; history may be filtered per user when `SEPERATE_USERS=true` |
| POST | `/queue` | Cancel / clear operations; auth required |
| GET | `/history` | Auth required; may filter by user |
| GET | `/history/{prompt_id}` | Auth required |

## Discovery and inputs

| Method | Path | MSS-Login behavior |
|--------|------|-------------------|
| GET | `/object_info` | Auth + `can_access_api` |
| GET | `/models/{folder}` | Model list filtered per user grants |
| GET | `/embeddings` | Filtered per user |
| POST | `/upload/image` | Requires `can_upload` |
| POST | `/upload/mask` | Requires `can_upload` |

## Outputs

| Method | Path | MSS-Login behavior |
|--------|------|-------------------|
| GET | `/view` | NSFW check; 403 if blocked for user |
| GET | `/view_metadata/...` | Auth; metadata access per policy |

## WebSocket

| URL | MSS-Login behavior |
|-----|-------------------|
| `/ws` | Token via `?token=` or `?access_token=` or cookie; used for queue/progress events |

Example:

```
wss://comfy.example.com/ws?token=<api-token>
```

## Userdata / workflows (intercepted)

Implemented or wrapped by `routes/workflow_routes.py`:

| Method | Path | Notes |
|--------|------|--------|
| GET | `/api/userdata?dir=workflows` | List workflows (global + user) |
| GET | `/api/userdata/workflows/{file}` | Load workflow JSON |
| POST | `/api/userdata/workflows/...` | Save; needs `can_modify_workflows` |
| DELETE | `/api/userdata/workflows/...` | Delete own; admin for global |

See [Workflow & intercepted endpoints](workflow-endpoints.md).

## MSS-Login-specific (not ComfyUI core)

| Method | Path | Purpose |
|--------|------|---------|
| GET | `/mss-login/api/me` | Current user, role, permissions |
| POST | `/mss-login/generate_token` | Create API token |
| GET | `/mss-login/api/tokens` | List API tokens |
| DELETE | `/mss-login/api/tokens` | Revoke token |
| POST | `/login` | Browser/session login |
| POST | `/mss-login/api/mfa/*` | MFA setup and verify |

### Model download (CivitAI / Hugging Face)

Requires `can_download_models`. Bearer auth only; suitable for mobile apps.

| Method | Path | Purpose |
|--------|------|---------|
| GET | `/mss-login/api/model-download/sources` | Sources, key presence, capabilities |
| GET | `/mss-login/api/model-download/folders` | Valid `folder_type` names |
| PUT | `/mss-login/api/model-download/api-keys` | Store per-user CivitAI/HF keys |
| POST | `/mss-login/api/model-download/download` | Queue download |
| GET | `/mss-login/api/model-download/jobs/{job_id}` | Poll one job |
| GET | `/mss-login/api/model-download/jobs` | List jobs + queue stats |
| POST | `/mss-login/api/model-download/jobs/{job_id}/cancel` | Cancel queued job |

Full guide: [Model download API](../guide/model-download-api.md).

Full MSS-Login route table: [HTTP Endpoints](endpoints.md).

## Typical mobile sequence

1. `POST /mss-login/generate_token`
2. `GET /mss-login/api/me`
3. `GET /object_info`
4. `POST /prompt`
5. `GET /queue` / `GET /history` / WebSocket
6. `GET /view?filename=...&type=output`

Optional model download flow: see [Model download API](../guide/model-download-api.md) (`PUT .../api-keys` → `POST .../download` → `GET .../jobs/{job_id}`).

Detailed narrative: [Image generation pipeline](../guide/image-generation.md).

## See also

- [Authentication](../guide/authentication.md)
- [Mobile and Comfy Portal](../integrations/comfy-portal.md)
