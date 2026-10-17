# ComfyUI extension scripts (`WEB_DIRECTORY`)

This folder is the only tree ComfyUI recursively scans for MSS-Login extension JavaScript (`WEB_DIRECTORY = "web/comfyui"`).

Do **not** place Vite source (`web/frontend`), `node_modules`, or build output (`web/dist`) here. Those are served via `/mss-login/*` static routes or used only at build time. Putting them under `WEB_DIRECTORY` causes Comfy to preload hundreds of Node modules in the browser (`node:path` CORS / `[vite:preloadError]`).
