# Loading screen (experimental)

## What it does

After login, redirects to `/loading` before the main ComfyUI UI. Tips rotate; a fail-safe redirects to `/` after a timeout.

## How to enable

- Master + `experimental.loading_screen: true` or `EXPERIMENTAL_LOADING_SCREEN=1`
- Timeout: `MSS_LOGIN_LOADING_TIMEOUT_SECONDS` or `loading_screen.timeout_seconds` (1–300, default 15)

## When it is required

Optional UX only. Not required for auth, downloads, or API clients.

## See also

- [Experimental Settings hub](index.md)
