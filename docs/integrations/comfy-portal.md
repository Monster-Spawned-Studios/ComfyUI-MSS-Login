# Mobile and Comfy Portal

This page covers integrating **mobile apps** and [Comfy Portal](https://github.com/ShunL12324/comfy-portal) with a ComfyUI instance that runs MSS-Login.

## Comfy Portal (iOS / Android)

Comfy Portal uses **standard ComfyUI APIs**:

- `POST /prompt` — run workflows
- `GET /queue`, `GET /history` — status and results
- `GET /view` — output images
- WebSocket `/ws` — live updates
- Userdata workflow APIs for sync

MSS-Login does not require a proprietary Portal protocol. Configure Portal with your server URL and authenticate the same way as any API client.

### Recommended auth for Portal

1. In ComfyUI (browser), log in as the user.
2. Generate a long-lived **API token** via Settings → mss-login → Generate Token, or:

   ```http
   POST /mss-login/generate_token
   ```

3. Configure Portal to send `Authorization: Bearer <token>` (or the token mechanism Portal supports).

Ensure the user's role has `can_run` and `can_access_api`. Remote clients (including cellular / non-LAN IPs) must send a **valid** token; the remote API guard allows `/api/cpe/*` and `/cpe/*` only when the token is present and valid.

### Per-user workflow list (CPE paths)

Comfy Portal calls [comfy-portal-endpoint](https://github.com/ShunL12324/comfy-portal-endpoint) paths such as `GET /api/cpe/workflow/list`. MSS-Login intercepts those routes and serves **that user's** stored workflows (plus any shared/global JSON files), using the same response shape as CPE:

| Method | Path | Body / query | Notes |
|--------|------|----------------|-------|
| GET | `/cpe/workflow/list` and `/api/cpe/workflow/list` | — | `{ "status": "success", "workflows": [ { "filename", "size", "modified" } ] }` |
| GET | `/cpe/workflow/get` | `?filename=` | `{ "status": "success", "filename", "workflow": "<raw JSON string>" }` |
| POST | `/cpe/workflow/save` | `{ "workflow": "<json string>", "name": "optional.json" }` | Writes into the authenticated user's workflow dir |
| GET | `/cpe/workflow/get-and-convert` | `?filename=` | API-format graphs convert in-process; UI-format graphs use **embedded** Playwright (JWT/Bearer-injected) when `cpe_embedded_convert` is on and Playwright+Chromium are installed; else sibling CPE if present; else `503` |
| POST | `/cpe/workflow/convert` | UI workflow JSON object | Same converter path as get-and-convert |
| GET | `/cpe/health` | — | `{ "status": "success", "browser": { "status": "<enum>" } }` — truthful (`not_installed` / `not_initialized` / `initializing` / `ready` / `error`) |
| GET | `/cpe/system/storage` | — | `{ "status": "success", "storage": { "path", "total", "used", "free" } }` (bytes for `DATA_DIR`) |

Saving through CPE requires `can_modify_workflows` (defaults to allowed for every role except guest). Convert does not require `can_modify_workflows` (read-only transform) but still needs `can_access_api`.

Unauthenticated calls to **`/cpe/*` and `/api/cpe/*` always receive JSON 401** (same as `/api/*`), not an HTML login redirect — even when `Accept` is `*/*`. Portal should send `Authorization: Bearer <token>` (and may also use `?token=` on prompt/view/ws).

### Auth and isolation (Portal run path)

With a valid API token and `can_run` + `can_access_api`:

1. `POST /prompt` (or `/api/prompt`) stamps the submitter on the queue item.
2. Worker restore uses that user so `SaveImage` writes under the submitter's output folder when `SEPERATE_USERS=true`.
3. `GET /history/{id}` and `GET /view?filename=…` resolve that user's outputs (NSFW policy may still 403).

Tokens are accepted from `Authorization: Bearer`, cookie `jwt_token`, or query `token` / `access_token`.

### Embedded UI→API convert (recommended)

MSS-Login can convert UI-format workflows **without** installing a separate comfy-portal-endpoint node, and **without** opening an unauthenticated login-wall bypass:

1. Optional dependency: `uv sync --group cpe-convert`
2. One-time browser: `playwright install chromium` (and on Linux, system deps if needed: `playwright install-deps`)
3. Config (default on): `"cpe_embedded_convert": true` in `config.json`
4. Optional: `"cpe_convert_token_minutes": 5` for short-lived JWT mint when the request has no Bearer yet

The headless browser opens ComfyUI on **loopback** with the caller's `jwt_token` cookie and `Authorization: Bearer` so the real frontend boots under auth.

If you also installed sibling `comfy-portal-endpoint`, **uninstall it** when using embedded convert to avoid two Playwright pools. Stock CPE still cannot load `/` under the login wall without cookie injection.

Attribution for adapted convert logic: [comfy-portal-endpoint NOTICE](../licenses/comfy-portal-endpoint-NOTICE.md).

## comfy-portal-endpoint

The [comfy-portal-endpoint](https://github.com/ShunL12324/comfy-portal-endpoint) extension is **optional** when MSS embedded convert is enabled. If you rely on stock CPE alone:

- Its headless browser loads `http://127.0.0.1/…` **without** auth cookies, so the MSS login wall typically breaks UI conversion.
- Prefer MSS embedded convert (above) or save API-format workflows from the desktop UI.

## Smooth auth for your own mobile app

For a custom mobile client, prefer this flow:

1. One-time: `POST /mss-login/generate_token` → store token securely.
2. Every request: `Authorization: Bearer <token>`.
3. WebSocket: `wss://host/ws?token=<token>`.
4. Validate once: `GET /mss-login/api/me` for permissions.
5. Run pipelines per [Image generation pipeline](../guide/image-generation.md).

Avoid scraping HTML login pages or cookies unless you control a WebView session.

## Troubleshooting

| Symptom | Likely cause |
|---------|----------------|
| 401 on `/prompt` from mobile network | Missing token, or `REQUIRE_AUTH_FOR_REMOTE_API=true` without Bearer auth |
| 401 on `/prompt` with token | Revoked token, wrong server, or clock skew on JWT sessions |
| 403 on `/prompt` | `can_run` false for role |
| 403 `MODEL_NOT_ALLOWED` | Workflow references models user cannot access (also on CPE save / get-and-convert for API-format graphs) |
| 403 on `/view` | NSFW policy blocked output, or filename from another user's history under `SEPERATE_USERS` |
| Portal cannot sync UI workflows | Playwright/Chromium missing, or convert failed; install `cpe-convert` group + Chromium, or save API-format workflows |
| 503 on get-and-convert (UI graph) | See `/cpe/health` browser status; check logs for Playwright errors |
| 401 JSON on `/cpe/workflow/list` or `/api/cpe/…` | Missing/invalid token, or remote API guard (non-LAN IP without Bearer) |
| Empty workflow list in Portal | No JSON files in that user's data-dir workflow folder yet; save one or copy into `users/<name>/workflows/` |
| HTML redirect to `/login` on non-CPE paths | Client sent `Accept: text/html` or `Sec-Fetch-Mode: navigate`; CPE/API paths return JSON 401 instead |

### Debug tips

- Confirm HTTPS and correct `HOST_BASE_URL` if links or redirects fail.
- Test with `curl -H "Authorization: Bearer …" https://host/mss-login/api/me`.
- Check server logs under the MSS-Login data directory.

## See also

- [Authentication](../guide/authentication.md)
- [Image generation pipeline](../guide/image-generation.md)
- [ComfyUI client APIs](../api-reference/comfyui-client-endpoints.md)
- [Headless JWT session](../guide/headless-jwt-session.md)
