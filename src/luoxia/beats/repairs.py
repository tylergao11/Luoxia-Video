from __future__ import annotations

from typing import Any, Dict, List

# Severity says how much of the final episode the harness made up on the model's behalf.
#   low    — bookkeeping only, no narrative impact
#   medium — structure nudged to satisfy craft rules (opening type, forced hook)
#   high   — content invented or destroyed (line synthesized, line cut mid-thought)
SEVERITIES = ("low", "medium", "high")
_RANK = {s: i for i, s in enumerate(SEVERITIES)}


class StrictRepairError(RuntimeError):
    """Raised when automatic repairs exceeded what the caller is willing to accept."""

    def __init__(self, message: str, *, repairs: List[Dict[str, Any]]):
        super().__init__(message)
        self.repairs = repairs


def record(
    doc: Dict[str, Any],
    *,
    code: str,
    severity: str,
    detail: str,
    beat_id: str | None = None,
    actor: str = "harness",
) -> Dict[str, Any]:
    """Append one automatic fix to the ledger.

    Every silent mutation must land here. The contract's value is knowing when the
    model did a bad job — auto-fixing without a trace throws that away.
    """
    if severity not in _RANK:
        raise ValueError(f"unknown severity {severity!r}")
    entry = {
        "code": code,
        "severity": severity,
        "beat_id": beat_id,
        "detail": detail,
        "actor": actor,
    }
    doc.setdefault("repairs", []).append(entry)
    return entry


def summarize(doc: Dict[str, Any]) -> Dict[str, Any]:
    repairs = doc.get("repairs") or []
    counts = {s: 0 for s in SEVERITIES}
    for r in repairs:
        sev = r.get("severity")
        if sev in counts:
            counts[sev] += 1
    worst = None
    for s in SEVERITIES:
        if counts[s]:
            worst = s
    return {
        "repair_count": len(repairs),
        "by_severity": counts,
        "worst_severity": worst,
        "invented_lines": sum(1 for r in repairs if r.get("code") == "line_invented"),
        "truncated_lines": sum(1 for r in repairs if r.get("code") == "line_truncated"),
    }


def enforce(doc: Dict[str, Any], *, max_severity: str = "medium") -> Dict[str, Any]:
    """Gate before spending money. Default tolerates nudges, refuses invented content."""
    if max_severity not in _RANK:
        raise ValueError(f"unknown severity {max_severity!r}")
    quality = summarize(doc)
    doc["quality"] = quality
    worst = quality["worst_severity"]
    if worst and _RANK[worst] > _RANK[max_severity]:
        offenders = [r for r in (doc.get("repairs") or []) if _RANK[r.get("severity", "low")] > _RANK[max_severity]]
        lines = "\n".join(f"  - [{r['severity']}] {r['code']} {r.get('beat_id') or ''}: {r['detail']}" for r in offenders)
        raise StrictRepairError(
            f"{len(offenders)} repair(s) above max_severity={max_severity}; "
            f"the model's output was patched rather than accepted:\n{lines}",
            repairs=offenders,
        )
    return quality
