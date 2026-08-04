"""Luoxia-Video app bridge: map Studio projects onto beats/timeline artifacts.

Authority:
  - beats  → keep/drop content selection
  - timeline → sole duration source (audio-first)

Identity layout (aligned with pipeline._resolve_duration_from_timeline):
  work_id    = series_id if present else script_id
  episode_id = script_id   (timeline lives at output/<script_id>/timeline.json)
"""
from __future__ import annotations

import os
import shutil
import time
import uuid
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from src.apps.comic_gen.models import (
    Character,
    GenerationStatus,
    Script,
    StoryboardFrame,
)
from src.luoxia.beats.analyzer import analyze_novel
from src.luoxia.beats.io import load_beats, save_beats
from src.luoxia.beats.repairs import StrictRepairError
from src.luoxia.beats.selector import select_beats
from src.luoxia.beats.to_timeline import BridgeError, build_timeline_draft
from src.luoxia.beats.validator import BeatsValidationError, validate_beats
from src.luoxia.llm.client import LuoxiaLLM
from src.luoxia.paths import beats_path, timeline_frozen_path, timeline_path
from src.luoxia.timeline.freeze import BudgetExceededError, freeze_timeline
from src.luoxia.timeline.io import load_timeline, save_timeline
from src.luoxia.timeline.solver import solve_timeline
from src.luoxia.timeline.validator import TimelineValidationError, validate_timeline

OUTPUT_ROOT = Path("output")


def work_id_for(script: Script) -> str:
    return script.series_id or script.id


def episode_id_for(script: Script) -> str:
    return script.id


def _bpath(script: Script) -> Path:
    return beats_path(OUTPUT_ROOT, work_id_for(script))


def _tpath(script: Script) -> Path:
    return timeline_path(OUTPUT_ROOT, episode_id_for(script))


def _media_url(local_path: Optional[str]) -> Optional[str]:
    if not local_path:
        return None
    p = Path(local_path)
    # Prefer relative path under output/ for /files static mount.
    try:
        rel = p.as_posix()
        if rel.startswith("output/"):
            return f"/files/{rel[len('output/'):]}"
        # Absolute under cwd/output
        cwd_out = (Path.cwd() / "output").resolve()
        resolved = p.resolve()
        if str(resolved).startswith(str(cwd_out)):
            return f"/files/{resolved.relative_to(cwd_out).as_posix()}"
    except Exception:
        pass
    name = p.name
    return f"/files/{name}" if name else None


def _ensure_episode_id(doc: Dict[str, Any], script: Script) -> str:
    """Force the target episode_id to equal script.id so timeline path matches duration resolver."""
    eid = episode_id_for(script)
    episodes = doc.get("episodes") or []
    if not episodes:
        return eid
    # Prefer matching episode_number when series multi-pack; else first.
    chosen = None
    if script.episode_number is not None:
        chosen = next(
            (e for e in episodes if e.get("episode_no") == script.episode_number),
            None,
        )
    if chosen is None:
        chosen = episodes[0]
    old_id = chosen.get("episode_id")
    if old_id != eid:
        chosen["episode_id"] = eid
        # Avoid duplicate ids if another episode already used script.id
        for e in episodes:
            if e is not chosen and e.get("episode_id") == eid:
                e["episode_id"] = f"{old_id or 'ep'}_alt"
    return eid


def load_beats_doc(script: Script) -> Optional[Dict[str, Any]]:
    path = _bpath(script)
    if not path.is_file():
        return None
    return load_beats(path)


def load_timeline_doc(script: Script) -> Optional[Dict[str, Any]]:
    path = _tpath(script)
    if not path.is_file():
        frozen = timeline_frozen_path(OUTPUT_ROOT, episode_id_for(script))
        if frozen.is_file():
            return load_timeline(frozen)
        return None
    return load_timeline(path)


