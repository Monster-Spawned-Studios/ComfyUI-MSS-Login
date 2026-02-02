# --- START OF FILE utils/debug_log.py ---
"""Debug-mode NDJSON logging for diagnosis. No secrets or tokens logged."""
import json
import os
import time

def _debug_write(payload: dict) -> None:
    # #region agent log
    try:
        from ..constants import DEBUG_MODE, DEBUG_LOG_PATH
        if not DEBUG_MODE:
            return
        payload.setdefault("timestamp", int(time.time() * 1000))
        os.makedirs(os.path.dirname(DEBUG_LOG_PATH), exist_ok=True)
        with open(DEBUG_LOG_PATH, "a", encoding="utf-8") as f:
            f.write(json.dumps(payload) + "\n")
    except Exception:
        pass
    # #endregion
