# --- START OF FILE routes/admin.py ---
"""Routes for the admin API.

This module contains the routes for the admin API.
"""

from aiohttp import web

from ..constants import (
	CONFIG_FILE_PATH,
	DEFAULT_GROUP_CONFIG_PATH,
	GROUPS_CONFIG_FILE,
	USERS_DB_CONFIG,
	get_domain,
	get_experimental_flags,
	get_experimental_failsafe_settings,
	reload_allow_guest_jwt,
	reload_api_token_store_config,
	reload_experimental_features,
	reload_experimental_failsafe,
	reload_users_db_config,
	save_experimental_failsafe_settings,
)
from ..globals import ip_filter, jwt_auth, logger, routes, users_db
from ..utils.admin_logic import delete_user_record, patch_user_group
from ..utils.api_token_store import reset_api_token_store
from ..utils.bootstrap import _apply_owner_max_merge, load_default_groups
from ..utils.json_utils import load_json_file, save_json_file
from ..utils.model_cache import get_model_cache
from ..utils.ntfy_notifier import (
	EVENT_KEYS,
	get_ntfy_config,
	save_ntfy_config,
	send_notification,
	verify_signed_action_token,
)
from ..utils.path_safety import is_safe_filename, is_safe_folder_segment, path_under
from ..utils.quarantine_store import (
	get_quarantine_settings,
	list_quarantine_items,
	mark_quarantine_item_reviewed,
	quarantine_image_file,
)
from ..utils.shared_items_store import get_shared_items_store
from ..utils.updater import get_cached_status
from ..utils.user_console_log import get_lines as get_user_console_lines
from ..utils.user_console_log import list_users as list_console_users
from ..utils.model_download_redirect import (
	get_configured_route_patterns,
	get_effective_route_patterns,
	save_configured_route_patterns,
)
from ..utils.model_visibility_policy import user_can_manage_model_sharing


def is_admin(request):
	"""Check if the user is an admin.

	Args:
	    request (web.Request): The request object.

	Returns:
	    bool: True if the user is an admin, False otherwise.
	"""
	token = jwt_auth.get_token_from_request(request)
	if not token:
		return False
	try:
		p = jwt_auth.decode_access_token(token)
		_, u = users_db.get_user(p["username"])
		groups = [g.lower() for g in (u.get("groups") or [])]
		return u.get("admin", False) or "admin" in groups or "owner" in groups
	except Exception:
		return False


def _get_caller_username_and_groups(request):
	"""Return (username, groups) for the authenticated caller, or (None, [])."""
	token = jwt_auth.get_token_from_request(request)
	if not token:
		return None, []
	try:
		p = jwt_auth.decode_access_token(token)
		username = p.get("username")
		if not username:
			return None, []
		_, u = users_db.get_user(username)
		if not u:
			return None, []
		groups = [g.lower() for g in (u.get("groups") or [])]
		return username, groups
	except Exception:
		return None, []


def is_owner(request):
	"""Return True if the authenticated user has 'owner' in their groups."""
	_username, groups = _get_caller_username_and_groups(request)
	return "owner" in groups


def _get_caller_role_and_permissions(request):
	"""Resolve caller role/perms using access control for consistent RBAC checks."""
	try:
		from ..globals import access_control

		role, perms, username = access_control._get_user_role_and_permissions(request)
		return role, perms, username
	except Exception:
		return "guest", {}, None


def _can_manage_model_sharing(request) -> bool:
	role, perms, _ = _get_caller_role_and_permissions(request)
	return user_can_manage_model_sharing(role, perms)


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


routes.get("/api/mss-login/api/settings/guest-jwt")(api_get_guest_jwt)
routes.put("/api/mss-login/api/settings/guest-jwt")(api_put_guest_jwt)


@routes.get("/mss-login/api/settings/model-isolation-download-patterns")
async def api_get_model_isolation_download_patterns(request):
	"""Return model download redirect patterns (owner only)."""
	if not is_owner(request):
		return web.json_response({"error": "Owner only"}, status=403)
	return web.json_response(
		{
			"configured_patterns": get_configured_route_patterns(),
			"effective_patterns": get_effective_route_patterns(),
		}
	)


