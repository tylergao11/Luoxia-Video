from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from src.luoxia.beats import repairs as repair_log
from src.luoxia.beats.hashing import compute_beats_hash
from src.luoxia.beats.validator import (
    RETAINED,
    compute_selection_stats,
    script_char_count,
    validate_beats,
)

DEFAULT_OPENING_TYPES = (
    "conflict_escalation",
    "face_slap",
    "reversal",
    "identity_reveal",
    "emotional_peak",
)

# 中文 TTS 在 speed=1.0 下的粗略吐字速率，仅用于分集打包时估长。
# 真实时长永远以 timeline 阶段的 ffprobe 实测为准，这里的数字不进入任何成片决策。
CHARS_PER_SECOND = 4.5
LINE_OVERHEAD_S = 0.8


class SelectionError(RuntimeError):
    pass


def estimate_beat_duration_s(beat: Dict[str, Any]) -> float:
    """Rough length of a beat once filmed. Packing-only estimate, never a timing source."""
    lines = beat.get("lines") or []
    speech = script_char_count(beat) / CHARS_PER_SECOND + LINE_OVERHEAD_S * len(lines)
    visual = beat.get("visual") or {}
    return speech + float(visual.get("action_duration_s") or 0)


def apply_thresholds(beats_doc: Dict[str, Any]) -> Dict[str, int]:
    """Turn intensity scores into keep/compress/drop. Deterministic and re-runnable."""
    g = beats_doc.get("global") or {}
    keep_at = float(g.get("keep_threshold", 6.5))
    compress_at = float(g.get("compress_threshold", 3.0))
    if compress_at > keep_at:
        raise SelectionError(f"compress_threshold {compress_at} must not exceed keep_threshold {keep_at}")

    changed = {"keep": 0, "compress": 0, "drop": 0}
    for beat in beats_doc.get("beats") or []:
        if beat.get("decision_locked"):
            continue
        intensity = beat.get("intensity")
        if intensity is None:
            raise SelectionError(f"{beat.get('beat_id')}: intensity required before selection")
        score = float(intensity)

        if score >= keep_at:
            decision = "keep"
        elif score >= compress_at:
            decision = "compress"
        else:
            decision = "drop"

        # 平淡段落即便分数虚高也不给完整篇幅，最多压成一句。
        if beat.get("beat_type") == "filler" and decision == "keep":
            decision = "compress"

        beat["decision"] = decision
        changed[decision] += 1
        if decision == "drop":
            beat.setdefault("drop_reason", None)
            if not beat.get("drop_reason"):
                beat["drop_reason"] = "flat"
        else:
            beat["drop_reason"] = None
            beat["merged_into"] = None
    return changed


def repair_dependencies(beats_doc: Dict[str, Any]) -> List[str]:
    """Rescue setups that a kept payoff still needs. Returns the beat_ids that were upgraded."""
    beats = beats_doc.get("beats") or []
    by_id = {b.get("beat_id"): b for b in beats}
    rescued: List[str] = []

    for _ in range(len(beats) + 1):
        pending = []
        for beat in beats:
            if beat.get("decision") not in RETAINED:
                continue
            for dep_id in beat.get("depends_on") or []:
                dep = by_id.get(dep_id)
                if dep is None:
                    raise SelectionError(f"{beat.get('beat_id')}: depends_on '{dep_id}' does not exist")
                if dep.get("decision") == "drop":
                    pending.append((dep, beat.get("beat_id")))
        if not pending:
            return rescued

        for dep, payoff_id in pending:
            if dep.get("decision_locked"):
                raise SelectionError(
                    f"{dep.get('beat_id')} is locked as drop but {payoff_id} depends on it; "
                    "unlock it or remove the dependency"
                )
            dep["decision"] = "compress"
            dep["drop_reason"] = None
            dep["merged_into"] = None
            rescued.append(dep.get("beat_id"))
            if not (dep.get("lines") or dep.get("visual")):
                raise SelectionError(
                    f"{dep.get('beat_id')} must be retained for {payoff_id} but has no lines or visual to show"
                )

    raise SelectionError("dependency repair did not converge; check for a cycle in depends_on")


