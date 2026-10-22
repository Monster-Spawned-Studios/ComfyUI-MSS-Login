"""
Login / credential-route lockout (AUDIT-013).

- Keyed by (username, IP) with exponential backoff; per-IP and soft per-account caps.
- ~15 minute sliding decay; ~10k LRU; prune on load; debounced atomic writes.
- Never lock out a trusted proxy peer.
- Middleware matches credential routes by path identity (login, register, mfa,
  mfa/verify, generate_token, local-login).
"""

from __future__ import annotations

import json
import os
import threading
import time
from collections import OrderedDict
from datetime import UTC, datetime, timedelta
from typing import Optional

from aiohttp import web

from .ip_filter import IPFilter, get_ip, is_trusted_proxy_peer

# Sliding window: unused entries decay after this many seconds
_DECAY_SECONDS = 15 * 60
# Soft caps
_MAX_ENTRIES = 10_000
_PER_IP_CAP = 50  # failed attempts across all users from one IP before hard timeout
_PER_ACCOUNT_SOFT = 30  # soft per-username cap across IPs
# Debounce persistence writes
_SAVE_DEBOUNCE_SEC = 1.0

# Credential routes enforced by middleware (exact path match, POST unless noted)
CREDENTIAL_LIMITED_PATHS = (
	"/login",
	"/register",
	"/mfa",
	"/mss-login/api/mfa/verify",
	"/api/mss-login/api/mfa/verify",
	"/mss-login/generate_token",
	"/generate_token",
	"/mss-login/api/auth/local-login",
	"/api/mss-login/api/auth/local-login",
)


def _default_timeout_state_path() -> str | None:
	"""Persist login lockout counters under the MSS data dir when available."""
	try:
		from .data_dir import get_data_subdir

		base = get_data_subdir("data")
		os.makedirs(base, exist_ok=True)
		return os.path.join(base, "login_timeout_state.json")
	except Exception:
		return None


def _pair_key(username: str | None, ip: str) -> str:
	user = (username or "").strip().lower() or "*"
	return f"{user}|{ip}"


