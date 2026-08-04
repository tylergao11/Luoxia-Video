from __future__ import annotations

import hashlib
import json
from typing import Any, Dict, List


def selection_fingerprint(beats_doc: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Stable subset used for selection integrity: only what determines 取舍与成片文本."""
    rows = []
    for beat in beats_doc.get("beats") or []:
        span = beat.get("source_span") or {}
        rows.append(
            {
                "beat_id": beat.get("beat_id"),
                "index": beat.get("index"),
                "decision": beat.get("decision"),
                "start_char": span.get("start_char"),
                "end_char": span.get("end_char"),
                "lines": [
                    {"character_id": ln.get("character_id"), "text": ln.get("text")}
                    for ln in (beat.get("lines") or [])
                ],
            }
        )
    return rows


def compute_beats_hash(beats_doc: Dict[str, Any]) -> str:
    payload = json.dumps(
        selection_fingerprint(beats_doc),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    digest = hashlib.sha256(payload.encode("utf-8")).hexdigest()
    return f"sha256:{digest}"


def assert_beats_hash(beats_doc: Dict[str, Any]) -> None:
    expected = beats_doc.get("beats_hash")
    if not expected:
        raise ValueError("beats_hash missing; selection is not locked")
    actual = compute_beats_hash(beats_doc)
    if actual != expected:
        raise ValueError(
            f"beats_hash mismatch: expected {expected}, got {actual}. "
            "Selection was modified after delivery; re-run select."
        )
