from __future__ import annotations

from pathlib import Path
from typing import Any, Callable, Dict, Optional

from src.luoxia.stills.characters import reference_map, refs_for_shot
from src.luoxia.stills.sizing import size_for_aspect

GenerateFn = Callable[..., str]
# generate(prompt, output_path, *, size, negative_prompt, ref_image_paths) -> local_path


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
    aspect = (timeline.get("global") or {}).get("aspect_ratio") or "9:16"
    size = size_for_aspect(aspect)
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
        shot_refs = refs_for_shot(shot, refs)
        out = root / "stills" / f"{shot['shot_id']}.png"
        out.parent.mkdir(parents=True, exist_ok=True)
        still["status"] = "generating"
        still["aspect_ratio"] = aspect
        if shot_refs:
            still["reference_image_paths"] = shot_refs
        try:
            path = gen(
                prompt,
                str(out),
                size=size,
                negative_prompt=still.get("negative_prompt"),
                ref_image_paths=shot_refs or None,
            )
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


def _default_generate() -> GenerateFn:
    from src.models.image import WanxImageModel

    # i2i_model_name is what the upstream model switches to once references are present.
    model = WanxImageModel(
        {"params": {"model_name": "wan2.7-image-pro", "i2i_model_name": "wan2.7-image"}}
    )

    def generate(
        prompt: str,
        output_path: str,
        *,
        size: str,
        negative_prompt: Optional[str] = None,
        ref_image_paths: Optional[list] = None,
    ) -> str:
        path, _elapsed = model.generate(
            prompt,
            output_path,
            size=size,
            negative_prompt=negative_prompt,
            ref_image_paths=ref_image_paths,
        )
        return path

    return generate
