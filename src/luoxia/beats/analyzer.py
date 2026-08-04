from __future__ import annotations

import hashlib
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Sequence, Tuple

from src.audio.xai_tts import voices_for_gender
from src.luoxia.beats import repairs as repair_log
from src.luoxia.beats.prompts import (
    ANALYZE_CARRYOVER_TEMPLATE,
    ANALYZE_SYSTEM,
    ANALYZE_USER_TEMPLATE,
    BEAT_TYPES,
)
from src.luoxia.beats.segmenter import (
    Paragraph,
    chunk_paragraphs,
    coverage_gaps,
    excerpt_for,
    render_numbered,
    span_for_range,
    split_paragraphs,
)
from src.luoxia.beats.validator import script_char_count
from src.luoxia.llm.client import LuoxiaLLM

ChatJSON = Callable[..., Dict[str, Any]]

DEFAULT_GLOBAL = {
    "keep_threshold": 6.5,
    "compress_threshold": 3.0,
    "max_compression_ratio": 0.15,
    "min_drop_rate": 0.3,
    "episode_min_peak": 7.0,
    "opening_conflict_types": [
        "conflict_escalation",
        "face_slap",
        "reversal",
        "identity_reveal",
        "emotional_peak",
    ],
    "target_episode_duration_s": 90,
}

DEFAULT_FEMALE_VOICES = tuple(voices_for_gender("female"))
DEFAULT_MALE_VOICES = tuple(voices_for_gender("male"))
# The model picks from the same registry the TTS adapter validates against, so a voice can
# never be accepted here and then silently recast as the API default at synthesis time.
VOICE_CATALOG = (
    f"女性角色：{', '.join(DEFAULT_FEMALE_VOICES)}\n"
    f"男性角色：{', '.join(DEFAULT_MALE_VOICES)}"
)
OPENING_TYPES = frozenset(DEFAULT_GLOBAL["opening_conflict_types"])
VALID_TYPES = frozenset(BEAT_TYPES)
SHOT_SIZES = {"extreme_close_up", "close_up", "medium", "full", "wide", "insert"}
CHUNK_CHARS = 5500
CARRYOVER_BEATS = 6


class AnalyzeError(RuntimeError):
    pass


