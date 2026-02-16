# --- START OF FILE routes/admin.py ---
import os
from aiohttp import web
from ..globals import routes, jwt_auth, users_db, ip_filter
from ..constants import (
	GROUPS_CONFIG_FILE,
	DEFAULT_GROUP_CONFIG_PATH,
	WHITELIST_FILE,
	BLACKLIST_FILE,
	CONFIG_FILE_PATH,
	reload_api_token_store_config,
	reload_allow_guest_jwt,
	reload_users_db_config,
	USERS_DB_CONFIG,
)
from ..utils.json_utils import load_json_file, save_json_file
from ..utils.admin_logic import patch_user_group, delete_user_record
from ..utils.bootstrap import load_default_groups
from ..utils.api_token_store import reset_api_token_store
from ..utils.user_console_log import (
	get_lines as get_user_console_lines,
	list_users as list_console_users,
)
from ..utils.ntfy_notifier import get_ntfy_config, save_ntfy_config, send_notification, EVENT_KEYS
from ..utils.shared_items_store import get_shared_items_store
from ..utils.model_cache import get_model_cache
from ..constants import USERS_DB_CONFIG


def is_admin(request):
	token = jwt_auth.get_token_from_request(request)
	if not token:
		return False
	try:
		p = jwt_auth.decode_access_token(token)
		_, u = users_db.get_user(p["username"])
		return u.get("admin", False) or "admin" in u.get("groups", [])
	except:
		return False


@routes.get("/mss-login/api/settings/guest-jwt")
async def api_get_guest_jwt(request):
	"""Return allow_guest_jwt (authenticated; any user can read)."""
	token = jwt_auth.get_token_from_request(request)
	if not token:
		return web.json_response({"error": "Authentication required"}, status=401)
	try:
		cfg = load_json_file(CONFIG_FILE_PATH, {})
		allow = bool(cfg.get("allow_guest_jwt", False))
		return web.json_response({"allow_guest_jwt": allow})
	except Exception:
		return web.json_response({"allow_guest_jwt": False})


@routes.put("/mss-login/api/settings/guest-jwt")
async def api_put_guest_jwt(request):
	"""Update allow_guest_jwt (Admin only)."""
	if not is_admin(request):
		return web.json_response({"error": "Admin only"}, status=403)
	try:
		data = await request.json()
		allow = bool(data.get("allow_guest_jwt", False))
		cfg = load_json_file(CONFIG_FILE_PATH, {})
		if not isinstance(cfg, dict):
			cfg = {}
		cfg["allow_guest_jwt"] = allow
		save_json_file(CONFIG_FILE_PATH, cfg)
		reload_allow_guest_jwt()
		return web.json_response({"status": "ok", "allow_guest_jwt": allow})
	except Exception as e:
		return web.json_response({"error": str(e)}, status=500)


@routes.get("/mss-login/api/settings/ntfy")
async def api_get_ntfy_settings(request):
	"""Return ntfy config (topic, enabled_events). Authenticated; read available to all."""
	token = jwt_auth.get_token_from_request(request)
	if not token:
		return web.json_response({"error": "Authentication required"}, status=401)
	cfg = get_ntfy_config()
	return web.json_response(
		{
			"topic": cfg.get("topic", ""),
			"enabled_events": cfg.get("enabled_events", []),
			"event_keys": EVENT_KEYS,
		}
	)


@routes.put("/mss-login/api/settings/ntfy")
async def api_put_ntfy_settings(request):
	"""Update ntfy config (Admin only)."""
	if not is_admin(request):
		return web.json_response({"error": "Admin only"}, status=403)
	try:
		data = await request.json()
		topic = (data.get("topic") or "").strip()
		enabled = data.get("enabled_events")
		if not isinstance(enabled, list):
			enabled = []
		save_ntfy_config(topic, enabled)
		return web.json_response({"status": "ok"})
	except Exception as e:
		return web.json_response({"error": str(e)}, status=500)


@routes.get("/mss-login/api/admin/consoles")
async def api_admin_consoles_list(request):
	"""Return list of usernames that have console log entries (Admin only)."""
	if not is_admin(request):
		return web.json_response({"error": "Admin only"}, status=403)
	users = list_console_users()
	return web.json_response({"users": users})


