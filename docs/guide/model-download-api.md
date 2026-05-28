# Model download API (mobile and headless clients)

Queue CivitAI and Hugging Face model downloads on a ComfyUI host protected by MSS-Login. The API is **JSON-only**, works with **Bearer tokens** (JWT or long-lived API token), and reuses the same features as the ComfyUI admin UI: per-user API keys, `can_download_models` permission, model isolation paths, S3 destinations (experimental), and concurrency limits.

No ComfyUI HTML or browser session is required.

## Authentication

Every request:

```http
Authorization: Bearer <jwt-or-api-token>
Content-Type: application/json
```

Requirements:

- Valid token (see [Authentication](authentication.md))
- Role permission `can_download_models` (enabled for `owner` and `admin` by default; configurable per group)

Remote clients (`REQUIRE_AUTH_FOR_REMOTE_API=true`) must send Bearer auth; paths under `/mss-login/` are not exempt from remote protection when a token is missing.

## Quick start

1. `POST /mss-login/generate_token` — create an API token (needs `can_have_api_tokens`).
2. `GET /mss-login/api/me` — confirm `can_download_models` (or infer from role).
3. `PUT /mss-login/api/model-download/api-keys` — store CivitAI and/or Hugging Face keys (encrypted, per user).
4. `POST /mss-login/api/model-download/download` — queue a download; receive `job_id`.
5. `GET /mss-login/api/model-download/jobs/{job_id}` — poll until `status` is `completed`, `failed`, or `cancelled`.

## Endpoints

| Method | Path | Purpose |
|--------|------|---------|
| GET | `/mss-login/api/model-download/sources` | Sources, which keys are set, and capabilities |
| GET | `/mss-login/api/model-download/folders` | Valid `folder_type` values (e.g. `checkpoints`, `loras`) |
| GET | `/mss-login/api/model-download/api-keys` | Which sources have keys (not the secrets) |
| PUT | `/mss-login/api/model-download/api-keys` | Set or clear a source API key |
| POST | `/mss-login/api/model-download/download` | Queue a download job |
| GET | `/mss-login/api/model-download/jobs` | List your jobs + queue stats |
| GET | `/mss-login/api/model-download/jobs/{job_id}` | Single job (polling) |
| POST | `/mss-login/api/model-download/jobs/{job_id}/cancel` | Cancel a **queued** job |

Alternate prefix (same handlers): `/api/mss-login/api/model-download/...`

## Capabilities (`GET .../sources`)

Example response:

```json
{
  "sources": ["civitai", "huggingface"],
  "sources_with_keys": ["civitai"],
  "capabilities": {
    "sources": ["civitai", "huggingface"],
    "destination_types": ["local", "s3"],
    "limits": {
      "active_total": 5,
      "active_civitai": 3,
      "active_huggingface": 2
    },
    "experimental": {
      "s3": false,
      "model_isolation": true
    },
    "civitai_fields": ["model_version_id", "type", "format", "size", "fp"],
    "huggingface_fields": ["repo_id", "filename", "subfolder"]
  }
}
```

## Store API keys

```http
PUT /mss-login/api/model-download/api-keys
Authorization: Bearer <token>

{"source": "civitai", "api_key": "your-civitai-token"}
```

Clear a key with an empty `api_key`:

```json
{"source": "huggingface", "api_key": ""}
```

Keys are stored per user and never returned on GET.

## Queue CivitAI download

```http
POST /mss-login/api/model-download/download
Authorization: Bearer <token>

{
  "source": "civitai",
  "model_version_id": "138296",
  "folder_type": "checkpoints",
  "destination_type": "local",
  "format": "SafeTensor",
  "size": "pruned",
  "fp": "fp16"
}
```

| Field | Required | Notes |
|-------|----------|--------|
| `source` | yes | `civitai` or `huggingface` |
| `model_version_id` | CivitAI | Also accepts `modelVersionId` |
| `folder_type` | no | Default `checkpoints`; use `GET .../folders` |
| `destination_type` | no | `local` (default) or `s3` if experimental S3 enabled |
| `type`, `format`, `size`, `fp` | no | CivitAI download query parameters |

## Queue Hugging Face download

```json
{
  "source": "huggingface",
  "repo_id": "owner/model-name",
  "filename": "model.safetensors",
  "folder_type": "checkpoints",
  "destination_type": "local",
  "subfolder": "optional/subfolder"
}
```

## Poll job status

```http
GET /mss-login/api/model-download/jobs/abc123...
Authorization: Bearer <token>
```

```json
{
  "job": {
    "job_id": "abc123...",
    "source": "civitai",
    "status": "running",
    "progress_pct": 42.5,
    "bytes_done": 104857600,
    "total_bytes": 2469606195,
    "eta_seconds": 120.3,
    "model_version_id": "138296",
    "destination": "",
    "error": "",
    "can_cancel": false
  },
  "stats": {
    "active_total": 1,
    "pending_total": 0,
    "limit_total": 5
  }
}
```

Statuses: `queued`, `running`, `completed`, `failed`, `cancelled`.

## Model isolation

When `experimental.model_isolation` is enabled, downloads go to per-user folders under the isolation tree (same as the ComfyUI UI). Use `target_username` only if the caller has `can_manage_model_sharing` (admin/owner); otherwise the job runs for the authenticated user.

## Admin download for another user

```json
{
  "source": "civitai",
  "model_version_id": "12345",
  "target_username": "alice",
  "folder_type": "checkpoints"
}
```

Requires sharing permission; uses the target user’s stored CivitAI/HF key.

## Errors

| HTTP | Meaning |
|------|---------|
| 401 | Missing or invalid token |
| 403 | No `can_download_models`, S3 disabled, or sharing not allowed |
| 400 | Invalid body, missing API key for source, bad `folder_type` |
| 404 | Unknown `job_id` or `target_username` |

## See also

- [Authentication](authentication.md)
- [Model isolation](model-isolation.md)
- [ComfyUI client APIs](../api-reference/comfyui-client-endpoints.md)
- [Mobile and Comfy Portal](../integrations/comfy-portal.md)
