# Notice: comfy-portal-endpoint (adapted code)

Portions of MSS-Login's embedded Comfy Portal Endpoint converter
(`utils/cpe_browser.py`) are adapted from:

- Project: [comfy-portal-endpoint](https://github.com/ShunL12324/comfy-portal-endpoint)
- Copyright: © 2025 Shun.L
- License: MIT License

The MIT License permits use, modification, and redistribution with attribution.
MSS-Login does **not** copy CPE's runtime `pip install playwright` installer;
Playwright is an optional dependency (`uv sync --group cpe-convert`) and
Chromium must be installed separately (`playwright install chromium`).

Full upstream license text: https://github.com/ShunL12324/comfy-portal-endpoint/blob/main/LICENSE
