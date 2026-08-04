from __future__ import annotations

import os
from pathlib import Path
from typing import Dict

_LOADED = False


def parse_env_file(path: Path) -> Dict[str, str]:
    """Minimal `.env` reader: `KEY=VALUE`, `#` comments, optional quotes and `export`.

    Deliberately dependency-free. Relying on python-dotenv here would mean a missing dev
    dependency silently reproduces the exact failure this module exists to prevent.
    """
    values: Dict[str, str] = {}
    if not path.is_file():
        return values

    for raw in path.read_text(encoding="utf-8-sig").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.removeprefix("export ").strip()
        if not key:
            continue
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
            value = value[1:-1]
        values[key] = value
    return values


def load_env_once(root: Path | None = None) -> Dict[str, str]:
    """Load `.env` for entrypoints that run outside the FastAPI app.

    Only `src/config.py` called `load_dotenv()`, and nothing under `src/luoxia` imports
    it, so every CLI/pipeline run started blind to `DASHSCOPE_API_KEY` even when `.env`
    had it — which is how one run "concluded" TTS was unavailable and substituted a
    generated tone for an entire episode.

    Existing environment variables win, so an explicit export still overrides the file.
    """
    global _LOADED
    if _LOADED and root is None:
        return {}

    base = root or Path(__file__).resolve().parents[2]
    values = parse_env_file(base / ".env")
    for key, value in values.items():
        os.environ.setdefault(key, value)
    if root is None:
        _LOADED = True
    return values
