from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, List

from src.luoxia.pricing import estimate_shot_video_cost_usd


@dataclass(frozen=True)
class ShotCostLine:
    shot_id: str
    provider: str
    resolution: str
    request_duration_s: int
    has_input_image: bool
    cost_usd: float


@dataclass(frozen=True)
class CostEstimate:
    lines: List[ShotCostLine]
    estimated_usd: float

    def detail_text(self) -> str:
        rows = [
            f"- {line.shot_id}: {line.request_duration_s}s @ {line.resolution} "
            f"via {line.provider} = ${line.cost_usd:.4f}"
            for line in self.lines
        ]
        rows.append(f"TOTAL estimated_usd = ${self.estimated_usd:.4f}")
        return "\n".join(rows)


def estimate_timeline_cost(timeline: Dict[str, Any]) -> CostEstimate:
    resolution = (timeline.get("global") or {}).get("resolution") or "720p"
    lines: List[ShotCostLine] = []
    total = 0.0
    for shot in timeline.get("shots") or []:
        video = shot.get("video") or {}
        still = shot.get("still") or {}
        provider = video.get("provider")
        request = int((shot.get("timing") or {})["request_duration_s"])
        has_image = bool(still.get("local_path") or still.get("asset_id") or (video.get("request") or {}).get("image"))
        cost = estimate_shot_video_cost_usd(
            provider=provider,
            resolution=resolution,
            request_duration_s=request,
            has_input_image=has_image,
        )
        lines.append(
            ShotCostLine(
                shot_id=shot.get("shot_id") or "?",
                provider=str(provider),
                resolution=resolution,
                request_duration_s=request,
                has_input_image=has_image,
                cost_usd=cost,
            )
        )
        total += cost
    return CostEstimate(lines=lines, estimated_usd=round(total, 6))
