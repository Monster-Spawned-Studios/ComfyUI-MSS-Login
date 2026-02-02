# --- START OF FILE utils/ntfy_notifier.py ---
"""
NTFY.SH push notifications over HTTPS for user actions (login, NSFW block, etc.).
Admin configures topic and enabled event types in settings.
"""
import urllib.request
import urllib.error
import json
from typing import Optional, List

DEFAULT_BASE_URL = "https://ntfy.sh"
EVENT_KEYS = [
    "nsfw_block",
    "user_created",
    "user_login",
    "user_logout",
    "api_token_created",
    "login_failure",
]


def _load_ntfy_config():
    """Load ntfy config from config.json (topic, enabled_events)."""
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


def send_notification(event_key: str, title: str, message: str) -> bool:
    """
    Send a push notification to ntfy if the event is enabled and topic is set.
    Returns True if sent (or skipped because disabled), False on send error.
    """
    cfg = _load_ntfy_config()
    topic = cfg.get("topic")
    if not topic:
        return True
    enabled = cfg.get("enabled_events", [])
    if event_key not in enabled:
        return True
    base_url = cfg.get("base_url", DEFAULT_BASE_URL)
    url = f"{base_url}/{topic}"
    try:
        data = message.encode("utf-8")
        req = urllib.request.Request(
            url,
            data=data,
            headers={
                "Title": title[:250],
                "Content-Type": "text/plain; charset=utf-8",
            },
            method="POST",
        )
        urllib.request.urlopen(req, timeout=10)
        return True
    except Exception:
        return False


def get_ntfy_config() -> dict:
    """Return current ntfy config (topic, enabled_events, base_url) for API."""
    return _load_ntfy_config()


def save_ntfy_config(topic: str, enabled_events: List[str]) -> None:
    """Persist ntfy config to config.json."""
    from ..constants import CONFIG_FILE_PATH
    from ..utils.json_utils import load_json_file, save_json_file
    cfg = load_json_file(CONFIG_FILE_PATH, {})
    if not isinstance(cfg, dict):
        cfg = {}
    cfg["ntfy"] = {
        "topic": (topic or "").strip(),
        "enabled_events": list(enabled_events) if isinstance(enabled_events, list) else [],
        "base_url": DEFAULT_BASE_URL,
    }
    save_json_file(CONFIG_FILE_PATH, cfg)
