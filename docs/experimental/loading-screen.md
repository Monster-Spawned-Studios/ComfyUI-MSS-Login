# Loading screen (experimental)

## What it does

After login, redirects to `/loading` before the main ComfyUI UI. Tips rotate; a fail-safe redirects to `/` after a timeout.

MSS `/loading` is a **pre-ComfyUI interstitial** owned by MSS-Login. It does **not** replace or inject into ComfyUI’s default in-app splash at `/`. After Continue or the timeout, the browser navigates fully to `/`; ComfyUI may still show its own brief loader. This load-before approach stays compatible across ComfyUI_frontend / PyPI UI package versions.

`web/js/loading.js` only runs when `pathname === "/loading"`, so it never redirects or mutates the ComfyUI app shell if the script is ever loaded elsewhere.

## How to enable

- Master + `experimental.loading_screen: true` or `EXPERIMENTAL_LOADING_SCREEN=1`
- Timeout: `MSS_LOGIN_LOADING_TIMEOUT_SECONDS` or `loading_screen.timeout_seconds` (1–300, default 15)

## When it is required

Optional UX only. Not required for auth, downloads, or API clients.

## See also

- [Experimental Settings hub](index.md)
