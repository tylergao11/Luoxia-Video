"""Generic session persistence (provider-agnostic JSON)."""
from __future__ import annotations

import json
import os
import time
from pathlib import Path
from typing import Any, Dict, Optional

from .config import _user_data_dir


def session_path(provider: str) -> Path:
    safe = "".join(c if c.isalnum() or c in "-_" else "_" for c in (provider or "default"))
    return _user_data_dir() / "sessions" / f"{safe}.json"


def load_session(provider: str) -> Optional[Dict[str, Any]]:
    path = session_path(provider)
    if not path.is_file():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else None
    except Exception:
        return None


def save_session(provider: str, session: Dict[str, Any]) -> Dict[str, Any]:
    path = session_path(provider)
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        **session,
        "provider": provider,
        "updated_at": time.time(),
    }
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
    return payload


def clear_session(provider: str) -> None:
    path = session_path(provider)
    if path.is_file():
        path.unlink()
