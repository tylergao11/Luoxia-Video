from pathlib import Path

from PIL import Image, ImageDraw, ImageFilter


BASE_PATH = Path(r"C:\Users\Tylergao\AppData\Local\Temp\codex-clipboard-8fd325a1-f0dc-41dd-9b27-56d3067a5cd0.png")
PATCH_PATH = Path(r"D:\Luoxia-Video\tmp\imagegen\hongguo_face_repair_user_selected_patch_20260814.png")
OUTPUT_PATH = Path(r"D:\Luoxia-Video\tmp\imagegen\hongguo_user_selected_face_composite_preview_v3_20260814.png")

CROP_X = 390
CROP_Y = 20
CROP_SIZE = 256
WORK_SIZE = 1024

base = Image.open(BASE_PATH).convert("RGB")
original_crop = base.crop(
    (CROP_X, CROP_Y, CROP_X + CROP_SIZE, CROP_Y + CROP_SIZE)
).resize((WORK_SIZE, WORK_SIZE), Image.Resampling.LANCZOS)

# Align the generated face patch to the user's untouched target using the eye
# line, nose, mouth, and chin anchors. The original crop remains the canvas.
repair = Image.open(PATCH_PATH).convert("RGB")
repair = repair.resize((1085, 1085), Image.Resampling.LANCZOS)
aligned_repair = original_crop.copy()
aligned_repair.paste(repair, (-34, -20))

# Limit replacement to the facial interior and feather into the untouched
# target. Hair, head silhouette, neck, collar, and all full-body pixels remain.
mask = Image.new("L", (WORK_SIZE, WORK_SIZE), 0)
draw = ImageDraw.Draw(mask)
draw.ellipse((370, 340, 730, 850), fill=255)
mask = mask.filter(ImageFilter.GaussianBlur(24))

repaired_crop = aligned_repair.resize(
    (CROP_SIZE, CROP_SIZE), Image.Resampling.LANCZOS
)
paste_mask = mask.resize((CROP_SIZE, CROP_SIZE), Image.Resampling.LANCZOS)

result = base.copy()
result.paste(repaired_crop, (CROP_X, CROP_Y), paste_mask)
result.save(OUTPUT_PATH, format="PNG", optimize=True)
print(OUTPUT_PATH)
