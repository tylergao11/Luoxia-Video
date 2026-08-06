from __future__ import annotations

from typing import Any, Dict, List, Optional

from src.luoxia.beats.hashing import compute_beats_hash
from src.luoxia.beats.validator import RETAINED
from src.luoxia.speech import provider_for_voice

DEFAULT_GLOBAL: Dict[str, Any] = {
    # grok-imagine-video delivers 24fps; declaring 25 only duplicates frames on encode.
    "fps": 24,
    "aspect_ratio": "16:9",
    # 1080p costs the same as 720p on grok-imagine-video-1.5, so there is no reason to ship less.
    "resolution": "1080p",
    "lead_in_s": 0.3,
    "tail_out_s": 0.5,
    "min_speed_ratio": 0.92,
    "max_speed_ratio": 1.10,
    "default_action_duration_s": 4,
}


class BridgeError(RuntimeError):
    pass


def build_timeline_draft(
    beats_doc: Dict[str, Any],
    episode_id: str,
    *,
    provider: str = "xai",
    model: str = "grok-imagine-video-1.5",
    global_overrides: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Turn one selected episode into a draft timeline.

    The result is intentionally *not* timeline-contract-valid yet: every shot carries
    empty timing because durations may only come from measured audio. Run the solver
    on it, then validate.
    """
    if beats_doc.get("phase") not in {"selected", "delivered"}:
        raise BridgeError(f"beats phase must be selected before bridging, got {beats_doc.get('phase')}")
    compute_beats_hash(beats_doc)  # cheap structural sanity before we fan out

    episode = _find_episode(beats_doc, episode_id)
    by_id = {b.get("beat_id"): b for b in beats_doc.get("beats") or []}
    cast_by_id = {c.get("character_id"): c for c in beats_doc.get("cast") or []}

    g = {**DEFAULT_GLOBAL, **(global_overrides or {})}
    shots: List[Dict[str, Any]] = []
    used_characters: set[str] = set()

    for beat_id in episode.get("beat_ids") or []:
        beat = by_id.get(beat_id)
        if beat is None:
            raise BridgeError(f"episode {episode_id} references missing beat '{beat_id}'")
        if beat.get("decision") not in RETAINED:
            raise BridgeError(f"beat '{beat_id}' was dropped but is scheduled in {episode_id}")

        lines = beat.get("lines") or []
        # The action shot shows whoever speaks in this beat, so it gets their portraits too.
        beat_characters = []
        for line in lines:
            cid = line.get("character_id")
            if cid and cid not in beat_characters:
                beat_characters.append(cid)

        visuals = _coverage(beat)
        by_slot: Dict[int, List[Dict[str, Any]]] = {}
        for visual in visuals:
            slot = int(visual.get("after_line") or 0)
            if slot > len(lines):
                raise BridgeError(
                    f"beat '{beat_id}': visual after_line={slot} but the beat has {len(lines)} line(s)"
                )
            by_slot.setdefault(slot, []).append(visual)

        # Emit the beat's coverage in reading order: shots before line 1, line 1, shots
        # after line 1, and so on. This is the whole point of after_line.
        for v_index, visual in enumerate(by_slot.get(0) or [], start=1):
            shots.append(
                _visual_shot(
                    episode_id, beat, visual, g, provider, model,
                    slot=0, ordinal=v_index,
                    cast_by_id=cast_by_id, characters=beat_characters,
                )
            )
        for n, line in enumerate(lines, start=1):
            cid = line.get("character_id")
            if cid not in cast_by_id:
                raise BridgeError(f"beat '{beat_id}' line {n}: character '{cid}' not in cast")
            used_characters.add(cid)
            shots.append(_dialogue_shot(episode_id, beat, line, n, cast_by_id[cid], g, provider, model))
            for v_index, visual in enumerate(by_slot.get(n) or [], start=1):
                shots.append(
                    _visual_shot(
                        episode_id, beat, visual, g, provider, model,
                        slot=n, ordinal=v_index,
                        cast_by_id=cast_by_id, characters=beat_characters,
                    )
                )
        for visual in visuals:
            subject = visual.get("subject")
            if subject:
                if subject not in cast_by_id:
                    raise BridgeError(
                        f"beat '{beat_id}': visual subject '{subject}' not in cast"
                    )
                used_characters.add(subject)
            for cid in visual.get("characters") or []:
                if cid not in cast_by_id:
                    raise BridgeError(
                        f"beat '{beat_id}': visual character '{cid}' not in cast"
                    )
                used_characters.add(cid)

    if not shots:
        raise BridgeError(f"episode {episode_id} produced no shots")

    for i, shot in enumerate(shots):
        shot["index"] = i

    return {
        "schema_version": "1.2.0",
        "project_id": beats_doc.get("work_id"),
        "episode_id": episode_id,
        "title": episode.get("title") or beats_doc.get("title"),
        "phase": "draft",
        "frozen_at": None,
        "timeline_hash": None,
        "global": g,
        "cast": [_timeline_cast_entry(cast_by_id[cid]) for cid in sorted(used_characters)],
        "shots": shots,
        "audit": [
            {
                "at": beats_doc.get("selected_at"),
                "actor": "bridge:beats_to_timeline",
                "action": "build_draft",
                "detail": (
                    f"{episode_id} built from {len(episode.get('beat_ids') or [])} beats "
                    f"of {beats_doc.get('work_id')} @ {beats_doc.get('beats_hash')}"
                ),
            }
        ],
    }


def _find_episode(beats_doc: Dict[str, Any], episode_id: str) -> Dict[str, Any]:
    for ep in beats_doc.get("episodes") or []:
        if ep.get("episode_id") == episode_id:
            return ep
    known = [e.get("episode_id") for e in beats_doc.get("episodes") or []]
    raise BridgeError(f"episode '{episode_id}' not found; known episodes: {known}")


def _timeline_cast_entry(entry: Dict[str, Any]) -> Dict[str, Any]:
    voice_id = entry.get("voice_id")
    if not voice_id:
        raise BridgeError(f"cast '{entry.get('character_id')}' has no voice_id; TTS cannot run")
    return {
        "character_id": entry["character_id"],
        "display_name": entry.get("display_name") or entry["character_id"],
        "voice_id": voice_id,
        "tts_provider": provider_for_voice(voice_id, entry.get("tts_provider")),
        "reference_image_asset_id": entry.get("reference_image_path"),
    }


def _coverage(beat: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Ordered silent shots for a beat, accepting the deprecated single `visual`."""
    visuals = beat.get("visuals")
    if visuals:
        return list(visuals)
    legacy = beat.get("visual")
    return [{**legacy, "kind": "establishing", "after_line": 0}] if legacy else []


# A silent shot's beats-side kind maps onto the timeline shot type that a reviewer reads.
_KIND_TO_TYPE = {
    "establishing": "transition",
    "reaction": "reaction",
    "insert": "insert",
    "action": "action",
}

# Reaction and insert shots are punctuation: at 4s they stop reading as cuts and start
# reading as dead air, so they get their own default instead of default_action_duration_s.
_KIND_DEFAULT_DURATION_S = {"reaction": 1.5, "insert": 1.5}

_KIND_DEFAULT_SHOT_SIZE = {"reaction": "close_up", "insert": "insert"}


def _visual_shot(
    episode_id: str,
    beat: Dict[str, Any],
    visual: Dict[str, Any],
    g: Dict[str, Any],
    provider: str,
    model: str,
    *,
    slot: int,
    ordinal: int,
    cast_by_id: Dict[str, Any],
    characters: Optional[List[str]] = None,
) -> Dict[str, Any]:
    kind = str(visual.get("kind") or "establishing")
    subject = visual.get("subject")
    declared_characters = list(visual.get("characters") or [])
    # A reaction shot exists to show one face; anyone else in frame defeats it.
    if kind == "reaction" and subject:
        shot_characters = [subject]
    elif declared_characters:
        shot_characters = declared_characters
    elif subject:
        shot_characters = [subject]
    else:
        shot_characters = list(characters or [])
    duration = (
        visual.get("action_duration_s")
        or _KIND_DEFAULT_DURATION_S.get(kind)
        or g["default_action_duration_s"]
    )
    shot = {
        "shot_id": f"{episode_id}_{beat['beat_id']}_v{slot}{ordinal}",
        "index": 0,
        "type": _KIND_TO_TYPE.get(kind, "action"),
        "timing_driver": "rhythm",
        "scene_id": visual.get("scene_id") or beat.get("scene_id"),
        "shot_size": visual.get("shot_size") or _KIND_DEFAULT_SHOT_SIZE.get(kind),
        "characters": shot_characters,
        "timing": {
            "target_duration_s": float(duration),
            "trim": {"strategy": "tail", "head_s": 0.0, "tail_s": 0.0},
        },
        "still": {
            "status": "pending",
            "aspect_ratio": g["aspect_ratio"],
            "prompt": visual.get("prompt"),
            "attempts": 0,
        },
        "video": {
            "status": "pending",
            "provider": provider,
            "model": model,
            "has_audio_track": False,
            "audio_stripped": False,
            "attempts": 0,
        },
        "lipsync": {"required": False, "status": "skipped"},
        "subtitle": {
            "text": None,
            "description": _visual_description(kind, subject, beat, cast_by_id),
        },
        "transition": {"kind": "cut", "duration_s": 0.0, "note": None},
    }
    return shot


_KIND_LABEL = {
    "establishing": "建立镜头",
    "reaction": "反应镜头",
    "insert": "插入镜头",
    "action": "动作镜头",
}


def _visual_description(
    kind: str,
    subject: Optional[str],
    beat: Dict[str, Any],
    cast_by_id: Dict[str, Any],
) -> str:
    """Context for the still-prompt writer. A reaction shot must not describe the whole
    beat, or the image comes back as a wide two-shot instead of one face."""
    label = _KIND_LABEL.get(kind, kind)
    summary = beat.get("summary") or ""
    if kind == "reaction" and subject:
        name = (cast_by_id.get(subject) or {}).get("display_name") or subject
        return f"{label}：{name}的表情反应（{summary}）"
    return f"{label}：{summary}" if summary else label


def _dialogue_shot(
    episode_id: str,
    beat: Dict[str, Any],
    line: Dict[str, Any],
    n: int,
    cast_entry: Dict[str, Any],
    g: Dict[str, Any],
    provider: str,
    model: str,
) -> Dict[str, Any]:
    return {
        "shot_id": f"{episode_id}_{beat['beat_id']}_l{n:02d}",
        "index": 0,
        "type": line.get("line_type") or "dialogue",
        "timing_driver": "audio",
        "scene_id": beat.get("scene_id"),
        "shot_size": line.get("shot_size"),
        "characters": [line["character_id"]],
        "dialogue": {
            "character_id": line["character_id"],
            "text": line["text"],
            "source_text": None,
            "rewrite_count": 0,
            "emotion": line.get("delivery"),
            "performance": line.get("performance"),
        },
        "audio": {
            "status": "pending",
            "provider": provider_for_voice(
                cast_entry.get("voice_id"), cast_entry.get("tts_provider")
            ),
            "voice_id": cast_entry.get("voice_id"),
            "speed": 1.0,
        },
        "timing": {"trim": {"strategy": "tail", "head_s": 0.0, "tail_s": 0.0}},
        "still": {
            "status": "pending",
            "aspect_ratio": g["aspect_ratio"],
            "attempts": 0,
        },
        "video": {
            "status": "pending",
            "provider": provider,
            "model": model,
            "has_audio_track": False,
            "audio_stripped": False,
            "attempts": 0,
        },
        "lipsync": {"required": False, "status": "skipped"},
        "subtitle": {"text": line["text"]},
        "transition": {"kind": "cut", "duration_s": 0.0, "note": None},
    }
