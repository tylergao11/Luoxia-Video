from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict


def load_timeline(path: str | Path) -> Dict[str, Any]:
    p = Path(path)
    with p.open("r", encoding="utf-8") as f:
        data = json.load(f)
    if not isinstance(data, dict):
        raise ValueError(f"timeline must be a JSON object: {p}")
    return data


def save_timeline(path: str | Path, timeline: Dict[str, Any]) -> Path:
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(p.suffix + ".tmp")
    with tmp.open("w", encoding="utf-8", newline="\n") as f:
        json.dump(timeline, f, ensure_ascii=False, indent=2)
        f.write("\n")
    tmp.replace(p)
    return p