@routes.put("/mss-login/api/settings/model-isolation-download-patterns")
async def api_put_model_isolation_download_patterns(request):
	"""Update owner-defined model download redirect patterns (owner only)."""
	if not is_owner(request):
		return web.json_response({"error": "Owner only"}, status=403)
	try:
		data = await request.json()
		patterns = data.get("patterns")
		if not isinstance(patterns, list):
			return web.json_response({"error": "patterns must be an array of strings"}, status=400)
		normalized = [str(x).strip().lower() for x in patterns if str(x).strip()]
		if len(normalized) > 200:
			return web.json_response({"error": "Too many patterns (max 200)"}, status=400)
		updated = save_configured_route_patterns(normalized)
		return web.json_response(
			{
				"status": "ok",
				"configured_patterns": updated,
				"effective_patterns": get_effective_route_patterns(),
			}
		)
	except Exception as e:
		return web.json_response({"error": str(e)}, status=500)


routes.get("/api/mss-login/api/settings/model-isolation-download-patterns")(
	api_get_model_isolation_download_patterns
)
routes.put("/api/mss-login/api/settings/model-isolation-download-patterns")(
	api_put_model_isolation_download_patterns
)


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
			"base_url": cfg.get("base_url", ""),
			"enabled_events": cfg.get("enabled_events", []),
			"has_api_token": bool(cfg.get("has_api_token", False)),
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
		base_url = (data.get("base_url") or "").strip()
		api_token = data.get("api_token")
		enabled = data.get("enabled_events")
		if not isinstance(enabled, list):
			enabled = []
		if api_token is not None and not isinstance(api_token, str):
			return web.json_response({"error": "api_token must be a string"}, status=400)
		save_ntfy_config(topic, enabled, base_url=base_url, api_token=api_token)
		return web.json_response({"status": "ok"})
	except Exception as e:
		return web.json_response({"error": str(e)}, status=500)


routes.get("/api/mss-login/api/settings/ntfy")(api_get_ntfy_settings)
routes.put("/api/mss-login/api/settings/ntfy")(api_put_ntfy_settings)


def _resolve_ntfy_quarantine_source_path(raw_path: str) -> str | None:
	"""Resolve and validate file path for ntfy quarantine actions."""
	if not raw_path or not isinstance(raw_path, str):
		return None
	try:
		import os
		import folder_paths  # type: ignore[import-untyped]
		from ..utils.data_dir import get_data_subdir

		candidate = os.path.realpath(raw_path)
		allowed_roots = [
			os.path.realpath(folder_paths.get_output_directory()),
			os.path.realpath(folder_paths.get_temp_directory()),
			os.path.realpath(get_data_subdir("output")),
			os.path.realpath(get_data_subdir("temp")),
		]
		for root in allowed_roots:
			if path_under(candidate, root):
				return candidate
	except Exception:
		return None
	return None


@routes.get("/mss-login/api/ntfy/quarantine")
async def api_ntfy_quarantine_action(request):
	"""Handle signed ntfy action to quarantine an offending image (owner only)."""
	if not is_owner(request):
		return web.json_response({"error": "Owner only"}, status=403)
	token = (request.query.get("token") or "").strip()
	payload = verify_signed_action_token(token)
	if not payload or payload.get("action") != "quarantine_nsfw_image":
		return web.json_response({"error": "Invalid or expired token"}, status=400)
	source_path = _resolve_ntfy_quarantine_source_path(str(payload.get("path") or ""))
	if not source_path:
		return web.json_response({"error": "Invalid source path"}, status=400)
	quarantine_cfg = get_quarantine_settings()
	retention_days = int(quarantine_cfg.get("retention_days", 30) or 30)
	result = quarantine_image_file(
		source_path=source_path,
		username=str(payload.get("username") or "unknown"),
		workflow_name=str(payload.get("workflow_name") or "unknown"),
		generated_at=str(payload.get("generated_at") or ""),
		score=payload.get("score"),
		severity=payload.get("severity"),
		retention_days=retention_days,
	)
	status = 200 if result.get("status") in ("ok", "already_quarantined") else 404
	return web.json_response(result, status=status)


@routes.get("/mss-login/api/quarantine")
async def api_quarantine_list(request):
	"""List quarantine records (owner/admin only)."""
	if not is_admin(request):
		return web.json_response({"error": "Admin only"}, status=403)
	items = list_quarantine_items()
	return web.json_response({"items": items, "count": len(items)})


@routes.post("/mss-login/api/quarantine/{record_id}/review")
async def api_quarantine_mark_reviewed(request):
	"""Mark a quarantined record as reviewed (owner/admin only)."""
	if not is_admin(request):
		return web.json_response({"error": "Admin only"}, status=403)
	record_id = (request.match_info.get("record_id") or "").strip()
	if not record_id:
		return web.json_response({"error": "record_id required"}, status=400)
	updated = mark_quarantine_item_reviewed(record_id)
	if not updated:
		return web.json_response({"error": "record not found"}, status=404)
	return web.json_response({"status": "ok", "record": updated})


