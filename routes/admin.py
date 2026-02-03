# --- START OF FILE routes/admin.py ---
import os
from aiohttp import web
from ..globals import routes, jwt_auth, users_db, ip_filter
from ..constants import (
    GROUPS_CONFIG_FILE, DEFAULT_GROUP_CONFIG_PATH, WHITELIST_FILE, BLACKLIST_FILE, USERS_FILE,
    CONFIG_FILE_PATH, reload_api_token_store_config, reload_allow_guest_jwt,
)
from ..utils.json_utils import load_json_file, save_json_file
from ..utils.admin_logic import patch_user_group, delete_user_record
from ..utils.bootstrap import load_default_groups
from ..utils.api_token_store import reset_api_token_store
from ..utils.user_console_log import get_lines as get_user_console_lines, list_users as list_console_users
from ..utils.ntfy_notifier import get_ntfy_config, save_ntfy_config, send_notification, EVENT_KEYS

def is_admin(request):
    token = jwt_auth.get_token_from_request(request)
    if not token: return False
    try:
        p = jwt_auth.decode_access_token(token)
        _, u = users_db.get_user(p['username'])
        return u.get('admin', False) or "admin" in u.get('groups', [])
    except: return False

@routes.get("/usgromana/api/settings/guest-jwt")
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


@routes.put("/usgromana/api/settings/guest-jwt")
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


@routes.get("/usgromana/api/settings/ntfy")
async def api_get_ntfy_settings(request):
    """Return ntfy config (topic, enabled_events). Authenticated; read available to all."""
    token = jwt_auth.get_token_from_request(request)
    if not token:
        return web.json_response({"error": "Authentication required"}, status=401)
    cfg = get_ntfy_config()
    return web.json_response({
        "topic": cfg.get("topic", ""),
        "enabled_events": cfg.get("enabled_events", []),
        "event_keys": EVENT_KEYS,
    })


@routes.put("/usgromana/api/settings/ntfy")
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


@routes.get("/usgromana/api/admin/consoles")
async def api_admin_consoles_list(request):
    """Return list of usernames that have console log entries (Admin only)."""
    if not is_admin(request):
        return web.json_response({"error": "Admin only"}, status=403)
    users = list_console_users()
    return web.json_response({"users": users})


@routes.get("/usgromana/api/admin/consoles/{username}")
async def api_admin_consoles_user(request):
    """Return console log lines for the given user (Admin only)."""
    if not is_admin(request):
        return web.json_response({"error": "Admin only"}, status=403)
    username = request.match_info.get("username", "")
    lines = get_user_console_lines(username)
    return web.json_response({"username": username, "lines": lines})


@routes.get("/usgromana/api/groups")
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

@routes.put("/usgromana/api/groups")
async def api_update_groups(request):
    if not is_admin(request): return web.json_response({"error": "Admin only"}, status=403)
    try:
        data = await request.json()
        new_groups = data.get("groups", {})
        current = load_json_file(GROUPS_CONFIG_FILE, {})
        for g, perms in new_groups.items():
            g_lower = g.lower()
            if g_lower not in current: current[g_lower] = {}
            for k, v in perms.items():
                current[g_lower][k] = bool(v)
        save_json_file(GROUPS_CONFIG_FILE, current)
        return web.json_response({"status": "ok"})
    except Exception as e:
        return web.json_response({"error": str(e)}, status=500)

@routes.get("/usgromana/api/users")
async def api_users(request):
    # Security: You might want to restrict this to admins only too
    if not is_admin(request): return web.json_response({"error": "Admin only"}, status=403)
    
    raw = load_json_file(USERS_FILE, {})
    users_list = []
    iterable = raw.get("users", raw).values() if isinstance(raw.get("users", raw), dict) else raw.get("users", raw)
    for u in iterable:
        users_list.append({
            "username": u.get("username", "unknown"),
            "groups": [g.lower() for g in u.get("groups", ["user"])],
            "is_admin": u.get("admin", False),
            # NEW: per-user SFW flag; default = True (SFW enabled)
            "sfw_check": u.get("sfw_check", True),
        })
    return web.json_response({"users": users_list})

@routes.put("/usgromana/api/users/{target_user}")
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

@routes.delete("/usgromana/api/users/{target_user}")
async def api_delete_user_route(request):
    if not is_admin(request): return web.json_response({"error": "Admin only"}, status=403)
    target = request.match_info["target_user"]
    if target == "guest": return web.json_response({"error": "Cannot delete guest"}, status=400)
    
    result = delete_user_record(target)
    if result == "last_admin": return web.json_response({"error": "Cannot delete last admin"}, status=400)
    if result is False: return web.Response(status=404)
    return web.json_response({"status": "ok"})

