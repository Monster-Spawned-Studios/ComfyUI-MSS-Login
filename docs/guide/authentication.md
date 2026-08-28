# Authentication

MSS-Login protects ComfyUI with JWT sessions, long-lived API tokens, and role-based permissions. Mobile and headless clients should use **API tokens** for the smoothest experience: one token, sent on every HTTP request and WebSocket connection, without cookie or HTML flows.

## Token types

| Type | How obtained | Typical use |
|------|----------------|-------------|
| **Session JWT** | `POST /login` (after password, and MFA if enabled) | Browser sessions; short-lived; revocable via session store |
| **API token** | `POST /mss-login/generate_token` | Mobile apps, scripts, Comfy Portal; long-lived; revocable in Settings or `DELETE /mss-login/api/tokens` |

Both are accepted wherever the docs say "Bearer token". The server distinguishes them automatically: JWTs contain dots; API tokens are opaque strings stored in the database.

## Recommended flow for mobile apps

1. **Create an API token once** (user enters credentials in your app):

   ```http
   POST /mss-login/generate_token
   Content-Type: application/json

   {"username": "user", "password": "secret"}
   ```

   Response includes a token string. Store it securely (Keychain, EncryptedSharedPreferences, etc.).

2. **Verify role and permissions**:

   ```http
   GET /mss-login/api/me
   Authorization: Bearer <token>
   ```

   Confirm the user has at least:

   - `can_run` — submit workflows (`POST /prompt`)
   - `can_access_api` — `GET /object_info`, queue, history, userdata
   - `can_have_api_tokens` — only needed when generating tokens from the app

3. **Use the token on every ComfyUI call** — see [Image generation pipeline](image-generation.md) and [Headless JWT session](headless-jwt-session.md).

### MFA during token generation

If MFA is enabled, `POST /login` may return `mfa_required` and a `mfa_temp_token`. Complete verification with `POST /mss-login/api/mfa/verify`, then use the returned `jwt_token` to call `POST /mss-login/generate_token`, or pass MFA fields supported by the generate-token endpoint (see [HTTP Endpoints](../api-reference/endpoints.md)).

## Sending the token

| Channel | Method |
|---------|--------|
| REST | `Authorization: Bearer <token>` (preferred) |
| REST | Cookie `jwt_token=<token>` |
| REST | Query `?token=<token>` or `?access_token=<token>` where applicable |
| WebSocket | `ws://host/ws?token=<token>` or cookie on handshake |

For mobile, **always use the Bearer header** on REST. For WebSocket, append `?token=` to the URL if your client does not send cookies.

## Browser login (reference)

| Endpoint | Purpose |
|----------|---------|
| `POST /login` | Username/password → `jwt_token` cookie or JSON |
| `GET /logout` | End session |
| `POST /register` | New user. Public **only** for the first admin (empty user database). After that, an authenticated admin session is required (Settings → MSS-Login → Register a New User). |

Public paths (no token required) include `/login`, `/mfa`, `/mss-login/*` static pages, and assets. `/register` is public only until the first admin exists. **Not** public: `/prompt`, `/queue`, `/history`, `/view`, `/object_info`, `/ws`, `/api/cpe/*`.

## Remote API guard

When `REQUIRE_AUTH_FOR_REMOTE_API=true` (env or `config.json`), clients **outside** the server's local network must present a **valid** token on protected routes. Local clients (loopback / configured CIDRs) may still reach some paths without auth depending on setup.

Mobile apps connecting over the internet should always send a Bearer token and expect **401 JSON** on failure (not an HTML redirect to `/login`).

## Permissions that affect API use

Roles are defined in `users/mss_login_groups.json`. Common permission keys:

| Permission | Effect on API clients |
|------------|------------------------|
| `can_run` | Required for `POST /prompt` |
| `can_access_api` | Required for most `/api/*` and `GET /object_info` |
| `can_upload` | Required for `POST /upload/image` |
| `can_modify_workflows` | Save/delete via userdata workflow API |
| `can_have_api_tokens` | Allow `POST /mss-login/generate_token` |
| `can_view_all_comfyui_items` | Unfiltered `/models` and `/embeddings` lists |

Guest role typically has `can_run: false` — mobile apps should surface a clear error if `/mss-login/api/me` shows insufficient permissions.

## Session management

| Endpoint | Purpose |
|----------|---------|
| `GET /mss-login/me/sessions` | List active session JWTs for current user |
| `POST /mss-login/me/sessions/revoke` | Revoke a session by `jti` |

API tokens are listed and revoked via `GET` / `DELETE /mss-login/api/tokens`.

## Security notes

- Never embed tokens in client-side web pages or commit them to source control.
- Use HTTPS in production (`FORCE_HTTPS` or reverse proxy TLS).
- Rotate API tokens if a device is lost; revoke via admin or user token list.

## See also

- [Image generation pipeline](image-generation.md) — full run flow after auth
- [Headless JWT session](headless-jwt-session.md) — WebSocket and REST-only clients
- [Mobile and Comfy Portal](../integrations/comfy-portal.md) — iOS/Android integration
- [HTTP Endpoints](../api-reference/endpoints.md) — generated route table
