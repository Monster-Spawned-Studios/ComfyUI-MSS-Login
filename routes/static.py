# --- START OF FILE routes/static.py ---
import os
from aiohttp import web
from ..globals import routes
from ..constants import CSS_DIR, JS_DIR, ASSETS_DIR, HTML_DIR

# Subfolder under assets for background music; only these extensions are listed.
BG_MUSIC_SUBDIR = "bgMusic"
ALLOWED_AUDIO_EXTENSIONS = frozenset({".mp3", ".ogg", ".wav", ".m4a", ".aac", ".webm"})

# --- FIX: Create directories if they don't exist to prevent crash ---
_bg_music_dir = os.path.join(ASSETS_DIR, BG_MUSIC_SUBDIR)
for directory in [CSS_DIR, JS_DIR, ASSETS_DIR, HTML_DIR, _bg_music_dir]:
	if not os.path.exists(directory):
		try:
			os.makedirs(directory, exist_ok=True)
			print(f"[MSS-Login] Created missing directory: {directory}")
		except Exception as e:
			print(f"[MSS-Login] Error creating directory {directory}: {e}")


@routes.get("/mss-login/api/bg-music")
async def list_bg_music(request):
	"""
	List background music files from the filesystem (web/assets/bgMusic).
	Returns JSON: { "files": ["/mss-login/assets/bgMusic/file1.mp3", ...] }.
	Only files with allowed audio extensions are included.
	"""
	# Path is under ASSETS_DIR so we do not escape the node's web assets.
	bg_music_dir = os.path.join(ASSETS_DIR, BG_MUSIC_SUBDIR)
	if not os.path.isdir(bg_music_dir):
		return web.json_response({"files": []})

	files = []
	for name in os.listdir(bg_music_dir):
		if os.path.isfile(os.path.join(bg_music_dir, name)):
			ext = os.path.splitext(name)[1].lower()
			if ext in ALLOWED_AUDIO_EXTENSIONS:
				# URL path under the static mount; no user input in path.
				files.append(f"/mss-login/assets/{BG_MUSIC_SUBDIR}/{name}")

	return web.json_response({"files": files})


# Register static routes
# (aiohttp will crash if the path doesn't exist on disk, hence the loop above)
routes.static("/mss-login/css", CSS_DIR)
routes.static("/mss-login/js", JS_DIR)
routes.static("/mss-login/assets", ASSETS_DIR)