@routes.get("/mss-login/api/admin/consoles/{username}")
async def api_admin_consoles_user(request):
	"""Return console log lines for the given user (Admin only)."""
	if not is_admin(request):
		return web.json_response({"error": "Admin only"}, status=403)
	username = request.match_info.get("username", "")
	lines = get_user_console_lines(username)
	return web.json_response({"username": username, "lines": lines})


@routes.get("/mss-login/api/groups")
async def api_groups(request):
	default_cfg = load_default_groups()
	current = load_json_file(GROUPS_CONFIG_FILE, default_cfg)
	if not isinstance(current, dict):
		current = dict(default_cfg) if default_cfg else {}
	# Merge default keys into each role so new permissions (e.g. can_have_non_expiring_jwt) always appear in the UI
	for role, default_perms in (default_cfg or {}).items():
		if role not in current:
			current[role] = dict(default_perms)
		elif isinstance(default_perms, dict):
			for key, val in default_perms.items():
				if key not in current[role]:
					current[role][key] = val
	return web.json_response({"groups": current})


@routes.put("/mss-login/api/groups")
async def api_update_groups(request):
	if not is_admin(request):
		return web.json_response({"error": "Admin only"}, status=403)
	try:
		data = await request.json()
		new_groups = data.get("groups", {})
		current = load_json_file(GROUPS_CONFIG_FILE, {})
		for g, perms in new_groups.items():
			g_lower = g.lower()
			if g_lower not in current:
				current[g_lower] = {}
			for k, v in perms.items():
				current[g_lower][k] = bool(v)
		save_json_file(GROUPS_CONFIG_FILE, current)
		return web.json_response({"status": "ok"})
	except Exception as e:
		return web.json_response({"error": str(e)}, status=500)


@routes.get("/mss-login/api/users")
async def api_users(request):
	if not is_admin(request):
		return web.json_response({"error": "Admin only"}, status=403)
	users_list = users_db.list_users_for_admin()
	return web.json_response({"users": users_list})


@routes.put("/mss-login/api/users/{target_user}")
async def api_update_user_route(request):
	if not is_admin(request):
		return web.json_response({"error": "Admin only"}, status=403)

	target = request.match_info["target_user"]
	data = await request.json()

	groups = [g.lower() for g in data.get("groups", [])]
	is_admin_flag = "admin" in groups

	# NEW: optional SFW flag
	sfw_check = data.get("sfw_check", None)

	success = patch_user_group(target, groups, is_admin_flag, sfw_check)
	if success:
		return web.json_response({"status": "ok"})
	return web.Response(status=404)


@routes.delete("/mss-login/api/users/{target_user}")
async def api_delete_user_route(request):
	if not is_admin(request):
		return web.json_response({"error": "Admin only"}, status=403)
	target = request.match_info["target_user"]
	if target == "guest":
		return web.json_response({"error": "Cannot delete guest"}, status=400)

	result = delete_user_record(target)
	if result == "last_admin":
		return web.json_response({"error": "Cannot delete last admin"}, status=400)
	if result is False:
		return web.Response(status=404)
	return web.json_response({"status": "ok"})


@routes.get("/mss-login/api/users-db-config")
async def api_get_users_db_config(request):
	"""Return current users DB backend, paths, and encryption_level (admin only). Password never returned."""
	if not is_admin(request):
		return web.json_response({"error": "Admin only"}, status=403)
	out = {
		"backend": USERS_DB_CONFIG.get("backend", "sqlite"),
		"sqlite_path": USERS_DB_CONFIG.get("sqlite_path", "users/users.db"),
		"postgres_host": USERS_DB_CONFIG.get("postgres_host", "localhost"),
		"postgres_port": USERS_DB_CONFIG.get("postgres_port", 5432),
		"postgres_database": USERS_DB_CONFIG.get("postgres_database", "mss_login"),
		"postgres_user": USERS_DB_CONFIG.get("postgres_user", "mss_login"),
		"encryption_level": USERS_DB_CONFIG.get("encryption_level", ""),
	}
	return web.json_response(out)