def plan_episodes(beats_doc: Dict[str, Any], *, episode_prefix: str = "ep") -> List[Dict[str, Any]]:
    """Pack retained beats into episodes, cutting only where a cliffhanger exists."""
    g = beats_doc.get("global") or {}
    target = float(g.get("target_episode_duration_s", 90))
    min_peak = float(g.get("episode_min_peak", 7.0))

    retained = [b for b in (beats_doc.get("beats") or []) if b.get("decision") in RETAINED]
    if not retained:
        raise SelectionError("no beats survived selection")

    groups: List[List[Dict[str, Any]]] = []
    current: List[Dict[str, Any]] = []
    elapsed = 0.0
    for beat in retained:
        current.append(beat)
        elapsed += estimate_beat_duration_s(beat)
        has_hook = bool((beat.get("cliffhanger") or {}).get("tier"))
        if has_hook and elapsed >= target:
            groups.append(current)
            current = []
            elapsed = 0.0
    if current:
        groups.append(current)

    # 一集没有峰值就不成立，向后并入下一集。
    merged: List[List[Dict[str, Any]]] = []
    for group in groups:
        peak = max((float(b.get("intensity") or 0) for b in group), default=0.0)
        if merged and peak < min_peak:
            merged[-1].extend(group)
        else:
            merged.append(group)
    for i in range(len(merged) - 1, 0, -1):
        peak = max((float(b.get("intensity") or 0) for b in merged[i]), default=0.0)
        if peak < min_peak:
            merged[i - 1].extend(merged.pop(i))

    last_group = merged[-1]
    if not (last_group[-1].get("cliffhanger") or {}).get("tier"):
        raise SelectionError(
            f"last beat '{last_group[-1].get('beat_id')}' has no cliffhanger; "
            "an episode cannot end without one"
        )
    for group in merged[:-1]:
        if not (group[-1].get("cliffhanger") or {}).get("tier"):
            raise SelectionError(f"episode boundary at '{group[-1].get('beat_id')}' has no cliffhanger")

    episodes = []
    for i, group in enumerate(merged, start=1):
        episodes.append(
            {
                "episode_id": f"{episode_prefix}{i:02d}",
                "episode_no": i,
                "title": None,
                "beat_ids": [b["beat_id"] for b in group],
                "target_duration_s": target,
            }
        )
    beats_doc["episodes"] = episodes
    _apply_cold_open(beats_doc)
    return episodes


def _apply_cold_open(beats_doc: Dict[str, Any]) -> None:
    """Make episode 1 start on conflict.

    Narrative order lives in episodes[].beat_ids, not in the beats array — so a cold open
    is a reordering here, never a reshuffle of source order. Only dependency-free beats
    can be promoted, otherwise the payoff would air before its setup (invariant 20).
    """
    episodes = beats_doc.get("episodes") or []
    if not episodes:
        return
    g = beats_doc.get("global") or {}
    allowed = set(g.get("opening_conflict_types") or DEFAULT_OPENING_TYPES)
    by_id = {b["beat_id"]: b for b in (beats_doc.get("beats") or [])}
    first_ep = min(episodes, key=lambda e: e.get("episode_no", 0))
    ids = list(first_ep.get("beat_ids") or [])
    if not ids:
        return

    opener = by_id.get(ids[0])
    if opener is not None and opener.get("beat_type") in allowed:
        return

    promote = next(
        (
            bid
            for bid in ids[1:6]
            if by_id.get(bid)
            and by_id[bid].get("beat_type") in allowed
            and float(by_id[bid].get("intensity") or 0) >= 6.0
            and not (by_id[bid].get("depends_on") or [])
        ),
        None,
    )
    if promote:
        ids.remove(promote)
        first_ep["beat_ids"] = [promote] + ids
        repair_log.record(
            beats_doc,
            code="cold_open_applied",
            severity="low",
            beat_id=promote,
            detail=f"moved '{promote}' to the front of {first_ep['episode_id']} so it opens on conflict",
            actor="selector",
        )
        return

    if opener is None:
        return
    repair_log.record(
        beats_doc,
        code="opening_type_forced",
        severity="medium",
        beat_id=opener["beat_id"],
        detail=(
            f"episode 1 opens on '{opener.get('beat_type')}' and no dependency-free hot beat "
            "was available to promote; type forced to conflict_escalation"
        ),
        actor="selector",
    )
    opener["beat_type"] = "conflict_escalation"
    opener["intensity"] = max(float(opener.get("intensity") or 0), 7.0)