def analyze_novel(
    text: str,
    *,
    work_id: str,
    title: Optional[str] = None,
    source_uri: Optional[str] = None,
    llm: Optional[LuoxiaLLM] = None,
    chat_json: Optional[ChatJSON] = None,
    global_overrides: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Slice + score a novel into a phase=scored beats document.

    The model never reports character offsets — it only groups pre-numbered paragraphs.
    Every source_span here is computed from the real text, so the audit trail is true.
    """
    body = (text or "").strip()
    if not body:
        raise AnalyzeError("novel text is empty")
    wid = _slug(work_id)
    if not wid:
        raise AnalyzeError("work_id must contain letters/digits")

    call = chat_json
    if call is None:
        client = llm or LuoxiaLLM()
        call = client.chat_json

    paragraphs = split_paragraphs(body)
    doc: Dict[str, Any] = {
        "schema_version": "1.0.0",
        "work_id": wid,
        "title": title or wid,
        "phase": "scored",
        "selected_at": None,
        "beats_hash": None,
        "source": {
            "source_id": "src",
            "title": title or wid,
            "uri": source_uri,
            "char_count": len(body),
            "sha256": f"sha256:{hashlib.sha256(body.encode('utf-8')).hexdigest()}",
        },
        "global": {**DEFAULT_GLOBAL, **(global_overrides or {})},
        "cast": [],
        "beats": [],
        "repairs": [],
        "audit": [],
    }

    raw_cast, raw_beats, chunk_count = _analyze_chunks(
        paragraphs, work_id=wid, title=title or wid, chat_json=call, doc=doc
    )
    doc["title"] = doc["title"] or wid
    doc["cast"] = _normalize_cast(raw_cast, doc=doc)
    cast_ids = {c["character_id"] for c in doc["cast"]}

    ordered = _resolve_spans(raw_beats, paragraphs=paragraphs, doc=doc)
    ordered = _fill_coverage_gaps(ordered, paragraphs=paragraphs, text=body, doc=doc)
    doc["beats"] = _normalize_beats(ordered, cast_ids=cast_ids, text=body)

    _ensure_opening(doc)
    _ensure_closing_hook(doc)
    _ensure_lines_for_hot_beats(doc)

    doc["quality"] = repair_log.summarize(doc)
    doc["audit"].append(
        {
            "at": datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds"),
            "actor": "agent:analyzer",
            "action": "segment_score",
            "detail": (
                f"chars={len(body)} paragraphs={len(paragraphs)} chunks={chunk_count} "
                f"beats={len(doc['beats'])} repairs={doc['quality']['repair_count']}"
            ),
        }
    )
    return doc


def analyze_novel_file(
    path: str | Path,
    *,
    work_id: Optional[str] = None,
    title: Optional[str] = None,
    **kwargs,
) -> Dict[str, Any]:
    p = Path(path)
    text = p.read_text(encoding="utf-8")
    wid = work_id or _slug(p.stem) or "work"
    return analyze_novel(text, work_id=wid, title=title or p.stem, source_uri=str(p), **kwargs)


def _analyze_chunks(
    paragraphs: Sequence[Paragraph],
    *,
    work_id: str,
    title: str,
    chat_json: ChatJSON,
    doc: Dict[str, Any],
) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]], int]:
    chunks = chunk_paragraphs(paragraphs, max_chars=CHUNK_CHARS)
    cast: List[Dict[str, Any]] = []
    seen_cast: set[str] = set()
    beats: List[Dict[str, Any]] = []

    for i, chunk in enumerate(chunks, start=1):
        carryover = ""
        if cast or beats:
            # Cross-chunk continuity: reuse ids instead of inventing a second "林晚".
            carryover = ANALYZE_CARRYOVER_TEMPLATE.format(
                known_cast="\n".join(
                    f"- {c.get('character_id')} = {c.get('display_name')}" for c in cast
                )
                or "（无）",
                known_beats="\n".join(
                    f"- {b.get('beat_id')}: {b.get('summary')}" for b in beats[-CARRYOVER_BEATS:]
                )
                or "（无）",
            )
        user = ANALYZE_USER_TEMPLATE.format(
            work_id=work_id,
            title=title,
            chunk_no=i,
            chunk_total=len(chunks),
            para_lo=chunk[0].index,
            para_hi=chunk[-1].index,
            numbered=render_numbered(chunk),
            carryover=carryover,
            voice_catalog=VOICE_CATALOG,
        )
        try:
            data = chat_json(
                [
                    {"role": "system", "content": ANALYZE_SYSTEM},
                    {"role": "user", "content": user},
                ]
            )
        except Exception as exc:
            raise AnalyzeError(f"chunk {i}/{len(chunks)} analysis failed: {exc}") from exc

        if i == 1 and data.get("title"):
            doc["title"] = data["title"]
        for c in data.get("cast") or []:
            cid = _slug(c.get("character_id") or c.get("display_name") or "")
            if not cid or cid in seen_cast:
                continue
            seen_cast.add(cid)
            cast.append({**c, "character_id": cid})
        for b in data.get("beats") or []:
            beats.append({**b, "_chunk": i, "_lo": chunk[0].index, "_hi": chunk[-1].index})

    if not beats:
        raise AnalyzeError("LLM returned no beats")
    return cast, beats, len(chunks)


def _resolve_spans(
    raw_beats: List[Dict[str, Any]],
    *,
    paragraphs: Sequence[Paragraph],
    doc: Dict[str, Any],
) -> List[Dict[str, Any]]:
    """Turn model-reported paragraph ranges into exact, non-overlapping char spans."""
    staged: List[Dict[str, Any]] = []
    for b in raw_beats:
        lo = b.get("para_start")
        hi = b.get("para_end", lo)
        if lo is None:
            repair_log.record(
                doc,
                code="beat_dropped_no_range",
                severity="medium",
                beat_id=str(b.get("beat_id") or "?"),
                detail="model returned a beat without para_start; dropped",
                actor="analyzer",
            )
            continue
        try:
            lo_i, hi_i = int(lo), int(hi if hi is not None else lo)
        except (TypeError, ValueError):
            repair_log.record(
                doc,
                code="beat_dropped_bad_range",
                severity="medium",
                beat_id=str(b.get("beat_id") or "?"),
                detail=f"unparsable paragraph range {lo!r}..{hi!r}; dropped",
                actor="analyzer",
            )
            continue
        # Keep the model inside the chunk it was actually shown.
        lo_i = max(lo_i, int(b.get("_lo", lo_i)))
        hi_i = min(hi_i, int(b.get("_hi", hi_i)))
        staged.append({**b, "_range": (min(lo_i, hi_i), max(lo_i, hi_i))})

    staged.sort(key=lambda b: b["_range"])
    resolved: List[Dict[str, Any]] = []
    cursor = -1
    for b in staged:
        lo_i, hi_i = b["_range"]
        if hi_i <= cursor:
            repair_log.record(
                doc,
                code="beat_dropped_overlap",
                severity="low",
                beat_id=str(b.get("beat_id") or "?"),
                detail=f"paragraphs {lo_i}-{hi_i} already covered; duplicate dropped",
                actor="analyzer",
            )
            continue
        if lo_i <= cursor:
            repair_log.record(
                doc,
                code="beat_range_trimmed",
                severity="low",
                beat_id=str(b.get("beat_id") or "?"),
                detail=f"paragraph range trimmed from {lo_i} to {cursor + 1} to remove overlap",
                actor="analyzer",
            )
            lo_i = cursor + 1
        span = span_for_range(paragraphs, lo_i, hi_i)
        if not span:
            continue
        resolved.append({**b, "_range": (lo_i, hi_i), "source_span": span})
        cursor = hi_i

    if not resolved:
        raise AnalyzeError("no beats survived paragraph range resolution")
    return resolved


def _fill_coverage_gaps(
    beats: List[Dict[str, Any]],
    *,
    paragraphs: Sequence[Paragraph],
    text: str,
    doc: Dict[str, Any],
) -> List[Dict[str, Any]]:
    """Paragraphs nobody claimed become explicit filler beats.

    Skipped prose must stay visible in the ledger; otherwise the drop_rate statistic
    silently ignores whatever the model forgot to mention.
    """
    gaps = coverage_gaps(paragraphs, [b["_range"] for b in beats])
    if not gaps:
        return beats
    for lo, hi in gaps:
        span = span_for_range(paragraphs, lo, hi)
        if not span:
            continue
        beats.append(
            {
                "beat_id": f"gap_{lo}_{hi}",
                "_range": (lo, hi),
                "source_span": span,
                "summary": excerpt_for(text, span, limit=40) or "未被模型归类的原文",
                "beat_type": "filler",
                "intensity": 1.0,
                "depends_on": [],
                "lines": [],
                "notes": "模型未归类，程序补为 filler",
            }
        )
    repair_log.record(
        doc,
        code="coverage_gap_filled",
        severity="low",
        detail=f"{len(gaps)} unclaimed paragraph run(s) recorded as filler",
        actor="analyzer",
    )
    beats.sort(key=lambda b: b["_range"])
    return beats


def _normalize_cast(cast: List[Dict[str, Any]], *, doc: Dict[str, Any]) -> List[Dict[str, Any]]:
    out: List[Dict[str, Any]] = []
    female_i = male_i = 0
    for i, raw in enumerate(cast):
        cid = _slug(raw.get("character_id") or raw.get("display_name") or f"char_{i+1}")
        display = (raw.get("display_name") or cid).strip()
        role = (
            raw.get("role")
            if raw.get("role") in {"protagonist", "antagonist", "support", "narrator"}
            else "support"
        )
        voice = raw.get("voice_id")
        if not voice:
            female = any(k in display for k in ("女", "姐", "妹", "娘", "妃", "太太", "小姐"))
            if role == "protagonist" and not female:
                female = i == 0
            if female:
                voice = DEFAULT_FEMALE_VOICES[female_i % len(DEFAULT_FEMALE_VOICES)]
                female_i += 1
            else:
                voice = DEFAULT_MALE_VOICES[male_i % len(DEFAULT_MALE_VOICES)]
                male_i += 1
            repair_log.record(
                doc,
                code="voice_assigned",
                severity="low",
                detail=f"{cid}: no voice_id from model, assigned {voice}",
                actor="analyzer",
            )
        appearance = (raw.get("appearance") or "").strip() or None
        if not appearance:
            repair_log.record(
                doc,
                code="appearance_missing",
                severity="medium",
                detail=f"{cid}: no appearance description; character sheet will be generic and faces may drift",
                actor="analyzer",
            )
        out.append(
            {
                "character_id": cid,
                "display_name": display,
                "voice_id": voice,
                "role": role,
                "appearance": appearance,
                "aliases": list(raw.get("aliases") or []),
            }
        )
    if not out:
        repair_log.record(
            doc,
            code="cast_empty",
            severity="high",
            detail="model returned no cast; falling back to a single narrator voice",
            actor="analyzer",
        )
        out = [
            {
                "character_id": "narrator",
                "display_name": "旁白",
                "voice_id": DEFAULT_MALE_VOICES[0],
                "role": "narrator",
                "appearance": None,
                "aliases": [],
            }
        ]
    return out


def _normalize_beats(
    beats: List[Dict[str, Any]],
    *,
    cast_ids: set[str],
    text: str,
) -> List[Dict[str, Any]]:
    if not cast_ids:
        cast_ids = {"narrator"}
    fallback_cid = next(iter(cast_ids))
    out: List[Dict[str, Any]] = []
    used_ids: set[str] = set()

    for i, raw in enumerate(beats):
        bid = _slug(raw.get("beat_id") or "") or f"b{i+1:03d}"
        while bid in used_ids:
            bid = f"{bid}_{i+1:02d}"
        used_ids.add(bid)

        span = dict(raw["source_span"])
        span["excerpt"] = excerpt_for(text, span)
        btype = raw.get("beat_type") if raw.get("beat_type") in VALID_TYPES else "setup"
        try:
            intensity = float(raw.get("intensity"))
        except (TypeError, ValueError):
            intensity = 3.0
        intensity = max(0.0, min(10.0, intensity))

        lines = []
        for ln in raw.get("lines") or []:
            cid = _slug(ln.get("character_id") or "") or fallback_cid
            if cid not in cast_ids:
                cid = fallback_cid
            t = (ln.get("text") or "").strip()
            if not t:
                continue
            size = ln.get("shot_size")
            lines.append(
                {
                    "character_id": cid,
                    "text": t,
                    "delivery": ln.get("delivery"),
                    "shot_size": size if size in SHOT_SIZES else "medium",
                    "line_type": "narration" if ln.get("line_type") == "narration" else "dialogue",
                }
            )

        visual = raw.get("visual") if isinstance(raw.get("visual"), dict) else None
        if visual:
            size = visual.get("shot_size")
            visual = {
                "scene_id": visual.get("scene_id") or raw.get("scene_id"),
                "shot_size": size if size in SHOT_SIZES else None,
                "prompt": visual.get("prompt"),
                "action_duration_s": _clamp_duration(visual.get("action_duration_s")),
            }

        cliff = raw.get("cliffhanger")
        if isinstance(cliff, dict) and cliff.get("tier") in {"tier_1", "tier_2", "tier_3", "daily"}:
            cliff = {"tier": cliff["tier"], "question": cliff.get("question")}
        else:
            cliff = None

        beat = {
            "beat_id": bid,
            "index": i,
            "source_span": span,
            "summary": (raw.get("summary") or span["excerpt"] or bid).strip(),
            "beat_type": btype,
            "intensity": intensity,
            "depends_on": [_slug(str(d)) for d in (raw.get("depends_on") or []) if _slug(str(d))],
            "scene_id": raw.get("scene_id"),
            "lines": lines,
            "script_char_count": 0,
            "visual": visual,
            "cliffhanger": cliff,
            "notes": raw.get("notes"),
        }
        beat["script_char_count"] = script_char_count(beat)
        out.append(beat)

    known = {b["beat_id"] for b in out}
    for beat in out:
        beat["depends_on"] = [d for d in beat["depends_on"] if d in known and d != beat["beat_id"]]
    return out


def _clamp_duration(value: Any) -> float:
    try:
        return max(1.0, min(15.0, float(value)))
    except (TypeError, ValueError):
        return 3.0


def _ensure_opening(doc: Dict[str, Any]) -> None:
    """Only flag the problem here.

    The beats array must stay in source order (invariant 4), so a cold open cannot be
    produced by shuffling it. Reordering belongs to episode planning, which is the layer
    that owns narrative order. If no hot beat exists up front, planning will fall back to
    forcing this one's type — also logged.
    """
    beats = doc["beats"]
    if not beats:
        raise AnalyzeError("no beats after normalize")
    if beats[0].get("beat_type") in OPENING_TYPES:
        return
    hot_nearby = any(
        b.get("beat_type") in OPENING_TYPES and float(b.get("intensity") or 0) >= 6.0
        for b in beats[:6]
    )
    repair_log.record(
        doc,
        code="weak_opening_detected",
        severity="low" if hot_nearby else "medium",
        beat_id=beats[0]["beat_id"],
        detail=(
            f"source opens on '{beats[0].get('beat_type')}' (intensity {beats[0].get('intensity')}); "
            + ("a hotter beat is available for a cold open" if hot_nearby else "no hot beat nearby to promote")
        ),
        actor="analyzer",
    )


def _ensure_closing_hook(doc: Dict[str, Any]) -> None:
    last = doc["beats"][-1]
    if (last.get("cliffhanger") or {}).get("tier"):
        return
    repair_log.record(
        doc,
        code="hook_forced",
        severity="medium",
        beat_id=last["beat_id"],
        detail="last beat had no cliffhanger; synthesized a tier_1 hook from its summary",
        actor="analyzer",
    )
    last["beat_type"] = "hook"
    last["intensity"] = max(float(last.get("intensity") or 0), 7.5)
    last["cliffhanger"] = {
        "tier": "tier_1",
        "question": last.get("summary") or "接下来会发生什么？",
    }


def _ensure_lines_for_hot_beats(doc: Dict[str, Any]) -> None:
    """High-intensity beats must speak. Inventing the line is a last resort, and it is logged."""
    cast = doc.get("cast") or []
    fallback = cast[0]["character_id"] if cast else "narrator"
    speaker = next((c["character_id"] for c in cast if c.get("role") == "protagonist"), fallback)
    for beat in doc["beats"]:
        if float(beat.get("intensity") or 0) < 6.5 or beat.get("lines"):
            continue
        summary = (beat.get("summary") or "").strip() or "……"
        text = summary if len(summary) <= 40 else summary[:39] + "。"
        beat["lines"] = [
            {
                "character_id": speaker,
                "text": text,
                "delivery": "克制",
                "shot_size": "medium",
                "line_type": "dialogue",
            }
        ]
        beat["script_char_count"] = script_char_count(beat)
        repair_log.record(
            doc,
            code="line_invented",
            severity="high",
            beat_id=beat["beat_id"],
            detail=(
                f"intensity {beat.get('intensity')} beat had no dialogue; "
                "synthesized a line from the summary (this reads like narration, not drama)"
            ),
            actor="analyzer",
        )


def _reindex(beats: List[Dict[str, Any]]) -> None:
    for i, beat in enumerate(beats):
        beat["index"] = i


def _slug(value: str) -> str:
    s = (value or "").strip().lower()
    s = re.sub(r"[^a-z0-9_]+", "_", s)
    s = re.sub(r"_+", "_", s).strip("_")
    if not s and value and value.strip():
        digest = hashlib.sha1(value.strip().encode("utf-8")).hexdigest()[:8]
        s = f"c_{digest}"
    return s
