from __future__ import annotations

import hashlib
import json
from typing import Any, Dict, List


def timing_fingerprint(timeline: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Stable subset used for freeze integrity: only timing fields per shot."""
    rows = []
    for shot in timeline.get("shots") or []:
        timing = dict(shot.get("timing") or {})
        rows.append(
            {
                "shot_id": shot.get("shot_id"),
                "index": shot.get("index"),
                "timing_driver": shot.get("timing_driver"),
                "timing": timing,
            }
        )
    return rows


def compute_timeline_hash(timeline: Dict[str, Any]) -> str:
    payload = json.dumps(
        timing_fingerprint(timeline),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    digest = hashlib.sha256(payload.encode("utf-8")).hexdigest()
    return f"sha256:{digest}"


def assert_timeline_hash(timeline: Dict[str, Any]) -> None:
    expected = timeline.get("timeline_hash")
    if not expected:
        raise ValueError("timeline_hash missing; timeline is not frozen")
    actual = compute_timeline_hash(timeline)
    if actual != expected:
        raise ValueError(
            f"timeline_hash mismatch: expected {expected}, got {actual}. "
            "Timing was modified after freeze; unfreeze and re-solve."
        )
