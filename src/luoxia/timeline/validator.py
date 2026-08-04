from __future__ import annotations

import copy
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence

from jsonschema import Draft202012Validator

from src.luoxia.catalog_limits import find_shot_video_model, resolve_video_duration_bounds
from src.luoxia.paths import TIMELINE_SCHEMA_PATH
from src.luoxia.timeline.transitions import CUT, DISSOLVE, head_room_s, tail_room_s, transition_of

EPS = 1e-6
FROZEN_PHASES = frozenset({"frozen", "rendering", "rendered"})


@dataclass(frozen=True)
class ValidationIssue:
    code: str
    message: str
    shot_id: Optional[str] = None
    invariant: Optional[int] = None

    def __str__(self) -> str:
        loc = f"shot={self.shot_id} " if self.shot_id else ""
        inv = f"invariant#{self.invariant} " if self.invariant is not None else ""
        return f"{inv}{loc}{self.code}: {self.message}"


class TimelineValidationError(ValueError):
    def __init__(self, issues: Sequence[ValidationIssue]):
        self.issues = list(issues)
        super().__init__("\n".join(str(i) for i in self.issues) or "timeline invalid")


def load_schema(schema_path: Optional[Path] = None) -> Dict[str, Any]:
    path = Path(schema_path or TIMELINE_SCHEMA_PATH)
    with path.open("r", encoding="utf-8") as f:
        return json.load(f)


def validate_timeline(
    timeline: Dict[str, Any],
    *,
    schema: Optional[Dict[str, Any]] = None,
    catalog: Optional[Dict[str, Any]] = None,
    raise_on_error: bool = True,
) -> List[ValidationIssue]:
    issues: List[ValidationIssue] = []
    active_schema = schema or load_schema()

    # A draft has no timing yet by design: durations may only come from measured audio.
    # Checking it against the full contract would just report every timing field as missing.
    draft = timeline.get("phase") == "draft"
    if draft:
        active_schema = _relax_timing_requirements(active_schema)

    Draft202012Validator.check_schema(active_schema)
    for err in sorted(Draft202012Validator(active_schema).iter_errors(timeline), key=lambda e: list(e.path)):
        path = "/".join(str(x) for x in err.path)
        shot_id = _shot_id_from_path(timeline, err.path)
        issues.append(
            ValidationIssue(
                code="schema",
                message=f"/{path}: {err.message}" if path else err.message,
                shot_id=shot_id,
            )
        )

    issues.extend(_check_invariants(timeline, catalog=catalog, draft=draft))

    if raise_on_error and issues:
        raise TimelineValidationError(issues)
    return issues


def _relax_timing_requirements(schema: Dict[str, Any]) -> Dict[str, Any]:
    relaxed = copy.deepcopy(schema)
    relaxed.get("$defs", {}).get("timing", {}).pop("required", None)
    return relaxed


def _shot_id_from_path(timeline: Dict[str, Any], path) -> Optional[str]:
    parts = list(path)
    if len(parts) >= 2 and parts[0] == "shots" and isinstance(parts[1], int):
        shots = timeline.get("shots") or []
        if 0 <= parts[1] < len(shots):
            return shots[parts[1]].get("shot_id")
    return None


