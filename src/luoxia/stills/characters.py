from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Sequence

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
    # Reference portraits stay vertical even though the episode ships 16:9: a full-body
    # locked look needs the height, and these images are never composited into the film.
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
        key = _cache_key(prompt, aspect_ratio)
        image_path = root / f"{cid}.png"
        meta_path = root / f"{cid}.json"
        cached = _cached_image(meta_path)

        if cached and Path(cached).is_file() and _cached_key(meta_path) == key:
            entry["reference_image_path"] = cached
            sheets[cid] = cached
            continue

        produced = gen(
            prompt, str(image_path), aspect_ratio=aspect_ratio, negative_prompt=SHEET_NEGATIVE
        )
        meta_path.write_text(
            json.dumps(
                {
                    "cache_key": key,
                    "prompt": prompt,
                    "aspect_ratio": aspect_ratio,
                    "appearance": appearance,
                    # The provider picks the encoding, so remember where it actually landed.
                    "image_path": str(produced),
                },
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
    return [r["path"] for r in _pick_refs(shot, refs, limit=limit)]


def named_refs_for_shot(
    shot: Dict[str, Any],
    timeline: Dict[str, Any],
    refs: Optional[Dict[str, str]] = None,
    *,
    limit: int = 3,
) -> List[Dict[str, str]]:
    """Same portraits, labelled with display names.

    A multi-reference prompt addresses its sources positionally, so the prompt has to state
    which portrait is which character or the model swaps faces between them.
    """
    refs = reference_map(timeline) if refs is None else refs
    names = {
        c.get("character_id"): (c.get("display_name") or c.get("character_id"))
        for c in timeline.get("cast") or []
    }
    picked = _pick_refs(shot, refs, limit=limit)
    for entry in picked:
        entry["display_name"] = names.get(entry["character_id"]) or entry["character_id"]
    return picked


def _pick_refs(
    shot: Dict[str, Any], refs: Dict[str, str], *, limit: int
) -> List[Dict[str, str]]:
    """One definition of reference order: screen order, deduped, capped."""
    out: List[Dict[str, str]] = []
    seen: set[str] = set()
    for cid in shot.get("characters") or []:
        path = refs.get(cid)
        if path and path not in seen:
            seen.add(path)
            out.append({"character_id": cid, "path": path})
    return out[:limit]


def _cache_key(prompt: str, aspect_ratio: str) -> str:
    payload = f"{SHEET_PROMPT_VERSION}|{aspect_ratio}|{prompt}"
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:16]


def _cached_key(meta_path: Path) -> Optional[str]:
    return _sheet_meta(meta_path).get("cache_key")


def _cached_image(meta_path: Path) -> Optional[str]:
    return _sheet_meta(meta_path).get("image_path")


def _sheet_meta(meta_path: Path) -> Dict[str, Any]:
    if not meta_path.is_file():
        return {}
    try:
        return json.loads(meta_path.read_text(encoding="utf-8")) or {}
    except (ValueError, OSError):
        return {}


def _default_generate() -> GenerateFn:
    from src.models.xai_image import XaiImageModel

    # 1k is enough for a reference portrait: it is only ever read back as an I2I source,
    # never composited into the episode.
    model = XaiImageModel({"params": {"resolution": "1k"}})

    def generate(
        prompt: str,
        output_path: str,
        *,
        aspect_ratio: str,
        negative_prompt: Optional[str] = None,
    ) -> str:
        path, _elapsed = model.generate(
            prompt, output_path, aspect_ratio=aspect_ratio, negative_prompt=negative_prompt
        )
        return path

    return generate
