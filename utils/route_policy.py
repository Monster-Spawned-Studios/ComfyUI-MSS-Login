"""
Default-deny route policy for /mss-login* and /api/mss-login* (AUDIT-002).

Policies are keyed by HTTP method + canonical path template (not prefix).
Unmapped MSS routes fail closed: 401 (anonymous) or 403 (authenticated).

MFA / Comfy Portal note: this middleware never re-checks MFA. Valid session JWTs
and long-lived API tokens (Portal Bearer / ?token=) remain valid after MFA is
enrolled. MFA only gates interactive credential issuance (login, generate_token,
identity-header, local-login).
"""

from __future__ import annotations

import re
from enum import Enum
from typing import Callable
from urllib.parse import unquote

from aiohttp import web


class Policy(str, Enum):
	PUBLIC = "PUBLIC"
	OPTIONAL_AUTH = "OPTIONAL_AUTH"
	AUTH = "AUTH"
	PERM = "PERM"  # value stored as PERM:<flag>
	ADMIN = "ADMIN"
	OWNER = "OWNER"


def _p(method: str, path: str, policy: str) -> tuple[str, str, str]:
	return (method.upper(), path, policy)


# Exact method+path policies. Path templates use {param} for dynamic segments.
# Dual /api/mss-login aliases are registered alongside /mss-login where applicable.
_ROUTE_POLICIES: list[tuple[str, str, str]] = [
	# --- Public static / login assets ---
	_p("GET", "/mss-login/css/{tail:.*}", Policy.PUBLIC),
	_p("GET", "/mss-login/js/{tail:.*}", Policy.PUBLIC),
	_p("GET", "/mss-login/assets/{tail:.*}", Policy.PUBLIC),
	_p("GET", "/mss-login/dist/{tail:.*}", Policy.PUBLIC),
	_p("GET", "/mss-login/vendor/{tail:.*}", Policy.PUBLIC),
	_p("GET", "/mss-login/api/bg-music", Policy.PUBLIC),
	_p("GET", "/mss-login/loading-tips.json", Policy.PUBLIC),
	_p("GET", "/mss-login/api/login-background", Policy.PUBLIC),
	_p("GET", "/mss-login/api/login-background/media", Policy.PUBLIC),
	_p("GET", "/api/mss-login/api/login-background", Policy.PUBLIC),
	_p("GET", "/api/mss-login/api/login-background/media", Policy.PUBLIC),
	_p("GET", "/mss-login/api/update-notice", Policy.PUBLIC),
	_p("GET", "/api/mss-login/api/update-notice", Policy.PUBLIC),
	_p("GET", "/mss-login/api/is-https", Policy.PUBLIC),
	_p("GET", "/mss-login/api/debug-mode", Policy.OPTIONAL_AUTH),
	_p("GET", "/api/mss-login/api/debug-mode", Policy.OPTIONAL_AUTH),
	_p("GET", "/mss-login/api/news/feed.xml", Policy.PUBLIC),
	_p("GET", "/api/mss-login/api/news/feed.xml", Policy.PUBLIC),
	# Credential / token issuance (public; lockout + MFA at handler)
	_p("POST", "/mss-login/api/login", Policy.PUBLIC),
	_p("POST", "/api/mss-login/api/login", Policy.PUBLIC),
	_p("POST", "/mss-login/api/anti-bot/telemetry", Policy.PUBLIC),
	_p("POST", "/api/mss-login/api/anti-bot/telemetry", Policy.PUBLIC),
	_p("GET", "/mss-login/generate_token", Policy.PUBLIC),
	_p("POST", "/mss-login/generate_token", Policy.PUBLIC),
	_p("POST", "/generate_token", Policy.PUBLIC),
	_p("POST", "/mss-login/api/mfa/verify", Policy.PUBLIC),
	# MFA setup may use mfa_temp_token (no session JWT yet) — handler enforces JWT or temp.
	# Does not affect already-issued Portal Bearer tokens.
	_p("POST", "/mss-login/api/mfa/setup", Policy.PUBLIC),
	_p("POST", "/mss-login/api/mfa/verify-setup", Policy.PUBLIC),
	_p("POST", "/mss-login/api/mfa/recovery-codes/regenerate", Policy.AUTH),
	_p("POST", "/mss-login/api/mfa/reset-user", Policy.OWNER),
	# WebAuthn
	_p("GET", "/mss-login/passkeys", Policy.AUTH),
	_p("GET", "/passkeys", Policy.AUTH),
	_p("GET", "/mss-login/api/webauthn/status", Policy.PUBLIC),
	_p("POST", "/mss-login/webauthn/register/options", Policy.AUTH),
	_p("POST", "/mss-login/webauthn/register/verify", Policy.AUTH),
	_p("POST", "/mss-login/webauthn/auth/options", Policy.PUBLIC),
	_p("POST", "/mss-login/webauthn/auth/verify", Policy.PUBLIC),
	_p("POST", "/mss-login/webauthn/passwordless/options", Policy.PUBLIC),
	_p("POST", "/mss-login/webauthn/passwordless/verify", Policy.PUBLIC),
	_p("GET", "/mss-login/webauthn/credentials", Policy.AUTH),
	_p("DELETE", "/mss-login/webauthn/credentials", Policy.AUTH),
	_p("GET", "/mss-login/api/auth/local-login-status", Policy.PUBLIC),
	_p("GET", "/api/mss-login/api/auth/local-login-status", Policy.PUBLIC),
	_p("POST", "/mss-login/api/auth/local-login", Policy.PUBLIC),
	_p("POST", "/api/mss-login/api/auth/local-login", Policy.PUBLIC),
	# Optional auth
	_p("GET", "/mss-login/api/me", Policy.OPTIONAL_AUTH),
	_p("GET", "/api/mss-login/api/me", Policy.OPTIONAL_AUTH),
	# Session / me
	_p("GET", "/mss-login/me/console", Policy.AUTH),
	_p("GET", "/mss-login/me/sessions", Policy.AUTH),
	_p("GET", "/mss-login/me/current-token", Policy.AUTH),
	_p("POST", "/mss-login/me/sessions/revoke", Policy.AUTH),
	_p("GET", "/mss-login/api/me/avatar", Policy.AUTH),
	_p("POST", "/mss-login/api/me/avatar", Policy.AUTH),
	_p("PUT", "/mss-login/api/me/avatar", Policy.AUTH),
	_p("DELETE", "/mss-login/api/me/avatar", Policy.AUTH),
	_p("GET", "/api/mss-login/api/me/avatar", Policy.AUTH),
	_p("POST", "/api/mss-login/api/me/avatar", Policy.AUTH),
	_p("PUT", "/api/mss-login/api/me/avatar", Policy.AUTH),
	_p("DELETE", "/api/mss-login/api/me/avatar", Policy.AUTH),
	_p("GET", "/mss-login/api/tokens", Policy.AUTH),
	_p("DELETE", "/mss-login/api/tokens", Policy.AUTH),
	_p("GET", "/mss-login/api/settings/guest-jwt", Policy.AUTH),
	_p("GET", "/api/mss-login/api/settings/guest-jwt", Policy.AUTH),
	_p("GET", "/mss-login/api/settings/ntfy", Policy.AUTH),
	_p("GET", "/api/mss-login/api/settings/ntfy", Policy.AUTH),
	_p("POST", "/mss-login/api/user-env", Policy.AUTH),
	_p("POST", "/api/mss-login/api/user-env", Policy.AUTH),
	_p("GET", "/mss-login/api/update-status", Policy.AUTH),
	_p("GET", "/api/mss-login/api/update-status", Policy.AUTH),
	# Model download
	_p("GET", "/mss-login/api/model-download/{tail:.*}", "PERM:can_download_models"),
	_p("POST", "/mss-login/api/model-download/{tail:.*}", "PERM:can_download_models"),
	_p("PUT", "/mss-login/api/model-download/{tail:.*}", "PERM:can_download_models"),
	_p("DELETE", "/mss-login/api/model-download/{tail:.*}", "PERM:can_download_models"),
	_p("GET", "/api/mss-login/api/model-download/{tail:.*}", "PERM:can_download_models"),
	_p("POST", "/api/mss-login/api/model-download/{tail:.*}", "PERM:can_download_models"),
	_p("PUT", "/api/mss-login/api/model-download/{tail:.*}", "PERM:can_download_models"),
	_p("DELETE", "/api/mss-login/api/model-download/{tail:.*}", "PERM:can_download_models"),
	_p("POST", "/mss/models/download", "PERM:can_download_models"),
	_p("GET", "/mss/models/download/{job_id}", "PERM:can_download_models"),
	_p("DELETE", "/mss/models/download/{job_id}", "PERM:can_download_models"),
	_p("POST", "/api/mss/models/download", "PERM:can_download_models"),
	_p("GET", "/api/mss/models/download/{job_id}", "PERM:can_download_models"),
	_p("DELETE", "/api/mss/models/download/{job_id}", "PERM:can_download_models"),
	# Gallery / ownership
	_p("POST", "/mss-login-gallery/mark-nsfw", Policy.AUTH),
	_p("POST", "/mss-login/api/update-apply", Policy.AUTH),
	_p("POST", "/api/mss-login/api/update-apply", Policy.AUTH),
	_p("GET", "/mss-login/api/users/{username}/shared-items", Policy.AUTH),
	_p("POST", "/mss-login/api/users/{username}/shared-items", Policy.AUTH),
	_p("DELETE", "/mss-login/api/users/{username}/shared-items", Policy.AUTH),
	_p("GET", "/api/mss-login/api/users/{username}/shared-items", Policy.AUTH),
	_p("POST", "/api/mss-login/api/users/{username}/shared-items", Policy.AUTH),
	_p("DELETE", "/api/mss-login/api/users/{username}/shared-items", Policy.AUTH),
	_p("GET", "/mss-login/api/s3/workflows/status", "PERM:can_access_s3_storage"),
	_p("POST", "/mss-login/api/s3/workflows/sync", "PERM:can_access_s3_storage"),
	_p("POST", "/mss-login/api/s3/workflows/sync-all", "PERM:can_access_s3_storage"),
	_p("GET", "/mss-login/api/s3/status", "PERM:can_access_s3_storage"),
	_p("GET", "/mss-login/api/s3/list", "PERM:can_access_s3_storage"),
	_p("POST", "/mss-login/api/s3/upload", "PERM:can_access_s3_storage"),
	_p("POST", "/mss-login/api/s3/download", "PERM:can_access_s3_storage"),
	_p("DELETE", "/mss-login/api/s3/delete", "PERM:can_access_s3_storage"),
	_p("GET", "/mss-login/api/s3/mount/status", "PERM:can_access_s3_storage"),
	_p("POST", "/mss-login/api/s3/mount/sync", "PERM:can_access_s3_storage"),
	_p("POST", "/mss-login/api/s3/mount/remount", "PERM:can_access_s3_storage"),
	_p("POST", "/mss-login/api/s3/mount/unmount", "PERM:can_access_s3_storage"),
	_p("POST", "/s3_mounter/unmount", Policy.ADMIN),
	# Admin
	_p("GET", "/mss-login/api/groups", Policy.ADMIN),
	_p("PUT", "/mss-login/api/groups", Policy.ADMIN),
	_p("GET", "/api/mss-login/api/groups", Policy.ADMIN),
	_p("PUT", "/api/mss-login/api/groups", Policy.ADMIN),
	_p("GET", "/mss-login/api/users", Policy.ADMIN),
	_p("POST", "/mss-login/api/users", Policy.ADMIN),
	_p("PUT", "/mss-login/api/users/{target_user}", Policy.ADMIN),
	_p("DELETE", "/mss-login/api/users/{target_user}", Policy.ADMIN),
	_p("GET", "/api/mss-login/api/users", Policy.ADMIN),
	_p("POST", "/api/mss-login/api/users", Policy.ADMIN),
	_p("PUT", "/api/mss-login/api/users/{target_user}", Policy.ADMIN),
	_p("DELETE", "/api/mss-login/api/users/{target_user}", Policy.ADMIN),
	_p("GET", "/mss-login/api/ip-lists", Policy.ADMIN),
	_p("PUT", "/mss-login/api/ip-lists", Policy.ADMIN),
	_p("GET", "/api/mss-login/api/ip-lists", Policy.ADMIN),
	_p("PUT", "/api/mss-login/api/ip-lists", Policy.ADMIN),
	_p("POST", "/mss-login/api/admin/install-node-deps", Policy.ADMIN),
	_p("POST", "/api/mss-login/api/admin/install-node-deps", Policy.ADMIN),
	_p("GET", "/mss-login/api/quarantine", Policy.ADMIN),
	_p("POST", "/mss-login/api/quarantine/{record_id}/review", Policy.ADMIN),
	_p("GET", "/api/mss-login/api/quarantine", Policy.ADMIN),
	_p("POST", "/api/mss-login/api/quarantine/{record_id}/review", Policy.ADMIN),
	_p("PUT", "/mss-login/api/settings/guest-jwt", Policy.ADMIN),
	_p("PUT", "/api/mss-login/api/settings/guest-jwt", Policy.ADMIN),
	_p("PUT", "/mss-login/api/settings/ntfy", Policy.ADMIN),
	_p("PUT", "/api/mss-login/api/settings/ntfy", Policy.ADMIN),
	_p("GET", "/mss-login/api/settings/experimental", Policy.ADMIN),
	_p("PUT", "/mss-login/api/settings/experimental", Policy.ADMIN),
	_p("GET", "/api/mss-login/api/settings/experimental", Policy.ADMIN),
	_p("PUT", "/api/mss-login/api/settings/experimental", Policy.ADMIN),
	_p("GET", "/mss-login/api/settings/experimental-failsafe", Policy.ADMIN),
	_p("PUT", "/mss-login/api/settings/experimental-failsafe", Policy.ADMIN),
	_p("GET", "/api/mss-login/api/settings/experimental-failsafe", Policy.ADMIN),
	_p("PUT", "/api/mss-login/api/settings/experimental-failsafe", Policy.ADMIN),
	_p("GET", "/mss-login/api/settings/tailscale-auth", Policy.ADMIN),
	_p("PUT", "/mss-login/api/settings/tailscale-auth", Policy.ADMIN),
	_p("GET", "/api/mss-login/api/settings/tailscale-auth", Policy.ADMIN),
	_p("PUT", "/api/mss-login/api/settings/tailscale-auth", Policy.ADMIN),
	_p("GET", "/mss-login/api/settings/debug-filter", Policy.ADMIN),
	_p("PUT", "/mss-login/api/settings/debug-filter", Policy.ADMIN),
	_p("GET", "/api/mss-login/api/settings/debug-filter", Policy.ADMIN),
	_p("PUT", "/api/mss-login/api/settings/debug-filter", Policy.ADMIN),
	_p("GET", "/mss-login/api/admin/consoles", Policy.ADMIN),
	_p("GET", "/mss-login/api/admin/consoles/{username}", Policy.ADMIN),
	_p("GET", "/api/mss-login/api/admin/consoles", Policy.ADMIN),
	_p("GET", "/api/mss-login/api/admin/consoles/{username}", Policy.ADMIN),
	_p("GET", "/mss-login/api/trash", Policy.ADMIN),
	_p("POST", "/mss-login/api/trash/restore", Policy.ADMIN),
	_p("POST", "/mss-login/api/trash/empty", Policy.ADMIN),
	_p("GET", "/api/mss-login/api/trash", Policy.ADMIN),
	_p("POST", "/api/mss-login/api/trash/restore", Policy.ADMIN),
	_p("POST", "/api/mss-login/api/trash/empty", Policy.ADMIN),
	_p("GET", "/mss-login/api/debug-log/export", Policy.ADMIN),
	_p("GET", "/api/mss-login/api/debug-log/export", Policy.ADMIN),
	_p("GET", "/mss-login/api/model-cache/folders", Policy.ADMIN),
	_p("GET", "/mss-login/api/model-cache/folders/{folder}/items", Policy.ADMIN),
	_p("POST", "/mss-login/api/model-cache/refresh", Policy.ADMIN),
	_p("GET", "/api/mss-login/api/model-cache/folders", Policy.ADMIN),
	_p("GET", "/api/mss-login/api/model-cache/folders/{folder}/items", Policy.ADMIN),
	_p("POST", "/api/mss-login/api/model-cache/refresh", Policy.ADMIN),
	_p("GET", "/mss-login/api/available-model-folders", Policy.ADMIN),
	_p("GET", "/mss-login/api/available-models/{folder}", Policy.ADMIN),
	_p("GET", "/api/mss-login/api/available-model-folders", Policy.ADMIN),
	_p("GET", "/api/mss-login/api/available-models/{folder}", Policy.ADMIN),
	_p("GET", "/mss-login/api/users-db-config", Policy.ADMIN),
	_p("PUT", "/mss-login/api/users-db-config", Policy.ADMIN),
	_p("GET", "/api/mss-login/api/users-db-config", Policy.ADMIN),
	_p("PUT", "/api/mss-login/api/users-db-config", Policy.ADMIN),
	_p("GET", "/mss-login/api/token-storage-config", Policy.ADMIN),
	_p("PUT", "/mss-login/api/token-storage-config", Policy.ADMIN),
	_p("GET", "/api/mss-login/api/token-storage-config", Policy.ADMIN),
	_p("PUT", "/api/mss-login/api/token-storage-config", Policy.ADMIN),
	_p("POST", "/mss-login/api/nsfw-management", Policy.ADMIN),
	_p("POST", "/api/mss-login/api/nsfw-management", Policy.ADMIN),
	_p("GET", "/api/mss-login/models", Policy.AUTH),
	_p("POST", "/api/mss-login/recovery/reset-mfa", Policy.ADMIN),
	# Owner
	_p("GET", "/mss-login/api/settings/local-cidrs", Policy.OWNER),
	_p("PUT", "/mss-login/api/settings/local-cidrs", Policy.OWNER),
	_p("GET", "/api/mss-login/api/settings/local-cidrs", Policy.OWNER),
	_p("PUT", "/api/mss-login/api/settings/local-cidrs", Policy.OWNER),
	_p("GET", "/mss-login/api/s3/config", Policy.OWNER),
	_p("PUT", "/mss-login/api/s3/config", Policy.OWNER),
	_p("GET", "/mss-login/api/settings/login-background", Policy.OWNER),
	_p("PUT", "/mss-login/api/settings/login-background", Policy.OWNER),
	_p("GET", "/api/mss-login/api/settings/login-background", Policy.OWNER),
	_p("PUT", "/api/mss-login/api/settings/login-background", Policy.OWNER),
	_p("GET", "/mss-login/api/settings/model-isolation-download-patterns", Policy.OWNER),
	_p("PUT", "/mss-login/api/settings/model-isolation-download-patterns", Policy.OWNER),
	_p("GET", "/api/mss-login/api/settings/model-isolation-download-patterns", Policy.OWNER),
	_p("PUT", "/api/mss-login/api/settings/model-isolation-download-patterns", Policy.OWNER),
	_p("GET", "/mss-login/api/settings/loading-screen", Policy.OWNER),
	_p("PUT", "/mss-login/api/settings/loading-screen", Policy.OWNER),
	_p("GET", "/api/mss-login/api/settings/loading-screen", Policy.OWNER),
	_p("PUT", "/api/mss-login/api/settings/loading-screen", Policy.OWNER),
	_p("GET", "/mss-login/api/ntfy/quarantine", Policy.OWNER),
	_p("GET", "/api/mss-login/api/ntfy/quarantine", Policy.OWNER),
]