def _check_invariants(
    timeline: Dict[str, Any],
    *,
    catalog: Optional[Dict[str, Any]] = None,
    draft: bool = False,
) -> List[ValidationIssue]:
    issues: List[ValidationIssue] = []
    g = timeline.get("global") or {}
    cast_ids = {c.get("character_id") for c in (timeline.get("cast") or [])}
    shots = timeline.get("shots") or []
    if not shots:
        return issues

    # 3. first start_s == 0
    if not draft:
        first = shots[0].get("timing") or {}
        first_start = first.get("start_s")
        if first_start is None or abs(float(first_start)) > EPS:
            issues.append(
                ValidationIssue(
                    code="start_not_zero",
                    message=f"shots[0].timing.start_s must be 0, got {first.get('start_s')}",
                    shot_id=shots[0].get("shot_id"),
                    invariant=3,
                )
            )

    prev_end: Optional[float] = None
    for i, shot in enumerate(shots):
        sid = shot.get("shot_id")
        timing = shot.get("timing") or {}
        audio = shot.get("audio") or {}
        dialogue = shot.get("dialogue") or {}
        still = shot.get("still") or {}
        subtitle = shot.get("subtitle") or {}

        # 4. index contiguous
        if shot.get("index") != i:
            issues.append(
                ValidationIssue(
                    code="index_mismatch",
                    message=f"index {shot.get('index')} != array position {i}",
                    shot_id=sid,
                    invariant=4,
                )
            )

        # 9. speed band
        if audio.get("speed") is not None:
            speed = float(audio["speed"])
            mn = float(g.get("min_speed_ratio", 0.92))
            mx = float(g.get("max_speed_ratio", 1.10))
            if speed < mn - EPS or speed > mx + EPS:
                issues.append(
                    ValidationIssue(
                        code="speed_out_of_band",
                        message=f"audio.speed {speed} outside [{mn}, {mx}]",
                        shot_id=sid,
                        invariant=9,
                    )
                )

        # 10. rewrite_count
        if dialogue.get("rewrite_count") is not None and int(dialogue["rewrite_count"]) > 3:
            issues.append(
                ValidationIssue(
                    code="rewrite_overflow",
                    message=f"dialogue.rewrite_count {dialogue['rewrite_count']} > 3",
                    shot_id=sid,
                    invariant=10,
                )
            )

        # 12. character in cast
        cid = dialogue.get("character_id")
        if cid and cid not in cast_ids:
            issues.append(
                ValidationIssue(
                    code="unknown_character",
                    message=f"dialogue.character_id '{cid}' not in cast",
                    shot_id=sid,
                    invariant=12,
                )
            )

        # 13. still aspect == global
        still_ar = still.get("aspect_ratio")
        global_ar = g.get("aspect_ratio")
        if still_ar and global_ar and still_ar != global_ar:
            issues.append(
                ValidationIssue(
                    code="aspect_mismatch",
                    message=f"still.aspect_ratio {still_ar} != global.aspect_ratio {global_ar}",
                    shot_id=sid,
                    invariant=13,
                )
            )

        # 16a. transition kind and duration agree (checkable without solved timing)
        kind = str((shot.get("transition") or {}).get("kind") or CUT)
        declared = float((shot.get("transition") or {}).get("duration_s") or 0.0)
        if kind == CUT and declared > EPS:
            issues.append(
                ValidationIssue(
                    code="cut_with_duration",
                    message=f"transition.kind=cut must have duration_s 0, got {declared}",
                    shot_id=sid,
                    invariant=16,
                )
            )
        if kind != CUT and declared <= EPS:
            issues.append(
                ValidationIssue(
                    code="transition_without_duration",
                    message=f"transition.kind={kind} requires duration_s > 0",
                    shot_id=sid,
                    invariant=16,
                )
            )
        if kind == DISSOLVE and i == len(shots) - 1:
            issues.append(
                ValidationIssue(
                    code="dissolve_on_last_shot",
                    message="last shot has no successor to dissolve into; use fade_black or cut",
                    shot_id=sid,
                    invariant=16,
                )
            )

        # Everything below reads solved timing, which a draft does not have yet.
        if draft:
            continue

        try:
            start = float(timing["start_s"])
            end = float(timing["end_s"])
            target = float(timing["target_duration_s"])
            request = int(timing["request_duration_s"])
            slack = float(timing.get("slack_s", 0))
        except (KeyError, TypeError, ValueError) as exc:
            issues.append(
                ValidationIssue(
                    code="timing_incomplete",
                    message=f"timing fields incomplete: {exc}",
                    shot_id=sid,
                    invariant=1,
                )
            )
            continue

        # 1. end - start == target
        if abs((end - start) - target) > EPS:
            issues.append(
                ValidationIssue(
                    code="span_ne_target",
                    message=f"end_s - start_s ({end - start}) != target_duration_s ({target})",
                    shot_id=sid,
                    invariant=1,
                )
            )

        # 2. adjacency
        if prev_end is not None and abs(start - prev_end) > EPS:
            issues.append(
                ValidationIssue(
                    code="gap_or_overlap",
                    message=f"start_s {start} != previous end_s {prev_end}",
                    shot_id=sid,
                    invariant=2,
                )
            )
        prev_end = end

        # 5. slack
        expected_slack = request - target
        if abs(slack - expected_slack) > EPS:
            issues.append(
                ValidationIssue(
                    code="slack_mismatch",
                    message=f"slack_s {slack} != request - target ({expected_slack})",
                    shot_id=sid,
                    invariant=5,
                )
            )
        if slack < -EPS:
            issues.append(
                ValidationIssue(
                    code="negative_slack",
                    message=f"slack_s must be >= 0, got {slack}",
                    shot_id=sid,
                    invariant=5,
                )
            )

        # 6. trim absorbs slack
        trim = timing.get("trim") or {}
        head = float(trim.get("head_s", 0) or 0)
        tail = float(trim.get("tail_s", 0) or 0)
        if abs((head + tail) - slack) > EPS:
            issues.append(
                ValidationIssue(
                    code="trim_mismatch",
                    message=f"trim.head_s + trim.tail_s ({head + tail}) != slack_s ({slack})",
                    shot_id=sid,
                    invariant=6,
                )
            )

        # 7. audio-driven requires rendered audio
        if shot.get("timing_driver") == "audio":
            if audio.get("status") != "rendered" or audio.get("measured_duration_s") is None:
                issues.append(
                    ValidationIssue(
                        code="audio_not_rendered",
                        message="timing_driver=audio requires audio.status=rendered and measured_duration_s",
                        shot_id=sid,
                        invariant=7,
                    )
                )

        # 8. pinned
        if shot.get("timing_driver") == "pinned":
            pinned = timing.get("pinned_duration_s")
            if pinned is None or abs(float(pinned) - target) > EPS:
                issues.append(
                    ValidationIssue(
                        code="pinned_mismatch",
                        message=f"pinned target_duration_s must equal pinned_duration_s (got {target} vs {pinned})",
                        shot_id=sid,
                        invariant=8,
                    )
                )

        # 11. request_duration in provider range (from catalog)
        model = find_shot_video_model(shot, timeline)
        if model:
            try:
                pmin, pmax = resolve_video_duration_bounds(model, catalog=catalog)
                if request < pmin or request > pmax:
                    issues.append(
                        ValidationIssue(
                            code="request_out_of_range",
                            message=f"request_duration_s {request} outside catalog range [{pmin}, {pmax}] for {model}",
                            shot_id=sid,
                            invariant=11,
                        )
                    )
            except (KeyError, ValueError) as exc:
                issues.append(
                    ValidationIssue(
                        code="catalog_bounds",
                        message=str(exc),
                        shot_id=sid,
                        invariant=11,
                    )
                )

        # 15. subtitle inside shot
        if subtitle.get("start_s") is not None and subtitle.get("end_s") is not None:
            ss = float(subtitle["start_s"])
            se = float(subtitle["end_s"])
            if ss < start - EPS or se > end + EPS:
                issues.append(
                    ValidationIssue(
                        code="subtitle_out_of_bounds",
                        message=f"subtitle [{ss}, {se}] outside shot [{start}, {end}]",
                        shot_id=sid,
                        invariant=15,
                    )
                )

        # 16b. the transition must fit in breathing room so it never covers a spoken word
        kind, duration = transition_of(shot)
        if duration > EPS:
            nxt = shots[i + 1] if i + 1 < len(shots) else None
            if kind == DISSOLVE:
                # The overlap sits after this shot's target window, so only the incoming
                # shot's pre-speech room constrains it.
                room = head_room_s(nxt) if nxt else 0.0
                if duration > room + EPS:
                    issues.append(
                        ValidationIssue(
                            code="transition_covers_speech",
                            message=(
                                f"dissolve {duration}s exceeds next shot's pre-speech room {room}s; "
                                "raise lead_in_s or shorten the dissolve"
                            ),
                            shot_id=sid,
                            invariant=16,
                        )
                    )
            else:
                room = tail_room_s(shot)
                if duration > room + EPS:
                    issues.append(
                        ValidationIssue(
                            code="transition_covers_speech",
                            message=(
                                f"{kind} {duration}s exceeds this shot's post-speech room {room}s; "
                                "raise tail_out_s, shorten the fade, or insert a transition shot"
                            ),
                            shot_id=sid,
                            invariant=16,
                        )
                    )
                if nxt is not None:
                    next_room = head_room_s(nxt)
                    if duration > next_room + EPS:
                        issues.append(
                            ValidationIssue(
                                code="transition_covers_speech",
                                message=(
                                    f"{kind} {duration}s exceeds next shot's pre-speech room "
                                    f"{next_room}s; raise lead_in_s or shorten the fade"
                                ),
                                shot_id=sid,
                                invariant=16,
                            )
                        )

    # 14. frozen phase requires hash + frozen_at
    phase = timeline.get("phase")
    if phase in FROZEN_PHASES:
        if not timeline.get("timeline_hash") or not timeline.get("frozen_at"):
            issues.append(
                ValidationIssue(
                    code="missing_freeze_meta",
                    message=f"phase={phase} requires timeline_hash and frozen_at",
                    invariant=14,
                )
            )

    return issues