routes.get("/api/mss-login/api/ntfy/quarantine")(api_ntfy_quarantine_action)
routes.get("/api/mss-login/api/quarantine")(api_quarantine_list)
routes.post("/api/mss-login/api/quarantine/{record_id}/review")(api_quarantine_mark_reviewed)


@routes.get("/mss-login/api/settings/experimental")
async def api_get_experimental(request):
	"""Return experimental_features (master) and experimental (per-feature flags). Authenticated; any user can read."""
	token = jwt_auth.get_token_from_request(request)
	if not token or not jwt_auth.is_token_valid(token):
		return web.json_response({"error": "Authentication required"}, status=401)
	try:
		cfg = load_json_file(CONFIG_FILE_PATH, {})
		master = bool(cfg.get("experimental_features", False))
		block = cfg.get("experimental")
		if not isinstance(block, dict):
			block = {}
		experimental = {
			"mfa": bool(block.get("mfa", False)),
			"s3": bool(block.get("s3", False)),
			"loading_screen": bool(block.get("loading_screen", False)),
			"news": bool(block.get("news", False)),
		}
		return web.json_response({"experimental_features": master, "experimental": experimental})
	except Exception as e:
		return web.json_response({"error": str(e)}, status=500)


@routes.put("/mss-login/api/settings/experimental")
async def api_put_experimental(request):
	"""Update experimental per-feature flags (Admin only). Body: { experimental: { mfa?, s3?, loading_screen?, news? } }."""
	if not is_admin(request):
		return web.json_response({"error": "Admin only"}, status=403)
	try:
		data = await request.json()
		if not isinstance(data, dict):
			return web.json_response({"error": "Invalid body"}, status=400)
		cfg = load_json_file(CONFIG_FILE_PATH, {})
		if not isinstance(cfg, dict):
			cfg = {}
		block = cfg.get("experimental")
		if not isinstance(block, dict):
			block = {}
		incoming = data.get("experimental")
		if isinstance(incoming, dict):
			for key in ("mfa", "s3", "loading_screen", "news"):
				if key in incoming:
					block[key] = bool(incoming[key])
		cfg["experimental"] = block
		save_json_file(CONFIG_FILE_PATH, cfg)
		reload_experimental_features()
		return web.json_response({"status": "ok", "experimental": get_experimental_flags()})
	except Exception as e:
		return web.json_response({"error": str(e)}, status=500)


routes.get("/api/mss-login/api/settings/experimental")(api_get_experimental)
routes.put("/api/mss-login/api/settings/experimental")(api_put_experimental)


@routes.get("/mss-login/api/settings/experimental-failsafe")
async def api_get_experimental_failsafe(request):
	"""Return non-experimental failsafe settings. Authenticated users can read."""
	token = jwt_auth.get_token_from_request(request)
	if not token or not jwt_auth.is_token_valid(token):
		return web.json_response({"error": "Authentication required"}, status=401)
	try:
		return web.json_response(get_experimental_failsafe_settings())
	except Exception as e:
		return web.json_response({"error": str(e)}, status=500)


@routes.put("/mss-login/api/settings/experimental-failsafe")
async def api_put_experimental_failsafe(request):
	"""Update non-experimental failsafe settings (Admin only)."""
	if not is_admin(request):
		return web.json_response({"error": "Admin only"}, status=403)
	try:
		data = await request.json()
		if not isinstance(data, dict):
			return web.json_response({"error": "Invalid body"}, status=400)
		enabled = data.get("enabled")
		escalate = data.get("escalate_after_repeated_failure")
		updated = save_experimental_failsafe_settings(
			enabled=(None if enabled is None else bool(enabled)),
			escalate_after_repeated_failure=(None if escalate is None else bool(escalate)),
		)
		reload_experimental_failsafe()
		return web.json_response({"status": "ok", **updated})
	except Exception as e:
		return web.json_response({"error": str(e)}, status=500)


routes.get("/api/mss-login/api/settings/experimental-failsafe")(api_get_experimental_failsafe)
routes.put("/api/mss-login/api/settings/experimental-failsafe")(api_put_experimental_failsafe)


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


routes.get("/api/mss-login/api/admin/consoles")(api_admin_consoles_list)
routes.get("/api/mss-login/api/admin/consoles/{username}")(api_admin_consoles_user)


