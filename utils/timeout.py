import json
import os
from datetime import UTC, datetime, timedelta
from typing import Optional

from aiohttp import web

from .ip_filter import IPFilter, get_ip


def _default_timeout_state_path() -> str | None:
	"""Persist login lockout counters under the MSS data dir when available."""
	try:
		from .data_dir import get_data_subdir

		base = get_data_subdir("data")
		os.makedirs(base, exist_ok=True)
		return os.path.join(base, "login_timeout_state.json")
	except Exception:
		return None


class Timeout:
	def __init__(
		self, ip_filter: IPFilter, blacklist_after_attempts: int = 0, state_path: str | None = None
	):
		self.ip_filter = ip_filter
		self.blacklist_after_attempts = blacklist_after_attempts

		self._failed_attempts_ip: dict[str, int] = {}
		self._timeout_end_time_ip: dict[str, datetime] = {}
		# AUDIT-013: durable lockout state across process restarts (optional path for tests).
		if state_path is None:
			self._state_path = _default_timeout_state_path()
		else:
			self._state_path = state_path or None
		self._load_state()

	def _load_state(self) -> None:
		path = self._state_path
		if not path or not os.path.isfile(path):
			return
		try:
			with open(path, encoding="utf-8") as f:
				raw = json.load(f)
		except Exception:
			return
		if not isinstance(raw, dict):
			return
		attempts = raw.get("failed_attempts") or {}
		timeouts = raw.get("timeout_end_times") or {}
		now = datetime.now(UTC)
		if isinstance(attempts, dict):
			for ip, count in attempts.items():
				try:
					n = int(count)
				except (TypeError, ValueError):
					continue
				if n > 0 and isinstance(ip, str) and ip.strip():
					self._failed_attempts_ip[ip.strip()] = n
		if isinstance(timeouts, dict):
			for ip, iso in timeouts.items():
				if not isinstance(ip, str) or not ip.strip() or not isinstance(iso, str):
					continue
				try:
					end = datetime.fromisoformat(iso)
					if end.tzinfo is None:
						end = end.replace(tzinfo=UTC)
				except Exception:
					continue
				if end > now:
					self._timeout_end_time_ip[ip.strip()] = end

	def _save_state(self) -> None:
		path = self._state_path
		if not path:
			return
		try:
			parent = os.path.dirname(path)
			if parent:
				os.makedirs(parent, exist_ok=True)
			payload = {
				"failed_attempts": dict(self._failed_attempts_ip),
				"timeout_end_times": {
					ip: end.astimezone(UTC).isoformat()
					for ip, end in self._timeout_end_time_ip.items()
				},
			}
			tmp = f"{path}.tmp"
			with open(tmp, "w", encoding="utf-8") as f:
				json.dump(payload, f, indent=0, sort_keys=True)
			os.replace(tmp, path)
		except Exception:
			pass

	def get_failed_attempts(self, ip: str) -> int:
		"""Get the number of failed attempts for a given IP."""
		return self._failed_attempts_ip.get(ip, 0)

	def add_failed_attempt(self, ip: str) -> None:
		"""Add a failed attempt for a given IP and set timeout or blacklist IP if necessary."""
		self.ip_filter.load_filter_list()
		if self.ip_filter.is_whitelisted(ip):
			return

		self._failed_attempts_ip[ip] = self._failed_attempts_ip.get(ip, 0) + 1
		failed_attempts = self._failed_attempts_ip[ip]

		if not self.blacklist_after_attempts == 0:
			if failed_attempts >= self.blacklist_after_attempts:
				self.ip_filter.add_to_blacklist(ip)

		timeout_duration = 0
		if failed_attempts >= 9:
			timeout_duration = 300
		elif failed_attempts >= 6:
			timeout_duration = 90
		elif failed_attempts >= 3:
			timeout_duration = 60

		if timeout_duration > 0:
			self._timeout_end_time_ip[ip] = datetime.now(UTC) + timedelta(seconds=timeout_duration)

		self._save_state()

	def remove_failed_attempts(self, ip: str) -> None:
		"""Remove failed attempts and timeout for a given IP."""
		self._failed_attempts_ip.pop(ip, None)
		self._timeout_end_time_ip.pop(ip, None)
		self._save_state()

	def get_timeout_end_time(self, ip: str) -> datetime | None:
		"""Get the timeout end time for a given IP."""
		return self._timeout_end_time_ip.get(ip)

	def check_is_timed_out(self, ip: str) -> tuple[bool, int, int]:
		"""Check if a given IP is currently timed out."""
		timeout_end_time = self.get_timeout_end_time(ip)

		if timeout_end_time and datetime.now(UTC) < timeout_end_time:
			remaining_seconds = round((timeout_end_time - datetime.now(UTC)).total_seconds())
			return True, self.get_failed_attempts(ip), remaining_seconds

		return False, self.get_failed_attempts(ip), 0

	def create_time_out_middleware(self, limited: tuple = ()) -> web.middleware:
		"""Create middleware for handling timeouts."""

		@web.middleware
		async def time_out_middleware(request: web.Request, handler) -> web.Response:
			"""Middleware to handle request timeouts."""
			if request.path in limited and request.method == "POST":
				is_timed_out, failed_attempts, remaining_seconds = self.check_is_timed_out(
					get_ip(request)
				)

				if is_timed_out:
					minutes, seconds = divmod(int(remaining_seconds), 60)

					if minutes > 0:
						remaining_time = f"{minutes} minute{'s' if minutes > 1 else ''} and {seconds} second{'s' if seconds > 1 else ''}"
					else:
						remaining_time = f"{seconds} second{'s' if seconds > 1 else ''}"

					return web.json_response(
						{
							"error": f"Too many failed attempts. Please wait {remaining_time}",
							"failed_attempts": failed_attempts,
							"remaining_seconds": remaining_seconds,
						},
						status=403,
					)

			return await handler(request)

		return time_out_middleware
