"""Runtime configuration resolved from a config file and environment variables.

Precedence, highest first:

1. an explicit command-line override (``--offline``, ``--no-cache`` …)
2. environment variables
3. ``.keypulse.json`` in the current directory, then ``.keypulse.json`` at the
   git repo root

The file exists mainly so tests and CI can pin behaviour without exporting
variables everywhere; it also lets a project commit "always offline" settings.
All settings are optional.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from pathlib import Path

DEFAULT_CACHE_DIR = Path.home() / ".cache" / "keypulse"
CONFIG_FILENAME = ".keypulse.json"


def _find_config_file() -> dict:
    """Read the nearest ``.keypulse.json``; empty when there is none."""
    candidates = [Path.cwd() / CONFIG_FILENAME]
    for parent in [Path.cwd(), *Path.cwd().parents]:
        if (parent / ".git").exists():
            candidates.append(parent / CONFIG_FILENAME)
            break
    for path in candidates:
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if isinstance(payload, dict):
            return payload
    return {}


_FILE_CONFIG: dict | None = None


def _file_config() -> dict:
    global _FILE_CONFIG
    if _FILE_CONFIG is None:
        _FILE_CONFIG = _find_config_file()
    return _FILE_CONFIG


def _env_float(name: str, default: float) -> float:
    raw = os.environ.get(name)
    if not raw:
        return default
    try:
        return float(raw)
    except ValueError:
        return default


def _env_int(name: str, default: int) -> int:
    raw = os.environ.get(name)
    if not raw:
        return default
    try:
        return int(raw)
    except ValueError:
        return default


def _lookup(key: str, env_name: str, default):
    """env var → config file → default, in that order."""
    env = os.environ.get(env_name)
    if env not in (None, ""):
        return env
    config = _file_config()
    if key in config:
        return config[key]
    return default


def _as_bool(value) -> bool:
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() in {"1", "true", "yes", "on"}


@dataclass
class Settings:
    """Process-wide knobs, overridable per CLI invocation."""

    cache_dir: Path = field(
        default_factory=lambda: Path(_lookup("cache_dir", "KEYPULSE_CACHE_DIR", DEFAULT_CACHE_DIR))
    )
    cache_ttl: int = field(
        default_factory=lambda: int(_lookup("cache_ttl", "KEYPULSE_CACHE_TTL", 6 * 3600))
    )
    timeout: float = field(
        default_factory=lambda: float(_lookup("timeout", "KEYPULSE_TIMEOUT", 15.0))
    )
    retries: int = field(
        default_factory=lambda: int(_lookup("retries", "KEYPULSE_RETRIES", 2))
    )
    workers: int = field(
        default_factory=lambda: int(_lookup("workers", "KEYPULSE_WORKERS", 4))
    )
    # Minimum spacing between requests to the same host, in seconds.
    host_interval: float = field(
        default_factory=lambda: float(_lookup("host_interval", "KEYPULSE_HOST_INTERVAL", 0.8))
    )
    offline: bool = field(
        default_factory=lambda: _as_bool(_lookup("offline", "KEYPULSE_OFFLINE", False))
    )
    # Optional cookies for providers that gate on a logged-in session.
    baidu_cookie: str = field(
        default_factory=lambda: str(_lookup("baidu_cookie", "KEYPULSE_BAIDU_COOKIE", ""))
    )
    github_token: str = field(
        default_factory=lambda: str(_lookup("github_token", "GITHUB_TOKEN", ""))
    )
    # Route requests through a proxy, e.g. "http://127.0.0.1:7897".  Google
    # Trends rate-limits by IP, so a proxy with a different egress is the
    # practical fix; urllib otherwise follows the standard *_proxy variables.
    proxy: str = field(
        default_factory=lambda: str(_lookup("proxy", "KEYPULSE_PROXY", ""))
    )

    def __post_init__(self) -> None:
        # A cookie stored by ``keypulse login`` is a fallback for the
        # environment variable, never an override of it.
        if not self.baidu_cookie:
            try:
                from .login import load_stored_cookie

                self.baidu_cookie = load_stored_cookie(self)
            except Exception:  # noqa: BLE001 - never fail startup over credentials
                self.baidu_cookie = ""

    def ensure_cache_dir(self) -> Path:
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        return self.cache_dir