@routes.put("/mss-login/api/users-db-config")
async def api_put_users_db_config(request):
	"""Update users DB config (admin only). Restart required for new backend to take effect. Password from env only."""
	if not is_admin(request):
		return web.json_response({"error": "Admin only"}, status=403)
	try:
		data = await request.json()
		backend = (data.get("backend") or "sqlite").lower()
		if backend not in ("sqlite", "postgresql"):
			return web.json_response(
				{"error": "Invalid backend; use sqlite or postgresql"}, status=400
			)
		cfg = load_json_file(CONFIG_FILE_PATH, {})
		if not isinstance(cfg, dict):
			cfg = {}
		udb = cfg.get("users_db") or {}
		if isinstance(udb, str):
			udb = {}
		udb["backend"] = backend
		udb["sqlite_path"] = data.get("sqlite_path", udb.get("sqlite_path", "users/users.db"))
		udb["postgres_host"] = data.get("postgres_host", udb.get("postgres_host", "localhost"))
		udb["postgres_port"] = data.get("postgres_port", udb.get("postgres_port", 5432))
		udb["postgres_database"] = data.get(
			"postgres_database", udb.get("postgres_database", "mss_login")
		)
		udb["postgres_user"] = data.get("postgres_user", udb.get("postgres_user", "mss_login"))
		udb["encryption_level"] = (
			data.get("encryption_level") or udb.get("encryption_level") or ""
		).strip()
		cfg["users_db"] = udb
		save_json_file(CONFIG_FILE_PATH, cfg)
		reload_users_db_config()
		return web.json_response(
			{"status": "ok", "message": "Restart required for new backend to take effect."}
		)
	except Exception as e:
		return web.json_response({"error": str(e)}, status=500)


@routes.get("/mss-login/api/token-storage-config")
async def api_get_token_storage_config(request):
	"""Return token storage config (admin only). Uses same DB as users unless backend is json. Postgres password is env-only."""
	if not is_admin(request):
		return web.json_response({"error": "Admin only"}, status=403)
	cfg = load_json_file(CONFIG_FILE_PATH, {})
	store_cfg = cfg.get("api_token_store") or {}
	use_same_db = (store_cfg.get("backend") or "").strip().lower() != "json"
	out = {
		"backend": USERS_DB_CONFIG.get("backend", "sqlite") if use_same_db else "json",
		"json_path": store_cfg.get("json_path", "users/api_tokens.json"),
		"use_same_db_as_users": use_same_db,
	}
	return web.json_response(out)


@routes.put("/mss-login/api/token-storage-config")
async def api_put_token_storage_config(request):
	"""Update token storage config (admin only). Backend: 'json' = legacy file; else use same DB as users. Postgres password is env-only."""
	if not is_admin(request):
		return web.json_response({"error": "Admin only"}, status=403)
	try:
		data = await request.json()
		backend = (data.get("backend") or "database").strip().lower()
		if backend not in ("json", "database", "sqlite", "postgresql"):
			return web.json_response(
				{"error": "Invalid backend; use json or database (same DB as users)"}, status=400
			)
		if backend in ("sqlite", "postgresql"):
			backend = "database"
		cfg = load_json_file(CONFIG_FILE_PATH, {})
		if not isinstance(cfg, dict):
			cfg = {}
		api_cfg = cfg.get("api_token_store") or {}
		api_cfg["backend"] = backend
		api_cfg["json_path"] = data.get(
			"json_path", api_cfg.get("json_path", "users/api_tokens.json")
		)
		cfg["api_token_store"] = api_cfg
		save_json_file(CONFIG_FILE_PATH, cfg)
		reload_api_token_store_config()
		reset_api_token_store()
		return web.json_response({"status": "ok"})
	except Exception as e:
		return web.json_response({"error": str(e)}, status=500)


@routes.get("/mss-login/api/ip-lists")
async def api_ip_lists(request):
	whitelist, blacklist = ip_filter.load_filter_list()
	return web.json_response(
		{
			"whitelist": [str(ip) for ip in (whitelist or [])],
			"blacklist": [str(ip) for ip in (blacklist or [])],
		}
	)


