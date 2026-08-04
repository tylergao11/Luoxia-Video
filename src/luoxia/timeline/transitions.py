from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, List, Tuple

CUT = "cut"
DISSOLVE = "dissolve"
KINDS = (CUT, "fade_black", "fade_white", DISSOLVE)

# kind -> ffmpeg fade colour
FADE_COLORS = {"fade_black": "black", "fade_white": "white"}


def transition_of(shot: Dict[str, Any]) -> Tuple[str, float]:
    """(kind, duration_s) for the transition leaving this shot. Absent means hard cut."""
    transition = shot.get("transition") or {}
    kind = str(transition.get("kind") or CUT)
    duration = float(transition.get("duration_s") or 0.0)
    if kind == CUT:
        return CUT, 0.0
    return kind, duration


def has_speech(shot: Dict[str, Any]) -> bool:
    measured = (shot.get("audio") or {}).get("measured_duration_s")
    return measured is not None and float(measured) > 0


def target_of(shot: Dict[str, Any]) -> float:
    return float((shot.get("timing") or {}).get("target_duration_s") or 0.0)


def head_room_s(shot: Dict[str, Any]) -> float:
    """Picture time before the first spoken word. A silent shot is all breathing room."""
    if not has_speech(shot):
        return target_of(shot)
    return float((shot.get("timing") or {}).get("lead_in_s") or 0.0)


def tail_room_s(shot: Dict[str, Any]) -> float:
    """Picture time after the last spoken word."""
    if not has_speech(shot):
        return target_of(shot)
    return float((shot.get("timing") or {}).get("tail_out_s") or 0.0)


@dataclass
class SegmentPlan:
    """How one shot becomes one rendered segment, and how it joins the previous one."""

    shot: Dict[str, Any]
    duration_s: float
    fade_in_s: float = 0.0
    fade_in_color: str = "black"
    fade_out_s: float = 0.0
    fade_out_color: str = "black"
    extend_s: float = 0.0
    dissolve_in_s: float = 0.0

    @property
    def shot_id(self) -> str:
        return str(self.shot.get("shot_id"))

    @property
    def segment_duration_s(self) -> float:
        """Video length of the segment file. Audio always stays at duration_s."""
        return self.duration_s + self.extend_s


def plan_segments(timeline: Dict[str, Any]) -> List[SegmentPlan]:
    """Translate shots[].transition into per-segment render instructions.

    Every transition is paid for out of breathing room or out of the slack frames trim
    would have thrown away, so the sum of segment_duration_s minus the dissolve overlaps
    still equals the sum of target_duration_s. The master clock never moves.
    """
    shots = timeline.get("shots") or []
    plans = [SegmentPlan(shot=shot, duration_s=target_of(shot)) for shot in shots]

    for i, shot in enumerate(shots):
        kind, duration = transition_of(shot)
        if kind == CUT or duration <= 0:
            continue
        nxt = plans[i + 1] if i + 1 < len(plans) else None

        if kind == DISSOLVE:
            if nxt is None:
                # Nothing to dissolve into; degrade to a hard cut.
                continue
            allowed = min(duration, head_room_s(shots[i + 1]))
            if allowed <= 0:
                continue
            plans[i].extend_s = allowed
            nxt.dissolve_in_s = allowed
            continue

        color = FADE_COLORS.get(kind, "black")
        out_allowed = min(duration, tail_room_s(shot))
        if out_allowed > 0:
            plans[i].fade_out_s = out_allowed
            plans[i].fade_out_color = color
        if nxt is not None:
            in_allowed = min(duration, head_room_s(shots[i + 1]))
            if in_allowed > 0:
                nxt.fade_in_s = in_allowed
                nxt.fade_in_color = color

    return plans


def needs_filter_graph(plans: List[SegmentPlan]) -> bool:
    """Dissolves overlap segments, so they cannot go through the stream-copy concat."""
    return any(p.dissolve_in_s > 0 for p in plans)


def total_duration_s(plans: List[SegmentPlan]) -> float:
    return sum(p.segment_duration_s for p in plans) - sum(p.dissolve_in_s for p in plans)
