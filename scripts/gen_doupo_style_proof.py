"""Reproduce near-parity Hongguo AI-manhua sheet + scene (see docs/luoxia/07-STYLE-REF.md)."""
from __future__ import annotations

import shutil
from pathlib import Path

from PIL import Image

from src.luoxia.env import load_env_once

load_env_once()

from src.luoxia.stills.characters import HONGGUO_STYLE_LOCK, SHEET_NEGATIVE  # noqa: E402
from src.models.xai_image import XaiImageModel  # noqa: E402

ROOT = Path("output/doupo_moyan")
REFS = ROOT / "refs"
CHARS = ROOT / "characters"
STILLS = ROOT / "stills"
for p in (REFS, CHARS, STILLS):
    p.mkdir(parents=True, exist_ok=True)

SRC = REFS / "style_ref_male_closeup.jpg"
TIGHT = REFS / "style_ref_male_face_tight.jpg"


def crop_tight() -> Path:
    im = Image.open(SRC).convert("RGB")
    w, h = im.size
    face = im.crop((int(w * 0.22), int(h * 0.12), int(w * 0.78), int(h * 0.78)))
    face.save(TIGHT, quality=96)
    return TIGHT


def main() -> None:
    if not SRC.is_file():
        raise SystemExit(f"missing {SRC}")
    crop_tight()
    style = {
        "display_name": "红果封面级精致3D风格",
        "path": str(TIGHT),
        "role": "style",
    }
    # Near-parity path: style frame as edit source + identity rewrite
    prompt = (
        "Keep the EXACT same premium Chinese AI short-drama 3D CGI render quality as the reference: "
        "Unreal-engine material density, sharp elongated face bone structure, narrow cold eyes with sharp highlights, "
        "porcelain poreless skin, dramatic volumetric rim light, high-detail hair strands. "
        "Completely change identity: black messy short-to-medium hair (NO silver/white hair), "
        "dark brown/black eyes (NO red eyes), remove forehead mark and earrings and red-black costume. "
        "Young teen male Xiao Yan in worn deep-blue martial robe with frayed fabric detail, "
        "cold intense expression, same lighting language as reference, Hongguo manhua-drama cover quality, "
        "not photoreal, not cute chibi."
    )
    neg = SHEET_NEGATIVE + ", silver hair, white hair, red eyes, cute moe, chibi"
    model = XaiImageModel({"params": {"resolution": "2k"}})
    sheet = CHARS / "xiao_yan.png"
    if sheet.is_file():
        sheet.unlink()
    path, elapsed = model.generate(
        prompt, str(sheet), aspect_ratio="9:16", negative_prompt=neg, ref_images=[style]
    )
    print("sheet", path, f"{elapsed:.1f}s")

    ident = {"display_name": "萧炎", "path": path, "role": "identity"}
    scene_prompt = (
        f"{HONGGUO_STYLE_LOCK}，横屏16:9。中近景英雄位：萧炎议事厅抬头怒视，"
        "窄长冷眼、锋利骨相、瓷光皮肤、黑发微乱、深蓝旧武服高材质密度，"
        "拳头紧握，戏剧体积光，红果AI漫剧成片，无字幕。"
    )
    scene = STILLS / "s03_declaration.png"
    if scene.is_file():
        scene.unlink()
    spath, se = model.generate(
        scene_prompt,
        str(scene),
        aspect_ratio="16:9",
        negative_prompt=neg + ", text, watermark",
        ref_images=[style, ident],
    )
    print("scene", spath, f"{se:.1f}s")
    shutil.copy2(path, CHARS / f"xiao_yan_best{Path(path).suffix}")
    print("DONE")


if __name__ == "__main__":
    main()
