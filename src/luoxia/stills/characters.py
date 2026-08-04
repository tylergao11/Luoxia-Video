from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Sequence

GenerateFn = Callable[..., str]

# Bump when the sheet prompt changes so cached portraits regenerate instead of going stale.
SHEET_PROMPT_VERSION = "4"

# Product default: 红果 AI 漫 near-parity target. See docs/luoxia/07-STYLE-REF.md.
# Sharp adult 精致3D漫剧 (narrow eyes, bone structure, material density, drama light)
# — NOT photo-real, NOT 2D cel, NOT soft Q-cute round face.
HONGGUO_STYLE_LOCK = (
    "中国AI漫剧/红果短剧同款精致3D CGI角色渲染，虚幻引擎级材质密度，"
    "锋利骨相与修长脸型，窄长眼型与冷冽高光，瓷光无毛孔皮肤，"
    "发丝丝缕清晰、布料纤维与磨损高细节，戏剧体积光与暗部层次，"
    "封面级精修压迫感，成年向美型3D动漫角色（非幼态Q版）"
)

SHEET_TEMPLATE = (
    f"{HONGGUO_STYLE_LOCK}。"
    "角色定妆照，单人半身近景，中性微冷表情，直视镜头，"
    "戏剧侧光+柔和环境光，纯深灰背景，服装发型材质清晰；"
    "脸模锋利度与瓷光完成度必须接近红果封面男主；"
    "禁止真人写真、毛孔写实、幼态大圆眼、Q版圆脸、2D赛璐璐厚线稿。\n"
    "角色：{display_name}。{appearance}"
)

SHEET_NEGATIVE = (
    "photorealistic human, real person, live action, real human photo, "
    "idol photoshoot, documentary skin pores, realistic skin pores, freckles, "
    "oily skin, natural imperfect skin, DSLR photo, phone snapshot, "
    "chibi, baby face, cute round face, big round moe eyes, soft q-version, "
    "2d anime cel shading, thick outlines, flat color, sketch, "
    "low poly, plastic toy, silver hair, white hair, red eyes, "
    "多人，背影，侧脸大角度，遮挡面部，动态模糊，文字，水印，复杂背景，拼图，UI"
)


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
    style_ref_images: Optional[Sequence[Dict[str, Any]]] = None,
) -> Dict[str, str]:
    """Generate one locked portrait per character and cache it by appearance.

    This is the only thing standing between the harness and a protagonist whose face
    changes every shot: each still later passes these portraits back as I2I references.
    Regenerating on every run would defeat that, so the cache key is the appearance text.

    Optional `style_ref_images` are Hongguo / medium references with role=style (not identity).
    They condition render language without locking the ref character's face.
    """
    root = Path(output_root) / "characters"
    root.mkdir(parents=True, exist_ok=True)
    gen = generate or _default_generate()
    style_refs = _normalize_style_refs(style_ref_images)

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
        key = _cache_key(prompt, aspect_ratio, style_refs=style_refs)
        image_path = root / f"{cid}.png"
        meta_path = root / f"{cid}.json"
        cached = _cached_image(meta_path)

        if cached and Path(cached).is_file() and _cached_key(meta_path) == key:
            entry["reference_image_path"] = cached
            sheets[cid] = cached
            continue

        produced = gen(
            prompt,
            str(image_path),
            aspect_ratio=aspect_ratio,
            negative_prompt=SHEET_NEGATIVE,
            ref_images=style_refs or None,
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
    """Same portraits, labelled with display names and role=identity.

    Optional timeline.global.style_ref_images (or shot.still.style_ref_images) are prepended
    with role=style so medium and identity stay separated in compose_prompt.
    """
    refs = reference_map(timeline) if refs is None else refs
    names = {
        c.get("character_id"): (c.get("display_name") or c.get("character_id"))
        for c in timeline.get("cast") or []
    }
    style_raw = (
        ((shot.get("still") or {}).get("style_ref_images"))
        or ((timeline.get("global") or {}).get("style_ref_images"))
        or []
    )
    style_list = _normalize_style_refs(style_raw)
    # Reserve slots for identity; style first, then characters (provider max 3).
    id_budget = max(0, limit - min(len(style_list), 1))
    # Prefer one style frame so at least two identity slots remain for two-handers.
    style_list = style_list[:1] if style_list else []
    picked = _pick_refs(shot, refs, limit=id_budget if id_budget else limit)
    for entry in picked:
        entry["display_name"] = names.get(entry["character_id"]) or entry["character_id"]
        entry["role"] = "identity"
    combined = list(style_list) + picked
    return combined[:limit]


def _normalize_style_refs(
    style_ref_images: Optional[Sequence[Dict[str, Any]]],
) -> List[Dict[str, Any]]:
    """Force role=style on medium references; drop missing paths."""
    out: List[Dict[str, Any]] = []
    for raw in style_ref_images or []:
        path = (raw.get("path") or "").strip()
        if not path or not Path(path).is_file():
            continue
        out.append(
            {
                "display_name": raw.get("display_name") or "红果AI漫剧风格参考",
                "path": path,
                "role": "style",
            }
        )
    return out


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


def _cache_key(
    prompt: str,
    aspect_ratio: str,
    *,
    style_refs: Optional[Sequence[Dict[str, Any]]] = None,
) -> str:
    style_sig = "|".join(
        f"{r.get('path')}:{Path(r['path']).stat().st_mtime_ns}"
        for r in (style_refs or [])
        if r.get("path") and Path(r["path"]).is_file()
    )
    payload = f"{SHEET_PROMPT_VERSION}|{aspect_ratio}|{prompt}|style:{style_sig}"
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
