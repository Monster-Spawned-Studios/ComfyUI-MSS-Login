"""Anti-bot detection, honeypot validation, and loading buffer configuration.

Provides:
- Honeypot field validation on interactive authentication forms (hp_auth_shield).
- Loading screen lobby buffer duration clamping (minimum 15 seconds, up to 3600 seconds).
- Scanner / automated attack user-agent detection.
- Structured bot telemetry recording.
- Loopback and API client exemptions (Krita, Comfy Portal, embedded convert).
- Gated by experimental_anti_bot_enabled() with a clean path to permanent core enablement.
"""

from __future__ import annotations

import ipaddress
import logging
import re
from typing import Any

logger = logging.getLogger("mss_login.anti_bot")

# Field name used for the invisible honeypot trap
HONEYPOT_FIELD_NAME = "hp_auth_shield"

# Lobby / Queue duration bounds (seconds)
MIN_LOADING_TIMEOUT_SECONDS = 15
MAX_LOADING_TIMEOUT_SECONDS = 3600
DEFAULT_LOADING_TIMEOUT_SECONDS = 30

# Minimum human interaction time threshold (milliseconds)
MIN_HUMAN_REACTION_MS = 800

# Aggressive automated scanner / penetration testing tool user-agents
_AUTOMATED_TOOL_PATTERNS = (
	re.compile(
		r"\b(sqlmap|nikto|masscan|dirbuster|nmap|scrapy|zgrab|censys|shodan|wpscan|gobuster)\b",
		re.IGNORECASE,
	),
)

_LOOPBACK_IPS = frozenset({"127.0.0.1", "::1", "localhost", "testclient"})


def is_anti_bot_active() -> bool:
	"""Return True if anti-bot protections are currently active.

	In Phase 1, delegates to the experimental toggle `experimental_anti_bot_enabled()`.
	In future phases, this can be transitioned to permanently return True for silent core protection.
	"""
	try:
		from ..constants import experimental_anti_bot_enabled

		return bool(experimental_anti_bot_enabled())
	except Exception:
		try:
			from constants import experimental_anti_bot_enabled  # type: ignore

			return bool(experimental_anti_bot_enabled())
		except Exception:
			return False


def clamp_loading_timeout(val: Any) -> int:
	"""Validate and clamp loading screen buffer duration between 15s and 3600s.

	Returns DEFAULT_LOADING_TIMEOUT_SECONDS (30) on invalid or missing values.
	"""
	if val is None:
		return DEFAULT_LOADING_TIMEOUT_SECONDS
	try:
		num = int(val)
	except (TypeError, ValueError):
		return DEFAULT_LOADING_TIMEOUT_SECONDS

	if num < MIN_LOADING_TIMEOUT_SECONDS:
		return MIN_LOADING_TIMEOUT_SECONDS
	if num > MAX_LOADING_TIMEOUT_SECONDS:
		return MAX_LOADING_TIMEOUT_SECONDS
	return num


def is_honeypot_triggered(data: dict[str, Any] | None) -> bool:
	"""Check if the honeypot field is present and filled.

	Returns False if data is None, or if hp_auth_shield is absent or empty.
	Returns True ONLY if hp_auth_shield contains a non-empty string or value.
	This ensures clean JSON API payloads from Krita AI Diffusion or Comfy Portal
	(which omit the field) are NEVER falsely flagged.
	"""
	if not data or not isinstance(data, dict):
		return False
	val = data.get(HONEYPOT_FIELD_NAME)
	if val is None:
		return False
	if isinstance(val, str):
		return bool(val.strip())
	return bool(val)


def is_automated_user_agent(user_agent: str | None) -> tuple[bool, str]:
	"""Check if a User-Agent matches known aggressive scraping or attack tools.

	Returns (True, tool_name) if matched, else (False, "").
	"""
	if not user_agent:
		return False, ""
	ua = str(user_agent).strip()
	for pattern in _AUTOMATED_TOOL_PATTERNS:
		match = pattern.search(ua)
		if match:
			return True, match.group(1).lower()
	return False, ""


def is_loopback_ip(ip: str | None) -> bool:
	"""Return True if IP is a local loopback address (e.g. internal embedded Playwright pool)."""
	if not ip:
		return False
	clean_ip = str(ip).strip().lower()
	if clean_ip in _LOOPBACK_IPS or clean_ip.startswith("127."):
		return True
	try:
		return ipaddress.ip_address(clean_ip).is_loopback
	except ValueError:
		return False


def record_bot_detection(ip: str, reason: str, details: dict[str, Any] | None = None) -> None:
	"""Record a structured security warning for detected bot activity."""
	extra = details or {}
	logger.warning(
		f"[anti-bot] Bot activity detected from IP={ip} reason='{reason}' details={extra}"
	)
