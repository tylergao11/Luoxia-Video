from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Sequence

from src.luoxia.stills.sizing import size_for_aspect

GenerateFn = Callable[..., str]

# Bump when the sheet prompt changes so cached portraits regenerate instead of going stale.
SHEET_PROMPT_VERSION = "1"

SHEET_TEMPLATE = (
    "角色定妆照，单人半身正面，中性表情，直视镜头，均匀柔光，纯灰色背景，"
    "服装与发型清晰完整，写实电影质感。\n"
    "角色：{display_name}。{appearance}"
)

SHEET_NEGATIVE = "多人，背影，侧脸大角度，遮挡面部，夸张表情，动态模糊，文字，水印，复杂背景，拼图"


class CharacterSheetError(RuntimeError):
    pass


def ensure_character_sheets(
    cast: Sequence[Dict[str, Any]],
    *,
    output_root: Path | str,
    aspect_ratio: str = "9:16",
    generate: Optional[GenerateFn] = None,
    require_appearance: bool = False,
) -> Dict[str, str]:
    """Generate one locked portrait per character and cache it by appearance.

    This is the only thing standing between the harness and a protagonist whose face
    changes every shot: each still later passes these portraits back as I2I references.
    Regenerating on every run would defeat that, so the cache key is the appearance text.
    """
    root = Path(output_root) / "characters"
    root.mkdir(parents=True, exist_ok=True)
    size = size_for_aspect(aspect_ratio)
    gen = generate or _default_generate()

    sheets: Dict[str, str] = {}
    for entry in cast:
        cid = entry.get("character_id")
        if not cid:
            continue
        appearance = (entry.get("appearance") or "").strip()
        if not appearance:
            if require_appearance:
                raise CharacterSheetError(
                    f"cast '{cid}' has no appearance; cannot lock its look across shots"
                )
            continue

        prompt = SHEET_TEMPLATE.format(
            display_name=entry.get("display_name") or cid,
            appearance=appearance,
        )
        key = _cache_key(prompt, size)
        image_path = root / f"{cid}.png"
        meta_path = root / f"{cid}.json"

        if image_path.is_file() and _cached_key(meta_path) == key:
            entry["reference_image_path"] = str(image_path)
            sheets[cid] = str(image_path)
            continue

        produced = gen(prompt, str(image_path), size=size, negative_prompt=SHEET_NEGATIVE)
        meta_path.write_text(
            json.dumps(
                {"cache_key": key, "prompt": prompt, "size": size, "appearance": appearance},
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )
        entry["reference_image_path"] = str(produced)
        sheets[cid] = str(produced)
    return sheets


def reference_map(timeline: Dict[str, Any]) -> Dict[str, str]:
    """Character -> portrait path, taken from the timeline cast."""
    out: Dict[str, str] = {}
    for entry in timeline.get("cast") or []:
        path = entry.get("reference_image_asset_id")
        cid = entry.get("character_id")
        if cid and path and Path(path).is_file():
            out[cid] = path
    return out


def refs_for_shot(shot: Dict[str, Any], refs: Dict[str, str], *, limit: int = 3) -> List[str]:
    """Portraits for whoever is on screen, deduped and capped to the provider's limit."""
    seen: List[str] = []
    for cid in shot.get("characters") or []:
        path = refs.get(cid)
        if path and path not in seen:
            seen.append(path)
    return seen[:limit]


def _cache_key(prompt: str, size: str) -> str:
    payload = f"{SHEET_PROMPT_VERSION}|{size}|{prompt}"
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:16]


def _cached_key(meta_path: Path) -> Optional[str]:
    if not meta_path.is_file():
        return None
    try:
        return json.loads(meta_path.read_text(encoding="utf-8")).get("cache_key")
    except (ValueError, OSError):
        return None


def _default_generate() -> GenerateFn:
    from src.models.image import WanxImageModel

    model = WanxImageModel({"params": {"model_name": "wan2.7-image-pro"}})

    def generate(prompt: str, output_path: str, *, size: str, negative_prompt: Optional[str] = None) -> str:
        path, _elapsed = model.generate(prompt, output_path, size=size, negative_prompt=negative_prompt)
        return path

    return generate
