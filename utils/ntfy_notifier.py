# --- START OF FILE utils/ntfy_notifier.py ---
"""
NTFY push notifications over HTTPS for ComfyUI MSS-Login events.

Supports the full ntfy publishing API (https://docs.ntfy.sh/publish/):
  - Text messages with title, priority, tags, markdown, and click actions
  - File attachments (local file via PUT, or external URL via Attach header)
  - Icon URLs for branded notifications
  - Action buttons (view, http, copy)
  - Bearer-token authentication

Admin configures topic, base URL, and per-event toggles in config.json
(via the /mss-login/api/settings/ntfy admin endpoint).
"""

from __future__ import annotations

import json
import logging
import mimetypes
import os
import threading
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Union

import requests

from ..constants import DEBUG_MODE, NTFY_API_KEY

logger = logging.getLogger("mss-login.ntfy")

# ---------------------------------------------------------------------------
# Defaults
# ---------------------------------------------------------------------------

DEFAULT_BASE_URL: str = "https://ntfy.sh"
"""Default ntfy server URL (public instance)."""

DEFAULT_TIMEOUT_SECONDS: int = 30
"""HTTP request timeout when publishing to the ntfy server."""

# ---------------------------------------------------------------------------
# Valid ntfy priority levels
# Reference: https://docs.ntfy.sh/publish/#message-priority
# ---------------------------------------------------------------------------

PRIORITY_MIN: str = "min"  # ID 1 - no vibration or sound, under the fold
PRIORITY_LOW: str = "low"  # ID 2 - no vibration or sound
PRIORITY_DEFAULT: str = "default"  # ID 3 - short default vibration and sound
PRIORITY_HIGH: str = "high"  # ID 4 - long vibration burst, pop-over
PRIORITY_MAX: str = "max"  # ID 5 - really long vibration bursts, pop-over

VALID_PRIORITIES = frozenset(
	{
		PRIORITY_MIN,
		PRIORITY_LOW,
		PRIORITY_DEFAULT,
		PRIORITY_HIGH,
		PRIORITY_MAX,
		"1",
		"2",
		"3",
		"4",
		"5",
		"urgent",  # alias for max
	}
)

# ---------------------------------------------------------------------------
# Event keys (toggleable by admin in settings)
# ---------------------------------------------------------------------------

EVENT_KEYS: List[str] = [
	"nsfw_block",
	"user_created",
	"user_login",
	"user_logout",
	"api_token_created",
	"login_failure",
	"mfa_enabled",
	"mfa_disabled",
	"image_generated",
	"image_generation_failed",
	"user_deleted",
	"user_role_changed",
	"settings_changed",
	"server_started",
	"server_stopped",
	"update_available",
	"shared_items_added",
	"experimental_recovery",
]
"""All recognized event keys that admins can toggle on/off."""

# ---------------------------------------------------------------------------
# Per-event default metadata (tags, priority, icon emoji short-codes)
# Tags that match ntfy emoji short codes are rendered as emoji in clients.
# Reference: https://docs.ntfy.sh/publish/#tags-emojis
# Reference: https://docs.ntfy.sh/emojis/
# ---------------------------------------------------------------------------

