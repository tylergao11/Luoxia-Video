from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Dict, Optional

from src.luoxia.beats.analyzer import analyze_novel, analyze_novel_file
from src.luoxia.beats.io import load_beats, save_beats
from src.luoxia.beats.selector import select_beats
from src.luoxia.beats.to_timeline import build_timeline_draft
from src.luoxia.beats.validator import validate_beats
from src.luoxia.compose.assembler import assemble_episode
from src.luoxia.llm.client import LuoxiaLLM
from src.luoxia.paths import beats_path, timeline_frozen_path, timeline_path
from src.luoxia.render.runner import render_timeline_videos
from src.luoxia.render.still_hold import render_still_hold_videos
from src.luoxia.rewrite import make_rewrite_fn
from src.luoxia.stills.characters import ensure_character_sheets
from src.luoxia.stills.prompts import polish_timeline_prompts
from src.luoxia.stills.runner import render_timeline_stills
from src.luoxia.timeline.freeze import freeze_timeline
from src.luoxia.timeline.io import load_timeline, save_timeline
from src.luoxia.timeline.solver import solve_timeline
from src.luoxia.timeline.validator import validate_timeline


@dataclass
class RunResult:
    work_id: str
    episode_id: str
    beats_path: Path
    timeline_path: Path
    final_path: Path
    phase: str


