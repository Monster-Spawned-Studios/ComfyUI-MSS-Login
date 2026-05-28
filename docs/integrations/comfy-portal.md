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

Ensure the user's role has `can_run` and `can_access_api`.

## comfy-portal-endpoint

The [comfy-portal-endpoint](https://github.com/ShunL12324/comfy-portal-endpoint) service provides workflow list/get/save/convert using a **headless browser** that loads the real ComfyUI frontend.

Implications for MSS-Login:

- A **login wall** that prevents the ComfyUI UI from loading breaks workflow conversion.
- If you enable strict auth, allow unauthenticated access to **minimal frontend assets** required for conversion, or run conversion on an internal network without the login intercept.
- Document for users that full auth + loading-page flows may disable Portal workflow sync.

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
| 403 `MODEL_NOT_ALLOWED` | Workflow references models user cannot access |
| 403 on `/view` | NSFW policy blocked output |
| Portal cannot sync workflows | Frontend blocked by auth; see comfy-portal-endpoint section |
| HTML redirect to `/login` instead of JSON | Client sent `Accept: text/html`; use Bearer header and expect JSON 401 |

### Debug tips

- Confirm HTTPS and correct `HOST_BASE_URL` if links or redirects fail.
- Test with `curl -H "Authorization: Bearer …" https://host/mss-login/api/me`.
- Check server logs under the MSS-Login data directory.

## See also

- [Authentication](../guide/authentication.md)
- [Image generation pipeline](../guide/image-generation.md)
- [ComfyUI client APIs](../api-reference/comfyui-client-endpoints.md)
- [Headless JWT session](../guide/headless-jwt-session.md)
