# --- START OF FILE utils/debug_log.py ---
"""Debug-mode NDJSON logging for diagnosis. No secrets or tokens logged.
When DEBUG_MODE_FROM_ENV is true, also prints to ComfyUI stdout/console."""

import json
import os
import time


def debug_write(payload: dict) -> None:
    """Write a debug payload to file when DEBUG_MODE, and to stdout when DEBUG_MODE_FROM_ENV."""
    try:
        from ..constants import DEBUG_MODE, DEBUG_MODE_FROM_ENV, DEBUG_LOG_PATH

        if not DEBUG_MODE:
            return
        payload = dict(payload)
        payload.setdefault("timestamp", int(time.time() * 1000))
        line = json.dumps(payload)
        os.makedirs(os.path.dirname(DEBUG_LOG_PATH), exist_ok=True)
        with open(DEBUG_LOG_PATH, "a", encoding="utf-8") as f:
            f.write(line + "\n")
        if DEBUG_MODE_FROM_ENV:
            print(f"[mss-login::DEBUG] {line}", flush=True)
    except Exception:
        pass