def run_from_novel(
    novel_path: str | Path,
    *,
    output_root: str | Path = "output",
    work_id: Optional[str] = None,
    title: Optional[str] = None,
    episode_id: Optional[str] = None,
    episode_no: int = 1,
    budget_usd: float = 10.0,
    provider: str = "xai",
    model: str = "grok-imagine-video-1.5",
    still_hold: bool = False,
    skip_compose: bool = False,
    resume: bool = True,
    max_repair_severity: Optional[str] = None,
    lock_faces: bool = True,
    beats_overrides: Optional[Dict[str, Any]] = None,
    on_step: Optional[Callable[[str, Dict[str, Any]], None]] = None,
) -> RunResult:
    """Full audio-first pipeline: novel → beats → timeline → audio → stills → video → final.mp4.

    Requires:
      - DASHSCOPE_API_KEY for LLM + TTS + stills
      - XAI_API_KEY for Grok video (unless still_hold=True)

    `max_repair_severity` refuses to continue when the harness had to patch the model's
    output too heavily; see beats_doc["quality"]. `lock_faces` generates one portrait per
    character up front and reuses it as an I2I reference in every shot. `beats_overrides`
    tunes the selection budget (keep_threshold, max_compression_ratio, ...) per work.
    """
    root = Path(output_root)
    novel_path = Path(novel_path)
    text = novel_path.read_text(encoding="utf-8")
    wid = work_id or novel_path.stem.replace(" ", "_")
    llm = LuoxiaLLM()

    def note(step: str, **extra):
        if on_step:
            on_step(step, extra)

    # --- 1. analyze / load beats ---
    bpath = beats_path(root, wid)
    if resume and bpath.is_file():
        doc = load_beats(bpath)
        note("beats_resume", path=str(bpath), phase=doc.get("phase"))
    else:
        note("analyze", chars=len(text))
        doc = analyze_novel(
            text,
            work_id=wid,
            title=title or novel_path.stem,
            source_uri=str(novel_path),
            llm=llm,
            global_overrides=beats_overrides,
        )
        save_beats(bpath, doc)

    # --- 2. select ---
    if doc.get("phase") in {"draft", "scored"} or not doc.get("beats_hash"):
        note("select")
        try:
            select_beats(doc, actor="pipeline:run", max_repair_severity=max_repair_severity)
        finally:
            # Persist even on a strict refusal so the ledger is inspectable.
            save_beats(bpath, doc)
        validate_beats(doc)
    else:
        validate_beats(doc)
    q = doc.get("quality") or {}
    note("quality", repairs=q.get("repair_count"), worst=q.get("worst_severity"))

    # --- 2b. character sheets (one locked portrait per character) ---
    if lock_faces and any((c.get("appearance") or "").strip() for c in doc.get("cast") or []):
        needs_sheet = any(
            (c.get("appearance") or "").strip()
            and not Path(c.get("reference_image_path") or "").is_file()
            for c in doc["cast"]
        )
        if needs_sheet:
            note("character_sheets", cast=len(doc["cast"]))
            ensure_character_sheets(doc["cast"], output_root=root / wid)
            save_beats(bpath, doc)

    # --- 3. pick episode ---
    episodes = doc.get("episodes") or []
    if not episodes:
        raise RuntimeError("selection produced no episodes")
    if episode_id:
        ep = next((e for e in episodes if e["episode_id"] == episode_id), None)
        if ep is None:
            raise RuntimeError(f"episode_id={episode_id} not in {[e['episode_id'] for e in episodes]}")
    else:
        ep = next((e for e in episodes if e.get("episode_no") == episode_no), episodes[0])
    eid = ep["episode_id"]
    note("episode", episode_id=eid, beats=len(ep.get("beat_ids") or []))

    # --- 4. bridge ---
    tpath = timeline_path(root, eid)
    ep_root = root / eid
    if resume and tpath.is_file():
        tl = load_timeline(tpath)
        note("timeline_resume", phase=tl.get("phase"))
    else:
        note("bridge")
        tl = build_timeline_draft(doc, eid, provider=provider, model=model)
        tl.setdefault("cost", {})["budget_ceiling_usd"] = budget_usd
        save_timeline(tpath, tl)
        # mark beats delivered once a draft exists
        if doc.get("phase") == "selected":
            doc["phase"] = "delivered"
            save_beats(bpath, doc)

    # --- 5. polish prompts (cheap, before audio) ---
    if tl.get("phase") == "draft":
        note("polish_prompts")
        polish_timeline_prompts(tl, llm=llm)
        save_timeline(tpath, tl)

    # --- 6. solve audio ---
    if tl.get("phase") == "draft":
        note("solve")
        synthesize = _make_tts(ep_root, tl)
        rewrite = make_rewrite_fn(llm)
        solve_timeline(tl, synthesize=synthesize, rewrite=rewrite)
        validate_timeline(tl)
        save_timeline(tpath, tl)

    # --- 7. stills ---
    if tl.get("phase") == "audio_locked":
        missing_still = any(
            (s.get("still") or {}).get("status") != "ready"
            or not Path((s.get("still") or {}).get("local_path") or "").is_file()
            for s in tl["shots"]
        )
        if missing_still:
            note("stills")
            render_timeline_stills(tl, output_root=ep_root)
            save_timeline(tpath, tl)

    # --- 8. freeze ---
    if tl.get("phase") == "audio_locked":
        note("freeze", budget=budget_usd)
        tl.setdefault("cost", {})["budget_ceiling_usd"] = budget_usd
        freeze_timeline(tl, frozen_path=timeline_frozen_path(root, eid), actor="pipeline:run")
        save_timeline(tpath, tl)

    # --- 9. video ---
    if tl.get("phase") in {"frozen", "rendering"}:
        use_hold = still_hold or not os.getenv("XAI_API_KEY")
        if use_hold and provider == "xai" and not still_hold:
            note("video_fallback", reason="XAI_API_KEY missing → still-hold")
        if still_hold or (use_hold and provider == "xai"):
            note("video_still_hold")
            render_still_hold_videos(tl, output_root=ep_root)
        else:
            note("video_cloud")
            render_timeline_videos(tl, output_root=ep_root, timeline_path=tpath)
        save_timeline(tpath, tl)

    # --- 10. compose ---
    final = ep_root / "final.mp4"
    if not skip_compose:
        note("compose")
        assemble_episode(tl, output_path=final, work_dir=ep_root / "_compose")
        save_timeline(tpath, tl)

    note("done", final=str(final), phase=tl.get("phase"))
    return RunResult(
        work_id=wid,
        episode_id=eid,
        beats_path=bpath,
        timeline_path=tpath,
        final_path=final,
        phase=str(tl.get("phase")),
    )


def _make_tts(episode_dir: Path, timeline: dict):
    from src.audio.tts import TTSProcessor

    tts = TTSProcessor()
    cast_voices = {
        c.get("character_id"): c.get("voice_id")
        for c in (timeline.get("cast") or [])
        if c.get("character_id")
    }

    def synthesize(shot, speed: float):
        dialogue = shot.get("dialogue") or {}
        text = dialogue.get("text") or ""
        voice = (shot.get("audio") or {}).get("voice_id") or cast_voices.get(dialogue.get("character_id"))
        if not voice:
            raise ValueError(f"{shot.get('shot_id')}: no voice_id in audio or cast")
        out = episode_dir / "audio" / f"{shot['shot_id']}.wav"
        out.parent.mkdir(parents=True, exist_ok=True)
        path, measured, digest = tts.synthesize_measured(
            text=text,
            output_path=str(out),
            voice=voice,
            speech_rate=speed,
            instructions=dialogue.get("emotion"),
        )
        return measured, path, digest

    return synthesize


# Re-export for callers that import analyzer helpers from pipeline.
__all__ = ["run_from_novel", "RunResult", "analyze_novel", "analyze_novel_file"]
