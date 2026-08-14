from pathlib import Path

from PIL import Image, ImageChops, ImageDraw, ImageFilter


COSTUME_MASTER = Path(
    r"D:\Luoxia-Video\output\character_designs\hongguo_girl_costume_master_treasure_hunter_v1.png"
)
HEAD_PATCH = Path(
    r"D:\Luoxia-Video\tmp\imagegen\hongguo_final_assembly_head_patch_transparent_v1.png"
)
FACE_MASTER = Path(
    r"D:\Luoxia-Video\output\style_refs\hongguo_treasure_hunter_girl_face_master_20260814.png"
)
OUTPUT = Path(
    r"D:\Luoxia-Video\tmp\imagegen\hongguo_final_character_master_composite_preview_v3.png"
)

base = Image.open(COSTUME_MASTER).convert("RGBA")
base = base.resize((base.width * 2, base.height * 2), Image.Resampling.LANCZOS)

# The source crop covered x=256..768 and y=0..512 on the original master.
# At 2x output resolution it occupies a 1024px square beginning at x=512.
patch = Image.open(HEAD_PATCH).convert("RGBA")
patch = patch.resize((1024, 1024), Image.Resampling.LANCZOS)
alpha = patch.getchannel("A")

# Core head/face/neck area. The lower boundary is deliberately narrow so the
# costume collar and torso continue to come from the locked costume master.
core_shape = Image.new("L", patch.size, 0)
draw = ImageDraw.Draw(core_shape)
draw.polygon(
    [
        (250, 0),
        (775, 0),
        (875, 245),
        (805, 470),
        (650, 535),
        (600, 610),
        (430, 610),
        (385, 535),
        (220, 470),
        (150, 245),
    ],
    fill=255,
)
core_shape = core_shape.filter(ImageFilter.GaussianBlur(12))
core_mask = ImageChops.multiply(core_shape, alpha)

# Preserve long black hair below the head without copying generated robe pixels.
luma = patch.convert("L")
dark_hair = luma.point(lambda value: 255 if value < 105 else 0)
hair_extent = Image.new("L", patch.size, 0)
ImageDraw.Draw(hair_extent).rectangle((130, 120, 895, 850), fill=255)
dark_hair = ImageChops.multiply(dark_hair, hair_extent)
dark_hair = ImageChops.multiply(dark_hair, alpha)
dark_hair = dark_hair.filter(ImageFilter.GaussianBlur(2))

assembly_mask = ImageChops.lighter(core_mask, dark_hair)

layer = Image.new("RGBA", base.size, (0, 0, 0, 0))
layer.paste(patch, (512, 0), assembly_mask)
result = Image.alpha_composite(base, layer)

# Apply the user-selected face master directly, without another generation pass.
# The transform is anchored to both eyes, mouth, and chin in the 2x canvas.
face_master = Image.open(FACE_MASTER).convert("RGBA")
face_master = face_master.resize((673, 497), Image.Resampling.LANCZOS)
face_master = face_master.filter(ImageFilter.UnsharpMask(radius=1.2, percent=70, threshold=3))
face_canvas = Image.new("RGBA", result.size, (0, 0, 0, 0))
face_canvas.paste(face_master, (653, 34))

face_mask = Image.new("L", result.size, 0)
draw = ImageDraw.Draw(face_mask)
draw.polygon(
    [
        (920, 145),
        (1100, 140),
        (1160, 235),
        (1165, 370),
        (1110, 445),
        (1040, 485),
        (960, 460),
        (890, 385),
        (885, 245),
    ],
    fill=255,
)
face_mask = face_mask.filter(ImageFilter.GaussianBlur(16))
face_mask = ImageChops.multiply(face_mask, face_canvas.getchannel("A"))
face_mask = face_mask.point(lambda value: int(value * 0.62))

face_layer = Image.new("RGBA", result.size, (0, 0, 0, 0))
face_layer.paste(face_canvas, (0, 0), face_mask)
result = Image.alpha_composite(result, face_layer)

result.save(OUTPUT, format="PNG", optimize=True)
print(OUTPUT)
