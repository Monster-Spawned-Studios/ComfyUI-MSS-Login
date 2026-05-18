# Headless JWT session (WebSocket and REST only)

Clients with a **valid JWT** (or long-lived API token) can use ComfyUI in a **headless** way: **only WebSocket and REST (GET/PUT/POST)**. No HTML is served for headless sessions. You never need to load the loading page or the full ComfyUI UI.

## How it works

- **Obtain a token:** Use `POST /login` (username/password) or `POST /mss-login/generate_token` (with credentials) to get a JWT, or use a long-lived API token from Settings → mss-login → Generate Token.
- **Send the token** on every WebSocket and REST request (see below). The server does not serve any HTML for headless; you only talk to `/ws` and the REST endpoints.

## WebSocket

Connect with the token in the URL (or via cookie if your client sends it):

- **URL:** `ws://host/ws?token=<jwt>` or `ws://host/ws?access_token=<jwt>`
- **Cookie:** `jwt_token=<jwt>` (if the client sends cookies on the WebSocket handshake)

Use the WebSocket for:

- Real-time queue and execution updates
- Current image preview while a prompt is running

Same token format (JWT or API token) as REST.

## REST (GET / PUT / POST)

Send the token on every request using one of:

- **Header:** `Authorization: Bearer <token>`
- **Cookie:** `jwt_token=<token>`
- **Query (where applicable):** `?token=<token>` or `?access_token=<token>`

### Endpoints headless clients use

| Purpose | Method | Path / behavior |
|--------|--------|------------------|
| **Execution** | POST / PUT | `/prompt`, `/api/prompt` — queue a workflow |
| **Queue** | GET / POST | `/queue`, `/api/queue` — list queue, cancel, etc. |
| **History** | GET | `/history` — ComfyUI core; returns execution history |
| **Workflows** | GET | `/api/userdata?dir=workflows` — list workflows (global + user) |
| **Workflows** | GET | `.../userdata/workflows/{filename}` — load workflow content |
| **Workflows** | POST / DELETE | Same base path — save or delete (if permitted) |
| **Images** | GET | `/view?filename=...&type=output` — output image (NSFW enforced) |

All of the above require a valid token; they are **not** in the JWT public list. Unauthenticated requests get 401 (or redirect to `/login` for browser `Accept: text/html`).

## No loading page or HTML

- Headless clients **do not** request `GET /` or `GET /loading`.
- They use **only** WebSocket and REST with the same token.
- NSFW enforcement (e.g. on `/view`) and other security (auth, permissions) are unchanged.

## Summary

| Channel | How to send token |
|---------|-------------------|
| WebSocket | `ws://host/ws?token=<jwt>` or cookie `jwt_token` |
| REST | `Authorization: Bearer <token>`, or cookie `jwt_token`, or query `token` / `access_token` |

No HTML is required; a headless session is purely WebSocket + GET/PUT/POST.

## See also

- [Authentication](authentication.md) — API tokens and permissions for mobile apps
- [Image generation pipeline](image-generation.md) — Full flow from token to output images
- [ComfyUI client APIs](../api-reference/comfyui-client-endpoints.md) — Endpoint reference table
- [NSFW and outputs](nsfw-and-outputs.md) — 403 behavior on `/view`
- [Mobile and Comfy Portal](../integrations/comfy-portal.md)
