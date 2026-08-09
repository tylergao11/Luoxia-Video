"""Generate premium stills for 佛怒火莲 v3 (Hongguo 3D CGI, battle VFX plates)."""
from __future__ import annotations

import json
import sys
from pathlib import Path

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.luoxia.env import load_env_once

load_env_once()

from src.models.xai_image import XaiImageModel  # noqa: E402

ROOT = Path("output/doupo_fury_lotus_v3")
PLAN = ROOT / "stills_plan.json"


def main() -> None:
    plan = json.loads(PLAN.read_text(encoding="utf-8"))
    neg = plan.get("negative")
    model = XaiImageModel({"params": {"resolution": plan.get("resolution") or "2k"}})

    for i, shot in enumerate(plan["stills"], 1):
        out = Path(shot["path"])
        out.parent.mkdir(parents=True, exist_ok=True)
        if out.is_file() and out.stat().st_size > 10_000:
            print(f"[{i}/{len(plan['stills'])}] skip existing {out}", flush=True)
            continue
        ar = shot.get("aspect_ratio") or plan.get("aspect_ratio") or "16:9"
        print(f"[{i}/{len(plan['stills'])}] generate {out} ar={ar}", flush=True)
        path, elapsed = model.generate(
            shot["prompt"],
            str(out),
            aspect_ratio=ar,
            resolution=plan.get("resolution") or "2k",
            negative_prompt=neg,
        )
        cost = getattr(model, "last_cost_usd", None)
        print(f"  -> {path}  {elapsed:.1f}s  cost={cost}", flush=True)


if __name__ == "__main__":
    main()
