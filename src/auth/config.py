"""Entry-layer auth configuration (mode + provider id).

Switching vendors later = change AUTH_PROVIDER / adapter registration,
not rewrite beats/timeline.
"""
from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Dict, Literal, Optional

# session = subscription-pool / OAuth session (default product path)
# api_key = long-lived vendor keys in env (alternate)
# offline = no cloud generation; still-hold / local only
AuthMode = Literal["session", "api_key", "offline"]

# Default pool provider id — swap via config without code forks in pipeline.
DEFAULT_AUTH_PROVIDER = "xai_pool"

_ENV_MODE = "LUOXIA_AUTH_MODE"
_ENV_PROVIDER = "LUOXIA_AUTH_PROVIDER"


def _user_data_dir() -> Path:
    override = os.getenv("LUOXIA_DATA_DIR") or os.getenv("LUMENX_DATA_DIR")
    if override:
        return Path(override)
    # Align with desktop packaging home when present.
    home = Path.home()
    for name in (".luoxia-video", ".lumen-x"):
        p = home / name
        if p.is_dir():
            return p
    return home / ".luoxia-video"


def auth_config_path() -> Path:
    return _user_data_dir() / "auth_config.json"


@dataclass
class AuthConfig:
    mode: AuthMode = "session"
    provider: str = DEFAULT_AUTH_PROVIDER

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


def load_auth_config() -> AuthConfig:
    """Resolve config: env overrides file; defaults to session + default pool provider."""
    mode: AuthMode = "session"
    provider = DEFAULT_AUTH_PROVIDER

    path = auth_config_path()
    if path.is_file():
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            if data.get("mode") in ("session", "api_key", "offline"):
                mode = data["mode"]
            if data.get("provider"):
                provider = str(data["provider"]).strip() or provider
        except Exception:
            pass

    env_mode = (os.getenv(_ENV_MODE) or "").strip().lower()
    if env_mode in ("session", "api_key", "offline"):
        mode = env_mode  # type: ignore[assignment]
    env_provider = (os.getenv(_ENV_PROVIDER) or "").strip()
    if env_provider:
        provider = env_provider

    return AuthConfig(mode=mode, provider=provider)


def save_auth_config(config: AuthConfig) -> AuthConfig:
    path = auth_config_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(config.to_dict(), indent=2), encoding="utf-8")
    # Do not force os.environ here — env is operator override and must not
    # leak across tests or stick after UI switches modes in-process incorrectly.
    # Clear sticky overrides when saving so file becomes source of truth.
    os.environ.pop(_ENV_MODE, None)
    os.environ.pop(_ENV_PROVIDER, None)
    return config
