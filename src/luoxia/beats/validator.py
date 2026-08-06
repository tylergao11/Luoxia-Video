from __future__ import annotations

import copy
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

from jsonschema import Draft202012Validator

from src.audio.performance import validate_performance
from src.luoxia.beats.hashing import compute_beats_hash
from src.luoxia.paths import BEATS_SCHEMA_PATH

EPS = 1e-9
RATIO_EPS = 1e-6
SCORED_PHASES = frozenset({"scored", "selected", "delivered"})
SELECTED_PHASES = frozenset({"selected", "delivered"})
RETAINED = frozenset({"keep", "compress"})
DEFAULT_COVERAGE = {
    "peak_threshold": 7.0,
    "peak_max_shots": 6,
    "mid_max_shots": 3,
    "low_max_shots": 1,
}
DEFAULT_OPENING_TYPES = (
    "conflict_escalation",
    "face_slap",
    "reversal",
    "identity_reveal",
    "emotional_peak",
)


@dataclass(frozen=True)
class BeatsIssue:
    code: str
    message: str
    beat_id: Optional[str] = None
    invariant: Optional[int] = None

    def __str__(self) -> str:
        loc = f"beat={self.beat_id} " if self.beat_id else ""
        inv = f"invariant#{self.invariant} " if self.invariant is not None else ""
        return f"{inv}{loc}{self.code}: {self.message}"


class BeatsValidationError(ValueError):
    def __init__(self, issues: Sequence[BeatsIssue]):
        self.issues = list(issues)
        super().__init__("\n".join(str(i) for i in self.issues) or "beats invalid")


def load_schema(schema_path: Optional[Path] = None) -> Dict[str, Any]:
    path = Path(schema_path or BEATS_SCHEMA_PATH)
    with path.open("r", encoding="utf-8") as f:
        return json.load(f)


def script_char_count(beat: Dict[str, Any]) -> int:
    return sum(len((ln.get("text") or "")) for ln in (beat.get("lines") or []))