def public_status(script: Script) -> Dict[str, Any]:
    """Aggregated status for the pipeline UI."""
    beats = load_beats_doc(script)
    timeline = load_timeline_doc(script)
    quality = (beats or {}).get("quality") or {}
    cast_media = []
    if beats:
        for c in beats.get("cast") or []:
            ref = c.get("reference_image_path")
            cast_media.append(
                {
                    "character_id": c.get("character_id"),
                    "display_name": c.get("display_name"),
                    "appearance": c.get("appearance"),
                    "reference_image_path": ref,
                    "reference_image_url": _media_url(ref),
                }
            )
    shots_media = []
    if timeline:
        for s in timeline.get("shots") or []:
            still = s.get("still") or {}
            video = s.get("video") or {}
            shots_media.append(
                {
                    "shot_id": s.get("shot_id"),
                    "index": s.get("index"),
                    "type": s.get("type"),
                    "timing_driver": s.get("timing_driver"),
                    "request_duration_s": (s.get("timing") or {}).get("request_duration_s"),
                    "target_duration_s": (s.get("timing") or {}).get("target_duration_s"),
                    "dialogue": s.get("dialogue"),
                    "still_status": still.get("status"),
                    "still_url": _media_url(still.get("local_path")),
                    "video_status": video.get("status"),
                    "video_url": _media_url(video.get("local_path") or video.get("url")),
                    "audio_url": _media_url((s.get("audio") or {}).get("local_path")),
                }
            )
    ep_root = OUTPUT_ROOT / episode_id_for(script)
    final_path = ep_root / "final.mp4"
    return {
        "work_id": work_id_for(script),
        "episode_id": episode_id_for(script),
        "beats_path": str(_bpath(script)) if beats else None,
        "timeline_path": str(_tpath(script)) if timeline else None,
        "beats_phase": (beats or {}).get("phase"),
        "timeline_phase": (timeline or {}).get("phase"),
        "title": (beats or {}).get("title") or script.title,
        "quality": quality,
        "beats": (beats or {}).get("beats") or [],
        "episodes": (beats or {}).get("episodes") or [],
        "cast": cast_media or (beats or {}).get("cast") or [],
        "repairs": (beats or {}).get("repairs") or [],
        "shots": shots_media,
        "final_video_url": _media_url(str(final_path)) if final_path.is_file() else (
            script.merged_video_url
        ),
        "cost": (timeline or {}).get("cost"),
        "has_beats": beats is not None,
        "has_timeline": timeline is not None,
    }


def analyze_project_text(
    script: Script,
    *,
    text: Optional[str] = None,
    resume: bool = False,
    max_repair_severity: Optional[str] = "medium",
) -> Dict[str, Any]:
    """Novel/script → scored+selected beats for this work."""
    body = (text if text is not None else script.original_text) or ""
    if not body.strip():
        raise ValueError("empty script text")

    bpath = _bpath(script)
    wid = work_id_for(script)

    if resume and bpath.is_file():
        doc = load_beats(bpath)
    else:
        llm = LuoxiaLLM()
        doc = analyze_novel(
            body,
            work_id=wid,
            title=script.title,
            source_uri=f"project:{script.id}",
            llm=llm,
        )
        save_beats(bpath, doc)

    if doc.get("phase") in {"draft", "scored"} or not doc.get("beats_hash"):
        try:
            select_beats(doc, actor="app:analyze", max_repair_severity=max_repair_severity)
        except StrictRepairError:
            save_beats(bpath, doc)
            raise
        finally:
            save_beats(bpath, doc)
        validate_beats(doc)
    else:
        validate_beats(doc)

    _ensure_episode_id(doc, script)
    save_beats(bpath, doc)
    return public_status(script)


