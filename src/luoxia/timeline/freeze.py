from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Optional

from src.luoxia.timeline.cost import estimate_timeline_cost
from src.luoxia.timeline.hashing import assert_timeline_hash, compute_timeline_hash
from src.luoxia.timeline.io import save_timeline
from src.luoxia.timeline.validator import validate_timeline


class BudgetExceededError(RuntimeError):
    def __init__(self, message: str, *, detail: str, estimated_usd: float, ceiling_usd: float):
        super().__init__(message)
        self.detail = detail
        self.estimated_usd = estimated_usd
        self.ceiling_usd = ceiling_usd


def freeze_timeline(
    timeline: Dict[str, Any],
    *,
    frozen_path: Optional[Path | str] = None,
    catalog: Optional[Dict[str, Any]] = None,
    actor: str = "agent",
) -> Dict[str, Any]:
    if timeline.get("phase") != "audio_locked":
        raise ValueError(
            f"freeze requires phase=audio_locked (run solver first); got {timeline.get('phase')}"
        )

    validate_timeline(timeline, catalog=catalog)

    estimate = estimate_timeline_cost(timeline)
    cost = timeline.setdefault("cost", {"currency": "USD"})
    cost["currency"] = "USD"
    cost["estimated_usd"] = estimate.estimated_usd

    ceiling = cost.get("budget_ceiling_usd")
    if ceiling is not None and estimate.estimated_usd > float(ceiling):
        raise BudgetExceededError(
            f"estimated ${estimate.estimated_usd:.4f} exceeds budget_ceiling_usd ${float(ceiling):.4f}",
            detail=estimate.detail_text(),
            estimated_usd=estimate.estimated_usd,
            ceiling_usd=float(ceiling),
        )

    timeline["timeline_hash"] = compute_timeline_hash(timeline)
    timeline["frozen_at"] = datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")
    timeline["phase"] = "frozen"
    audit = timeline.setdefault("audit", [])
    audit.append(
        {
            "at": timeline["frozen_at"],
            "actor": actor,
            "action": "freeze",
            "detail": f"estimated_usd={estimate.estimated_usd}; ceiling={ceiling}",
        }
    )

    validate_timeline(timeline, catalog=catalog)

    if frozen_path is not None:
        save_timeline(frozen_path, timeline)
    return timeline


def unfreeze_timeline(timeline: Dict[str, Any], *, actor: str = "agent", reason: str = "") -> Dict[str, Any]:
    if timeline.get("phase") not in {"frozen", "rendering"}:
        raise ValueError(f"cannot unfreeze phase={timeline.get('phase')}")
    timeline["phase"] = "audio_locked"
    timeline["timeline_hash"] = None
    timeline["frozen_at"] = None
    audit = timeline.setdefault("audit", [])
    audit.append(
        {
            "at": datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds"),
            "actor": actor,
            "action": "unfreeze",
            "detail": reason or "explicit unfreeze before timing change",
        }
    )
    return timeline


def assert_writable_for_render(timeline: Dict[str, Any]) -> None:
    """Call before any expensive render write-back."""
    if timeline.get("phase") not in {"frozen", "rendering"}:
        raise ValueError("render requires frozen/rendering phase")
    assert_timeline_hash(timeline)