def mutate_for_invariant_violation(
    timeline: Dict[str, Any],
    invariant: int,
) -> Dict[str, Any]:
    """Return a deep copy that deliberately violates one invariant (for tests)."""
    tl = copy.deepcopy(timeline)
    shot = tl["shots"][1] if len(tl["shots"]) > 1 else tl["shots"][0]
    timing = shot["timing"]

    if invariant == 1:
        timing["end_s"] = timing["start_s"] + timing["target_duration_s"] + 0.5
    elif invariant == 2:
        tl["shots"][1]["timing"]["start_s"] = tl["shots"][0]["timing"]["end_s"] + 1.0
        tl["shots"][1]["timing"]["end_s"] = tl["shots"][1]["timing"]["start_s"] + tl["shots"][1]["timing"]["target_duration_s"]
    elif invariant == 3:
        tl["shots"][0]["timing"]["start_s"] = 0.5
        tl["shots"][0]["timing"]["end_s"] = 0.5 + tl["shots"][0]["timing"]["target_duration_s"]
    elif invariant == 4:
        shot["index"] = 99
    elif invariant == 5:
        timing["slack_s"] = timing["request_duration_s"] - timing["target_duration_s"] + 1.0
    elif invariant == 6:
        timing.setdefault("trim", {})
        timing["trim"]["head_s"] = 0
        timing["trim"]["tail_s"] = 0
    elif invariant == 7:
        for s in tl["shots"]:
            if s.get("timing_driver") == "audio":
                s["audio"]["status"] = "pending"
                s["audio"]["measured_duration_s"] = None
                break
    elif invariant == 8:
        for s in tl["shots"]:
            if s.get("timing_driver") == "pinned":
                s["timing"]["pinned_duration_s"] = s["timing"]["target_duration_s"] + 1.0
                break
    elif invariant == 9:
        for s in tl["shots"]:
            if s.get("audio"):
                s["audio"]["speed"] = float(tl["global"]["max_speed_ratio"]) + 0.5
                break
    elif invariant == 10:
        for s in tl["shots"]:
            if s.get("dialogue"):
                s["dialogue"]["rewrite_count"] = 4
                break
    elif invariant == 11:
        timing["request_duration_s"] = 99
        timing["slack_s"] = 99 - timing["target_duration_s"]
        timing.setdefault("trim", {})["tail_s"] = timing["slack_s"]
        timing["trim"]["head_s"] = 0
    elif invariant == 12:
        for s in tl["shots"]:
            if s.get("dialogue"):
                s["dialogue"]["character_id"] = "not_in_cast"
                break
    elif invariant == 13:
        for s in tl["shots"]:
            if s.get("still"):
                s["still"]["aspect_ratio"] = "16:9" if tl["global"]["aspect_ratio"] != "16:9" else "1:1"
                break
    elif invariant == 14:
        tl["phase"] = "frozen"
        tl["timeline_hash"] = None
        tl["frozen_at"] = None
    elif invariant == 15:
        for s in tl["shots"]:
            sub = s.get("subtitle") or {}
            if sub.get("start_s") is not None:
                s["subtitle"]["end_s"] = s["timing"]["end_s"] + 1.0
                break
    elif invariant == 16:
        shot["transition"] = {"kind": "fade_black", "duration_s": 1.5}
    else:
        raise ValueError(f"unknown invariant {invariant}")
    return tl
