# --- START OF FILE routes/static.py ---
import os
from aiohttp import web
from ..globals import routes
from ..constants import CSS_DIR, JS_DIR, ASSETS_DIR, HTML_DIR

# --- FIX: Create directories if they don't exist to prevent crash ---
for directory in [CSS_DIR, JS_DIR, ASSETS_DIR, HTML_DIR]:
    if not os.path.exists(directory):
        try:
            os.makedirs(directory, exist_ok=True)
            print(f"[MSS-Login] Created missing directory: {directory}")
        except Exception as e:
            print(f"[MSS-Login] Error creating directory {directory}: {e}")

# Register static routes
# (aiohttp will crash if the path doesn't exist on disk, hence the loop above)
routes.static("/mss-login/css", CSS_DIR)
routes.static("/mss-login/js", JS_DIR)
routes.static("/mss-login/assets", ASSETS_DIR)