class Timeout:
	def __init__(
		self,
		ip_filter: IPFilter,
		blacklist_after_attempts: int = 0,
		state_path: str | None = None,
		*,
		decay_seconds: int = _DECAY_SECONDS,
		max_entries: int = _MAX_ENTRIES,
	):
		self.ip_filter = ip_filter
		self.blacklist_after_attempts = blacklist_after_attempts
		self.decay_seconds = decay_seconds
		self.max_entries = max_entries

		# OrderedDict for LRU: key -> {"count": int, "last": float epoch}
		self._failed_attempts: OrderedDict[str, dict] = OrderedDict()
		self._timeout_end_time: dict[str, datetime] = {}
		# Per-IP aggregate counts (for cap)
		self._ip_counts: dict[str, int] = {}
		self._account_counts: dict[str, int] = {}

		if state_path is None:
			self._state_path = _default_timeout_state_path()
		else:
			self._state_path = state_path or None
		self._save_lock = threading.RLock()
		self._save_timer: threading.Timer | None = None
		self._load_state()

	def _touch_lru(self, key: str) -> None:
		if key in self._failed_attempts:
			self._failed_attempts.move_to_end(key)

	def _prune_decayed(self, now: float | None = None) -> None:
		now = now if now is not None else time.time()
		cutoff = now - self.decay_seconds
		stale = [k for k, v in self._failed_attempts.items() if float(v.get("last", 0)) < cutoff]
		for k in stale:
			self._remove_key(k, save=False)
		# Drop expired timeouts
		expired = [k for k, end in self._timeout_end_time.items() if end <= datetime.now(UTC)]
		for k in expired:
			self._timeout_end_time.pop(k, None)

	def _enforce_lru(self) -> None:
		while len(self._failed_attempts) > self.max_entries:
			oldest, _ = self._failed_attempts.popitem(last=False)
			self._timeout_end_time.pop(oldest, None)
			# Rebuild soft aggregates lazily on next access

	def _remove_key(self, key: str, *, save: bool = True) -> None:
		entry = self._failed_attempts.pop(key, None)
		self._timeout_end_time.pop(key, None)
		if entry:
			parts = key.split("|", 1)
			if len(parts) == 2:
				user, ip = parts
				cnt = int(entry.get("count", 0))
				if ip in self._ip_counts:
					self._ip_counts[ip] = max(0, self._ip_counts[ip] - cnt)
				if user != "*" and user in self._account_counts:
					self._account_counts[user] = max(0, self._account_counts[user] - cnt)
		if save:
			self._schedule_save()

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
		now = time.time()
		cutoff = now - self.decay_seconds
		attempts = raw.get("failed_attempts") or {}
		timeouts = raw.get("timeout_end_times") or {}
		# Support legacy per-IP-only format: {"203.0.113.9": 3}
		if attempts and all(isinstance(v, (int, float)) for v in attempts.values()):
			converted = {}
			for ip, count in attempts.items():
				if isinstance(ip, str) and ip.strip():
					converted[_pair_key("*", ip.strip())] = {"count": int(count), "last": now}
			attempts = converted
		if isinstance(attempts, dict):
			for key, meta in attempts.items():
				if not isinstance(key, str) or "|" not in key:
					continue
				if isinstance(meta, dict):
					try:
						n = int(meta.get("count", 0))
						last = float(meta.get("last", now))
					except (TypeError, ValueError):
						continue
				else:
					try:
						n = int(meta)
						last = now
					except (TypeError, ValueError):
						continue
				if n <= 0 or last < cutoff:
					continue
				self._failed_attempts[key] = {"count": n, "last": last}
				user, ip = key.split("|", 1)
				self._ip_counts[ip] = self._ip_counts.get(ip, 0) + n
				if user != "*":
					self._account_counts[user] = self._account_counts.get(user, 0) + n
		if isinstance(timeouts, dict):
			for key, iso in timeouts.items():
				if not isinstance(key, str) or not isinstance(iso, str):
					continue
				# Legacy: key was bare IP
				if "|" not in key:
					key = _pair_key("*", key)
				try:
					end = datetime.fromisoformat(iso)
					if end.tzinfo is None:
						end = end.replace(tzinfo=UTC)
				except Exception:
					continue
				if end > datetime.now(UTC):
					self._timeout_end_time[key] = end
		self._prune_decayed(now)
		self._enforce_lru()

	def _schedule_save(self) -> None:
		with self._save_lock:
			if self._save_timer is not None:
				self._save_timer.cancel()
			self._save_timer = threading.Timer(_SAVE_DEBOUNCE_SEC, self._save_state)
			self._save_timer.daemon = True
			self._save_timer.start()

	def _save_state(self) -> None:
		path = self._state_path
		if not path:
			return
		try:
			self._prune_decayed()
			parent = os.path.dirname(path)
			if parent:
				os.makedirs(parent, exist_ok=True)
			payload = {
				"version": 2,
				"failed_attempts": {
					k: {"count": int(v["count"]), "last": float(v["last"])}
					for k, v in self._failed_attempts.items()
				},
				"timeout_end_times": {
					k: end.astimezone(UTC).isoformat() for k, end in self._timeout_end_time.items()
				},
			}
			tmp = f"{path}.tmp"
			with open(tmp, "w", encoding="utf-8") as f:
				json.dump(payload, f, indent=0, sort_keys=True)
			os.replace(tmp, path)
		except Exception:
			pass

	def get_failed_attempts(self, ip: str, username: str | None = None) -> int:
		"""Get failed attempts for (username, IP). username=None uses wildcard key."""
		self._prune_decayed()
		key = _pair_key(username, ip)
		entry = self._failed_attempts.get(key)
		return int(entry["count"]) if entry else 0

	def add_failed_attempt(self, ip: str, username: str | None = None) -> None:
		"""Record a failed attempt for (username, IP). Never locks out trusted proxy peers."""
		if not ip or is_trusted_proxy_peer(ip):
			return
		try:
			self.ip_filter.load_filter_list()
			if self.ip_filter.is_whitelisted(ip):
				return
		except Exception:
			pass

		now = time.time()
		self._prune_decayed(now)
		key = _pair_key(username, ip)
		entry = self._failed_attempts.get(key)
		if entry:
			entry["count"] = int(entry["count"]) + 1
			entry["last"] = now
			self._touch_lru(key)
		else:
			self._failed_attempts[key] = {"count": 1, "last": now}
		failed_attempts = int(self._failed_attempts[key]["count"])

		user = (username or "").strip().lower() or "*"
		self._ip_counts[ip] = self._ip_counts.get(ip, 0) + 1
		if user != "*":
			self._account_counts[user] = self._account_counts.get(user, 0) + 1

		if self.blacklist_after_attempts and failed_attempts >= self.blacklist_after_attempts:
			try:
				self.ip_filter.add_to_blacklist(ip)
			except Exception:
				pass

		# Exponential backoff based on pair attempts
		timeout_duration = 0
		if failed_attempts >= 9:
			timeout_duration = min(3600, 60 * (2 ** min(failed_attempts - 3, 6)))
		elif failed_attempts >= 6:
			timeout_duration = 90
		elif failed_attempts >= 3:
			timeout_duration = 60

		# Per-IP hard cap
		if self._ip_counts.get(ip, 0) >= _PER_IP_CAP:
			timeout_duration = max(timeout_duration, 300)
		# Soft per-account
		if user != "*" and self._account_counts.get(user, 0) >= _PER_ACCOUNT_SOFT:
			timeout_duration = max(timeout_duration, 120)

		if timeout_duration > 0:
			self._timeout_end_time[key] = datetime.now(UTC) + timedelta(seconds=timeout_duration)
			# Also apply a short IP-wide timeout under wildcard so middleware without username still works
			wild = _pair_key("*", ip)
			self._timeout_end_time[wild] = datetime.now(UTC) + timedelta(seconds=timeout_duration)

		self._enforce_lru()
		self._schedule_save()

	def remove_failed_attempts(self, ip: str, username: str | None = None) -> None:
		"""Clear failed attempts for (username, IP); also clears wildcard IP key."""
		self._remove_key(_pair_key(username, ip), save=False)
		self._remove_key(_pair_key("*", ip), save=True)

	def clear_all_for_user(self, username: str) -> None:
		"""Clear all failed attempts and timeouts associated with a specific username across all IPs."""
		if not username:
			return
		with self._save_lock:
			target = f"{username.strip().lower()}|"
			keys_to_remove = [k for k in list(self._failed_attempts) if k.startswith(target)] + [
				k for k in list(self._timeout_end_time) if k.startswith(target)
			]
			for k in set(keys_to_remove):
				self._remove_key(k, save=False)
			self._schedule_save()

	def get_timeout_end_time(self, ip: str, username: str | None = None) -> datetime | None:
		"""Get the timeout end time for (username, IP), preferring the later of pair/wildcard."""
		pair = self._timeout_end_time.get(_pair_key(username, ip))
		wild = self._timeout_end_time.get(_pair_key("*", ip))
		candidates = [t for t in (pair, wild) if t is not None]
		return max(candidates) if candidates else None

	def check_is_timed_out(self, ip: str, username: str | None = None) -> tuple[bool, int, int]:
		"""Check if (username, IP) is currently timed out."""
		if not ip or is_trusted_proxy_peer(ip):
			return False, 0, 0
		self._prune_decayed()
		timeout_end_time = self.get_timeout_end_time(ip, username)

		if timeout_end_time and datetime.now(UTC) < timeout_end_time:
			remaining_seconds = round((timeout_end_time - datetime.now(UTC)).total_seconds())
			return True, self.get_failed_attempts(ip, username), remaining_seconds

		return False, self.get_failed_attempts(ip, username), 0

	def create_time_out_middleware(self, limited: tuple | None = None) -> web.middleware:
		"""Create middleware for handling timeouts on credential routes."""
		paths = tuple(limited) if limited is not None else CREDENTIAL_LIMITED_PATHS

		@web.middleware
		async def time_out_middleware(request: web.Request, handler) -> web.Response:
			path = request.path
			if path in paths and request.method in ("POST", "PUT"):
				try:
					from .emergency_recovery import check_and_execute_emergency_recovery

					check_and_execute_emergency_recovery(timeout_instance=self)
				except Exception:
					pass

				peer = getattr(request, "remote", "") or ""
				if is_trusted_proxy_peer(peer):
					return await handler(request)

				ip = get_ip(request)
				# Username may be in JSON body; avoid consuming body — use IP/wildcard only here.
				is_timed_out, failed_attempts, remaining_seconds = self.check_is_timed_out(ip)

				if is_timed_out:
					minutes, seconds = divmod(int(remaining_seconds), 60)

					if minutes > 0:
						remaining_time = (
							f"{minutes} minute{'s' if minutes > 1 else ''} and "
							f"{seconds} second{'s' if seconds > 1 else ''}"
						)
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