_EVENT_DEFAULTS: Dict[str, Dict[str, Union[str, List[str]]]] = {
	"nsfw_block": {
		"tags": ["no_entry_sign", "nsfw"],
		"priority": PRIORITY_HIGH,
	},
	"user_created": {
		"tags": ["tada", "new_user"],
		"priority": PRIORITY_DEFAULT,
	},
	"user_login": {
		"tags": ["key", "login"],
		"priority": PRIORITY_DEFAULT,
	},
	"user_logout": {
		"tags": ["wave", "logout"],
		"priority": PRIORITY_LOW,
	},
	"api_token_created": {
		"tags": ["lock", "api_token"],
		"priority": PRIORITY_DEFAULT,
	},
	"login_failure": {
		"tags": ["warning", "rotating_light", "login_failure"],
		"priority": PRIORITY_HIGH,
	},
	"mfa_enabled": {
		"tags": ["heavy_check_mark", "shield", "mfa"],
		"priority": PRIORITY_DEFAULT,
	},
	"mfa_disabled": {
		"tags": ["warning", "shield", "mfa"],
		"priority": PRIORITY_HIGH,
	},
	"image_generated": {
		"tags": ["framed_picture", "image"],
		"priority": PRIORITY_LOW,
	},
	"image_generation_failed": {
		"tags": ["x", "image"],
		"priority": PRIORITY_DEFAULT,
	},
	"user_deleted": {
		"tags": ["skull", "user_deleted"],
		"priority": PRIORITY_HIGH,
	},
	"user_role_changed": {
		"tags": ["busts_in_silhouette", "role"],
		"priority": PRIORITY_DEFAULT,
	},
	"settings_changed": {
		"tags": ["gear", "settings"],
		"priority": PRIORITY_LOW,
	},
	"server_started": {
		"tags": ["rocket", "server"],
		"priority": PRIORITY_DEFAULT,
	},
	"server_stopped": {
		"tags": ["octagonal_sign", "server"],
		"priority": PRIORITY_HIGH,
	},
	"update_available": {
		"tags": ["loudspeaker", "update"],
		"priority": PRIORITY_DEFAULT,
	},
	"shared_items_added": {
		"tags": ["package", "shared_items"],
		"priority": PRIORITY_DEFAULT,
	},
	"shared_items_removed": {
		"tags": ["package", "shared_items"],
		"priority": PRIORITY_DEFAULT,
	},
	"experimental_recovery": {
		"tags": ["rotating_light", "shield", "recovery"],
		"priority": PRIORITY_HIGH,
	},
}


# ---------------------------------------------------------------------------
# Configuration helpers
# ---------------------------------------------------------------------------


def _load_ntfy_config() -> dict:
	"""
	Load ntfy configuration from config.json.

	Expected structure inside config.json::

	    {
	        "ntfy": {
	            "topic": "my-comfyui-topic",
	            "base_url": "https://ntfy.sh",
	            "enabled_events": ["user_login", "nsfw_block", ...]
	        }
	    }

	Returns a dict with keys: topic, enabled_events, base_url.
	"""
	try:
		from ..constants import CONFIG_FILE_PATH
		from ..utils.json_utils import load_json_file

		cfg = load_json_file(CONFIG_FILE_PATH, {})
		ntfy = cfg.get("ntfy") or {}
		topic = (ntfy.get("topic") or "").strip()
		enabled = ntfy.get("enabled_events")
		if not isinstance(enabled, list):
			enabled = []
		base_url = (ntfy.get("base_url") or DEFAULT_BASE_URL).rstrip("/")
		return {"topic": topic, "enabled_events": enabled, "base_url": base_url}
	except Exception:
		return {"topic": "", "enabled_events": [], "base_url": DEFAULT_BASE_URL}


def get_ntfy_config() -> dict:
	"""Return current ntfy config (topic, enabled_events, base_url) for the admin API."""
	return _load_ntfy_config()


def _validate_ntfy_base_url(url: str) -> str:
	"""
	Validate and normalize a ntfy base URL. Requires HTTPS unless the host is
	localhost or a loopback address (127.x.x.x / ::1) for local self-hosted use.

	Raises ValueError if the URL scheme is HTTP for a non-local host.
	Returns the normalised URL (trailing slash stripped).
	"""
	from urllib.parse import urlparse

	url = (url or DEFAULT_BASE_URL).strip().rstrip("/")
	parsed = urlparse(url)
	scheme = (parsed.scheme or "").lower()
	hostname = (parsed.hostname or "").lower()

	_local_hosts = {"localhost", "127.0.0.1", "::1"}
	is_local = hostname in _local_hosts or hostname.startswith("127.")

	if scheme == "http" and not is_local:
		raise ValueError(
			f"ntfy base_url must use HTTPS for non-local hosts (got: {url!r}). "
			"Use https:// to protect your API key and notification content in transit."
		)
	return url


