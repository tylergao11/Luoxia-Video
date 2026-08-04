"""Pixel geometry implied by the timeline's global.aspect_ratio + resolution.

Stills and subtitles both need the same answer: a still is the clip's first frame, so
generating it at a different size than the frame means the provider rescales it.
"""
from __future__ import annotations

from typing import Any, Dict, Tuple

_ASPECT_WH = {
    "16:9": (16, 9),
    "9:16": (9, 16),
    "4:3": (4, 3),
    "3:4": (3, 4),
    "1:1": (1, 1),
    "21:9": (21, 9),
}


def frame_size(timeline: Dict[str, Any]) -> Tuple[int, int]:
    """(width, height) implied by global.aspect_ratio + resolution.

    For measuring an existing file, probe it instead: a provider can hand back a clip
    that ignores the requested size.
    """
    g = timeline.get("global") or {}
    label = str(g.get("resolution") or "1080p")
    try:
        base = int(label.rstrip("pP") or 1080)
    except ValueError:
        base = 1080
    w, h = _ASPECT_WH.get(str(g.get("aspect_ratio") or "16:9"), (16, 9))
    scale = base / min(w, h)
    # h.264 needs even dimensions.
    return int(round(w * scale / 2)) * 2, int(round(h * scale / 2)) * 2
