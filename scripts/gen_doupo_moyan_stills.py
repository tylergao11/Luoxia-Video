"""Generate Hongguo-style AI-manhua stills for the 三十年河东 scene.

Style target: 红果短剧「漫剧」— semi-realistic 3D CGI characters, NOT 2D anime.
"""
from __future__ import annotations

from pathlib import Path

from src.luoxia.env import load_env_once

load_env_once()

from src.models.xai_image import XaiImageModel  # noqa: E402
from src.output_contract import OUTPUT  # noqa: E402

root = OUTPUT.sample_dir("doupo_moyan")
for p in [root / "characters", root / "stills", root / "video", root / "audio"]:
    p.mkdir(parents=True, exist_ok=True)

model = XaiImageModel({"params": {"resolution": "2k"}})

# Hongguo AI short-drama manhua lock (半写实3D CGI)
STYLE = (
    "中国AI漫剧风格，红果短剧质感，半写实3D角色渲染，电影级体积光，"
    "细腻皮肤次表面散射，真实布料褶皱与发丝体积，浅景深，"
    "高端CG角色动画静帧，非2D动漫，非赛璐璐，非线稿平涂，非真人实拍照片"
)
NEG = (
    "2d anime, cel shading, flat color, thick outlines, cartoon, chibi, "
    "comic line art, illustration, sketch, anime eyes, live action photo, "
    "real human photo, phone snapshot, low poly, plastic toy, "
    "text, watermark, logo, subtitles, captions"
)

jobs = [
    {
        "path": root / "characters" / "xiao_yan.png",
        "ar": "9:16",
        "prompt": (
            f"{STYLE}。角色定妆照，单人半身正面，中性表情，直视镜头，均匀柔光，纯灰色背景。"
            "角色：萧炎。十五六岁少年，黑色微乱短发，剑眉深目，清瘦下颌，"
            "深蓝旧式武服，袖口与下摆有磨损褶皱，气质倔强隐忍，"
            "皮肤细腻，发丝有体积，服装材质清晰完整。"
        ),
    },
    {
        "path": root / "characters" / "nalan_yanran.png",
        "ar": "9:16",
        "prompt": (
            f"{STYLE}。角色定妆照，单人半身正面，中性表情，直视镜头，均匀柔光，纯灰色背景。"
            "角色：纳兰嫣然。十六七岁少女，乌黑长发高挽，白底金纹云岚宗弟子服，"
            "容貌清冷高傲美型，金饰与纱质衣料有光泽与褶皱，气质出尘。"
        ),
    },
    {
        "path": root / "stills" / "s01_humiliation.png",
        "ar": "16:9",
        "prompt": (
            f"{STYLE}，横屏16:9电影构图。萧家议事厅，广角偏中景。"
            "白衣金纹少女纳兰嫣然立于厅中，侧脸高傲，目光居高临下；"
            "对面深蓝旧武服少年萧炎站立，肩膀紧绷，黑发微乱，下颌咬紧，"
            "眼底压着耻辱与怒意；背景族人侧影与窃笑氛围，"
            "暖烛体积光与冷侧光对撞，戏剧张力强，无字幕无文字。"
        ),
    },
    {
        "path": root / "stills" / "s02_fists.png",
        "ar": "16:9",
        "prompt": (
            f"{STYLE}，横屏16:9。特写插入镜头：少年萧炎双手攥拳，指节发白，"
            "深蓝武服袖口布料褶皱，指甲掐进掌心，青筋隐现，"
            "侧光下灰尘微粒浮动，表达愤怒与不甘，无字幕无文字。"
        ),
    },
    {
        "path": root / "stills" / "s03_declaration.png",
        "ar": "16:9",
        "prompt": (
            f"{STYLE}，横屏16:9。中近景英雄位：少年萧炎抬起头，"
            "黑眸燃着怒火与不甘，眉峰紧锁，嘴角绷直像要咬碎牙，"
            "额前碎发被厅风掀起，深蓝旧武服布料猎猎微动，"
            "一只拳头仍紧握在身侧，议事厅背景虚化，逆光勾边体积光，"
            "神情是愤怒不甘的宣战感，无字幕无文字。"
        ),
    },
    {
        "path": root / "stills" / "s04_aftermath.png",
        "ar": "16:9",
        "prompt": (
            f"{STYLE}，横屏16:9。反应镜头：白衣纳兰嫣然娇躯微震，"
            "清冷表情裂开一丝震动，目光落在对面少年身上；"
            "前景虚化中少年剪影挺直脊背，厅内嘲笑似被压住，"
            "烛火摇曳体积光，尘埃静止一瞬，情绪落地的静默张力，无字幕无文字。"
        ),
    },
]


def main() -> None:
    for i, job in enumerate(jobs, 1):
        out = job["path"]
        # Force overwrite so old 2D anime frames do not linger.
        if out.is_file():
            out.unlink()
        print(f"[{i}/{len(jobs)}] {out.name} ...", flush=True)
        path, elapsed = model.generate(
            job["prompt"],
            str(out),
            aspect_ratio=job["ar"],
            negative_prompt=NEG,
        )
        print(f"  -> {path} ({elapsed:.1f}s)", flush=True)
    print("DONE")


if __name__ == "__main__":
    main()
