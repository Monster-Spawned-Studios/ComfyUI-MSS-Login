"""Simple in-memory per-user rate limiter for model-download endpoints."""

from __future__ import annotations

import time
from collections import defaultdict, deque

# Max requests per user within the sliding window.
DEFAULT_LIMIT = 60
DEFAULT_WINDOW_SECONDS = 60.0

_HITS: dict[str, deque[float]] = defaultdict(deque)


def check_rate_limit(
	user_key: str,
	*,
	limit: int = DEFAULT_LIMIT,
	window_seconds: float = DEFAULT_WINDOW_SECONDS,
	now: float | None = None,
) -> bool:
	"""
	Return True when the request is allowed; False when rate-limited.

	Records the hit when allowed.
	"""
	key = (user_key or "").strip() or "anonymous"
	ts = time.monotonic() if now is None else float(now)
	window_start = ts - float(window_seconds)
	q = _HITS[key]
	while q and q[0] < window_start:
		q.popleft()
	if len(q) >= int(limit):
		return False
	q.append(ts)
	return True


def reset_rate_limits() -> None:
	"""Test helper: clear all counters."""
	_HITS.clear()