@routes.get("/mss-login/api/groups")
async def api_groups(request: web.Request) -> web.Response:
	"""Return groups (admin only)."""
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


routes.get("/api/mss-login/api/groups")(api_groups)


@routes.put("/mss-login/api/groups")
async def api_update_groups(request: web.Request) -> web.Response:
	"""Update groups (admin only)."""
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
		_apply_owner_max_merge(current)
		save_json_file(GROUPS_CONFIG_FILE, current)
		return web.json_response({"status": "ok"})
	except Exception as e:
		return web.json_response({"error": str(e)}, status=500)


routes.put("/api/mss-login/api/groups")(api_update_groups)


@routes.get("/mss-login/api/users")
async def api_users(request: web.Request) -> web.Response:
	"""Return users (admin only)."""
	if not is_admin(request):
		return web.json_response({"error": "Admin only"}, status=403)
	users_list = users_db.list_users_for_admin()
	return web.json_response({"users": users_list})


routes.get("/api/mss-login/api/users")(api_users)


@routes.put("/mss-login/api/users/{target_user}")
async def api_update_user_route(request: web.Request) -> web.Response:
	"""Update user (admin only)."""
	if not is_admin(request):
		return web.json_response({"error": "Admin only"}, status=403)

	target = request.match_info["target_user"]
	data = await request.json()
	caller_username, caller_groups = _get_caller_username_and_groups(request)
	owner_username = users_db.get_owner_username()
	target_uid, target_user = users_db.get_user(username=target)
	if not target_uid or not target_user:
		return web.Response(status=404)
	target_current_groups = [g.lower() for g in target_user.get("groups", [])]
	target_is_owner = "owner" in target_current_groups

	groups = [g.lower() for g in data.get("groups", [])]
	wants_owner = "owner" in groups
	is_admin_flag = "admin" in groups
	sfw_check = data.get("sfw_check", None)

	# Owner cannot be demoted via this API
	if target_is_owner and not wants_owner:
		return web.json_response(
			{"error": "Owner's role cannot be changed. Only transfer of ownership is allowed."},
			status=403,
		)

	# Assigning owner: only current owner can assign (transfer); max one owner
	if wants_owner and not target_is_owner:
		if owner_username is None:
			# No owner yet: _ensure_owner_assigned will run on next load; allow this assign
			# only if caller is admin (e.g. first setup). Prefer: only owner can assign.
			# Per plan: only owner can assign; if no owner, rely on _ensure_owner_assigned.
			return web.json_response(
				{
					"error": "Only the current owner can assign the owner role. If there is no owner, restart the server so the first admin is promoted to owner."
				},
				status=403,
			)
		if caller_username != owner_username:
			return web.json_response(
				{"error": "Only the current owner can assign the owner role (transfer)."},
				status=403,
			)
		# Transfer: target becomes owner, caller (current owner) becomes admin
		success = patch_user_group(
			target, ["owner", "admin"] if is_admin_flag else ["owner"], True, sfw_check
		)
		if success and caller_username:
			patch_user_group(caller_username, ["admin"], True, None)
		return web.json_response({"status": "ok"}) if success else web.Response(status=404)

	success = patch_user_group(target, groups, is_admin_flag, sfw_check)
	if success:
		return web.json_response({"status": "ok"})
	return web.Response(status=404)


routes.put("/api/mss-login/api/users/{target_user}")(api_update_user_route)


@routes.delete("/mss-login/api/users/{target_user}")
async def api_delete_user_route(request: web.Request) -> web.Response:
	"""Delete user (admin only)."""
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


routes.delete("/api/mss-login/api/users/{target_user}")(api_delete_user_route)


@routes.get("/mss-login/api/users-db-config")
async def api_get_users_db_config(request):
	"""Return current users DB backend, paths, and encryption_level (admin only). Password never returned."""
	if not is_admin(request):
		return web.json_response({"error": "Admin only"}, status=403)
	out = {
		"backend": USERS_DB_CONFIG.get("backend", "sqlite"),
		"sqlite_path": USERS_DB_CONFIG.get("sqlite_path", "data/mss_login_data.db"),
		"postgres_host": USERS_DB_CONFIG.get("postgres_host", "localhost"),
		"postgres_port": USERS_DB_CONFIG.get("postgres_port", 5432),
		"postgres_database": USERS_DB_CONFIG.get("postgres_database", "mss_login"),
		"postgres_user": USERS_DB_CONFIG.get("postgres_user", "mss_login"),
		"mysql_host": USERS_DB_CONFIG.get("mysql_host", "localhost"),
		"mysql_port": USERS_DB_CONFIG.get("mysql_port", 3306),
		"mysql_database": USERS_DB_CONFIG.get("mysql_database", "mss_login"),
		"mysql_user": USERS_DB_CONFIG.get("mysql_user", "mss_login"),
		"encryption_level": USERS_DB_CONFIG.get("encryption_level", ""),
	}
	return web.json_response(out)