def select_project_beats(
    script: Script,
    *,
    max_repair_severity: Optional[str] = "medium",
    decisions: Optional[List[Dict[str, Any]]] = None,
) -> Dict[str, Any]:
    """Apply optional human keep/drop overrides then re-select/pack."""
    doc = load_beats_doc(script)
    if not doc:
        raise FileNotFoundError("beats.json missing — run analyze first")

    if decisions:
        by_id = {b.get("beat_id"): b for b in doc.get("beats") or []}
        for d in decisions:
            bid = d.get("beat_id")
            beat = by_id.get(bid)
            if not beat:
                continue
            if d.get("decision") in {"keep", "compress", "drop"}:
                beat["decision"] = d["decision"]
                beat["decision_locked"] = True
            if "lines" in d and isinstance(d["lines"], list):
                beat["lines"] = d["lines"]
            if d.get("summary") is not None:
                beat["summary"] = d["summary"]

    # Unlock phase so select can re-hash
    if doc.get("phase") in {"selected", "delivered"}:
        doc["phase"] = "scored"
        doc["beats_hash"] = None

    try:
        select_beats(doc, actor="app:select", max_repair_severity=max_repair_severity)
    except StrictRepairError:
        save_beats(_bpath(script), doc)
        raise
    finally:
        save_beats(_bpath(script), doc)

    validate_beats(doc)
    _ensure_episode_id(doc, script)
    save_beats(_bpath(script), doc)
    return public_status(script)


def bridge_to_timeline(
    script: Script,
    *,
    provider: str = "xai",
    model: str = "grok-imagine-video-1.5",
    budget_usd: float = 10.0,
) -> Tuple[Dict[str, Any], Script]:
    """selected beats → draft timeline + sync frames onto Script."""
    doc = load_beats_doc(script)
    if not doc:
        raise FileNotFoundError("beats.json missing — run analyze first")
    if doc.get("phase") not in {"selected", "delivered"}:
        raise BridgeError(f"beats phase must be selected/delivered, got {doc.get('phase')}")

    eid = _ensure_episode_id(doc, script)
    save_beats(_bpath(script), doc)

    tl = build_timeline_draft(doc, eid, provider=provider, model=model)
    tl.setdefault("cost", {})["budget_ceiling_usd"] = budget_usd
    # Identity alignment
    tl["episode_id"] = eid
    tl["project_id"] = work_id_for(script)
    save_timeline(_tpath(script), tl)

    if doc.get("phase") == "selected":
        doc["phase"] = "delivered"
        save_beats(_bpath(script), doc)

    script = _sync_script_from_luoxia(script, doc, tl)
    return public_status(script), script


def solve_audio(script: Script) -> Tuple[Dict[str, Any], Script]:
    tl = load_timeline_doc(script)
    if not tl:
        raise FileNotFoundError("timeline.json missing — run bridge first")
    if tl.get("phase") != "draft":
        return public_status(script), script

    from src.luoxia.rewrite import make_rewrite_fn
    from src.luoxia.speech import make_tts_synthesize

    ep_root = OUTPUT_ROOT / episode_id_for(script)
    synthesize = make_tts_synthesize(ep_root, tl)

    llm = LuoxiaLLM()
    solve_timeline(tl, synthesize=synthesize, rewrite=make_rewrite_fn(llm))
    validate_timeline(tl)
    save_timeline(_tpath(script), tl)
    script = _sync_script_from_luoxia(script, load_beats_doc(script), tl)
    return public_status(script), script


def freeze_episode(script: Script, *, budget_usd: Optional[float] = None) -> Dict[str, Any]:
    tl = load_timeline_doc(script)
    if not tl:
        raise FileNotFoundError("timeline.json missing")
    if budget_usd is not None:
        tl.setdefault("cost", {})["budget_ceiling_usd"] = budget_usd
    freeze_timeline(
        tl,
        frozen_path=timeline_frozen_path(OUTPUT_ROOT, episode_id_for(script)),
        actor="app:freeze",
    )
    save_timeline(_tpath(script), tl)
    return public_status(script)


