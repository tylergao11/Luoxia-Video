from __future__ import annotations

import math
import re
from dataclasses import dataclass
from typing import Any, Callable, Dict, List, Optional, Tuple

from src.luoxia.catalog_limits import find_shot_video_model, resolve_video_duration_bounds

RewriteFn = Callable[[str, float, Dict[str, Any]], str]
SynthesizeFn = Callable[[Dict[str, Any], float], Tuple[float, str, str]]
# synthesize(shot, speed) -> (measured_duration_s, local_path, sha256)


class SolverError(RuntimeError):
    pass


@dataclass
class ProviderWindow:
    min_s: int
    max_s: int


def solve_timeline(
    timeline: Dict[str, Any],
    *,
    synthesize: Optional[SynthesizeFn] = None,
    rewrite: Optional[RewriteFn] = None,
    catalog: Optional[Dict[str, Any]] = None,
    planned_durations: Optional[Dict[str, float]] = None,
) -> Dict[str, Any]:
    """Resolve audio/timing for all shots. Advances master clock by target, not request."""
    if timeline.get("phase") in {"frozen", "rendering", "rendered"}:
        raise SolverError("cannot solve a frozen timeline; unfreeze first")

    g = timeline["global"]
    lead_default = float(g.get("lead_in_s", 0.3))
    tail_default = float(g.get("tail_out_s", 0.5))
    min_speed = float(g["min_speed_ratio"])
    max_speed = float(g["max_speed_ratio"])
    default_action = float(g.get("default_action_duration_s", 4))
    planned = planned_durations or {}

    # Expand splits iteratively until stable.
    for _ in range(32):
        expanded = False
        new_shots: List[Dict[str, Any]] = []
        for shot in list(timeline["shots"]):
            model = find_shot_video_model(shot, timeline)
            if not model:
                raise SolverError(f"shot {shot.get('shot_id')} missing video.model for duration bounds")
            pmin, pmax = resolve_video_duration_bounds(model, catalog=catalog)
            window = ProviderWindow(pmin, pmax)

            driver = shot.get("timing_driver")
            if driver == "rhythm":
                _solve_rhythm(shot, default_action=default_action, lead=lead_default, tail=tail_default, window=window)
                new_shots.append(shot)
            elif driver == "pinned":
                if synthesize is None:
                    raise SolverError("synthesize callback required for pinned shots")
                _solve_pinned(
                    shot,
                    synthesize=synthesize,
                    rewrite=rewrite,
                    lead=lead_default,
                    tail=tail_default,
                    min_speed=min_speed,
                    max_speed=max_speed,
                    window=window,
                )
                new_shots.append(shot)
            elif driver == "audio":
                if synthesize is None:
                    # Allow dry-run when audio already rendered.
                    if (shot.get("audio") or {}).get("status") != "rendered":
                        raise SolverError("synthesize callback required for audio-driven shots")
                parts = _solve_audio(
                    shot,
                    synthesize=synthesize,
                    rewrite=rewrite,
                    lead=lead_default,
                    tail=tail_default,
                    min_speed=min_speed,
                    max_speed=max_speed,
                    window=window,
                    planned=planned.get(shot["shot_id"]),
                )
                if len(parts) > 1:
                    expanded = True
                new_shots.extend(parts)
            else:
                raise SolverError(f"unknown timing_driver: {driver}")
        timeline["shots"] = new_shots
        if not expanded:
            break
    else:
        raise SolverError("shot split did not converge")

    _layout_master_clock(timeline)
    _reindex(timeline)
    timeline["phase"] = "audio_locked"
    return timeline


def _solve_rhythm(
    shot: Dict[str, Any],
    *,
    default_action: float,
    lead: float,
    tail: float,
    window: ProviderWindow,
) -> None:
    timing = shot.setdefault("timing", {})
    target = float(timing.get("target_duration_s") or default_action)
    _quantize(timing, target=target, lead=lead, tail=tail, window=window, measured=None, branch="none")


