"""Configuration management."""
import json
from pathlib import Path

DEFAULT_CONFIG = {
    "port": 8081,
    "host": "0.0.0.0",
    "retry_attempts": 3,
    "retry_delay_sec": 2,
    "request_timeout_sec": 180,
    "gemini_bl": "boq_assistant-bard-web-server_20260925.18_p1",
    "auth_user": None,
    "xsrf_token": None,
    "default_model": "gemini-3.6-flash",
    "log_requests": True,
    "cookie_file": None,
    "proxy": None,
    "api_keys": [],
    "temporary_chats": False,
}

CONFIG = dict(DEFAULT_CONFIG)


def load_config(path: str = None):
    """Load config from JSON file."""
    if path and Path(path).exists():
        CONFIG.update(json.loads(Path(path).read_text()))
    return CONFIG


def find_config():
    """Search for config file in standard locations."""
    for p in [Path("config.json"), Path.home() / ".config/gemini-web2api/config.json"]:
        if p.exists():
            return str(p)
    return None