routes.get("/api/mss-login/api/users-db-config")(api_get_users_db_config)


@routes.put("/mss-login/api/users-db-config")
async def api_put_users_db_config(request):
	"""Update users DB config (admin only). Restart required for new backend to take effect. Password from env only."""
	if not is_admin(request):
		return web.json_response({"error": "Admin only"}, status=403)
	try:
		data = await request.json()
		backend = (data.get("backend") or "sqlite").lower()
		if backend not in ("sqlite", "postgresql", "mysql"):
			return web.json_response(
				{"error": "Invalid backend; use sqlite, postgresql, or mysql"}, status=400
			)
		cfg = load_json_file(CONFIG_FILE_PATH, {})
		if not isinstance(cfg, dict):
			cfg = {}
		udb = cfg.get("users_db") or {}
		if isinstance(udb, str):
			udb = {}
		udb["backend"] = backend
		udb["sqlite_path"] = data.get(
			"sqlite_path", udb.get("sqlite_path", "data/mss_login_data.db")
		)
		udb["postgres_host"] = data.get("postgres_host", udb.get("postgres_host", "localhost"))
		udb["postgres_port"] = data.get("postgres_port", udb.get("postgres_port", 5432))
		udb["postgres_database"] = data.get(
			"postgres_database", udb.get("postgres_database", "mss_login")
		)
		udb["postgres_user"] = data.get("postgres_user", udb.get("postgres_user", "mss_login"))
		udb["mysql_host"] = data.get("mysql_host", udb.get("mysql_host", "localhost"))
		udb["mysql_port"] = data.get("mysql_port", udb.get("mysql_port", 3306))
		udb["mysql_database"] = data.get("mysql_database", udb.get("mysql_database", "mss_login"))
		udb["mysql_user"] = data.get("mysql_user", udb.get("mysql_user", "mss_login"))
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
		logger.error(f"[admin.py] api_put_users_db_config: {str(e)}")
		return web.json_response({"error": "Internal server error"}, status=500)


routes.put("/api/mss-login/api/users-db-config")(api_put_users_db_config)


@routes.get("/mss-login/api/token-storage-config")
async def api_get_token_storage_config(request):
	"""Return token storage config (admin only). Token store always uses same DB as users (one database). Passwords are env-only."""
	if not is_admin(request):
		return web.json_response({"error": "Admin only"}, status=403)
	cfg = load_json_file(CONFIG_FILE_PATH, {})
	store_cfg = cfg.get("api_token_store") or {}
	out = {
		"backend": USERS_DB_CONFIG.get("backend", "sqlite"),
		"json_path": store_cfg.get("json_path", "data/api_tokens.json"),
		"use_same_db_as_users": True,
	}
	out["sqlite_path"] = USERS_DB_CONFIG.get("sqlite_path", "data/mss_login_data.db")
	out["postgres_host"] = USERS_DB_CONFIG.get("postgres_host", "localhost")
	out["postgres_port"] = USERS_DB_CONFIG.get("postgres_port", 5432)
	out["postgres_database"] = USERS_DB_CONFIG.get("postgres_database", "mss_login")
	out["postgres_user"] = USERS_DB_CONFIG.get("postgres_user", "mss_login")
	out["mysql_host"] = USERS_DB_CONFIG.get("mysql_host", "localhost")
	out["mysql_port"] = USERS_DB_CONFIG.get("mysql_port", 3306)
	out["mysql_database"] = USERS_DB_CONFIG.get("mysql_database", "mss_login")
	out["mysql_user"] = USERS_DB_CONFIG.get("mysql_user", "mss_login")
	return web.json_response(out)


routes.get("/api/mss-login/api/token-storage-config")(api_get_token_storage_config)