def save_ntfy_config(
	topic: str,
	enabled_events: List[str],
	base_url: str = "",
) -> None:
	"""
	Persist ntfy config to config.json.

	Parameters
	----------
	topic : str
	    The ntfy topic name (acts as a channel/password).
	enabled_events : list[str]
	    Which EVENT_KEYS should trigger a notification.
	base_url : str, optional
	    Custom ntfy server URL. Defaults to ``DEFAULT_BASE_URL``.
	    Must use HTTPS for non-local hosts.

	Raises
	------
	ValueError
	    If ``base_url`` uses HTTP for a non-local hostname.
	"""
	from ..constants import CONFIG_FILE_PATH
	from ..utils.json_utils import load_json_file, save_json_file

	validated_url = _validate_ntfy_base_url(base_url)
	cfg = load_json_file(CONFIG_FILE_PATH, {})
	if not isinstance(cfg, dict):
		cfg = {}
	cfg["ntfy"] = {
		"topic": (topic or "").strip(),
		"enabled_events": (list(enabled_events) if isinstance(enabled_events, list) else []),
		"base_url": validated_url,
	}
	save_json_file(CONFIG_FILE_PATH, cfg)


# ---------------------------------------------------------------------------
# Notification result
# ---------------------------------------------------------------------------


@dataclass
class NotificationResult:
	"""Outcome of a notification attempt."""

	success: bool
	"""True when the notification was sent (HTTP 200) or intentionally skipped."""

	skipped: bool = False
	"""True when the event was disabled or no topic was configured."""

	status_code: Optional[int] = None
	"""HTTP status code returned by the ntfy server (None if skipped or errored)."""

	error: Optional[str] = None
	"""Error message if the send failed."""

	response_body: Optional[str] = None
	"""Raw response body from the ntfy server (useful for debugging)."""


# ---------------------------------------------------------------------------
# Core publishing function
# ---------------------------------------------------------------------------


def send_notification(
	event_key: str,
	title: str = "",
	message: str = "",
	*,
	priority: str = "",
	tags: Optional[List[str]] = None,
	click: str = "",
	icon: str = "",
	markdown: bool = False,
	attachment_url: str = "",
	attachment_file_path: str = "",
	attachment_filename: str = "",
	actions: Optional[List[dict]] = None,
	delay: str = "",
	email: str = "",
	api_key: str = "",
	timeout: int = DEFAULT_TIMEOUT_SECONDS,
	_async: bool = True,
) -> Union[NotificationResult, bool]:
	"""
	Send a push notification to ntfy if the event is enabled and a topic is set.

	This function builds proper ntfy-compliant HTTP requests following the
	official publishing API (https://docs.ntfy.sh/publish/).

	**Text messages** are sent as ``POST /<topic>`` with ntfy headers.
	**File attachments** (local) are sent as ``PUT /<topic>`` with the file
	as the request body and a ``Filename`` header.
	**URL attachments** use the ``Attach`` header on a normal ``POST``.

	Parameters
	----------
	event_key : str
	    One of :data:`EVENT_KEYS`. The notification is only sent if this
	    event is in the admin's ``enabled_events`` list.
	title : str
	    Notification title (``Title`` header / ``title`` JSON field).
	message : str
	    Notification body text.
	priority : str
	    Message priority: ``min``, ``low``, ``default``, ``high``, ``max``
	    (or ``1``--``5``). Falls back to the event's default if empty.
	tags : list[str] | None
	    Tags / emoji short-codes. Merged with event defaults if provided.
	click : str
	    URL to open when the notification is tapped (``Click`` header).
	icon : str
	    URL for the notification icon (JPEG/PNG, ``Icon`` header).
	markdown : bool
	    Set to True if ``message`` contains Markdown formatting.
	attachment_url : str
	    External URL for a file attachment (``Attach`` header).
	attachment_file_path : str
	    Local file to upload as the notification body (sent via ``PUT``).
	attachment_filename : str
	    Override filename for the attachment (``Filename`` header).
	actions : list[dict] | None
	    Up to 3 action buttons. Each dict must follow the ntfy action
	    schema, e.g. ``{"action": "view", "label": "Open", "url": "..."}``.
	delay : str
	    Scheduled delivery (``Delay`` header), e.g. ``"30m"``, ``"tomorrow, 10am"``.
	email : str
	    Forward the notification to this email address (``Email`` header).
	api_key : str
	    Override the default ``NTFY_API_KEY`` for this request.
	timeout : int
	    HTTP request timeout in seconds.
	_async : bool
	    If True (default), the HTTP request is fired in a background thread
	    so it does not block the ComfyUI server's event loop.

	Returns
	-------
	NotificationResult | bool
	    When ``_async=False``, returns a :class:`NotificationResult`.
	    When ``_async=True``, returns ``True`` immediately (the request
	    is dispatched to a daemon thread). Legacy callers that only
	    checked ``bool(result)`` remain compatible either way.
	"""
	cfg = _load_ntfy_config()
	topic = cfg.get("topic", "")
	if not topic:
		if DEBUG_MODE:
			logger.debug("Notification skipped: no ntfy topic configured.")
		return NotificationResult(success=True, skipped=True) if not _async else True

	enabled = cfg.get("enabled_events", [])
	if event_key not in enabled:
		if DEBUG_MODE:
			logger.debug("Notification skipped: event '%s' is not enabled.", event_key)
		return NotificationResult(success=True, skipped=True) if not _async else True

	base_url = cfg.get("base_url", DEFAULT_BASE_URL)
	resolved_key = api_key or NTFY_API_KEY

	# Build the request parameters in a serialisable form so we can hand
	# them off to a background thread without closure pitfalls.
	request_kwargs = _build_request(
		base_url=base_url,
		topic=topic,
		event_key=event_key,
		title=title,
		message=message,
		priority=priority,
		tags=tags,
		click=click,
		icon=icon,
		markdown=markdown,
		attachment_url=attachment_url,
		attachment_file_path=attachment_file_path,
		attachment_filename=attachment_filename,
		actions=actions,
		delay=delay,
		email=email,
		api_key=resolved_key,
		timeout=timeout,
	)

	if _async:
		thread = threading.Thread(
			target=_send_request,
			kwargs=request_kwargs,
			daemon=True,
			name=f"ntfy-{event_key}",
		)
		thread.start()
		return True

	return _send_request(**request_kwargs)