# Compile path matchers: convert {name} and {name:.*} to regex
_COMPILED: list[tuple[str, re.Pattern[str], str]] = []


def _compile_path(template: str) -> re.Pattern[str]:
	parts: list[str] = []
	i = 0
	while i < len(template):
		if template[i] == "{":
			j = template.find("}", i)
			if j < 0:
				parts.append(re.escape(template[i:]))
				break
			name = template[i + 1 : j]
			if name.endswith(":.*"):
				parts.append("(?P<" + name[:-3] + ">.*)")
			else:
				parts.append("(?P<" + name + ">[^/]+)")
			i = j + 1
		else:
			# escape until next {
			k = template.find("{", i)
			if k < 0:
				parts.append(re.escape(template[i:]))
				break
			parts.append(re.escape(template[i:k]))
			i = k
	return re.compile("^" + "".join(parts) + "$")


def _ensure_compiled() -> None:
	if _COMPILED:
		return
	for method, path, policy in _ROUTE_POLICIES:
		_COMPILED.append((method, _compile_path(path), policy))


def normalize_request_path(path: str) -> str | None:
	"""
	Normalize path for policy matching. Return None if the path is rejected
	(//, %2e, %2f, backslash, or dot-segment).
	"""
	if not path:
		return "/"
	raw = path
	# Reject backslashes and encoded traversal before decode
	lower = raw.lower()
	if "\\" in raw or "%2e" in lower or "%2f" in lower or "%5c" in lower:
		return None
	if "//" in raw:
		return None
	try:
		decoded = unquote(raw)
	except Exception:
		return None
	if "\\" in decoded or "//" in decoded:
		return None
	segments = decoded.split("/")
	for seg in segments:
		if seg in (".", ".."):
			return None
	# Collapse empty trailing only; keep leading slash
	norm = "/" + "/".join(s for s in segments if s != "")
	if decoded.endswith("/") and norm != "/":
		norm += "/"
	# For matching we strip trailing slash except root
	if len(norm) > 1 and norm.endswith("/"):
		norm = norm.rstrip("/")
	return norm or "/"


