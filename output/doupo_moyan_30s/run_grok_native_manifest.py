from __future__ import annotations

import argparse
import json
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from src.luoxia.env import load_env_once
from src.models.grok import GrokVideoModel


class NativeAudioGrok(GrokVideoModel):
    """Keep the audio track that Grok returns for this native-dialogue proof."""

    def _strip_audio(self, path: str) -> str:
        return path


def render_shot(shot: dict, model_name: str, resolution: str) -> dict:
    output = REPO_ROOT / shot["output"]
    image = REPO_ROOT / shot["image"]
    output.parent.mkdir(parents=True, exist_ok=True)
    model = NativeAudioGrok(
        {
            "poll_interval_s": 5,
            "poll_timeout_s": 1200,
            "model_name": model_name,
        }
    )
    path, elapsed = model.generate(
        shot["prompt"],
        str(output),
        duration=int(shot["duration"]),
        resolution=resolution,
        image=str(image),
    )
    return {
        "id": shot["id"],
        "output": Path(path).relative_to(REPO_ROOT).as_posix(),
        "elapsed_s": round(elapsed, 2),
        "request_id": getattr(model, "last_request_id", None),
        "source_url": getattr(model, "last_source_url", None),
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("manifest", type=Path)
    parser.add_argument("--workers", type=int, default=3)
    args = parser.parse_args()

    load_env_once()
    manifest_path = args.manifest.resolve()
    plan = json.loads(manifest_path.read_text(encoding="utf-8"))
    shots = plan["shots"]
    results = []
    errors = []

    with ThreadPoolExecutor(max_workers=max(1, args.workers)) as pool:
        futures = {
            pool.submit(
                render_shot,
                shot,
                plan["model"],
                plan["resolution"],
            ): shot["id"]
            for shot in shots
        }
        for future in as_completed(futures):
            shot_id = futures[future]
            try:
                result = future.result()
                results.append(result)
                print(f"READY {shot_id} {result['elapsed_s']}s {result['output']}", flush=True)
            except Exception as exc:
                errors.append({"id": shot_id, "error": repr(exc)})
                print(f"FAILED {shot_id} {exc!r}", flush=True)

    order = {shot["id"]: index for index, shot in enumerate(shots)}
    results.sort(key=lambda item: order[item["id"]])
    result_path = manifest_path.with_name("grok_native_full_results.json")
    result_path.write_text(
        json.dumps({"results": results, "errors": errors}, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
        newline="\n",
    )
    return 1 if errors else 0


if __name__ == "__main__":
    raise SystemExit(main())
