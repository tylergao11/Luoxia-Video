from __future__ import annotations

import argparse
import sys
from pathlib import Path

from src.luoxia.beats.io import load_beats, save_beats
from src.luoxia.beats.selector import select_beats
from src.luoxia.beats.to_timeline import build_timeline_draft
from src.luoxia.beats.validator import BeatsValidationError, validate_beats
from src.luoxia.env import load_env_once
from src.luoxia.paths import (
    REPO_ROOT,
    episode_dir,
    project_dir,
    timeline_frozen_path,
    timeline_path,
)
from src.luoxia.timeline.cost import estimate_timeline_cost
from src.luoxia.timeline.freeze import BudgetExceededError, freeze_timeline, unfreeze_timeline
from src.luoxia.timeline.io import load_timeline, save_timeline
from src.luoxia.timeline.solver import solve_timeline
from src.luoxia.timeline.validator import TimelineValidationError, validate_timeline
from src.output_contract import DEFAULT_OUTPUT_ROOT


def main(argv: list[str] | None = None) -> int:
    # Every subcommand that touches TTS, stills or video needs .env; nothing else in the
    # luoxia package loads it.
    load_env_once()
    parser = argparse.ArgumentParser(prog="luoxia", description="Luoxia audio-first short-drama harness")
    parser.add_argument(
        "--output-root",
        default=str(DEFAULT_OUTPUT_ROOT),
        help=f"Managed output root (default: {DEFAULT_OUTPUT_ROOT})",
    )
    sub = parser.add_subparsers(dest="cmd", required=True)

    p_bval = sub.add_parser("beats-validate", help="Validate a beats.json")
    p_bval.add_argument("beats")

    p_bsel = sub.add_parser("beats-select", help="Apply thresholds, rescue setups, pack episodes, lock selection")
    p_bsel.add_argument("beats")
    p_bsel.add_argument("--actor", default="selector:threshold")
    p_bsel.add_argument("--no-plan", action="store_true", help="Keep existing episodes instead of repacking")
    p_bsel.add_argument(
        "--max-repair-severity",
        choices=["low", "medium", "high"],
        default=None,
        help="Refuse the selection if auto-repairs exceed this severity (medium = no invented dialogue)",
    )

    p_bridge = sub.add_parser("beats-bridge", help="Build a draft timeline for one episode from beats.json")
    p_bridge.add_argument("beats")
    p_bridge.add_argument("episode_id")
    p_bridge.add_argument("--provider", default="xai")
    p_bridge.add_argument("--model", default="grok-imagine-video-1.5")

    p_analyze = sub.add_parser("analyze", help="Slice+score a novel into scored beats.json (needs LLM)")
    p_analyze.add_argument("novel", help="Path to novel .txt")
    p_analyze.add_argument("--work-id", default=None)
    p_analyze.add_argument("--title", default=None)

    p_stills = sub.add_parser("stills", help="Generate still images for a timeline episode")
    p_stills.add_argument("episode_id")

    p_run = sub.add_parser("run", help="End-to-end: novel → final.mp4 (LLM+TTS+stills+video)")
    p_run.add_argument("novel", help="Path to novel .txt")
    p_run.add_argument("--work-id", default=None)
    p_run.add_argument("--title", default=None)
    p_run.add_argument("--episode-id", default=None)
    p_run.add_argument("--episode-no", type=int, default=1)
    p_run.add_argument("--budget", type=float, default=10.0, help="USD budget ceiling before freeze")
    p_run.add_argument("--provider", default="xai")
    p_run.add_argument("--model", default="grok-imagine-video-1.5")
    p_run.add_argument("--no-resume", action="store_true", help="Ignore existing beats/timeline and rebuild")
    p_run.add_argument("--skip-compose", action="store_true")
    p_run.add_argument(
        "--max-repair-severity",
        choices=["low", "medium", "high"],
        default=None,
        help="Stop before spending money if auto-repairs exceed this severity",
    )
    p_run.add_argument(
        "--no-lock-faces",
        action="store_true",
        help="Skip per-character reference portraits (faces will drift between shots)",
    )

    p_sheets = sub.add_parser("sheets", help="Generate one locked reference portrait per character")
    p_sheets.add_argument("beats")
    p_sheets.add_argument("--aspect", default="9:16")

    p_val = sub.add_parser("validate", help="Validate a timeline.json")
    p_val.add_argument("timeline")

    p_solve = sub.add_parser("solve", help="Solve timing from audio (requires TTS for pending audio)")
    p_solve.add_argument("episode_id")
    p_solve.add_argument("--dry-run", action="store_true", help="Only rearrange already-rendered audio")
    p_solve.add_argument("--no-rewrite", action="store_true", help="Disable LLM dialogue rewrite")

    p_freeze = sub.add_parser("freeze", help="Freeze audio_locked timeline after cost gate")
    p_freeze.add_argument("episode_id")
    p_freeze.add_argument("--actor", default="agent:cli")

    p_unfreeze = sub.add_parser("unfreeze", help="Return frozen timeline to audio_locked")
    p_unfreeze.add_argument("episode_id")
    p_unfreeze.add_argument("--reason", default="")

    p_cost = sub.add_parser("cost", help="Estimate render cost from timeline")
    p_cost.add_argument("timeline")

    p_render = sub.add_parser("render", help="Render videos from frozen timeline (idempotent)")
    p_render.add_argument("episode_id")

    p_compose = sub.add_parser("compose", help="Assemble final episode from timeline")
    p_compose.add_argument("episode_id")
    p_compose.add_argument("--out", default=None)

    p_lipsync = sub.add_parser("lipsync", help="Run required MuseTalk audio-driven lipsync")
    p_lipsync.add_argument("episode_id")

    args = parser.parse_args(argv)
    root = Path(args.output_root)

    try:
        if args.cmd == "beats-validate":
            doc = load_beats(args.beats)
            validate_beats(doc)
            print("OK")
            return 0
        if args.cmd == "beats-select":
            from src.luoxia.beats.repairs import StrictRepairError

            doc = load_beats(args.beats)
            try:
                select_beats(
                    doc,
                    actor=args.actor,
                    plan=not args.no_plan,
                    max_repair_severity=args.max_repair_severity,
                )
            except StrictRepairError as exc:
                save_beats(args.beats, doc)
                print(f"refused: {exc}")
                return 3
            validate_beats(doc)
            save_beats(args.beats, doc)
            stats = doc["selection"]
            q = doc.get("quality") or {}
            print(
                f"selected keep={stats['kept']} compress={stats['compressed']} drop={stats['dropped']} "
                f"ratio={stats['compression_ratio']:.4f} episodes={len(doc.get('episodes') or [])} "
                f"repairs={q.get('repair_count', 0)}(worst={q.get('worst_severity') or 'none'})"
            )
            return 0
        if args.cmd == "beats-bridge":
            doc = load_beats(args.beats)
            validate_beats(doc)
            draft = build_timeline_draft(
                doc, args.episode_id, provider=args.provider, model=args.model
            )
            path = timeline_path(root, args.episode_id)
            save_timeline(path, draft)
            print(f"draft -> {path} shots={len(draft['shots'])} (run `solve` next)")
            return 0
        if args.cmd == "analyze":
            from src.luoxia.beats.analyzer import analyze_novel_file
            from src.luoxia.paths import beats_path as _beats_path

            doc = analyze_novel_file(args.novel, work_id=args.work_id, title=args.title)
            path = _beats_path(root, doc["work_id"])
            save_beats(path, doc)
            q = doc.get("quality") or {}
            print(
                f"scored -> {path} beats={len(doc['beats'])} cast={len(doc['cast'])} "
                f"repairs={q.get('repair_count', 0)}(worst={q.get('worst_severity') or 'none'})"
            )
            for r in doc.get("repairs") or []:
                if r["severity"] == "high":
                    print(f"  ! {r['code']} {r.get('beat_id') or ''}: {r['detail']}")
            return 0
        if args.cmd == "sheets":
            from src.luoxia.stills.characters import ensure_character_sheets

            doc = load_beats(args.beats)
            work_root = project_dir(root, doc["work_id"])
            sheets = ensure_character_sheets(
                doc.get("cast") or [], output_root=work_root, aspect_ratio=args.aspect
            )
            save_beats(args.beats, doc)
            missing = [c["character_id"] for c in doc["cast"] if c["character_id"] not in sheets]
            print(f"sheets {len(sheets)}/{len(doc['cast'])} -> {work_root / 'characters'}")
            if missing:
                print(f"  no appearance, faces will drift: {', '.join(missing)}")
            return 0
        if args.cmd == "stills":
            from src.luoxia.stills.prompts import polish_timeline_prompts
            from src.luoxia.stills.runner import render_timeline_stills

            path = timeline_path(root, args.episode_id)
            tl = load_timeline(path)
            polish_timeline_prompts(tl, strict=True)
            render_timeline_stills(tl, output_root=episode_dir(root, args.episode_id))
            save_timeline(path, tl)
            ready = sum(1 for s in tl["shots"] if (s.get("still") or {}).get("status") == "ready")
            print(f"stills ready {ready}/{len(tl['shots'])}")
            return 0
        if args.cmd == "run":
            from src.luoxia.pipeline import run_from_novel

            def _step(name, extra):
                detail = " ".join(f"{k}={v}" for k, v in extra.items())
                print(f"[{name}] {detail}".rstrip())

            result = run_from_novel(
                args.novel,
                output_root=root,
                work_id=args.work_id,
                title=args.title,
                episode_id=args.episode_id,
                episode_no=args.episode_no,
                budget_usd=args.budget,
                provider=args.provider,
                model=args.model,
                skip_compose=args.skip_compose,
                resume=not args.no_resume,
                max_repair_severity=args.max_repair_severity,
                lock_faces=not args.no_lock_faces,
                on_step=_step,
            )
            print(f"done episode={result.episode_id} phase={result.phase} final={result.final_path}")
            return 0
        if args.cmd == "validate":
            tl = load_timeline(args.timeline)
            validate_timeline(tl)
            print("OK")
            return 0
        if args.cmd == "solve":
            from src.luoxia.rewrite import make_rewrite_fn

            path = timeline_path(root, args.episode_id)
            tl = load_timeline(path)
            synthesize = None
            if not args.dry_run:
                synthesize = _make_tts_synthesize(episode_dir(root, args.episode_id), tl)
            rewrite = None if args.no_rewrite else make_rewrite_fn()
            solve_timeline(tl, synthesize=synthesize, rewrite=rewrite)
            save_timeline(path, tl)
            print(f"solved -> {path} phase={tl['phase']}")
            return 0
        if args.cmd == "freeze":
            path = timeline_path(root, args.episode_id)
            tl = load_timeline(path)
            freeze_timeline(tl, frozen_path=timeline_frozen_path(root, args.episode_id), actor=args.actor)
            save_timeline(path, tl)
            print(f"frozen estimated_usd={tl['cost']['estimated_usd']}")
            return 0
        if args.cmd == "unfreeze":
            path = timeline_path(root, args.episode_id)
            tl = load_timeline(path)
            unfreeze_timeline(tl, reason=args.reason)
            save_timeline(path, tl)
            print("unfrozen")
            return 0
        if args.cmd == "cost":
            tl = load_timeline(args.timeline)
            est = estimate_timeline_cost(tl)
            print(est.detail_text())
            return 0
        if args.cmd == "render":
            from src.luoxia.render.runner import render_timeline_videos

            path = timeline_path(root, args.episode_id)
            tl = load_timeline(path)
            render_timeline_videos(
                tl,
                output_root=episode_dir(root, args.episode_id),
                timeline_path=path,
            )
            save_timeline(path, tl)
            print("render complete")
            return 0
        if args.cmd == "compose":
            from src.luoxia.compose.assembler import assemble_episode

            path = timeline_path(root, args.episode_id)
            tl = load_timeline(path)
            ep_root = episode_dir(root, args.episode_id)
            out = Path(args.out) if args.out else ep_root / "final.mp4"
            try:
                assemble_episode(
                    tl,
                    output_path=out,
                    work_dir=ep_root / "_compose",
                )
            finally:
                save_timeline(path, tl)
            print(f"composed -> {out}")
            return 0
        if args.cmd == "lipsync":
            from src.luoxia.lipsync.runner import apply_lipsync

            path = timeline_path(root, args.episode_id)
            tl = load_timeline(path)
            try:
                apply_lipsync(tl, output_root=episode_dir(root, args.episode_id))
            finally:
                save_timeline(path, tl)
            print("required lipsync pass complete")
            return 0
    except BudgetExceededError as exc:
        print(str(exc), file=sys.stderr)
        print(exc.detail, file=sys.stderr)
        return 2
    except (TimelineValidationError, BeatsValidationError) as exc:
        print(str(exc), file=sys.stderr)
        return 1
    except Exception as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1
    return 1


def _make_tts_synthesize(episode_dir: Path, timeline: dict):
    from src.luoxia.speech import make_tts_synthesize

    return make_tts_synthesize(episode_dir, timeline)


if __name__ == "__main__":
    # Ensure repo root on sys.path when executed as a script.
    if str(REPO_ROOT) not in sys.path:
        sys.path.insert(0, str(REPO_ROOT))
    raise SystemExit(main())
