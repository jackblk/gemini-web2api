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
    """Load config from JSON file. A relative cookie_file resolves against the config's folder."""
    if path and (config_path := Path(path)).exists():
        data = json.loads(config_path.read_text())
        cookie_file = data.get("cookie_file")
        if cookie_file and not Path(cookie_file).is_absolute():
            data["cookie_file"] = str((config_path.parent / cookie_file).resolve())
        CONFIG.update(data)
    return CONFIG


def find_config():
    """Search for config file in standard locations."""
    for p in [Path("config.json"), Path.home() / ".config/gemini-web2api/config.json"]:
        if p.exists():
            return str(p)
    return None
