# S3 storage (AWS and Backblaze B2)

S3 support is **experimental**. Enable it as described in [Experimental → S3](../experimental/s3.md).

## Modes

| Mode | Platforms | Behavior |
|------|-----------|----------|
| **FUSE / s3fs** | Linux, WSL2 (when `/dev/fuse` + s3fs available) | Bucket mounted under `local_mount_path`; writes go through to the bucket |
| **boto3** | Linux, Windows, WSL2, macOS | API upload/download/list; used automatically when FUSE is unavailable |

macOS: use **boto3** as the supported path. macFUSE/s3fs is optional and not required for security or day-to-day use.

## Bucket layout

Objects are stored under:

- `{prefix}/models/{checkpoints|loras|…}/…`
- `{prefix}/users/{user}/workflows/` (when workflow sync is enabled)

## Backblaze B2

Use the S3-compatible endpoint for your region (for example `https://s3.us-west-004.backblazeb2.com`). When the endpoint hostname contains `backblazeb2.com`, MSS-Login defaults **`use_path_style` to true** if you have not set it explicitly. Create an application key with read/write on the bucket.

## AWS S3

Leave `endpoint_url` empty for default AWS endpoints, or set a regional/custom endpoint. Path-style addressing defaults off unless you enable it.

## Credentials

Prefer environment variables (`S3_ACCESS_KEY_ID`, `S3_SECRET_ACCESS_KEY`, names configurable in `s3_storage`). Keys saved from the UI are stored **Fernet-encrypted** in the users database (`app_settings`), never as plaintext in `config.json`.

## Model downloads to S3

`POST /mss-login/api/model-download/download` with `destination_type: "s3"`:

- **FUSE mounted:** files write directly into the mount’s models folder
- **boto3 mode:** download stages locally under the mount path, then uploads via boto3

Requires `experimental.s3`, `can_download_models`, and `can_access_s3_storage` as applicable.

## See also

- [Secrets and database](secrets-and-database.md)
- [Model download API](model-download-api.md)