@routes.put("/mss-login/api/ip-lists")
async def api_update_ip_lists(request):
	if not is_admin(request):
		return web.json_response({"error": "Admin only"}, status=403)
	try:
		data = await request.json()
		whitelist = data.get("whitelist", [])
		blacklist = data.get("blacklist", [])

		# Validate and write whitelist
		import ipaddress

		# Write whitelist
		with open(WHITELIST_FILE, "w") as f:
			for ip_entry in whitelist:
				ip_entry = ip_entry.strip()
				if ip_entry:
					try:
						# Validate IP or CIDR
						try:
							ipaddress.ip_address(ip_entry)
						except ValueError:
							ipaddress.ip_network(ip_entry, strict=False)
						f.write(ip_entry + "\n")
					except ValueError:
						# Skip invalid entries
						continue

		# Write blacklist
		with open(BLACKLIST_FILE, "w") as f:
			for ip_entry in blacklist:
				ip_entry = ip_entry.strip()
				if ip_entry:
					try:
						# Validate IP or CIDR
						try:
							ipaddress.ip_address(ip_entry)
						except ValueError:
							ipaddress.ip_network(ip_entry, strict=False)
						f.write(ip_entry + "\n")
					except ValueError:
						# Skip invalid entries
						continue

		# Reload the filter lists to update in-memory cache
		ip_filter.load_filter_list()

		return web.json_response({"status": "ok"})
	except Exception as e:
		return web.json_response({"error": str(e)}, status=500)


@routes.get("/mss-login/api/available-model-folders")
async def api_available_model_folders(request):
	"""List ComfyUI model folder names (for admin shared-items UI). Admin only."""
	if not is_admin(request):
		return web.json_response({"error": "Admin only"}, status=403)
	try:
		import folder_paths  # type: ignore[import-untyped]  # ComfyUI core module; resolves at runtime

		folders = list(folder_paths.folder_names_and_paths.keys())
	except Exception:
		folders = [
			"checkpoints",
			"loras",
			"vae",
			"embeddings",
			"controlnet",
			"clip_vision",
			"upscale_models",
		]
	return web.json_response({"folders": folders})


@routes.get("/mss-login/api/available-models/{folder}")
async def api_available_models_in_folder(request):
	"""List model/item names in a folder (for admin shared-items UI). Admin only."""
	if not is_admin(request):
		return web.json_response({"error": "Admin only"}, status=403)
	folder = request.match_info.get("folder", "")
	try:
		import folder_paths  # type: ignore[import-untyped]  # ComfyUI core module; resolves at runtime

		names = folder_paths.get_filename_list(folder)
	except Exception:
		names = []
	return web.json_response({"folder": folder, "items": names})


@routes.get("/mss-login/api/model-cache/folders")
async def api_model_cache_folders(request):
	"""List model folder names from cache (admin only). For admin Shared Models UI."""
	if not is_admin(request):
		return web.json_response({"error": "Admin only"}, status=403)
	try:
		cache = get_model_cache(USERS_DB_CONFIG)
		folders = cache.list_folders()
		return web.json_response({"folders": folders})
	except Exception as e:
		return web.json_response({"error": str(e)}, status=500)


@routes.get("/mss-login/api/model-cache/folders/{folder}/items")
async def api_model_cache_folder_items(request):
	"""List model/item names in a folder from cache (admin only)."""
	if not is_admin(request):
		return web.json_response({"error": "Admin only"}, status=403)
	folder = request.match_info.get("folder", "")
	try:
		cache = get_model_cache(USERS_DB_CONFIG)
		items = cache.list_items(folder)
		return web.json_response({"folder": folder, "items": items})
	except Exception as e:
		return web.json_response({"error": str(e)}, status=500)


@routes.post("/mss-login/api/model-cache/refresh")
async def api_model_cache_refresh(request):
	"""Refresh model cache from ComfyUI folder_paths (admin only)."""
	if not is_admin(request):
		return web.json_response({"error": "Admin only"}, status=403)
	try:
		cache = get_model_cache(USERS_DB_CONFIG)
		folders, total = cache.refresh_from_folder_paths()
		return web.json_response({
			"status": "ok",
			"folders_count": len(folders),
			"items_count": total,
			"folders": folders,
		})
	except Exception as e:
		return web.json_response({"error": str(e)}, status=500)


@routes.get("/mss-login/api/users/{username}/shared-items")
async def api_get_shared_items(request):
	"""List shared ComfyUI items (models, LoRAs, VAEs, embeddings) for a user (admin only)."""
	if not is_admin(request):
		return web.json_response({"error": "Admin only"}, status=403)
	username = request.match_info.get("username", "")
	user_id, _ = users_db.get_user(username=username)
	if not user_id:
		return web.json_response({"error": "User not found"}, status=404)
	store = get_shared_items_store(USERS_DB_CONFIG)
	items = store.list_for_user(user_id)
	return web.json_response({"username": username, "items": items})