def ensure_retained_payload(beats_doc: Dict[str, Any]) -> None:
    """Retained beats must show something. Inventing a line is a last resort and is logged."""
    cast = beats_doc.get("cast") or []
    fallback = cast[0]["character_id"] if cast else "narrator"
    protagonist = next((c["character_id"] for c in cast if c.get("role") == "protagonist"), fallback)
    for beat in beats_doc.get("beats") or []:
        if beat.get("decision") not in RETAINED:
            continue
        if beat.get("lines") or beat.get("visual"):
            continue
        summary = (beat.get("summary") or beat.get("beat_id") or "……").strip()
        text = summary if len(summary) <= 36 else summary[:35] + "。"
        beat["lines"] = [
            {
                "character_id": protagonist,
                "text": text,
                "delivery": "克制",
                "shot_size": "medium",
                "line_type": "dialogue",
            }
        ]
        beat["script_char_count"] = script_char_count(beat)
        repair_log.record(
            beats_doc,
            code="line_invented",
            severity="high",
            beat_id=beat["beat_id"],
            detail=(
                f"retained beat ({beat.get('decision')}) had neither lines nor visual; "
                "synthesized a line from the summary"
            ),
            actor="selector",
        )


def select_beats(
    beats_doc: Dict[str, Any],
    *,
    actor: str = "selector:threshold",
    plan: bool = True,
    now: Optional[datetime] = None,
    max_repair_severity: Optional[str] = None,
) -> Dict[str, Any]:
    """Full selection pass: score -> decide -> rescue setups -> pack episodes -> lock.

    `max_repair_severity` gates on the repair ledger: pass "medium" to refuse a document
    whose dialogue the harness had to invent or cut mid-thought. Default is no gate, so
    callers can inspect `beats_doc["quality"]` and decide for themselves.
    """
    if beats_doc.get("phase") == "delivered":
        raise SelectionError("beats already delivered; re-running selection would invalidate the timeline")

    counts = apply_thresholds(beats_doc)
    rescued = repair_dependencies(beats_doc)
    ensure_retained_payload(beats_doc)

    for beat in beats_doc.get("beats") or []:
        beat["script_char_count"] = script_char_count(beat)

    if plan:
        plan_episodes(beats_doc)

    trimmed = _enforce_compression_budget(beats_doc)

    stats = compute_selection_stats(beats_doc)
    beats_doc["selection"] = stats
    beats_doc["quality"] = repair_log.summarize(beats_doc)
    beats_doc["phase"] = "selected"
    beats_doc["selected_at"] = (now or datetime.now(timezone.utc)).isoformat()
    beats_doc["beats_hash"] = compute_beats_hash(beats_doc)

    detail = (
        f"keep={stats['kept']} compress={stats['compressed']} drop={stats['dropped']}, "
        f"{stats['total_script_chars']}/{stats['total_source_chars']} chars "
        f"(ratio {stats['compression_ratio']:.4f}, drop_rate {stats['drop_rate']:.4f})"
    )
    if rescued:
        detail += f"; rescued setups: {', '.join(rescued)}"
    if trimmed:
        detail += f"; auto-trimmed lines on {len(set(trimmed))} beat(s)"
    q = beats_doc["quality"]
    detail += f"; repairs={q['repair_count']} (worst={q['worst_severity'] or 'none'})"
    beats_doc.setdefault("audit", []).append(
        {
            "at": beats_doc["selected_at"],
            "actor": actor,
            "action": "select",
            "detail": detail,
        }
    )
    validate_beats(beats_doc)
    if max_repair_severity:
        repair_log.enforce(beats_doc, max_severity=max_repair_severity)
    _ = counts
    return beats_doc


