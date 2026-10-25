# --- START OF FILE utils/access_control.py ---
import contextvars
import copy
import heapq
import json
import os
import shutil

import folder_paths
from aiohttp import web
from execution import MAXIMUM_HISTORY_SIZE, PromptQueue
from server import PromptServer

from .api_token_store import get_api_token_store
from .data_dir import get_data_subdir
from .debug_log import debug_write
from .user_isolation import safe_user_dir_segment, user_id_from_queue_item, username_from_queue_item
from .users_db import UsersDB

# Map Permission Keys -> URL Paths to Block
EXTENSION_BLOCK_MAP = {
	"settings_itools": ["/extensions/ComfyUI-iTools", "/api/itools"],
	"settings_crystools": ["/extensions/ComfyUI-Crystools", "/api/crystools"],
	"settings_rgthree": ["/extensions/rgthree-comfy", "/api/rgthree", "/rgthree"],
	"settings_gallery": ["/extensions/comfyui-gallery", "/api/gallery"],
	"can_access_manager": ["/extensions/comfyui-manager", "/api/manager", "/manager"],
	"can_manage_extensions": [
		"/Comfy.Extension",
		"/api/settings/Comfy/Extension",
		"/api/settings/Comfy/Extension/enable",
		"/api/settings/Comfy/Extension/Disabled",
		"/api/extensions/apply",
		"/extensions",
	],
	"can_modify_workflows": [
		"/api/userdata/workflows:",
		"/api/userdata/workflows/save",
		"/api/userdata/workflows/export",
	],
}

# Sensitive flags that must default-deny when absent/malformed (AUDIT-003).
_SENSITIVE_PERM_FLAGS = frozenset({"can_run", "can_upload", "can_access_api"})


def _sensitive_perm_allowed(perms: dict | None, key: str) -> bool:
	"""
	Return True only when a sensitive permission flag is explicitly truthy.

	Missing keys, None, False, 0, and non-affirmative strings (e.g. "false") deny.
	Admin/owner shortcuts elsewhere (can_modify_workflows, extension blocks) are unchanged.
	"""
	if key not in _SENSITIVE_PERM_FLAGS:
		raise ValueError(f"not a sensitive permission flag: {key}")
	if not isinstance(perms, dict):
		return False
	val = perms.get(key)
	if val is None:
		return False
	if isinstance(val, bool):
		return val
	if isinstance(val, (int, float)):
		return val != 0
	if isinstance(val, str):
		return val.strip().lower() in ("1", "true", "yes", "on")
	return bool(val)