def lookup_policy(method: str, path: str) -> str | None:
	"""Return policy string for method+path, or None if unmapped."""
	_ensure_compiled()
	m = (method or "GET").upper()
	for meth, rx, policy in _COMPILED:
		if meth != m:
			continue
		if rx.match(path):
			return policy
	return None


def is_mss_scoped_path(path: str) -> bool:
	"""True if path is under MSS-Login policy scope."""
	p = path or ""
	return (
		p.startswith("/mss-login")
		or p.startswith("/api/mss-login")
		or p.startswith("/mss-login-gallery")
		or p.startswith("/mss/models/")
		or p.startswith("/api/mss/models/")
		or p == "/generate_token"
		or p == "/s3_mounter/unmount"
		or p == "/passkeys"
	)


def listed_policy_routes() -> list[tuple[str, str, str]]:
	"""Return a copy of registered policies (for CI / startup self-check)."""
	return list(_ROUTE_POLICIES)


def create_route_policy_middleware(
	jwt_auth,
	access_control=None,
	*,
	is_admin_fn: Callable | None = None,
	is_owner_fn: Callable | None = None,
) -> web.middleware:
	"""Middleware: after JWT parsing, enforce default-deny policy on MSS routes."""

	@web.middleware
	async def route_policy_middleware(request: web.Request, handler) -> web.Response:
		raw_path = request.path or ""
		# Path normalize for all MSS-scoped requests
		if is_mss_scoped_path(raw_path) or "%2e" in raw_path.lower() or "%2f" in raw_path.lower():
			norm = normalize_request_path(raw_path)
			if norm is None:
				return web.json_response({"error": "Invalid path"}, status=400)
		else:
			norm = raw_path

		if not is_mss_scoped_path(norm):
			return await handler(request)

		# Prefer already-resolved identity from JWT middleware; do not re-touch idle
		# for optional paths. Never check MFA here — Portal Bearer/API tokens must work.
		user_id = request.get("user_id")
		username = request.get("user")
		if not username:
			user_id, username = jwt_auth.resolve_request_identity(request, touch_idle=False)

		policy = lookup_policy(request.method, norm)
		if policy is None:
			# Default deny
			if username:
				return web.json_response({"error": "Forbidden"}, status=403)
			return web.json_response({"error": "Authentication required"}, status=401)

		if policy == Policy.PUBLIC or policy == Policy.PUBLIC.value:
			return await handler(request)

		if policy == Policy.OPTIONAL_AUTH or policy == Policy.OPTIONAL_AUTH.value:
			return await handler(request)

		if not username:
			return web.json_response({"error": "Authentication required"}, status=401)

		if policy == Policy.AUTH or policy == Policy.AUTH.value:
			return await handler(request)

		if policy.startswith("PERM:"):
			flag = policy.split(":", 1)[1]
			role, perms, _ = (None, {}, None)
			if access_control is not None:
				try:
					role, perms, _ = access_control._get_user_role_and_permissions(request)
				except Exception:
					perms = {}
			# Owner/admin bypass for S3-style flags if already elevated
			if is_owner_fn and is_owner_fn(request):
				return await handler(request)
			if is_admin_fn and is_admin_fn(request) and flag != "can_download_models":
				# Admins still need explicit download perm unless they have it
				pass
			allowed = perms.get(flag)
			if allowed is True or (role in ("admin", "owner") and flag == "can_access_s3_storage"):
				return await handler(request)
			if (
				role in ("admin", "owner")
				and flag == "can_download_models"
				and allowed is not False
			):
				# Prefer explicit flag; if missing, admin/owner historically could download
				if allowed is None:
					return await handler(request)
			if allowed is not True:
				return web.json_response({"error": "Forbidden"}, status=403)
			return await handler(request)

		if policy == Policy.ADMIN or policy == Policy.ADMIN.value:
			if is_admin_fn and is_admin_fn(request):
				return await handler(request)
			return web.json_response({"error": "Admin only"}, status=403)

		if policy == Policy.OWNER or policy == Policy.OWNER.value:
			if is_owner_fn and is_owner_fn(request):
				return await handler(request)
			return web.json_response({"error": "Owner only"}, status=403)

		# Unknown policy string → deny
		return web.json_response({"error": "Forbidden"}, status=403)

	return route_policy_middleware