def _solve_pinned(
    shot: Dict[str, Any],
    *,
    synthesize: SynthesizeFn,
    rewrite: Optional[RewriteFn],
    lead: float,
    tail: float,
    min_speed: float,
    max_speed: float,
    window: ProviderWindow,
) -> None:
    timing = shot.setdefault("timing", {})
    pinned = timing.get("pinned_duration_s")
    if pinned is None:
        raise SolverError(f"{shot.get('shot_id')}: pinned_duration_s required")
    target = float(pinned)
    avail = target - lead - tail
    if avail <= 0:
        raise SolverError(f"{shot.get('shot_id')}: pinned duration too short for lead/tail")

    dialogue = shot.setdefault("dialogue", {})
    audio = shot.setdefault("audio", {})
    speed = 1.0
    measured, path, digest = synthesize(shot, speed)
    audio.update(
        {
            "status": "rendered",
            "speed": speed,
            "measured_duration_s": measured,
            "local_path": path,
            "sha256": digest,
            "error": None,
        }
    )

    if measured > avail:
        # speed up
        needed = measured / avail
        if needed <= max_speed + 1e-9:
            speed = min(max_speed, max(min_speed, needed))
            measured, path, digest = synthesize(shot, speed)
            audio.update({"speed": speed, "measured_duration_s": measured, "local_path": path, "sha256": digest})
        if measured > avail + 1e-6:
            if rewrite is None or int(dialogue.get("rewrite_count") or 0) >= 3:
                raise SolverError(
                    f"{shot.get('shot_id')}: pinned fit failed; audio {measured:.3f}s > avail {avail:.3f}s"
                )
            dialogue["source_text"] = dialogue.get("source_text") or dialogue.get("text")
            dialogue["text"] = rewrite(dialogue["text"], avail, shot)
            dialogue["rewrite_count"] = int(dialogue.get("rewrite_count") or 0) + 1
            # Character-span plans belong to the exact pre-rewrite text.  Never slide
            # stale offsets onto different words; the legacy intent will be recompiled
            # conservatively for the rewritten take.
            dialogue["performance"] = None
            measured, path, digest = synthesize(shot, speed)
            audio.update({"measured_duration_s": measured, "local_path": path, "sha256": digest})
            if measured > avail + 1e-6:
                raise SolverError(
                    f"{shot.get('shot_id')}: pinned fit still exceeds after rewrite"
                )

    # Extra room becomes tail_out breathing room (target stays pinned).
    effective_tail = max(tail, target - lead - measured)
    _quantize(
        timing,
        target=target,
        lead=lead,
        tail=effective_tail,
        window=window,
        measured=measured,
        branch="pinned_fit",
        pinned=target,
    )


def _solve_audio(
    shot: Dict[str, Any],
    *,
    synthesize: Optional[SynthesizeFn],
    rewrite: Optional[RewriteFn],
    lead: float,
    tail: float,
    min_speed: float,
    max_speed: float,
    window: ProviderWindow,
    planned: Optional[float],
) -> List[Dict[str, Any]]:
    dialogue = shot.setdefault("dialogue", {})
    audio = shot.setdefault("audio", {})
    timing = shot.setdefault("timing", {})
    branch = "none"
    speed = float(audio.get("speed") or 1.0)

    if synthesize is not None:
        measured, path, digest = synthesize(shot, speed)
        audio.update(
            {
                "status": "rendered",
                "speed": speed,
                "measured_duration_s": measured,
                "local_path": path,
                "sha256": digest,
                "error": None,
            }
        )
    else:
        measured = float(audio["measured_duration_s"])

    target = lead + measured + tail

    if planned and planned > 0:
        deviation = abs(target - planned) / planned
        timing["deviation_ratio"] = deviation
        if deviation <= 0.15:
            # adjust speed toward planned speech window
            speech_budget = planned - lead - tail
            if speech_budget > 0 and synthesize is not None:
                needed = measured / speech_budget
                clamped = min(max_speed, max(min_speed, needed))
                if abs(clamped - speed) > 1e-6:
                    speed = clamped
                    measured, path, digest = synthesize(shot, speed)
                    audio.update(
                        {
                            "speed": speed,
                            "measured_duration_s": measured,
                            "local_path": path,
                            "sha256": digest,
                        }
                    )
                    target = lead + measured + tail
                    branch = "speed_adjust"
        elif deviation <= 0.35:
            if rewrite is None or int(dialogue.get("rewrite_count") or 0) >= 3:
                return _split_shot(shot)
            speech_budget = max(0.1, planned - lead - tail)
            dialogue["source_text"] = dialogue.get("source_text") or dialogue.get("text")
            dialogue["text"] = rewrite(dialogue["text"], speech_budget, shot)
            dialogue["rewrite_count"] = int(dialogue.get("rewrite_count") or 0) + 1
            dialogue["performance"] = None
            if synthesize is None:
                raise SolverError("rewrite requires synthesize callback")
            measured, path, digest = synthesize(shot, 1.0)
            audio.update(
                {
                    "speed": 1.0,
                    "measured_duration_s": measured,
                    "local_path": path,
                    "sha256": digest,
                    "status": "rendered",
                }
            )
            target = lead + measured + tail
            branch = "llm_rewrite"
        else:
            return _split_shot(shot)

    # Hard split if ceil(target) exceeds provider max.
    if math.ceil(target - 1e-9) > window.max_s:
        return _split_shot(shot)

    _quantize(
        timing,
        target=target,
        lead=lead,
        tail=tail,
        window=window,
        measured=measured,
        branch=branch,
    )
    return [shot]