# ---------------------------------------------------------------------------
# Request builder (pure, no I/O)
# ---------------------------------------------------------------------------


def _build_request(
	*,
	base_url: str,
	topic: str,
	event_key: str,
	title: str,
	message: str,
	priority: str,
	tags: Optional[List[str]],
	click: str,
	icon: str,
	markdown: bool,
	attachment_url: str,
	attachment_file_path: str,
	attachment_filename: str,
	actions: Optional[List[dict]],
	delay: str,
	email: str,
	api_key: str,
	timeout: int,
) -> dict:
	"""
	Assemble kwargs for :func:`_send_request`.

	Returns a dict of keyword arguments (url, method, headers, data/files, timeout).
	"""
	url = f"{base_url}/{topic}"
	headers: Dict[str, str] = {}
	method = "POST"
	data: Optional[Union[str, bytes]] = None
	is_file_upload = False

	# --- Authentication ---
	# Reference: https://docs.ntfy.sh/publish/#authentication
	if api_key:
		headers["Authorization"] = f"Bearer {api_key}"

	# --- Title ---
	# Reference: https://docs.ntfy.sh/publish/#message-title
	if title:
		headers["Title"] = title

	# --- Priority ---
	# Reference: https://docs.ntfy.sh/publish/#message-priority
	event_defaults = _EVENT_DEFAULTS.get(event_key, {})
	resolved_priority = priority or event_defaults.get("priority", PRIORITY_DEFAULT)
	if resolved_priority and resolved_priority != PRIORITY_DEFAULT:
		headers["Priority"] = str(resolved_priority)

	# --- Tags ---
	# Reference: https://docs.ntfy.sh/publish/#tags-emojis
	default_tags: List[str] = list(event_defaults.get("tags", []))
	if tags:
		merged_tags = list(dict.fromkeys(default_tags + list(tags)))
	else:
		merged_tags = default_tags
	if merged_tags:
		headers["Tags"] = ",".join(merged_tags)

	# --- Click action ---
	# Reference: https://docs.ntfy.sh/publish/#click-action
	if click:
		headers["Click"] = click

	# --- Icon ---
	# Reference: https://docs.ntfy.sh/publish/#icons
	if icon:
		headers["Icon"] = icon

	# --- Markdown ---
	# Reference: https://docs.ntfy.sh/publish/#markdown-formatting
	if markdown:
		headers["Markdown"] = "yes"

	# --- Scheduled delivery ---
	# Reference: https://docs.ntfy.sh/publish/#scheduled-delivery
	if delay:
		headers["Delay"] = delay

	# --- Email forwarding ---
	# Reference: https://docs.ntfy.sh/publish/#e-mail-notifications
	if email:
		headers["Email"] = email

	# --- Action buttons (up to 3) ---
	# Reference: https://docs.ntfy.sh/publish/#action-buttons
	# When sending via headers, we use the JSON array syntax in the
	# "Actions" header for clarity and to avoid comma-escaping issues.
	if actions and isinstance(actions, list):
		trimmed = actions[:3]
		headers["Actions"] = json.dumps(trimmed)

	# --- Attachments ---
	# Two modes per the ntfy spec:
	#
	# 1. Local file upload: PUT with the file bytes as the request body
	#    and a "Filename" header.
	#    Reference: https://docs.ntfy.sh/publish/#attach-local-file
	#
	# 2. URL attachment: POST with the "Attach" header pointing to an
	#    external URL (the ntfy client downloads it on its own).
	#    Reference: https://docs.ntfy.sh/publish/#attach-file-from-a-url
	if attachment_file_path and os.path.isfile(attachment_file_path):
		method = "PUT"
		is_file_upload = True
		filename = attachment_filename or os.path.basename(attachment_file_path)
		headers["Filename"] = filename

		# If we also have a text message, set it in the "Message" header
		# because the body will be the file bytes.
		if message:
			headers["Message"] = message
	elif attachment_url:
		headers["Attach"] = attachment_url
		if attachment_filename:
			headers["Filename"] = attachment_filename
		data = message.encode("utf-8") if message else None
	else:
		data = message.encode("utf-8") if message else None

	return {
		"url": url,
		"method": method,
		"headers": headers,
		"data": data,
		"attachment_file_path": attachment_file_path if is_file_upload else "",
		"timeout": timeout,
		"event_key": event_key,
		"title": title,
		"message": message,
	}