def assert_router_policies_covered(router) -> list[str]:
	"""
	Walk aiohttp router resources; return list of uncovered MSS route descriptions.
	Empty list means all covered.
	"""
	_ensure_compiled()
	missing: list[str] = []
	try:
		resources = list(router.resources())
	except Exception:
		return missing

	for resource in resources:
		try:
			info = resource.get_info()
		except Exception:
			continue
		# Static routes
		if "prefix" in info:
			prefix = info.get("prefix") or ""
			if is_mss_scoped_path(prefix.rstrip("/") or prefix):
				# Static under /mss-login/css etc. covered by catch-all templates
				path = prefix.rstrip("/")
				if path.startswith("/mss-login/") and any(
					path.startswith(p)
					for p in (
						"/mss-login/css",
						"/mss-login/js",
						"/mss-login/assets",
						"/mss-login/dist",
						"/mss-login/vendor",
					)
				):
					continue
				# Still check
		path_tpl = None
		if "path" in info:
			path_tpl = info["path"]
		elif "formatter" in info:
			path_tpl = info["formatter"]
		if not path_tpl:
			continue
		# Normalize {var} style
		canon = str(path_tpl)
		if not is_mss_scoped_path(canon.split("{")[0] if "{" in canon else canon):
			# Also check gallery / generate_token exact
			if not (
				canon.startswith("/mss-login")
				or canon.startswith("/api/mss-login")
				or canon.startswith("/mss-login-gallery")
				or canon.startswith("/mss/models")
				or canon.startswith("/api/mss/models")
				or canon in ("/generate_token", "/s3_mounter/unmount")
			):
				continue
		# Determine methods
		methods: set[str] = set()
		try:
			for route in resource:
				meth = getattr(route, "method", None)
				if meth and meth != "*":
					methods.add(meth.upper())
		except Exception:
			methods = {"GET"}
		if not methods:
			methods = {"GET"}
		for meth in methods:
			# Try lookup with concrete-ish path (replace {x} with sample)
			sample = re.sub(r"\{[^}]+\}", "x", canon)
			sample = normalize_request_path(sample) or sample
			pol = lookup_policy(meth, sample)
			if pol is None:
				# Also try exact template match against our list
				found = False
				for m, p, _pol in _ROUTE_POLICIES:
					if m != meth:
						continue
					# Compare ignoring param names
					a = re.sub(r"\{[^}]+\}", "{}", p)
					b = re.sub(r"\{[^}]+\}", "{}", canon)
					if a == b or _compile_path(p).match(sample):
						found = True
						break
				if not found:
					missing.append(f"{meth} {canon}")
	return missing