@routes.put("/mss-login/api/token-storage-config")
async def api_put_token_storage_config(request):
	"""Update token storage config (admin only). Token store always uses same DB as users (one database). Only users_db backend/path are configurable."""
	if not is_admin(request):
		return web.json_response({"error": "Admin only"}, status=403)
	try:
		data = await request.json()
		backend = (data.get("backend") or "sqlite").strip().lower()
		if backend not in ("sqlite", "postgresql", "mysql"):
			return web.json_response(
				{
					"error": "Invalid backend; use sqlite, postgresql, or mysql (one database for users and API tokens)."
				},
				status=400,
			)
		cfg = load_json_file(CONFIG_FILE_PATH, {})
		if not isinstance(cfg, dict):
			cfg = {}
		udb = cfg.get("users_db") or {}
		if isinstance(udb, str):
			udb = {}
		udb["backend"] = backend
		udb["sqlite_path"] = data.get(
			"sqlite_path", udb.get("sqlite_path", "data/mss_login_data.db")
		)
		udb["postgres_host"] = data.get("postgres_host", udb.get("postgres_host", "localhost"))
		udb["postgres_port"] = data.get("postgres_port", udb.get("postgres_port", 5432))
		udb["postgres_database"] = data.get(
			"postgres_database", udb.get("postgres_database", "mss_login")
		)
		udb["postgres_user"] = data.get("postgres_user", udb.get("postgres_user", "mss_login"))
		udb["mysql_host"] = data.get("mysql_host", udb.get("mysql_host", "localhost"))
		udb["mysql_port"] = data.get("mysql_port", udb.get("mysql_port", 3306))
		udb["mysql_database"] = data.get("mysql_database", udb.get("mysql_database", "mss_login"))
		udb["mysql_user"] = data.get("mysql_user", udb.get("mysql_user", "mss_login"))
		cfg["users_db"] = udb
		api_cfg = cfg.get("api_token_store") or {}
		api_cfg["backend"] = backend
		cfg["api_token_store"] = api_cfg
		save_json_file(CONFIG_FILE_PATH, cfg)
		reload_users_db_config()
		reload_api_token_store_config()
		reset_api_token_store()
		return web.json_response({"status": "ok"})
	except Exception as e:
		return web.json_response({"error": str(e)}, status=500)


routes.put("/api/mss-login/api/token-storage-config")(api_put_token_storage_config)


@routes.get("/mss-login/api/update-status")
async def api_get_update_status(request):
	"""Return update status (current version, latest, update_available, mode). Admin only."""
	if not is_admin(request):
		return web.json_response({"error": "Admin only"}, status=403)
	try:
		status = get_cached_status()
		return web.json_response(status)
	except Exception as e:
		return web.json_response({"error": str(e)}, status=500)


routes.get("/api/mss-login/api/update-status")(api_get_update_status)


@routes.get("/mss-login/api/ip-lists")
async def api_ip_lists(request: web.Request) -> web.Response:
	"""Return IP lists (admin only)."""
	if not is_admin(request):
		return web.json_response({"error": "Admin only"}, status=403)
	store = ip_filter.lockout_store
	whitelist = store.get_whitelist()
	blacklist_with_expiry = store.get_blacklist_with_expiry()
	blacklist = []
	for ip, expires_at in blacklist_with_expiry:
		if expires_at is None:
			blacklist.append({"ip": ip, "permanent": True})
		else:
			blacklist.append({"ip": ip, "permanent": False, "expires_at": expires_at})
	return web.json_response({"whitelist": whitelist, "blacklist": blacklist})


@routes.put("/mss-login/api/ip-lists")
async def api_update_ip_lists(request):
	"""Update IP lists (admin only)."""
	if not is_admin(request):
		return web.json_response({"error": "Admin only"}, status=403)
	try:
		import ipaddress
		import time

		data = await request.json()
		whitelist_raw = data.get("whitelist", [])
		blacklist_raw = data.get("blacklist", [])

		whitelist = []
		for entry in whitelist_raw:
			entry = (entry if isinstance(entry, str) else entry.get("entry", "")).strip()
			if not entry:
				continue
			try:
				try:
					ipaddress.ip_address(entry)
				except ValueError:
					ipaddress.ip_network(entry, strict=False)
				whitelist.append(entry)
			except ValueError:
				continue

		blacklist_entries = []
		for item in blacklist_raw:
			if isinstance(item, str):
				ip = item.strip()
				permanent = True
				expires_in_hours = None
			else:
				ip = (item.get("ip") or "").strip()
				permanent = item.get("permanent", True)
				expires_in_hours = item.get("expires_in_hours")
			if not ip:
				continue
			try:
				ipaddress.ip_address(ip)
			except ValueError:
				continue
			if permanent or expires_in_hours is None:
				blacklist_entries.append((ip, None))
			else:
				try:
					h = float(expires_in_hours)
					expires_at = int(time.time()) + int(h * 3600)
					blacklist_entries.append((ip, expires_at))
				except (TypeError, ValueError):
					blacklist_entries.append((ip, None))

		store = ip_filter.lockout_store
		store.set_whitelist(whitelist)
		store.set_blacklist(blacklist_entries)
		ip_filter.load_filter_list()

		return web.json_response({"status": "ok"})
	except Exception as e:
		return web.json_response({"error": str(e)}, status=500)