# ---------------------------------------------------------------------------
# HTTP sender (performs I/O)
# ---------------------------------------------------------------------------


def _send_request(
	*,
	url: str,
	method: str,
	headers: Dict[str, str],
	data: Optional[Union[str, bytes]],
	attachment_file_path: str,
	timeout: int,
	event_key: str,
	title: str,
	message: str,
) -> NotificationResult:
	"""
	Execute the HTTP request to the ntfy server.

	For file uploads (``method="PUT"``), the file at ``attachment_file_path``
	is streamed as the request body so we never load large files entirely
	into memory.
	"""
	try:
		if method == "PUT" and attachment_file_path:
			with open(attachment_file_path, "rb") as fh:
				resp = requests.put(
					url,
					data=fh,
					headers=headers,
					timeout=timeout,
				)
		else:
			resp = requests.post(
				url,
				data=data,
				headers=headers,
				timeout=timeout,
			)

		if resp.status_code == 200:
			if DEBUG_MODE:
				logger.debug(
					"Notification sent [%s]: %s - %s",
					event_key,
					title,
					message[:80] if message else "(no body)",
				)
			return NotificationResult(
				success=True,
				status_code=resp.status_code,
				response_body=resp.text,
			)

		error_msg = (
			f"ntfy responded with HTTP {resp.status_code}: "
			f"{resp.text[:200] if resp.text else '(empty body)'}"
		)
		logger.warning("[mss-login] %s", error_msg)
		return NotificationResult(
			success=False,
			status_code=resp.status_code,
			error=error_msg,
			response_body=resp.text,
		)

	except requests.Timeout:
		error_msg = f"ntfy request timed out after {timeout}s"
		logger.error("[mss-login] %s", error_msg)
		return NotificationResult(success=False, error=error_msg)
	except requests.ConnectionError as exc:
		error_msg = f"ntfy connection error: {exc}"
		logger.error("[mss-login] %s", error_msg)
		return NotificationResult(success=False, error=error_msg)
	except Exception as exc:
		error_msg = f"Failed to send ntfy notification: {exc}"
		logger.error("[mss-login] %s", error_msg)
		return NotificationResult(success=False, error=error_msg)


# ---------------------------------------------------------------------------
# Convenience senders for specific ComfyUI / MSS-Login events
# ---------------------------------------------------------------------------
# These thin wrappers pre-fill the event_key and title so callers
# only need to supply the dynamic parts (username, IP, filename, etc.).
# All extra ``**kwargs`` are forwarded to ``send_notification``.