@routes.get("/usgromana/api/token-storage-config")
async def api_get_token_storage_config(request):
    """Return current API token store backend and non-secret options (admin only)."""
    if not is_admin(request):
        return web.json_response({"error": "Admin only"}, status=403)
    cfg = load_json_file(CONFIG_FILE_PATH, {})
    store_cfg = cfg.get("api_token_store") or {}
    out = {
        "backend": store_cfg.get("backend", "sqlite"),
        "json_path": store_cfg.get("json_path", "users/api_tokens.json"),
        "sqlite_path": store_cfg.get("sqlite_path", "users/api_tokens.db"),
        "postgres_host": store_cfg.get("postgres_host", "localhost"),
        "postgres_port": store_cfg.get("postgres_port", 5432),
        "postgres_database": store_cfg.get("postgres_database", "usgromana"),
        "postgres_user": store_cfg.get("postgres_user", "usgromana"),
    }
    return web.json_response(out)


@routes.put("/usgromana/api/token-storage-config")
async def api_put_token_storage_config(request):
    """Update API token store config (admin only). Password from env API_TOKEN_DB_PASSWORD only."""
    if not is_admin(request):
        return web.json_response({"error": "Admin only"}, status=403)
    try:
        data = await request.json()
        backend = (data.get("backend") or "sqlite").lower()
        if backend not in ("json", "sqlite", "postgresql"):
            return web.json_response({"error": "Invalid backend"}, status=400)
        cfg = load_json_file(CONFIG_FILE_PATH, {})
        if not isinstance(cfg, dict):
            cfg = {}
        api_cfg = cfg.get("api_token_store") or {}
        api_cfg["backend"] = backend
        api_cfg["json_path"] = data.get("json_path", api_cfg.get("json_path", "users/api_tokens.json"))
        api_cfg["sqlite_path"] = data.get("sqlite_path", api_cfg.get("sqlite_path", "users/api_tokens.db"))
        api_cfg["postgres_host"] = data.get("postgres_host", api_cfg.get("postgres_host", "localhost"))
        api_cfg["postgres_port"] = data.get("postgres_port", api_cfg.get("postgres_port", 5432))
        api_cfg["postgres_database"] = data.get("postgres_database", api_cfg.get("postgres_database", "usgromana"))
        api_cfg["postgres_user"] = data.get("postgres_user", api_cfg.get("postgres_user", "usgromana"))
        if "postgres_password" in api_cfg:
            del api_cfg["postgres_password"]
        cfg["api_token_store"] = api_cfg
        save_json_file(CONFIG_FILE_PATH, cfg)
        reload_api_token_store_config()
        reset_api_token_store()
        return web.json_response({"status": "ok"})
    except Exception as e:
        return web.json_response({"error": str(e)}, status=500)


@routes.get("/usgromana/api/ip-lists")
async def api_ip_lists(request):
    whitelist, blacklist = ip_filter.load_filter_list()
    return web.json_response({
        "whitelist": [str(ip) for ip in (whitelist or [])],
        "blacklist": [str(ip) for ip in (blacklist or [])]
    })

@routes.put("/usgromana/api/ip-lists")
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

@routes.post("/usgromana/api/nsfw-management")
async def api_nsfw_management(request):
    """Admin-only NSFW management endpoints."""
    if not is_admin(request):
        return web.json_response({"error": "Admin only"}, status=403)
    
    try:
        data = await request.json()
        action = data.get("action", "").strip()
        
        print(f"[Usgromana] NSFW management action: {action}")
        
        from ..utils.sfw_intercept.nsfw_guard import (
            scan_all_images_in_output_directory,
            fix_incorrectly_cached_tags,
            clear_all_nsfw_tags
        )
        
        # Run blocking operations in executor to avoid blocking the event loop
        import asyncio
        loop = asyncio.get_event_loop()
        
        if action == "scan_all":
            force_rescan = bool(data.get("force_rescan", False))
            print(f"[Usgromana] Starting scan_all (force_rescan={force_rescan}) in executor...")
            result = await loop.run_in_executor(
                None, 
                scan_all_images_in_output_directory, 
                force_rescan
            )
            print(f"[Usgromana] scan_all completed: {result}")
            return web.json_response({
                "status": "ok",
                "message": f"Scanned {result['scanned']} images. Found {result['nsfw_found']} NSFW images.",
                "stats": result
            })
        
        elif action == "fix_incorrect":
            print(f"[Usgromana] Starting fix_incorrect in executor...")
            fixed_count = await loop.run_in_executor(
                None,
                fix_incorrectly_cached_tags
            )
            print(f"[Usgromana] fix_incorrect completed: {fixed_count} fixed")
            return web.json_response({
                "status": "ok",
                "message": f"Fixed {fixed_count} incorrectly cached images.",
                "fixed_count": fixed_count
            })
        
        elif action == "clear_all_tags":
            print(f"[Usgromana] Starting clear_all_tags in executor...")
            cleared_count = await loop.run_in_executor(
                None,
                clear_all_nsfw_tags
            )
            print(f"[Usgromana] clear_all_tags completed: {cleared_count} cleared")
            return web.json_response({
                "status": "ok",
                "message": f"Cleared NSFW tags from {cleared_count} images.",
                "cleared_count": cleared_count
            })
        
        else:
            return web.json_response({"error": f"Unknown action: {action}"}, status=400)
    
    except Exception as e:
        import traceback
        print(f"[Usgromana] NSFW management error: {e}")
        traceback.print_exc()
        return web.json_response({"error": str(e)}, status=500)