routes.get("/api/mss-login/api/ip-lists")(api_ip_lists)
routes.put("/api/mss-login/api/ip-lists")(api_update_ip_lists)


@routes.get("/mss-login/api/available-model-folders")
async def api_available_model_folders(request):
	"""List ComfyUI model folder names (for admin shared-items UI). Admin only."""
	if not is_admin(request):
		return web.json_response({"error": "Admin only"}, status=403)
	try:
		import folder_paths  # type: ignore[import-untyped]  # ComfyUI core module; resolves at runtime

		folders = list(folder_paths.folder_names_and_paths.keys())
	except ImportError as e:
		logger.error(f"[MSS-Login] Error importing folder_paths: {e}")
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


routes.get("/api/mss-login/api/available-model-folders")(api_available_model_folders)


def _safe_folder_segment(folder: str) -> bool:
	"""Return True if folder is a single path segment (no path traversal)."""
	return is_safe_folder_segment(folder)


@routes.get("/mss-login/api/available-models/{folder}")
async def api_available_models_in_folder(request):
	"""List model/item names in a folder (for admin shared-items UI). Admin only."""
	if not is_admin(request):
		return web.json_response({"error": "Admin only"}, status=403)
	folder = request.match_info.get("folder", "")
	if not _safe_folder_segment(folder):
		return web.json_response({"error": "Invalid folder"}, status=400)
	try:
		import folder_paths  # type: ignore[import-untyped]  # ComfyUI core module; resolves at runtime

		names = folder_paths.get_filename_list(folder)
	except ImportError as e:
		logger.error(f"[MSS-Login] api_available_models_in_folder: {e}")
		names = []
		return web.json_response({"error": str(e)}, status=500)
	return web.json_response({"folder": folder, "items": names})


routes.get("/api/mss-login/api/available-models/{folder}")(api_available_models_in_folder)


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


routes.get("/api/mss-login/api/model-cache/folders")(api_model_cache_folders)


@routes.get("/mss-login/api/model-cache/folders/{folder}/items")
async def api_model_cache_folder_items(request):
	"""List model/item names in a folder from cache (admin only)."""
	if not is_admin(request):
		return web.json_response({"error": "Admin only"}, status=403)
	folder = request.match_info.get("folder", "")
	if not _safe_folder_segment(folder):
		return web.json_response({"error": "Invalid folder"}, status=400)
	try:
		cache = get_model_cache(USERS_DB_CONFIG)
		items = cache.list_items(folder)
		return web.json_response({"folder": folder, "items": items})
	except Exception as e:
		return web.json_response({"error": str(e)}, status=500)


routes.get("/api/mss-login/api/model-cache/folders/{folder}/items")(api_model_cache_folder_items)


@routes.post("/mss-login/api/model-cache/refresh")
async def api_model_cache_refresh(request):
	"""Refresh model cache from ComfyUI folder_paths (admin only)."""
	if not is_admin(request):
		return web.json_response({"error": "Admin only"}, status=403)
	try:
		cache = get_model_cache(USERS_DB_CONFIG)
		folders, total = cache.refresh_from_folder_paths()
		return web.json_response(
			{
				"status": "ok",
				"folders_count": len(folders),
				"items_count": total,
				"folders": folders,
			}
		)
	except Exception as e:
		return web.json_response({"error": str(e)}, status=500)


routes.post("/api/mss-login/api/model-cache/refresh")(api_model_cache_refresh)


@routes.get("/mss-login/api/users/{username}/shared-items")
async def api_get_shared_items(request):
	"""List shared ComfyUI items for a user (owner/admin with sharing permission)."""
	if not _can_manage_model_sharing(request):
		return web.json_response(
			{"error": "Owner/Admin with sharing permission required"}, status=403
		)
	username = request.match_info.get("username", "")
	user_id, _ = users_db.get_user(username=username)
	if not user_id:
		return web.json_response({"error": "User not found"}, status=404)
	store = get_shared_items_store(USERS_DB_CONFIG)
	items = store.list_for_user(user_id)
	return web.json_response({"username": username, "items": items})