def notify_user_login(username: str, ip: str, **kwargs) -> Union[NotificationResult, bool]:
	"""Notify that a user logged in."""
	return send_notification(
		"user_login",
		title="MSS-Login: User login",
		message=f"User **{username}** logged in from IP: `{ip}`",
		markdown=True,
		**kwargs,
	)


def notify_user_logout(username: str, ip: str, **kwargs) -> Union[NotificationResult, bool]:
	"""Notify that a user logged out."""
	return send_notification(
		"user_logout",
		title="MSS-Login: User logout",
		message=f"User **{username}** logged out from IP: `{ip}`",
		markdown=True,
		**kwargs,
	)


def notify_login_failure(username: str, ip: str, **kwargs) -> Union[NotificationResult, bool]:
	"""Notify of a failed login attempt."""
	return send_notification(
		"login_failure",
		title="MSS-Login: Login failure",
		message=f"Failed login attempt for user **{username}** from IP: `{ip}`",
		markdown=True,
		**kwargs,
	)


def notify_user_created(
	new_username: str,
	registered_by: str,
	ip: str,
	**kwargs,
) -> Union[NotificationResult, bool]:
	"""Notify that a new user was created."""
	return send_notification(
		"user_created",
		title="MSS-Login: User created",
		message=(f"New user: **{new_username}** (registered by {registered_by}) from IP: `{ip}`"),
		markdown=True,
		**kwargs,
	)


def notify_user_deleted(
	username: str, deleted_by: str, **kwargs
) -> Union[NotificationResult, bool]:
	"""Notify that a user was deleted."""
	return send_notification(
		"user_deleted",
		title="MSS-Login: User deleted",
		message=f"User **{username}** was deleted by **{deleted_by}**.",
		markdown=True,
		**kwargs,
	)


def notify_api_token_created(
	username: str,
	ip: str,
	**kwargs,
) -> Union[NotificationResult, bool]:
	"""Notify that an API token was generated."""
	return send_notification(
		"api_token_created",
		title="MSS-Login: API token created",
		message=f"User **{username}** created an API token from IP: `{ip}`",
		markdown=True,
		**kwargs,
	)


def notify_mfa_enabled(username: str, ip: str, **kwargs) -> Union[NotificationResult, bool]:
	"""Notify that a user enabled MFA."""
	return send_notification(
		"mfa_enabled",
		title="MSS-Login: MFA enabled",
		message=f"User **{username}** enabled MFA from IP: `{ip}`",
		markdown=True,
		**kwargs,
	)


def notify_mfa_disabled(username: str, ip: str, **kwargs) -> Union[NotificationResult, bool]:
	"""Notify that a user disabled MFA."""
	return send_notification(
		"mfa_disabled",
		title="MSS-Login: MFA disabled",
		message=f"User **{username}** disabled MFA from IP: `{ip}`",
		markdown=True,
		**kwargs,
	)


def notify_nsfw_block(
	username: str,
	image_name: str,
	*,
	score: Optional[float] = None,
	cached: bool = False,
	**kwargs,
) -> Union[NotificationResult, bool]:
	"""
	Notify that an NSFW image was blocked.

	Parameters
	----------
	username : str
	    The user who triggered the NSFW check.
	image_name : str
	    Base filename of the blocked image.
	score : float | None
	    NSFW confidence score (0.0 -- 1.0) if available.
	cached : bool
	    True when the result came from the metadata cache.
	"""
	detail = f" (Score: {score:.2f})" if score is not None else ""
	if cached:
		detail += " [cached]"
	return send_notification(
		"nsfw_block",
		title="MSS-Login: NSFW image blocked",
		message=(
			f"User **{username}** attempted to view/generate NSFW content: `{image_name}`{detail}"
		),
		markdown=True,
		**kwargs,
	)


def notify_image_generated(
	username: str,
	image_path: str = "",
	*,
	attachment_url: str = "",
	click: str = "",
	**kwargs,
) -> Union[NotificationResult, bool]:
	"""
	Notify that an image was successfully generated.

	Optionally attaches the generated image (either as a local file upload
	or as a URL the ntfy client can fetch).

	Parameters
	----------
	username : str
	    The user who generated the image.
	image_path : str
	    Local filesystem path to the generated image (uploaded via PUT).
	attachment_url : str
	    Public URL for the generated image (attached via Attach header).
	click : str
	    URL to open when the notification is tapped (e.g. gallery link).
	"""
	filename = os.path.basename(image_path) if image_path else "image"
	return send_notification(
		"image_generated",
		title="MSS-Login: Image generated",
		message=f"User **{username}** generated an image: `{filename}`",
		markdown=True,
		attachment_file_path=image_path,
		attachment_url=attachment_url,
		click=click,
		**kwargs,
	)


