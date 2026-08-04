from __future__ import annotations

from typing import Any, Dict, List, Optional

from src.luoxia.beats.hashing import compute_beats_hash
from src.luoxia.beats.validator import RETAINED

DEFAULT_GLOBAL: Dict[str, Any] = {
    "fps": 25,
    "aspect_ratio": "9:16",
    "resolution": "720p",
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

        visual = beat.get("visual") or None
        lines = beat.get("lines") or []
        # The action shot shows whoever speaks in this beat, so it gets their portraits too.
        beat_characters = []
        for line in lines:
            cid = line.get("character_id")
            if cid and cid not in beat_characters:
                beat_characters.append(cid)
        if visual:
            shots.append(
                _visual_shot(
                    episode_id, beat, visual, g, provider, model,
                    has_lines=bool(lines), characters=beat_characters,
                )
            )
        for n, line in enumerate(lines, start=1):
            cid = line.get("character_id")
            if cid not in cast_by_id:
                raise BridgeError(f"beat '{beat_id}' line {n}: character '{cid}' not in cast")
            used_characters.add(cid)
            shots.append(_dialogue_shot(episode_id, beat, line, n, cast_by_id[cid], g, provider, model))

    if not shots:
        raise BridgeError(f"episode {episode_id} produced no shots")

    for i, shot in enumerate(shots):
        shot["index"] = i

    return {
        "schema_version": "1.0.0",
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
        "reference_image_asset_id": entry.get("reference_image_path"),
    }


def _visual_shot(
    episode_id: str,
    beat: Dict[str, Any],
    visual: Dict[str, Any],
    g: Dict[str, Any],
    provider: str,
    model: str,
    *,
    has_lines: bool,
    characters: Optional[List[str]] = None,
) -> Dict[str, Any]:
    return {
        "shot_id": f"{episode_id}_{beat['beat_id']}_v",
        "index": 0,
        "type": "action" if has_lines else "transition",
        "timing_driver": "rhythm",
        "scene_id": visual.get("scene_id") or beat.get("scene_id"),
        "shot_size": visual.get("shot_size"),
        "characters": list(characters or []),
        "timing": {
            "target_duration_s": float(visual.get("action_duration_s") or g["default_action_duration_s"]),
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
        "subtitle": {"text": None, "description": beat.get("summary")},
    }


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
        },
        "audio": {
            "status": "pending",
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
    }
