"""Animate approved keyframes with Grok Imagine Video 1.5."""
from __future__ import annotations

import json
import sys
from pathlib import Path

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.luoxia.env import load_env_once

load_env_once()

from src.luoxia.media.ffprobe import measure_media_duration_s  # noqa: E402
from src.models.grok import GrokVideoModel  # noqa: E402
from src.output_contract import OUTPUT  # noqa: E402


ROOT = OUTPUT.sample_dir("doupo_moyan_30s")
VISUAL_MANIFEST = ROOT / "visual_manifest.json"
VIDEO_DIR = ROOT / "video"
VIDEO_MANIFEST = ROOT / "video_manifest.json"

MOTION = {
    "s01_nalan_break": (
        "One continuous cinematic shot. Begin with a restrained wide composition, then "
        "slowly dolly toward the woman in white. She speaks calmly with cold authority, "
        "subtle natural lip and eye movement. The young man in blue remains tense in the "
        "background. Elders barely shift. Realistic cloth and hair motion, no cuts, no text."
    ),
    "s02_mock_and_fist": (
        "One continuous humiliation reaction shot. The two background clansmen exchange a "
        "quiet mocking glance and restrained chuckle while the camera racks focus to the "
        "young man's clenched fist. His fingers tighten once and his jaw hardens. The woman "
        "in white stays still in the distance. No cuts, no text, no exaggerated gestures."
    ),
    "s03_xiao_declare": (
        "One continuous dramatic close shot. The young man speaks directly to his opponent "
        "for the whole shot. Start controlled and wounded, then steadily build anger and "
        "resolve. Natural continuous lip motion, subtle breathing, eyes fixed forward. A "
        "slow camera push-in reaches its strongest intensity at the end. Wind lifts only a "
        "few hair strands and the robe edge. No cuts, no text, no face change."
    ),
    "s04_nalan_react": (
        "A restrained two-second reaction close-up. The woman blinks once; her pupils tighten "
        "and her cold expression cracks for an instant before she regains control. Candlelight "
        "flickers softly in her eyes. Locked camera, no speech, no text, no face change."
    ),
    "s05_xiao_vow": (
        "One continuous low-angle vow shot. The young man speaks a short final oath with hard "
        "controlled resolve, natural lip motion, then closes his mouth and holds eye contact. "
        "A gust moves his robe and loose hair as dust crosses the backlight. Slow subtle push-in, "
        "no cuts, no text, no face change, no wild arm motion."
    ),
}


def write_manifest(items: list[dict]) -> None:
    payload = {
        "video_provider": "xai",
        "video_model": "grok-imagine-video-1.5",
        "resolution": "720p",
        "shots": items,
    }
    VIDEO_MANIFEST.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
        newline="\n",
    )


def main() -> None:
    visual = json.loads(VISUAL_MANIFEST.read_text(encoding="utf-8"))
    shots = {item["id"]: item for item in visual["shots"]}
    VIDEO_DIR.mkdir(parents=True, exist_ok=True)
    rendered: list[dict] = []
    model = GrokVideoModel({"poll_interval_s": 5, "poll_timeout_s": 1200})

    for shot_id, prompt in MOTION.items():
        shot = shots[shot_id]
        duration_s = int(shot["duration_s"])
        output = VIDEO_DIR / f"{shot_id}.mp4"
        if output.is_file():
            try:
                actual = measure_media_duration_s(output)
            except Exception:
                actual = 0.0
            if actual >= duration_s - 0.35:
                rendered.append(
                    {
                        "id": shot_id,
                        "duration_s": duration_s,
                        "video_path": output.as_posix(),
                        "resumed": True,
                    }
                )
                print(f"{shot_id}: reuse {actual:.2f}s -> {output}", flush=True)
                write_manifest(rendered)
                continue

        print(f"{shot_id}: submit {duration_s}s", flush=True)
        path, elapsed = model.generate(
            prompt,
            str(output),
            duration=duration_s,
            resolution="720p",
            image=shot["still_path"],
        )
        item = {
            "id": shot_id,
            "duration_s": duration_s,
            "video_path": Path(path).as_posix(),
            "request_id": getattr(model, "last_request_id", None),
            "source_url": getattr(model, "last_source_url", None),
            "cost_usd": getattr(model, "last_cost_usd", None),
            "elapsed_s": round(elapsed, 2),
            "audio_stripped": bool(getattr(model, "last_audio_stripped", False)),
        }
        rendered.append(item)
        write_manifest(rendered)
        print(f"{shot_id}: ready in {elapsed:.1f}s -> {path}", flush=True)


if __name__ == "__main__":
    main()