def _split_shot(shot: Dict[str, Any]) -> List[Dict[str, Any]]:
    text = ((shot.get("dialogue") or {}).get("text") or "").strip()
    parts = _split_text(text)
    if len(parts) < 2:
        # Force a mid split for hard provider overflow.
        mid = max(1, len(text) // 2)
        parts = [text[:mid].strip(), text[mid:].strip()]
        parts = [p for p in parts if p]
    if len(parts) < 2:
        raise SolverError(f"{shot.get('shot_id')}: cannot split shot further")

    out: List[Dict[str, Any]] = []
    for idx, part in enumerate(parts):
        clone = {
            **{k: v for k, v in shot.items() if k not in {"dialogue", "audio", "timing", "subtitle", "video", "lipsync", "transition"}},
            "shot_id": f"{shot['shot_id']}_p{idx + 1}",
            "timing_driver": "audio",
            "type": shot.get("type") or "dialogue",
            "dialogue": {
                **(shot.get("dialogue") or {}),
                "text": part,
                "source_text": (shot.get("dialogue") or {}).get("source_text")
                or (shot.get("dialogue") or {}).get("text"),
                "rewrite_count": (shot.get("dialogue") or {}).get("rewrite_count") or 0,
                "performance": None,
            },
            "audio": {
                "status": "pending",
                "provider": (shot.get("audio") or {}).get("provider"),
                "voice_id": (shot.get("audio") or {}).get("voice_id"),
                "take_id": (shot.get("audio") or {}).get("take_id"),
            },
            "timing": {
                "resolution_branch": "split_shot",
                "trim": {"strategy": "tail", "head_s": 0.0, "tail_s": 0.0},
            },
            "still": shot.get("still"),
            "video": {
                **(shot.get("video") or {}),
                "status": "pending",
                "request_id": None,
                "local_path": None,
                "source_url": None,
                "attempts": 0,
            },
            "lipsync": {"required": False, "status": "skipped"},
            "subtitle": {"text": part},
            # Inner boundaries of a split are hard cuts; the original transition still
            # belongs to whatever now ends the group.
            "transition": {"kind": "cut", "duration_s": 0.0, "note": None},
        }
        out.append(clone)
    if shot.get("transition"):
        out[-1]["transition"] = shot["transition"]
    return out


def _split_text(text: str) -> List[str]:
    chunks = re.split(r"(?<=[。！？!?；;])\s*", text)
    chunks = [c.strip() for c in chunks if c and c.strip()]
    if len(chunks) >= 2:
        return chunks
    chunks = re.split(r"(?<=[，,、])\s*", text)
    return [c.strip() for c in chunks if c and c.strip()]


def _quantize(
    timing: Dict[str, Any],
    *,
    target: float,
    lead: float,
    tail: float,
    window: ProviderWindow,
    measured: Optional[float],
    branch: str,
    pinned: Optional[float] = None,
) -> None:
    request = int(max(window.min_s, min(window.max_s, math.ceil(target - 1e-9))))
    slack = request - target
    strategy = (timing.get("trim") or {}).get("strategy") or "tail"
    if strategy == "head":
        head, tail_trim = slack, 0.0
    elif strategy == "both":
        head = slack / 2.0
        tail_trim = slack - head
    else:
        head, tail_trim = 0.0, slack

    timing.update(
        {
            "lead_in_s": lead,
            "tail_out_s": tail,
            "target_duration_s": target,
            "request_duration_s": request,
            "slack_s": slack,
            "trim": {"strategy": strategy, "head_s": head, "tail_s": tail_trim},
            "resolution_branch": branch,
        }
    )
    if pinned is not None:
        timing["pinned_duration_s"] = pinned
    if measured is not None:
        timing.setdefault("deviation_ratio", timing.get("deviation_ratio"))


def _layout_master_clock(timeline: Dict[str, Any]) -> None:
    cursor = 0.0
    g = timeline["global"]
    lead_default = float(g.get("lead_in_s", 0.3))
    for shot in timeline["shots"]:
        timing = shot["timing"]
        target = float(timing["target_duration_s"])
        timing["start_s"] = cursor
        timing["end_s"] = cursor + target
        cursor = timing["end_s"]

        audio = shot.get("audio") or {}
        measured = audio.get("measured_duration_s")
        subtitle = shot.setdefault("subtitle", {})
        if measured is not None and shot.get("timing_driver") in {"audio", "pinned"}:
            lead = float(timing.get("lead_in_s", lead_default))
            text = ((shot.get("dialogue") or {}).get("text")) or subtitle.get("text")
            subtitle["text"] = text
            subtitle["start_s"] = timing["start_s"] + lead
            subtitle["end_s"] = subtitle["start_s"] + float(measured)
        _apply_lipsync_gate(shot)


def _apply_lipsync_gate(shot: Dict[str, Any]) -> None:
    lipsync = shot.setdefault("lipsync", {})
    timing = shot.get("timing") or {}
    required = (
        shot.get("type") == "dialogue"
        and shot.get("shot_size") in {"close_up", "extreme_close_up"}
        and float(timing.get("target_duration_s") or 0) > 3.0
    )
    lipsync["required"] = required
    if required:
        lipsync.setdefault("model", "latentsync")
        if lipsync.get("status") == "skipped":
            lipsync["status"] = "pending"
        lipsync["reason"] = "close-up dialogue longer than 3s"
    else:
        lipsync["status"] = "skipped"
        lipsync.setdefault("reason", None)


def _reindex(timeline: Dict[str, Any]) -> None:
    for i, shot in enumerate(timeline["shots"]):
        shot["index"] = i
