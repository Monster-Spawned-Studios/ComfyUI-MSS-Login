# NSFW and outputs

MSS-Login enforces safe-for-work (SFW) policies for users who have SFW checking enabled. Enforcement happens at **three layers** so mobile and API clients receive consistent behavior when fetching images.

## Policy per user

- Each user has an `sfw_check` flag in the user database (default: **enforced** / block NSFW).
- Toggle it per user in **Settings → MSS-Login → Users & Roles** (SFW Check column). The enforcement cache is cleared on save so the change applies immediately.
- During a prompt, the queue item is stamped with the submitter so worker-thread `SaveImage` / `PreviewImage` and `/view` use that user's policy (including Comfy Portal `POST /prompt`).
- Owners may receive ntfy notifications on blocks and generated images (see [Configuration](configuration.md#push-notifications-ntfy)).

## Layer 1: Save-time interception (node interceptor)

At startup, MSS-Login patches ComfyUI core save nodes:

- `SaveImage`
- `PreviewImage`
- `SaveAnimatedWEBP` / `SaveAnimatedPNG` (when present)

When SFW is enforced for the executing user and the classifier marks output as NSFW:

- The saved file is replaced with a **black image** (same dimensions).
- NSFW metadata may be embedded in PNG/JPEG for fast later checks.
- Optional **ntfy** alert with quarantine action button (owner).

Implementation: `utils/sfw_intercept/node_interceptor.py`.

!!! warning "Third-party save nodes"
    Custom nodes that write files without going through patched ComfyUI save classes may **not** be intercepted at save time. Rely on `/view` blocking and document workflow requirements for strict SFW deployments.

## Layer 2: HTTP `/view`

Before ComfyUI serves an output or temp image:

```http
GET /view?filename=...&type=output
```

The workflow interceptor checks NSFW metadata / classifier results for the **current user**. If blocked:

- **403** with body such as `NSFW content blocked for this user.` or `NSFW Blocked`

Mobile apps should treat 403 on `/view` as policy enforcement, not a network error.

## Layer 3: Static gallery

`GET /static_gallery/...` paths apply the same checks for gallery integrations.

## Classifier

- Model: Hugging Face `Falconsai/nsfw_image_detection`
- Typical threshold: label `nsfw` with score ≥ 0.5
- Cached scores in image metadata avoid re-running the model on every `/view`

Details and Python API for other extensions: [NSFW Guard API](../extension-api/nsfw-guard-api.md).

## Reactor and other extensions

`utils/reactor_sfw_intercept.py` can align third-party Reactor NSFW checks with MSS-Login policy for users without SFW enforcement.

## Manual tagging

Admins and gallery tools can mark images NSFW via MSS-Login gallery APIs (see admin UI and `mark-nsfw` routes in the codebase). Tagged images are blocked on `/view` for SFW users even if the classifier score was low.

## Mobile client checklist

1. After `GET /history`, parse output filenames from the execution record.
2. Request each file via `GET /view` with the same Bearer token used for `/prompt`.
3. On **403**, show a policy message; do not retry blindly.
4. Do not rely on WebSocket latent previews for final image content (previews are disabled to reduce leakage).

## Quarantine (owner)

NSFW notifications can include a signed action URL:

```http
GET /mss-login/api/ntfy/quarantine?action=quarantine&token=<signed>
```

Owner-only; moves offending files into quarantine storage. See quarantine settings in `config.json` (`quarantine` block).

## See also

- [Image generation pipeline](image-generation.md)
- [NSFW Guard API](../extension-api/nsfw-guard-api.md)