def coverage_visuals(beat: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Ordered silent shots of a beat, accepting the deprecated single `visual`."""
    visuals = beat.get("visuals")
    if visuals:
        return list(visuals)
    legacy = beat.get("visual")
    return [{**legacy, "kind": "establishing", "after_line": 0}] if legacy else []


def shot_count(beat: Dict[str, Any]) -> int:
    """Shots this beat will cost to film: one per line plus one per silent shot."""
    return len(beat.get("lines") or []) + len(coverage_visuals(beat))


def coverage_settings(beats_doc: Dict[str, Any]) -> Dict[str, Any]:
    merged = dict(DEFAULT_COVERAGE)
    merged.update((beats_doc.get("global") or {}).get("coverage") or {})
    return merged


def coverage_budget(beat: Dict[str, Any], coverage: Dict[str, Any], g: Dict[str, Any]) -> int:
    """Shot allowance for this beat. Money follows intensity: peaks get real coverage."""
    intensity = float(beat.get("intensity") or 0)
    if intensity >= float(coverage["peak_threshold"]):
        return int(coverage["peak_max_shots"])
    if intensity >= float(g.get("compress_threshold", 3.0)):
        return int(coverage["mid_max_shots"])
    return int(coverage["low_max_shots"])


def compute_selection_stats(beats_doc: Dict[str, Any]) -> Dict[str, Any]:
    """Recompute the selection block from the beats themselves."""
    beats = beats_doc.get("beats") or []
    total_source = int((beats_doc.get("source") or {}).get("char_count") or 0)
    kept = sum(1 for b in beats if b.get("decision") == "keep")
    compressed = sum(1 for b in beats if b.get("decision") == "compress")
    dropped = sum(1 for b in beats if b.get("decision") == "drop")
    total_script = sum(script_char_count(b) for b in beats if b.get("decision") in RETAINED)
    return {
        "total_source_chars": total_source,
        "total_script_chars": total_script,
        "compression_ratio": (total_script / total_source) if total_source else 0.0,
        "drop_rate": (dropped / len(beats)) if beats else 0.0,
        "kept": kept,
        "compressed": compressed,
        "dropped": dropped,
    }


def validate_beats(
    beats_doc: Dict[str, Any],
    *,
    schema: Optional[Dict[str, Any]] = None,
    raise_on_error: bool = True,
) -> List[BeatsIssue]:
    issues: List[BeatsIssue] = []
    active_schema = schema or load_schema()

    Draft202012Validator.check_schema(active_schema)
    for err in sorted(Draft202012Validator(active_schema).iter_errors(beats_doc), key=lambda e: list(e.path)):
        path = "/".join(str(x) for x in err.path)
        issues.append(
            BeatsIssue(
                code="schema",
                message=f"/{path}: {err.message}" if path else err.message,
                beat_id=_beat_id_from_path(beats_doc, err.path),
            )
        )

    issues.extend(_check_invariants(beats_doc))

    if raise_on_error and issues:
        raise BeatsValidationError(issues)
    return issues


def _beat_id_from_path(beats_doc: Dict[str, Any], path) -> Optional[str]:
    parts = list(path)
    if len(parts) >= 2 and parts[0] == "beats" and isinstance(parts[1], int):
        beats = beats_doc.get("beats") or []
        if 0 <= parts[1] < len(beats):
            return beats[parts[1]].get("beat_id")
    return None


def _check_invariants(beats_doc: Dict[str, Any]) -> List[BeatsIssue]:
    issues: List[BeatsIssue] = []
    beats = beats_doc.get("beats") or []
    if not beats:
        return issues

    phase = beats_doc.get("phase")
    scored = phase in SCORED_PHASES
    selected = phase in SELECTED_PHASES
    g = beats_doc.get("global") or {}
    source_chars = int((beats_doc.get("source") or {}).get("char_count") or 0)
    cast_ids = {c.get("character_id") for c in (beats_doc.get("cast") or [])}
    by_id = {b.get("beat_id"): b for b in beats}

    _check_per_beat(issues, beats, source_chars, cast_ids, scored=scored, selected=selected)
    _check_coverage(issues, beats, cast_ids, coverage_settings(beats_doc), g, selected=selected)
    if selected:
        _check_dependencies(issues, beats, by_id)
        _check_episodes(issues, beats_doc, beats, by_id, g)
        _check_budgets(issues, beats_doc, g)
        _check_lock(issues, beats_doc)
    return issues


def _check_per_beat(
    issues: List[BeatsIssue],
    beats: List[Dict[str, Any]],
    source_chars: int,
    cast_ids,
    *,
    scored: bool,
    selected: bool,
) -> None:
    seen_ids: Dict[str, int] = {}
    prev_end: Optional[int] = None

    for i, beat in enumerate(beats):
        bid = beat.get("beat_id")
        span = beat.get("source_span") or {}

        # 1. unique beat_id
        if bid in seen_ids:
            issues.append(
                BeatsIssue(
                    code="duplicate_beat_id",
                    message=f"beat_id '{bid}' already used at position {seen_ids[bid]}",
                    beat_id=bid,
                    invariant=1,
                )
            )
        else:
            seen_ids[bid] = i

        # 2. index contiguous
        if beat.get("index") != i:
            issues.append(
                BeatsIssue(
                    code="index_mismatch",
                    message=f"index {beat.get('index')} != array position {i}",
                    beat_id=bid,
                    invariant=2,
                )
            )

        # 3. span well-formed and in bounds
        start = span.get("start_char")
        end = span.get("end_char")
        if not isinstance(start, int) or not isinstance(end, int):
            issues.append(
                BeatsIssue(code="span_incomplete", message="source_span requires integer offsets", beat_id=bid, invariant=3)
            )
            continue
        if start >= end:
            issues.append(
                BeatsIssue(
                    code="span_empty",
                    message=f"start_char {start} must be < end_char {end}",
                    beat_id=bid,
                    invariant=3,
                )
            )
        if source_chars and end > source_chars:
            issues.append(
                BeatsIssue(
                    code="span_out_of_bounds",
                    message=f"end_char {end} exceeds source.char_count {source_chars}",
                    beat_id=bid,
                    invariant=3,
                )
            )

        # 4. no overlap with previous span (gaps are allowed: skipped prose)
        if prev_end is not None and start < prev_end:
            issues.append(
                BeatsIssue(
                    code="span_overlap",
                    message=f"start_char {start} overlaps previous end_char {prev_end}",
                    beat_id=bid,
                    invariant=4,
                )
            )
        prev_end = max(prev_end or 0, end)

        # 5. scored phase needs type + intensity
        if scored:
            if not beat.get("beat_type"):
                issues.append(
                    BeatsIssue(code="missing_beat_type", message="phase>=scored requires beat_type", beat_id=bid, invariant=5)
                )
            if beat.get("intensity") is None:
                issues.append(
                    BeatsIssue(code="missing_intensity", message="phase>=scored requires intensity", beat_id=bid, invariant=5)
                )

        decision = beat.get("decision")

        # 6. selected phase needs a decision
        if selected and decision not in {"keep", "compress", "drop"}:
            issues.append(
                BeatsIssue(code="missing_decision", message="phase>=selected requires decision", beat_id=bid, invariant=6)
            )

        # 7. retained beats must deliver something
        if decision in RETAINED and not (beat.get("lines") or coverage_visuals(beat)):
            issues.append(
                BeatsIssue(
                    code="empty_retained_beat",
                    message=f"decision={decision} requires at least one line or a visual",
                    beat_id=bid,
                    invariant=7,
                )
            )

        # 8. dropped beats must say why; merged content must land somewhere
        if decision == "drop":
            reason = beat.get("drop_reason")
            if not reason:
                issues.append(
                    BeatsIssue(code="missing_drop_reason", message="decision=drop requires drop_reason", beat_id=bid, invariant=8)
                )
            elif reason == "merged":
                target = beat.get("merged_into")
                if not target:
                    issues.append(
                        BeatsIssue(
                            code="missing_merge_target",
                            message="drop_reason=merged requires merged_into",
                            beat_id=bid,
                            invariant=8,
                        )
                    )

        # 10. filler is never worth keeping in full
        if beat.get("beat_type") == "filler" and decision == "keep":
            issues.append(
                BeatsIssue(
                    code="filler_kept",
                    message="beat_type=filler cannot be kept in full; compress or drop it",
                    beat_id=bid,
                    invariant=10,
                )
            )

        # 16. declared script length must match the lines
        declared = beat.get("script_char_count")
        actual = script_char_count(beat)
        if declared is not None and int(declared) != actual:
            issues.append(
                BeatsIssue(
                    code="script_char_mismatch",
                    message=f"script_char_count {declared} != sum(len(line.text)) {actual}",
                    beat_id=bid,
                    invariant=16,
                )
            )

        # 17. speakers must exist in cast
        for ln in beat.get("lines") or []:
            cid = ln.get("character_id")
            if cid and cid not in cast_ids:
                issues.append(
                    BeatsIssue(
                        code="unknown_character",
                        message=f"line character_id '{cid}' not in cast",
                        beat_id=bid,
                        invariant=17,
                    )
                )
            for message in validate_performance(ln.get("text") or "", ln.get("performance")):
                issues.append(
                    BeatsIssue(
                        code="invalid_performance",
                        message=message,
                        beat_id=bid,
                        invariant=22,
                    )
                )


def _check_coverage(
    issues: List[BeatsIssue],
    beats: List[Dict[str, Any]],
    cast_ids,
    coverage: Dict[str, Any],
    g: Dict[str, Any],
    *,
    selected: bool,
) -> None:
    """21. the shot list must be readable as a shot list, and must fit the budget."""
    for beat in beats:
        bid = beat.get("beat_id")

        if beat.get("visuals") and beat.get("visual"):
            issues.append(
                BeatsIssue(
                    code="visual_and_visuals",
                    message="fill either visuals (ordered coverage) or the deprecated visual, not both",
                    beat_id=bid,
                    invariant=21,
                )
            )

        visuals = beat.get("visuals") or []
        line_count = len(beat.get("lines") or [])
        previous_slot = 0
        for n, visual in enumerate(visuals, start=1):
            slot = int(visual.get("after_line") or 0)
            if slot > line_count:
                issues.append(
                    BeatsIssue(
                        code="after_line_out_of_range",
                        message=f"visuals[{n - 1}].after_line {slot} exceeds line count {line_count}",
                        beat_id=bid,
                        invariant=21,
                    )
                )
            if slot < previous_slot:
                issues.append(
                    BeatsIssue(
                        code="coverage_out_of_order",
                        message=(
                            f"visuals[{n - 1}].after_line {slot} goes back before "
                            f"{previous_slot}; coverage must read in shot order"
                        ),
                        beat_id=bid,
                        invariant=21,
                    )
                )
            previous_slot = max(previous_slot, slot)

            subject = visual.get("subject")
            if visual.get("kind") == "reaction" and not subject:
                issues.append(
                    BeatsIssue(
                        code="reaction_without_subject",
                        message=f"visuals[{n - 1}] is a reaction shot with no subject; whose face is it?",
                        beat_id=bid,
                        invariant=21,
                    )
                )
            if subject and subject not in cast_ids:
                issues.append(
                    BeatsIssue(
                        code="unknown_character",
                        message=f"visuals[{n - 1}].subject '{subject}' not in cast",
                        beat_id=bid,
                        invariant=21,
                    )
                )
            visible_characters = visual.get("characters") or []
            for cid in visible_characters:
                if cid not in cast_ids:
                    issues.append(
                        BeatsIssue(
                            code="unknown_character",
                            message=f"visuals[{n - 1}].characters contains '{cid}' not in cast",
                            beat_id=bid,
                            invariant=21,
                        )
                    )
            if visual.get("kind") == "reaction" and visible_characters not in ([], [subject]):
                issues.append(
                    BeatsIssue(
                        code="reaction_has_extra_characters",
                        message=(
                            f"visuals[{n - 1}] is a reaction shot; characters must contain "
                            "only its subject"
                        ),
                        beat_id=bid,
                        invariant=21,
                    )
                )

        # Budget is only meaningful once decisions exist; a draft may still be oversized.
        if selected and beat.get("decision") in RETAINED:
            # The budget governs the silent shots we add, not the lines content selection
            # already approved, so a line-heavy beat never trips this check.
            allowance = max(coverage_budget(beat, coverage, g), len(beat.get("lines") or []))
            actual = shot_count(beat)
            if actual > allowance:
                issues.append(
                    BeatsIssue(
                        code="coverage_over_budget",
                        message=(
                            f"{actual} shots exceeds the {allowance}-shot budget for "
                            f"intensity {beat.get('intensity')}"
                        ),
                        beat_id=bid,
                        invariant=21,
                    )
                )


def _check_dependencies(issues: List[BeatsIssue], beats: List[Dict[str, Any]], by_id: Dict[str, Any]) -> None:
    for beat in beats:
        bid = beat.get("beat_id")
        decision = beat.get("decision")

        # 8 (second half). merge target must survive
        if decision == "drop" and beat.get("drop_reason") == "merged":
            target = beat.get("merged_into")
            if target:
                dep = by_id.get(target)
                if dep is None:
                    issues.append(
                        BeatsIssue(
                            code="unknown_merge_target",
                            message=f"merged_into '{target}' does not exist",
                            beat_id=bid,
                            invariant=8,
                        )
                    )
                elif dep.get("decision") not in RETAINED:
                    issues.append(
                        BeatsIssue(
                            code="merge_target_dropped",
                            message=f"merged_into '{target}' was itself dropped; the information is lost",
                            beat_id=bid,
                            invariant=8,
                        )
                    )

        if decision not in RETAINED:
            continue

        # 9. a kept payoff cannot rest on a dropped setup
        for dep_id in beat.get("depends_on") or []:
            dep = by_id.get(dep_id)
            if dep is None:
                issues.append(
                    BeatsIssue(
                        code="unknown_dependency",
                        message=f"depends_on '{dep_id}' does not exist",
                        beat_id=bid,
                        invariant=9,
                    )
                )
            elif dep.get("decision") == "drop":
                issues.append(
                    BeatsIssue(
                        code="dependency_dropped",
                        message=f"depends_on '{dep_id}' was dropped; this beat no longer lands",
                        beat_id=bid,
                        invariant=9,
                    )
                )


def _narrative_positions(beats_doc: Dict[str, Any]) -> Dict[str, Tuple[int, int]]:
    positions: Dict[str, Tuple[int, int]] = {}
    for ep in sorted(beats_doc.get("episodes") or [], key=lambda e: e.get("episode_no", 0)):
        for pos, bid in enumerate(ep.get("beat_ids") or []):
            positions.setdefault(bid, (int(ep.get("episode_no", 0)), pos))
    return positions


def _check_episodes(
    issues: List[BeatsIssue],
    beats_doc: Dict[str, Any],
    beats: List[Dict[str, Any]],
    by_id: Dict[str, Any],
    g: Dict[str, Any],
) -> None:
    episodes = beats_doc.get("episodes") or []
    if not episodes:
        issues.append(
            BeatsIssue(
                code="no_episodes",
                message=f"phase={beats_doc.get('phase')} requires at least one episode",
                invariant=18,
            )
        )
        return

    membership: Dict[str, int] = {}
    for ep in episodes:
        ep_beats = []
        for bid in ep.get("beat_ids") or []:
            beat = by_id.get(bid)
            if beat is None:
                issues.append(
                    BeatsIssue(
                        code="unknown_episode_beat",
                        message=f"episode {ep.get('episode_id')} references missing beat '{bid}'",
                        beat_id=bid,
                        invariant=18,
                    )
                )
                continue
            membership[bid] = membership.get(bid, 0) + 1
            ep_beats.append(beat)

        if not ep_beats:
            continue

        # 11. every episode must end on a hook
        last = ep_beats[-1]
        if not (last.get("cliffhanger") or {}).get("tier"):
            issues.append(
                BeatsIssue(
                    code="episode_without_cliffhanger",
                    message=f"episode {ep.get('episode_id')} ends on a beat with no cliffhanger.tier",
                    beat_id=last.get("beat_id"),
                    invariant=11,
                )
            )

        # 12. every episode must have a peak
        min_peak = float(g.get("episode_min_peak", 7.0))
        peak = max((float(b.get("intensity") or 0) for b in ep_beats), default=0.0)
        if peak < min_peak - EPS:
            issues.append(
                BeatsIssue(
                    code="episode_without_peak",
                    message=f"episode {ep.get('episode_id')} peak intensity {peak} < episode_min_peak {min_peak}",
                    invariant=12,
                )
            )

    # 13. the opening cannot be a setup
    first_ep = min(episodes, key=lambda e: e.get("episode_no", 0))
    first_ids = first_ep.get("beat_ids") or []
    if first_ids:
        opener = by_id.get(first_ids[0])
        allowed = tuple(g.get("opening_conflict_types") or DEFAULT_OPENING_TYPES)
        if opener is not None and opener.get("beat_type") not in allowed:
            issues.append(
                BeatsIssue(
                    code="weak_opening",
                    message=(
                        f"first beat of episode {first_ep.get('episode_id')} is "
                        f"'{opener.get('beat_type')}'; opening must be one of {list(allowed)}"
                    ),
                    beat_id=opener.get("beat_id"),
                    invariant=13,
                )
            )

    # 18. retained beats belong to exactly one episode, dropped beats to none
    for beat in beats:
        bid = beat.get("beat_id")
        count = membership.get(bid, 0)
        if beat.get("decision") in RETAINED and count != 1:
            issues.append(
                BeatsIssue(
                    code="bad_episode_membership",
                    message=f"retained beat appears in {count} episodes, expected exactly 1",
                    beat_id=bid,
                    invariant=18,
                )
            )
        if beat.get("decision") == "drop" and count:
            issues.append(
                BeatsIssue(
                    code="dropped_beat_scheduled",
                    message=f"dropped beat is still scheduled in {count} episode(s)",
                    beat_id=bid,
                    invariant=18,
                )
            )

    # 20. setup must air before the payoff that depends on it
    positions = _narrative_positions(beats_doc)
    for beat in beats:
        if beat.get("decision") not in RETAINED:
            continue
        bid = beat.get("beat_id")
        here = positions.get(bid)
        if here is None:
            continue
        for dep_id in beat.get("depends_on") or []:
            there = positions.get(dep_id)
            if there is None:
                continue
            if there >= here:
                issues.append(
                    BeatsIssue(
                        code="dependency_after_payoff",
                        message=f"depends_on '{dep_id}' airs at {there} but this beat airs at {here}",
                        beat_id=bid,
                        invariant=20,
                    )
                )


def _check_budgets(issues: List[BeatsIssue], beats_doc: Dict[str, Any], g: Dict[str, Any]) -> None:
    stats = compute_selection_stats(beats_doc)

    # 14. the whole point: the script must be much shorter than the novel
    max_ratio = float(g.get("max_compression_ratio", 0.15))
    if stats["compression_ratio"] > max_ratio + RATIO_EPS:
        issues.append(
            BeatsIssue(
                code="under_compressed",
                message=(
                    f"compression_ratio {stats['compression_ratio']:.4f} exceeds "
                    f"max_compression_ratio {max_ratio}: {stats['total_script_chars']} script chars "
                    f"from {stats['total_source_chars']} source chars"
                ),
                invariant=14,
            )
        )

    # 15. and something must actually have been cut
    min_drop = float(g.get("min_drop_rate", 0.3))
    if stats["drop_rate"] < min_drop - RATIO_EPS:
        issues.append(
            BeatsIssue(
                code="nothing_dropped",
                message=f"drop_rate {stats['drop_rate']:.4f} below min_drop_rate {min_drop}",
                invariant=15,
            )
        )


def _check_lock(issues: List[BeatsIssue], beats_doc: Dict[str, Any]) -> None:
    # 19. locked selection needs provenance and honest stats
    if not beats_doc.get("beats_hash") or not beats_doc.get("selected_at"):
        issues.append(
            BeatsIssue(
                code="missing_selection_meta",
                message=f"phase={beats_doc.get('phase')} requires beats_hash and selected_at",
                invariant=19,
            )
        )
    else:
        actual = compute_beats_hash(beats_doc)
        if actual != beats_doc["beats_hash"]:
            issues.append(
                BeatsIssue(
                    code="beats_hash_mismatch",
                    message=f"beats_hash {beats_doc['beats_hash']} != recomputed {actual}",
                    invariant=19,
                )
            )

    declared = beats_doc.get("selection")
    if declared:
        stats = compute_selection_stats(beats_doc)
        for key, value in stats.items():
            if key not in declared:
                continue
            given = declared[key]
            if isinstance(value, float):
                if abs(float(given) - value) > 1e-4:
                    issues.append(
                        BeatsIssue(
                            code="selection_stats_mismatch",
                            message=f"selection.{key} {given} != recomputed {value}",
                            invariant=19,
                        )
                    )
            elif int(given) != int(value):
                issues.append(
                    BeatsIssue(
                        code="selection_stats_mismatch",
                        message=f"selection.{key} {given} != recomputed {value}",
                        invariant=19,
                    )
                )


def mutate_for_invariant_violation(beats_doc: Dict[str, Any], invariant: int) -> Dict[str, Any]:
    """Return a deep copy that deliberately violates one invariant (for tests)."""
    doc = copy.deepcopy(beats_doc)
    beats = doc["beats"]
    g = doc["global"]

    if invariant == 1:
        beats[1]["beat_id"] = beats[0]["beat_id"]
    elif invariant == 2:
        beats[1]["index"] = 99
    elif invariant == 3:
        beats[1]["source_span"]["start_char"] = beats[1]["source_span"]["end_char"] + 10
    elif invariant == 4:
        beats[2]["source_span"]["start_char"] = beats[1]["source_span"]["start_char"]
    elif invariant == 5:
        beats[1].pop("intensity", None)
    elif invariant == 6:
        beats[1].pop("decision", None)
    elif invariant == 7:
        target = _first_retained(beats)
        target["lines"] = []
        target.pop("visual", None)
        target.pop("visuals", None)
        target["script_char_count"] = 0
    elif invariant == 8:
        _first_dropped(beats)["drop_reason"] = None
    elif invariant == 9:
        dep_id = _first_with_dependency(beats)["depends_on"][0]
        dep = next(b for b in beats if b["beat_id"] == dep_id)
        dep["decision"] = "drop"
        dep["drop_reason"] = "flat"
    elif invariant == 10:
        _first_retained(beats)["beat_type"] = "filler"
    elif invariant == 11:
        for beat in beats:
            beat["cliffhanger"] = None
    elif invariant == 12:
        for beat in beats:
            beat["intensity"] = 1.0
    elif invariant == 13:
        _episode_opener(doc)["beat_type"] = "setup"
    elif invariant == 14:
        g["max_compression_ratio"] = 0.001
    elif invariant == 15:
        g["min_drop_rate"] = 0.99
    elif invariant == 16:
        _first_retained(beats)["script_char_count"] = 9999
    elif invariant == 17:
        _first_retained(beats)["lines"][0]["character_id"] = "not_in_cast"
    elif invariant == 18:
        doc["episodes"][0]["beat_ids"] = doc["episodes"][0]["beat_ids"][1:]
    elif invariant == 19:
        doc["selection"]["total_script_chars"] = 123456
    elif invariant == 20:
        payoff = _first_with_dependency(beats)
        ids = list(doc["episodes"][0]["beat_ids"])
        ids.remove(payoff["beat_id"])
        doc["episodes"][0]["beat_ids"] = [payoff["beat_id"]] + ids
    elif invariant == 21:
        target = _first_retained(beats)
        target["visuals"] = [
            {"kind": "reaction", "after_line": len(target["lines"]), "subject": None},
            {"kind": "insert", "after_line": 0},
        ]
    else:
        raise ValueError(f"unknown invariant {invariant}")
    return doc


def _first_retained(beats: List[Dict[str, Any]]) -> Dict[str, Any]:
    return next(b for b in beats if b.get("decision") in RETAINED and b.get("lines"))


def _first_dropped(beats: List[Dict[str, Any]]) -> Dict[str, Any]:
    return next(b for b in beats if b.get("decision") == "drop")


def _first_with_dependency(beats: List[Dict[str, Any]]) -> Dict[str, Any]:
    return next(b for b in beats if b.get("depends_on"))


def _episode_opener(doc: Dict[str, Any]) -> Dict[str, Any]:
    first_ep = min(doc["episodes"], key=lambda e: e.get("episode_no", 0))
    opener_id = first_ep["beat_ids"][0]
    return next(b for b in doc["beats"] if b["beat_id"] == opener_id)
