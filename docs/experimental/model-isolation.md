# Model isolation (experimental)

## What it does

Isolates model folders per user and enforces **grant-based** visibility via `shared_items`. Third-party downloaders (for example Civicomfy) can have destination paths rewritten into the user tree. Users without `can_download_models` receive **403** on matching download routes before rewrite.

## How to enable

- Master + `experimental.model_isolation: true` or `EXPERIMENTAL_MODEL_ISOLATION=1`
- Related permissions: `can_view_all_comfyui_items`, `can_manage_model_sharing`, `can_download_models`

## When it is required

Required when non-privileged users must not see the full shared model library. Owner/admin with view-all still see all models. New downloads default-deny for users without view-all (exact-item grant to the downloader under isolation only).

## See also

- [Model isolation guide](../guide/model-isolation.md)
- [Experimental Settings hub](index.md)