routes.get("/api/mss-login/api/users/{username}/shared-items")(api_get_shared_items)


@routes.post("/mss-login/api/users/{username}/shared-items")
async def api_add_shared_item(request):
	"""Add one shared item for a user."""
	if not _can_manage_model_sharing(request):
		return web.json_response(
			{"error": "Owner/Admin with sharing permission required"}, status=403
		)
	username = request.match_info.get("username", "")
	user_id, _ = users_db.get_user(username=username)
	if not user_id:
		return web.json_response({"error": "User not found"}, status=404)
	role, _perms, caller_username = _get_caller_role_and_permissions(request)
	caller_user_id, _ = (
		users_db.get_user(username=caller_username) if caller_username else (None, {})
	)
	try:
		data = await request.json()
		folder = (data.get("folder") or "").strip()
		item_name = (data.get("item_name") or "").strip()
		source_backend = (data.get("source_backend") or "unknown").strip().lower()
		if source_backend not in ("local", "s3", "unknown"):
			source_backend = "unknown"
		if not folder or not item_name:
			return web.json_response({"error": "folder and item_name required"}, status=400)
		if not is_safe_folder_segment(folder) or not is_safe_filename(item_name):
			return web.json_response(
				{"error": "Invalid folder or item_name (path traversal not allowed)"}, status=400
			)
		store = get_shared_items_store(USERS_DB_CONFIG)
		if store.add(
			user_id,
			folder,
			item_name,
			source_backend=source_backend,
			granted_by_user_id=caller_user_id or "",
			granted_by_role=role or "",
		):
			send_notification(
				"shared_items_added",
				"MSS-Login: Shared item added",
				f"Shared item: {folder}/{item_name} added to user: {username}",
				priority="default",
			)
			return web.json_response(
				{
					"status": "ok",
					"folder": folder,
					"item_name": item_name,
					"source_backend": source_backend,
				}
			)
		return web.json_response({"error": "Failed to add (may already exist)"}, status=400)
	except Exception as e:
		return web.json_response({"error": str(e)}, status=500)


routes.post("/api/mss-login/api/users/{username}/shared-items")(api_add_shared_item)


@routes.delete("/mss-login/api/users/{username}/shared-items")
async def api_remove_shared_item(request):
	"""Remove one shared item for a user."""
	if not _can_manage_model_sharing(request):
		return web.json_response(
			{"error": "Owner/Admin with sharing permission required"}, status=403
		)
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
		if not is_safe_folder_segment(folder) or not is_safe_filename(item_name):
			return web.json_response(
				{"error": "Invalid folder or item_name (path traversal not allowed)"}, status=400
			)
		store = get_shared_items_store(USERS_DB_CONFIG)
		if store.remove(user_id, folder, item_name):
			send_notification(
				"shared_items_removed",
				"MSS-Login: Shared item removed",
				f"Shared item: {folder}/{item_name} removed from user: {username}",
				priority="default",
			)
			return web.json_response({"status": "ok"})
		return web.json_response({"error": "Item not found"}, status=404)
	except Exception as e:
		return web.json_response({"error": str(e)}, status=500)


routes.delete("/api/mss-login/api/users/{username}/shared-items")(api_remove_shared_item)


@routes.post("/mss-login/api/nsfw-management")
async def api_nsfw_management(request):
	"""Admin-only NSFW management endpoints."""
	if not is_admin(request):
		return web.json_response({"error": "Admin only"}, status=403)

	try:
		data = await request.json()
		action = data.get("action", "").strip()

		print(f"[mss_login] NSFW management action: {action}")

		# Run blocking operations in executor to avoid blocking the event loop
		import asyncio

		from ..utils.sfw_intercept.nsfw_guard import (
			clear_all_nsfw_tags,
			fix_incorrectly_cached_tags,
			scan_all_images_in_output_directory,
		)

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
			print("[mss_login] Starting fix_incorrect in executor...")
			fixed_count = await loop.run_in_executor(None, fix_incorrectly_cached_tags)
			print("[mss_login] fix_incorrect completed: {fixed_count} fixed")
			return web.json_response(
				{
					"status": "ok",
					"message": f"Fixed {fixed_count} incorrectly cached images.",
					"fixed_count": fixed_count,
				}
			)

		elif action == "clear_all_tags":
			print("[mss_login] Starting clear_all_tags in executor...")
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


routes.post("/api/mss-login/api/nsfw-management")(api_nsfw_management)