def update_cast_reference(
    script: Script,
    character_id: str,
    source_file: str,
) -> Dict[str, Any]:
    """Copy uploaded image into work characters/ and point beats cast at it."""
    doc = load_beats_doc(script)
    if not doc:
        raise FileNotFoundError("beats.json missing")
    dest_dir = OUTPUT_ROOT / work_id_for(script) / "characters"
    dest_dir.mkdir(parents=True, exist_ok=True)
    ext = Path(source_file).suffix or ".png"
    dest = dest_dir / f"{character_id}{ext}"
    shutil.copy2(source_file, dest)
    for c in doc.get("cast") or []:
        if c.get("character_id") == character_id:
            c["reference_image_path"] = str(dest).replace("\\", "/")
            break
    else:
        raise KeyError(f"character_id={character_id} not in cast")
    save_beats(_bpath(script), doc)

    # Mirror onto Script.characters when id matches
    for ch in script.characters:
        if ch.id == character_id or getattr(ch, "name", None) == character_id:
            url = _media_url(str(dest))
            if url:
                ch.image_url = url
                ch.avatar_url = url
            break
    return public_status(script)


def _sync_script_from_luoxia(
    script: Script,
    beats: Optional[Dict[str, Any]],
    timeline: Optional[Dict[str, Any]],
) -> Script:
    """Push cast + shots into Script so Cast / Storyboard / Assembly stay populated."""
    if beats:
        existing = {c.id: c for c in script.characters}
        new_chars: List[Character] = []
        for c in beats.get("cast") or []:
            cid = c.get("character_id") or str(uuid.uuid4())[:8]
            ref = c.get("reference_image_path")
            url = _media_url(ref)
            if cid in existing:
                ch = existing[cid]
                ch.name = c.get("display_name") or ch.name
                ch.description = c.get("appearance") or ch.description
                if c.get("voice_id"):
                    ch.voice_id = c.get("voice_id")
                if url:
                    ch.image_url = url
                    ch.avatar_url = url
                new_chars.append(ch)
            else:
                ch = Character(
                    id=cid,
                    name=c.get("display_name") or cid,
                    description=c.get("appearance") or "",
                    voice_id=c.get("voice_id"),
                    image_url=url,
                    avatar_url=url,
                )
                new_chars.append(ch)
        if new_chars:
            script.characters = new_chars

    if timeline:
        frames: List[StoryboardFrame] = []
        for s in timeline.get("shots") or []:
            still = s.get("still") or {}
            video = s.get("video") or {}
            dialogue = s.get("dialogue") or {}
            still_url = _media_url(still.get("local_path"))
            video_url = _media_url(video.get("local_path") or video.get("url"))
            audio_url = _media_url((s.get("audio") or {}).get("local_path"))
            req_dur = (s.get("timing") or {}).get("request_duration_s")
            prompt = still.get("prompt") or (video.get("request") or {}).get("prompt") or ""
            frames.append(
                StoryboardFrame(
                    id=s.get("shot_id") or f"shot_{s.get('index', 0)}",
                    scene_id=s.get("scene_id") or "scene_default",
                    character_ids=list(s.get("characters") or []),
                    action_description=prompt,
                    visual_description=prompt,
                    dialogue=dialogue.get("text"),
                    speaker=dialogue.get("character_id"),
                    shot_size=s.get("shot_size"),
                    duration=int(req_dur) if req_dur is not None else None,
                    image_url=still_url,
                    image_prompt=prompt,
                    video_prompt=(video.get("request") or {}).get("prompt") or prompt,
                    video_url=video_url,
                    audio_url=audio_url,
                    status=GenerationStatus.COMPLETED if video_url or still_url else GenerationStatus.PENDING,
                )
            )
        if frames:
            script.frames = frames

        final = OUTPUT_ROOT / episode_id_for(script) / "final.mp4"
        if final.is_file():
            script.merged_video_url = _media_url(str(final))

    script.updated_at = time.time()
    return script


__all__ = [
    "analyze_project_text",
    "select_project_beats",
    "bridge_to_timeline",
    "solve_audio",
    "freeze_episode",
    "update_cast_reference",
    "public_status",
    "load_beats_doc",
    "load_timeline_doc",
    "work_id_for",
    "episode_id_for",
    "StrictRepairError",
    "BridgeError",
    "BudgetExceededError",
    "BeatsValidationError",
    "TimelineValidationError",
]