def notify_image_generation_failed(
	username: str,
	reason: str = "",
	**kwargs,
) -> Union[NotificationResult, bool]:
	"""Notify that an image generation failed."""
	body = f"Image generation failed for user **{username}**."
	if reason:
		body += f"\nReason: {reason}"
	return send_notification(
		"image_generation_failed",
		title="MSS-Login: Image generation failed",
		message=body,
		markdown=True,
		**kwargs,
	)


def notify_user_role_changed(
	username: str,
	old_role: str,
	new_role: str,
	changed_by: str = "",
	**kwargs,
) -> Union[NotificationResult, bool]:
	"""Notify that a user's role was changed."""
	actor = f" by **{changed_by}**" if changed_by else ""
	return send_notification(
		"user_role_changed",
		title="MSS-Login: Role changed",
		message=(f"User **{username}** role changed from `{old_role}` to `{new_role}`{actor}."),
		markdown=True,
		**kwargs,
	)


def notify_settings_changed(
	changed_by: str,
	setting_name: str = "",
	**kwargs,
) -> Union[NotificationResult, bool]:
	"""Notify that admin settings were changed."""
	detail = f": **{setting_name}**" if setting_name else ""
	return send_notification(
		"settings_changed",
		title="MSS-Login: Settings changed",
		message=f"Settings updated{detail} by **{changed_by}**.",
		markdown=True,
		**kwargs,
	)


def notify_server_started(**kwargs) -> Union[NotificationResult, bool]:
	"""Notify that the ComfyUI server has started."""
	return send_notification(
		"server_started",
		title="MSS-Login: Server started",
		message="The ComfyUI server has started and is ready to accept connections.",
		**kwargs,
	)


def notify_server_stopped(**kwargs) -> Union[NotificationResult, bool]:
	"""Notify that the ComfyUI server has stopped."""
	return send_notification(
		"server_stopped",
		title="MSS-Login: Server stopped",
		message="The ComfyUI server has stopped.",
		**kwargs,
	)


def notify_update_available(
	current_version: str = "",
	new_version: str = "",
	release_url: str = "",
	**kwargs,
) -> Union[NotificationResult, bool]:
	"""Notify that a new plugin update is available."""
	parts = ["A new MSS-Login update is available."]
	if current_version and new_version:
		parts.append(f"Current: `{current_version}` -> New: `{new_version}`")
	return send_notification(
		"update_available",
		title="MSS-Login: Update available",
		message="\n".join(parts),
		markdown=True,
		click=release_url or "",
		**kwargs,
	)


def notify_experimental_recovery(
	*,
	reason: str,
	recovery_action: str,
	failure_count: int,
	occurred_at: str = "",
	details: str = "",
	**kwargs,
) -> Union[NotificationResult, bool]:
	"""Notify when experimental failsafe/recovery is triggered."""
	timestamp = occurred_at or "unknown time"
	guidance = "No action required."
	if recovery_action == "config_reset":
		guidance = "Experimental flags were disabled automatically. Restart ComfyUI and verify stability."
	elif recovery_action == "recovery_update":
		guidance = "Recovery update completed. Validate that all services and auth flows are healthy."
	elif recovery_action == "recovery_update_failed":
		guidance = "Automatic recovery update failed. Review logs and perform controlled manual maintenance."
	body_lines = [
		"Experimental recovery event detected.",
		f"- Time: `{timestamp}`",
		f"- Reason: `{reason or 'unknown'}`",
		f"- Action: `{recovery_action}`",
		f"- Failure count: `{failure_count}`",
		f"- Guidance: {guidance}",
		"- Credential safety: user DB credentials and token stores are preserved by failsafe/reset paths.",
	]
	if details:
		body_lines.append(f"- Details: `{details}`")
	return send_notification(
		"experimental_recovery",
		title="MSS-Login: Experimental recovery triggered",
		message="\n".join(body_lines),
		markdown=True,
		**kwargs,
	)
