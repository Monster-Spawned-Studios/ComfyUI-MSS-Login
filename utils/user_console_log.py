# --- START OF FILE utils/user_console_log.py ---
"""
Per-user console log buffer for isolating JWT and other user-specific messages.
Admin can read all users' logs via API.
"""
from collections import defaultdict
from typing import List
import threading

MAX_LINES_PER_USER = 500
_lock = threading.Lock()
_buffers = defaultdict(list)


def append(username: str, line: str) -> None:
    """Append a log line to the given user's buffer."""
    with _lock:
        buf = _buffers[username]
        buf.append(line)
        if len(buf) > MAX_LINES_PER_USER:
            del buf[: len(buf) - MAX_LINES_PER_USER]


def get_lines(username: str) -> List[str]:
    """Return the log lines for the given user (copy)."""
    with _lock:
        return list(_buffers.get(username, []))


def list_users() -> List[str]:
    """Return list of usernames that have at least one log line (for Admin)."""
    with _lock:
        return [u for u, buf in _buffers.items() if buf]
