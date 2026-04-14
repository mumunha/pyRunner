"""Global configuration management for PyRunner."""
from __future__ import annotations

import os
from pathlib import Path
from typing import Any

try:
    import tomllib
except ImportError:
    import tomli as tomllib  # type: ignore

import tomli_w

PYRUNNER_ROOT = Path(os.environ.get("PYRUNNER_ROOT", Path.home() / "pyrunner"))
CONFIG_PATH = Path(os.environ.get("PYRUNNER_CONFIG", PYRUNNER_ROOT / "config.toml"))

_DEFAULT_CONFIG: dict[str, Any] = {
    "server": {
        "host": "0.0.0.0",
        "port": 8420,
    },
    "git": {
        "poll_interval_minutes": 5,
        "ssh_key_path": str(Path.home() / ".ssh" / "id_ed25519"),
        "parallel_checks": 4,
        "max_retries": 3,
    },
    "supervisor": {
        "config_dir": str(PYRUNNER_ROOT / "supervisor" / "conf.d"),
        "socket": "unix:///tmp/supervisor.sock",
        "xmlrpc_url": "http://localhost:9001/RPC2",
        "username": "",
        "password": "",
    },
    "scheduler": {
        "timezone": "America/Sao_Paulo",
    },
    "logs": {
        "retention_days": 30,
        "max_size_mb": 50,
    },
    "notifications": {
        "deploy_webhook": "",
        "schedule_webhook": "",
    },
}


def _deep_merge(base: dict, override: dict) -> dict:
    result = base.copy()
    for key, value in override.items():
        if key in result and isinstance(result[key], dict) and isinstance(value, dict):
            result[key] = _deep_merge(result[key], value)
        else:
            result[key] = value
    return result


def load_config() -> dict[str, Any]:
    """Load config from file, merging with defaults."""
    if CONFIG_PATH.exists():
        with open(CONFIG_PATH, "rb") as f:
            file_config = tomllib.load(f)
        return _deep_merge(_DEFAULT_CONFIG, file_config)
    return _DEFAULT_CONFIG.copy()


def save_config(config: dict[str, Any]) -> None:
    """Save config to file."""
    CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(CONFIG_PATH, "wb") as f:
        tomli_w.dump(config, f)


def get_projects_dir() -> Path:
    return PYRUNNER_ROOT / "projects"


def get_supervisor_conf_dir() -> Path:
    cfg = load_config()
    return Path(cfg["supervisor"]["config_dir"]).expanduser()


def get_logs_dir() -> Path:
    return PYRUNNER_ROOT / "logs"


def get_db_path() -> Path:
    return PYRUNNER_ROOT / "data" / "pyrunner.db"
