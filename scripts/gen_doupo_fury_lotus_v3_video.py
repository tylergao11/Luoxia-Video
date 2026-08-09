"""I2V animate 佛怒火莲 v3 with anti-slow-motion prompts (Grok Imagine Video)."""
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

ROOT = Path("output/doupo_fury_lotus_v3")
PLAN = ROOT / "grok_video_plan.json"
MANIFEST = ROOT / "video_manifest.json"


def write_manifest(items: list[dict], meta: dict) -> None:
    payload = {
        "project": meta.get("project"),
        "video_provider": "xai",
        "video_model": meta.get("model"),
        "resolution": meta.get("resolution"),
        "tempo_policy": meta.get("tempo_policy"),
        "shots": items,
    }
    MANIFEST.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
        newline="\n",
    )


def main() -> None:
    plan = json.loads(PLAN.read_text(encoding="utf-8"))
    resolution = plan.get("resolution") or "1080p"
    shots = plan["shots"]
    model = GrokVideoModel({"poll_interval_s": 5, "poll_timeout_s": 1200})
    rendered: list[dict] = []

    for i, shot in enumerate(shots, 1):
        shot_id = shot["id"]
        duration_s = int(shot["duration"])
        image = Path(shot["image"])
        output = Path(shot["output"])
        output.parent.mkdir(parents=True, exist_ok=True)

        if not image.is_file():
            raise FileNotFoundError(
                f"missing still for {shot_id}: {image} — run gen_doupo_fury_lotus_v3_stills.py first"
            )

        if output.is_file():
            try:
                actual = measure_media_duration_s(output)
            except Exception:
                actual = 0.0
            if actual >= duration_s - 0.35:
                item = {
                    "id": shot_id,
                    "duration_s": duration_s,
                    "video_path": output.as_posix(),
                    "image": image.as_posix(),
                    "resumed": True,
                    "actual_duration_s": round(actual, 3),
                }
                rendered.append(item)
                write_manifest(rendered, plan)
                print(f"[{i}/{len(shots)}] reuse {shot_id} {actual:.2f}s -> {output}", flush=True)
                continue

        print(f"[{i}/{len(shots)}] submit {shot_id} {duration_s}s res={resolution}", flush=True)
        path, elapsed = model.generate(
            shot["prompt"],
            str(output),
            duration=duration_s,
            resolution=resolution,
            image=str(image),
        )
        item = {
            "id": shot_id,
            "duration_s": duration_s,
            "video_path": Path(path).as_posix(),
            "image": image.as_posix(),
            "request_id": getattr(model, "last_request_id", None),
            "source_url": getattr(model, "last_source_url", None),
            "cost_usd": getattr(model, "last_cost_usd", None),
            "elapsed_s": round(elapsed, 2),
            "audio_stripped": bool(getattr(model, "last_audio_stripped", False)),
        }
        rendered.append(item)
        write_manifest(rendered, plan)
        print(
            f"  -> {path}  {elapsed:.1f}s  cost={item['cost_usd']}  req={item['request_id']}",
            flush=True,
        )

    print(f"done: {len(rendered)}/{len(shots)} shots -> {MANIFEST}", flush=True)


if __name__ == "__main__":
    main()