_CLAUSE_ENDS = "，,。！？!?；;、…"


def _enforce_compression_budget(beats_doc: Dict[str, Any]) -> List[str]:
    """Bring the script under budget by cutting the softest material first.

    Order of harm: drop a surplus line on a compress beat, then cut a line at a clause
    boundary, and only mid-word as the final resort. Every cut is logged, because a
    mechanically shortened line is a real quality loss, not a formatting detail.
    """
    g = beats_doc.get("global") or {}
    max_ratio = float(g.get("max_compression_ratio", 0.15))
    source = int((beats_doc.get("source") or {}).get("char_count") or 0)
    if source <= 0:
        return []
    budget = int(source * max_ratio)
    trimmed: List[str] = []

    for _ in range(32):
        stats = compute_selection_stats(beats_doc)
        over = stats["total_script_chars"] - budget
        if over <= 0:
            return trimmed
        candidates = [
            b
            for b in (beats_doc.get("beats") or [])
            if b.get("decision") in RETAINED and b.get("lines")
        ]
        candidates.sort(
            key=lambda b: (
                0 if b.get("decision") == "compress" else 1,
                float(b.get("intensity") or 0),
                -script_char_count(b),
            )
        )
        if not candidates:
            break
        target = candidates[0]

        # A compress beat is only entitled to one line; surplus lines go first.
        if target.get("decision") == "compress" and len(target["lines"]) > 1:
            dropped = target["lines"].pop()
            repair_log.record(
                beats_doc,
                code="line_dropped",
                severity="medium",
                beat_id=target["beat_id"],
                detail=f"over budget by {over} chars; dropped surplus line \u300c{(dropped.get('text') or '')[:20]}\u300d",
                actor="selector",
            )
            target["script_char_count"] = script_char_count(target)
            trimmed.append(target["beat_id"])
            continue

        longest = max(target["lines"], key=lambda ln: len(ln.get("text") or ""))
        text = longest.get("text") or ""
        if len(text) <= 8:
            if len(target["lines"]) > 1:
                target["lines"].remove(longest)
                repair_log.record(
                    beats_doc,
                    code="line_dropped",
                    severity="medium",
                    beat_id=target["beat_id"],
                    detail=f"over budget by {over} chars; dropped short line \u300c{text}\u300d",
                    actor="selector",
                )
                target["script_char_count"] = script_char_count(target)
                trimmed.append(target["beat_id"])
                continue
            break

        cut = _cut_at_clause(text, over)
        clean = cut != text[: len(cut)].rstrip() or cut.endswith(("。", "！", "？"))
        longest["text"] = cut
        repair_log.record(
            beats_doc,
            code="line_truncated",
            severity="medium" if clean else "high",
            beat_id=target["beat_id"],
            detail=(
                f"over budget by {over} chars; \u300c{text}\u300d -> \u300c{cut}\u300d"
                + ("" if clean else " (no clause boundary available; cut mid-thought)")
            ),
            actor="selector",
        )
        target["script_char_count"] = script_char_count(target)
        trimmed.append(target["beat_id"])
    return trimmed


def _cut_at_clause(text: str, needed: int) -> str:
    """Shorten `text` by at least `needed` chars, preferring a clause boundary."""
    limit = max(6, len(text) - max(needed, 1))
    window = text[:limit]
    cut = max(window.rfind(ch) for ch in _CLAUSE_ENDS)
    if cut >= 6:
        return window[: cut + 1].rstrip("，,、；;").rstrip() or window
    return window.rstrip("，,、；;").rstrip() + "。"
