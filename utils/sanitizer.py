import html
import re
import unicodedata

from aiohttp import web
from bleach import clean

from .input_sanitizer import sanitize_password_input

# Fields that must never go through XSS transforms (bcrypt / credential verification).
_PASSWORD_FIELD_NAMES = frozenset(
	{"password", "new_user_password", "old_password", "confirm_password", "current_password"}
)


def is_password_field(key: str | None) -> bool:
	"""Return True if the form/JSON key holds a password credential."""
	if not key or not isinstance(key, str):
		return False
	return key.strip().lower() in _PASSWORD_FIELD_NAMES


def is_json_content_type(content_type: str | None) -> bool:
	"""Return True for application/json and application/*+json."""
	if not content_type:
		return False
	main = content_type.split(";")[0].strip().lower()
	return main == "application/json" or main.endswith("+json")


def apply_legacy_xss_sanitize(value: str) -> str:
	"""
	Apply the historical XSS sanitize_input transforms to a string.

	Used only for dual-verify of passwords that were hashed after middleware
	mutation (pre-fix installs). Do not use for new credential storage.
	"""
	if not isinstance(value, str):
		return ""
	value = value.strip()
	value = unicodedata.normalize("NFC", value)
	value = html.escape(value)
	value = value.replace("\r", "").replace("\n", "")
	value = re.sub(r"([;'\-()<>`=])", r"\\\1", value)
	value = re.sub(r"[;&|`]", "", value)
	value = clean(value, tags=[], attributes=[], protocols=[])

	xss_patterns = [
		r"<script.*?>.*?</script>",
		r"javascript:",
		r"vbscript:",
		r"data:text/html",
		r"data:image",
	]
	for pattern in xss_patterns:
		value = re.sub(pattern, "", value, flags=re.IGNORECASE)
	return value


class Sanitizer:
	@staticmethod
	def sanitize_input(value):
		"""Sanitize user input of various types to prevent security risks."""
		if isinstance(value, str):
			return apply_legacy_xss_sanitize(value)

		elif isinstance(value, (int, float)):
			return value

		elif isinstance(value, (list, dict)):
			return (
				[Sanitizer.sanitize_input(item) for item in value]
				if isinstance(value, list)
				else {key: Sanitizer._sanitize_field(key, val) for key, val in value.items()}
			)

		return value

	@staticmethod
	def _sanitize_field(key, value):
		"""Sanitize one key/value; passwords get control-char strip only."""
		if is_password_field(key) and isinstance(value, str):
			return sanitize_password_input(value)
		return Sanitizer.sanitize_input(value)

	def create_sanitizer_middleware(self) -> web.middleware:
		"""Create middleware to sanitize all request inputs."""

		@web.middleware
		async def sanitizer_middleware(request: web.Request, handler) -> web.Response:
			"""Middleware to sanitize all request inputs."""
			if request.can_read_body:
				try:
					content_type = request.content_type or ""
					if is_json_content_type(content_type):
						# Do not call request.post() — it would consume the JSON body.
						body = await request.json()
						if isinstance(body, dict):
							sanitized_data = {
								key: self._sanitize_field(key, value) for key, value in body.items()
							}
							request["_sanitized_data"] = sanitized_data
					else:
						data = await request.post()
						sanitized_data = {
							key: self._sanitize_field(key, value) for key, value in data.items()
						}
						request["_sanitized_data"] = sanitized_data
				except Exception:
					pass

			if request.query:
				sanitized_query = {
					key: self.sanitize_input(value) for key, value in request.query.items()
				}
				request["_sanitized_query"] = sanitized_query

			return await handler(request)

		return sanitizer_middleware
