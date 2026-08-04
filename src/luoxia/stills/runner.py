from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Any, Callable, Dict, Optional

from src.luoxia.media.ffprobe import measure_video_size
from src.luoxia.media.geometry import frame_size
from src.luoxia.stills.characters import named_refs_for_shot, reference_map
from src.utils.system_check import get_ffmpeg_path

GenerateFn = Callable[..., str]
# generate(prompt, output_path, *, aspect_ratio, negative_prompt, ref_images) -> local_path


def render_timeline_stills(
    timeline: Dict[str, Any],
    *,
    output_root: Path | str,
    generate: Optional[GenerateFn] = None,
    continue_on_error: bool = False,
) -> Dict[str, Any]:
    """Idempotent still generation. Writes under output_root/stills/.

    Shots carry their speakers' locked portraits as I2I references, which is what keeps
    one character looking like one character across the episode.
    """
    root = Path(output_root)
    root.mkdir(parents=True, exist_ok=True)
    aspect = (timeline.get("global") or {}).get("aspect_ratio") or "16:9"
    width, height = frame_size(timeline)
    gen = generate or _default_generate()
    refs = reference_map(timeline)

    failures: list[str] = []
    for shot in timeline.get("shots") or []:
        still = shot.setdefault("still", {})
        local = still.get("local_path")
        if local and Path(local).is_file() and still.get("status") == "ready":
            continue
        prompt = (still.get("prompt") or "").strip()
        if not prompt:
            prompt = (shot.get("video") or {}).get("request", {}).get("prompt") or shot["shot_id"]
            still["prompt"] = prompt
        shot_refs = named_refs_for_shot(shot, timeline, refs)
        out = root / "stills" / f"{shot['shot_id']}.png"
        out.parent.mkdir(parents=True, exist_ok=True)
        still["status"] = "generating"
        still["aspect_ratio"] = aspect
        if shot_refs:
            still["reference_image_paths"] = [r["path"] for r in shot_refs]
        try:
            path = gen(
                prompt,
                str(out),
                aspect_ratio=aspect,
                negative_prompt=still.get("negative_prompt"),
                ref_images=shot_refs or None,
            )
            path = _fit_to_frame(path, width, height)
            still.update(
                {
                    "status": "ready",
                    "local_path": path,
                    "error": None,
                    "attempts": int(still.get("attempts") or 0) + 1,
                }
            )
        except Exception as exc:
            still["status"] = "failed"
            still["attempts"] = int(still.get("attempts") or 0) + 1
            still["error"] = str(exc)
            if not continue_on_error:
                raise
            failures.append(shot["shot_id"])

    if failures:
        timeline.setdefault("audit", []).append(
            {
                "at": None,
                "actor": "stills:runner",
                "action": "render_stills",
                "detail": f"{len(failures)} shot(s) failed: {', '.join(failures[:10])}",
            }
        )
    return timeline


def _fit_to_frame(path: str, width: int, height: int) -> str:
    """Resize a still to exactly the frame it will be the first frame of.

    Providers round aspect ratios their own way, so cover-and-crop rather than stretch.
    A still already at the right size is left alone.
    """
    src = Path(path)
    if measure_video_size(src) == (width, height):
        return path

    ffmpeg = get_ffmpeg_path()
    if not ffmpeg:
        raise RuntimeError("ffmpeg not found; cannot fit still to frame")
    out = src.with_name(f"{src.stem}_frame.jpg")
    result = subprocess.run(
        [
            ffmpeg, "-y", "-v", "error", "-i", str(src),
            "-vf",
            f"scale={width}:{height}:force_original_aspect_ratio=increase:flags=lanczos,"
            f"crop={width}:{height}",
            # mjpeg cannot take an alpha channel, which a provider PNG may carry.
            "-pix_fmt", "yuvj420p",
            "-q:v", "2", str(out),
        ],
        capture_output=True,
        text=True,
        timeout=120,
    )
    if result.returncode != 0:
        raise RuntimeError(f"fitting still to {width}x{height} failed: {result.stderr[-500:]}")
    return str(out)


def _default_generate() -> GenerateFn:
    from src.models.xai_image import XaiImageModel

    # Images cost $0.02 whatever the resolution, and 1k tops out at 1280x720 — below a
    # 1080p frame. Ask for 2k and let the runner resize down to the exact frame.
    model = XaiImageModel({"params": {"resolution": "2k"}})

    def generate(
        prompt: str,
        output_path: str,
        *,
        aspect_ratio: str,
        negative_prompt: Optional[str] = None,
        ref_images: Optional[list] = None,
    ) -> str:
        path, _elapsed = model.generate(
            prompt,
            output_path,
            aspect_ratio=aspect_ratio,
            negative_prompt=negative_prompt,
            ref_images=ref_images,
        )
        return path

    return generate
