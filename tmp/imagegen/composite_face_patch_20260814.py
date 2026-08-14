from pathlib import Path

from PIL import Image, ImageDraw, ImageFilter


BASE_PATH = Path(r"C:\Users\Tylergao\AppData\Local\Temp\codex-clipboard-8fd325a1-f0dc-41dd-9b27-56d3067a5cd0.png")
PATCH_PATH = Path(r"D:\Luoxia-Video\tmp\imagegen\hongguo_face_repair_generated_patch_20260814.png")
OUTPUT_PATH = Path(r"D:\Luoxia-Video\tmp\imagegen\hongguo_face_repair_composite_preview_20260814.png")

# The original face crop was taken from this exact rectangle and enlarged 4x.
CROP_X = 390
CROP_Y = 20
CROP_SIZE = 256
WORK_SIZE = 1024

base = Image.open(BASE_PATH).convert("RGB")
original_crop = base.crop(
    (CROP_X, CROP_Y, CROP_X + CROP_SIZE, CROP_Y + CROP_SIZE)
).resize((WORK_SIZE, WORK_SIZE), Image.Resampling.LANCZOS)

repair = Image.open(PATCH_PATH).convert("RGB")
repair_size = 962
repair = repair.resize((repair_size, repair_size), Image.Resampling.LANCZOS)
aligned_repair = Image.new("RGB", (WORK_SIZE, WORK_SIZE))
aligned_repair.paste(repair, (53, 50))

# Restrict the generated pixels to the facial interior. Everything outside this
# softly feathered oval remains byte-for-byte derived from the user's base image.
mask = Image.new("L", (WORK_SIZE, WORK_SIZE), 0)
draw = ImageDraw.Draw(mask)
draw.ellipse((345, 325, 765, 900), fill=255)
mask = mask.filter(ImageFilter.GaussianBlur(35))

repaired_crop = Image.composite(aligned_repair, original_crop, mask)
repaired_crop = repaired_crop.resize(
    (CROP_SIZE, CROP_SIZE), Image.Resampling.LANCZOS
)

result = base.copy()
result.paste(repaired_crop, (CROP_X, CROP_Y))
result.save(OUTPUT_PATH, format="PNG", optimize=True)
print(OUTPUT_PATH)