class AccessControl:
	def __init__(
		self,
		users_db: UsersDB,
		server: PromptServer,
		groups_config_file: str,
		api_token_store_config: dict = None,
	):
		self.users_db = users_db
		self.server = server
		self.groups_config_file = groups_config_file
		self.api_token_store_config = api_token_store_config or {}

		self._current_user = contextvars.ContextVar("user_id", default=None)
		self.__current_user_id = None
		self._current_username = contextvars.ContextVar("access_control_username", default=None)
		self.__current_username = None
		self.__get_output_directory = folder_paths.get_output_directory
		self.__get_temp_directory = folder_paths.get_temp_directory
		self.__get_input_directory = folder_paths.get_input_directory
		self.__prompt_queue = self.server.prompt_queue
		self.__prompt_queue_put = self.__prompt_queue.put

	def _load_group_config(self):
		if not os.path.exists(self.groups_config_file):
			return {}
		try:
			with open(self.groups_config_file, "r") as f:
				return json.load(f)
		except Exception:
			return {}

	def _get_user_role_and_permissions(self, request):
		token = None
		if "jwt_token" in request.cookies:
			token = request.cookies.get("jwt_token")
		if not token and "Authorization" in request.headers:
			parts = request.headers.get("Authorization", "").split(" ")
			if len(parts) == 2:
				token = parts[1]
		if not token:
			try:
				q = getattr(getattr(request, "rel_url", None), "query", None)
				if q and (q.get("token") or q.get("access_token")):
					token = (q.get("token") or q.get("access_token") or "").strip()
			except Exception:
				pass

		if not token:
			return "guest", {}, None

		# Try long-lived API token store first (Bearer tokens)
		try:
			api_store = get_api_token_store(self.api_token_store_config)
			api_user = api_store.get_user_for_token(token)
			if api_user is not None:
				_user_id, username = api_user
				_, user_rec = self.users_db.get_user(username)
				if not user_rec:
					return "guest", {}, None
				groups = [g.lower() for g in user_rec.get("groups", [])]
				role = groups[0] if groups else "user"
				cfg = self._load_group_config()
				perms = cfg.get(role, {})
				return role, perms, username
		except Exception:
			pass

		try:
			payload = self._decode_verified_jwt_claims(token)
			if not payload:
				return "guest", {}, None
			username = payload.get("username")
			if not username:
				return "guest", {}, None

			_, user_rec = self.users_db.get_user(username)
			if not user_rec:
				return "guest", {}, None

			groups = [g.lower() for g in user_rec.get("groups", [])]
			role = groups[0] if groups else "user"
			cfg = self._load_group_config()
			perms = cfg.get(role, {})
			return role, perms, username
		except Exception:
			return "guest", {}, None

	@staticmethod
	def _decode_verified_jwt_claims(token: str) -> dict | None:
		"""
		Decode JWT with signature + algorithm verification (AUDIT-004).

		Never trust unverified claims for role/permission resolution. Returns None
		on missing secret, bad signature, expiry, or decode errors.
		"""
		token = (token or "").strip()
		if not token or token.count(".") < 2:
			return None
		try:
			import jwt

			from ..constants import SECRET_KEY, TOKEN_ALGORITHM
		except Exception:
			return None
		secret = SECRET_KEY or ""
		if not secret:
			return None
		alg = TOKEN_ALGORITHM or "HS256"
		try:
			return jwt.decode(token, secret, algorithms=[alg])
		except Exception:
			return None

	def create_mss_login_middleware(self):
		@web.middleware
		async def middleware(request: web.Request, handler):
			path = request.path

			# 1. Public Whitelist (exact auth pages / static only — NOT all of /mss-login;
			#    MSS API/me/gallery RBAC is enforced by route_policy middleware + handlers.)
			if (
				path.startswith(
					(
						"/login",
						"/register",
						"/logout",
						"/loading",
						"/static",
						"/favicon",
						"/ws",
						"/assets",
					)
				)
				or path == "/"
				or path.startswith(
					("/mss-login/css", "/mss-login/js", "/mss-login/assets", "/mss-login/dist")
				)
			):
				return await handler(request)

			# 2. Core Extensions
			if path.startswith(
				("/extensions/core", "/extensions/ComfyUI-MSS-Login", "/extensions/MSS-Login")
			):
				return await handler(request)

			# 3. Resolve User
			role, perms, username = self._get_user_role_and_permissions(request)

			# 4. Check Permissions (token-authenticated users get standard ComfyUI + custom endpoints
			#    e.g. comfy-portal / Comfy Portal iOS when can_run/can_access_api are true)
			is_queue = path.startswith(("/prompt", "/api/prompt", "/api/queue", "/queue"))
			is_upload = path.startswith(("/upload", "/api/upload"))
			is_userdata_workflow = path.startswith(
				("/api/userdata/workflows", "/api/userdata/workflows:")
			)
			is_cpe_api = path.startswith(("/api/cpe/", "/cpe/"))
			mutating_methods = ("POST", "PUT", "DELETE", "PATCH")
			try:
				from .cpe_workflows import cpe_path_requires_modify_workflows
			except Exception:

				def cpe_path_requires_modify_workflows(p, m):
					if (m or "").upper() not in mutating_methods:
						return False
					if p in ("/api/cpe/workflow/convert", "/cpe/workflow/convert"):
						return False
					return (p or "").startswith(("/api/cpe/workflow", "/cpe/workflow"))

			if is_queue and not _sensitive_perm_allowed(perms, "can_run"):
				debug_write(
					{
						"location": "access_control",
						"message": "deny_403",
						"data": {"path": path, "reason": "can_run"},
						"hypothesisId": "D",
					}
				)
				return web.json_response({"error": "MSS-Login: Execution Denied"}, status=403)

			if is_upload and not _sensitive_perm_allowed(perms, "can_upload"):
				debug_write(
					{
						"location": "access_control",
						"message": "deny_403",
						"data": {"path": path, "reason": "can_upload"},
						"hypothesisId": "D",
					}
				)
				return web.json_response({"error": "MSS-Login: Upload Denied"}, status=403)

			needs_modify_workflows = (
				is_userdata_workflow and request.method in mutating_methods
			) or cpe_path_requires_modify_workflows(path, request.method)

			if needs_modify_workflows:
				can_modify = perms.get("can_modify_workflows")
				if can_modify is None:
					can_modify = role != "guest"
				if role in ("admin", "owner"):
					can_modify = True

				if not can_modify:
					return web.json_response(
						{
							"error": "MSS-Login: Workflow Denied",
							"code": "WORKFLOW_DENIED",
							"role": role,
						},
						status=403,
					)

			for perm_key, blocked_paths in EXTENSION_BLOCK_MAP.items():
				allow = perms.get(perm_key)
				if allow is None:
					allow = role != "guest"
				if role in ("admin", "owner"):
					allow = True
				if allow is False:
					for blocked_prefix in blocked_paths:
						if path.lower().startswith(blocked_prefix.lower()):
							return web.Response(status=403, text="mss-login: Access Denied")

			if path.startswith("/mss-login/api/s3/"):
				can_s3 = perms.get("can_access_s3_storage")
				if can_s3 is None:
					can_s3 = role in ("admin", "owner")
				if not can_s3:
					return web.json_response(
						{"error": "MSS-Login: S3 Storage Access Denied"}, status=403
					)

			if not is_queue and not is_upload and (path.startswith("/api/") or is_cpe_api):
				if not _sensitive_perm_allowed(perms, "can_access_api"):
					debug_write(
						{
							"location": "access_control",
							"message": "deny_403",
							"data": {"path": path, "reason": "can_access_api"},
							"hypothesisId": "D",
						}
					)
					return web.json_response({"error": "MSS-Login: API Denied"}, status=403)

			return await handler(request)

		return middleware

	# --- Folder & User Context Methods ---

	def set_current_user_id(
		self, user_id: str, set_fallback=False, username: str | None = None
	):
		self._current_user.set(user_id)
		if username:
			self._current_username.set(username)
		if set_fallback:
			self.__current_user_id = user_id
			if username:
				self.__current_username = username

	def set_current_user(
		self, user_id: str, username: str | None = None, set_fallback=False
	):
		self.set_current_user_id(user_id, set_fallback=set_fallback, username=username)

	def get_current_user_id(self):
		return self._current_user.get() or self.__current_user_id

	def get_current_username(self) -> str:
		"""Resolve the current active username (falls back to lookup or user_id or 'public')."""
		uname = self._current_username.get()
		if uname and uname not in ("guest", "unknown", "public"):
			return safe_user_dir_segment(uname)
		try:
			from ..globals import current_username_var

			g_uname = current_username_var.get(None)
			if g_uname and g_uname not in ("guest", "unknown", "public"):
				return safe_user_dir_segment(g_uname)
		except Exception:
			pass
		if self.__current_username and self.__current_username not in (
			"guest",
			"unknown",
			"public",
		):
			return safe_user_dir_segment(self.__current_username)
		uid = self.get_current_user_id()
		if uid:
			rec = self._lookup_user_record(uid)
			if rec and rec.get("username"):
				return safe_user_dir_segment(rec["username"])
			return safe_user_dir_segment(uid)
		return "public"

	@staticmethod
	def _normalize_user_id(user_id) -> str | None:
		"""Normalize user ids for stamp/filter compares (UUID str vs other types)."""
		if user_id is None:
			return None
		text = str(user_id).strip()
		return text or None

	def _user_id_matches(self, stamped_user_id, current_user_id) -> bool:
		left = self._normalize_user_id(stamped_user_id)
		right = self._normalize_user_id(current_user_id)
		if left is None or right is None:
			return False
		if left == right:
			return True
		# Dual-read match: one may be UUID and the other may be username
		rec_left = self._lookup_user_record(left)
		rec_right = self._lookup_user_record(right)
		if rec_left and rec_right:
			left_uname = (rec_left.get("username") or "").strip().lower()
			right_uname = (rec_right.get("username") or "").strip().lower()
			if left_uname and left_uname == right_uname:
				return True
		if rec_left and (rec_left.get("username") or "").strip().lower() == right.lower():
			return True
		if rec_right and (rec_right.get("username") or "").strip().lower() == left.lower():
			return True
		return False

	def _lookup_user_record(self, user_key: str | None) -> dict | None:
		"""Resolve a users_db record from a user_id (UUID) or username."""
		if not user_key or user_key == "public":
			return None
		try:
			_uid, rec = self.users_db.get_user(user_id=user_key)
			if rec:
				return rec
			_uid, rec = self.users_db.get_user(username=user_key)
			if rec:
				return rec
		except Exception:
			return None
		return None

	def _is_current_user_admin_or_owner(self) -> bool:
		"""Return True if the current user has admin or owner role."""
		user_id = self.get_current_user_id()
		user_rec = self._lookup_user_record(user_id)
		if not user_rec:
			return False
		groups = [g.lower() for g in user_rec.get("groups", [])]
		return "admin" in groups or "owner" in groups or bool(user_rec.get("admin"))

	def _get_output_base(self):
		"""Base directory for user output under MSS_LOGIN_DATA_DIR."""
		return get_data_subdir("output")

	def _get_temp_base(self):
		"""Base directory for user temp under MSS_LOGIN_DATA_DIR."""
		return get_data_subdir("temp")

	def _get_input_base(self):
		"""Base directory for user input under MSS_LOGIN_DATA_DIR."""
		return get_data_subdir("input")

	@staticmethod
	def _migrate_user_storage_folder(
		base_dir: str, uuid_seg: str, uname_seg: str
	) -> None:
		"""Consolidate legacy <base>/<uuid> into <base>/<username>."""
		if (
			not uuid_seg
			or not uname_seg
			or uuid_seg == uname_seg
			or uuid_seg == "public"
		):
			return
		src = os.path.join(base_dir, uuid_seg)
		dst = os.path.join(base_dir, uname_seg)
		if not os.path.isdir(src):
			return
		os.makedirs(dst, exist_ok=True)
		try:
			for item in os.listdir(src):
				if not item or item in (".", ".."):
					continue
				s_item = os.path.join(src, item)
				d_item = os.path.join(dst, item)
				if os.path.isdir(s_item):
					shutil.copytree(s_item, d_item, dirs_exist_ok=True)
					shutil.rmtree(s_item, ignore_errors=True)
				elif os.path.isfile(s_item):
					if not os.path.exists(d_item):
						try:
							os.replace(s_item, d_item)
						except OSError:
							shutil.copy2(s_item, d_item)
							os.remove(s_item)
					else:
						try:
							os.remove(s_item)
						except OSError:
							pass
			try:
				os.rmdir(src)
			except OSError:
				pass
		except OSError:
			pass

	def get_user_output_directory(self):
		get_uname = getattr(self, "get_current_username", None)
		username = safe_user_dir_segment(get_uname()) if callable(get_uname) else safe_user_dir_segment(self.get_current_user_id())
		user_id = safe_user_dir_segment(self.get_current_user_id())
		self._migrate_user_storage_folder(self._get_output_base(), user_id, username)
		directory = os.path.join(self._get_output_base(), username)
		os.makedirs(directory, exist_ok=True)
		return directory

	def get_user_temp_directory(self):
		get_uname = getattr(self, "get_current_username", None)
		username = safe_user_dir_segment(get_uname()) if callable(get_uname) else safe_user_dir_segment(self.get_current_user_id())
		user_id = safe_user_dir_segment(self.get_current_user_id())
		self._migrate_user_storage_folder(self._get_temp_base(), user_id, username)
		directory = os.path.join(self._get_temp_base(), username)
		os.makedirs(directory, exist_ok=True)
		return directory

	def get_user_input_directory(self):
		get_uname = getattr(self, "get_current_username", None)
		username = safe_user_dir_segment(get_uname()) if callable(get_uname) else safe_user_dir_segment(self.get_current_user_id())
		user_id = safe_user_dir_segment(self.get_current_user_id())
		self._migrate_user_storage_folder(self._get_input_base(), user_id, username)
		directory = os.path.join(self._get_input_base(), username)
		os.makedirs(directory, exist_ok=True)
		return directory

	def add_user_specific_folder_paths(self, json_data):
		get_uname = getattr(self, "get_current_username", None)
		if callable(get_uname):
			raw_user = get_uname()
		else:
			get_uid = getattr(self, "get_current_user_id", None)
			raw_user = get_uid() if callable(get_uid) else "public"
		user_seg = safe_user_dir_segment(raw_user)
		if isinstance(json_data, dict):
			for k, v in json_data.items():
				if k in ("extra_data", "extra_pnginfo", "workflow"):
					continue
				if k == "filename_prefix":
					json_data[k] = f"{user_seg}/{v}"
				else:
					self.add_user_specific_folder_paths(v)
		elif isinstance(json_data, list):
			for item in json_data:
				self.add_user_specific_folder_paths(item)
		return json_data

	def patch_folder_paths(self):
		"""Point ComfyUI output/temp/input to per-user dirs under MSS_LOGIN_DATA_DIR."""
		for base in (self._get_output_base(), self._get_temp_base(), self._get_input_base()):
			os.makedirs(base, exist_ok=True)
		folder_paths.get_output_directory = self.get_user_output_directory
		folder_paths.get_temp_directory = self.get_user_temp_directory
		folder_paths.get_input_directory = self.get_user_input_directory
		self.server.add_on_prompt_handler(self.add_user_specific_folder_paths)

	# --- MISSING METHOD RESTORED HERE ---
	def create_folder_access_control_middleware(self):
		folder_paths_check = (
			self._get_output_base(),
			self._get_temp_base(),
			self._get_input_base(),
		)

		@web.middleware
		async def middleware(request: web.Request, handler):
			if not request.path.startswith(folder_paths_check):
				return await handler(request)
			# Future expansion: Check permissions for specific file access here
			return await handler(request)

		return middleware

	# --- Queue Patching ---

	def patch_prompt_queue(self):
		self.__prompt_queue.put = self.user_queue_put
		self.__prompt_queue.get = self.user_queue_get
		self.__prompt_queue.task_done = self.user_queue_task_done
		self.__prompt_queue.get_current_queue = self.user_queue_get_current_queue
		self.__prompt_queue.wipe_queue = self.user_queue_wipe_queue
		self.__prompt_queue.delete_queue_item = self.user_queue_delete_queue_item
		self.__prompt_queue.get_history = self.user_queue_get_history
		self.__prompt_queue.wipe_history = self.user_queue_wipe_history

	def user_queue_put(self, item):
		current_user_id = self._normalize_user_id(self.get_current_user_id())
		user_rec = self._lookup_user_record(current_user_id)

		if user_rec:
			if os.path.exists(self.groups_config_file):
				try:
					with open(self.groups_config_file, "r") as f:
						cfg = json.load(f)
					groups = user_rec.get("groups", ["user"])
					role = groups[0] if groups else "user"
					perms = cfg.get(role, {})
					if not _sensitive_perm_allowed(perms, "can_run"):
						print(f"[AccessControl] Blocked execution for {current_user_id}")
						# Fail closed: never silently drop a prompt (Portal/clients
						# would see a successful path with no queue progress).
						raise PermissionError(
							"MSS-Login: Execution Denied (can_run is false for this role)"
						)
				except PermissionError:
					raise
				except Exception:
					pass

		stamp_username = None
		if user_rec:
			stamp_username = user_rec.get("username")
		if not stamp_username:
			cand = self.get_current_username()
			if cand and cand != "public":
				stamp_username = cand
		# Always stamp a normalized string user_id so SEPERATE_USERS queue/history
		# filters match Portal Bearer/API-token identity comparisons.
		stamp = {"user_id": current_user_id, "username": stamp_username}
		if isinstance(item, tuple):
			new_item = (*item, stamp)
		else:
			new_item = (item, stamp)
		self.__prompt_queue_put(new_item)

	def user_queue_get(self, timeout=None):
		with self.__prompt_queue.not_empty:
			while not self.__prompt_queue.queue:
				self.__prompt_queue.not_empty.wait(timeout=timeout)
				if timeout and not self.__prompt_queue.queue:
					return None
			entry = heapq.heappop(self.__prompt_queue.queue)
			task_id = self.__prompt_queue.task_counter
			self.__prompt_queue.currently_running[task_id] = entry
			self.__prompt_queue.task_counter += 1
			# Worker threads do not inherit the HTTP request ContextVar. Stamp
			# this job's user onto the worker so SaveImage/LoadImage and
			# get_output_directory() resolve the submitter's isolated folders.
			job_user = user_id_from_queue_item(entry)
			job_name = username_from_queue_item(entry)
			if job_user:
				self.set_current_user_id(job_user, set_fallback=True, username=job_name)
			if job_name:
				self._current_username.set(job_name)
				self.__current_username = job_name
				try:
					from ..globals import current_username_var

					current_username_var.set(job_name)
				except Exception:
					pass
				try:
					from .sfw_intercept.nsfw_guard import set_latest_prompt_user

					set_latest_prompt_user(job_name)
				except Exception:
					pass
			self.server.queue_updated()
			return (entry, task_id)

	def user_queue_task_done(
		self, item_id, history_result, status=None, process_item=None, **kwargs
	):
		# Forward-compat: ignore unknown kwargs from future ComfyUI cores (log once).
		if kwargs:
			unknown = sorted(k for k in kwargs if k not in ("completed", "messages"))
			if unknown and not getattr(AccessControl, "_task_done_unknown_kwargs_logged", False):
				AccessControl._task_done_unknown_kwargs_logged = True
				debug_write(
					{
						"location": "access_control.py:user_queue_task_done",
						"message": "Ignoring unknown task_done kwargs",
						"data": {"keys": unknown},
					}
				)
		with self.__prompt_queue.mutex:
			item = self.__prompt_queue.currently_running.pop(item_id)
			while len(self.__prompt_queue.history) > MAXIMUM_HISTORY_SIZE:
				self.__prompt_queue.history.pop(next(iter(self.__prompt_queue.history)))
			meta = item[-1] if isinstance(item[-1], dict) else {}
			raw = item[:-1] if isinstance(item[-1], dict) else item
			# Apply core process_item (e.g. strip sensitive extra_data) before MSS normalize.
			if process_item is not None:
				try:
					raw = process_item(raw)
				except Exception:
					pass
			# ComfyUI comfy_execution/jobs.py normalize_history_item expects exactly 5 elements
			prompt_tuple = tuple(raw[:5]) if len(raw) > 5 else raw
			if status is not None:
				if hasattr(status, "_asdict"):
					status_dict = copy.deepcopy(status._asdict())
				elif isinstance(status, dict):
					status_dict = dict(status)
				else:
					status_dict = {
						"completed": kwargs.get("completed"),
						"messages": kwargs.get("messages") or [],
					}
			else:
				# Legacy callers / older tests: completed= and messages= kwargs
				status_dict = {
					"completed": kwargs.get("completed"),
					"messages": kwargs.get("messages") or [],
				}
				completed = status_dict.get("completed")
				if isinstance(completed, bool):
					status_dict["status_str"] = "success" if completed else "error"
			# Coerce missing / None / non-list messages without KeyError or char-splitting strings.
			raw_messages = status_dict.get("messages")
			if isinstance(raw_messages, list):
				status_dict["messages"] = raw_messages
			elif isinstance(raw_messages, tuple):
				status_dict["messages"] = list(raw_messages)
			else:
				status_dict["messages"] = []
			self.__prompt_queue.history[prompt_tuple[1]] = {
				"prompt": prompt_tuple,
				"outputs": {},
				"status": status_dict,
				"user_id": self._normalize_user_id(meta.get("user_id")),
			}
			if history_result:
				self.__prompt_queue.history[prompt_tuple[1]].update(history_result)
			# ComfyUI jobs.normalize_history_item iterates status.messages; ensure it is never None
			entry = self.__prompt_queue.history[prompt_tuple[1]]
			st = entry.get("status")
			if isinstance(st, dict) and st.get("messages") is None:
				st["messages"] = []
			self.server.queue_updated()

	def user_queue_get_current_queue(self):
		def unwrap(entry):
			if isinstance(entry, tuple) and isinstance(entry[-1], dict):
				return entry[:-1]
			return entry

		current_user = self.get_current_user_id()
		show_all = self._is_current_user_admin_or_owner()
		with self.__prompt_queue.mutex:
			running = []
			pending = []
			for item in self.__prompt_queue.currently_running.values():
				meta = item[-1] if isinstance(item[-1], dict) else None
				if not show_all and (
					not meta or not self._user_id_matches(meta.get("user_id"), current_user)
				):
					continue
				running.append(unwrap(item))
			for item in self.__prompt_queue.queue:
				meta = item[-1] if isinstance(item[-1], dict) else None
				if not show_all and (
					not meta or not self._user_id_matches(meta.get("user_id"), current_user)
				):
					continue
				pending.append(unwrap(item))
			return (running, copy.deepcopy(pending))

	def user_queue_wipe_queue(self):
		with self.__prompt_queue.mutex:
			if self._is_current_user_admin_or_owner():
				self.__prompt_queue.queue = []
			else:
				current_user = self.get_current_user_id()
				self.__prompt_queue.queue = [
					i
					for i in self.__prompt_queue.queue
					if not (
						isinstance(i[-1], dict)
						and self._user_id_matches(i[-1].get("user_id"), current_user)
					)
				]
			self.server.queue_updated()

	def user_queue_delete_queue_item(self, func):
		def unwrap(entry):
			if isinstance(entry, tuple) and isinstance(entry[-1], dict):
				return entry[:-1]
			return entry

		with self.__prompt_queue.mutex:
			for i, item in enumerate(self.__prompt_queue.queue):
				meta = item[-1] if isinstance(item[-1], dict) else None
				if (
					meta
					and self._user_id_matches(meta.get("user_id"), self.get_current_user_id())
					and func(unwrap(item))
				):
					self.__prompt_queue.queue.pop(i)
					heapq.heapify(self.__prompt_queue.queue)
					self.server.queue_updated()
					return True
		return False

	def user_queue_get_history(self, prompt_id=None, max_items=None, offset=-1):
		with self.__prompt_queue.mutex:
			user = self.get_current_user_id()
			if self._is_current_user_admin_or_owner():
				filtered = dict(self.__prompt_queue.history)
			else:
				filtered = {
					k: v
					for k, v in self.__prompt_queue.history.items()
					if self._user_id_matches(v.get("user_id"), user)
				}
			if prompt_id:
				return {prompt_id: filtered.get(prompt_id)} if prompt_id in filtered else {}
			keys = list(filtered.keys())
			if offset < 0:
				offset = max(0, len(keys) - max_items) if max_items else 0
			result = {}
			for k in keys[offset:]:
				result[k] = filtered[k]
				if max_items and len(result) >= max_items:
					break
			return result

	def user_queue_wipe_history(self):
		with self.__prompt_queue.mutex:
			if self._is_current_user_admin_or_owner():
				self.__prompt_queue.history = {}
			else:
				u = self.get_current_user_id()
				self.__prompt_queue.history = {
					k: v
					for k, v in self.__prompt_queue.history.items()
					if not self._user_id_matches(v.get("user_id"), u)
				}
