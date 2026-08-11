from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Dict, Optional

from src.luoxia.beats.analyzer import analyze_novel, analyze_novel_file
from src.luoxia.beats.io import load_beats, save_beats
from src.luoxia.beats.validator import validate_beats
from src.luoxia.compose.assembler import assemble_episode
from src.luoxia.env import load_env_once
from src.luoxia.llm.client import LuoxiaLLM
from src.luoxia.lipsync.runner import apply_lipsync
from src.luoxia.orchestration import ProductionOrchestrator
from src.luoxia.paths import (
    beats_path,
    episode_dir,
    project_dir,
    timeline_frozen_path,
    timeline_path,
)
from src.luoxia.timeline.freeze import freeze_timeline
from src.luoxia.timeline.io import load_timeline, save_timeline
from src.output_contract import DEFAULT_OUTPUT_ROOT


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
    output_root: str | Path = DEFAULT_OUTPUT_ROOT,
    work_id: Optional[str] = None,
    title: Optional[str] = None,
    episode_id: Optional[str] = None,
    episode_no: int = 1,
    budget_usd: float = 10.0,
    provider: str = "xai",
    model: str = "grok-imagine-video-1.5",
    skip_compose: bool = False,
    resume: bool = True,
    max_repair_severity: Optional[str] = None,
    lock_faces: bool = True,
    beats_overrides: Optional[Dict[str, Any]] = None,
    on_step: Optional[Callable[[str, Dict[str, Any]], None]] = None,
    render_videos: Optional[Callable[..., Any]] = None,
) -> RunResult:
    """Three-agent audio-first pipeline ending in deterministic assembly.

    Requires:
      - the configured dialogue provider (Doubao Seed-TTS 2.0 by default)
      - the local MuseTalk 1.5 runtime for required dialogue close-ups
      - resolvable xAI credentials for LLM + stills + video: either session login
        (subscription pool) or LUOXIA_AUTH_MODE=api_key with XAI_API_KEY
      - DASHSCOPE_API_KEY only when the legacy DashScope LLM/provider path is selected

    `max_repair_severity` refuses to continue when the harness had to patch the model's
    output too heavily; see beats_doc["quality"]. `lock_faces` generates one portrait per
    character up front and reuses it as an I2I reference in every shot. `beats_overrides`
    tunes the selection budget (keep_threshold, max_compression_ratio, ...) per work.
    `render_videos` overrides the render step; only tests should need it.
    """
    load_env_once()
    root = Path(output_root)
    novel_path = Path(novel_path)
    text = novel_path.read_text(encoding="utf-8")
    wid = work_id or novel_path.stem.replace(" ", "_")
    llm = LuoxiaLLM()
    orchestrator = ProductionOrchestrator.default(llm=llm)

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
        doc = orchestrator.language.adapt(
            text,
            work_id=wid,
            title=title or novel_path.stem,
            source_uri=str(novel_path),
            global_overrides=beats_overrides,
        )
        save_beats(bpath, doc)

    # --- 2. language selection + voice direction + visual direction ---
    if doc.get("phase") in {"draft", "scored"} or not doc.get("beats_hash"):
        note("direct_beats", agents=("language", "voice", "visual"))
        try:
            orchestrator.direct_existing_beats(
                doc,
                max_repair_severity=max_repair_severity,
            )
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
            orchestrator.visual.ensure_character_sheets(
                doc["cast"],
                output_root=project_dir(root, wid),
            )
            save_beats(bpath, doc)

    # --- 3. pick episode ---
    episodes = doc.get("episodes") or []
    if not episodes:
        raise RuntimeError("selection produced no episodes")
    if episode_id:
        ep = next((e for e in episodes if e["episode_id"] == episode_id), None)
        if ep is None:
            known = [item["episode_id"] for item in episodes]
            raise RuntimeError(f"episode_id={episode_id} not in {known}")
    else:
        ep = next((e for e in episodes if e.get("episode_no") == episode_no), episodes[0])
    eid = ep["episode_id"]
    note("episode", episode_id=eid, beats=len(ep.get("beat_ids") or []))

    # --- 4. bridge ---
    tpath = timeline_path(root, eid)
    ep_root = episode_dir(root, eid)
    if resume and tpath.is_file():
        tl = load_timeline(tpath)
        note("timeline_resume", phase=tl.get("phase"))
    else:
        note("bridge")
        tl = orchestrator.build_timeline(
            doc,
            eid,
            provider=provider,
            model=model,
        )
        tl.setdefault("cost", {})["budget_ceiling_usd"] = budget_usd
        save_timeline(tpath, tl)
        # mark beats delivered once a draft exists
        if doc.get("phase") == "selected":
            doc["phase"] = "delivered"
            save_beats(bpath, doc)

    # --- 5. voice agent locks measured audio first ---
    if tl.get("phase") == "draft":
        note("voice_lock")
        try:
            orchestrator.lock_audio(tl, episode_dir=ep_root)
        finally:
            save_timeline(tpath, tl)

    # --- 6. visual agent now sees exact durations and owns motion + transitions ---
    has_visual_direction = any(
        item.get("actor") == "agent:visual"
        and item.get("action") == "direct_timeline"
        for item in (tl.get("audit") or [])
    )
    has_rendered_stills = any(
        (shot.get("still") or {}).get("status") == "ready"
        for shot in (tl.get("shots") or [])
    )
    if (
        tl.get("phase") == "audio_locked"
        and not has_visual_direction
        and not has_rendered_stills
    ):
        note("visual_direction")
        try:
            orchestrator.visual.direct_timeline(tl)
        finally:
            save_timeline(tpath, tl)

    # --- 7. visual agent renders stills ---
    if tl.get("phase") == "audio_locked":
        missing_still = any(
            (s.get("still") or {}).get("status") != "ready"
            or not Path((s.get("still") or {}).get("local_path") or "").is_file()
            for s in tl["shots"]
        )
        if missing_still:
            note("stills")
            orchestrator.visual.render_stills(tl, output_root=ep_root)
            save_timeline(tpath, tl)

    # --- 8. freeze ---
    if tl.get("phase") == "audio_locked":
        note("freeze", budget=budget_usd)
        tl.setdefault("cost", {})["budget_ceiling_usd"] = budget_usd
        freeze_timeline(tl, frozen_path=timeline_frozen_path(root, eid), actor="pipeline:run")
        save_timeline(tpath, tl)

    # --- 9. video ---
    if tl.get("phase") in {"frozen", "rendering"}:
        if render_videos is None:
            assert_video_credentials()
        note("video_cloud")
        orchestrator.visual.render_videos(
            tl,
            output_root=ep_root,
            timeline_path=tpath,
            renderer=render_videos,
        )
        save_timeline(tpath, tl)

    # --- 9b. audio-driven mouth pass ---
    needs_lipsync = any(
        (shot.get("lipsync") or {}).get("required")
        and (shot.get("lipsync") or {}).get("status") != "done"
        for shot in tl.get("shots") or []
    )
    if needs_lipsync:
        note("lipsync")
        try:
            apply_lipsync(tl, output_root=ep_root)
        finally:
            # Preserve the exact failed shot/reason before propagating the hard failure.
            save_timeline(tpath, tl)

    # --- 10. compose ---
    final = ep_root / "final.mp4"
    if not skip_compose:
        note("compose")
        try:
            assemble_episode(tl, output_path=final, work_dir=ep_root / "_compose")
        finally:
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


def assert_video_credentials() -> None:
    """Refuse to start rendering when no credential can pay for it.

    This used to be `not os.getenv("XAI_API_KEY")`, which is blind to session auth: a
    signed-in subscription-pool user looked unconfigured, and the pipeline quietly
    swapped in held stills — producing a silent slideshow that still reported success.
    Ask the auth layer instead, and fail loudly.
    """
    from src.auth.resolver import status

    st = status()
    if st.mode == "offline":
        raise RuntimeError(
            "auth mode is offline, so cloud video cannot run. Log in to the subscription "
            "pool or set LUOXIA_AUTH_MODE=api_key with XAI_API_KEY."
        )
    if not st.signed_in:
        raise RuntimeError(
            f"no video credential resolved (auth mode={st.mode}, provider={st.provider}): "
            f"{st.message}"
        )


# Re-export for callers that import analyzer helpers from pipeline.
__all__ = ["run_from_novel", "RunResult", "analyze_novel", "analyze_novel_file"]