@routes.post("/mss-login/api/users/{username}/shared-items")
async def api_add_shared_item(request):
	"""Add one shared item for a user. Body: { "folder": "checkpoints", "item_name": "model.safetensors" } (admin only)."""
	if not is_admin(request):
		return web.json_response({"error": "Admin only"}, status=403)
	username = request.match_info.get("username", "")
	user_id, _ = users_db.get_user(username=username)
	if not user_id:
		return web.json_response({"error": "User not found"}, status=404)
	try:
		data = await request.json()
		folder = (data.get("folder") or "").strip()
		item_name = (data.get("item_name") or "").strip()
		if not folder or not item_name:
			return web.json_response({"error": "folder and item_name required"}, status=400)
		store = get_shared_items_store(USERS_DB_CONFIG)
		if store.add(user_id, folder, item_name):
			return web.json_response({"status": "ok", "folder": folder, "item_name": item_name})
		return web.json_response({"error": "Failed to add (may already exist)"}, status=400)
	except Exception as e:
		return web.json_response({"error": str(e)}, status=500)


@routes.delete("/mss-login/api/users/{username}/shared-items")
async def api_remove_shared_item(request):
	"""Remove one shared item for a user. Body: { "folder": "...", "item_name": "..." } (admin only)."""
	if not is_admin(request):
		return web.json_response({"error": "Admin only"}, status=403)
	username = request.match_info.get("username", "")
	user_id, _ = users_db.get_user(username=username)
	if not user_id:
		return web.json_response({"error": "User not found"}, status=404)
	try:
		data = await request.json()
		folder = (data.get("folder") or "").strip()
		item_name = (data.get("item_name") or "").strip()
		if not folder or not item_name:
			return web.json_response({"error": "folder and item_name required"}, status=400)
		store = get_shared_items_store(USERS_DB_CONFIG)
		if store.remove(user_id, folder, item_name):
			return web.json_response({"status": "ok"})
		return web.json_response({"error": "Item not found"}, status=404)
	except Exception as e:
		return web.json_response({"error": str(e)}, status=500)


@routes.post("/mss-login/api/nsfw-management")
async def api_nsfw_management(request):
	"""Admin-only NSFW management endpoints."""
	if not is_admin(request):
		return web.json_response({"error": "Admin only"}, status=403)

	try:
		data = await request.json()
		action = data.get("action", "").strip()

		print(f"[mss_login] NSFW management action: {action}")

		from ..utils.sfw_intercept.nsfw_guard import (
			scan_all_images_in_output_directory,
			fix_incorrectly_cached_tags,
			clear_all_nsfw_tags,
		)

		# Run blocking operations in executor to avoid blocking the event loop
		import asyncio

		loop = asyncio.get_event_loop()

		if action == "scan_all":
			force_rescan = bool(data.get("force_rescan", False))
			print(f"[mss_login] Starting scan_all (force_rescan={force_rescan}) in executor...")
			result = await loop.run_in_executor(
				None, scan_all_images_in_output_directory, force_rescan
			)
			print(f"[mss_login] scan_all completed: {result}")
			return web.json_response(
				{
					"status": "ok",
					"message": f"Scanned {result['scanned']} images. Found {result['nsfw_found']} NSFW images.",
					"stats": result,
				}
			)

		elif action == "fix_incorrect":
			print(f"[mss_login] Starting fix_incorrect in executor...")
			fixed_count = await loop.run_in_executor(None, fix_incorrectly_cached_tags)
			print(f"[mss_login] fix_incorrect completed: {fixed_count} fixed")
			return web.json_response(
				{
					"status": "ok",
					"message": f"Fixed {fixed_count} incorrectly cached images.",
					"fixed_count": fixed_count,
				}
			)

		elif action == "clear_all_tags":
			print(f"[mss_login] Starting clear_all_tags in executor...")
			cleared_count = await loop.run_in_executor(None, clear_all_nsfw_tags)
			print(f"[mss_login] clear_all_tags completed: {cleared_count} cleared")
			return web.json_response(
				{
					"status": "ok",
					"message": f"Cleared NSFW tags from {cleared_count} images.",
					"cleared_count": cleared_count,
				}
			)

		else:
			return web.json_response({"error": f"Unknown action: {action}"}, status=400)

	except Exception as e:
		import traceback

		print(f"[mss_login] NSFW management error: {e}")
		traceback.print_exc()
		return web.json_response({"error": str(e)}, status=500)
